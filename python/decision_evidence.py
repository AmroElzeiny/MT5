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
PROVIDER_DECISION_CONTEXT_VERSION = "20260909_provider_decision_context_v6"


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
        "deferred_execution_triggers": list(
            getattr(profile, "deferred_execution_triggers", ()) or ()
        ),
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


_ASSET_CLASS_ALIASES = {
    "forex": "fx",
    "fx": "fx",
    "metal": "metals",
    "metals": "metals",
    "index": "indices",
    "indices": "indices",
    "energy": "energy",
    "crypto": "crypto",
    "cfd": "other",
    "other": "other",
}
_FX_CURRENCIES = {
    "AUD", "CAD", "CHF", "CNH", "EUR", "GBP", "HKD", "HUF", "JPY",
    "MXN", "NOK", "NZD", "PLN", "SEK", "SGD", "TRY", "USD", "ZAR",
}


def _canonical_asset_class(value: Any) -> str:
    return _ASSET_CLASS_ALIASES.get(str(value or "").strip().lower(), "")


def _asset_class_from_symbol(symbol: Any) -> str:
    token = re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())
    if any(name in token for name in ("XAU", "GOLD", "XAG", "SILVER", "XPT", "XPD")):
        return "metals"
    if any(name in token for name in ("WTI", "BRENT", "OIL", "NGAS")):
        return "energy"
    if any(name in token for name in ("BTC", "ETH", "SOL", "LTC", "XRP")):
        return "crypto"
    if any(
        name in token
        for name in (
            "US30", "USNDAQ", "NASDAQ", "NAS100", "USSPX", "SPX", "US500",
            "GERMANY", "GER40", "DE40", "DAX", "UK100", "JAPAN", "JP225",
            "US2000", "FRANCE", "FRA40", "EURO50",
        )
    ):
        return "indices"
    letters = re.sub(r"[^A-Z]", "", token)
    if len(letters) >= 6 and letters[:3] in _FX_CURRENCIES and letters[3:6] in _FX_CURRENCIES:
        return "fx"
    return "other"


def _resolved_asset_class(item: Mapping[str, Any], request_symbol: str) -> tuple[str, str]:
    symbol = str(item.get("symbol") or request_symbol or "")
    inferred = _asset_class_from_symbol(symbol)
    explicit = _canonical_asset_class(item.get("asset_class"))
    normalized_fvg = _canonical_asset_class(item.get("fvg_normalized_asset_class"))
    # Known symbol aliases are stronger than legacy generic-six-letter fallbacks,
    # which mislabeled #USNDAQ100/#Japan225 as FX in the captured live requests.
    if inferred != "other":
        return inferred, "symbol_alias_classifier"
    if explicit:
        return explicit, "candidate_asset_class"
    if normalized_fvg:
        return normalized_fvg, "normalized_fvg_asset_class"
    return "other", "unclassified"


def _target_review_projection(item: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = _mapping(item.get("target_candidates"))
    current = _mapping(raw.get("keep_current_target"))
    current_price = _first(current, ("tp2", "tp"), item.get("tp2"))
    current_source = str(
        raw.get("current_target_source")
        or item.get("target_source")
        or item.get("tp_model")
        or ""
    )
    semantics = {
        "current_selection_identity": "keep_current",
        "current_underlying_source": current_source,
        "current_underlying_tp_model": str(
            raw.get("current_tp_model") or item.get("tp_model") or current_source
        ),
        "current_target_price": current_price,
        "family_target_policy": str(item.get("target_model") or ""),
        "liquidity_objective_model": str(item.get("liquidity_target_model") or ""),
        "labels_have_distinct_meanings": True,
        "interpretation": (
            "keep_current names the selectable incumbent route; current_underlying_source "
            "names its price source; family_target_policy is a preference and is not the "
            "selected execution target. Never compare these as aliases."
        ),
    }
    menu: dict[str, Any] = {
        "arbitration_required": bool(raw.get("arbitration_required", item.get("target_arbitration_required"))),
        "incumbent_target": {
            "identity": "keep_current",
            "underlying_source": current_source,
            "underlying_tp_model": semantics["current_underlying_tp_model"],
            "tp2": current_price,
            "rr2": _first(current, ("rr2", "rr"), raw.get("current_rr2")),
            "available": current.get("available"),
            "feasible_for_tp2": current.get("feasible_for_tp2"),
            "infeasible_reason": current.get("infeasible_reason"),
        },
    }
    for key in (
        "keep_current_target",
        "liquidity_target",
        "partial_before_obstacle_then_liquidity",
        "capped_before_obstacle",
        "synthetic_rr_fallback",
        "synthetic_rr_capped_to_max_distance",
        "blocker_features",
    ):
        value = raw.get(key)
        if isinstance(value, Mapping):
            menu[key] = dict(value)
    for key in ("obstacle_kind", "obstacle_price", "obstacle_r", "obstacle_distance_r", "obstacle_tf"):
        if key in raw:
            menu[key] = raw[key]
    return semantics, menu


def _family_event_evidence(
    item: Mapping[str, Any], taxonomy: str, po3: Mapping[str, Any]
) -> dict[str, Any]:
    branch = str(item.get("entry_branch") or item.get("entry_model") or "").strip().lower()
    valid_zone = bool(
        _finite(item.get("fvg_lower"))
        and _finite(item.get("fvg_upper"))
        and float(item.get("fvg_upper")) > float(item.get("fvg_lower"))
        and not bool(item.get("fvg_invalidated"))
        and not bool(item.get("fvg_fully_filled"))
        and not bool(item.get("fvg_entry_invalid"))
        and not bool(item.get("fvg_structure_invalidated"))
    )
    touched = bool(item.get("fvg_touched") or item.get("fvg_mid_mitigated"))
    trigger_state = (
        "INVALIDATED"
        if not valid_zone
        else ("OBSERVED" if touched else "PENDING_ENTRY_TRIGGER")
    )
    source_confirmed = bool(
        str(item.get("source_context_tier") or po3.get("context_tier") or "").strip()
        and (int(item.get("source_t_sweep") or po3.get("t_sweep") or 0) > 0
             or int(item.get("source_t_disp") or po3.get("t_disp") or 0) > 0)
    )
    displacement_confirmed = bool(
        item.get("displacement_confirmed")
        or po3.get("has_displacement")
        or int(item.get("source_t_disp") or po3.get("t_disp") or 0) > 0
    )
    structure_confirmed = bool(
        item.get("breaker_formation_confirmed")
        or po3.get("has_bos")
        or po3.get("htf_mss")
        or po3.get("htf_choch")
        or po3.get("ltf_bos")
        or po3.get("ltf_mss")
        or po3.get("ltf_choch")
    )
    branch_validated = bool(item.get("branch_contract_validated", bool(branch)))
    approval: dict[str, str] = {
        "source_context": "CONFIRMED" if source_confirmed else "MISSING",
    }
    triggers: dict[str, str] = {}
    if taxonomy == SetupTaxonomy.MICRO_BREAKER_RETEST.value:
        approval["breaker_formation"] = (
            "CONFIRMED" if branch == "breaker_retest" and branch_validated and structure_confirmed else "MISSING"
        )
        triggers["clean_retest"] = trigger_state
    elif taxonomy == SetupTaxonomy.MICRO_OTE_REVERSAL.value:
        ote_state = str(item.get("ote_state") or "").strip().lower()
        ote_geometry = bool(
            item.get("ote_geometry_valid")
            or (branch == "ote_inside_fvg" and branch_validated and ote_state not in {"lost", "unavailable"})
        )
        approval["displacement"] = "CONFIRMED" if displacement_confirmed else "MISSING"
        approval["ote_geometry"] = "CONFIRMED" if ote_geometry else "MISSING"
        triggers["ote_price_retracement"] = trigger_state
    elif taxonomy == SetupTaxonomy.MICRO_RANGE_REENTRY.value:
        approval["range_identity"] = "CONFIRMED" if bool(item.get("dealing_range_valid", branch_validated)) else "MISSING"
        approval["range_excursion"] = "CONFIRMED" if bool(po3.get("has_sweep") or item.get("source_t_sweep")) else "MISSING"
        approval["range_reentry_plan"] = "CONFIRMED" if branch == "range_reentry" and branch_validated else "MISSING"
        triggers["range_reentry_price"] = trigger_state
    elif taxonomy == SetupTaxonomy.MICRO_SESSION_REENTRY.value:
        approval["session_range_identity"] = "CONFIRMED" if bool(item.get("session_range_valid", branch_validated)) else "MISSING"
        approval["session_excursion"] = "CONFIRMED" if bool(po3.get("has_sweep") or item.get("source_t_sweep")) else "MISSING"
        approval["session_reentry_plan"] = "CONFIRMED" if branch == "session_reentry" and branch_validated else "MISSING"
        triggers["session_reentry_price"] = trigger_state
    else:
        approval["branch_contract"] = "CONFIRMED" if branch_validated else "MISSING"
        if displacement_confirmed:
            approval["displacement"] = "CONFIRMED"
        triggers["entry_zone_touch"] = trigger_state
    return {
        "approval_events": approval,
        "execution_triggers": triggers,
        "approval_contract_satisfied": all(value == "CONFIRMED" for value in approval.values()),
        "execution_trigger_pending": any(value == "PENDING_ENTRY_TRIGGER" for value in triggers.values()),
        "contract_rule": (
            "Only a MISSING approval event can be mandatory evidence for AI review. "
            "A PENDING_ENTRY_TRIGGER is enforced later by the MQL watchlist and is not missing preapproval evidence."
        ),
    }


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
    request_symbol: str = "",
    po3_context: Mapping[str, Any] | None = None,
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
            # Counterfactual (shadow) history for comparable prior decisions.
            # Diagnostic by construction: the payload carries its own authority
            # and promotion-gate state, and is built only from outcomes that had
            # already resolved before this request's own timestamp.
            "shadow_historical_evidence",
            "rule_score",
            "bucket_prior",
        )
        if key in item
    }
    compact["candidate_index"] = int(item.get("candidate_index", index))
    resolved_asset_class, asset_source = _resolved_asset_class(item, request_symbol)
    compact["asset_class"] = resolved_asset_class
    compact["asset_class_source"] = asset_source
    target_semantics, target_menu = _target_review_projection(item)
    compact["target_semantics"] = target_semantics
    compact["target_choice_menu"] = target_menu
    compact["family_event_evidence"] = _family_event_evidence(
        item, taxonomy, po3_context or {}
    )
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
            request_symbol=str(payload.get("symbol") or ""),
            po3_context=po3,
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

    candidate_asset_classes = {
        str(row.get("asset_class") or "") for row in candidate_rows if str(row.get("asset_class") or "")
    }
    root_explicit_asset = _canonical_asset_class(payload.get("asset_class"))
    inferred_root_asset = _asset_class_from_symbol(payload.get("symbol"))
    resolved_root_asset = (
        inferred_root_asset
        if inferred_root_asset != "other"
        else (next(iter(candidate_asset_classes)) if len(candidate_asset_classes) == 1 else root_explicit_asset or "other")
    )
    asset_consistent = len(candidate_asset_classes) <= 1 and all(
        value == resolved_root_asset for value in candidate_asset_classes
    )
    if not asset_consistent:
        invalid.append("instrument.asset_class_candidate_mismatch")

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
            "asset_class": resolved_root_asset,
            "asset_class_source": (
                "symbol_alias_classifier" if inferred_root_asset != "other" else "candidate_consensus"
            ),
            "asset_class_consistent": asset_consistent,
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
            "bos_level": po3.get("bos_level"),
            "dr_high": po3.get("dr_high"),
            "dr_low": po3.get("dr_low"),
            "session_high": po3.get("session_high"),
            "session_low": po3.get("session_low"),
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
            "candidate_targets": [row.get("target_choice_menu") for row in candidate_rows],
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
                (
                    "candidate_id, candidate_hash, and execution fingerprints are internal identity and are not "
                    "provider evidence. Never parse an embedded identity component as an entry price; use only "
                    "authoritative_numbers.entry for the executable entry."
                ),
                (
                    "target_semantics separates the selectable current route from its underlying price source, "
                    "the family's target policy, and the alternative liquidity objective. Those labels have "
                    "different meanings and must never be compared as aliases or treated as a contradiction."
                ),
                (
                    "family_event_evidence separates approval events from deferred execution triggers. Only a "
                    "MISSING approval event is missing mandatory evidence. PENDING_ENTRY_TRIGGER is valid for a "
                    "staged plan because the MQL watchlist waits for and revalidates that trigger before execution."
                ),
                (
                    "shadow_historical_evidence, when present, reports what comparable "
                    "PRIOR decisions of the same kind actually did after the fact. It is "
                    "counterfactual research: every sample resolved strictly before this "
                    "request's own timestamp, ambiguous, data-loss and entry-never-reached "
                    "cases are excluded rather than counted as wins or losses, and "
                    "state=INSUFFICIENT_SAMPLE means there is not enough history to read "
                    "anything from it. Treat authority=DIAGNOSTIC_SHADOW_ONLY as context "
                    "for your reasoning only: it must never by itself approve a trade, "
                    "override a veto, or replace the evidence in front of you."
                ),
                (
                    "shadow_historical_evidence state=INSUFFICIENT_SAMPLE is a RESOLVED "
                    "absence, not adverse evidence: the research is silent, not negative. "
                    "Do not reject, abstain, lower a score, or record a missing "
                    "confirmation solely because shadow history is insufficient, absent, "
                    "or reports zero samples. This forbids treating the ABSENCE of history "
                    "as a finding; it does not forbid relying on history that IS present -- "
                    "when state=SUFFICIENT_SAMPLE the reported rates are real evidence and "
                    "may be cited for or against the setup."
                ),
                (
                    "shadow_historical_evidence.match_specificity says how the reported "
                    "history was matched. EXACT_FAMILY_TAXONOMY_STATE matched family, setup "
                    "taxonomy and decision state; FAMILY_TAXONOMY_ANY_DECIDED_STATE widened "
                    "the decision state; FAMILY_ANY_DECIDED_STATE widened to the family "
                    "alone. A widened match describes a wider population, so weight it "
                    "accordingly and name the specificity you relied on. Rates are never "
                    "pooled across decision states: every rate you are shown was computed "
                    "inside the single state named by decision_state_compared."
                ),
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
