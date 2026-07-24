from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ai_gate
from ai_provider import (
    LocalOpenAICompatibleProvider,
    PROVIDER_MODE_LOCAL,
    PROVIDER_MODE_REMOTE,
    ProviderResult,
    RemoteAPIProvider,
    UnavailableProvider,
    endpoint_class,
)
from architecture_contracts import LIVE_FORWARD
from decision_integrity import DECISION_ABSTAIN, DECISION_APPROVE
from decision_pipeline import ROLE_CONTRACT_VERSION, run_qualitative_consensus
from structured_models import ModelCandidateAssessment, StrictStructuredModel
from tests.test_decision_integrity import assessment as integrity_assessment
from tests.test_decision_integrity import candidate as integrity_candidate
from trade_memory import TRADE_MEMORY_SCHEMA_VERSION, TradeMemoryStore


try:
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover - production import is fail-closed
    raise RuntimeError("missing_dependency_pydantic") from exc


class _ProbeSchema(StrictStructuredModel):
    ok: bool


def _provider_result(parsed: object, *, role: str, provider_mode: str = PROVIDER_MODE_LOCAL) -> ProviderResult:
    return ProviderResult(
        parsed=parsed,
        raw_response={"model": "test-model", "usage": {}},
        provider_mode=provider_mode,
        provider_id="test-provider",
        endpoint_class="loopback" if provider_mode == PROVIDER_MODE_LOCAL else "official_remote",
        requested_model="test-model",
        actual_model="test-model",
        fallback_model="",
        model_fingerprint="test-fingerprint",
        role=role,
        latency_sec=0.01,
        retry_count=0,
        transport_retry_count=0,
        schema_retry_count=0,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        tokens_per_second=None,
        generation_settings_hash="test-generation",
        health_state="healthy",
    )


class _ScriptedProvider:
    provider_mode = PROVIDER_MODE_LOCAL
    provider_id = "scripted-local"
    endpoint_class = "loopback"

    def __init__(self, outputs: dict[str, list[dict]]) -> None:
        self.outputs = {role: list(rows) for role, rows in outputs.items()}
        self.calls: list[tuple[str, dict]] = []

    def model_for_role(self, role: str) -> str:
        return "test-model"

    def generate_structured(
        self,
        *,
        role: str,
        evidence: dict,
        response_schema: type,
        request_metadata: dict,
        **_: object,
    ) -> ProviderResult:
        self.calls.append((role, copy.deepcopy(evidence)))
        row = self.outputs[role].pop(0)
        return _provider_result(response_schema.model_validate(row), role=role)


class _IdentityProvider:
    def __init__(self, mode: str, provider_id: str, model: str, fingerprint: str, generation: str) -> None:
        self.provider_mode = mode
        self.provider_id = provider_id
        self.endpoint_class = "loopback" if mode == PROVIDER_MODE_LOCAL else "official_remote"
        self._model = model
        self._fingerprint = fingerprint
        self._generation = generation

    def model_for_role(self, role: str) -> str:
        return self._model

    def identity(self, role: str = "analyst") -> dict:
        return self.generation_identity(role, {})

    def generation_identity(self, role: str = "analyst", request_metadata: dict | None = None) -> dict:
        return {
            "provider_mode": self.provider_mode,
            "provider_id": self.provider_id,
            "endpoint_class": self.endpoint_class,
            "endpoint_identity_hash": f"endpoint-{self.provider_id}",
            "configured_models_hash": f"models-{self._model}",
            "configured_model_ids": [self._model],
            "model_id": self._model,
            "model_fingerprint": self._fingerprint,
            "generation_settings_hash": self._generation,
        }


class _RetrievalStore:
    def __init__(self, retrieval_hash: str) -> None:
        self.retrieval_hash = retrieval_hash

    def retrieve_analogues(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(retrieval_hash=self.retrieval_hash)

    def record_pending_decision(self, **kwargs: object) -> bool:
        return True


class _FullOrchestrationProvider(_IdentityProvider):
    def __init__(self, candidate: dict) -> None:
        super().__init__(PROVIDER_MODE_LOCAL, "local", "qwen3.5-9b", "qwen-fingerprint", "local-generation")
        self.candidate = candidate
        self.calls: list[str] = []

    def generate_structured(self, *, role: str, response_schema: type, **kwargs: object) -> ProviderResult:
        self.calls.append(role)
        evidence = dict(kwargs.get("evidence") or {})
        request_metadata = dict(kwargs.get("request_metadata") or {})
        if role == "analyst":
            item = integrity_assessment(self.candidate)
            item["evidence_refs"] = ["entry_and_invalidation.candidates.0.candidate_hash"]
            comparison_item = {
                "usable": True,
                "reason": "Deterministic candidate is feasible.",
                "risk": "No qualitative target contradiction.",
                "expected_role": "tp2",
            }
            item["target_arbitration"]["target_comparison"] = {
                "liquidity_target": dict(comparison_item),
                "partial_before_obstacle_then_liquidity": {
                    **comparison_item,
                    "usable": False,
                    "expected_role": "reject",
                },
                "capped_before_obstacle": {
                    **comparison_item,
                    "usable": False,
                    "expected_role": "reject",
                },
                "synthetic_rr_capped_to_max_distance": {
                    **comparison_item,
                    "usable": False,
                    "expected_role": "reject",
                },
                "synthetic_rr_fallback": {
                    **comparison_item,
                    "usable": False,
                    "expected_role": "reject",
                },
            }
            item = {
                name: item[name]
                for name in ModelCandidateAssessment.model_fields
            }
            parsed = response_schema.model_validate(
                {
                    "decision_quality_tier": ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
                    "response_quality": ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
                    "selected_candidate_index": item["candidate_index"],
                    "candidate_assessments": [item],
                    "reasons": "Complete local provider fixture.",
                }
            )
        elif role == "critic":
            critic = _critic("PASS")
            parsed = response_schema.model_validate(critic)
        else:  # pragma: no cover - this fixture should not require adjudication
            raise AssertionError("unexpected adjudicator call")
        result = _provider_result(parsed, role=role)
        return replace(
            result,
            provider_id="local",
            requested_model="qwen3.5-9b",
            actual_model="qwen3.5-9b",
            model_fingerprint="qwen-fingerprint",
            generation_settings_hash="local-generation",
        )


def _base_local_env() -> dict[str, str]:
    return {
        "AI_USE_REMOTE_API": "false",
        "LOCAL_AI_BASE_URL": "http://127.0.0.1:1234/v1",
        "LOCAL_AI_API_KEY": "local",
        "LOCAL_AI_MODEL": "qwen3.5-9b",
        "LOCAL_AI_ANALYST_MODEL": "qwen3.5-9b",
        "LOCAL_AI_CRITIC_MODEL": "qwen3.5-9b",
        "LOCAL_AI_ADJUDICATOR_MODEL": "qwen3.5-9b",
    }


def _base_remote_env() -> dict[str, str]:
    return {
        "AI_USE_REMOTE_API": "true",
        "OPENAI_API_KEY": "remote-secret-for-test",
        "AI_GATE_MODEL": "remote-primary",
        "AI_GATE_FALLBACK_MODELS": "remote-fallback",
    }


def _cache_payload() -> dict:
    candidate = {
        "candidate_index": 0,
        "candidate_id": "candidate-A",
        "candidate_hash": "HASH-A-12345678",
        "request_execution_fingerprint": "REQ-FP-A-12345678",
        "symbol": "GOLD",
        "direction": "buy",
        "setup_family": "full_po3",
        "setup_class": "reversal",
        "entry_branch": "fvg_mid",
        "source_t_sweep": 100,
        "source_t_disp": 101,
        "source_t_bos": 102,
        "entry_est": 100.0,
        "sl": 99.0,
        "tp1": 101.0,
        "tp2": 102.0,
        "spread_r": 0.02,
        "execution_cost_r": 0.04,
        "target_source": "liquidity",
        "target_model": "liquidity_target",
        "structure_state": "confirmed",
        "fvg_mitigation_state": "virgin",
    }
    return {
        "id": "request-A",
        "request_created_sim_time": 1_720_000_000,
        "request_created_wall_time": 1_720_000_001,
        "symbol": "GOLD",
        "is_buy": True,
        "workload_mode": "RESEARCH",
        "runtime_input_hash": "runtime-record-only",
        "engine_version": "engine-test-v1",
        "input_schema_version": "input-test-v1",
        "snapshot_metadata": {"entry_candle_time": 1_720_000_000},
        "runtime_inputs": {
            "runtime_input_hash": "runtime-record-only",
            "tester_ai_cache": True,
            "tester_ai_mode": "TESTER_AI_RECORD_ONLY",
            "tester_allow_live_wait_debug_trading": False,
            "min_live_rr2": 1.05,
            "fallback_rr2": 1.70,
            "fallback_rr_buffer_r": 0.05,
            "max_target_atr_mult": 5.0,
            "max_target_adr_frac": 0.8,
        },
        "plan": copy.deepcopy(candidate),
        "candidates": [candidate],
        "po3": {"session_name": "LON", "in_killzone": True},
    }


def _consensus_evidence() -> dict:
    return {
        "entry_and_invalidation": {
            "candidates": [
                {
                    "candidate_id": "candidate-A",
                    "candidate_hash": "HASH-A-12345678",
                    "structure_state": "confirmed",
                }
            ]
        },
        "market_regime": {"regime": "trending"},
        "sequence": {"has_sweep": True},
        "liquidity": {"session_name": "LON"},
        "targets_and_obstacles": {},
        "correlations": {},
        "validation": {"missing_fields": [], "hard_blockers": []},
        "historical_analogues": [],
        "authority_manifest": {"llm": "qualitative_only"},
    }


def _analyst_assessment() -> dict:
    return {
        "candidate_index": 0,
        "candidate_id": "candidate-A",
        "candidate_hash": "HASH-A-12345678",
        "decision_state": DECISION_APPROVE,
        "confidence_band": "HIGH",
        "missing_required_evidence": [],
    }


def _critic(verdict: str = "PASS") -> dict:
    blocking = []
    if verdict == "BLOCK":
        blocking = [
            {
                "code": "ai_veto_structural_contradiction",
                "evidence_refs": ["candidate.structure_state"],
                "reason": "The supplied structure state contradicts the proposed narrative.",
            }
        ]
    return {
        "candidate_index": 0,
        "verdict": verdict,
        "blocking_objections": blocking,
        "non_blocking_objections": [],
        "missing_required_evidence": [],
        "evidence_refs": ["candidate.candidate_hash"],
        "confidence_band": "HIGH",
        "summary": "Independent evidence audit.",
    }


def _adjudicator(verdict: str) -> dict:
    resolved = ["ai_veto_structural_contradiction"] if verdict == "UPHOLD_APPROVE" else []
    unresolved = [] if verdict == "UPHOLD_APPROVE" else ["ai_veto_structural_contradiction"]
    return {
        "candidate_index": 0,
        "verdict": verdict,
        "resolved_objection_codes": resolved,
        "unresolved_objection_codes": unresolved,
        "evidence_refs": ["candidate.structure_state"],
        "resolution_reason": "The exact structured evidence was reviewed without changing the plan.",
    }


class ProviderConfigurationTests(unittest.TestCase):
    def test_explicit_remote_and_local_switches(self) -> None:
        remote = ai_gate.AIGateRuntimeConfig.from_env(_base_remote_env())
        local = ai_gate.AIGateRuntimeConfig.from_env(_base_local_env())
        self.assertTrue(remote.provider_config_valid)
        self.assertTrue(remote.use_remote_api)
        self.assertTrue(local.provider_config_valid)
        self.assertFalse(local.use_remote_api)

    def test_missing_or_invalid_switch_fails_closed(self) -> None:
        missing = ai_gate.AIGateRuntimeConfig.from_env({})
        invalid = ai_gate.AIGateRuntimeConfig.from_env({"AI_USE_REMOTE_API": "sometimes"})
        self.assertFalse(missing.provider_config_valid)
        self.assertIsNone(missing.use_remote_api)
        self.assertFalse(invalid.provider_config_valid)
        self.assertIn("AI_USE_REMOTE_API=missing_or_invalid", invalid.provider_config_errors)

    def test_local_endpoint_requires_loopback_or_explicit_ack(self) -> None:
        blocked_env = {**_base_local_env(), "LOCAL_AI_BASE_URL": "http://192.168.1.50:1234/v1"}
        acknowledged_env = {**blocked_env, "LOCAL_AI_ALLOW_NON_LOOPBACK_ACK": "true"}
        self.assertFalse(ai_gate.AIGateRuntimeConfig.from_env(blocked_env).provider_config_valid)
        self.assertTrue(ai_gate.AIGateRuntimeConfig.from_env(acknowledged_env).provider_config_valid)
        self.assertEqual(endpoint_class("http://localhost:1234/v1"), "loopback")
        self.assertEqual(endpoint_class("http://127.0.0.1:1234/v1"), "loopback")

    def test_safe_summary_redacts_secrets_and_sensitive_model_path(self) -> None:
        env = {
            **_base_local_env(),
            "OPENAI_API_KEY": "remote-secret-must-not-be-read",
            "LOCAL_AI_API_KEY": "local-secret-must-not-be-logged",
            "LOCAL_AI_MODEL_PATH": r"C:\sensitive\weights\qwen.gguf",
        }
        summary = json.dumps(ai_gate.AIGateRuntimeConfig.from_env(env).safe_log_dict(), sort_keys=True)
        self.assertNotIn("remote-secret", summary)
        self.assertNotIn("local-secret", summary)
        self.assertNotIn("qwen.gguf", summary)
        self.assertIn('"local_model_path_configured": true', summary)

    def test_provider_selection_constructs_only_the_selected_transport(self) -> None:
        local_config = ai_gate.AIGateRuntimeConfig.from_env(_base_local_env())
        remote_config = ai_gate.AIGateRuntimeConfig.from_env(_base_remote_env())
        local_sentinel = object()
        remote_sentinel = object()
        with patch("ai_gate.LocalOpenAICompatibleProvider", return_value=local_sentinel) as local_ctor, patch(
            "ai_gate.RemoteAPIProvider", return_value=remote_sentinel
        ) as remote_ctor:
            self.assertIs(ai_gate._build_ai_provider(local_config), local_sentinel)
            local_ctor.assert_called_once()
            remote_ctor.assert_not_called()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "remote-secret-for-test"}, clear=False), patch(
            "ai_gate.LocalOpenAICompatibleProvider", return_value=local_sentinel
        ) as local_ctor, patch("ai_gate.RemoteAPIProvider", return_value=remote_sentinel) as remote_ctor:
            self.assertIs(ai_gate._build_ai_provider(remote_config), remote_sentinel)
            remote_ctor.assert_called_once()
            local_ctor.assert_not_called()


class ProviderTransportTests(unittest.TestCase):
    def test_local_healthcheck_verifies_model_and_structured_output(self) -> None:
        class Completions:
            def create(self, **kwargs: object) -> dict:
                return {
                    "model": kwargs["model"],
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
                }

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        provider = LocalOpenAICompatibleProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="local",
            analyst_model="qwen3.5-9b",
            critic_model="qwen3.5-9b",
            adjudicator_model="qwen3.5-9b",
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=30,
            max_retries=0,
            max_output_tokens=256,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=False,
            require_json_schema=True,
            parallelism=1,
            context_budget_tokens=2048,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=30,
            log=lambda message: None,
            client_factory=lambda **kwargs: client,
            health_fetcher=lambda url, headers, timeout: {"data": [{"id": "qwen3.5-9b"}]},
        )
        health = provider.healthcheck(probe_structured=True)
        self.assertTrue(health.healthy)
        self.assertTrue(health.model_available)
        self.assertTrue(health.structured_output_available)
        self.assertTrue(provider.identity()["model_fingerprint"])

    def test_local_fallback_stays_inside_local_provider(self) -> None:
        calls: list[str] = []

        class Completions:
            def create(self, **kwargs: object) -> dict:
                model = str(kwargs["model"])
                calls.append(model)
                if model == "local-primary":
                    raise RuntimeError("local primary unavailable")
                return {
                    "model": model,
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
                }

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        provider = LocalOpenAICompatibleProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="local",
            analyst_model="local-primary",
            critic_model="local-primary",
            adjudicator_model="local-primary",
            fallback_models=("local-fallback",),
            healthcheck_path="/models",
            timeout_sec=30,
            max_retries=0,
            max_output_tokens=256,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=False,
            require_json_schema=True,
            parallelism=1,
            context_budget_tokens=2048,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=30,
            log=lambda message: None,
            client_factory=lambda **kwargs: client,
        )
        result = provider.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"candidate": "A"},
            response_schema=_ProbeSchema,
            request_metadata={},
        )
        self.assertEqual(calls, ["local-primary", "local-fallback"])
        self.assertEqual(result.actual_model, "local-fallback")
        self.assertEqual(result.fallback_model, "local-fallback")
        self.assertEqual(result.provider_mode, PROVIDER_MODE_LOCAL)

    def test_remote_fallback_stays_inside_remote_provider(self) -> None:
        calls: list[str] = []

        class Responses:
            def create(self, **kwargs: object) -> object:
                model = str(kwargs["model"])
                calls.append(model)
                if model == "remote-primary":
                    raise RuntimeError("remote primary unavailable")
                return SimpleNamespace(
                    model=model,
                    output_text='{"ok":true}',
                    usage=SimpleNamespace(input_tokens=2, output_tokens=2, total_tokens=4),
                )

        client = SimpleNamespace(responses=Responses(), with_options=lambda **kwargs: SimpleNamespace(responses=Responses()))
        provider = RemoteAPIProvider(
            api_key="remote-test",
            base_url="",
            primary_model="remote-primary",
            fallback_models=("remote-fallback",),
            analytics_model="remote-primary",
            reasoning_effort="low",
            timeout_sec=30,
            max_output_tokens=256,
            prompt_cache_enable=False,
            prompt_cache_key="",
            prompt_cache_retention="24h",
            service_tier="auto",
            flex_unavailable_retry_enable=False,
            flex_unavailable_max_retries=0,
            flex_unavailable_cooldown_sec=0,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=30,
            log=lambda message: None,
            client_factory=lambda **kwargs: client,
        )
        result = provider.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"candidate": "A"},
            response_schema=_ProbeSchema,
            request_metadata={},
        )
        self.assertTrue(calls)
        self.assertEqual(set(calls), {"remote-primary", "remote-fallback"})
        self.assertEqual(result.actual_model, "remote-fallback")
        self.assertEqual(result.provider_mode, PROVIDER_MODE_REMOTE)

    def test_oversized_local_context_fails_before_transport(self) -> None:
        client_factory = Mock(side_effect=AssertionError("transport must not be called"))
        provider = LocalOpenAICompatibleProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="local",
            analyst_model="qwen",
            critic_model="qwen",
            adjudicator_model="qwen",
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=30,
            max_retries=0,
            max_output_tokens=256,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=False,
            require_json_schema=True,
            parallelism=1,
            context_budget_tokens=512,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=30,
            log=lambda message: None,
            client_factory=client_factory,
        )
        with self.assertRaisesRegex(ValueError, "local_context_budget_exceeded"):
            provider.generate_structured(
                role="analyst",
                system_prompt="Return JSON.",
                evidence={"large": "x" * 20_000},
                response_schema=_ProbeSchema,
                request_metadata={},
            )
        client_factory.assert_not_called()

    def test_malformed_local_json_fails_closed_after_one_bounded_schema_retry(self) -> None:
        calls = 0

        class Completions:
            def create(self, **kwargs: object) -> dict:
                nonlocal calls
                calls += 1
                return {
                    "model": kwargs["model"],
                    "choices": [{"message": {"content": "not-json"}}],
                    "usage": {},
                }

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        provider = LocalOpenAICompatibleProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="local",
            analyst_model="qwen",
            critic_model="qwen",
            adjudicator_model="qwen",
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=30,
            max_retries=1,
            max_output_tokens=256,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=False,
            require_json_schema=True,
            parallelism=1,
            context_budget_tokens=2048,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=30,
            log=lambda message: None,
            client_factory=lambda **kwargs: client,
        )
        with self.assertRaisesRegex(RuntimeError, "local_provider_models_failed"):
            provider.generate_structured(
                role="analyst",
                system_prompt="Return JSON.",
                evidence={"candidate": "A"},
                response_schema=_ProbeSchema,
                request_metadata={},
            )
        self.assertEqual(calls, 2)


class ConsensusPipelineTests(unittest.TestCase):
    def test_analyst_approve_and_independent_critic_pass(self) -> None:
        provider = _ScriptedProvider({"critic": [_critic("PASS")]})
        result = run_qualitative_consensus(
            provider=provider,
            evidence=_consensus_evidence(),
            analyst_assessment=_analyst_assessment(),
            request_metadata={
                "request_id": "request-A",
                "request_identity_hash": "REQUESTIDENTITYCONSENSUS123",
            },
        )
        self.assertTrue(result.python_allow)
        self.assertEqual(result.decision_state, DECISION_APPROVE)
        self.assertEqual([role for role, _ in provider.calls], ["critic"])
        self.assertNotIn("analyst", provider.calls[0][1])

    def test_disagreement_runs_adjudicator_and_can_resolve_only_supported_objection(self) -> None:
        provider = _ScriptedProvider(
            {
                "critic": [_critic("BLOCK")],
                "adjudicator": [_adjudicator("UPHOLD_APPROVE")],
            }
        )
        result = run_qualitative_consensus(
            provider=provider,
            evidence=_consensus_evidence(),
            analyst_assessment=_analyst_assessment(),
            request_metadata={
                "request_id": "request-A",
                "request_identity_hash": "REQUESTIDENTITYCONSENSUS123",
            },
        )
        self.assertTrue(result.python_allow)
        self.assertEqual([role for role, _ in provider.calls], ["critic", "adjudicator"])

    def test_unresolved_disagreement_abstains(self) -> None:
        provider = _ScriptedProvider(
            {
                "critic": [_critic("BLOCK")],
                "adjudicator": [_adjudicator("ABSTAIN")],
            }
        )
        result = run_qualitative_consensus(
            provider=provider,
            evidence=_consensus_evidence(),
            analyst_assessment=_analyst_assessment(),
            request_metadata={
                "request_id": "request-A",
                "request_identity_hash": "REQUESTIDENTITYCONSENSUS123",
            },
        )
        self.assertFalse(result.python_allow)
        self.assertEqual(result.decision_state, DECISION_ABSTAIN)

    def test_unknown_critic_veto_code_fails_closed(self) -> None:
        critic = _critic("BLOCK")
        critic["blocking_objections"][0]["code"] = "free_text_block"
        provider = _ScriptedProvider({"critic": [critic]})
        with self.assertRaisesRegex(ValueError, "critic_objection_code_unknown"):
            run_qualitative_consensus(
                provider=provider,
                evidence=_consensus_evidence(),
                analyst_assessment=_analyst_assessment(),
                request_metadata={
                    "request_id": "request-A",
                    "request_identity_hash": "REQUESTIDENTITYCONSENSUS123",
                },
            )


class TradeMemoryRetrievalTests(unittest.TestCase):
    @staticmethod
    def _memory(memory_id: str, *, taxonomy: str, family: str, lineage: str, request_id: str) -> dict:
        completed = {
            "setup_taxonomy_enum": taxonomy,
            "setup_family": family,
            "entry_branch": "fvg_mid",
            "direction": "buy",
            "asset_class": "metal",
            "session": "LON",
            "killzone": "K",
            "regime_profile": "trending",
            "target_model": "liquidity_target",
            "rr2": 2.0,
            "execution_cost_r": 0.04,
            "full_close_r": 1.0,
            "mfe_r": 1.5,
            "mae_r": 0.2,
            "target_before_stop": True,
            "full_close_reason": "tp",
        }
        return {
            "memory_schema_version": TRADE_MEMORY_SCHEMA_VERSION,
            "memory_id": memory_id,
            "request_id": request_id,
            "trade_key": f"trade-{memory_id}",
            "candidate_hash": f"hash-{memory_id}",
            "lineage_id": lineage,
            "immutable_pre_entry_evidence": {"candidate": completed},
            "completed_trade": completed,
            "resolved_at": 1_720_000_000,
        }

    def test_exact_taxonomy_is_preferred_and_retrieval_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradeMemoryStore(Path(tmp) / "memory.sqlite3")
            store._insert_completed(
                self._memory("exact", taxonomy="FULL_PO3_REVERSAL", family="full_po3", lineage="old-1", request_id="old-1")
            )
            store._insert_completed(
                self._memory("family", taxonomy="FULL_PO3_CONTINUATION", family="full_po3", lineage="old-2", request_id="old-2")
            )
            candidate = {
                "candidate_hash": "current-hash",
                "setup_taxonomy_enum": "FULL_PO3_REVERSAL",
                "setup_family": "full_po3",
                "entry_branch": "fvg_mid",
                "direction": "buy",
                "asset_class": "metal",
                "session_code": "LON",
                "killzone_code": "K",
                "regime_profile": "trending",
                "target_model": "liquidity_target",
                "effective_rr2": 2.0,
                "execution_cost_r": 0.04,
            }
            first = store.retrieve_analogues(candidate, request_id="current", lineage_id="current-lineage")
            second = store.retrieve_analogues(candidate, request_id="current", lineage_id="current-lineage")
            self.assertEqual(first.analogue_ids, second.analogue_ids)
            self.assertEqual(first.analogue_ids[0], "exact")
            self.assertEqual(first.state, "AVAILABLE")

    def test_current_request_same_lineage_and_quarantine_are_never_retrieved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradeMemoryStore(Path(tmp) / "memory.sqlite3")
            store._insert_completed(
                self._memory("same-lineage", taxonomy="FULL_PO3_REVERSAL", family="full_po3", lineage="current-lineage", request_id="old")
            )
            store._insert_completed(
                self._memory("same-request", taxonomy="FULL_PO3_REVERSAL", family="full_po3", lineage="old", request_id="current")
            )
            store._quarantine("quarantined", "ledger_not_clean", {"setup_taxonomy_enum": "FULL_PO3_REVERSAL"})
            result = store.retrieve_analogues(
                {
                    "candidate_hash": "current-hash",
                    "setup_taxonomy_enum": "FULL_PO3_REVERSAL",
                    "setup_family": "full_po3",
                },
                request_id="current",
                lineage_id="current-lineage",
            )
            self.assertEqual(result.state, "INSUFFICIENT_SAMPLE")
            self.assertEqual(result.sample_count, 0)


class ProviderCacheAndShadowTests(unittest.TestCase):
    def _signature(self, payload: dict, provider: object, retrieval_hash: str = "retrieval-v1") -> str:
        with patch("ai_gate._provider", return_value=provider), patch(
            "ai_gate._trade_memory_store", return_value=_RetrievalStore(retrieval_hash)
        ), patch("ai_gate._load_live_bucket_priors", return_value={}), patch(
            "ai_gate._load_repeatability_artifact", return_value={"groups": {}}
        ):
            return ai_gate._decision_cache_signature(payload, 0)[0]

    def test_provider_model_fingerprint_generation_and_retrieval_partition_cache(self) -> None:
        payload = _cache_payload()
        remote = _IdentityProvider(PROVIDER_MODE_REMOTE, "remote", "gpt", "fp-remote", "gen-remote")
        local = _IdentityProvider(PROVIDER_MODE_LOCAL, "local", "qwen", "fp-local", "gen-local")
        remote_signature = self._signature(payload, remote)
        self.assertNotEqual(remote_signature, self._signature(payload, local))
        self.assertNotEqual(remote_signature, self._signature(payload, _IdentityProvider(PROVIDER_MODE_REMOTE, "remote", "gpt", "fp-v2", "gen-remote")))
        self.assertNotEqual(remote_signature, self._signature(payload, remote, retrieval_hash="retrieval-v2"))

    def test_tester_workflow_fields_change_runtime_hash_but_not_decision_signature(self) -> None:
        provider = _IdentityProvider(PROVIDER_MODE_REMOTE, "remote", "gpt", "fp", "generation")
        record = _cache_payload()
        replay = copy.deepcopy(record)
        replay["runtime_input_hash"] = "runtime-cache-only"
        replay["runtime_inputs"]["runtime_input_hash"] = "runtime-cache-only"
        replay["runtime_inputs"]["tester_ai_mode"] = "TESTER_AI_CACHE_ONLY"
        replay["runtime_inputs"]["tester_allow_live_wait_debug_trading"] = True
        self.assertNotEqual(record["runtime_input_hash"], replay["runtime_input_hash"])
        self.assertEqual(self._signature(record, provider), self._signature(replay, provider))

    def test_shadow_comparison_is_never_run_in_live_forward(self) -> None:
        config = replace(ai_gate.AI_CONFIG, shadow_compare_providers=True)
        decision = ai_gate.Decision(allow=False, score=0.0)
        with patch.object(ai_gate, "AI_CONFIG", config), patch("ai_gate._shadow_provider") as shadow:
            ai_gate._run_provider_shadow_comparison(
                {"id": "live", "workload_mode": LIVE_FORWARD},
                decision,
            )
            shadow.assert_not_called()

    def test_local_authority_never_builds_remote_shadow(self) -> None:
        selected = _IdentityProvider(PROVIDER_MODE_LOCAL, "local", "qwen", "fp", "generation")
        with patch("ai_gate._provider", return_value=selected):
            shadow = ai_gate._build_non_selected_shadow_provider()
        self.assertIsInstance(shadow, UnavailableProvider)
        self.assertEqual(shadow.reason, "remote_shadow_forbidden_by_local_privacy_contract")

    def test_shadow_unavailability_record_has_zero_authority(self) -> None:
        config = replace(ai_gate.AI_CONFIG, shadow_compare_providers=True)
        selected_provider = _IdentityProvider(PROVIDER_MODE_REMOTE, "remote", "gpt", "fp", "generation")
        selected_decision = ai_gate.Decision(
            allow=True,
            score=8.0,
            decision_state=DECISION_APPROVE,
            selected_candidate_hash="HASH-A-12345678",
            provider_mode=PROVIDER_MODE_REMOTE,
            provider_id="remote",
            actual_model_id="gpt",
            input_fingerprint="input-fp",
            mandatory_fields_complete=True,
        )
        captured: list[dict] = []
        with patch.object(ai_gate, "AI_CONFIG", config), patch(
            "ai_gate._provider", return_value=selected_provider
        ), patch("ai_gate._shadow_provider", return_value=UnavailableProvider("shadow unavailable")), patch(
            "ai_gate._append_provider_shadow_comparison", side_effect=lambda row: captured.append(dict(row)) or True
        ):
            ai_gate._run_provider_shadow_comparison(
                {"id": "research", "workload_mode": "RESEARCH"},
                selected_decision,
            )
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0]["trading_authority"])
        self.assertFalse(captured[0]["outcome_attribution"])
        self.assertEqual(captured[0]["shadow_status"], "UNAVAILABLE")


class PreModelSafetyTests(unittest.TestCase):
    def test_missing_candidate_identity_prevents_provider_invocation(self) -> None:
        provider = Mock()
        decision = ai_gate._score_setup_ai(
            {"id": "bad-request", "symbol": "GOLD", "candidates": [{"entry_est": 100.0}]},
            provider_override=provider,
        )
        self.assertFalse(decision.allow)
        self.assertIn("degraded_ai_response_non_trading", decision.rejection_codes)
        provider.generate_structured.assert_not_called()

    def test_mocked_local_response_uses_real_evidence_schema_and_consensus_path(self) -> None:
        candidate = integrity_candidate()
        provider = _FullOrchestrationProvider(candidate)
        payload = {
            "id": "local-e2e-request",
            "request_identity_version": ai_gate.AI_REQUEST_IDENTITY_VERSION,
            "request_identity_hash": "REQUESTIDENTITYE2E1234567890",
            "request_created_sim_time": 1780000000,
            "request_created_wall_time": 1780000000000,
            "symbol": "GOLD",
            "asset_class": "metal",
            "is_buy": True,
            "workload_mode": "RESEARCH",
            "engine_version": "engine-e2e-v1",
            "input_schema_version": "input-e2e-v1",
            "runtime": {"require_snapshots": False},
            "runtime_inputs": {"runtime_input_hash": "runtime-e2e"},
            "runtime_input_hash": "runtime-e2e",
            "plan": copy.deepcopy(candidate),
            "candidates": [candidate],
            "candidate_count": 1,
            "ordered_candidate_identities": [
                {
                    "candidate_index": candidate["candidate_index"],
                    "candidate_id": candidate["candidate_id"],
                    "candidate_hash": candidate["candidate_hash"],
                    "request_execution_fingerprint": candidate["request_execution_fingerprint"],
                }
            ],
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
        memory = _RetrievalStore("retrieval-e2e")
        memory.retrieve_analogues = lambda *args, **kwargs: SimpleNamespace(
            state="INSUFFICIENT_SAMPLE",
            analogue_ids=(),
            analogues=(),
            retrieval_hash="retrieval-e2e",
        )
        with patch("ai_gate._trade_memory_store", return_value=memory), patch(
            "ai_gate.log_ai_usage", return_value={}
        ), patch("ai_gate._write_ai_cost_report", return_value=None), patch(
            "ai_gate._load_live_bucket_priors", return_value={}
        ):
            decision = ai_gate._score_setup_ai(payload, provider_override=provider)
        self.assertTrue(decision.allow, decision)
        self.assertEqual(decision.decision_state, DECISION_APPROVE)
        self.assertEqual(provider.calls, ["analyst", "critic"])
        self.assertEqual(decision.provider_mode, PROVIDER_MODE_LOCAL)
        self.assertEqual(decision.provider_id, "local")
        self.assertEqual(decision.actual_model_id, "qwen3.5-9b")
        self.assertTrue(decision.input_fingerprint)
        self.assertTrue(decision.analyst_response_fingerprint)
        self.assertTrue(decision.critic_response_fingerprint)
        self.assertEqual(decision.final_resolver_reason, "analyst_approve_critic_pass")


if __name__ == "__main__":
    unittest.main()
