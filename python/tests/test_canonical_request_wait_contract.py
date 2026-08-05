from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_gate
from compatibility_manifest import (
    compatibility_manifest,
    compatibility_manifest_hash,
    validate_mql_contract,
)
from decision_integrity import freeze_ai_request
from pipeline_integrity import (
    FROZEN_REQUEST_MUTATION,
    FrozenRequestMutationError,
    classify_local_pipeline_failure,
)
from tester_wait_contract import TesterAIWaitDeadline, TesterWaitState
from tests.test_decision_integrity import candidate
from tests.test_python_owned_identity_lifecycle import (
    EmptyMemory,
    ReverseAssessmentProvider,
    _payload,
)


ROOT = Path(__file__).resolve().parents[1]
MQL_INCLUDE = Path(
    r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal"
    r"\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex"
)
MQL_EXPERT = Path(
    r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal"
    r"\0148BD5691B65B0F2157627A4231F3DE\MQL5\Experts\MT5_PO3_Codex"
)


def mql_contract_payload() -> dict:
    manifest = compatibility_manifest()
    cand = candidate()
    payload = _payload([cand])
    payload.update(
        {
            "engine_version": manifest["engine_version"],
            "input_schema_version": manifest["engine_input_schema"],
            "decision_schema_version": manifest["decision_schema_version"],
            "request_identity_version": manifest["request_identity_version"],
            "contract_manifest_version": manifest["contract_manifest_version"],
            "contract_manifest_hash": compatibility_manifest_hash(),
            "contract_manifest": manifest,
        }
    )
    payload["runtime_inputs"].update(
        {
            "engine_input_schema": manifest["engine_input_schema"],
            "ai_decision_schema_version": manifest["decision_schema_version"],
            "ai_target_arbitration_schema_version": manifest[
                "target_arbitration_schema_version"
            ],
            "ai_prompt_contract_version": manifest["prompt_contract_version"],
            "repeatability_schema_version": manifest[
                "repeatability_schema_version"
            ],
        }
    )
    return payload


class CanonicalTaxonomyLifecycleTests(unittest.TestCase):
    def test_taxonomy_enrichment_precedes_freeze_and_remains_byte_stable(self) -> None:
        payload = mql_contract_payload()
        second = candidate(1, "B")
        payload["candidates"].append(second)
        payload["plan"] = copy.deepcopy(payload["candidates"][0])
        for field in (
            "setup_taxonomy_version",
            "setup_taxonomy_enum",
            "taxonomy_mapping_source",
        ):
            for item in payload["candidates"]:
                item[field] = ""
        failures = ai_gate._enrich_candidate_taxonomy_before_freeze(payload)
        self.assertEqual(failures, [])
        provider = ReverseAssessmentProvider(payload["candidates"])
        frozen = ai_gate._freeze_request_for_provider(payload, provider)
        canonical = frozen.thaw_payload()
        before = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        frozen.assert_unchanged(canonical, stage="before_analyst")
        with (
            patch.object(ai_gate, "_trade_memory_store", return_value=EmptyMemory()),
            patch.object(ai_gate, "_provider", return_value=provider),
            patch.object(
                ai_gate,
                "_run_shadow_repeat_evaluation",
                return_value={"sampled": False},
            ),
        ):
            decision = ai_gate._score_setup_ai(
                canonical,
                provider_override=provider,
                frozen_request=frozen,
            )
        after = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        self.assertEqual(before, after)
        self.assertEqual(decision.decision_quality_tier, "FULL_STRUCTURED")

    def test_conflicting_preexisting_taxonomy_fails_before_freeze(self) -> None:
        payload = mql_contract_payload()
        payload["candidates"][0]["setup_taxonomy_enum"] = "MICRO_BREAKER_RETEST"
        failures = ai_gate._enrich_candidate_taxonomy_before_freeze(payload)
        self.assertEqual(len(failures), 1)
        self.assertIn(
            "preexisting_taxonomy_conflict",
            failures[0]["mapping_failure_reason"],
        )

    def test_frozen_mutation_has_local_integrity_category(self) -> None:
        payload = mql_contract_payload()
        provider = ReverseAssessmentProvider(payload["candidates"])
        frozen = ai_gate._freeze_request_for_provider(payload, provider)
        thawed = frozen.thaw_payload()
        thawed["candidates"][0]["setup_taxonomy_enum"] = "MUTATED"
        with self.assertRaises(FrozenRequestMutationError) as captured:
            frozen.assert_unchanged(thawed)
        details = classify_local_pipeline_failure(captured.exception)
        self.assertEqual(details.category, FROZEN_REQUEST_MUTATION)
        self.assertFalse(details.provider_call_attempted)

    def test_provider_time_mutation_preserves_typed_local_failure(self) -> None:
        payload = mql_contract_payload()
        provider = ReverseAssessmentProvider(payload["candidates"])
        frozen = ai_gate._freeze_request_for_provider(payload, provider)
        canonical = frozen.thaw_payload()
        generate = provider.generate_structured

        def mutating_generate(**kwargs):
            result = generate(**kwargs)
            canonical["candidates"][0]["taxonomy_mapping_source"] = "mutated"
            return result

        with (
            patch.object(provider, "generate_structured", side_effect=mutating_generate),
            patch.object(ai_gate, "_trade_memory_store", return_value=EmptyMemory()),
        ):
            with self.assertRaises(FrozenRequestMutationError) as captured:
                ai_gate._score_setup_ai(
                    canonical,
                    provider_override=provider,
                    frozen_request=frozen,
                )
        details = classify_local_pipeline_failure(captured.exception)
        self.assertEqual(details.category, FROZEN_REQUEST_MUTATION)
        self.assertTrue(details.provider_call_attempted)
        self.assertTrue(details.http_request_sent)


class ContractCompatibilityTests(unittest.TestCase):
    def test_python_manifest_accepts_exact_mql_contract(self) -> None:
        result = validate_mql_contract(mql_contract_payload())
        self.assertTrue(result.compatible, result.mismatches)

    def test_contract_mismatch_fails_closed(self) -> None:
        payload = mql_contract_payload()
        payload["contract_manifest"]["prompt_contract_version"] = "old"
        result = validate_mql_contract(payload)
        self.assertFalse(result.compatible)
        self.assertIn("prompt_contract_version", result.mismatches)

    def test_mql_and_python_manifest_material_are_synchronized(self) -> None:
        config = (MQL_INCLUDE / "Config.mqh").read_text(encoding="utf-8")
        for value in compatibility_manifest().values():
            self.assertIn(value, config)
        self.assertIn("PO3ContractManifestHash", config)

    def test_incompatible_contract_never_selects_or_calls_provider(self) -> None:
        payload = mql_contract_payload()
        payload["contract_manifest"]["decision_schema_version"] = "stale"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_path = root / "request.json"
            response_dir = root / "responses"
            stale_dir = root / "stale"
            request_path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            with patch.object(
                ai_gate,
                "_provider",
                side_effect=AssertionError("provider_must_not_be_selected"),
            ):
                with self.assertRaisesRegex(
                    ValueError, "contract_manifest_incompatible"
                ):
                    ai_gate.process_one(request_path, response_dir, stale_dir)


class TesterWaitDeadlineTests(unittest.TestCase):
    def test_full_two_minute_deadline_with_fake_clock(self) -> None:
        wait = TesterAIWaitDeadline(wall_start_ms=10_000, timeout_ms=120_000)
        for seconds in (14, 15, 30, 60, 119):
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    wait.state(
                        10_000 + seconds * 1000,
                        valid_response_present=False,
                    ),
                    TesterWaitState.WAITING,
                )
                self.assertEqual(
                    wait.state(
                        10_000 + seconds * 1000,
                        valid_response_present=True,
                    ),
                    TesterWaitState.RESPONSE_APPLIED,
                )
        self.assertEqual(
            wait.state(130_000, valid_response_present=False),
            TesterWaitState.TIMEOUT,
        )
        self.assertEqual(
            wait.state(130_001, valid_response_present=True),
            TesterWaitState.TIMEOUT,
        )

    def test_simulated_time_cannot_change_wall_deadline(self) -> None:
        wait = TesterAIWaitDeadline(wall_start_ms=500, timeout_ms=120_000)
        simulated_times = (0, 60, 15_000, 86_400)
        states = [
            wait.state(60_500, valid_response_present=False)
            for _ in simulated_times
        ]
        self.assertEqual(states, [TesterWaitState.WAITING] * 4)
        self.assertEqual(wait.deadline_ms, 120_500)

    def test_active_mql_has_no_processing_file_15_second_timeout(self) -> None:
        engine = (MQL_INCLUDE / "TradeEngine.mqh").read_text(encoding="utf-8")
        ea = (MQL_EXPERT / "PO3_AIGate_ScannerEA.mq5").read_text(
            encoding="utf-8"
        )
        config = (MQL_INCLUDE / "Config.mqh").read_text(encoding="utf-8")
        self.assertNotIn("_WallElapsedMs(oldest_wall_request) > 15000", engine)
        self.assertIn("GetTickCount64()", engine)
        self.assertIn(
            "input int InpAiWaitTimeoutRealMin = 2;",
            " ".join(config.split()),
        )
        self.assertIn(
            "InpAiWaitTimeoutRealMin * 60 * 1000",
            ea,
        )
        self.assertIn(
            "started_ms = g_engine.PendingAIOldestWallStartMs()",
            ea,
        )
        self.assertIn("deadline_ms = started_ms + (ulong)timeout_ms", ea)
        self.assertIn("[tester_ai_wait_started]", ea)
        self.assertIn("[tester_ai_wait_progress]", ea)
        self.assertIn("[tester_ai_wait_completed]", ea)


class ErrorEnvelopeAndLineageTests(unittest.TestCase):
    def test_explicit_error_envelope_retains_transport_identity(self) -> None:
        payload = mql_contract_payload()
        provider = ReverseAssessmentProvider(payload["candidates"])
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            ai_gate, "_provider", return_value=provider
        ):
            output = Path(temp_dir)
            ai_gate._write_error_response(
                payload["id"],
                output,
                "frozen_request_candidate_mutation",
                request_payload=payload,
                failure_category=FROZEN_REQUEST_MUTATION,
            )
            response = ai_gate.read_json_any_encoding(
                output / f"{payload['id']}.json"
            )
        self.assertEqual(response["session_id"], payload["session_id"])
        self.assertEqual(response["request_nonce"], payload["request_nonce"])
        self.assertEqual(response["candidate_count"], 1)
        self.assertEqual(len(response["ordered_candidate_identities"]), 1)
        self.assertEqual(response["candidate_assessments"], [])
        self.assertFalse(response["python_final_allow"])
        self.assertEqual(response["suggested_risk_multiplier"], 0.0)
        self.assertEqual(response["decision_quality_tier"], "DEGRADED_NON_TRADING")

    def test_mql_po3_and_net_reward_repairs_are_on_runtime_path(self) -> None:
        engine = (MQL_INCLUDE / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn("action=restored_from_source_story", engine)
        self.assertIn("reason=t_disp_not_after_t_sweep", engine)
        self.assertIn("reason=t_bos_not_after_t_disp", engine)
        self.assertNotIn("p.po3.has_displacement = false;", engine)
        self.assertIn("_FinalizePlanEconomics(p, \"plan_prices_finalized\", true)", engine)
        self.assertIn("net_reward_after_cost_r", engine)
        self.assertIn(
            "MathMax(0.0, p.execution_cost_r) + MathMax(0.0, p.slippage_r) + MathMax(0.0, p.commission_r)",
            engine,
        )


if __name__ == "__main__":
    unittest.main()
