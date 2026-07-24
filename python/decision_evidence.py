"""Canonical, provider-neutral evidence envelope for Version Z AI passes."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Sequence

from family_context import FAMILY_PROFILE_VERSION, family_context_for
from governance_contracts import SETUP_TAXONOMY_VERSION


EVIDENCE_ENVELOPE_VERSION = "20260718_decision_evidence_v1"


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


def _candidate_evidence(candidate: Mapping[str, Any], index: int, request_time: int, now: int) -> tuple[dict[str, Any], list[str], list[str]]:
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
            "target_candidates",
            "bucket_prior",
        )
        if key in item
    }
    compact["candidate_index"] = int(item.get("candidate_index", index))
    compact["authoritative_numbers"] = authoritative_numbers
    compact["family_profile"] = profile.as_payload() if profile is not None else {}
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
        row, row_missing, row_invalid = _candidate_evidence(candidate, index, request_time, now)
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
            "has_sweep": po3.get("has_sweep"),
            "has_displacement": po3.get("has_displacement"),
            "has_bos": po3.get("has_bos"),
            "t_sweep": po3.get("t_sweep"),
            "t_disp": po3.get("t_disp"),
            "t_bos": po3.get("t_bos"),
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
