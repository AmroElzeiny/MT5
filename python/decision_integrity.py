from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence


AI_DECISION_SCHEMA_VERSION = "20260718_ai_decision_authority_v6"
AI_TARGET_ARBITRATION_SCHEMA_VERSION = "20260717_target_fingerprint_authority_v6"
AI_PROMPT_CONTRACT_VERSION = "20260718_qualitative_veto_repeatability_v8"

DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"
DECISION_ABSTAIN = "ABSTAIN"
DECISION_STATES = {DECISION_APPROVE, DECISION_REJECT, DECISION_ABSTAIN}

DECISION_QUALITY_FULL_STRUCTURED = "FULL_STRUCTURED"
DECISION_QUALITY_CACHE_FULL_STRUCTURED = "CACHE_OF_FULL_STRUCTURED"
DECISION_QUALITY_DEGRADED_NON_TRADING = "DEGRADED_NON_TRADING"
DECISION_QUALITY_RULE_ONLY_NON_TRADING = "RULE_ONLY_NON_TRADING"
TRADING_DECISION_QUALITY_TIERS = {
    DECISION_QUALITY_FULL_STRUCTURED,
    DECISION_QUALITY_CACHE_FULL_STRUCTURED,
}

# Read-only source compatibility names.  They are aliases of the canonical
# decision_quality_tier values and are never consulted independently.
RESPONSE_FULL_STRUCTURED = DECISION_QUALITY_FULL_STRUCTURED
RESPONSE_CACHE_FULL_STRUCTURED = DECISION_QUALITY_CACHE_FULL_STRUCTURED
RESPONSE_DEGRADED_NON_TRADING = DECISION_QUALITY_DEGRADED_NON_TRADING
RESPONSE_RULE_ONLY_NON_TRADING = DECISION_QUALITY_RULE_ONLY_NON_TRADING
TRADING_RESPONSE_QUALITIES = TRADING_DECISION_QUALITY_TIERS

# These values are LLM assessments, not empirically calibrated probabilities.
# They are research diagnostics only and have no direct positive or negative
# trade authority. Only an enumerated, evidence-backed qualitative veto may
# remove authority.
LLM_VETO_CODES = {
    "ai_veto_missing_mandatory_evidence",
    "ai_veto_structural_contradiction",
    "ai_veto_sequence_contradiction",
    "ai_veto_target_arbitration_incoherent",
    "ai_veto_execution_plan_mismatch",
    "ai_veto_prior_override_unsupported",
    "ai_veto_data_integrity_failure",
}
MANDATORY_ASSESSMENT_FIELDS = (
    "candidate_index",
    "candidate_id",
    "candidate_hash",
    "request_execution_fingerprint",
    "setup_taxonomy_version",
    "setup_taxonomy_enum",
    "taxonomy_mapping_source",
    "rule_score",
    "llm_quality_score",
    "blended_legacy_score",
    "legacy_agreement_confidence",
    "llm_self_reported_confidence",
    "raw_allow",
    "decision_state",
    "structure_quality_score",
    "entry_timing_score",
    "follow_through_probability",
    "invalidation_risk",
    "chop_risk",
    "cost_risk",
    "symbol_bucket_risk",
    "session_bucket_risk",
    "post_entry_failure_risk",
    "final_trade_expectancy_score",
    "veto",
    "bucket_prior_override_justification",
    "reasons",
    "rejection_codes",
    "narrative_state",
    "invalidation_risks",
    "missing_confirmations",
    "selected_target_identity",
    "selected_target_price",
    "entry",
    "sl",
    "tp1",
    "tp2",
    "assessed_execution_fingerprint",
    "suggested_risk_multiplier",
    "model_version",
    "target_arbitration",
    "calibration_bucket",
    "calibration_sample_size",
    "calibration_model_version",
    "calibration_data_window_start",
    "calibration_data_window_end",
    "calibration_available",
)

MANDATORY_NULLABLE_CALIBRATION_FIELDS = (
    "calibrated_win_probability",
    "expected_net_r",
    "oos_predicted_probability",
    "calibration_lower_bound",
    "calibration_upper_bound",
)

UNIT_INTERVAL_FIELDS = (
    "legacy_agreement_confidence",
    "llm_self_reported_confidence",
    "follow_through_probability",
    "invalidation_risk",
    "chop_risk",
    "cost_risk",
    "symbol_bucket_risk",
    "session_bucket_risk",
    "post_entry_failure_risk",
    "suggested_risk_multiplier",
)

SCORE_FIELDS = (
    "rule_score",
    "llm_quality_score",
    "blended_legacy_score",
    "structure_quality_score",
    "entry_timing_score",
    "final_trade_expectancy_score",
)


@dataclass(frozen=True)
class SchemaValidationResult:
    valid: bool
    missing_fields: tuple[str, ...]
    invalid_fields: tuple[str, ...]


@dataclass(frozen=True)
class FingerprintTolerance:
    price_ticks: float = 2.0
    entry_r: float = 0.05
    stop_r: float = 0.05
    target_r: float = 0.05
    rr: float = 0.05
    cost_r: float = 0.02


DEFAULT_FINGERPRINT_TOLERANCE = FingerprintTolerance()


@dataclass(frozen=True)
class BrokerIdentityResolution:
    verified: bool
    quarantined: bool
    reason: str
    position_ticket: int = 0
    position_identifier: int = 0


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def canonical_decision_quality_tier(value: Mapping[str, Any] | str) -> str:
    """Return the canonical tier and reject a conflicting migration alias."""

    if isinstance(value, Mapping):
        tier = str(value.get("decision_quality_tier") or "")
        alias = value.get("response_quality")
        if alias is not None and str(alias) != tier:
            raise ValueError("response_quality_alias_conflict")
        return tier
    return str(value or "")


def validate_candidate_assessment(
    assessment: Mapping[str, Any],
    candidate: Mapping[str, Any] | None = None,
) -> SchemaValidationResult:
    missing = [name for name in MANDATORY_ASSESSMENT_FIELDS if name not in assessment or assessment[name] is None]
    missing.extend(name for name in MANDATORY_NULLABLE_CALIBRATION_FIELDS if name not in assessment)
    invalid: list[str] = []

    state = str(assessment.get("decision_state") or "").upper()
    if state not in DECISION_STATES:
        invalid.append("decision_state")
    if not isinstance(assessment.get("raw_allow"), bool):
        invalid.append("raw_allow")
    for name in SCORE_FIELDS:
        value = assessment.get(name)
        if value is not None and (not _finite_number(value) or not 0.0 <= float(value) <= 10.0):
            invalid.append(name)
    for name in UNIT_INTERVAL_FIELDS:
        value = assessment.get(name)
        if value is not None and (not _finite_number(value) or not 0.0 <= float(value) <= 1.0):
            invalid.append(name)

    for name in ("candidate_index",):
        value = assessment.get(name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            invalid.append(name)
    for name in ("selected_target_price", "entry", "sl", "tp1", "tp2"):
        value = assessment.get(name)
        if value is not None and not _finite_number(value):
            invalid.append(name)

    veto = assessment.get("veto")
    if (
        not isinstance(veto, Mapping)
        or not isinstance(veto.get("enabled"), bool)
        or "code" not in veto
        or "evidence_fields" not in veto
        or "reason" not in veto
    ):
        invalid.append("veto")
        veto_enabled = True
    else:
        veto_enabled = bool(veto.get("enabled"))
        veto_code = str(veto.get("code") or "").strip()
        veto_evidence = veto.get("evidence_fields")
        veto_reason = str(veto.get("reason") or "").strip()
        if not isinstance(veto_evidence, list) or any(
            not isinstance(field, str) or not field.strip() for field in (veto_evidence or [])
        ):
            invalid.append("veto.evidence_fields")
            veto_evidence = []
        if veto_enabled:
            if veto_code not in LLM_VETO_CODES:
                invalid.append("veto.code")
            if not veto_evidence:
                invalid.append("veto.evidence_fields")
            if not veto_reason:
                invalid.append("veto.reason")
        elif veto_code or veto_evidence or veto_reason:
            invalid.append("veto.disabled_payload")

    risk_multiplier = assessment.get("suggested_risk_multiplier")
    raw_allow = assessment.get("raw_allow") is True
    if state == DECISION_APPROVE and (
        not raw_allow
        or veto_enabled
        or not _finite_number(risk_multiplier)
        or float(risk_multiplier) <= 0.0
    ):
        invalid.append("approve_state_contract")
    if state in {DECISION_REJECT, DECISION_ABSTAIN} and raw_allow:
        invalid.append("non_approve_raw_allow")
    if state == DECISION_REJECT and not veto_enabled:
        invalid.append("reject_requires_evidence_backed_veto")
    if state == DECISION_ABSTAIN and _finite_number(risk_multiplier) and float(risk_multiplier) != 0.0:
        invalid.append("abstain_risk_multiplier")

    calibration_available = assessment.get("calibration_available")
    if calibration_available is not False:
        invalid.append("calibration_available")
    for name in MANDATORY_NULLABLE_CALIBRATION_FIELDS:
        if assessment.get(name) is not None:
            invalid.append(name)
    calibration_sample_size = assessment.get("calibration_sample_size")
    if (
        not isinstance(calibration_sample_size, int)
        or isinstance(calibration_sample_size, bool)
        or calibration_sample_size != 0
    ):
        invalid.append("calibration_sample_size")
    for name in (
        "calibration_bucket",
        "calibration_model_version",
        "calibration_data_window_start",
        "calibration_data_window_end",
    ):
        if str(assessment.get(name) or ""):
            invalid.append(name)

    for name in ("candidate_id", "candidate_hash", "request_execution_fingerprint", "assessed_execution_fingerprint", "model_version", "selected_target_identity"):
        if name in assessment and not str(assessment.get(name) or "").strip():
            invalid.append(name)
    for name in ("rejection_codes", "invalidation_risks", "missing_confirmations"):
        if name in assessment and not isinstance(assessment.get(name), list):
            invalid.append(name)

    target_arbitration = assessment.get("target_arbitration")
    if not isinstance(target_arbitration, Mapping):
        invalid.append("target_arbitration")
    else:
        required_arbitration_fields = (
            "target_arbitration_schema_version",
            "prompt_contract_version",
            "arbitration_required",
            "chosen_target_model",
            "chosen_tp1",
            "chosen_tp2",
            "chosen_rr1",
            "chosen_rr2",
            "rejected_target_models",
            "blocker_kind",
            "blocker_severity",
            "blocker_class",
            "blocker_is_trade_killer",
            "why_not_liquidity_target",
            "why_not_partial_before_obstacle",
            "why_not_capped_before_obstacle",
            "why_not_synthetic_fallback",
            "target_decision_reason",
            "target_comparison",
        )
        for name in required_arbitration_fields:
            if name not in target_arbitration or target_arbitration[name] is None:
                invalid.append(f"target_arbitration.{name}")
        if target_arbitration.get("target_arbitration_schema_version") != AI_TARGET_ARBITRATION_SCHEMA_VERSION:
            invalid.append("target_arbitration_schema_version")
        if target_arbitration.get("prompt_contract_version") != AI_PROMPT_CONTRACT_VERSION:
            invalid.append("prompt_contract_version")
        if not isinstance(target_arbitration.get("arbitration_required"), bool):
            invalid.append("target_arbitration.arbitration_required")
        if not isinstance(target_arbitration.get("blocker_is_trade_killer"), bool):
            invalid.append("target_arbitration.blocker_is_trade_killer")
        for name in ("chosen_tp1", "chosen_tp2", "chosen_rr1", "chosen_rr2", "blocker_severity"):
            value = target_arbitration.get(name)
            if value is not None and not _finite_number(value):
                invalid.append(f"target_arbitration.{name}")
        if not isinstance(target_arbitration.get("rejected_target_models"), list):
            invalid.append("target_arbitration.rejected_target_models")
        if not isinstance(target_arbitration.get("target_comparison"), Mapping):
            invalid.append("target_arbitration.target_comparison")

    if candidate is not None:
        if assessment.get("candidate_index") != candidate.get("candidate_index"):
            invalid.append("candidate_index_mismatch")
        if str(assessment.get("candidate_id") or "") != str(candidate.get("candidate_id") or ""):
            invalid.append("candidate_id_mismatch")
        if str(assessment.get("candidate_hash") or "") != str(candidate.get("candidate_hash") or ""):
            invalid.append("candidate_hash_mismatch")
        if str(assessment.get("request_execution_fingerprint") or "") != str(
            candidate.get("request_execution_fingerprint")
            or candidate.get("assessed_execution_fingerprint")
            or ""
        ):
            invalid.append("request_execution_fingerprint_mismatch")
        for name in ("setup_taxonomy_version", "setup_taxonomy_enum", "taxonomy_mapping_source"):
            if str(assessment.get(name) or "") != str(candidate.get(name) or ""):
                invalid.append(f"{name}_mismatch")

    return SchemaValidationResult(
        valid=not missing and not invalid,
        missing_fields=tuple(sorted(set(missing))),
        invalid_fields=tuple(sorted(set(invalid))),
    )


def validate_decision_envelope(
    envelope: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> SchemaValidationResult:
    missing: list[str] = []
    invalid: list[str] = []
    for name in (
        "decision_schema_version",
        "decision_quality_tier",
        "selected_candidate_id",
        "selected_candidate_hash",
        "candidate_assessments",
    ):
        if name not in envelope or envelope[name] is None:
            missing.append(name)

    if envelope.get("decision_schema_version") != AI_DECISION_SCHEMA_VERSION:
        invalid.append("decision_schema_version")
    canonical_tier = str(envelope.get("decision_quality_tier") or "")
    if canonical_tier != DECISION_QUALITY_FULL_STRUCTURED:
        invalid.append("decision_quality_tier")
    migration_alias = envelope.get("response_quality")
    if migration_alias is not None and str(migration_alias) != canonical_tier:
        invalid.append("response_quality_alias_conflict")

    raw_assessments = envelope.get("candidate_assessments")
    assessments = raw_assessments if isinstance(raw_assessments, list) else []
    if len(assessments) != len(candidates):
        invalid.append("candidate_assessment_count")

    candidate_by_hash = {str(c.get("candidate_hash") or ""): c for c in candidates}
    seen: set[str] = set()
    for assessment in assessments:
        if not isinstance(assessment, Mapping):
            invalid.append("candidate_assessment_type")
            continue
        candidate_hash = str(assessment.get("candidate_hash") or "")
        if not candidate_hash or candidate_hash in seen:
            invalid.append("candidate_hash_duplicate_or_missing")
            continue
        seen.add(candidate_hash)
        candidate = candidate_by_hash.get(candidate_hash)
        if candidate is None:
            invalid.append("candidate_hash_unknown")
            continue
        result = validate_candidate_assessment(assessment, candidate)
        missing.extend(result.missing_fields)
        invalid.extend(result.invalid_fields)

    selected_hash = str(envelope.get("selected_candidate_hash") or "")
    selected_id = str(envelope.get("selected_candidate_id") or "")
    selected = next((a for a in assessments if isinstance(a, Mapping) and str(a.get("candidate_hash") or "") == selected_hash), None)
    if selected is None:
        invalid.append("selected_candidate_hash")
    elif str(selected.get("candidate_id") or "") != selected_id:
        invalid.append("selected_candidate_id")

    return SchemaValidationResult(
        valid=not missing and not invalid,
        missing_fields=tuple(sorted(set(missing))),
        invalid_fields=tuple(sorted(set(invalid))),
    )


def canonical_execution_components(plan: Mapping[str, Any]) -> dict[str, Any]:
    decision_input_hash = str(plan.get("decision_input_hash") or plan.get("runtime_input_hash") or "")
    return {
        "symbol": str(plan.get("symbol") or "").upper(),
        "direction": str(plan.get("direction") or "").lower(),
        "candidate_id": str(plan.get("candidate_id") or ""),
        "candidate_hash": str(plan.get("candidate_hash") or ""),
        "setup_code": str(plan.get("setup_code") or plan.get("model_code") or "").upper(),
        "setup_family": str(plan.get("setup_family") or "").lower(),
        "setup_taxonomy_version": str(plan.get("setup_taxonomy_version") or ""),
        "setup_taxonomy_enum": str(plan.get("setup_taxonomy_enum") or "").upper(),
        "taxonomy_mapping_source": str(plan.get("taxonomy_mapping_source") or ""),
        "entry_branch": str(plan.get("entry_branch") or plan.get("entry_model") or "").lower(),
        "source_t_sweep": int(plan.get("source_t_sweep") or 0),
        "source_t_disp": int(plan.get("source_t_disp") or 0),
        "source_t_bos": int(plan.get("source_t_bos") or 0),
        "entry": _mql_canonical_price(plan.get("entry") if plan.get("entry") is not None else plan.get("entry_est"), plan),
        "sl": _mql_canonical_price(plan.get("sl"), plan),
        "tp1": _mql_canonical_price(plan.get("tp1"), plan),
        "tp2": _mql_canonical_price(plan.get("tp2"), plan),
        "net_rr": f"{float(plan.get('net_rr') if plan.get('net_rr') is not None else plan.get('rr2') or 0.0):.6f}",
        "spread_r": f"{float(plan.get('spread_r') or 0.0):.6f}",
        "slippage_estimate_r": f"{float(plan.get('slippage_estimate_r') if plan.get('slippage_estimate_r') is not None else plan.get('slippage_r') or 0.0):.6f}",
        "execution_cost_r": f"{float(plan.get('execution_cost_r') or 0.0):.6f}",
        "target_source": str(plan.get("target_source") or "").lower(),
        "target_model": str(plan.get("target_model") or plan.get("tp_model") or "").lower(),
        "obstacle_identity": str(plan.get("obstacle_identity") or plan.get("obstacle_kind") or "").lower(),
        "obstacle_tf": str(plan.get("obstacle_tf") or "").lower(),
        "obstacle_price": _mql_canonical_price(plan.get("obstacle_price"), plan),
        # This is the strategy-relevant runtime hash. Tester workflow/debug
        # flags are deliberately excluded so RECORD_ONLY decisions replay in
        # CACHE_ONLY without changing economic identity.
        "runtime_input_hash": decision_input_hash,
        "strategy_schema_version": str(plan.get("strategy_schema_version") or ""),
    }


def execution_fingerprint(plan: Mapping[str, Any]) -> str:
    components = canonical_execution_components(plan)
    canonical = "|".join(
        (
            str(components["symbol"]),
            str(components["direction"]).upper(),
            str(components["candidate_id"]),
            str(components["candidate_hash"]),
            str(components["setup_code"]),
            str(components["setup_family"]),
            str(components["setup_taxonomy_version"]),
            str(components["setup_taxonomy_enum"]),
            str(components["taxonomy_mapping_source"]),
            str(components["entry_branch"]),
            str(components["source_t_sweep"]),
            str(components["source_t_disp"]),
            str(components["source_t_bos"]),
            str(components["entry"]),
            str(components["sl"]),
            str(components["tp1"]),
            str(components["tp2"]),
            str(components["net_rr"]),
            str(components["spread_r"]),
            str(components["slippage_estimate_r"]),
            str(components["execution_cost_r"]),
            str(components["target_source"]),
            str(components["target_model"]),
            str(components["obstacle_identity"]),
            str(components["obstacle_tf"]),
            str(components["obstacle_price"]),
            str(components["runtime_input_hash"]),
            str(components["strategy_schema_version"]),
            AI_DECISION_SCHEMA_VERSION,
        )
    )
    return mql_integrity_hash(canonical)


def _mql_fnv1a(value: str, seed: int) -> int:
    result = seed & 0xFFFFFFFF
    for char in value:
        result = ((result ^ ord(char)) * 16777619) & 0xFFFFFFFF
    return result


def mql_integrity_hash(value: str) -> str:
    return f"{_mql_fnv1a(value, 2166136261):08X}{_mql_fnv1a('po3|' + value, 2246822519):08X}"


def _mql_canonical_price(value: Any, candidate: Mapping[str, Any]) -> str:
    digits = max(0, int(candidate.get("symbol_digits") or 5))
    tick = Decimal(str(candidate.get("symbol_tick_size") or 0))
    if tick <= 0:
        tick = Decimal(1).scaleb(-digits)
    price = Decimal(str(float(value or 0.0)))
    rounded = (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    return f"{rounded:.{digits}f}"


def assessed_execution_fingerprint(
    candidate: Mapping[str, Any],
    assessment: Mapping[str, Any],
) -> str:
    request_fingerprint = str(
        candidate.get("request_execution_fingerprint")
        or candidate.get("assessed_execution_fingerprint")
        or ""
    )
    canonical = "|".join(
        (
            request_fingerprint,
            str(candidate.get("candidate_hash") or assessment.get("candidate_hash") or ""),
            str(assessment.get("selected_target_identity") or ""),
            _mql_canonical_price(assessment.get("entry"), candidate),
            _mql_canonical_price(assessment.get("sl"), candidate),
            _mql_canonical_price(assessment.get("tp1"), candidate),
            _mql_canonical_price(assessment.get("tp2"), candidate),
            _mql_canonical_price(assessment.get("selected_target_price"), candidate),
            AI_DECISION_SCHEMA_VERSION,
        )
    )
    return mql_integrity_hash(canonical)


def candidate_hash(candidate: Mapping[str, Any]) -> str:
    existing = str(candidate.get("candidate_hash") or "")
    if existing:
        return existing
    direction = str(candidate.get("direction") or ("buy" if candidate.get("is_buy") else "sell")).upper()
    canonical = "|".join(
        (
            str(candidate.get("symbol") or "").upper(),
            direction,
            str(candidate.get("candidate_id") or ""),
            str(candidate.get("setup_id") or ""),
            str(candidate.get("fvg_id") or ""),
            str(candidate.get("setup_code") or candidate.get("model_code") or ""),
            str(candidate.get("setup_family") or ""),
            str(candidate.get("setup_taxonomy_version") or ""),
            str(candidate.get("setup_taxonomy_enum") or ""),
            str(candidate.get("taxonomy_mapping_source") or ""),
            str(candidate.get("entry_branch") or candidate.get("entry_model") or ""),
            str(int(candidate.get("source_t_sweep") or 0)),
            str(int(candidate.get("source_t_disp") or 0)),
            str(int(candidate.get("source_t_bos") or 0)),
            _mql_canonical_price(candidate.get("fvg_lower") or candidate.get("lower"), candidate),
            _mql_canonical_price(candidate.get("fvg_upper") or candidate.get("upper"), candidate),
            _mql_canonical_price(candidate.get("entry_est") or candidate.get("entry"), candidate),
            _mql_canonical_price(candidate.get("sl"), candidate),
            _mql_canonical_price(candidate.get("tp1"), candidate),
            _mql_canonical_price(candidate.get("tp2"), candidate),
            str(candidate.get("target_source") or ""),
            str(candidate.get("target_model") or candidate.get("tp_model") or ""),
            str(candidate.get("obstacle_kind") or ""),
            str(candidate.get("obstacle_tf") or ""),
            _mql_canonical_price(candidate.get("obstacle_price"), candidate),
            str(candidate.get("decision_input_hash") or candidate.get("runtime_input_hash") or ""),
            str(candidate.get("strategy_schema_version") or ""),
            AI_DECISION_SCHEMA_VERSION,
        )
    )
    return mql_integrity_hash(canonical)


def material_execution_changes(
    assessed: Mapping[str, Any],
    final: Mapping[str, Any],
    *,
    tick_size: float,
    tolerance: FingerprintTolerance = DEFAULT_FINGERPRINT_TOLERANCE,
) -> list[str]:
    changes: list[str] = []
    assessed_c = canonical_execution_components(assessed)
    final_c = canonical_execution_components(final)
    for name in (
        "symbol",
        "direction",
        "candidate_id",
        "candidate_hash",
        "setup_code",
        "setup_family",
        "setup_taxonomy_version",
        "setup_taxonomy_enum",
        "taxonomy_mapping_source",
        "entry_branch",
        "source_t_sweep",
        "source_t_disp",
        "source_t_bos",
        "target_source",
        "target_model",
        "obstacle_identity",
        "obstacle_tf",
        "runtime_input_hash",
        "strategy_schema_version",
    ):
        if assessed_c[name] != final_c[name]:
            changes.append(name)

    risk = abs(float(assessed_c["entry"]) - float(assessed_c["sl"]))
    tick = max(abs(float(tick_size)), 1e-12)

    def changed_price(name: str, r_tol: float) -> bool:
        delta = abs(float(assessed_c[name]) - float(final_c[name]))
        allowed = max(tolerance.price_ticks * tick, r_tol * risk)
        return delta > allowed + tick * 1e-6

    if changed_price("entry", tolerance.entry_r):
        changes.append("entry")
    if changed_price("sl", tolerance.stop_r):
        changes.append("sl")
    for name in ("tp1", "tp2"):
        if changed_price(name, tolerance.target_r):
            changes.append(name)
    if changed_price("obstacle_price", tolerance.target_r):
        changes.append("obstacle_price")
    if abs(float(assessed_c["net_rr"]) - float(final_c["net_rr"])) > tolerance.rr:
        changes.append("net_rr")
    for name in ("spread_r", "slippage_estimate_r", "execution_cost_r"):
        if abs(float(assessed_c[name]) - float(final_c[name])) > tolerance.cost_r:
            changes.append(name)
    return changes


def resolved_risk_multiplier(value: Any) -> float:
    if value is None or isinstance(value, bool) or not _finite_number(value):
        raise ValueError("risk_multiplier_missing_or_invalid")
    resolved = float(value)
    if not 0.0 <= resolved <= 1.0:
        raise ValueError("risk_multiplier_out_of_range")
    return resolved


def response_can_trade(decision_quality_tier: str, decision_state: str, risk_multiplier: Any) -> bool:
    try:
        risk = resolved_risk_multiplier(risk_multiplier)
    except ValueError:
        return False
    return (
        decision_quality_tier in TRADING_DECISION_QUALITY_TIERS
        and str(decision_state or "").upper() == DECISION_APPROVE
        and risk > 0.0
    )


def validate_broker_execution_identity(
    expected: Mapping[str, Any],
    order: Mapping[str, Any] | None,
    deal: Mapping[str, Any] | None,
    positions: Sequence[Mapping[str, Any]],
    *,
    account_mode: str,
    volume_tolerance: float = 1e-8,
    open_time_tolerance_sec: float = 10.0,
    existing_managed_same_symbol_positions: int = 0,
    netting_virtual_subposition_ledger: bool = False,
) -> BrokerIdentityResolution:
    """Pure verifier mirroring the MQL order/deal/position attribution contract.

    Matching is rooted in the result order and result deal relationship, then
    DEAL_POSITION_ID. Symbol-only position selection is deliberately absent.
    """

    def quarantine(reason: str) -> BrokerIdentityResolution:
        return BrokerIdentityResolution(False, True, reason)

    mode = str(account_mode or "").strip().upper()
    if mode in {"HEDGING", "HEDGING_EXACT_POSITION_ID"}:
        mode = "HEDGING_EXACT_POSITION_ID"
    elif mode in {"NETTING", "NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK"}:
        mode = "NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK"
    elif mode == "NETTING_VIRTUAL_SUBPOSITION_LEDGER":
        mode = "NETTING_VIRTUAL_SUBPOSITION_LEDGER"
    else:
        return quarantine("account_position_mode_unknown")
    if mode == "NETTING_VIRTUAL_SUBPOSITION_LEDGER" and not netting_virtual_subposition_ledger:
        return quarantine("netting_virtual_subposition_ledger_unavailable")
    if mode == "NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK" and existing_managed_same_symbol_positions > 0:
        return quarantine("netting_mode_one_managed_trade_per_symbol")
    if order is None:
        return quarantine("missing_result_order_ticket")
    if deal is None:
        return quarantine("missing_result_deal_ticket")

    for field in ("trade_key", "candidate_id", "candidate_hash", "execution_fingerprint"):
        if not str(expected.get(field) or ""):
            return quarantine("missing_trade_candidate_or_fingerprint_identity")

    order_ticket = int(order.get("ticket") or 0)
    deal_ticket = int(deal.get("ticket") or 0)
    if order_ticket <= 0:
        return quarantine("missing_result_order_ticket")
    if deal_ticket <= 0:
        return quarantine("missing_result_deal_ticket")
    if int(deal.get("order_ticket") or 0) != order_ticket:
        return quarantine("deal_order_ticket_mismatch")

    expected_magic = int(expected.get("magic") or 0)
    expected_symbol = str(expected.get("symbol") or "")
    expected_direction = str(expected.get("direction") or "").lower()
    expected_comment = str(expected.get("broker_comment") or "")
    requested_volume = float(expected.get("requested_volume") or 0.0)
    tolerance = max(abs(float(volume_tolerance)), 1e-12)

    if int(deal.get("magic") or 0) != expected_magic:
        return quarantine("deal_magic_mismatch")
    if str(deal.get("symbol") or "") != expected_symbol:
        return quarantine("deal_symbol_mismatch")
    if str(deal.get("entry") or "").lower() not in {"in", "inout"}:
        return quarantine("deal_is_not_entry")
    if str(deal.get("direction") or "").lower() != expected_direction:
        return quarantine("deal_direction_mismatch")
    if requested_volume <= 0.0 or abs(float(deal.get("volume") or 0.0) - requested_volume) > tolerance:
        return quarantine("deal_volume_mismatch")

    if int(order.get("magic") or 0) != expected_magic:
        return quarantine("order_magic_mismatch")
    if str(order.get("symbol") or "") != expected_symbol:
        return quarantine("order_symbol_mismatch")
    if not expected_comment or str(order.get("comment") or "") != expected_comment:
        return quarantine("order_comment_mismatch")
    if abs(float(order.get("volume") or 0.0) - requested_volume) > tolerance:
        return quarantine("order_volume_mismatch")

    position_identifier = int(deal.get("position_id") or 0)
    if position_identifier <= 0:
        return quarantine("missing_deal_position_id")
    matches = [p for p in positions if int(p.get("identifier") or 0) == position_identifier]
    if len(matches) != 1:
        return quarantine("exact_position_not_found_by_deal_position_id")
    position = matches[0]
    if int(position.get("ticket") or 0) <= 0:
        return quarantine("exact_position_not_found_by_deal_position_id")
    if int(position.get("magic") or 0) != expected_magic:
        return quarantine("position_magic_mismatch")
    if str(position.get("symbol") or "") != expected_symbol:
        return quarantine("position_symbol_mismatch")
    if str(position.get("direction") or "").lower() != expected_direction:
        return quarantine("position_direction_mismatch")
    if str(position.get("comment") or "") != expected_comment:
        return quarantine("position_comment_mismatch")
    position_volume = float(position.get("volume") or 0.0)
    if position_volume <= 0.0 or position_volume > requested_volume + tolerance:
        return quarantine("position_volume_exceeds_verified_entry_volume")
    position_open_time = float(position.get("open_time") or 0.0)
    deal_time = float(deal.get("time") or 0.0)
    if position_open_time <= 0.0 or deal_time <= 0.0 or abs(position_open_time - deal_time) > abs(open_time_tolerance_sec):
        return quarantine("position_open_time_mismatch")

    return BrokerIdentityResolution(
        True,
        False,
        "verified_order_deal_position_chain",
        int(position.get("ticket") or 0),
        position_identifier,
    )
