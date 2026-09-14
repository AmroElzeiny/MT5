"""Muse fallback leg: glm-5.3-flash on OpenCode Go ``/chat/completions``.

2026-09-14: the leg asked after a Muse failure on the normal route changed from
deepseek-v4.1-flash (``/responses``) to glm-5.3-flash.  A live probe that day
showed ``/responses`` answering HTTP 500 for GLM while ``/chat/completions``
accepted a strict json_schema ``response_format`` with ``reasoning_effort=high``
-- so the leg is a different transport, not a renamed model.

Pinned here, through the real providers and validators:

1. Muse is asked first.
2. A successful Muse answer never reaches GLM.
3. A failing Muse is answered by GLM.
4. The secondary leg is never the Muse fallback (it stays the important route's primary).
5. GLM speaks the correct OpenCode transport and wire requirements.
6. A GLM answer passes the same strict schema validation, or it does not answer.
7. Timeout / 429 / 5xx on Muse fall back to GLM; the same on GLM fall back to Luna.
8. Telemetry and the attempt ledger name GLM.
9. No other route changed.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate  # noqa: E402
import opencode_go_accounting as acct  # noqa: E402
from ai_provider import (  # noqa: E402
    OPENCODE_MUSE_FALLBACK_PROVIDER_ID,
    OPENCODE_PRIMARY_PROVIDER_ID,
    OPENCODE_SECONDARY_PROVIDER_ID,
    OpenCodeChatCompletionsProvider,
    OpenCodeResponsesProvider,
    OpenCodeRoutedProvider,
    OpenCodeTransportError,
    PROVIDER_MODE_OPENCODE,
    PROVIDER_MODE_REMOTE,
    ProviderCallError,
)
from opencode_routing import IMPORTANCE_CRITICAL, OpenCodeRoutingPolicy  # noqa: E402
from test_opencode_provider_contract import (  # noqa: E402
    GLM,
    LUNA,
    MALFORMED_ANSWER,
    MUSE,
    SCHEMA_INVALID_ANSWER,
    SECONDARY_LEG_MODEL,
    VALID_ANSWER,
    _DecisionProbe,
    _call,
    _config,
    _glm,
    _luna,
    _muse,
    _routed,
    _secondary,
)


def _status_error(status: int, message: str) -> OpenCodeTransportError:
    return OpenCodeTransportError(f"Error code: {status} - {message}", status_code=status)


def _routed_with(*, muse, glm, secondary=None, luna=None, policy=None, enable=True):
    lines: list[str] = []
    routed = OpenCodeRoutedProvider(
        muse=muse,
        muse_fallback=glm,
        secondary=secondary or _secondary(VALID_ANSWER, lines.append),
        fallback=luna or _luna(VALID_ANSWER, lines.append),
        policy=policy or OpenCodeRoutingPolicy(),
        log=lines.append,
        fallback_service_tier="flex",
        secondary_fallback_enable=enable,
    )
    routed.log_lines = lines
    return routed


def _no_admission_retry_muse(answer):
    """Muse with its admission retry off, so a 429 fails immediately (no sleep)."""

    provider = _muse(answer)
    provider.admission_retry_enable = False
    provider.admission_max_retries = 0
    return provider


def _no_admission_retry_glm(answer, **overrides):
    return _glm(answer, admission_retry_enable=False, admission_max_retries=0, **overrides)


class MuseFirstTests(unittest.TestCase):
    def test_1_2_muse_is_asked_first_and_a_valid_answer_never_reaches_glm(self) -> None:
        routed = _routed()
        result = _call(routed)
        self.assertEqual(result.actual_model, MUSE)
        self.assertEqual(result.provider_id, OPENCODE_PRIMARY_PROVIDER_ID)
        self.assertEqual(len(routed._muse.transport_double.calls), 1)
        self.assertEqual(len(routed._muse_fallback.transport_double.calls), 0)
        self.assertEqual(len(routed._secondary.transport_double.calls), 0)
        self.assertEqual(len(routed._fallback.transport_double.calls), 0)
        routing = [line for line in routed.log_lines if line.startswith("[opencode_routing]")]
        self.assertIn(f"routed_model={MUSE}", routing[0])


class GlmFallbackTests(unittest.TestCase):
    def test_3_4_a_failing_muse_is_answered_by_glm_and_never_by_the_secondary(self) -> None:
        routed = _routed(muse_answer=_status_error(500, "upstream failure"))
        result = _call(routed)
        self.assertEqual(result.actual_model, GLM)
        self.assertEqual(result.provider_mode, PROVIDER_MODE_OPENCODE)
        self.assertEqual(result.provider_id, OPENCODE_MUSE_FALLBACK_PROVIDER_ID)
        self.assertEqual(len(routed._muse.transport_double.calls), 1)
        self.assertEqual(len(routed._muse_fallback.transport_double.calls), 1)
        self.assertEqual(len(routed._secondary.transport_double.calls), 0)
        self.assertEqual(len(routed._fallback.transport_double.calls), 0)
        # Muse strictly before GLM.
        chain = [line for line in routed.log_lines if line.startswith("[opencode_fallback] ")]
        self.assertIn(f"from_model={MUSE}", chain[0])
        self.assertIn(f"to_model={GLM}", chain[0])

    def test_4_the_built_muse_fallback_is_never_the_secondary_leg(self) -> None:
        secrets = {"OPENCODE_GO_API_KEY": "oc-test-key", "OPENAI_API_KEY": "sk-test-openai"}
        with mock.patch.dict(os.environ, secrets):
            provider = ai_gate._build_ai_provider(_config())
        self.assertIsInstance(provider, OpenCodeRoutedProvider)
        self.assertIsInstance(provider._muse_fallback, OpenCodeChatCompletionsProvider)
        self.assertEqual(provider._muse_fallback.model_for_role("analyst"), GLM)
        self.assertNotEqual(provider._muse_fallback.provider_id, OPENCODE_SECONDARY_PROVIDER_ID)
        legs = provider._fallback_legs_after(provider._muse)
        self.assertIs(legs[0], provider._muse_fallback)
        self.assertIs(legs[1], provider._fallback)
        self.assertNotIn(provider._secondary, legs)
        # Muse stays the primary and keeps its identity.
        self.assertEqual(provider._muse.model_for_role("analyst"), MUSE)
        self.assertEqual(provider.provider_id, OPENCODE_PRIMARY_PROVIDER_ID)

    def test_7_muse_timeout_429_and_5xx_each_fall_back_to_glm(self) -> None:
        for label, failure in (
            ("timeout", TimeoutError("request timed out")),
            ("429", _status_error(429, "{'error': {'code': 'rate_limit_exceeded'}}")),
            ("500", _status_error(500, "internal server error")),
            ("502", _status_error(502, "bad gateway")),
            ("503", _status_error(503, "service unavailable")),
        ):
            with self.subTest(failure=label):
                routed = _routed_with(
                    muse=_no_admission_retry_muse(failure), glm=_glm(VALID_ANSWER)
                )
                result = _call(routed)
                self.assertEqual(result.actual_model, GLM)
                self.assertEqual(len(routed._muse_fallback.transport_double.calls), 1)
                self.assertEqual(len(routed._secondary.transport_double.calls), 0)
                self.assertEqual(len(routed._fallback.transport_double.calls), 0)

    def test_7_glm_timeout_429_and_5xx_are_failed_attempts_that_end_on_luna(self) -> None:
        for label, failure in (
            ("timeout", TimeoutError("request timed out")),
            ("429", _status_error(429, "{'error': {'code': 'rate_limit_exceeded'}}")),
            ("500", _status_error(500, "internal server error")),
            ("503", _status_error(503, "service unavailable")),
        ):
            with self.subTest(failure=label):
                routed = _routed_with(
                    muse=_muse(MALFORMED_ANSWER), glm=_no_admission_retry_glm(failure)
                )
                result = _call(routed)
                self.assertEqual(result.actual_model, LUNA)
                self.assertEqual(result.provider_mode, PROVIDER_MODE_REMOTE)
                # An ambiguous GLM failure is never resubmitted to GLM.
                self.assertEqual(len(routed._muse_fallback.transport_double.calls), 1)
                self.assertEqual(len(routed._secondary.transport_double.calls), 0)

    def test_7_a_glm_429_uses_the_existing_admission_retry_before_failing_over(self) -> None:
        """A proven admission refusal gets the shared retry budget, not a new policy."""

        glm_lines: list[str] = []
        glm = _glm(_status_error(429, "{'error': {'code': 'rate_limit_exceeded'}}"),
                   glm_lines.append,
                   admission_max_retries=1, admission_backoff_initial_sec=0.0,
                   admission_backoff_max_sec=0.0)
        routed = _routed_with(muse=_muse(MALFORMED_ANSWER), glm=glm)
        with mock.patch("ai_provider.time.sleep"):
            result = _call(routed)
        self.assertEqual(result.actual_model, LUNA)
        self.assertEqual(len(glm.transport_double.calls), 2)
        retry_lines = [line for line in glm_lines if line.startswith("[provider_admission_retry]")]
        self.assertTrue(retry_lines)
        self.assertIn("admitted=false resubmission_safe=true", retry_lines[0])


class GlmTransportTests(unittest.TestCase):
    def _sent(self, provider, request_id="591813800_1786509126_GBPNZD_13183"):
        provider.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": request_id, "service_tier": "auto"},
        )
        return provider.transport_double.calls[0]

    def test_5_glm_speaks_chat_completions_with_every_opencode_requirement(self) -> None:
        glm = _glm(VALID_ANSWER)
        self.assertEqual(glm.provider_mode, PROVIDER_MODE_OPENCODE)
        self.assertEqual(glm.provider_id, OPENCODE_MUSE_FALLBACK_PROVIDER_ID)
        self.assertNotIsInstance(glm, OpenCodeResponsesProvider)
        sent = self._sent(glm)
        # /chat/completions body shape, not /responses.
        self.assertEqual(sent["model"], GLM)
        self.assertIn("messages", sent)
        self.assertNotIn("input", sent)
        self.assertNotIn("text", sent)
        self.assertEqual(sent["messages"][0]["role"], "system")
        self.assertEqual(sent["messages"][1]["role"], "user")
        fmt = sent["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["json_schema"]["strict"])
        self.assertEqual(
            set(fmt["json_schema"]["schema"]["required"]), {"decision_state", "llm_quality_score"}
        )
        self.assertEqual(sent["extra_body"], {"reasoning_effort": "high"})
        self.assertNotIn("service_tier", sent)
        self.assertNotIn("temperature", sent)
        session = sent["extra_headers"]["x-opencode-session"]
        self.assertTrue(session.startswith("po3-prefix-"), session)
        self.assertNotIn("GBPNZD", session)

    def test_5_glm_reserves_reasoning_tokens_on_top_of_the_schema_budget(self) -> None:
        reserved = self._sent(_glm(VALID_ANSWER, reasoning_token_reserve=24000))["max_tokens"]
        bare = self._sent(_glm(VALID_ANSWER, reasoning_token_reserve=0))["max_tokens"]
        self.assertEqual(reserved, bare + 24000)

    def test_5_request_session_scope_and_omitted_effort(self) -> None:
        sent = self._sent(_glm(VALID_ANSWER, session_scope="request", reasoning_effort="auto"))
        self.assertEqual(
            sent["extra_headers"]["x-opencode-session"], "po3-591813800_1786509126_GBPNZD_13183"
        )
        self.assertNotIn("extra_body", sent)

    def test_5_glm_and_muse_share_one_session_derivation_for_one_schema(self) -> None:
        muse = _muse(VALID_ANSWER)
        muse.generate_structured(
            role="analyst", system_prompt="Return JSON.", evidence={"x": 1},
            response_schema=_DecisionProbe, request_metadata={"request_id": "r"},
        )
        glm_session = self._sent(_glm(VALID_ANSWER))["extra_headers"]["x-opencode-session"]
        muse_session = muse.transport_double.calls[0]["extra_headers"]["x-opencode-session"]
        # Different models, so different sessions -- but both are prefix sessions.
        self.assertTrue(glm_session.startswith("po3-prefix-"))
        self.assertTrue(muse_session.startswith("po3-prefix-"))
        self.assertNotEqual(glm_session, muse_session)

    def test_5_health_is_configuration_not_a_billed_call(self) -> None:
        health = _glm(VALID_ANSWER).healthcheck()
        self.assertTrue(health.healthy)
        self.assertEqual(health.model_id, GLM)


class GlmValidationTests(unittest.TestCase):
    def test_6_a_valid_glm_answer_is_a_validated_model_instance(self) -> None:
        routed = _routed(muse_answer=MALFORMED_ANSWER)
        result = _call(routed)
        self.assertEqual(result.actual_model, GLM)
        self.assertIsInstance(result.parsed, _DecisionProbe)
        self.assertEqual(result.parsed.llm_quality_score, 6.5)

    def test_6_an_unusable_glm_answer_is_never_success(self) -> None:
        for label, answer in (
            ("schema_invalid", SCHEMA_INVALID_ANSWER),
            ("malformed_json", MALFORMED_ANSWER),
            ("empty", ""),
            ("prose", "I think ABSTAIN with 6.5"),
            ("wrong_type", '{"decision_state":"ABSTAIN","llm_quality_score":"high"}'),
            ("extra_field", '{"decision_state":"ABSTAIN","llm_quality_score":6.5,"x":1}'),
        ):
            with self.subTest(answer=label):
                routed = _routed(muse_answer=MALFORMED_ANSWER, muse_fallback_answer=answer)
                result = _call(routed)
                self.assertEqual(result.actual_model, LUNA)
                # Not re-asked of GLM.
                self.assertEqual(len(routed._muse_fallback.transport_double.calls), 1)

    def test_6_a_glm_answer_from_another_model_is_rejected(self) -> None:
        routed = _routed_with(
            muse=_muse(MALFORMED_ANSWER), glm=_glm(VALID_ANSWER, answer_model="some-other-model")
        )
        self.assertEqual(_call(routed).actual_model, LUNA)

    def test_6_no_leg_valid_means_no_answer(self) -> None:
        routed = _routed(
            muse_answer=MALFORMED_ANSWER,
            muse_fallback_answer=SCHEMA_INVALID_ANSWER,
            luna_answer=SCHEMA_INVALID_ANSWER,
        )
        with self.assertRaises(ProviderCallError):
            _call(routed)


class GlmTelemetryTests(unittest.TestCase):
    def test_8_routing_logs_name_glm(self) -> None:
        routed = _routed(muse_answer=_status_error(500, "upstream"))
        _call(routed)
        text = "\n".join(routed.log_lines)
        self.assertIn(f"to={OPENCODE_MUSE_FALLBACK_PROVIDER_ID}", text)
        self.assertIn(f"to_model={GLM}", text)
        self.assertIn(f"fallback_chain={MUSE}>{GLM}>{LUNA}", text)
        self.assertIn(f"provider={OPENCODE_MUSE_FALLBACK_PROVIDER_ID}", text)
        completed = [l for l in routed.log_lines if l.startswith("[opencode_fallback_completed]")]
        self.assertIn(f"model={GLM}", completed[0])
        # The secondary leg shares Muse's model id, so it is excluded by id.
        self.assertNotIn(OPENCODE_SECONDARY_PROVIDER_ID, text)
        self.assertNotIn("oc-test-key", text)

    def test_8_attempt_ledger_records_the_glm_attempt_on_chat_completions(self) -> None:
        ledger: list[dict] = []
        routed = _routed(muse_answer=MALFORMED_ANSWER)
        for leg in (routed._muse, routed._muse_fallback, routed._secondary, routed._fallback):
            leg.attempt_observer = ledger.append
        routed.call_observer = ledger.append
        _call(routed)
        attempts = [r for r in ledger if r.get("event") == acct.EVENT_ATTEMPT]
        glm_rows = [r for r in attempts if r.get("model") == GLM]
        self.assertEqual(len(glm_rows), 1)
        row = glm_rows[0]
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(row["provider_id"], OPENCODE_MUSE_FALLBACK_PROVIDER_ID)
        self.assertTrue(str(row["endpoint"]).endswith("/chat/completions"))
        self.assertTrue(row["schema_strict"])
        self.assertEqual(row["reasoning_effort_sent"], "high")
        self.assertTrue(str(row["session_id"]).startswith("po3-prefix-"))
        self.assertFalse(
            any(r.get("provider_id") == OPENCODE_SECONDARY_PROVIDER_ID for r in attempts)
        )
        logical = [r for r in ledger if r.get("event") == acct.EVENT_LOGICAL_CALL]
        self.assertEqual(len(logical), 1)

    def test_8_a_schema_invalid_glm_attempt_is_recorded(self) -> None:
        ledger: list[dict] = []
        routed = _routed(muse_answer=MALFORMED_ANSWER, muse_fallback_answer=SCHEMA_INVALID_ANSWER)
        routed._muse_fallback.attempt_observer = ledger.append
        _call(routed)
        self.assertEqual([r["outcome"] for r in ledger], ["schema_invalid"])

    def test_8_usage_ledger_reports_glm_effort(self) -> None:
        cfg = _config(OPENCODE_MUSE_FALLBACK_REASONING_EFFORT="max")
        self.assertEqual(
            ai_gate._opencode_leg_reasoning_effort(OPENCODE_MUSE_FALLBACK_PROVIDER_ID, cfg), "max"
        )
        self.assertEqual(
            ai_gate._opencode_leg_reasoning_effort(OPENCODE_SECONDARY_PROVIDER_ID, cfg), "high"
        )


class GlmConfigurationTests(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = _config()
        self.assertTrue(cfg.provider_config_valid)
        self.assertEqual(cfg.model, MUSE)
        self.assertEqual(cfg.opencode_muse_fallback_model, GLM)
        self.assertEqual(cfg.opencode_muse_fallback_reasoning_effort, "high")
        self.assertEqual(cfg.opencode_muse_fallback_reasoning_token_reserve, 24000)
        # The important route's secondary leg runs Muse (was DeepSeek until 2026-09-14).
        self.assertEqual(cfg.opencode_secondary_model, SECONDARY_LEG_MODEL)
        self.assertEqual(cfg.validation_warnings, ())

    def test_invalid_effort_warns_and_keeps_high(self) -> None:
        bad = _config(OPENCODE_MUSE_FALLBACK_REASONING_EFFORT="extreme")
        self.assertEqual(bad.opencode_muse_fallback_reasoning_effort, "high")
        self.assertIn("OPENCODE_MUSE_FALLBACK_REASONING_EFFORT=invalid", bad.validation_warnings)

    def test_banner_reports_the_leg_only_under_this_selection(self) -> None:
        rendered = _config().safe_log_dict()
        self.assertEqual(rendered["opencode_muse_fallback_model"], GLM)
        self.assertEqual(rendered["opencode_muse_fallback_reasoning_effort"], "high")
        other = ai_gate.AIGateRuntimeConfig.from_env(
            {"AI_PROVIDER_SELECT": "openai_remote", "OPENAI_API_KEY": "sk"}
        ).safe_log_dict()
        self.assertEqual(other["opencode_muse_fallback_model"], "")

    def test_the_gate_builds_the_glm_leg_as_configured(self) -> None:
        secrets = {"OPENCODE_GO_API_KEY": "oc-test-key", "OPENAI_API_KEY": "sk-test-openai"}
        with mock.patch.dict(os.environ, secrets):
            provider = ai_gate._build_ai_provider(
                _config(OPENCODE_MUSE_FALLBACK_REASONING_TOKEN_RESERVE="16000")
            )
        glm = provider._muse_fallback
        self.assertEqual(glm.base_url, "https://opencode.ai/zen/go/v1")
        self.assertEqual(glm.reasoning_effort, "high")
        self.assertEqual(glm.reasoning_token_reserve, 16000)
        self.assertEqual(glm.max_retries, 0)
        self.assertTrue(glm.require_json_schema)
        self.assertIs(glm.attempt_observer, provider.call_observer)


class UnchangedRoutingTests(unittest.TestCase):
    def test_9_important_route_is_still_secondary_then_luna_never_glm(self) -> None:
        policy = OpenCodeRoutingPolicy(call_directing=True)
        ok = _routed(policy=policy)
        ok_result = _call(ok)
        self.assertEqual(ok_result.actual_model, SECONDARY_LEG_MODEL)
        self.assertEqual(ok_result.provider_id, OPENCODE_SECONDARY_PROVIDER_ID)
        failing = _routed(policy=policy, secondary_answer=_status_error(502, "bad gateway"))
        self.assertEqual(_call(failing).actual_model, LUNA)
        for routed in (ok, failing):
            self.assertEqual(len(routed._muse.transport_double.calls), 0)
            self.assertEqual(len(routed._muse_fallback.transport_double.calls), 0)

    def test_9_critical_route_is_still_luna_only(self) -> None:
        routed = _routed(
            policy=OpenCodeRoutingPolicy(call_directing=True, forced_importance=IMPORTANCE_CRITICAL)
        )
        self.assertEqual(_call(routed).actual_model, LUNA)
        self.assertEqual(len(routed._muse_fallback.transport_double.calls), 0)
        self.assertEqual(len(routed._secondary.transport_double.calls), 0)

    def test_9_disabling_the_stage_still_restores_muse_then_luna(self) -> None:
        routed = _routed(muse_answer=MALFORMED_ANSWER, secondary_fallback_enable=False)
        self.assertEqual(_call(routed).actual_model, LUNA)
        self.assertEqual(len(routed._muse_fallback.transport_double.calls), 0)

    def test_9_identity_still_follows_muse_and_reports_the_same_legs(self) -> None:
        identity = _routed().identity("analyst")
        self.assertEqual(identity["provider_id"], OPENCODE_PRIMARY_PROVIDER_ID)
        self.assertEqual(identity["configured_model_ids"], [MUSE])
        self.assertEqual(
            identity["opencode_legs"],
            {"normal": MUSE, "important": SECONDARY_LEG_MODEL, "critical": LUNA},
        )

    def test_9_circuit_helpers_cover_the_glm_leg(self) -> None:
        routed = _routed()
        with mock.patch.object(routed._muse_fallback, "configuration_circuit_count", return_value=2):
            self.assertEqual(routed.configuration_circuit_count(), 2)
        with mock.patch.object(routed._muse_fallback, "clear_configuration_circuit") as clear:
            routed.clear_configuration_circuit("k")
        clear.assert_called_once_with("k")


if __name__ == "__main__":
    unittest.main()
