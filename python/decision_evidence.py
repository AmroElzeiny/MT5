"""Canonical, provider-neutral evidence envelope for Version Z AI passes."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Sequence

from family_context import FAMILY_PROFILE_VERSION, family_context_for
from governance_contracts import SETUP_TAXONOMY_VERSION, SetupTaxonomy


EVIDENCE_ENVELOPE_VERSION = "20260718_decision_evidence_v1"
PROVIDER_DECISION_CONTEXT_VERSION = "20260814_provider_decision_context_v3"


_FULL_PO3_TAXONOMIES = {
    SetupTaxonomy.FULL_PO3_REVERSAL.value,
    SetupTaxonomy.FULL_PO3_CONTINUATION.value,
}


# Global PO3 sequence flags that a provider can see in `sequence` but that are only
# *mandatory* when the family names them in required_event_sequence.  Each entry is
# (contract_name, sequence_field, regex matched against required_event_sequence).
#
# Scope is deliberately limited to flags whose mapping is unambiguous.  `BOS` appears
# only in the two FULL_PO3 sequences, and follow-through appears in none of them, so
# both map exactly.  `sweep`/`displacement` are intentionally NOT inferred: the wording
# varies across families ("liquidity sweep" vs "liquidity event") and guessing there
# would invent or erase a requirement rather than report one.
_SEQUENCE_FLAG_SPECS: tuple[tuple[str, str, str], ...] = (
    ("htf_bos", "htf_bos", r"\bbos\b"),
    ("follow_through", "has_follow_through", r"follow[\s\-]?through"),
)

_ABSENCE_PRESENT = "NOT_ABSENT_OBSERVED_PRESENT"
_ABSENCE_MANDATORY = "MANDATORY_MISSING"
_ABSENCE_OPTIONAL = "OPTIONAL_CONTEXT_ONLY"


def _classify_sequence_flag(observed: Any, required: bool) -> tuple[bool, str]:
    """Classify one global sequence flag.

    Returns (observed_present, absence_classification).  An unknown/None observation is
    NOT treated as present, so a missing field still fails closed into the required
    branch rather than silently reading as satisfied.
    """

    present = bool(observed) if observed is not None else False
    if present:
        return present, _ABSENCE_PRESENT
    return present, (_ABSENCE_MANDATORY if required else _ABSENCE_OPTIONAL)


def _sequence_flag_requirements(
    required_sequence: Sequence[str], observed_flags: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    haystack = [str(entry or "").strip().lower() for entry in required_sequence]
    out: dict[str, dict[str, Any]] = {}
    for name, field, pattern in _SEQUENCE_FLAG_SPECS:
        matched = next((entry for entry in haystack if re.search(pattern, entry)), None)
        required = matched is not None
        observed = observed_flags.get(field)
        present, classification = _classify_sequence_flag(observed, required)
        out[name] = {
            "sequence_field": f"sequence.{field}",
            "observed": present,
            "required": required,
            "required_by": matched,
            "absence_classification": classification,
        }
    return out


def _family_requirement_contract(
    taxonomy: str, profile: Any, observed_flags: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Make the family evidence boundary explicit for every provider.

    The family profile is intentionally descriptive.  Smaller remote models
    were treating the globally reported HTF BOS field as mandatory for every
    setup, even when a micro-family profile never required it.  This is a
    deterministic interpretation of the existing taxonomy, not a new trading
    rule and not model-generated context.

    An ``*_absence_classification`` classifies an *absence*, so it must read the
    observed value.  ``htf_bos_absence_classification`` previously derived only
    from the family, which made it emit ``MANDATORY_MISSING`` for every full-PO3
    candidate even when ``sequence.htf_bos`` was true.  Providers correctly
    reported that as ``ai_veto_data_integrity_failure`` /
    ``ai_veto_sequence_contradiction``: one field said the HTF BOS existed while
    another said it was mandatorily missing.

    ``sequence_field_requirements`` generalises the same treatment so the next
    optional global flag does not repeat the defect.  ``follow_through`` was the
    second instance: it is named in no family's required_event_sequence, yet with
    no explicit contract entry the model fell back to its own judgement and
    abstained on its absence -- which ``optional_absence_rule`` forbids.
    """

    normalized = str(taxonomy or "").strip().upper()
    full_po3 = normalized in _FULL_PO3_TAXONOMIES
    required_sequence = list(getattr(profile, "required_event_sequence", ()) or ())
    flags = _sequence_flag_requirements(required_sequence, observed_flags or {})
    bos = flags["htf_bos"]
    follow = flags["follow_through"]
    return {
        "context_version": PROVIDER_DECISION_CONTEXT_VERSION,
        "family_scope": "FULL_PO3" if full_po3 else "MICRO_OR_TIER_B",
        "full_po3_sequence_required": full_po3,
        "sequence_field_requirements": flags,
        "htf_bos_required": bos["required"],
        "htf_bos_observed": bos["observed"],
        "htf_bos_absence_classification": bos["absence_classification"],
        "follow_through_required": follow["required"],
        "follow_through_observed": follow["observed"],
        "follow_through_absence_classification": follow["absence_classification"],
        "required_event_sequence": required_sequence,
        "mandatory_evidence_rule": (
            "Only an absent event named by family_profile.required_event_sequence "
            "may be reported as missing mandatory family evidence."
        ),
        "optional_absence_rule": (
            "Do not reject, abstain, lower a score, or add missing evidence solely "
            "because an optional global PO3 field is false or absent."
        ),
    }


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _first(mapping: Mapping[str, Any], names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return default


def _number_evidence(
    value: Any,
    *,
    unit: str,
    source: str,
    observed_at: int,
    now: int,
    required: bool = True,
) -> dict[str, Any]:
    valid = _finite(value)
    normalized = float(value) if valid else None
    body = {
        "value": normalized,
        "unit": unit,
        "source": source,
        "observed_at": int(observed_at or 0),
        "freshness_sec": max(0, int(now - observed_at)) if observed_at > 0 else None,
        "valid": bool(valid),
        "required": bool(required),
    }
    body["lineage_hash"] = canonical_hash(body)
    return body


@dataclass(frozen=True)
class EvidenceBuildResult:
    envelope: dict[str, Any]
    valid: bool
    missing_fields: tuple[str, ...]
    invalid_fields: tuple[str, ...]
    hard_blockers: tuple[str, ...]


def _candidate_evidence(
    candidate: Mapping[str, Any],
    index: int,
    request_time: int,
    now: int,
    observed_flags: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    item = dict(candidate)
    missing: list[str] = []
    invalid: list[str] = []
    taxonomy = str(item.get("setup_taxonomy_enum") or "").strip().upper()
    try:
        profile = family_context_for(taxonomy)
    except ValueError:
        profile = None
        missing.append(f"candidates[{index}].family_context")

    required_text = (
        "candidate_id",
        "candidate_hash",
        "request_execution_fingerprint",
        "setup_taxonomy_version",
        "setup_taxonomy_enum",
        "taxonomy_mapping_source",
    )
    for name in required_text:
        if not str(item.get(name) or "").strip():
            missing.append(f"candidates[{index}].{name}")

    numeric_specs = {
        "entry": (_first(item, ("entry_est", "entry")), "price", "MQL5_trade_plan"),
        "sl": (item.get("sl"), "price", "MQL5_stop_model"),
        "tp1": (item.get("tp1"), "price", "MQL5_target_builder"),
        "tp2": (item.get("tp2"), "price", "MQL5_target_builder"),
        "net_rr": (
            _first(item, ("net_reward_after_cost_r", "effective_rr2", "net_rr", "rr2")),
            "R",
            "MQL5_execution_cost_model",
        ),
        "spread_r": (item.get("spread_r"), "R", "MQL5_broker_quote"),
        "slippage_r": (item.get("slippage_r"), "R", "MQL5_execution_cost_model"),
        "execution_cost_r": (item.get("execution_cost_r"), "R", "MQL5_execution_cost_model"),
        "atr_pct": (item.get("atr_pct"), "percent", "MQL5_ATR_calculator"),
        "adr_pct": (item.get("adr_pct"), "percent", "MQL5_ADR_calculator"),
        "vwap_dist_atr": (item.get("vwap_dist_atr"), "ATR_multiple", "MQL5_VWAP_calculator"),
        "volume_impulse_score": (_first(item, ("volume_impulse_score", "displacement_volume_ratio")), "normalized_score", "MQL5_volume_proxy"),
        "sequence_quality": (item.get("sequence_quality"), "score", "MQL5_sequence_validator"),
        "htf_alignment_score": (item.get("htf_alignment_score"), "score", "MQL5_HTF_context"),
        "adverse_context_score": (item.get("adverse_context_score"), "score", "MQL5_context_engine"),
        "obstacle_distance_r": (item.get("obstacle_distance_r"), "R", "MQL5_target_builder"),
        "liquidity_rr": (item.get("liquidity_rr"), "R", "MQL5_target_builder"),
    }
    authoritative_numbers: dict[str, Any] = {}
    required_positive = {"entry", "sl", "tp2"}
    for name, (value, unit, source) in numeric_specs.items():
        required = name in {"entry", "sl", "tp2", "net_rr", "spread_r", "execution_cost_r"}
        evidence = _number_evidence(
            value,
            unit=unit,
            source=source,
            observed_at=request_time,
            now=now,
            required=required,
        )
        authoritative_numbers[name] = evidence
        if required and not evidence["valid"]:
            invalid.append(f"candidates[{index}].{name}")
        if name in required_positive and evidence["valid"] and float(evidence["value"]) <= 0.0:
            invalid.append(f"candidates[{index}].{name}_non_positive")

    compact = {
        key: item.get(key)
        for key in (
            "candidate_index",
            "candidate_id",
            "candidate_hash",
            "request_execution_fingerprint",
            "assessed_execution_fingerprint",
            "symbol",
            "direction",
            "is_buy",
            "setup_code",
            "model_code",
            "setup_family",
            "setup_class",
            "setup_taxonomy_version",
            "setup_taxonomy_enum",
            "taxonomy_mapping_source",
            "entry_branch",
            "entry_model",
            "source_t_sweep",
            "source_t_disp",
            "source_t_bos",
            "session_name",
            "session_code",
            "killzone_code",
            "asset_class",
            "regime_profile",
            "volatility_profile",
            "target_source",
            "target_model",
            "tp_model",
            "obstacle_kind",
            "obstacle_tf",
            "fvg_lower",
            "fvg_upper",
            "fvg_mid",
            "fvg_fresh",
            "fvg_touched",
            "fvg_retested",
            "structure_state",
            "po3_state",
            "po3_state_id",
            "po3_state_reason",
            "po3_scope",
            "source_context_tier",
            "structure_type",
            "final_setup_class",
            "fvg_execution_class",
            "fvg_mitigation_state",
            "fvg_invalidation_reason",
            "fvg_continuation",
            "fvg_reversal",
            "fvg_context_type",
            "fvg_mid_mitigated",
            "fvg_fully_filled",
            "fvg_invalidated",
            "fvg_entry_invalid",
            "fvg_structure_invalidated",
            "fvg_score",
            "origin_score",
            "cleanliness_score",
            "freshness_score",
            "retest_quality_score",
            "continuation_score",
            "reversal_score",
            "displacement_candle_score",
            "opposing_obstruction_score",
            "stop_model",
            "configured_stop_model",
            "stop_quality_score",
            "target_arbitration_required",
            "liquidity_target_valid_structurally",
            "liquidity_target_blocked_by_obstacle",
            "historical_evidence_state",
            "retrieved_analogue_ids",
            "rule_score",
            "target_candidates",
            "bucket_prior",
        )
        if key in item
    }
    compact["candidate_index"] = int(item.get("candidate_index", index))
    compact["authoritative_numbers"] = authoritative_numbers
    compact["family_profile"] = profile.as_payload() if profile is not None else {}
    compact["family_requirement_contract"] = _family_requirement_contract(
        taxonomy, profile, observed_flags
    )
    contract = compact["family_requirement_contract"]
    compact["family_scope"] = contract["family_scope"]
    compact["full_po3_sequence_required"] = contract["full_po3_sequence_required"]
    for name in (
        "htf_bos_required",
        "htf_bos_observed",
        "htf_bos_absence_classification",
        "follow_through_required",
        "follow_through_observed",
        "follow_through_absence_classification",
    ):
        compact[name] = contract[name]
    compact["family_profile_version"] = FAMILY_PROFILE_VERSION
    compact["candidate_evidence_hash"] = canonical_hash(compact)
    return compact, missing, invalid


def build_decision_evidence_envelope(
    payload: Mapping[str, Any],
    *,
    historical_analogues: Sequence[Mapping[str, Any]] = (),
    provider_identity: Mapping[str, Any] | None = None,
) -> EvidenceBuildResult:
    now = int(time.time())
    request_time = int(
        payload.get("setup_snapshot_time")
        or payload.get("ai_request_time")
        or payload.get("created_at")
        or now
    )
    runtime = _mapping(payload.get("runtime"))
    runtime_inputs = _mapping(payload.get("runtime_inputs"))
    po3 = _mapping(payload.get("po3"))
    regime = _mapping(payload.get("regime"))
    validation = _mapping(payload.get("validation"))
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    missing: list[str] = []
    invalid: list[str] = []
    hard_blockers: list[str] = []
    candidate_rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            invalid.append(f"candidates[{index}]_not_object")
            continue
        row, row_missing, row_invalid = _candidate_evidence(
            candidate,
            index,
            request_time,
            now,
            {field: po3.get(field) for _, field, _ in _SEQUENCE_FLAG_SPECS},
        )
        candidate_rows.append(row)
        missing.extend(row_missing)
        invalid.extend(row_invalid)
    if not candidate_rows:
        missing.append("candidates[]")

    reported_missing = validation.get("missing_fields")
    if isinstance(reported_missing, list):
        missing.extend(str(value) for value in reported_missing if str(value).strip())
    reported_blockers = validation.get("hard_blockers")
    if isinstance(reported_blockers, list):
        hard_blockers.extend(str(value) for value in reported_blockers if str(value).strip())

    envelope: dict[str, Any] = {
        "evidence_envelope_version": EVIDENCE_ENVELOPE_VERSION,
        "identity": {
            "request_id": str(payload.get("id") or ""),
            "session_id": str(payload.get("session_id") or ""),
            "request_nonce": str(payload.get("request_nonce") or ""),
            "request_fingerprint": str(payload.get("request_fingerprint") or ""),
            "request_identity_version": str(payload.get("request_identity_version") or ""),
            "request_identity_hash": str(payload.get("request_identity_hash") or ""),
            "request_created_sim_time": int(payload.get("request_created_sim_time") or 0),
            "request_created_wall_time": int(payload.get("request_created_wall_time") or 0),
            "ordered_candidate_identities": list(payload.get("ordered_candidate_identities") or []),
            "lineage_id": str(payload.get("setup_lineage_id") or payload.get("story_id") or payload.get("id") or ""),
        },
        "runtime": {
            "workload_mode": str(payload.get("workload_mode") or runtime.get("workload_mode") or ""),
            "runtime_input_hash": str(payload.get("runtime_input_hash") or runtime_inputs.get("runtime_input_hash") or ""),
            "decision_input_hash": str(payload.get("decision_input_hash") or runtime_inputs.get("decision_input_hash") or ""),
            "observed_at": request_time,
        },
        "instrument": {
            "symbol": str(payload.get("symbol") or ""),
            "asset_class": str(payload.get("asset_class") or ""),
            "symbol_digits": payload.get("symbol_digits"),
            "symbol_tick_size": payload.get("symbol_tick_size"),
        },
        "setup_taxonomy": {
            "taxonomy_version": SETUP_TAXONOMY_VERSION,
            "candidate_taxonomies": [row.get("setup_taxonomy_enum") for row in candidate_rows],
            "family_profile_version": FAMILY_PROFILE_VERSION,
        },
        "sequence": {
            "po3_state": po3.get("po3_state"),
            "po3_state_reason": po3.get("po3_state_reason"),
            "po3_scope": po3.get("po3_scope"),
            "context_tier": po3.get("context_tier"),
            "sweep_side": po3.get("sweep_side"),
            "structure_type": po3.get("structure_type"),
            "htf_structure_type": po3.get("htf_structure_type"),
            "ltf_structure_type": po3.get("ltf_structure_type"),
            "has_sweep": po3.get("has_sweep"),
            "has_displacement": po3.get("has_displacement"),
            "has_bos": po3.get("has_bos"),
            "has_follow_through": po3.get("has_follow_through"),
            "developing_bos": po3.get("developing_bos"),
            "htf_bos": po3.get("htf_bos"),
            "htf_internal_bos": po3.get("htf_internal_bos"),
            "htf_swing_bos": po3.get("htf_swing_bos"),
            "htf_mss": po3.get("htf_mss"),
            "htf_choch": po3.get("htf_choch"),
            "ltf_bos": po3.get("ltf_bos"),
            "ltf_internal_bos": po3.get("ltf_internal_bos"),
            "ltf_swing_bos": po3.get("ltf_swing_bos"),
            "ltf_mss": po3.get("ltf_mss"),
            "ltf_choch": po3.get("ltf_choch"),
            "t_sweep": po3.get("t_sweep"),
            "t_disp": po3.get("t_disp"),
            "t_bos": po3.get("t_bos"),
            "t_follow": po3.get("t_follow"),
            "ltf_structure_time": po3.get("ltf_structure_time"),
            "ltf_structure_level": po3.get("ltf_structure_level"),
            "displacement_score": po3.get("displacement_score"),
            "displacement_body_frac": po3.get("displacement_body_frac"),
            "displacement_range_atr": po3.get("displacement_range_atr"),
            "displacement_volume_ratio": po3.get("displacement_volume_ratio"),
            "daily_bias_dir": po3.get("daily_bias_dir"),
            "h4_bias_dir": po3.get("h4_bias_dir"),
            "h1_bias_dir": po3.get("h1_bias_dir"),
        },
        "market_regime": regime,
        "htf_context": _mapping(payload.get("htf_context")),
        "ltf_execution": _mapping(payload.get("ltf_execution")),
        "liquidity": {
            "session_name": po3.get("session_name"),
            "in_killzone": po3.get("in_killzone"),
            "liquidity_target": po3.get("liquidity_target"),
        },
        "entry_and_invalidation": {"candidates": candidate_rows},
        "targets_and_obstacles": {
            "candidate_targets": [row.get("target_candidates") for row in candidate_rows],
        },
        "execution_costs": {
            "authoritative_source": "MQL5_execution_cost_model",
            "per_candidate": [row["authoritative_numbers"] for row in candidate_rows],
        },
        "risk": {
            "deterministic_only": True,
            "portfolio_state": _mapping(payload.get("portfolio_state")),
        },
        "correlations": _mapping(payload.get("correlations")),
        "validation": {
            "missing_fields": sorted(set(missing)),
            "invalid_fields": sorted(set(invalid)),
            "hard_blockers": sorted(set(hard_blockers)),
            "deterministic_numbers_authoritative": True,
        },
        "provider_decision_context": {
            "context_version": PROVIDER_DECISION_CONTEXT_VERSION,
            "source": "deterministic_projection_of_frozen_mql_request",
            "purpose": "navigation_and_family_requirement_disambiguation",
            "authority": (
                "Observed values remain authoritative in the canonical sections; "
                "this block only explains how to interpret them."
            ),
            "rules": [
                "Evaluate each candidate against its own family_requirement_contract.",
                "Do not apply the full PO3 sweep-displacement-HTF-BOS sequence to a micro family.",
                "False or absent optional context is neutral, not missing mandatory evidence.",
                (
                    "family_requirement_contract.sequence_field_requirements resolves every "
                    "global PO3 sequence flag for this candidate. Each entry mirrors the "
                    "sequence field it names: observed is the value you can read there, "
                    "required says whether required_event_sequence names it, and "
                    "absence_classification is NOT_ABSENT_OBSERVED_PRESENT when it was "
                    "observed, MANDATORY_MISSING when it is absent and required, or "
                    "OPTIONAL_CONTEXT_ONLY when it is absent and not required."
                ),
                (
                    "OPTIONAL_CONTEXT_ONLY is resolved, not unresolved. Do not abstain, "
                    "lower confidence, or record a missing confirmation because such a flag "
                    "is false; follow_through in particular is optional for every family, "
                    "so its absence alone is not a reason to withhold a decision."
                ),
                "Use historical evidence only when its asset-class applicability is sufficient.",
                "Use only supplied feasible targets and never invent a price.",
            ],
        },
        "historical_analogues": [dict(row) for row in historical_analogues],
        "authority_manifest": {
            "deterministic": "prices_costs_risk_session_broker_feasibility_identity_fingerprints",
            "statistical": "validated_artifacts_only",
            "llm": "qualitative_audit_veto_abstention_narrative_only",
            "portfolio": "aggregate_risk_and_exposure",
            "management": "independently_validated_exit_policy",
            "provider": dict(provider_identity or {}),
        },
    }
    envelope["input_fingerprint"] = canonical_hash(envelope)
    valid = not missing and not invalid and not hard_blockers
    return EvidenceBuildResult(
        envelope=envelope,
        valid=valid,
        missing_fields=tuple(sorted(set(missing))),
        invalid_fields=tuple(sorted(set(invalid))),
        hard_blockers=tuple(sorted(set(hard_blockers))),
    )
