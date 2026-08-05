"""Exactly-one terminal outcome per AI request identity.

A request can finish in several ways -- a validated response, a deadline
timeout, an interrupted Strategy Tester run, or a quarantine -- but it may only
finish *once*.  This registry is the arbiter.

It exists because a bounded worker pool plus a real network makes late results
unavoidable: a provider call started at t=5s can return at t=254s, long after
the MT5 terminal stopped waiting and Python already wrote an identity-bound
timeout envelope.  Such a result must never overwrite the authoritative
outcome, never enter the decision cache, and never bill a second provider call
for the same identity.

The registry also owns the absolute :class:`RequestDeadline` for each in-flight
request, so any thread can ask how much budget is left without re-deriving it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Mapping

from provider_deadline import RequestDeadline


TERMINAL_STATE_VERSION = "20260730_single_terminal_outcome_v1"

# Terminal outcomes.  Each is final: the first one claimed wins.
TERMINAL_COMPLETED = "COMPLETED"
TERMINAL_TIMEOUT = "PROVIDER_DEADLINE_TIMEOUT"
TERMINAL_TEST_END_INTERRUPTED = "TEST_END_INTERRUPTED"
TERMINAL_QUARANTINED = "QUARANTINED"
TERMINAL_ERROR = "ERROR_RESPONSE_WRITTEN"

TERMINAL_STATES = (
    TERMINAL_COMPLETED,
    TERMINAL_TIMEOUT,
    TERMINAL_TEST_END_INTERRUPTED,
    TERMINAL_QUARANTINED,
    TERMINAL_ERROR,
)

# A late result is never authoritative, but the reason it was late matters for
# diagnosis, so the claim that lost the race is recorded rather than discarded.
LATE_RESULT_QUARANTINE = "quarantine"


@dataclass(frozen=True)
class TerminalOutcome:
    request_id: str
    state: str
    reason: str
    elapsed_ms: int


class RequestTerminalRegistry:
    """Thread-safe deadline + terminal-state registry for one gate process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._deadlines: dict[str, RequestDeadline] = {}
        self._terminal: dict[str, TerminalOutcome] = {}
        self._late_results: dict[str, int] = {}

    # ---- deadlines -----------------------------------------------------

    def register_deadline(self, deadline: RequestDeadline) -> RequestDeadline:
        """Register the absolute deadline, or return the existing one.

        Re-registration never restarts the clock: a deadline that already
        exists for this identity is the authoritative one.
        """

        with self._lock:
            existing = self._deadlines.get(deadline.request_id)
            if existing is not None:
                return existing
            self._deadlines[deadline.request_id] = deadline
            return deadline

    def deadline_for(self, request_id: str) -> RequestDeadline | None:
        with self._lock:
            return self._deadlines.get(str(request_id))

    # ---- terminal state ------------------------------------------------

    def is_terminal(self, request_id: str) -> bool:
        with self._lock:
            return str(request_id) in self._terminal

    def terminal_outcome(self, request_id: str) -> TerminalOutcome | None:
        with self._lock:
            return self._terminal.get(str(request_id))

    def claim_terminal(
        self,
        request_id: str,
        state: str,
        *,
        reason: str = "",
        elapsed_ms: int = -1,
    ) -> tuple[bool, TerminalOutcome]:
        """Attempt to become the single terminal outcome for this identity.

        Returns ``(won, outcome)``.  ``won`` is False when another thread
        already finished this request; ``outcome`` is then the *existing*
        authoritative outcome, and the caller must not write a response.
        """

        if state not in TERMINAL_STATES:
            raise ValueError(f"unknown_terminal_state:{state}")
        key = str(request_id)
        with self._lock:
            existing = self._terminal.get(key)
            if existing is not None:
                self._late_results[key] = self._late_results.get(key, 0) + 1
                return False, existing
            outcome = TerminalOutcome(
                request_id=key,
                state=state,
                reason=str(reason),
                elapsed_ms=int(elapsed_ms),
            )
            self._terminal[key] = outcome
            return True, outcome

    def late_result_count(self, request_id: str) -> int:
        with self._lock:
            return self._late_results.get(str(request_id), 0)

    # ---- lifecycle -----------------------------------------------------

    def release(self, request_id: str) -> None:
        """Drop the in-flight deadline; the terminal outcome is retained."""

        with self._lock:
            self._deadlines.pop(str(request_id), None)

    def mark_pending_test_end_interrupted(self, reason: str) -> tuple[str, ...]:
        """Terminalize every still-active request when the tester run ends.

        Pending requests at test end are *not* provider timeouts: nothing was
        slow, the simulated period simply finished.  Mislabeling them hides real
        transport health.
        """

        with self._lock:
            pending = [
                request_id
                for request_id in self._deadlines
                if request_id not in self._terminal
            ]
            for request_id in pending:
                self._terminal[request_id] = TerminalOutcome(
                    request_id=request_id,
                    state=TERMINAL_TEST_END_INTERRUPTED,
                    reason=str(reason),
                    elapsed_ms=-1,
                )
            return tuple(sorted(pending))

    def snapshot(self) -> Mapping[str, str]:
        with self._lock:
            return {
                request_id: outcome.state
                for request_id, outcome in self._terminal.items()
            }


# One registry per gate process.
REQUEST_TERMINAL_REGISTRY = RequestTerminalRegistry()
