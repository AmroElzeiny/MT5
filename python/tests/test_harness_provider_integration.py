"""Integration tests for the deterministic local OpenAI-compatible harness.

These tests deliberately use the **real** ``LocalOpenAICompatibleProvider`` over
**real** HTTP against a real local server.  Nothing about the transport, the
strict ``json_schema`` request, the pydantic validation, or the provider result
construction is mocked.  Only the model's judgement is deterministic.

That distinction matters: the previous repair attempts validated hand-written
dicts against pydantic models, which proves the model class is self-consistent
but proves nothing about whether the provider can actually round-trip a strict
schema through an OpenAI-compatible endpoint.  These tests close that gap.
"""

from __future__ import annotations

import json
import socket
import sys
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_provider import PROVIDER_MODE_LOCAL, LocalOpenAICompatibleProvider  # noqa: E402
from evidence_catalog import EvidenceCatalog, build_evidence_catalog  # noqa: E402
from structured_models import ModelAIGateOutput  # noqa: E402
from tools.harness_local_provider import (  # noqa: E402
    HARNESS_PROVIDER_VERSION,
    HarnessMalformation,
    HarnessPolicy,
    build_role_output,
    extract_catalog,
    serve,
)

CANDIDATE_COUNT = 3


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _envelope() -> dict:
    """A canonical-shaped envelope the production catalog builder understands."""
    return {
        "entry_and_invalidation": {
            "candidates": [
                {
                    "candidate_index": index,
                    "candidate_id": f"cand-{index}",
                    "candidate_hash": f"hash-{index}" * 2,
                    "symbol": "GOLD",
                    "direction": "BUY",
                    "is_buy": True,
                    "setup_code": f"PO3_FVG_{index}",
                    "setup_family": "PO3",
                    "entry_model": "FVG_RETEST",
                    "session_name": "LON",
                    "authoritative_numbers": {
                        "entry": {"value": 2000.0 + index},
                        "stop": {"value": 1990.0 + index},
                        "target": {"value": 2020.0 + index},
                        "atr": {"value": 4.5 + index},
                    },
                }
                for index in range(CANDIDATE_COUNT)
            ]
        }
    }


def _catalog() -> EvidenceCatalog:
    """The real, production-built catalog -- not a hand-made stand-in."""
    return build_evidence_catalog(_envelope())


def _evidence_payload() -> dict:
    catalog = _catalog()
    return {
        "request_id": "harness-integration-1",
        "candidates": [{"index": i} for i in range(CANDIDATE_COUNT)],
        "evidence_catalog": {
            "catalog_version": catalog.catalog_version,
            "catalog_hash": catalog.catalog_hash,
            "items": catalog.provider_rows(),
        },
    }


def _provider(port: int, *, timeout_sec: float = 30.0) -> LocalOpenAICompatibleProvider:
    return LocalOpenAICompatibleProvider(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key="harness-not-a-secret",
        analyst_model="harness-deterministic-v1",
        critic_model="harness-deterministic-v1",
        adjudicator_model="harness-deterministic-v1",
        fallback_models=(),
        healthcheck_path="/models",
        timeout_sec=timeout_sec,
        max_retries=0,
        max_output_tokens=8192,
        temperature=0.0,
        top_p=1.0,
        seed=7,
        enable_thinking=False,
        require_json_schema=True,
        parallelism=1,
        context_budget_tokens=200_000,
        circuit_failure_threshold=99,
        circuit_cooldown_sec=0.0,
        log=lambda _msg: None,
    )


class HarnessServerTestCase(unittest.TestCase):
    """Base class that runs one harness server per test with a given policy."""

    policy: HarnessPolicy

    def start(self, policy: HarnessPolicy):
        self.policy = policy
        port = _free_port()
        server, _thread = serve(policy, port=port)
        self.addCleanup(server.shutdown)
        self.port = port
        return server

    def call_analyst(self, *, timeout_sec: float = 30.0):
        provider = _provider(self.port, timeout_sec=timeout_sec)
        return provider.generate_structured(
            role="analyst",
            system_prompt="Deterministic harness integration test.",
            evidence=_evidence_payload(),
            response_schema=ModelAIGateOutput,
            request_metadata={
                "request_id": "harness-integration-1",
                "request_identity_hash": "0" * 32,
                "max_output_tokens": 8192,
            },
        )


class HarnessTransportTests(HarnessServerTestCase):
    def test_models_healthcheck_is_openai_shaped(self) -> None:
        self.start(HarnessPolicy())
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1/models", timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body["object"], "list")
        self.assertTrue(body["data"])
        self.assertIn("id", body["data"][0])

    def test_real_provider_round_trips_strict_schema(self) -> None:
        """The whole point: real HTTP + real strict json_schema + real parse."""
        self.start(HarnessPolicy(selected_candidate_index=1))
        result = self.call_analyst()

        self.assertEqual(result.provider_mode, PROVIDER_MODE_LOCAL)
        self.assertEqual(result.provider_id, "local_openai_compatible")
        self.assertIsInstance(result.parsed, ModelAIGateOutput)
        self.assertEqual(result.parsed.decision_quality_tier, "FULL_STRUCTURED")
        self.assertEqual(len(result.parsed.candidate_assessments), CANDIDATE_COUNT)
        self.assertEqual(result.transport_retry_count, 0)
        self.assertEqual(result.schema_retry_count, 0)
        self.assertEqual(result.model_fingerprint, HARNESS_PROVIDER_VERSION)
        self.assertGreater(result.latency_sec, 0.0)

    def test_provider_mode_is_one_mql_accepts(self) -> None:
        """MQL hardcodes an allowlist; the harness must not need it widened.

        AIGateBridge.mqh:1616 accepts only REMOTE_API and
        LOCAL_OPENAI_COMPATIBLE.  If this ever fails, the harness has stopped
        being a genuine local endpoint and started being a special case.
        """
        self.start(HarnessPolicy())
        result = self.call_analyst()
        self.assertIn(
            result.provider_mode,
            {"REMOTE_API", "LOCAL_OPENAI_COMPATIBLE"},
        )

    def test_healthcheck_reports_structured_output_available(self) -> None:
        """Regression: the gate refuses a provider whose probe reports ok=false.

        The generic boolean default made ``StructuredCapabilityProbe.ok`` False,
        so ``healthcheck(probe_structured=True)`` returned
        ``local_structured_output_probe_failed`` and the gate would not use the
        harness at all. This is the first blocker the real run exposed, and it
        was invisible to every schema-level test because the payload was valid.
        """
        self.start(HarnessPolicy())
        provider = _provider(self.port)
        health = provider.healthcheck(probe_structured=True)

        self.assertTrue(health.model_available, health.reason)
        self.assertTrue(health.structured_output_available, health.reason)
        self.assertTrue(health.healthy, health.reason)
        self.assertEqual(health.reason, "")

    def test_capability_probe_returns_ok_true(self) -> None:
        from structured_models import StructuredCapabilityProbe

        payload = build_role_output("StructuredCapabilityProbe", {}, HarnessPolicy())
        self.assertIs(StructuredCapabilityProbe.model_validate(payload).ok, True)

    def test_usage_and_actual_model_are_reported(self) -> None:
        self.start(HarnessPolicy())
        result = self.call_analyst()
        self.assertIsNotNone(result.prompt_tokens)
        self.assertIsNotNone(result.completion_tokens)
        self.assertGreater(result.completion_tokens or 0, 0)
        self.assertEqual(result.actual_model, "harness-deterministic-v1")


class HarnessDecisionTests(HarnessServerTestCase):
    def test_approve_selects_only_the_named_candidate(self) -> None:
        self.start(HarnessPolicy(selected_candidate_index=1))
        parsed = self.call_analyst().parsed

        self.assertEqual(parsed.selected_candidate_index, 1)
        states = {a.candidate_index: a.decision_state for a in parsed.candidate_assessments}
        self.assertEqual(states[1], "APPROVE")
        self.assertEqual(states[0], "REJECT")
        self.assertEqual(states[2], "REJECT")

    def test_approved_candidate_has_no_veto_and_nonzero_risk(self) -> None:
        self.start(HarnessPolicy(selected_candidate_index=1))
        parsed = self.call_analyst().parsed
        approved = next(a for a in parsed.candidate_assessments if a.candidate_index == 1)

        self.assertTrue(approved.raw_allow)
        self.assertFalse(approved.veto.enabled)
        self.assertGreater(approved.suggested_risk_multiplier, 0.0)

    def test_rejected_candidates_carry_veto_and_zero_risk(self) -> None:
        self.start(HarnessPolicy(selected_candidate_index=1))
        parsed = self.call_analyst().parsed
        for assessment in parsed.candidate_assessments:
            if assessment.candidate_index == 1:
                continue
            self.assertFalse(assessment.raw_allow)
            self.assertTrue(assessment.veto.enabled)
            self.assertEqual(assessment.suggested_risk_multiplier, 0.0)

    def test_reject_policy_approves_nothing(self) -> None:
        self.start(HarnessPolicy(decision="REJECT"))
        parsed = self.call_analyst().parsed
        self.assertTrue(all(not a.raw_allow for a in parsed.candidate_assessments))
        self.assertTrue(
            all(a.decision_state == "REJECT" for a in parsed.candidate_assessments)
        )

    def test_verdict_always_matches_decision_state(self) -> None:
        """decision_integrity requires verdict == decision_state."""
        self.start(HarnessPolicy(selected_candidate_index=2))
        parsed = self.call_analyst().parsed
        for assessment in parsed.candidate_assessments:
            self.assertEqual(assessment.verdict, assessment.decision_state)


class HarnessEvidenceTests(HarnessServerTestCase):
    def test_cited_ids_come_from_the_catalog_and_own_candidate(self) -> None:
        self.start(HarnessPolicy(selected_candidate_index=1))
        parsed = self.call_analyst().parsed

        ids_by_candidate, all_ids, _ = extract_catalog(_evidence_payload())
        for assessment in parsed.candidate_assessments:
            self.assertTrue(assessment.evidence_ref_ids, "must cite at least one id")
            for ref in assessment.evidence_ref_ids:
                self.assertIn(ref, all_ids)
                self.assertIn(ref, ids_by_candidate[assessment.candidate_index])

    def test_cited_ids_resolve_through_the_real_catalog(self) -> None:
        """End-to-end: harness IDs must survive real EvidenceCatalog.resolve."""
        self.start(HarnessPolicy(selected_candidate_index=1))
        parsed = self.call_analyst().parsed

        catalog = _catalog()
        for assessment in parsed.candidate_assessments:
            resolution = catalog.resolve(
                assessment.evidence_ref_ids,
                candidate_index=assessment.candidate_index,
            )
            self.assertFalse(
                resolution.unknown_ids,
                f"unresolved ids for candidate {assessment.candidate_index}",
            )
            self.assertTrue(resolution.resolved_paths)


class HarnessAuthoritativeEnvelopeTests(unittest.TestCase):
    """The harness must survive the REAL authoritative envelope validator.

    Regression for the second blocker the live run exposed.  The provider
    returned ``quality_tier=FULL_STRUCTURED`` and passed raw schema validation,
    identity validation, and evidence resolution -- then
    ``authoritative_envelope_validation`` failed with
    ``invalid_fields=historical_evidence_state,veto.code,veto.disabled_payload``
    and every response was written as ``DEGRADED_NON_TRADING``.

    Pydantic could not catch this: all three fields were type-correct.  Only
    ``decision_integrity.validate_candidate_assessment`` knows the value
    domains, so the harness is now validated against it directly.
    """

    def _authoritative(self, model_assessment: dict, *, index: int) -> dict:
        """Wrap model-owned output with the Python-owned fields the gate adds."""
        from decision_integrity import (
            AI_PROMPT_CONTRACT_VERSION,
            AI_ROLE_CONTRACT_VERSION,
            AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        )

        merged = dict(model_assessment)
        veto = dict(merged.pop("veto", {}))
        merged["veto"] = {
            "enabled": veto.get("enabled", False),
            "code": veto.get("code", ""),
            "reason": veto.get("reason", ""),
            "evidence_fields": [
                f"entry_and_invalidation.candidates.{index}.candidate_id"
                for _ in veto.get("evidence_ref_ids", [])
            ],
        }
        merged["evidence_refs"] = [
            f"entry_and_invalidation.candidates.{index}.candidate_id"
            for _ in merged.pop("evidence_ref_ids", [])
        ] or [f"entry_and_invalidation.candidates.{index}.candidate_id"]

        arbitration = dict(merged.get("target_arbitration") or {})
        arbitration["target_arbitration_schema_version"] = AI_TARGET_ARBITRATION_SCHEMA_VERSION
        arbitration["prompt_contract_version"] = AI_PROMPT_CONTRACT_VERSION
        merged["target_arbitration"] = arbitration

        merged.update(
            {
                "request_id": "harness-req",
                "request_identity_hash": "0" * 32,
                "provider_id": "local_openai_compatible",
                "model_id": "harness-deterministic-v1",
                "role_schema_version": AI_ROLE_CONTRACT_VERSION,
                "role_contract_version": AI_ROLE_CONTRACT_VERSION,
                "role": "analyst",
                "candidate_id": f"cand-{index}",
                "candidate_hash": "h" * 16,
                "request_execution_fingerprint": "f" * 16,
                "assessed_execution_fingerprint": "f" * 16,
                "setup_taxonomy_version": "v1",
                "setup_taxonomy_enum": "PO3_FVG",
                "taxonomy_mapping_source": "deterministic",
                "rule_score": 7.0,
                "blended_legacy_score": 7.0,
                "legacy_agreement_confidence": 0.8,
                "selected_target_identity": "LIQUIDITY_TARGET",
                "selected_target_price": 2020.0,
                "entry": 2000.0,
                "sl": 1990.0,
                "tp1": 2010.0,
                "tp2": 2020.0,
                "model_version": "harness-deterministic-v1",
                "calibration_bucket": "",
                "calibration_sample_size": 0,
                "calibration_model_version": "",
                "calibration_data_window_start": "",
                "calibration_data_window_end": "",
                "calibration_available": False,
                "calibrated_win_probability": None,
                "expected_net_r": None,
                "oos_predicted_probability": None,
                "calibration_lower_bound": None,
                "calibration_upper_bound": None,
            }
        )
        return merged

    def _assessments(self, policy: HarnessPolicy) -> list[dict]:
        payload = build_role_output("ModelAIGateOutput", _evidence_payload(), policy)
        return payload["candidate_assessments"]

    def test_approved_assessment_passes_real_validator(self) -> None:
        from decision_integrity import validate_candidate_assessment

        policy = HarnessPolicy(selected_candidate_index=0)
        approved = self._assessments(policy)[0]
        result = validate_candidate_assessment(self._authoritative(approved, index=0))

        self.assertEqual(list(result.invalid_fields), [], f"invalid: {result.invalid_fields}")
        self.assertTrue(result.valid, f"missing={result.missing_fields}")

    def test_rejected_assessment_passes_real_validator(self) -> None:
        from decision_integrity import validate_candidate_assessment

        policy = HarnessPolicy(selected_candidate_index=0)
        rejected = self._assessments(policy)[1]
        result = validate_candidate_assessment(self._authoritative(rejected, index=1))

        self.assertEqual(list(result.invalid_fields), [], f"invalid: {result.invalid_fields}")
        self.assertTrue(result.valid, f"missing={result.missing_fields}")

    def test_historical_evidence_state_is_an_accepted_value(self) -> None:
        for assessment in self._assessments(HarnessPolicy(selected_candidate_index=0)):
            with self.subTest(index=assessment["candidate_index"]):
                self.assertIn(
                    assessment["historical_evidence_state"],
                    {"SUPPORTIVE", "MIXED", "ADVERSE", "INSUFFICIENT_SAMPLE"},
                )

    def test_disabled_veto_carries_no_payload(self) -> None:
        approved = self._assessments(HarnessPolicy(selected_candidate_index=0))[0]
        veto = approved["veto"]

        self.assertFalse(veto["enabled"])
        self.assertEqual(veto["code"], "")
        self.assertEqual(veto["reason"], "")
        self.assertEqual(veto["evidence_ref_ids"], [])

    def test_enabled_veto_uses_a_recognised_code_and_full_payload(self) -> None:
        from decision_integrity import LLM_VETO_CODES

        rejected = self._assessments(HarnessPolicy(selected_candidate_index=0))[1]
        veto = rejected["veto"]

        self.assertTrue(veto["enabled"])
        self.assertIn(veto["code"], LLM_VETO_CODES)
        self.assertTrue(veto["reason"])
        self.assertTrue(veto["evidence_ref_ids"])


class HarnessRoleContractTests(unittest.TestCase):
    """Regressions for blockers 3 and 4, both found only by running the gate.

    Blocker 3: target arbitration is a choice among *supplied* options.  The
    harness emitted placeholder zeros for ``chosen_tp1``/``chosen_tp2``, so
    every request failed with ``selected target price does not match target
    arbitration`` and was written ``DEGRADED_NON_TRADING``.

    Blocker 4: each role has its own verdict vocabulary.  Emitting the analyst's
    ``APPROVE``/``REJECT`` for the critic raised
    ``ValueError:critic_verdict_invalid`` and killed the pipeline after the
    analyst stage had started passing.
    """

    def _evidence(self) -> dict:
        return {
            "candidates": [{"i": 0}],
            "evidence_catalog": {
                "items": [
                    {"id": 1, "p": "entry_and_invalidation.candidates.0.candidate_id", "v": "c0", "c": 0},
                    {"id": 2, "p": "entry_and_invalidation.candidates.0.authoritative_numbers.tp1.value", "v": 4007.16, "c": 0},
                    {"id": 3, "p": "entry_and_invalidation.candidates.0.authoritative_numbers.tp2.value", "v": 3995.8675, "c": 0},
                    {"id": 4, "p": "entry_and_invalidation.candidates.0.target_model", "v": "next_liquidity_session_range", "c": 0},
                ]
            },
        }

    def test_chosen_target_comes_from_the_offered_values(self) -> None:
        from structured_models import ModelAIGateOutput as M

        out = build_role_output("ModelAIGateOutput", self._evidence(), HarnessPolicy(selected_candidate_index=0))
        arb = M.model_validate(out).candidate_assessments[0].target_arbitration

        self.assertEqual(arb.chosen_tp2, 3995.8675)
        self.assertEqual(arb.chosen_tp1, 4007.16)
        self.assertEqual(arb.chosen_target_model, "next_liquidity_session_range")

    def test_chosen_tp2_is_positive_for_an_approval(self) -> None:
        """decision_integrity rejects chosen_tp2 <= 0 outright."""
        from structured_models import ModelAIGateOutput as M

        out = build_role_output("ModelAIGateOutput", self._evidence(), HarnessPolicy(selected_candidate_index=0))
        arb = M.model_validate(out).candidate_assessments[0].target_arbitration
        self.assertGreater(arb.chosen_tp2, 0.0)

    def test_critic_uses_the_critic_verdict_vocabulary(self) -> None:
        from structured_models import ModelCriticDecision as M

        out = build_role_output("ModelCriticDecision", self._evidence(), HarnessPolicy(selected_candidate_index=0))
        parsed = M.model_validate(out)
        self.assertIn(parsed.verdict, {"PASS", "BLOCK", "ABSTAIN"})
        self.assertEqual(parsed.verdict, "PASS")

    def test_adjudicator_uses_the_adjudicator_verdict_vocabulary(self) -> None:
        from structured_models import ModelAdjudicatorDecision as M

        out = build_role_output("ModelAdjudicatorDecision", self._evidence(), HarnessPolicy(selected_candidate_index=0))
        parsed = M.model_validate(out)
        self.assertIn(parsed.verdict, {"UPHOLD_APPROVE", "UPHOLD_BLOCK", "ABSTAIN"})
        self.assertEqual(parsed.verdict, "UPHOLD_APPROVE")

    def test_critic_pass_carries_no_blocking_objections(self) -> None:
        out = build_role_output("ModelCriticDecision", self._evidence(), HarnessPolicy(selected_candidate_index=0))
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["blocking_objections"], [])

    def test_critic_block_carries_at_least_one_valid_objection(self) -> None:
        from decision_integrity import LLM_VETO_CODES

        out = build_role_output("ModelCriticDecision", self._evidence(), HarnessPolicy(decision="REJECT"))
        self.assertEqual(out["verdict"], "BLOCK")
        self.assertTrue(out["blocking_objections"])
        for objection in out["blocking_objections"]:
            self.assertIn(objection["code"], LLM_VETO_CODES)
            self.assertTrue(objection["evidence_ref_ids"])

    def test_all_three_roles_validate_against_their_schemas(self) -> None:
        import structured_models as sm

        for schema in ("ModelAIGateOutput", "ModelCriticDecision", "ModelAdjudicatorDecision"):
            with self.subTest(schema=schema):
                out = build_role_output(schema, self._evidence(), HarnessPolicy(selected_candidate_index=0))
                getattr(sm, schema).model_validate(out)


class HarnessNegativeFixtureTests(HarnessServerTestCase):
    """Each malformation must be rejected, and rejected for the right reason."""

    def _build(self, malformation: str) -> dict:
        policy = HarnessPolicy(selected_candidate_index=1, malformation=malformation)
        return build_role_output("ModelAIGateOutput", _evidence_payload(), policy)

    def test_empty_evidence_is_rejected_by_strict_schema(self) -> None:
        with self.assertRaises(Exception) as ctx:
            ModelAIGateOutput.model_validate(
                self._build(HarnessMalformation.EMPTY_EVIDENCE)
            )
        self.assertIn("evidence_ref_ids", str(ctx.exception))

    def test_invalid_confidence_band_is_rejected_by_strict_enum(self) -> None:
        with self.assertRaises(Exception) as ctx:
            ModelAIGateOutput.model_validate(
                self._build(HarnessMalformation.INVALID_CONFIDENCE_BAND)
            )
        self.assertIn("confidence_band", str(ctx.exception))

    def test_model_cannot_own_python_contract_versions(self) -> None:
        """The original P0: the LLM must not supply contract identity.

        The provider-facing arbitration model has no such fields, and the model
        is strict, so a model attempting to forge them is rejected outright
        rather than silently downgrading the contract version.
        """
        with self.assertRaises(Exception) as ctx:
            ModelAIGateOutput.model_validate(
                self._build(HarnessMalformation.CONTRACT_VERSION_INJECTION)
            )
        message = str(ctx.exception)
        self.assertIn("Extra inputs are not permitted", message)
        self.assertTrue(
            "prompt_contract_version" in message
            or "target_arbitration_schema_version" in message
        )

    def test_unknown_evidence_id_passes_schema_but_fails_catalog(self) -> None:
        """Layering check: the schema cannot know catalog membership."""
        payload = self._build(HarnessMalformation.UNKNOWN_EVIDENCE_ID)
        parsed = ModelAIGateOutput.model_validate(payload)  # schema-valid

        catalog = _catalog()
        assessment = parsed.candidate_assessments[0]
        resolution = catalog.resolve(
            assessment.evidence_ref_ids,
            candidate_index=assessment.candidate_index,
        )
        self.assertTrue(resolution.unknown_ids, "unknown id must fail closed")

    def test_cross_candidate_evidence_fails_catalog_resolution(self) -> None:
        payload = self._build(HarnessMalformation.CROSS_CANDIDATE_EVIDENCE)
        parsed = ModelAIGateOutput.model_validate(payload)

        catalog = _catalog()
        assessment = parsed.candidate_assessments[0]
        resolution = catalog.resolve(
            assessment.evidence_ref_ids,
            candidate_index=assessment.candidate_index,
        )
        self.assertTrue(
            resolution.unknown_ids or resolution.cross_candidate_ids,
            "citing another candidate's evidence must fail closed",
        )

    def test_candidate_count_short_is_detectable(self) -> None:
        payload = self._build(HarnessMalformation.CANDIDATE_COUNT_SHORT)
        parsed = ModelAIGateOutput.model_validate(payload)
        self.assertEqual(len(parsed.candidate_assessments), CANDIDATE_COUNT - 1)

    def test_candidate_count_long_is_detectable(self) -> None:
        payload = self._build(HarnessMalformation.CANDIDATE_COUNT_LONG)
        parsed = ModelAIGateOutput.model_validate(payload)
        self.assertEqual(len(parsed.candidate_assessments), CANDIDATE_COUNT + 1)

    def test_bad_candidate_index_is_detectable(self) -> None:
        payload = self._build(HarnessMalformation.BAD_CANDIDATE_INDEX)
        parsed = ModelAIGateOutput.model_validate(payload)
        indexes = [a.candidate_index for a in parsed.candidate_assessments]
        self.assertTrue(all(i >= CANDIDATE_COUNT for i in indexes))

    def test_selected_index_out_of_range_is_detectable(self) -> None:
        payload = self._build(HarnessMalformation.SELECTED_INDEX_OUT_OF_RANGE)
        parsed = ModelAIGateOutput.model_validate(payload)
        self.assertGreaterEqual(parsed.selected_candidate_index, CANDIDATE_COUNT)

    def test_verdict_state_mismatch_is_detectable(self) -> None:
        payload = self._build(HarnessMalformation.VERDICT_STATE_MISMATCH)
        parsed = ModelAIGateOutput.model_validate(payload)
        mismatched = [
            a for a in parsed.candidate_assessments if a.verdict != a.decision_state
        ]
        self.assertTrue(mismatched, "harness must actually produce the mismatch")

    def test_non_json_content_fails_the_real_provider(self) -> None:
        """A non-JSON body must surface as a provider error, not a silent pass."""
        self.start(HarnessPolicy(malformation=HarnessMalformation.NON_JSON_CONTENT))
        with self.assertRaises(Exception):
            self.call_analyst()


class HarnessTransportFailureTests(HarnessServerTestCase):
    def test_upstream_failure_after_n_calls_is_raised(self) -> None:
        self.start(HarnessPolicy(fail_after_n_calls=1))
        self.call_analyst()  # first call succeeds
        with self.assertRaises(Exception):
            self.call_analyst()  # second is a 503

    def test_call_log_records_every_provider_call(self) -> None:
        self.start(HarnessPolicy(selected_candidate_index=0))
        self.call_analyst()
        self.call_analyst()

        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/v1/harness/calls", timeout=10
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(body["call_count"], 2)
        for call in body["calls"]:
            self.assertEqual(call["schema_name"], "ModelAIGateOutput")
            self.assertEqual(call["role_hint"], "analyst")
            self.assertEqual(call["candidate_count"], CANDIDATE_COUNT)
            self.assertEqual(call["catalog_size"], len(_catalog()))

    def test_one_provider_call_per_generate_structured(self) -> None:
        """Guards the 'duplicate provider calls' defect class."""
        self.start(HarnessPolicy())
        self.call_analyst()

        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/v1/harness/calls", timeout=10
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(body["call_count"], 1)


class HarnessConsensusTests(HarnessServerTestCase):
    """Drive the REAL ``run_qualitative_consensus`` with the real provider.

    ``critic_verdict_invalid`` was only ever reachable through this function,
    and nothing exercised it end to end: the analyst tests stop at the analyst,
    and the role-contract tests validate a payload without running consensus.
    That gap is why a broken critic vocabulary survived until a Strategy Tester
    run degraded every advisory.

    All five branches are covered, including the two that were previously
    unreachable because the critic could not be steered independently of the
    analyst (the adjudicator is only consulted when the critic does not PASS).
    """

    def _consensus(
        self,
        *,
        analyst_state: str,
        critic_verdict: str = "",
        adjudicator_verdict: str = "",
        confidence_band: str = "HIGH",
    ):
        from decision_pipeline import run_qualitative_consensus

        self.start(
            HarnessPolicy(
                critic_verdict=critic_verdict,
                adjudicator_verdict=adjudicator_verdict,
            )
        )
        catalog = _catalog()
        envelope = _envelope()
        candidate = envelope["entry_and_invalidation"]["candidates"][0]
        return run_qualitative_consensus(
            provider=_provider(self.port),
            evidence=envelope,
            analyst_assessment={
                "candidate_index": 0,
                "candidate_id": candidate["candidate_id"],
                "candidate_hash": candidate["candidate_hash"],
                "decision_state": analyst_state,
                "confidence_band": confidence_band,
                "missing_required_evidence": [],
            },
            request_metadata={
                "request_id": "harness-consensus-1",
                "request_identity_hash": "c" * 32,
            },
            evidence_catalog=catalog,
        )

    def test_analyst_approve_critic_pass_allows(self) -> None:
        result = self._consensus(analyst_state="APPROVE", critic_verdict="PASS")
        self.assertEqual(result.decision_state, "APPROVE")
        self.assertTrue(result.python_allow)
        self.assertEqual(result.reason, "analyst_approve_critic_pass")
        # A PASS must not consult the adjudicator at all.
        self.assertIsNone(result.adjudicator_result)

    def test_analyst_reject_blocks_without_adjudication(self) -> None:
        result = self._consensus(analyst_state="REJECT", critic_verdict="PASS")
        self.assertEqual(result.decision_state, "REJECT")
        self.assertFalse(result.python_allow)
        self.assertEqual(result.reason, "analyst_reject")

    def test_critic_block_with_adjudicator_uphold_block(self) -> None:
        result = self._consensus(
            analyst_state="APPROVE",
            critic_verdict="BLOCK",
            adjudicator_verdict="UPHOLD_BLOCK",
        )
        self.assertEqual(result.decision_state, "REJECT")
        self.assertFalse(result.python_allow)
        self.assertEqual(result.reason, "qualitative_block_upheld")
        self.assertEqual(result.critic["verdict"], "BLOCK")
        self.assertTrue(result.critic["blocking_objections"])
        self.assertEqual(
            result.adjudicator["unresolved_objection_codes"],
            [result.critic["blocking_objections"][0]["code"]],
        )

    def test_critic_block_with_adjudicator_resolving_objections(self) -> None:
        result = self._consensus(
            analyst_state="APPROVE",
            critic_verdict="BLOCK",
            adjudicator_verdict="UPHOLD_APPROVE",
        )
        self.assertEqual(result.decision_state, "APPROVE")
        self.assertTrue(result.python_allow)
        self.assertEqual(result.reason, "adjudicator_resolved_qualitative_dispute")
        # The adjudicator may only approve by resolving EVERY blocking code.
        self.assertEqual(
            sorted(result.adjudicator["resolved_objection_codes"]),
            sorted(row["code"] for row in result.critic["blocking_objections"]),
        )
        self.assertEqual(result.adjudicator["unresolved_objection_codes"], [])

    def test_abstention_resolves_to_abstain(self) -> None:
        result = self._consensus(
            analyst_state="ABSTAIN",
            critic_verdict="ABSTAIN",
            adjudicator_verdict="ABSTAIN",
        )
        self.assertEqual(result.decision_state, "ABSTAIN")
        self.assertFalse(result.python_allow)
        self.assertEqual(result.reason, "qualitative_dispute_unresolved")
        # ABSTAIN must not smuggle a blocking objection through.
        self.assertEqual(result.critic["blocking_objections"], [])

    def test_no_branch_raises_critic_verdict_invalid(self) -> None:
        """The acceptance criterion, stated directly as a test."""
        for analyst_state, critic, adjudicator in (
            ("APPROVE", "PASS", ""),
            ("REJECT", "PASS", ""),
            ("APPROVE", "BLOCK", "UPHOLD_BLOCK"),
            ("APPROVE", "BLOCK", "UPHOLD_APPROVE"),
            ("ABSTAIN", "ABSTAIN", "ABSTAIN"),
        ):
            with self.subTest(analyst=analyst_state, critic=critic):
                try:
                    self._consensus(
                        analyst_state=analyst_state,
                        critic_verdict=critic,
                        adjudicator_verdict=adjudicator,
                    )
                except ValueError as exc:  # pragma: no cover - failure path
                    self.fail(f"consensus raised {exc}")


class CanonicalRoleVocabularyTests(unittest.TestCase):
    """One vocabulary, enforced at the provider boundary.

    Before this, ``ModelCriticDecision.verdict`` and
    ``ModelCriticObjection.code`` were free strings while
    ``decision_pipeline._validate_objections`` enforced closed sets.  A
    structurally valid provider response could therefore be paid for, counted,
    and only then rejected as ``critic_verdict_invalid`` /
    ``critic_objection_code_unknown``.
    """

    def test_validator_and_schema_share_one_veto_vocabulary(self) -> None:
        from decision_integrity import LLM_VETO_CODES
        from structured_models import QUALITATIVE_VETO_CODES

        self.assertEqual(set(LLM_VETO_CODES), set(QUALITATIVE_VETO_CODES))

    def test_every_allowed_objection_code_is_accepted(self) -> None:
        from structured_models import QUALITATIVE_VETO_CODES, ModelCriticObjection

        for code in QUALITATIVE_VETO_CODES:
            with self.subTest(code=code):
                objection = ModelCriticObjection.model_validate(
                    {"code": code, "evidence_ref_ids": [1], "reason": "r"}
                )
                self.assertEqual(objection.code, code)

    def test_unknown_objection_codes_fail_schema_validation(self) -> None:
        import pydantic

        from structured_models import ModelCriticObjection

        for code in (
            "",
            "ai_veto_unknown",
            "AI_VETO_MISSING_MANDATORY_EVIDENCE",
            "ai_veto_missing_mandatory_evidence ",
            "structural_contradiction",
        ):
            with self.subTest(code=code):
                with self.assertRaises(pydantic.ValidationError):
                    ModelCriticObjection.model_validate(
                        {"code": code, "evidence_ref_ids": [1], "reason": "r"}
                    )

    def test_critic_verdict_enum_rejects_the_analyst_vocabulary(self) -> None:
        import pydantic

        from structured_models import CRITIC_VERDICTS, ModelCriticDecision

        def payload(verdict: str) -> dict:
            return {
                "candidate_index": 0,
                "verdict": verdict,
                "blocking_objections": [],
                "non_blocking_objections": [],
                "missing_required_evidence": [],
                "evidence_ref_ids": [1],
                "confidence_band": "HIGH",
                "summary": "s",
            }

        for verdict in CRITIC_VERDICTS:
            with self.subTest(accept=verdict):
                self.assertEqual(
                    ModelCriticDecision.model_validate(payload(verdict)).verdict, verdict
                )
        for verdict in ("APPROVE", "REJECT", "UPHOLD_APPROVE", "pass", ""):
            with self.subTest(reject=verdict):
                with self.assertRaises(pydantic.ValidationError):
                    ModelCriticDecision.model_validate(payload(verdict))

    def test_adjudicator_verdict_enum_rejects_the_critic_vocabulary(self) -> None:
        import pydantic

        from structured_models import ADJUDICATOR_VERDICTS, ModelAdjudicatorDecision

        def payload(verdict: str) -> dict:
            return {
                "candidate_index": 0,
                "verdict": verdict,
                "resolved_objection_codes": [],
                "unresolved_objection_codes": [],
                "evidence_ref_ids": [1],
                "resolution_reason": "r",
            }

        for verdict in ADJUDICATOR_VERDICTS:
            with self.subTest(accept=verdict):
                self.assertEqual(
                    ModelAdjudicatorDecision.model_validate(payload(verdict)).verdict,
                    verdict,
                )
        for verdict in ("PASS", "BLOCK", "APPROVE", ""):
            with self.subTest(reject=verdict):
                with self.assertRaises(pydantic.ValidationError):
                    ModelAdjudicatorDecision.model_validate(payload(verdict))

    def test_adjudicator_objection_code_lists_are_constrained(self) -> None:
        import pydantic

        from structured_models import ModelAdjudicatorDecision

        with self.assertRaises(pydantic.ValidationError):
            ModelAdjudicatorDecision.model_validate(
                {
                    "candidate_index": 0,
                    "verdict": "UPHOLD_APPROVE",
                    "resolved_objection_codes": ["not_a_real_code"],
                    "unresolved_objection_codes": [],
                    "evidence_ref_ids": [1],
                    "resolution_reason": "r",
                }
            )

    def test_prompt_quotes_the_enforced_vocabulary(self) -> None:
        """A prompt that lists codes the schema forbids re-creates the drift."""
        from structured_models import (
            QUALITATIVE_VETO_CODES,
            objection_code_vocabulary_prompt,
        )

        rendered = objection_code_vocabulary_prompt()
        for code in QUALITATIVE_VETO_CODES:
            self.assertIn(code, rendered)


class SchemaFailureCategoryTests(unittest.TestCase):
    """A schema disagreement is not a transport failure.

    The eleventh run reported ``PROVIDER_TRANSPORT_ERROR`` for what was purely a
    vocabulary mismatch, pointing the investigation at the network.
    """

    def test_only_schema_failures_report_structured_response_invalid(self) -> None:
        from ai_provider import _terminal_failure_category

        self.assertEqual(
            _terminal_failure_category(["m:schema:ValueError:x", "m:schema:ValueError:y"]),
            "STRUCTURED_RESPONSE_INVALID",
        )

    def test_any_transport_failure_keeps_the_transport_category(self) -> None:
        from ai_provider import _terminal_failure_category

        self.assertEqual(
            _terminal_failure_category(["m:schema:ValueError:x", "m:PROVIDER_TIMEOUT:t"]),
            "PROVIDER_TRANSPORT_ERROR",
        )

    def test_no_recorded_errors_defaults_to_transport(self) -> None:
        from ai_provider import _terminal_failure_category

        self.assertEqual(_terminal_failure_category([]), "PROVIDER_TRANSPORT_ERROR")


if __name__ == "__main__":
    unittest.main()


