from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from calibration_pipeline import artifact_compatible, run_shadow_calibration
from experiment_registry import ExperimentRegistry
from governance_contracts import (
    AccountPositionMode,
    FEATURE_LINEAGE,
    FEATURE_LINEAGE_VERSION,
    LedgerIntegrityStatus,
    NettingPolicy,
    SETUP_TAXONOMY_VERSION,
    SetupTaxonomy,
    audit_trade_records,
    block_bootstrap_uncertainty,
    calculate_deal_accounting,
    calculate_outcome_metrics,
    classify_setup_taxonomy,
    enforce_policy_governance,
    feature_lineage_violations,
    purged_chronological_folds,
    resolve_account_position_mode,
    suppression_eligibility,
)


ROOT = Path(__file__).resolve().parents[1]


def _resolve_mql_include_root() -> Path:
    """Resolve staged or active terminal includes without binding tests to one layout."""

    override = os.environ.get("PO3_MQL_INCLUDE_ROOT", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        if not (path / "TradeEngine.mqh").is_file():
            raise FileNotFoundError(f"PO3_MQL_INCLUDE_ROOT is invalid: {path}")
        return path

    staged = ROOT.parent / "mql" / "Include" / "MT5_PO3_Codex"
    if (staged / "TradeEngine.mqh").is_file():
        return staged

    appdata = os.environ.get("APPDATA", "").strip()
    terminal_root = Path(appdata) / "MetaQuotes" / "Terminal" if appdata else None
    candidates = (
        sorted(terminal_root.glob("*/MQL5/Include/MT5_PO3_Codex"))
        if terminal_root is not None and terminal_root.is_dir()
        else []
    )
    valid = [path for path in candidates if (path / "TradeEngine.mqh").is_file()]
    if len(valid) == 1:
        return valid[0]
    if not valid:
        raise FileNotFoundError(
            "No PO3 MQL include tree found; set PO3_MQL_INCLUDE_ROOT to the active include directory"
        )
    raise RuntimeError(
        "Multiple PO3 MQL include trees found; set PO3_MQL_INCLUDE_ROOT explicitly: "
        + "; ".join(str(path) for path in valid)
    )


MQL_STAGE = _resolve_mql_include_root()


def clean_trade(position_id: int = 9001, *, risk: float = 100.0, pnl: float = 100.0) -> dict:
    entry_commission = -1.0
    exit_commission = -1.0
    exit_swap = -1.0
    exit_fee = -1.0
    exit_profit = pnl - entry_commission - exit_commission - exit_swap - exit_fee
    return {
        "ledger_schema_version": "20260716_trade_ledger_integrity_v4",
        "position_id": str(position_id),
        "broker_position_identifier": str(position_id),
        "position_ticket": str(position_id + 100),
        "attribution_status": "verified",
        "execution_identity_verified": True,
        "execution_identity_quarantined": False,
        "account_position_mode": AccountPositionMode.HEDGING_EXACT_POSITION_ID.value,
        "trade_key": f"trade-{position_id}",
        "candidate_id": f"candidate-{position_id}",
        "candidate_hash": f"hash-{position_id}",
        "candidate_hash_match": True,
        "assessed_execution_fingerprint": f"fp-{position_id}",
        "final_execution_fingerprint": f"fp-{position_id}",
        "execution_fingerprint_match": True,
        "decision_quality_tier": "FULL_STRUCTURED",
        "setup_taxonomy_enum": SetupTaxonomy.MICRO_FVG_MID_REVERSAL.value,
        "setup_taxonomy_version": SETUP_TAXONOMY_VERSION,
        "taxonomy_mapping_source": "explicit_internal_taxonomy_enum",
        "symbol": "GOLD",
        "magic_number": 5303001,
        "is_buy": True,
        "direction": "buy",
        "symbol_tick_size": 0.01,
        "symbol_digits": 2,
        "planned_entry": 100.0,
        "filled_entry": 100.0,
        "planned_sl": 99.0,
        "planned_tp1": 101.0,
        "planned_tp2": 102.0,
        "opened_at": 1_700_000_000 + position_id,
        "filled_at": 1_700_000_000 + position_id,
        "closed_at": 1_700_003_600 + position_id,
        "configured_fixed_initial_balance": 100_000.0,
        "account_equity_at_entry": 100_000.0,
        "initial_risk_money": risk,
        "broker_net_pnl": pnl,
        "internal_net_pnl": pnl,
        "result_r_initial_risk": pnl / risk,
        "mfe_r": max(0.1, pnl / risk + 0.2),
        "mae_r": 0.25,
        "engine_version": "engine-v4",
        "runtime_input_hash": "runtime-v4",
        "prompt_contract_version": "prompt-v4",
        "decision_schema_version": "decision-v4",
        "policy_id": "policy-v1",
        "management_version": "management-v1",
        "session_name": "London",
        "regime_bucket": "normal",
        "asset_class": "metal",
        "commission_r": 0.02,
        "total_commission": -2.0,
        "deals": [
            {
                "ticket": position_id + 1,
                "position_id": str(position_id),
                "symbol": "GOLD",
                "magic": 5303001,
                "entry": "in",
                "direction": "buy",
                "volume": 1.0,
                "profit": 0.0,
                "commission": entry_commission,
                "swap": 0.0,
                "fee": 0.0,
            },
            {
                "ticket": position_id + 2,
                "position_id": str(position_id),
                "symbol": "GOLD",
                "magic": 5303001,
                "entry": "out",
                "direction": "sell",
                "volume": 1.0,
                "profit": exit_profit,
                "commission": exit_commission,
                "swap": exit_swap,
                "fee": exit_fee,
            },
        ],
    }


def negative_evidence_records(count: int = 100) -> list[dict]:
    output = []
    for index in range(count):
        row = clean_trade(20_000 + index, pnl=-50.0)
        row["opened_at"] = 1_700_000_000 + index * 86_400
        row["filled_at"] = row["opened_at"]
        row["closed_at"] = row["opened_at"] + 3_600
        row["result_r_initial_risk"] = -0.5
        row["broker_net_pnl"] = -50.0
        row["internal_net_pnl"] = -50.0
        row["mfe_r"] = 0.1
        row["engine_version"] = f"engine-v{index % 2 + 1}"
        row["runtime_input_hash"] = f"runtime-{index % 2}"
        row["prompt_contract_version"] = f"prompt-{index % 2}"
        row["policy_id"] = f"policy-{index % 2}"
        row["management_version"] = f"management-{index % 2}"
        row["symbol"] = "GOLD" if index % 2 == 0 else "SILVER"
        row["session_name"] = "London" if index % 2 == 0 else "NewYork"
        row["regime_bucket"] = "normal" if index % 2 == 0 else "expansion"
        row["asset_class"] = "metal" if index % 2 == 0 else "fx"
        row["ledger_integrity_status"] = LedgerIntegrityStatus.CLEAN.value
        row["suppression_eligible"] = True
        output.append(row)
    return output


class GovernanceContractTests(unittest.TestCase):
    def test_hedging_mode_is_exact_position_mode(self) -> None:
        result = resolve_account_position_mode("HEDGING")
        self.assertTrue(result.startup_allowed)
        self.assertEqual(result.internal_mode, AccountPositionMode.HEDGING_EXACT_POSITION_ID)

    def test_netting_default_fails_closed(self) -> None:
        result = resolve_account_position_mode("NETTING")
        self.assertFalse(result.startup_allowed)
        self.assertEqual(result.internal_mode, AccountPositionMode.UNSUPPORTED_ACCOUNT_MODE)

    def test_netting_one_position_fallback_requires_explicit_policy(self) -> None:
        result = resolve_account_position_mode(
            "NETTING", NettingPolicy.FORCE_ONE_MANAGED_POSITION_PER_SYMBOL
        )
        self.assertTrue(result.startup_allowed)
        self.assertEqual(result.internal_mode, AccountPositionMode.NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK)

    def test_complete_deal_accounting_includes_all_costs_and_partials(self) -> None:
        row = clean_trade()
        accounting = calculate_deal_accounting("9001", row["deals"], expected_symbol="GOLD")
        self.assertAlmostEqual(accounting["internal_net_pnl"], 100.0)
        self.assertAlmostEqual(accounting["total_commission"], -2.0)
        self.assertAlmostEqual(accounting["total_swap"], -1.0)
        self.assertAlmostEqual(accounting["total_fees"], -1.0)
        self.assertEqual(accounting["opened_volume"], accounting["closed_volume"])

    def test_deal_position_id_mismatch_is_not_repaired(self) -> None:
        row = clean_trade()
        row["deals"][1]["position_id"] = "other"
        accounting = calculate_deal_accounting("9001", row["deals"], expected_symbol="GOLD")
        self.assertIn("deal_position_id_mismatch", accounting["errors"])

    def test_three_outcome_metrics_share_direction(self) -> None:
        metrics = calculate_outcome_metrics(
            250.0,
            fixed_initial_balance=100_000.0,
            equity_at_entry=50_000.0,
            initial_risk_money=500.0,
        )
        self.assertAlmostEqual(metrics["result_pct_fixed_initial_balance"], 0.25)
        self.assertAlmostEqual(metrics["result_pct_equity_at_entry"], 0.5)
        self.assertAlmostEqual(metrics["result_r_initial_risk"], 0.5)
        self.assertTrue(metrics["outcome_direction_match"])

    def test_clean_ledger_record_is_eligible(self) -> None:
        audited, report = audit_trade_records([clean_trade()])
        self.assertEqual(report["global_status"], LedgerIntegrityStatus.CLEAN.value)
        self.assertTrue(audited[0]["learning_eligible"])

    def test_missing_position_id_is_unattributed(self) -> None:
        row = clean_trade()
        row["position_id"] = ""
        row["broker_position_identifier"] = ""
        audited, report = audit_trade_records([row])
        self.assertEqual(audited[0]["ledger_integrity_status"], LedgerIntegrityStatus.UNATTRIBUTED.value)
        self.assertFalse(audited[0]["optimization_eligible"])
        self.assertEqual(report["global_status"], LedgerIntegrityStatus.QUARANTINED.value)

    def test_duplicate_position_final_records_are_quarantined(self) -> None:
        first = clean_trade()
        second = clean_trade()
        second["trade_key"] = "other-trade"
        audited, report = audit_trade_records([first, second])
        self.assertEqual(report["duplicate_position_ids"], ["9001"])
        self.assertTrue(all(not row["learning_eligible"] for row in audited))

    def test_broker_internal_pnl_mismatch_quarantines(self) -> None:
        row = clean_trade()
        row["internal_net_pnl"] = 500.0
        audited, _ = audit_trade_records([row])
        self.assertEqual(audited[0]["ledger_integrity_status"], LedgerIntegrityStatus.QUARANTINED.value)

    def test_equal_and_risk_weighted_expectancy_are_separate(self) -> None:
        first = clean_trade(9001, risk=100.0, pnl=100.0)
        second = clean_trade(9002, risk=900.0, pnl=-450.0)
        audited, report = audit_trade_records([first, second])
        self.assertTrue(all(row["ledger_integrity_status"] == "CLEAN" for row in audited))
        self.assertAlmostEqual(report["equal_trade_weighted_expectancy_r"], 0.25)
        self.assertAlmostEqual(report["risk_weighted_expectancy_r"], -0.35)

    def test_taxonomy_explicit_enum_wins(self) -> None:
        result = classify_setup_taxonomy({"setup_taxonomy_enum": "MICRO_BREAKER_RETEST"})
        self.assertEqual(result.taxonomy, SetupTaxonomy.MICRO_BREAKER_RETEST)

    def test_taxonomy_uses_exact_branch_not_comment_substrings(self) -> None:
        result = classify_setup_taxonomy({"entry_branch": "mystery", "comment": "breaker_retest"})
        self.assertEqual(result.taxonomy, SetupTaxonomy.UNKNOWN_UNCLASSIFIED)

    def test_ledger_report_enumerates_unknown_taxonomy_combinations(self) -> None:
        row = clean_trade()
        row["setup_taxonomy_enum"] = "UNKNOWN_UNCLASSIFIED"
        row["entry_branch"] = "future_branch"
        row["setup_family"] = "future_family"
        _, report = audit_trade_records([row])
        self.assertEqual(report["unknown_taxonomy_combinations"][0]["entry_branch"], "future_branch")
        self.assertEqual(report["unknown_taxonomy_combinations"][0]["count"], 1)

    def test_all_supported_taxonomy_branches_map(self) -> None:
        cases = {
            "fvg_mid": SetupTaxonomy.MICRO_FVG_MID_REVERSAL,
            "fvg_edge": SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL,
            "breaker_retest": SetupTaxonomy.MICRO_BREAKER_RETEST,
            "ote_inside_fvg": SetupTaxonomy.MICRO_OTE_REVERSAL,
            "continuation_reentry": SetupTaxonomy.MICRO_CONTINUATION_FVG,
            "range_reentry": SetupTaxonomy.MICRO_RANGE_REENTRY,
            "session_reentry": SetupTaxonomy.MICRO_SESSION_REENTRY,
        }
        for branch, expected in cases.items():
            with self.subTest(branch=branch):
                self.assertEqual(classify_setup_taxonomy({"entry_branch": branch}).taxonomy, expected)

    def test_taxonomy_python_matches_mql_continuation_precedence(self) -> None:
        continuation = classify_setup_taxonomy(
            {"entry_branch": "breaker_retest", "structure_type": "micro_continuation"}
        )
        nested = classify_setup_taxonomy({"entry_branch": "nested_fvg_edge"})
        failed = classify_setup_taxonomy(
            {"entry_branch": "fvg_mid", "structure_type": "micro_failed_breakout_reclaim"}
        )
        self.assertEqual(continuation.taxonomy, SetupTaxonomy.MICRO_CONTINUATION_FVG)
        self.assertEqual(nested.taxonomy, SetupTaxonomy.MICRO_NESTED_CONTINUATION)
        self.assertEqual(failed.taxonomy, SetupTaxonomy.FAILED_BREAKOUT_RECLAIM)

    def test_day_block_bootstrap_preserves_cluster_dependence(self) -> None:
        rows = negative_evidence_records(20)
        result = block_bootstrap_uncertainty(rows, iterations=100, block_type="day")
        self.assertTrue(result["available"])
        self.assertEqual(result["effective_block_count"], 20)
        self.assertLess(result["mean_r_ci_high"], 0.0)

    def test_purged_walk_forward_is_chronological(self) -> None:
        folds = purged_chronological_folds(negative_evidence_records(60), folds=3)
        self.assertGreaterEqual(len(folds), 2)
        self.assertTrue(all(row["test_avg_r"] < 0 for row in folds))

    def test_suppression_requires_every_evidence_gate(self) -> None:
        result = suppression_eligibility(
            negative_evidence_records(100),
            ledger_status=LedgerIntegrityStatus.CLEAN.value,
            min_clean_sample=50,
            bootstrap_iterations=200,
        )
        self.assertTrue(result["suppression_eligible"], result["suppression_block_reasons"])

    def test_suppression_fails_when_ci_crosses_zero_or_blocks_insufficient(self) -> None:
        rows = negative_evidence_records(4)
        result = suppression_eligibility(
            rows,
            ledger_status=LedgerIntegrityStatus.CLEAN.value,
            min_clean_sample=4,
            bootstrap_iterations=100,
        )
        self.assertFalse(result["suppression_eligible"])
        self.assertIn("uncertainty_unavailable", result["suppression_block_reasons"])

    def test_policy_governance_neutralizes_unregistered_actions(self) -> None:
        rows = enforce_policy_governance(
            [{"action": "suppress", "risk_multiplier": 0.0, "score_bias": -3.0}],
            activation_allowed=False,
            block_reasons=["experiment_not_authorized"],
        )
        self.assertEqual(rows[0]["action"], "diagnostic_only")
        self.assertEqual(rows[0]["risk_multiplier"], 1.0)
        self.assertEqual(rows[0]["score_bias"], 0.0)

    def test_feature_lineage_excludes_setup_score_from_empirical_model(self) -> None:
        setup_rows = [row for row in FEATURE_LINEAGE if row["feature_name"] == "diagnostic_legacy_setup_score"]
        self.assertEqual(len(setup_rows), 1)
        self.assertFalse(setup_rows[0]["sent_to_llm"])
        self.assertFalse(setup_rows[0]["used_by_statistical_model"])

    def test_feature_lineage_detects_authority_leak(self) -> None:
        manifest = {"features": [{"feature_name": "setup_score", "sent_to_llm": True}]}
        self.assertIn("legacy_feature_has_authority:setup_score", feature_lineage_violations(manifest))

    def test_shadow_calibration_stays_inactive_when_sample_is_small(self) -> None:
        rows = [{"ledger_integrity_status": "CLEAN", "learning_eligible": True} for _ in range(10)]
        artifact = run_shadow_calibration(
            rows,
            ledger_status="CLEAN",
            engine_version="engine-v4",
            decision_schema_version="decision-v4",
        )
        self.assertFalse(artifact["calibration_available"])
        self.assertFalse(artifact["trading_activation"])

    def test_calibration_artifact_never_activates_by_fit_alone(self) -> None:
        rows = []
        for index in range(140):
            row = {
                "ledger_integrity_status": "CLEAN",
                "learning_eligible": True,
                "opened_at": 1_700_000_000 + index * 86_400,
                "result_r_initial_risk": 1.0 if index % 3 else -1.0,
            }
            for feature_index, feature in enumerate(FEATURE_LINEAGE[:14]):
                row[feature["feature_name"]] = ((index + feature_index) % 11) / 10.0
            rows.append(row)
        artifact = run_shadow_calibration(
            rows,
            ledger_status="CLEAN",
            engine_version="engine-v4",
            decision_schema_version="decision-v4",
            min_clean_sample=120,
        )
        self.assertFalse(artifact["trading_activation"])
        self.assertFalse(
            artifact_compatible(
                artifact,
                engine_version="other-engine",
                decision_schema_version="decision-v4",
            )
        )

    def test_experiment_registry_detects_holdout_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_file = root / "test.set"
            set_file.write_text("x=1", encoding="utf-8")
            registry = ExperimentRegistry(root / "registry.jsonl")
            spec = {
                "experiment_id": "exp-1",
                "hypothesis": "negative bucket is stable",
                "engine_version": "engine-v4",
                "set_file_path": str(set_file),
                "runtime_input_hash": "runtime",
                "prompt_contract_version": "prompt",
                "decision_schema_version": "decision",
                "target_schema_version": "target",
                "ai_model_version": "model",
                "policy_id": "policy",
                "setup_taxonomy_version": SETUP_TAXONOMY_VERSION,
                "management_version": "management",
                "hierarchical_prior_schema_version": "20260717_hierarchical_prior_v1",
                "hierarchical_prior_artifact_hash": "prior-artifact-hash-001",
                "symbol_universe_hash": "symbols",
                "tested_date_range": {"start": "2026-01-01", "end": "2026-03-31"},
                "training_range": {"start": "2026-01-01", "end": "2026-01-31"},
                "calibration_range": {"start": "2026-02-01", "end": "2026-02-15"},
                "validation_range": {"start": "2026-02-16", "end": "2026-02-28"},
                "final_untouched_holdout_range": {"start": "2026-03-01", "end": "2026-03-31"},
            }
            registry.create(spec, repo_root=root)
            authorized, _ = registry.optimization_authorized("exp-1", spec["hypothesis"])
            self.assertTrue(authorized)
            registry.mark_period_inspected("exp-1", spec["final_untouched_holdout_range"], purpose="review")
            status = registry.holdout_status(spec["final_untouched_holdout_range"], exclude_experiment_id="")
            self.assertTrue(status.contaminated)

    def test_mql_penalty_state_uses_position_identifier_primary(self) -> None:
        source = (MQL_STAGE / "PenaltyWatcher.mqh").read_text(encoding="utf-8")
        self.assertIn("_FindState(const long position_identifier", source)
        self.assertNotIn("m_states[i].position_ticket == ticket", source)

    def test_mql_taxonomy_resets_for_every_branch_candidate(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        marker = 'p.entry_model = branch;\n      p.setup_taxonomy = UNKNOWN_UNCLASSIFIED;'
        self.assertIn(marker, source)

    def test_mql_outcome_direction_contract_is_canonical(self) -> None:
        engine = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        state = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8")
        self.assertIn("meta.outcome_direction_broker", engine)
        self.assertIn("meta.outcome_direction_match", engine)
        self.assertIn("meta.outcome_reconciliation_status", engine)
        self.assertNotIn("meta.outcome_direction_money", engine)
        self.assertNotIn("meta.outcome_direction_fixed_pct", engine)
        self.assertIn('JsonKVStr("outcome_direction_broker", p.outcome_direction_broker)', state)
        self.assertNotIn("p.outcome_direction_money", state)

    def test_mql_completed_position_has_primary_identity_marker(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn("_CompletedPositionMarkerPath", source)
        self.assertIn("DEAL_POSITION_ID", source)
        self.assertIn("[broker_pnl_reconciliation] position_id=", source)

    def test_mql_account_mode_log_and_netting_reject_are_explicit(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn('" internal_mode=", m_account_position_mode', source)
        self.assertIn("virtual_subposition_ledger_available=false", source)
        self.assertIn("reason=netting_virtual_subposition_ledger_unavailable", source)


if __name__ == "__main__":
    unittest.main()
