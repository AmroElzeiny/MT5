"""PenaltyWatcher -> ONE management AI review -> APPROVE executes / DENY cools down.

Two halves:
* the Python bus service, exercised for real (files, ledger, strict schema,
  envelope) with a counting provider double;
* the MQL watcher, whose pure timing helpers are executed from the real
  PenaltyWatcher.mqh text and whose control flow is asserted function-scoped.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai_provider import ProviderCallError, UnavailableProvider
from management_review import (
    MANAGEMENT_REVIEW_SCHEMA_VERSION,
    ManagementReviewService,
    ModelManagementReview,
    management_review_schema_preflight,
)
from mql_pure_eval import function_body, load_mql_function, mql_constants, read_repo_mql

POSITION_ID = "325229449"


def _request(seq: int = 1, *, action: str = "PARTIAL_CLOSE", cut: float = 0.25, requested_at: int = 1_790_000_000,
             position: str = POSITION_ID, action_id: str | None = None) -> dict:
    return {
        "schema_version": MANAGEMENT_REVIEW_SCHEMA_VERSION,
        "request_kind": "management_review",
        "request_id": f"mgmt_{position}_{seq}_123456789",
        "request_fingerprint": str(900_000 + seq),
        "review_seq": seq,
        "requested_at": requested_at,
        "position_identifier": position,
        "ticket": 325229449,
        "symbol": "EURCAD",
        "action_id": action_id or f"{position}:20260718_management_action_lifecycle_v4:WARNING:stuck_no_mfe:1790000000",
        "proposal": {"requested_action": action, "requested_cut_fraction": cut, "trigger_reason": "stuck_no_mfe",
                     "desired_state": "WARNING", "strikes": 0, "close_strikes": 6,
                     "authority": "deterministic_penalty_watcher"},
        "position": {"is_buy": True, "entry": 1.60636, "sl": 1.60100, "tp": 1.61800, "current_price": 1.60600,
                     "volume": 1.21, "current_r": -0.07, "mfe_r": 0.0054, "mae_r": 0.1888, "minutes_open": 120},
        "invalidation": {"structural_invalid": False, "fvg_invalid": False, "dealing_range_invalid": False,
                         "thesis_raw_breach": False, "thesis_confirmed": False},
        "thesis": {"trade_meta_available": True, "setup_family": "full_po3_reversal", "po3_state": "PO3_CONFIRMED"},
        "state": {"current_state": "WARNING", "previous_state": "HEALTHY"},
    }


class CountingProvider:
    provider_mode = "OPENCODE_API"
    provider_id = "fixture_provider"
    endpoint_class = "remote"

    def __init__(self, answer=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.answer = answer if answer is not None else {
            "verdict": "APPROVE", "reason_codes": ["MOMENTUM_STALLED"], "reason": "Stalled 120 min with no MFE.",
            "confidence": 0.7, "recommended_wait_minutes": 0,
        }
        self.error = error

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(parsed=self.answer, provider_id=self.provider_id, provider_mode=self.provider_mode,
                               actual_model="fixture-model", requested_model="fixture-model")


class ManagementReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bus = Path(self._tmp.name)
        self.logs: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _service(self, provider, **kwargs) -> ManagementReviewService:
        service = ManagementReviewService(self.bus, provider, log=self.logs.append, stable_after_sec=0.0, **kwargs)
        service.ensure()
        return service

    def _publish(self, service: ManagementReviewService, doc: dict, *, utf16: bool = True, age_sec: float = 0.0) -> Path:
        path = service.requests_dir / f"{doc['request_id']}.json"
        text = json.dumps(doc)
        path.write_bytes(text.encode("utf-16") if utf16 else text.encode("utf-8"))  # MQL FILE_TXT is UTF-16 + BOM
        if age_sec:
            stamp = time.time() - age_sec
            os.utime(path, (stamp, stamp))
        return path

    def _drain(self, service: ManagementReviewService) -> list[dict]:
        return [service.process_claimed(path) for path in service.claim_ready(10)]

    def _response(self, service: ManagementReviewService, request_id: str) -> dict:
        return json.loads((service.responses_dir / f"{request_id}.json").read_text(encoding="utf-8"))

    def test_schema_is_strict_and_has_no_action_authority(self) -> None:
        preflight = management_review_schema_preflight()
        self.assertTrue(preflight.valid, preflight.errors)
        self.assertEqual(set(ModelManagementReview.model_fields),
                         {"verdict", "reason_codes", "reason", "confidence", "recommended_wait_minutes"})

    # 1 (python half) + 14
    def test_approve_is_bound_to_the_exact_deterministic_proposal(self) -> None:
        provider = CountingProvider()
        service = self._service(provider)
        doc = _request(cut=0.25)
        self._publish(service, doc)
        (envelope,) = self._drain(service)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["role"], "management_review")
        written = self._response(service, doc["request_id"])
        self.assertEqual(written, envelope)
        self.assertEqual(written["status"], "RESOLVED")
        self.assertEqual(written["verdict"], "APPROVE")
        for key in ("request_id", "request_fingerprint", "position_identifier", "action_id", "requested_at"):
            self.assertEqual(written[key], doc[key])
        self.assertEqual(written["requested_action"], "PARTIAL_CLOSE")
        self.assertAlmostEqual(written["requested_cut_fraction"], 0.25)
        self.assertEqual(written["confidence_authority"], "diagnostic_only")
        self.assertEqual(written["recommended_wait_authority"], "diagnostic_only")
        self.assertEqual(written["cooldown_authority"], "mql_input_InpPenaltyCooldownMin")
        self.assertFalse((service.processing_dir / f"{doc['request_id']}.json").exists())

    # 14
    def test_model_cannot_smuggle_a_changed_cut_or_action(self) -> None:
        answer = {"verdict": "APPROVE", "reason_codes": ["RISK_REDUCTION_PRUDENT"], "reason": "cut more",
                  "confidence": 0.9, "recommended_wait_minutes": 0, "requested_cut_fraction": 1.0,
                  "requested_action": "FULL_CLOSE"}
        provider = CountingProvider(answer=answer)
        service = self._service(provider)
        doc = _request(cut=0.25)
        self._publish(service, doc)
        (envelope,) = self._drain(service)
        self.assertEqual(envelope["status"], "ERROR")
        self.assertEqual(envelope["verdict"], "NONE")
        self.assertEqual(envelope["error_category"], "STRUCTURED_RESPONSE_INVALID")
        self.assertAlmostEqual(envelope["requested_cut_fraction"], 0.25)
        self.assertEqual(envelope["requested_action"], "PARTIAL_CLOSE")

    # 2 (python half)
    def test_deny_is_returned_as_a_bound_non_executing_verdict(self) -> None:
        provider = CountingProvider(answer={"verdict": "DENY", "reason_codes": ["STRUCTURE_INTACT"],
                                            "reason": "Swing low intact.", "confidence": 0.6,
                                            "recommended_wait_minutes": 45})
        service = self._service(provider)
        doc = _request()
        self._publish(service, doc)
        (envelope,) = self._drain(service)
        self.assertEqual(envelope["status"], "RESOLVED")
        self.assertEqual(envelope["verdict"], "DENY")
        self.assertEqual(envelope["recommended_wait_minutes"], 45)
        self.assertEqual(envelope["recommended_wait_authority"], "diagnostic_only")

    # 8 + restart
    def test_duplicate_publication_never_causes_a_second_provider_call(self) -> None:
        provider = CountingProvider()
        service = self._service(provider)
        doc = _request()
        self._publish(service, doc)
        first = self._drain(service)[0]
        for _ in range(3):  # timer ticks / EA restart re-publishing the same proposal
            self._publish(service, doc)
            replay = self._drain(service)[0]
            self.assertEqual(replay["response_fingerprint"], first["response_fingerprint"])
        restarted = self._service(provider)  # new gate process, same bus
        self._publish(restarted, doc)
        self._drain(restarted)
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(any("duplicate_suppressed" in line for line in self.logs))

    def test_process_death_mid_call_is_never_resubmitted(self) -> None:
        provider = CountingProvider()
        service = self._service(provider)
        doc = _request()
        path = self._publish(service, doc)
        (claimed,) = service.claim_ready(1)
        self.assertTrue(service._create_claim({"request_id": doc["request_id"],
                                               "request_fingerprint": doc["request_fingerprint"]}))
        restarted = self._service(provider)
        self.assertEqual(restarted.recover(), 1)
        envelope = self._response(restarted, doc["request_id"])
        self.assertEqual(envelope["status"], "ERROR")
        self.assertEqual(envelope["error_category"], "PROVIDER_OUTCOME_UNKNOWN")
        self.assertEqual(len(provider.calls), 0)
        self.assertFalse(path.exists() or claimed.exists())

    # 7 (python half)
    def test_fresh_proposal_after_cooldown_gets_exactly_one_new_call(self) -> None:
        provider = CountingProvider()
        service = self._service(provider)
        self._publish(service, _request(1))
        self._drain(service)
        self._publish(service, _request(2, requested_at=1_790_001_200))
        self._drain(service)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual([c["request_metadata"]["request_id"] for c in provider.calls],
                         [f"mgmt_{POSITION_ID}_1_123456789", f"mgmt_{POSITION_ID}_2_123456789"])

    # 10
    def test_stale_request_is_answered_without_a_provider_call(self) -> None:
        provider = CountingProvider()
        service = self._service(provider, request_max_age_sec=60.0)
        doc = _request()
        self._publish(service, doc, age_sec=600.0)
        (envelope,) = self._drain(service)
        self.assertEqual((envelope["status"], envelope["verdict"], envelope["error_category"]),
                         ("ERROR", "NONE", "REQUEST_STALE"))
        self.assertEqual(provider.calls, [])

    # 11
    def test_unbound_request_identity_is_rejected(self) -> None:
        provider = CountingProvider()
        service = self._service(provider)
        wrong_position = _request()
        wrong_position["request_id"] = "mgmt_999_1_123456789"
        wrong_action = _request(2, action_id="111:foreign_action")
        wrong_seq = _request(3)
        wrong_seq["review_seq"] = 4
        for doc in (wrong_position, wrong_action, wrong_seq):
            self._publish(service, doc)
        envelopes = self._drain(service)
        self.assertEqual(len(envelopes), 3)
        for envelope in envelopes:
            self.assertEqual((envelope["status"], envelope["verdict"]), ("ERROR", "NONE"))
            self.assertEqual(envelope["error_category"], "REQUEST_INVALID")
        self.assertEqual(provider.calls, [])

    # 12
    def test_malformed_request_or_model_json_cannot_approve(self) -> None:
        provider = CountingProvider(answer={"verdict": "APPROVE"})
        service = self._service(provider)
        broken = service.requests_dir / f"mgmt_{POSITION_ID}_9_1.json"
        broken.write_bytes(b'{"schema_version": "20260915_management_ai_review_v1", "request_id": ')
        self._publish(service, _request())
        envelopes = self._drain(service)
        self.assertEqual(sorted(e["status"] for e in envelopes), ["ERROR", "ERROR"])
        self.assertTrue(all(e["verdict"] == "NONE" for e in envelopes))
        self.assertEqual(sorted(e["error_category"] for e in envelopes), ["REQUEST_INVALID", "STRUCTURED_RESPONSE_INVALID"])
        self.assertEqual(len(provider.calls), 1)

    # 13
    def test_unavailable_or_failing_provider_cannot_approve(self) -> None:
        for provider, category in (
            (UnavailableProvider("provider_configuration_invalid"), "PROVIDER_UNAVAILABLE"),
            (CountingProvider(error=ProviderCallError("PROVIDER_TRANSPORT_ERROR", "429")), "PROVIDER_TRANSPORT_ERROR"),
        ):
            with self.subTest(category):
                with tempfile.TemporaryDirectory() as tmp:
                    service = ManagementReviewService(Path(tmp), provider, log=self.logs.append, stable_after_sec=0.0)
                    service.ensure()
                    doc = _request()
                    (service.requests_dir / f"{doc['request_id']}.json").write_text(json.dumps(doc), encoding="utf-8")
                    (envelope,) = [service.process_claimed(p) for p in service.claim_ready(1)]
                    self.assertEqual((envelope["status"], envelope["verdict"]), ("ERROR", "NONE"))
                    self.assertEqual(envelope["error_category"], category)

    def test_full_close_must_carry_cut_fraction_one(self) -> None:
        provider = CountingProvider()
        service = self._service(provider)
        self._publish(service, _request(action="FULL_CLOSE", cut=0.5))
        (envelope,) = self._drain(service)
        self.assertEqual(envelope["error_category"], "REQUEST_INVALID")
        self.assertEqual(provider.calls, [])


class PenaltyWatcherManagementReviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.penalty = read_repo_mql("PenaltyWatcher.mqh")
        cls.config = read_repo_mql("Config.mqh")
        cls.state = read_repo_mql("StateStore.mqh")
        cls.types = read_repo_mql("Types.mqh")
        cls.engine = read_repo_mql("TradeEngine.mqh")
        constants = mql_constants(cls.config)
        cls.cooldown_until = staticmethod(load_mql_function(cls.penalty, "ManagementAiCooldownUntil", constants))
        cls.cooldown_active = staticmethod(load_mql_function(cls.penalty, "ManagementAiCooldownActive", constants))
        cls.timed_out = staticmethod(load_mql_function(cls.penalty, "ManagementAiReviewTimedOut", constants))
        cls.tick = function_body(cls.penalty, "Tick")[1]

    def _body(self, name: str) -> str:
        return function_body(self.penalty, name)[1]

    def test_cooldown_input_is_the_existing_penalty_cooldown(self) -> None:
        self.assertRegex(self.config, r"input int\s+InpPenaltyCooldownMin\s+= 20;")
        self.assertNotIn("InpPenaltyAiDenialCooldownMin", self.config)
        await_body = self._body("_AwaitManagementAiReview")
        unresolved = self._body("_ResolveManagementAiUnresolved")
        for body in (await_body, unresolved):
            self.assertIn("ManagementAiCooldownUntil((long)now, InpPenaltyCooldownMin)", body)
            self.assertNotIn("recommended_wait", body)

    # 3
    def test_deny_starts_the_configured_cooldown(self) -> None:
        self.assertEqual(self.cooldown_until(1_000_000, 20)[0], 1_000_000 + 20 * 60)
        self.assertEqual(self.cooldown_until(1_000_000, 0)[0], 1_000_000 + 60)
        deny = self._body("_AwaitManagementAiReview").split('if(verdict == "APPROVE"){', 1)[1].split("return true;", 1)[1]
        self.assertIn('_SetActionLifecycle(st, "AI_DENIED"', deny)
        self.assertIn('_SetActionLifecycle(st, "AI_DENIAL_COOLDOWN"', deny)
        self.assertIn("st.management_ai_denied_at = now;", deny)

    # 2 (mql half)
    def test_deny_executes_nothing_and_counts_no_strike(self) -> None:
        deny = self._body("_AwaitManagementAiReview").split("return true;", 1)[1]
        for forbidden in ("PositionClose", "strikes++", "last_reduction_at", "_RememberExecutedAction",
                          "_ProcessPendingAction", "action_executed"):
            self.assertNotIn(forbidden, deny)
        pending = self._body("_ActionLifecyclePending")
        for state in ("AI_DENIED", "AI_DENIAL_COOLDOWN", "AI_REVIEW_UNRESOLVED", "AI_COOLDOWN_EXPIRED"):
            self.assertNotIn(f'"{state}"', pending)

    # 1 (mql half)
    def test_approve_makes_the_frozen_action_eligible_and_executes_it_in_the_same_pass(self) -> None:
        approve = self._body("_AwaitManagementAiReview").split('if(verdict == "APPROVE"){', 1)[1].split("return true;", 1)[0]
        self.assertIn('_SetActionLifecycle(st, "AI_APPROVED"', approve)
        self.assertIn('_SetActionLifecycle(st, "ELIGIBLE"', approve)
        for name in ("_AwaitManagementAiReview", "_ValidateManagementAiResponse", "_StartManagementAiReview"):
            body = self._body(name)
            self.assertIsNone(re.search(r"st\.requested_cut_fraction\s*=", body), name)
            self.assertIsNone(re.search(r"st\.requested_action\s*=", body), name)
        review = self.tick.index("_AwaitManagementAiReview(st, now);")
        execute = self.tick.index("_ProcessPendingAction(trade, st, ticket, vol, now);", review)
        self.assertLess(review, execute)
        process = self._body("_ProcessPendingAction")
        gate = process.index("!_ManagementAiApprovalBound(st)")
        self.assertLess(gate, process.index("trade.PositionClose"))
        self.assertIn("current_volume * st.requested_cut_fraction", process)

    # 4
    def test_cooldown_blocks_execution_proposals_and_calls(self) -> None:
        self.assertTrue(self.cooldown_active(1_000, 2_000)[0])
        self.assertFalse(self.cooldown_active(2_000, 2_000)[0])
        self.assertFalse(self.cooldown_active(2_000, 0)[0])
        branch = re.search(r"if\(management_action_required && management_ai_cooldown_active\)\{(.*?)\} else if\(management_action_required\)\{",
                           self.tick, re.S)
        self.assertIsNotNone(branch)
        code = re.sub(r"//[^\n]*", "", branch.group(1)).strip()
        self.assertEqual(code, "")

    # 5 + 6
    def test_expiry_discards_the_denied_proposal_before_fresh_evaluation(self) -> None:
        expiry = self.tick.index("!ManagementAiCooldownActive((long)now, (long)st.management_ai_cooldown_until)")
        required = self.tick.index("bool management_action_required")
        self.assertLess(expiry, required)
        block = self.tick[expiry:required]
        self.assertIn('st.action_id = "";', block)
        self.assertIn('_SetActionLifecycle(st, "AI_COOLDOWN_EXPIRED"', block)
        self.assertIn("StringLen(st.action_id) == 0", self.tick)  # empty id => new proposal when still required
        starts = [m.start() for m in re.finditer(r"_StartManagementAiReview\(", self.tick)]
        self.assertEqual(len(starts), 1)
        guarded = self.tick.rfind("} else if(management_action_required){", 0, starts[0])
        self.assertGreater(guarded, expiry)

    # 7 + 8 (mql half)
    def test_one_request_per_proposal_across_ticks(self) -> None:
        self.assertIn("if(!_ManagementAiRequestBound(st))", self.tick)
        start = self._body("_StartManagementAiReview")
        self.assertIn("st.management_ai_review_seq++;", start)
        self.assertIn('"mgmt_" + IntegerToString(st.position_identifier)', start)
        self.assertIn("m_immediate_persist_requested = true;", start)
        queue = self._body("_QueueManagementAction")
        self.assertIn("st.action_id == action_id", queue.split("\n", 3)[1] + queue)
        self.assertIn("if(m_penalty.ConsumeImmediatePersistRequest()) _PersistPenaltyStates();", self.engine)

    # 9
    def test_restart_restores_the_absolute_cooldown(self) -> None:
        fields = ("management_ai_review_seq", "management_ai_request_id", "management_ai_action_id",
                  "management_ai_request_fingerprint", "management_ai_verdict", "management_ai_reason_codes",
                  "management_ai_requested_at", "management_ai_resolved_at", "management_ai_denied_at",
                  "management_ai_cooldown_until", "management_ai_provider", "management_ai_model",
                  "management_ai_response_fingerprint")
        to_json = function_body(self.state, "PenaltyToJson")[1]
        from_json = function_body(self.state, "PenaltyFromJson")[1]
        for field in fields:
            self.assertIn(f'"{field}"', to_json, field)
            self.assertIn(f'"{field}"', from_json, field)
            self.assertRegex(self.types, rf"\b{field};")
        self.assertNotIn("management_ai_cooldown_until", function_body(self.penalty, "RestoreStates")[1])
        denied_at, restart_at = 1_790_000_000, 1_790_000_000 + 7 * 60
        until = self.cooldown_until(denied_at, 20)[0]
        self.assertTrue(self.cooldown_active(restart_at, until)[0])
        self.assertEqual(until - restart_at, 13 * 60)

    # 10 + 11 + 12 (mql half)
    def test_mql_validator_rejects_stale_unbound_and_malformed_responses(self) -> None:
        self.assertTrue(self.timed_out(1_000 + 181, 1_000, 180)[0])
        self.assertFalse(self.timed_out(1_000 + 180, 1_000, 180)[0])
        self.assertTrue(self.timed_out(5_000, 0, 180)[0])
        body = self._body("_ValidateManagementAiResponse")
        ordered = ["JsonValidateDocumentStrict", "response_schema_version_mismatch", "response_request_id_mismatch",
                   "response_error_envelope:", "response_request_fingerprint_mismatch", "response_position_mismatch",
                   "response_action_id_mismatch", "response_requested_action_altered", "response_cut_fraction_altered",
                   "response_requested_at_mismatch", "response_stale", "response_verdict_invalid"]
        positions = [body.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('value != "APPROVE" && value != "DENY"', body)
        self.assertLess(body.index("response_verdict_invalid"), body.index("verdict = value;"))
        await_body = self._body("_AwaitManagementAiReview")
        self.assertLess(await_body.index("_ValidateManagementAiResponse"), await_body.index('if(verdict == "APPROVE")'))

    # 13 (mql half): no response or unreadable response is never an approval
    def test_missing_response_times_out_to_unresolved_not_approval(self) -> None:
        await_body = self._body("_AwaitManagementAiReview")
        missing = await_body.split("if(!m_bus.Exists(response_path)){", 1)[1].split("}", 1)[0]
        self.assertIn('_ResolveManagementAiUnresolved(st, "management_ai_review_timeout", now)', missing)
        unresolved = self._body("_ResolveManagementAiUnresolved")
        self.assertIn('st.management_ai_verdict = "UNRESOLVED";', unresolved)
        self.assertNotIn("ELIGIBLE", unresolved)
        self.assertIn('st.management_ai_verdict == "APPROVE"', self._body("_ManagementAiApprovalBound"))

    # 15
    def test_account_level_protections_do_not_pass_through_the_review(self) -> None:
        self.assertNotIn("PositionModify", self.penalty)
        for name in ("Risk.mqh", "TradeEngine.mqh", "AIGateBridge.mqh"):
            source = read_repo_mql(name)
            self.assertNotIn("_ManagementAiReviewRequired", source, name)
            self.assertNotIn("ManagementReviewRequestDir", source, name)
        self.assertIn('requested_action != "NO_BROKER_ACTION"', self._body("_QueueManagementAction"))
        required = self._body("_ManagementAiReviewRequired")
        self.assertIn("InpPenaltyAiReviewEnable", required)
        self.assertIn("InpPenaltyAiReviewInTester", required)

    def test_gate_serves_reviews_from_the_bounded_worker_pool(self) -> None:
        gate = (Path(__file__).resolve().parents[1] / "ai_gate.py").read_text(encoding="utf-8")
        self.assertIn("management_review_service.claim_ready(claim_slots)", gate)
        self.assertIn("request_pool.submit(\n                        management_review_service.process_claimed", gate)
        self.assertIn("management_review_service.recover()", gate)
        self.assertEqual(self.config.count('MANAGEMENT_AI_REVIEW_SCHEMA_VERSION = "' + MANAGEMENT_REVIEW_SCHEMA_VERSION + '"'), 1)


if __name__ == "__main__":
    unittest.main()
