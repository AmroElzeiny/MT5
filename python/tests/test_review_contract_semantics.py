"""Regression coverage for the 2026-09-08 analyst-to-reviewer demotions.

The live cohort exposed four provider-facing contract defects: an empty or wrong
asset class, an internal identity price presented as if it were executable,
three target labels with different meanings presented as aliases, and entry
triggers described as already-required family formation events.
"""

from __future__ import annotations

from pathlib import Path

import ai_gate
from decision_evidence import build_decision_evidence_envelope
from evidence_catalog import build_evidence_catalog
from tests.test_decision_integrity import candidate
from tests.test_python_owned_identity_lifecycle import _payload


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "MT5_PO3_Codex Include" / "AIGateBridge.mqh"
ENGINE = ROOT / "MT5_PO3_Codex Include" / "TradeEngine.mqh"
FVG = ROOT / "MT5_PO3_Codex Include" / "FVG.mqh"
RISK = ROOT / "MT5_PO3_Codex Include" / "Risk.mqh"


def _review_payload(*, symbol: str = "EURGBP", taxonomy: str = "MICRO_BREAKER_RETEST") -> dict:
    cand = candidate()
    cand.update(
        {
            "symbol": symbol,
            "setup_taxonomy_enum": taxonomy,
            "setup_family": "micro_breaker_retest",
            "entry_branch": "breaker_retest",
            "entry_model": "breaker_retest",
            "asset_class": "",
            "fvg_normalized_asset_class": "fx",
            "entry_est": 0.85861,
            "fvg_lower": 0.85858,
            "fvg_upper": 0.85861,
            "fvg_mid": 0.858595,
            "fvg_touched": False,
            "fvg_mid_mitigated": False,
            "fvg_invalidated": False,
            "fvg_fully_filled": False,
            "fvg_entry_invalid": False,
            "fvg_structure_invalidated": False,
            "source_context_tier": "B",
            "source_t_sweep": 100,
            "source_t_disp": 200,
            "source_t_bos": 0,
            "target_source": "asia_session_high",
            "tp_model": "asia_session_high",
            "target_model": "next_liquidity_session_range",
            "liquidity_target_model": "next_liquidity_session_range",
            "target_arbitration_required": True,
            "target_candidates": {
                "arbitration_required": True,
                "current_target_source": "asia_session_high",
                "current_tp_model": "asia_session_high",
                "current_tp2": cand["tp2"],
                "current_rr2": 2.0,
                "keep_current_target": {
                    "available": True,
                    "model": "keep_current",
                    "tp2": cand["tp2"],
                    "rr2": 2.0,
                    "feasible_for_tp2": True,
                    "infeasible_reason": "",
                },
                "liquidity_target": {
                    "available": False,
                    "model": "next_liquidity_session_range",
                    "tp2": cand["tp2"] + 0.001,
                    "rr2": 3.0,
                    "feasible_for_tp2": False,
                    "infeasible_reason": "blocked_by_obstacle",
                    "blocked_by_obstacle": True,
                },
            },
        }
    )
    payload = _payload([cand])
    payload["symbol"] = symbol
    payload["asset_class"] = ""
    payload["po3"].update(
        {
            "context_tier": "B",
            "has_sweep": True,
            "has_displacement": True,
            "has_bos": False,
            "ltf_bos": True,
            "ltf_mss": True,
            "ltf_structure_time": 250,
            "ltf_structure_level": 0.85860,
            "bos_level": 0.85860,
        }
    )
    return payload


def test_asset_class_is_resolved_from_candidates_and_symbol() -> None:
    payload = _review_payload(symbol="#USNDAQ100")
    envelope = build_decision_evidence_envelope(payload).envelope
    assert envelope["instrument"]["asset_class"] == "indices"
    assert envelope["instrument"]["asset_class_consistent"] is True
    assert envelope["entry_and_invalidation"]["candidates"][0]["asset_class"] == "indices"


def test_target_labels_are_projected_into_one_unambiguous_menu() -> None:
    row = build_decision_evidence_envelope(_review_payload()).envelope[
        "entry_and_invalidation"
    ]["candidates"][0]
    assert "target_source" not in row
    assert "target_model" not in row
    assert "tp_model" not in row
    semantics = row["target_semantics"]
    assert semantics["current_selection_identity"] == "keep_current"
    assert semantics["current_underlying_source"] == "asia_session_high"
    assert semantics["family_target_policy"] == "next_liquidity_session_range"
    assert semantics["liquidity_objective_model"] == "next_liquidity_session_range"
    assert semantics["labels_have_distinct_meanings"] is True
    assert row["target_choice_menu"]["keep_current_target"]["feasible_for_tp2"] is True


def test_internal_candidate_identity_is_not_exposed_to_review_roles() -> None:
    envelope = build_decision_evidence_envelope(_review_payload()).envelope
    catalog = build_evidence_catalog(envelope)
    compact = ai_gate._compact_model_evidence_payload(envelope, catalog)
    row = compact["entry_and_invalidation"]["candidates"][0]
    for field in (
        "candidate_id",
        "candidate_hash",
        "request_execution_fingerprint",
        "assessed_execution_fingerprint",
    ):
        assert field not in row
        assert all(
            not str(item["p"]).endswith("." + field)
            for item in catalog.provider_rows()
        )
    assert row["candidate_index"] == 0
    assert row["authoritative_numbers"]["entry"] == 0.85861


def test_breaker_retest_trigger_is_explicitly_deferred_from_ai_approval() -> None:
    row = build_decision_evidence_envelope(_review_payload()).envelope[
        "entry_and_invalidation"
    ]["candidates"][0]
    events = row["family_event_evidence"]
    assert events["approval_contract_satisfied"] is True
    assert events["approval_events"]["breaker_formation"] == "CONFIRMED"
    assert events["execution_triggers"]["clean_retest"] == "PENDING_ENTRY_TRIGGER"
    contract = row["family_requirement_contract"]
    assert "clean retest" not in contract["required_event_sequence"]
    assert "clean retest" in contract["deferred_execution_triggers"]


def test_ote_price_touch_is_an_execution_trigger_not_a_preapproval_fact() -> None:
    payload = _review_payload(taxonomy="MICRO_OTE_REVERSAL")
    cand = payload["candidates"][0]
    cand.update(
        {
            "setup_family": "micro_po3_reversal",
            "entry_branch": "ote_inside_fvg",
            "entry_model": "ote_inside_fvg",
            "ote_state": "inside",
            "ote_distance_frac": 0.0,
        }
    )
    row = build_decision_evidence_envelope(payload).envelope[
        "entry_and_invalidation"
    ]["candidates"][0]
    assert row["family_event_evidence"]["approval_events"]["ote_geometry"] == "CONFIRMED"
    assert row["family_event_evidence"]["execution_triggers"]["ote_price_retracement"] == "PENDING_ENTRY_TRIGGER"
    assert "OTE retracement" not in row["family_requirement_contract"]["required_event_sequence"]


def test_mql_request_publishes_asset_and_family_event_facts() -> None:
    bridge = BRIDGE.read_text(encoding="utf-8", errors="ignore")
    for field in (
        'JsonKVStr("asset_class", c.asset_class)',
        'JsonKVBool("branch_contract_validated", true)',
        'JsonKVBool("breaker_formation_confirmed"',
        'JsonKVStr("entry_trigger_phase"',
        'JsonKVStr("ote_state", c.ote_state)',
    ):
        assert field in bridge


def test_all_mql_asset_classifiers_cover_live_index_aliases_before_fx_fallback() -> None:
    for path in (ENGINE, FVG, RISK):
        source = path.read_text(encoding="utf-8", errors="ignore")
        for alias in ("USNDAQ", "USSPX", "GERMANY", "JAPAN", "US2000", "FRANCE", "EURO50"):
            assert alias in source, f"{path.name} misses {alias}"
        assert 'return "indices"' in source
        assert 'return "fx"' in source
