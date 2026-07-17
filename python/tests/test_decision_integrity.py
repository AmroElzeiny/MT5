from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_gate
from decision_integrity import (
    AI_DECISION_SCHEMA_VERSION,
    AI_PROMPT_CONTRACT_VERSION,
    AI_TARGET_ARBITRATION_SCHEMA_VERSION,
    DECISION_ABSTAIN,
    MANDATORY_ASSESSMENT_FIELDS,
    MANDATORY_NULLABLE_CALIBRATION_FIELDS,
    RESPONSE_CACHE_FULL_STRUCTURED,
    RESPONSE_DEGRADED_NON_TRADING,
    RESPONSE_FULL_STRUCTURED,
    assessed_execution_fingerprint,
    execution_fingerprint,
    material_execution_changes,
    resolved_risk_multiplier,
    response_can_trade,
    validate_broker_execution_identity,
    validate_candidate_assessment,
    validate_decision_envelope,
)
from tools.verify_broker_fill_capture import CAPTURE_ORIGIN, verify_capture


ROOT = Path(__file__).resolve().parents[1]
MQL_INCLUDE = Path(
    r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex"
)


def candidate(index: int = 0, suffix: str = "A") -> dict:
    return {
        "candidate_index": index,
        "candidate_id": f"candidate-{suffix}",
        "candidate_hash": f"HASH{suffix}12345678",
        "request_execution_fingerprint": f"REQFP{suffix}12345678",
        "symbol": "GOLD",
        "direction": "buy",
        "is_buy": True,
        "setup_code": "FULL",
        "setup_family": "full_po3",
        "setup_taxonomy_version": "20260716_setup_taxonomy_v1",
        "setup_taxonomy_enum": "FULL_PO3_REVERSAL",
        "taxonomy_mapping_source": "explicit_internal_taxonomy_enum",
        "entry_branch": "fvg_mid",
        "source_t_sweep": 100,
        "source_t_disp": 101,
        "source_t_bos": 102,
        "entry_est": 100.0,
        "sl": 99.0,
        "tp1": 101.0,
        "tp2": 102.0,
        "net_rr": 2.0,
        "spread_r": 0.02,
        "slippage_r": 0.01,
        "execution_cost_r": 0.04,
        "target_source": "liquidity",
        "target_model": "liquidity_target",
        "obstacle_kind": "",
        "obstacle_tf": "",
        "obstacle_price": 0.0,
        "decision_input_hash": "economic-config-1",
        "runtime_input_hash": "workflow-runtime-1",
        "strategy_schema_version": "strategy-v1",
        "symbol_digits": 2,
        "symbol_tick_size": 0.01,
    }


def target_arbitration() -> dict:
    return {
        "target_arbitration_schema_version": AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
        "arbitration_required": False,
        "chosen_target_model": "liquidity_target",
        "chosen_tp1": 101.0,
        "chosen_tp2": 102.0,
        "chosen_rr1": 1.0,
        "chosen_rr2": 2.0,
        "rejected_target_models": [],
        "blocker_kind": "",
        "blocker_severity": -1.0,
        "blocker_class": "unknown",
        "blocker_is_trade_killer": False,
        "why_not_liquidity_target": "",
        "why_not_partial_before_obstacle": "not needed",
        "why_not_capped_before_obstacle": "not needed",
        "why_not_synthetic_fallback": "liquidity target is valid",
        "target_decision_reason": "valid liquidity target",
        "target_comparison": {},
    }


def assessment(cand: dict, *, quality: float = 8.0, state: str = "APPROVE") -> dict:
    approve = state == "APPROVE"
    item = {
        "candidate_index": cand["candidate_index"],
        "candidate_id": cand["candidate_id"],
        "candidate_hash": cand["candidate_hash"],
        "request_execution_fingerprint": cand["request_execution_fingerprint"],
        "setup_taxonomy_version": cand["setup_taxonomy_version"],
        "setup_taxonomy_enum": cand["setup_taxonomy_enum"],
        "taxonomy_mapping_source": cand["taxonomy_mapping_source"],
        "rule_score": 7.2,
        "llm_quality_score": quality,
        "blended_legacy_score": 1.0,
        "legacy_agreement_confidence": 0.1,
        "llm_self_reported_confidence": 0.05,
        "calibrated_win_probability": None,
        "expected_net_r": None,
        "oos_predicted_probability": None,
        "calibration_bucket": "",
        "calibration_sample_size": 0,
        "calibration_lower_bound": None,
        "calibration_upper_bound": None,
        "calibration_model_version": "",
        "calibration_data_window_start": "",
        "calibration_data_window_end": "",
        "calibration_available": False,
        "raw_allow": approve,
        "decision_state": state,
        "structure_quality_score": 8.1,
        "entry_timing_score": 7.8,
        "follow_through_probability": 0.7,
        "invalidation_risk": 0.2,
        "chop_risk": 0.2,
        "cost_risk": 0.2,
        "symbol_bucket_risk": 0.2,
        "session_bucket_risk": 0.2,
        "post_entry_failure_risk": 0.2,
        "final_trade_expectancy_score": 7.5,
        "veto": {"enabled": False, "reason": ""},
        "bucket_prior_override_justification": "",
        "reasons": "complete evidence",
        "rejection_codes": [],
        "narrative_state": "audited",
        "invalidation_risks": [],
        "missing_confirmations": [],
        "suggested_risk_multiplier": 0.5 if approve else 0.0,
        "selected_target_identity": "liquidity_target",
        "selected_target_price": 102.0,
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 101.0,
        "tp2": 102.0,
        "assessed_execution_fingerprint": "placeholder",
        "model_version": "integrity-test-model",
        "target_arbitration": target_arbitration(),
    }
    item["assessed_execution_fingerprint"] = assessed_execution_fingerprint(cand, item)
    return item


def envelope(candidates: list[dict], assessments: list[dict], selected: int = 0) -> dict:
    return {
        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        "decision_quality_tier": RESPONSE_FULL_STRUCTURED,
        "response_quality": RESPONSE_FULL_STRUCTURED,
        "selected_candidate_id": assessments[selected]["candidate_id"],
        "selected_candidate_hash": assessments[selected]["candidate_hash"],
        "candidate_assessments": assessments,
    }


def broker_fixture() -> tuple[dict, dict, dict, list[dict]]:
    expected = {
        "magic": 5303001,
        "symbol": "GOLD",
        "direction": "buy",
        "broker_comment": "FULL-LON-K-GOLD-18432",
        "requested_volume": 0.25,
        "trade_key": "trade-key-1",
        "candidate_id": "candidate-A",
        "candidate_hash": "HASHA12345678",
        "execution_fingerprint": "EXECFP12345678",
    }
    order = {
        "ticket": 7001,
        "magic": 5303001,
        "symbol": "GOLD",
        "comment": expected["broker_comment"],
        "volume": 0.25,
    }
    deal = {
        "ticket": 8001,
        "order_ticket": 7001,
        "magic": 5303001,
        "symbol": "GOLD",
        "entry": "in",
        "direction": "buy",
        "volume": 0.25,
        "position_id": 9001,
        "time": 1_720_000_000,
    }
    positions = [
        {
            "ticket": 9101,
            "identifier": 9001,
            "magic": 5303001,
            "symbol": "GOLD",
            "direction": "buy",
            "comment": expected["broker_comment"],
            "volume": 0.25,
            "open_time": 1_720_000_002,
        }
    ]
    return expected, order, deal, positions


class DecisionIntegrityTests(unittest.TestCase):
    def _demo_capture(self, order_kind: str) -> dict:
        expected, order, deal, positions = broker_fixture()
        return {
            "capture_origin": CAPTURE_ORIGIN,
            "order_kind": order_kind,
            "account_mode": "HEDGING_EXACT_POSITION_ID",
            "expected": expected,
            "order": order,
            "deal": deal,
            "positions": positions,
        }

    def test_controlled_demo_market_fill_verification_path(self) -> None:
        result = verify_capture(self._demo_capture("market"))
        self.assertTrue(result["verified"])
        self.assertEqual(result["position_identifier"], 9001)

    def test_controlled_demo_pending_fill_verification_path(self) -> None:
        result = verify_capture(self._demo_capture("pending"))
        self.assertTrue(result["verified"])
        self.assertEqual(result["position_identifier"], 9001)

    def test_synthetic_capture_is_not_accepted_as_operational_proof(self) -> None:
        payload = self._demo_capture("market")
        payload["capture_origin"] = "SYNTHETIC_FIXTURE"
        result = verify_capture(payload)
        self.assertFalse(result["verified"])
        self.assertFalse(result["operational_proof"])

    def test_each_candidate_has_independent_assessment(self) -> None:
        candidates = [candidate(0, "A"), candidate(1, "B")]
        assessments = [assessment(candidates[0], quality=8.2), assessment(candidates[1], quality=5.1, state="REJECT")]
        result = validate_decision_envelope(envelope(candidates, assessments), candidates)
        self.assertTrue(result.valid, (result.missing_fields, result.invalid_fields))
        self.assertNotEqual(assessments[0]["llm_quality_score"], assessments[1]["llm_quality_score"])

    def test_candidate_a_assessment_cannot_bind_candidate_b(self) -> None:
        candidates = [candidate(0, "A"), candidate(1, "B")]
        assessments = [assessment(candidates[0]), assessment(candidates[1])]
        assessments[1]["candidate_hash"] = candidates[0]["candidate_hash"]
        result = validate_decision_envelope(envelope(candidates, assessments), candidates)
        self.assertFalse(result.valid)
        self.assertIn("candidate_hash_duplicate_or_missing", result.invalid_fields)

    def test_invalid_selection_does_not_substitute_rule_best(self) -> None:
        cand = candidate()
        item = assessment(cand)
        response = envelope([cand], [item])
        response["selected_candidate_hash"] = "UNKNOWN-HASH"
        self.assertFalse(validate_decision_envelope(response, [cand]).valid)
        source = (MQL_INCLUDE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertNotIn("_RuleFallbackAllowed", source)
        self.assertNotIn("best.ai = p.ai", source)
        self.assertNotIn("ai_reused=", source)
        self.assertIn("alternative_candidate_requires_independent_ai_assessment", source)
        self.assertIn("refreshed_candidate_identity_changed && has_bound_ai_assessment", source)
        self.assertIn("candidate_hash_mismatch", source)

    def test_candidate_hash_mismatch_rejects(self) -> None:
        cand = candidate()
        item = assessment(cand)
        item["candidate_hash"] = "WRONGHASH123"
        result = validate_candidate_assessment(item, cand)
        self.assertFalse(result.valid)
        self.assertIn("candidate_hash_mismatch", result.invalid_fields)

    def test_execution_fingerprint_mismatch_rejects(self) -> None:
        cand = candidate()
        item = assessment(cand)
        item["request_execution_fingerprint"] = "WRONGFP123456"
        result = validate_candidate_assessment(item, cand)
        self.assertFalse(result.valid)
        self.assertIn("request_execution_fingerprint_mismatch", result.invalid_fields)

    def test_small_rounding_change_is_non_material(self) -> None:
        assessed = candidate()
        final = copy.deepcopy(assessed)
        final["entry_est"] += 0.004
        final["sl"] -= 0.004
        final["tp1"] += 0.004
        final["tp2"] += 0.004
        self.assertEqual(material_execution_changes(assessed, final, tick_size=0.01), [])

    def test_material_plan_changes_are_reported(self) -> None:
        assessed = candidate()
        cases = {
            "entry": ("entry_est", 0.06),
            "sl": ("sl", -0.06),
            "tp1": ("tp1", 0.06),
            "tp2": ("tp2", 0.06),
            "net_rr": ("net_rr", 0.06),
            "spread_r": ("spread_r", 0.03),
            "slippage_estimate_r": ("slippage_r", 0.03),
            "execution_cost_r": ("execution_cost_r", 0.03),
        }
        for expected_name, (field, delta) in cases.items():
            with self.subTest(field=field):
                final = copy.deepcopy(assessed)
                final[field] += delta
                self.assertIn(expected_name, material_execution_changes(assessed, final, tick_size=0.01))

    def test_every_mandatory_field_missing_rejects(self) -> None:
        cand = candidate()
        complete = assessment(cand)
        for field in (*MANDATORY_ASSESSMENT_FIELDS, *MANDATORY_NULLABLE_CALIBRATION_FIELDS):
            with self.subTest(field=field):
                item = copy.deepcopy(complete)
                item.pop(field, None)
                result = validate_candidate_assessment(item, cand)
                self.assertFalse(result.valid)

    def test_degraded_fallback_cannot_trade(self) -> None:
        self.assertFalse(response_can_trade(RESPONSE_DEGRADED_NON_TRADING, "APPROVE", 1.0))

    def test_abstain_cannot_trade_market_or_pending(self) -> None:
        self.assertFalse(response_can_trade(RESPONSE_FULL_STRUCTURED, DECISION_ABSTAIN, 0.0))
        cand = candidate()
        item = assessment(cand, state=DECISION_ABSTAIN)
        self.assertTrue(validate_candidate_assessment(item, cand).valid)
        source = (MQL_INCLUDE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("ai_abstain"), 3)

    def test_zero_ai_risk_multiplier_rejects(self) -> None:
        self.assertFalse(response_can_trade(RESPONSE_FULL_STRUCTURED, "APPROVE", 0.0))
        cand = candidate()
        item = assessment(cand)
        item["suggested_risk_multiplier"] = 0.0
        self.assertIn("approve_state_contract", validate_candidate_assessment(item, cand).invalid_fields)

    def test_zero_policy_multiplier_is_preserved(self) -> None:
        self.assertEqual(resolved_risk_multiplier(0.0), 0.0)
        self.assertFalse(response_can_trade(RESPONSE_FULL_STRUCTURED, "APPROVE", resolved_risk_multiplier(0.0)))

    def test_none_is_not_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing_or_invalid"):
            resolved_risk_multiplier(None)

    def test_exact_hedging_identity_succeeds(self) -> None:
        expected, order, deal, positions = broker_fixture()
        result = validate_broker_execution_identity(expected, order, deal, positions, account_mode="hedging")
        self.assertTrue(result.verified)
        self.assertEqual(result.position_ticket, 9101)

    def test_same_symbol_positions_do_not_cross_attach(self) -> None:
        expected, order, deal, positions = broker_fixture()
        positions.insert(0, {**positions[0], "ticket": 9199, "identifier": 9999, "comment": "OTHER"})
        result = validate_broker_execution_identity(expected, order, deal, positions, account_mode="hedging")
        self.assertTrue(result.verified)
        self.assertEqual(result.position_ticket, 9101)

    def test_failed_exact_attribution_is_quarantined(self) -> None:
        expected, order, deal, positions = broker_fixture()
        deal["position_id"] = 9999
        result = validate_broker_execution_identity(expected, order, deal, positions, account_mode="hedging")
        self.assertFalse(result.verified)
        self.assertTrue(result.quarantined)
        self.assertEqual(result.reason, "exact_position_not_found_by_deal_position_id")

    def test_netting_mode_is_explicitly_governed(self) -> None:
        expected, order, deal, positions = broker_fixture()
        result = validate_broker_execution_identity(
            expected,
            order,
            deal,
            positions,
            account_mode="netting",
            existing_managed_same_symbol_positions=1,
            netting_virtual_subposition_ledger=False,
        )
        self.assertEqual(result.reason, "netting_mode_one_managed_trade_per_symbol")

    def test_old_cache_schema_cannot_trade(self) -> None:
        cand = candidate()
        item = assessment(cand)
        response = envelope([cand], [item])
        response["decision_schema_version"] = "legacy-v0"
        result = validate_decision_envelope(response, [cand])
        self.assertFalse(result.valid)
        self.assertIn("decision_schema_version", result.invalid_fields)

    def test_current_cache_rejects_incomplete_candidate_assessment(self) -> None:
        cand = candidate()
        item = assessment(cand)
        comparison = {
            "liquidity_target": {},
            "partial_before_obstacle_then_liquidity": {},
            "capped_before_obstacle": {},
            "synthetic_rr_fallback": {},
        }
        cached = {
            "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
            "decision_quality_tier": RESPONSE_FULL_STRUCTURED,
            "response_quality": RESPONSE_FULL_STRUCTURED,
            "mandatory_fields_complete": True,
            "missing_mandatory_fields": [],
            "invalid_mandatory_fields": [],
            "candidate_assessments": [item],
            "selected_candidate_id": item["candidate_id"],
            "selected_candidate_hash": item["candidate_hash"],
            "request_execution_fingerprint": item["request_execution_fingerprint"],
            "assessed_execution_fingerprint": item["assessed_execution_fingerprint"],
            "selected_target_identity": item["selected_target_identity"],
            "decision_state": item["decision_state"],
            "raw_allow": item["raw_allow"],
            "model_raw_allow": item["raw_allow"],
            "python_final_allow": item["raw_allow"],
            "mql_final_allow": None,
            "rule_score": item["rule_score"],
            "llm_quality_score": item["llm_quality_score"],
            "blended_legacy_score": item["blended_legacy_score"],
            "legacy_agreement_confidence": item["legacy_agreement_confidence"],
            "llm_self_reported_confidence": item["llm_self_reported_confidence"],
            "suggested_risk_multiplier": item["suggested_risk_multiplier"],
            "selected_target_identity": item["selected_target_identity"],
            "selected_target_price": item["selected_target_price"],
            "target_arbitration_schema_version": AI_TARGET_ARBITRATION_SCHEMA_VERSION,
            "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
            "chosen_target_model": "liquidity_target",
            "target_comparison": comparison,
        }
        self.assertEqual(ai_gate._cached_decision_schema_miss_reason(cached), "")
        cached["candidate_assessments"][0].pop("cost_risk")
        self.assertEqual(
            ai_gate._cached_decision_schema_miss_reason(cached),
            "cache_miss_due_to_schema_version",
        )

    def test_full_cached_decision_retains_exact_binding(self) -> None:
        cand = candidate()
        item = assessment(cand)
        self.assertTrue(validate_decision_envelope(envelope([cand], [item]), [cand]).valid)
        self.assertTrue(response_can_trade(RESPONSE_CACHE_FULL_STRUCTURED, "APPROVE", item["suggested_risk_multiplier"]))
        self.assertEqual(item["assessed_execution_fingerprint"], assessed_execution_fingerprint(cand, item))

    def test_legacy_scores_and_self_confidence_have_no_threshold_authority(self) -> None:
        payload = {
            "runtime_inputs": {"min_llm_quality_score_trend": 7.0},
            "candidates": [{"candidate_index": 0, "setup_family": "full_po3"}],
        }
        decision = ai_gate.Decision(
            allow=True,
            score=8.0,
            llm_quality_score=8.0,
            blended_legacy_score=0.0,
            legacy_agreement_confidence=0.0,
            llm_self_reported_confidence=0.0,
        )
        self.assertTrue(ai_gate._apply_family_ai_threshold_gate(payload, decision, 0).allow)
        decision.llm_quality_score = 6.0
        decision.blended_legacy_score = 10.0
        decision.legacy_agreement_confidence = 1.0
        decision.llm_self_reported_confidence = 1.0
        self.assertFalse(ai_gate._apply_family_ai_threshold_gate(payload, decision, 0).allow)

    def test_unavailable_calibration_cannot_be_fabricated(self) -> None:
        cand = candidate()
        item = assessment(cand)
        item["calibrated_win_probability"] = 0.8
        result = validate_candidate_assessment(item, cand)
        self.assertFalse(result.valid)
        self.assertIn("calibrated_win_probability", result.invalid_fields)

    def test_workflow_flags_do_not_change_decision_cache_signature(self) -> None:
        cand = candidate()
        payload = {
            "symbol": "GOLD",
            "is_buy": True,
            "candidates": [cand],
            "runtime_inputs": {
                "tester_ai_cache": False,
                "tester_ai_mode": "record_only",
                "tester_allow_live_wait_debug_trading": False,
                "fallback_rr2": 1.7,
            },
            "runtime_input_hash": "record-runtime-hash",
        }
        first = ai_gate._decision_cache_signature(payload, 0)[0]
        replay = copy.deepcopy(payload)
        replay["runtime_inputs"].update(
            tester_ai_cache=True,
            tester_ai_mode="cache_only",
            tester_allow_live_wait_debug_trading=True,
        )
        replay["runtime_input_hash"] = "cache-runtime-hash"
        second = ai_gate._decision_cache_signature(replay, 0)[0]
        self.assertEqual(first, second)
        replay["runtime_inputs"]["fallback_rr2"] = 1.8
        self.assertNotEqual(first, ai_gate._decision_cache_signature(replay, 0)[0])

    def test_prompt_contract_version_changes_cache_signature(self) -> None:
        payload = {"symbol": "GOLD", "is_buy": True, "candidates": [candidate()], "runtime_inputs": {"fallback_rr2": 1.7}}
        first = ai_gate._decision_cache_signature(payload, 0)[0]
        with patch.object(ai_gate, "AI_PROMPT_CONTRACT_VERSION", "future-contract"):
            second = ai_gate._decision_cache_signature(payload, 0)[0]
        self.assertNotEqual(first, second)

    def test_execution_fingerprint_is_deterministic(self) -> None:
        plan = candidate()
        self.assertEqual(execution_fingerprint(plan), execution_fingerprint(copy.deepcopy(plan)))


if __name__ == "__main__":
    unittest.main()
