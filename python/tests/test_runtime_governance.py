from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from runtime_governance import (
    COMMISSION_MODEL_SCHEMA_VERSION,
    DECISION_NON_REPEATABLE,
    FVG_MODE_ENFORCE,
    FVG_MODE_OFF,
    FVG_MODE_SHADOW,
    HIERARCHICAL_PRIOR_SCHEMA_VERSION,
    MANAGEMENT_HEALTHY,
    MANAGEMENT_THESIS_INVALID,
    REPEATABLE,
    RISK_FACTOR_SCHEMA_VERSION,
    SCORE_NON_REPEATABLE,
    BrokerCostSample,
    FlattenRetryState,
    InvalidationConfirmationState,
    ManagementState,
    RepeatabilityThresholds,
    RiskExposure,
    SessionWindow,
    aggregate_initial_risk_gate,
    append_shadow_candidate,
    audit_prior_artifact,
    build_hierarchical_prior_artifact,
    canonical_hash,
    cost_prediction_error,
    effective_aggregate_risk_cap,
    estimate_broker_cost,
    evaluate_price_path_contract,
    evaluate_repeatability,
    factor_contributions,
    flatten_retry_allowed,
    management_alpha,
    management_policy_authority,
    normalize_closing_volume,
    normalize_opening_volume,
    normalized_fvg_minimum,
    priors_for_candidate,
    repeatability_authority,
    request_fingerprint,
    resolve_invalidation_policy,
    resolve_project_path,
    response_fingerprint,
    risk_factor_gate,
    session_action,
    transition_management_state,
    confirm_invalidation,
)


def clean_row(index: int, *, family: str = "micro", symbol: str = "EURUSD", result_r: float = 0.5) -> dict:
    return {
        "trade_key": f"t{index}",
        "symbol": symbol,
        "asset_class": "fx",
        "setup_family": family,
        "entry_branch": "breaker_retest",
        "session": "LON",
        "killzone": "K",
        "result_r_initial_risk": result_r,
        "closed_at": f"2026-07-{(index % 20) + 1:02d}T12:00:00Z",
        "engine_version": "engine-v1",
        "input_snapshot_hash": "inputs-v1",
        "prompt_contract_version": "prompt-v1",
        "decision_schema_version": "decision-v1",
        "ledger_schema_version": "ledger-v1",
        "ledger_integrity_status": "clean",
        "attribution_status": "exact_verified",
        "execution_identity_quarantined": False,
        "learning_eligible": True,
        "optimization_eligible": True,
        "suppression_eligible": True,
    }


def response(*, score: float, decision: str = "APPROVE", chosen: str = "a", veto: bool = False, target: str = "liquidity_target") -> dict:
    return {
        "id": f"r-{score}-{decision}-{chosen}",
        "model_version": "gpt-test",
        "decision_quality_tier": "FULL_STRUCTURED",
        "decision_state": decision,
        "selected_candidate_hash": chosen,
        "chosen_index": 0,
        "veto_enabled": veto,
        "veto_reason": "risk" if veto else "",
        "llm_quality_score": score,
        "suggested_risk_multiplier": 0.5,
        "chosen_target_model": target,
        "candidate_assessments": [{"candidate_hash": chosen, "score": score}],
    }


class FingerprintAndRepeatabilityTests(unittest.TestCase):
    def test_request_and_response_fingerprints_are_complete_and_stable(self) -> None:
        payload = {
            "id": "req-1",
            "session_id": "session-1",
            "runtime_input_hash": "runtime-hash",
            "candidates": [
                {
                    "candidate_index": 0,
                    "candidate_id": "cand-0",
                    "candidate_hash": "hash-0",
                    "request_execution_fingerprint": "exec-0",
                }
            ],
        }
        first = request_fingerprint(
            payload,
            model="gpt-test",
            reasoning_effort="high",
            decision_quality_tier="FULL_STRUCTURED_REQUIRED",
            prompt_contract_version="prompt-v1",
            decision_schema_version="decision-v1",
            target_schema_version="target-v1",
            prior_artifact_hash="prior-hash",
            prior_artifact_version="prior-v1",
        )
        second = request_fingerprint(
            payload,
            model="gpt-test",
            reasoning_effort="high",
            decision_quality_tier="FULL_STRUCTURED_REQUIRED",
            prompt_contract_version="prompt-v1",
            decision_schema_version="decision-v1",
            target_schema_version="target-v1",
            prior_artifact_hash="prior-hash",
            prior_artifact_version="prior-v1",
        )
        self.assertEqual(first["request_fingerprint"], second["request_fingerprint"])
        self.assertEqual(first["candidate_order"], [0])
        self.assertEqual(first["prior_artifact_hash"], "prior-hash")
        result = response_fingerprint(response(score=8.0))
        self.assertEqual(len(result["response_fingerprint"]), 64)
        self.assertEqual(len(result["full_structured_response_hash"]), 64)

    def test_repeatability_statuses_and_exact_contract_partition(self) -> None:
        thresholds = RepeatabilityThresholds(
            minimum_evaluated_candidates=3,
            maximum_score_stddev=0.2,
            minimum_decision_agreement=0.8,
            minimum_chosen_candidate_agreement=0.8,
            minimum_veto_agreement=0.8,
            minimum_target_choice_agreement=0.8,
        )
        stable = evaluate_repeatability(
            [response(score=8.0), response(score=8.1), response(score=8.0)],
            model="gpt-test",
            prompt_contract_version="prompt-a",
            decision_schema_version="decision-a",
            target_schema_version="target-a",
            decision_quality_tier="FULL_STRUCTURED",
            thresholds=thresholds,
        )
        self.assertEqual(stable["status"], REPEATABLE)

        score_bad = evaluate_repeatability(
            [response(score=5.0), response(score=8.0), response(score=10.0)],
            model="gpt-test",
            prompt_contract_version="prompt-a",
            decision_schema_version="decision-a",
            target_schema_version="target-a",
            decision_quality_tier="FULL_STRUCTURED",
            thresholds=thresholds,
        )
        self.assertEqual(score_bad["status"], SCORE_NON_REPEATABLE)
        self.assertFalse(repeatability_authority(score_bad)["score_threshold_authority"])
        self.assertFalse(repeatability_authority(score_bad)["trading_eligible"])

        decision_bad = evaluate_repeatability(
            [response(score=8.0, decision="APPROVE"), response(score=8.0, decision="REJECT"), response(score=8.0, decision="ABSTAIN")],
            model="gpt-test",
            prompt_contract_version="prompt-a",
            decision_schema_version="decision-a",
            target_schema_version="target-a",
            decision_quality_tier="FULL_STRUCTURED",
            thresholds=thresholds,
        )
        self.assertEqual(decision_bad["status"], DECISION_NON_REPEATABLE)
        self.assertFalse(repeatability_authority(decision_bad)["trading_eligible"])
        other_prompt = dict(decision_bad)
        other_prompt["prompt_contract_version"] = "prompt-b"
        self.assertNotEqual(canonical_hash(decision_bad), canonical_hash(other_prompt))


class HierarchicalPriorTests(unittest.TestCase):
    def test_all_hierarchy_levels_are_separate_and_dirty_rows_are_excluded(self) -> None:
        rows = [clean_row(i, result_r=0.4 if i % 2 == 0 else -0.1) for i in range(40)]
        dirty = clean_row(100, result_r=100.0)
        dirty["ledger_integrity_status"] = "quarantined"
        rows.append(dirty)
        artifact = build_hierarchical_prior_artifact(rows)
        self.assertEqual(artifact["schema_version"], HIERARCHICAL_PRIOR_SCHEMA_VERSION)
        self.assertEqual(artifact["clean_record_count"], 40)
        self.assertEqual(artifact["rejected_record_count"], 1)
        self.assertEqual(
            set(artifact["levels"]),
            {"global", "asset_class", "symbol", "family", "branch", "session", "killzone", "family_symbol", "family_session"},
        )
        candidate = clean_row(1)
        transmitted = priors_for_candidate(artifact, candidate)
        self.assertEqual(set(transmitted["hierarchy"]), set(artifact["levels"]))
        self.assertEqual(transmitted["artifact_hash"], artifact["artifact_hash"])

    def test_four_trade_bucket_cannot_dominate_supported_parent(self) -> None:
        broad = [clean_row(i, family="broad", result_r=0.4) for i in range(400)]
        narrow = [clean_row(1000 + i, family="tiny", result_r=-4.0) for i in range(4)]
        artifact = build_hierarchical_prior_artifact(broad + narrow, prior_strength=20.0)
        tiny = artifact["levels"]["family"]["tiny"]
        self.assertLess(tiny["shrinkage_weight"], 0.2)
        self.assertGreater(tiny["shrunk_estimate"], -1.0)
        self.assertEqual(tiny["raw_prior"]["mean_net_r"], -4.0)

    def test_prior_hash_changes_with_clean_evidence(self) -> None:
        first = build_hierarchical_prior_artifact([clean_row(i) for i in range(20)])
        second_rows = [clean_row(i) for i in range(20)]
        second_rows[-1]["result_r_initial_risk"] = -2.0
        second = build_hierarchical_prior_artifact(second_rows)
        self.assertNotEqual(first["artifact_hash"], second["artifact_hash"])

    def test_prior_path_is_module_relative_and_mandatory_audit_fails_closed(self) -> None:
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                self.assertTrue(str(resolve_project_path("data/priors.json")).startswith(str(Path(__file__).resolve().parents[1])))
            finally:
                os.chdir(original)
        missing = audit_prior_artifact(Path("definitely_missing_prior.json"), mandatory=True, max_age_days=30)
        self.assertEqual(missing["status"], "missing")
        self.assertFalse(missing["trading_eligible"])


class PortfolioRiskTests(unittest.TestCase):
    def exposure(self, symbol: str, money: float, *, kind: str = "open", volume: float = 1.0, asset: str = "fx", session: str = "LON", direction: str = "buy") -> RiskExposure:
        return RiskExposure(symbol, 1.2, 1.1, volume, money / volume, kind, asset, session, direction)

    def test_percentage_and_money_caps_use_stricter_limit_and_include_pending(self) -> None:
        cap = effective_aggregate_risk_cap(base_money=100_000, percentage_cap=3.0, money_cap=2_000)
        self.assertEqual(cap["effective_cap_money"], 2_000)
        gate = aggregate_initial_risk_gate(
            open_exposures=[self.exposure("EURUSD", 1_000)],
            pending_exposures=[self.exposure("GBPUSD", 600, kind="pending")],
            proposed_exposure=self.exposure("USDJPY", 500),
            base_money=100_000,
            percentage_cap=3.0,
            money_cap=2_000,
        )
        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["pending_risk_money"], 600)
        self.assertAlmostEqual(gate["post_trade_risk_pct"], 2.1)

    def test_remaining_original_risk_uses_remaining_volume(self) -> None:
        partial = self.exposure("EURUSD", 400, volume=0.4)
        self.assertEqual(partial.initial_risk_money, 400)

    def test_factor_cluster_asset_session_macro_and_symbol_caps(self) -> None:
        config = {
            "schema_version": RISK_FACTOR_SCHEMA_VERSION,
            "caps_pct": {
                "symbol": 1.0,
                "asset_class": 2.0,
                "session": 2.0,
                "currency": 1.5,
                "factor": 1.5,
                "cluster": 1.5,
                "macro": 1.5,
            },
            "symbol_aliases": {
                "EURUSD": {"factors": {"usd_short": -1.0}, "cluster": "usd_fx", "macro_direction": "risk_on"}
            },
        }
        proposed = self.exposure("EURUSD", 1_100, asset="fx", direction="buy")
        contributions = factor_contributions(proposed, base_money=100_000, config=config)
        self.assertIn("currency:EUR", contributions)
        self.assertIn("currency:USD", contributions)
        self.assertIn("cluster:usd_fx", contributions)
        result = risk_factor_gate(existing=[], proposed=proposed, base_money=100_000, config=config)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["covariance_shadow"]["authority"])


class BrokerCostTests(unittest.TestCase):
    def sample(self, index: int, total: float) -> BrokerCostSample:
        return BrokerCostSample(
            symbol="EURUSD",
            asset_class="fx",
            lot_size=1.0,
            account_currency="USD",
            broker_account_id="broker-1",
            direction="buy",
            entry_commission=total * 0.3,
            exit_commission=total * 0.3,
            swap=0.0,
            fees=total * 0.1,
            observed_spread_money=total * 0.2,
            observed_slippage_money=total * 0.1,
            timestamp=f"2026-07-{index + 1:02d}T00:00:00Z",
        )

    def test_broker_cost_median_stressed_percentile_and_prediction_error(self) -> None:
        samples = [self.sample(i, value) for i, value in enumerate((5, 6, 7, 8, 20))]
        estimate = estimate_broker_cost(
            samples,
            symbol="EURUSD",
            asset_class="fx",
            account_currency="USD",
            broker_account_id="broker-1",
            stressed_percentile=0.9,
            minimum_samples=5,
            fallback_per_lot=12,
        )
        self.assertEqual(estimate["schema_version"], COMMISSION_MODEL_SCHEMA_VERSION)
        self.assertEqual(estimate["source"], "broker_history")
        self.assertAlmostEqual(estimate["median_round_turn_cost_per_lot"], 7)
        self.assertGreater(estimate["stressed_round_turn_cost_per_lot"], 8)
        self.assertEqual(cost_prediction_error(10, 12)["prediction_error"], 2)

    def test_insufficient_history_uses_nonzero_fallback(self) -> None:
        estimate = estimate_broker_cost(
            [],
            symbol="XAUUSD",
            asset_class="metals",
            account_currency="USD",
            broker_account_id="broker-1",
            stressed_percentile=0.95,
            minimum_samples=10,
            fallback_per_lot=0.0,
        )
        self.assertEqual(estimate["source"], "configured_fallback")
        self.assertGreater(estimate["stressed_round_turn_cost_per_lot"], 0)


class ManagementAndVolumeTests(unittest.TestCase):
    def test_state_transition_is_idempotent_across_restart(self) -> None:
        state = ManagementState(position_id="42")
        first = transition_management_state(
            state,
            target_state=MANAGEMENT_THESIS_INVALID,
            reason="confirmed_structure_break",
            evidence={"close": 1.0},
            policy_action="partial_exit",
            transition_time="2026-07-17T00:00:00Z",
        )
        self.assertTrue(first["changed"])
        duplicate_state = ManagementState(position_id="42", current_state=MANAGEMENT_HEALTHY)
        duplicate_state.executed_action_ids.add(first["action_id"])
        duplicate = transition_management_state(
            duplicate_state,
            target_state=MANAGEMENT_THESIS_INVALID,
            reason="confirmed_structure_break",
            evidence={"close": 1.0},
            policy_action="partial_exit",
            transition_time="2026-07-17T00:01:00Z",
        )
        self.assertEqual(duplicate["reason"], "duplicate_action_id")

    def test_management_experiments_remain_shadow_without_oos_evidence(self) -> None:
        result = management_policy_authority(
            clean_ledger=True,
            exact_identity=True,
            sample_size=100,
            minimum_sample=50,
            oos_management_alpha=-0.1,
            uncertainty_passed=True,
            uncontaminated_holdout=True,
        )
        self.assertFalse(result["active_policy_change_eligible"])
        self.assertTrue(result["alternatives_shadow_only"])

    def test_opening_and_closing_normalization_are_distinct(self) -> None:
        opening = normalize_opening_volume(0.006, minimum=0.01, maximum=100, step=0.01, minimum_risk_valid=False)
        self.assertEqual(opening.action, "reject")
        opening_valid = normalize_opening_volume(0.006, minimum=0.01, maximum=100, step=0.01, minimum_risk_valid=True)
        self.assertEqual(opening_valid.normalized_volume, 0.01)
        closing = normalize_closing_volume(
            0.019,
            current=0.05,
            minimum=0.01,
            maximum=100,
            step=0.01,
            allow_close_all_below_minimum=False,
        )
        self.assertEqual(closing.normalized_volume, 0.01)
        self.assertLessEqual(closing.normalized_volume, closing.requested_volume)

    def test_tiny_residual_and_full_close(self) -> None:
        tiny = normalize_closing_volume(
            0.025,
            current=0.03,
            minimum=0.01,
            maximum=100,
            step=0.01,
            allow_close_all_below_minimum=False,
        )
        self.assertEqual(tiny.normalized_volume, 0.02)
        self.assertGreaterEqual(tiny.residual_volume, 0.01 - 1e-9)
        full = normalize_closing_volume(
            0.03,
            current=0.03,
            minimum=0.01,
            maximum=100,
            step=0.01,
            allow_close_all_below_minimum=False,
        )
        self.assertEqual(full.action, "close_all")

    def test_confirmation_rejects_wick_and_confirms_closed_bar_or_persistence(self) -> None:
        state = InvalidationConfirmationState("CLOSED_M1_BAR", "M1", 100.0, 0.2, 0.1)
        wick = confirm_invalidation(state, is_buy=True, current_price=99.0, now_epoch=10, closed_bar_close=100.0, closed_bar_time=9)
        self.assertFalse(wick["confirmed"])
        closed = confirm_invalidation(state, is_buy=True, current_price=99.0, now_epoch=20, closed_bar_close=99.0, closed_bar_time=19)
        self.assertTrue(closed["confirmed"])
        persistence = InvalidationConfirmationState("N_SECOND_PERSISTENCE", "M1", 100.0, 0.0, 0.0)
        self.assertFalse(confirm_invalidation(persistence, is_buy=True, current_price=99.0, now_epoch=1, persistence_seconds=30)["confirmed"])
        self.assertTrue(confirm_invalidation(persistence, is_buy=True, current_price=99.0, now_epoch=31, persistence_seconds=30)["confirmed"])

    def test_asset_class_invalidation_policy_and_invalid_override(self) -> None:
        policy = {
            "schema_version": "asset-invalidation-v1",
            "asset_classes": {
                "metals": {
                    "mode": "N_SECOND_PERSISTENCE",
                    "reference_timeframe": "M1",
                    "persistence_seconds": 30,
                    "spread_buffer_multiple": 1.5,
                }
            },
        }
        resolved = resolve_invalidation_policy(
            asset_class="metals",
            default_mode="CLOSED_M1_BAR",
            default_reference_timeframe="M1",
            default_persistence_seconds=15,
            default_spread_buffer_multiple=1.0,
            policy=policy,
        )
        self.assertTrue(resolved["valid"])
        self.assertEqual(resolved["source"], "asset_class_policy")
        self.assertEqual(resolved["persistence_seconds"], 30)
        malformed = dict(policy)
        malformed["asset_classes"] = {"metals": {"mode": "MAGIC_PASS"}}
        invalid = resolve_invalidation_policy(
            asset_class="metals",
            default_mode="CLOSED_M1_BAR",
            default_reference_timeframe="M1",
            default_persistence_seconds=15,
            default_spread_buffer_multiple=1.0,
            policy=malformed,
        )
        self.assertFalse(invalid["valid"])

    def test_management_alpha_excludes_ambiguous_counterfactual(self) -> None:
        ambiguous = management_alpha(actual_managed_result_r=-0.2, original_sl_tp_result_r=1.0, ambiguous=True)
        self.assertIsNone(ambiguous["management_alpha"])
        self.assertFalse(ambiguous["eligible_for_policy_selection"])
        clear = management_alpha(actual_managed_result_r=0.2, original_sl_tp_result_r=-1.0, ambiguous=False)
        self.assertAlmostEqual(clear["management_alpha"], 1.2)

    def test_counterfactual_remains_pending_until_horizon(self) -> None:
        bars = [{"time": 100, "high": 101.0, "low": 99.5, "close": 100.5}]
        pending = evaluate_price_path_contract(
            bars=bars,
            is_buy=True,
            entry=100.0,
            stop=90.0,
            target=120.0,
            horizon_reached=False,
        )
        self.assertEqual(pending["status"], "PENDING")
        self.assertFalse(pending["complete"])
        self.assertIsNone(pending["result_r"])
        resolved = evaluate_price_path_contract(
            bars=bars,
            is_buy=True,
            entry=100.0,
            stop=90.0,
            target=120.0,
            horizon_reached=True,
        )
        self.assertEqual(resolved["status"], "RESOLVED_HORIZON")
        self.assertAlmostEqual(resolved["result_r"], 0.05)

    def test_counterfactual_same_bar_sl_tp_is_ambiguous(self) -> None:
        result = evaluate_price_path_contract(
            bars=[{"time": 100, "high": 121.0, "low": 89.0, "close": 105.0}],
            is_buy=True,
            entry=100.0,
            stop=90.0,
            target=120.0,
            horizon_reached=True,
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["result_r"])


class SessionShadowAndFvgTests(unittest.TestCase):
    def test_symbol_sessions_differ_and_drive_no_entry_flatten(self) -> None:
        eur = SessionWindow("EURUSD", 1, 0, 1000, 900, 950, 2000)
        gold = SessionWindow("GOLD", 1, 0, 800, 700, 750, 1800)
        self.assertFalse(session_action(eur, 850)["no_new_entry"])
        self.assertTrue(session_action(gold, 760)["flatten"])
        self.assertNotEqual(eur.close_epoch, gold.close_epoch)

    def test_flatten_retry_requires_state_change_or_elapsed_interval(self) -> None:
        state = FlattenRetryState("GOLD", last_state_hash=canonical_hash({"tickets": [1]}), last_attempt_epoch=100)
        self.assertFalse(flatten_retry_allowed(state, exposure_snapshot={"tickets": [1]}, now_epoch=110, retry_seconds=30))
        self.assertTrue(flatten_retry_allowed(state, exposure_snapshot={"tickets": [1, 2]}, now_epoch=110, retry_seconds=30))

    def test_shadow_candidate_never_has_trade_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shadow.jsonl"
            record = append_shadow_candidate(path, {"candidate_id": "c1", "decision_stage": "pre_ai", "accepted": True})
            self.assertFalse(record["can_trade"])
            self.assertEqual(record["authority"], "RECOMMENDATION_ONLY")
            self.assertTrue(path.is_file())

    def test_normalized_fvg_uses_max_component_and_modes(self) -> None:
        kwargs = dict(
            raw_width_price=0.0005,
            point=0.0001,
            minimum_ticks=1,
            spread_price=0.0002,
            spread_multiple=2,
            atr_price=0.001,
            atr_fraction=0.25,
            session_noise_price=0.002,
            session_noise_fraction=0.3,
            asset_class="fx",
            policy_version="asset-class-v1",
            sample_size=100,
        )
        shadow = normalized_fvg_minimum(mode=FVG_MODE_SHADOW, **kwargs)
        self.assertAlmostEqual(shadow["normalized_minimum_price"], 0.0006)
        self.assertFalse(shadow["shadow_pass"])
        self.assertTrue(shadow["enforced_pass"])
        enforced = normalized_fvg_minimum(mode=FVG_MODE_ENFORCE, **kwargs)
        self.assertFalse(enforced["enforced_pass"])
        off = normalized_fvg_minimum(mode=FVG_MODE_OFF, **kwargs)
        self.assertTrue(off["enforced_pass"])
        self.assertFalse(off["per_symbol_tuning"])

    def test_normalized_fvg_enforce_fails_closed_without_asset_class_sample(self) -> None:
        result = normalized_fvg_minimum(
            raw_width_price=0.002,
            point=0.0001,
            minimum_ticks=1,
            spread_price=0.0001,
            spread_multiple=1,
            atr_price=0.001,
            atr_fraction=0.1,
            session_noise_price=0.001,
            session_noise_fraction=0.1,
            mode=FVG_MODE_ENFORCE,
            asset_class="fx",
            policy_version="asset-class-v1",
            sample_size=4,
            minimum_asset_class_samples=100,
        )
        self.assertFalse(result["enforced_pass"])
        self.assertFalse(result["asset_class_evidence_sufficient"])
        self.assertEqual(result["authority_reason"], "insufficient_asset_class_samples")


if __name__ == "__main__":
    unittest.main()
