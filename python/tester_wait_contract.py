from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TesterWaitState(str, Enum):
    __test__ = False

    WAITING = "WAITING"
    RESPONSE_APPLIED = "RESPONSE_APPLIED"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class TesterAIWaitDeadline:
    """Pure mirror of the MQL monotonic wall-clock deadline contract."""

    __test__ = False

    wall_start_ms: int
    timeout_ms: int

    def __post_init__(self) -> None:
        if self.wall_start_ms < 0:
            raise ValueError("wall_start_ms_invalid")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms_invalid")

    @property
    def deadline_ms(self) -> int:
        return self.wall_start_ms + self.timeout_ms

    def elapsed_ms(self, now_ms: int) -> int:
        return max(0, int(now_ms) - self.wall_start_ms)

    def state(self, now_ms: int, *, valid_response_present: bool) -> TesterWaitState:
        # A response is current only strictly before the immutable deadline.
        if self.elapsed_ms(now_ms) >= self.timeout_ms:
            return TesterWaitState.TIMEOUT
        if valid_response_present:
            return TesterWaitState.RESPONSE_APPLIED
        return TesterWaitState.WAITING
