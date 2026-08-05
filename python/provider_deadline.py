"""One absolute deadline for the complete AI request lifecycle.

The MT5 terminal owns the outermost deadline: the Strategy Tester / live EA
stops waiting after ``InpAiWaitTimeoutRealMin`` and fails the setup closed.
Everything Python does for that request -- provider connect, response wait,
read, parse, validation, envelope construction, and the atomic response write --
has to finish inside that window, with enough margin left to actually write a
schema-valid response.

This module is the single source of that arithmetic.  Callers never invent
relative timers: they derive one :class:`RequestDeadline` from an absolute
monotonic start and ask it how much budget remains.

Deadline hierarchy (all derived, no scattered literals)::

    mt5_terminal_deadline   = start + mt5_terminal_timeout
    python_response_deadline = mt5_terminal_deadline - response_write_margin
    provider_deadline        = min(configured_provider_timeout, remaining budget)
"""

from __future__ import annotations

import time
from dataclasses import dataclass


DEADLINE_CONTRACT_VERSION = "20260730_absolute_request_deadline_v1"

# Stage labels used by [provider_deadline_exceeded]; a precise stage is always
# preferred over a generic timeout label.
STAGE_CONNECT = "connect"
STAGE_RESPONSE_WAIT = "response_wait"
STAGE_READ = "read"
STAGE_PARSE = "parse"
STAGE_POSTPROCESS = "postprocess"

LATE_RESULT_QUARANTINE = "quarantine"


@dataclass(frozen=True)
class DeadlinePolicy:
    """Derived millisecond budgets for one workload."""

    mt5_terminal_timeout_ms: int
    response_write_margin_ms: int
    min_attempt_ms: int

    def __post_init__(self) -> None:
        if self.mt5_terminal_timeout_ms <= 0:
            raise ValueError("mt5_terminal_timeout_ms_invalid")
        if self.response_write_margin_ms < 0:
            raise ValueError("response_write_margin_ms_invalid")
        if self.min_attempt_ms <= 0:
            raise ValueError("min_attempt_ms_invalid")
        if self.response_write_margin_ms >= self.mt5_terminal_timeout_ms:
            raise ValueError("response_write_margin_exceeds_terminal_timeout")

    @property
    def python_deadline_ms(self) -> int:
        """Budget for producing a response, reserving the write margin."""

        return self.mt5_terminal_timeout_ms - self.response_write_margin_ms

    @classmethod
    def derive(
        cls,
        *,
        mt5_terminal_timeout_sec: float,
        response_write_margin_sec: float,
        min_attempt_sec: float,
    ) -> "DeadlinePolicy":
        terminal_ms = int(round(float(mt5_terminal_timeout_sec) * 1000.0))
        margin_ms = int(round(float(response_write_margin_sec) * 1000.0))
        # A margin can never consume the whole terminal window; clamp to half so
        # a misconfigured margin degrades to "less provider time", never to
        # "no provider time and no response".
        margin_ms = max(0, min(margin_ms, terminal_ms // 2))
        return cls(
            mt5_terminal_timeout_ms=terminal_ms,
            response_write_margin_ms=margin_ms,
            min_attempt_ms=max(1, int(round(float(min_attempt_sec) * 1000.0))),
        )


@dataclass(frozen=True)
class RequestDeadline:
    """An absolute, monotonic, non-resettable deadline for one request id."""

    request_id: str
    policy: DeadlinePolicy
    wall_start_monotonic: float

    @classmethod
    def start(
        cls,
        request_id: str,
        policy: DeadlinePolicy,
        *,
        now: float | None = None,
    ) -> "RequestDeadline":
        return cls(
            request_id=str(request_id),
            policy=policy,
            wall_start_monotonic=float(time.monotonic() if now is None else now),
        )

    def _now(self, now: float | None) -> float:
        return float(time.monotonic() if now is None else now)

    def elapsed_ms(self, now: float | None = None) -> int:
        return max(0, int(round((self._now(now) - self.wall_start_monotonic) * 1000.0)))

    def remaining_ms(self, now: float | None = None) -> int:
        """Milliseconds left before the Python response deadline."""

        return self.policy.python_deadline_ms - self.elapsed_ms(now)

    def terminal_remaining_ms(self, now: float | None = None) -> int:
        """Milliseconds left before MT5 itself stops waiting."""

        return self.policy.mt5_terminal_timeout_ms - self.elapsed_ms(now)

    def expired(self, now: float | None = None) -> bool:
        return self.remaining_ms(now) <= 0

    def terminal_expired(self, now: float | None = None) -> bool:
        return self.terminal_remaining_ms(now) <= 0

    def can_start_attempt(self, now: float | None = None) -> bool:
        """False when too little budget remains to finish another attempt."""

        return self.remaining_ms(now) >= self.policy.min_attempt_ms

    def provider_timeout_sec(
        self,
        configured_timeout_sec: float,
        *,
        now: float | None = None,
    ) -> float:
        """The real HTTP timeout to hand the SDK for the next attempt.

        Never larger than what the absolute deadline still allows, so a
        configured 90s timeout cannot outlive a 105s request budget that already
        spent 60s.
        """

        remaining_sec = self.remaining_ms(now) / 1000.0
        return max(0.0, min(float(configured_timeout_sec), remaining_sec))

    def as_log_fields(self) -> str:
        return (
            f" mt5_terminal_timeout_ms={self.policy.mt5_terminal_timeout_ms}"
            f" python_deadline_ms={self.policy.python_deadline_ms}"
            f" write_margin_ms={self.policy.response_write_margin_ms}"
        )
