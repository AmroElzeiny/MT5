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
from opencode_routing import (
    IMPORTANCE_CRITICAL,
    IMPORTANCE_IMPORTANT,
    IMPORTANCE_NORMAL,
    ROUTING_POLICY_VERSION,
    OpenCodeRoutingPolicy,
    classify_importance,
)


PROVIDER_CONTRACT_VERSION = "20260723_provider_neutral_transport_v2"
PROVIDER_EXCHANGE_CONTRACT_VERSION = "20260811_provider_exchange_v1"
PROVIDER_MODE_REMOTE = "REMOTE_API"
PROVIDER_MODE_LOCAL = "LOCAL_OPENAI_COMPATIBLE"
PROVIDER_MODE_OPENROUTER = "OPENROUTER_API"
PROVIDER_MODE_OPENCODE = "OPENCODE_API"
PROVIDER_MODE_UNAVAILABLE = "UNAVAILABLE"

# Every mode MQL is willing to bind a decision to.  ``AIGateBridge.mqh`` and
# ``StateStore.mqh`` carry the identical literal set; adding a mode here without
# adding it there turns a healthy decision into an MQL schema rejection.
#
# ``OPENCODE_API`` names the OpenCode Go transport only.  A request that this
# mode routes to its OpenAI Luna fallback comes back reporting ``REMOTE_API``,
# because that is the transport that actually answered it -- the routed leg is
# never relabelled as OpenCode.
PROVIDER_MODES_TRADING = (
    PROVIDER_MODE_REMOTE,
    PROVIDER_MODE_LOCAL,
    PROVIDER_MODE_OPENROUTER,
    PROVIDER_MODE_OPENCODE,
)

# Reserved, non-trading request id used by the structured-output capability
# probe.  A local OpenAI-compatible server (including the Local AI Review
# Bridge) uses it to answer the probe itself instead of consuming a real
# review slot.  It is never a trading request id.
PROVIDER_CAPABILITY_PROBE_ID = "provider_capability_probe"

# Upper bound for the local ``/models`` reachability GET.  See
# ``LocalOpenAICompatibleProvider._health_timeout_sec``.
LOCAL_HEALTHCHECK_MAX_TIMEOUT_SEC = 150.0


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


def _health_failure_reason(exc: Exception) -> str:
    """Name why a local model listing failed, precisely.

    ``HTTPError`` alone sent operators looking for a dead server when the real
    cause was a rejected bearer key or an unroutable port.  Authentication,
    authorisation, refused connection, and timeout are separate operator
    actions and must be separate reasons.
    """

    if isinstance(exc, urllib.error.HTTPError):
        status = int(getattr(exc, "code", 0) or 0)
        if status == 401:
            return f"authentication_failed:http_{status}"
        if status == 403:
            return f"permission_denied:http_{status}"
        if status == 404:
            return f"healthcheck_path_not_found:http_{status}"
        return f"http_error:http_{status}"
    if isinstance(exc, urllib.error.URLError):
        inner = getattr(exc, "reason", None)
        if isinstance(inner, TimeoutError):
            return "unreachable:timeout"
        if inner is None:
            return "unreachable"
        return "unreachable:" + type(inner).__name__
    if isinstance(exc, TimeoutError):
        return "unreachable:timeout"
    return type(exc).__name__


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
    exchange_contract_hash: str = ""


@dataclass(frozen=True)
class ProviderExchangeContract:
    """Transport-neutral request that both browser and API providers must carry."""

    version: str
    role: str
    system_prompt: str
    evidence_json: str
    schema_name: str
    schema_fingerprint: str
    schema: dict[str, Any]
    request_id: str
    request_identity_hash: str
    max_output_tokens: int
    timeout_sec: float
    deadline_contract_version: str
    contract_hash: str


def build_provider_exchange_contract(
    *,
    role: str,
    system_prompt: str,
    evidence: Mapping[str, Any],
    preflight: StructuredSchemaPreflight,
    request_metadata: Mapping[str, Any],
    default_max_output_tokens: int,
    effective_timeout_sec: float,
) -> ProviderExchangeContract:
    """Freeze the semantic request before transport-specific wire encoding."""

    evidence_json = json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    payload = {
        "version": PROVIDER_EXCHANGE_CONTRACT_VERSION,
        "role": str(role).strip().lower(),
        "system_prompt": str(system_prompt),
        "evidence_json": evidence_json,
        "schema_name": preflight.schema_name,
        "schema_fingerprint": preflight.schema_fingerprint,
        "schema": preflight.schema,
        "request_id": str(request_metadata.get("request_id") or ""),
        "request_identity_hash": str(
            request_metadata.get("request_identity_hash") or ""
        ),
        "max_output_tokens": max(
            1,
            int(
                request_metadata.get("max_output_tokens")
                or default_max_output_tokens
            ),
        ),
        "timeout_sec": float(effective_timeout_sec),
        "deadline_contract_version": DEADLINE_CONTRACT_VERSION,
    }
    return ProviderExchangeContract(
        **payload,
        contract_hash=_canonical_hash(payload),
    )


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
        provider_call_attempted: bool = True,
        http_request_sent: bool = True,
    ) -> None:
        self.category = str(category)
        self.status_code = status_code
        self.retryable = bool(retryable)
        self.configuration_block = bool(configuration_block)
        # Refusals raised *before* the transport is touched must say so. The
        # gate used to hardcode both flags to true for every ProviderCallError,
        # so a breaker short-circuit -- which sends nothing -- was logged as
        # "http_request_sent=true http_status=0", asserting a call that never
        # happened. Defaults keep real call failures reporting as before.
        self.provider_call_attempted = bool(provider_call_attempted)
        self.http_request_sent = bool(http_request_sent)
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


def model_response_matches_request(requested_model: str, actual_model: str) -> bool:
    """Bind a routed response to the exact requested model or its latest release.

    OpenRouter's ``~vendor/family-latest`` smart alias deliberately returns the
    concrete dated release in ``response.model``.  Treating that as a mismatch
    breaks cache identity, while accepting any returned model lets a routing
    defect silently change trading authority.  A latest alias therefore accepts
    only the same vendor/family followed by a numeric release suffix.
    """

    requested = str(requested_model or "").strip()
    actual = str(actual_model or "").strip()
    if not requested or not actual:
        return False
    if actual == requested:
        return True
    if not (requested.startswith("~") and requested.endswith("-latest")):
        return False
    family = requested[1 : -len("-latest")]
    prefix = family + "-"
    if not actual.startswith(prefix):
        return False
    release = actual[len(prefix) :]
    return bool(release) and release.isdigit()


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
    # A bare "missing_json" is a correct fail-closed verdict with no evidence
    # for WHY, which is the defect class this project has already paid for
    # twice.  The Responses envelope states the reason itself -- an exhausted
    # output budget, a content filter, a still-running background response --
    # so carry it.  Diagnostic only: the failure and its category are unchanged.
    detail: list[str] = []
    status = getattr(response, "status", None)
    if isinstance(status, str) and status:
        detail.append("status=" + status)
    incomplete = getattr(response, "incomplete_details", None)
    reason = getattr(incomplete, "reason", None)
    if reason is None and isinstance(incomplete, Mapping):
        reason = incomplete.get("reason")
    if isinstance(reason, str) and reason:
        detail.append("incomplete_reason=" + reason)
    usage = getattr(response, "usage", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if isinstance(output_tokens, int):
        detail.append(f"output_tokens={output_tokens}")
    details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None)
    if isinstance(reasoning_tokens, int):
        detail.append(f"reasoning_tokens={reasoning_tokens}")
    raise ValueError(
        "structured_response_missing_json"
        + (":" + " ".join(detail) if detail else "")
    )


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
    # Cooldown waits are sliced so a worker notices another worker's recovery
    # (which zeroes ``_circuit_open_until``) instead of sleeping through it.
    _CIRCUIT_WAIT_SLICE_SEC = 1.0

    # Whether an AMBIGUOUS transport failure (timeout, connection error,
    # generic 5xx) may be resubmitted.  True only for transports where a second
    # submission cannot cost a second billed upstream call -- i.e. the loopback
    # local server.  Every paid remote transport sets this False and relies on
    # the admission-retry budget below, which resubmits only failures that are
    # PROVEN never to have been admitted.  Without the distinction, "retryable"
    # silently means "may double-bill and race a late result".
    resubmit_ambiguous_transport_failures = True

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
        # Admission-retry defaults.  Every transport carries the attributes so
        # the shared retry helpers below are usable from any subclass; a
        # subclass that has its own configuration overwrites them after
        # ``super().__init__``.
        self.admission_retry_enable = True
        self.admission_max_retries = 0
        self.admission_backoff_initial_sec = 2.0
        self.admission_backoff_max_sec = 30.0

    # ------------------------------------------------------------------
    # Admission-refusal classification, shared by every transport.
    #
    # These lived on ``RemoteAPIProvider`` only, so the OpenRouter transport --
    # which is a paid remote endpoint reached through the local-compatible loop
    # -- had no admission handling at all: it retried a 429 IMMEDIATELY, twice,
    # with no Retry-After and no backoff, which is the behaviour that
    # re-triggers the limit rather than clearing it.
    # ------------------------------------------------------------------

    # A quota exhaustion is a 429 that no amount of waiting inside this request
    # can clear -- it is a billing/configuration state, not congestion.  Retrying
    # it burns the whole deadline to reach the same answer.
    _ADMISSION_NON_RETRYABLE_MARKERS = (
        "insufficient_quota",
        "exceeded your current quota",
        "billing_hard_limit",
        "billing hard limit",
    )

    # Markers that prove the provider refused ADMISSION.  ``rate_limit_exceeded``
    # and "processing too many requests" are the exact strings the live gate saw
    # on 2026-09-07; ``_flex_capacity_rejected`` missed them because its only
    # near match was "rate limit" with a space.
    _ADMISSION_REJECTED_MARKERS = (
        "rate_limit_exceeded",
        "rate limit",
        "ratelimit",
        "too many requests",
        "try again later",
        "resource_unavailable",
        "temporarily unavailable",
        "overloaded",
        "server_overloaded",
        "capacity",
        "slow down",
    )

    @classmethod
    def _admission_rejected(cls, exc: Exception) -> bool:
        """True only when the provider refused to ADMIT the request at all.

        This is the same safety argument as ``_flex_capacity_rejected``, lifted
        off the flex service tier.  A 429/503 admission refusal means no tokens
        were produced, nothing is running server-side, and nothing can complete
        late -- so resubmitting still yields exactly one authoritative provider
        call per request identity.  Timeouts, connection errors and generic 5xx
        stay excluded because they are ambiguous: the call may have been
        admitted and still be running, where a resubmission would produce a
        second billed call and a late result racing the first.

        Quota exhaustion is excluded separately: it is a 429 that waiting cannot
        clear.
        """
        text = str(exc).lower()
        error_body = getattr(exc, "body", None)
        if isinstance(error_body, Mapping):
            text += " " + json.dumps(error_body, sort_keys=True, default=str).lower()
        if any(
            marker in text
            for marker in ("timeout", "timed out", "connection error", "read error")
        ):
            return False
        if any(marker in text for marker in cls._ADMISSION_NON_RETRYABLE_MARKERS):
            return False
        if cls._status_code(exc) not in {429, 503}:
            return False
        return any(marker in text for marker in cls._ADMISSION_REJECTED_MARKERS)

    @staticmethod
    def _retry_after_sec(exc: Exception) -> float | None:
        """The provider's own ``Retry-After`` instruction, in seconds.

        Honoured verbatim rather than capped: the affordability check against
        the absolute deadline decides whether we can wait that long, and a
        shorter wait than the server asked for is what produces a second 429.
        """
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        for name in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
            try:
                raw = headers.get(name)
            except Exception:
                continue
            if raw is None:
                continue
            text = str(raw).strip().lower()
            multiplier = 1.0
            if text.endswith("ms"):
                text, multiplier = text[:-2], 0.001
            elif text.endswith("s"):
                text, multiplier = text[:-1], 1.0
            try:
                value = float(text) * multiplier
            except (TypeError, ValueError):
                continue
            if value >= 0.0:
                return value
        return None

    def _admission_backoff_sec(self, attempt: int, request_id: str) -> float:
        """Exponential backoff with request-derived jitter.

        The jitter is derived from the request id instead of ``random`` so a
        replay of the same request sleeps the same amount, while concurrent
        workers that hit the same rate limit spread out instead of retrying in
        lockstep and re-triggering it.
        """
        base = self.admission_backoff_initial_sec * (2.0 ** max(0, attempt - 1))
        base = min(base, self.admission_backoff_max_sec)
        digest = sha256(f"{request_id}|{attempt}".encode("utf-8")).digest()
        jitter_fraction = (digest[0] / 255.0) * 0.25
        return base * (1.0 + jitter_fraction)

    def _admission_wait_plan(
        self,
        exc: Exception,
        *,
        attempts_used: int,
        deadline: Any,
        request_id: str,
    ) -> tuple[float, bool, bool] | None:
        """``(wait_sec, retry_after_header_present, affordable)`` or None.

        None means this failure is not an admission refusal, or the budget is
        exhausted, or admission retries are disabled -- i.e. nothing to decide.
        The sleep is spent INSIDE the absolute request budget, so both the wait
        and the attempt that follows it must fit or the retry only guarantees a
        deadline breach.
        """
        if not self.admission_retry_enable:
            return None
        if attempts_used >= self.admission_max_retries:
            return None
        if not self._admission_rejected(exc):
            return None
        wait_sec = self._retry_after_sec(exc)
        retry_after_present = wait_sec is not None
        if wait_sec is None:
            wait_sec = self._admission_backoff_sec(attempts_used + 1, request_id)
        affordable = deadline is None or (
            deadline.remaining_ms()
            >= int(wait_sec * 1000) + deadline.policy.min_attempt_ms
        )
        return wait_sec, retry_after_present, affordable

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
        default_headers = self._client_default_headers()
        if default_headers:
            kwargs["default_headers"] = default_headers
        # Test doubles and older factories may not accept every keyword.  Drop
        # the optional ones one at a time; ``api_key``/``timeout`` are required
        # and a factory rejecting those is a genuine configuration error.
        for optional in ("default_headers", "max_retries"):
            try:
                self._client = factory(**kwargs)
                return self._client
            except TypeError:
                if optional not in kwargs:
                    continue
                kwargs.pop(optional, None)
        self._client = factory(**kwargs)
        return self._client

    def _client_default_headers(self) -> dict[str, str]:
        """Per-transport headers applied to every request on this client."""

        return {}

    def _response_model_allowed(self, requested_model: str, actual_model: str) -> bool:
        """Transport hook for providers which resolve model aliases."""

        return True

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
                provider_call_attempted=False,
                http_request_sent=False,
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
        # An OpenRouter routing rejection is OUR request being unroutable, not
        # the transport failing.  It answers HTTP 404 with a routing funnel and
        # a ``failed_routing_step``; the generic branch below would call that
        # PROVIDER_TRANSPORT_ERROR, leave the configuration circuit closed, and
        # let every following request repeat the same doomed call while blaming
        # the network.  Measured on 2026-09-08: ``openai/gpt-5.6-luna``
        # publishes no endpoint that accepts ``temperature``/``top_p``, so with
        # ``provider.require_parameters=true`` a single sampling key emptied the
        # funnel and returned ``failed_routing_step="Filter by Parameters"``.
        # These messages are OpenRouter's own wording; no other transport in
        # this file can produce them.
        routing_rejected = status == 404 and any(
            marker in text
            for marker in (
                "no endpoints found",
                "no allowed providers",
                "failed_routing_step",
            )
        )
        if routing_rejected:
            return ProviderCallError(
                "PROVIDER_CONFIGURATION_ERROR",
                "router_no_eligible_endpoint:" + str(exc),
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

    def _circuit_wait_budget_ms(self, deadline: Any | None) -> int | None:
        """How long this request may spend waiting out a breaker cooldown.

        ``None`` means the caller supplied no absolute deadline, so nothing
        proves there is budget to spend; that caller must fail rather than
        block a worker for an unbounded time.  The reserve is the policy's own
        ``min_attempt_ms``: waiting until there is no time left to actually
        call the provider would trade one useless outcome for another.
        """

        if deadline is None:
            return None
        try:
            remaining_ms = int(deadline.remaining_ms())
            min_attempt_ms = int(deadline.policy.min_attempt_ms)
        except Exception:
            return None
        return max(0, remaining_ms - min_attempt_ms)

    def _circuit_check(
        self,
        *,
        request_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        # The breaker only ever opens because provider calls failed, so its
        # refusals belong to the provider/transport domain. Raising a bare
        # RuntimeError filed them as LOCAL_PIPELINE_ERROR instead, which read as
        # a defect in our own pipeline every time the network went down -- a
        # local label on a provider condition, the mirror of the mislabel fixed
        # for selected_provider_call_failed.
        #
        # An open breaker is a COOLDOWN, not a verdict on this request. It used
        # to raise immediately, and the gate turns any ProviderCallError into a
        # terminal DEGRADED_NON_TRADING response -- so a 60s cooldown consumed
        # every request claimed inside that window, permanently. One record-only
        # cohort lost 295 requests that way against 77 real connection errors:
        # the breaker destroyed four times more work than the outage it was
        # protecting against. Waiting spends time we already own (the request
        # deadline) instead of spending the request itself.
        metadata = request_metadata or {}
        deadline = metadata.get("deadline")
        request_id = str(metadata.get("request_id") or "")
        waited_ms = 0
        while True:
            with self._state_lock:
                cooldown_remaining_sec = self._circuit_open_until - time.monotonic()
                should_probe = (
                    cooldown_remaining_sec <= 0.0 and self._circuit_open_until > 0.0
                )
            if cooldown_remaining_sec > 0.0:
                cooldown_remaining_ms = int(math.ceil(cooldown_remaining_sec * 1000.0))
                # Recomputed every pass, so it already accounts for what this
                # request has spent waiting so far.
                budget_ms = self._circuit_wait_budget_ms(deadline)
                if budget_ms is None or budget_ms < cooldown_remaining_ms:
                    self._log(
                        "[provider_circuit_wait]"
                        f" request_id={request_id}"
                        f" provider={self.provider_id}"
                        f" cooldown_remaining_ms={cooldown_remaining_ms}"
                        f" request_budget_ms={-1 if budget_ms is None else budget_ms}"
                        f" waited_ms={waited_ms}"
                        " action=fail_deadline_cannot_cover_cooldown"
                    )
                    raise ProviderCallError(
                        "PROVIDER_TRANSPORT_ERROR",
                        "provider_circuit_open",
                        retryable=False,
                        provider_call_attempted=False,
                        http_request_sent=False,
                    )
                if waited_ms == 0:
                    self._log(
                        "[provider_circuit_wait]"
                        f" request_id={request_id}"
                        f" provider={self.provider_id}"
                        f" cooldown_remaining_ms={cooldown_remaining_ms}"
                        f" request_budget_ms={budget_ms}"
                        " action=wait_for_cooldown"
                    )
                slice_sec = min(cooldown_remaining_sec, self._CIRCUIT_WAIT_SLICE_SEC)
                time.sleep(slice_sec)
                waited_ms += int(round(slice_sec * 1000.0))
                continue
            if should_probe:
                health = self.healthcheck(probe_structured=False)
                if not health.healthy:
                    with self._state_lock:
                        self._circuit_open_until = (
                            time.monotonic() + self._circuit_cooldown_sec
                        )
                    self._log(
                        "[provider_circuit_wait]"
                        f" request_id={request_id}"
                        f" provider={self.provider_id}"
                        f" reason=recovery_healthcheck_failed:{health.reason}"
                        f" waited_ms={waited_ms}"
                        " action=reopen_and_recheck_within_budget"
                    )
                    continue
                self._record_success()
            if waited_ms > 0:
                self._log(
                    "[provider_circuit_wait]"
                    f" request_id={request_id}"
                    f" provider={self.provider_id}"
                    f" waited_ms={waited_ms}"
                    " action=cooldown_cleared_proceeding"
                )
            return

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
        admission_retry_enable: bool = True,
        admission_max_retries: int = 3,
        admission_backoff_initial_sec: float = 2.0,
        admission_backoff_max_sec: float = 30.0,
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
            # Transport retries are disabled exactly like the browser path.
            # A schema-only correction remains separately bounded to one turn.
            max_retries=0,
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
        # Transport flow stays equivalent to the browser path -- one submission
        # per admitted request. A flex *capacity rejection* is not a second
        # submission: the tier refused to admit the request at all, so no
        # output was generated, nothing is running server-side, and no late
        # result can arrive. Resubmitting it still yields exactly one
        # authoritative provider call for the request identity.
        # ``_flex_capacity_rejected`` is what keeps that true -- it excludes
        # timeouts and generic 5xx, which can mean the call *was* admitted.
        self.flex_unavailable_retry_enable = bool(flex_unavailable_retry_enable)
        self.flex_unavailable_max_retries = max(0, int(flex_unavailable_max_retries))
        self.flex_unavailable_cooldown_sec = max(0.0, float(flex_unavailable_cooldown_sec))
        # Tier-independent admission retry.  ``max_retries`` stays 0 for every
        # AMBIGUOUS transport failure -- this budget is separate and is spent
        # only on failures ``_admission_rejected`` proves were never admitted,
        # so the "one authoritative provider call per request identity"
        # invariant is preserved by construction rather than by configuration.
        self.admission_retry_enable = bool(admission_retry_enable)
        self.admission_max_retries = max(0, int(admission_max_retries))
        self.admission_backoff_initial_sec = max(0.0, float(admission_backoff_initial_sec))
        self.admission_backoff_max_sec = max(
            self.admission_backoff_initial_sec, float(admission_backoff_max_sec)
        )

    # How many times a *content* validation failure may be re-asked of the same
    # model before the call is abandoned.  1 preserves the transport's existing
    # single-repair behaviour exactly.  A transport that has a cheaper, better
    # answer to bad JSON than asking the same model again -- the OpenCode legs,
    # which fall back to Luna instead -- sets this to 0 so a malformed response
    # is not paid for twice.
    schema_repair_attempts = 1

    def _wire_responses_kwargs(
        self,
        kwargs: dict[str, Any],
        *,
        request_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Last-chance shaping of the Responses request body.

        The default is the identity function, so the official transport is
        byte-identical to before.  It exists so a Responses-compatible endpoint
        that is *not* api.openai.com can remove the OpenAI-account-specific keys
        (service tiers, prompt-cache handles) instead of a subclass having to
        copy the whole request loop to omit two fields.
        """

        return kwargs

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
    def _flex_capacity_rejected(cls, exc: Exception) -> bool:
        """True only when the service tier refused to admit the request.

        The distinction this draws is the whole safety argument for retrying a
        flex failure at all. A capacity rejection means the request was never
        accepted: no tokens were produced, nothing is running, nothing can
        complete late. Resubmitting it keeps the "one authoritative provider
        call per request identity" invariant intact.

        Timeouts, connection errors and generic 5xx are excluded precisely
        because they are ambiguous -- the call may have been admitted and still
        be running server-side, where a resubmission would produce a second
        billed call and a late result racing the first.
        """
        text = str(exc).lower()
        if any(
            marker in text
            for marker in ("timeout", "timed out", "connection error", "read error")
        ):
            return False
        if cls._status_code(exc) not in {429, 503}:
            return False
        return any(
            marker in text
            for marker in (
                "flex",
                "service tier",
                "service_tier",
                "resource_unavailable",
                "temporarily unavailable",
                "overloaded",
                "capacity",
                "rate limit",
            )
        )

    # ``_admission_rejected``, ``_retry_after_sec`` and ``_admission_backoff_sec``
    # now live on ``_OpenAICompatibleProviderBase`` so every paid transport --
    # this one and OpenRouter -- shares one definition of "was never admitted".
    # They were duplicated here while OpenRouter had none at all.

    def generate_structured(
        self,
        *,
        role: str,
        system_prompt: str,
        evidence: Mapping[str, Any],
        response_schema: type,
        request_metadata: Mapping[str, Any],
    ) -> ProviderResult:
        self._circuit_check(request_metadata=request_metadata)
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
        last_preflight: StructuredSchemaPreflight | None = None
        for model_index, model in enumerate(self._models_for_role(role)):
            preflight, circuit_key = self._prepare_schema(
                response_schema=response_schema,
                model=model,
                request_metadata=request_metadata,
            )
            last_preflight = preflight
            client = self._client_instance()
            transport_retries = 0
            schema_retries = 0
            flex_retries = 0
            admission_retries = 0
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
                    effective_timeout = configured_timeout
                    if deadline is not None:
                        # Both the API and browser paths receive the same
                        # absolute remaining budget; neither may reset it.
                        effective_timeout = deadline.provider_timeout_sec(
                            configured_timeout
                        )
                    exchange = build_provider_exchange_contract(
                        role=role,
                        system_prompt=system_prompt,
                        evidence=evidence,
                        preflight=preflight,
                        request_metadata=request_metadata,
                        default_max_output_tokens=self.max_output_tokens,
                        effective_timeout_sec=effective_timeout,
                    )
                    user_content: list[dict[str, Any]] = [
                        {
                            "type": "input_text",
                            "text": exchange.evidence_json,
                        }
                    ]
                    image_parts = request_metadata.get("image_parts")
                    if isinstance(image_parts, list):
                        user_content.extend(dict(part) for part in image_parts if isinstance(part, Mapping))
                    kwargs: dict[str, Any] = {
                        "model": model,
                        "instructions": exchange.system_prompt,
                        "input": [{"role": "user", "content": user_content}],
                        "max_output_tokens": exchange.max_output_tokens,
                        "store": False,
                        "truncation": "auto",
                        "text": {
                            "format": {
                                "type": "json_schema",
                                "name": exchange.schema_name,
                                "strict": True,
                                "schema": exchange.schema,
                            },
                            "verbosity": "low",
                        },
                    }
                    if deadline is not None:
                        transport_budget_ms = min(
                            max(0, deadline.remaining_ms()),
                            max(1000, int(effective_timeout * 1000.0)),
                        )
                        provider_budget_ms = max(1000, transport_budget_ms - 2000)
                        kwargs["extra_headers"] = {
                            "X-PO3-Deadline-Epoch-Ms": str(
                                int(time.time() * 1000.0 + provider_budget_ms)
                            ),
                            "X-PO3-Request-Id": request_id,
                            "X-PO3-Deadline-Contract": DEADLINE_CONTRACT_VERSION,
                        }
                    if self.reasoning_effort and self.reasoning_effort not in {"auto", "none"}:
                        kwargs["reasoning"] = {"effort": self.reasoning_effort}
                    if self.prompt_cache_enable:
                        kwargs["prompt_cache_key"] = self.prompt_cache_key
                        # openai-python 2.4 exposes ``prompt_cache_key`` but not
                        # the newer ``prompt_cache_retention`` keyword.  Passing
                        # it as a top-level SDK argument raises TypeError before
                        # any HTTP request is sent and was incorrectly consumed
                        # as a structured-output repair. ``extra_body`` is the
                        # SDK-supported compatibility path for newer API fields.
                        if self.prompt_cache_retention:
                            kwargs["extra_body"] = {
                                "prompt_cache_retention": self.prompt_cache_retention
                            }
                    service_tier = str(request_metadata.get("service_tier") or self.service_tier or "auto")
                    if service_tier:
                        kwargs["service_tier"] = service_tier
                    kwargs = self._wire_responses_kwargs(
                        kwargs, request_metadata=request_metadata
                    )
                    call_client = client
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
                        "[provider_exchange_contract]"
                        f" request_id={exchange.request_id}"
                        f" request_identity_hash={exchange.request_identity_hash[:16]}"
                        f" version={exchange.version}"
                        f" role={exchange.role}"
                        f" schema_fingerprint={exchange.schema_fingerprint[:16]}"
                        f" exchange_hash={exchange.contract_hash[:16]}"
                    )
                    self._log(
                        "[provider_attempt]"
                        f" request_id={request_id}"
                        f" attempt={attempt}"
                        f" model={model}"
                        f" transport_retry={transport_retries}"
                        f" schema_repair={schema_retries}"
                        f" flex_retry={flex_retries}"
                        f" admission_retry={admission_retries}"
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
                        exchange.contract_hash,
                    )
                except (ValueError, TypeError) as exc:
                    errors.append(f"{model}:schema:{type(exc).__name__}:{exc}")
                    if schema_retries < self.schema_repair_attempts and (
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
                        and self._flex_capacity_rejected(exc)
                        and flex_retries < self.flex_unavailable_max_retries
                        and cooldown_affordable
                    ):
                        flex_retries += 1
                        self._log(
                            "[ai_provider] flex_unavailable_retry"
                            f" provider_id={self.provider_id} model={model}"
                            f" attempt={flex_retries}/{self.flex_unavailable_max_retries}"
                            f" http_status={failure.status_code or 0}"
                            " admitted=false resubmission_safe=true"
                            f" cooldown_sec={cooldown_sec:.1f}"
                            f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                        )
                        if cooldown_sec > 0:
                            time.sleep(cooldown_sec)
                        continue
                    # An admission refusal that the flex branch above did not
                    # already own.  Guarded so flex keeps exactly its previous
                    # budget instead of silently gaining a second one.
                    flex_owns_failure = (
                        service_tier == "flex"
                        and self.flex_unavailable_retry_enable
                        and self._flex_capacity_rejected(exc)
                    )
                    if (
                        self.admission_retry_enable
                        and not flex_owns_failure
                        and admission_retries < self.admission_max_retries
                        and self._admission_rejected(exc)
                    ):
                        wait_sec = self._retry_after_sec(exc)
                        retry_after_present = wait_sec is not None
                        if wait_sec is None:
                            wait_sec = self._admission_backoff_sec(
                                admission_retries + 1, request_id
                            )
                        # The sleep is spent INSIDE the absolute request budget.
                        # Both the wait and the attempt that follows it must fit,
                        # or the retry only guarantees a deadline breach.
                        affordable = deadline is None or (
                            deadline.remaining_ms()
                            >= int(wait_sec * 1000) + deadline.policy.min_attempt_ms
                        )
                        if affordable:
                            admission_retries += 1
                            self._log(
                                "[provider_admission_retry]"
                                f" request_id={request_id}"
                                f" provider={self.provider_id} model={model}"
                                f" attempt={admission_retries}/{self.admission_max_retries}"
                                f" http_status={failure.status_code or 0}"
                                f" error_category={failure.category}"
                                " admitted=false resubmission_safe=true"
                                f" retry_after_header={str(retry_after_present).lower()}"
                                f" wait_sec={wait_sec:.3f}"
                                f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                            )
                            if wait_sec > 0:
                                time.sleep(wait_sec)
                            continue
                        self._log(
                            "[provider_admission_retry]"
                            f" request_id={request_id}"
                            f" provider={self.provider_id} model={model}"
                            f" attempt={admission_retries + 1}/{self.admission_max_retries}"
                            f" http_status={failure.status_code or 0}"
                            f" wait_sec={wait_sec:.3f}"
                            f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                            f" min_attempt_ms={deadline.policy.min_attempt_ms if deadline is not None else -1}"
                            " action=skipped_insufficient_budget"
                        )
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
            schema_fingerprint=(
                last_preflight.schema_fingerprint if last_preflight is not None else ""
            ),
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
        provider_mode: str = PROVIDER_MODE_LOCAL,
        provider_id: str = "local_openai_compatible",
        max_retries_ceiling: int = 1,
    ) -> None:
        super().__init__(
            provider_mode=provider_mode,
            provider_id=provider_id,
            base_url=base_url,
            api_key=api_key,
            role_models={"analyst": analyst_model, "critic": critic_model, "adjudicator": adjudicator_model},
            fallback_models=fallback_models,
            timeout_sec=timeout_sec,
            max_retries=min(int(max_retries_ceiling), max(0, int(max_retries))),
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

    # ---- transport wire hooks -------------------------------------------
    # The chat-completions request body below is shared by every transport in
    # this family.  Only these two hooks differ between them, so a new
    # transport overrides the hooks instead of copying the request loop.

    def _wire_extra_body(self) -> dict[str, Any]:
        """Vendor-specific ``extra_body``.

        The local server family enables its thinking mode through the
        vLLM/LM-Studio chat-template keyword.  Returning ``{}`` means no
        ``extra_body`` key is sent at all.
        """

        if self.enable_thinking:
            return {"chat_template_kwargs": {"enable_thinking": True}}
        return {}

    def _wire_max_tokens(self, requested: int) -> int:
        """Output-token budget actually sent on the wire.

        The caller sizes ``requested`` from the schema alone.  A transport whose
        ``max_tokens`` also has to cover server-side reasoning tokens must widen
        it here, or the reasoning consumes the whole budget and the response
        comes back truncated with no content at all.
        """

        return int(requested)

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

    def _health_timeout_sec(self) -> float:
        """A model-listing GET is not a generation call.

        ``healthcheck`` is re-run while live requests are in flight, so binding
        it to the full generation timeout (180s for the browser bridge) let one
        unresponsive server consume an entire MT5 terminal window before the
        gate could even decide the provider was unhealthy.  A loopback model
        listing that cannot answer inside this bound is unhealthy by definition.
        """

        return max(1.0, min(float(self.timeout_sec), LOCAL_HEALTHCHECK_MAX_TIMEOUT_SEC))

    def _fetch_health(self) -> Any:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        timeout_sec = self._health_timeout_sec()
        if self._health_fetcher is not None:
            return self._health_fetcher(self._health_url(), headers, timeout_sec)
        request = urllib.request.Request(self._health_url(), headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
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
                        # ``request_metadata`` never crosses the wire on the
                        # chat-completions transport, so a server that has to
                        # distinguish a non-trading capability probe from a real
                        # decision request can only see the message bodies.  The
                        # probe therefore identifies itself *inside* the evidence
                        # as well, using the same reserved id.  This is probe-only
                        # evidence; no authoritative request payload is affected.
                        evidence={
                            "request_id": PROVIDER_CAPABILITY_PROBE_ID,
                            "non_trading": True,
                            "probe": "return ok=true",
                        },
                        response_schema=StructuredCapabilityProbe,
                        request_metadata={
                            "request_id": PROVIDER_CAPABILITY_PROBE_ID,
                            "decision_schema_version": PROVIDER_CAPABILITY_PROBE_ID,
                            "prompt_contract_version": PROVIDER_CAPABILITY_PROBE_ID,
                            "non_trading": True,
                        },
                    )
                    structured = bool(getattr(result.parsed, "ok", False))
                    if not structured:
                        reason = "local_structured_output_probe_failed"
                except ProviderCallError as exc:
                    reason = "local_structured_output_probe_failed:" + exc.category
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
                "local_healthcheck_failed:" + _health_failure_reason(exc),
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
        self._circuit_check(request_metadata=request_metadata)
        started = time.perf_counter()
        errors: list[str] = []
        transport_retries = 0
        schema_retries = 0
        admission_retries = 0
        unsupported: set[str] = set()
        last_preflight: StructuredSchemaPreflight | None = None
        # The absolute request deadline is owned by the caller and is never
        # restarted here.  Local transports are not exempt from it: a loopback
        # server that blocks on a human review can outlive the MT5 terminal
        # window just as easily as a slow remote API, and a result that arrives
        # after the deadline can never become authoritative.
        deadline = request_metadata.get("deadline")
        configured_timeout = float(request_metadata.get("timeout_sec") or self.timeout_sec)
        request_id = str(request_metadata.get("request_id") or "")
        if deadline is not None:
            self._log(
                "[provider_deadline]"
                f" request_id={request_id}"
                + deadline.as_log_fields()
                + f" configured_provider_timeout_ms={int(configured_timeout * 1000)}"
                f" deadline_contract_version={DEADLINE_CONTRACT_VERSION}"
            )

        def _deadline_stop(stage: str, attempt_number: int, extra: str = "") -> ProviderCallError:
            self._log(
                "[provider_deadline_exceeded]"
                f" request_id={request_id}"
                f" provider={self.provider_id}"
                f" stage={stage}"
                f" attempt={attempt_number}"
                f" remaining_ms={deadline.remaining_ms()}"
                f" min_attempt_ms={deadline.policy.min_attempt_ms}"
                + extra
                + f" late_result_action={LATE_RESULT_QUARANTINE}"
            )
            return ProviderCallError(
                "PROVIDER_DEADLINE_EXCEEDED",
                "insufficient_remaining_budget_for_provider_attempt:"
                f"remaining_ms={deadline.remaining_ms()}",
                retryable=False,
                schema_name=response_schema.__name__,
                schema_fingerprint=(
                    last_preflight.schema_fingerprint if last_preflight is not None else ""
                ),
            )

        # Fail before queueing on the single-slot transport semaphore when the
        # budget is already spent; waiting for a slot we can never use only
        # delays the fail-closed answer MT5 is waiting for.
        if deadline is not None and not deadline.can_start_attempt():
            self._record_failure()
            raise _deadline_stop(STAGE_CONNECT, 0, " queued=false")
        with self._semaphore:
            for model_index, model in enumerate(self._models_for_role(role)):
                preflight, circuit_key = self._prepare_schema(
                    response_schema=response_schema,
                    model=model,
                    request_metadata=request_metadata,
                )
                last_preflight = preflight
                client = self._client_instance()
                # ``attempt`` counts only what is charged to ``max_retries``.
                # An admission retry deliberately does NOT consume it: the two
                # budgets are separate by contract -- one covers failures the
                # transport may repeat, the other covers refusals proven never
                # to have been admitted.  Merging them would let a rate limit
                # eat the schema-repair budget.
                attempt = 0
                while attempt <= self.max_retries:
                    kwargs: dict[str, Any] = {}
                    # Serialized transports queue: time spent waiting for the
                    # semaphore is deadline time already spent.  Re-check here so
                    # a queued role call cannot start a request it cannot finish.
                    if deadline is not None and not deadline.can_start_attempt():
                        self._record_failure()
                        raise _deadline_stop(STAGE_CONNECT, attempt, " queued=true")
                    try:
                        effective_timeout = configured_timeout
                        if deadline is not None:
                            effective_timeout = deadline.provider_timeout_sec(configured_timeout)
                        exchange = build_provider_exchange_contract(
                            role=role,
                            system_prompt=system_prompt,
                            evidence=fitted_evidence,
                            preflight=preflight,
                            request_metadata=request_metadata,
                            default_max_output_tokens=self.max_output_tokens,
                            effective_timeout_sec=effective_timeout,
                        )
                        kwargs = {
                            "model": model,
                            "messages": [
                                {"role": "system", "content": exchange.system_prompt},
                                {
                                    "role": "user",
                                    "content": exchange.evidence_json,
                                },
                            ],
                        "max_tokens": self._wire_max_tokens(exchange.max_output_tokens),
                        }
                        if deadline is not None:
                            # The browser bridge must stop work when the owning
                            # MT5/Python request expires, not when its own fresh
                            # relative timer happens to end. Convert the
                            # monotonic remaining budget to a wall-clock epoch
                            # only at the HTTP boundary.
                            # Let the bridge expire a hung browser turn shortly
                            # before the SDK timeout, leaving time for its 504 to
                            # reach Python and for Python to write MT5's fail-
                            # closed response.
                            transport_budget_ms = min(
                                max(0, deadline.remaining_ms()),
                                max(1000, int(effective_timeout * 1000.0)),
                            )
                            bridge_budget_ms = max(1000, transport_budget_ms - 2000)
                            deadline_epoch_ms = int(time.time() * 1000.0 + bridge_budget_ms)
                            kwargs["extra_headers"] = {
                                "X-PO3-Deadline-Epoch-Ms": str(deadline_epoch_ms),
                                "X-PO3-Request-Id": request_id,
                                "X-PO3-Deadline-Contract": DEADLINE_CONTRACT_VERSION,
                            }
                        if self.temperature is not None:
                            kwargs["temperature"] = self.temperature
                        if self.top_p is not None:
                            kwargs["top_p"] = self.top_p
                        if self.seed is not None:
                            kwargs["seed"] = self.seed
                        extra_body = self._wire_extra_body()
                        if extra_body:
                            kwargs["extra_body"] = extra_body
                        if self.require_json_schema:
                            kwargs["response_format"] = {
                                "type": "json_schema",
                                "json_schema": {
                                    "name": exchange.schema_name,
                                    "strict": True,
                                    "schema": exchange.schema,
                                },
                            }
                        # The SDK receives the *remaining* budget, never a fresh
                        # relative timer.  Without this, a configured 180s local
                        # timeout outlived a 165s Python response deadline and
                        # left no margin to write an authoritative response.
                        call_client = client
                        with_options = getattr(client, "with_options", None)
                        if callable(with_options):
                            try:
                                call_client = with_options(
                                    timeout=effective_timeout, max_retries=0
                                )
                            except TypeError:
                                call_client = with_options(timeout=effective_timeout)
                        self._log(
                            "[provider_exchange_contract]"
                            f" request_id={exchange.request_id}"
                            f" request_identity_hash={exchange.request_identity_hash[:16]}"
                            f" version={exchange.version}"
                            f" role={exchange.role}"
                            f" schema_fingerprint={exchange.schema_fingerprint[:16]}"
                            f" exchange_hash={exchange.contract_hash[:16]}"
                        )
                        self._log(
                            "[provider_call_started]"
                            f" request_id={request_metadata.get('request_id', '')}"
                            f" request_identity_hash={str(request_metadata.get('request_identity_hash') or '')[:16]}"
                            f" provider={self.provider_id} model={model} role={role}"
                            f" schema={preflight.schema_name}"
                            f" schema_fingerprint={preflight.schema_fingerprint[:16]}"
                            f" transport_retry={attempt}"
                            f" schema_repair={schema_retries}"
                            f" sdk_timeout={effective_timeout:.3f}"
                            " sdk_max_retries=0"
                            f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                        )
                        response = call_client.chat.completions.create(**kwargs)
                        actual_model = _actual_model(response, model)
                        if not self._response_model_allowed(model, actual_model):
                            self._record_failure()
                            self._log(
                                "[provider_model_identity]"
                                f" request_id={request_id}"
                                f" provider={self.provider_id}"
                                f" requested_model={model}"
                                f" actual_model={actual_model}"
                                " allowed=false action=fail_closed"
                            )
                            raise ProviderCallError(
                                "PROVIDER_MODEL_IDENTITY_MISMATCH",
                                f"requested={model};actual={actual_model}",
                                retryable=False,
                                configuration_block=True,
                                schema_name=preflight.schema_name,
                                schema_fingerprint=preflight.schema_fingerprint,
                            )
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
                            f" model={actual_model} role={role}"
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
                            actual_model,
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
                            exchange.contract_hash,
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
                            attempt += 1
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
                        attempt += 1
                        continue
                    except ProviderCallError:
                        raise
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
                            attempt += 1
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
                        # An admission refusal is the one failure class proven
                        # never to have reached the model, so it is the only one
                        # a paid transport may resubmit.  Retried with the
                        # provider's own Retry-After, or exponential backoff with
                        # request-derived jitter -- the immediate retry this loop
                        # used to perform is what re-triggers a rate limit.
                        plan = self._admission_wait_plan(
                            exc,
                            attempts_used=admission_retries,
                            deadline=deadline,
                            request_id=request_id,
                        )
                        if plan is not None:
                            wait_sec, retry_after_present, affordable = plan
                            if affordable:
                                admission_retries += 1
                                self._log(
                                    "[provider_admission_retry]"
                                    f" request_id={request_id}"
                                    f" provider={self.provider_id} model={model}"
                                    f" attempt={admission_retries}/{self.admission_max_retries}"
                                    f" http_status={failure.status_code or 0}"
                                    f" error_category={failure.category}"
                                    " admitted=false resubmission_safe=true"
                                    f" retry_after_header={str(retry_after_present).lower()}"
                                    f" wait_sec={wait_sec:.3f}"
                                    f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                                )
                                errors.append(f"{model}:{failure.category}:{failure}")
                                if wait_sec > 0:
                                    time.sleep(wait_sec)
                                continue
                            self._log(
                                "[provider_admission_retry]"
                                f" request_id={request_id}"
                                f" provider={self.provider_id} model={model}"
                                f" attempt={admission_retries + 1}/{self.admission_max_retries}"
                                f" http_status={failure.status_code or 0}"
                                f" wait_sec={wait_sec:.3f}"
                                f" remaining_ms={deadline.remaining_ms() if deadline is not None else -1}"
                                f" min_attempt_ms={deadline.policy.min_attempt_ms if deadline is not None else -1}"
                                " action=skipped_insufficient_budget"
                            )
                        transport_retries += 1
                        errors.append(f"{model}:{failure.category}:{failure}")
                        if deadline is not None and not deadline.can_start_attempt():
                            # The remaining budget cannot safely complete another
                            # attempt, so retries and model fallback stop here
                            # regardless of what the configuration would allow.
                            self._log(
                                "[provider_deadline_exceeded]"
                                f" request_id={request_id}"
                                f" provider={self.provider_id}"
                                f" stage={STAGE_RESPONSE_WAIT}"
                                f" attempt={attempt}"
                                f" remaining_ms={deadline.remaining_ms()}"
                                f" error_category={failure.category}"
                                f" late_result_action={LATE_RESULT_QUARANTINE}"
                            )
                            self._record_failure()
                            raise ProviderCallError(
                                "PROVIDER_DEADLINE_EXCEEDED",
                                "local_provider_deadline_exceeded:" + " | ".join(errors),
                                retryable=False,
                                schema_name=preflight.schema_name,
                                schema_fingerprint=preflight.schema_fingerprint,
                                repair_attempted=schema_retries > 0,
                                repair_result="failed" if schema_retries else "not_attempted",
                            ) from exc
                        # Anything reaching here is either not an admission
                        # refusal, or one whose separate budget is spent.  A
                        # paid transport must not resubmit it: an ambiguous
                        # failure may mean the call WAS admitted, where a
                        # resubmission produces a second billed call and a late
                        # result racing the first.
                        if not self.resubmit_ambiguous_transport_failures:
                            self._log(
                                "[provider_ambiguous_failure_not_resubmitted]"
                                f" request_id={request_id}"
                                f" provider={self.provider_id} model={model}"
                                f" http_status={failure.status_code or 0}"
                                f" error_category={failure.category}"
                                " admitted=unknown resubmission_safe=false"
                            )
                            break
                        if not failure.retryable or attempt >= self.max_retries:
                            break
                        attempt += 1
                        continue
        self._record_failure()
        raise ProviderCallError(
            _terminal_failure_category(errors),
            "local_provider_models_failed:" + " | ".join(errors),
            retryable=False,
            schema_name=response_schema.__name__,
            # ``last_preflight`` stays None when no model is configured for the
            # role.  Reporting an empty fingerprint is a correct fail-closed
            # answer; an UnboundLocalError here used to mask a configuration
            # defect as an unrelated Python crash.
            schema_fingerprint=(
                last_preflight.schema_fingerprint if last_preflight is not None else ""
            ),
            repair_attempted=any(":schema:" in error for error in errors),
            repair_result="failed",
        )


class OpenRouterProvider(LocalOpenAICompatibleProvider):
    """OpenRouter chat-completions transport (GLM and every other routed model).

    OpenRouter speaks the same OpenAI chat-completions dialect as the local
    server family, including ``response_format`` with a strict ``json_schema``,
    so the whole request/retry/deadline loop is inherited rather than copied.
    Only three things genuinely differ and each is confined to one hook:

    ``_wire_extra_body``
        OpenRouter owns reasoning through its own ``reasoning`` object, not
        through the vLLM chat-template keyword, and it accepts a ``provider``
        routing block that can *require* an endpoint which actually enforces
        structured outputs instead of treating the schema as a hint.

    ``_wire_max_tokens``
        ``max_tokens`` on this transport bounds reasoning tokens *and* content
        tokens together.  A reasoning model handed a content-sized budget spends
        all of it thinking and returns ``finish_reason=length`` with empty
        content, so the reserve is added here rather than by widening the
        caller's schema-derived budget for every transport.

    ``_client_default_headers``
        Optional OpenRouter attribution headers.

    The endpoint is remote by construction, so it is deliberately NOT subject to
    the loopback contract that governs the local provider: it is a separate
    provider mode with its own credential, and it never reads the OpenAI secret.
    That same fact is why it overrides the inherited retry posture below.
    """

    # A billed upstream call.  The local transport may repeat an ambiguous
    # failure because a loopback server costs nothing and produces no late
    # billed result; here a resubmission after a timeout or a 5xx can be a
    # second charge racing a first call that was in fact admitted.
    resubmit_ambiguous_transport_failures = False

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
        temperature: float | None,
        top_p: float | None,
        seed: int | None,
        enable_thinking: bool,
        reasoning_effort: str,
        reasoning_token_reserve: int,
        require_json_schema: bool,
        require_structured_provider: bool,
        allowed_providers: Sequence[str],
        parallelism: int,
        context_budget_tokens: int,
        circuit_failure_threshold: int,
        circuit_cooldown_sec: float,
        log: Callable[[str], None],
        app_url: str = "",
        app_title: str = "",
        admission_retry_enable: bool = True,
        admission_max_retries: int = 3,
        admission_backoff_initial_sec: float = 2.0,
        admission_backoff_max_sec: float = 30.0,
        client_factory: Callable[..., Any] | None = None,
        health_fetcher: Callable[[str, Mapping[str, str], float], Any] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            analyst_model=analyst_model,
            critic_model=critic_model,
            adjudicator_model=adjudicator_model,
            fallback_models=fallback_models,
            healthcheck_path=healthcheck_path or "/models",
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            enable_thinking=enable_thinking,
            require_json_schema=require_json_schema,
            parallelism=parallelism,
            context_budget_tokens=context_budget_tokens,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_cooldown_sec=circuit_cooldown_sec,
            log=log,
            client_factory=client_factory,
            health_fetcher=health_fetcher,
            provider_mode=PROVIDER_MODE_OPENROUTER,
            provider_id="openrouter_api",
            max_retries_ceiling=3,
        )
        self.reasoning_effort = str(reasoning_effort or "").strip().lower()
        self.reasoning_token_reserve = max(0, int(reasoning_token_reserve))
        self.require_structured_provider = bool(require_structured_provider)
        self.allowed_providers = tuple(
            str(name).strip() for name in allowed_providers if str(name).strip()
        )
        self.app_url = str(app_url or "").strip()
        self.app_title = str(app_title or "").strip()
        # OpenRouter is a PAID remote endpoint reached through the local
        # transport loop, so it inherits neither the loopback assumption nor the
        # remote transport's admission handling.  Both halves are corrected
        # here: ambiguous failures are never resubmitted (a second submission
        # can be a second billed upstream call racing a late first result), and
        # a proven admission refusal gets the same Retry-After / jittered
        # backoff budget the remote transport uses.  ``max_retries`` keeps its
        # configured value: it still governs schema repair.
        self.admission_retry_enable = bool(admission_retry_enable)
        self.admission_max_retries = max(0, int(admission_max_retries))
        self.admission_backoff_initial_sec = max(0.0, float(admission_backoff_initial_sec))
        self.admission_backoff_max_sec = max(
            self.admission_backoff_initial_sec, float(admission_backoff_max_sec)
        )

    def _generation_settings(self, role: str) -> dict[str, Any]:
        settings = super()._generation_settings(role)
        # Deliberately NOT extended with the admission-retry settings: this
        # mapping is hashed into ``generation_settings_hash``, which is decision
        # identity.  A transport retry policy does not change what the model was
        # asked, and folding it in would move the identity of every request.
        settings["reasoning_effort"] = self.reasoning_effort
        settings["reasoning_token_reserve"] = self.reasoning_token_reserve
        settings["require_structured_provider"] = self.require_structured_provider
        settings["allowed_providers"] = list(self.allowed_providers)
        return settings

    def _client_default_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.app_url:
            headers["HTTP-Referer"] = self.app_url
        if self.app_title:
            headers["X-Title"] = self.app_title
        return headers

    def _response_model_allowed(self, requested_model: str, actual_model: str) -> bool:
        return model_response_matches_request(requested_model, actual_model)

    def _wire_extra_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.enable_thinking:
            reasoning: dict[str, Any] = {"enabled": True}
            if self.reasoning_effort in {"minimal", "low", "medium", "high", "xhigh"}:
                reasoning["effort"] = self.reasoning_effort
            body["reasoning"] = reasoning
        else:
            # Explicit, not omitted.  A routed reasoning model reasons by
            # default, and the default is what returned an empty content field
            # against a schema-sized budget.
            body["reasoning"] = {"enabled": False}
        routing: dict[str, Any] = {}
        if self.require_structured_provider:
            # OpenRouter decides structured-output support per endpoint, not per
            # model, and silently routes to an endpoint that treats the schema
            # as a hint unless the requirement is stated.  Without this a valid
            # schema can come back unenforced, which is a silent authority
            # change rather than a visible failure.
            routing["require_parameters"] = True
        if self.allowed_providers:
            routing["order"] = list(self.allowed_providers)
            routing["allow_fallbacks"] = False
        if routing:
            body["provider"] = routing
        return body

    def _wire_max_tokens(self, requested: int) -> int:
        if not self.enable_thinking:
            return int(requested)
        return int(requested) + self.reasoning_token_reserve


# ---------------------------------------------------------------------------
# OpenCode Go transports.
#
# One selection, three legs, and exactly one strict validation contract shared
# with every other transport in this file:
#
#   ``OpenCodeResponsesProvider``  Muse Spark 1.3 Contributor, /zen/go/v1/responses
#   ``OpenCodeMessagesProvider``   qwen3.8-flash, /zen/go/v1/messages (Anthropic dialect)
#   ``OpenCodeRoutedProvider``     deterministic routing + one Luna fallback
#
# Neither OpenCode leg re-implements the request loop, the deadline contract,
# the admission-retry rules, the circuit breaker, or the structured validation.
# The Responses leg subclasses the official transport and shapes two keys; the
# Messages leg reuses the chat-completions loop verbatim behind a client adapter
# that speaks the Anthropic-compatible wire dialect.  That adapter is the only
# place in the codebase that knows ``responses`` and ``messages`` differ.
# ---------------------------------------------------------------------------


class OpenCodeTransportError(RuntimeError):
    """HTTP failure from an OpenCode endpoint, shaped for the shared classifiers.

    ``_status_code``, ``_classify_transport_exception``, ``_admission_rejected``
    and ``_retry_after_sec`` all read an exception through duck typing
    (``status_code``, ``body``, ``response.headers``).  Carrying those three
    attributes is what lets an OpenCode 429 reuse the admission-retry rules
    rather than needing a second, divergent definition of "never admitted".
    """

    class _Response:
        def __init__(self, headers: Mapping[str, str]) -> None:
            self.headers = dict(headers)

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.status = status_code
        self.body = body if isinstance(body, Mapping) else None
        self.response = self._Response(headers or {})


# Measured against the live endpoint on 2026-09-09, both dialects:
#
#   * A request whose ``User-Agent`` is urllib's default (``Python-urllib/3.11``)
#     is refused by the edge with HTTP 403 / ``error code: 1010`` before it ever
#     reaches OpenCode.  Every other agent tried -- including ``curl``, ``node``
#     and ``openai-python`` -- was accepted, so this is an agent *denylist*, not
#     an allowlist, and the SDK's own agent needs no override.  Only the raw
#     urllib transport in this file does.
#   * A request with no ``x-opencode-session`` header is refused with HTTP 400
#     ``MissingSessionID`` on BOTH ``/responses`` and ``/messages``.  Any opaque
#     value is accepted; the header exists so the provider can route a
#     conversation consistently.  Deriving it from the request id is therefore
#     free and strictly better than a random one: the same request replays to the
#     same session, and two concurrent workers never share one.
OPENCODE_SESSION_HEADER = "x-opencode-session"
OPENCODE_DEFAULT_USER_AGENT = "opencode/1.0.0"
_OPENCODE_SESSION_FALLBACK = "po3-aigate"


def _opencode_session_id(request_id: str) -> str:
    """Header-safe, deterministic session id for one request."""

    token = "".join(
        ch if (ch.isalnum() or ch in "-_.") else "-" for ch in str(request_id or "")
    ).strip("-")
    if not token:
        return _OPENCODE_SESSION_FALLBACK
    return ("po3-" + token)[:120]


class _OpenCodeMessagesClient:
    """OpenAI chat-completions facade over the Anthropic-compatible dialect.

    The chat-completions request loop in ``LocalOpenAICompatibleProvider`` is
    the transport contract this project has already hardened: absolute deadline,
    separated admission/schema budgets, model-identity binding, strict JSON
    extraction.  Re-implementing it for a second wire dialect would duplicate
    exactly the business logic that must not be duplicated, so instead this
    object is injected through the existing ``client_factory`` seam and performs
    a pure translation in both directions:

    request   ``messages`` + ``response_format(json_schema)``
              -> ``system`` + ``messages`` + a SINGLE tool whose ``input_schema``
                 IS the strict schema.  Declaring exactly one tool, and saying in
                 the system text that the answer must be delivered through it, is
                 the strongest structural guarantee this endpoint actually
                 accepts -- see ``tool_choice`` below.

    response  ``content[].tool_use.input`` -> ``choices[0].message.content`` as
              a JSON string, which the caller then parses and validates through
              the identical ``_strict_json_object`` + schema path used for every
              other provider.  Nothing is trusted because it came back from a
              tool call; the same validator still has the last word.  ``thinking``
              blocks are never content: they are reasoning, not the answer.

    A tool-less answer degrades to its concatenated ``text`` blocks rather than
    being silently accepted: if that text is not a valid instance of the schema
    the shared validator rejects it, and the routed provider falls back.

    ``tool_choice`` IS NOT SENT, and that is measured, not an oversight.  This
    endpoint answers any request carrying ``tool_choice`` -- ``{"type":"tool"}``
    and ``{"type":"any"}`` alike -- with HTTP 400 and an opaque ``{"model": ...}``
    body, i.e. sending it makes every Qwen call fail rather than making it
    strict.  With the key omitted the model chose the declared tool in 3 of 3
    live runs against a prompt that explicitly invited prose, each time with a
    schema-conforming ``input``.  So the forcing is contractual rather than
    parametric, and it is backstopped where it matters: an answer that is not a
    valid instance of the schema is rejected by the shared validator and routed
    to Luna, exactly like any other unusable response.
    """

    _KNOWN_KEYS = frozenset(
        {
            "model",
            "messages",
            "max_tokens",
            "temperature",
            "top_p",
            "extra_body",
            "extra_headers",
            "response_format",
        }
    )

    class _Completions:
        def __init__(self, client: "_OpenCodeMessagesClient") -> None:
            self._client = client

        def create(self, **kwargs: Any) -> dict[str, Any]:
            return self._client._create(**kwargs)

    class _Chat:
        def __init__(self, client: "_OpenCodeMessagesClient") -> None:
            self.completions = _OpenCodeMessagesClient._Completions(client)

    def __init__(
        self,
        *,
        api_key: str,
        timeout: float,
        base_url: str = "",
        max_retries: int = 0,
        default_headers: Mapping[str, str] | None = None,
        anthropic_version: str = "2023-06-01",
        user_agent: str = OPENCODE_DEFAULT_USER_AGENT,
        transport: Callable[[str, dict[str, Any], Mapping[str, str], float], Any] | None = None,
    ) -> None:
        self._api_key = str(api_key or "")
        self.timeout = float(timeout)
        self.base_url = str(base_url or "").rstrip("/")
        self._default_headers = dict(default_headers or {})
        self._anthropic_version = str(anthropic_version or "2023-06-01")
        self._user_agent = str(user_agent or OPENCODE_DEFAULT_USER_AGENT)
        self._transport = transport
        self.chat = self._Chat(self)

    def with_options(self, *, timeout: float | None = None, max_retries: int | None = None) -> "_OpenCodeMessagesClient":
        clone = _OpenCodeMessagesClient(
            api_key=self._api_key,
            timeout=self.timeout if timeout is None else float(timeout),
            base_url=self.base_url,
            default_headers=self._default_headers,
            anthropic_version=self._anthropic_version,
            user_agent=self._user_agent,
            transport=self._transport,
        )
        return clone

    # ---- request translation -------------------------------------------

    @staticmethod
    def _split_system(messages: Sequence[Mapping[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        turns: list[dict[str, Any]] = []
        for message in messages or ():
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content")
            text = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
            if role == "system":
                system_parts.append(text)
                continue
            turns.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": [{"type": "text", "text": text}],
                }
            )
        return "\n\n".join(part for part in system_parts if part), turns

    def _build_body(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(kwargs) - self._KNOWN_KEYS)
        if unknown:
            # Never send a key this dialect has no representation for, and never
            # drop one silently either: an unrepresentable parameter is a
            # configuration defect and must say so before any HTTP request.
            raise OpenCodeTransportError(
                "opencode_messages_unsupported_request_parameter:" + ",".join(unknown)
            )
        system_text, turns = self._split_system(kwargs.get("messages") or ())
        body: dict[str, Any] = {
            "model": str(kwargs.get("model") or ""),
            "max_tokens": max(1, int(kwargs.get("max_tokens") or 1)),
            "messages": turns,
        }
        if system_text:
            body["system"] = system_text
        for optional in ("temperature", "top_p"):
            if kwargs.get(optional) is not None:
                body[optional] = kwargs[optional]
        response_format = kwargs.get("response_format")
        if isinstance(response_format, Mapping):
            json_schema = response_format.get("json_schema")
            if not isinstance(json_schema, Mapping) or not isinstance(
                json_schema.get("schema"), Mapping
            ):
                raise OpenCodeTransportError(
                    "opencode_messages_response_format_not_translatable"
                )
            tool_name = str(json_schema.get("name") or "structured_response")
            body["tools"] = [
                {
                    "name": tool_name,
                    "description": (
                        "Return the decision as this tool's input object. "
                        "Every field is required and the schema is strict."
                    ),
                    "input_schema": dict(json_schema["schema"]),
                }
            ]
            # No ``tool_choice``: this endpoint rejects the key outright (see the
            # class docstring).  The obligation is stated in the system text
            # instead, and enforced downstream by the same strict validator that
            # protects every other transport.
            directive = (
                "You MUST deliver your entire answer by calling the "
                f"`{tool_name}` tool exactly once. Do not answer in prose."
            )
            body["system"] = (
                (body["system"] + "\n\n" + directive) if body.get("system") else directive
            )
        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, Mapping):
            body.update(dict(extra_body))
        return body

    def _headers(self, kwargs: Mapping[str, Any]) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            # Both are mandatory at this endpoint; see the constants above for
            # the measured failures that prove it.  The agent must not be
            # urllib's default, and the request must name a session.
            "user-agent": self._user_agent,
            # OpenCode Go issues one key for both of its dialects.  The bearer
            # header is what the /responses leg uses; ``x-api-key`` is the
            # Anthropic-dialect convention.  Both name the same credential and
            # neither is ever logged.
            "authorization": "Bearer " + self._api_key,
            "x-api-key": self._api_key,
            "anthropic-version": self._anthropic_version,
        }
        headers.update(self._default_headers)
        extra = kwargs.get("extra_headers")
        if isinstance(extra, Mapping):
            headers.update({str(k): str(v) for k, v in extra.items()})
        # Derived last, from the request identity the transport already carries,
        # so one request always names one session and a replay names the same
        # one.  An explicitly supplied session header still wins.
        if not headers.get(OPENCODE_SESSION_HEADER):
            headers[OPENCODE_SESSION_HEADER] = _opencode_session_id(
                headers.get("X-PO3-Request-Id") or ""
            )
        return headers

    # ---- response translation ------------------------------------------

    @staticmethod
    def _content_to_text(payload: Mapping[str, Any]) -> tuple[str, str]:
        """``(content_text, finish_reason)`` from an Anthropic-shaped response."""

        blocks = payload.get("content")
        finish_reason = str(payload.get("stop_reason") or "")
        if isinstance(blocks, Sequence) and not isinstance(blocks, (str, bytes)):
            for block in blocks:
                if not isinstance(block, Mapping):
                    continue
                if str(block.get("type") or "") == "tool_use":
                    tool_input = block.get("input")
                    if isinstance(tool_input, Mapping):
                        return json.dumps(dict(tool_input), sort_keys=False), finish_reason
            texts = [
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, Mapping) and str(block.get("type") or "") == "text"
            ]
            joined = "".join(texts)
            if joined.strip():
                return joined, finish_reason
        return "", finish_reason

    def _create(self, **kwargs: Any) -> dict[str, Any]:
        body = self._build_body(kwargs)
        headers = self._headers(kwargs)
        url = self.base_url + "/messages"
        payload = self._post(url, body, headers, self.timeout)
        if not isinstance(payload, Mapping):
            raise OpenCodeTransportError("opencode_messages_response_root_not_object")
        content, finish_reason = self._content_to_text(payload)
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total = None
        try:
            if input_tokens is not None and output_tokens is not None:
                total = int(input_tokens) + int(output_tokens)
        except (TypeError, ValueError):
            total = None
        return {
            "id": str(payload.get("id") or ""),
            "model": str(payload.get("model") or body.get("model") or ""),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total,
            },
            "opencode_stop_reason": finish_reason,
        }

    def _post(
        self,
        url: str,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> Any:
        if self._transport is not None:
            return self._transport(url, dict(body), dict(headers), float(timeout))
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                raw = ""
            parsed: Any = None
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = None
            raise OpenCodeTransportError(
                f"opencode_messages_http_{int(getattr(exc, 'code', 0) or 0)}:{raw[:500]}",
                status_code=int(getattr(exc, "code", 0) or 0),
                body=parsed,
                headers=dict(getattr(exc, "headers", {}) or {}),
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                raise OpenCodeTransportError("opencode_messages_timeout:read timed out") from exc
            raise OpenCodeTransportError(
                "opencode_messages_connection error:" + str(reason or exc)
            ) from exc
        except TimeoutError as exc:
            raise OpenCodeTransportError("opencode_messages_timeout:read timed out") from exc


class OpenCodeResponsesProvider(RemoteAPIProvider):
    """Muse Spark 1.3 Contributor over the OpenCode Go ``/responses`` endpoint.

    Subclasses the official Responses transport rather than copying it, so the
    deadline contract, admission-retry budget, circuit breaker, strict schema
    preflight and structured validation are the identical code paths that
    already protect api.openai.com traffic.

    Two things genuinely differ and both are confined to one hook:

    * ``service_tier`` and the prompt-cache handles are OpenAI *account*
      features. OpenCode publishes no such parameters, and this transport routes
      with a strict schema, so sending them risks the whole request being
      rejected for an unknown key.  ``_wire_responses_kwargs`` removes them.
    * A malformed answer is not re-asked of the same model.  ``Luna`` is a
      better and cheaper answer to bad JSON than a second Muse call, so
      ``schema_repair_attempts`` is 0 and the routed provider falls back.
    """

    resubmit_ambiguous_transport_failures = False
    schema_repair_attempts = 0

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_sec: float,
        max_output_tokens: int,
        reasoning_token_reserve: int = 24000,
        circuit_failure_threshold: int,
        circuit_cooldown_sec: float,
        log: Callable[[str], None],
        admission_retry_enable: bool = True,
        admission_max_retries: int = 3,
        admission_backoff_initial_sec: float = 2.0,
        admission_backoff_max_sec: float = 30.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            primary_model=model,
            # Model fallback inside one provider is deliberately empty: the
            # fallback for this leg is a different provider entirely (Luna), and
            # that decision belongs to OpenCodeRoutedProvider, not here.
            fallback_models=(),
            analytics_model=model,
            reasoning_effort=reasoning_effort,
            timeout_sec=timeout_sec,
            max_output_tokens=max_output_tokens,
            prompt_cache_enable=False,
            prompt_cache_key="",
            prompt_cache_retention="",
            service_tier="",
            flex_unavailable_retry_enable=False,
            flex_unavailable_max_retries=0,
            flex_unavailable_cooldown_sec=0.0,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_cooldown_sec=circuit_cooldown_sec,
            log=log,
            admission_retry_enable=admission_retry_enable,
            admission_max_retries=admission_max_retries,
            admission_backoff_initial_sec=admission_backoff_initial_sec,
            admission_backoff_max_sec=admission_backoff_max_sec,
            client_factory=client_factory,
        )
        self.provider_mode = PROVIDER_MODE_OPENCODE
        self.provider_id = "opencode_go_responses"
        self.reasoning_token_reserve = max(0, int(reasoning_token_reserve))

    def _wire_responses_kwargs(
        self,
        kwargs: dict[str, Any],
        *,
        request_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        shaped = dict(kwargs)
        # Unconditional, not "only when self.service_tier is empty": the gate
        # supplies "auto" in request_metadata for every non-OpenAI transport, and
        # the base loop treats that as a value to send.
        shaped.pop("service_tier", None)
        shaped.pop("prompt_cache_key", None)
        extra_body = shaped.get("extra_body")
        if isinstance(extra_body, Mapping):
            trimmed = {
                key: value
                for key, value in extra_body.items()
                if key != "prompt_cache_retention"
            }
            if trimmed:
                shaped["extra_body"] = trimmed
            else:
                shaped.pop("extra_body", None)
        # ``truncation`` is the one Responses parameter whose OpenAI default is
        # not merely ignored here but REJECTED: measured 2026-09-09, this
        # endpoint answers ``auto`` with HTTP 400 ``value `auto` is not
        # supported. Only `disabled` is supported``, which failed every single
        # Muse call.  Restated rather than dropped, because ``disabled`` is also
        # the value this project wants: ``auto`` lets the upstream silently drop
        # evidence out of the middle of an over-long context and answer anyway,
        # which is a decision made on evidence Python never agreed to omit.
        # With it disabled an over-long request fails loudly instead, and the
        # context-budget fitter -- not the provider -- stays the one thing that
        # decides what evidence a decision is allowed to rest on.
        if "truncation" in shaped:
            shaped["truncation"] = "disabled"
        # ``max_output_tokens`` bounds reasoning tokens AND content together on
        # this API, so a schema-derived budget is not a content budget -- it is
        # a budget the reasoning spends first.  Measured 2026-09-09 on a real
        # archived live request: Muse at effort=high returned
        # ``status=incomplete incomplete_reason=max_output_tokens`` with an empty
        # message on the analyst role -- every time -- while the smaller critic
        # and adjudicator roles completed normally on the same budget.  This is
        # the same trap this repo already documented on OpenRouter
        # (OPENROUTER_REASONING_TOKEN_RESERVE), so it takes the same shape here:
        # a reserve added ON TOP of the caller's budget rather than taken out of
        # it, so the schema still gets every token it was promised.  It is a
        # ceiling, not a spend -- an answer that reasons less is billed less.
        if self.reasoning_token_reserve > 0:
            requested = shaped.get("max_output_tokens")
            if isinstance(requested, int) and requested > 0:
                shaped["max_output_tokens"] = requested + self.reasoning_token_reserve
        # Mandatory at this endpoint: without it the request is refused with
        # HTTP 400 MissingSessionID before the model is reached.  The base loop
        # only builds ``extra_headers`` when a deadline is present, so this
        # cannot be folded into that block -- a deadline-free call needs the
        # header just as much.
        headers = dict(shaped.get("extra_headers") or {})
        if not headers.get(OPENCODE_SESSION_HEADER):
            headers[OPENCODE_SESSION_HEADER] = _opencode_session_id(
                str(request_metadata.get("request_id") or "")
            )
        shaped["extra_headers"] = headers
        return shaped


class OpenCodeMessagesProvider(LocalOpenAICompatibleProvider):
    """qwen3.8-flash over the OpenCode Go Anthropic-compatible ``/messages``.

    Reuses the chat-completions request loop unchanged; the dialect difference
    lives entirely in ``_OpenCodeMessagesClient``.  Sampling parameters default
    to omitted so no unsupported key reaches the wire, and, like the Responses
    leg, a malformed answer routes to Luna instead of being re-asked here.
    """

    resubmit_ambiguous_transport_failures = False

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float,
        max_output_tokens: int,
        context_budget_tokens: int,
        circuit_failure_threshold: int,
        circuit_cooldown_sec: float,
        log: Callable[[str], None],
        anthropic_version: str = "2023-06-01",
        enable_thinking: bool = False,
        thinking_budget_tokens: int = 0,
        parallelism: int = 3,
        admission_retry_enable: bool = True,
        admission_max_retries: int = 3,
        admission_backoff_initial_sec: float = 2.0,
        admission_backoff_max_sec: float = 30.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.anthropic_version = str(anthropic_version or "2023-06-01")
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            analyst_model=model,
            critic_model=model,
            adjudicator_model=model,
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=timeout_sec,
            # 0 repairs: a bad answer goes to Luna, it is not re-asked here.
            max_retries=0,
            max_output_tokens=max_output_tokens,
            temperature=None,
            top_p=None,
            seed=None,
            enable_thinking=bool(enable_thinking),
            require_json_schema=True,
            parallelism=parallelism,
            context_budget_tokens=context_budget_tokens,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_cooldown_sec=circuit_cooldown_sec,
            log=log,
            client_factory=client_factory or self._default_client_factory,
            provider_mode=PROVIDER_MODE_OPENCODE,
            provider_id="opencode_go_messages",
            max_retries_ceiling=0,
        )
        self.thinking_budget_tokens = max(0, int(thinking_budget_tokens))
        self.admission_retry_enable = bool(admission_retry_enable)
        self.admission_max_retries = max(0, int(admission_max_retries))
        self.admission_backoff_initial_sec = max(0.0, float(admission_backoff_initial_sec))
        self.admission_backoff_max_sec = max(
            self.admission_backoff_initial_sec, float(admission_backoff_max_sec)
        )

    def _default_client_factory(self, **kwargs: Any) -> "_OpenCodeMessagesClient":
        return _OpenCodeMessagesClient(
            api_key=str(kwargs.get("api_key") or ""),
            timeout=float(kwargs.get("timeout") or self.timeout_sec),
            base_url=str(kwargs.get("base_url") or self.base_url),
            default_headers=kwargs.get("default_headers") or {},
            anthropic_version=self.anthropic_version,
        )

    def _generation_settings(self, role: str) -> dict[str, Any]:
        settings = super()._generation_settings(role)
        settings["anthropic_version"] = self.anthropic_version
        settings["thinking_budget_tokens"] = (
            self.thinking_budget_tokens if self.enable_thinking else 0
        )
        return settings

    def _wire_extra_body(self) -> dict[str, Any]:
        # The vLLM chat-template keyword the local family sends means nothing to
        # this dialect.  Extended thinking is the dialect's own key, and it is
        # only sent when it is explicitly configured, so a deployment that has
        # not enabled it never puts an unsupported parameter on the wire.
        if self.enable_thinking and self.thinking_budget_tokens > 0:
            return {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": self.thinking_budget_tokens,
                }
            }
        return {}

    def _wire_max_tokens(self, requested: int) -> int:
        # ``max_tokens`` bounds thinking tokens and content together in this
        # dialect, exactly as on OpenRouter, so the thinking budget is added on
        # top of the caller's schema-derived budget rather than eaten out of it.
        if self.enable_thinking and self.thinking_budget_tokens > 0:
            return int(requested) + self.thinking_budget_tokens
        return int(requested)

    def healthcheck(self, *, probe_structured: bool = False) -> ProviderHealth:
        """Configuration health, not a model listing.

        The inherited implementation GETs ``/models`` and reads an OpenAI-shaped
        catalogue.  OpenCode Go publishes no such listing on this dialect, so
        inheriting it would report a correctly configured transport as unhealthy
        and -- worse -- would keep the circuit breaker permanently open, because
        breaker recovery re-runs exactly this check.  What is actually knowable
        without spending a billed call is whether the credential and model are
        configured; the real answer comes from the call itself, which falls back
        to Luna when it fails.
        """

        model = self.model_for_role("analyst")
        healthy = bool(self._api_key and model)
        health = ProviderHealth(
            healthy,
            self.provider_mode,
            self.provider_id,
            self.endpoint_class,
            model,
            healthy,
            healthy,
            "" if healthy else "opencode_api_key_or_model_missing",
        )
        self._last_health = health
        return health


class OpenCodeRoutedProvider:
    """OpenCode Go with deterministic routing and a single OpenAI Luna fallback.

    Owns *routing only*.  Every leg it dispatches to is a fully independent
    provider that performs its own strict schema validation, so this class can
    never forward an unvalidated answer: it either returns a ``ProviderResult``
    that a leg already validated, or it raises.

    Routing (``OPENCODE_CALL_DIRECTING`` off is the default):

        directing off              -> Muse, fallback Luna
        directing on, normal       -> Muse, fallback Luna
        directing on, important    -> Qwen, fallback Luna
        directing on, critical     -> Luna directly

    Importance is classified by ``opencode_routing.classify_importance``: a pure
    function of signals the request already carries.  No extra model call is
    made to decide where a request goes.

    Identity is delegated to the leg the *default* route would use, so under the
    default configuration -- directing off, every request on Muse -- the
    decision cache's provider-identity check matches exactly as it does for any
    single-transport mode.  When directing sends a request elsewhere, the result
    truthfully reports the leg that answered, and a cache row written by one leg
    is correctly not replayed under another leg's identity.
    """

    def __init__(
        self,
        *,
        muse: Any,
        qwen: Any,
        fallback: Any,
        policy: Any,
        log: Callable[[str], None],
        fallback_service_tier: str = "flex",
    ) -> None:
        self._muse = muse
        self._qwen = qwen
        self._fallback = fallback
        self._policy = policy
        self._log = log
        self._fallback_service_tier = str(fallback_service_tier or "").strip().lower()
        default_leg = self._default_leg()
        self.provider_mode = default_leg.provider_mode
        self.provider_id = default_leg.provider_id
        self.endpoint_class = default_leg.endpoint_class

    def _fallback_metadata(self, request_metadata: Mapping[str, Any]) -> dict[str, Any]:
        """Request metadata for the OpenAI leg, carrying its configured tier.

        The gate supplies ``service_tier="auto"`` for every transport that is not
        ``PROVIDER_MODE_REMOTE``, and the remote transport lets request metadata
        outrank its constructor value -- so without this the Luna fallback would
        silently run on the standard tier no matter how it was configured.  The
        rewrite is confined to the leg that owns the parameter, and the cost
        ledger stays correct on its own because ``_response_service_tier`` reads
        the tier back off the response rather than trusting the request.
        """

        metadata = dict(request_metadata)
        if self._fallback_service_tier:
            metadata["service_tier"] = self._fallback_service_tier
        return metadata

    # ---- routing --------------------------------------------------------

    def _default_leg(self) -> Any:
        """The leg an unclassified call uses; also the identity this reports."""

        return self._muse

    def _leg_for(self, importance: str) -> tuple[Any, bool]:
        """``(leg, is_opencode_leg)`` for a classified importance."""

        if importance == IMPORTANCE_CRITICAL:
            return self._fallback, False
        if importance == IMPORTANCE_IMPORTANT:
            return self._qwen, True
        if importance != IMPORTANCE_NORMAL:
            # Unreachable while the classifier can only return the three known
            # levels, and written this way so it stays unreachable: if a fourth
            # level is ever added, an unmapped request takes the STRONGEST route
            # rather than falling through to the cheapest one by accident.
            return self._fallback, False
        return self._muse, True

    def classify(
        self,
        request_metadata: Mapping[str, Any] | None,
        evidence: Mapping[str, Any] | None,
    ) -> Any:
        return classify_importance(
            policy=self._policy,
            request_metadata=request_metadata,
            evidence=evidence,
        )

    # ---- AIProvider protocol -------------------------------------------

    def healthcheck(self, *, probe_structured: bool = False) -> ProviderHealth:
        primary = self._default_leg().healthcheck(probe_structured=False)
        fallback = self._fallback.healthcheck(probe_structured=False)
        reasons = [
            f"{name}:{health.reason}"
            for name, health in (("opencode", primary), ("fallback", fallback))
            if not health.healthy and health.reason
        ]
        return ProviderHealth(
            bool(primary.healthy or fallback.healthy),
            self.provider_mode,
            self.provider_id,
            self.endpoint_class,
            primary.model_id or fallback.model_id,
            bool(primary.model_available or fallback.model_available),
            bool(primary.structured_output_available or fallback.structured_output_available),
            ";".join(reasons),
        )

    def model_for_role(self, role: str) -> str:
        return self._default_leg().model_for_role(role)

    def configured_models(self, role: str) -> tuple[str, ...]:
        return self._default_leg().configured_models(role)

    def identity(self, role: str = "analyst") -> dict[str, Any]:
        identity = dict(self._default_leg().identity(role))
        identity["opencode_routing"] = self._policy.fingerprint()
        identity["opencode_legs"] = {
            "normal": self._muse.model_for_role(role),
            "important": self._qwen.model_for_role(role),
            "critical": self._fallback.model_for_role(role),
        }
        return identity

    def generation_identity(
        self,
        role: str = "analyst",
        request_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._default_leg().generation_identity(role, request_metadata)

    def clear_configuration_circuit(self, key: str) -> None:
        for leg in (self._muse, self._qwen, self._fallback):
            clear = getattr(leg, "clear_configuration_circuit", None)
            if callable(clear):
                clear(key)

    def configuration_circuit_count(self) -> int:
        return sum(
            int(getattr(leg, "configuration_circuit_count", lambda: 0)())
            for leg in (self._muse, self._qwen, self._fallback)
        )

    @staticmethod
    def _failure_fields(exc: Exception) -> tuple[str, str]:
        """``(category, detail)`` for telemetry, precise where it is knowable."""

        if isinstance(exc, ProviderCallError):
            return exc.category, str(exc)[:200]
        return type(exc).__name__, str(exc)[:200]

    def generate_structured(
        self,
        *,
        role: str,
        system_prompt: str,
        evidence: Mapping[str, Any],
        response_schema: type,
        request_metadata: Mapping[str, Any],
    ) -> ProviderResult:
        decision = self.classify(request_metadata, evidence)
        leg, is_opencode = self._leg_for(decision.importance)
        request_id = str(request_metadata.get("request_id") or "")
        started = time.perf_counter()
        self._log(
            "[opencode_routing]"
            f" request_id={request_id}"
            f" role={role}"
            + decision.as_log_fields()
            + f" routed_provider={leg.provider_id}"
            f" routed_model={leg.model_for_role(role)}"
            f" fallback_available={str(is_opencode).lower()}"
            f" routing_policy_version={ROUTING_POLICY_VERSION}"
        )
        leg_metadata = (
            request_metadata if is_opencode else self._fallback_metadata(request_metadata)
        )
        try:
            result = leg.generate_structured(
                role=role,
                system_prompt=system_prompt,
                evidence=evidence,
                response_schema=response_schema,
                request_metadata=leg_metadata,
            )
        except Exception as exc:
            category, detail = self._failure_fields(exc)
            if not is_opencode:
                # The critical route is already Luna.  There is no second
                # fallback: a failure here is the transport's real answer and is
                # raised unchanged rather than retried against itself.
                self._log(
                    "[opencode_fallback]"
                    f" request_id={request_id}"
                    f" importance={decision.importance}"
                    f" from={leg.provider_id} action=none"
                    f" reason=no_fallback_configured_for_direct_openai_route"
                    f" error_category={category}"
                    f" latency_sec={time.perf_counter() - started:.3f}"
                )
                raise
            deadline = request_metadata.get("deadline")
            if deadline is not None and not deadline.can_start_attempt():
                # Falling back would begin an attempt the absolute deadline
                # cannot cover.  The deadline is never extended to make room for
                # a fallback, so the OpenCode failure is the terminal answer.
                self._log(
                    "[opencode_fallback]"
                    f" request_id={request_id}"
                    f" importance={decision.importance}"
                    f" from={leg.provider_id} action=skipped"
                    " reason=insufficient_remaining_budget"
                    f" error_category={category}"
                    f" remaining_ms={deadline.remaining_ms()}"
                    f" latency_sec={time.perf_counter() - started:.3f}"
                )
                raise
            self._log(
                "[opencode_fallback]"
                f" request_id={request_id}"
                f" importance={decision.importance}"
                f" from={leg.provider_id}"
                f" from_model={leg.model_for_role(role)}"
                f" to={self._fallback.provider_id}"
                f" to_model={self._fallback.model_for_role(role)}"
                " action=fallback_once"
                f" error_category={category}"
                f" fallback_reason={detail}"
                f" opencode_latency_sec={time.perf_counter() - started:.3f}"
            )
            fallback_started = time.perf_counter()
            fallback_result = self._fallback.generate_structured(
                role=role,
                system_prompt=system_prompt,
                evidence=evidence,
                response_schema=response_schema,
                request_metadata=self._fallback_metadata(request_metadata),
            )
            self._log(
                "[opencode_fallback_completed]"
                f" request_id={request_id}"
                f" provider={fallback_result.provider_id}"
                f" provider_mode={fallback_result.provider_mode}"
                f" model={fallback_result.actual_model}"
                " validation=passed"
                f" latency_sec={time.perf_counter() - fallback_started:.3f}"
            )
            return fallback_result
        self._log(
            "[opencode_call_completed]"
            f" request_id={request_id}"
            f" importance={decision.importance}"
            f" provider={result.provider_id}"
            f" provider_mode={result.provider_mode}"
            f" model={result.actual_model}"
            " validation=passed fallback_used=false"
            f" latency_sec={result.latency_sec:.3f}"
        )
        return result
