"""A transient HTTP 403 must not disable a provider for the life of the gate.

Measured 2026-09-13, gate session ``python_376_1789303136``: Muse answered two
calls with HTTP 403 ``permission_denied`` at 15:38.  The configuration circuit
that refusal opened was only ever cleared by a *successful* call, but it was
checked *before* any call was sent -- so no call could succeed and the circuit
could never clear.  159 of 159 later Muse calls were refused locally in under a
second, and the whole session fell through to the secondary leg and Luna.  At
~16:05 the same key, model and requests answered HTTP 200.

The fix makes a 403 circuit recoverable: it refuses exactly as before for a
cooldown, then lets ONE probe through.  Every other configuration refusal (bad
key, invalid schema, local preflight) stays permanent, because waiting cannot
repair our own configuration.

Tests marked "fails pre-change" were run against ``ai_provider.py`` with this
change reverted and failed there.  The permanence tests are guards: they pass
both before and after, and exist so recovery can never widen past 403.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_provider  # noqa: E402
from ai_provider import (  # noqa: E402
    LocalOpenAICompatibleProvider,
    OPENCODE_MUSE_FALLBACK_PROVIDER_ID,
    OPENCODE_PRIMARY_PROVIDER_ID,
    OpenCodeResponsesProvider,
    ProviderCallError,
)
from pydantic import BaseModel  # noqa: E402
from structured_models import StrictStructuredModel  # noqa: E402
from test_opencode_provider_contract import (  # noqa: E402
    VALID_ANSWER,
    _call as _routed_call,
    _routed,
)

# The default is read from the module so the guards below still import (and
# fail informatively) against a tree that predates the setting.
COOLDOWN_SEC = float(getattr(ai_provider, "CONFIGURATION_RECOVERY_COOLDOWN_SEC", 300.0))
OK_ANSWER = '{"ok":true}'
METADATA = {
    "request_id": "req-circuit-1",
    "decision_schema_version": "schema-v",
    "prompt_contract_version": "prompt-v",
}


class _Probe(StrictStructuredModel):
    ok: bool


class _NonStrictProbe(BaseModel):
    ok: bool


class _PermissionDenied(RuntimeError):
    status_code = 403

    def __init__(self) -> None:
        super().__init__("Error code: 403 - permission_denied")


class _Unauthorized(RuntimeError):
    status_code = 401

    def __init__(self) -> None:
        super().__init__("invalid API key")


class _InvalidSchema(RuntimeError):
    status_code = 400

    def __init__(self) -> None:
        super().__init__(
            "Invalid schema for response_format 'probe': "
            "'additionalProperties' is required to be supplied and to be false"
        )


class _ServerError(RuntimeError):
    status_code = 500

    def __init__(self) -> None:
        super().__init__("Error code: 500 - Internal server error")


class _Clock:
    """Deterministic stand-in for ``time.monotonic`` -- no real sleeping."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _Script:
    """Answers wire calls from a script, then answers ``OK_ANSWER`` forever."""

    def __init__(self, *steps: object) -> None:
        self.steps = list(steps)
        self.calls = 0

    def next(self) -> str:
        self.calls += 1
        step = self.steps.pop(0) if self.steps else OK_ANSWER
        if callable(step) and not isinstance(step, BaseException):
            step = step()
        if isinstance(step, BaseException):
            raise step
        return str(step)


def _responses_client(script: _Script) -> object:
    def create(**kwargs: object) -> object:
        return SimpleNamespace(
            model=kwargs["model"],
            output_text=script.next(),
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        )

    responses = SimpleNamespace(create=create)
    return SimpleNamespace(responses=responses, with_options=lambda **_: SimpleNamespace(responses=responses))


def _chat_client(script: _Script) -> object:
    def create(**kwargs: object) -> dict:
        return {
            "model": kwargs["model"],
            "choices": [{"message": {"content": script.next()}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    return SimpleNamespace(chat=chat, with_options=lambda **_: SimpleNamespace(chat=chat))


def _responses_provider(script: _Script, logs: list[str]) -> OpenCodeResponsesProvider:
    # The Muse transport class: no schema repair and no resubmission of an
    # ambiguous failure, so every scripted step maps to exactly one outcome.
    client = _responses_client(script)
    return OpenCodeResponsesProvider(
        base_url="https://opencode.ai/zen/go/v1",
        api_key="oc-test-key",
        model="muse-spark-1.3-contributor",
        reasoning_effort="high",
        timeout_sec=60.0,
        max_output_tokens=512,
        circuit_failure_threshold=100,
        circuit_cooldown_sec=1.0,
        log=logs.append,
        client_factory=lambda **_: client,
    )


def _chat_provider(script: _Script, logs: list[str]) -> LocalOpenAICompatibleProvider:
    provider = LocalOpenAICompatibleProvider(
        base_url="http://127.0.0.1:1234/v1",
        api_key="test-key",
        analyst_model="model-a",
        critic_model="model-a",
        adjudicator_model="model-a",
        fallback_models=(),
        healthcheck_path="/models",
        timeout_sec=60.0,
        max_retries=0,
        max_output_tokens=512,
        temperature=None,
        top_p=None,
        seed=None,
        enable_thinking=False,
        require_json_schema=True,
        parallelism=4,
        context_budget_tokens=131072,
        circuit_failure_threshold=100,
        circuit_cooldown_sec=1.0,
        log=logs.append,
    )
    provider._client = _chat_client(script)
    return provider


def _call(provider: object, schema: type = _Probe) -> object:
    return provider.generate_structured(
        role="analyst",
        system_prompt="Return strict JSON.",
        evidence={"fixture": True},
        response_schema=schema,
        request_metadata=dict(METADATA),
    )


class _ClockedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        patcher = mock.patch.object(ai_provider.time, "monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logs: list[str] = []

    def assertBlocked(
        self,
        provider: object,
        schema: type = _Probe,
        *,
        reason: str = "PROVIDER_CONFIGURATION_ERROR",
    ) -> None:
        with self.assertRaises(ProviderCallError) as caught:
            _call(provider, schema)
        error = caught.exception
        # Byte-identical to the pre-change refusal: same category, same message,
        # and still honest that nothing was sent.
        self.assertEqual(
            str(error),
            "PROVIDER_CONFIGURATION_ERROR:provider_configuration_block:" + reason,
        )
        self.assertTrue(error.configuration_block)
        self.assertFalse(error.provider_call_attempted)
        self.assertFalse(error.http_request_sent)

    def lines(self, tag: str) -> list[str]:
        return [line for line in self.logs if line.startswith(tag)]


class RecoverableForbiddenTests(_ClockedTest):
    def test_403_blocks_inside_the_cooldown_without_a_wire_call(self) -> None:
        """Guard: inside the cooldown the refusal is exactly today's."""

        script = _Script(_PermissionDenied())
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError) as first:
            _call(provider)
        self.assertEqual(first.exception.status_code, 403)
        self.assertTrue(first.exception.configuration_block)
        self.assertBlocked(provider)
        self.clock.advance(COOLDOWN_SEC - 1.0)
        self.assertBlocked(provider)
        self.assertEqual(script.calls, 1)

    def test_403_recovers_through_one_probe_after_the_cooldown(self) -> None:
        """Fails pre-change: the latch refused this call forever."""

        script = _Script(_PermissionDenied())
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC)
        self.assertTrue(_call(provider).parsed.ok)
        self.assertEqual(script.calls, 2)
        self.assertEqual(provider.configuration_circuit_count(), 0)
        # Cleared, not merely probed: the next call goes straight to the wire.
        self.assertTrue(_call(provider).parsed.ok)
        self.assertEqual(script.calls, 3)

    def test_probe_meeting_403_again_reopens_with_a_fresh_cooldown(self) -> None:
        """Fails pre-change: no probe ever reached the wire."""

        script = _Script(_PermissionDenied(), _PermissionDenied())
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC + 1.0)
        with self.assertRaises(ProviderCallError) as probe:
            _call(provider)
        self.assertEqual(probe.exception.status_code, 403)
        self.assertEqual(script.calls, 2)
        # The cooldown restarted at the probe, not at the original refusal.
        self.clock.advance(COOLDOWN_SEC - 1.0)
        self.assertBlocked(provider)
        self.assertEqual(script.calls, 2)
        self.clock.advance(1.0)
        self.assertTrue(_call(provider).parsed.ok)
        self.assertEqual(script.calls, 3)

    def test_only_one_probe_is_in_flight_per_circuit(self) -> None:
        """Fails pre-change: the probe itself was refused, so it never entered."""

        entered = threading.Event()
        release = threading.Event()

        def slow_answer() -> str:
            entered.set()
            if not release.wait(timeout=10.0):
                raise AssertionError("probe was never released by the test")
            return OK_ANSWER

        script = _Script(_PermissionDenied(), slow_answer)
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC)
        outcome: dict[str, object] = {}

        def run_probe() -> None:
            try:
                outcome["result"] = _call(provider)
            except BaseException as exc:  # pragma: no cover - reported below
                outcome["error"] = exc

        probe = threading.Thread(target=run_probe)
        probe.start()
        try:
            self.assertTrue(entered.wait(timeout=10.0), "probe did not reach the wire")
            # A concurrent caller for the same circuit is refused immediately.
            self.assertBlocked(provider)
            self.assertEqual(script.calls, 2)
        finally:
            release.set()
            probe.join(timeout=10.0)
        self.assertNotIn("error", outcome)
        self.assertTrue(outcome["result"].parsed.ok)
        self.assertEqual(provider.configuration_circuit_count(), 0)

    def test_probe_is_released_after_a_non_403_failure(self) -> None:
        """Fails pre-change: the follow-up call was still latched."""

        script = _Script(_PermissionDenied(), _ServerError())
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC)
        with self.assertRaises(ProviderCallError) as probe:
            _call(provider)
        self.assertFalse(probe.exception.configuration_block)
        self.assertEqual(script.calls, 2)
        # No verdict on the permission: the circuit stays, the probe slot does
        # not, so the very next caller may probe without another cooldown.
        self.assertEqual(provider.configuration_circuit_count(), 1)
        self.assertTrue(_call(provider).parsed.ok)
        self.assertEqual(script.calls, 3)

    def test_probe_is_released_when_the_call_fails_before_sending(self) -> None:
        """Fails pre-change: the follow-up call was still latched."""

        script = _Script(_PermissionDenied())
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC)
        good_client = provider._client_instance()

        def refuse_options(**_: object) -> object:
            raise RuntimeError("client construction failed before send")

        provider._client = SimpleNamespace(
            responses=good_client.responses, with_options=refuse_options
        )
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.assertEqual(script.calls, 1)
        provider._client = good_client
        self.assertTrue(_call(provider).parsed.ok)
        self.assertEqual(script.calls, 2)

    def test_chat_completions_transport_recovers_the_same_way(self) -> None:
        """Fails pre-change: the local/OpenRouter loop shared the latch."""

        script = _Script(_PermissionDenied())
        provider = _chat_provider(script, self.logs)
        with self.assertRaises(ProviderCallError) as first:
            _call(provider)
        self.assertEqual(first.exception.status_code, 403)
        self.assertBlocked(provider)
        self.assertEqual(script.calls, 1)
        self.clock.advance(COOLDOWN_SEC)
        self.assertTrue(_call(provider).parsed.ok)
        self.assertEqual(script.calls, 2)
        self.assertEqual(provider.configuration_circuit_count(), 0)


class PermanentRefusalGuards(_ClockedTest):
    def test_401_stays_blocked_long_after_the_cooldown(self) -> None:
        script = _Script(_Unauthorized())
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC * 100)
        self.assertBlocked(provider)
        self.assertEqual(script.calls, 1)

    def test_invalid_schema_400_stays_blocked_long_after_the_cooldown(self) -> None:
        script = _Script(_InvalidSchema())
        provider = _chat_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC * 100)
        self.assertBlocked(provider, reason="STRUCTURED_SCHEMA_INVALID")
        self.assertEqual(script.calls, 1)

    def test_local_schema_preflight_never_reaches_the_wire(self) -> None:
        script = _Script()
        provider = _responses_provider(script, self.logs)
        for _ in range(2):
            with self.assertRaises(ProviderCallError) as caught:
                _call(provider, _NonStrictProbe)
            self.assertEqual(caught.exception.category, "STRUCTURED_SCHEMA_INVALID")
            self.clock.advance(COOLDOWN_SEC * 100)
        self.assertEqual(script.calls, 0)


class TelemetryTests(_ClockedTest):
    def test_block_and_probe_lines_name_recoverability_and_outcome(self) -> None:
        """Fails pre-change: neither field nor probe line existed."""

        script = _Script(_PermissionDenied(), _PermissionDenied(), _ServerError())
        provider = _responses_provider(script, self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        self.clock.advance(COOLDOWN_SEC)
        with self.assertRaises(ProviderCallError):
            _call(provider)  # probe -> 403 again
        self.clock.advance(COOLDOWN_SEC)
        with self.assertRaises(ProviderCallError):
            _call(provider)  # probe -> 500, released
        _call(provider)  # probe -> success, cleared

        blocks = self.lines("[provider_configuration_block]")
        self.assertEqual(len(blocks), 2)
        for line in blocks:
            self.assertIn(" recoverable=true", line)
            self.assertIn(f" cooldown_sec={COOLDOWN_SEC:g}", line)
            self.assertNotIn("oc-test-key", line)
        actions = [
            line.split(" action=")[1].split()[0]
            for line in self.lines("[provider_configuration_probe]")
        ]
        self.assertEqual(
            actions,
            [
                "probe_started",
                "reopened",
                "probe_started",
                "released_without_verdict",
                "probe_started",
                "cleared",
            ],
        )

    def test_permanent_refusal_is_logged_as_not_recoverable(self) -> None:
        """Fails pre-change: the field did not exist."""

        provider = _responses_provider(_Script(_Unauthorized()), self.logs)
        with self.assertRaises(ProviderCallError):
            _call(provider)
        blocks = self.lines("[provider_configuration_block]")
        self.assertEqual(len(blocks), 1)
        self.assertIn(" recoverable=false", blocks[0])
        self.assertNotIn("cooldown_sec=", blocks[0])
        self.assertEqual(self.lines("[provider_configuration_probe]"), [])


class RoutedIncidentReplayTests(_ClockedTest):
    def test_muse_returns_to_the_route_after_a_transient_403(self) -> None:
        """Fails pre-change: the 2026-09-13 session, Muse never came back."""

        routed = _routed()
        muse = routed._muse.transport_double
        # The leg that answers a Muse failure: GLM since 2026-09-14.
        secondary = routed._muse_fallback.transport_double
        muse.answer = _PermissionDenied()

        first = _routed_call(routed)
        self.assertEqual(first.provider_id, OPENCODE_MUSE_FALLBACK_PROVIDER_ID)
        self.assertEqual(len(muse.calls), 1)

        # Inside the cooldown Muse is skipped without a wire call.
        second = _routed_call(routed)
        self.assertEqual(second.provider_id, OPENCODE_MUSE_FALLBACK_PROVIDER_ID)
        self.assertEqual(len(muse.calls), 1)
        self.assertEqual(len(secondary.calls), 2)

        # The refusal has cleared upstream; after the cooldown Muse answers.
        muse.answer = VALID_ANSWER
        self.clock.advance(COOLDOWN_SEC)
        third = _routed_call(routed)
        self.assertEqual(third.provider_id, OPENCODE_PRIMARY_PROVIDER_ID)
        self.assertEqual(len(muse.calls), 2)
        self.assertEqual(len(secondary.calls), 2)
        self.assertEqual(routed.configuration_circuit_count(), 0)


if __name__ == "__main__":
    unittest.main()
