"""Deterministic request-importance classification for the OpenCode transport.

Isolated on purpose.  The routing decision is a *policy*, not transport, and it
must never become an extra model call: asking an AI which model to ask would add
a billed round trip, a second failure mode, and a non-reproducible input to a
pipeline whose whole identity contract is deterministic.  Everything below is a
pure function of signals the request already carries by the time the analyst
call is built.

The signals used, and why each one is already authoritative:

``workload_mode``
    Resolved by ``ai_gate._payload_workload_mode`` before any provider is
    touched and already fails safe to ``LIVE_FORWARD`` for an unclassified
    payload.  A backtest, replay, research or analytics request cannot place an
    order, so it is never worth the most expensive route.

``non_trading_shadow``
    The gate's own flag for a non-authoritative research repeat.  A shadow call
    has no trading authority by construction.

``rule_score``
    The deterministic 0..10 technical-quality score Python computes per
    candidate (``ai_gate._rule_score``) and already publishes on every candidate
    row in the evidence envelope.  It is the pipeline's own statement of which
    live setups are close to tradeable, so it is the correct place to spend the
    strongest model.  It is read here strictly as an *input to routing* and is
    never given trading authority.

Nothing here reads the model's output, and nothing here can change a decision:
the worst a misclassification can do is send a request to a different model,
whose answer is then validated by exactly the same strict schema and semantic
contract as every other answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

ROUTING_POLICY_VERSION = "20260909_opencode_importance_v1"

IMPORTANCE_NORMAL = "normal"
IMPORTANCE_IMPORTANT = "important"
IMPORTANCE_CRITICAL = "critical"

IMPORTANCE_LEVELS: tuple[str, ...] = (
    IMPORTANCE_NORMAL,
    IMPORTANCE_IMPORTANT,
    IMPORTANCE_CRITICAL,
)

# Mirrors ``runtime_governance.LIVE_FORWARD`` without importing it: this module
# is deliberately dependency-free so it can be unit tested and reused without
# dragging the governance surface in.  A parity test pins the two together.
LIVE_FORWARD = "LIVE_FORWARD"


@dataclass(frozen=True)
class OpenCodeRoutingPolicy:
    """Configurable, deterministic importance policy.

    ``call_directing`` off is the documented default: every request goes to the
    primary Muse route and the classifier is not consulted at all.
    """

    call_directing: bool = False
    critical_rule_score: float = 8.5
    important_rule_score: float = 0.0
    forced_importance: str = ""

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        warnings: list[str] | None = None,
    ) -> "OpenCodeRoutingPolicy":
        warn = warnings if warnings is not None else []

        def _bool(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None or str(raw).strip() == "":
                return default
            text = str(raw).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            warn.append(f"{name}=invalid_bool")
            return default

        def _float(name: str, default: float) -> float:
            raw = env.get(name)
            if raw is None or str(raw).strip() == "":
                return default
            try:
                value = float(str(raw).strip())
            except (TypeError, ValueError):
                warn.append(f"{name}=invalid_float")
                return default
            return min(10.0, max(0.0, value))

        forced = str(env.get("OPENCODE_IMPORTANCE_FORCE") or "").strip().lower()
        if forced and forced not in IMPORTANCE_LEVELS:
            warn.append("OPENCODE_IMPORTANCE_FORCE=invalid")
            forced = ""
        return cls(
            call_directing=_bool("OPENCODE_CALL_DIRECTING", False),
            critical_rule_score=_float("OPENCODE_CRITICAL_RULE_SCORE", 8.5),
            important_rule_score=_float("OPENCODE_IMPORTANT_RULE_SCORE", 0.0),
            forced_importance=forced,
        )

    def fingerprint(self) -> dict[str, Any]:
        """Stable description of the policy for identity and telemetry."""

        return {
            "routing_policy_version": ROUTING_POLICY_VERSION,
            "call_directing": self.call_directing,
            "critical_rule_score": self.critical_rule_score,
            "important_rule_score": self.important_rule_score,
            "forced_importance": self.forced_importance,
        }


@dataclass(frozen=True)
class ImportanceDecision:
    importance: str
    reason: str
    workload_mode: str
    non_trading_shadow: bool
    max_rule_score: float
    candidate_count: int

    def as_log_fields(self) -> str:
        return (
            f" importance={self.importance}"
            f" routing_reason={self.reason}"
            f" workload_mode={self.workload_mode}"
            f" non_trading_shadow={str(self.non_trading_shadow).lower()}"
            f" max_rule_score={self.max_rule_score:.4f}"
            f" candidate_count={self.candidate_count}"
        )


def max_candidate_rule_score(evidence: Mapping[str, Any] | None) -> tuple[float, int]:
    """Highest deterministic ``rule_score`` in the evidence, and the row count.

    Returns ``(-1.0, 0)`` when the field is genuinely absent.  A missing score
    is reported as missing rather than as zero, so "no score was published" can
    never be mistaken for "every candidate scored zero" -- the exact defect
    class recorded in section 4j of the project notes, where a structurally
    unpublished field printed as ``0.00`` and made a whole funnel stage
    undiagnosable.
    """

    rows = (evidence or {}).get("candidates")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return -1.0, 0
    best = -1.0
    count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        count += 1
        raw = row.get("rule_score")
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > best:
            best = value
    return best, count


def classify_importance(
    *,
    policy: OpenCodeRoutingPolicy,
    request_metadata: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> ImportanceDecision:
    """Classify one request deterministically.  No model call, ever."""

    metadata = dict(request_metadata or {})
    workload_mode = str(
        metadata.get("workload_mode") or (evidence or {}).get("workload_mode") or ""
    ).strip()
    non_trading_shadow = bool(metadata.get("non_trading_shadow"))
    max_rule_score, candidate_count = max_candidate_rule_score(evidence)

    def _decide(importance: str, reason: str) -> ImportanceDecision:
        return ImportanceDecision(
            importance=importance,
            reason=reason,
            workload_mode=workload_mode or "unknown",
            non_trading_shadow=non_trading_shadow,
            max_rule_score=max_rule_score,
            candidate_count=candidate_count,
        )

    if not policy.call_directing:
        return _decide(IMPORTANCE_NORMAL, "call_directing_disabled")
    if policy.forced_importance:
        return _decide(policy.forced_importance, "forced_by_configuration")
    if non_trading_shadow:
        return _decide(IMPORTANCE_NORMAL, "non_trading_shadow_repeat")
    if workload_mode != LIVE_FORWARD:
        # Everything that is not live-forward -- backtest, replay, research,
        # analytics -- cannot place an order.  An unclassified payload is
        # already normalised to LIVE_FORWARD upstream, so this branch never
        # downgrades an unknown request by accident.
        return _decide(IMPORTANCE_NORMAL, f"workload_mode_not_live:{workload_mode or 'unknown'}")
    if max_rule_score >= policy.critical_rule_score:
        return _decide(
            IMPORTANCE_CRITICAL,
            f"live_rule_score_at_or_above_critical:{policy.critical_rule_score:g}",
        )
    if max_rule_score >= policy.important_rule_score:
        return _decide(
            IMPORTANCE_IMPORTANT,
            f"live_rule_score_at_or_above_important:{policy.important_rule_score:g}",
        )
    # Live, but the deterministic score was never published.  Live requests are
    # never downgraded to the cheapest route on missing evidence: absent data is
    # treated as "at least important", which is the fail-safe direction here.
    return _decide(IMPORTANCE_IMPORTANT, "live_rule_score_unavailable")
