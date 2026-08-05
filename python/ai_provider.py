"""Provider-neutral structured AI transport for the Version Z decision gate.

This module owns transport only.  It does not score setups, resolve candidates,
or grant trading authority.  A selected provider can fall back only to models
served by that same provider; provider switching is intentionally impossible.
"""

from __future__ import annotations

import inspect
import ipaddress
import json
import math
import copy
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlparse

from provider_deadline import (
    DEADLINE_CONTRACT_VERSION,
    LATE_RESULT_QUARANTINE,
    STAGE_CONNECT,
    STAGE_RESPONSE_WAIT,
)
from structured_models import (
    StructuredCapabilityProbe,
    StructuredSchemaPreflight,
    strict_structured_schema,
)


PROVIDER_CONTRACT_VERSION = "20260723_provider_neutral_transport_v2"
PROVIDER_MODE_REMOTE = "REMOTE_API"
PROVIDER_MODE_LOCAL = "LOCAL_OPENAI_COMPATIBLE"
PROVIDER_MODE_UNAVAILABLE = "UNAVAILABLE"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def _strict_json_object(text: str) -> dict[str, Any]:
    duplicate_keys: list[str] = []

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicate_keys.append(key)
            result[key] = value
        return result

    decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non_finite_json:{value}")))
    value, end = decoder.raw_decode(text.lstrip())
    if text.lstrip()[end:].strip():
        raise ValueError("trailing_json_content")
    if duplicate_keys:
        raise ValueError("duplicate_json_keys:" + ",".join(sorted(set(duplicate_keys))))
    if not isinstance(value, dict):
        raise ValueError("structured_response_root_not_object")
    return value


def endpoint_class(base_url: str) -> str:
    try:
        host = (urlparse(base_url).hostname or "").strip().lower()
        if host == "localhost":
            return "loopback"
        if host:
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return "loopback"
            except ValueError:
                pass
        return "non_loopback" if host else "invalid"
    except Exception:
        return "invalid"


@dataclass(frozen=True)
class ProviderHealth:
    healthy: bool
    provider_mode: str
    provider_id: str
    endpoint_class: str
    model_id: str
    model_available: bool
    structured_output_available: bool
    reason: str = ""
    checked_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ProviderResult:
    parsed: Any
    raw_response: Any
    provider_mode: str
    provider_id: str
    endpoint_class: str
    requested_model: str
    actual_model: str
    fallback_model: str
    model_fingerprint: str
    role: str
    latency_sec: float
    retry_count: int
    transport_retry_count: int
    schema_retry_count: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    tokens_per_second: float | None
    generation_settings_hash: str
    health_state: str
    estimated_context_tokens: int | None = None
    unsupported_generation_parameters: tuple[str, ...] = ()
    schema_fingerprint: str = ""
    repair_attempted: bool = False


def _terminal_failure_category(errors: Sequence[str]) -> str:
    """Name the failure for what it actually was.

    Every per-attempt failure is appended to ``errors`` tagged with its own
    category; schema-validation failures carry a ``:schema:`` marker.  When the
    provider answered on every attempt and only the *content* failed strict
    validation, nothing about the transport went wrong, and reporting
    ``PROVIDER_TRANSPORT_ERROR`` sent operators to look at the network while the
    real defect was a schema/vocabulary disagreement.  A run whose attempts were
    exclusively schema failures is therefore reported as
    ``STRUCTURED_RESPONSE_INVALID``; any transport failure in the mix keeps the
    transport category, because the transport genuinely did fail at least once.
    """

    if errors and all(":schema:" in error for error in errors):
        return "STRUCTURED_RESPONSE_INVALID"
    return "PROVIDER_TRANSPORT_ERROR"


class ProviderCallError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        configuration_block: bool = False,
        schema_name: str = "",
        schema_fingerprint: str = "",
        repair_attempted: bool = False,
        repair_result: str = "not_attempted",
    ) -> None:
        self.category = str(category)
        self.status_code = status_code
        self.retryable = bool(retryable)
        self.configuration_block = bool(configuration_block)
        self.schema_name = str(schema_name)
        self.schema_fingerprint = str(schema_fingerprint)
        self.repair_attempted = bool(repair_attempted)
        self.repair_result = str(repair_result)
        super().__init__(f"{self.category}:{message}")


class AIProvider(Protocol):
    provider_mode: str
    provider_id: str
    endpoint_class: str

    def healthcheck(self, *, probe_structured: bool = False) -> ProviderHealth: ...

    def model_for_role(self, role: str) -> str: ...

    def configured_models(self, role: str) -> tuple[str, ...]: ...

    def identity(self, role: str = "analyst") -> dict[str, Any]: ...

    def generation_identity(
        self,
        role: str = "analyst",
        request_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def generate_structured(
        self,
        *,
        role: str,
        system_prompt: str,
        evidence: Mapping[str, Any],
        response_schema: type,
        request_metadata: Mapping[str, Any],
    ) -> ProviderResult: ...


def _usage_value(response: Any, *names: str) -> int | None:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("usage")
    for name in names:
        value = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return None


def _model_fingerprint(response: Any) -> str:
    for name in ("system_fingerprint", "model_fingerprint"):
        value = response.get(name) if isinstance(response, Mapping) else getattr(response, name, None)
        if value:
            return str(value)
    return ""


def _actual_model(response: Any, fallback: str) -> str:
    value = response.get("model") if isinstance(response, Mapping) else getattr(response, "model", None)
    return str(value or fallback)


def _schema_validate(schema: type, value: Mapping[str, Any]) -> Any:
    if hasattr(schema, "model_validate"):
        return schema.model_validate(value)
    return schema(**dict(value))


def _extract_responses_value(response: Any) -> dict[str, Any]:
    parsed = getattr(response, "output_parsed", None)
    if parsed is not None:
        if hasattr(parsed, "model_dump"):
            return dict(parsed.model_dump())
        if isinstance(parsed, Mapping):
            return dict(parsed)
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return _strict_json_object(output_text)
    output = getattr(response, "output", None)
    if isinstance(output, Sequence):
        for item in output:
            contents = getattr(item, "content", None)
            if not isinstance(contents, Sequence):
                continue
            for content in contents:
                candidate = getattr(content, "parsed", None)
                if candidate is not None:
                    if hasattr(candidate, "model_dump"):
                        return dict(candidate.model_dump())
                    if isinstance(candidate, Mapping):
                        return dict(candidate)
                text = getattr(content, "text", None)
                if isinstance(text, str) and text.strip():
                    return _strict_json_object(text)
    raise ValueError("structured_response_missing_json")


def _extract_chat_value(response: Any) -> dict[str, Any]:
    choices = response.get("choices") if isinstance(response, Mapping) else getattr(response, "choices", None)
    if not choices:
        raise ValueError("local_response_missing_choices")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else getattr(choice, "message", None)
    content = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
        content = "".join(text_parts)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("local_response_missing_content")
    return _strict_json_object(content)


class UnavailableProvider:
    provider_mode = PROVIDER_MODE_UNAVAILABLE
    provider_id = "unavailable"
    endpoint_class = "invalid"

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "provider_configuration_invalid")

    def healthcheck(self, *, probe_structured: bool = False) -> ProviderHealth:
        return ProviderHealth(False, self.provider_mode, self.provider_id, self.endpoint_class, "", False, False, self.reason)

    def model_for_role(self, role: str) -> str:
        return ""

    def configured_models(self, role: str) -> tuple[str, ...]:
        return ()

    def identity(self, role: str = "analyst") -> dict[str, Any]:
        return {
            "provider_contract_version": PROVIDER_CONTRACT_VERSION,
            "provider_mode": self.provider_mode,
            "provider_id": self.provider_id,
            "endpoint_class": self.endpoint_class,
            "model_id": "",
            "model_fingerprint": "",
            "generation_settings_hash": "",
            "configuration_valid": False,
            "configuration_error": self.reason,
        }

    def generation_identity(
        self,
        role: str = "analyst",
        request_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.identity(role)

    def generate_structured(self, **_: Any) -> ProviderResult:
        raise RuntimeError(self.reason)


class _OpenAICompatibleProviderBase:
    def __init__(
        self,
        *,
        provider_mode: str,
        provider_id: str,
        base_url: str,
        api_key: str,
        role_models: Mapping[str, str],
        fallback_models: Sequence[str],
        timeout_sec: float,
        max_retries: int,
        max_output_tokens: int,
        temperature: float | None,
        top_p: float | None,
        seed: int | None,
        parallelism: int,
        require_json_schema: bool,
        log: Callable[[str], None],
        client_factory: Callable[..., Any] | None = None,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_sec: float = 60.0,
    ) -> None:
        self.provider_mode = provider_mode
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.endpoint_class = endpoint_class(base_url) if base_url else "official_remote"
        self._api_key = api_key
        self._role_models = {str(k).lower(): str(v) for k, v in role_models.items() if str(v).strip()}
        self._fallback_models = tuple(str(v).strip() for v in fallback_models if str(v).strip())
        self.timeout_sec = float(timeout_sec)
        self.max_retries = max(0, int(max_retries))
        self.max_output_tokens = int(max_output_tokens)
        self.temperature = temperature
        self.top_p = top_p
        self.seed = seed
        self.require_json_schema = bool(require_json_schema)
        self._log = log
        self._client_factory = client_factory
        self._client: Any = None
        self._semaphore = threading.BoundedSemaphore(max(1, int(parallelism)))
        self._state_lock = threading.Lock()
        self._failure_count = 0
        self._circuit_open_until = 0.0
        self._circuit_failure_threshold = max(1, int(circuit_failure_threshold))
        self._circuit_cooldown_sec = max(1.0, float(circuit_cooldown_sec))
        self._last_health: ProviderHealth | None = None
        self._known_model_fingerprint = ""
        self._unsupported_logged: set[str] = set()
        self._configuration_circuits: dict[str, str] = {}

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        factory = self._client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("missing_dependency_openai") from exc
            factory = OpenAI
        # max_retries MUST be explicit.  The OpenAI SDK defaults to
        # DEFAULT_MAX_RETRIES=2 and gives every internal retry a *fresh* copy of
        # the full timeout, so a configured 90s timeout silently became a
        # ~270s+ wall-clock operation that outlived the MT5 terminal deadline.
        # Retry policy belongs to this class, bounded by the absolute request
        # deadline, not to the transport library.
        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": self.timeout_sec,
            "max_retries": 0,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        try:
            self._client = factory(**kwargs)
        except TypeError:
            # Test doubles and older factories may not accept max_retries.
            kwargs.pop("max_retries", None)
            self._client = factory(**kwargs)
        return self._client

    def model_for_role(self, role: str) -> str:
        role_key = str(role or "analyst").strip().lower()
        return self._role_models.get(role_key) or self._role_models.get("analyst") or ""

    def configured_models(self, role: str) -> tuple[str, ...]:
        return tuple(self._models_for_role(role))

    def _generation_settings(self, role: str) -> dict[str, Any]:
        return {
            "role": str(role).lower(),
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": self.seed,
            "require_json_schema": self.require_json_schema,
        }

    def identity(self, role: str = "analyst") -> dict[str, Any]:
        settings = self._generation_settings(role)
        configured_models = self.configured_models(role)
        endpoint_identity_hash = _canonical_hash(
            {
                "provider_mode": self.provider_mode,
                "base_url": self.base_url or "official_remote_default",
            }
        )
        return {
            "provider_contract_version": PROVIDER_CONTRACT_VERSION,
            "provider_mode": self.provider_mode,
            "provider_id": self.provider_id,
            "endpoint_class": self.endpoint_class,
            "endpoint_identity_hash": endpoint_identity_hash,
            "model_id": self.model_for_role(role),
            "model_fingerprint": self._known_model_fingerprint,
            "configured_model_ids": list(configured_models),
            "configured_models_hash": _canonical_hash(configured_models),
            "generation_settings": settings,
            "generation_settings_hash": _canonical_hash(settings),
            "configuration_valid": True,
        }

    def generation_identity(
        self,
        role: str = "analyst",
        request_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(request_metadata or {})
        identity = self.identity(role)
        settings = dict(identity.get("generation_settings") or {})
        settings["effective_timeout_sec"] = float(
            metadata.get("timeout_sec") or self.timeout_sec
        )
        settings["effective_max_output_tokens"] = max(
            1,
            int(metadata.get("max_output_tokens") or self.max_output_tokens),
        )
        identity["generation_settings"] = settings
        identity["generation_settings_hash"] = _canonical_hash(settings)
        return identity

    def _models_for_role(self, role: str) -> list[str]:
        values: list[str] = []
        for model in (self.model_for_role(role), *self._fallback_models):
            if model and model not in values:
                values.append(model)
        return values

    def _schema_circuit_key(
        self,
        *,
        model: str,
        request_metadata: Mapping[str, Any],
        preflight: StructuredSchemaPreflight,
    ) -> str:
        return _canonical_hash(
            {
                "provider_id": self.provider_id,
                "model_id": model,
                "decision_schema_version": str(request_metadata.get("decision_schema_version") or ""),
                "prompt_contract_version": str(request_metadata.get("prompt_contract_version") or ""),
                "schema_fingerprint": preflight.schema_fingerprint,
            }
        )

    def _configuration_circuit_check(self, key: str) -> None:
        with self._state_lock:
            reason = self._configuration_circuits.get(key, "")
        if reason:
            raise ProviderCallError(
                "PROVIDER_CONFIGURATION_ERROR",
                "provider_configuration_block:" + reason,
                configuration_block=True,
            )

    def _open_configuration_circuit(self, key: str, reason: str) -> None:
        with self._state_lock:
            self._configuration_circuits[key] = str(reason)
        self._log(
            "[provider_configuration_block]"
            f" provider_id={self.provider_id} key={key[:16]} reason={reason}"
        )

    def clear_configuration_circuit(self, key: str) -> None:
        with self._state_lock:
            self._configuration_circuits.pop(key, None)

    def configuration_circuit_count(self) -> int:
        with self._state_lock:
            return len(self._configuration_circuits)

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        for name in ("status_code", "status", "http_status"):
            value = getattr(exc, name, None)
            try:
                if value is not None:
                    return int(value)
            except Exception:
                continue
        return None

    @classmethod
    def _classify_transport_exception(cls, exc: Exception) -> ProviderCallError:
        if isinstance(exc, ProviderCallError):
            return exc
        status = cls._status_code(exc)
        text = str(exc).lower()
        error_body = getattr(exc, "body", None)
        if isinstance(error_body, Mapping):
            text += " " + json.dumps(error_body, sort_keys=True, default=str).lower()
        invalid_schema = status == 400 and any(
            marker in text
            for marker in (
                "invalid_json_schema",
                "invalid schema for response_format",
                "additionalproperties",
                "text.format.schema",
            )
        )
        unsupported_format = status == 400 and any(
            marker in text
            for marker in ("unsupported response_format", "response_format is not supported")
        )
        invalid_model = status == 400 and any(
            marker in text for marker in ("model_not_found", "invalid model", "does not exist")
        )
        if invalid_schema:
            return ProviderCallError(
                "STRUCTURED_SCHEMA_INVALID",
                str(exc),
                status_code=status,
                configuration_block=True,
            )
        if unsupported_format:
            return ProviderCallError(
                "PROVIDER_CONFIGURATION_ERROR",
                "unsupported_response_format:" + str(exc),
                status_code=status,
                configuration_block=True,
            )
        if status == 401:
            return ProviderCallError(
                "PROVIDER_CONFIGURATION_ERROR",
                "authentication_failed",
                status_code=status,
                configuration_block=True,
            )
        if status == 403:
            return ProviderCallError(
                "PROVIDER_CONFIGURATION_ERROR",
                "permission_denied",
                status_code=status,
                configuration_block=True,
            )
        if invalid_model:
            return ProviderCallError(
                "PROVIDER_CONFIGURATION_ERROR",
                "invalid_model:" + str(exc),
                status_code=status,
                configuration_block=True,
            )
        retryable = bool(
            status in {408, 409, 425, 429}
            or (status is not None and status >= 500)
            or any(marker in text for marker in ("timeout", "connection error", "temporarily unavailable"))
        )
        return ProviderCallError(
            "PROVIDER_TRANSPORT_ERROR",
            str(exc),
            status_code=status,
            retryable=retryable,
        )

    def _prepare_schema(
        self,
        *,
        response_schema: type,
        model: str,
        request_metadata: Mapping[str, Any],
    ) -> tuple[StructuredSchemaPreflight, str]:
        preflight = strict_structured_schema(response_schema)
        key = self._schema_circuit_key(
            model=model,
            request_metadata=request_metadata,
            preflight=preflight,
        )
        if not preflight.valid:
            reason = "local_schema_preflight:" + "|".join(preflight.errors)
            self._open_configuration_circuit(key, reason)
            raise ProviderCallError(
                "STRUCTURED_SCHEMA_INVALID",
                reason,
                configuration_block=True,
                schema_name=preflight.schema_name,
                schema_fingerprint=preflight.schema_fingerprint,
            )
        self._configuration_circuit_check(key)
        return preflight, key

    def _circuit_check(self) -> None:
        should_probe = False
        with self._state_lock:
            if self._circuit_open_until > time.monotonic():
                raise RuntimeError("provider_circuit_open")
            should_probe = self._circuit_open_until > 0.0
        if should_probe:
            health = self.healthcheck(probe_structured=False)
            if not health.healthy:
                with self._state_lock:
                    self._circuit_open_until = time.monotonic() + self._circuit_cooldown_sec
                raise RuntimeError("provider_circuit_recovery_healthcheck_failed:" + health.reason)
            self._record_success()

    def _record_success(self) -> None:
        with self._state_lock:
            self._failure_count = 0
            self._circuit_open_until = 0.0

    def _record_failure(self) -> None:
        with self._state_lock:
            self._failure_count += 1
            if self._failure_count >= self._circuit_failure_threshold:
                self._circuit_open_until = time.monotonic() + self._circuit_cooldown_sec

    def healthcheck(self, *, probe_structured: bool = False) -> ProviderHealth:
        raise NotImplementedError


class RemoteAPIProvider(_OpenAICompatibleProviderBase):
    """Official/remote Responses API transport with remote-only model fallback."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        primary_model: str,
        fallback_models: Sequence[str],
        analytics_model: str,
        reasoning_effort: str,
        timeout_sec: float,
        max_output_tokens: int,
        prompt_cache_enable: bool,
        prompt_cache_key: str,
        prompt_cache_retention: str,
        service_tier: str,
        flex_unavailable_retry_enable: bool,
        flex_unavailable_max_retries: int,
        flex_unavailable_cooldown_sec: float,
        circuit_failure_threshold: int,
        circuit_cooldown_sec: float,
        log: Callable[[str], None],
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            provider_mode=PROVIDER_MODE_REMOTE,
            provider_id="openai_remote_api",
            base_url=base_url,
            api_key=api_key,
            role_models={
                "analyst": primary_model,
                "critic": primary_model,
                "adjudicator": primary_model,
                "analytics": analytics_model or primary_model,
            },
            fallback_models=fallback_models,
            timeout_sec=timeout_sec,
            max_retries=1,
            max_output_tokens=max_output_tokens,
            temperature=None,
            top_p=None,
            seed=None,
            parallelism=16,
            require_json_schema=True,
            log=log,
            client_factory=client_factory,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_cooldown_sec=circuit_cooldown_sec,
        )
        self.reasoning_effort = reasoning_effort
        self.prompt_cache_enable = bool(prompt_cache_enable)
        self.prompt_cache_key = prompt_cache_key
        self.prompt_cache_retention = prompt_cache_retention
        self.service_tier = service_tier
        self.flex_unavailable_retry_enable = bool(flex_unavailable_retry_enable)
        self.flex_unavailable_max_retries = max(0, int(flex_unavailable_max_retries))
        self.flex_unavailable_cooldown_sec = max(0.0, float(flex_unavailable_cooldown_sec))

    def _generation_settings(self, role: str) -> dict[str, Any]:
        settings = super()._generation_settings(role)
        settings.update(
            {
                "reasoning_effort": self.reasoning_effort,
                "prompt_cache_enable": self.prompt_cache_enable,
                "prompt_cache_key": self.prompt_cache_key if self.prompt_cache_enable else "",
                "prompt_cache_retention": self.prompt_cache_retention if self.prompt_cache_enable else "",
                "service_tier": self.service_tier,
            }
        )
        return settings

    def healthcheck(self, *, probe_structured: bool = False) -> ProviderHealth:
        healthy = bool(self._api_key and self.model_for_role("analyst"))
        reason = "" if healthy else "remote_api_key_or_model_missing"
        health = ProviderHealth(
            healthy,
            self.provider_mode,
            self.provider_id,
            self.endpoint_class,
            self.model_for_role("analyst"),
            healthy,
            healthy,
            reason,
        )
        self._last_health = health
        return health

    def generation_identity(
        self,
        role: str = "analyst",
        request_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(request_metadata or {})
        identity = super().generation_identity(role, metadata)
        settings = dict(identity.get("generation_settings") or {})
        settings["effective_service_tier"] = str(metadata.get("service_tier") or self.service_tier or "auto")
        identity["generation_settings"] = settings
        identity["generation_settings_hash"] = _canonical_hash(settings)
        return identity

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        for name in ("status_code", "status", "http_status"):
            value = getattr(exc, name, None)
            try:
                if value is not None:
                    return int(value)
            except Exception:
                continue
        return None

    @classmethod
    def _flex_retryable(cls, exc: Exception) -> bool:
        status = cls._status_code(exc)
        if status in {408, 409, 425, 429} or (status is not None and status >= 500):
            return True
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "flex unavailable",
                "service tier",
                "temporarily unavailable",
                "rate limit",
                "timeout",
                "connection error",
            )
        )

    def generate_structured(
        self,
        *,
        role: str,
        system_prompt: str,
        evidence: Mapping[str, Any],
        response_schema: type,
        request_metadata: Mapping[str, Any],
    ) -> ProviderResult:
        self._circuit_check()
        errors: list[str] = []
        started = time.perf_counter()
        deadline = request_metadata.get("deadline")
        configured_timeout = float(
            request_metadata.get("timeout_sec") or self.timeout_sec
        )
        request_id = str(request_metadata.get("request_id") or "")
        if deadline is not None:
            self._log(
                "[provider_deadline]"
                f" request_id={request_id}"
                + deadline.as_log_fields()
                + f" configured_provider_timeout_ms={int(configured_timeout * 1000)}"
                f" deadline_contract_version={DEADLINE_CONTRACT_VERSION}"
            )
        attempt = 0
        for model_index, model in enumerate(self._models_for_role(role)):
            preflight, circuit_key = self._prepare_schema(
                response_schema=response_schema,
                model=model,
                request_metadata=request_metadata,
            )
            client = self._client_instance()
            transport_retries = 0
            schema_retries = 0
            flex_retries = 0
            while True:
                if deadline is not None and not deadline.can_start_attempt():
                    # Never begin an attempt that cannot finish inside the
                    # absolute budget; the caller still needs time to validate
                    # and atomically write an identity-bound response.
                    self._log(
                        "[provider_deadline_exceeded]"
                        f" request_id={request_id}"
                        f" stage={STAGE_CONNECT}"
                        f" attempt={attempt}"
                        f" remaining_ms={deadline.remaining_ms()}"
                        f" min_attempt_ms={deadline.policy.min_attempt_ms}"
                        f" late_result_action={LATE_RESULT_QUARANTINE}"
                    )
                    self._record_failure()
                    raise ProviderCallError(
                        "PROVIDER_DEADLINE_EXCEEDED",
                        "insufficient_remaining_budget_for_provider_attempt:"
                        f"remaining_ms={deadline.remaining_ms()}",
                        retryable=False,
                        schema_name=preflight.schema_name,
                        schema_fingerprint=preflight.schema_fingerprint,
                    )
                try:
                    user_content: list[dict[str, Any]] = [
                        {
                            "type": "input_text",
                            "text": json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                        }
                    ]
                    image_parts = request_metadata.get("image_parts")
                    if isinstance(image_parts, list):
                        user_content.extend(dict(part) for part in image_parts if isinstance(part, Mapping))
                    kwargs: dict[str, Any] = {
                        "model": model,
                        "instructions": system_prompt,
                        "input": [{"role": "user", "content": user_content}],
                        "max_output_tokens": max(
                            1,
                            int(request_metadata.get("max_output_tokens") or self.max_output_tokens),
                        ),
                        "store": False,
                        "truncation": "auto",
                        "text": {
                            "format": {
                                "type": "json_schema",
                                "name": response_schema.__name__,
                                "strict": True,
                                "schema": preflight.schema,
                            },
                            "verbosity": "low",
                        },
                    }
                    if self.reasoning_effort and self.reasoning_effort not in {"auto", "none"}:
                        kwargs["reasoning"] = {"effort": self.reasoning_effort}
                    if self.prompt_cache_enable:
                        kwargs["prompt_cache_key"] = self.prompt_cache_key
                        kwargs["prompt_cache_retention"] = self.prompt_cache_retention
                    service_tier = str(request_metadata.get("service_tier") or self.service_tier or "auto")
                    if service_tier:
                        kwargs["service_tier"] = service_tier
                    call_client = client
                    effective_timeout = configured_timeout
                    if deadline is not None:
                        # The SDK receives the *remaining* budget, never a fresh
                        # relative timer.  A deadline is never reset by a retry,
                        # a model fallback, or a schema repair.
                        effective_timeout = deadline.provider_timeout_sec(
                            configured_timeout
                        )
                    with_options = getattr(client, "with_options", None)
                    if callable(with_options):
                        try:
                            call_client = with_options(
                                timeout=effective_timeout, max_retries=0
                            )
                        except TypeError:
                            call_client = with_options(timeout=effective_timeout)
                    attempt += 1
                    self._log(
                        "[provider_attempt]"
                        f" request_id={request_id}"
                        f" attempt={attempt}"
                        f" model={model}"
                        f" transport_retry={transport_retries}"
                        f" schema_repair={schema_retries}"
                        f" flex_retry={flex_retries}"
                        f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                        f" sdk_timeout={effective_timeout:.3f}"
                        " sdk_max_retries=0"
                    )
                    self._log(
                        "[provider_call_started]"
                        f" request_id={request_metadata.get('request_id', '')}"
                        f" request_identity_hash={str(request_metadata.get('request_identity_hash') or '')[:16]}"
                        f" provider={self.provider_id} model={model} role={role}"
                        f" schema={preflight.schema_name}"
                        f" schema_fingerprint={preflight.schema_fingerprint[:16]}"
                        f" transport_retry={transport_retries}"
                        f" schema_repair={schema_retries}"
                    )
                    response = call_client.responses.create(**kwargs)
                    value = _extract_responses_value(response)
                    parsed = _schema_validate(response_schema, value)
                    elapsed = time.perf_counter() - started
                    completion = _usage_value(response, "output_tokens", "completion_tokens")
                    self.clear_configuration_circuit(circuit_key)
                    self._record_success()
                    settings_hash = self.generation_identity(role, request_metadata)["generation_settings_hash"]
                    self._log(
                        "[provider_call_completed]"
                        f" request_id={request_metadata.get('request_id', '')}"
                        f" request_identity_hash={str(request_metadata.get('request_identity_hash') or '')[:16]}"
                        f" provider={self.provider_id}"
                        f" model={_actual_model(response, model)} role={role}"
                        f" quality_tier=FULL_STRUCTURED latency_sec={elapsed:.3f}"
                        f" transport_retries={transport_retries + flex_retries}"
                        f" schema_repairs={schema_retries}"
                    )
                    return ProviderResult(
                        parsed,
                        response,
                        self.provider_mode,
                        self.provider_id,
                        self.endpoint_class,
                        self.model_for_role(role),
                        _actual_model(response, model),
                        model if model_index > 0 else "",
                        _model_fingerprint(response),
                        role,
                        elapsed,
                        transport_retries + schema_retries + flex_retries + model_index,
                        transport_retries + flex_retries,
                        schema_retries,
                        _usage_value(response, "input_tokens", "prompt_tokens"),
                        completion,
                        _usage_value(response, "total_tokens"),
                        None,
                        settings_hash,
                        "healthy",
                        None,
                        (),
                        preflight.schema_fingerprint,
                        schema_retries > 0,
                    )
                except (ValueError, TypeError) as exc:
                    errors.append(f"{model}:schema:{type(exc).__name__}:{exc}")
                    if schema_retries < 1 and (
                        deadline is None or deadline.can_start_attempt()
                    ):
                        schema_retries += 1
                        self._log(
                            "[structured_validation]"
                            f" provider={self.provider_id} model={model}"
                            f" schema={preflight.schema_name}"
                            f" schema_fingerprint={preflight.schema_fingerprint[:16]}"
                            " valid=false repair_attempted=true"
                        )
                        continue
                    break
                except Exception as exc:
                    failure = self._classify_transport_exception(exc)
                    if failure.configuration_block:
                        self._open_configuration_circuit(circuit_key, failure.category)
                        self._log(
                            "[provider_call_failed]"
                            f" provider={self.provider_id} model={model}"
                            f" http_status={failure.status_code or 0}"
                            f" schema={preflight.schema_name}"
                            f" schema_version={request_metadata.get('decision_schema_version', '')}"
                            f" error_category={failure.category}"
                            f" repair_attempted={str(schema_retries > 0).lower()}"
                            " final_quality_tier=DEGRADED_NON_TRADING"
                        )
                        if "invalid_model" in str(failure).lower() and model_index + 1 < len(self._models_for_role(role)):
                            errors.append(f"{model}:{failure.category}:{failure}")
                            break
                        raise ProviderCallError(
                            failure.category,
                            str(failure),
                            status_code=failure.status_code,
                            retryable=False,
                            configuration_block=True,
                            schema_name=preflight.schema_name,
                            schema_fingerprint=preflight.schema_fingerprint,
                            repair_attempted=schema_retries > 0,
                            repair_result="failed" if schema_retries else "not_attempted",
                        ) from exc
                    if deadline is not None and not deadline.can_start_attempt():
                        # The remaining budget cannot safely complete another
                        # attempt, so retries stop here regardless of how many
                        # the configuration would still allow.
                        self._log(
                            "[provider_deadline_exceeded]"
                            f" request_id={request_id}"
                            f" stage={STAGE_RESPONSE_WAIT}"
                            f" attempt={attempt}"
                            f" remaining_ms={deadline.remaining_ms()}"
                            f" error_category={failure.category}"
                            f" late_result_action={LATE_RESULT_QUARANTINE}"
                        )
                        errors.append(f"{model}:{failure.category}:{failure}")
                        break
                    service_tier = str(request_metadata.get("service_tier") or self.service_tier or "auto").lower()
                    cooldown_sec = self.flex_unavailable_cooldown_sec
                    cooldown_affordable = deadline is None or (
                        deadline.remaining_ms()
                        >= int(cooldown_sec * 1000) + deadline.policy.min_attempt_ms
                    )
                    if (
                        service_tier == "flex"
                        and self.flex_unavailable_retry_enable
                        and failure.retryable
                        and flex_retries < self.flex_unavailable_max_retries
                        and cooldown_affordable
                    ):
                        flex_retries += 1
                        self._log(
                            "[ai_provider] flex_unavailable_retry"
                            f" provider_id={self.provider_id} model={model}"
                            f" attempt={flex_retries}/{self.flex_unavailable_max_retries}"
                            f" cooldown_sec={cooldown_sec:.1f}"
                            f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                        )
                        if cooldown_sec > 0:
                            time.sleep(cooldown_sec)
                        continue
                    errors.append(f"{model}:{failure.category}:{failure}")
                    if failure.retryable and transport_retries < self.max_retries:
                        transport_retries += 1
                        continue
                    break
        self._record_failure()
        raise ProviderCallError(
            _terminal_failure_category(errors),
            "remote_provider_models_failed:" + " | ".join(errors),
            retryable=False,
            schema_name=response_schema.__name__,
            schema_fingerprint=preflight.schema_fingerprint,
            repair_attempted=any(":schema:" in error for error in errors),
            repair_result="failed",
        )


class LocalOpenAICompatibleProvider(_OpenAICompatibleProviderBase):
    """OpenAI-compatible local transport for LM Studio/llama.cpp-class servers."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        analyst_model: str,
        critic_model: str,
        adjudicator_model: str,
        fallback_models: Sequence[str],
        healthcheck_path: str,
        timeout_sec: float,
        max_retries: int,
        max_output_tokens: int,
        temperature: float,
        top_p: float,
        seed: int,
        enable_thinking: bool,
        require_json_schema: bool,
        parallelism: int,
        context_budget_tokens: int,
        circuit_failure_threshold: int,
        circuit_cooldown_sec: float,
        log: Callable[[str], None],
        client_factory: Callable[..., Any] | None = None,
        health_fetcher: Callable[[str, Mapping[str, str], float], Any] | None = None,
    ) -> None:
        super().__init__(
            provider_mode=PROVIDER_MODE_LOCAL,
            provider_id="local_openai_compatible",
            base_url=base_url,
            api_key=api_key,
            role_models={"analyst": analyst_model, "critic": critic_model, "adjudicator": adjudicator_model},
            fallback_models=fallback_models,
            timeout_sec=timeout_sec,
            max_retries=min(1, max(0, int(max_retries))),
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            parallelism=parallelism,
            require_json_schema=require_json_schema,
            log=log,
            client_factory=client_factory,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_cooldown_sec=circuit_cooldown_sec,
        )
        self.healthcheck_path = healthcheck_path or "/models"
        self.enable_thinking = bool(enable_thinking)
        self.context_budget_tokens = max(512, int(context_budget_tokens))
        self._health_fetcher = health_fetcher

    def _generation_settings(self, role: str) -> dict[str, Any]:
        settings = super()._generation_settings(role)
        settings["enable_thinking"] = self.enable_thinking
        settings["context_budget_tokens"] = self.context_budget_tokens
        return settings

    @staticmethod
    def _estimated_tokens(system_prompt: str, evidence: Mapping[str, Any]) -> int:
        text = system_prompt + "\n" + json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return max(1, math.ceil(len(text.encode("utf-8")) / 4.0))

    def _fit_context(
        self,
        system_prompt: str,
        evidence: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int]:
        compact = copy.deepcopy(dict(evidence))
        estimated = self._estimated_tokens(system_prompt, compact)
        if estimated <= self.context_budget_tokens:
            return compact, estimated
        # Historical memory is contextual and ordered by deterministic
        # similarity. Keep the closest five before refusing an oversized
        # authoritative request; never prune deterministic validation fields.
        analogues = compact.get("historical_analogues")
        if isinstance(analogues, list) and len(analogues) > 5:
            compact["historical_analogues"] = analogues[:5]
            compact["historical_analogues_compacted"] = {
                "original_count": len(analogues),
                "retained_count": 5,
                "policy": "closest_similarity_first",
            }
            estimated = self._estimated_tokens(system_prompt, compact)
        if estimated > self.context_budget_tokens:
            raise ValueError(
                f"local_context_budget_exceeded:estimated={estimated}:budget={self.context_budget_tokens}"
            )
        return compact, estimated

    def _log_unsupported_once(self, parameter: str) -> None:
        if parameter in self._unsupported_logged:
            return
        self._unsupported_logged.add(parameter)
        self._log(
            "[ai_provider] unsupported_local_generation_parameter"
            f" provider_id={self.provider_id} parameter={parameter} action=omit_and_retry"
        )

    @staticmethod
    def _unsupported_parameter(exc: Exception, kwargs: Mapping[str, Any]) -> str:
        message = str(exc).lower()
        unsupported_markers = ("unsupported", "unexpected", "unknown", "not permitted", "not allowed")
        if not any(marker in message for marker in unsupported_markers):
            return ""
        for parameter in ("seed", "temperature", "top_p", "extra_body"):
            if parameter in kwargs and parameter in message:
                return parameter
        return ""

    def _health_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", self.healthcheck_path.lstrip("/"))

    def _fetch_health(self) -> Any:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        if self._health_fetcher is not None:
            return self._health_fetcher(self._health_url(), headers, self.timeout_sec)
        request = urllib.request.Request(self._health_url(), headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            return _strict_json_object(response.read().decode("utf-8"))

    def healthcheck(self, *, probe_structured: bool = False) -> ProviderHealth:
        model = self.model_for_role("analyst")
        try:
            payload = self._fetch_health()
            rows = payload.get("data") if isinstance(payload, Mapping) else None
            available = {
                str(row.get("id") or row.get("model") or "")
                for row in (rows or [])
                if isinstance(row, Mapping)
            }
            model_available = model in available if available else False
            if model_available:
                model_row = next(
                    (
                        dict(row)
                        for row in (rows or [])
                        if isinstance(row, Mapping) and str(row.get("id") or row.get("model") or "") == model
                    ),
                    {"id": model},
                )
                self._known_model_fingerprint = _canonical_hash(model_row)
            structured = False
            reason = "" if model_available else "configured_local_model_not_available"
            if model_available and probe_structured:
                try:
                    result = self.generate_structured(
                        role="analyst",
                        system_prompt="Return exactly the requested JSON object. No prose.",
                        evidence={"probe": "return ok=true"},
                        response_schema=StructuredCapabilityProbe,
                        request_metadata={
                            "request_id": "provider_capability_probe",
                            "decision_schema_version": "provider_capability_probe",
                            "prompt_contract_version": "provider_capability_probe",
                            "non_trading": True,
                        },
                    )
                    structured = bool(getattr(result.parsed, "ok", False))
                    if not structured:
                        reason = "local_structured_output_probe_failed"
                except Exception as exc:
                    reason = "local_structured_output_probe_failed:" + type(exc).__name__
            elif model_available:
                structured = True
            health = ProviderHealth(
                bool(model_available and structured),
                self.provider_mode,
                self.provider_id,
                self.endpoint_class,
                model,
                model_available,
                structured,
                reason,
            )
        except Exception as exc:
            health = ProviderHealth(
                False,
                self.provider_mode,
                self.provider_id,
                self.endpoint_class,
                model,
                False,
                False,
                "local_healthcheck_failed:" + type(exc).__name__,
            )
        self._last_health = health
        return health

    def generate_structured(
        self,
        *,
        role: str,
        system_prompt: str,
        evidence: Mapping[str, Any],
        response_schema: type,
        request_metadata: Mapping[str, Any],
    ) -> ProviderResult:
        # Deterministic request-size validation precedes client construction,
        # circuit recovery probes, and every model call. An oversized evidence
        # envelope therefore fails locally without touching the configured
        # provider transport.
        fitted_evidence, estimated_context_tokens = self._fit_context(system_prompt, evidence)
        self._circuit_check()
        started = time.perf_counter()
        errors: list[str] = []
        transport_retries = 0
        schema_retries = 0
        unsupported: set[str] = set()
        with self._semaphore:
            for model_index, model in enumerate(self._models_for_role(role)):
                preflight, circuit_key = self._prepare_schema(
                    response_schema=response_schema,
                    model=model,
                    request_metadata=request_metadata,
                )
                client = self._client_instance()
                for attempt in range(self.max_retries + 1):
                    kwargs: dict[str, Any] = {}
                    try:
                        kwargs = {
                            "model": model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {
                                    "role": "user",
                                    "content": json.dumps(fitted_evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                                },
                            ],
                        "max_tokens": max(
                            1,
                            int(request_metadata.get("max_output_tokens") or self.max_output_tokens),
                        ),
                        }
                        if self.temperature is not None:
                            kwargs["temperature"] = self.temperature
                        if self.top_p is not None:
                            kwargs["top_p"] = self.top_p
                        if self.seed is not None:
                            kwargs["seed"] = self.seed
                        if self.enable_thinking:
                            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
                        if self.require_json_schema:
                            kwargs["response_format"] = {
                                "type": "json_schema",
                                "json_schema": {
                                    "name": response_schema.__name__,
                                    "strict": True,
                                    "schema": preflight.schema,
                                },
                            }
                        self._log(
                            "[provider_call_started]"
                            f" request_id={request_metadata.get('request_id', '')}"
                            f" request_identity_hash={str(request_metadata.get('request_identity_hash') or '')[:16]}"
                            f" provider={self.provider_id} model={model} role={role}"
                            f" schema={preflight.schema_name}"
                            f" schema_fingerprint={preflight.schema_fingerprint[:16]}"
                            f" transport_retry={attempt}"
                            f" schema_repair={schema_retries}"
                        )
                        response = client.chat.completions.create(**kwargs)
                        value = _extract_chat_value(response)
                        parsed = _schema_validate(response_schema, value)
                        elapsed = time.perf_counter() - started
                        completion = _usage_value(response, "completion_tokens", "output_tokens")
                        tokens_per_second = None
                        if completion is not None and elapsed > 0:
                            tokens_per_second = float(completion) / elapsed
                        self.clear_configuration_circuit(circuit_key)
                        self._record_success()
                        self._log(
                            "[provider_call_completed]"
                            f" request_id={request_metadata.get('request_id', '')}"
                            f" request_identity_hash={str(request_metadata.get('request_identity_hash') or '')[:16]}"
                            f" provider={self.provider_id}"
                            f" model={_actual_model(response, model)} role={role}"
                            f" quality_tier=FULL_STRUCTURED latency_sec={elapsed:.3f}"
                            f" transport_retries={transport_retries}"
                            f" schema_repairs={schema_retries}"
                        )
                        return ProviderResult(
                            parsed,
                            response,
                            self.provider_mode,
                            self.provider_id,
                            self.endpoint_class,
                            self.model_for_role(role),
                            _actual_model(response, model),
                            model if model_index > 0 else "",
                            _model_fingerprint(response) or self._known_model_fingerprint,
                            role,
                            elapsed,
                            transport_retries + schema_retries,
                            transport_retries,
                            schema_retries,
                            _usage_value(response, "prompt_tokens", "input_tokens"),
                            completion,
                            _usage_value(response, "total_tokens"),
                            tokens_per_second,
                            self.generation_identity(role, request_metadata)["generation_settings_hash"],
                            "healthy",
                            estimated_context_tokens,
                            tuple(sorted(unsupported)),
                            preflight.schema_fingerprint,
                            schema_retries > 0,
                        )
                    except (ValueError, TypeError) as exc:
                        unsupported_parameter = self._unsupported_parameter(exc, kwargs)
                        if unsupported_parameter and unsupported_parameter not in unsupported:
                            unsupported.add(unsupported_parameter)
                            self._log_unsupported_once(unsupported_parameter)
                            if unsupported_parameter == "extra_body":
                                self.enable_thinking = False
                            elif unsupported_parameter == "seed":
                                self.seed = None
                            elif unsupported_parameter == "temperature":
                                self.temperature = None
                            elif unsupported_parameter == "top_p":
                                self.top_p = None
                            continue
                        schema_retries += 1
                        errors.append(f"{model}:schema:{type(exc).__name__}:{exc}")
                        self._log(
                            "[structured_validation]"
                            f" provider={self.provider_id} model={model}"
                            f" schema={preflight.schema_name}"
                            f" schema_fingerprint={preflight.schema_fingerprint[:16]}"
                            " valid=false repair_attempted=true"
                        )
                        if attempt >= self.max_retries:
                            break
                    except Exception as exc:
                        unsupported_parameter = self._unsupported_parameter(exc, kwargs)
                        if unsupported_parameter and unsupported_parameter not in unsupported:
                            unsupported.add(unsupported_parameter)
                            self._log_unsupported_once(unsupported_parameter)
                            if unsupported_parameter == "extra_body":
                                self.enable_thinking = False
                            elif unsupported_parameter == "seed":
                                self.seed = None
                            elif unsupported_parameter == "temperature":
                                self.temperature = None
                            elif unsupported_parameter == "top_p":
                                self.top_p = None
                            continue
                        failure = self._classify_transport_exception(exc)
                        if failure.configuration_block:
                            self._open_configuration_circuit(circuit_key, failure.category)
                            self._log(
                                "[provider_call_failed]"
                                f" provider={self.provider_id} model={model}"
                                f" http_status={failure.status_code or 0}"
                                f" schema={preflight.schema_name}"
                                f" schema_version={request_metadata.get('decision_schema_version', '')}"
                                f" error_category={failure.category}"
                                f" repair_attempted={str(schema_retries > 0).lower()}"
                                " final_quality_tier=DEGRADED_NON_TRADING"
                            )
                            if "invalid_model" in str(failure).lower() and model_index + 1 < len(self._models_for_role(role)):
                                errors.append(f"{model}:{failure.category}:{failure}")
                                break
                            raise ProviderCallError(
                                failure.category,
                                str(failure),
                                status_code=failure.status_code,
                                retryable=False,
                                configuration_block=True,
                                schema_name=preflight.schema_name,
                                schema_fingerprint=preflight.schema_fingerprint,
                                repair_attempted=schema_retries > 0,
                                repair_result="failed" if schema_retries else "not_attempted",
                            ) from exc
                        transport_retries += 1
                        errors.append(f"{model}:{failure.category}:{failure}")
                        if not failure.retryable or attempt >= self.max_retries:
                            break
        self._record_failure()
        raise ProviderCallError(
            _terminal_failure_category(errors),
            "local_provider_models_failed:" + " | ".join(errors),
            retryable=False,
            schema_name=response_schema.__name__,
            schema_fingerprint=preflight.schema_fingerprint,
            repair_attempted=any(":schema:" in error for error in errors),
            repair_result="failed",
        )
