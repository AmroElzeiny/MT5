"""AI-side score/verdict authority.

The analyst still returns APPROVE / REJECT / ABSTAIN, but it no longer owns the
quality score that gates eligibility.  It returns eight independent contextual
judgements (``quality_components``); this module turns them into
``authoritative_ai_quality_score`` with fixed, Python-owned weights, resolves
the pattern-specific required score (base threshold + conservative adaptive
adjustment), classifies the verdict/score pair, and -- only on a genuine
contradiction -- asks the SCORE_VERDICT_ARBITER role to decide.

Authority boundaries:
  * AI-side eligibility only.  Deterministic setup validity, risk, portfolio,
    spread/execution, account locks, broker validation and every hard gate stay
    downstream and keep their own authority.
  * The score is never mapped to position size or risk percentage here.
  * Historical statistics reach the decision only through the required
    threshold; they are withheld from the analyst's evidence.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from structured_models import (
    QUALITY_COMPONENT_NAMES,
    SCORE_VERDICT_ARBITER_REASON_CODES,
    ModelScoreVerdictArbiterDecision,
)


SCORE_AUTHORITY_CONTRACT_VERSION = "20260915_score_verdict_authority_v1"
QUALITY_COMPONENT_WEIGHTS_VERSION = "20260915_quality_component_weights_v1"
ADAPTIVE_THRESHOLD_VERSION = "20260915_adaptive_pattern_threshold_v1"
TELEMETRY_SCHEMA_VERSION = "20260915_score_verdict_telemetry_v1"
ARBITER_ROLE = "score_verdict_arbiter"

# Weights are a Python-owned contract.  They sum to exactly 1.0 and are never
# shown to the analyst, so a model cannot tune components towards a cutoff.
QUALITY_COMPONENT_WEIGHTS: dict[str, float] = {
    "thesis_coherence": 0.20,
    "sequence_quality": 0.15,
    "context_regime_fit": 0.15,
    "entry_location_timing": 0.15,
    "liquidity_target_path": 0.125,
    "invalidation_quality": 0.075,
    "execution_robustness": 0.075,
    "evidence_coherence": 0.075,
}
if tuple(QUALITY_COMPONENT_WEIGHTS) != QUALITY_COMPONENT_NAMES:
    raise RuntimeError("quality_component_weights_do_not_match_schema_names")
if not math.isclose(math.fsum(QUALITY_COMPONENT_WEIGHTS.values()), 1.0, abs_tol=1e-12):
    raise RuntimeError("quality_component_weights_do_not_sum_to_one")

DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"
DECISION_ABSTAIN = "ABSTAIN"

CASE_A_APPROVE_SCORE_PASS = "A_APPROVE_SCORE_PASS"
CASE_B_REJECT_SCORE_FAIL = "B_REJECT_SCORE_FAIL"
CASE_C_APPROVE_SCORE_FAIL = "C_APPROVE_SCORE_FAIL"
CASE_D_REJECT_SCORE_PASS = "D_REJECT_SCORE_PASS"
CASE_E_ABSTAIN_SCORE_FAIL = "E_ABSTAIN_SCORE_FAIL"
CASE_F_ABSTAIN_SCORE_PASS = "F_ABSTAIN_SCORE_PASS"

_CONFLICT_TYPE_BY_CASE = {
    CASE_C_APPROVE_SCORE_FAIL: "APPROVE_BELOW_THRESHOLD",
    CASE_D_REJECT_SCORE_PASS: "REJECT_ABOVE_THRESHOLD",
    CASE_F_ABSTAIN_SCORE_PASS: "ABSTAIN_ABOVE_THRESHOLD",
}

# (conflict_type, final_verdict) -> (more_accurate_authority, score_assessment,
# verdict_assessment).  Any other combination is self-contradictory output.
ARBITER_COHERENCE: dict[tuple[str, str], tuple[str, str, str]] = {
    ("APPROVE_BELOW_THRESHOLD", DECISION_APPROVE): ("VERDICT", "UNDERSTATED", "ACCURATE"),
    ("APPROVE_BELOW_THRESHOLD", DECISION_REJECT): ("SCORE", "ACCURATE", "TOO_PERMISSIVE"),
    ("REJECT_ABOVE_THRESHOLD", DECISION_APPROVE): ("SCORE", "ACCURATE", "TOO_STRICT"),
    ("REJECT_ABOVE_THRESHOLD", DECISION_REJECT): ("VERDICT", "OVERSTATED", "ACCURATE"),
    ("ABSTAIN_ABOVE_THRESHOLD", DECISION_APPROVE): ("SCORE", "ACCURATE", "TOO_STRICT"),
    ("ABSTAIN_ABOVE_THRESHOLD", DECISION_REJECT): ("VERDICT", "OVERSTATED", "ACCURATE"),
}

# Evidence the analyst must not see: historical performance of the pattern.
_HISTORICAL_CANDIDATE_FIELDS = (
    "bucket_prior",
    "shadow_historical_evidence",
    "historical_evidence_state",
    "retrieved_analogue_ids",
)
_HISTORICAL_PATH_MARKERS = (
    ".bucket_prior",
    ".shadow_historical_evidence",
    ".historical_evidence_state",
    ".retrieved_analogue_ids",
    "historical_analogues",
)


def _canonical_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(lo, min(hi, value))


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# 1. Authoritative score
# ---------------------------------------------------------------------------


def validate_quality_components(components: Any) -> dict[str, float]:
    """Return the eight components as floats, or raise (fail closed)."""

    if not isinstance(components, Mapping):
        raise ValueError("quality_components_missing")
    keys = set(components)
    expected = set(QUALITY_COMPONENT_NAMES)
    if keys != expected:
        raise ValueError(
            "quality_components_keys_invalid:"
            f"missing={sorted(expected - keys)}:unknown={sorted(keys - expected)}"
        )
    values: dict[str, float] = {}
    for name in QUALITY_COMPONENT_NAMES:
        raw = components[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"quality_component_not_numeric:{name}")
        value = float(raw)
        if not math.isfinite(value) or value < 0.0 or value > 10.0:
            raise ValueError(f"quality_component_out_of_range:{name}")
        values[name] = value
    return values


def compute_authoritative_quality_score(components: Any) -> float:
    """Deterministic weighted score; the model's own estimate is never used."""

    values = validate_quality_components(components)
    score = math.fsum(QUALITY_COMPONENT_WEIGHTS[name] * values[name] for name in QUALITY_COMPONENT_NAMES)
    return round(max(0.0, min(10.0, score)), 6)


# ---------------------------------------------------------------------------
# 2. Decision matrix
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreVerdictMatrix:
    analyst_verdict: str
    authoritative_score: float
    required_score: float
    score_passed: bool
    case: str
    contradiction: bool
    conflict_type: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_score_verdict(verdict: str, score: float, required_score: float) -> ScoreVerdictMatrix:
    state = str(verdict or "").strip().upper()
    score_value = round(float(score), 6)
    required = round(float(required_score), 6)
    if not math.isfinite(score_value) or not math.isfinite(required):
        raise ValueError("score_verdict_matrix_non_finite")
    passed = score_value >= required
    if state == DECISION_APPROVE:
        case = CASE_A_APPROVE_SCORE_PASS if passed else CASE_C_APPROVE_SCORE_FAIL
    elif state == DECISION_REJECT:
        case = CASE_D_REJECT_SCORE_PASS if passed else CASE_B_REJECT_SCORE_FAIL
    elif state == DECISION_ABSTAIN:
        case = CASE_F_ABSTAIN_SCORE_PASS if passed else CASE_E_ABSTAIN_SCORE_FAIL
    else:
        raise ValueError(f"score_verdict_matrix_verdict_invalid:{state}")
    conflict = _CONFLICT_TYPE_BY_CASE.get(case, "")
    return ScoreVerdictMatrix(
        analyst_verdict=state,
        authoritative_score=score_value,
        required_score=required,
        score_passed=passed,
        case=case,
        contradiction=bool(conflict),
        conflict_type=conflict,
    )


def qualitative_gate_blocks(critic_verdict: str, adjudicator_verdict: str) -> bool:
    """Critic/adjudicator stay an independent AND on any AI-side approval.

    Blocked when the adjudicator upheld a block, or when the critic did not
    PASS and no adjudicator explicitly resolved every objection.
    """

    critic = str(critic_verdict or "").upper()
    adjudicator = str(adjudicator_verdict or "").upper()
    if adjudicator == "UPHOLD_BLOCK":
        return True
    return critic != "PASS" and adjudicator != "UPHOLD_APPROVE"


# ---------------------------------------------------------------------------
# 3 + 7. Pattern thresholds: configured base + conservative adaptation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdaptiveThresholdPolicy:
    enabled: bool = True
    min_samples: int = 20
    min_samples_for_reduction: int = 40
    prior_strength: float = 60.0
    r_scale: float = 0.5
    max_increase: float = 1.5
    max_decrease: float = 0.4
    min_profit_factor_for_reduction: float = 1.15
    min_positive_fold_rate_for_reduction: float = 0.60
    min_folds_for_reduction: int = 3
    folds: int = 4
    max_drawdown_r: float = 8.0
    degradation_tolerance_r: float = 0.10
    absolute_floor: float = 5.0
    absolute_ceiling: float = 9.5
    refresh_sec: float = 300.0

    @classmethod
    def from_env(cls) -> "AdaptiveThresholdPolicy":
        return cls(
            enabled=_env_bool("AI_SCORE_ADAPTIVE_THRESHOLD_ENABLE", True),
            min_samples=_env_int("AI_SCORE_ADAPTIVE_MIN_SAMPLES", 20, 5, 10000),
            min_samples_for_reduction=_env_int("AI_SCORE_ADAPTIVE_MIN_SAMPLES_REDUCTION", 40, 10, 10000),
            prior_strength=_env_float("AI_SCORE_ADAPTIVE_PRIOR_STRENGTH", 60.0, 1.0, 10000.0),
            r_scale=_env_float("AI_SCORE_ADAPTIVE_R_SCALE", 0.5, 0.05, 5.0),
            max_increase=_env_float("AI_SCORE_ADAPTIVE_MAX_INCREASE", 1.5, 0.0, 3.0),
            max_decrease=_env_float("AI_SCORE_ADAPTIVE_MAX_DECREASE", 0.4, 0.0, 1.0),
            min_profit_factor_for_reduction=_env_float("AI_SCORE_ADAPTIVE_MIN_PF_REDUCTION", 1.15, 1.0, 10.0),
            min_positive_fold_rate_for_reduction=_env_float("AI_SCORE_ADAPTIVE_MIN_FOLD_RATE_REDUCTION", 0.60, 0.5, 1.0),
            min_folds_for_reduction=_env_int("AI_SCORE_ADAPTIVE_MIN_FOLDS_REDUCTION", 3, 2, 20),
            folds=_env_int("AI_SCORE_ADAPTIVE_FOLDS", 4, 2, 20),
            max_drawdown_r=_env_float("AI_SCORE_ADAPTIVE_MAX_DRAWDOWN_R", 8.0, 0.5, 1000.0),
            degradation_tolerance_r=_env_float("AI_SCORE_ADAPTIVE_DEGRADATION_TOLERANCE_R", 0.10, 0.0, 5.0),
            absolute_floor=_env_float("AI_SCORE_THRESHOLD_ABSOLUTE_FLOOR", 5.0, 0.0, 10.0),
            absolute_ceiling=_env_float("AI_SCORE_THRESHOLD_ABSOLUTE_CEILING", 9.5, 0.0, 10.0),
            refresh_sec=_env_float("AI_SCORE_ADAPTIVE_REFRESH_SEC", 300.0, 0.0, 86400.0),
        )

    def policy_hash(self) -> str:
        return _canonical_hash({"version": ADAPTIVE_THRESHOLD_VERSION, **asdict(self)})[:16]


@dataclass(frozen=True)
class PatternStatistics:
    """Realized statistics for one pattern, measured chronologically."""

    pattern_key: str
    sample_size: int
    expectancy_r: float
    profit_factor: float
    max_drawdown_r: float
    folds: int
    positive_fold_rate: float
    fold_expectancy_std_r: float
    recent_fold_expectancy_r: float
    degradation_r: float

    def as_dict(self) -> dict[str, Any]:
        return {key: (round(value, 6) if isinstance(value, float) else value) for key, value in asdict(self).items()}


def _max_drawdown_r(values: Sequence[float]) -> float:
    # Same equity-curve definition as expectancy_report._max_drawdown_r.
    try:
        from expectancy_report import _max_drawdown_r as report_drawdown

        return float(report_drawdown(list(values)))
    except Exception:
        equity = peak = drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return drawdown


def compute_pattern_statistics(
    pattern_key: str,
    records: Iterable[Mapping[str, Any]],
    *,
    folds: int = 4,
) -> PatternStatistics:
    """Chronological fold statistics from ``{"time": int, "r": float}`` rows.

    Each fold is judged only on trades that closed inside it, so a fold's
    expectancy is out-of-sample with respect to every earlier fold.
    """

    rows = sorted(
        (
            (int(row.get("time") or 0), float(row["r"]))
            for row in records
            if isinstance(row, Mapping)
            and isinstance(row.get("r"), (int, float))
            and not isinstance(row.get("r"), bool)
            and math.isfinite(float(row["r"]))
        ),
        key=lambda item: item[0],
    )
    rs = [value for _, value in rows]
    count = len(rs)
    if count == 0:
        return PatternStatistics(pattern_key, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0)
    gross_win = math.fsum(value for value in rs if value > 0.0)
    gross_loss = abs(math.fsum(value for value in rs if value < 0.0))
    profit_factor = gross_win / gross_loss if gross_loss > 0.0 else (10.0 if gross_win > 0.0 else 0.0)
    expectancy = math.fsum(rs) / count
    fold_count = max(1, min(int(folds), count // 5 if count >= 10 else 1))
    size = int(math.ceil(count / fold_count))
    fold_means = [
        math.fsum(rs[start : start + size]) / len(rs[start : start + size])
        for start in range(0, count, size)
        if rs[start : start + size]
    ]
    positive = sum(1 for value in fold_means if value > 0.0)
    fold_std = statistics.pstdev(fold_means) if len(fold_means) >= 2 else 0.0
    recent = fold_means[-1]
    degradation = max(0.0, expectancy - recent) if len(fold_means) >= 2 else 0.0
    return PatternStatistics(
        pattern_key=pattern_key,
        sample_size=count,
        expectancy_r=expectancy,
        profit_factor=profit_factor,
        max_drawdown_r=_max_drawdown_r(rs),
        folds=len(fold_means),
        positive_fold_rate=positive / len(fold_means),
        fold_expectancy_std_r=fold_std,
        recent_fold_expectancy_r=recent,
        degradation_r=degradation,
    )


@dataclass(frozen=True)
class ThresholdResolution:
    pattern_key: str
    base_threshold: float
    base_source: str
    required_threshold: float
    adjustment: float
    source: str
    version: str
    policy_hash: str
    shrinkage_weight: float
    statistics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_required_pattern_score(
    *,
    pattern_key: str,
    base_threshold: float,
    base_source: str,
    stats: PatternStatistics | None,
    policy: AdaptiveThresholdPolicy,
) -> ThresholdResolution:
    """Base threshold plus a shrunk, asymmetric, bounded adjustment.

    n  = realized sample size for the pattern
    w  = n / (n + prior_strength)                         (shrinkage to base)
    weakness in [0, 1]:
        clamp(-expectancy_r / r_scale, 0, 1)
      + 0.25 if profit_factor < 1.0
      + 0.25 if positive_fold_rate < 0.5
      + clamp(degradation_r / r_scale, 0, 0.5)
      + 0.25 if max_drawdown_r > max_drawdown_r_limit
    increase = max_increase * w * clamp(weakness, 0, 1)
    decrease = max_decrease * w * clamp(expectancy_r / r_scale, 0, 1)
               * clamp(1 - fold_std / r_scale, 0, 1)
               only when every reduction condition holds (n, PF, fold rate,
               folds, drawdown, no recent degradation)
    required = clamp(base + increase - decrease,
                     max(absolute_floor, base - max_decrease),
                     min(absolute_ceiling, base + max_increase))
    """

    base = max(0.0, min(10.0, float(base_threshold)))
    version = ADAPTIVE_THRESHOLD_VERSION
    policy_hash = policy.policy_hash()
    lower = max(policy.absolute_floor, base - policy.max_decrease)
    upper = min(policy.absolute_ceiling, base + policy.max_increase)
    if lower > base:
        lower = base
    if upper < base:
        upper = base

    def result(required: float, adjustment: float, source: str, weight: float) -> ThresholdResolution:
        return ThresholdResolution(
            pattern_key=pattern_key,
            base_threshold=round(base, 6),
            base_source=base_source,
            required_threshold=round(required, 6),
            adjustment=round(adjustment, 6),
            source=source,
            version=version,
            policy_hash=policy_hash,
            shrinkage_weight=round(weight, 6),
            statistics=stats.as_dict() if stats is not None else {},
        )

    if not policy.enabled:
        return result(base, 0.0, f"{base_source}+adaptive_disabled", 0.0)
    if stats is None or stats.sample_size < policy.min_samples:
        return result(base, 0.0, f"{base_source}+adaptive_insufficient_sample", 0.0)

    n = float(stats.sample_size)
    weight = n / (n + policy.prior_strength)
    scale = policy.r_scale
    weakness = max(0.0, min(1.0, -stats.expectancy_r / scale))
    if stats.profit_factor < 1.0:
        weakness += 0.25
    if stats.positive_fold_rate < 0.5:
        weakness += 0.25
    weakness += max(0.0, min(0.5, stats.degradation_r / scale))
    if stats.max_drawdown_r > policy.max_drawdown_r:
        weakness += 0.25
    weakness = max(0.0, min(1.0, weakness))
    increase = policy.max_increase * weight * weakness

    reduction_allowed = bool(
        stats.expectancy_r > 0.0
        and stats.sample_size >= policy.min_samples_for_reduction
        and stats.profit_factor >= policy.min_profit_factor_for_reduction
        and stats.positive_fold_rate >= policy.min_positive_fold_rate_for_reduction
        and stats.folds >= policy.min_folds_for_reduction
        and stats.max_drawdown_r <= policy.max_drawdown_r
        and stats.degradation_r <= policy.degradation_tolerance_r
    )
    decrease = 0.0
    if reduction_allowed:
        strength = max(0.0, min(1.0, stats.expectancy_r / scale))
        stability = max(0.0, min(1.0, 1.0 - stats.fold_expectancy_std_r / scale))
        decrease = policy.max_decrease * weight * strength * stability

    adjustment = increase - decrease
    required = max(lower, min(upper, base + adjustment))
    if adjustment > 0.0:
        source = f"{base_source}+adaptive_increase"
    elif adjustment < 0.0:
        source = f"{base_source}+adaptive_reduction"
    else:
        source = f"{base_source}+adaptive_neutral"
    return result(required, required - base, source, weight)


# ---------------------------------------------------------------------------
# Realized outcomes: telemetry joined to the completed-trade ledger
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _first_number(row: Mapping[str, Any], names: Sequence[str]) -> float | None:
    for name in names:
        value = row.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                continue
            if math.isfinite(parsed):
                return parsed
    return None


def normalize_outcome_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    r_value = _first_number(row, ("full_close_r", "net_realized_r", "realized_r", "result_r_initial_risk"))
    if r_value is None:
        return None
    return {
        "request_id": str(row.get("request_id") or row.get("ai_request_id") or ""),
        "candidate_hash": str(row.get("candidate_hash") or row.get("selected_candidate_hash") or ""),
        "pattern_key": str(
            row.get("setup_taxonomy_enum") or row.get("setup_taxonomy") or row.get("setup_family") or ""
        ).upper(),
        "r": r_value,
        "mae_r": _first_number(row, ("mae_r",)),
        "mfe_r": _first_number(row, ("mfe_r",)),
        "time": int(_first_number(row, ("full_close_time", "close_time", "closed_at", "exit_time", "time")) or 0),
    }


def join_telemetry_outcomes(
    telemetry_rows: Iterable[Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach realized R/MAE/MFE to telemetry by (request_id, candidate_hash)."""

    outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    by_hash: dict[str, dict[str, Any]] = {}
    for raw in outcome_rows:
        normalized = normalize_outcome_row(raw)
        if normalized is None:
            continue
        outcomes[(normalized["request_id"], normalized["candidate_hash"])] = normalized
        if normalized["candidate_hash"]:
            by_hash[normalized["candidate_hash"]] = normalized
    joined: list[dict[str, Any]] = []
    for raw in telemetry_rows:
        row = dict(raw)
        key = (str(row.get("request_id") or ""), str(row.get("candidate_hash") or ""))
        outcome = outcomes.get(key) or by_hash.get(key[1])
        row["outcome_available"] = outcome is not None
        row["realized_r"] = outcome["r"] if outcome else None
        row["mae_r"] = outcome["mae_r"] if outcome else None
        row["mfe_r"] = outcome["mfe_r"] if outcome else None
        row["outcome_time"] = outcome["time"] if outcome else None
        joined.append(row)
    return joined


class PatternStatisticsSource:
    """Pattern statistics refreshed at most every ``policy.refresh_sec``."""

    def __init__(
        self,
        telemetry_path: Callable[[], Path | None],
        outcomes_path: Callable[[], Path | None],
        policy: AdaptiveThresholdPolicy,
    ) -> None:
        self._telemetry_path = telemetry_path
        self._outcomes_path = outcomes_path
        self._policy = policy
        self._lock = threading.Lock()
        self._loaded_at = -1.0
        self._signature: tuple[Any, ...] = ()
        self._by_pattern: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _file_signature(path: Path | None) -> tuple[Any, ...]:
        if path is None or not path.exists():
            return (str(path), None)
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns)

    def _refresh(self) -> None:
        telemetry = self._telemetry_path()
        outcomes = self._outcomes_path()
        signature = (self._file_signature(telemetry), self._file_signature(outcomes))
        now = time.monotonic()
        if self._loaded_at >= 0.0 and (
            signature == self._signature or now - self._loaded_at < self._policy.refresh_sec
        ):
            return
        by_pattern: dict[str, list[dict[str, Any]]] = {}
        seen: set[tuple[str, str]] = set()
        for row in join_telemetry_outcomes(_read_jsonl(telemetry), _read_jsonl(outcomes)):
            # Only trades Python actually allowed can have realized outcomes.
            if not row.get("outcome_available") or not row.get("python_final_allow"):
                continue
            key = (str(row.get("request_id") or ""), str(row.get("candidate_hash") or ""))
            if key in seen:
                continue
            seen.add(key)
            pattern = str(row.get("pattern_key") or "").upper()
            if not pattern:
                continue
            by_pattern.setdefault(pattern, []).append(
                {"time": int(row.get("outcome_time") or row.get("decided_at") or 0), "r": float(row["realized_r"])}
            )
        self._by_pattern = by_pattern
        self._signature = signature
        self._loaded_at = now

    def statistics_for(self, pattern_key: str) -> PatternStatistics:
        key = str(pattern_key or "").upper()
        with self._lock:
            try:
                self._refresh()
            except OSError:
                self._by_pattern = {}
            rows = list(self._by_pattern.get(key, []))
        return compute_pattern_statistics(key, rows, folds=self._policy.folds)


# ---------------------------------------------------------------------------
# 6. Analyst evidence: keep it on the current trade
# ---------------------------------------------------------------------------


def withhold_historical_performance_for_analyst(model_payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[int]]:
    """Return a copy of the analyst evidence without pattern performance history.

    Removes bucket priors, shadow outcome history, analogue outcomes and the
    related interpretation rules, drops their catalog rows and removes their ids
    from every candidate's allowed citation set.  The canonical envelope and
    catalog are untouched, so other roles and Python keep their evidence.
    """

    payload = dict(model_payload)
    withheld_ids: list[int] = []
    catalog = payload.get("evidence_catalog")
    if isinstance(catalog, Mapping) and isinstance(catalog.get("items"), list):
        kept: list[Any] = []
        for row in catalog["items"]:
            path = str(row.get("p") or "") if isinstance(row, Mapping) else ""
            if path and any(marker in path for marker in _HISTORICAL_PATH_MARKERS):
                try:
                    withheld_ids.append(int(row.get("id")))
                except (TypeError, ValueError):
                    pass
                continue
            kept.append(row)
        new_catalog = dict(catalog)
        new_catalog["items"] = kept
        payload["evidence_catalog"] = new_catalog
    withheld = set(withheld_ids)
    payload.pop("historical_analogues", None)
    section = payload.get("entry_and_invalidation")
    if isinstance(section, Mapping) and isinstance(section.get("candidates"), list):
        rows: list[Any] = []
        for row in section["candidates"]:
            if not isinstance(row, Mapping):
                rows.append(row)
                continue
            slim = {key: value for key, value in row.items() if key not in _HISTORICAL_CANDIDATE_FIELDS}
            allowed = slim.get("allowed_evidence_ref_ids")
            if isinstance(allowed, list):
                slim["allowed_evidence_ref_ids"] = [value for value in allowed if value not in withheld]
            rows.append(slim)
        new_section = dict(section)
        new_section["candidates"] = rows
        payload["entry_and_invalidation"] = new_section
    context = payload.get("provider_decision_context")
    if isinstance(context, Mapping) and isinstance(context.get("rules"), list):
        new_context = dict(context)
        new_context["rules"] = [
            rule for rule in context["rules"] if "historical" not in str(rule).lower()
        ]
        payload["provider_decision_context"] = new_context
    return payload, sorted(withheld)


# ---------------------------------------------------------------------------
# 5 + 10. SCORE_VERDICT_ARBITER
# ---------------------------------------------------------------------------


SCORE_VERDICT_ARBITER_PROMPT = (
    "You are the SCORE_VERDICT_ARBITER in a disciplined PO3 + FVG trade audit. "
    "The Analyst's verdict and a Python-computed contextual quality score disagree about one candidate. "
    "score_verdict_conflict states the Analyst verdict, the eight Analyst component judgements, the Python "
    "weights, the authoritative weighted score, and the pattern's required score. Decide which authority was "
    "more accurate for THIS candidate using only the supplied current-trade evidence, the Analyst reasoning, "
    "the Critic findings and the Adjudicator result. You do not produce a replacement score, prices, SL, TP, "
    "probability, expectancy or size, and you cannot override deterministic, risk, broker or integrity gates. "
    "An eventual APPROVE still has to pass every independent gate after you, so decide on the merits of the "
    "evidence, never to be helpful or cautious by default. "
    "Return exactly one coherent combination for the stated conflict_type: "
    "APPROVE_BELOW_THRESHOLD with final_verdict=APPROVE -> more_accurate_authority=VERDICT, "
    "score_assessment=UNDERSTATED, verdict_assessment=ACCURATE; APPROVE_BELOW_THRESHOLD with final_verdict=REJECT "
    "-> SCORE, ACCURATE, TOO_PERMISSIVE; REJECT_ABOVE_THRESHOLD or ABSTAIN_ABOVE_THRESHOLD with "
    "final_verdict=APPROVE -> SCORE, ACCURATE, TOO_STRICT; REJECT_ABOVE_THRESHOLD or ABSTAIN_ABOVE_THRESHOLD with "
    "final_verdict=REJECT -> VERDICT, OVERSTATED, ACCURATE. Any other combination is discarded. "
    "conflict_type must be copied exactly from score_verdict_conflict.conflict_type and candidate_index from "
    "score_verdict_conflict.candidate_index. reason_codes are 1 to 6 distinct values from the schema enum. "
    "evidence_ref_ids must contain 1 to 16 distinct integers copied only from allowed_evidence_ref_ids; never "
    "invent an id or return a path. summary is at most 240 ASCII characters. Python owns all request, "
    "candidate, provider, model and schema identity. Return strict JSON only and no hidden reasoning."
)


class ScoreVerdictArbiterMemo:
    """Contradiction fingerprint -> validated arbiter decision.

    In-process dictionary backed by an append-only JSONL file, so the same
    contradiction (same request, candidate, analyst output, components,
    threshold, critic and adjudicator) never buys a second provider call --
    within a request, across retries, or after a restart.
    """

    def __init__(self, path_factory: Callable[[], Path | None]) -> None:
        self._path_factory = path_factory
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._loaded_path: str | None = None

    def _ensure_loaded(self) -> Path | None:
        path = self._path_factory()
        if str(path) != self._loaded_path:
            self._entries = {}
            for row in _read_jsonl(path):
                if row.get("score_authority_contract_version") != SCORE_AUTHORITY_CONTRACT_VERSION:
                    continue
                fingerprint = str(row.get("contradiction_fingerprint") or "")
                decision = row.get("decision")
                if fingerprint and isinstance(decision, dict):
                    self._entries[fingerprint] = decision
            self._loaded_path = str(path)
        return path

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_loaded()
            decision = self._entries.get(fingerprint)
            return dict(decision) if decision is not None else None

    def put(self, fingerprint: str, decision: Mapping[str, Any]) -> None:
        with self._lock:
            path = self._ensure_loaded()
            self._entries[fingerprint] = dict(decision)
            if path is None:
                return
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "score_authority_contract_version": SCORE_AUTHORITY_CONTRACT_VERSION,
                                "contradiction_fingerprint": fingerprint,
                                "stored_at": int(time.time()),
                                "decision": dict(decision),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        )
                        + "\n"
                    )
            except OSError:
                # The in-process entry still prevents a duplicate call in this
                # process; persistence failure is not an authority change.
                pass


@dataclass(frozen=True)
class ArbiterOutcome:
    decision: dict[str, Any]
    fingerprint: str
    memo_hit: bool
    provider_result: Any = None


def contradiction_fingerprint(
    *,
    request_identity_hash: str,
    candidate_hash: str,
    analyst_assessment: Mapping[str, Any],
    matrix: ScoreVerdictMatrix,
    threshold: ThresholdResolution,
    critic: Mapping[str, Any],
    adjudicator: Mapping[str, Any],
    provider_id: str,
    model_id: str,
) -> str:
    return _canonical_hash(
        {
            "contract": SCORE_AUTHORITY_CONTRACT_VERSION,
            "weights": QUALITY_COMPONENT_WEIGHTS_VERSION,
            "request_identity_hash": str(request_identity_hash),
            "candidate_hash": str(candidate_hash),
            "analyst": _canonical_hash(dict(analyst_assessment)),
            "matrix": matrix.as_dict(),
            "required_score": threshold.required_threshold,
            "threshold_version": threshold.version,
            "critic": _canonical_hash(dict(critic)),
            "adjudicator": _canonical_hash(dict(adjudicator or {})),
            "provider_id": str(provider_id),
            "model_id": str(model_id),
        }
    )


def _candidate_evidence_row(evidence: Mapping[str, Any], candidate_index: int) -> dict[str, Any]:
    section = evidence.get("entry_and_invalidation")
    rows = section.get("candidates") if isinstance(section, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("score_verdict_arbiter_candidate_evidence_missing")
    for position, row in enumerate(rows):
        if isinstance(row, Mapping) and int(row.get("candidate_index", position)) == candidate_index:
            return {key: value for key, value in row.items() if key not in _HISTORICAL_CANDIDATE_FIELDS}
    raise ValueError("score_verdict_arbiter_candidate_evidence_missing")


def build_arbiter_evidence(
    *,
    evidence: Mapping[str, Any],
    evidence_catalog: Any,
    analyst_assessment: Mapping[str, Any],
    matrix: ScoreVerdictMatrix,
    threshold: ThresholdResolution,
    critic: Mapping[str, Any],
    adjudicator: Mapping[str, Any],
    setup_identity: Mapping[str, Any],
    request_metadata: Mapping[str, Any],
) -> tuple[dict[str, Any], list[int]]:
    candidate_index = int(analyst_assessment.get("candidate_index", -1))
    rows = [
        row
        for row in evidence_catalog.provider_rows()
        if row.get("c") in (None, candidate_index)
        and not any(marker in str(row.get("p") or "") for marker in _HISTORICAL_PATH_MARKERS)
    ]
    allowed = [int(row["id"]) for row in rows]
    veto = analyst_assessment.get("veto") if isinstance(analyst_assessment.get("veto"), Mapping) else {}
    payload = {
        "request_id": str(request_metadata.get("request_id") or ""),
        "request_identity_hash": str(request_metadata.get("request_identity_hash") or ""),
        "setup_identity": dict(setup_identity),
        "candidate": _candidate_evidence_row(evidence, candidate_index),
        "market_regime": evidence.get("market_regime"),
        "sequence": evidence.get("sequence"),
        "liquidity": evidence.get("liquidity"),
        "targets_and_obstacles": evidence.get("targets_and_obstacles"),
        "validation": evidence.get("validation"),
        "score_verdict_conflict": {
            "candidate_index": candidate_index,
            "conflict_type": matrix.conflict_type,
            "analyst_verdict": matrix.analyst_verdict,
            "quality_components": dict(analyst_assessment.get("quality_components") or {}),
            "quality_component_weights": dict(QUALITY_COMPONENT_WEIGHTS),
            "authoritative_ai_quality_score": matrix.authoritative_score,
            "required_pattern_score": threshold.required_threshold,
            "score_passed": matrix.score_passed,
        },
        "analyst": {
            "verdict": str(analyst_assessment.get("decision_state") or ""),
            "thesis_supported": bool(analyst_assessment.get("thesis_supported")),
            "confidence_band": str(analyst_assessment.get("confidence_band") or ""),
            "summary": str(analyst_assessment.get("summary") or ""),
            "reasons": str(analyst_assessment.get("reasons") or ""),
            "material_contradictions": list(analyst_assessment.get("material_contradictions") or []),
            "missing_required_evidence": list(analyst_assessment.get("missing_required_evidence") or []),
            "major_risks": list(analyst_assessment.get("major_risks") or []),
            "evidence_refs": list(analyst_assessment.get("evidence_refs") or []),
            "veto": {
                "enabled": bool(veto.get("enabled")),
                "code": str(veto.get("code") or ""),
                "reason": str(veto.get("reason") or ""),
                "evidence_fields": list(veto.get("evidence_fields") or []),
            },
        },
        "critic": dict(critic),
        "adjudicator": dict(adjudicator or {}),
        "evidence_catalog": {
            "catalog_version": evidence_catalog.catalog_version,
            "catalog_hash": evidence_catalog.catalog_hash,
            "items": rows,
        },
        "allowed_evidence_ref_ids": allowed,
    }
    return payload, allowed


def validate_arbiter_output(
    model_output: Mapping[str, Any],
    *,
    matrix: ScoreVerdictMatrix,
    candidate_index: int,
    allowed_ids: Sequence[int],
    evidence_catalog: Any,
) -> list[str]:
    """Raise ValueError on any malformed/mismatched output; return resolved refs."""

    if int(model_output.get("candidate_index", -1)) != candidate_index:
        raise ValueError("score_verdict_arbiter_candidate_index_mismatch")
    conflict = str(model_output.get("conflict_type") or "")
    if not matrix.contradiction or conflict != matrix.conflict_type:
        raise ValueError("score_verdict_arbiter_conflict_type_mismatch")
    final = str(model_output.get("final_verdict") or "")
    expected = ARBITER_COHERENCE.get((conflict, final))
    if expected is None:
        raise ValueError("score_verdict_arbiter_final_verdict_invalid")
    actual = (
        str(model_output.get("more_accurate_authority") or ""),
        str(model_output.get("score_assessment") or ""),
        str(model_output.get("verdict_assessment") or ""),
    )
    if actual != expected:
        raise ValueError("score_verdict_arbiter_incoherent_assessment")
    codes = list(model_output.get("reason_codes") or [])
    if not codes or len(set(codes)) != len(codes) or any(code not in SCORE_VERDICT_ARBITER_REASON_CODES for code in codes):
        raise ValueError("score_verdict_arbiter_reason_codes_invalid")
    ids = model_output.get("evidence_ref_ids")
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        raise ValueError("score_verdict_arbiter_evidence_refs_invalid")
    allowed = set(int(value) for value in allowed_ids)
    if any(isinstance(value, bool) or not isinstance(value, int) or value not in allowed for value in ids):
        raise ValueError("score_verdict_arbiter_evidence_refs_invalid")
    resolution = evidence_catalog.resolve(list(ids), candidate_index=candidate_index)
    if not resolution.valid:
        raise ValueError("score_verdict_arbiter_evidence_refs_invalid")
    return list(resolution.resolved_paths)


def run_score_verdict_arbiter(
    *,
    provider: Any,
    evidence: Mapping[str, Any],
    evidence_catalog: Any,
    analyst_assessment: Mapping[str, Any],
    matrix: ScoreVerdictMatrix,
    threshold: ThresholdResolution,
    critic: Mapping[str, Any],
    adjudicator: Mapping[str, Any],
    setup_identity: Mapping[str, Any],
    request_metadata: Mapping[str, Any],
    memo: ScoreVerdictArbiterMemo | None,
    event_logger: Callable[[str], None] | None = None,
) -> ArbiterOutcome:
    if not matrix.contradiction:
        raise ValueError("score_verdict_arbiter_called_without_contradiction")
    request_id = str(request_metadata.get("request_id") or "")
    request_identity_hash = str(request_metadata.get("request_identity_hash") or "")
    if not request_id or not request_identity_hash:
        raise ValueError("score_verdict_arbiter_request_identity_missing")
    candidate_index = int(analyst_assessment.get("candidate_index", -1))
    candidate_hash = str(analyst_assessment.get("candidate_hash") or "")
    candidate_id = str(analyst_assessment.get("candidate_id") or "")
    if candidate_index < 0 or not candidate_hash:
        raise ValueError("score_verdict_arbiter_candidate_identity_missing")
    try:
        configured_model = str(provider.model_for_role(ARBITER_ROLE) or "")
    except Exception:
        configured_model = ""
    fingerprint = contradiction_fingerprint(
        request_identity_hash=request_identity_hash,
        candidate_hash=candidate_hash,
        analyst_assessment=analyst_assessment,
        matrix=matrix,
        threshold=threshold,
        critic=critic,
        adjudicator=adjudicator,
        provider_id=str(getattr(provider, "provider_id", "") or ""),
        model_id=configured_model,
    )
    if memo is not None:
        cached = memo.get(fingerprint)
        if cached is not None:
            if (
                cached.get("request_identity_hash") != request_identity_hash
                or cached.get("candidate_hash") != candidate_hash
                or cached.get("conflict_type") != matrix.conflict_type
            ):
                raise ValueError("score_verdict_arbiter_memo_binding_mismatch")
            if event_logger is not None:
                event_logger(
                    "[score_verdict_arbiter] memo_hit=true provider_call=false"
                    f" request_id={request_id} fingerprint={fingerprint[:16]}"
                    f" final_verdict={cached.get('final_verdict')}"
                )
            return ArbiterOutcome(decision=cached, fingerprint=fingerprint, memo_hit=True)

    arbiter_evidence, allowed = build_arbiter_evidence(
        evidence=evidence,
        evidence_catalog=evidence_catalog,
        analyst_assessment=analyst_assessment,
        matrix=matrix,
        threshold=threshold,
        critic=critic,
        adjudicator=adjudicator,
        setup_identity=setup_identity,
        request_metadata=request_metadata,
    )
    prompt = SCORE_VERDICT_ARBITER_PROMPT + f" For this candidate the complete allowed id set is: {allowed}."
    if event_logger is not None:
        event_logger(
            "[score_verdict_arbiter] provider_call=true"
            f" request_id={request_id} conflict_type={matrix.conflict_type}"
            f" score={matrix.authoritative_score:.4f} required={threshold.required_threshold:.4f}"
            f" fingerprint={fingerprint[:16]}"
        )
    def _accepted_output(candidate_result: Any) -> tuple[dict[str, Any], list[str]]:
        parsed = candidate_result.parsed
        output = dict(parsed.model_dump()) if hasattr(parsed, "model_dump") else dict(parsed or {})
        ModelScoreVerdictArbiterDecision.model_validate(output)
        refs = validate_arbiter_output(
            output,
            matrix=matrix,
            candidate_index=candidate_index,
            allowed_ids=allowed,
            evidence_catalog=evidence_catalog,
        )
        return output, refs

    call_metadata = dict(request_metadata)
    if getattr(provider, "supports_result_validator", False):
        # A routed provider runs the same arbiter contract on every leg's
        # answer, so a coherent-looking but invalid Muse output falls back to
        # the next leg (GLM, then Luna) instead of ending the call.  The check
        # below still runs on whatever answer comes back.
        from ai_provider import ROUTED_RESULT_VALIDATOR_METADATA_KEY

        call_metadata[ROUTED_RESULT_VALIDATOR_METADATA_KEY] = _accepted_output
    result = provider.generate_structured(
        role=ARBITER_ROLE,
        system_prompt=prompt,
        evidence=arbiter_evidence,
        response_schema=ModelScoreVerdictArbiterDecision,
        request_metadata=call_metadata,
    )
    deadline = request_metadata.get("deadline")
    if deadline is not None and hasattr(deadline, "expired") and deadline.expired():
        raise ValueError("score_verdict_arbiter_result_after_deadline")
    model_output, resolved_refs = _accepted_output(result)
    decision = {
        **{key: value for key, value in model_output.items() if key != "evidence_ref_ids"},
        "evidence_refs": resolved_refs,
        "role": ARBITER_ROLE,
        "score_authority_contract_version": SCORE_AUTHORITY_CONTRACT_VERSION,
        "request_id": request_id,
        "request_identity_hash": request_identity_hash,
        "candidate_id": candidate_id,
        "candidate_hash": candidate_hash,
        "provider_id": str(getattr(result, "provider_id", "") or ""),
        "model_id": str(getattr(result, "actual_model", "") or ""),
        "contradiction_fingerprint": fingerprint,
    }
    if not decision["provider_id"] or not decision["model_id"]:
        raise ValueError("score_verdict_arbiter_provider_identity_missing")
    if memo is not None:
        memo.put(fingerprint, decision)
    if event_logger is not None:
        event_logger(
            "[score_verdict_arbiter] valid=true"
            f" request_id={request_id} final_verdict={decision['final_verdict']}"
            f" more_accurate_authority={decision['more_accurate_authority']}"
            f" score_assessment={decision['score_assessment']}"
            f" verdict_assessment={decision['verdict_assessment']}"
            f" reason_codes={','.join(decision.get('reason_codes') or [])}"
        )
    return ArbiterOutcome(decision=decision, fingerprint=fingerprint, memo_hit=False, provider_result=result)


# ---------------------------------------------------------------------------
# 8. Telemetry + learning statistics
# ---------------------------------------------------------------------------

_TELEMETRY_LOCK = threading.Lock()


def build_telemetry_row(
    *,
    request_id: str,
    symbol: str,
    candidate_hash: str,
    score_authority: Mapping[str, Any],
    python_final_allow: bool,
    decision_state: str,
    decision_source: str,
    decided_at: int | None = None,
) -> dict[str, Any]:
    arbiter = score_authority.get("arbiter_output") if isinstance(score_authority.get("arbiter_output"), Mapping) else {}
    return {
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "score_authority_contract_version": score_authority.get("score_authority_contract_version"),
        "decided_at": int(decided_at if decided_at is not None else time.time()),
        "request_id": str(request_id),
        "symbol": str(symbol),
        "candidate_hash": str(candidate_hash),
        "pattern_key": str(score_authority.get("pattern_key") or ""),
        "setup_family": str(score_authority.get("setup_family") or ""),
        "setup_taxonomy_enum": str(score_authority.get("setup_taxonomy_enum") or ""),
        "analyst_verdict": score_authority.get("analyst_verdict"),
        "quality_components": dict(score_authority.get("quality_components") or {}),
        "authoritative_ai_quality_score": score_authority.get("authoritative_ai_quality_score"),
        "model_estimated_quality_score": score_authority.get("model_estimated_quality_score"),
        "required_pattern_score": score_authority.get("required_pattern_score"),
        "threshold_source": score_authority.get("threshold_source"),
        "threshold_version": score_authority.get("threshold_version"),
        "threshold_policy_hash": score_authority.get("threshold_policy_hash"),
        "score_passed": score_authority.get("score_passed"),
        "matrix_case": score_authority.get("matrix_case"),
        "contradiction": score_authority.get("contradiction"),
        "conflict_type": score_authority.get("conflict_type"),
        "critic_verdict": score_authority.get("critic_verdict"),
        "adjudicator_verdict": score_authority.get("adjudicator_verdict"),
        "arbiter_invoked": bool(score_authority.get("arbiter_invoked")),
        "arbiter_memo_hit": bool(score_authority.get("arbiter_memo_hit")),
        "arbiter_skipped_reason": score_authority.get("arbiter_skipped_reason") or "",
        "arbiter_final_verdict": arbiter.get("final_verdict"),
        "arbiter_more_accurate_authority": arbiter.get("more_accurate_authority"),
        "arbiter_score_assessment": arbiter.get("score_assessment"),
        "arbiter_verdict_assessment": arbiter.get("verdict_assessment"),
        "final_ai_verdict": score_authority.get("final_ai_verdict"),
        "ai_side_allow": bool(score_authority.get("ai_side_allow")),
        "python_final_allow": bool(python_final_allow),
        "decision_state": str(decision_state),
        "decision_source": str(decision_source),
    }


def append_telemetry_row(path: Path | None, row: Mapping[str, Any]) -> bool:
    if path is None:
        return False
    try:
        with _TELEMETRY_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
        return True
    except OSError:
        return False


def _mean(values: Sequence[float]) -> float | None:
    return (math.fsum(values) / len(values)) if values else None


def _score_band(score: Any) -> str:
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return "unknown"
    lower = max(0, min(9, int(math.floor(float(score)))))
    return f"{lower}.0-{lower + 1}.0"


def build_score_verdict_report(
    telemetry_rows: Iterable[Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = join_telemetry_outcomes(telemetry_rows, outcome_rows)

    def outcome_summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        realized = [float(item["realized_r"]) for item in items if item.get("realized_r") is not None]
        mae = [float(item["mae_r"]) for item in items if item.get("mae_r") is not None]
        mfe = [float(item["mfe_r"]) for item in items if item.get("mfe_r") is not None]
        return {
            "decisions": len(items),
            "trades_with_outcome": len(realized),
            "avg_r": _mean(realized),
            "win_rate": (sum(1 for value in realized if value > 0.0) / len(realized)) if realized else None,
            "avg_mae_r": _mean(mae),
            "avg_mfe_r": _mean(mfe),
        }

    by_pattern: dict[str, dict[str, Any]] = {}
    for pattern in sorted({str(row.get("pattern_key") or "") for row in rows}):
        items = [row for row in rows if str(row.get("pattern_key") or "") == pattern]
        contradictions = [row for row in items if row.get("contradiction")]
        by_pattern[pattern or "unknown"] = {
            "decisions": len(items),
            "contradictions": len(contradictions),
            "contradiction_rate": (len(contradictions) / len(items)) if items else None,
        }

    arbitrated = [row for row in rows if row.get("arbiter_invoked") and row.get("arbiter_more_accurate_authority")]
    sided_score = [row for row in arbitrated if row.get("arbiter_more_accurate_authority") == "SCORE"]
    sided_verdict = [row for row in arbitrated if row.get("arbiter_more_accurate_authority") == "VERDICT"]
    arbitration_types: dict[str, Any] = {}
    for row in arbitrated:
        key = f"{row.get('conflict_type')}->{row.get('arbiter_final_verdict')}"
        arbitration_types.setdefault(key, []).append(row)

    score_bands: dict[str, Any] = {}
    for row in rows:
        score_bands.setdefault(_score_band(row.get("authoritative_ai_quality_score")), []).append(row)

    over_time: dict[str, Any] = {}
    for row in rows:
        stamp = int(row.get("decided_at") or 0)
        week = time.strftime("%G-W%V", time.gmtime(stamp)) if stamp > 0 else "unknown"
        key = f"{row.get('pattern_key') or 'unknown'}|{week}|{row.get('threshold_version') or ''}"
        over_time.setdefault(key, []).append(row)

    return {
        "report_schema_version": TELEMETRY_SCHEMA_VERSION,
        "score_authority_contract_version": SCORE_AUTHORITY_CONTRACT_VERSION,
        "decisions": len(rows),
        "contradiction_rate_by_pattern": by_pattern,
        "arbiter": {
            "invocations": len(arbitrated),
            "sided_with_score_pct": (100.0 * len(sided_score) / len(arbitrated)) if arbitrated else None,
            "sided_with_verdict_pct": (100.0 * len(sided_verdict) / len(arbitrated)) if arbitrated else None,
        },
        "outcome_by_arbitration_type": {key: outcome_summary(items) for key, items in sorted(arbitration_types.items())},
        "calibration_by_score_band": {key: outcome_summary(items) for key, items in sorted(score_bands.items())},
        "pattern_threshold_performance_over_time": {
            key: {
                **outcome_summary(items),
                "avg_required_pattern_score": _mean(
                    [float(item["required_pattern_score"]) for item in items if isinstance(item.get("required_pattern_score"), (int, float))]
                ),
                "score_pass_rate": (sum(1 for item in items if item.get("score_passed")) / len(items)) if items else None,
            }
            for key, items in sorted(over_time.items())
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Score/verdict authority learning report")
    parser.add_argument("--telemetry", required=True, help="score_verdict_telemetry.jsonl")
    parser.add_argument("--outcomes", required=True, help="completed_ai_trades.jsonl")
    parser.add_argument("--out", default="", help="write the JSON report here (stdout when empty)")
    args = parser.parse_args(argv)
    report = build_score_verdict_report(_read_jsonl(Path(args.telemetry)), _read_jsonl(Path(args.outcomes)))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
