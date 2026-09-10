"""Flex-tier routing on OpenRouter, and the three defects that block it.

All three were measured against the live OpenRouter API on 2026-09-08 with the
real key, not inferred from documentation:

1.  ``openai/gpt-5.6-luna`` publishes seven endpoints and NOT ONE of them lists
    ``temperature`` or ``top_p`` in ``supported_parameters``.  The transport
    sends ``provider.require_parameters=true`` (that is what keeps a strict
    json_schema from being downgraded to a hint), and require_parameters filters
    routing to endpoints accepting EVERY supplied parameter -- so one sampling
    key emptied the funnel and the call died as HTTP 404
    ``No endpoints found that can handle the requested parameters``
    ``failed_routing_step="Filter by Parameters"``.  With both omitted the same
    request returned HTTP 200 with schema-valid content.  ``_env_float`` folded a
    present-but-empty value onto its default, so the .env could not express
    "omit", and every decision on this transport would have failed closed before
    the model was reached.

2.  That 404 classified as ``PROVIDER_TRANSPORT_ERROR`` -- our own unroutable
    request reported as the network failing, with the configuration circuit left
    closed so every following request repeated the same doomed call.

3.  OpenRouter has no ``service_tier`` parameter: the flex tier is a separate
    ENDPOINT (tag ``openai/flex``, $0.10/$0.60 per Mtok against ``openai`` at
    $0.20/$1.20) and BOTH report ``provider: "OpenAI"`` on the response.  No
    ``(model, endpoint)`` rate key can tell them apart, so the model-level row
    priced half the calls at 2x while reporting status ``priced``.  The response
    carries ``usage.cost``, which is what the call was actually charged.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate  # noqa: E402
import openai_usage_logger as usage  # noqa: E402
from ai_gate import AIGateRuntimeConfig  # noqa: E402
from ai_provider import (  # noqa: E402
    PROVIDER_MODE_OPENROUTER,
    PROVIDER_MODE_REMOTE,
    OpenRouterProvider,
    _OpenAICompatibleProviderBase,
)

LUNA = "openai/gpt-5.6-luna"


def _config(**env: str) -> AIGateRuntimeConfig:
    base = {
        "AI_PROVIDER_SELECT": "openrouter",
        "OPENROUTER_API_KEY": "test-key",
        "OPENROUTER_MODEL": LUNA,
    }
    base.update(env)
    return AIGateRuntimeConfig.from_env(base)


class _RouterError(Exception):
    """An OpenRouter routing rejection, shaped like the real one."""

    def __init__(self, message: str, status_code: int = 404, body=None) -> None:
        super().__init__(message)
        self.status_code = status_code
        if body is not None:
            self.body = body


class _Response:
    """A completion, with the usage block OpenRouter actually returns."""

    def __init__(self, prompt: int, completion: int, cost=None, upstream=None) -> None:
        self.id = "gen-test"
        self.provider = "OpenAI"
        self.model = LUNA
        self.usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": 0},
        }
        if cost is not None:
            self.usage["cost"] = cost
        if upstream is not None:
            self.usage["cost_details"] = {"upstream_inference_cost": upstream}


class SamplingParameterOmissionTests(unittest.TestCase):
    """An empty value must mean "not on the wire", not "send the default"."""

    def test_empty_value_omits_the_parameter(self) -> None:
        cfg = _config(OPENROUTER_TEMPERATURE="", OPENROUTER_TOP_P="")
        self.assertIsNone(cfg.openrouter_temperature)
        self.assertIsNone(cfg.openrouter_top_p)

    def test_absent_key_still_means_the_default(self) -> None:
        """The compatibility half: existing files must not change behaviour."""

        cfg = _config()
        self.assertEqual(cfg.openrouter_temperature, 0.15)
        self.assertEqual(cfg.openrouter_top_p, 0.85)

    def test_a_number_is_still_read_as_that_number(self) -> None:
        cfg = _config(OPENROUTER_TEMPERATURE="0.4", OPENROUTER_TOP_P="0.9")
        self.assertEqual(cfg.openrouter_temperature, 0.4)
        self.assertEqual(cfg.openrouter_top_p, 0.9)

    def test_explicit_omit_words_are_accepted(self) -> None:
        for word in ("none", "None", "omit", "unset", "null", "  "):
            with self.subTest(word=word):
                cfg = _config(OPENROUTER_TEMPERATURE=word)
                self.assertIsNone(cfg.openrouter_temperature)

    def test_an_unparseable_number_is_not_silently_omitted(self) -> None:
        """A typo must fall back to the default and say so, not disappear."""

        warnings: list[str] = []
        value = ai_gate._env_float_or_none(
            {"X": "banana"}, "X", 0.15, warnings, min_value=0.0, max_value=2.0
        )
        self.assertEqual(value, 0.15)
        self.assertIn("X=invalid_float", warnings)

    def test_bounds_still_apply_to_a_real_value(self) -> None:
        warnings: list[str] = []
        self.assertEqual(
            ai_gate._env_float_or_none({"X": "9"}, "X", 0.15, warnings, max_value=2.0), 2.0
        )
        self.assertIn("X=above_max", warnings)

    def test_deployed_env_omits_sampling_while_luna_is_configured(self) -> None:
        """The checked-in block must not carry a key luna cannot be routed with.

        Conditioned on the model, not on the selection: the block is inert today
        but the 404 lands the moment it is selected.
        """

        values = {}
        for raw in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        if not (values.get("OPENROUTER_MODEL") or "").startswith("openai/gpt-5.6"):
            self.skipTest(
                f"deployed OPENROUTER_MODEL={values.get('OPENROUTER_MODEL')!r}"
                " is not a reasoning-only OpenAI model"
            )
        if values.get("OPENROUTER_REQUIRE_STRUCTURED_PROVIDER") != "true":
            self.skipTest("require_parameters is off; unsupported keys are dropped, not fatal")
        for key in ("OPENROUTER_TEMPERATURE", "OPENROUTER_TOP_P"):
            self.assertEqual(
                values.get(key),
                "",
                f"{key} must be empty: no openai/gpt-5.6-luna endpoint accepts it,"
                " and require_parameters turns that into HTTP 404 on every call",
            )


class FlexEndpointRoutingTests(unittest.TestCase):
    """The tier is chosen by routing order, because there is no tier parameter."""

    @staticmethod
    def _provider(**kwargs) -> OpenRouterProvider:
        defaults = dict(
            base_url="https://openrouter.ai/api/v1",
            api_key="test-key",
            analyst_model=LUNA,
            critic_model=LUNA,
            adjudicator_model=LUNA,
            fallback_models=(),
            healthcheck_path="/models",
            timeout_sec=900.0,
            max_retries=2,
            max_output_tokens=25000,
            temperature=None,
            top_p=None,
            seed=42,
            enable_thinking=True,
            reasoning_effort="low",
            reasoning_token_reserve=24000,
            require_json_schema=True,
            require_structured_provider=True,
            allowed_providers=("openai/flex", "openai"),
            parallelism=6,
            context_budget_tokens=131072,
            circuit_failure_threshold=3,
            circuit_cooldown_sec=60.0,
            log=lambda _message: None,
        )
        defaults.update(kwargs)
        return OpenRouterProvider(**defaults)

    def test_endpoint_tags_reach_the_routing_order_in_order(self) -> None:
        routing = self._provider()._wire_extra_body()["provider"]
        self.assertEqual(routing["order"], ["openai/flex", "openai"])
        self.assertIs(routing["allow_fallbacks"], False)
        self.assertIs(routing["require_parameters"], True)

    def test_a_second_entry_is_what_keeps_a_flex_outage_off_the_decision(self) -> None:
        """allow_fallbacks=false restricts the SET, it does not forbid the walk.

        Pinning flex alone would make an endpoint outage a fail-closed decision;
        the standard endpoint is listed so it is a price change instead.
        """

        routing = self._provider(allowed_providers=("openai/flex",))._wire_extra_body()["provider"]
        self.assertEqual(routing["order"], ["openai/flex"])
        self.assertIs(routing["allow_fallbacks"], False)

    def test_low_effort_reaches_the_wire(self) -> None:
        body = self._provider()._wire_extra_body()
        self.assertEqual(body["reasoning"], {"enabled": True, "effort": "low"})

    def test_reserve_stays_inside_the_model_completion_ceiling(self) -> None:
        # openai/gpt-5.6-luna publishes max_completion_tokens=128000.
        self.assertLessEqual(self._provider()._wire_max_tokens(25000), 128000)


class RoutingRejectionClassificationTests(unittest.TestCase):
    """Our unroutable request is a configuration defect, not a transport one."""

    def test_routing_funnel_404_is_a_configuration_block(self) -> None:
        error = _RouterError(
            "No endpoints found that can handle the requested parameters.",
            body={"error": {"metadata": {"failed_routing_step": "Filter by Parameters"}}},
        )
        classified = _OpenAICompatibleProviderBase._classify_transport_exception(error)
        self.assertEqual(classified.category, "PROVIDER_CONFIGURATION_ERROR")
        self.assertTrue(classified.configuration_block)
        self.assertFalse(classified.retryable)
        self.assertIn("router_no_eligible_endpoint", str(classified))

    def test_no_allowed_providers_is_the_same_class(self) -> None:
        error = _RouterError("No allowed providers are available for the selected model.")
        classified = _OpenAICompatibleProviderBase._classify_transport_exception(error)
        self.assertEqual(classified.category, "PROVIDER_CONFIGURATION_ERROR")
        self.assertTrue(classified.configuration_block)

    def test_an_ordinary_404_is_left_alone(self) -> None:
        """The branch must not swallow every 404 -- only the routing wording."""

        classified = _OpenAICompatibleProviderBase._classify_transport_exception(
            _RouterError("Not Found")
        )
        self.assertEqual(classified.category, "PROVIDER_TRANSPORT_ERROR")
        self.assertFalse(classified.configuration_block)

    def test_a_429_is_still_retryable(self) -> None:
        classified = _OpenAICompatibleProviderBase._classify_transport_exception(
            _RouterError("rate_limit_exceeded", status_code=429)
        )
        self.assertEqual(classified.category, "PROVIDER_TRANSPORT_ERROR")
        self.assertTrue(classified.retryable)


class ProviderReportedCostTests(unittest.TestCase):
    """Only the response knows which tier served the call, so only it knows the price."""

    def test_reported_cost_wins_over_the_rate_table(self) -> None:
        response = _Response(7, 9, cost=0.0000061, upstream=0.0000061)
        cost, status = usage.price_call(
            model=LUNA,
            provider_mode=PROVIDER_MODE_OPENROUTER,
            input_tokens=7,
            output_tokens=9,
            routed_endpoint=usage.response_routed_endpoint(response),
            provider_reported_cost=usage.response_reported_cost_usd(response),
        )
        self.assertEqual(status, usage.PRICING_STATUS_PROVIDER_REPORTED)
        self.assertAlmostEqual(cost, 0.0000061, places=10)

    def test_the_table_alone_would_have_doubled_a_flex_call(self) -> None:
        """Why the change exists, stated as a number rather than as a claim."""

        table_cost, table_status = usage.price_call(
            model=LUNA,
            provider_mode=PROVIDER_MODE_OPENROUTER,
            input_tokens=7,
            output_tokens=9,
        )
        self.assertEqual(table_status, usage.PRICING_STATUS_PRICED)
        self.assertAlmostEqual(table_cost, 0.0000122, places=10)

    def test_both_cost_surfaces_agree_on_the_same_call(self) -> None:
        response = _Response(7, 9, cost=0.0000061, upstream=0.0000061)
        # The autouse fixture in conftest.py disables usage logging for every
        # test, and log_ai_usage then returns an EMPTY row by contract.  This
        # test needs the row itself, so it re-enables logging for its own scope
        # only -- exactly as ``test_usage_cost_accounting`` does -- while the
        # bus below keeps the write off the production ledger.
        prev_enable = os.environ.get("AI_USAGE_LOG_ENABLE")
        os.environ["AI_USAGE_LOG_ENABLE"] = "true"
        # The bus is redirected first: log_ai_usage WRITES, and a test that
        # forgets this appends rows to the live ledger it is meant to protect.
        try:
            with tempfile.TemporaryDirectory() as tmp:
                usage.set_ai_usage_bus(Path(tmp))
                row = usage.log_ai_usage(
                    source="test",
                    operation="provider_neutral_analyst",
                    model=LUNA,
                    response=response,
                    provider_mode=PROVIDER_MODE_OPENROUTER,
                    provider_id="openrouter_api",
                )
        finally:
            if prev_enable is None:
                os.environ.pop("AI_USAGE_LOG_ENABLE", None)
            else:
                os.environ["AI_USAGE_LOG_ENABLE"] = prev_enable
        cost, status = usage.price_call(
            model=LUNA,
            provider_mode=PROVIDER_MODE_OPENROUTER,
            input_tokens=7,
            output_tokens=9,
            routed_endpoint=usage.response_routed_endpoint(response),
            provider_reported_cost=usage.response_reported_cost_usd(response),
        )
        self.assertEqual(row["pricing_status"], status)
        self.assertEqual(row["estimated_cost_usd"], cost)
        self.assertEqual(row["routed_endpoint"], "OpenAI")

    def test_byok_takes_the_larger_of_the_two_figures(self) -> None:
        """Under BYOK ``cost`` is OpenRouter's fee only; upstream bills separately."""

        response = _Response(7, 9, cost=0.0, upstream=0.0000061)
        self.assertAlmostEqual(
            usage.response_reported_cost_usd(response), 0.0000061, places=10
        )

    def test_a_malformed_cost_falls_back_to_the_table(self) -> None:
        for bad in ("free", -1.0, float("nan"), float("inf"), True, None):
            with self.subTest(bad=bad):
                response = _Response(7, 9, cost=bad)
                self.assertIsNone(usage.response_reported_cost_usd(response))

    def test_a_response_without_a_cost_field_changes_nothing(self) -> None:
        """The OpenAI transport reports no cost, so its rows must be untouched."""

        response = _Response(10_000, 1_000)
        self.assertIsNone(usage.response_reported_cost_usd(response))
        cost, status = usage.price_call(
            model="gpt-5.6-luna",
            provider_mode=PROVIDER_MODE_REMOTE,
            input_tokens=10_000,
            output_tokens=1_000,
            service_tier="flex",
            provider_reported_cost=usage.response_reported_cost_usd(response),
        )
        self.assertEqual(status, usage.PRICING_STATUS_PRICED)
        self.assertAlmostEqual(cost, 0.0016, places=8)

    def test_a_free_transport_is_never_priced_from_a_reported_number(self) -> None:
        """not_billed outranks everything; a local server reporting a cost is noise."""

        self.assertEqual(
            usage._pricing_status(LUNA, "LOCAL_OPENAI_COMPATIBLE", "", "", 0.5),
            usage.PRICING_STATUS_NOT_BILLED,
        )

    def test_reported_cost_carries_a_real_number_in_the_status_set(self) -> None:
        self.assertIn(
            usage.PRICING_STATUS_PROVIDER_REPORTED, usage.PRICING_STATUSES_WITH_COST
        )


if __name__ == "__main__":
    unittest.main()
