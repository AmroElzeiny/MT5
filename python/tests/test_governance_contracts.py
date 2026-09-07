from __future__ import annotations

import copy
import json
import os
import re
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


def _function_body(source: str, name: str) -> str:
    """Return one MQL5 function body, brace-matched.

    Scoping an assertion to a single function is what stops it passing because some
    unrelated function elsewhere in a 15k-line file happens to contain the token.
    Braces inside comments and string literals are skipped so a journal line such as
    "{kind}" cannot unbalance the scan.
    """

    signature = re.search(rf"^\s*\w[\w\s\*&]*\b{re.escape(name)}\s*\(", source, re.M)
    if signature is None:
        raise AssertionError(f"function not found in MQL source: {name}")

    opening = source.find("{", signature.end())
    if opening < 0:
        raise AssertionError(f"no body found for MQL function: {name}")

    depth = 0
    index = opening
    end = len(source)
    while index < end:
        char = source[index]
        pair = source[index : index + 2]
        if pair == "//":
            index = source.find("\n", index)
            if index < 0:
                break
            continue
        if pair == "/*":
            index = source.find("*/", index)
            if index < 0:
                break
            index += 2
            continue
        if char in ('"', "'"):
            quote = char
            index += 1
            while index < end and source[index] != quote:
                index += 2 if source[index] == "\\" else 1
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
        index += 1

    raise AssertionError(f"unbalanced braces while scanning MQL function: {name}")


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
        # The invariant is that the SAME plan object has its taxonomy reset immediately
        # after entry_model is assigned, so no branch can inherit the previous
        # candidate's taxonomy.  The backreference pins both statements to one object;
        # only the local variable's name is left free, so renaming it (p -> out_plan)
        # cannot silently disable this guard the way a hardcoded name did.
        marker = re.compile(
            r"(\w+)\.entry_model = branch;\s*\n\s*\1\.setup_taxonomy = UNKNOWN_UNCLASSIFIED;"
        )
        self.assertRegex(source, marker)

    def test_permitted_entry_branches_resolve_back_to_their_own_taxonomy(self) -> None:
        """Every branch a family profile permits must actually produce that family.

        classify_setup_taxonomy is the only thing that turns an entry_branch into a
        taxonomy, so a profile listing a branch that resolves elsewhere is telling the
        model something the pipeline contradicts.  FULL_PO3_CONTINUATION used to list
        ("full_po3_continuation", "continuation_fvg") -- a family name and a state
        name, neither of which MQL emits as entry_branch -- so every full-PO3
        candidate was vetoed ai_veto_execution_plan_mismatch on a branch that Python
        itself had just used to assign the family.
        """

        from family_context import FAMILY_CONTEXT_REGISTRY
        from governance_contracts import classify_setup_taxonomy

        # Only the accompanying state a taxonomy genuinely requires; the branch alone
        # must carry the rest, otherwise the profile is claiming more than it can.
        state = {
            "MICRO_CONTINUATION_FVG": {"structure_state": "micro_continuation"},
            "FAILED_BREAKOUT_RECLAIM": {"structure_state": "micro_failed_breakout_reclaim"},
            "FULL_PO3_REVERSAL": {"po3_scope": "institutional_po3"},
            "FULL_PO3_CONTINUATION": {
                "po3_scope": "institutional_po3",
                "structure_state": "continuation_bos",
            },
        }

        self.assertTrue(FAMILY_CONTEXT_REGISTRY)
        for taxonomy, profile in FAMILY_CONTEXT_REGISTRY.items():
            self.assertTrue(
                profile.permitted_entry_branches,
                msg=f"{taxonomy} permits no entry branch at all",
            )
            for branch in profile.permitted_entry_branches:
                with self.subTest(taxonomy=taxonomy, branch=branch):
                    fields = {"entry_branch": branch}
                    fields.update(state.get(taxonomy, {}))
                    resolved = classify_setup_taxonomy(fields).taxonomy.value
                    self.assertEqual(
                        resolved,
                        taxonomy,
                        msg=(
                            f"{taxonomy} permits branch {branch!r}, but "
                            f"classify_setup_taxonomy maps it to {resolved}"
                        ),
                    )

    def test_full_po3_profiles_permit_every_branch_mql_emits_for_them(self) -> None:
        """The branches seen live must be permitted, or the veto is guaranteed."""

        from family_context import FAMILY_CONTEXT_REGISTRY

        # Observed in the 2026-08-18 luna run: 17 breaker_retest, 11 nested_htf_ltf_fvg,
        # 2 continuation_reentry -- 100% of full-PO3 candidates, all vetoed.
        continuation = FAMILY_CONTEXT_REGISTRY["FULL_PO3_CONTINUATION"].permitted_entry_branches
        reversal = FAMILY_CONTEXT_REGISTRY["FULL_PO3_REVERSAL"].permitted_entry_branches
        for branch in ("breaker_retest", "nested_htf_ltf_fvg", "continuation_reentry"):
            self.assertIn(branch, continuation)
        for branch in ("breaker_retest", "nested_htf_ltf_fvg", "fvg_mid", "fvg_edge"):
            self.assertIn(branch, reversal)
        # continuation_reentry forces continuation=True, so a reversal can never
        # arrive on it; permitting it there would be the same defect mirrored.
        self.assertNotIn("continuation_reentry", reversal)
        # No family/state token may reappear in a branch list.
        for name, branches in (("continuation", continuation), ("reversal", reversal)):
            for token in ("full_po3", "full_po3_continuation", "po3_reversal", "continuation_fvg"):
                self.assertNotIn(token, branches, msg=f"{name} profile still lists {token}")

    def test_mql_killer_obstacle_gate_separates_degenerate_obstacles(self) -> None:
        """A sub-InpObstacleMinStopMult level must not be published as a killer crossing.

        _ObstacleSeverity adds a flat +1.5 for any obstacle inside InpObstacleRejectR,
        so a level 0.0003R from entry scores the same 8.50 as one at 0.69R.  Both are
        rejected -- that is correct and must stay correct -- but 80% of the live
        `all_routes_cross_killer_obstacle` bucket was really "the entry has no room in
        front of it", which _DeterministicExecutionGate already calls obstacle_too_close.
        The reason string is the only thing that told them apart, so it is pinned here.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        gate = _function_body(source, "_SelectObstacleAwareTarget")

        # The degenerate test must exist, and must be measured against the SAME input
        # the downstream execution gate uses -- not a fresh literal.
        self.assertRegex(
            gate,
            re.compile(
                r"(\w+)\s*=\s*\(p\.obstacle_distance_r\s*>\s*0\.0\s*&&\s*"
                r"p\.obstacle_distance_r\s*<\s*InpObstacleMinStopMult\)"
            ),
        )
        # The published reason must branch on that test, so the two conditions can
        # never again collapse into one name.
        self.assertRegex(
            gate,
            re.compile(
                r'reason\s*=\s*\(\s*degenerate_obstacle\s*\?\s*"obstacle_inside_entry_structure"'
                r'\s*:\s*"all_routes_cross_killer_obstacle"\s*\)'
            ),
        )
        # Diagnostics must carry the threshold that made the call, otherwise the
        # bucket is undiagnosable again the moment the input moves.
        self.assertIn("min_stop_mult=", gate)
        self.assertIn("degenerate=", gate)

    def test_mql_obstacle_proximity_threshold_has_one_owner(self) -> None:
        """Both obstacle-proximity gates must key off InpObstacleMinStopMult.

        The target selector and _DeterministicExecutionGate reject the same condition
        at different stages.  If either one grows its own literal they drift, and the
        funnel starts reporting two different answers for one market fact.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        selector = _function_body(source, "_SelectObstacleAwareTarget")
        execution = _function_body(source, "_DeterministicExecutionGate")

        self.assertIn("InpObstacleMinStopMult", selector)
        self.assertIn("InpObstacleMinStopMult", execution)
        self.assertIn("obstacle_too_close", execution)

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
        self.assertIn('"\\\\completed_scope_" + m_state.RuntimeScope()', source)
        self.assertIn('JsonKVStr("run_session_id", m_ai.SessionId())', source)
        self.assertIn('JsonKVStr("runtime_state_scope", m_state.RuntimeScope())', source)
        self.assertIn("DEAL_POSITION_ID", source)
        self.assertIn("[broker_pnl_reconciliation] position_id=", source)

    def test_mql_partial_exit_cannot_finalize_an_open_position(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        start = source.index("bool _WriteClosedTradeOutcome")
        end = source.index("void _FinalizeClosedTrades", start)
        body = source[start:end]
        self.assertIn("_FindPositionTicketByIdentifier(position_id) > 0", body)
        self.assertIn("partial_exit_not_final=true", body)
        self.assertGreaterEqual(body.count("_TradeResultPath(key)"), 3)

    def test_mql_mutable_runtime_state_is_isolated_from_tester_and_other_live_strategies(self) -> None:
        engine = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        state = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8")
        self.assertIn('runtime_state_scope = "tester_session_" + m_ai.SessionId()', engine)
        self.assertIn('runtime_state_scope = "live_account_"', engine)
        self.assertIn('"_magic_" + IntegerToString((long)InpMagicNumber)', engine)
        self.assertIn("m_state.SetRuntimeScope(runtime_state_scope)", engine)
        self.assertIn('return m_bus.LogDir() + "\\\\runtime_state";', state)
        self.assertIn('string PenaltyPath() const { return _ScopedPath("penalty.ndjson"); }', state)
        self.assertNotIn('m_bus.LogDir() + "\\\\penalty.ndjson"', state)

    def test_record_only_exports_each_tester_cache_signature_once(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn("m_tester_recorded_cache_signatures", source)
        self.assertIn("_TesterRecordSignatureSeen(tester_cache_signature)", source)
        self.assertIn("action=skip_duplicate_export", source)
        self.assertIn("_RememberTesterRecordSignature(tester_cache_signature)", source)

    # ------------------------------------------------------------------
    # Obstacle-label authority.  p.obstacle_kind is engine-owned evidence.  The AI
    # response field target_blocker_kind used to overwrite it inside
    # _ApplyAiTargetArbitration, so _LockAssessedPlan froze the MODEL's free-text
    # spelling as the plan's immutable identity while execution re-derived the
    # ENGINE's spelling through _PublishObstacleEvidence.  The two vocabularies could
    # never agree, and the plan died as execution_fingerprint_mismatch:obstacle_kind --
    # 4 of the 11 approvals in the 2026-09-05 week replay, where the model wrote
    # "opposing_htf_imbalance" / "fresh_htf_opposing_imbalance" / "crossed_session_high"
    # for obstacles the engine names "crossed_htf_opposing_imbalance" and
    # "crossed_opposing_imbalance" at identical severity.
    # ------------------------------------------------------------------

    def test_mql_obstacle_label_is_never_owned_by_the_model(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        bounds = self._mql_function_bodies(source)
        self.assertGreater(len(bounds), 50, "function scan found almost nothing - the scan is broken")

        writer = re.compile(r"\b(\w+(?:\[\w+\])?)\.obstacle_kind\s*=\s*([^;]+);")
        model_writes: list[str] = []
        for name, start, end in bounds:
            for match in writer.finditer(source[start:end]):
                value = match.group(2)
                if "dec." in value or "m_ai." in value or ".ai." in value:
                    model_writes.append(f"{name}: {match.group(0).strip()}")
        self.assertEqual(
            [],
            model_writes,
            "obstacle_kind must be derived by the engine, never taken from an AI decision",
        )

        # The engine's single derivation point still exists, and the disagreement
        # between the engine's label and the model's stays observable.
        self.assertIn(
            "p.obstacle_kind = (mark_crossed ? _CrossedObstacleKind(obstacle_kind) : obstacle_kind);",
            source,
        )
        self.assertIn("[obstacle_label_authority]", source)
        self.assertIn("action=model_label_kept_diagnostic_only", source)
        # The model's own view is still carried, just not as identity.
        self.assertIn("p.ai_blocker_severity = severity;", source)
        self.assertIn('JsonKVStr("target_blocker_kind", dec.target_blocker_kind)', source)

    # ------------------------------------------------------------------
    # Replay-cache key stability.  _TesterAiCacheSignature used to include
    # assessed_execution_fingerprint, and _ExecutionFingerprint mixes identity with
    # four LIVE cost measurements rendered at six decimals.  The execution contract
    # explicitly tolerates those drifting by max_cost_deterioration_r, so hashing them
    # into a key compared for equality contradicts the contract: 61 of the 159
    # tester_ai_cache_miss rejections in the 2026-09-05 week replay carried a
    # signature identical to a recorded one except for that field, and one lost
    # approval differed only by 1e-6 in net_reward_after_cost_r.
    # ------------------------------------------------------------------

    def test_tester_cache_signature_is_identity_not_live_cost(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        start = source.index("string _TesterAiCacheSignature(")
        end = source.index("string _TesterAiCacheKey(", start)
        body = source[start:end]

        self.assertIn("plans[i].candidate_hash", body, "the key must still bind candidate identity")
        for field in (
            "assessed_execution_fingerprint",
            "request_execution_fingerprint",
            "final_execution_fingerprint",
            "spread_r",
            "slippage_r",
            "execution_cost_r",
            "net_reward_after_cost_r",
        ):
            self.assertNotIn(
                f"plans[i].{field}",
                body,
                f"the replay cache key must not depend on {field}",
            )

        # The values removed from the key are still checked where they belong:
        # with the contract's tolerance, not by string equality.
        self.assertIn("execution_cost_r_deterioration", source)
        self.assertIn("spread_r_deterioration", source)
        self.assertIn("slippage_r_deterioration", source)
        self.assertIn("c.max_cost_deterioration_r", source)

    def test_mql_account_mode_log_and_netting_reject_are_explicit(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn('" internal_mode=", m_account_position_mode', source)
        self.assertIn("virtual_subposition_ledger_available=false", source)
        self.assertIn("reason=netting_virtual_subposition_ledger_unavailable", source)

    # ------------------------------------------------------------------
    # TP1 authority.  A target model that defines its own first leg is the
    # authority on it.  _BuildPlanPrices used to rebuild tp1 unconditionally from
    # tp1_r_multiple, and its 0.65R floor moved a partial sitting 0.0108R in front
    # of a major obstacle out to 1.0R -- past the obstacle -- so every shipped plan
    # contradicted its own tp_model and its own target_candidates table.  The AI
    # read both and vetoed with ai_veto_target_arbitration_incoherent.
    # ------------------------------------------------------------------

    @staticmethod
    def _mql_function_bodies(source: str) -> list[tuple[str, int, int]]:
        """(name, start, end) for every CTradeEngine member, in file order."""

        signature = re.compile(
            r"(?m)^   (?:bool|void|double|int|long|ulong|string|datetime)\s+(_\w+)\s*\("
        )
        marks = [(m.start(), m.group(1)) for m in signature.finditer(source)]
        bounds: list[tuple[str, int, int]] = []
        for index, (start, name) in enumerate(marks):
            end = marks[index + 1][0] if index + 1 < len(marks) else len(source)
            bounds.append((name, start, end))
        return bounds

    def test_mql_every_tp1_owner_declares_its_authority(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        bounds = self._mql_function_bodies(source)
        self.assertGreater(len(bounds), 50, "function scan found almost nothing - the scan is broken")

        # The generic R-multiple builder is the one legitimate non-owner: it only
        # runs in the else branch, when no target model claimed the first leg.
        generic = re.compile(r"p\.tp1 = p\.entry_est [+-] tp1_reward;")
        undeclared: list[str] = []
        owners = 0
        for match in re.finditer(r"\bp\.tp1\s*=", source):
            line_end = source.index("\n", match.start())
            if generic.search(source[match.start() : line_end + 1]):
                continue
            owners += 1
            enclosing = [b for b in bounds if b[1] <= match.start() < b[2]]
            self.assertEqual(1, len(enclosing), "tp1 assignment outside any member function")
            name, start, end = enclosing[0]
            if "p.tp1_from_target_model = true;" not in source[start:end]:
                undeclared.append(f"{name} (offset {match.start()})")

        self.assertGreaterEqual(owners, 5, "expected every known tp1-owning path to be scanned")
        self.assertEqual(
            [],
            undeclared,
            "every function that assigns p.tp1 must declare p.tp1_from_target_model = true, "
            "otherwise _BuildPlanPrices silently rebuilds that leg from tp1_r_multiple: "
            + ", ".join(undeclared),
        )

    def test_mql_generic_tp1_builder_cannot_move_a_route_owned_first_leg(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        start = source.index(
            "bool _BuildPlanPrices(TradePlan &p, const double entry_price, string &reject_reason)"
        )
        end = source.index("bool _BuildPlanPrices(TradePlan &p, const double entry_price)", start)
        body = source[start:end]

        guard = body.index("if(p.tp1_from_target_model && p.tp1 > 0.0){")
        rebuild = body.index("double tp1_reward = stop_dist * MathMax(0.40, p.tp1_r_multiple);")
        self.assertLess(
            guard,
            rebuild,
            "the route-owned tp1 must be handled before the generic R-multiple rebuild",
        )

        # A route-owned leg that cannot be placed fails closed; it is never relocated.
        self.assertIn('reject_reason = "target_model_tp1_below_min_reward";', body)
        self.assertIn('reject_reason = "target_model_tp1_beyond_target";', body)
        self.assertIn('reject_reason = "target_model_tp1_wrong_side_of_entry";', body)
        self.assertIn("[tp1_authority] symbol=", body)

        # The 0.65R floor must not be reachable for a route-owned leg: the only
        # MathMax(...) clamp of tp1_reward lives after the guard, in the else branch.
        self.assertEqual(
            1,
            body.count("tp1_reward = MathMax(tp1_reward, min_tp1_reward);"),
            "the min-TP1 clamp must exist exactly once, inside the generic branch",
        )
        self.assertGreater(body.index("tp1_reward = MathMax(tp1_reward, min_tp1_reward);"), rebuild)

    def test_mql_partial_route_admission_measures_its_own_first_leg(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")

        # Eligibility: a route carrying a first leg is only eligible if that leg clears
        # the floor.  Previously only the runner's RR was measured.
        self.assertIn(
            "if(c.partial_tp > 0.0 && !c.partial_meets_floor) return false;",
            source,
        )

        start = source.index("bool _SelectObstacleAwareTarget(TradePlan &p, const double stop_dist,")
        end = source.index("bool _SelectObstacleAwareTarget(TradePlan &p, const double stop_dist) {", start)
        body = source[start:end]
        # The floor is published on the plan so the AI target-candidate payload can
        # read the same number the ranking used.
        self.assertIn("p.min_tp1_reward = _MinTp1Reward(p, stop_dist);", body)
        self.assertIn("partial_reward >= p.min_tp1_reward", body)
        self.assertIn("partial_reward < full_reward", body)
        self.assertIn('p_reject = "partial_leg_below_tp1_floor";', body)
        self.assertIn("[partial_leg_gate] symbol=", body)
        # Publication must happen before any route is scored, or the ranking reads 0.
        self.assertLess(
            body.index("p.min_tp1_reward = _MinTp1Reward(p, stop_dist);"),
            body.index("for(int i=0; i<ArraySize(targets); i++){"),
        )

        # The old reason claimed to measure the partial leg while reading only the
        # runner.  Renaming it truthfully is part of the fix, so the lie must be gone.
        self.assertNotIn("partial_leg_below_floor", source)
        self.assertIn('p_reject = "partial_runner_below_floor";', body)

    def test_mql_ai_payload_never_offers_a_partial_the_engine_will_refuse(self) -> None:
        """The AI target-candidate payload must judge BOTH legs.

        It used to compute availability and feasibility from the runner alone, so a
        route whose first leg the engine cannot place was advertised with an empty
        infeasible_reason.  The model could then select it, and the plan rebuild would
        reject it -- an infrastructure rejection after a paid provider call.
        """

        source = (MQL_STAGE / "AIGateBridge.mqh").read_text(encoding="utf-8")
        self.assertIn("double capped_tp1_reward", source)
        self.assertIn("capped_tp1_reward >= p.min_tp1_reward", source)
        # An unset floor means the plan predates the fix; do not advertise the partial.
        self.assertIn("p.min_tp1_reward > 0.0 &&", source)

        # The floor and its owner must survive a state round-trip, or a reloaded plan
        # silently reverts to the permissive behaviour.
        state = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8")
        self.assertIn('JsonKVBool("tp1_from_target_model", p.tp1_from_target_model)', state)
        self.assertIn('JsonKVNum("min_tp1_reward", p.min_tp1_reward, 8)', state)
        self.assertIn('p.tp1_from_target_model = JsonGetBool(json, "tp1_from_target_model", false);', state)
        self.assertIn('p.min_tp1_reward = JsonGetNumber(json, "min_tp1_reward", 0);', state)

        start = source.index('j += "\\"partial_before_obstacle_then_liquidity\\":{";')
        end = source.index('j += "\\"synthetic_rr_fallback\\":{";', start)
        option = source[start:end]
        # available / feasible_for_tp2 / infeasible_reason must all consult the leg.
        self.assertIn('JsonKVBool("available"', option)
        self.assertIn("capped_tp1_clears_floor", option)
        self.assertEqual(
            3,
            option.count("capped_tp1_clears_floor"),
            "available, feasible_for_tp2 and infeasible_reason must each consult the first leg",
        )
        self.assertIn('"partial_leg_below_tp1_floor"', option)
        # The numbers behind the verdict travel with it, so the model can reason.
        self.assertIn('JsonKVNum("tp1_reward_price"', option)
        self.assertIn('JsonKVNum("tp1_min_required_reward"', option)

        # tp1_before_obstacle_possible is evidence the prompt leans on; it must agree.
        self.assertIn(
            "bool tp1_before_obstacle_possible = (InpAllowPartialBeforeObstacle && capped_tp > 0.0 &&\n"
            "                                           capped_rr > 0.0 && capped_tp1_clears_floor);",
            source,
        )

    def test_python_forwards_first_leg_economics_to_the_model(self) -> None:
        """_compact_target_candidates is a whitelist; new MQL keys must be added to it.

        Leaving them out would silently drop the evidence and re-open the MQL/Python
        divergence this fix exists to close.
        """

        from ai_gate import _compact_target_candidates

        payload = {
            "partial_before_obstacle_then_liquidity": {
                "available": False,
                "tp1": 1.2345,
                "rr1": 0.30,
                "tp1_reward_price": 0.00384,
                "tp1_min_required_reward": 0.00825,
                "infeasible_reason": "partial_leg_below_tp1_floor",
            }
        }
        out = _compact_target_candidates(payload)
        option = out["partial_before_obstacle_then_liquidity"]
        self.assertEqual(0.00384, option["tp1_reward_price"])
        self.assertEqual(0.00825, option["tp1_min_required_reward"])
        self.assertEqual("partial_leg_below_tp1_floor", option["infeasible_reason"])
        self.assertFalse(option["available"])

    # ------------------------------------------------------------------
    # Obstacle symmetry.  A synthetic fallback was the only route allowed to
    # measure its reward THROUGH an obstacle; every structural route was scored on
    # its clamped reward and discarded.  So "cross it for 1.05R" beat "cross it for
    # 4.75R", and the model vetoed with ai_veto_target_arbitration_incoherent:
    # "the feasible liquidity target was not selected".
    # ------------------------------------------------------------------

    def test_mql_structural_route_through_obstacle_competes_with_the_synthetic(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        start = source.index("bool _SelectObstacleAwareTarget(TradePlan &p, const double stop_dist,")
        end = source.index("bool _SelectObstacleAwareTarget(TradePlan &p, const double stop_dist) {", start)
        body = source[start:end]

        # The truncated branch must offer the FULL target marked as crossing, not only
        # the clamped route and the partial.
        self.assertIn('through_obstacle_exceeds_max_target_distance', body)
        self.assertIn('through_obstacle_below_floor', body)
        self.assertIn(
            "_RankAddTarget(ranked, targets[i].kind, candidate_target,\n"
            "                              0.0, 0.0, true, full_rr, true, severity,",
            body,
        )

        # …and it must be compared against the synthetic once no clean route survives.
        self.assertIn("int crossing_best = _PickBestCrossingRoute(ranked);", body)
        self.assertIn("ranked[crossing_best].rr > fallback_rr + _RREps()", body)
        self.assertIn("action=prefer_structural_through_same_obstacle", body)

        # Order matters: the clean-route path must still win before any of this runs.
        self.assertLess(
            body.index("if(_RankedListHasCleanRoute(ranked)){"),
            body.index("int crossing_best = _PickBestCrossingRoute(ranked);"),
        )

    def test_mql_killer_obstacle_rejects_instead_of_shipping_a_crossing_synthetic(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn("if(synthetic_severity >= InpBlockerKillSeverity){", source)

        # The branch now picks between a degenerate obstacle and a genuine killer
        # crossing, so the property under test is "this branch rejects, and a genuine
        # killer crossing is still named as such" -- not one exact spelling of the
        # assignment.  Scoping to the branch also stops these tokens from being
        # satisfied by an unrelated match elsewhere in a 15k-line file, which the
        # previous whole-file assertIn allowed.
        gate = _function_body(source, "_SelectObstacleAwareTarget")
        branch_start = gate.index("if(synthetic_severity >= InpBlockerKillSeverity){")
        branch = gate[
            branch_start : gate.index(
                "int crossing_best = _PickBestCrossingRoute(ranked);", branch_start
            )
        ]
        self.assertIn('"all_routes_cross_killer_obstacle"', branch)
        # The action token is now selected inside a ternary, so match the token itself
        # rather than the "action=..." concatenation it used to be glued to.
        self.assertIn("reject_no_route_without_killer_crossing", branch)
        self.assertIn("action=", branch)
        self.assertIn("return false;", branch)

        # A killer crossing can never be selected as a target either.
        picker_start = source.index("int _PickBestCrossingRoute(const TargetRankCandidate &list[]) const {")
        picker_end = source.index("\n   }", picker_start)
        picker = source[picker_start:picker_end]
        self.assertIn("if(list[i].obstacle_severity >= InpBlockerKillSeverity) continue;", picker)
        self.assertIn("if(!list[i].crosses_obstacle) continue;", picker)
        self.assertIn("if(!_TargetRankEligible(list[i])) continue;", picker)

        # The reject must precede the preference step: never prefer a killer route.
        self.assertLess(
            source.index("if(synthetic_severity >= InpBlockerKillSeverity){"),
            source.index("int crossing_best = _PickBestCrossingRoute(ranked);"),
        )

    def test_mql_obstacle_kind_and_severity_are_published_together(self) -> None:
        """obstacle_strength_features was empty in 6 of 10 live requests.

        It had a single writer -- the branch that runs when an obstacle sits before the
        liquidity target -- so a plan with no valid liquidity target shipped
        obstacle_kind with the severity blank, asking the model to classify an
        obstacle's strength with the strength missing.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn("void _PublishObstacleEvidence(TradePlan &p, const string obstacle_kind,", source)

        helper_start = source.index("void _PublishObstacleEvidence(TradePlan &p, const string obstacle_kind,")
        helper_end = source.index("\n   }", helper_start)
        helper = source[helper_start:helper_end]
        for field in ("p.obstacle_kind", "p.obstacle_price", "p.obstacle_distance_r",
                      "p.obstacle_tf", "p.obstacle_strength_features"):
            self.assertIn(field, helper, f"{field} must be published with the obstacle")
        self.assertIn("_ObstacleSeverityClass(severity)", helper)

        # Every site that publishes an obstacle must go through the helper, so the kind
        # and its severity can never travel apart again.
        self.assertGreaterEqual(source.count("_PublishObstacleEvidence(p, "), 3)
        self.assertEqual(
            1,
            source.count('p.obstacle_strength_features = "severity='),
            "obstacle_strength_features must have exactly one writer",
        )

    def test_mql_min_tp1_reward_is_defined_once_for_selection_and_pricing(self) -> None:
        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        # One definition, so route selection and price building cannot drift apart.
        # The drift is precisely what let selection choose a partial the builder
        # then had to move past the obstacle the route existed to respect.
        # "geometry_stop_dist * 0.65" contains "stop_dist * 0.65", so a reintroduced
        # bare stop_dist floor anywhere else takes this count to 2.
        self.assertEqual(
            1,
            source.count("stop_dist * 0.65"),
            "the TP1 geometry floor must have a single definition, in _MinTp1GeometryReward",
        )
        self.assertEqual(1, source.count("geometry_stop_dist * 0.65"))
        definition = source.index(
            "double _MinTp1GeometryReward(const TradePlan &p, const double live_stop_dist) const"
        )
        floor = source.index("stop_dist * 0.65")
        self.assertLess(definition, floor)
        self.assertLess(
            floor, definition + 400, "the 0.65R floor must live inside _MinTp1GeometryReward"
        )
        # _MinTp1Reward stays the one composed floor both callers publish and rank on.
        self.assertIn(
            "return MathMax(_MinTp1GeometryReward(p, stop_dist), _MinTp1SpreadReward(p));", source
        )
        self.assertGreaterEqual(source.count("_MinTp1Reward(p, stop_dist)"), 2)

    def test_mql_tp1_floor_geometry_is_anchored_to_the_approved_stop(self) -> None:
        """Regression: three of eleven 2026-09-05 approvals died on this.

        The floor has two halves.  ``spread * InpMinTP1SpreadMult`` asks whether the
        broker can place the leg -- a live question.  The 0.65 term asks whether the
        leg is a meaningful fraction of the risk -- a property of the plan.  Measuring
        the second against the *rebuilt* stop made the floor rise from the same
        authorized drift that shrank the frozen leg's reward, so a plan needed
        ``tp1_reward >= 0.65R + 1.65d`` (TP1 at 1.31R for the permitted d = 0.4R) to
        survive its own execution.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")

        # The geometry half must go through the anchor helper, and the helper must
        # prefer the assessed stop for a locked plan.
        self.assertIn(
            "double _Tp1FloorStopDistance(const TradePlan &p, const double live_stop_dist) const {",
            source,
        )
        anchor = source.index("double _Tp1FloorStopDistance(const TradePlan &p")
        anchor_end = source.index("double _MinTp1Reward(const TradePlan &p", anchor)
        anchor_body = source[anchor:anchor_end]
        self.assertIn("p.assessed_plan_locked && p.assessed_stop_distance > 0.0", anchor_body)
        self.assertIn("return p.assessed_stop_distance;", anchor_body)
        self.assertIn("return live_stop_dist;", anchor_body)

        # The anchor applies to the geometry half only; the spread half is a live
        # broker constraint and must stay live in both states.
        geo_start = source.index("double _MinTp1GeometryReward(const TradePlan &p")
        geo_end = source.index("double _MinTp1SpreadReward(const TradePlan &p", geo_start)
        geometry = source[geo_start:geo_end]
        self.assertIn("_Tp1FloorStopDistance(p, live_stop_dist)", geometry)
        self.assertIn("geometry_stop_dist * 0.65", geometry)

        spread_start = source.index("double _MinTp1SpreadReward(const TradePlan &p")
        spread_end = source.index("double _MinTp1Reward(const TradePlan &p", spread_start)
        spread = source[spread_start:spread_end]
        self.assertIn("_CurrentSpreadPrice(p.symbol) * InpMinTP1SpreadMult", spread)
        self.assertNotIn("assessed_", spread)

        # Anchoring the stop alone is not enough: the contract also authorises the
        # ENTRY to drift, and a frozen route-owned TP1 loses reward from that drift
        # by itself.  GBPJPY's leg was 0.142 against a 0.115 plan-time floor and
        # became 0.106 purely because the entry moved 212.693 -> 212.657.
        leg_start = source.index("double _Tp1GeometryLegReward(const TradePlan &p")
        leg_end = source.index("void _RankAddTarget(", leg_start)
        leg = source[leg_start:leg_end]
        self.assertIn("p.assessed_plan_locked && p.assessed_tp1 > 0.0 && p.assessed_entry > 0.0", leg)
        self.assertIn("_RewardToTarget(p.is_buy, p.assessed_entry, p.assessed_tp1)", leg)
        self.assertIn("return live_leg_reward;", leg)

        # Each half must be compared against the quantity it is about, and the
        # placement half against the LIVE leg.
        build_start = source.index(
            "bool _BuildPlanPrices(TradePlan &p, const double entry_price, string &reject_reason)"
        )
        build_end = source.index("bool _BuildPlanPrices(TradePlan &p, const double entry_price)", build_start)
        build = source[build_start:build_end]
        self.assertIn("double geometry_leg_reward = _Tp1GeometryLegReward(p, route_tp1_reward);", build)
        self.assertIn("bool geometry_fail = (geometry_floor > 0.0 && geometry_leg_reward < geometry_floor);", build)
        self.assertIn("bool spread_fail   = (spread_floor > 0.0 && route_tp1_reward < spread_floor);", build)
        # The live-price safety checks must remain on live prices.
        self.assertIn('reject_reason = "target_model_tp1_wrong_side_of_entry";', build)
        self.assertIn('reject_reason = "target_model_tp1_beyond_target";', build)
        self.assertIn("double route_tp1_reward = _RewardToTarget(p.is_buy, p.entry_est, p.tp1);", build)

        # The anchor is worthless if a reloaded plan comes back unlocked, so the lock
        # and the stop distance must both survive a state round-trip.
        state = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8")
        for field in ("assessed_plan_locked", "assessed_stop_distance", "assessed_tp1"):
            with self.subTest(field=field):
                self.assertIn(f'"{field}", p.{field}', state)
                self.assertIn(f'p.{field} = JsonGet', state)

    def test_mql_assessed_plan_lock_survives_a_state_round_trip(self) -> None:
        """A reloaded approved plan used to come back UNLOCKED.

        Without ``assessed_plan_locked`` the fingerprint check fell through to the
        legacy branch and ``_ApplyAssessedTargetUnderContract`` never ran, so the live
        rebuild was free to re-derive the target -- the exact substitution the
        execution adjustment contract exists to make unrepresentable.
        """

        state = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8")
        for field in (
            "assessed_plan_locked",
            "assessed_tp_model",
            "assessed_selected_target_identity",
            "assessed_selected_target_price",
            "assessed_stop_distance",
        ):
            with self.subTest(field=field):
                self.assertIn(f'"{field}", p.{field}', state)
                self.assertIn(f'p.{field} = JsonGet', state)
        # A record written before the field existed must not leave the floor unanchored.
        self.assertIn(
            "p.assessed_stop_distance = MathAbs(p.assessed_entry - p.assessed_sl);", state
        )

    def test_mql_spread_guard_is_calibrated_per_symbol(self) -> None:
        """Regression: a raw point count cannot guard a mixed-scale universe.

        ``InpMaxSpreadTicks=100`` is 0.001 on EURUSD (inert) and 1.00 index point on
        #Japan225 -- an eighth of that symbol's normal spread, so unreachable by
        construction.  The 2026-09-05 replay lost its only index approval to 213
        rejections at a constant ``ticks=800.0``, a spread worth 1.4% of the planned
        risk against a 22% allowance.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")

        # The raw comparison is gone from the placement path.
        self.assertNotIn("spread too wide ticks=", source)
        self.assertNotIn("spread_ticks > InpMaxSpreadTicks", source)

        start = source.index(
            "double _MaxSpreadPriceForSymbol(const string symbol, const double reference_price,"
        )
        end = source.index("bool _SpreadWithinLimits(", start)
        cap = source[start:end]
        # Both units present, and the price fraction is the floor under the ceiling.
        self.assertIn("InpMaxSpreadTicks > 0 ? (double)InpMaxSpreadTicks * point : 0.0", cap)
        self.assertIn("reference_price * InpMaxSpreadPriceFrac", cap)
        self.assertIn("cap_source", cap)

        gate_start = source.index("bool _SpreadWithinLimits(")
        gate_end = source.index("double _LiveEntryPrice(", gate_start)
        gate = source[gate_start:gate_end]
        # The risk guard -- the economically meaningful one -- is untouched.
        self.assertIn("planned_risk * InpMaxSpreadRiskFrac", gate)
        self.assertIn("[spread_gate] symbol=", gate)
        # It must report every unit it judges in; none was logged before.
        for field in (
            "spread_price=",
            "spread_ticks=",
            "spread_frac_of_price=",
            "spread_frac_of_risk=",
            "abs_cap_price=",
            "abs_cap_source=",
            "risk_cap_price=",
        ):
            with self.subTest(field=field):
                self.assertIn(field, gate)

        config = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8")
        self.assertIn("input double InpMaxSpreadPriceFrac", config)
        self.assertIn("input int    InpMaxPersistentSpreadAttempts", config)

    def test_mql_a_constant_spread_is_not_classified_transient(self) -> None:
        """213 rejections at exactly ticks=800.0 were all labelled TRANSIENT."""

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")

        start = source.index("string _ClassifySpreadFailure(const TradePlan &p, const double spread) const {")
        end = source.index("string _ClassifyExecutionFailure(", start)
        body = source[start:end]
        self.assertIn("p.execution_spread_unchanged_attempts", body)
        self.assertIn("InpMaxPersistentSpreadAttempts", body)
        self.assertIn("return EXEC_FAIL_PERMANENT_BROKER;", body)
        self.assertIn("return EXEC_FAIL_TRANSIENT_SPREAD;", body)

        # A transient spread must be suppressed while the SPREAD is unchanged; the
        # generic state fingerprint moves with any quote drift, which is why one
        # blocked plan produced 213 prechecks and 5,597 duplicate attempts.
        suppress_start = source.index("bool _SuppressExecutionRetry(TradePlan &p, string &suppress_reason) {")
        suppress_end = source.index("void _RecordExecutionFailure(", suppress_start)
        suppress = source[suppress_start:suppress_end]
        self.assertIn("p.execution_failure_class == EXEC_FAIL_TRANSIENT_SPREAD", suppress)
        self.assertIn("unchanged_spread_after_", suppress)

        # Classification must read THIS attempt's detector verdict, not the previous
        # attempt's stored class -- the stored one made the first label permanent.
        classify = source[
            source.index("string _ClassifyExecutionFailure(") : source.index(
                "string _ExecutionStateFingerprint("
            )
        ]
        self.assertIn("m_last_execution_failure_class", classify)
        self.assertNotIn("return p.execution_failure_class;", classify)

    def test_mql_execution_sequence_gate_respects_the_setup_family(self) -> None:
        """Regression: #Germany40 touched its entry zone 3,340 times, 0 attempts.

        The watchlist gate demanded a closed sweep+displacement+BOS sequence without
        consulting the family.  A micro_continuation_fvg has no sweep by definition,
        so ``tier_b_execution_allowed`` could never apply and the gate waited forever
        for an event the family excludes.  _DeterministicExecutionGate has guarded the
        same checks with _FamilyRequiresFullPO3Sequence since the BOS contract gate.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        marker = source.index("bool watch_requires_full_po3 = _FamilyRequiresFullPO3Sequence(p);")
        window = source[marker : marker + 4000]
        self.assertIn("bool family_sequence_hold = (watch_requires_full_po3", window)
        self.assertIn("(needs_live_sequence && !tier_b_execution_allowed)", window)
        # A family outside the full-PO3 contract is held on its own evidence.
        self.assertIn("(!p.po3.has_displacement || p.po3.sweep_running)", window)
        # The absolute override is preserved.
        self.assertIn(
            "if(ok && (InpRequireConfirmedPO3ForExecution || family_sequence_hold) && watch_state != PO3_CONFIRMED){",
            window,
        )
        self.assertIn("[execution_sequence_gate] symbol=", window)
        # The old family-blind condition must be gone.
        self.assertNotIn(
            "if(ok && (InpRequireConfirmedPO3ForExecution || (needs_live_sequence && "
            "!tier_b_execution_allowed)) && watch_state != PO3_CONFIRMED){",
            source,
        )

        # The PROMOTION criterion must be reachable by the family being promoted too.
        # Holding a micro continuation and then demanding a closed sweep -> displacement
        # -> BOS chain to release it just moves the same unsatisfiable requirement one
        # step later.
        promote = source[marker : marker + 6000]
        self.assertIn("if(watch_requires_full_po3 || InpRequireConfirmedPO3ForExecution){", promote)
        # The full-PO3 branch keeps the closed BOS chain.
        self.assertIn("live_confirm_po3.has_bos &&", promote)
        # The family branch must not require a BOS, and must still require the
        # evidence the family's own contract names.
        family_branch = promote[promote.index("} else {", promote.index("if(watch_requires_full_po3 ||")) :]
        family_branch = family_branch[: family_branch.index("if(!confirmed_now)")]
        self.assertNotIn("has_bos", family_branch)
        self.assertNotIn("t_sweep", family_branch)
        self.assertIn("live_confirm_po3.has_displacement", family_branch)
        self.assertIn("!live_confirm_po3.sweep_running", family_branch)
        self.assertIn("fvg_after_disp_ok", family_branch)

    def test_mql_execution_reject_reasons_stay_low_cardinality(self) -> None:
        """A reason string carrying measured prices shatters the funnel reject table.

        Every reject reason is bucketed by name; embedding the spread and the cap
        would give each rejection a unique bucket and make the table useless -- the
        same "a correct decision made undiagnosable by its own log line" failure this
        engine has already fixed three times.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn('reject_reason = "spread_above_symbol_cap";', source)
        self.assertNotIn('reject_reason = "spread_above_symbol_cap spread="', source)
        # The evidence lives in the journal line instead.
        self.assertIn("[spread_gate] symbol=", source)

    def test_mql_broker_cost_history_is_not_rescanned_per_plan(self) -> None:
        """The plan path must not walk the deal history once per plan build.

        BrokerCostEstimatePerLot() runs HistorySelect() across
        InpBrokerCostHistoryDays and then walks every deal in the selection.
        _EstimateExecutionCosts() calls it once per plan build, so one scan cycle of
        the 2026.08.03 replay called it 2,215 times and the tester ran at 0.57x real
        time -- slower than the market it was simulating.  The expensive computation
        must therefore be reachable only through a memo.
        """

        risk = (MQL_STAGE / "Risk.mqh").read_text(encoding="utf-8")

        # The scan itself still exists -- this is a caching fix, not a removal of the
        # broker-cost model.
        uncached = _function_body(risk, "_BrokerCostEstimatePerLotUncached")
        self.assertIn("HistorySelect(from, now)", uncached)
        self.assertIn("HistoryDealsTotal()", uncached)

        # ... and it is the ONLY place in Risk.mqh that selects history, so no caller
        # can reach the full scan without passing the memo.
        self.assertEqual(
            1,
            risk.count("HistorySelect(from, now)"),
            "the deal-history scan must exist exactly once, inside the uncached helper",
        )

        cached = _function_body(risk, "BrokerCostEstimatePerLot")
        self.assertIn("_bce_hits++", cached)
        self.assertIn("InpBrokerCostCacheSeconds", cached)
        # A memo that never expires and a memo that ignores new deals are both wrong:
        # both freshness conditions must be part of the hit test.
        self.assertIn("_bce_epoch[idx] == _bce_current_epoch", cached)
        self.assertIn("(now - _bce_computed_at[idx]) < (datetime)InpBrokerCostCacheSeconds", cached)
        # Time running backwards (a new tester pass) must miss, not serve a future entry.
        self.assertIn("now >= _bce_computed_at[idx]", cached)

    def test_mql_broker_cost_memo_is_dropped_before_the_deal_is_filtered(self) -> None:
        """A new deal must invalidate the cost memo whatever kind of deal it is.

        Entry deals carry commission; exit deals carry commission, swap and fee. The
        handler's magic and entry-kind filters answer a different question -- whether
        this deal opens one of our positions -- so invalidating after them would keep
        serving a cost estimate computed before the trade that changed it.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        body = _function_body(source, "HandleTradeTransaction")
        self.assertIn("BrokerCostHistoryInvalidate();", body)
        self.assertLess(
            body.index("BrokerCostHistoryInvalidate();"),
            body.index("DEAL_MAGIC"),
            "the memo must be dropped before the magic filter, not after it",
        )
        self.assertLess(
            body.index("BrokerCostHistoryInvalidate();"),
            body.index("DEAL_ENTRY_IN"),
            "the memo must be dropped before the entry-kind filter, not after it",
        )

    def test_mql_repeat_journal_lines_are_counted_not_silently_dropped(self) -> None:
        """Suppressing an identical diagnostic is only honest if it is counted.

        [broker_cost_estimate] printed 2,215 lines in one scan cycle -- 10.8% of the
        journal's bytes -- restating 13 distinct facts, one per symbol.  Suppressing
        the repeats is correct; suppressing them without saying so would delete
        evidence about how often the engine looked.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        dedup = _function_body(source, "_JournalOnChange")
        self.assertIn("m_jd_suppressed[idx]++", dedup)
        # A line that CHANGED must always print, or the memo would hide real movement.
        self.assertIn("m_jd_last[idx] == msg", dedup)

        summary = _function_body(source, "_LogJournalDedupSummary")
        self.assertIn("suppressed_identical=", summary)

        # The counts have to actually reach the log, not just exist in memory.
        final_summary = _function_body(source, "_LogFinalSummary")
        self.assertIn("_LogJournalDedupSummary();", final_summary)
        self.assertIn("broker_cost_cache_hits_total=", final_summary)
        self.assertIn("broker_cost_compute_seconds=", final_summary)

        # The high-frequency line is the one routed through the deduplicator.
        costs = _function_body(source, "_EstimateExecutionCosts")
        self.assertIn('_JournalOnChange("broker_cost_estimate|" + p.symbol,', costs)

    def test_mql_journal_detail_level_never_hides_authority_evidence(self) -> None:
        """Grading the journal must not silence the lines the fixes are verified by.

        Route detail is graded to level 2 because [target_rank] alone is 51.7% of the
        journal's bytes and a full replay writes ~4.8 GB.  The gate and authority lines
        are the acceptance criteria for every fix in this engine, so they must stay at
        the default level -- a log that is cheap and proves nothing is not an
        improvement over a log that is expensive.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")

        # Demoted: the measured high-volume producers, guarded before the message is
        # built so the concatenation is skipped too.
        self.assertIn("_JournalDetailEnabled(2)", _function_body(source, "_LogTargetRank"))
        self.assertIn("_JournalDetailEnabled(2)", _function_body(source, "_LogTargetCandidates"))
        self.assertIn("_JournalDetailEnabled(2)", _function_body(source, "_FinalizePlanEconomics"))
        self.assertIn("_JournalDetailEnabled(2)", _function_body(source, "_RefreshTargetFeasibility"))

        # The authoritative field is still computed when the diagnostic is suppressed.
        economics = _function_body(source, "_FinalizePlanEconomics")
        self.assertLess(
            economics.index("p.net_reward_after_cost_r = _CanonicalNetRewardAfterCostR(p);"),
            economics.index("_JournalDetailEnabled(2)"),
        )

        # Blast radius is pinned: exactly the six demoted sites and one graded emit.
        # A seventh has to change this test, and therefore has to be justified.
        self.assertEqual(6, source.count("_JournalDetailEnabled(2)"))
        self.assertEqual(1, source.count("_JournalDetail(2, "))

        # NOT demoted: every line an acceptance criterion is read from.
        for tag in (
            "[tp1_authority]",
            "[spread_gate]",
            "[execution_sequence_gate]",
            "[obstacle_crossing_gate]",
            "[setup_floor_gate]",
            "[bos_contract_gate]",
            "[stop_distance_cap]",
            "[semantic_plan_match]",
            "[execution_adjustment_validation]",
            "[final_summary]",
            "[journal_dedup]",
        ):
            with self.subTest(tag=tag):
                self.assertIn(f'_Journal("{tag}', source)
                self.assertNotIn(f'_JournalDetail(2, "{tag}', source)

    def test_mql_trade_meta_is_not_rewritten_when_its_content_is_unchanged(self) -> None:
        """Holding a position must not cost a full meta rewrite per simulated second.

        MaintainPositions() runs once per simulated second per managed position and
        writes the trade meta twice -- once in the main loop, once in the penalty loop.
        One meta is 1,737 fields / 76,084 characters, _WriteTradeMeta() fans it out in
        ten write calls (seven distinct files for a filled position -- the ticket and
        position-id aliases collapse), and CFileBus::WriteText() costs six filesystem
        operations per path.  On the 2026.08.03 replay two consecutive writes of
        trade_position_2.json
        were byte-identical and simulated time advanced 15 seconds in 45 seconds of wall
        clock -- 0.333x real time, against 359x before the position opened.  So every
        alias write must pass through the content memo.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        writer = _function_body(source, "_WriteTradeMeta")

        # The alias fan-out is unchanged -- this is a write memo, not a reduction of the
        # set of paths a consumer may look the trade up by.
        self.assertEqual(10, writer.count("_WriteTradeMetaFile("))
        self.assertNotIn(
            "m_bus.WriteText(",
            writer,
            "every alias write must go through the memo, not straight to the bus",
        )

        gate = _function_body(source, "_WriteTradeMetaFile")
        self.assertIn("m_tm_writes_skipped_identical++", gate)
        # The comparison must ignore the tick watermark.  PenaltyWatcher stamps
        # latest_observed_tick_time / _msc on every tick and _ApplyPenaltyStateToMeta
        # copies them into the meta, so comparing the raw document makes every write
        # look material and the memo can never coalesce -- measured directly: two
        # consecutive writes differed in exactly those two fields and nothing else.
        # The key is now derived once by the fan-out and handed down (every alias gets
        # the identical document, so ten derivations could only ever agree), which is
        # why this asserts the derivation in the caller and the *use* of the derived
        # value here.  The property under test is unchanged: what the memo compares is
        # the blanked document, never the raw one.
        self.assertIn("_TradeMetaComparisonKey(j)", writer)
        self.assertIn("m_tm_json[idx] == comparison_key", gate)
        self.assertIn("_TradeMetaMemoStore(path, comparison_key)", gate)
        self.assertNotIn("m_tm_json[idx] == json", gate)

        key_fn = _function_body(source, "_TradeMetaComparisonKey")
        for field in ("latest_observed_tick_time", "latest_observed_tick_msc"):
            with self.subTest(field=field):
                self.assertIn(f'_BlankJsonNumber(key, "{field}")', key_fn)

        # Ignoring the watermark must not let it go stale without bound.  Its source of
        # truth is PenaltyState, persisted every 15 simulated seconds, so the mirror is
        # refreshed on the same interval and a material change still writes at once.
        self.assertIn("_TradeMetaWatermarkDue(idx)", gate)
        self.assertIn(
            "input int    InpTradeMetaWatermarkSeconds = 15;",
            (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8"),
        )
        # Skipping is only sound while the file is actually still there: an external
        # delete must be repaired by the next write, not masked by the memo.
        self.assertIn("m_bus.Exists(path)", gate)
        # Exists() must be a real existence check.  _PathExists() answers the same
        # question by reading the whole 152 KB document back, which would reintroduce
        # the cost this memo exists to remove.
        bus = (MQL_STAGE / "FileBus.mqh").read_text(encoding="utf-8")
        self.assertIn("FileIsExist(rel_path, FILE_COMMON)", _function_body(bus, "Exists"))
        self.assertNotIn("_PathExists(path)", gate)

    def test_mql_trade_meta_parse_memo_only_serves_a_parse_of_the_bytes_on_disk(self) -> None:
        """A cached plan must be the same value a reparse would produce, never a guess.

        JsonLite's key lookup rescans from position 0 and materializes a temporary
        string for every quoted token it passes, so parsing 1,737 fields out of 76,084
        characters is quadratic -- and MaintainPositions() does it twice per simulated
        second.  Memoizing the parse is sound only because ParseTradePlanJson() is
        deterministic, which means the memo must be keyed on the exact bytes just read
        and must be dropped whenever those bytes are rewritten.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        reader = _function_body(source, "_ReadTradeMetaPath")

        # The file is still read every time; only the reparse of identical bytes is
        # skipped, so a writer outside this process is still observed.
        self.assertIn("m_bus.ReadText(path, txt)", reader)
        self.assertIn("m_tm_json[idx] == key", reader)
        self.assertIn("m_tm_parsed[idx]", reader)
        self.assertIn("m_tm_parses_skipped_identical++", reader)

        # The memo compares on the watermark-blanked document, so a hit must read the
        # two blanked fields back out of the bytes on disk.  Without this the caller
        # would receive an older tick time than the file it just read actually holds,
        # which is a wrong value rather than a cheaper one.
        for field in ("latest_observed_tick_time", "latest_observed_tick_msc"):
            with self.subTest(field=field):
                self.assertIn(f'JsonGetNumber(txt, "{field}", 0)', reader)
                self.assertLess(
                    reader.index(f'JsonGetNumber(txt, "{field}", 0)'),
                    reader.index("m_tm_parses_skipped_identical++"),
                    "the watermark must be restored before the hit is reported",
                )
        self.assertLess(
            reader.index("m_bus.ReadText(path, txt)"),
            reader.index("m_tm_parses_skipped_identical++"),
            "the memo may only answer after the bytes on disk have been read back",
        )

        # Every real write invalidates the stored parse, so a plan can never outlive
        # the bytes it was parsed from.
        # Matched loosely on whitespace: the invariant is that the store clears the
        # parsed flag, not how the assignment happens to be aligned.
        self.assertRegex(
            _function_body(source, "_TradeMetaMemoStore"),
            r"m_tm_parsed\[slot\]\s*=\s*false;",
        )
        # The stored value is the blanked comparison key the caller derived, so the
        # slot the parse memo later matches against is the same string the write memo
        # compared -- the two memos share one notion of "same document".
        self.assertIn(
            "_TradeMetaMemoStore(path, comparison_key);",
            _function_body(source, "_WriteTradeMetaFile"),
        )

    def test_mql_trade_meta_memo_is_disableable_and_its_cost_is_measured(self) -> None:
        """Both memos must be falsifiable at runtime and must report what they saved.

        A memo that cannot be turned off cannot be proven to be the thing that fixed
        the run, and a memo that reports nothing turns the next regression back into an
        inference from gaps between journal lines.
        """

        config = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8")
        self.assertIn("input int    InpTradeMetaMemoEntries = 256;", config)

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        # 0 restores per-call write and reparse on both paths.
        self.assertIn("InpTradeMetaMemoEntries > 0", _function_body(source, "_WriteTradeMetaFile"))
        self.assertIn("InpTradeMetaMemoEntries > 0", _function_body(source, "_ReadTradeMetaPath"))

        # The stage that collapsed throughput is timed at its own entry point.
        maintain = _function_body(source, "MaintainPositions")
        self.assertIn("GetMicrosecondCount()", maintain)
        self.assertIn("_MaintainPositionsBody();", maintain)

        summary = _function_body(source, "_LogFinalSummary")
        for field in (
            "trade_meta_writes_total=",
            "trade_meta_writes_skipped_identical=",
            "trade_meta_parses_total=",
            "trade_meta_parses_skipped_identical=",
            "trade_meta_parse_us_per_call=",
            "maintain_positions_calls_total=",
            "maintain_positions_us_per_call=",
        ):
            with self.subTest(field=field):
                self.assertIn(field, summary)

    def test_mql_trade_meta_serialization_is_reserved_and_attributed_per_scan(self) -> None:
        """The document must be built once into a reserved buffer, and timed separately.

        PlanToJson appends roughly 1,700 times to build a ~76,000 character document.
        Without a reserve every append can reallocate and copy the whole buffer, so one
        trade meta is quadratic in its own length -- and the position-maintenance loop
        builds two of them per simulated second, before the write memo is even allowed to
        decide the bytes are unchanged. The memo cannot remove that cost because it needs
        the characters in order to compare them, so the reserve is the only thing standing
        between a held position and a replay that never finishes.

        The serialize clock is asserted separately from the write and parse clocks: a
        single 'maintain positions is slow' number cannot say which of the three to fix,
        which is what turned the first diagnosis of this stall into a guess.
        """

        state = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8")
        plan_to_json = _function_body(state, "PlanToJson")
        self.assertIn("StringReserve(j, 131072);", plan_to_json)
        # A reserve taken after the appends have already run reserves nothing.
        self.assertLess(
            plan_to_json.index("StringReserve(j, 131072);"),
            plan_to_json.index("j += "),
            "StringReserve must precede the first append or the buffer has already grown",
        )

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        writer = _function_body(source, "_WriteTradeMeta")
        self.assertIn("m_state.TradePlanToJson(meta);", writer)
        self.assertIn("m_tm_serialize_us +=", writer)
        self.assertIn("m_tm_serializes++;", writer)
        # The clock must wrap the serialize itself, not the whole fan-out, or the write
        # cost would be charged to the serializer.
        self.assertLess(
            writer.index("m_state.TradePlanToJson(meta);"),
            writer.index("m_tm_serialize_us +="),
        )
        self.assertLess(
            writer.index("m_tm_serialize_us +="),
            writer.index("_WriteTradeMetaFile("),
        )

        perf = _function_body(source, "_LogTradeMetaPerf")
        for field in (
            "serializes=",
            "serialize_seconds=",
            "serialize_us_per_call=",
            "write_seconds=",
            "parse_seconds=",
            "maintain_positions_seconds=",
        ):
            with self.subTest(field=field):
                self.assertIn(field, perf)

        # Emitted per scan, not only at shutdown: a counter that prints once the run has
        # ended cannot be read while the run is still deciding whether it will ever end.
        self.assertIn('_LogTradeMetaPerf("scan");', _function_body(source, "_LogSetupFunnel"))
        self.assertIn('_LogTradeMetaPerf("final_summary");', _function_body(source, "_LogFinalSummary"))

    def test_mql_json_key_index_is_identity_checked_and_falsifiable(self) -> None:
        """The key index must be a speed change only, and provably so.

        JsonLite used to locate a key by rescanning the document from position 0 and
        materializing a temporary string for every quoted token on the way. One
        ~76,000 character trade meta holds ~800 keys and StateStore reads all of them
        from the same string, so a single document cost ~800 full scans -- measured at
        505 ms, twice per simulated second for every open position. The index removes
        the rescan.

        A parser that is faster but answers differently is not a fix, so the shape that
        makes the index equivalent is asserted here rather than left to review:

          * the resident document is confirmed by a FULL character comparison, never by
            a hash or a length alone -- a fingerprint collision would hand a caller a
            different document's field, and this parses live position state;
          * the hash only chooses a bucket; membership is still a full key comparison;
          * the build stops at the exact character where the scan aborted, so keys past
            a malformed string stay unreachable on both paths;
          * the original scan survives as the small-document path and as the definition
            the index has to reproduce.
        """

        source = (MQL_STAGE / "JsonLite.mqh").read_text(encoding="utf-8")

        # The pre-index locator is still the implementation for small documents.
        self.assertIn("bool _JsonScanLocateKeyValue(", source)
        dispatcher = _function_body(source, "_JsonLocateKeyValue")
        self.assertIn("_JsonScanLocateKeyValue(json, key, value_pos)", dispatcher)
        self.assertIn("JSON_INDEX_MIN_CHARS", dispatcher)

        # Identity of the resident document: length is only a pre-check.
        self.assertIn("_json_index_doc != json", dispatcher)
        self.assertLess(
            dispatcher.index("StringLen(_json_index_doc) != len"),
            dispatcher.index("_json_index_doc != json"),
            "the O(1) length check must precede the full comparison, not replace it",
        )

        # The hash picks a bucket; it never decides two keys are equal.
        finder = _function_body(source, "_JsonIndexFindEntry")
        self.assertIn("_json_index_key[e] == key", finder)

        # Truncation equivalence: the build must stop where the scan stopped.
        builder = _function_body(source, "_JsonIndexBuild")
        self.assertIn("if(!_JsonReadString(json, i, token, end_pos)) break;", builder)
        # First occurrence wins, exactly as the scan returned it.
        self.assertIn("_JsonIndexFindEntry(token) < 0", builder)
        # The scan's "value must be inside the document" test belongs at lookup time.
        self.assertIn("value_pos < len", dispatcher)

        # The unescaped fast path must not become a second definition of an escape.
        reader = _function_body(source, "_JsonReadString")
        self.assertIn("if(has_escape) break;", reader)
        self.assertIn("if(!has_escape) return false;", reader)

        # The equivalence itself is proven by a run, not by this file. The proof
        # carries verbatim copies of the pre-index functions; if they are ever deleted
        # or quietly re-pointed at the shipped ones, it proves nothing.
        selftest = (
            ROOT.parent / "MT5_PO3_Codex Experts" / "PO3_JsonIndexSelfTest.mq5"
        ).read_text(encoding="utf-8")
        self.assertIn("bool _RefJsonLocateKeyValue(", selftest)
        self.assertIn("bool _RefJsonReadString(", selftest)
        self.assertIn("_RefJsonReadString(json, i, token, end_pos)", selftest)
        for case in (
            "_CaseLargeFlat",
            "_CaseSmallDocumentKeepsScanPath",
            "_CaseDuplicateKeys",
            "_CaseEscapedKeysAndValues",
            "_CaseUnterminatedStringMidDocument",
            "_CaseKeyWithNoValueAtEnd",
            "_CaseInterleavedLargeDocuments",
            "_CaseAccessorsAgree",
            "_CaseRealTradeMeta",
        ):
            with self.subTest(case=case):
                self.assertIn(case + "();", selftest)

    def test_mql_position_maintenance_cost_is_attributed_to_named_segments(self) -> None:
        """The stage must report where its own time went, segment by segment.

        The first measurement of this stall attributed 30% of the cost and left 66%
        unexplained, which is not a diagnosis. Each named step of the loop carries its
        own clock so the remainder is the loop itself and nothing else, and so a future
        regression here names the step instead of restarting the investigation.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        body = _function_body(source, "_MaintainPositionsBody")
        for counter in ("m_mp_load_us", "m_mp_meta_us", "m_mp_penalty_us", "m_mp_tail_us"):
            with self.subTest(counter=counter):
                self.assertIn(counter + " +=", body)

        # The loader and the writer are the two calls that dominate the stage, and both
        # are reached twice per pass -- once for the position and once for the penalty
        # state -- so each site has to be timed.
        self.assertEqual(2, body.count("m_mp_load_us +="))
        self.assertEqual(2, body.count("m_mp_meta_us +="))

        perf = _function_body(source, "_LogTradeMetaPerf")
        for field in (
            "mp_load_seconds=",
            "mp_meta_seconds=",
            "mp_penalty_seconds=",
            "mp_tail_seconds=",
            "mp_other_seconds=",
        ):
            with self.subTest(field=field):
                self.assertIn(field, perf)
        # The remainder is derived, never counted separately, so the segments and the
        # total can never drift apart.
        self.assertIn("m_mp_us - m_mp_load_us - m_mp_meta_us", perf)

    def test_mql_penalty_state_merge_never_unlatches_a_first_observation(self) -> None:
        """PenaltyWatcher may add a first-observation stamp; it may never erase one.

        PenaltyWatcher and _UpdateAnalyticsSnapshot measure the same excursion against
        different risk distances, so one can still read "threshold not reached" after
        the other has latched the crossing. While _ApplyPenaltyStateToMeta copied the
        watcher's value straight over the top, the erased stamp was immediately
        re-latched to "now" further down the same pass, and the trade meta alternated
        between 0 and a timestamp forever -- a document that never repeats, so the
        content memo could not skip a single one of the seven alias writes.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        body = _function_body(source, "_ApplyPenaltyStateToMeta")

        for field in (
            "first_0_25r_time",
            "first_0_50r_time",
            "first_adverse_threshold_time",
        ):
            with self.subTest(field=field):
                self.assertIn(f"meta.{field} =", body)
                self.assertRegex(
                    body,
                    rf"meta\.{field}\s*=\s*(?:\n\s*)?_EarliestObservation\(",
                )
                # The defect itself: a bare copy of the watcher's value.
                self.assertIsNone(
                    re.search(rf"meta\.{field}\s*=\s*state\.", body),
                    f"{field} is overwritten from PenaltyState instead of merged",
                )

        # The excursion prices are the same defect one field over: _UpdateAnalyticsSnapshot
        # ratchets them outward, so a lagging copy from the watcher flips them back.
        for field, favourable in (("mfe_price", "true"), ("mae_price", "false")):
            with self.subTest(field=field):
                self.assertIn(
                    f"meta.{field} = _MergeExcursionPrice(meta.{field}, state.{field}, "
                    f"meta.is_buy, {favourable});",
                    body,
                )
                self.assertIsNone(
                    re.search(rf"meta\.{field}\s*=\s*state\.", body),
                    f"{field} is overwritten from PenaltyState instead of merged",
                )

        # The merge helpers must actually mean "keep both observations".
        earliest = _function_body(source, "_EarliestObservation")
        self.assertIn("if(a <= 0) return b;", earliest)
        self.assertIn("if(b <= 0) return a;", earliest)
        self.assertIn("return (a < b ? a : b);", earliest)

        excursion = _function_body(source, "_MergeExcursionPrice")
        self.assertIn("if(incoming <= 0.0) return current;", excursion)
        self.assertIn("if(current <= 0.0) return incoming;", excursion)
        self.assertIn("(is_buy == favourable) ? MathMax(current, incoming)", excursion)

        # The other writer must stay a create-once latch, or the pair starts fighting
        # again from the opposite side.
        snapshot = _function_body(source, "_UpdateAnalyticsSnapshot")
        self.assertIn("if(meta.first_0_25r_time <= 0 && meta.mfe_r >= 0.25)", snapshot)
        self.assertIn("if(meta.first_0_50r_time <= 0 && meta.mfe_r >= 0.50)", snapshot)

    def test_mql_minutes_to_mfe_has_exactly_one_definition(self) -> None:
        """A derived field may have many publishers but only one formula.

        minutes_to_0_*r_mfe had three publishers computing three different things: the
        stamp minus open, "now" minus open, and the stamp minus filled_at with no
        planned_at fallback. Because 0 is the correct answer when the threshold is
        crossed inside the first minute, the "<= 0 means not computed" guard re-fired on
        every pass, so two publishers overwrote each other forever and the trade meta
        never repeated -- the second reason the content memo could not coalesce, found
        only after the first was fixed and the oscillation moved one field to the left.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")

        derived = _function_body(source, "_MinutesFromOpenToStamp")
        # -1 is "not derivable", which is what keeps it distinguishable from a real 0.
        self.assertIn("if(stamp <= 0 || opened_at <= 0) return -1;", derived)
        self.assertIn("return (int)(delta / 60);", derived)

        # Every publisher must take its value from that one derivation.
        assignments = re.findall(
            r"meta\.minutes_to_0_(?:25|50)r_mfe\s*=\s*([A-Za-z_]\w*)\s*;", source
        )
        self.assertGreaterEqual(len(assignments), 6, "expected every publisher to be found")
        for name in assignments:
            with self.subTest(assigned_from=name):
                self.assertIn(f"int {name} = _MinutesFromOpenToStamp(", source)

        # The two formulas that disagreed must be gone, not merely outnumbered.
        self.assertIsNone(
            re.search(r"minutes_to_0_(?:25|50)r_mfe\s*=\s*minutes_open", source),
            "a publisher still reports minutes-since-open as minutes-to-threshold",
        )
        self.assertIsNone(
            re.search(r"minutes_to_0_(?:25|50)r_mfe\s*=\s*\(int\)MathMax", source),
            "a publisher still open-codes the derivation",
        )

        # One definition of the open time as well -- one of the three publishers was
        # missing the planned_at fallback the other two applied.
        self.assertEqual(
            1,
            source.count("(meta.filled_at > 0 ? meta.filled_at : meta.planned_at)"),
            "opened_at must be spelled out only inside _MetaOpenedAt",
        )
        self.assertIn(
            "return (meta.filled_at > 0 ? meta.filled_at : meta.planned_at);",
            _function_body(source, "_MetaOpenedAt"),
        )

        # The status strings are the same shape of conflict: _UpdateAnalyticsSnapshot
        # fills them when empty, so an empty copy from PenaltyState must not erase them.
        applied = _function_body(source, "_ApplyPenaltyStateToMeta")
        self.assertIn(
            'if(StringLen(state.path_completeness_status) > 0 &&\n'
            '         state.path_completeness_status != "UNKNOWN")',
            applied,
        )
        self.assertIn("if(StringLen(state.path_observation_source) > 0)", applied)

    def test_mql_trade_meta_comparison_key_is_derived_once_per_document(self) -> None:
        """One document, one comparison key -- not one per alias.

        Every alias receives the identical characters, so deriving the key inside the
        per-alias writer rebuilt the same ~76,000 character string ten times per
        maintenance pass and could never produce a different answer.
        """

        source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
        writer = _function_body(source, "_WriteTradeMetaFile")
        fanout = _function_body(source, "_WriteTradeMeta")

        self.assertIn(
            "void _WriteTradeMetaFile(const string path, const string json, "
            "const string comparison_key)",
            source,
        )
        self.assertNotIn("_TradeMetaComparisonKey(", writer)
        self.assertEqual(1, fanout.count("_TradeMetaComparisonKey("))

        # Every alias must be handed the derived key -- a site left on the old
        # two-argument form would not compile, but a future site added without it
        # would silently reintroduce the per-alias derivation.
        call_sites = fanout.count("_WriteTradeMetaFile(")
        self.assertGreaterEqual(call_sites, 10)
        self.assertEqual(call_sites, fanout.count(", comparison_key)"))

        # The memo still governs whether the key is needed at all.
        self.assertIn(
            "string comparison_key = (InpTradeMetaMemoEntries > 0 "
            "? _TradeMetaComparisonKey(j) : \"\");",
            fanout,
        )


if __name__ == "__main__":
    unittest.main()
