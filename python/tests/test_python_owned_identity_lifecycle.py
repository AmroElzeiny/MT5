from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ai_gate
from ai_provider import PROVIDER_MODE_LOCAL, ProviderResult
from architecture_contracts import FileBusLifecycle
from compatibility_manifest import compatibility_manifest_hash
from decision_integrity import (
    AI_IDENTITY_CANONICALIZATION_VERSION,
    canonical_decimal,
    freeze_ai_request,
)
from decision_pipeline import ROLE_CONTRACT_VERSION
from request_lifecycle import (
    RequestIdempotencyLedger,
    heartbeat_allows_recovery,
)
from structured_models import ModelCandidateAssessment
from pipeline_integrity import FrozenRequestMutationError
from tests.test_decision_integrity import (
    assessment,
    candidate,
    catalog_ids_for,
    model_assessment,
)


def _result(parsed: object, role: str) -> ProviderResult:
    return ProviderResult(
        parsed=parsed,
        raw_response={"model": "identity-test-model", "usage": {}},
        provider_mode=PROVIDER_MODE_LOCAL,
        provider_id="identity-test-provider",
        endpoint_class="loopback",
        requested_model="identity-test-model",
        actual_model="identity-test-model",
        fallback_model="",
        model_fingerprint="identity-test-fingerprint",
        role=role,
        latency_sec=0.01,
        retry_count=0,
        transport_retry_count=0,
        schema_retry_count=0,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        tokens_per_second=None,
        generation_settings_hash="identity-generation-v1",
        health_state="healthy",
    )


def _comparison() -> dict:
    usable = {
        "usable": True,
        "reason": "Deterministic target is feasible.",
        "risk": "No target contradiction.",
        "expected_role": "tp2",
    }
    unavailable = {
        "usable": False,
        "reason": "Not selected for this fixture.",
        "risk": "No additional risk.",
        "expected_role": "reject",
    }
    return {
        "liquidity_target": usable,
        "partial_before_obstacle_then_liquidity": unavailable,
        "capped_before_obstacle": unavailable,
        "synthetic_rr_capped_to_max_distance": unavailable,
        "synthetic_rr_fallback": unavailable,
    }


class ReverseAssessmentProvider:
    provider_mode = PROVIDER_MODE_LOCAL
    provider_id = "identity-test-provider"
    endpoint_class = "loopback"

    def __init__(self, candidates: list[dict]) -> None:
        self.candidates = candidates
        self.calls: list[str] = []
        # Set by tests that need a genuine evidence-reference failure.
        self.force_evidence_ref_ids: list[int] | None = None

    def model_for_role(self, role: str) -> str:
        return "identity-test-model"

    def identity(self, role: str = "analyst") -> dict:
        return self.generation_identity(role, {})

    def generation_identity(
        self,
        role: str = "analyst",
        request_metadata: dict | None = None,
    ) -> dict:
        return {
            "provider_mode": self.provider_mode,
            "provider_id": self.provider_id,
            "endpoint_class": self.endpoint_class,
            "endpoint_identity_hash": "identity-endpoint",
            "configured_models_hash": "identity-models",
            "configured_model_ids": ["identity-test-model"],
            "model_id": "identity-test-model",
            "model_fingerprint": "identity-test-fingerprint",
            "generation_settings_hash": "identity-generation-v1",
        }

    def generate_structured(
        self,
        *,
        role: str,
        response_schema: type,
        evidence: dict | None = None,
        request_metadata: dict | None = None,
        **_: object,
    ) -> ProviderResult:
        evidence = dict(evidence or {})
        self.calls.append(role)
        if role == "analyst":
            rows: list[dict] = []
            for position, cand in enumerate(self.candidates):
                row = model_assessment(
                    cand,
                    quality=7.25 if position == 0 else 8.75,
                    evidence_ref_ids=(
                        self.force_evidence_ref_ids
                        if self.force_evidence_ref_ids is not None
                        else catalog_ids_for(evidence, position)
                    ),
                )
                row["target_arbitration"]["target_comparison"] = _comparison()
                rows.append(row)
            parsed = response_schema.model_validate(
                {
                    "decision_quality_tier": "FULL_STRUCTURED",
                    "response_quality": "FULL_STRUCTURED",
                    "selected_candidate_index": 1,
                    "candidate_assessments": list(reversed(rows)),
                    "reasons": "Independent assessments returned in reverse order.",
                }
            )
            return _result(parsed, role)
        if role == "critic":
            parsed = response_schema.model_validate(
                {
                    "candidate_index": 1,
                    "verdict": "PASS",
                    "blocking_objections": [],
                    "non_blocking_objections": [],
                    "missing_required_evidence": [],
                    "evidence_ref_ids": catalog_ids_for(evidence, 1),
                    "confidence_band": "HIGH",
                    "summary": "No evidence-backed contradiction.",
                }
            )
            return _result(parsed, role)
        raise AssertionError(f"unexpected role:{role}")


class EmptyMemory:
    def ingest_completed_ledger(self, path: Path) -> dict:
        return {}

    def retrieve_analogues(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            state="INSUFFICIENT_SAMPLE",
            analogue_ids=(),
            analogues=(),
            retrieval_hash="none",
        )

    def record_pending_decision(self, **kwargs: object) -> bool:
        return True


def _payload(candidates: list[dict]) -> dict:
    return {
        "id": "identity-e2e",
        "session_id": "session-e2e",
        "request_nonce": "nonce-e2e",
        "request_created_sim_time": 1_780_000_000,
        "request_created_wall_time": 1_780_000_001,
        "contract_manifest_hash": compatibility_manifest_hash(),
        "symbol": "GOLD",
        "asset_class": "metal",
        "is_buy": True,
        "workload_mode": "RESEARCH",
        "runtime_input_hash": "runtime-e2e",
        "engine_version": "engine-e2e",
        "input_schema_version": "input-e2e",
        "runtime": {"require_snapshots": False},
        "runtime_inputs": {"runtime_input_hash": "runtime-e2e"},
        "plan": copy.deepcopy(candidates[0]),
        "candidates": copy.deepcopy(candidates),
        "po3": {
            "has_sweep": True,
            "has_displacement": True,
            "has_bos": True,
            "t_sweep": 100,
            "t_disp": 101,
            "t_bos": 102,
            "session_name": "LON",
            "in_killzone": True,
        },
        "validation": {"missing_fields": [], "hard_blockers": []},
    }


class FrozenIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider_identity = ReverseAssessmentProvider([]).generation_identity()

    def _freeze(self, payload: dict):
        return freeze_ai_request(
            payload,
            provider_identity=self.provider_identity,
            schema_fingerprint="schema-v1",
            family_profile_version="family-v1",
            retrieval_policy_version="retrieval-v1",
        )

    def test_sort_deduplicate_and_hash_before_provider(self) -> None:
        first = candidate(0, "A")
        second = candidate(1, "B")
        payload = _payload([second, first, copy.deepcopy(first)])
        frozen = self._freeze(payload)
        thawed = frozen.thaw_payload()
        self.assertEqual(
            [row["candidate_index"] for row in thawed["candidates"]],
            [0, 1],
        )
        self.assertEqual(len(thawed["candidates"]), 2)
        self.assertEqual(
            frozen.identity["canonicalization_version"],
            AI_IDENTITY_CANONICALIZATION_VERSION,
        )

    def test_mutation_after_hashing_is_detected(self) -> None:
        frozen = self._freeze(_payload([candidate()]))
        thawed = frozen.thaw_payload()
        thawed["candidates"][0]["entry_est"] += 0.01
        with self.assertRaisesRegex(
            FrozenRequestMutationError,
            "frozen_request_candidate_mutation",
        ):
            frozen.assert_unchanged(thawed)

    def test_dictionary_order_and_wall_clock_do_not_change_hash(self) -> None:
        payload = _payload([candidate()])
        reversed_payload = dict(reversed(list(payload.items())))
        reversed_payload["request_created_wall_time"] += 999
        self.assertEqual(
            self._freeze(payload).request_identity_hash,
            self._freeze(reversed_payload).request_identity_hash,
        )

    def test_candidate_order_changes_ordered_identity(self) -> None:
        first = candidate(0, "A")
        second = candidate(1, "B")
        original = self._freeze(_payload([first, second]))
        changed = copy.deepcopy(_payload([first, second]))
        changed["candidates"][0]["candidate_index"] = 1
        changed["candidates"][1]["candidate_index"] = 0
        reordered = self._freeze(changed)
        self.assertNotEqual(
            original.request_identity_hash,
            reordered.request_identity_hash,
        )

    def test_price_decimal_is_locale_independent_and_finite(self) -> None:
        self.assertEqual(canonical_decimal(100.12000000000001), "100.12")
        with self.assertRaisesRegex(ValueError, "identity_non_finite_number"):
            canonical_decimal(float("nan"))


class PythonOwnedResponseTests(unittest.TestCase):
    def test_reverse_model_order_binds_and_preserves_actual_metrics(self) -> None:
        candidates = [candidate(0, "A"), candidate(1, "B")]
        provider = ReverseAssessmentProvider(candidates)
        logs: list[str] = []
        with patch("ai_gate._trade_memory_store", return_value=EmptyMemory()), patch(
            "ai_gate.log_ai_usage",
            return_value={},
        ), patch("ai_gate._write_ai_cost_report", return_value=None), patch(
            "ai_gate._load_live_bucket_priors",
            return_value={},
        ), patch("ai_gate.log", side_effect=logs.append):
            decision = ai_gate._score_setup_ai(
                _payload(candidates),
                provider_override=provider,
            )
        self.assertTrue(decision.allow, decision)
        self.assertEqual(decision.selected_candidate_hash, candidates[1]["candidate_hash"])
        self.assertEqual(decision.llm_quality_score, 8.75)
        self.assertEqual(
            [item["candidate_index"] for item in decision.candidate_assessments],
            [0, 1],
        )
        self.assertEqual(provider.calls, ["analyst", "critic"])
        self.assertTrue(
            any("[identity_validation] valid=true" in line for line in logs),
            logs,
        )

    def test_duplicate_or_unknown_assessment_index_fails_closed(self) -> None:
        candidates = [candidate(0, "A"), candidate(1, "B")]
        provider = ReverseAssessmentProvider(candidates)
        original = provider.generate_structured

        def duplicate(**kwargs: object) -> ProviderResult:
            result = original(**kwargs)
            if kwargs["role"] != "analyst":
                return result
            raw = result.parsed.model_dump()
            raw["candidate_assessments"][0]["candidate_index"] = 0
            raw["candidate_assessments"][1]["candidate_index"] = 0
            return _result(kwargs["response_schema"].model_validate(raw), "analyst")

        provider.generate_structured = duplicate  # type: ignore[method-assign]
        with patch("ai_gate._trade_memory_store", return_value=EmptyMemory()), patch(
            "ai_gate.log_ai_usage",
            return_value={},
        ), patch("ai_gate._write_ai_cost_report", return_value=None):
            decision = ai_gate._score_setup_ai(
                _payload(candidates),
                provider_override=provider,
            )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.decision_source, "structured_response_invalid")


class ExactlyOnceLifecycleTests(unittest.TestCase):
    def test_validated_response_is_reused_without_new_provider_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            response_path = root / "responses" / "request.json"
            ledger = RequestIdempotencyLedger(
                root / "request_ledger",
                worker_id="worker-a",
            )
            kwargs = {
                "request_id": "request",
                "request_identity_hash": "hash-a",
                "provider_id": "provider",
                "model_id": "model",
                "prompt_contract_version": "prompt",
                "schema_fingerprint": "schema",
                "response_path": response_path,
                "stale_after_sec": 3600,
            }
            first = ledger.begin(**kwargs)
            self.assertEqual(first.action, "PROCESS")
            response = {
                "id": "request",
                "request_identity_hash": "hash-a",
                "provider_id": "provider",
                "decision_quality_tier": "FULL_STRUCTURED",
                "mandatory_fields_complete": True,
            }
            ledger.transition(
                "request",
                "RESPONSE_VALIDATED",
                extra={"validated_response": response},
            )
            second = RequestIdempotencyLedger(
                root / "request_ledger",
                worker_id="worker-b",
            ).begin(**kwargs)
            self.assertEqual(second.action, "REUSE")
            self.assertEqual(second.response, response)

    def test_request_id_collision_is_quarantinable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ledger = RequestIdempotencyLedger(
                root / "request_ledger",
                worker_id="worker-a",
            )
            common = {
                "request_id": "request",
                "provider_id": "provider",
                "model_id": "model",
                "prompt_contract_version": "prompt",
                "schema_fingerprint": "schema",
                "response_path": root / "response.json",
                "stale_after_sec": 3600,
            }
            ledger.begin(request_identity_hash="hash-a", **common)
            collision = ledger.begin(request_identity_hash="hash-b", **common)
            self.assertEqual(collision.action, "COLLISION")
            self.assertEqual(collision.reason, "request_id_identity_collision")

    def test_active_heartbeat_blocks_recovery_and_expired_allows_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            heartbeat = Path(temp) / "request.lock"
            heartbeat.write_text(
                json.dumps(
                    {
                        "process_id": os.getpid(),
                        "hostname": "",
                        "heartbeat_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(
                heartbeat_allows_recovery(
                    heartbeat,
                    stale_after_sec=10,
                    stale_grace_sec=5,
                )
            )
            heartbeat.write_text(
                json.dumps(
                    {
                        "process_id": 999_999_999,
                        "hostname": "",
                        "heartbeat_at": time.time() - 100,
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                heartbeat_allows_recovery(
                    heartbeat,
                    stale_after_sec=10,
                    stale_grace_sec=5,
                )
            )

    def test_file_bus_recovery_does_not_reclaim_fresh_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lifecycle = FileBusLifecycle(root, "worker")
            lifecycle.ensure()
            processing = root / "processing" / "old__request.json"
            processing.write_text("{}", encoding="utf-8")
            lock = root / "locks" / "request.json.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text(
                json.dumps(
                    {
                        "process_id": os.getpid(),
                        "hostname": "",
                        "heartbeat_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            recovered = lifecycle.recover_processing(
                stale_after_sec=1,
                stale_grace_sec=1,
            )
            self.assertEqual(recovered, [])
            self.assertTrue(processing.exists())


class RepeatabilityOrderingTests(unittest.TestCase):
    def test_identity_invalid_primary_never_launches_shadow_provider_calls(self) -> None:
        primary = ai_gate.Decision(
            allow=False,
            score=0.0,
            decision_quality_tier="DEGRADED_NON_TRADING",
            mandatory_fields_complete=False,
            decision_source="request_identity_mismatch",
        )
        with patch("ai_gate._shadow_repeat_sampled", return_value=True), patch(
            "ai_gate._score_setup_ai"
        ) as scorer:
            ai_gate._run_shadow_repeat_evaluation(
                {"id": "authoritative-request"},
                primary,
            )
        scorer.assert_not_called()

    def test_shadow_repeats_use_derivative_non_authoritative_request_ids(self) -> None:
        payload = _payload([candidate()])
        primary = ai_gate.Decision(
            allow=True,
            score=8.0,
            decision_quality_tier="FULL_STRUCTURED",
            mandatory_fields_complete=True,
            decision_source="ai_approved",
        )
        observed_ids: list[str] = []

        def observe(
            shadow_payload: dict,
            **kwargs: object,
        ) -> ai_gate.Decision:
            observed_ids.append(str(shadow_payload["id"]))
            self.assertTrue(kwargs.get("non_authoritative_shadow"))
            return copy.deepcopy(primary)

        with patch("ai_gate._shadow_repeat_sampled", return_value=True), patch(
            "ai_gate._score_setup_ai",
            side_effect=observe,
        ), patch("ai_gate._update_repeatability_artifact", return_value=None):
            ai_gate._run_shadow_repeat_evaluation(payload, primary)
        self.assertTrue(observed_ids)
        self.assertNotIn(payload["id"], observed_ids)
        self.assertEqual(len(observed_ids), len(set(observed_ids)))
        self.assertTrue(
            all("__shadow_repeat_" in request_id for request_id in observed_ids)
        )


if __name__ == "__main__":
    unittest.main()
