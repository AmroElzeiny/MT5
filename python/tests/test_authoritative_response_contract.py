"""Regression coverage for Python-owned response authority and deadlines.

Every test here fails on the pre-fix code:

* the provider schema used to *require* ``prompt_contract_version`` and
  ``target_arbitration_schema_version`` from the model, so a FULL_STRUCTURED
  response was downgraded to DEGRADED_NON_TRADING on every single call;
* ``confidence_band`` was an unconstrained 12-char string that Python later
  rejected against an undocumented ``{LOW, MEDIUM, HIGH}`` set;
* the OpenAI client was built without ``max_retries``, so the SDK default of 2
  gave every internal retry a fresh copy of the full timeout and a configured
  90s call became a ~270s operation that outlived the 120s terminal deadline;
* nothing prevented a late result from overwriting a terminal outcome, and
  live-wait debug ran four workers against a terminal that waits on one request.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_gate
from decision_evidence import build_decision_evidence_envelope
from evidence_catalog import build_evidence_catalog
from architecture_contracts import (
    COMPAT_ARTIFACT_NOT_STAMPED,
    COMPAT_AWAITING_RUNTIME_AUTHORITY,
    COMPAT_INCOMPATIBLE,
    COMPAT_MATCHED,
    COMPAT_NOT_APPLICABLE,
    RUNTIME_AUTHORITY_PENDING,
    PolicySpec,
    _compatibility_state,
    policy_manifest_entry,
)
from decision_integrity import (
    AI_PROMPT_CONTRACT_VERSION,
    AI_TARGET_ARBITRATION_SCHEMA_VERSION,
    validate_candidate_assessment,
)
from provider_deadline import DeadlinePolicy, RequestDeadline
from request_terminal_state import (
    TERMINAL_COMPLETED,
    TERMINAL_TEST_END_INTERRUPTED,
    TERMINAL_TIMEOUT,
    RequestTerminalRegistry,
)
from structured_models import (
    CONFIDENCE_BANDS,
    CandidateAssessment,
    ModelAIGateOutput,
    ModelCandidateAssessment,
    ModelCriticDecision,
    ModelTargetArbitrationDecision,
    TargetArbitrationDecision,
    normalize_confidence_band,
    strict_structured_schema,
)
from tests.test_decision_integrity import (
    assessment,
    candidate,
    model_assessment,
    model_target_arbitration,
)
from tests.test_python_owned_identity_lifecycle import (
    EmptyMemory,
    ReverseAssessmentProvider,
    _comparison,
    _payload,
)


PYTHON_OWNED_CONTRACT_FIELDS = (
    "prompt_contract_version",
    "target_arbitration_schema_version",
)


class ProviderSchemaOwnershipTests(unittest.TestCase):
    """The model may not be asked for deterministic internal constants."""

    def _property_names(self, node: object) -> set[str]:
        """Every property name the provider schema asks the model to fill."""

        names: set[str] = set()
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(str(key) for key in properties)
            for value in node.values():
                names |= self._property_names(value)
        elif isinstance(node, list):
            for item in node:
                names |= self._property_names(item)
        return names

    def test_provider_schema_omits_python_owned_contract_versions(self) -> None:
        schema = strict_structured_schema(ModelAIGateOutput).schema
        requested = self._property_names(schema)
        for field in PYTHON_OWNED_CONTRACT_FIELDS:
            self.assertNotIn(
                field,
                requested,
                f"provider schema must not request Python-owned {field}",
            )
        # Sanity: the traversal really does see nested arbitration properties.
        self.assertIn("chosen_target_model", requested)

    def test_model_arbitration_has_no_version_fields(self) -> None:
        for field in PYTHON_OWNED_CONTRACT_FIELDS:
            self.assertNotIn(field, ModelTargetArbitrationDecision.model_fields)
            self.assertIn(field, TargetArbitrationDecision.model_fields)

    def test_model_output_without_contract_versions_is_valid(self) -> None:
        row = model_assessment(candidate())
        row["target_arbitration"]["target_comparison"] = _comparison()
        parsed = ModelAIGateOutput.model_validate(
            {
                "decision_quality_tier": "FULL_STRUCTURED",
                "response_quality": "FULL_STRUCTURED",
                "selected_candidate_index": row["candidate_index"],
                "candidate_assessments": [row],
                "reasons": "analysis only",
            }
        )
        self.assertEqual(len(parsed.candidate_assessments), 1)

    def test_model_output_echoing_contract_versions_is_rejected(self) -> None:
        row = model_assessment(candidate())
        row["target_arbitration"]["target_comparison"] = _comparison()
        row["target_arbitration"]["prompt_contract_version"] = "model-guess"
        with self.assertRaises(Exception) as captured:
            ModelAIGateOutput.model_validate(
                {
                    "decision_quality_tier": "FULL_STRUCTURED",
                    "response_quality": "FULL_STRUCTURED",
                    "selected_candidate_index": row["candidate_index"],
                    "candidate_assessments": [row],
                    "reasons": "analysis only",
                }
            )
        self.assertIn("extra_forbidden", str(captured.exception))

    def test_prompt_does_not_ask_the_model_for_constants(self) -> None:
        source = Path(ai_gate.__file__).read_text(encoding="utf-8")
        self.assertNotIn(
            "Fill target_arbitration_schema_version and prompt_contract_version",
            source,
        )

    def test_authoritative_envelope_still_carries_versions_for_mql(self) -> None:
        # MQL re-validates both fields, so the final envelope must keep them.
        auth = assessment(candidate())
        self.assertEqual(
            auth["target_arbitration"]["prompt_contract_version"],
            AI_PROMPT_CONTRACT_VERSION,
        )
        self.assertTrue(validate_candidate_assessment(auth).valid)


class PythonOwnedInjectionTests(unittest.TestCase):
    """Python injects the active constants; a wrong echo has no authority."""

    def _bind(
        self,
        arbitration_overrides: dict | None = None,
        *,
        evidence_ref_ids: list[int] | None = None,
        confidence_band: str | None = None,
    ):
        cand = candidate()
        payload = _payload([cand])
        catalog = build_evidence_catalog(
            build_decision_evidence_envelope(payload).envelope
        )
        row = model_assessment(
            cand,
            evidence_ref_ids=(
                evidence_ref_ids
                if evidence_ref_ids is not None
                else [item.evidence_id for item in catalog.items[:2]]
            ),
        )
        if confidence_band is not None:
            row["confidence_band"] = confidence_band
        row["target_arbitration"]["target_comparison"] = _comparison()
        if arbitration_overrides:
            row["target_arbitration"].update(arbitration_overrides)
        enriched = [{"rule_score": 7.2}]
        provider_result = type(
            "R",
            (),
            {
                "provider_id": "identity-test-provider",
                "actual_model": "identity-test-model",
                "schema_fingerprint": "fingerprint",
            },
        )()
        return ai_gate._bind_python_owned_analyst_envelope(
            model_output={
                "decision_quality_tier": "FULL_STRUCTURED",
                "response_quality": "FULL_STRUCTURED",
                "selected_candidate_index": cand["candidate_index"],
                "candidate_assessments": [row],
                "reasons": "analysis only",
            },
            candidates=[cand],
            enriched_candidates=enriched,
            request_id="request-inject",
            request_identity_hash="REQUESTIDENTITY1234567890",
            ordered_candidate_identities=[
                {
                    "candidate_index": cand["candidate_index"],
                    "candidate_id": cand["candidate_id"],
                    "candidate_hash": cand["candidate_hash"],
                    "request_execution_fingerprint": cand[
                        "request_execution_fingerprint"
                    ],
                    "setup_snapshot_time": int(cand.get("setup_snapshot_time") or 0),
                    "setup_taxonomy_enum": cand["setup_taxonomy_enum"],
                }
            ],
            provider_result=provider_result,
            evidence_catalog=catalog,
        )

    def test_python_injects_exact_active_versions(self) -> None:
        envelope, diagnostics = self._bind()
        arbitration = envelope["candidate_assessments"][0]["target_arbitration"]
        self.assertEqual(
            arbitration["prompt_contract_version"], AI_PROMPT_CONTRACT_VERSION
        )
        self.assertEqual(
            arbitration["target_arbitration_schema_version"],
            AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        )
        self.assertEqual(diagnostics["python_owned_field_echoes"], [])

    def test_incorrect_echo_is_overwritten_and_recorded(self) -> None:
        # A legacy cache entry or a non-compliant provider may still echo.
        envelope, diagnostics = self._bind(
            {
                "prompt_contract_version": "attacker-supplied",
                "target_arbitration_schema_version": "stale-v1",
            }
        )
        arbitration = envelope["candidate_assessments"][0]["target_arbitration"]
        self.assertEqual(
            arbitration["prompt_contract_version"], AI_PROMPT_CONTRACT_VERSION
        )
        self.assertEqual(
            arbitration["target_arbitration_schema_version"],
            AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        )
        echoes = diagnostics["python_owned_field_echoes"]
        self.assertEqual(len(echoes), 2)
        self.assertTrue(any("attacker-supplied" in item for item in echoes))

    def test_genuine_model_analysis_survives_injection(self) -> None:
        envelope, _ = self._bind({"chosen_target_model": "liquidity_target"})
        bound = envelope["candidate_assessments"][0]
        self.assertEqual(bound["confidence_band"], "MEDIUM")
        self.assertEqual(bound["summary"], "independent candidate assessment")
        self.assertEqual(
            bound["target_arbitration"]["chosen_target_model"], "liquidity_target"
        )
        # Not an empty error envelope.
        self.assertTrue(envelope["candidate_assessments"])

    def test_unknown_confidence_band_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "model_confidence_band_invalid"):
            self._bind(confidence_band="EXTREMELY_HIGH")

    def test_unknown_evidence_id_fails_closed(self) -> None:
        with self.assertRaises(ai_gate.EvidenceReferenceError) as captured:
            self._bind(evidence_ref_ids=[999_999])
        self.assertEqual(captured.exception.diagnostics["unknown_ids"], [999_999])

    def test_negative_evidence_id_fails_closed(self) -> None:
        with self.assertRaises(ai_gate.EvidenceReferenceError):
            self._bind(evidence_ref_ids=[-1])

    def test_empty_evidence_ids_fail_closed(self) -> None:
        with self.assertRaises(ai_gate.EvidenceReferenceError):
            self._bind(evidence_ref_ids=[])

    def test_resolved_paths_are_python_generated(self) -> None:
        envelope, diagnostics = self._bind()
        refs = envelope["candidate_assessments"][0]["evidence_refs"]
        self.assertTrue(refs)
        for ref in refs:
            self.assertIsInstance(ref, str)
            self.assertRegex(ref, r"^[a-z_]+(\.[A-Za-z0-9_]+)+$")
        entry = diagnostics["evidence_reference_diagnostics"][0]
        self.assertTrue(entry["valid"])
        self.assertEqual(entry["unknown_ids"], [])
        self.assertEqual(entry["cross_candidate_ids"], [])
        self.assertTrue(entry["catalog_hash"])


class ConfidenceBandContractTests(unittest.TestCase):
    def test_canonical_set_is_three_bands(self) -> None:
        self.assertEqual(CONFIDENCE_BANDS, ("LOW", "MEDIUM", "HIGH"))

    def test_every_canonical_value_passes_every_role_schema(self) -> None:
        for band in CONFIDENCE_BANDS:
            with self.subTest(band=band):
                self.assertEqual(normalize_confidence_band(band), band)
                row = model_assessment(candidate())
                row["target_arbitration"]["target_comparison"] = _comparison()
                row["confidence_band"] = band
                ModelCandidateAssessment.model_validate(row)
                ModelCriticDecision.model_validate(
                    {
                        "candidate_index": 0,
                        "verdict": "PASS",
                        "blocking_objections": [],
                        "non_blocking_objections": [],
                        "missing_required_evidence": [],
                        "evidence_ref_ids": [0],
                        "confidence_band": band,
                        "summary": "ok",
                    }
                )
                auth = assessment(candidate())
                auth["confidence_band"] = band
                self.assertTrue(validate_candidate_assessment(auth).valid)

    def test_documented_formatting_variants_normalize(self) -> None:
        for raw, expected in (
            ("high", "HIGH"),
            ("  Medium ", "MEDIUM"),
            ("med", "MEDIUM"),
            ("MODERATE", "MEDIUM"),
            ("l o w", "LOW"),
            ("HIGH-", "HIGH"),
            ("_LOW_", "LOW"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_confidence_band(raw), expected)

    def test_arbitrary_values_fail_closed(self) -> None:
        for raw in (
            "VERY_HIGH",
            "VERY_LOW",
            "EXTREME",
            "HI",
            "high confidence",
            "8/10",
            "",
            None,
            7,
            True,
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_confidence_band(raw))

    def test_provider_schema_constrains_the_model_to_the_enum(self) -> None:
        schema = strict_structured_schema(ModelAIGateOutput).schema
        band = schema["$defs"]["ModelCandidateAssessment"]["properties"][
            "confidence_band"
        ]
        self.assertEqual(band["enum"], list(CONFIDENCE_BANDS))

    def test_authoritative_assessment_uses_the_same_enum(self) -> None:
        auth = assessment(candidate())
        auth["target_arbitration"]["target_comparison"] = _comparison()
        auth["confidence_band"] = "MEDIUM"
        CandidateAssessment.model_validate(auth)
        auth["confidence_band"] = "VERY_HIGH"
        with self.assertRaises(Exception):
            CandidateAssessment.model_validate(auth)


class DeadlinePolicyTests(unittest.TestCase):
    """Derived budgets, monotonic absolute deadline, no resets."""

    def policy(self) -> DeadlinePolicy:
        return DeadlinePolicy.derive(
            mt5_terminal_timeout_sec=120.0,
            response_write_margin_sec=15.0,
            min_attempt_sec=10.0,
        )

    def test_hierarchy_is_derived_not_hardcoded(self) -> None:
        policy = self.policy()
        self.assertEqual(policy.mt5_terminal_timeout_ms, 120_000)
        self.assertEqual(policy.python_deadline_ms, 105_000)
        self.assertEqual(policy.response_write_margin_ms, 15_000)

    def test_margin_can_never_consume_the_whole_window(self) -> None:
        policy = DeadlinePolicy.derive(
            mt5_terminal_timeout_sec=120.0,
            response_write_margin_sec=600.0,
            min_attempt_sec=10.0,
        )
        self.assertEqual(policy.response_write_margin_ms, 60_000)
        self.assertGreater(policy.python_deadline_ms, 0)

    def test_result_arrival_boundaries(self) -> None:
        policy = self.policy()
        deadline = RequestDeadline.start("req", policy, now=0.0)
        for arrival_sec, expect_expired in (
            (60.0, False),
            (90.0, False),
            (104.0, False),
            (104.999, False),
            (106.0, True),
            (120.0, True),
        ):
            with self.subTest(arrival_sec=arrival_sec):
                self.assertEqual(deadline.expired(now=arrival_sec), expect_expired)

    def test_terminal_deadline_is_later_than_python_deadline(self) -> None:
        deadline = RequestDeadline.start("req", self.policy(), now=0.0)
        self.assertTrue(deadline.expired(now=106.0))
        self.assertFalse(deadline.terminal_expired(now=106.0))
        self.assertTrue(deadline.terminal_expired(now=120.0))

    def test_provider_timeout_shrinks_and_never_resets(self) -> None:
        deadline = RequestDeadline.start("req", self.policy(), now=0.0)
        self.assertEqual(deadline.provider_timeout_sec(90.0, now=0.0), 90.0)
        # 60s spent -> only 45s of budget remains, so the SDK gets 45s, not 90s.
        self.assertAlmostEqual(deadline.provider_timeout_sec(90.0, now=60.0), 45.0)
        self.assertEqual(deadline.provider_timeout_sec(90.0, now=105.0), 0.0)
        # Asking again does not restore budget.
        self.assertAlmostEqual(deadline.provider_timeout_sec(90.0, now=60.0), 45.0)

    def test_attempt_is_refused_without_enough_budget(self) -> None:
        deadline = RequestDeadline.start("req", self.policy(), now=0.0)
        self.assertTrue(deadline.can_start_attempt(now=94.0))
        self.assertFalse(deadline.can_start_attempt(now=96.0))

    def test_elapsed_uses_monotonic_start(self) -> None:
        deadline = RequestDeadline.start("req", self.policy(), now=1000.0)
        self.assertEqual(deadline.elapsed_ms(now=1060.0), 60_000)
        self.assertEqual(deadline.remaining_ms(now=1060.0), 45_000)


class ProviderSdkBudgetTests(unittest.TestCase):
    """The real HTTP client must receive a bounded timeout and no hidden retries."""

    def _provider(self, captured: dict):
        from ai_provider import RemoteAPIProvider

        def factory(**kwargs):
            captured.update(kwargs)

            class _Client:
                def with_options(self, **options):
                    captured.setdefault("with_options", []).append(options)
                    return self

            return _Client()

        return RemoteAPIProvider(
            api_key="k",
            base_url="",
            primary_model="m",
            fallback_models=(),
            analytics_model="m",
            reasoning_effort="none",
            timeout_sec=90.0,
            max_output_tokens=1024,
            prompt_cache_enable=False,
            prompt_cache_key="",
            prompt_cache_retention="",
            service_tier="auto",
            flex_unavailable_retry_enable=False,
            flex_unavailable_max_retries=0,
            flex_unavailable_cooldown_sec=0.0,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=60.0,
            log=lambda _msg: None,
            client_factory=factory,
        )

    def test_sdk_retries_are_disabled_explicitly(self) -> None:
        captured: dict = {}
        provider = self._provider(captured)
        provider._client_instance()
        self.assertEqual(
            captured.get("max_retries"),
            0,
            "SDK default max_retries=2 multiplies the timeout budget",
        )
        self.assertEqual(captured.get("timeout"), 90.0)


class TerminalStateTests(unittest.TestCase):
    """Exactly one terminal outcome; late results are quarantined."""

    def setUp(self) -> None:
        self.registry = RequestTerminalRegistry()
        self.policy = DeadlinePolicy.derive(
            mt5_terminal_timeout_sec=120.0,
            response_write_margin_sec=15.0,
            min_attempt_sec=10.0,
        )

    def test_first_claim_wins_and_second_loses(self) -> None:
        won, _ = self.registry.claim_terminal("req", TERMINAL_TIMEOUT, reason="deadline")
        self.assertTrue(won)
        won_again, existing = self.registry.claim_terminal(
            "req", TERMINAL_COMPLETED, reason="late provider result"
        )
        self.assertFalse(won_again)
        self.assertEqual(existing.state, TERMINAL_TIMEOUT)
        self.assertEqual(self.registry.late_result_count("req"), 1)

    def test_late_completion_cannot_overwrite_timeout(self) -> None:
        self.registry.claim_terminal("req", TERMINAL_TIMEOUT)
        self.assertEqual(
            self.registry.terminal_outcome("req").state, TERMINAL_TIMEOUT
        )
        self.registry.claim_terminal("req", TERMINAL_COMPLETED)
        self.assertEqual(
            self.registry.terminal_outcome("req").state, TERMINAL_TIMEOUT
        )

    def test_two_workers_cannot_both_finish_one_identity(self) -> None:
        outcomes = [
            self.registry.claim_terminal("shared", TERMINAL_COMPLETED)[0]
            for _ in range(2)
        ]
        self.assertEqual(outcomes, [True, False])

    def test_deadline_registration_never_restarts_the_clock(self) -> None:
        first = RequestDeadline.start("req", self.policy, now=0.0)
        registered = self.registry.register_deadline(first)
        self.assertIs(registered, first)
        second = RequestDeadline.start("req", self.policy, now=90.0)
        again = self.registry.register_deadline(second)
        self.assertIs(again, first, "a re-registration must not reset the deadline")

    def test_test_end_interruption_is_not_a_provider_timeout(self) -> None:
        self.registry.register_deadline(RequestDeadline.start("pending", self.policy))
        interrupted = self.registry.mark_pending_test_end_interrupted("period_complete")
        self.assertEqual(interrupted, ("pending",))
        outcome = self.registry.terminal_outcome("pending")
        self.assertEqual(outcome.state, TERMINAL_TEST_END_INTERRUPTED)
        self.assertNotEqual(outcome.state, TERMINAL_TIMEOUT)

    def test_late_result_after_test_end_is_non_authoritative(self) -> None:
        self.registry.register_deadline(RequestDeadline.start("pending", self.policy))
        self.registry.mark_pending_test_end_interrupted("period_complete")
        won, existing = self.registry.claim_terminal("pending", TERMINAL_COMPLETED)
        self.assertFalse(won)
        self.assertEqual(existing.state, TERMINAL_TEST_END_INTERRUPTED)

    def test_already_terminal_request_is_not_resurrected_by_recovery(self) -> None:
        self.registry.claim_terminal("req", TERMINAL_TIMEOUT)
        self.registry.release("req")
        self.assertTrue(self.registry.is_terminal("req"))


class WorkerModeTests(unittest.TestCase):
    def test_live_wait_debug_uses_one_effective_worker(self) -> None:
        self.assertEqual(ai_gate.effective_worker_count(4, "live_wait_debug"), 1)

    def test_other_modes_keep_configured_concurrency(self) -> None:
        for source in ("record_only", "cache_only", "tester", "live_forward"):
            with self.subTest(source=source):
                self.assertEqual(ai_gate.effective_worker_count(4, source), 4)

    def test_configured_workers_are_clamped(self) -> None:
        self.assertEqual(ai_gate.effective_worker_count(0, "tester"), 1)
        self.assertEqual(ai_gate.effective_worker_count(99, "tester"), 16)


class EndToEndAuthoritativeResponseTests(unittest.TestCase):
    """A mocked provider returning analysis only must reach FULL_STRUCTURED."""

    def test_multi_candidate_response_stays_full_structured(self) -> None:
        candidates = [candidate(0, "A"), candidate(1, "B")]
        provider = ReverseAssessmentProvider(candidates)
        logs: list[str] = []
        with patch("ai_gate._trade_memory_store", return_value=EmptyMemory()), patch(
            "ai_gate.log_ai_usage", return_value={}
        ), patch("ai_gate._write_ai_cost_report", return_value=None), patch(
            "ai_gate._load_live_bucket_priors", return_value={}
        ), patch("ai_gate.log", side_effect=logs.append):
            decision = ai_gate._score_setup_ai(
                _payload(candidates),
                provider_override=provider,
            )
        joined = "\n".join(logs)

        self.assertEqual(decision.decision_quality_tier, "FULL_STRUCTURED")
        self.assertNotEqual(decision.decision_source, "degraded_ai_response")
        self.assertNotEqual(decision.decision_source, "provider_transport_error")

        # Staged logs must distinguish raw model output from the final envelope.
        self.assertIn("[raw_model_schema_validation] valid=true", joined)
        self.assertIn("[authoritative_envelope_constructed]", joined)
        self.assertIn("[authoritative_envelope_validation] valid=true", joined)
        self.assertIn("[ai_schema_validation] valid=true", joined)
        self.assertIn("[identity_validation] valid=true", joined)

        # Real assessments survived, not a fail-closed empty envelope.
        self.assertEqual(len(decision.candidate_assessments), 2)
        self.assertNotEqual(decision.llm_quality_score, 0.0)
        for row in decision.candidate_assessments:
            self.assertIn(row["confidence_band"], CONFIDENCE_BANDS)
            self.assertEqual(
                row["target_arbitration"]["prompt_contract_version"],
                AI_PROMPT_CONTRACT_VERSION,
            )
            self.assertEqual(
                row["target_arbitration"]["target_arbitration_schema_version"],
                AI_TARGET_ARBITRATION_SCHEMA_VERSION,
            )

        # The selected candidate binding survived reordering.
        self.assertEqual(
            decision.selected_candidate_hash, candidates[1]["candidate_hash"]
        )

    def test_positive_path_reaches_python_final_allow(self) -> None:
        """The full authority path, not merely a FULL_STRUCTURED tier."""

        candidates = [candidate(0, "A"), candidate(1, "B")]
        provider = ReverseAssessmentProvider(candidates)
        logs: list[str] = []
        with patch("ai_gate._trade_memory_store", return_value=EmptyMemory()), patch(
            "ai_gate.log_ai_usage", return_value={}
        ), patch("ai_gate._write_ai_cost_report", return_value=None), patch(
            "ai_gate._load_live_bucket_priors", return_value={}
        ), patch("ai_gate.log", side_effect=logs.append):
            decision = ai_gate._score_setup_ai(
                _payload(candidates),
                provider_override=provider,
            )
        joined = "\n".join(logs)

        # Evidence references resolved against the Python-owned catalog.
        self.assertIn("[evidence_reference_validation] valid=true", joined)
        self.assertNotIn("[evidence_reference_validation] valid=false", joined)

        # The exact failure mode from the 2026-07-30 run must be gone.
        self.assertNotEqual(
            decision.decision_source, "decision_evidence_reference_reject"
        )
        self.assertNotEqual(decision.decision_source, "degraded_ai_response")
        self.assertNotEqual(decision.decision_source, "provider_transport_error")

        # Genuine model analysis preserved, not fail-closed defaults.
        self.assertEqual(decision.decision_quality_tier, "FULL_STRUCTURED")
        self.assertTrue(decision.allow, decision.reasons)
        self.assertTrue(decision.raw_allow)
        self.assertGreater(decision.llm_quality_score, 0.0)
        self.assertGreater(decision.suggested_risk_multiplier, 0.0)
        self.assertEqual(len(decision.candidate_assessments), 2)

        # The selected candidate is a real request candidate with a feasible target.
        self.assertEqual(
            decision.selected_candidate_hash, candidates[1]["candidate_hash"]
        )
        self.assertTrue(decision.selected_candidate_id)
        selected = next(
            row
            for row in decision.candidate_assessments
            if row["candidate_hash"] == decision.selected_candidate_hash
        )
        self.assertGreater(float(selected["tp2"]), 0.0)
        self.assertFalse(selected["veto"]["enabled"])

        # MT5-facing binding fields that previously reported false.
        self.assertEqual(
            len(decision.candidate_assessments), len(candidates)
        )  # candidate_assessment_count_match
        request_hashes = {c["candidate_hash"] for c in candidates}
        assessment_hashes = {r["candidate_hash"] for r in decision.candidate_assessments}
        self.assertEqual(assessment_hashes, request_hashes)  # candidate_hash_match

    def test_evidence_ids_outside_the_catalog_are_rejected_end_to_end(self) -> None:
        """A genuine evidence failure still fails closed with diagnostics."""

        candidates = [candidate(0, "A"), candidate(1, "B")]
        provider = ReverseAssessmentProvider(candidates)
        provider.force_evidence_ref_ids = [10_000]
        logs: list[str] = []
        with patch("ai_gate._trade_memory_store", return_value=EmptyMemory()), patch(
            "ai_gate.log_ai_usage", return_value={}
        ), patch("ai_gate._write_ai_cost_report", return_value=None), patch(
            "ai_gate._load_live_bucket_priors", return_value={}
        ), patch("ai_gate.log", side_effect=logs.append):
            decision = ai_gate._score_setup_ai(
                _payload(candidates),
                provider_override=provider,
            )
        joined = "\n".join(logs)
        self.assertEqual(
            decision.decision_source, "decision_evidence_reference_reject"
        )
        self.assertIn("[evidence_reference_validation] valid=false", joined)
        self.assertIn("unknown_ids=[10000]", joined)
        self.assertIn("catalog_size=", joined)

    def test_mocked_provider_returns_no_python_owned_fields(self) -> None:
        # Guards the fixture itself: a mock that echoes Python constants would
        # hide the production defect this suite exists to catch.
        row = model_assessment(candidate())
        encoded = json.dumps(row)
        for field in PYTHON_OWNED_CONTRACT_FIELDS:
            self.assertNotIn(field, encoded)
        self.assertNotIn(
            "target_arbitration_schema_version", model_target_arbitration()
        )


class ReplayCacheAuthorityTests(unittest.TestCase):
    """A cached decision must carry the same authority as a fresh one."""

    def _response(self, **overrides) -> dict:
        """A response whose contract fields are all current.

        Built this way so the quality-tier gate is genuinely reached instead of
        short-circuiting on an earlier legacy-contract check.
        """

        resp = {
            "id": "req-cache",
            "request_identity_version": ai_gate.AI_REQUEST_IDENTITY_VERSION,
            "contract_manifest_hash": ai_gate.compatibility_manifest_hash(),
            "request_identity_hash": "REQUESTIDENTITY1234567890",
            "ordered_candidate_identities": [{"candidate_index": 0}],
            "candidate_count": 1,
            "decision_schema_version": ai_gate.AI_DECISION_SCHEMA_VERSION,
            "target_arbitration_schema_version": AI_TARGET_ARBITRATION_SCHEMA_VERSION,
            "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
            "provider_contract_version": ai_gate.PROVIDER_CONTRACT_VERSION,
            "evidence_envelope_version": ai_gate.EVIDENCE_ENVELOPE_VERSION,
            "family_profile_version": ai_gate.FAMILY_PROFILE_VERSION,
            "memory_schema_version": ai_gate.TRADE_MEMORY_SCHEMA_VERSION,
            "retrieval_policy_version": ai_gate.RETRIEVAL_POLICY_VERSION,
            "role_contract_version": ai_gate.ROLE_CONTRACT_VERSION,
            "consensus_resolver_version": ai_gate.CONSENSUS_RESOLVER_VERSION,
            "provider_mode": ai_gate.PROVIDER_MODE_LOCAL,
            "provider_id": "p",
            "endpoint_identity_hash": "e",
            "configured_models_hash": "c",
            "actual_model_id": "m",
            "model_fingerprint": "f",
            "generation_settings_hash": "g",
            "input_fingerprint": "i",
            "analyst_response_fingerprint": "a",
            "critic_response_fingerprint": "cr",
            "decision_quality_tier": ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
            "response_quality": ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
            "mandatory_fields_complete": True,
            "candidate_assessments": [{}],
        }
        resp.update(overrides)
        return resp

    def test_fixture_reaches_the_quality_gate(self) -> None:
        # Guards the fixture: an earlier legacy check firing would make the
        # degraded/alias assertions below vacuous.
        reason = ai_gate._mql_tester_cache_skip_reason(self._response())
        self.assertFalse(reason.startswith("legacy_"), reason)
        self.assertFalse(reason.startswith("missing_"), reason)

    def test_degraded_output_never_enters_the_replay_cache(self) -> None:
        degraded = self._response(
            decision_quality_tier=ai_gate.DECISION_QUALITY_DEGRADED_NON_TRADING,
            response_quality=ai_gate.DECISION_QUALITY_DEGRADED_NON_TRADING,
        )
        self.assertEqual(
            ai_gate._mql_tester_cache_skip_reason(degraded),
            "degraded_decision_quality_tier",
        )

    def test_incomplete_mandatory_fields_never_enter_the_cache(self) -> None:
        incomplete = self._response(mandatory_fields_complete=False)
        self.assertEqual(
            ai_gate._mql_tester_cache_skip_reason(incomplete),
            "incomplete_mandatory_fields",
        )

    def test_quality_alias_conflict_is_rejected(self) -> None:
        conflicted = self._response(response_quality="SOMETHING_ELSE")
        self.assertEqual(
            ai_gate._mql_tester_cache_skip_reason(conflicted),
            "decision_quality_alias_conflict",
        )

    def test_live_wait_debug_is_not_replay_authoritative(self) -> None:
        payload = {
            "id": "req",
            "runtime_inputs": {"tester_ai_mode_name": "tester_ai_live_wait_debug"},
        }
        self.assertEqual(ai_gate._tester_workflow_source(payload), "live_wait_debug")
        with patch("ai_gate.log"):
            result = ai_gate._export_mql_tester_replay_cache(
                payload, self._response(), Path("/nonexistent-bus")
            )
        self.assertEqual(
            result, "skipped:live_wait_debug_not_replay_authoritative"
        )

    def test_workflow_sources_are_distinguished(self) -> None:
        for name, expected in (
            ("tester_ai_record_only", "record_only"),
            ("tester_ai_cache_only", "cache_only"),
            ("tester_ai_live_wait_debug", "live_wait_debug"),
        ):
            with self.subTest(name=name):
                payload = {"runtime_inputs": {"tester_ai_mode_name": name}}
                self.assertEqual(ai_gate._tester_workflow_source(payload), expected)


class PolicyClassificationTests(unittest.TestCase):
    """Pending evaluation must not be reported as proven incompatibility."""

    def test_pending_runtime_authority_is_not_incompatible(self) -> None:
        self.assertEqual(
            _compatibility_state(RUNTIME_AUTHORITY_PENDING, "anything"),
            COMPAT_AWAITING_RUNTIME_AUTHORITY,
        )

    def test_unstamped_artifact_is_distinguished_from_mismatch(self) -> None:
        self.assertEqual(_compatibility_state("expected-v1", ""), COMPAT_ARTIFACT_NOT_STAMPED)
        self.assertEqual(_compatibility_state("expected-v1", "other-v2"), COMPAT_INCOMPATIBLE)
        self.assertEqual(_compatibility_state("expected-v1", "expected-v1"), COMPAT_MATCHED)
        self.assertEqual(_compatibility_state("", "irrelevant"), COMPAT_NOT_APPLICABLE)

    def _spec(self, tmp: Path, **kwargs) -> PolicySpec:
        path = tmp / "policy.json"
        path.write_text(
            json.dumps({"schema_version": "schema-v1", "rows": []}), encoding="utf-8"
        )
        defaults = dict(
            policy_type="risk_factors",
            policy_id="risk_factor_policy",
            path=path,
            enabled=True,
            requested_authority="active",
            expected_schema="schema-v1",
            stale_after_days=36500,
        )
        defaults.update(kwargs)
        return PolicySpec(**defaults)

    def test_awaiting_runtime_authority_is_not_blocked(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            entry = policy_manifest_entry(
                self._spec(tmp, expected_runtime_input_hash=RUNTIME_AUTHORITY_PENDING),
                ledger_integrity_status="UNKNOWN",
            )
        self.assertEqual(entry["status"], "awaiting_runtime_authority")
        self.assertEqual(entry["rejection_reasons"], [])
        self.assertIn(
            "runtime_input_awaiting_runtime_authority", entry["pending_reasons"]
        )
        # Still fail-closed: never active without proof.
        self.assertNotEqual(entry["authority"], "active")

    def test_unknown_ledger_is_unverified_not_dirty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            entry = policy_manifest_entry(
                self._spec(Path(raw)),
                ledger_integrity_status="UNKNOWN",
            )
        self.assertNotIn("ledger_not_clean", entry["rejection_reasons"])
        self.assertIn("ledger_unverified_no_completed_history", entry["pending_reasons"])

    def test_genuinely_dirty_ledger_still_blocks(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            entry = policy_manifest_entry(
                self._spec(Path(raw)),
                ledger_integrity_status="RECONCILIATION_FAILED",
            )
        self.assertIn("ledger_not_clean", entry["rejection_reasons"])
        self.assertEqual(entry["authority"], "blocked")

    def test_real_incompatibility_still_blocks(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            entry = policy_manifest_entry(
                self._spec(Path(raw), expected_schema="different-v9"),
                ledger_integrity_status="clean",
            )
        self.assertIn("code_schema_incompatible", entry["rejection_reasons"])
        self.assertEqual(entry["status"], "incompatible")
        self.assertEqual(entry["authority"], "blocked")

    def test_disabled_optional_policy_reports_disabled(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            entry = policy_manifest_entry(
                self._spec(Path(raw), enabled=False, requested_authority="shadow"),
                ledger_integrity_status="UNKNOWN",
            )
        self.assertEqual(entry["status"], "disabled")
        self.assertEqual(entry["activation_state"], "disabled")
        self.assertNotEqual(entry["authority"], "blocked")

    def test_missing_optional_artifact_does_not_block(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            entry = policy_manifest_entry(
                PolicySpec(
                    policy_type="calibration",
                    policy_id="calibration_artifact",
                    path=Path(raw) / "absent.json",
                    enabled=False,
                ),
                ledger_integrity_status="UNKNOWN",
            )
        self.assertEqual(entry["status"], "disabled")
        self.assertNotEqual(entry["authority"], "blocked")

    def test_missing_mandatory_artifact_blocks_explicitly(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            entry = policy_manifest_entry(
                PolicySpec(
                    policy_type="risk_factors",
                    policy_id="risk_factor_policy",
                    path=Path(raw) / "absent.json",
                    enabled=True,
                    requested_authority="active",
                ),
                ledger_integrity_status="clean",
            )
        self.assertEqual(entry["rejection_reasons"], ["policy_file_missing"])
        self.assertEqual(entry["authority"], "blocked")


class DeploymentManifestTests(unittest.TestCase):
    def test_manifest_is_generated_from_real_build_facts(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw, patch("ai_gate.log"):
            manifest = ai_gate._deployment_manifest_state(Path(raw))
        self.assertEqual(
            manifest["schema_version"], ai_gate.DEPLOYMENT_MANIFEST_SCHEMA_VERSION
        )
        contract = manifest["python_contract"]
        self.assertEqual(contract["prompt_contract_version"], AI_PROMPT_CONTRACT_VERSION)
        self.assertEqual(
            contract["target_arbitration_schema_version"],
            AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        )
        self.assertTrue(manifest["written"])
        # Component hashes are observed, never invented.
        for component in manifest["mql_components"]:
            if component["exists"]:
                self.assertTrue(component["sha256"])
            else:
                self.assertEqual(component["sha256"], "")


class MqlParityTests(unittest.TestCase):
    """Python and MQL must agree on the same authoritative constants."""

    MQL_INCLUDE = Path(
        r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal"
        r"\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex"
    )

    def _read(self, name: str) -> str:
        path = self.MQL_INCLUDE / name
        if not path.is_file():
            self.skipTest(f"MQL include not deployed: {path}")
        return path.read_text(encoding="utf-8", errors="ignore")

    def test_mql_contract_versions_match_python(self) -> None:
        config = self._read("Config.mqh")
        self.assertIn(AI_PROMPT_CONTRACT_VERSION, config)
        self.assertIn(AI_TARGET_ARBITRATION_SCHEMA_VERSION, config)

    def test_mql_publishes_its_terminal_wait_budget(self) -> None:
        bridge = self._read("AIGateBridge.mqh")
        self.assertIn("ai_wait_timeout_ms", bridge)

    def test_python_derives_terminal_timeout_from_the_payload(self) -> None:
        payload = {"runtime_inputs": {"ai_wait_timeout_ms": 90_000}}
        self.assertEqual(ai_gate._mt5_terminal_timeout_sec(payload), 90.0)
        policy = ai_gate._deadline_policy_for_payload(payload)
        self.assertEqual(policy.mt5_terminal_timeout_ms, 90_000)

    def test_missing_payload_timeout_falls_back_to_config(self) -> None:
        self.assertEqual(
            ai_gate._mt5_terminal_timeout_sec({}),
            float(ai_gate.AI_CONFIG.mt5_terminal_timeout_sec),
        )


if __name__ == "__main__":
    unittest.main()
