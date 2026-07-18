"""Provider-grade architecture contracts for the PO3/FVG system.

This module deliberately owns cross-cutting contracts that must agree across
the AI bridge, analytics, cache, and research tooling.  It does not grant
trading authority.  Statistical artifacts produced here are shadow-only until
their clean-data, chronological OOS, calibration, and compatibility gates pass.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ARCHITECTURE_CONTRACT_VERSION = "20260718_version_z_reliability_v3"
LIVE_FORWARD_CONTRACT_VERSION = "20260717_live_forward_v1"
SEMANTIC_CACHE_SCHEMA_VERSION = "20260717_semantic_cache_v1"
POLICY_MANIFEST_SCHEMA_VERSION = "20260717_policy_manifest_v1"
COHORT_SCHEMA_VERSION = "20260717_homogeneous_cohort_v1"
DEPLOYMENT_MANIFEST_SCHEMA_VERSION = "20260717_deployment_manifest_v1"
HIERARCHICAL_OUTCOME_MODEL_VERSION = "20260717_hierarchical_outcome_shadow_v2"
ENTRY_MODEL_VERSION = "20260717_entry_path_shadow_v2"
MANAGEMENT_MODEL_VERSION = "20260717_management_alpha_shadow_v2"
SHADOW_OUTCOME_SCHEMA_VERSION = "20260717_shadow_outcome_complete_v2"
FILE_BUS_LIFECYCLE_VERSION = "20260717_file_bus_lifecycle_v2"

LIVE_FORWARD = "LIVE_FORWARD"
TESTER_RECORD_ONLY = "TESTER_AI_RECORD_ONLY"
TESTER_CACHE_ONLY = "TESTER_AI_CACHE_ONLY"
TESTER_LIVE_WAIT_DEBUG = "TESTER_AI_LIVE_WAIT_DEBUG"

FILE_BUS_TERMINAL_STATES = {
    "completed",
    "rejected",
    "timed_out",
    "stale",
    "quarantined",
    "shutdown",
}

SEMANTIC_CACHE_REASONS = (
    "cache_stale_new_entry_bar",
    "cache_stale_entry_drift",
    "cache_stale_stop_drift",
    "cache_stale_target_drift",
    "cache_stale_spread_bucket",
    "cache_stale_structure",
    "cache_stale_fvg_state",
    "cache_stale_session",
    "cache_stale_killzone",
    "cache_stale_prior_version",
    "cache_stale_contract_version",
)

COHORT_FIELDS = (
    "engine_version",
    "git_commit",
    "dirty_tree_status",
    "set_file_hash",
    "runtime_input_hash",
    "prompt_contract_version",
    "model_version",
    "reasoning_configuration",
    "decision_schema_version",
    "target_schema_version",
    "policy_id",
    "policy_hash",
    "bucket_prior_hash",
    "management_version",
    "taxonomy_version",
    "feature_version",
    "calibration_artifact_id",
    "repeatability_artifact_id",
)

PRE_ENTRY_FEATURE_NAMES = (
    "effective_rr2",
    "liquidity_rr",
    "sequence_quality",
    "htf_alignment_score",
    "stop_quality_score",
    "trend_strength",
    "session_vol_ratio",
    "fvg_score",
    "adverse_context_score",
    "execution_cost_r",
    "slippage_r",
    "commission_r",
    "vwap_dist_atr",
    "news_risk",
)

MANAGEMENT_FEATURE_NAMES = (
    "management_snapshot_mfe_r",
    "management_snapshot_mae_r",
    "management_snapshot_minutes_open",
    "management_snapshot_distance_to_sl_r",
    "management_snapshot_distance_to_tp_r",
    "management_snapshot_spread_r",
    "management_snapshot_execution_cost_r",
    "management_snapshot_structure_valid",
)

ENTRY_TARGET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "business_report_label": {
        "role": "reporting_only",
        "winner": "full_close_pct > 0.1",
        "loser": "full_close_pct < -0.1",
        "neutral": "otherwise",
        "primary_model_target": False,
    },
    "unmanaged_original_plan_net_r": {
        "kind": "continuous",
        "role": "primary_entry_path_outcome",
        "horizon": "original_stop_or_target_or_configured_horizon_or_session_close",
        "account_percentage_forbidden": True,
    },
    "unmanaged_original_plan_target_before_stop": {
        "kind": "binary",
        "role": "primary_entry_event",
        "same_bar_policy": "AMBIGUOUS_EXCLUDED",
    },
    "unmanaged_mfe_before_mae": {
        "kind": "binary",
        "role": "path_order_event",
        "definition": "first_0_25r_favorable_before_0_50r_adverse",
        "same_bar_policy": "AMBIGUOUS_EXCLUDED",
    },
    "unmanaged_reached_0_25r_before_adverse_threshold": {
        "kind": "binary",
        "role": "path_order_event",
        "adverse_threshold_r": 0.50,
        "same_bar_policy": "AMBIGUOUS_EXCLUDED",
    },
    "unmanaged_reached_0_50r_before_adverse_threshold": {
        "kind": "binary",
        "role": "path_order_event",
        "adverse_threshold_r": 0.50,
        "same_bar_policy": "AMBIGUOUS_EXCLUDED",
    },
    "unmanaged_mfe_r": {
        "kind": "continuous",
        "role": "expected_mfe",
        "horizon": "original_plan_observation_horizon",
        "units": "nonnegative_R_magnitude",
    },
    "unmanaged_mae_r": {
        "kind": "continuous",
        "role": "expected_mae",
        "horizon": "original_plan_observation_horizon",
        "units": "nonnegative_adverse_R_magnitude",
    },
    "unmanaged_time_to_0_25r_minutes": {
        "kind": "time_to_event",
        "event_field": "unmanaged_reached_0_25r",
        "duration_or_censor_field": "unmanaged_time_to_0_25r_or_censor_minutes",
        "censoring": "right_censored_at_terminal_horizon_session_close_or_data_loss",
    },
    "unmanaged_time_to_0_50r_minutes": {
        "kind": "time_to_event",
        "event_field": "unmanaged_reached_0_50r",
        "duration_or_censor_field": "unmanaged_time_to_0_50r_or_censor_minutes",
        "censoring": "right_censored_at_terminal_horizon_session_close_or_data_loss",
    },
    "unmanaged_time_to_invalidation_minutes": {
        "kind": "time_to_event",
        "event_field": "unmanaged_adverse_threshold_reached",
        "duration_or_censor_field": "unmanaged_time_to_invalidation_or_censor_minutes",
        "adverse_threshold_r": 0.50,
        "censoring": "right_censored_at_terminal_horizon_session_close_or_data_loss",
    },
    "management_alpha": {
        "kind": "continuous",
        "role": "management_model_only",
        "definition": "managed_remaining_outcome_minus_original_policy_remaining_outcome",
        "entry_model_forbidden": True,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("ascii")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(dict(payload), handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class StrictJsonError(ValueError):
    """Raised when a file-bus JSON document is ambiguous or malformed."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StrictJsonError(f"non_finite_json_number:{value}")


def _validate_unicode(value: Any, path: str = "$") -> None:
    if isinstance(value, str):
        for char in value:
            code = ord(char)
            if 0xD800 <= code <= 0xDFFF:
                raise StrictJsonError(f"invalid_unicode_surrogate:{path}")
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _validate_unicode(key, f"{path}.<key>")
            _validate_unicode(nested, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_unicode(nested, f"{path}[{index}]")


def strict_json_loads(text: str, *, require_object: bool = True) -> Any:
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except StrictJsonError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StrictJsonError(f"invalid_json_document:{exc}") from exc
    if require_object and not isinstance(value, dict):
        raise StrictJsonError("json_root_must_be_object")
    _validate_unicode(value)
    return value


def strict_json_load(path: Path, *, require_object: bool = True) -> Any:
    raw = path.read_bytes()
    decoded: str | None = None
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeError as exc:
            last_error = exc
    if decoded is None:
        raise StrictJsonError(f"invalid_json_encoding:{last_error}")
    return strict_json_loads(decoded, require_object=require_object)


def fnv1a_decimal(value: str) -> str:
    result = 2166136261
    for char in value:
        result ^= ord(char)
        result = (result * 16777619) & 0xFFFFFFFF
    return str(result % 2147483647)


def _fixed(value: Any, digits: int) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise StrictJsonError("response_binding_non_numeric") from exc
    if not math.isfinite(number):
        raise StrictJsonError("response_binding_non_finite")
    return f"{number:.{digits}f}"


def response_binding_material(response: Mapping[str, Any]) -> str:
    """Canonical ASCII material that MQL can recompute without a JSON library."""

    return "|".join(
        (
            str(response.get("id") or ""),
            str(response.get("session_id") or ""),
            str(response.get("request_nonce") or ""),
            str(response.get("decision_schema_version") or ""),
            str(response.get("decision_quality_tier") or ""),
            str(response.get("decision_state") or ""),
            "1" if bool(response.get("model_raw_allow")) else "0",
            "1" if bool(response.get("python_final_allow")) else "0",
            str(response.get("selected_candidate_id") or ""),
            str(response.get("selected_candidate_hash") or ""),
            str(response.get("assessed_execution_fingerprint") or ""),
            str(response.get("selected_target_identity") or ""),
            _fixed(response.get("selected_target_price"), 8),
            _fixed(response.get("llm_quality_score"), 6),
            _fixed(response.get("suggested_risk_multiplier"), 6),
        )
    )


def response_binding_hash(response: Mapping[str, Any]) -> str:
    return fnv1a_decimal(response_binding_material(response))


def validate_response_binding(
    response: Mapping[str, Any],
    *,
    request_id: str,
    session_id: str,
    request_nonce: str,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if str(response.get("id") or "") != request_id:
        reasons.append("response_request_id_mismatch")
    if str(response.get("session_id") or "") != session_id:
        reasons.append("response_session_id_mismatch")
    if str(response.get("request_nonce") or "") != request_nonce:
        reasons.append("response_nonce_mismatch")
    expected = response_binding_hash(response)
    if str(response.get("response_binding_hash") or "") != expected:
        reasons.append("response_hash_mismatch")
    return not reasons, reasons


def workload_mode(payload: Mapping[str, Any]) -> str:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), Mapping) else {}
    tester = bool(runtime.get("tester") or payload.get("tester"))
    if tester:
        raw = str(runtime.get("tester_ai_mode") or payload.get("tester_ai_mode") or "").upper()
        if raw in {"0", "RECORD_ONLY", TESTER_RECORD_ONLY}:
            return TESTER_RECORD_ONLY
        if raw in {"1", "CACHE_ONLY", TESTER_CACHE_ONLY}:
            return TESTER_CACHE_ONLY
        return TESTER_LIVE_WAIT_DEBUG
    trade_mode = str(runtime.get("account_trade_mode") or payload.get("account_trade_mode") or "").lower()
    if trade_mode in {"demo", "contest", "real", "live"}:
        return LIVE_FORWARD
    explicit = str(payload.get("mode") or payload.get("workload_mode") or "").lower()
    if explicit in {"live", "demo", "contest", "real", "live_forward"}:
        return LIVE_FORWARD
    return explicit or "research"


def live_forward_behavior_contract() -> dict[str, Any]:
    contract = {
        "version": LIVE_FORWARD_CONTRACT_VERSION,
        "workload_mode": LIVE_FORWARD,
        "account_destination_fields_excluded": ["account_login", "account_trade_mode", "broker_server"],
        "behavior": {
            "ai_failure": "fail_closed_full_structured_only",
            "rule_only_fallback": "non_trading",
            "risk_caps": "identical",
            "stale_decision": "wall_clock_and_semantic_state",
            "schema_validation": "strict",
            "mandatory_priors": "identical",
            "management": "identical",
            "policy_activation": "clean_compatible_only",
            "cache_validation": "semantic_and_contract",
            "attribution": "exact_or_quarantine",
            "ledger_output": "identical",
        },
    }
    contract["behavior_contract_hash"] = canonical_hash(contract)
    return contract


@dataclass(frozen=True)
class MultiplierResolution:
    value_present: bool
    raw_value: Any
    resolved_value: float | None
    valid: bool
    blocked: bool
    source: str
    optional: bool
    rejection_reason: str


def resolve_multiplier(
    value: Any,
    *,
    present: bool,
    optional: bool,
    source: str,
    maximum: float = 1.0,
) -> MultiplierResolution:
    if not present or value is None:
        if optional:
            return MultiplierResolution(False, value, 1.0, True, False, source, True, "")
        return MultiplierResolution(False, value, None, False, True, source, False, "multiplier_missing")
    if isinstance(value, bool):
        return MultiplierResolution(True, value, None, False, True, source, optional, "multiplier_not_numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return MultiplierResolution(True, value, None, False, True, source, optional, "multiplier_not_numeric")
    if not math.isfinite(number):
        return MultiplierResolution(True, value, None, False, True, source, optional, "multiplier_non_finite")
    if number < 0.0:
        return MultiplierResolution(True, value, None, False, True, source, optional, "multiplier_below_zero")
    if number > maximum:
        return MultiplierResolution(True, value, None, False, True, source, optional, "multiplier_above_maximum")
    return MultiplierResolution(True, value, number, True, number == 0.0, source, optional, "resolved_risk_multiplier_zero" if number == 0.0 else "")


def resolve_multiplier_chain(items: Sequence[MultiplierResolution]) -> MultiplierResolution:
    invalid = next((item for item in items if not item.valid), None)
    if invalid is not None:
        return invalid
    if any(item.blocked for item in items):
        source = "+".join(item.source for item in items if item.blocked)
        return MultiplierResolution(True, 0.0, 0.0, True, True, source, False, "resolved_risk_multiplier_zero")
    value = 1.0
    sources: list[str] = []
    for item in items:
        value *= float(item.resolved_value if item.resolved_value is not None else 1.0)
        sources.append(item.source)
    return MultiplierResolution(True, [item.raw_value for item in items], value, True, value == 0.0, "+".join(sources), False, "")


@dataclass(frozen=True)
class SemanticCacheState:
    entry_bar_id: str
    entry: float
    stop: float
    target: float
    spread_r_bucket: str
    structure_state: str
    fvg_mitigation_state: str
    session: str
    killzone: str
    bucket_prior_hash: str
    candidate_hash: str
    execution_fingerprint: str
    policy_version: str
    model_version: str
    prompt_contract_version: str
    decision_schema_version: str
    target_schema_version: str


def semantic_cache_state(fields: Mapping[str, Any]) -> SemanticCacheState:
    def number(*names: str) -> float:
        for name in names:
            try:
                value = float(fields.get(name))
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value):
                return value
        return 0.0

    def text(*names: str) -> str:
        for name in names:
            value = fields.get(name)
            if value not in (None, ""):
                return str(value)
        return ""

    return SemanticCacheState(
        entry_bar_id=text("entry_bar_id", "entry_candle_time", "candle_time"),
        entry=number("entry_est", "entry"),
        stop=number("sl", "stop"),
        target=number("tp2", "target"),
        spread_r_bucket=text("spread_r_bucket"),
        structure_state=text("structure_state", "po3_state"),
        fvg_mitigation_state=text("fvg_mitigation_state", "mitigation_state"),
        session=text("session_name", "session"),
        killzone=text("killzone_code", "killzone"),
        bucket_prior_hash=text("bucket_prior_hash", "hierarchical_prior_artifact_hash"),
        candidate_hash=text("candidate_hash", "selected_candidate_hash"),
        execution_fingerprint=text("execution_fingerprint", "request_execution_fingerprint"),
        policy_version=text("policy_version", "policy_hash"),
        model_version=text("model_version"),
        prompt_contract_version=text("ai_prompt_contract_version", "prompt_contract_version"),
        decision_schema_version=text("ai_decision_schema_version", "decision_schema_version"),
        target_schema_version=text("ai_target_arbitration_schema_version", "target_schema_version"),
    )


def semantic_cache_invalidation_reasons(
    cached: SemanticCacheState,
    current: SemanticCacheState,
    *,
    entry_tolerance_r: float = 0.05,
    stop_tolerance_r: float = 0.05,
    target_tolerance_r: float = 0.05,
) -> list[str]:
    reasons: list[str] = []
    if cached.entry_bar_id != current.entry_bar_id:
        reasons.append("cache_stale_new_entry_bar")
    risk = abs(cached.entry - cached.stop)
    if risk <= 0.0:
        risk = abs(current.entry - current.stop)
    if risk <= 0.0:
        risk = 1.0
    if abs(current.entry - cached.entry) / risk > entry_tolerance_r:
        reasons.append("cache_stale_entry_drift")
    if abs(current.stop - cached.stop) / risk > stop_tolerance_r:
        reasons.append("cache_stale_stop_drift")
    if abs(current.target - cached.target) / risk > target_tolerance_r:
        reasons.append("cache_stale_target_drift")
    if cached.spread_r_bucket != current.spread_r_bucket:
        reasons.append("cache_stale_spread_bucket")
    if cached.structure_state != current.structure_state:
        reasons.append("cache_stale_structure")
    if cached.fvg_mitigation_state != current.fvg_mitigation_state:
        reasons.append("cache_stale_fvg_state")
    if cached.session != current.session:
        reasons.append("cache_stale_session")
    if cached.killzone != current.killzone:
        reasons.append("cache_stale_killzone")
    if cached.bucket_prior_hash != current.bucket_prior_hash:
        reasons.append("cache_stale_prior_version")
    contract_values = (
        (cached.candidate_hash, current.candidate_hash),
        (cached.execution_fingerprint, current.execution_fingerprint),
        (cached.policy_version, current.policy_version),
        (cached.model_version, current.model_version),
        (cached.prompt_contract_version, current.prompt_contract_version),
        (cached.decision_schema_version, current.decision_schema_version),
        (cached.target_schema_version, current.target_schema_version),
    )
    if any(left != right for left, right in contract_values):
        reasons.append("cache_stale_contract_version")
    return list(dict.fromkeys(reasons))


def semantic_cache_row(state: SemanticCacheState) -> dict[str, Any]:
    return {"semantic_cache_schema_version": SEMANTIC_CACHE_SCHEMA_VERSION, **asdict(state)}


@dataclass(frozen=True)
class DecisionAuthority:
    model_raw_allow: bool
    python_final_allow: bool
    mql_final_allow: bool | None
    python_reasons: tuple[str, ...]
    mql_reasons: tuple[str, ...]


def resolve_python_decision_authority(
    *,
    model_raw_allow: bool,
    decision_state: str,
    schema_valid: bool,
    veto_passed: bool,
    repeatability_passed: bool,
    prior_passed: bool,
    statistical_policy_passed: bool,
    multiplier: MultiplierResolution,
    reasons: Iterable[str] = (),
) -> DecisionAuthority:
    failures = list(reasons)
    if decision_state.upper() != "APPROVE":
        failures.append("decision_state_not_approve")
    if not model_raw_allow:
        failures.append("model_raw_allow_false")
    if not schema_valid:
        failures.append("ai_quality_schema_incomplete")
    if not veto_passed:
        failures.append("ai_veto")
    if not repeatability_passed:
        failures.append("repeatability_gate_failed")
    if not prior_passed:
        failures.append("prior_gate_failed")
    if not statistical_policy_passed:
        failures.append("statistical_policy_gate_failed")
    if not multiplier.valid:
        failures.append(multiplier.rejection_reason or "multiplier_invalid")
    elif multiplier.blocked:
        failures.append("resolved_risk_multiplier_zero")
    final = not failures
    return DecisionAuthority(model_raw_allow, final, None, tuple(dict.fromkeys(failures)), ())


def resolve_mql_decision_authority(
    python: DecisionAuthority,
    *,
    candidate_hash_match: bool,
    execution_fingerprint_match: bool,
    freshness_passed: bool,
    risk_passed: bool,
    broker_passed: bool,
    session_passed: bool,
    policy_passed: bool,
) -> DecisionAuthority:
    failures: list[str] = []
    if not candidate_hash_match:
        failures.append("candidate_hash_mismatch")
    if not execution_fingerprint_match:
        failures.append("execution_fingerprint_mismatch")
    if not freshness_passed:
        failures.append("decision_stale")
    if not risk_passed:
        failures.append("risk_gate_failed")
    if not broker_passed:
        failures.append("broker_feasibility_failed")
    if not session_passed:
        failures.append("session_gate_failed")
    if not policy_passed:
        failures.append("mql_policy_gate_failed")
    final = python.python_final_allow and not failures
    return DecisionAuthority(
        python.model_raw_allow,
        python.python_final_allow,
        final,
        python.python_reasons,
        tuple(failures),
    )


LAYERED_AUTHORITY = {
    "deterministic": {
        "candidate_construction",
        "objective_validity",
        "entry_sl_tp",
        "costs",
        "broker_feasibility",
        "exact_risk",
        "session_feasibility",
        "execution_fingerprint",
    },
    "statistical": {
        "target_before_stop_probability",
        "reach_0_25r_probability",
        "reach_0_50r_probability",
        "expected_mfe",
        "expected_mae",
        "expected_net_r",
        "time_to_event",
    },
    "llm": {"anomaly_veto", "contradiction_veto", "missing_data_veto", "narrative_explanation", "data_integrity_review"},
    "portfolio": {"aggregate_risk", "factor_scheduling", "exposure_caps"},
    "management": {"validated_exit_policy"},
}


def authority_source(field: str) -> str:
    for owner, fields in LAYERED_AUTHORITY.items():
        if field in fields:
            return owner
    return "unassigned"


def validate_authority_assignment(field: str, source: str) -> bool:
    return authority_source(field) == source


def decision_field_authority_manifest() -> dict[str, Any]:
    """Machine-readable ownership for every decision-critical output family."""

    return {
        "schema_version": ARCHITECTURE_CONTRACT_VERSION,
        "candidate_entry_sl_tp": {"owner": "deterministic", "authority": "active"},
        "broker_feasibility": {"owner": "deterministic", "authority": "active"},
        "exact_risk_size": {"owner": "deterministic_portfolio", "authority": "active"},
        "calibrated_probability": {"owner": "statistical", "authority": "unavailable"},
        "expected_net_r": {"owner": "statistical", "authority": "unavailable"},
        "llm_quality_score": {"owner": "llm", "authority": "diagnostic_only_uncalibrated"},
        "llm_risk_assessments": {"owner": "llm", "authority": "diagnostic_only_uncalibrated"},
        "llm_qualitative_veto": {"owner": "llm", "authority": "evidence_backed_enumerated_veto"},
        "llm_narrative": {"owner": "llm", "authority": "diagnostic"},
        "python_final_allow": {"owner": "python_policy", "authority": "intermediate"},
        "mql_final_allow": {"owner": "mql_execution", "authority": "final"},
        "portfolio_exposure": {"owner": "portfolio", "authority": "active"},
        "management_action": {"owner": "management", "authority": "shadow_until_validated"},
    }


def cohort_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    metadata = {field: record.get(field) for field in COHORT_FIELDS}
    missing = [field for field, value in metadata.items() if value in (None, "")]
    identity = {field: metadata[field] for field in COHORT_FIELDS}
    return {
        "cohort_schema_version": COHORT_SCHEMA_VERSION,
        "cohort_id": canonical_hash(identity),
        "cohort_complete": not missing,
        "missing_cohort_fields": missing,
        "fields": metadata,
    }


def cohort_compatibility(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_meta = cohort_metadata(left)
    right_meta = cohort_metadata(right)
    changed = [
        field
        for field in COHORT_FIELDS
        if left_meta["fields"].get(field) != right_meta["fields"].get(field)
    ]
    compatible = left_meta["cohort_complete"] and right_meta["cohort_complete"] and not changed
    return {
        "compatible": compatible,
        "left_cohort_id": left_meta["cohort_id"],
        "right_cohort_id": right_meta["cohort_id"],
        "changed_fields": changed,
        "reason": "" if compatible else ("cohort_metadata_incomplete" if not left_meta["cohort_complete"] or not right_meta["cohort_complete"] else "mixed_version_cohort"),
    }


def partition_homogeneous_cohorts(records: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        metadata = cohort_metadata(record)
        key = metadata["cohort_id"] if metadata["cohort_complete"] else "INCOMPLETE"
        enriched = dict(record)
        enriched.update({key: value for key, value in metadata.items() if key != "fields"})
        groups.setdefault(key, []).append(enriched)
    return groups


def require_homogeneous_cohort(records: Sequence[Mapping[str, Any]]) -> str:
    groups = partition_homogeneous_cohorts(records)
    if "INCOMPLETE" in groups:
        raise ValueError("cohort_metadata_incomplete")
    if len(groups) != 1:
        raise ValueError("mixed_version_cohort_analysis_blocked")
    return next(iter(groups)) if groups else ""


@dataclass(frozen=True)
class OutcomeTargetConfig:
    horizon_minutes: int = 1440
    adverse_threshold_r: float = 0.50
    same_bar_policy: str = "AMBIGUOUS"


def evaluate_path_targets(
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    entry_time: int,
    bars: Sequence[Mapping[str, Any]],
    config: OutcomeTargetConfig | None = None,
) -> dict[str, Any]:
    cfg = config or OutcomeTargetConfig()
    is_buy = direction.lower() in {"buy", "long", "1"}
    risk = abs(entry - stop)
    if risk <= 0.0 or entry <= 0.0 or stop <= 0.0 or target <= 0.0:
        raise ValueError("invalid_shadow_plan_prices")
    horizon_end = entry_time + max(1, cfg.horizon_minutes) * 60
    mfe_r = 0.0
    mae_r = 0.0
    times: dict[str, int | None] = {
        "time_to_0_25r": None,
        "time_to_0_50r": None,
        "time_to_stop": None,
        "time_to_target": None,
        "time_to_invalidation": None,
    }
    result = "CENSORED_HORIZON"
    target_before_stop: bool | None = None
    ambiguous = False
    threshold_025_ambiguous = False
    threshold_050_ambiguous = False
    observed_until = entry_time
    last_observed_close: float | None = None
    for bar in sorted(bars, key=lambda row: int(row.get("time") or 0)):
        bar_time = int(bar.get("time") or 0)
        if bar_time < entry_time:
            continue
        if bar_time > horizon_end:
            break
        try:
            high = float(bar.get("high"))
            low = float(bar.get("low"))
        except (TypeError, ValueError, OverflowError):
            result = "CENSORED_DATA_LOSS"
            break
        if not math.isfinite(high) or not math.isfinite(low) or high < low:
            result = "CENSORED_DATA_LOSS"
            break
        observed_until = bar_time
        try:
            close = float(bar.get("close"))
        except (TypeError, ValueError, OverflowError):
            close = (high + low) / 2.0
        if not math.isfinite(close):
            result = "CENSORED_DATA_LOSS"
            break
        last_observed_close = close
        favorable = (high - entry) / risk if is_buy else (entry - low) / risk
        adverse = (entry - low) / risk if is_buy else (high - entry) / risk
        mfe_r = max(mfe_r, favorable)
        mae_r = max(mae_r, adverse)
        elapsed = max(0, int((bar_time - entry_time) / 60))
        stop_hit = low <= stop if is_buy else high >= stop
        target_hit = high >= target if is_buy else low <= target
        adverse_hit = adverse >= cfg.adverse_threshold_r
        new_adverse = times["time_to_invalidation"] is None and adverse_hit
        new_025 = times["time_to_0_25r"] is None and favorable >= 0.25
        new_050 = times["time_to_0_50r"] is None and favorable >= 0.50
        if new_025:
            if new_adverse:
                threshold_025_ambiguous = True
            times["time_to_0_25r"] = elapsed
        if new_050:
            if new_adverse:
                threshold_050_ambiguous = True
            times["time_to_0_50r"] = elapsed
        if times["time_to_invalidation"] is None and adverse_hit:
            times["time_to_invalidation"] = elapsed
        if stop_hit and times["time_to_stop"] is None:
            times["time_to_stop"] = elapsed
        if target_hit and times["time_to_target"] is None:
            times["time_to_target"] = elapsed
        if stop_hit and target_hit:
            ambiguous = True
            result = "AMBIGUOUS_SAME_BAR_STOP_AND_TARGET"
            target_before_stop = None
            break
        if target_hit:
            result = "TARGET_FIRST"
            target_before_stop = True
            break
        if stop_hit:
            result = "STOP_FIRST"
            target_before_stop = False
            break
    reached_025_before_adverse = None
    reached_050_before_adverse = None
    if threshold_025_ambiguous:
        reached_025_before_adverse = None
    elif times["time_to_0_25r"] is not None or times["time_to_invalidation"] is not None:
        reached_025_before_adverse = (
            times["time_to_0_25r"] is not None
            and (times["time_to_invalidation"] is None or times["time_to_0_25r"] < times["time_to_invalidation"])
        )
    if threshold_050_ambiguous:
        reached_050_before_adverse = None
    elif times["time_to_0_50r"] is not None or times["time_to_invalidation"] is not None:
        reached_050_before_adverse = (
            times["time_to_0_50r"] is not None
            and (times["time_to_invalidation"] is None or times["time_to_0_50r"] < times["time_to_invalidation"])
        )
    censored = result.startswith("CENSORED")
    mfe_before_mae: bool | None = None
    if not censored and not threshold_025_ambiguous:
        favorable_time = times["time_to_0_25r"]
        adverse_time = times["time_to_invalidation"]
        if favorable_time is not None or adverse_time is not None:
            mfe_before_mae = favorable_time is not None and (
                adverse_time is None or favorable_time < adverse_time
            )
    observed_path_minutes = max(0.0, (observed_until - entry_time) / 60.0)
    gross_result_r: float | None = None
    if result == "TARGET_FIRST":
        gross_result_r = abs(target - entry) / risk
    elif result == "STOP_FIRST":
        gross_result_r = -1.0
    elif result == "CENSORED_HORIZON" and last_observed_close is not None:
        gross_result_r = ((last_observed_close - entry) if is_buy else (entry - last_observed_close)) / risk
    reached_025 = times["time_to_0_25r"] is not None
    reached_050 = times["time_to_0_50r"] is not None
    adverse_reached = times["time_to_invalidation"] is not None
    return {
        "target_contract_version": ENTRY_MODEL_VERSION,
        "event_horizon_minutes": cfg.horizon_minutes,
        "adverse_threshold_r": cfg.adverse_threshold_r,
        "same_bar_policy": cfg.same_bar_policy,
        "target_before_stop": target_before_stop,
        "mfe_before_mae": None if ambiguous else mfe_before_mae,
        "reached_0_25r_before_adverse_threshold": reached_025_before_adverse,
        "reached_0_50r_before_adverse_threshold": reached_050_before_adverse,
        "observed_mfe_r": mfe_r,
        "observed_mae_r": mae_r,
        "unmanaged_original_plan_net_r_gross": gross_result_r,
        "unmanaged_original_plan_target_before_stop": target_before_stop,
        "unmanaged_mfe_before_mae": None if ambiguous else mfe_before_mae,
        "unmanaged_reached_0_25r_before_adverse_threshold": reached_025_before_adverse,
        "unmanaged_reached_0_50r_before_adverse_threshold": reached_050_before_adverse,
        "unmanaged_mfe_r": max(0.0, mfe_r),
        "unmanaged_mae_r": max(0.0, mae_r),
        "unmanaged_reached_0_25r": reached_025,
        "unmanaged_reached_0_50r": reached_050,
        "unmanaged_adverse_threshold_reached": adverse_reached,
        "unmanaged_observed_path_minutes": observed_path_minutes,
        "unmanaged_time_to_0_25r_minutes": times["time_to_0_25r"],
        "unmanaged_time_to_0_50r_minutes": times["time_to_0_50r"],
        "unmanaged_time_to_invalidation_minutes": times["time_to_invalidation"],
        "unmanaged_time_to_0_25r_or_censor_minutes": (
            times["time_to_0_25r"] if reached_025 else observed_path_minutes
        ),
        "unmanaged_time_to_0_50r_or_censor_minutes": (
            times["time_to_0_50r"] if reached_050 else observed_path_minutes
        ),
        "unmanaged_time_to_invalidation_or_censor_minutes": (
            times["time_to_invalidation"] if adverse_reached else observed_path_minutes
        ),
        **times,
        "horizon_result": result,
        "ambiguity_status": (
            "SAME_BAR_STOP_TARGET_AMBIGUOUS"
            if ambiguous
            else "SAME_BAR_THRESHOLD_ORDER_AMBIGUOUS"
            if threshold_025_ambiguous or threshold_050_ambiguous
            else "UNAMBIGUOUS"
        ),
        "censoring_status": result if censored else "NOT_CENSORED",
        "observed_until": observed_until,
    }


def _clean_exact_homogeneous_pre_entry(record: Mapping[str, Any]) -> bool:
    integrity = str(record.get("ledger_integrity_status") or "").lower()
    attribution = str(record.get("attribution_status") or "").lower()
    if integrity not in {"clean", "verified_clean", "reconciled_clean"}:
        return False
    if attribution not in {"exact", "verified", "exact_verified"}:
        return False
    if bool(record.get("execution_identity_quarantined")):
        return False
    metadata = cohort_metadata(record)
    if not metadata["cohort_complete"]:
        return False
    if record.get("pre_entry_features_complete") is False:
        return False
    return all(record.get(name) is not None for name in PRE_ENTRY_FEATURE_NAMES)


def _number(record: Mapping[str, Any], name: str) -> float:
    value = float(record.get(name))
    if not math.isfinite(value):
        raise ValueError(f"non_finite_feature:{name}")
    return value


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _logit(value: float) -> float:
    value = min(1.0 - 1e-6, max(1e-6, value))
    return math.log(value / (1.0 - value))


def _fit_global_logistic(records: Sequence[Mapping[str, Any]], target_field: str) -> dict[str, Any]:
    matrix = [[_number(row, name) for name in PRE_ENTRY_FEATURE_NAMES] for row in records]
    targets = [1.0 if bool(row.get(target_field)) else 0.0 for row in records]
    centers = [statistics.fmean(column) for column in zip(*matrix)]
    scales = [max(statistics.pstdev(column), 1e-6) for column in zip(*matrix)]
    x = [[(value - centers[i]) / scales[i] for i, value in enumerate(row)] for row in matrix]
    base = min(0.99, max(0.01, statistics.fmean(targets)))
    intercept = _logit(base)
    weights = [0.0] * len(PRE_ENTRY_FEATURE_NAMES)
    n = float(len(x))
    for _ in range(1000):
        grad_b = 0.0
        grad_w = [0.0] * len(weights)
        for features, target in zip(x, targets):
            pred = _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, features)))
            error = pred - target
            grad_b += error
            for index, value in enumerate(features):
                grad_w[index] += error * value
        intercept -= 0.035 * grad_b / n
        for index in range(len(weights)):
            weights[index] -= 0.035 * (grad_w[index] / n + 0.25 * weights[index])
    return {
        "feature_names": list(PRE_ENTRY_FEATURE_NAMES),
        "centers": centers,
        "scales": scales,
        "intercept": intercept,
        "coefficients": weights,
        "l2": 0.25,
    }


def _predict_global(model: Mapping[str, Any], row: Mapping[str, Any]) -> float:
    features = [
        (_number(row, name) - float(model["centers"][index])) / float(model["scales"][index])
        for index, name in enumerate(model["feature_names"])
    ]
    return _sigmoid(float(model["intercept"]) + sum(float(weight) * value for weight, value in zip(model["coefficients"], features)))


def _predict_with_asset_adjustment(
    model: Mapping[str, Any],
    row: Mapping[str, Any],
    asset_adjustments: Mapping[str, Mapping[str, Any]],
) -> float:
    probability = _predict_global(model, row)
    adjustment = asset_adjustments.get(_asset_class(row))
    if adjustment and adjustment.get("eligible"):
        probability = _sigmoid(_logit(probability) + float(adjustment["logit_adjustment"]))
    return probability


def _asset_class(record: Mapping[str, Any]) -> str:
    explicit = str(record.get("asset_class") or "").lower()
    if explicit:
        return explicit
    symbol = str(record.get("symbol") or "").upper()
    if any(value in symbol for value in ("XAU", "GOLD", "XAG", "SILVER")):
        return "metals"
    if any(value in symbol for value in ("WTI", "BRENT", "OIL", "NGAS")):
        return "energy"
    if any(value in symbol for value in ("BTC", "ETH", "SOL")):
        return "crypto"
    if any(value in symbol for value in ("US30", "NAS", "SPX", "GER", "DAX", "UK100", "JP225")):
        return "indices"
    return "fx" if len("".join(char for char in symbol if char.isalpha())) >= 6 else "other"


def _posterior_adjustment(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_field: str,
    parent_probability: float,
    prior_strength: float,
) -> dict[str, Any]:
    n = len(rows)
    successes = sum(bool(row.get(target_field)) for row in rows)
    alpha = parent_probability * prior_strength + successes
    beta = (1.0 - parent_probability) * prior_strength + n - successes
    posterior = alpha / (alpha + beta)
    variance = alpha * beta / (((alpha + beta) ** 2) * (alpha + beta + 1.0))
    se = math.sqrt(max(0.0, variance))
    weight = n / (n + prior_strength)
    return {
        "sample_size": n,
        "effective_sample_size": n * weight,
        "successes": successes,
        "raw_event_rate": successes / n if n else 0.0,
        "parent_probability": parent_probability,
        "posterior_probability": posterior,
        "logit_adjustment": _logit(posterior) - _logit(parent_probability),
        "shrinkage_weight": weight,
        "uncertainty": {
            "standard_error": se,
            "lower": max(0.0, posterior - 1.96 * se),
            "upper": min(1.0, posterior + 1.96 * se),
        },
    }


def build_hierarchical_outcome_artifact(
    records: Sequence[Mapping[str, Any]],
    *,
    target_field: str = "target_before_stop",
    min_clean_sample: int = 80,
    min_asset_sample: int = 30,
    min_symbol_sample: int = 80,
    prior_strength: float = 30.0,
) -> dict[str, Any]:
    clean = [dict(row) for row in records if _clean_exact_homogeneous_pre_entry(row)]
    clean = [row for row in clean if row.get(target_field) in {True, False}]
    unavailable = {
        "schema_version": HIERARCHICAL_OUTCOME_MODEL_VERSION,
        "status": "shadow_unavailable",
        "shadow_only": True,
        "trading_authority": False,
        "target_field": target_field,
        "input_record_count": len(records),
        "clean_record_count": len(clean),
        "rejected_record_count": len(records) - len(clean),
        "activation_block_reasons": [],
        "global_model": None,
        "asset_class_adjustments": {},
        "symbol_adjustments": {},
        "generated_at": utc_now(),
    }
    if len(clean) < min_clean_sample:
        unavailable["activation_block_reasons"] = ["minimum_clean_homogeneous_sample_not_met"]
        unavailable["artifact_hash"] = canonical_hash(unavailable)
        return unavailable
    try:
        cohort_id = require_homogeneous_cohort(clean)
    except ValueError as exc:
        unavailable["status"] = "shadow_blocked"
        unavailable["activation_block_reasons"] = [str(exc)]
        unavailable["cohort_counts"] = {
            key: len(value) for key, value in partition_homogeneous_cohorts(clean).items()
        }
        unavailable["artifact_hash"] = canonical_hash(unavailable)
        return unavailable
    ordered = sorted(clean, key=lambda row: int(row.get("opened_at") or row.get("candidate_timestamp") or 0))
    train_end = max(40, int(len(ordered) * 0.60))
    calibration_end = max(train_end + 10, int(len(ordered) * 0.80))
    if calibration_end >= len(ordered):
        unavailable["activation_block_reasons"] = ["chronological_oos_windows_unavailable"]
        unavailable["artifact_hash"] = canonical_hash(unavailable)
        return unavailable
    train = ordered[:train_end]
    calibration = ordered[train_end:calibration_end]
    validation = ordered[calibration_end:]
    model = _fit_global_logistic(train, target_field)
    global_rate = statistics.fmean(1.0 if bool(row[target_field]) else 0.0 for row in train)
    assets: dict[str, Any] = {}
    symbols: dict[str, Any] = {}
    grouped_assets: dict[str, list[dict[str, Any]]] = {}
    grouped_symbols: dict[str, list[dict[str, Any]]] = {}
    for row in train:
        grouped_assets.setdefault(_asset_class(row), []).append(row)
        grouped_symbols.setdefault(str(row.get("symbol") or "").upper(), []).append(row)
    for asset, rows in sorted(grouped_assets.items()):
        adjustment = _posterior_adjustment(rows, target_field=target_field, parent_probability=global_rate, prior_strength=prior_strength)
        adjustment["eligible"] = len(rows) >= min_asset_sample
        adjustment["authority"] = "shadow" if adjustment["eligible"] else "blocked_insufficient_sample"
        assets[asset] = adjustment
    for symbol, rows in sorted(grouped_symbols.items()):
        asset = _asset_class(rows[0])
        parent = assets.get(asset) or {"posterior_probability": global_rate}
        adjustment = _posterior_adjustment(
            rows,
            target_field=target_field,
            parent_probability=float(parent["posterior_probability"]),
            prior_strength=prior_strength * 2.0,
        )
        uncertainty_width = float(adjustment["uncertainty"]["upper"]) - float(adjustment["uncertainty"]["lower"])
        adjustment["asset_class"] = asset
        adjustment["eligible"] = len(rows) >= min_symbol_sample and uncertainty_width <= 0.30
        adjustment["authority"] = "shadow" if adjustment["eligible"] else "blocked_insufficient_sample_or_uncertainty"
        symbols[symbol] = adjustment
    calibration_by_asset: dict[str, Any] = {}
    all_oos_assets = sorted({_asset_class(row) for row in calibration + validation})
    for asset in all_oos_assets:
        calibration_rows = [row for row in calibration if _asset_class(row) == asset]
        validation_rows = [row for row in validation if _asset_class(row) == asset]
        calibration_predictions = [
            _predict_with_asset_adjustment(model, row, assets) for row in calibration_rows
        ]
        calibration_parent = (
            statistics.fmean(calibration_predictions)
            if calibration_predictions
            else float((assets.get(asset) or {}).get("posterior_probability", global_rate))
        )
        calibrator = _posterior_adjustment(
            calibration_rows,
            target_field=target_field,
            parent_probability=calibration_parent,
            prior_strength=prior_strength,
        )
        calibrator_eligible = len(calibration_rows) >= min_asset_sample
        calibration_offset = float(calibrator["logit_adjustment"]) if calibrator_eligible else 0.0
        probabilities: list[float] = []
        outcomes: list[float] = []
        for row in validation_rows:
            probability = _predict_with_asset_adjustment(model, row, assets)
            probability = _sigmoid(_logit(probability) + calibration_offset)
            probabilities.append(probability)
            outcomes.append(1.0 if bool(row[target_field]) else 0.0)
        brier = (
            statistics.fmean((prob - outcome) ** 2 for prob, outcome in zip(probabilities, outcomes))
            if probabilities
            else None
        )
        observed = statistics.fmean(outcomes) if outcomes else None
        predicted = statistics.fmean(probabilities) if probabilities else None
        calibration_gap = abs(predicted - observed) if predicted is not None and observed is not None else None
        eligible = bool(
            calibrator_eligible
            and len(validation_rows) >= min_asset_sample
            and brier is not None
            and brier <= 0.30
            and calibration_gap is not None
            and calibration_gap <= 0.15
        )
        calibration_by_asset[asset] = {
            "calibration_sample_size": len(calibration_rows),
            "validation_sample_size": len(validation_rows),
            "calibration_parent_probability": calibration_parent,
            "calibration_posterior_probability": calibrator["posterior_probability"],
            "calibration_logit_offset": calibration_offset,
            "calibration_shrinkage_weight": calibrator["shrinkage_weight"],
            "calibration_uncertainty": calibrator["uncertainty"],
            "mean_predicted_probability": predicted,
            "observed_event_rate": observed,
            "brier_score": brier,
            "calibration_gap": calibration_gap,
            "eligible": eligible,
            "authority": "shadow" if eligible else "blocked_oos_calibration_gate",
        }
    blocks: list[str] = []
    if not calibration_by_asset:
        blocks.append("asset_class_oos_calibration_unavailable")
    if any(not row["eligible"] for row in calibration_by_asset.values()):
        blocks.append("asset_class_oos_sample_insufficient")
    artifact = {
        "schema_version": HIERARCHICAL_OUTCOME_MODEL_VERSION,
        "status": "validated_shadow" if not blocks else "shadow_blocked",
        "shadow_only": True,
        "trading_authority": False,
        "target_field": target_field,
        "feature_version": str(clean[0].get("feature_version") or ""),
        "taxonomy_version": str(clean[0].get("taxonomy_version") or ""),
        "cohort_id": cohort_id,
        "input_record_count": len(records),
        "clean_record_count": len(clean),
        "rejected_record_count": len(records) - len(clean),
        "global_model": model,
        "global_prior": {"event_rate": global_rate, "sample_size": len(train)},
        "asset_class_adjustments": assets,
        "symbol_adjustments": symbols,
        "asset_class_calibration": calibration_by_asset,
        "calibration_method": "asset_class_bayesian_logit_offset_chronological_oos",
        "train_window": [int(train[0].get("opened_at") or 0), int(train[-1].get("opened_at") or 0)],
        "calibration_window": [int(calibration[0].get("opened_at") or 0), int(calibration[-1].get("opened_at") or 0)],
        "validation_window": [int(validation[0].get("opened_at") or 0), int(validation[-1].get("opened_at") or 0)],
        "activation_block_reasons": blocks + ["shadow_only_explicit_activation_required"],
        "generated_at": utc_now(),
    }
    artifact["artifact_hash"] = canonical_hash(artifact)
    return artifact


def _fit_ridge_linear(
    records: Sequence[Mapping[str, Any]],
    *,
    feature_names: Sequence[str],
    target_field: str,
) -> dict[str, Any]:
    matrix = [[_number(row, name) for name in feature_names] for row in records]
    targets = [_number(row, target_field) for row in records]
    centers = [statistics.fmean(column) for column in zip(*matrix)]
    scales = [max(statistics.pstdev(column), 1e-6) for column in zip(*matrix)]
    normalized = [
        [(value - centers[index]) / scales[index] for index, value in enumerate(row)]
        for row in matrix
    ]
    intercept = statistics.fmean(targets)
    coefficients = [0.0] * len(feature_names)
    count = float(len(records))
    for _ in range(1200):
        intercept_gradient = 0.0
        coefficient_gradient = [0.0] * len(coefficients)
        for features, target in zip(normalized, targets):
            prediction = intercept + sum(weight * value for weight, value in zip(coefficients, features))
            error = prediction - target
            intercept_gradient += error
            for index, value in enumerate(features):
                coefficient_gradient[index] += error * value
        intercept -= 0.02 * intercept_gradient / count
        for index in range(len(coefficients)):
            coefficients[index] -= 0.02 * (coefficient_gradient[index] / count + 0.35 * coefficients[index])
    return {
        "feature_names": list(feature_names),
        "centers": centers,
        "scales": scales,
        "intercept": intercept,
        "coefficients": coefficients,
        "l2": 0.35,
    }


def _predict_ridge_linear(model: Mapping[str, Any], row: Mapping[str, Any]) -> float:
    features = [
        (_number(row, name) - float(model["centers"][index])) / float(model["scales"][index])
        for index, name in enumerate(model["feature_names"])
    ]
    return float(model["intercept"]) + sum(
        float(weight) * value for weight, value in zip(model["coefficients"], features)
    )


def _continuous_entry_shadow_artifact(
    records: Sequence[Mapping[str, Any]],
    *,
    target_field: str,
    min_clean_sample: int,
) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    for source in records:
        if not _clean_exact_homogeneous_pre_entry(source):
            continue
        try:
            target = float(source.get(target_field))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(target):
            continue
        row = dict(source)
        row[target_field] = target
        eligible.append(row)
    artifact: dict[str, Any] = {
        "schema_version": ENTRY_MODEL_VERSION,
        "target_field": target_field,
        "target_definition": ENTRY_TARGET_DEFINITIONS[target_field],
        "status": "shadow_unavailable",
        "shadow_only": True,
        "trading_authority": False,
        "input_record_count": len(records),
        "clean_target_sample_count": len(eligible),
        "model": None,
        "oos_report": None,
        "train_window": None,
        "calibration_window": None,
        "validation_window": None,
        "activation_block_reasons": [],
        "generated_at": utc_now(),
    }
    if len(eligible) < min_clean_sample:
        artifact["activation_block_reasons"] = ["minimum_clean_target_sample_not_met"]
        artifact["artifact_hash"] = canonical_hash(artifact)
        return artifact
    try:
        cohort_id = require_homogeneous_cohort(eligible)
    except ValueError as exc:
        artifact["status"] = "shadow_blocked"
        artifact["activation_block_reasons"] = [str(exc)]
        artifact["cohort_counts"] = {
            key: len(value) for key, value in partition_homogeneous_cohorts(eligible).items()
        }
        artifact["artifact_hash"] = canonical_hash(artifact)
        return artifact
    ordered = sorted(eligible, key=lambda row: int(row.get("opened_at") or row.get("candidate_timestamp") or 0))
    train_end = max(40, int(len(ordered) * 0.60))
    calibration_end = max(train_end + 10, int(len(ordered) * 0.80))
    if calibration_end >= len(ordered):
        artifact["activation_block_reasons"] = ["chronological_oos_windows_unavailable"]
        artifact["cohort_id"] = cohort_id
        artifact["artifact_hash"] = canonical_hash(artifact)
        return artifact
    train = ordered[:train_end]
    calibration = ordered[train_end:calibration_end]
    validation = ordered[calibration_end:]
    model = _fit_ridge_linear(train, feature_names=PRE_ENTRY_FEATURE_NAMES, target_field=target_field)
    calibration_residuals = [
        float(row[target_field]) - _predict_ridge_linear(model, row) for row in calibration
    ]
    offset = statistics.fmean(calibration_residuals) if calibration_residuals else 0.0
    model["calibration_offset"] = offset
    predictions = [_predict_ridge_linear(model, row) + offset for row in validation]
    actual = [float(row[target_field]) for row in validation]
    errors = [predicted - observed for predicted, observed in zip(predictions, actual)]
    mae = statistics.fmean(abs(value) for value in errors)
    rmse = math.sqrt(statistics.fmean(value * value for value in errors))
    baseline = statistics.fmean(float(row[target_field]) for row in train)
    baseline_mae = statistics.fmean(abs(value - baseline) for value in actual)
    blocks = ["shadow_only_explicit_activation_required"]
    if len(validation) < max(10, min_clean_sample // 5):
        blocks.insert(0, "continuous_target_oos_sample_insufficient")
    if baseline_mae - mae <= 0.0:
        blocks.insert(0, "continuous_target_no_oos_improvement_over_train_mean")
    artifact.update(
        {
            "status": "validated_shadow" if len(blocks) == 1 else "shadow_blocked",
            "cohort_id": cohort_id,
            "model": model,
            "oos_report": {
                "calibration_sample_size": len(calibration),
                "validation_sample_size": len(validation),
                "mae": mae,
                "rmse": rmse,
                "train_mean_baseline_mae": baseline_mae,
                "mae_improvement_over_train_mean": baseline_mae - mae,
            },
            "train_window": [int(train[0].get("opened_at") or 0), int(train[-1].get("opened_at") or 0)],
            "calibration_window": [int(calibration[0].get("opened_at") or 0), int(calibration[-1].get("opened_at") or 0)],
            "validation_window": [int(validation[0].get("opened_at") or 0), int(validation[-1].get("opened_at") or 0)],
            "activation_block_reasons": blocks,
        }
    )
    artifact["artifact_hash"] = canonical_hash(artifact)
    return artifact


def _kaplan_meier(rows: Sequence[Mapping[str, Any]], duration_field: str, event_field: str) -> dict[str, Any]:
    observations = sorted(
        (max(0.0, float(row[duration_field])), bool(row[event_field])) for row in rows
    )
    at_risk = len(observations)
    survival = 1.0
    curve: list[dict[str, Any]] = []
    for duration in sorted({item[0] for item in observations}):
        events = sum(1 for value, event in observations if value == duration and event)
        censored = sum(1 for value, event in observations if value == duration and not event)
        if at_risk > 0 and events:
            survival *= 1.0 - events / at_risk
        curve.append(
            {
                "time_minutes": duration,
                "at_risk": at_risk,
                "events": events,
                "censored": censored,
                "survival_probability": survival,
                "cumulative_event_probability": 1.0 - survival,
            }
        )
        at_risk -= events + censored
    median = next((row["time_minutes"] for row in curve if row["survival_probability"] <= 0.5), None)
    return {
        "sample_size": len(observations),
        "event_count": sum(event for _, event in observations),
        "censored_count": sum(not event for _, event in observations),
        "median_event_time_minutes": median,
        "curve": curve,
    }


def _time_to_event_shadow_artifact(
    records: Sequence[Mapping[str, Any]],
    *,
    duration_field: str,
    event_field: str,
    min_clean_sample: int,
    min_asset_sample: int = 30,
    min_symbol_sample: int = 80,
) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    for source in records:
        if not _clean_exact_homogeneous_pre_entry(source) or source.get(event_field) not in {True, False}:
            continue
        try:
            duration = float(source.get(duration_field))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(duration) or duration < 0.0:
            continue
        row = dict(source)
        row[duration_field] = duration
        eligible.append(row)
    artifact: dict[str, Any] = {
        "schema_version": ENTRY_MODEL_VERSION,
        "duration_field": duration_field,
        "event_field": event_field,
        "status": "shadow_unavailable",
        "shadow_only": True,
        "trading_authority": False,
        "censoring_method": "kaplan_meier_right_censoring",
        "input_record_count": len(records),
        "clean_target_sample_count": len(eligible),
        "global": None,
        "asset_class": {},
        "symbol": {},
        "activation_block_reasons": [],
        "generated_at": utc_now(),
    }
    if len(eligible) < min_clean_sample:
        artifact["activation_block_reasons"] = ["minimum_clean_time_to_event_sample_not_met"]
        artifact["artifact_hash"] = canonical_hash(artifact)
        return artifact
    try:
        artifact["cohort_id"] = require_homogeneous_cohort(eligible)
    except ValueError as exc:
        artifact["status"] = "shadow_blocked"
        artifact["activation_block_reasons"] = [str(exc)]
        artifact["cohort_counts"] = {
            key: len(value) for key, value in partition_homogeneous_cohorts(eligible).items()
        }
        artifact["artifact_hash"] = canonical_hash(artifact)
        return artifact
    artifact["global"] = _kaplan_meier(eligible, duration_field, event_field)
    for asset in sorted({_asset_class(row) for row in eligible}):
        subset = [row for row in eligible if _asset_class(row) == asset]
        summary = _kaplan_meier(subset, duration_field, event_field)
        summary["eligible"] = len(subset) >= min_asset_sample
        summary["authority"] = "shadow" if summary["eligible"] else "blocked_insufficient_sample"
        artifact["asset_class"][asset] = summary
    for symbol in sorted({str(row.get("symbol") or "").upper() for row in eligible}):
        subset = [row for row in eligible if str(row.get("symbol") or "").upper() == symbol]
        summary = _kaplan_meier(subset, duration_field, event_field)
        summary["asset_class"] = _asset_class(subset[0])
        summary["shrinkage_weight"] = len(subset) / (len(subset) + 60.0)
        summary["eligible"] = len(subset) >= min_symbol_sample
        summary["authority"] = "shadow" if summary["eligible"] else "blocked_insufficient_sample"
        artifact["symbol"][symbol] = summary
    artifact["status"] = "validated_shadow"
    artifact["activation_block_reasons"] = ["shadow_only_explicit_activation_required"]
    artifact["artifact_hash"] = canonical_hash(artifact)
    return artifact


def _management_row_eligible(row: Mapping[str, Any]) -> bool:
    if not _clean_exact_homogeneous_pre_entry(row):
        return False
    if not bool(row.get("management_features_time_safe", False)):
        return False
    required = (*MANAGEMENT_FEATURE_NAMES, "management_alpha")
    try:
        return all(math.isfinite(float(row.get(name))) for name in required)
    except (TypeError, ValueError, OverflowError):
        return False


def build_entry_and_management_shadow_artifacts(
    records: Sequence[Mapping[str, Any]],
    *,
    min_clean_sample: int = 80,
) -> dict[str, Any]:
    entry_target = "unmanaged_original_plan_target_before_stop"
    entry = build_hierarchical_outcome_artifact(
        records,
        target_field=entry_target,
        min_clean_sample=min_clean_sample,
    )
    binary_event_fields = (
        "unmanaged_original_plan_target_before_stop",
        "unmanaged_mfe_before_mae",
        "unmanaged_reached_0_25r_before_adverse_threshold",
        "unmanaged_reached_0_50r_before_adverse_threshold",
    )
    binary_event_models = {
        field: build_hierarchical_outcome_artifact(
            records,
            target_field=field,
            min_clean_sample=min_clean_sample,
        )
        for field in binary_event_fields
    }
    continuous_target_models = {
        field: _continuous_entry_shadow_artifact(
            records,
            target_field=field,
            min_clean_sample=min_clean_sample,
        )
        for field in (
            "unmanaged_original_plan_net_r",
            "unmanaged_mfe_r",
            "unmanaged_mae_r",
        )
    }
    time_to_event_models = {
        "time_to_0_25r": _time_to_event_shadow_artifact(
            records,
            duration_field="unmanaged_time_to_0_25r_or_censor_minutes",
            event_field="unmanaged_reached_0_25r",
            min_clean_sample=min_clean_sample,
        ),
        "time_to_0_50r": _time_to_event_shadow_artifact(
            records,
            duration_field="unmanaged_time_to_0_50r_or_censor_minutes",
            event_field="unmanaged_reached_0_50r",
            min_clean_sample=min_clean_sample,
        ),
        "time_to_invalidation": _time_to_event_shadow_artifact(
            records,
            duration_field="unmanaged_time_to_invalidation_or_censor_minutes",
            event_field="unmanaged_adverse_threshold_reached",
            min_clean_sample=min_clean_sample,
        ),
    }
    management_rows = [dict(row) for row in records if _management_row_eligible(row)]
    management_status = "shadow_unavailable"
    management_blocks: list[str] = []
    management_model: dict[str, Any] | None = None
    management_oos: dict[str, Any] | None = None
    train_window: list[int] | None = None
    calibration_window: list[int] | None = None
    validation_window: list[int] | None = None
    cohort_id = ""
    management_cohort_counts: dict[str, int] = {}
    management_clean_count = len(management_rows)
    if len(management_rows) < min_clean_sample:
        management_blocks.append("minimum_clean_management_sample_not_met")
    else:
        try:
            cohort_id = require_homogeneous_cohort(management_rows)
        except ValueError as exc:
            management_blocks.append(str(exc))
            management_cohort_counts = {
                key: len(value) for key, value in partition_homogeneous_cohorts(management_rows).items()
            }
        if not management_blocks:
            ordered = sorted(
                management_rows,
                key=lambda row: int(row.get("management_decision_at") or row.get("closed_at") or row.get("opened_at") or 0),
            )
            train_end = max(40, int(len(ordered) * 0.60))
            calibration_end = max(train_end + 10, int(len(ordered) * 0.80))
            if calibration_end >= len(ordered):
                management_blocks.append("chronological_management_oos_windows_unavailable")
            else:
                train = ordered[:train_end]
                calibration = ordered[train_end:calibration_end]
                validation = ordered[calibration_end:]
                management_model = _fit_ridge_linear(
                    train,
                    feature_names=MANAGEMENT_FEATURE_NAMES,
                    target_field="management_alpha",
                )
                calibration_residuals = [
                    float(row["management_alpha"]) - _predict_ridge_linear(management_model, row)
                    for row in calibration
                ]
                calibration_offset = statistics.fmean(calibration_residuals) if calibration_residuals else 0.0
                predictions = [
                    _predict_ridge_linear(management_model, row) + calibration_offset for row in validation
                ]
                actual = [float(row["management_alpha"]) for row in validation]
                errors = [predicted - observed for predicted, observed in zip(predictions, actual)]
                mae = statistics.fmean(abs(value) for value in errors)
                rmse = math.sqrt(statistics.fmean(value * value for value in errors))
                baseline_mae = statistics.fmean(abs(value) for value in actual)
                sign_accuracy = statistics.fmean(
                    1.0 if (predicted >= 0.0) == (observed >= 0.0) else 0.0
                    for predicted, observed in zip(predictions, actual)
                )
                management_model["calibration_offset"] = calibration_offset
                management_oos = {
                    "calibration_sample_size": len(calibration),
                    "validation_sample_size": len(validation),
                    "mae": mae,
                    "rmse": rmse,
                    "zero_intervention_baseline_mae": baseline_mae,
                    "mae_improvement_over_zero_baseline": baseline_mae - mae,
                    "sign_accuracy": sign_accuracy,
                    "mean_predicted_management_alpha": statistics.fmean(predictions),
                    "mean_observed_management_alpha": statistics.fmean(actual),
                }
                time_field = lambda row: int(row.get("management_decision_at") or row.get("closed_at") or row.get("opened_at") or 0)
                train_window = [time_field(train[0]), time_field(train[-1])]
                calibration_window = [time_field(calibration[0]), time_field(calibration[-1])]
                validation_window = [time_field(validation[0]), time_field(validation[-1])]
                if len(validation) < max(10, min_clean_sample // 5):
                    management_blocks.append("management_oos_sample_insufficient")
                if baseline_mae - mae <= 0.0:
                    management_blocks.append("management_model_no_oos_alpha_over_zero_baseline")
                if not management_blocks:
                    management_status = "validated_shadow"
                management_blocks.append("shadow_only_explicit_activation_required")
    if management_clean_count >= min_clean_sample and management_blocks and management_status == "shadow_unavailable":
        management_status = "shadow_blocked"
    management = {
        "schema_version": MANAGEMENT_MODEL_VERSION,
        "status": management_status,
        "shadow_only": True,
        "trading_authority": False,
        "target": "management_alpha_incremental_remaining_outcome",
        "feature_timing": "management_decision_time_only",
        "feature_manifest": {
            "feature_names": list(MANAGEMENT_FEATURE_NAMES),
            "future_or_post_decision_features_forbidden": True,
        },
        "cohort_id": cohort_id,
        "input_record_count": len(records),
        "clean_time_safe_sample_count": management_clean_count,
        "cohort_counts": management_cohort_counts,
        "model": management_model,
        "oos_report": management_oos,
        "train_window": train_window,
        "calibration_window": calibration_window,
        "validation_window": validation_window,
        "activation_block_reasons": list(dict.fromkeys(management_blocks)),
        "generated_at": utc_now(),
    }
    management["artifact_hash"] = canonical_hash(management)
    return {
        "entry_model": {
            "model_version": ENTRY_MODEL_VERSION,
            "target": entry_target,
            "feature_timing": "immutable_pre_entry_only",
            "target_definitions": ENTRY_TARGET_DEFINITIONS,
            "feature_manifest": {
                "feature_names": list(PRE_ENTRY_FEATURE_NAMES),
                "managed_realized_pnl_forbidden": True,
            },
            "artifact": entry,
            "binary_event_models": binary_event_models,
            "continuous_target_models": continuous_target_models,
            "time_to_event_models": time_to_event_models,
            "account_percentage_role": "provider_reporting_only",
        },
        "management_model": management,
    }


def complete_shadow_candidate(
    candidate: Mapping[str, Any],
    *,
    future_bars: Sequence[Mapping[str, Any]],
    config: OutcomeTargetConfig | None = None,
) -> dict[str, Any]:
    required = (
        "candidate_timestamp",
        "entry",
        "sl",
        "tp2",
        "direction",
        "candidate_hash",
        "execution_fingerprint",
        "decision_stage",
        "model_raw_allow",
        "python_final_allow",
        "mql_final_allow",
    )
    missing = [field for field in required if field not in candidate or candidate.get(field) is None]
    if missing:
        raise ValueError("shadow_candidate_missing_fields:" + ",".join(missing))
    result = evaluate_path_targets(
        direction=str(candidate["direction"]),
        entry=float(candidate["entry"]),
        stop=float(candidate["sl"]),
        target=float(candidate["tp2"]),
        entry_time=int(candidate["candidate_timestamp"]),
        bars=future_bars,
        config=config,
    )
    output = dict(candidate)
    output.update(result)
    gross_result = result.get("unmanaged_original_plan_net_r_gross")
    if gross_result is None:
        output["unmanaged_original_plan_net_r"] = None
    else:
        try:
            cost_r = max(0.0, float(candidate.get("execution_cost_r") or 0.0))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("shadow_candidate_execution_cost_invalid")
        output["unmanaged_original_plan_net_r"] = float(gross_result) - cost_r
    output["pre_entry_features_complete"] = all(
        output.get(name) is not None for name in PRE_ENTRY_FEATURE_NAMES
    )
    output["shadow_outcome_schema_version"] = SHADOW_OUTCOME_SCHEMA_VERSION
    output["shadow_only"] = True
    output["trading_authority"] = False
    output["cohort"] = cohort_metadata(output)
    output["shadow_record_hash"] = canonical_hash({key: value for key, value in output.items() if key != "shadow_record_hash"})
    return output


def _shadow_execution_fingerprint(row: Mapping[str, Any]) -> str:
    for field in (
        "execution_fingerprint",
        "candidate_execution_fingerprint",
        "request_execution_fingerprint",
    ):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _seconds_to_minutes(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds < 0.0:
        return None
    return seconds / 60.0


def consolidate_shadow_event_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Join the append-only shadow stream without candidate-only matching.

    The parent record hash binds the path outcome to one observation, while
    candidate hash plus request execution fingerprint binds decision updates.
    Legacy rows without those identities remain diagnostic and are rejected
    from model input rather than guessed into a candidate.
    """

    observed_by_hash: dict[str, dict[str, Any]] = {}
    decision_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    def reject(index: int, reason: str, row: Mapping[str, Any]) -> None:
        rejected.append(
            {
                "row_index": index,
                "reason": reason,
                "event_type": str(row.get("event_type") or ""),
                "candidate_hash": str(row.get("candidate_hash") or ""),
            }
        )

    for index, source in enumerate(rows, 1):
        row = dict(source)
        event = str(row.get("event_type") or "")
        candidate_hash = str(row.get("candidate_hash") or "").strip()
        fingerprint = _shadow_execution_fingerprint(row)
        if event == "candidate_observed":
            record_hash = str(row.get("record_hash") or "").strip()
            if not record_hash or not candidate_hash or not fingerprint:
                reject(index, "shadow_observation_identity_incomplete", row)
                continue
            if record_hash in observed_by_hash:
                reject(index, "duplicate_shadow_parent_record_hash", row)
                continue
            row["execution_fingerprint"] = fingerprint
            observed_by_hash[record_hash] = row
            continue
        if event == "candidate_decision_update":
            if not candidate_hash or not fingerprint:
                reject(index, "shadow_decision_identity_incomplete", row)
                continue
            row["execution_fingerprint"] = fingerprint
            decision_by_identity[(candidate_hash, fingerprint)] = row
            continue
        if event != "hypothetical_outcome_resolution":
            reject(index, "unknown_shadow_event_type", row)
            continue

        parent_hash = str(row.get("parent_record_hash") or "").strip()
        parent = observed_by_hash.get(parent_hash)
        if parent is None:
            reject(index, "shadow_outcome_parent_missing", row)
            continue
        parent_candidate = str(parent.get("candidate_hash") or "").strip()
        parent_fingerprint = _shadow_execution_fingerprint(parent)
        if not candidate_hash or candidate_hash != parent_candidate:
            reject(index, "shadow_outcome_candidate_hash_mismatch", row)
            continue
        if not fingerprint or fingerprint != parent_fingerprint:
            reject(index, "shadow_outcome_execution_fingerprint_mismatch", row)
            continue
        decision = decision_by_identity.get((candidate_hash, parent_fingerprint), {})
        consolidated = {**parent, **decision, **row}
        consolidated["execution_fingerprint"] = parent_fingerprint
        consolidated["shadow_join_status"] = "EXACT_PARENT_CANDIDATE_FINGERPRINT"
        consolidated["direction"] = str(consolidated.get("direction") or (
            "BUY" if bool(consolidated.get("is_buy")) else "SELL"
        ))
        consolidated["unmanaged_original_plan_target_before_stop"] = row.get("target_before_stop")
        consolidated["unmanaged_original_plan_net_r"] = row.get("result_r")
        consolidated["unmanaged_mfe_before_mae"] = (
            None
            if bool(row.get("threshold_0_25_order_ambiguous"))
            else row.get("reached_0_25r_before_adverse_threshold")
        )
        consolidated["unmanaged_reached_0_25r_before_adverse_threshold"] = row.get(
            "reached_0_25r_before_adverse_threshold"
        )
        consolidated["unmanaged_reached_0_50r_before_adverse_threshold"] = row.get(
            "reached_0_50r_before_adverse_threshold"
        )
        try:
            consolidated["unmanaged_mfe_r"] = max(0.0, float(row.get("mfe_r")))
            consolidated["unmanaged_mae_r"] = abs(float(row.get("mae_r")))
        except (TypeError, ValueError, OverflowError):
            consolidated["unmanaged_mfe_r"] = None
            consolidated["unmanaged_mae_r"] = None
        observed_minutes = _seconds_to_minutes(row.get("time_to_event_sec"))
        t025 = _seconds_to_minutes(row.get("time_to_0_25r_sec"))
        t050 = _seconds_to_minutes(row.get("time_to_0_50r_sec"))
        tinvalid = _seconds_to_minutes(row.get("time_to_adverse_threshold_sec"))
        reached_025 = bool(row.get("reached_0_25r"))
        reached_050 = bool(row.get("reached_0_50r"))
        adverse_reached = tinvalid is not None
        consolidated.update(
            {
                "unmanaged_reached_0_25r": reached_025,
                "unmanaged_reached_0_50r": reached_050,
                "unmanaged_adverse_threshold_reached": adverse_reached,
                "unmanaged_observed_path_minutes": observed_minutes,
                "unmanaged_time_to_0_25r_minutes": t025,
                "unmanaged_time_to_0_50r_minutes": t050,
                "unmanaged_time_to_invalidation_minutes": tinvalid,
                "unmanaged_time_to_0_25r_or_censor_minutes": t025 if reached_025 else observed_minutes,
                "unmanaged_time_to_0_50r_or_censor_minutes": t050 if reached_050 else observed_minutes,
                "unmanaged_time_to_invalidation_or_censor_minutes": tinvalid if adverse_reached else observed_minutes,
            }
        )
        consolidated["pre_entry_features_complete"] = all(
            consolidated.get(name) is not None for name in PRE_ENTRY_FEATURE_NAMES
        )
        outcomes.append(consolidated)
    return outcomes, rejected


def merge_completed_and_shadow_records(
    completed_records: Sequence[Mapping[str, Any]],
    shadow_outcomes: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach broker outcomes only through exact candidate/final-fingerprint identity."""

    index: dict[tuple[str, str], list[int]] = {}
    for shadow_index, row in enumerate(shadow_outcomes):
        candidate_hash = str(row.get("candidate_hash") or "").strip()
        final_fingerprint = str(row.get("final_execution_fingerprint") or "").strip()
        if candidate_hash and final_fingerprint:
            index.setdefault((candidate_hash, final_fingerprint), []).append(shadow_index)
    consumed: set[int] = set()
    merged: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unmanaged_fields = tuple(
        name for name in ENTRY_TARGET_DEFINITIONS if name != "business_report_label" and name != "management_alpha"
    ) + (
        "unmanaged_reached_0_25r",
        "unmanaged_reached_0_50r",
        "unmanaged_adverse_threshold_reached",
        "unmanaged_observed_path_minutes",
        "unmanaged_time_to_0_25r_or_censor_minutes",
        "unmanaged_time_to_0_50r_or_censor_minutes",
        "unmanaged_time_to_invalidation_or_censor_minutes",
    )
    for completed_index, source in enumerate(completed_records):
        completed = dict(source)
        candidate_hash = str(completed.get("candidate_hash") or "").strip()
        final_fingerprint = str(completed.get("final_execution_fingerprint") or "").strip()
        matches = index.get((candidate_hash, final_fingerprint), []) if candidate_hash and final_fingerprint else []
        if len(matches) == 1:
            shadow_index = matches[0]
            consumed.add(shadow_index)
            row = {**dict(shadow_outcomes[shadow_index]), **completed}
            for field in unmanaged_fields:
                if row.get(field) is None and shadow_outcomes[shadow_index].get(field) is not None:
                    row[field] = shadow_outcomes[shadow_index].get(field)
            row["completed_shadow_join_status"] = "EXACT_CANDIDATE_FINAL_FINGERPRINT"
            merged.append(row)
            continue
        merged.append(completed)
        rejected.append(
            {
                "completed_index": completed_index,
                "candidate_hash": candidate_hash,
                "final_execution_fingerprint": final_fingerprint,
                "reason": (
                    "completed_shadow_identity_incomplete"
                    if not candidate_hash or not final_fingerprint
                    else "completed_shadow_exact_match_missing"
                    if not matches
                    else "completed_shadow_exact_match_ambiguous"
                ),
            }
        )
    merged.extend(dict(row) for index_value, row in enumerate(shadow_outcomes) if index_value not in consumed)
    return merged, rejected


def compare_shadow_decision_groups(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, list[float]]] = {
        key: {"target": [], "net_r": []}
        for key in ("approved", "rejected", "abstained", "policy_blocked")
    }
    for row in records:
        outcome = row.get("target_before_stop")
        if str(row.get("decision_state") or "").upper() == "ABSTAIN":
            key = "abstained"
        elif bool(row.get("mql_final_allow")):
            key = "approved"
        elif str(row.get("decision_stage") or "").lower() in {"pre_ai_hard_gate", "policy", "bucket_policy"}:
            key = "policy_blocked"
        else:
            key = "rejected"
        if outcome in {True, False}:
            groups[key]["target"].append(1.0 if outcome else 0.0)
        try:
            net_r = float(row.get("unmanaged_original_plan_net_r", row.get("result_r")))
        except (TypeError, ValueError, OverflowError):
            net_r = math.nan
        if math.isfinite(net_r):
            groups[key]["net_r"].append(net_r)
    summary: dict[str, Any] = {}
    for key, values in groups.items():
        summary[key] = {
            "target_outcome_count": len(values["target"]),
            "target_before_stop_rate": statistics.fmean(values["target"]) if values["target"] else None,
            "net_r_count": len(values["net_r"]),
            "mean_unmanaged_net_r": statistics.fmean(values["net_r"]) if values["net_r"] else None,
            "median_unmanaged_net_r": statistics.median(values["net_r"]) if values["net_r"] else None,
        }
        summary[key]["count"] = max(summary[key]["target_outcome_count"], summary[key]["net_r_count"])
    all_targets = [value for group in groups.values() for value in group["target"]]
    all_net_r = [value for group in groups.values() for value in group["net_r"]]
    baseline_target = statistics.fmean(all_targets) if all_targets else None
    baseline_net_r = statistics.fmean(all_net_r) if all_net_r else None
    approved_target = summary["approved"]["target_before_stop_rate"]
    approved_net_r = summary["approved"]["mean_unmanaged_net_r"]
    rejected_target = summary["rejected"]["target_before_stop_rate"]
    summary["deterministic_candidate_baseline"] = {
        "target_outcome_count": len(all_targets),
        "target_before_stop_rate": baseline_target,
        "net_r_count": len(all_net_r),
        "mean_unmanaged_net_r": baseline_net_r,
    }
    summary["approved_minus_rejected_target_before_stop_rate"] = (
        None if approved_target is None or rejected_target is None else approved_target - rejected_target
    )
    summary["ai_added_value_target_before_stop_rate"] = (
        None if approved_target is None or baseline_target is None else approved_target - baseline_target
    )
    summary["ai_added_value_mean_unmanaged_net_r"] = (
        None if approved_net_r is None or baseline_net_r is None else approved_net_r - baseline_net_r
    )
    summary["false_rejection_cost_positive_unmanaged_r"] = sum(
        max(0.0, value)
        for key in ("rejected", "abstained", "policy_blocked")
        for value in groups[key]["net_r"]
    )
    summary["false_approval_cost_adverse_unmanaged_r"] = sum(
        max(0.0, -value) for value in groups["approved"]["net_r"]
    )
    calibrated = []
    for row in records:
        if not bool(row.get("calibration_available")):
            continue
        try:
            probability = float(row.get("calibrated_win_probability"))
        except (TypeError, ValueError, OverflowError):
            continue
        outcome = row.get("target_before_stop")
        if math.isfinite(probability) and 0.0 <= probability <= 1.0 and outcome in {True, False}:
            calibrated.append((probability, 1.0 if outcome else 0.0))
    summary["calibration_monotonicity"] = {
        "available": False,
        "reason": "calibrated_probability_unavailable" if not calibrated else "insufficient_calibration_buckets",
        "sample_size": len(calibrated),
        "buckets": [],
        "monotonic": None,
    }
    if len(calibrated) >= 20:
        buckets: list[dict[str, Any]] = []
        for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
            values = [outcome for probability, outcome in calibrated if lower <= probability < lower + 0.2 or (lower == 0.8 and probability == 1.0)]
            if values:
                buckets.append({"lower": lower, "upper": lower + 0.2, "count": len(values), "event_rate": statistics.fmean(values)})
        rates = [bucket["event_rate"] for bucket in buckets]
        summary["calibration_monotonicity"] = {
            "available": len(buckets) >= 2,
            "reason": "available" if len(buckets) >= 2 else "insufficient_calibration_buckets",
            "sample_size": len(calibrated),
            "buckets": buckets,
            "monotonic": all(left <= right for left, right in zip(rates, rates[1:])) if len(rates) >= 2 else None,
        }
    summary["account_percentage_role"] = "provider_risk_reporting_only_not_entry_target"
    return summary


@dataclass(frozen=True)
class PolicySpec:
    policy_type: str
    policy_id: str
    path: Path
    enabled: bool
    requested_authority: str = "shadow"
    expected_schema: str = ""
    expected_runtime_input_hash: str = ""
    expected_decision_schema: str = ""
    expected_taxonomy: str = ""
    stale_after_days: int = 30


def policy_manifest_entry(
    spec: PolicySpec,
    *,
    ledger_integrity_status: str,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    entry: dict[str, Any] = {
        "policy_type": spec.policy_type,
        "policy_id": spec.policy_id,
        "enabled": spec.enabled,
        "absolute_path": str(spec.path.resolve()),
        "file_hash": "",
        "schema_version": "",
        "activation_state": "disabled" if not spec.enabled else "requested",
        "shadow_state": spec.requested_authority != "active",
        "rows_loaded": 0,
        "rows_rejected": 0,
        "rejection_reasons": [],
        "code_compatibility": False,
        "runtime_input_compatibility": False,
        "decision_schema_compatibility": False,
        "taxonomy_compatibility": False,
        "ledger_integrity_status": ledger_integrity_status,
        "data_window": {},
        "stale": True,
        "authority": "blocked",
        "status": "missing",
    }
    if not spec.path.is_file():
        entry["rejection_reasons"] = ["policy_file_missing"] if spec.enabled else []
        entry["authority"] = "blocked" if spec.enabled else "shadow"
        return entry
    try:
        if spec.path.suffix.lower() in {".ndjson", ".jsonl"}:
            parsed_rows = [
                strict_json_loads(line)
                for line in spec.path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
            payload = {
                "schema_version": spec.expected_schema,
                "rows": parsed_rows,
                "generated_at": datetime.fromtimestamp(spec.path.stat().st_mtime, timezone.utc).isoformat(),
            }
        else:
            payload = strict_json_load(spec.path)
    except Exception as exc:
        entry["status"] = "invalid"
        entry["rejection_reasons"] = [f"policy_json_invalid:{exc}"]
        return entry
    entry["file_hash"] = file_sha256(spec.path)
    entry["schema_version"] = str(payload.get("schema_version") or payload.get("version") or "")
    rows = payload.get("rows")
    if isinstance(rows, list):
        entry["rows_loaded"] = len([row for row in rows if isinstance(row, Mapping)])
        entry["rows_rejected"] = len(rows) - entry["rows_loaded"]
    elif isinstance(payload.get("levels"), Mapping):
        entry["rows_loaded"] = sum(len(value) for value in payload["levels"].values() if isinstance(value, Mapping))
    elif isinstance(payload.get("asset_classes"), Mapping):
        entry["rows_loaded"] = len(payload["asset_classes"])
    entry["data_window"] = payload.get("data_window") if isinstance(payload.get("data_window"), Mapping) else {}
    generated = str(payload.get("generated_at") or "")
    generated_epoch = spec.path.stat().st_mtime
    if generated:
        try:
            generated_epoch = datetime.fromisoformat(generated.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    entry["stale"] = now - generated_epoch > max(1, spec.stale_after_days) * 86400
    entry["code_compatibility"] = not spec.expected_schema or entry["schema_version"] == spec.expected_schema
    artifact_runtime = str(payload.get("runtime_input_hash") or "")
    entry["runtime_input_compatibility"] = not spec.expected_runtime_input_hash or artifact_runtime == spec.expected_runtime_input_hash
    artifact_decision = str(payload.get("decision_schema_version") or "")
    entry["decision_schema_compatibility"] = not spec.expected_decision_schema or artifact_decision == spec.expected_decision_schema
    artifact_taxonomy = str(payload.get("taxonomy_version") or "")
    entry["taxonomy_compatibility"] = not spec.expected_taxonomy or artifact_taxonomy == spec.expected_taxonomy
    reasons: list[str] = []
    if not entry["code_compatibility"]:
        reasons.append("code_schema_incompatible")
    if not entry["runtime_input_compatibility"]:
        reasons.append("runtime_input_incompatible")
    if not entry["decision_schema_compatibility"]:
        reasons.append("decision_schema_incompatible")
    if not entry["taxonomy_compatibility"]:
        reasons.append("taxonomy_incompatible")
    if entry["stale"]:
        reasons.append("policy_stale")
    clean = ledger_integrity_status.lower() in {"clean", "verified_clean", "reconciled_clean"}
    if spec.requested_authority == "active" and not clean:
        reasons.append("ledger_not_clean")
    entry["rejection_reasons"] = reasons
    entry["status"] = "compatible" if not reasons else "blocked"
    if not spec.enabled:
        entry["authority"] = "shadow"
        entry["activation_state"] = "disabled"
    elif spec.requested_authority == "active" and not reasons:
        entry["authority"] = "active"
        entry["activation_state"] = "active"
    else:
        entry["authority"] = "shadow" if not reasons else "blocked"
        entry["activation_state"] = "shadow" if not reasons else "blocked"
    return entry


def build_startup_policy_manifest(
    specs: Sequence[PolicySpec],
    *,
    ledger_integrity_status: str,
    runtime_input_hash: str,
) -> dict[str, Any]:
    rows = [policy_manifest_entry(spec, ledger_integrity_status=ledger_integrity_status) for spec in specs]
    manifest = {
        "schema_version": POLICY_MANIFEST_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "ledger_integrity_status": ledger_integrity_status,
        "runtime_input_hash": runtime_input_hash,
        "policies": rows,
        "counts": {
            "active": sum(row["authority"] == "active" for row in rows),
            "shadow": sum(row["authority"] == "shadow" for row in rows),
            "blocked": sum(row["authority"] == "blocked" for row in rows),
        },
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    return manifest


class FileBusLifecycle:
    """Atomic, single-owner file-bus lifecycle with terminal archives."""

    def __init__(self, root: Path, session_id: str) -> None:
        self.root = Path(root)
        self.session_id = session_id
        self.directories = {
            name: self.root / name
            for name in (
                "requests",
                "responses",
                "processing",
                "completed",
                "rejected",
                "timed_out",
                "stale",
                "quarantined",
                "shutdown",
            )
        }
        self.counters = {name: 0 for name in (*self.directories, "recovered", "duplicates")}

    def ensure(self) -> None:
        for directory in self.directories.values():
            directory.mkdir(parents=True, exist_ok=True)

    def submit_request(self, request_id: str, payload: Mapping[str, Any]) -> Path:
        self.ensure()
        path = self.directories["requests"] / f"{request_id}.json"
        if path.exists():
            self.counters["duplicates"] += 1
            raise FileExistsError(f"duplicate_request:{request_id}")
        atomic_write_json(path, payload)
        self.counters["requests"] += 1
        return path

    def claim(self, request_path: Path) -> Path:
        self.ensure()
        target = self.directories["processing"] / f"{self.session_id}__{request_path.name}"
        if target.exists():
            raise FileExistsError(f"duplicate_processing_claim:{target.name}")
        os.replace(request_path, target)
        self.counters["processing"] += 1
        return target

    def write_response(self, request_id: str, response: Mapping[str, Any]) -> Path:
        path = self.directories["responses"] / f"{request_id}.json"
        atomic_write_json(path, response)
        self.counters["responses"] += 1
        return path

    def archive(self, artifact: Path, state: str, *, reason: str = "") -> Path:
        if state not in FILE_BUS_TERMINAL_STATES:
            raise ValueError(f"invalid_file_bus_terminal_state:{state}")
        self.ensure()
        target = self.directories[state] / artifact.name
        if target.exists():
            target = self.directories[state] / f"{time.time_ns()}__{artifact.name}"
        os.replace(artifact, target)
        if reason:
            atomic_write_json(target.with_suffix(target.suffix + ".meta.json"), {
                "file_bus_lifecycle_version": FILE_BUS_LIFECYCLE_VERSION,
                "session_id": self.session_id,
                "terminal_state": state,
                "reason": reason,
                "archived_at": utc_now(),
            })
        self.counters[state] += 1
        return target

    def recover_processing(self, *, stale_after_sec: int = 1800) -> list[Path]:
        self.ensure()
        recovered: list[Path] = []
        now = time.time()
        for path in self.directories["processing"].glob("*.json"):
            if now - path.stat().st_mtime < stale_after_sec:
                continue
            recovered.append(self.archive(path, "quarantined", reason="startup_recovery_orphaned_processing"))
            self.counters["recovered"] += 1
        return recovered

    def summary(self) -> dict[str, Any]:
        return {
            "file_bus_lifecycle_version": FILE_BUS_LIFECYCLE_VERSION,
            "session_id": self.session_id,
            "counters": dict(self.counters),
        }
