from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
import importlib.util

from architecture_contracts import (
    COHORT_FIELDS,
    FILE_BUS_TERMINAL_STATES,
    LIVE_FORWARD,
    MANAGEMENT_FEATURE_NAMES,
    PRE_ENTRY_FEATURE_NAMES,
    DecisionAuthority,
    FileBusLifecycle,
    OutcomeTargetConfig,
    PolicySpec,
    atomic_write_json,
    build_entry_and_management_shadow_artifacts,
    build_hierarchical_outcome_artifact,
    build_startup_policy_manifest,
    cohort_metadata,
    compare_shadow_decision_groups,
    complete_shadow_candidate,
    consolidate_shadow_event_rows,
    live_forward_behavior_contract,
    merge_completed_and_shadow_records,
    require_homogeneous_cohort,
    resolve_mql_decision_authority,
    resolve_multiplier,
    resolve_multiplier_chain,
    resolve_python_decision_authority,
    response_binding_hash,
    semantic_cache_invalidation_reasons,
    semantic_cache_state,
    strict_json_load,
    strict_json_loads,
    validate_authority_assignment,
    validate_response_binding,
    workload_mode,
)
from runtime_governance import FVG_MODE_ENFORCE, FVG_MODE_SHADOW, normalized_fvg_minimum


def _cohort(seed: str = "a") -> dict:
    return {field: f"{field}-{seed}" for field in COHORT_FIELDS}


def _clean_row(index: int, *, symbol: str, asset_class: str, target: bool) -> dict:
    row = {
        **_cohort("one"),
        "trade_key": f"trade-{index}",
        "symbol": symbol,
        "asset_class": asset_class,
        "ledger_integrity_status": "CLEAN",
        "attribution_status": "EXACT_VERIFIED",
        "execution_identity_quarantined": False,
        "pre_entry_features_complete": True,
        "target_before_stop": target,
        "unmanaged_original_plan_target_before_stop": target,
        "unmanaged_mfe_before_mae": target,
        "unmanaged_reached_0_25r_before_adverse_threshold": target,
        "unmanaged_reached_0_50r_before_adverse_threshold": target,
        "unmanaged_original_plan_net_r": 1.2 if target else -1.0,
        "unmanaged_mfe_r": 1.5 if target else 0.15,
        "unmanaged_mae_r": 0.2 if target else 1.0,
        "unmanaged_reached_0_25r": target,
        "unmanaged_reached_0_50r": target,
        "unmanaged_adverse_threshold_reached": not target,
        "unmanaged_observed_path_minutes": 30.0,
        "unmanaged_time_to_0_25r_or_censor_minutes": 5.0 if target else 30.0,
        "unmanaged_time_to_0_50r_or_censor_minutes": 10.0 if target else 30.0,
        "unmanaged_time_to_invalidation_or_censor_minutes": 30.0 if target else 8.0,
        "opened_at": 1_700_000_000 + index * 60,
        "management_alpha": 0.10 if index % 2 else -0.05,
        "management_features_time_safe": True,
    }
    for feature_index, name in enumerate(PRE_ENTRY_FEATURE_NAMES):
        row[name] = 0.1 + ((index + feature_index) % 17) / 20.0
    for feature_index, name in enumerate(MANAGEMENT_FEATURE_NAMES):
        row[name] = 0.05 + ((index + feature_index) % 13) / 15.0
    return row


class ArchitectureContractsTests(unittest.TestCase):
    def test_deployment_manifest_requires_explicit_git_and_set_identity(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "tools" / "generate_deployment_manifest.py"
        spec = importlib.util.spec_from_file_location("generate_deployment_manifest", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            set_file = root / "run.set"
            set_file.write_text("InpUseAI=true\n", encoding="utf-8")

            def fake_git(source: Path, args: list[str]) -> str:
                if args == ["rev-parse", "--show-toplevel"]:
                    return str(source)
                if args == ["rev-parse", "HEAD"]:
                    return "a" * 40
                if args[0] == "status":
                    return " M python/ai_gate.py"
                raise AssertionError(args)

            manifest = module.build_manifest(root, set_file, git_runner=fake_git)
            self.assertEqual(manifest["git_commit"], "a" * 40)
            self.assertEqual(manifest["dirty_tree_status"], "DIRTY")
            self.assertEqual(len(str(manifest["set_file_hash"])), 64)
            self.assertNotEqual(manifest["set_file_hash"], "UNAVAILABLE")

            with self.assertRaises(FileNotFoundError):
                module.build_manifest(root, root / "missing.set", git_runner=fake_git)
    def test_normalized_fvg_point_40_shadow_and_enforce(self) -> None:
        shadow = normalized_fvg_minimum(
            raw_width_price=0.8,
            point=0.1,
            minimum_ticks=2.0,
            spread_price=0.3,
            spread_multiple=2.0,
            atr_price=10.0,
            atr_fraction=0.05,
            session_noise_price=4.0,
            session_noise_fraction=0.2,
            mode="INVALID_DEFAULTS_TO_SHADOW",
            asset_class="metals",
            policy_version="v1",
            sample_size=0,
            minimum_asset_class_samples=50,
        )
        self.assertEqual(shadow["mode"], FVG_MODE_SHADOW)
        self.assertAlmostEqual(shadow["normalized_minimum_price"], 0.8)
        self.assertTrue(shadow["enforced_pass"])
        self.assertFalse(shadow["per_symbol_tuning"])
        enforce = normalized_fvg_minimum(
            raw_width_price=1.0,
            point=0.1,
            minimum_ticks=2.0,
            spread_price=0.3,
            spread_multiple=2.0,
            atr_price=10.0,
            atr_fraction=0.05,
            session_noise_price=4.0,
            session_noise_fraction=0.2,
            mode=FVG_MODE_ENFORCE,
            asset_class="metals",
            policy_version="v1",
            sample_size=10,
            minimum_asset_class_samples=50,
        )
        self.assertFalse(enforce["enforced_pass"])
        self.assertEqual(enforce["authority_reason"], "insufficient_asset_class_samples")

    def test_live_forward_unifies_demo_contest_and_real(self) -> None:
        modes = {
            workload_mode({"runtime": {"account_trade_mode": mode}})
            for mode in ("demo", "contest", "real")
        }
        self.assertEqual(modes, {LIVE_FORWARD})
        contract = live_forward_behavior_contract()
        self.assertEqual(contract["workload_mode"], LIVE_FORWARD)
        self.assertTrue(contract["behavior_contract_hash"])

    def test_multiplier_semantics(self) -> None:
        optional = resolve_multiplier(None, present=False, optional=True, source="optional")
        mandatory = resolve_multiplier(None, present=False, optional=False, source="mandatory")
        zero = resolve_multiplier(0.0, present=True, optional=False, source="policy")
        reduced = resolve_multiplier(0.25, present=True, optional=False, source="policy")
        negative = resolve_multiplier(-0.1, present=True, optional=False, source="policy")
        high = resolve_multiplier(1.1, present=True, optional=False, source="policy")
        self.assertEqual(optional.resolved_value, 1.0)
        self.assertFalse(mandatory.valid)
        self.assertTrue(zero.valid and zero.blocked)
        self.assertEqual(reduced.resolved_value, 0.25)
        self.assertFalse(negative.valid)
        self.assertFalse(high.valid)
        self.assertTrue(resolve_multiplier_chain([reduced, zero]).blocked)

    def test_every_semantic_cache_invalidation_reason(self) -> None:
        base = {
            "entry_bar_id": "100",
            "entry": 100.0,
            "sl": 99.0,
            "tp2": 102.0,
            "spread_r_bucket": "low",
            "structure_state": "confirmed",
            "fvg_mitigation_state": "fresh",
            "session": "LON",
            "killzone": "K",
            "bucket_prior_hash": "prior-a",
            "candidate_hash": "candidate-a",
            "execution_fingerprint": "fingerprint-a",
            "policy_version": "policy-a",
            "model_version": "model-a",
            "prompt_contract_version": "prompt-a",
            "decision_schema_version": "decision-a",
            "target_schema_version": "target-a",
        }
        mutations = {
            "cache_stale_new_entry_bar": {"entry_bar_id": "101"},
            "cache_stale_entry_drift": {"entry": 100.2},
            "cache_stale_stop_drift": {"sl": 98.8},
            "cache_stale_target_drift": {"tp2": 102.2},
            "cache_stale_spread_bucket": {"spread_r_bucket": "high"},
            "cache_stale_structure": {"structure_state": "invalid"},
            "cache_stale_fvg_state": {"fvg_mitigation_state": "mitigated"},
            "cache_stale_session": {"session": "NY"},
            "cache_stale_killzone": {"killzone": "NK"},
            "cache_stale_prior_version": {"bucket_prior_hash": "prior-b"},
            "cache_stale_contract_version": {"decision_schema_version": "decision-b"},
        }
        cached = semantic_cache_state(base)
        for expected, mutation in mutations.items():
            current = semantic_cache_state({**base, **mutation})
            self.assertIn(expected, semantic_cache_invalidation_reasons(cached, current), expected)

    def test_ttl_cannot_override_semantic_staleness(self) -> None:
        cached = semantic_cache_state({"entry_bar_id": "1", "entry": 10, "sl": 9, "tp2": 12})
        current = semantic_cache_state({"entry_bar_id": "2", "entry": 10, "sl": 9, "tp2": 12})
        self.assertEqual(semantic_cache_invalidation_reasons(cached, current), ["cache_stale_new_entry_bar"])

    def test_policy_manifest_blocks_active_authority_on_dirty_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            payload = {
                "schema_version": "policy-v1",
                "runtime_input_hash": "runtime-a",
                "decision_schema_version": "decision-v1",
                "taxonomy_version": "taxonomy-v1",
                "generated_at": "2099-01-01T00:00:00Z",
                "rows": [{"key": "x"}],
            }
            atomic_write_json(path, payload)
            manifest = build_startup_policy_manifest(
                [PolicySpec("active", "id", path, True, "active", "policy-v1", "runtime-a", "decision-v1", "taxonomy-v1")],
                ledger_integrity_status="QUARANTINED",
                runtime_input_hash="runtime-a",
            )
            row = manifest["policies"][0]
            self.assertEqual(row["authority"], "blocked")
            self.assertIn("ledger_not_clean", row["rejection_reasons"])

    def test_three_decision_stages_remain_distinct(self) -> None:
        multiplier = resolve_multiplier(1.0, present=True, optional=False, source="ai")
        python = resolve_python_decision_authority(
            model_raw_allow=True,
            decision_state="APPROVE",
            schema_valid=True,
            veto_passed=True,
            repeatability_passed=True,
            prior_passed=True,
            statistical_policy_passed=True,
            multiplier=multiplier,
        )
        self.assertTrue(python.model_raw_allow)
        self.assertTrue(python.python_final_allow)
        self.assertIsNone(python.mql_final_allow)
        mql = resolve_mql_decision_authority(
            python,
            candidate_hash_match=True,
            execution_fingerprint_match=False,
            freshness_passed=True,
            risk_passed=True,
            broker_passed=True,
            session_passed=True,
            policy_passed=True,
        )
        self.assertTrue(mql.model_raw_allow and mql.python_final_allow)
        self.assertFalse(mql.mql_final_allow)
        self.assertIn("execution_fingerprint_mismatch", mql.mql_reasons)

    def test_strict_json_rejects_duplicate_nonfinite_and_bad_unicode(self) -> None:
        with self.assertRaisesRegex(Exception, "duplicate_json_key"):
            strict_json_loads('{"allow":true,"allow":false}')
        with self.assertRaises(Exception):
            strict_json_loads('{"score":NaN}')
        with self.assertRaises(Exception):
            strict_json_loads('{"value":"\\ud800"}')

    def test_response_hash_binding(self) -> None:
        response = {
            "id": "req-1",
            "session_id": "session-1",
            "request_nonce": "nonce-1",
            "workload_mode": LIVE_FORWARD,
            "decision_schema_version": "decision-v1",
            "selected_candidate_hash": "candidate-a",
            "assessed_execution_fingerprint": "fingerprint-a",
            "model_raw_allow": True,
            "python_final_allow": True,
            "mql_final_allow": None,
            "decision_state": "APPROVE",
            "decision_quality_tier": "FULL_STRUCTURED",
            "selected_candidate_id": "candidate-id-a",
            "selected_target_identity": "liquidity_target",
            "selected_target_price": 102.0,
            "llm_quality_score": 8.0,
            "suggested_risk_multiplier": 0.5,
        }
        response["response_binding_hash"] = response_binding_hash(response)
        valid, reasons = validate_response_binding(response, request_id="req-1", session_id="session-1", request_nonce="nonce-1")
        self.assertTrue(valid, reasons)
        response["python_final_allow"] = False
        valid, reasons = validate_response_binding(response, request_id="req-1", session_id="session-1", request_nonce="nonce-1")
        self.assertFalse(valid)
        self.assertIn("response_hash_mismatch", reasons)

    def test_file_bus_atomic_lifecycle_and_terminal_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bus = FileBusLifecycle(Path(temp), "session-a")
            request = bus.submit_request("req", {"id": "req", "nonce": "n"})
            claimed = bus.claim(request)
            archived = bus.archive(claimed, "completed", reason="success")
            self.assertTrue(archived.is_file())
            self.assertFalse(request.exists())
            for state in FILE_BUS_TERMINAL_STATES - {"completed"}:
                artifact = Path(temp) / f"{state}.json"
                atomic_write_json(artifact, {"state": state})
                self.assertTrue(bus.archive(artifact, state, reason="test").is_file())
            with self.assertRaises(FileExistsError):
                bus.submit_request("duplicate", {"id": 1})
                bus.submit_request("duplicate", {"id": 2})

    def test_homogeneous_cohort_passes_and_mixed_blocks(self) -> None:
        first = _cohort("a")
        second = _cohort("a")
        self.assertTrue(require_homogeneous_cohort([first, second]))
        mixed = dict(second)
        mixed["prompt_contract_version"] = "different"
        with self.assertRaisesRegex(ValueError, "mixed_version_cohort"):
            require_homogeneous_cohort([first, mixed])

    def test_llm_cannot_own_calibrated_probability_or_expected_r(self) -> None:
        self.assertFalse(validate_authority_assignment("target_before_stop_probability", "llm"))
        self.assertFalse(validate_authority_assignment("expected_net_r", "llm"))
        self.assertTrue(validate_authority_assignment("anomaly_veto", "llm"))

    def test_hierarchical_global_asset_symbol_shrinkage(self) -> None:
        records = []
        for index in range(180):
            if index % 9 == 0:
                symbol, asset, target = "USDJPY", "fx", index % 2 == 0
            elif index % 2 == 0:
                symbol, asset, target = "GOLD", "metals", index % 5 != 0
            else:
                symbol, asset, target = "EURUSD", "fx", index % 3 == 0
            records.append(_clean_row(index, symbol=symbol, asset_class=asset, target=target))
        artifact = build_hierarchical_outcome_artifact(
            records,
            min_clean_sample=50,
            min_asset_sample=8,
            min_symbol_sample=35,
            prior_strength=20.0,
        )
        self.assertIsNotNone(artifact["global_model"])
        self.assertIn("metals", artifact["asset_class_adjustments"])
        self.assertIn("fx", artifact["asset_class_calibration"])
        self.assertGreater(artifact["symbol_adjustments"]["GOLD"]["shrinkage_weight"], 0.0)
        self.assertFalse(artifact["symbol_adjustments"]["USDJPY"]["eligible"])
        self.assertFalse(artifact["trading_authority"])

    def test_hierarchical_model_refuses_unclean_rows(self) -> None:
        rows = [_clean_row(i, symbol="EURUSD", asset_class="fx", target=True) for i in range(100)]
        for row in rows:
            row["ledger_integrity_status"] = "QUARANTINED"
        artifact = build_hierarchical_outcome_artifact(rows, min_clean_sample=10)
        self.assertEqual(artifact["status"], "shadow_unavailable")
        self.assertEqual(artifact["clean_record_count"], 0)

    def test_correct_targets_preserve_same_bar_ambiguity(self) -> None:
        candidate = {
            **_cohort("shadow"),
            "candidate_timestamp": 1000,
            "entry": 100.0,
            "sl": 99.0,
            "tp2": 102.0,
            "direction": "BUY",
            "candidate_hash": "candidate",
            "execution_fingerprint": "fingerprint",
            "decision_stage": "ai",
            "decision_state": "REJECT",
            "model_raw_allow": False,
            "python_final_allow": False,
            "mql_final_allow": False,
        }
        outcome = complete_shadow_candidate(
            candidate,
            future_bars=[{"time": 1000, "high": 102.1, "low": 98.9}],
            config=OutcomeTargetConfig(horizon_minutes=60, adverse_threshold_r=0.5),
        )
        self.assertIsNone(outcome["target_before_stop"])
        self.assertIn("AMBIGUOUS", outcome["horizon_result"])
        self.assertFalse(outcome["trading_authority"])

    def test_rejected_candidates_receive_complete_shadow_outcomes(self) -> None:
        candidate = {
            **_cohort("shadow"),
            "candidate_timestamp": 1000,
            "entry": 100.0,
            "sl": 99.0,
            "tp2": 102.0,
            "direction": "BUY",
            "candidate_hash": "candidate",
            "execution_fingerprint": "fingerprint",
            "decision_stage": "pre_ai_hard_gate",
            "decision_state": "REJECT",
            "rejection_reason": "policy_block",
            "model_raw_allow": False,
            "python_final_allow": False,
            "mql_final_allow": False,
            "execution_cost_r": 0.1,
        }
        outcome = complete_shadow_candidate(
            candidate,
            future_bars=[{"time": 1060, "high": 102.1, "low": 99.5}],
        )
        self.assertTrue(outcome["target_before_stop"])
        self.assertEqual(outcome["decision_stage"], "pre_ai_hard_gate")
        self.assertTrue(outcome["shadow_only"])

    def test_entry_and_management_models_are_independent_shadow_artifacts(self) -> None:
        rows = [_clean_row(i, symbol="EURUSD", asset_class="fx", target=i % 2 == 0) for i in range(120)]
        artifacts = build_entry_and_management_shadow_artifacts(rows, min_clean_sample=30)
        self.assertEqual(artifacts["entry_model"]["target"], "unmanaged_original_plan_target_before_stop")
        self.assertEqual(artifacts["management_model"]["target"], "management_alpha_incremental_remaining_outcome")
        self.assertFalse(artifacts["entry_model"]["artifact"]["trading_authority"])
        self.assertFalse(artifacts["management_model"]["trading_authority"])

    def test_entry_target_suite_uses_right_censoring_and_stays_shadow_only(self) -> None:
        rows = [_clean_row(i, symbol="EURUSD", asset_class="fx", target=i % 3 != 0) for i in range(140)]
        artifacts = build_entry_and_management_shadow_artifacts(rows, min_clean_sample=30)
        entry = artifacts["entry_model"]
        self.assertEqual(
            set(entry["continuous_target_models"]),
            {"unmanaged_original_plan_net_r", "unmanaged_mfe_r", "unmanaged_mae_r"},
        )
        survival = entry["time_to_event_models"]["time_to_0_25r"]
        self.assertEqual(survival["censoring_method"], "kaplan_meier_right_censoring")
        self.assertGreater(survival["global"]["censored_count"], 0)
        self.assertFalse(survival["trading_authority"])

    def test_mixed_cohorts_block_model_build_instead_of_mixing(self) -> None:
        rows = [_clean_row(i, symbol="EURUSD", asset_class="fx", target=i % 2 == 0) for i in range(100)]
        for row in rows[50:]:
            row["prompt_contract_version"] = "incompatible-version"
        artifact = build_hierarchical_outcome_artifact(rows, min_clean_sample=20)
        self.assertEqual(artifact["status"], "shadow_blocked")
        self.assertIn("mixed_version_cohort_analysis_blocked", artifact["activation_block_reasons"])

    def test_shadow_event_join_uses_parent_candidate_and_execution_fingerprint(self) -> None:
        observed = {
            **_clean_row(1, symbol="EURUSD", asset_class="fx", target=True),
            "event_type": "candidate_observed",
            "record_hash": "parent-a",
            "candidate_hash": "candidate-a",
            "execution_fingerprint": "request-fp-a",
            "candidate_timestamp": 1000,
            "entry": 100.0,
            "sl": 99.0,
            "tp2": 102.0,
            "direction": "BUY",
        }
        exact_decision = {
            "event_type": "candidate_decision_update",
            "candidate_hash": "candidate-a",
            "execution_fingerprint": "request-fp-a",
            "mql_final_allow": False,
            "decision_state": "REJECT",
        }
        wrong_decision = {
            "event_type": "candidate_decision_update",
            "candidate_hash": "candidate-a",
            "execution_fingerprint": "request-fp-b",
            "mql_final_allow": True,
            "decision_state": "APPROVE",
        }
        resolution = {
            "event_type": "hypothetical_outcome_resolution",
            "parent_record_hash": "parent-a",
            "candidate_hash": "candidate-a",
            "execution_fingerprint": "request-fp-a",
            "target_before_stop": True,
            "result_r": 1.2,
            "mfe_r": 1.4,
            "mae_r": 0.2,
            "time_to_event_sec": 600,
            "time_to_0_25r_sec": 120,
            "time_to_0_50r_sec": 240,
            "time_to_adverse_threshold_sec": None,
            "reached_0_25r": True,
            "reached_0_50r": True,
            "reached_0_25r_before_adverse_threshold": True,
            "reached_0_50r_before_adverse_threshold": True,
        }
        outcomes, rejected = consolidate_shadow_event_rows(
            [observed, exact_decision, wrong_decision, resolution]
        )
        self.assertFalse(rejected)
        self.assertEqual(len(outcomes), 1)
        self.assertFalse(outcomes[0]["mql_final_allow"])
        mismatch = dict(resolution)
        mismatch["execution_fingerprint"] = "request-fp-b"
        outcomes, rejected = consolidate_shadow_event_rows([observed, mismatch])
        self.assertFalse(outcomes)
        self.assertEqual(rejected[0]["reason"], "shadow_outcome_execution_fingerprint_mismatch")

    def test_completed_trade_shadow_join_does_not_cross_attach_same_candidate(self) -> None:
        shadow = [
            {"candidate_hash": "same", "final_execution_fingerprint": "fp-a", "unmanaged_original_plan_net_r": 1.0},
            {"candidate_hash": "same", "final_execution_fingerprint": "fp-b", "unmanaged_original_plan_net_r": -1.0},
        ]
        completed = [
            {"trade_key": "a", "candidate_hash": "same", "final_execution_fingerprint": "fp-a"},
            {"trade_key": "b", "candidate_hash": "same", "final_execution_fingerprint": "fp-b"},
        ]
        merged, rejected = merge_completed_and_shadow_records(completed, shadow)
        self.assertFalse(rejected)
        by_key = {row["trade_key"]: row for row in merged}
        self.assertEqual(by_key["a"]["unmanaged_original_plan_net_r"], 1.0)
        self.assertEqual(by_key["b"]["unmanaged_original_plan_net_r"], -1.0)

    def test_approved_vs_rejected_shadow_analysis(self) -> None:
        summary = compare_shadow_decision_groups(
            [
                {"mql_final_allow": True, "target_before_stop": True, "decision_state": "APPROVE"},
                {"mql_final_allow": True, "target_before_stop": False, "decision_state": "APPROVE"},
                {"mql_final_allow": False, "target_before_stop": True, "decision_state": "REJECT"},
                {"mql_final_allow": False, "target_before_stop": False, "decision_state": "ABSTAIN"},
            ]
        )
        self.assertEqual(summary["approved"]["count"], 2)
        self.assertEqual(summary["rejected"]["count"], 1)
        self.assertEqual(summary["abstained"]["count"], 1)
        self.assertIsNotNone(summary["approved_minus_rejected_target_before_stop_rate"])
        self.assertFalse(summary["calibration_monotonicity"]["available"])


if __name__ == "__main__":
    unittest.main()
