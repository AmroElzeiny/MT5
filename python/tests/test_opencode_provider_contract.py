"""OpenCode provider mode: selection, deterministic routing, and Luna fallback.

Every test here fails against the pre-change tree.  The ones that matter most:

``test_schema_invalid_opencode_answer_falls_back_to_luna``
    An OpenCode answer that parses as JSON but is not a valid instance of the
    decision schema must never reach a downstream consumer.  This asserts the
    *whole* strict path runs on the OpenCode leg -- the same
    ``_strict_json_object`` + ``model_validate`` the OpenAI transport uses -- and
    that the fallback answer is validated in turn.

``test_a_bad_opencode_answer_is_not_re_asked_of_the_same_model``
    The requirement that a malformed OpenCode response goes to Luna instead of
    being retried against the model that just produced it.  Pinned by counting
    calls, not by reading configuration.

``test_critical_route_never_touches_opencode``
    A critical live request goes straight to Luna.  If the OpenCode legs are
    reachable on that route the classification is decorative.

``test_existing_openai_and_openrouter_wire_shapes_are_unchanged``
    The hooks added for OpenCode must not alter the two transports they were
    factored out of.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate  # noqa: E402
from ai_gate import (  # noqa: E402
    AIGateRuntimeConfig,
    PROVIDER_SELECT_LOCAL,
    PROVIDER_SELECT_OPENAI,
    PROVIDER_SELECT_OPENCODE,
    PROVIDER_SELECT_OPENROUTER,
    resolve_provider_select,
)
from ai_provider import (  # noqa: E402
    LocalOpenAICompatibleProvider,
    OpenCodeMessagesProvider,
    OpenCodeResponsesProvider,
    OpenCodeRoutedProvider,
    OpenCodeTransportError,
    OpenRouterProvider,
    PROVIDER_MODE_LOCAL,
    PROVIDER_MODE_OPENCODE,
    PROVIDER_MODE_OPENROUTER,
    PROVIDER_MODE_REMOTE,
    PROVIDER_MODES_TRADING,
    ProviderCallError,
    RemoteAPIProvider,
    _OpenCodeMessagesClient,
)
import openai_usage_logger as usage  # noqa: E402
import po3_env  # noqa: E402
from opencode_routing import (  # noqa: E402
    IMPORTANCE_CRITICAL,
    IMPORTANCE_IMPORTANT,
    IMPORTANCE_NORMAL,
    LIVE_FORWARD,
    OpenCodeRoutingPolicy,
    classify_importance,
)
from structured_models import StrictStructuredModel  # noqa: E402

MUSE = "muse-spark-1.3-contributor"
QWEN = "qwen3.8-flash"
LUNA = "gpt-5.6-luna"
BASE_URL = "https://opencode.ai/zen/go/v1"


class _DecisionProbe(StrictStructuredModel):
    """Two required fields, so "valid JSON" and "valid answer" can differ."""

    decision_state: str
    llm_quality_score: float


VALID_ANSWER = '{"decision_state":"ABSTAIN","llm_quality_score":6.5}'
# Parses as JSON, is not an instance of the schema: the exact shape that must
# never be forwarded downstream.
SCHEMA_INVALID_ANSWER = '{"decision_state":"ABSTAIN"}'
MALFORMED_ANSWER = '{"decision_state":"ABSTAIN",'


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "AI_PROVIDER_SELECT": "opencode",
        "OPENCODE_GO_API_KEY": "oc-test-key",
        "OPENAI_API_KEY": "sk-test-openai",
    }
    base.update(overrides)
    return base


def _config(**overrides: str) -> AIGateRuntimeConfig:
    return AIGateRuntimeConfig.from_env(_env(**overrides))


# ---------------------------------------------------------------------------
# Transport doubles.  Each returns the *real* wire shape of its dialect, so the
# providers under test run their genuine extraction and validation code rather
# than a mocked shortcut.
# ---------------------------------------------------------------------------


class _ResponsesDouble:
    """OpenAI Responses transport double for the Muse and Luna legs."""

    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if isinstance(self.answer, Exception):
            raise self.answer
        return SimpleNamespace(
            model=kwargs["model"],
            output_text=self.answer,
            service_tier=kwargs.get("service_tier"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
        )

    def as_client(self):
        outer = self

        class _Client:
            responses = outer

            @staticmethod
            def with_options(**_kwargs):
                return _Client

        return _Client


class _MessagesDouble:
    """OpenCode Anthropic-dialect HTTP double for the Qwen leg.

    Substituted at the *HTTP* seam, not above the adapter, so every translation
    the adapter performs is exercised on the way in and on the way out.
    """

    def __init__(self, answer: str | Exception, *, as_text: bool = False) -> None:
        self.answer = answer
        self.as_text = as_text
        self.requests: list[dict] = []

    def __call__(self, url, body, headers, timeout):
        self.requests.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.as_text:
            content = [{"type": "text", "text": self.answer}]
        else:
            try:
                block_input = json.loads(self.answer)
            except ValueError:
                # A model that emitted un-parseable text rather than a tool call.
                content = [{"type": "text", "text": self.answer}]
            else:
                content = [
                    {"type": "tool_use", "name": "probe", "input": block_input}
                ]
        return {
            "id": "msg_1",
            "model": body["model"],
            "content": content,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


def _muse(answer, log=None) -> OpenCodeResponsesProvider:
    double = _ResponsesDouble(answer)
    provider = OpenCodeResponsesProvider(
        base_url=BASE_URL,
        api_key="oc-test-key",
        model=MUSE,
        reasoning_effort="high",
        timeout_sec=60.0,
        max_output_tokens=2048,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=1.0,
        log=log or (lambda _m: None),
        client_factory=lambda **_k: double.as_client(),
    )
    provider.transport_double = double
    return provider


def _qwen(answer, log=None, *, as_text: bool = False) -> OpenCodeMessagesProvider:
    double = _MessagesDouble(answer, as_text=as_text)
    provider = OpenCodeMessagesProvider(
        base_url=BASE_URL,
        api_key="oc-test-key",
        model=QWEN,
        timeout_sec=60.0,
        max_output_tokens=2048,
        context_budget_tokens=32768,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=1.0,
        log=log or (lambda _m: None),
        client_factory=lambda **kwargs: _OpenCodeMessagesClient(
            api_key=str(kwargs.get("api_key") or "oc-test-key"),
            timeout=float(kwargs.get("timeout") or 60.0),
            base_url=BASE_URL,
            transport=double,
        ),
    )
    provider.transport_double = double
    return provider


def _luna(answer, log=None, *, service_tier: str = "flex") -> RemoteAPIProvider:
    double = _ResponsesDouble(answer)
    provider = RemoteAPIProvider(
        api_key="sk-test-openai",
        base_url="",
        primary_model=LUNA,
        fallback_models=[],
        analytics_model=LUNA,
        reasoning_effort="low",
        timeout_sec=60.0,
        max_output_tokens=2048,
        prompt_cache_enable=False,
        prompt_cache_key="",
        prompt_cache_retention="",
        service_tier=service_tier,
        flex_unavailable_retry_enable=False,
        flex_unavailable_max_retries=0,
        flex_unavailable_cooldown_sec=0.0,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=1.0,
        log=log or (lambda _m: None),
        client_factory=lambda **_k: double.as_client(),
    )
    provider.transport_double = double
    return provider


def _routed(
    *,
    muse_answer=VALID_ANSWER,
    qwen_answer=VALID_ANSWER,
    luna_answer=VALID_ANSWER,
    policy: OpenCodeRoutingPolicy | None = None,
    log=None,
    qwen_as_text: bool = False,
) -> OpenCodeRoutedProvider:
    lines: list[str] = []
    sink = log if log is not None else lines.append
    routed = OpenCodeRoutedProvider(
        muse=_muse(muse_answer, sink),
        qwen=_qwen(qwen_answer, sink, as_text=qwen_as_text),
        fallback=_luna(luna_answer, sink),
        policy=policy or OpenCodeRoutingPolicy(),
        log=sink,
        fallback_service_tier="flex",
    )
    routed.log_lines = lines
    return routed


def _call(routed: OpenCodeRoutedProvider, **metadata):
    meta = {"request_id": "req-1", "workload_mode": LIVE_FORWARD}
    meta.update(metadata)
    return routed.generate_structured(
        role="analyst",
        system_prompt="Return JSON.",
        evidence={"candidates": [{"candidate_index": 0, "rule_score": 5.0}]},
        response_schema=_DecisionProbe,
        request_metadata=meta,
    )


# ---------------------------------------------------------------------------


class ProviderSelectionTests(unittest.TestCase):
    """Requirement 1: ``opencode`` is a selectable provider mode."""

    def test_opencode_resolves_as_its_own_selection(self) -> None:
        self.assertEqual(resolve_provider_select("opencode", None), (PROVIDER_SELECT_OPENCODE, ""))
        self.assertIn(PROVIDER_SELECT_OPENCODE, po3_env.PROVIDER_SELECT_VALUES)

    def test_opencode_is_its_own_provider_mode(self) -> None:
        cfg = _config()
        self.assertEqual(cfg.provider_select, PROVIDER_SELECT_OPENCODE)
        self.assertEqual(cfg.provider_mode, PROVIDER_MODE_OPENCODE)
        self.assertTrue(cfg.is_opencode_provider)
        # OpenAI-only behaviour (Responses defaults, Batch, prompt caching) keys
        # off use_remote_api.  OpenCode must never satisfy it even though its
        # fallback leg is the OpenAI transport.
        self.assertFalse(cfg.use_remote_api)
        self.assertFalse(cfg.is_openrouter_provider)
        self.assertFalse(cfg.is_local_provider)

    def test_defaults_pin_the_documented_models_and_endpoint(self) -> None:
        cfg = _config()
        self.assertEqual(cfg.opencode_muse_model, MUSE)
        self.assertEqual(cfg.opencode_qwen_model, QWEN)
        self.assertEqual(cfg.opencode_base_url, BASE_URL)
        self.assertEqual(cfg.model, MUSE)
        self.assertFalse(cfg.opencode_call_directing)

    def test_fallback_defaults_are_luna_low_flex(self) -> None:
        cfg = _config()
        self.assertEqual(cfg.opencode_fallback_model, LUNA)
        self.assertEqual(cfg.opencode_fallback_reasoning_effort, "low")
        self.assertEqual(cfg.opencode_fallback_service_tier, "flex")

    def test_muse_reasoning_defaults_to_the_strongest_standard_level(self) -> None:
        self.assertEqual(_config().opencode_muse_reasoning_effort, "high")

    def test_missing_opencode_key_fails_closed(self) -> None:
        cfg = AIGateRuntimeConfig.from_env(
            {"AI_PROVIDER_SELECT": "opencode", "OPENAI_API_KEY": "sk"}
        )
        self.assertFalse(cfg.provider_config_valid)
        self.assertIn("OPENCODE_GO_API_KEY=missing", cfg.provider_config_errors)

    def test_missing_fallback_credential_fails_closed(self) -> None:
        """The Luna leg is declared, not optional.

        Every OpenCode route is defined as "OpenCode, then Luna", so a mode whose
        fallback credential is absent is a mode whose declared fallback cannot
        exist.  It must fail at configuration time, not at the first transport
        error with a live request already waiting.
        """

        cfg = AIGateRuntimeConfig.from_env(
            {"AI_PROVIDER_SELECT": "opencode", "OPENCODE_GO_API_KEY": "oc"}
        )
        self.assertFalse(cfg.provider_config_valid)
        self.assertIn("OPENAI_API_KEY=missing_opencode_fallback", cfg.provider_config_errors)

    def test_invalid_base_url_fails_closed(self) -> None:
        cfg = _config(OPENCODE_GO_BASE_URL="not-a-url")
        self.assertFalse(cfg.provider_config_valid)
        self.assertIn("OPENCODE_GO_BASE_URL=invalid", cfg.provider_config_errors)

    def test_neither_secret_is_ever_rendered_into_a_log_line(self) -> None:
        cfg = _config(OPENCODE_GO_API_KEY="oc-super-secret", OPENAI_API_KEY="sk-super-secret")
        rendered = json.dumps(cfg.safe_log_dict())
        self.assertNotIn("oc-super-secret", rendered)
        self.assertNotIn("sk-super-secret", rendered)
        self.assertTrue(cfg.safe_log_dict()["opencode_api_key_configured"])

    def test_secret_map_gives_opencode_both_credentials_it_actually_uses(self) -> None:
        owned = po3_env.PROVIDER_SECRET_KEYS[PROVIDER_SELECT_OPENCODE]
        self.assertIn("OPENCODE_GO_API_KEY", owned)
        self.assertIn("OPENAI_API_KEY", owned)
        self.assertIs(ai_gate._PROVIDER_SECRET_KEYS, po3_env.PROVIDER_SECRET_KEYS)

    def test_a_shared_credential_is_never_stripped_from_a_selection_that_owns_it(self) -> None:
        """The regression the shared OPENAI_API_KEY would otherwise cause.

        ``excluded_secret_keys`` used to exclude every key listed under any other
        owner.  Once ``opencode`` also owns ``OPENAI_API_KEY``, that would strip
        the OpenAI credential from ``openai_remote`` itself and break a
        selection this change never touched.
        """

        for selection in po3_env.PROVIDER_SELECT_VALUES:
            with self.subTest(selection=selection):
                excluded = set(po3_env.excluded_secret_keys(selection))
                for key in po3_env.PROVIDER_SECRET_KEYS[selection]:
                    self.assertNotIn(key, excluded)
        self.assertNotIn("OPENAI_API_KEY", po3_env.excluded_secret_keys(PROVIDER_SELECT_OPENAI))
        self.assertIn("OPENCODE_GO_API_KEY", po3_env.excluded_secret_keys(PROVIDER_SELECT_OPENAI))
        self.assertIn("OPENROUTER_API_KEY", po3_env.excluded_secret_keys(PROVIDER_SELECT_OPENCODE))
        self.assertIn("LOCAL_AI_API_KEY", po3_env.excluded_secret_keys(PROVIDER_SELECT_OPENCODE))

    def test_opencode_traffic_is_billed_not_recorded_as_free(self) -> None:
        """A paid transport priced at $0.00 by construction is the OpenRouter bug."""

        self.assertIn(PROVIDER_MODE_OPENCODE, usage.BILLED_PROVIDER_MODES)
        self.assertNotEqual(
            usage._pricing_status(MUSE, PROVIDER_MODE_OPENCODE),
            usage.PRICING_STATUS_NOT_BILLED,
        )


class ImportanceClassificationTests(unittest.TestCase):
    """Requirements 2-5, at the policy level: no model call decides a route."""

    def _decide(self, policy, **meta):
        evidence = {"candidates": meta.pop("candidates", [{"rule_score": 5.0}])}
        metadata = {"workload_mode": LIVE_FORWARD}
        metadata.update(meta)
        return classify_importance(policy=policy, request_metadata=metadata, evidence=evidence)

    def test_directing_off_classifies_everything_normal(self) -> None:
        decision = self._decide(
            OpenCodeRoutingPolicy(call_directing=False), candidates=[{"rule_score": 9.9}]
        )
        self.assertEqual(decision.importance, IMPORTANCE_NORMAL)
        self.assertEqual(decision.reason, "call_directing_disabled")

    def test_live_request_below_the_critical_threshold_is_important(self) -> None:
        decision = self._decide(
            OpenCodeRoutingPolicy(call_directing=True), candidates=[{"rule_score": 6.0}]
        )
        self.assertEqual(decision.importance, IMPORTANCE_IMPORTANT)

    def test_live_request_at_the_critical_threshold_is_critical(self) -> None:
        policy = OpenCodeRoutingPolicy(call_directing=True, critical_rule_score=8.5)
        self.assertEqual(
            self._decide(policy, candidates=[{"rule_score": 8.5}]).importance,
            IMPORTANCE_CRITICAL,
        )
        self.assertEqual(
            self._decide(policy, candidates=[{"rule_score": 8.4999}]).importance,
            IMPORTANCE_IMPORTANT,
        )

    def test_the_highest_scoring_candidate_decides_the_request(self) -> None:
        decision = self._decide(
            OpenCodeRoutingPolicy(call_directing=True),
            candidates=[{"rule_score": 1.0}, {"rule_score": 9.2}, {"rule_score": 2.0}],
        )
        self.assertEqual(decision.importance, IMPORTANCE_CRITICAL)
        self.assertEqual(decision.max_rule_score, 9.2)
        self.assertEqual(decision.candidate_count, 3)

    def test_non_live_workloads_are_never_escalated(self) -> None:
        policy = OpenCodeRoutingPolicy(call_directing=True)
        for mode in ("backtest", "replay", "research", "analytics"):
            with self.subTest(workload_mode=mode):
                decision = self._decide(
                    policy, workload_mode=mode, candidates=[{"rule_score": 9.9}]
                )
                self.assertEqual(decision.importance, IMPORTANCE_NORMAL)
                self.assertIn("workload_mode_not_live", decision.reason)

    def test_a_non_trading_shadow_repeat_is_never_escalated(self) -> None:
        decision = self._decide(
            OpenCodeRoutingPolicy(call_directing=True),
            non_trading_shadow=True,
            candidates=[{"rule_score": 9.9}],
        )
        self.assertEqual(decision.importance, IMPORTANCE_NORMAL)
        self.assertEqual(decision.reason, "non_trading_shadow_repeat")

    def test_a_live_request_with_no_published_score_is_not_downgraded(self) -> None:
        """Absent evidence must fail toward the stronger route, not the cheaper.

        A structurally unpublished field read as ``0.00`` is the defect class
        recorded in section 4j of the project notes; here it would silently send
        every live request to the weakest model.
        """

        decision = self._decide(
            OpenCodeRoutingPolicy(call_directing=True), candidates=[{"candidate_index": 0}]
        )
        self.assertEqual(decision.importance, IMPORTANCE_IMPORTANT)
        self.assertEqual(decision.reason, "live_rule_score_unavailable")
        self.assertEqual(decision.max_rule_score, -1.0)

    def test_forced_importance_is_configurable_and_reported(self) -> None:
        policy = OpenCodeRoutingPolicy(call_directing=True, forced_importance=IMPORTANCE_CRITICAL)
        decision = self._decide(policy, candidates=[{"rule_score": 0.1}])
        self.assertEqual(decision.importance, IMPORTANCE_CRITICAL)
        self.assertEqual(decision.reason, "forced_by_configuration")

    def test_policy_reads_from_env_with_safe_defaults(self) -> None:
        warnings: list[str] = []
        policy = OpenCodeRoutingPolicy.from_env(
            {"OPENCODE_CALL_DIRECTING": "true", "OPENCODE_CRITICAL_RULE_SCORE": "7.25"},
            warnings,
        )
        self.assertTrue(policy.call_directing)
        self.assertEqual(policy.critical_rule_score, 7.25)
        self.assertEqual(warnings, [])
        bad = OpenCodeRoutingPolicy.from_env(
            {"OPENCODE_CALL_DIRECTING": "maybe", "OPENCODE_IMPORTANCE_FORCE": "urgent"}, warnings
        )
        self.assertFalse(bad.call_directing)
        self.assertEqual(bad.forced_importance, "")
        self.assertIn("OPENCODE_CALL_DIRECTING=invalid_bool", warnings)
        self.assertIn("OPENCODE_IMPORTANCE_FORCE=invalid", warnings)

    def test_live_forward_literal_matches_the_authoritative_constant(self) -> None:
        """The classifier keeps its own copy so it stays dependency free.

        A copy that drifts would classify every live request as "not live" and
        silently route the whole live funnel to the cheapest model, so the two
        are pinned together here.
        """

        from architecture_contracts import LIVE_FORWARD as contract_live_forward

        self.assertEqual(LIVE_FORWARD, contract_live_forward)


class RoutedDispatchTests(unittest.TestCase):
    """Requirements 2-5 end to end, through the real providers and validators."""

    def test_directing_off_routes_to_muse(self) -> None:
        routed = _routed(policy=OpenCodeRoutingPolicy(call_directing=False))
        result = _call(routed)
        self.assertEqual(result.actual_model, MUSE)
        self.assertEqual(result.provider_mode, PROVIDER_MODE_OPENCODE)
        self.assertEqual(result.provider_id, "opencode_go_responses")
        self.assertEqual(len(routed._qwen.transport_double.requests), 0)
        self.assertEqual(len(routed._fallback.transport_double.calls), 0)

    def test_directing_on_normal_routes_to_muse(self) -> None:
        routed = _routed(
            policy=OpenCodeRoutingPolicy(call_directing=True),
            # A non-live workload classifies normal.
        )
        result = _call(routed, workload_mode="backtest")
        self.assertEqual(result.actual_model, MUSE)
        self.assertEqual(len(routed._qwen.transport_double.requests), 0)
        self.assertEqual(len(routed._fallback.transport_double.calls), 0)

    def test_important_routes_to_qwen_through_the_messages_dialect(self) -> None:
        routed = _routed(policy=OpenCodeRoutingPolicy(call_directing=True))
        result = _call(routed)  # live, rule_score 5.0 -> important
        self.assertEqual(result.actual_model, QWEN)
        self.assertEqual(result.provider_mode, PROVIDER_MODE_OPENCODE)
        self.assertEqual(result.provider_id, "opencode_go_messages")
        self.assertEqual(len(routed._muse.transport_double.calls), 0)
        self.assertEqual(len(routed._fallback.transport_double.calls), 0)
        sent = routed._qwen.transport_double.requests[0]
        self.assertTrue(sent["url"].endswith("/messages"))

    def test_critical_route_never_touches_opencode(self) -> None:
        routed = _routed(policy=OpenCodeRoutingPolicy(call_directing=True))
        result = routed.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"candidates": [{"candidate_index": 0, "rule_score": 9.4}]},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "req-critical", "workload_mode": LIVE_FORWARD},
        )
        self.assertEqual(result.actual_model, LUNA)
        self.assertEqual(result.provider_mode, PROVIDER_MODE_REMOTE)
        self.assertEqual(len(routed._muse.transport_double.calls), 0)
        self.assertEqual(len(routed._qwen.transport_double.requests), 0)

    def test_the_luna_leg_is_asked_at_the_configured_service_tier(self) -> None:
        """The gate supplies ``service_tier="auto"`` for every non-OpenAI mode.

        Without the routed provider re-stating the tier, the fallback would
        silently run standard-rate no matter how it was configured.
        """

        routed = _routed(policy=OpenCodeRoutingPolicy(call_directing=True))
        routed.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"candidates": [{"rule_score": 9.9}]},
            response_schema=_DecisionProbe,
            request_metadata={
                "request_id": "r",
                "workload_mode": LIVE_FORWARD,
                "service_tier": "auto",
            },
        )
        self.assertEqual(routed._fallback.transport_double.calls[0]["service_tier"], "flex")

    def test_identity_follows_the_default_route_so_the_decision_cache_still_matches(self) -> None:
        routed = _routed()
        identity = routed.identity("analyst")
        self.assertEqual(identity["provider_mode"], PROVIDER_MODE_OPENCODE)
        self.assertEqual(identity["provider_id"], "opencode_go_responses")
        self.assertEqual(identity["configured_model_ids"], [MUSE])
        self.assertEqual(identity["opencode_legs"]["important"], QWEN)
        self.assertEqual(identity["opencode_legs"]["critical"], LUNA)
        self.assertTrue(identity["opencode_routing"]["routing_policy_version"])
        result = _call(routed)
        # Default route: what answered and what identity claims are the same
        # transport, so a cached row written by this call validates against it.
        self.assertEqual(result.provider_mode, identity["provider_mode"])
        self.assertEqual(result.provider_id, identity["provider_id"])


class FallbackContractTests(unittest.TestCase):
    """Requirements 6-8: every OpenCode failure class ends at a validated Luna."""

    def test_malformed_muse_json_falls_back_to_luna(self) -> None:
        routed = _routed(muse_answer=MALFORMED_ANSWER)
        result = _call(routed)
        self.assertEqual(result.actual_model, LUNA)
        self.assertEqual(result.provider_mode, PROVIDER_MODE_REMOTE)
        self.assertEqual(result.parsed.decision_state, "ABSTAIN")

    def test_malformed_qwen_json_falls_back_to_luna(self) -> None:
        routed = _routed(
            qwen_answer=MALFORMED_ANSWER,
            qwen_as_text=True,
            policy=OpenCodeRoutingPolicy(call_directing=True),
        )
        result = _call(routed)
        self.assertEqual(result.actual_model, LUNA)
        self.assertEqual(result.provider_mode, PROVIDER_MODE_REMOTE)

    def test_schema_invalid_opencode_answer_falls_back_to_luna(self) -> None:
        """Valid JSON, invalid decision: it must not reach a consumer."""

        routed = _routed(muse_answer=SCHEMA_INVALID_ANSWER)
        result = _call(routed)
        self.assertEqual(result.actual_model, LUNA)
        # And the fallback answer is itself validated, not merely returned.
        self.assertIsInstance(result.parsed, _DecisionProbe)
        self.assertEqual(result.parsed.llm_quality_score, 6.5)

    def test_schema_invalid_qwen_answer_falls_back_to_luna(self) -> None:
        routed = _routed(
            qwen_answer=SCHEMA_INVALID_ANSWER,
            policy=OpenCodeRoutingPolicy(call_directing=True),
        )
        result = _call(routed)
        self.assertEqual(result.actual_model, LUNA)
        self.assertIsInstance(result.parsed, _DecisionProbe)

    def test_opencode_timeout_falls_back_to_luna(self) -> None:
        routed = _routed(muse_answer=TimeoutError("request timed out"))
        result = _call(routed)
        self.assertEqual(result.actual_model, LUNA)

    def test_opencode_api_error_falls_back_to_luna(self) -> None:
        failure = OpenCodeTransportError(
            "opencode_messages_http_500:upstream failure", status_code=500
        )
        routed = _routed(muse_answer=failure)
        self.assertEqual(_call(routed).actual_model, LUNA)

    def test_qwen_transport_failure_falls_back_to_luna(self) -> None:
        routed = _routed(
            qwen_answer=OpenCodeTransportError(
                "opencode_messages_http_502:bad gateway", status_code=502
            ),
            policy=OpenCodeRoutingPolicy(call_directing=True),
        )
        self.assertEqual(_call(routed).actual_model, LUNA)

    def test_an_unusable_empty_response_falls_back_to_luna(self) -> None:
        routed = _routed(
            qwen_answer="",
            qwen_as_text=True,
            policy=OpenCodeRoutingPolicy(call_directing=True),
        )
        self.assertEqual(_call(routed).actual_model, LUNA)

    def test_a_bad_opencode_answer_is_not_re_asked_of_the_same_model(self) -> None:
        """One OpenCode attempt, then Luna -- never a second billed Muse call."""

        routed = _routed(muse_answer=SCHEMA_INVALID_ANSWER)
        _call(routed)
        self.assertEqual(len(routed._muse.transport_double.calls), 1)
        self.assertEqual(len(routed._fallback.transport_double.calls), 1)

    def test_a_bad_qwen_answer_is_not_re_asked_of_the_same_model(self) -> None:
        routed = _routed(
            qwen_answer=SCHEMA_INVALID_ANSWER,
            policy=OpenCodeRoutingPolicy(call_directing=True),
        )
        _call(routed)
        self.assertEqual(len(routed._qwen.transport_double.requests), 1)

    def test_an_unmapped_importance_takes_the_strongest_route_not_the_cheapest(self) -> None:
        """Fail-safe direction if a fourth level is ever added to the policy."""

        routed = _routed()
        leg, is_opencode = routed._leg_for("some_future_level")
        self.assertIs(leg, routed._fallback)
        self.assertFalse(is_opencode)

    def test_a_failing_luna_on_the_critical_route_raises_instead_of_looping(self) -> None:
        routed = _routed(
            luna_answer=RuntimeError("luna down"),
            policy=OpenCodeRoutingPolicy(call_directing=True, forced_importance=IMPORTANCE_CRITICAL),
        )
        with self.assertRaises(ProviderCallError):
            _call(routed)
        self.assertEqual(len(routed._muse.transport_double.calls), 0)
        self.assertEqual(len(routed._qwen.transport_double.requests), 0)

    def test_the_fallback_is_skipped_when_the_deadline_cannot_cover_it(self) -> None:
        """The absolute deadline is never extended to make room for a fallback."""

        class _Deadline:
            policy = SimpleNamespace(min_attempt_ms=5000, python_deadline_ms=1000)

            @staticmethod
            def can_start_attempt() -> bool:
                return False

            @staticmethod
            def remaining_ms() -> int:
                return 0

            @staticmethod
            def expired() -> bool:
                return True

            @staticmethod
            def as_log_fields() -> str:
                return ""

            @staticmethod
            def provider_timeout_sec(configured: float) -> float:
                return configured

        routed = _routed(muse_answer=MALFORMED_ANSWER)
        with self.assertRaises(ProviderCallError):
            _call(routed, deadline=_Deadline())
        self.assertEqual(len(routed._fallback.transport_double.calls), 0)

    def test_routing_and_fallback_telemetry_is_explicit(self) -> None:
        routed = _routed(muse_answer=MALFORMED_ANSWER)
        _call(routed)
        text = "\n".join(routed.log_lines)
        self.assertIn("[opencode_routing]", text)
        self.assertIn("importance=normal", text)
        self.assertIn("routing_reason=call_directing_disabled", text)
        self.assertIn("routed_model=" + MUSE, text)
        self.assertIn("[opencode_fallback]", text)
        self.assertIn("action=fallback_once", text)
        self.assertIn("fallback_reason=", text)
        self.assertIn("[opencode_fallback_completed]", text)
        self.assertIn("validation=passed", text)
        self.assertIn("latency_sec=", text)
        # Never a credential, on any line.
        self.assertNotIn("oc-test-key", text)
        self.assertNotIn("sk-test-openai", text)

    def test_a_successful_opencode_call_reports_no_fallback(self) -> None:
        routed = _routed()
        _call(routed)
        text = "\n".join(routed.log_lines)
        self.assertIn("[opencode_call_completed]", text)
        self.assertIn("fallback_used=false", text)
        self.assertNotIn("[opencode_fallback]", text)


class MessagesDialectAdapterTests(unittest.TestCase):
    """The ``responses`` vs ``messages`` difference lives in one adapter."""

    def _client(self, double: _MessagesDouble) -> _OpenCodeMessagesClient:
        return _OpenCodeMessagesClient(
            api_key="oc-test-key", timeout=30.0, base_url=BASE_URL, transport=double
        )

    def test_system_turn_becomes_the_system_field(self) -> None:
        double = _MessagesDouble(VALID_ANSWER)
        self._client(double).chat.completions.create(
            model=QWEN,
            max_tokens=512,
            messages=[
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "USER"},
            ],
        )
        body = double.requests[0]["body"]
        self.assertEqual(body["system"], "SYS")
        self.assertEqual(body["messages"], [{"role": "user", "content": [{"type": "text", "text": "USER"}]}])
        self.assertNotIn("response_format", body)

    def test_strict_json_schema_becomes_the_single_declared_tool(self) -> None:
        double = _MessagesDouble(VALID_ANSWER)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        self._client(double).chat.completions.create(
            model=QWEN,
            max_tokens=512,
            messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": "x"}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "probe", "strict": True, "schema": schema},
            },
        )
        body = double.requests[0]["body"]
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tools"][0]["name"], "probe")
        self.assertEqual(body["tools"][0]["input_schema"], schema)
        # The obligation is carried in the system text, because the parameter
        # that would carry it is refused by the endpoint -- see the next test.
        self.assertIn("SYS", body["system"])
        self.assertIn("`probe`", body["system"])
        self.assertIn("MUST", body["system"])

    def test_tool_choice_is_never_sent_because_the_endpoint_refuses_it(self) -> None:
        """Measured against the live endpoint on 2026-09-09: a body carrying
        ``tool_choice`` -- ``{"type":"tool"}`` or ``{"type":"any"}`` -- is
        answered with HTTP 400 and an opaque ``{"model": ...}``.  Sending it does
        not make the leg strict, it makes every Qwen call fail."""

        double = _MessagesDouble(VALID_ANSWER)
        self._client(double).chat.completions.create(
            model=QWEN,
            max_tokens=512,
            messages=[{"role": "user", "content": "x"}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "probe", "strict": True, "schema": {"type": "object"}},
            },
        )
        self.assertNotIn("tool_choice", double.requests[0]["body"])

    def test_tool_use_output_becomes_chat_completion_content(self) -> None:
        double = _MessagesDouble(VALID_ANSWER)
        response = self._client(double).chat.completions.create(
            model=QWEN, max_tokens=512, messages=[{"role": "user", "content": "x"}]
        )
        self.assertEqual(response["model"], QWEN)
        self.assertEqual(json.loads(response["choices"][0]["message"]["content"])["llm_quality_score"], 6.5)
        self.assertEqual(response["usage"]["prompt_tokens"], 10)
        self.assertEqual(response["usage"]["completion_tokens"], 5)
        self.assertEqual(response["usage"]["total_tokens"], 15)

    def test_text_only_output_is_carried_through_for_validation_not_discarded(self) -> None:
        double = _MessagesDouble(VALID_ANSWER, as_text=True)
        response = self._client(double).chat.completions.create(
            model=QWEN, max_tokens=512, messages=[{"role": "user", "content": "x"}]
        )
        self.assertEqual(response["choices"][0]["message"]["content"], VALID_ANSWER)

    def test_an_unrepresentable_parameter_is_refused_before_any_http_request(self) -> None:
        """Never send a key this dialect has no meaning for -- and never drop one
        silently either, which would hide a configuration defect."""

        double = _MessagesDouble(VALID_ANSWER)
        with self.assertRaises(OpenCodeTransportError) as ctx:
            self._client(double).chat.completions.create(
                model=QWEN, max_tokens=512, messages=[], seed=42
            )
        self.assertIn("unsupported_request_parameter:seed", str(ctx.exception))
        self.assertEqual(double.requests, [])

    def test_credentials_and_dialect_version_travel_in_headers(self) -> None:
        double = _MessagesDouble(VALID_ANSWER)
        self._client(double).chat.completions.create(
            model=QWEN,
            max_tokens=512,
            messages=[{"role": "user", "content": "x"}],
            extra_headers={"X-PO3-Request-Id": "req-1"},
        )
        headers = double.requests[0]["headers"]
        self.assertEqual(headers["x-api-key"], "oc-test-key")
        self.assertEqual(headers["authorization"], "Bearer oc-test-key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["X-PO3-Request-Id"], "req-1")

    def test_the_two_headers_the_edge_makes_mandatory_are_always_present(self) -> None:
        """Measured against the live endpoint on 2026-09-09.  Without a
        ``user-agent`` other than urllib's default the edge answers HTTP 403
        ``error code: 1010``; without ``x-opencode-session`` the API answers HTTP
        400 ``MissingSessionID``.  Neither failure is reachable through a
        transport double, so both are pinned here explicitly."""

        double = _MessagesDouble(VALID_ANSWER)
        self._client(double).chat.completions.create(
            model=QWEN,
            max_tokens=512,
            messages=[{"role": "user", "content": "x"}],
            extra_headers={"X-PO3-Request-Id": "591813800_1786509126_GBPNZD_13183"},
        )
        headers = double.requests[0]["headers"]
        agent = headers.get("user-agent", "")
        self.assertTrue(agent)
        self.assertNotIn("urllib", agent.lower())
        # Derived from the request identity: one request, one session, and a
        # replay of the same request names the same session again.
        self.assertEqual(
            headers["x-opencode-session"], "po3-591813800_1786509126_GBPNZD_13183"
        )

    def test_a_session_is_named_even_when_the_request_id_is_absent(self) -> None:
        double = _MessagesDouble(VALID_ANSWER)
        self._client(double).chat.completions.create(
            model=QWEN, max_tokens=512, messages=[{"role": "user", "content": "x"}]
        )
        self.assertTrue(double.requests[0]["headers"]["x-opencode-session"])

    def test_transport_errors_carry_what_the_shared_classifiers_read(self) -> None:
        """An OpenCode 429 must reuse the admission-retry rules, not a second
        divergent definition of "was never admitted"."""

        error = OpenCodeTransportError(
            "rate_limit_exceeded: try again later",
            status_code=429,
            body={"error": {"code": "rate_limit_exceeded"}},
            headers={"retry-after": "3"},
        )
        self.assertEqual(RemoteAPIProvider._status_code(error), 429)
        self.assertTrue(RemoteAPIProvider._admission_rejected(error))
        self.assertEqual(RemoteAPIProvider._retry_after_sec(error), 3.0)

    def test_with_options_narrows_the_timeout_without_losing_the_transport(self) -> None:
        double = _MessagesDouble(VALID_ANSWER)
        client = self._client(double).with_options(timeout=5.0, max_retries=0)
        client.chat.completions.create(
            model=QWEN, max_tokens=512, messages=[{"role": "user", "content": "x"}]
        )
        self.assertEqual(double.requests[0]["timeout"], 5.0)


class OpenCodeWireContractTests(unittest.TestCase):
    def test_muse_never_sends_openai_account_only_parameters(self) -> None:
        """``service_tier`` reaches this transport as ``"auto"`` from the gate,
        and this endpoint publishes no such parameter."""

        muse = _muse(VALID_ANSWER)
        muse.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r", "service_tier": "auto"},
        )
        sent = muse.transport_double.calls[0]
        self.assertNotIn("service_tier", sent)
        self.assertNotIn("prompt_cache_key", sent)
        self.assertNotIn("extra_body", sent)

    def test_muse_reserves_reasoning_tokens_on_top_of_the_schema_budget(self) -> None:
        """Measured 2026-09-09, replaying a real archived live request:
        ``max_output_tokens`` bounds reasoning AND content together here, so Muse
        at effort=high spent the whole 25000-token budget reasoning and returned
        ``status=incomplete incomplete_reason=max_output_tokens`` with an empty
        message on every analyst call, while the smaller critic and adjudicator
        roles completed on the same budget.

        The reserve is added on top, never taken out of the caller's budget --
        the same shape this repo already uses for OPENROUTER_REASONING_TOKEN_RESERVE.
        """

        muse = _muse(VALID_ANSWER)
        muse.reasoning_token_reserve = 24000
        muse.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r"},
        )
        sent = muse.transport_double.calls[0]["max_output_tokens"]

        bare = _muse(VALID_ANSWER)
        bare.reasoning_token_reserve = 0
        bare.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r"},
        )
        schema_budget = bare.transport_double.calls[0]["max_output_tokens"]
        self.assertEqual(sent, schema_budget + 24000)

    def test_muse_never_asks_for_silent_context_truncation(self) -> None:
        """Measured 2026-09-09 against the live endpoint with the real
        production body: ``truncation="auto"`` -- the value the shared Responses
        loop sends -- is answered with HTTP 400 ``Only `disabled` is
        supported``, so every Muse call failed and fell back to Luna.  The same
        body with ``disabled`` returns HTTP 200.

        Restated rather than dropped: ``auto`` would let the upstream silently
        drop evidence out of an over-long context and answer anyway, which is a
        decision resting on evidence Python never agreed to omit."""

        muse = _muse(VALID_ANSWER)
        muse.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r"},
        )
        self.assertEqual(muse.transport_double.calls[0]["truncation"], "disabled")

    def test_muse_always_names_a_session_because_the_api_requires_one(self) -> None:
        """Measured 2026-09-09: ``/responses`` refuses a request with no
        ``x-opencode-session`` header -- HTTP 400 ``MissingSessionID`` -- before
        the model is reached.  The base loop only builds ``extra_headers`` when a
        deadline is present, so a deadline-free call must still carry it."""

        muse = _muse(VALID_ANSWER)
        muse.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "591813800_1786509126_GBPNZD_13183"},
        )
        headers = muse.transport_double.calls[0]["extra_headers"]
        self.assertEqual(
            headers["x-opencode-session"], "po3-591813800_1786509126_GBPNZD_13183"
        )

    def test_muse_asks_for_the_configured_reasoning_level(self) -> None:
        muse = _muse(VALID_ANSWER)
        muse.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r"},
        )
        self.assertEqual(muse.transport_double.calls[0]["reasoning"], {"effort": "high"})

    def test_muse_still_sends_the_strict_schema(self) -> None:
        muse = _muse(VALID_ANSWER)
        muse.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r"},
        )
        text_format = muse.transport_double.calls[0]["text"]["format"]
        self.assertEqual(text_format["type"], "json_schema")
        self.assertTrue(text_format["strict"])

    def test_opencode_legs_never_repair_a_bad_answer_in_place(self) -> None:
        self.assertEqual(OpenCodeResponsesProvider.schema_repair_attempts, 0)
        self.assertEqual(_qwen(VALID_ANSWER).max_retries, 0)

    def test_opencode_legs_never_resubmit_an_ambiguous_failure(self) -> None:
        """A paid endpoint: a resubmission after a timeout can be a second
        charge racing a first call that was in fact admitted."""

        self.assertFalse(OpenCodeResponsesProvider.resubmit_ambiguous_transport_failures)
        self.assertFalse(OpenCodeMessagesProvider.resubmit_ambiguous_transport_failures)

    def test_qwen_health_is_configuration_not_a_model_listing(self) -> None:
        """The inherited ``/models`` GET would report a healthy transport as
        unhealthy and keep the circuit breaker permanently open, because breaker
        recovery re-runs exactly this check."""

        health = _qwen(VALID_ANSWER).healthcheck()
        self.assertTrue(health.healthy)
        self.assertEqual(health.provider_mode, PROVIDER_MODE_OPENCODE)
        self.assertEqual(health.model_id, QWEN)

    def test_thinking_is_only_sent_when_it_is_configured(self) -> None:
        off = _qwen(VALID_ANSWER)
        self.assertEqual(off._wire_extra_body(), {})
        self.assertEqual(off._wire_max_tokens(1000), 1000)
        on = OpenCodeMessagesProvider(
            base_url=BASE_URL,
            api_key="oc",
            model=QWEN,
            timeout_sec=30.0,
            max_output_tokens=2048,
            context_budget_tokens=4096,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=1.0,
            log=lambda _m: None,
            enable_thinking=True,
            thinking_budget_tokens=4000,
        )
        self.assertEqual(
            on._wire_extra_body(), {"thinking": {"type": "enabled", "budget_tokens": 4000}}
        )
        self.assertEqual(on._wire_max_tokens(1000), 5000)

    def test_the_gate_builds_the_routed_provider_for_this_selection(self) -> None:
        import os

        previous = {k: os.environ.get(k) for k in ("OPENCODE_GO_API_KEY", "OPENAI_API_KEY")}
        os.environ["OPENCODE_GO_API_KEY"] = "oc-test-key"
        os.environ["OPENAI_API_KEY"] = "sk-test-openai"
        try:
            provider = ai_gate._build_ai_provider(_config())
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertIsInstance(provider, OpenCodeRoutedProvider)
        self.assertEqual(provider.provider_mode, PROVIDER_MODE_OPENCODE)
        self.assertIsInstance(provider._muse, OpenCodeResponsesProvider)
        self.assertIsInstance(provider._qwen, OpenCodeMessagesProvider)
        # The fallback is the system's existing OpenAI integration, configured --
        # not a second implementation of it.
        self.assertIsInstance(provider._fallback, RemoteAPIProvider)
        self.assertNotIsInstance(provider._fallback, OpenCodeResponsesProvider)
        self.assertEqual(provider._fallback.model_for_role("analyst"), LUNA)
        self.assertEqual(provider._fallback.reasoning_effort, "low")
        self.assertEqual(provider._fallback.service_tier, "flex")


class UnchangedBehaviourTests(unittest.TestCase):
    """Requirement 9: nothing that existed before this change behaves differently."""

    def test_existing_openai_and_openrouter_wire_shapes_are_unchanged(self) -> None:
        luna = _luna(VALID_ANSWER, service_tier="flex")
        luna.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r"},
        )
        sent = luna.transport_double.calls[0]
        # The official transport still sends its account-level parameters; only
        # the OpenCode subclass strips them.
        self.assertEqual(sent["service_tier"], "flex")
        self.assertEqual(sent["reasoning"], {"effort": "low"})
        # And it keeps its own truncation posture: the OpenCode-only rewrite to
        # "disabled" must not leak onto api.openai.com traffic.
        self.assertEqual(sent["truncation"], "auto")
        self.assertNotIn("x-opencode-session", sent.get("extra_headers") or {})
        self.assertEqual(luna.provider_mode, PROVIDER_MODE_REMOTE)
        self.assertEqual(luna.provider_id, "openai_remote_api")

        router = OpenRouterProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key="k",
            analyst_model="a",
            critic_model="a",
            adjudicator_model="a",
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=900.0,
            max_retries=2,
            max_output_tokens=25000,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=True,
            reasoning_effort="high",
            reasoning_token_reserve=24000,
            require_json_schema=True,
            require_structured_provider=True,
            allowed_providers=(),
            parallelism=3,
            context_budget_tokens=131072,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=60.0,
            log=lambda _m: None,
        )
        self.assertEqual(router.provider_mode, PROVIDER_MODE_OPENROUTER)
        self.assertEqual(router._wire_extra_body()["reasoning"], {"enabled": True, "effort": "high"})
        self.assertEqual(router._wire_max_tokens(10800), 34800)

    def test_the_official_transport_keeps_its_single_schema_repair(self) -> None:
        """``schema_repair_attempts`` exists so OpenCode can set 0.  The default
        must still be the transport's previous behaviour."""

        self.assertEqual(RemoteAPIProvider.schema_repair_attempts, 1)
        calls: list[dict] = []
        answers = [SCHEMA_INVALID_ANSWER, VALID_ANSWER]

        class _Responses:
            def create(self, **kwargs):
                calls.append(dict(kwargs))
                return SimpleNamespace(
                    model=kwargs["model"],
                    output_text=answers[min(len(calls) - 1, len(answers) - 1)],
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
                )

        endpoint = _Responses()
        client = SimpleNamespace(
            responses=endpoint,
            with_options=lambda **_kwargs: SimpleNamespace(responses=endpoint),
        )
        provider = RemoteAPIProvider(
            api_key="k",
            base_url="",
            primary_model="m",
            fallback_models=[],
            analytics_model="m",
            reasoning_effort="low",
            timeout_sec=30.0,
            max_output_tokens=256,
            prompt_cache_enable=False,
            prompt_cache_key="",
            prompt_cache_retention="",
            service_tier="auto",
            flex_unavailable_retry_enable=False,
            flex_unavailable_max_retries=0,
            flex_unavailable_cooldown_sec=0.0,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=1.0,
            log=lambda _m: None,
            client_factory=lambda **_k: client,
        )
        result = provider.generate_structured(
            role="analyst",
            system_prompt="Return JSON.",
            evidence={"x": 1},
            response_schema=_DecisionProbe,
            request_metadata={"request_id": "r"},
        )
        self.assertEqual(len(calls), 2, "the one existing schema repair was lost")
        self.assertEqual(result.schema_retry_count, 1)

    def test_the_other_selections_still_resolve_exactly_as_before(self) -> None:
        self.assertEqual(resolve_provider_select(None, "true"), (PROVIDER_SELECT_OPENAI, ""))
        self.assertEqual(resolve_provider_select(None, "false"), (PROVIDER_SELECT_LOCAL, ""))
        self.assertEqual(resolve_provider_select("openrouter", None), (PROVIDER_SELECT_OPENROUTER, ""))
        self.assertEqual(resolve_provider_select("gemini", None), (None, "AI_PROVIDER_SELECT=invalid"))

    def test_no_opencode_setting_leaks_into_another_selection(self) -> None:
        cfg = AIGateRuntimeConfig.from_env(
            {
                "AI_PROVIDER_SELECT": "openai_remote",
                "OPENAI_API_KEY": "sk",
                "OPENCODE_GO_API_KEY": "oc",
                "OPENCODE_CALL_DIRECTING": "true",
            }
        )
        self.assertTrue(cfg.provider_config_valid)
        self.assertEqual(cfg.provider_mode, PROVIDER_MODE_REMOTE)
        rendered = cfg.safe_log_dict()
        self.assertEqual(rendered["opencode_base_url"], "")
        self.assertFalse(rendered["opencode_api_key_configured"])
        self.assertFalse(rendered["opencode_call_directing"])

    def test_local_transport_wire_shape_is_untouched(self) -> None:
        local = LocalOpenAICompatibleProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="local",
            analyst_model="m",
            critic_model="m",
            adjudicator_model="m",
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=60.0,
            max_retries=1,
            max_output_tokens=4096,
            temperature=0.15,
            top_p=0.85,
            seed=42,
            enable_thinking=True,
            require_json_schema=True,
            parallelism=1,
            context_budget_tokens=7000,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=60.0,
            log=lambda _m: None,
        )
        self.assertEqual(local.provider_mode, PROVIDER_MODE_LOCAL)
        self.assertEqual(
            local._wire_extra_body(), {"chat_template_kwargs": {"enable_thinking": True}}
        )
        self.assertTrue(local.resubmit_ambiguous_transport_failures)


class PythonMqlAgreementTests(unittest.TestCase):
    def test_mql_accepts_the_opencode_provider_mode(self) -> None:
        """A mode Python can emit but MQL rejects is an infrastructure rejection
        on healthy infrastructure, which the project contract forbids."""

        from test_governance_contracts import MQL_STAGE

        source = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="ignore")
        self.assertIn('AI_PROVIDER_MODE_OPENCODE   = "OPENCODE_API"', source)
        self.assertIn("|| mode == AI_PROVIDER_MODE_OPENCODE", source)
        self.assertIn(PROVIDER_MODE_OPENCODE, PROVIDER_MODES_TRADING)

    def test_adding_the_mode_did_not_move_the_contract_manifest(self) -> None:
        """The provider-mode literals are not hashed material.

        If they were, adding one would invalidate every recorded replay cohort --
        the situation recorded in sections 4y/4z of the project notes.
        """

        from test_governance_contracts import MQL_STAGE

        source = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="ignore")
        start = source.find("string PO3ContractManifestMaterial(")
        self.assertGreater(start, 0)
        body = source[start : source.find("\n}", start)]
        self.assertNotIn("AI_PROVIDER_MODE", body)


if __name__ == "__main__":
    unittest.main()
