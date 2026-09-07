from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ai_provider import LocalOpenAICompatibleProvider, RemoteAPIProvider
from structured_models import StrictStructuredModel


class _ParitySchema(StrictStructuredModel):
    verdict: str


class _RemoteClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured
        self.responses = SimpleNamespace(create=self.create)

    def with_options(self, **options: Any) -> "_RemoteClient":
        self._captured["options"] = options
        return self

    def create(self, **kwargs: Any) -> Any:
        self._captured["request"] = kwargs
        return SimpleNamespace(
            output_text='{"verdict":"PASS"}',
            model=kwargs["model"],
            system_fingerprint="remote-test",
            usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12),
        )


class _LocalClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **options: Any) -> "_LocalClient":
        self._captured["options"] = options
        return self

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self._captured["request"] = kwargs
        return {
            "model": kwargs["model"],
            "system_fingerprint": "local-test",
            "choices": [{"message": {"content": '{"verdict":"PASS"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }


def _remote(logs: list[str]) -> RemoteAPIProvider:
    return RemoteAPIProvider(
        api_key="test-key",
        base_url="",
        primary_model="model-a",
        fallback_models=(),
        analytics_model="model-a",
        reasoning_effort="low",
        timeout_sec=300.0,
        max_output_tokens=2048,
        prompt_cache_enable=False,
        prompt_cache_key="",
        prompt_cache_retention="24h",
        service_tier="auto",
        flex_unavailable_retry_enable=False,
        flex_unavailable_max_retries=0,
        flex_unavailable_cooldown_sec=0.0,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=60.0,
        log=logs.append,
    )


def _local(logs: list[str]) -> LocalOpenAICompatibleProvider:
    return LocalOpenAICompatibleProvider(
        base_url="http://127.0.0.1:1234/v1",
        api_key="test-key",
        analyst_model="model-a",
        critic_model="model-a",
        adjudicator_model="model-a",
        fallback_models=(),
        healthcheck_path="/models",
        timeout_sec=300.0,
        max_retries=0,
        max_output_tokens=2048,
        temperature=0.15,
        top_p=0.85,
        seed=42,
        enable_thinking=False,
        require_json_schema=True,
        parallelism=1,
        context_budget_tokens=131072,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=60.0,
        log=logs.append,
    )


def test_remote_api_and_local_browser_share_one_semantic_exchange_contract():
    logs: list[str] = []
    remote_capture: dict[str, Any] = {}
    local_capture: dict[str, Any] = {}
    remote = _remote(logs)
    local = _local(logs)
    remote._client = _RemoteClient(remote_capture)
    local._client = _LocalClient(local_capture)
    metadata = {
        "request_id": "request-parity-1",
        "request_identity_hash": "a" * 64,
        "decision_schema_version": "decision-v1",
        "prompt_contract_version": "prompt-v1",
        "timeout_sec": 300.0,
        "max_output_tokens": 1024,
    }
    evidence = {"candidate_index": 3, "request_id": "request-parity-1"}

    remote_result = remote.generate_structured(
        role="analyst",
        system_prompt="Return the binding schema only.",
        evidence=evidence,
        response_schema=_ParitySchema,
        request_metadata=metadata,
    )
    local_result = local.generate_structured(
        role="analyst",
        system_prompt="Return the binding schema only.",
        evidence=evidence,
        response_schema=_ParitySchema,
        request_metadata=metadata,
    )

    remote_request = remote_capture["request"]
    local_request = local_capture["request"]
    assert remote_request["instructions"] == local_request["messages"][0]["content"]
    assert (
        remote_request["input"][0]["content"][0]["text"]
        == local_request["messages"][1]["content"]
    )
    assert remote_request["max_output_tokens"] == local_request["max_tokens"] == 1024
    assert (
        remote_request["text"]["format"]["schema"]
        == local_request["response_format"]["json_schema"]["schema"]
    )
    assert remote_result.exchange_contract_hash == local_result.exchange_contract_hash
    assert remote_result.parsed.model_dump() == local_result.parsed.model_dump()
    assert remote_result.transport_retry_count == local_result.transport_retry_count == 0
    assert sum("[provider_exchange_contract]" in line for line in logs) == 2


def test_remote_prompt_cache_retention_uses_sdk_compatible_extra_body():
    logs: list[str] = []
    capture: dict[str, Any] = {}
    provider = RemoteAPIProvider(
        api_key="test-key",
        base_url="",
        primary_model="model-a",
        fallback_models=(),
        analytics_model="model-a",
        reasoning_effort="low",
        timeout_sec=300.0,
        max_output_tokens=2048,
        prompt_cache_enable=True,
        prompt_cache_key="po3-live",
        prompt_cache_retention="24h",
        service_tier="auto",
        flex_unavailable_retry_enable=False,
        flex_unavailable_max_retries=0,
        flex_unavailable_cooldown_sec=0.0,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=60.0,
        log=logs.append,
    )
    provider._client = _RemoteClient(capture)
    provider.generate_structured(
        role="analyst",
        system_prompt="Return the binding schema only.",
        evidence={"candidate_index": 0},
        response_schema=_ParitySchema,
        request_metadata={"request_id": "cache-retention-test"},
    )

    request = capture["request"]
    assert request["prompt_cache_key"] == "po3-live"
    assert "prompt_cache_retention" not in request
    assert request["extra_body"] == {"prompt_cache_retention": "24h"}
