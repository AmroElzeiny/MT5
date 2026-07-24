"""Strict provider-facing schemas and recursive structured-output preflight.

The model classes in this module are transport contracts, not trading
authorities.  Python still validates candidate identity, target feasibility,
qualitative veto evidence, repeatability, and deterministic risk after parsing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


STRUCTURED_SCHEMA_ADAPTER_VERSION = "20260723_strict_structured_schema_v1"


class StrictStructuredModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        protected_namespaces=(),
    )


class TargetComparisonItem(StrictStructuredModel):
    usable: bool
    reason: str = Field(max_length=180)
    risk: str = Field(max_length=120)
    expected_role: str = Field(max_length=32)


class TargetComparison(StrictStructuredModel):
    liquidity_target: TargetComparisonItem
    partial_before_obstacle_then_liquidity: TargetComparisonItem
    capped_before_obstacle: TargetComparisonItem
    synthetic_rr_capped_to_max_distance: TargetComparisonItem
    synthetic_rr_fallback: TargetComparisonItem


class TargetArbitrationDecision(StrictStructuredModel):
    target_arbitration_schema_version: str = Field(max_length=64)
    prompt_contract_version: str = Field(max_length=64)
    arbitration_required: bool
    chosen_target_model: str = Field(max_length=64)
    chosen_tp1: float
    chosen_tp2: float
    chosen_rr1: float
    chosen_rr2: float
    rejected_target_models: list[str] = Field(max_length=6)
    blocker_kind: str = Field(max_length=64)
    blocker_severity: float = Field(ge=-1.0, le=10.0)
    blocker_class: str = Field(max_length=24)
    blocker_is_trade_killer: bool
    why_not_liquidity_target: str = Field(max_length=160)
    why_not_partial_before_obstacle: str = Field(max_length=160)
    why_not_capped_before_obstacle: str = Field(max_length=160)
    why_not_synthetic_fallback: str = Field(max_length=160)
    target_decision_reason: str = Field(max_length=160)
    target_comparison: TargetComparison


class VetoDecision(StrictStructuredModel):
    enabled: bool
    code: str = Field(max_length=64)
    evidence_fields: list[str] = Field(max_length=12)
    reason: str = Field(max_length=160)


class ModelCandidateAssessment(StrictStructuredModel):
    """Analytical output only; Python owns all transport and plan identity."""

    candidate_index: int = Field(ge=0)
    llm_quality_score: float = Field(ge=0.0, le=10.0)
    llm_self_reported_confidence: float = Field(ge=0.0, le=1.0)
    verdict: str = Field(min_length=1, max_length=16)
    thesis_supported: bool
    material_contradictions: list[str] = Field(max_length=12)
    missing_required_evidence: list[str] = Field(max_length=12)
    historical_evidence_state: str = Field(min_length=1, max_length=32)
    major_risks: list[str] = Field(max_length=12)
    evidence_refs: list[str] = Field(min_length=1, max_length=16)
    confidence_band: str = Field(min_length=1, max_length=12)
    summary: str = Field(min_length=1, max_length=240)
    raw_allow: bool
    decision_state: str = Field(max_length=16)
    structure_quality_score: float = Field(ge=0.0, le=10.0)
    entry_timing_score: float = Field(ge=0.0, le=10.0)
    follow_through_probability: float = Field(ge=0.0, le=1.0)
    invalidation_risk: float = Field(ge=0.0, le=1.0)
    chop_risk: float = Field(ge=0.0, le=1.0)
    cost_risk: float = Field(ge=0.0, le=1.0)
    symbol_bucket_risk: float = Field(ge=0.0, le=1.0)
    session_bucket_risk: float = Field(ge=0.0, le=1.0)
    post_entry_failure_risk: float = Field(ge=0.0, le=1.0)
    final_trade_expectancy_score: float = Field(ge=0.0, le=10.0)
    veto: VetoDecision
    bucket_prior_override_justification: str = Field(max_length=180)
    reasons: str = Field(max_length=240)
    rejection_codes: list[str] = Field(max_length=8)
    narrative_state: str = Field(max_length=48)
    invalidation_risks: list[str] = Field(max_length=8)
    missing_confirmations: list[str] = Field(max_length=8)
    suggested_risk_multiplier: float = Field(ge=0.0, le=1.0)
    target_arbitration: TargetArbitrationDecision


class ModelAIGateOutput(StrictStructuredModel):
    """Provider result before Python attaches immutable request identity."""

    decision_quality_tier: str = Field(max_length=48)
    response_quality: str | None = Field(max_length=48)
    selected_candidate_index: int = Field(ge=0)
    candidate_assessments: list[ModelCandidateAssessment] = Field(min_length=1)
    reasons: str = Field(max_length=240)


class CandidateAssessment(StrictStructuredModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        protected_namespaces=(),
    )

    request_id: str = Field(min_length=1, max_length=180)
    request_identity_hash: str = Field(min_length=16, max_length=128)
    provider_id: str = Field(min_length=1, max_length=80)
    model_id: str = Field(min_length=1, max_length=120)
    role_schema_version: str = Field(min_length=1, max_length=80)
    candidate_index: int = Field(ge=0)
    candidate_id: str = Field(min_length=1, max_length=160)
    candidate_hash: str = Field(min_length=8, max_length=128)
    request_execution_fingerprint: str = Field(min_length=8, max_length=128)
    setup_taxonomy_version: str = Field(min_length=1, max_length=80)
    setup_taxonomy_enum: str = Field(min_length=1, max_length=80)
    taxonomy_mapping_source: str = Field(min_length=1, max_length=80)
    rule_score: float = Field(ge=0.0, le=10.0)
    llm_quality_score: float = Field(ge=0.0, le=10.0)
    blended_legacy_score: float = Field(ge=0.0, le=10.0)
    legacy_agreement_confidence: float = Field(ge=0.0, le=1.0)
    llm_self_reported_confidence: float = Field(ge=0.0, le=1.0)
    calibrated_win_probability: float | None
    expected_net_r: float | None
    oos_predicted_probability: float | None
    calibration_bucket: str = Field(max_length=80)
    calibration_sample_size: int = Field(ge=0)
    calibration_lower_bound: float | None
    calibration_upper_bound: float | None
    calibration_model_version: str = Field(max_length=80)
    calibration_data_window_start: str = Field(max_length=40)
    calibration_data_window_end: str = Field(max_length=40)
    calibration_available: bool
    role_contract_version: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=16)
    verdict: str = Field(min_length=1, max_length=16)
    thesis_supported: bool
    material_contradictions: list[str] = Field(max_length=12)
    missing_required_evidence: list[str] = Field(max_length=12)
    historical_evidence_state: str = Field(min_length=1, max_length=32)
    major_risks: list[str] = Field(max_length=12)
    evidence_refs: list[str] = Field(min_length=1, max_length=16)
    confidence_band: str = Field(min_length=1, max_length=12)
    summary: str = Field(min_length=1, max_length=240)
    raw_allow: bool
    decision_state: str = Field(max_length=16)
    structure_quality_score: float = Field(ge=0.0, le=10.0)
    entry_timing_score: float = Field(ge=0.0, le=10.0)
    follow_through_probability: float = Field(ge=0.0, le=1.0)
    invalidation_risk: float = Field(ge=0.0, le=1.0)
    chop_risk: float = Field(ge=0.0, le=1.0)
    cost_risk: float = Field(ge=0.0, le=1.0)
    symbol_bucket_risk: float = Field(ge=0.0, le=1.0)
    session_bucket_risk: float = Field(ge=0.0, le=1.0)
    post_entry_failure_risk: float = Field(ge=0.0, le=1.0)
    final_trade_expectancy_score: float = Field(ge=0.0, le=10.0)
    veto: VetoDecision
    bucket_prior_override_justification: str = Field(max_length=180)
    reasons: str = Field(max_length=240)
    rejection_codes: list[str] = Field(max_length=8)
    narrative_state: str = Field(max_length=48)
    invalidation_risks: list[str] = Field(max_length=8)
    missing_confirmations: list[str] = Field(max_length=8)
    suggested_risk_multiplier: float = Field(ge=0.0, le=1.0)
    selected_target_identity: str = Field(min_length=1, max_length=80)
    selected_target_price: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    assessed_execution_fingerprint: str = Field(min_length=8, max_length=128)
    model_version: str = Field(min_length=1, max_length=100)
    target_arbitration: TargetArbitrationDecision


class CandidateIdentityEcho(StrictStructuredModel):
    candidate_index: int = Field(ge=0)
    candidate_id: str = Field(min_length=1, max_length=160)
    candidate_hash: str = Field(min_length=8, max_length=128)
    request_execution_fingerprint: str = Field(min_length=8, max_length=128)
    setup_snapshot_time: int
    setup_taxonomy_enum: str = Field(min_length=1, max_length=80)


class AIGateEnvelope(StrictStructuredModel):
    request_id: str = Field(min_length=1, max_length=180)
    request_identity_hash: str = Field(min_length=16, max_length=128)
    provider_id: str = Field(min_length=1, max_length=80)
    model_id: str = Field(min_length=1, max_length=120)
    role_schema_version: str = Field(min_length=1, max_length=80)
    decision_schema_version: str = Field(max_length=64)
    decision_quality_tier: str = Field(max_length=48)
    response_quality: str | None = Field(max_length=48)
    candidate_count: int = Field(ge=1)
    ordered_candidate_identities: list[CandidateIdentityEcho] = Field(min_length=1)
    selected_candidate_id: str = Field(min_length=1, max_length=160)
    selected_candidate_hash: str = Field(min_length=8, max_length=128)
    candidate_assessments: list[CandidateAssessment] = Field(min_length=1)
    reasons: str = Field(max_length=240)


class CriticObjection(StrictStructuredModel):
    code: str = Field(min_length=1, max_length=64)
    evidence_refs: list[str] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=180)


class ModelCriticDecision(StrictStructuredModel):
    candidate_index: int = Field(ge=0)
    verdict: str = Field(min_length=1, max_length=16)
    blocking_objections: list[CriticObjection] = Field(max_length=12)
    non_blocking_objections: list[CriticObjection] = Field(max_length=12)
    missing_required_evidence: list[str] = Field(max_length=12)
    evidence_refs: list[str] = Field(min_length=1, max_length=16)
    confidence_band: str = Field(min_length=1, max_length=12)
    summary: str = Field(min_length=1, max_length=240)


class CriticDecision(StrictStructuredModel):
    request_id: str = Field(min_length=1, max_length=180)
    request_identity_hash: str = Field(min_length=16, max_length=128)
    provider_id: str = Field(min_length=1, max_length=80)
    model_id: str = Field(min_length=1, max_length=120)
    role_schema_version: str = Field(min_length=1, max_length=80)
    role_contract_version: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=16)
    candidate_index: int = Field(ge=0)
    candidate_id: str = Field(min_length=1, max_length=160)
    candidate_hash: str = Field(min_length=8, max_length=128)
    verdict: str = Field(min_length=1, max_length=16)
    blocking_objections: list[CriticObjection] = Field(max_length=12)
    non_blocking_objections: list[CriticObjection] = Field(max_length=12)
    missing_required_evidence: list[str] = Field(max_length=12)
    evidence_refs: list[str] = Field(min_length=1, max_length=16)
    confidence_band: str = Field(min_length=1, max_length=12)
    summary: str = Field(min_length=1, max_length=240)


class ModelAdjudicatorDecision(StrictStructuredModel):
    candidate_index: int = Field(ge=0)
    verdict: str = Field(min_length=1, max_length=24)
    resolved_objection_codes: list[str] = Field(max_length=12)
    unresolved_objection_codes: list[str] = Field(max_length=12)
    evidence_refs: list[str] = Field(max_length=16)
    resolution_reason: str = Field(min_length=1, max_length=240)


class AdjudicatorDecision(StrictStructuredModel):
    request_id: str = Field(min_length=1, max_length=180)
    request_identity_hash: str = Field(min_length=16, max_length=128)
    provider_id: str = Field(min_length=1, max_length=80)
    model_id: str = Field(min_length=1, max_length=120)
    role_schema_version: str = Field(min_length=1, max_length=80)
    role_contract_version: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=16)
    candidate_index: int = Field(ge=0)
    candidate_id: str = Field(min_length=1, max_length=160)
    candidate_hash: str = Field(min_length=8, max_length=128)
    verdict: str = Field(min_length=1, max_length=24)
    resolved_objection_codes: list[str] = Field(max_length=12)
    unresolved_objection_codes: list[str] = Field(max_length=12)
    evidence_refs: list[str] = Field(max_length=16)
    resolution_reason: str = Field(min_length=1, max_length=240)


class StructuredCapabilityProbe(StrictStructuredModel):
    ok: bool


@dataclass(frozen=True)
class StructuredSchemaPreflight:
    schema_name: str
    schema: dict[str, Any]
    schema_fingerprint: str
    valid: bool
    errors: tuple[str, ...]


_UNSUPPORTED_KEYWORDS = {
    "unevaluatedProperties",
    "patternProperties",
    "dependentSchemas",
    "propertyNames",
    "$dynamicRef",
    "$dynamicAnchor",
}


def _schema_pointer_exists(schema: Mapping[str, Any], ref: str) -> bool:
    if not ref.startswith("#/"):
        return False
    current: Any = schema
    for raw_token in ref[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or token not in current:
            return False
        current = current[token]
    return True


def _preflight_node(
    node: Any,
    *,
    root: Mapping[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _preflight_node(item, root=root, path=f"{path}[{index}]", errors=errors)
        return
    if not isinstance(node, Mapping):
        return

    for keyword in sorted(_UNSUPPORTED_KEYWORDS.intersection(node)):
        errors.append(f"{path}:{keyword}_unsupported")
    if "default" in node:
        errors.append(f"{path}:default_not_allowed")
    if "$ref" in node and not _schema_pointer_exists(root, str(node["$ref"])):
        errors.append(f"{path}:unresolved_ref:{node['$ref']}")

    is_object_schema = node.get("type") == "object" or "properties" in node
    if is_object_schema:
        if node.get("additionalProperties") is not False:
            errors.append(f"{path}:additionalProperties_must_be_false")
        properties = node.get("properties")
        required = node.get("required")
        if not isinstance(properties, Mapping):
            errors.append(f"{path}:object_properties_missing")
        else:
            required_names = set(required) if isinstance(required, list) else set()
            missing_required = sorted(set(properties) - required_names)
            if missing_required:
                errors.append(f"{path}:properties_not_required:{','.join(missing_required)}")
        if not isinstance(required, list):
            errors.append(f"{path}:required_array_missing")

    for key, value in node.items():
        _preflight_node(value, root=root, path=f"{path}.{key}", errors=errors)


def strict_structured_schema(model: type[BaseModel]) -> StructuredSchemaPreflight:
    schema = model.model_json_schema(mode="validation")
    errors: list[str] = []
    _preflight_node(schema, root=schema, path="$", errors=errors)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return StructuredSchemaPreflight(
        schema_name=model.__name__,
        schema=schema,
        schema_fingerprint=sha256(encoded).hexdigest(),
        valid=not errors,
        errors=tuple(sorted(set(errors))),
    )


def assert_strict_structured_schema(model: type[BaseModel]) -> StructuredSchemaPreflight:
    result = strict_structured_schema(model)
    if not result.valid:
        raise ValueError(
            "structured_schema_preflight_failed:"
            + result.schema_name
            + ":"
            + "|".join(result.errors)
        )
    return result


def all_authoritative_structured_models() -> tuple[type[BaseModel], ...]:
    return (
        ModelAIGateOutput,
        ModelCriticDecision,
        ModelAdjudicatorDecision,
        StructuredCapabilityProbe,
    )
