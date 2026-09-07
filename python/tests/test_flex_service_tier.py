"""Regression coverage for the flex service tier.

Two defects are pinned here.

1.  ``_effective_service_tier`` silently downgraded every *live* request to the
    standard tier whenever ``AI_USE_FLEX`` was false, even though
    ``AI_SERVICE_TIER=flex`` and both live-permission acknowledgements were set.
    The gate log recorded this as ``flex_disabled_for_live`` and live traffic
    paid the 1x rate instead of the 0.5x flex rate.

2.  ``RemoteAPIProvider`` hardcoded ``flex_unavailable_retry_enable = False``,
    so the configured capacity retry was dead code and a flex capacity
    rejection became a lost decision -- an infrastructure rejection on healthy
    infrastructure.

The retry is only safe because a capacity rejection means the request was never
admitted. The timeout test below is what keeps that distinction honest: an
ambiguous failure must never be resubmitted, or one request identity would
produce two billed, authoritative provider calls.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import ai_gate
from ai_provider import RemoteAPIProvider
from structured_models import StrictStructuredModel


class _Probe(StrictStructuredModel):
    ok: bool


class _FlexCapacityError(Exception):
    """What the tier returns when it refuses to admit the request."""

    status_code = 503


class _ReadTimeoutError(Exception):
    """Ambiguous: the call may have been admitted and still be running."""

    status_code = 503


class _CountingClient:
    def __init__(self, error: Exception, succeed_after: int | None) -> None:
        self.calls = 0
        self._error = error
        self._succeed_after = succeed_after
        self.responses = SimpleNamespace(create=self.create)

    def with_options(self, **_options: Any) -> "_CountingClient":
        return self

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self._succeed_after is None or self.calls < self._succeed_after:
            raise self._error
        return SimpleNamespace(
            output_text='{"ok":true}',
            model=kwargs.get("model"),
            system_fingerprint="flex-test",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def _provider(
    client: _CountingClient,
    *,
    retry_enable: bool = True,
    max_retries: int = 3,
) -> RemoteAPIProvider:
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
        service_tier="flex",
        flex_unavailable_retry_enable=retry_enable,
        flex_unavailable_max_retries=max_retries,
        flex_unavailable_cooldown_sec=0.0,
        circuit_failure_threshold=100,
        circuit_cooldown_sec=60.0,
        log=lambda _message: None,
        client_factory=lambda **_kwargs: client,
    )
    provider._client = client
    return provider


def _call(provider: RemoteAPIProvider) -> Any:
    return provider.generate_structured(
        role="analyst",
        system_prompt="Return JSON.",
        evidence={"probe": True},
        response_schema=_Probe,
        request_metadata={"request_id": "flex-test", "service_tier": "flex"},
    )


# --------------------------------------------------------------------------
# Defect 1: live requests were downgraded off the flex tier
# --------------------------------------------------------------------------


def _live_payload() -> dict[str, Any]:
    return {"workload_mode": ai_gate.LIVE_FORWARD}


def test_live_request_uses_flex_when_triple_confirmed(monkeypatch):
    config = replace(
        ai_gate.AI_CONFIG,
        provider_select=ai_gate.PROVIDER_SELECT_OPENAI,
        use_remote_api=True,
        use_flex=True,
        allow_flex_for_live=True,
        flex_live_ack=True,
        service_tier="flex",
    )
    monkeypatch.setattr(ai_gate, "AI_CONFIG", config)

    tier, flex_used, reason = ai_gate._effective_service_tier(_live_payload())

    assert tier == "flex"
    assert flex_used is True
    assert reason == ""


def test_live_request_without_use_flex_falls_back_to_standard_tier(monkeypatch):
    """The measured defect: AI_SERVICE_TIER=flex alone bought nothing on live."""
    config = replace(
        ai_gate.AI_CONFIG,
        provider_select=ai_gate.PROVIDER_SELECT_OPENAI,
        use_remote_api=True,
        use_flex=False,
        allow_flex_for_live=True,
        flex_live_ack=True,
        service_tier="flex",
    )
    monkeypatch.setattr(ai_gate, "AI_CONFIG", config)

    tier, flex_used, reason = ai_gate._effective_service_tier(_live_payload())

    assert tier == "auto"
    assert flex_used is False
    assert reason == "flex_disabled_for_live"


def test_live_flex_still_requires_both_acknowledgements(monkeypatch):
    """use_flex alone must not be enough to put live money on a queued tier."""
    for allow, ack in ((False, True), (True, False), (False, False)):
        config = replace(
            ai_gate.AI_CONFIG,
            provider_select=ai_gate.PROVIDER_SELECT_OPENAI,
            use_remote_api=True,
            use_flex=True,
            allow_flex_for_live=allow,
            flex_live_ack=ack,
            service_tier="flex",
        )
        monkeypatch.setattr(ai_gate, "AI_CONFIG", config)

        tier, flex_used, reason = ai_gate._effective_service_tier(_live_payload())

        assert (tier, flex_used, reason) == ("auto", False, "flex_disabled_for_live")


# --------------------------------------------------------------------------
# Defect 2: the configured capacity retry was hardcoded off
# --------------------------------------------------------------------------


def test_remote_provider_honours_configured_flex_retry():
    provider = _provider(_CountingClient(_FlexCapacityError("x"), None), max_retries=7)

    assert provider.flex_unavailable_retry_enable is True
    assert provider.flex_unavailable_max_retries == 7


def test_flex_capacity_rejection_is_resubmitted_until_admitted():
    client = _CountingClient(_FlexCapacityError("503 - flex unavailable"), 3)

    result = _call(_provider(client))

    assert result.parsed.ok is True
    assert client.calls == 3


def test_flex_retry_is_bounded_by_the_configured_budget():
    client = _CountingClient(_FlexCapacityError("503 - flex unavailable"), None)

    try:
        _call(_provider(client, max_retries=2))
    except RuntimeError:
        pass
    else:  # pragma: no cover - the call must fail closed
        raise AssertionError("exhausted flex budget must fail closed")

    # one admitted-and-refused submission plus the two configured retries
    assert client.calls == 3


def test_flex_retry_disabled_by_config_makes_exactly_one_submission():
    client = _CountingClient(_FlexCapacityError("503 - flex unavailable"), None)

    try:
        _call(_provider(client, retry_enable=False))
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("capacity rejection must fail closed when retry is off")

    assert client.calls == 1


def test_ambiguous_timeout_is_never_resubmitted():
    """The safety property the whole retry rests on.

    A timeout can mean the request *was* admitted and is still running.
    Resubmitting it would bill and authorise a second provider call for one
    request identity, and a late result could race the retry.
    """
    client = _CountingClient(_ReadTimeoutError("request timeout while reading"), None)

    try:
        _call(_provider(client, max_retries=9))
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("timeout must fail closed")

    assert client.calls == 1


def test_capacity_predicate_separates_admission_from_ambiguity():
    rejected = [
        _FlexCapacityError("503 - flex unavailable, try again later"),
        _FlexCapacityError("429 resource_unavailable: service tier at capacity"),
        _FlexCapacityError("429 rate limit reached for gpt-5.6-luna"),
    ]
    ambiguous = [
        _ReadTimeoutError("read error: connection error"),
        _ReadTimeoutError("request timed out"),
        _FlexCapacityError("503 - upstream connection error"),
    ]

    for exc in rejected:
        assert RemoteAPIProvider._flex_capacity_rejected(exc) is True, exc

    for exc in ambiguous:
        assert RemoteAPIProvider._flex_capacity_rejected(exc) is False, exc


def test_non_flex_tier_never_enters_the_capacity_loop():
    client = _CountingClient(_FlexCapacityError("503 - flex unavailable"), None)
    provider = _provider(client, max_retries=9)

    try:
        provider.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"probe": True},
            response_schema=_Probe,
            request_metadata={"request_id": "auto-test", "service_tier": "auto"},
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("standard tier failure must fail closed")

    assert client.calls == 1 + provider.max_retries
