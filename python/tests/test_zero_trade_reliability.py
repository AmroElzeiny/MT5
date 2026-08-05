from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import ai_gate
from pydantic import BaseModel
from ai_provider import ProviderCallError, RemoteAPIProvider
from compatibility_manifest import compatibility_manifest_hash
from decision_integrity import (
    AI_REQUEST_IDENTITY_VERSION,
    build_ai_request_identity,
    validate_request_identity_echo,
)
from structured_models import (
    AIGateEnvelope,
    StrictStructuredModel,
    all_authoritative_structured_models,
    strict_structured_schema,
)


class _StrictProbe(StrictStructuredModel):
    ok: bool


class _NonStrictProbe(BaseModel):
    ok: bool


class _InvalidSchemaHttpError(RuntimeError):
    status_code = 400

    def __init__(self) -> None:
        super().__init__(
            "Invalid schema for response_format 'AIGateEnvelope': "
            "'additionalProperties' is required to be supplied and to be false"
        )


class _AuthenticationHttpError(RuntimeError):
    status_code = 401

    def __init__(self) -> None:
        super().__init__("invalid API key")


def _remote_provider(client: object) -> RemoteAPIProvider:
    return RemoteAPIProvider(
        api_key="test-key",
        base_url="",
        primary_model="test-model",
        fallback_models=(),
        analytics_model="test-model",
        reasoning_effort="low",
        timeout_sec=5,
        max_output_tokens=128,
        prompt_cache_enable=False,
        prompt_cache_key="",
        prompt_cache_retention="24h",
        service_tier="auto",
        flex_unavailable_retry_enable=False,
        flex_unavailable_max_retries=0,
        flex_unavailable_cooldown_sec=0,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=30,
        log=lambda _: None,
        client_factory=lambda **_: client,
    )


class StructuredSchemaReliabilityTests(unittest.TestCase):
    def test_all_authoritative_objects_are_strict_recursively(self) -> None:
        for model in all_authoritative_structured_models():
            result = strict_structured_schema(model)
            self.assertTrue(result.valid, (model.__name__, result.errors))
            self.assertFalse(result.schema.get("additionalProperties"))
            for definition in result.schema.get("$defs", {}).values():
                if definition.get("type") == "object":
                    self.assertIs(definition.get("additionalProperties"), False)

    def test_non_strict_model_fails_preflight(self) -> None:
        result = strict_structured_schema(_NonStrictProbe)
        self.assertFalse(result.valid)
        self.assertIn("$:additionalProperties_must_be_false", result.errors)

    def test_nullable_fields_remain_required(self) -> None:
        schema = strict_structured_schema(AIGateEnvelope).schema
        self.assertIn("response_quality", schema["required"])
        response_quality = schema["properties"]["response_quality"]
        self.assertIn({"type": "null"}, response_quality["anyOf"])

    def test_schema_fingerprint_changes_with_contract(self) -> None:
        class ChangedProbe(StrictStructuredModel):
            ok: bool
            reason: str

        self.assertNotEqual(
            strict_structured_schema(_StrictProbe).schema_fingerprint,
            strict_structured_schema(ChangedProbe).schema_fingerprint,
        )

    def test_http_400_invalid_schema_opens_nonretryable_schema_circuit(self) -> None:
        calls = 0

        class Responses:
            def create(self, **_: object) -> object:
                nonlocal calls
                calls += 1
                raise _InvalidSchemaHttpError()

        responses = Responses()
        client = SimpleNamespace(
            responses=responses,
            with_options=lambda **_: SimpleNamespace(responses=responses),
        )
        provider = _remote_provider(client)
        metadata = {
            "decision_schema_version": ai_gate.AI_DECISION_SCHEMA_VERSION,
            "prompt_contract_version": ai_gate.AI_PROMPT_CONTRACT_VERSION,
        }
        with self.assertRaises(ProviderCallError) as first:
            provider.generate_structured(
                role="analyst",
                system_prompt="Return strict JSON.",
                evidence={"fixture": True},
                response_schema=_StrictProbe,
                request_metadata=metadata,
            )
        self.assertEqual(first.exception.category, "STRUCTURED_SCHEMA_INVALID")
        self.assertTrue(first.exception.configuration_block)
        with self.assertRaises(ProviderCallError) as second:
            provider.generate_structured(
                role="analyst",
                system_prompt="Return strict JSON.",
                evidence={"fixture": True},
                response_schema=_StrictProbe,
                request_metadata=metadata,
            )
        self.assertEqual(second.exception.category, "PROVIDER_CONFIGURATION_ERROR")
        self.assertEqual(calls, 1)

    def test_http_401_opens_configuration_circuit_without_per_candidate_retry(self) -> None:
        calls = 0

        class Responses:
            def create(self, **_: object) -> object:
                nonlocal calls
                calls += 1
                raise _AuthenticationHttpError()

        responses = Responses()
        client = SimpleNamespace(
            responses=responses,
            with_options=lambda **_: SimpleNamespace(responses=responses),
        )
        provider = _remote_provider(client)
        metadata = {
            "decision_schema_version": ai_gate.AI_DECISION_SCHEMA_VERSION,
            "prompt_contract_version": ai_gate.AI_PROMPT_CONTRACT_VERSION,
        }
        with self.assertRaises(ProviderCallError) as first:
            provider.generate_structured(
                role="analyst",
                system_prompt="Return strict JSON.",
                evidence={"fixture": True},
                response_schema=_StrictProbe,
                request_metadata=metadata,
            )
        self.assertEqual(first.exception.category, "PROVIDER_CONFIGURATION_ERROR")
        with self.assertRaises(ProviderCallError) as second:
            provider.generate_structured(
                role="analyst",
                system_prompt="Return strict JSON.",
                evidence={"fixture": True},
                response_schema=_StrictProbe,
                request_metadata=metadata,
            )
        self.assertEqual(second.exception.category, "PROVIDER_CONFIGURATION_ERROR")
        self.assertEqual(calls, 1)

    def test_runtime_validation_requires_exact_repeatable_cohort(self) -> None:
        self.assertEqual(
            ai_gate._runtime_repeatability_block_reason(
                True,
                {
                    "status": "UNAVAILABLE",
                    "reason": "repeatability_unavailable",
                    "artifact_state": "missing_group",
                },
            ),
            "repeatability_unavailable",
        )
        self.assertEqual(
            ai_gate._runtime_repeatability_block_reason(
                True,
                {"status": "REPEATABLE", "reason": ""},
            ),
            "",
        )
        self.assertEqual(
            ai_gate._runtime_repeatability_block_reason(
                False,
                {"status": "UNAVAILABLE", "reason": "repeatability_unavailable"},
            ),
            "",
        )


class RequestIdentityReliabilityTests(unittest.TestCase):
    @staticmethod
    def _payload() -> dict:
        candidates = [
            {
                "candidate_index": index,
                "candidate_id": f"candidate-{index}",
                "candidate_hash": f"HASH{index}123456789",
                "request_execution_fingerprint": f"EXEC{index}123456789",
                "setup_snapshot_time": 1780000000 + index,
                "setup_taxonomy_version": "taxonomy-v1",
                "setup_taxonomy_enum": "FULL_PO3_REVERSAL",
            }
            for index in range(2)
        ]
        return {
            "id": "request-identity-test",
            "session_id": "request-identity-session",
            "request_nonce": "request-identity-nonce",
            "contract_manifest_hash": compatibility_manifest_hash(),
            "request_created_sim_time": 1780000000,
            "request_created_wall_time": 1780000000000,
            "symbol": "GOLD",
            "is_buy": True,
            "runtime_input_hash": "runtime-hash",
            "engine_version": "engine-v1",
            "input_schema_version": "input-v1",
            "runtime_inputs": {},
            "candidates": candidates,
        }

    def test_exact_echo_passes_and_reordered_candidates_fail(self) -> None:
        payload = self._payload()
        provider_identity = {
            "provider_mode": "REMOTE_API",
            "provider_id": "remote",
            "model_id": "test-model",
            "model_fingerprint": "model-fingerprint",
            "generation_settings_hash": "generation-hash",
        }
        identity = build_ai_request_identity(
            payload,
            provider_identity=provider_identity,
            schema_fingerprint="schema-fingerprint",
            family_profile_version="family-v1",
            retrieval_policy_version="retrieval-v1",
        )
        self.assertEqual(identity["identity_version"], AI_REQUEST_IDENTITY_VERSION)
        echo = {
            "request_id": identity["request_id"],
            "request_identity_hash": identity["request_identity_hash"],
            "provider_id": identity["provider_id"],
            "model_id": identity["model_id"],
            "candidate_count": identity["candidate_count"],
            "ordered_candidate_identities": copy.deepcopy(
                identity["ordered_candidate_identities"]
            ),
        }
        self.assertTrue(validate_request_identity_echo(echo, echo).valid)
        reordered = copy.deepcopy(echo)
        reordered["ordered_candidate_identities"].reverse()
        result = validate_request_identity_echo(reordered, echo)
        self.assertFalse(result.valid)
        self.assertIn("ordered_candidate_identities", result.invalid_fields)

    def test_live_wait_debug_is_not_exported_to_replay_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {
                "id": "debug-request",
                "tester_cache_signature": "economic-signature",
                "tester_cache_key": "12345",
                "runtime_inputs": {
                    "tester_ai_mode": 2,
                    "tester_ai_mode_name": "TESTER_AI_LIVE_WAIT_DEBUG",
                },
            }
            status = ai_gate._export_mql_tester_replay_cache(
                payload,
                {},
                Path(temp_dir),
            )
            self.assertEqual(
                status,
                "skipped:live_wait_debug_not_replay_authoritative",
            )
            self.assertFalse(
                (Path(temp_dir) / "logs" / "tester_ai_cache" / "12345.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
