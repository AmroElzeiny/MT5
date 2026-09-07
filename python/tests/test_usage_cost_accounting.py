"""Usage accounting must not report real spend as zero.

Two independent defects made ``estimated_cost_usd`` blind, and both were only
found from a billing statement rather than from the report that exists to show
this:

1. ``gpt-5.6-luna`` was never added to ``PRICE_PER_MILLION``, and an absent
   price returned ``0.0`` -- indistinguishable from a genuinely free call.  948
   logged luna calls (17.07M input / 1.41M output tokens) all recorded $0.00.
2. The cost was gated on ``provider_mode == "REMOTE_API"``.  OpenRouter reports
   ``OPENROUTER_API``, so every OpenRouter call would have been recorded at
   $0.00 by construction even after its price was added.

Each test below fails against the pre-change tree.
"""
from __future__ import annotations

import csv
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openai_usage_logger as usage  # noqa: E402
from ai_provider import (  # noqa: E402
    PROVIDER_MODE_LOCAL,
    PROVIDER_MODE_OPENROUTER,
    PROVIDER_MODE_REMOTE,
    PROVIDER_MODES_TRADING,
)


class _Response:
    """Stand-in for a chat-completions response.

    ``usage`` is a plain mapping because that is what ``_usage_from_response``
    reduces a real SDK usage object to via ``_plain``; an opaque object would
    stringify and silently yield zero tokens, which would make these tests pass
    for the wrong reason.
    """

    def __init__(
        self, prompt: int, completion: int, cached: int = 0, provider: str = ""
    ) -> None:
        self.id = "resp-test"
        # OpenRouter returns the endpoint that actually served the call here.
        self.provider = provider
        self.usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached},
        }


def _call_argument_blocks(source: str, opener: str) -> list[str]:
    """Every ``opener(...)`` call's argument text, paren-balanced.

    Counting substrings across a whole file cannot tell "each of four sites
    passes this" from "one site passes it four times", so the blocks are cut out
    and checked individually.
    """

    blocks: list[str] = []
    start = source.find(opener)
    while start != -1:
        cursor = start + len(opener)
        depth = 1
        while cursor < len(source) and depth:
            char = source[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            cursor += 1
        blocks.append(source[start + len(opener) : cursor - 1])
        start = source.find(opener, cursor)
    return blocks


def _log(tmp: Path, **kwargs):
    usage.set_ai_usage_bus(tmp)
    defaults = dict(
        source="test",
        operation="provider_neutral_analyst",
        status="ok",
        request_id="req-1",
    )
    defaults.update(kwargs)
    return usage.log_ai_usage(**defaults)


class PricingStatusTests(unittest.TestCase):
    def test_openrouter_mode_is_billed_not_free(self) -> None:
        """The whole point: OPENROUTER_API spends real money."""

        self.assertIn(PROVIDER_MODE_OPENROUTER, usage.BILLED_PROVIDER_MODES)
        self.assertIn(PROVIDER_MODE_REMOTE, usage.BILLED_PROVIDER_MODES)

    def test_self_hosted_transport_is_not_billed(self) -> None:
        self.assertNotIn(PROVIDER_MODE_LOCAL, usage.BILLED_PROVIDER_MODES)
        self.assertEqual(
            usage._pricing_status("anything", PROVIDER_MODE_LOCAL),
            usage.PRICING_STATUS_NOT_BILLED,
        )

    def test_every_billed_trading_mode_is_classified(self) -> None:
        """A trading provider mode that is neither billed nor local is a gap."""

        for mode in PROVIDER_MODES_TRADING:
            with self.subTest(mode=mode):
                self.assertIn(
                    mode,
                    set(usage.BILLED_PROVIDER_MODES) | {PROVIDER_MODE_LOCAL},
                    f"{mode} is neither priced nor declared free",
                )

    def test_unpriced_model_on_a_billed_transport_is_flagged(self) -> None:
        """Billed, but no price -- must not read as free.

        The example used to be ``gpt-5.6-luna`` itself.  Now that luna is priced,
        the fixture is a model id that genuinely has no row, so the *mechanism*
        stays covered without pinning the defect it was written for.
        """

        self.assertNotIn("model-with-no-published-rate", usage.PRICE_PER_MILLION)
        self.assertEqual(
            usage._pricing_status("model-with-no-published-rate", PROVIDER_MODE_REMOTE),
            usage.PRICING_STATUS_UNPRICED,
        )

    def test_the_live_gate_model_is_priced(self) -> None:
        """The regression itself: 2,648 measured luna calls recorded $0.00."""

        self.assertIn("gpt-5.6-luna", usage.PRICE_PER_MILLION)
        self.assertEqual(
            usage._pricing_status("gpt-5.6-luna", PROVIDER_MODE_REMOTE),
            usage.PRICING_STATUS_PRICED,
        )

    def test_openrouter_latest_concrete_release_uses_alias_route_price(self) -> None:
        self.assertEqual(
            usage._canonical_model("deepseek/deepseek-v4-flash-0731"),
            "~deepseek/deepseek-v4-flash-latest",
        )
        cost, status = usage.price_call(
            model="deepseek/deepseek-v4-flash-0731",
            provider_mode=PROVIDER_MODE_OPENROUTER,
            input_tokens=94,
            output_tokens=365,
            routed_endpoint="DeepInfra",
        )
        self.assertEqual(status, usage.PRICING_STATUS_PRICED)
        self.assertAlmostEqual(cost or 0.0, 0.00007134, places=8)

    def test_openrouter_latest_price_fold_rejects_sibling_models(self) -> None:
        self.assertEqual(
            usage._canonical_model("deepseek/deepseek-v4-flash-vision-exp"),
            "deepseek/deepseek-v4-flash-vision-exp",
        )
        self.assertEqual(
            usage._canonical_model("deepseek/deepseek-v4-pro-0731"),
            "deepseek/deepseek-v4-pro-0731",
        )

    def test_routed_endpoint_gives_an_exact_price(self) -> None:
        for endpoint in ("DeepInfra", "deepinfra", "Parasail"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    usage._pricing_status(
                        "z-ai/glm-5.3-flash", PROVIDER_MODE_OPENROUTER, endpoint
                    ),
                    usage.PRICING_STATUS_PRICED,
                )

    def test_unknown_route_is_an_upper_bound_not_an_exact_price(self) -> None:
        """``OPENROUTER_ALLOWED_PROVIDERS`` is an ordered list, so the configured
        first choice is not proof of what served the call."""

        for endpoint in ("", "SomeNewEndpoint"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    usage._pricing_status(
                        "z-ai/glm-5.3-flash", PROVIDER_MODE_OPENROUTER, endpoint
                    ),
                    usage.PRICING_STATUS_UPPER_BOUND,
                )
        self.assertIn(usage.PRICING_STATUS_UPPER_BOUND, usage.PRICING_STATUSES_WITH_COST)

    def test_single_endpoint_model_keeps_an_exact_price(self) -> None:
        """OpenAI models are not routed per endpoint; their one row is exact."""

        self.assertFalse(usage._model_has_endpoint_rates("gpt-5.4-nano"))
        self.assertEqual(
            usage._pricing_status("gpt-5.4-nano", PROVIDER_MODE_REMOTE),
            usage.PRICING_STATUS_PRICED,
        )

    def test_model_row_is_the_upper_bound_of_every_endpoint(self) -> None:
        """The structural guarantee that an unknown route can never under-report.

        If a cheaper endpoint were ever promoted into the model-level row, an
        unidentified route would be billed below its real cost -- silently, and
        in the same direction as the defect this module was fixed for.
        """

        models = {model for model, _ in usage.PRICE_PER_MILLION_BY_ENDPOINT}
        self.assertTrue(models, "no per-endpoint rates to check")
        for model in models:
            bound = usage.PRICE_PER_MILLION[model]
            for (m, endpoint), rates in usage.PRICE_PER_MILLION_BY_ENDPOINT.items():
                if m != model:
                    continue
                for field in ("input", "cached_input", "output"):
                    with self.subTest(model=model, endpoint=endpoint, field=field):
                        self.assertGreaterEqual(
                            bound[field],
                            rates[field],
                            f"{model} model row understates {endpoint}.{field}",
                        )


class CostArithmeticTests(unittest.TestCase):
    def test_openrouter_call_costs_more_than_zero(self) -> None:
        with TempBus() as tmp:
            row = _log(
                tmp,
                model="z-ai/glm-5.3-flash",
                provider_mode=PROVIDER_MODE_OPENROUTER,
                provider_id="openrouter_api",
                endpoint_class="openrouter",
                response=_Response(31000, 3246, provider="DeepInfra"),
            )
        self.assertEqual(row["pricing_status"], usage.PRICING_STATUS_PRICED)
        self.assertEqual(row["routed_endpoint"], "DeepInfra")
        self.assertGreater(
            row["estimated_cost_usd"],
            0.0,
            "OpenRouter traffic recorded as free is the defect this guards",
        )

    def test_the_serving_endpoint_sets_the_rate(self) -> None:
        """Same model id, same tokens, different endpoint -> different bill.

        This is why the configured first choice cannot be used as the price:
        with an ordered fallback list, a 429 on the cheap endpoint silently
        moves the call to a dearer one.
        """

        cheap = usage._estimated_cost("z-ai/glm-5.3-flash", 100_000, 0, 10_000, "DeepInfra")
        dear = usage._estimated_cost("z-ai/glm-5.3-flash", 100_000, 0, 10_000, "Parasail")
        self.assertLess(cheap, dear)
        self.assertAlmostEqual(dear / cheap, 2.0, places=6)

    def test_unknown_route_is_never_cheaper_than_any_real_route(self) -> None:
        bound = usage._estimated_cost("z-ai/glm-5.3-flash", 100_000, 20_000, 10_000)
        for _, endpoint in usage.PRICE_PER_MILLION_BY_ENDPOINT:
            with self.subTest(endpoint=endpoint):
                actual = usage._estimated_cost(
                    "z-ai/glm-5.3-flash", 100_000, 20_000, 10_000, endpoint
                )
                self.assertGreaterEqual(bound, actual)

    def test_cached_input_is_billed_at_the_cache_rate(self) -> None:
        """Measured cache hit rate was ~11%; the discount must be applied."""

        rates = usage.PRICE_PER_MILLION_BY_ENDPOINT[("z-ai/glm-5.3-flash", "deepinfra")]
        self.assertLess(rates["cached_input"], rates["input"])
        plain = usage._estimated_cost("z-ai/glm-5.3-flash", 100_000, 0, 0, "DeepInfra")
        cached = usage._estimated_cost("z-ai/glm-5.3-flash", 100_000, 100_000, 0, "DeepInfra")
        self.assertLess(cached, plain)

    def test_measured_per_decision_cost_is_reported(self) -> None:
        """52,522 in / 4,326 out is the measured mean of one 2.92-call decision."""

        cost = usage._estimated_cost(
            "z-ai/glm-5.3-flash", 52_522, 5_269, 4_326, "DeepInfra"
        )
        # DeepInfra: $0.075 in / $0.015 cached / $0.25 out per Mtok.
        expected = (
            (52_522 - 5_269) * 0.075 + 5_269 * 0.015 + 4_326 * 0.25
        ) / 1_000_000.0
        self.assertAlmostEqual(cost, round(expected, 8), places=8)
        self.assertGreater(cost, 0.0)

    def test_unpriced_billed_model_still_records_zero_but_says_so(self) -> None:
        with TempBus() as tmp:
            row = _log(
                tmp,
                model="model-with-no-published-rate",
                provider_mode=PROVIDER_MODE_REMOTE,
                response=_Response(31000, 3246),
            )
        self.assertEqual(row["estimated_cost_usd"], 0.0)
        self.assertEqual(row["pricing_status"], usage.PRICING_STATUS_UNPRICED)

    def test_the_live_gate_model_now_records_a_real_cost(self) -> None:
        with TempBus() as tmp:
            row = _log(
                tmp,
                model="gpt-5.6-luna",
                provider_mode=PROVIDER_MODE_REMOTE,
                response=_Response(31000, 3246),
            )
        self.assertEqual(row["pricing_status"], usage.PRICING_STATUS_PRICED)
        self.assertGreater(row["estimated_cost_usd"], 0.0)

    def test_measured_luna_decision_cost(self) -> None:
        """The measured mean of one 3-call decision: 60,935 in / 4,639 out.

        Analyst 38,590/3,793 + critic 11,713/612 + adjudicator 10,632/234, from
        2,648 ledger rows.  Every one of them was recorded at $0.00.
        """

        cost = sum(
            usage._estimated_cost("gpt-5.6-luna", inp, 0, out)
            for inp, out in ((38_590, 3_793), (11_713, 612), (10_632, 234))
        )
        expected = (60_935 * 0.20 + 4_639 * 1.20) / 1_000_000.0
        self.assertAlmostEqual(cost, expected, places=8)
        self.assertGreater(cost, 0.0)


class ReasoningEffortPricingTests(unittest.TestCase):
    """Effort is a token-count axis, not a rate axis -- one row prices them all.

    Artificial Analysis and OpenRouter both publish per-effort slugs
    (``gpt-5.6-luna-low`` ... ``-max``).  A run that switches
    ``AI_GATE_REASONING_EFFORT`` must not silently fall back to unpriced.
    """

    EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")

    def test_every_effort_slug_resolves_to_the_base_rate(self) -> None:
        base = usage.PRICE_PER_MILLION["gpt-5.6-luna"]
        for effort in self.EFFORTS:
            for model in (
                f"gpt-5.6-luna-{effort}",
                f"openai/gpt-5.6-luna-{effort}",
            ):
                with self.subTest(model=model):
                    self.assertEqual(usage._canonical_model(model), "gpt-5.6-luna")
                    rates, exact = usage._rates(model)
                    self.assertEqual(rates, base)
                    self.assertTrue(exact)
                    self.assertEqual(
                        usage._pricing_status(model, PROVIDER_MODE_REMOTE),
                        usage.PRICING_STATUS_PRICED,
                    )

    def test_effort_does_not_change_the_cost_of_identical_token_counts(self) -> None:
        costs = {
            effort: usage._estimated_cost(f"gpt-5.6-luna-{effort}", 40_000, 4_000, 3_000)
            for effort in self.EFFORTS
        }
        self.assertEqual(len(set(costs.values())), 1, costs)
        self.assertEqual(
            set(costs.values()),
            {usage._estimated_cost("gpt-5.6-luna", 40_000, 4_000, 3_000)},
        )

    def test_folding_never_redirects_a_distinct_priced_model(self) -> None:
        """An id that has its own row short-circuits before any suffix stripping."""

        for model in usage.PRICE_PER_MILLION:
            with self.subTest(model=model):
                self.assertEqual(usage._canonical_model(model), model)

    def test_an_unknown_base_is_still_unpriced_after_folding(self) -> None:
        """Stripping ``-high`` must not invent a rate for a model we do not know."""

        self.assertEqual(
            usage._pricing_status("some-new-model-high", PROVIDER_MODE_REMOTE),
            usage.PRICING_STATUS_UNPRICED,
        )

    def test_dated_snapshots_resolve_to_their_alias(self) -> None:
        """Measured, not hypothetical: 145 REMOTE_API calls sat at $0.00.

        ``gpt-5.4-nano-2026-03-17`` appears in the ledger beside priced
        ``gpt-5.4-nano`` rows -- one model, two ids, one rate.
        """

        self.assertEqual(usage._canonical_model("gpt-5.4-nano-2026-03-17"), "gpt-5.4-nano")
        self.assertEqual(
            usage._pricing_status("gpt-5.4-nano-2026-03-17", PROVIDER_MODE_REMOTE),
            usage.PRICING_STATUS_PRICED,
        )
        self.assertEqual(
            usage._estimated_cost("gpt-5.4-nano-2026-03-17", 10_000, 0, 1_000),
            usage._estimated_cost("gpt-5.4-nano", 10_000, 0, 1_000),
        )

    def test_effort_and_snapshot_together_resolve(self) -> None:
        self.assertEqual(
            usage._canonical_model("openai/gpt-5.6-luna-high-2026-06-01"),
            "gpt-5.6-luna",
        )

    def test_a_snapshot_of_an_unknown_model_stays_unpriced(self) -> None:
        """Stripping a date must not invent a rate for a model we never had."""

        self.assertNotIn("gpt-5-nano", usage.PRICE_PER_MILLION)
        self.assertEqual(
            usage._pricing_status("gpt-5-nano-2025-08-07", PROVIDER_MODE_REMOTE),
            usage.PRICING_STATUS_UNPRICED,
        )

    def test_endpoint_routed_models_are_unaffected_by_folding(self) -> None:
        self.assertEqual(
            usage._canonical_model("z-ai/glm-5.3-flash"), "z-ai/glm-5.3-flash"
        )
        self.assertEqual(
            usage._pricing_status(
                "z-ai/glm-5.3-flash", PROVIDER_MODE_OPENROUTER, "DeepInfra"
            ),
            usage.PRICING_STATUS_PRICED,
        )


class ServiceTierPricingTests(unittest.TestCase):
    """``AI_USE_FLEX`` halves the bill; pricing it at standard rates doubles it."""

    def test_flex_is_half_of_standard(self) -> None:
        standard = usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639, "", "default")
        flex = usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639, "", "flex")
        self.assertAlmostEqual(flex, standard / 2.0, places=8)

    def test_batch_matches_flex_and_fast_doubles(self) -> None:
        standard = usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639)
        self.assertAlmostEqual(
            usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639, "", "batch"),
            standard / 2.0,
            places=8,
        )
        self.assertAlmostEqual(
            usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639, "", "priority"),
            standard * 2.0,
            places=8,
        )

    def test_auto_and_empty_price_at_standard(self) -> None:
        standard = usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639)
        for tier in ("", "auto", "default", "standard", "AUTO", " Flex "):
            with self.subTest(tier=tier):
                expected = standard * usage.SERVICE_TIER_MULTIPLIER[tier.strip().lower()]
                self.assertAlmostEqual(
                    usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639, "", tier),
                    expected,
                    places=8,
                )

    def test_the_response_tier_beats_the_requested_tier(self) -> None:
        """``auto`` is a request; the API resolves it and echoes the outcome."""

        response = _Response(60_935, 4_639)
        response.service_tier = "flex"
        with TempBus() as tmp:
            row = _log(
                tmp,
                model="gpt-5.6-luna",
                provider_mode=PROVIDER_MODE_REMOTE,
                response=response,
                service_tier="auto",
            )
        self.assertEqual(row["service_tier"], "flex")
        self.assertEqual(row["price_multiplier"], 0.5)
        self.assertAlmostEqual(
            row["estimated_cost_usd"],
            usage._estimated_cost("gpt-5.6-luna", 60_935, 0, 4_639) / 2.0,
            places=8,
        )

    def test_requested_tier_is_used_when_the_response_is_silent(self) -> None:
        with TempBus() as tmp:
            row = _log(
                tmp,
                model="gpt-5.6-luna",
                provider_mode=PROVIDER_MODE_REMOTE,
                response=_Response(60_935, 4_639),
                service_tier="flex",
            )
        self.assertEqual(row["service_tier"], "flex")
        self.assertEqual(row["price_multiplier"], 0.5)

    def test_an_unknown_tier_is_named_not_guessed(self) -> None:
        """A new 2x tier billed silently at 1x is the original defect again."""

        self.assertEqual(
            usage._pricing_status("gpt-5.6-luna", PROVIDER_MODE_REMOTE, "", "turbo"),
            usage.PRICING_STATUS_TIER_UNKNOWN,
        )
        self.assertIn(
            usage.PRICING_STATUS_TIER_UNKNOWN, usage.PRICING_STATUSES_WITH_COST
        )
        with TempBus() as tmp:
            row = _log(
                tmp,
                model="gpt-5.6-luna",
                provider_mode=PROVIDER_MODE_REMOTE,
                response=_Response(60_935, 4_639),
                service_tier="turbo",
            )
        self.assertEqual(row["pricing_status"], usage.PRICING_STATUS_TIER_UNKNOWN)
        self.assertGreater(row["estimated_cost_usd"], 0.0)

    def test_every_pricing_gap_has_its_own_advice(self) -> None:
        """A gap pointed at the wrong table is a gap that never gets closed."""

        for status in (
            usage.PRICING_STATUS_UNPRICED,
            usage.PRICING_STATUS_UPPER_BOUND,
            usage.PRICING_STATUS_TIER_UNKNOWN,
        ):
            with self.subTest(status=status):
                self.assertIn(status, usage._PRICING_GAP_ADVICE)
        self.assertEqual(
            len(set(usage._PRICING_GAP_ADVICE.values())),
            len(usage._PRICING_GAP_ADVICE),
        )


class LongContextSurchargeTests(unittest.TestCase):
    """Published rule: past the threshold the *whole* request is rebilled."""

    def test_below_the_threshold_nothing_changes(self) -> None:
        rule = usage.LONG_CONTEXT_SURCHARGE["gpt-5.6-luna"]
        below = int(rule["threshold_input_tokens"])
        cost = usage._estimated_cost("gpt-5.6-luna", below, 0, 1_000)
        expected = (below * 0.20 + 1_000 * 1.20) / 1_000_000.0
        self.assertAlmostEqual(cost, expected, places=8)

    def test_above_the_threshold_the_whole_request_is_rebilled(self) -> None:
        rule = usage.LONG_CONTEXT_SURCHARGE["gpt-5.6-luna"]
        above = int(rule["threshold_input_tokens"]) + 1
        cost = usage._estimated_cost("gpt-5.6-luna", above, 0, 1_000)
        expected = (above * 0.20 * 2.0 + 1_000 * 1.20 * 1.5) / 1_000_000.0
        self.assertAlmostEqual(cost, expected, places=8)

    def test_the_surcharge_does_not_leak_onto_other_models(self) -> None:
        """It is a published GPT-5.6 rule, not a property of large prompts."""

        self.assertNotIn("z-ai/glm-5.3-flash", usage.LONG_CONTEXT_SURCHARGE)
        big = usage._estimated_cost("z-ai/glm-5.3-flash", 500_000, 0, 1_000, "DeepInfra")
        linear = (500_000 * 0.075 + 1_000 * 0.25) / 1_000_000.0
        self.assertAlmostEqual(big, linear, places=8)

    def test_our_measured_prompt_never_triggers_it(self) -> None:
        """38.6k analyst prompt vs a 272k threshold -- recorded, not active."""

        rule = usage.LONG_CONTEXT_SURCHARGE["gpt-5.6-luna"]
        self.assertGreater(rule["threshold_input_tokens"], 60_935)


class SinglePricingSourceTests(unittest.TestCase):
    """Two rate tables would drift; there is exactly one."""

    def test_price_call_separates_unknown_from_free(self) -> None:
        cost, status = usage.price_call(
            model="model-with-no-published-rate",
            provider_mode=PROVIDER_MODE_REMOTE,
            input_tokens=1_000,
            output_tokens=100,
        )
        self.assertIsNone(cost, "unknown must not be reported as a number")
        self.assertEqual(status, usage.PRICING_STATUS_UNPRICED)

        cost, status = usage.price_call(
            model="gpt-5.6-luna",
            provider_mode=PROVIDER_MODE_LOCAL,
            input_tokens=1_000,
            output_tokens=100,
        )
        self.assertIsNone(cost)
        self.assertEqual(status, usage.PRICING_STATUS_NOT_BILLED)

    def test_price_call_agrees_with_the_logged_row(self) -> None:
        """The cost report and the usage ledger must not disagree about a call."""

        response = _Response(60_935, 4_639, cached=5_000)
        response.service_tier = "flex"
        with TempBus() as tmp:
            row = _log(
                tmp,
                model="gpt-5.6-luna",
                provider_mode=PROVIDER_MODE_REMOTE,
                response=response,
            )
        cost, status = usage.price_call(
            model="gpt-5.6-luna",
            provider_mode=PROVIDER_MODE_REMOTE,
            input_tokens=60_935,
            cached_input_tokens=usage.response_cached_input_tokens(response),
            output_tokens=4_639,
            routed_endpoint=usage.response_routed_endpoint(response),
            service_tier=usage._response_service_tier(response),
        )
        self.assertEqual(cost, row["estimated_cost_usd"])
        self.assertEqual(status, row["pricing_status"])

    def test_the_cost_report_no_longer_hardcodes_none(self) -> None:
        """``estimated_cost`` was literally ``None`` on every row ever written."""

        source = (ROOT / "ai_gate.py").read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn('"estimated_cost": None', source)
        self.assertIn('"estimated_cost": cost_usd', source)
        self.assertIn("price_call(", source)

    def test_every_panel_role_reports_its_service_tier(self) -> None:
        """The critic and adjudicator dropped the tier -- 2 of every 3 calls.

        Scanned per call site rather than by counting occurrences: a repo-wide
        count is satisfied by any three sites passing it twice, which is exactly
        the state this test has to reject.
        """

        source = (ROOT / "ai_gate.py").read_text(encoding="utf-8", errors="ignore")
        sites = _call_argument_blocks(source, "log_ai_usage(")
        self.assertEqual(len(sites), 4, "call sites moved; re-check this test")
        for index, block in enumerate(sites):
            with self.subTest(site=index):
                self.assertIn(
                    "service_tier=",
                    block,
                    "this log_ai_usage call site prices at standard rates on a flex run",
                )


class CsvSchemaStabilityTests(unittest.TestCase):
    def test_pricing_status_does_not_enter_the_csv(self) -> None:
        """Two incompatible CSV layouts already coexist in the historical file.

        Adding a column mis-aligns every field after the insertion point for
        readers that assume a fixed layout -- the exact failure that made
        ``reasoning_effort`` parse as ``output_tokens``.  The field belongs in
        the NDJSON row only.
        """

        self.assertNotIn("pricing_status", usage.CSV_FIELDS)
        self.assertNotIn("routed_endpoint", usage.CSV_FIELDS)

        with TempBus() as tmp:
            _log(
                tmp,
                model="z-ai/glm-5.3-flash",
                provider_mode=PROVIDER_MODE_OPENROUTER,
                response=_Response(1000, 100, provider="DeepInfra"),
            )
            csv_path = tmp / "logs" / "openai_usage.csv"
            with csv_path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0], usage.CSV_FIELDS)
            self.assertEqual(len(rows[1]), len(usage.CSV_FIELDS))

            ndjson_path = tmp / "logs" / "openai_usage.ndjson"
            record = json.loads(ndjson_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["pricing_status"], usage.PRICING_STATUS_PRICED)
            self.assertGreater(record["estimated_cost_usd"], 0.0)


class TempBus:
    """Isolated bus directory so tests never touch the real usage log.

    ``conftest.disable_live_ai_usage_logs`` switches logging off for every test
    so a unit test cannot append synthetic calls to the production usage log.
    These tests have to exercise the write path itself, so logging is re-enabled
    for the duration -- but only while the bus points at a temporary directory,
    which preserves the isolation the fixture exists to guarantee.
    """

    def __enter__(self) -> Path:
        import os
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self._prev_bus = usage._BUS_PATH
        self._prev_enable = os.environ.get("AI_USAGE_LOG_ENABLE")
        os.environ["AI_USAGE_LOG_ENABLE"] = "true"
        return Path(self._tmp.name)

    def __exit__(self, *exc) -> None:
        import os

        usage._BUS_PATH = self._prev_bus
        if self._prev_enable is None:
            os.environ.pop("AI_USAGE_LOG_ENABLE", None)
        else:
            os.environ["AI_USAGE_LOG_ENABLE"] = self._prev_enable
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
