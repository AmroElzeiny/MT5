"""Python mirror of the MQL execution adjustment contract.

The authority split it encodes
-----------------------------
Three objects, deliberately distinct:

``AssessedTradePlan``
    What the AI assessed and Python approved.  Frozen the moment the approved
    target has been applied.  It owns request and candidate identity, direction,
    taxonomy, the entry and stop models, the assessed stop, the selected target
    identity/source/model/price, the obstacle identity and class, the assessed
    entry and TP levels, and the assessment fingerprint.

``ExecutionAdjustmentContract``
    The deterministic permission: exactly which of those values may differ at
    execution time, and by how much.

``LiveExecutionPlan``
    What the broker is actually asked to do, derived only through the contract.

Why it exists
-------------
The eleventh zero-trade run approved a plan (entry 4066.75, sl 4063.17,
tp2 4070.51, ``target_source=ai_selected_liquidity_target``,
``target_model=range_mid_opposite_side``), admitted it to the watchlist,
completed confirmation, and then failed all 91 execution attempts with::

    [execution_fingerprint] match=false stage=market
    changed_components=target_source,target_model,obstacle_kind,tp1,tp2

Nothing had invalidated the trade.  The rebuild re-derived the liquidity target
at the live price (resolving it to 4220.90 rather than the approved 4070.51),
blew the live distance cap, let the target sanitizer substitute
``synthetic_rr_fallback``, and then compared that materially different trade
against the immutable assessment fingerprint.  The integrity check was correct;
it was being handed a different trade.

This module is the Python side of the same contract, so both sides classify
identically.  ``tests/test_execution_adjustment_contract.py`` asserts the two
definitions have not drifted by parsing the MQL header directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

EXECUTION_ADJUSTMENT_CONTRACT_VERSION = "20260731_assessed_vs_execution_authority_v1"

# ---------------------------------------------------------------------------
# Target recalculation rules
# ---------------------------------------------------------------------------
# A structural level (liquidity pool, session range, opposing-imbalance cap) is
# a property of the market, so it does not move because our entry moved.  A
# synthetic target is defined as a multiple of risk, so it must be recomputed
# by the approved formula -- keeping the same model and source.
TARGET_RECALC_PRESERVE_FIXED_PRICE = "preserve_fixed_price"
TARGET_RECALC_DETERMINISTIC_RR = "deterministic_rr_from_entry"

TARGET_RECALCULATION_RULES: tuple[str, ...] = (
    TARGET_RECALC_PRESERVE_FIXED_PRICE,
    TARGET_RECALC_DETERMINISTIC_RR,
)

# ---------------------------------------------------------------------------
# Execution failure classification
# ---------------------------------------------------------------------------
EXEC_FAIL_NONE = "NONE"
EXEC_FAIL_TRANSIENT_QUOTE = "TRANSIENT_QUOTE_FAILURE"
EXEC_FAIL_TRANSIENT_SPREAD = "TRANSIENT_SPREAD_FAILURE"
EXEC_FAIL_TRANSIENT_BROKER = "TRANSIENT_BROKER_FAILURE"
EXEC_FAIL_SEMANTIC_PLAN_CHANGED = "SEMANTIC_PLAN_CHANGED"
EXEC_FAIL_STRUCTURAL_INVALIDATION = "STRUCTURAL_INVALIDATION"
EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE = "TARGET_NO_LONGER_FEASIBLE"
EXEC_FAIL_PERMANENT_BROKER = "PERMANENT_BROKER_CONSTRAINT"

EXECUTION_FAILURE_CLASSES: tuple[str, ...] = (
    EXEC_FAIL_NONE,
    EXEC_FAIL_TRANSIENT_QUOTE,
    EXEC_FAIL_TRANSIENT_SPREAD,
    EXEC_FAIL_TRANSIENT_BROKER,
    EXEC_FAIL_SEMANTIC_PLAN_CHANGED,
    EXEC_FAIL_STRUCTURAL_INVALIDATION,
    EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE,
    EXEC_FAIL_PERMANENT_BROKER,
)

TRANSIENT_FAILURE_CLASSES = frozenset(
    {EXEC_FAIL_TRANSIENT_QUOTE, EXEC_FAIL_TRANSIENT_SPREAD, EXEC_FAIL_TRANSIENT_BROKER}
)
TERMINAL_FAILURE_CLASSES = frozenset(
    {
        EXEC_FAIL_SEMANTIC_PLAN_CHANGED,
        EXEC_FAIL_STRUCTURAL_INVALIDATION,
        EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE,
        EXEC_FAIL_PERMANENT_BROKER,
    }
)

EXEC_ACTION_RETRY_BOUNDED = "retry_bounded_backoff"
EXEC_ACTION_TERMINAL_INVALIDATE = "terminal_invalidate_or_requeue_ai"
EXEC_ACTION_SUPPRESS = "suppress_until_input_changes"


def failure_is_transient(failure_class: str) -> bool:
    return failure_class in TRANSIENT_FAILURE_CLASSES


def failure_is_terminal(failure_class: str) -> bool:
    return failure_class in TERMINAL_FAILURE_CLASSES


def failure_action(failure_class: str) -> str:
    if failure_is_terminal(failure_class):
        return EXEC_ACTION_TERMINAL_INVALIDATE
    if failure_is_transient(failure_class):
        return EXEC_ACTION_RETRY_BOUNDED
    return EXEC_ACTION_SUPPRESS


# ---------------------------------------------------------------------------
# The semantic field partition
# ---------------------------------------------------------------------------
# Any difference in an immutable field means the live plan is a *different
# trade* from the approved one.  That is never a retryable execution failure:
# the setup must be terminally invalidated or requeued for a fresh assessment.
IMMUTABLE_SEMANTIC_FIELDS: tuple[str, ...] = (
    "candidate_hash",
    "candidate_id",
    "symbol",
    "direction",
    "setup_code",
    "setup_family",
    "setup_taxonomy_enum",
    "setup_taxonomy_version",
    "taxonomy_mapping_source",
    "entry_branch",
    "source_t_sweep",
    "source_t_disp",
    "source_t_bos",
    "target_source",
    "target_model",
    # The obstacle's IDENTITY, i.e. its kind with the live "crossed_" prefix
    # removed.  The prefix is not part of what the obstacle is: it records where
    # price currently sits relative to it, and MQL's _PublishObstacleEvidence
    # re-derives it on every plan rebuild.  It therefore flips exactly when price
    # travels into the entry zone -- the movement the watchlist is armed to wait
    # for -- so comparing the prefixed string as identity rejected plans for doing
    # what they were armed to do.  Three of the eleven 2026-09-05 approvals died
    # this way.  See OBSTACLE_CROSSING_PREFIX and base_obstacle_kind below.
    "obstacle_kind",
    "decision_input_hash",
    "strategy_schema_version",
    "selected_target_price",
)

# May differ, but only inside the contract's bounds.
ADJUSTABLE_FIELDS: tuple[str, ...] = (
    "entry",
    "sl",
    "tp1",
    "tp2",
    "obstacle_price",
    # Live-derived views of the same obstacle: where price sits relative to it,
    # and the timeframe label derived from its kind string.
    "obstacle_crossing_state",
    "obstacle_tf",
    "spread_r",
    "slippage_r",
    "execution_cost_r",
)

OBSTACLE_CROSSING_PREFIX = "crossed_"


def base_obstacle_kind(obstacle_kind: str) -> str:
    """The obstacle's identity, with the live crossing state removed."""

    if not obstacle_kind:
        return ""
    if obstacle_kind.startswith(OBSTACLE_CROSSING_PREFIX):
        return obstacle_kind[len(OBSTACLE_CROSSING_PREFIX) :]
    return obstacle_kind


def obstacle_is_crossed(obstacle_kind: str) -> bool:
    return bool(obstacle_kind) and obstacle_kind.startswith(OBSTACLE_CROSSING_PREFIX)


@dataclass(frozen=True)
class AssessedTradePlan:
    """The immutable record of what was approved."""

    request_id: str
    request_identity_hash: str
    candidate_id: str
    candidate_hash: str
    symbol: str
    is_buy: bool
    setup_taxonomy_enum: str
    entry_model: str
    stop_model: str
    selected_target_identity: str
    selected_target_source: str
    selected_target_model: str
    selected_target_price: float
    obstacle_kind: str
    obstacle_tf: str
    obstacle_price: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    assessment_fingerprint: str

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def net_rr(self) -> float:
        risk = self.stop_distance
        if risk <= 0.0:
            return 0.0
        reward = (self.tp2 - self.entry) if self.is_buy else (self.entry - self.tp2)
        return reward / risk


@dataclass(frozen=True)
class ExecutionAdjustmentContract:
    """Exactly what may move between assessment and execution."""

    contract_version: str = EXECUTION_ADJUSTMENT_CONTRACT_VERSION
    entry_adjustment_allowed: bool = True
    max_entry_drift_r: float = 0.0
    max_entry_drift_ticks: float = 2.0
    sl_adjustment_allowed: bool = True
    max_sl_drift_r: float = 0.10
    tp1_adjustment_allowed: bool = False
    tp2_adjustment_allowed: bool = False
    target_recalculation: str = TARGET_RECALC_PRESERVE_FIXED_PRICE
    target_identity_preserved: bool = True
    target_source_preserved: bool = True
    target_model_preserved: bool = True
    obstacle_revalidation_required: bool = True
    min_resulting_rr: float = 0.0
    max_target_distance: float = 0.0
    max_cost_deterioration_r: float = 0.02
    expiry: int = 0

    def max_entry_drift(self, *, stop_distance: float, tick_size: float) -> float:
        return max(stop_distance * self.max_entry_drift_r, tick_size * self.max_entry_drift_ticks)


def target_model_is_synthetic_rr(model: str) -> bool:
    """Mirror of ``CTradeEngine::_TargetModelIsSyntheticRr``."""
    return "synthetic_rr" in str(model or "").strip().lower()


def contract_for(
    plan: AssessedTradePlan,
    *,
    max_entry_drift_r: float,
    max_entry_drift_ticks: float,
    min_resulting_rr: float,
    max_target_distance: float,
    expiry: int = 0,
) -> ExecutionAdjustmentContract:
    synthetic = target_model_is_synthetic_rr(plan.selected_target_model)
    return ExecutionAdjustmentContract(
        max_entry_drift_r=max_entry_drift_r,
        max_entry_drift_ticks=max_entry_drift_ticks,
        # Only a synthetic fixed-RR target may be repriced, and only by the
        # approved formula.  A structural target keeps its price.
        tp1_adjustment_allowed=synthetic,
        tp2_adjustment_allowed=synthetic,
        target_recalculation=(
            TARGET_RECALC_DETERMINISTIC_RR if synthetic else TARGET_RECALC_PRESERVE_FIXED_PRICE
        ),
        min_resulting_rr=min_resulting_rr,
        max_target_distance=max_target_distance,
        expiry=expiry,
    )


@dataclass
class SemanticPlanMatchResult:
    semantic_match: bool = True
    adjustment_valid: bool = True
    immutable_fields_changed: list[str] = field(default_factory=list)
    authorized_fields_changed: list[str] = field(default_factory=list)
    unauthorized_fields_changed: list[str] = field(default_factory=list)
    adjustment_bounds: str = ""
    failure_class: str = EXEC_FAIL_NONE
    result: str = "pass"

    @property
    def ok(self) -> bool:
        return self.semantic_match and self.adjustment_valid

    @property
    def action(self) -> str:
        return failure_action(self.failure_class)


def evaluate_semantic_plan_match(
    plan: AssessedTradePlan,
    live: Mapping[str, object],
    contract: ExecutionAdjustmentContract,
    *,
    tick_size: float,
) -> SemanticPlanMatchResult:
    """Compare a live plan to its assessed plan in three buckets.

    ``live`` is a mapping of the same field names used in
    :data:`IMMUTABLE_SEMANTIC_FIELDS` / :data:`ADJUSTABLE_FIELDS`; absent keys
    are treated as unchanged, so a caller only supplies what it actually
    rebuilt.
    """

    out = SemanticPlanMatchResult()
    risk = plan.stop_distance
    price_tol = max(2.0 * tick_size, 0.05 * risk)
    entry_tol = contract.max_entry_drift(stop_distance=risk, tick_size=tick_size)
    sl_tol = max(2.0 * tick_size, risk * contract.max_sl_drift_r)

    out.adjustment_bounds = (
        f"entry_tol={entry_tol:.8f} sl_tol={sl_tol:.8f} price_tol={price_tol:.8f}"
        f" cost_tol_r={contract.max_cost_deterioration_r:.6f}"
        f" min_rr={contract.min_resulting_rr:.6f}"
        f" max_target_distance={contract.max_target_distance:.8f}"
    )

    assessed: dict[str, object] = {
        "candidate_hash": plan.candidate_hash,
        "candidate_id": plan.candidate_id,
        "symbol": plan.symbol,
        "direction": "BUY" if plan.is_buy else "SELL",
        "setup_taxonomy_enum": plan.setup_taxonomy_enum,
        "entry_branch": plan.entry_model,
        "target_source": plan.selected_target_source,
        "target_model": plan.selected_target_model,
    }
    for name, expected in assessed.items():
        if name in live and live[name] != expected:
            out.immutable_fields_changed.append(name)

    # The obstacle: base identity immutable, live crossing state authorized.
    if "obstacle_kind" in live:
        live_kind = str(live["obstacle_kind"])
        if base_obstacle_kind(live_kind) != base_obstacle_kind(plan.obstacle_kind):
            out.immutable_fields_changed.append("obstacle_kind")
        elif obstacle_is_crossed(live_kind) != obstacle_is_crossed(plan.obstacle_kind):
            out.authorized_fields_changed.append("obstacle_crossing_state")
    if "obstacle_tf" in live and str(live["obstacle_tf"]) != plan.obstacle_tf:
        # Derived from the same kind string, so with the base identity preserved a
        # difference is a derivation gap, not a different obstacle.
        if "obstacle_kind" not in out.immutable_fields_changed:
            out.authorized_fields_changed.append("obstacle_tf")

    # A structural target's price is immutable; a synthetic one is not.
    if not contract.tp2_adjustment_allowed and "tp2" in live:
        if abs(float(live["tp2"]) - plan.selected_target_price) > price_tol:  # type: ignore[arg-type]
            out.immutable_fields_changed.append("selected_target_price")

    def moved(name: str, assessed_value: float, tol: float, allowed: bool, code: str) -> None:
        if name not in live:
            return
        delta = abs(float(live[name]) - assessed_value)  # type: ignore[arg-type]
        if delta <= 0.0:
            return
        out.authorized_fields_changed.append(name)
        if not allowed or delta > tol:
            out.unauthorized_fields_changed.append(code)

    moved("entry", plan.entry, entry_tol, contract.entry_adjustment_allowed, "entry_drift_exceeds_contract")
    moved("sl", plan.sl, sl_tol, contract.sl_adjustment_allowed, "sl_drift_exceeds_contract")
    if "tp1" in live and abs(float(live["tp1"]) - plan.tp1) > price_tol:  # type: ignore[arg-type]
        out.authorized_fields_changed.append("tp1")
        if not contract.tp1_adjustment_allowed:
            out.unauthorized_fields_changed.append("tp1_changed_without_permission")
    if "tp2" in live and abs(float(live["tp2"]) - plan.tp2) > price_tol:  # type: ignore[arg-type]
        out.authorized_fields_changed.append("tp2")
        if not contract.tp2_adjustment_allowed:
            out.unauthorized_fields_changed.append("tp2_changed_without_permission")

    out.semantic_match = not out.immutable_fields_changed
    out.adjustment_valid = not out.unauthorized_fields_changed
    if not out.semantic_match:
        out.failure_class = EXEC_FAIL_SEMANTIC_PLAN_CHANGED
        out.result = "semantic_plan_changed"
    elif not out.adjustment_valid:
        out.failure_class = EXEC_FAIL_STRUCTURAL_INVALIDATION
        out.result = "adjustment_outside_contract"
    else:
        out.failure_class = EXEC_FAIL_NONE
        out.result = "pass"
    return out


# ---------------------------------------------------------------------------
# Canonical target feasibility
# ---------------------------------------------------------------------------


def price_distance_to_ticks(distance: float, tick_size: float) -> int:
    """Mirror of ``PriceDistanceToTicks``.

    The max-distance decision is made in whole ticks so a target capped exactly
    to the maximum passes deterministically rather than depending on which side
    accumulated the last binary rounding error.  The eleventh run logged
    ``reward=30.01 max_allowed=30.01 feasible=true`` from one validator and
    ``ai_chosen_target_exceeds_max_distance`` from another for the same plan.
    """

    if tick_size <= 0.0:
        return 0
    # Round half away from zero, matching MQL's MathRound.
    scaled = distance / tick_size
    return int(scaled + (0.5 if scaled >= 0 else -0.5))


def ticks_within_cap(reward_ticks: int, cap_ticks: int) -> bool:
    if cap_ticks <= 0:
        return True
    return reward_ticks <= cap_ticks


@dataclass
class TargetFeasibilityResult:
    feasible: bool = False
    reason: str = "unevaluated"
    authority: str = ""
    model: str = ""
    target_price: float = 0.0
    reward: float = 0.0
    risk: float = 0.0
    rr: float = 0.0
    max_allowed_distance: float = 0.0
    min_required_distance: float = 0.0
    min_required_rr: float = 0.0
    reward_ticks: int = 0
    max_allowed_ticks: int = 0
    direction_valid: bool = False
    target_reached: bool = False
    rr_floor_pass: bool = False
    max_distance_pass: bool = False
    min_distance_pass: bool = False


def evaluate_target_feasibility(
    *,
    is_buy: bool,
    entry: float,
    sl: float,
    target_price: float,
    tick_size: float,
    max_allowed_distance: float,
    min_required_distance: float = 0.0,
    min_required_rr: float = 0.0,
    current_price: float | None = None,
    model: str = "",
    authority: str = "canonical_target_feasibility",
    rr_epsilon: float = 1e-4,
) -> TargetFeasibilityResult:
    """The single implementation every stage reports through."""

    out = TargetFeasibilityResult(authority=authority, model=model or "unknown", target_price=target_price)
    if target_price <= 0.0 or entry <= 0.0 or sl <= 0.0:
        out.reason = "ai_chosen_target_missing_tp"
        return out
    risk = abs(entry - sl)
    out.risk = risk
    if risk <= 0.0:
        out.reason = "no_valid_stop"
        return out

    out.direction_valid = (target_price > entry) if is_buy else (target_price < entry)
    if not out.direction_valid:
        out.reason = "ai_chosen_target_invalid_direction"
        return out

    out.reward = (target_price - entry) if is_buy else (entry - target_price)
    if out.reward <= 0.0:
        out.reason = "liquidity_target_too_near"
        return out
    out.rr = out.reward / risk

    if current_price is not None and current_price > 0.0:
        eps = max(tick_size * 2.0, risk * 0.01)
        out.target_reached = (
            current_price >= target_price - eps if is_buy else current_price <= target_price + eps
        )
        if out.target_reached:
            out.reason = "ai_chosen_target_already_reached"
            return out

    out.max_allowed_distance = max_allowed_distance
    out.min_required_distance = min_required_distance
    out.min_required_rr = min_required_rr
    out.reward_ticks = price_distance_to_ticks(out.reward, tick_size)
    out.max_allowed_ticks = price_distance_to_ticks(max_allowed_distance, tick_size)

    out.max_distance_pass = max_allowed_distance <= 0.0 or ticks_within_cap(
        out.reward_ticks, out.max_allowed_ticks
    )
    if not out.max_distance_pass:
        out.reason = "ai_chosen_target_exceeds_max_distance"
        return out

    out.min_distance_pass = min_required_distance <= 0.0 or out.reward_ticks >= price_distance_to_ticks(
        min_required_distance, tick_size
    )
    if not out.min_distance_pass:
        out.reason = "target_too_close_for_swing_duration"
        return out

    out.rr_floor_pass = min_required_rr <= 0.0 or (out.rr + rr_epsilon >= min_required_rr)
    if not out.rr_floor_pass:
        out.reason = "rr_below_live_floor"
        return out

    out.feasible = True
    out.reason = "ok"
    return out


def derive_live_target(
    plan: AssessedTradePlan,
    contract: ExecutionAdjustmentContract,
    *,
    live_entry: float,
    live_sl: float,
) -> tuple[float, float]:
    """Return ``(tp1, tp2)`` for a live entry, through the contract only.

    This is the rule the MQL engine applies in
    ``_ApplyAssessedTargetUnderContract``.  A structural target keeps its price;
    a synthetic fixed-RR target is recomputed by the approved R multiple.
    """

    if contract.target_recalculation == TARGET_RECALC_DETERMINISTIC_RR:
        live_risk = abs(live_entry - live_sl)
        approved_rr = plan.net_rr
        if live_risk <= 0.0 or approved_rr <= 0.0:
            raise ValueError("assessed_rr_invalid_for_synthetic_recalculation")
        reward = live_risk * approved_rr
        tp2 = live_entry + reward if plan.is_buy else live_entry - reward
        assessed_reward = (plan.tp2 - plan.entry) if plan.is_buy else (plan.entry - plan.tp2)
        ratio = 0.0
        if assessed_reward > 0.0:
            tp1_reward = (plan.tp1 - plan.entry) if plan.is_buy else (plan.entry - plan.tp1)
            ratio = tp1_reward / assessed_reward
        if ratio <= 0.0 or ratio >= 1.0:
            ratio = 0.70
        tp1 = live_entry + reward * ratio if plan.is_buy else live_entry - reward * ratio
        return tp1, tp2
    return plan.tp1, plan.selected_target_price


# ---------------------------------------------------------------------------
# MQL parity
# ---------------------------------------------------------------------------

_MQL_HEADER = (
    Path(__file__).resolve().parent.parent
    / "MT5_PO3_Codex Include"
    / "ExecutionAdjustmentContract.mqh"
)


def mql_defines(path: Path | None = None) -> dict[str, str]:
    """Parse ``#define NAME "value"`` pairs out of the MQL contract header.

    Parity is asserted by reading the other side's source rather than by
    restating its values here, because a restated constant is exactly the kind
    of duplicate that drifted in the first place.
    """

    source = (path or _MQL_HEADER).read_text(encoding="utf-8", errors="replace")
    found: dict[str, str] = {}
    for name, value in re.findall(r'#define\s+(\w+)\s+"([^"]*)"', source):
        found[name] = value
    return found
