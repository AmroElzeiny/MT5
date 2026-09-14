"""OpenCode Go calls must be recorded at their real published price.

Before 2026-09-14 the usage ledger had no row for any OpenCode Go model, so every
live gate call was ``pricing_status=unpriced_model`` at ``estimated_cost_usd=0.0``
-- 2,499 ``muse-spark-1.3-contributor`` rows and 42 ``deepseek-v4.1-flash`` rows
in the live ledger -- even though ``opencode_go_accounting`` already carried the
published rates.  Rates: https://opencode.ai/docs/go/ ("Last updated: Sep 13, 2026").
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import opencode_go_accounting as go  # noqa: E402
import openai_usage_logger as usage  # noqa: E402
from ai_provider import _OpenCodeMessagesClient  # noqa: E402

from test_usage_cost_accounting import TempBus, _log  # noqa: E402

MUSE = "muse-spark-1.3-contributor"
DEEPSEEK = "deepseek-v4.1-flash"
QWEN = "qwen3.8-flash"
OPENCODE = "OPENCODE_API"

# Monday 2026-09-14.  02:30 UTC is inside the published 01:00-04:00 peak window.
MONDAY_PEAK = datetime(2026, 9, 14, 2, 30, tzinfo=timezone.utc)
MONDAY_OFF_PEAK = datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc)
SATURDAY_0230 = datetime(2026, 9, 12, 2, 30, tzinfo=timezone.utc)


class _ResponsesUsage:
    """A Responses-API shaped response, as the OpenCode transport returns it."""

    def __init__(self, inp: int, out: int, cached: int = 0, cache_write: int = 0) -> None:
        self.id = "resp_go"
        self.usage = {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": cache_write},
            "output_tokens_details": {"reasoning_tokens": out // 2},
        }


class PublishedRateTests(unittest.TestCase):
    def test_muse_is_priced_at_its_published_rate(self) -> None:
        self.assertEqual(
            usage._pricing_status(MUSE, OPENCODE), usage.PRICING_STATUS_PRICED
        )
        cost = usage._estimated_cost(MUSE, 1_000_000, 250_000, 500_000)
        # 0.75M uncached * $0.10 + 0.25M cached * $0.002 + 0.5M out * $0.20
        self.assertAlmostEqual(cost, 0.075 + 0.0005 + 0.10, places=8)

    def test_the_usage_ledger_and_the_attempt_ledger_agree_on_every_go_model(self) -> None:
        """One published table: the two cost surfaces cannot drift apart."""

        for model, variants in go.OPENCODE_GO_PRICES.items():
            categories = {
                "usage_exposed": True,
                "uncached_input_tokens": 700_000,
                "cached_read_tokens": 200_000,
                "cache_write_tokens": 100_000,
                "output_tokens": 300_000,
            }
            expected = go.expected_go_usage_usd(model, categories)
            for variant in variants:
                with self.subTest(model=model, variant=variant):
                    when = MONDAY_PEAK if variant == "peak" else SATURDAY_0230
                    cost = usage._estimated_cost(model, 1_000_000, 200_000, 300_000, "", "", 100_000, when)
                    self.assertAlmostEqual(cost, expected[variant], places=8)

    def test_a_logged_muse_call_records_a_real_cost(self) -> None:
        with TempBus() as tmp:
            row = _log(
                tmp,
                model=MUSE,
                provider_mode=OPENCODE,
                provider_id="opencode_go_responses",
                endpoint_class="opencode_go",
                response=_ResponsesUsage(37_000, 12_000, cached=7_153),
            )
            written = json.loads((tmp / "logs" / "openai_usage.ndjson").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(row["pricing_status"], usage.PRICING_STATUS_PRICED)
        expected = ((37_000 - 7_153) * 0.10 + 7_153 * 0.002 + 12_000 * 0.20) / 1_000_000.0
        self.assertAlmostEqual(row["estimated_cost_usd"], round(expected, 8), places=8)
        self.assertGreater(row["estimated_cost_usd"], 0.0)
        self.assertEqual(written["pricing_status"], usage.PRICING_STATUS_PRICED)
        self.assertEqual(written["pricing_variant"], "standard")


class TimeOfDayTariffTests(unittest.TestCase):
    """DeepSeek V4.1 Flash bills double in the published peak window."""

    def test_the_published_peak_window(self) -> None:
        self.assertTrue(go.is_go_peak_time(datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)))
        self.assertTrue(go.is_go_peak_time(datetime(2026, 9, 14, 9, 59, tzinfo=timezone.utc)))
        self.assertFalse(go.is_go_peak_time(datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc)))
        self.assertFalse(go.is_go_peak_time(datetime(2026, 9, 14, 5, 30, tzinfo=timezone.utc)))
        self.assertFalse(go.is_go_peak_time(datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)))
        self.assertFalse(go.is_go_peak_time(SATURDAY_0230), "weekends are off-peak all day")
        self.assertFalse(go.is_go_peak_time(datetime(2026, 9, 13, 7, 0, tzinfo=timezone.utc)))

    def test_the_rate_in_force_prices_the_call(self) -> None:
        peak = usage._estimated_cost(DEEPSEEK, 1_000_000, 0, 1_000_000, called_at=MONDAY_PEAK)
        off_peak = usage._estimated_cost(DEEPSEEK, 1_000_000, 0, 1_000_000, called_at=MONDAY_OFF_PEAK)
        self.assertAlmostEqual(peak, 0.30 + 1.20, places=8)
        self.assertAlmostEqual(off_peak, 0.15 + 0.60, places=8)
        self.assertEqual(
            usage._pricing_status(DEEPSEEK, OPENCODE, called_at=MONDAY_PEAK),
            usage.PRICING_STATUS_PRICED,
        )

    def test_an_unknown_instant_is_charged_the_upper_bound_and_says_so(self) -> None:
        status = usage._pricing_status(DEEPSEEK, OPENCODE)
        self.assertEqual(status, usage.PRICING_STATUS_UPPER_BOUND)
        self.assertIn(status, usage.PRICING_STATUSES_WITH_COST)
        unknown = usage._estimated_cost(DEEPSEEK, 1_000_000, 0, 1_000_000)
        for when in (MONDAY_PEAK, MONDAY_OFF_PEAK, SATURDAY_0230):
            with self.subTest(when=when.isoformat()):
                self.assertGreaterEqual(
                    unknown, usage._estimated_cost(DEEPSEEK, 1_000_000, 0, 1_000_000, called_at=when)
                )


class CacheWriteTests(unittest.TestCase):
    def test_a_published_cached_write_rate_is_applied(self) -> None:
        """Qwen writes the prompt cache at $0.20/M -- above its $0.15 input rate."""

        cost = usage._estimated_cost(QWEN, 1_000_000, 0, 0, cache_write_tokens=1_000_000)
        self.assertAlmostEqual(cost, 0.20, places=8)

    def test_models_without_a_cached_write_rate_bill_it_exactly_as_before(self) -> None:
        """A cache write used to sit inside billable input at the input rate."""

        for model in ("gpt-5.6-luna", MUSE):
            with self.subTest(model=model):
                rates = usage.PRICE_PER_MILLION[model]
                before = ((60_000 - 10_000) * rates["input"] + 10_000 * rates["cached_input"] + 4_000 * rates["output"]) / 1_000_000.0
                after = usage._estimated_cost(model, 60_000, 10_000, 4_000, cache_write_tokens=20_000)
                self.assertAlmostEqual(after, round(before, 8), places=8)

    def test_the_messages_leg_no_longer_drops_its_cached_tokens(self) -> None:
        """Anthropic-dialect usage reports cache tokens BESIDE input_tokens."""

        def transport(url, body, headers, timeout):
            return {
                "id": "msg_1",
                "model": body["model"],
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"ok":true}'}],
                "usage": {
                    "input_tokens": 1_000,
                    "cache_read_input_tokens": 30_000,
                    "cache_creation_input_tokens": 2_000,
                    "output_tokens": 500,
                },
            }

        client = _OpenCodeMessagesClient(
            api_key="oc-test-key", timeout=30.0, base_url="https://opencode.ai/zen/go/v1", transport=transport
        )
        response = client.chat.completions.create(
            model=QWEN, max_tokens=64, messages=[{"role": "user", "content": "x"}]
        )
        self.assertEqual(response["usage"]["prompt_tokens"], 33_000)
        self.assertEqual(
            response["usage"]["prompt_tokens_details"],
            {"cached_tokens": 30_000, "cache_write_tokens": 2_000},
        )
        self.assertEqual(response["usage"]["total_tokens"], 33_500)
        cost, status = usage.price_call(
            model=QWEN,
            provider_mode=OPENCODE,
            input_tokens=response["usage"]["prompt_tokens"],
            cached_input_tokens=usage.response_cached_input_tokens(response),
            output_tokens=response["usage"]["completion_tokens"],
            cache_write_tokens=usage.response_cache_write_tokens(response),
        )
        self.assertEqual(status, usage.PRICING_STATUS_PRICED)
        expected = (1_000 * 0.15 + 30_000 * 0.016 + 2_000 * 0.20 + 500 * 0.47) / 1_000_000.0
        self.assertAlmostEqual(cost, round(expected, 8), places=8)


if __name__ == "__main__":
    unittest.main()
