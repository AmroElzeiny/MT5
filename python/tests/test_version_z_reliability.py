from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import ai_gate
from decision_integrity import (
    DECISION_APPROVE,
    DECISION_QUALITY_FULL_STRUCTURED,
)
from runtime_governance import (
    DECISION_NON_REPEATABLE,
    INSUFFICIENT_SAMPLE,
    REPEATABLE,
    REPEATABILITY_SCHEMA_VERSION,
    SCORE_NON_REPEATABLE,
    UNAVAILABLE,
    canonical_hash,
)


ROOT = Path(__file__).resolve().parents[1]
_STAGED_MQL_ROOT = ROOT.parent
if (_STAGED_MQL_ROOT / "mql_include").is_dir():
    MQL_INCLUDE = _STAGED_MQL_ROOT / "mql_include"
    MQL_EXPERT = _STAGED_MQL_ROOT / "mql_expert"
else:
    _ACTIVE_MQL_ROOT = Path(
        r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5"
    )
    MQL_INCLUDE = _ACTIVE_MQL_ROOT / "Include" / "MT5_PO3_Codex"
    MQL_EXPERT = _ACTIVE_MQL_ROOT / "Experts" / "MT5_PO3_Codex"


def approving_decision(*, decision_id: str = "decision-primary", score: float = 8.0) -> ai_gate.Decision:
    return ai_gate.Decision(
        allow=True,
        raw_allow=True,
        model_raw_allow=True,
        python_final_allow=True,
        score=score,
        llm_quality_score=score,
        decision_state=DECISION_APPROVE,
        decision_quality_tier=DECISION_QUALITY_FULL_STRUCTURED,
        mandatory_fields_complete=True,
        suggested_risk_multiplier=1.0,
        model_version=ai_gate.AI_CONFIG.model,
        decision_id=decision_id,
        selected_candidate_id="candidate-0",
        selected_candidate_hash="HASH-CANDIDATE-0",
        chosen_index=0,
        chosen_target_model="liquidity_target",
        veto_evidence_fields=[],
        target_arbitration={"chosen_target_model": "liquidity_target"},
        candidate_assessments=[],
        reasons={},
    )


class RepeatabilityRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_config = ai_gate.AI_CONFIG
        self._tmp = tempfile.TemporaryDirectory()
        self.artifact = Path(self._tmp.name) / "repeatability.json"
        ai_gate.AI_CONFIG = replace(
            self._original_config,
            require_repeatability_live=True,
            shadow_repeat_enable=True,
            shadow_repeat_sample_rate=1.0,
            shadow_repeat_count=3,
            shadow_repeat_min_evaluated_candidates=2,
            shadow_repeat_artifact_file=self.artifact,
        )

    def tearDown(self) -> None:
        ai_gate.AI_CONFIG = self._original_config
        self._tmp.cleanup()

    def _group(self, status: str, *, mismatch: bool = False) -> dict:
        fields = ai_gate._repeatability_group_fields(
            ai_gate.AI_CONFIG.model,
            DECISION_QUALITY_FULL_STRUCTURED,
        )
        if mismatch:
            fields["prompt_contract_version"] = "wrong-prompt-contract"
        group = {
            **fields,
            "status": status,
            "score_threshold_authority": status == REPEATABLE,
            "trading_eligible": status == REPEATABLE,
            "artifact_hash": f"artifact-{status.lower()}",
        }
        return group

    def _write_artifact(self, group: dict | None = None) -> None:
        groups = {}
        if group is not None:
            key = ai_gate._repeatability_group_key(
                ai_gate.AI_CONFIG.model,
                DECISION_QUALITY_FULL_STRUCTURED,
            )
            groups[key] = group
        self.artifact.write_text(
            json.dumps({"schema_version": REPEATABILITY_SCHEMA_VERSION, "groups": groups}),
            encoding="utf-8",
        )

    def test_missing_empty_invalid_and_wrong_schema_are_unavailable(self) -> None:
        cases = (
            (None, "missing_artifact"),
            ("", "empty_artifact"),
            ("{broken", "unreadable_or_invalid_json"),
            (json.dumps({"schema_version": "old", "groups": {}}), "incompatible_schema"),
        )
        for contents, expected_state in cases:
            with self.subTest(expected_state=expected_state):
                self.artifact.unlink(missing_ok=True)
                if contents is not None:
                    self.artifact.write_text(contents, encoding="utf-8")
                authority = ai_gate._repeatability_authority_for(
                    ai_gate.AI_CONFIG.model,
                    DECISION_QUALITY_FULL_STRUCTURED,
                )
                self.assertEqual(authority["status"], UNAVAILABLE)
                self.assertEqual(authority["artifact_state"], expected_state)
                self.assertFalse(authority["trading_eligible"])

    def test_missing_group_and_group_identity_mismatch_fail_closed(self) -> None:
        self._write_artifact()
        missing = ai_gate._repeatability_authority_for(
            ai_gate.AI_CONFIG.model,
            DECISION_QUALITY_FULL_STRUCTURED,
        )
        self.assertEqual(missing["artifact_state"], "missing_group")
        self.assertFalse(missing["trading_eligible"])

        self._write_artifact(self._group(REPEATABLE, mismatch=True))
        mismatch = ai_gate._repeatability_authority_for(
            ai_gate.AI_CONFIG.model,
            DECISION_QUALITY_FULL_STRUCTURED,
        )
        self.assertEqual(mismatch["artifact_state"], "group_identity_mismatch")
        self.assertEqual(mismatch["reason"], "repeatability_group_mismatch")
        self.assertFalse(mismatch["trading_eligible"])

    def test_every_nonrepeatable_status_blocks_live_but_not_research_collection(self) -> None:
        expected_codes = {
            UNAVAILABLE: "repeatability_unavailable",
            INSUFFICIENT_SAMPLE: "repeatability_insufficient_sample",
            SCORE_NON_REPEATABLE: "model_prompt_score_non_repeatable",
            DECISION_NON_REPEATABLE: "model_prompt_decision_non_repeatable",
        }
        for status, code in expected_codes.items():
            with self.subTest(status=status):
                authority = {
                    "status": status,
                    "reason": code,
                    "artifact_state": "ok",
                    "trading_eligible": False,
                    "score_threshold_authority": False,
                }
                live = ai_gate._apply_repeatability_authority(
                    approving_decision(),
                    authority,
                    payload={"id": "live-request", "workload_mode": "LIVE_FORWARD"},
                )
                self.assertFalse(live.allow)
                self.assertFalse(live.raw_allow)
                self.assertFalse(live.python_final_allow)
                self.assertEqual(live.decision_state, "ABSTAIN")
                self.assertEqual(live.suggested_risk_multiplier, 0.0)
                self.assertIn(code, live.rejection_codes)

                research = ai_gate._apply_repeatability_authority(
                    approving_decision(),
                    authority,
                    payload={"id": "research-request", "workload_mode": "research"},
                )
                self.assertTrue(research.allow)
                self.assertEqual(research.suggested_risk_multiplier, 1.0)
                self.assertFalse(research.reasons["repeatability_authority"]["required_live"])

    def test_repeatable_group_allows_normal_downstream_processing(self) -> None:
        self._write_artifact(self._group(REPEATABLE))
        authority = ai_gate._repeatability_authority_for(
            ai_gate.AI_CONFIG.model,
            DECISION_QUALITY_FULL_STRUCTURED,
        )
        decision = ai_gate._apply_repeatability_authority(
            approving_decision(),
            authority,
            payload={"id": "live-repeatable", "workload_mode": "LIVE_FORWARD"},
        )
        self.assertEqual(authority["status"], REPEATABLE)
        self.assertTrue(authority["trading_eligible"])
        self.assertTrue(decision.allow)
        self.assertEqual(decision.suggested_risk_multiplier, 1.0)

    def test_shadow_repeat_failure_never_crashes_or_creates_authoritative_artifact(self) -> None:
        payload = {"id": "shadow-failure", "candidates": [], "workload_mode": "LIVE_FORWARD"}
        with patch.object(ai_gate, "_score_setup_ai", side_effect=RuntimeError("transport down")):
            ai_gate._run_shadow_repeat_evaluation(payload, approving_decision())
        self.assertFalse(self.artifact.exists())

    def test_repeatability_container_is_non_authoritative_and_idempotent(self) -> None:
        self.assertTrue(ai_gate._ensure_repeatability_artifact_container())
        artifact = json.loads(self.artifact.read_text(encoding="utf-8"))
        self.assertEqual(artifact["schema_version"], ai_gate.REPEATABILITY_SCHEMA_VERSION)
        self.assertEqual(artifact["groups"], {})
        self.assertFalse(ai_gate._ensure_repeatability_artifact_container())
        authority = ai_gate._repeatability_authority_for(
            "gpt-5.4-nano",
            ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
        )
        self.assertFalse(authority["trading_eligible"])
        self.assertEqual(authority["artifact_state"], "missing_group")

    def test_sampling_is_deterministic_and_retries_do_not_duplicate_observations(self) -> None:
        payload = {
            "id": "repeatable-request",
            "candidates": [
                {
                    "candidate_id": "candidate-0",
                    "candidate_hash": "HASH-CANDIDATE-0",
                    "request_execution_fingerprint": "FP-0",
                }
            ],
        }
        self.assertEqual(
            ai_gate._shadow_repeat_sampled(payload),
            ai_gate._shadow_repeat_sampled(copy.deepcopy(payload)),
        )
        primary = approving_decision(decision_id="primary")
        repeated = [
            approving_decision(decision_id="repeat-1"),
            approving_decision(decision_id="repeat-2"),
        ]
        ai_gate._update_repeatability_artifact(payload, primary, repeated)
        ai_gate._update_repeatability_artifact(payload, primary, repeated)
        artifact = json.loads(self.artifact.read_text(encoding="utf-8"))
        group = next(iter(artifact["groups"].values()))
        self.assertEqual(len(group["observations"]), 1)

    def test_concurrent_updates_are_atomic_and_reload_observes_modification(self) -> None:
        errors: list[BaseException] = []

        def update(index: int) -> None:
            try:
                payload = {
                    "id": f"concurrent-{index}",
                    "candidates": [
                        {
                            "candidate_id": f"candidate-{index}",
                            "candidate_hash": f"HASH-{index}",
                            "request_execution_fingerprint": f"FP-{index}",
                        }
                    ],
                }
                ai_gate._update_repeatability_artifact(
                    payload,
                    approving_decision(decision_id=f"primary-{index}"),
                    [approving_decision(decision_id=f"repeat-{index}")],
                )
            except BaseException as exc:  # pragma: no cover - captured for assertion
                errors.append(exc)

        threads = [threading.Thread(target=update, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
        self.assertFalse(errors)
        artifact = json.loads(self.artifact.read_text(encoding="utf-8"))
        group = next(iter(artifact["groups"].values()))
        self.assertEqual(len(group["observations"]), 4)

        self._write_artifact(self._group(INSUFFICIENT_SAMPLE))
        first = ai_gate._repeatability_authority_for(
            ai_gate.AI_CONFIG.model,
            DECISION_QUALITY_FULL_STRUCTURED,
        )
        self._write_artifact(self._group(REPEATABLE))
        second = ai_gate._repeatability_authority_for(
            ai_gate.AI_CONFIG.model,
            DECISION_QUALITY_FULL_STRUCTURED,
        )
        self.assertEqual(first["status"], INSUFFICIENT_SAMPLE)
        self.assertEqual(second["status"], REPEATABLE)


class LlmAuthorityTests(unittest.TestCase):
    def test_uncalibrated_numeric_threshold_crossings_have_no_negative_authority(self) -> None:
        decision = approving_decision()
        decision.follow_through_probability = 0.01
        decision.invalidation_risk = 0.99
        decision.chop_risk = 0.99
        decision.post_entry_failure_risk = 0.99
        decision.final_trade_expectancy_score = 0.01
        gated = ai_gate._apply_ai_veto_gate(
            {"id": "diagnostic-only", "runtime_inputs": {"ai_veto_enable": True}},
            decision,
        )
        self.assertTrue(gated.allow)
        self.assertEqual(
            gated.llm_numeric_diagnostics_authority,
            "uncalibrated_diagnostic_only_no_direct_trade_authority",
        )

    def test_explicit_evidence_backed_qualitative_veto_rejects(self) -> None:
        decision = approving_decision()
        decision.veto_enabled = True
        decision.veto_code = "ai_veto_structural_contradiction"
        decision.veto_evidence_fields = ["candidate.structure_state", "candidate.bos_direction"]
        decision.veto_reason = "BOS direction contradicts the supplied structure state."
        gated = ai_gate._apply_ai_veto_gate(
            {"id": "evidence-veto", "runtime_inputs": {"ai_veto_enable": True}},
            decision,
        )
        self.assertFalse(gated.allow)
        self.assertIn("ai_veto_structural_contradiction", gated.rejection_codes)

    def test_unknown_or_evidenceless_veto_fails_schema_closed(self) -> None:
        cases = (
            ("free_text_veto", ["candidate.anything"], "unsupported"),
            ("ai_veto_structural_contradiction", [], "missing evidence"),
            ("ai_veto_structural_contradiction", ["candidate.structure"], ""),
        )
        for code, evidence, reason in cases:
            with self.subTest(code=code, evidence=evidence, reason=reason):
                decision = approving_decision()
                decision.veto_enabled = True
                decision.veto_code = code
                decision.veto_evidence_fields = evidence
                decision.veto_reason = reason
                gated = ai_gate._apply_ai_veto_gate(
                    {"id": "invalid-veto", "runtime_inputs": {"ai_veto_enable": True}},
                    decision,
                )
                self.assertFalse(gated.allow)
                self.assertIn("ai_quality_schema_incomplete", gated.rejection_codes)

    def test_numeric_diagnostics_cannot_rescue_existing_deterministic_rejection(self) -> None:
        decision = approving_decision()
        decision.allow = False
        decision.raw_allow = False
        decision.model_raw_allow = False
        decision.python_final_allow = False
        decision.follow_through_probability = 1.0
        decision.invalidation_risk = 0.0
        decision.chop_risk = 0.0
        decision.post_entry_failure_risk = 0.0
        decision.final_trade_expectancy_score = 10.0
        gated = ai_gate._apply_ai_veto_gate(
            {"id": "cannot-rescue", "runtime_inputs": {"ai_veto_enable": True}},
            decision,
        )
        self.assertFalse(gated.allow)


class MqlVersionZContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = (MQL_INCLUDE / "TradeEngine.mqh").read_text(encoding="utf-8")
        cls.penalty = (MQL_INCLUDE / "PenaltyWatcher.mqh").read_text(encoding="utf-8")
        cls.state = (MQL_INCLUDE / "StateStore.mqh").read_text(encoding="utf-8")
        cls.types = (MQL_INCLUDE / "Types.mqh").read_text(encoding="utf-8")
        cls.ea = (MQL_EXPERT / "PO3_AIGate_ScannerEA.mq5").read_text(encoding="utf-8")

    def test_market_authority_is_false_before_submission_and_true_only_after_acceptance(self) -> None:
        start = self.engine.index("live.intended_order_type")
        pre = self.engine.index('_SetExecutionAuthority(live, "MQL_PRE_SUBMISSION_ELIGIBLE", false', start)
        attempted = self.engine.index('_SetExecutionAuthority(live, "BROKER_SUBMISSION_ATTEMPTED", false', pre)
        buy = self.engine.index("m_trade.Buy(", attempted)
        retcode = self.engine.index("live.broker_request_accepted = (ok && _BrokerRetcodeAccepted", buy)
        rejected = self.engine.index('_SetExecutionAuthority(live, "BROKER_REQUEST_REJECTED", false', retcode)
        accepted = self.engine.index('_SetExecutionAuthority(live, "BROKER_REQUEST_ACCEPTED", true', rejected)
        self.assertLess(pre, attempted)
        self.assertLess(attempted, buy)
        self.assertLess(buy, retcode)
        self.assertLess(retcode, rejected)
        self.assertLess(rejected, accepted)

    def test_pending_acceptance_is_not_reported_as_position_fill(self) -> None:
        start = self.engine.index("pending.intended_order_type")
        pre = self.engine.index('_SetExecutionAuthority(pending, "MQL_PRE_SUBMISSION_ELIGIBLE", false', start)
        submit = self.engine.index("m_trade.BuyLimit(", pre)
        rejected = self.engine.index('_SetExecutionAuthority(pending, "BROKER_REQUEST_REJECTED", false', submit)
        pending = self.engine.index('_SetExecutionAuthority(pending, "ORDER_ACCEPTED_PENDING", true', rejected)
        later_fill = self.engine.index('"POSITION_FILLED_IDENTITY_VERIFIED"', pending)
        self.assertLess(pre, submit)
        self.assertLess(submit, rejected)
        self.assertLess(rejected, pending)
        self.assertLess(pending, later_fill)
        self.assertIn("final_execution_success = false", self.engine[start:later_fill])

    def test_broker_retcode_identity_partial_fill_and_quarantine_contracts_are_present(self) -> None:
        for token in (
            "TRADE_RETCODE_DONE",
            "TRADE_RETCODE_PLACED",
            "TRADE_RETCODE_DONE_PARTIAL",
            "broker_retcode_description",
            "result_order_ticket",
            "result_deal_ticket",
            "broker_partial_fill",
            "BROKER_ACCEPTED_IDENTITY_QUARANTINED",
            "POSITION_PARTIALLY_FILLED_IDENTITY_VERIFIED",
            "execution_authority_events.jsonl",
        ):
            self.assertIn(token, self.engine)
        self.assertIn("_ResolveExactExecutionIdentity", self.engine)
        self.assertIn("HandleTradeTransaction", self.engine)

    def test_management_action_is_consumed_only_after_verified_effect(self) -> None:
        process_start = self.penalty.index("void _ProcessPendingAction")
        process_end = self.penalty.index("string _TransitionActionId", process_start)
        body = self.penalty[process_start:process_end]
        cooldown = body.index('"DEFERRED_COOLDOWN"')
        submit = body.index("trade.PositionClose")
        accepted = body.index("_ManagementRetcodeAccepted")
        remember = body.index("_RememberExecutedAction")
        retryable = body.index('"FAILED_RETRYABLE"', remember)
        self.assertLess(cooldown, submit)
        self.assertLess(submit, accepted)
        self.assertLess(accepted, remember)
        self.assertLess(remember, retryable)
        self.assertIn("normalized_close_volume_invalid_recompute_later", body)
        self.assertIn("broker_accepted_effect_not_yet_verified", body)
        self.assertIn("management_retry_limit_exhausted", body)

    def test_management_restart_supersession_and_exact_identity_are_persisted(self) -> None:
        for token in (
            "DETECTED",
            "ELIGIBLE",
            "DEFERRED_COOLDOWN",
            "SUBMISSION_ATTEMPTED",
            "SUCCEEDED",
            "FAILED_RETRYABLE",
            "FAILED_TERMINAL",
            "SUPERSEDED",
            "restart_requires_effect_verification_before_retry",
            "position_identifier",
            "position_ticket",
            "executed_action_ids",
        ):
            self.assertIn(token, self.penalty)
        for token in (
            "action_lifecycle_state",
            "action_retry_count",
            "next_eligible_retry_time",
            "executed_action_ids",
        ):
            self.assertIn(token, self.state)
            self.assertIn(token, self.types)

    def test_tick_path_uses_executable_spread_side_and_honest_completeness(self) -> None:
        self.assertIn("void OnTick()", self.ea)
        self.assertIn("g_engine.ObserveChartTick(_Symbol);", self.ea)
        self.assertIn("double executable_price = (is_buy ? tick.bid : tick.ask);", self.penalty)
        self.assertIn("st.mfe_r = MathMax(st.mfe_r", self.penalty)
        self.assertIn("st.mae_r = MathMax(st.mae_r", self.penalty)
        self.assertIn("first_0_25r_time", self.penalty)
        self.assertIn("first_0_50r_time", self.penalty)
        self.assertIn("first_adverse_threshold_time", self.penalty)
        self.assertIn('"TICK_COMPLETE"', self.penalty)
        self.assertIn('"TIMER_SAMPLED"', self.penalty)
        self.assertIn('"DATA_GAP"', self.penalty)
        self.assertIn('"UNKNOWN"', self.penalty)
        self.assertIn('"ON_TICK_CHART_SYMBOL", true', self.penalty)
        self.assertIn('"TIMER_SAMPLED_MARKET_WATCH", false', self.penalty)
        self.assertIn("RESTORED_AFTER_OFFLINE_INTERVAL", self.penalty)

    def test_tick_path_handles_multiple_exact_positions_without_symbol_metadata_guessing(self) -> None:
        observe_start = self.penalty.index("void ObserveChartTick")
        tick_end = self.penalty.index("void Tick(CTrade", observe_start)
        body = self.penalty[observe_start:tick_end]
        self.assertIn("for(int i=PositionsTotal()-1; i>=0; i--)", body)
        self.assertIn("PositionGetTicket(i)", body)
        self.assertIn("_ObserveSelectedPositionPath(ticket", body)
        helper_start = self.penalty.index("void _ObserveSelectedPositionPath")
        helper_end = self.penalty.index("public:", helper_start)
        helper = self.penalty[helper_start:helper_end]
        self.assertIn("PositionSelectByTicket(ticket)", helper)
        self.assertIn("PositionMatchesMagic(ticket)", helper)
        self.assertIn("POSITION_IDENTIFIER", helper)

    def test_old_contracts_are_invalidated_consistently_across_python_and_mql(self) -> None:
        config = (MQL_INCLUDE / "Config.mqh").read_text(encoding="utf-8")
        self.assertIn(ai_gate.AI_DECISION_SCHEMA_VERSION, config)
        self.assertIn(ai_gate.AI_PROMPT_CONTRACT_VERSION, config)
        self.assertIn(REPEATABILITY_SCHEMA_VERSION, config)
        self.assertIn("cached_repeatability_schema != REPEATABILITY_SCHEMA_VERSION", self.engine)
        self.assertIn("repeatability_nontrading_approve", (MQL_INCLUDE / "AIGateBridge.mqh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
