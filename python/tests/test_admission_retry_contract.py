"""Regression coverage for provider ADMISSION retries (HTTP 429/503).

The measured defect (live run 2026-09-07, 16:36-20:34):

    [provider_attempt]     attempt=1 transport_retry=0 remaining_ms=2675344
                           sdk_max_retries=0
    [provider_call_failed] error_category=PROVIDER_TRANSPORT_ERROR
                           Error code: 429 - {'error': {'message': "We're
                           currently processing too many requests please try
                           again later.", 'code': 'rate_limit_exceeded'}}
                           final_quality_tier=DEGRADED_NON_TRADING

Nine of thirty-three live decisions -- 27% -- died exactly this way, each with
roughly 44 minutes of the request budget still unspent.  ``RemoteAPIProvider``
hardcodes ``max_retries=0``, and the only retry that existed was gated on the
*flex* service tier, so a rate-limit refusal on the standard tier was terminal
on its first occurrence.  That is an infrastructure rejection on healthy
infrastructure, which the workflow contract does not accept.

The retry added here is safe for exactly the reason the flex retry is safe, and
no other: an admission refusal means the request was never accepted, so no
tokens were produced, nothing is running server-side, and nothing can complete
late.  Resubmitting still yields one authoritative provider call per request
identity.

``test_ambiguous_transport_failure_is_never_resubmitted`` and
``test_quota_exhaustion_is_not_an_admission_rejection`` are what keep that
argument honest.  Without them this file would read as permission to retry
anything.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from pathlib import Path

from ai_provider import (
    LocalOpenAICompatibleProvider,
    OpenRouterProvider,
    ProviderCallError,
    RemoteAPIProvider,
)
from provider_deadline import DeadlinePolicy, RequestDeadline
from structured_models import StrictStructuredModel

ROOT = Path(__file__).resolve().parents[1]


class _Probe(StrictStructuredModel):
    ok: bool


def _rate_limit_error(retry_after: str | None = None) -> Exception:
    """The exact shape the live gate received on 2026-09-07."""

    class _RateLimitError(Exception):
        status_code = 429
        body = {
            "error": {
                "message": (
                    "We're currently processing too many requests please try "
                    "again later."
                ),
                "type": "invalid_request_error",
                "code": "rate_limit_exceeded",
            }
        }

    exc = _RateLimitError(
        "Error code: 429 - {'error': {'message': \"We're currently processing "
        "too many requests please try again later.\", 'type': "
        "'invalid_request_error', 'code': 'rate_limit_exceeded'}}"
    )
    if retry_after is not None:
        exc.response = SimpleNamespace(headers={"retry-after": retry_after})
    return exc


def _quota_error() -> Exception:
    class _QuotaError(Exception):
        status_code = 429
        body = {
            "error": {
                "message": "You exceeded your current quota, please check your plan.",
                "code": "insufficient_quota",
            }
        }

    return _QuotaError(
        "Error code: 429 - {'error': {'code': 'insufficient_quota'}}"
    )


def _flex_capacity_error() -> Exception:
    """A refusal BOTH classifiers recognise, used to prove the budgets do not stack."""

    class _CapacityError(Exception):
        status_code = 503

    return _CapacityError(
        "Error code: 503 - service tier flex is at capacity for this model"
    )


def _timeout_error() -> Exception:
    class _TimeoutError(Exception):
        status_code = 503

    return _TimeoutError("Request timed out.")


def _server_error() -> Exception:
    class _ServerError(Exception):
        status_code = 500

    return _ServerError("Internal server error")


class _CountingClient:
    def __init__(self, errors: list[Exception]) -> None:
        self.calls = 0
        self._errors = list(errors)
        self.responses = SimpleNamespace(create=self.create)

    def with_options(self, **_options: Any) -> "_CountingClient":
        return self

    def create(self, **kwargs: Any) -> Any:
        index = self.calls
        self.calls += 1
        if index < len(self._errors):
            raise self._errors[index]
        return SimpleNamespace(
            output_text='{"ok":true}',
            model=kwargs.get("model"),
            system_fingerprint="admission-test",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def _provider(
    client: _CountingClient,
    *,
    admission_retry_enable: bool = True,
    admission_max_retries: int = 3,
    backoff_initial: float = 2.0,
    backoff_max: float = 30.0,
    service_tier: str = "auto",
    flex_retry_enable: bool = False,
    flex_max_retries: int = 0,
    log: list[str] | None = None,
) -> RemoteAPIProvider:
    sink = log if log is not None else []
    provider = RemoteAPIProvider(
        api_key="test-key",
        base_url="",
        primary_model="model-a",
        fallback_models=(),
        analytics_model="model-a",
        reasoning_effort="medium",
        timeout_sec=300.0,
        max_output_tokens=2048,
        prompt_cache_enable=False,
        prompt_cache_key="",
        prompt_cache_retention="24h",
        service_tier=service_tier,
        flex_unavailable_retry_enable=flex_retry_enable,
        flex_unavailable_max_retries=flex_max_retries,
        flex_unavailable_cooldown_sec=0.0,
        circuit_failure_threshold=100,
        circuit_cooldown_sec=60.0,
        log=sink.append,
        admission_retry_enable=admission_retry_enable,
        admission_max_retries=admission_max_retries,
        admission_backoff_initial_sec=backoff_initial,
        admission_backoff_max_sec=backoff_max,
        client_factory=lambda **_kwargs: client,
    )
    provider._client = client
    return provider


def _deadline(remaining_sec: float, min_attempt_sec: float = 30.0) -> RequestDeadline:
    policy = DeadlinePolicy.derive(
        mt5_terminal_timeout_sec=remaining_sec + 15.0,
        response_write_margin_sec=15.0,
        min_attempt_sec=min_attempt_sec,
    )
    return RequestDeadline.start("admission-test", policy)


def _call(
    provider: RemoteAPIProvider,
    *,
    deadline: RequestDeadline | None = None,
    service_tier: str = "auto",
    request_id: str = "admission-test",
) -> Any:
    metadata: dict[str, Any] = {
        "request_id": request_id,
        "service_tier": service_tier,
    }
    if deadline is not None:
        metadata["deadline"] = deadline
    return provider.generate_structured(
        role="analyst",
        system_prompt="Return JSON.",
        evidence={"probe": True},
        response_schema=_Probe,
        request_metadata=metadata,
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Assert on the requested wait without spending it."""

    slept: list[float] = []
    monkeypatch.setattr("ai_provider.time.sleep", slept.append)
    return slept


# --------------------------------------------------------------------------
# Classification -- the whole safety argument lives here
# --------------------------------------------------------------------------


def test_live_rate_limit_body_is_an_admission_rejection():
    """The exact 429 that lost nine live decisions must be recognised."""

    assert RemoteAPIProvider._admission_rejected(_rate_limit_error()) is True


def test_live_rate_limit_body_was_missed_by_the_flex_classifier():
    """Why the pre-existing flex retry could not have saved these decisions.

    ``_flex_capacity_rejected`` matches "rate limit" with a space; the live body
    carries ``rate_limit_exceeded`` with an underscore and never says
    "capacity".  This pins the gap rather than trusting that the tier gate was
    the only reason it did not fire.
    """

    assert RemoteAPIProvider._flex_capacity_rejected(_rate_limit_error()) is False


def test_quota_exhaustion_is_not_an_admission_rejection():
    """A 429 that waiting cannot clear must not consume the request budget."""

    assert RemoteAPIProvider._admission_rejected(_quota_error()) is False


def test_ambiguous_failures_are_not_admission_rejections():
    """Timeouts and generic 5xx may have been ADMITTED and still be running."""

    assert RemoteAPIProvider._admission_rejected(_timeout_error()) is False
    assert RemoteAPIProvider._admission_rejected(_server_error()) is False


# --------------------------------------------------------------------------
# Retry behaviour
# --------------------------------------------------------------------------


def test_rate_limited_request_is_retried_and_succeeds(_no_real_sleep):
    """The defect itself: attempt 1 was the only attempt."""

    client = _CountingClient([_rate_limit_error(), _rate_limit_error()])
    log: list[str] = []
    provider = _provider(client, log=log)

    result = _call(provider, deadline=_deadline(2700.0))

    assert client.calls == 3
    assert result.parsed.ok is True
    retries = [line for line in log if line.startswith("[provider_admission_retry]")]
    assert len(retries) == 2
    assert "admitted=false resubmission_safe=true" in retries[0]
    assert len(_no_real_sleep) == 2


def test_admission_retries_are_bounded(_no_real_sleep):
    client = _CountingClient([_rate_limit_error() for _ in range(10)])
    provider = _provider(client, admission_max_retries=2)

    with pytest.raises(ProviderCallError):
        _call(provider, deadline=_deadline(2700.0))

    assert client.calls == 3  # first attempt + 2 retries


def test_admission_retry_can_be_disabled(_no_real_sleep):
    client = _CountingClient([_rate_limit_error() for _ in range(10)])
    provider = _provider(client, admission_retry_enable=False)

    with pytest.raises(ProviderCallError):
        _call(provider, deadline=_deadline(2700.0))

    assert client.calls == 1
    assert _no_real_sleep == []


def test_backoff_is_exponential_deterministic_and_capped(_no_real_sleep):
    client = _CountingClient([_rate_limit_error() for _ in range(10)])
    provider = _provider(client, admission_max_retries=6, backoff_initial=2.0, backoff_max=30.0)

    with pytest.raises(ProviderCallError):
        _call(provider, deadline=_deadline(2700.0, min_attempt_sec=1.0))

    waits = list(_no_real_sleep)
    assert len(waits) == 6
    # Jitter is +0%..+25% of the base, so each wait sits in its own band and the
    # sequence still grows until the cap binds.
    for index, base in enumerate((2.0, 4.0, 8.0, 16.0, 30.0, 30.0)):
        assert base <= waits[index] <= base * 1.25

    # Deterministic per request identity: a replay sleeps identically.
    replay_client = _CountingClient([_rate_limit_error() for _ in range(10)])
    replay_log: list[float] = []
    replay = _provider(replay_client, admission_max_retries=6)
    import ai_provider

    original_sleep = ai_provider.time.sleep
    ai_provider.time.sleep = replay_log.append
    try:
        with pytest.raises(ProviderCallError):
            _call(replay, deadline=_deadline(2700.0, min_attempt_sec=1.0))
    finally:
        ai_provider.time.sleep = original_sleep
    assert replay_log == waits


def test_backoff_differs_between_request_identities(_no_real_sleep):
    """Concurrent workers must not retry in lockstep and re-trigger the limit."""

    provider = _provider(_CountingClient([]))
    waits = {
        provider._admission_backoff_sec(1, f"request-{index}") for index in range(8)
    }
    assert len(waits) > 1


def test_retry_after_header_is_honoured_verbatim(_no_real_sleep):
    client = _CountingClient([_rate_limit_error(retry_after="7")])
    log: list[str] = []
    provider = _provider(client, backoff_initial=2.0, log=log)

    result = _call(provider, deadline=_deadline(2700.0))

    assert result.parsed.ok is True
    assert _no_real_sleep == [7.0]
    assert any("retry_after_header=true" in line for line in log)


def test_retry_is_skipped_when_the_deadline_cannot_afford_the_wait(_no_real_sleep):
    """The wait is spent INSIDE the request budget, so it must fit."""

    client = _CountingClient([_rate_limit_error() for _ in range(4)])
    log: list[str] = []
    provider = _provider(client, backoff_initial=60.0, backoff_max=60.0, log=log)

    with pytest.raises(ProviderCallError):
        _call(provider, deadline=_deadline(45.0, min_attempt_sec=30.0))

    assert client.calls == 1
    assert _no_real_sleep == []
    assert any(
        "action=skipped_insufficient_budget" in line
        for line in log
        if line.startswith("[provider_admission_retry]")
    )


def test_ambiguous_transport_failure_is_never_resubmitted(_no_real_sleep):
    """One request identity must never produce two billed provider calls."""

    for error in (_timeout_error(), _server_error()):
        client = _CountingClient([error, error])
        provider = _provider(client)
        with pytest.raises(ProviderCallError):
            _call(provider, deadline=_deadline(2700.0))
        assert client.calls == 1
        assert _no_real_sleep == []


def test_quota_exhaustion_is_not_retried(_no_real_sleep):
    client = _CountingClient([_quota_error() for _ in range(4)])
    provider = _provider(client)

    with pytest.raises(ProviderCallError):
        _call(provider, deadline=_deadline(2700.0))

    assert client.calls == 1
    assert _no_real_sleep == []


def test_flex_keeps_exactly_its_own_budget(_no_real_sleep):
    """The flex retry must not silently gain a second budget on top of its own.

    Uses a body BOTH classifiers recognise -- that overlap is the only case
    where the two budgets could stack.
    """

    client = _CountingClient([_flex_capacity_error() for _ in range(10)])
    assert RemoteAPIProvider._flex_capacity_rejected(_flex_capacity_error()) is True
    assert RemoteAPIProvider._admission_rejected(_flex_capacity_error()) is True

    provider = _provider(
        client,
        service_tier="flex",
        flex_retry_enable=True,
        flex_max_retries=1,
        admission_max_retries=3,
    )

    with pytest.raises(ProviderCallError):
        _call(provider, deadline=_deadline(2700.0), service_tier="flex")

    assert client.calls == 2  # first attempt + 1 flex retry, no admission retries


def test_flex_tier_still_gets_admission_retries_for_bodies_flex_cannot_classify(
    _no_real_sleep,
):
    """The complement of the test above, and the reason the guard is narrow.

    A flex-tier request that hits the live ``rate_limit_exceeded`` body is not
    owned by the flex branch at all -- ``_flex_capacity_rejected`` returns False
    for it -- so the admission budget must still rescue the decision instead of
    the tier gate silently swallowing it.
    """

    client = _CountingClient([_rate_limit_error(), _rate_limit_error()])
    provider = _provider(
        client,
        service_tier="flex",
        flex_retry_enable=True,
        flex_max_retries=1,
        admission_max_retries=3,
    )

    result = _call(provider, deadline=_deadline(2700.0), service_tier="flex")

    assert result.parsed.ok is True
    assert client.calls == 3


def test_admission_retry_counter_is_reported_on_every_attempt(_no_real_sleep):
    client = _CountingClient([_rate_limit_error()])
    log: list[str] = []
    provider = _provider(client, log=log)

    _call(provider, deadline=_deadline(2700.0))

    attempts = [line for line in log if line.startswith("[provider_attempt]")]
    assert len(attempts) == 2
    assert "admission_retry=0" in attempts[0]
    assert "admission_retry=1" in attempts[1]


# --------------------------------------------------------------------------
# The same contract on the OpenRouter transport.
#
# OpenRouter is a PAID remote endpoint that reaches the provider through the
# LOCAL-compatible call loop, so before this it inherited neither half of the
# admission contract: a 429 was retried IMMEDIATELY (no Retry-After, no
# backoff -- the behaviour that re-triggers a rate limit) while an AMBIGUOUS
# timeout was also resubmitted, which on a billed endpoint can be a second
# charge racing a late first result.
# --------------------------------------------------------------------------


class _CountingChatClient:
    """A chat-completions client, which is the dialect OpenRouter speaks."""

    def __init__(self, errors: list[Exception]) -> None:
        self.calls = 0
        self._errors = list(errors)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **_options: Any) -> "_CountingChatClient":
        return self

    def create(self, **kwargs: Any) -> Any:
        index = self.calls
        self.calls += 1
        if index < len(self._errors):
            raise self._errors[index]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))
            ],
            model=kwargs.get("model"),
            system_fingerprint="admission-test",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def _router(
    client: _CountingChatClient,
    *,
    admission_retry_enable: bool = True,
    admission_max_retries: int = 3,
    max_retries: int = 2,
    log: list[str] | None = None,
) -> OpenRouterProvider:
    sink = log if log is not None else []
    provider = OpenRouterProvider(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        analyst_model="openai/model-a",
        critic_model="openai/model-a",
        adjudicator_model="openai/model-a",
        fallback_models=(),
        healthcheck_path="/models",
        timeout_sec=300.0,
        max_retries=max_retries,
        max_output_tokens=2048,
        temperature=None,
        top_p=None,
        seed=42,
        enable_thinking=False,
        reasoning_effort="low",
        reasoning_token_reserve=0,
        require_json_schema=True,
        require_structured_provider=True,
        allowed_providers=("openai/flex", "openai"),
        parallelism=1,
        context_budget_tokens=131072,
        circuit_failure_threshold=100,
        circuit_cooldown_sec=60.0,
        log=sink.append,
        admission_retry_enable=admission_retry_enable,
        admission_max_retries=admission_max_retries,
        admission_backoff_initial_sec=2.0,
        admission_backoff_max_sec=30.0,
        client_factory=lambda **_kwargs: client,
    )
    provider._client = client
    return provider


def _router_call(
    provider: OpenRouterProvider,
    *,
    deadline: RequestDeadline | None = None,
    request_id: str = "admission-test",
) -> Any:
    metadata: dict[str, Any] = {"request_id": request_id}
    if deadline is not None:
        metadata["deadline"] = deadline
    return provider.generate_structured(
        role="analyst",
        system_prompt="Return JSON.",
        evidence={"probe": True},
        response_schema=_Probe,
        request_metadata=metadata,
    )


def test_openrouter_shares_one_admission_classifier_with_the_remote_transport():
    """One definition of 'never admitted', not two that can drift apart."""

    exc = _rate_limit_error()
    assert OpenRouterProvider._admission_rejected(exc) is True
    assert RemoteAPIProvider._admission_rejected(exc) is True
    assert OpenRouterProvider._admission_rejected(_quota_error()) is False
    assert OpenRouterProvider._admission_rejected(_timeout_error()) is False


def test_openrouter_waits_before_resubmitting_a_rate_limit(_no_real_sleep):
    client = _CountingChatClient([_rate_limit_error()])
    log: list[str] = []
    provider = _router(client, log=log)

    result = _router_call(provider, deadline=_deadline(2700.0))

    assert result.parsed.ok is True
    assert client.calls == 2
    # The point of the fix: it WAITED.  The pre-fix loop retried immediately.
    assert _no_real_sleep == [pytest.approx(2.0, abs=0.5)]
    assert any(line.startswith("[provider_admission_retry]") for line in log)


def test_openrouter_honours_retry_after(_no_real_sleep):
    client = _CountingChatClient([_rate_limit_error(retry_after="7")])
    provider = _router(client)

    _router_call(provider, deadline=_deadline(2700.0))

    assert _no_real_sleep == [7.0]


def test_openrouter_admission_budget_is_separate_from_max_retries(_no_real_sleep):
    """Three admission refusals clear on a transport configured max_retries=0."""

    client = _CountingChatClient([_rate_limit_error()] * 3)
    provider = _router(client, max_retries=0, admission_max_retries=3)

    result = _router_call(provider, deadline=_deadline(2700.0))

    assert result.parsed.ok is True
    assert client.calls == 4
    assert len(_no_real_sleep) == 3


def test_openrouter_admission_retries_are_bounded(_no_real_sleep):
    client = _CountingChatClient([_rate_limit_error()] * 9)
    provider = _router(client, max_retries=0, admission_max_retries=2)

    with pytest.raises(ProviderCallError):
        _router_call(provider, deadline=_deadline(2700.0))

    # max_retries=0, so all three calls are the admission budget and nothing
    # else.  Leaving max_retries at 2 made the same count reachable through the
    # old immediate-retry path, which is exactly what this pins against.
    assert client.calls == 3
    assert len(_no_real_sleep) == 2


def test_openrouter_never_resubmits_an_ambiguous_timeout(_no_real_sleep):
    """A billed call that may have been ADMITTED must not be sent twice."""

    client = _CountingChatClient([_timeout_error(), _timeout_error()])
    log: list[str] = []
    provider = _router(client, max_retries=2, log=log)

    with pytest.raises(ProviderCallError):
        _router_call(provider, deadline=_deadline(2700.0))

    assert client.calls == 1
    assert any(
        line.startswith("[provider_ambiguous_failure_not_resubmitted]") for line in log
    )


def test_openrouter_never_resubmits_a_generic_5xx(_no_real_sleep):
    client = _CountingChatClient([_server_error(), _server_error()])
    provider = _router(client, max_retries=2)

    with pytest.raises(ProviderCallError):
        _router_call(provider, deadline=_deadline(2700.0))

    assert client.calls == 1


def test_openrouter_quota_exhaustion_is_not_retried(_no_real_sleep):
    """Waiting cannot clear a billing state; burning the deadline is not a fix."""

    client = _CountingChatClient([_quota_error()] * 4)
    provider = _router(client)

    with pytest.raises(ProviderCallError):
        _router_call(provider, deadline=_deadline(2700.0))

    assert client.calls == 1
    assert _no_real_sleep == []


def test_openrouter_skips_a_retry_it_cannot_afford(_no_real_sleep):
    client = _CountingChatClient([_rate_limit_error(retry_after="600")])
    log: list[str] = []
    provider = _router(client, log=log)

    with pytest.raises(ProviderCallError):
        _router_call(provider, deadline=_deadline(120.0, min_attempt_sec=30.0))

    assert client.calls == 1
    assert _no_real_sleep == []
    assert any("action=skipped_insufficient_budget" in line for line in log)


def test_the_loopback_transport_keeps_its_permissive_retry_posture():
    """The change is scoped to paid transports; the local server is untouched."""

    assert LocalOpenAICompatibleProvider.resubmit_ambiguous_transport_failures is True
    assert OpenRouterProvider.resubmit_ambiguous_transport_failures is False


def test_the_gate_wires_the_admission_budget_into_the_openrouter_transport():
    """A budget the factory never passes is a setting that does not exist."""

    source = (ROOT / "ai_gate.py").read_text(encoding="utf-8")
    start = source.index("return OpenRouterProvider(")
    block = source[start : source.index("\n    if cfg.use_remote_api:", start)]
    for field in (
        "admission_retry_enable=cfg.admission_retry_enable",
        "admission_max_retries=cfg.admission_max_retries",
        "admission_backoff_initial_sec=cfg.admission_backoff_initial_sec",
        "admission_backoff_max_sec=cfg.admission_backoff_max_sec",
    ):
        assert field in block, field
