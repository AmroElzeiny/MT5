"""Contract tests for the MQL test order adapter and its injection point.

Python cannot compile MQL, so these tests assert on the MQL sources directly.
That is worth doing because the adapter's whole value depends on two properties
that are easy to break silently:

1. It must cover every CTrade method TradeEngine actually calls, or a harness
   run would take the real broker path for the uncovered one and the journal
   would under-report what happened.
2. It must be invisible to production. A production compile never defines
   PO3_TEST_ORDER_ADAPTER, and nothing outside an ``#ifdef`` may reference the
   adapter, or the production binary changes.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

MQL_ROOT = Path(__file__).resolve().parents[2] / "MT5_PO3_Codex Include"
EA_ROOT = Path(__file__).resolve().parents[2] / "MT5_PO3_Codex Experts"

TRADE_ENGINE = MQL_ROOT / "TradeEngine.mqh"
ADAPTER = MQL_ROOT / "TestOrderAdapter.mqh"
HARNESS_EA = EA_ROOT / "PO3_AIGate_PositivePath_Harness.mq5"
PRODUCTION_EA = EA_ROOT / "PO3_AIGate_ScannerEA.mq5"

# Methods inherited unchanged from CTrade. These are exposure-maintenance and
# configuration calls, deliberately NOT shadowed: the harness must not alter how
# existing positions are flattened or how the trade object is configured.
INHERITED_FROM_CTRADE = {
    "SetExpertMagicNumber",
    "SetDeviationInPoints",
    "SetTypeFilling",
    "SetAsyncMode",
    "LogLevel",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


class AdapterCoverageTests(unittest.TestCase):
    """The adapter must not silently miss a broker method TradeEngine uses."""

    def setUp(self) -> None:
        self.engine = _read(TRADE_ENGINE)
        self.adapter = _read(ADAPTER)

    def test_trade_engine_calls_are_all_covered(self) -> None:
        used = set(re.findall(r"m_trade\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", self.engine))
        self.assertTrue(used, "expected to find m_trade calls in TradeEngine")

        uncovered = []
        for method in sorted(used):
            if method in INHERITED_FROM_CTRADE:
                continue
            if re.search(rf"\b{re.escape(method)}\s*\(", self.adapter):
                continue
            uncovered.append(method)

        self.assertEqual(
            uncovered,
            [],
            f"TradeEngine calls m_trade.{uncovered} but the adapter does not "
            "shadow them; a harness run would bypass the journal for those.",
        )

    def test_order_submission_methods_are_shadowed(self) -> None:
        """The four entry-path methods must be intercepted, not inherited."""
        for method in ("Buy", "Sell", "BuyLimit", "SellLimit"):
            with self.subTest(method=method):
                self.assertRegex(
                    self.adapter,
                    rf"bool\s+{method}\s*\(",
                    f"{method} must be shadowed so every attempt is journalled",
                )

    def test_result_accessors_are_shadowed(self) -> None:
        """Callers read results through the concrete type in both modes."""
        for method in (
            "ResultRetcode",
            "ResultRetcodeDescription",
            "ResultOrder",
            "ResultDeal",
            "ResultVolume",
            "ResultPrice",
        ):
            with self.subTest(method=method):
                self.assertIn(method, self.adapter)


class AdapterSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = _read(ADAPTER)
        self.engine = _read(TRADE_ENGINE)

    def test_adapter_inherits_ctrade(self) -> None:
        """Required: three production helpers take a CTrade& reference."""
        self.assertRegex(self.adapter, r"class\s+CPO3TestTrade\s*:\s*public\s+CTrade")

    def test_live_account_is_blocked_from_passthrough(self) -> None:
        self.assertIn("LiveAccountBlocked", self.adapter)
        self.assertIn("ACCOUNT_TRADE_MODE_DEMO", self.adapter)
        self.assertIn("ACCOUNT_TRADE_MODE_CONTEST", self.adapter)
        # A real account must fall through to "return true" (blocked).
        block = re.search(
            r"bool\s+LiveAccountBlocked\s*\(\s*\)\s*const\s*\{(.+?)\n   \}",
            self.adapter,
            re.S,
        )
        self.assertIsNotNone(block, "LiveAccountBlocked body not found")
        self.assertIn("return true", block.group(1))

    def test_binding_downgrades_passthrough_on_live_account(self) -> None:
        self.assertRegex(
            self.adapter,
            r"if\(LiveAccountBlocked\(\).*?\)\s*\{\s*\n\s*m_mode\s*=\s*PO3_ADAPTER_MODE_INTERCEPT",
        )

    def test_intercept_mode_never_calls_the_broker(self) -> None:
        """In INTERCEPT the base CTrade methods must not be reached."""
        for block in re.findall(
            r"if\(m_mode\s*==\s*PO3_ADAPTER_MODE_INTERCEPT\)\s*\{(.+?)\n      \}",
            self.adapter,
            re.S,
        ):
            self.assertNotIn(
                "CTrade::",
                block,
                "INTERCEPT branch must not delegate to the real CTrade",
            )

    def test_every_attempt_is_journalled(self) -> None:
        for helper in ("_Market", "_Pending"):
            body = re.search(
                rf"bool\s+{helper}\(.*?\n   \}}", self.adapter, re.S
            )
            self.assertIsNotNone(body, f"{helper} not found")
            self.assertIn("Journal(", body.group(0))
            self.assertIn("m_attempts++", body.group(0))


class ExposureCoverageTests(unittest.TestCase):
    """No inherited CTrade method may create exposure without journalling it.

    The adapter derives from CTrade, so any exposure-creating method it does not
    shadow silently reaches the broker and never appears in the attempt journal.
    Coverage is therefore defined by exposure semantics, not by which methods
    TradeEngine happens to call today.
    """

    # Every CTrade method that can open a position or place a pending order.
    EXPOSURE_CREATING = (
        "Buy",
        "Sell",
        "BuyLimit",
        "SellLimit",
        "BuyStop",
        "SellStop",
        "PositionOpen",
        "OrderOpen",
    )

    def setUp(self) -> None:
        self.adapter = _read(ADAPTER)

    def test_all_exposure_creating_methods_are_shadowed(self) -> None:
        for method in self.EXPOSURE_CREATING:
            with self.subTest(method=method):
                self.assertRegex(
                    self.adapter,
                    rf"bool\s+{method}\s*\(",
                    f"{method} can create exposure but is not shadowed; a call "
                    "would reach the broker unjournalled",
                )

    def test_shadowed_exposure_methods_route_through_journalled_helpers(self) -> None:
        for method in self.EXPOSURE_CREATING:
            with self.subTest(method=method):
                body = re.search(
                    rf"bool\s+{method}\s*\([^)]*\)\s*\{{(.*?)\n   \}}",
                    self.adapter,
                    re.S,
                )
                self.assertIsNotNone(body, f"{method} body not found")
                self.assertRegex(
                    body.group(1),
                    r"_Market\(|_Pending\(",
                    f"{method} must route through _Market/_Pending so it is "
                    "counted and journalled",
                )

    def test_ctrade_gains_no_uncovered_exposure_method(self) -> None:
        """Fails if the installed CTrade exposes a new exposure-creating method.

        Guards against an MT5 build adding e.g. BuyStopLimit and the adapter
        silently not covering it.
        """
        trade_mqh = (
            Path(os.environ["APPDATA"])
            / "MetaQuotes/Terminal/0148BD5691B65B0F2157627A4231F3DE"
            / "MQL5/Include/Trade/Trade.mqh"
        )
        if not trade_mqh.is_file():
            self.skipTest("terminal Trade.mqh not available on this machine")

        declared = set(
            re.findall(
                r"bool\s+(Buy\w*|Sell\w*|PositionOpen|OrderOpen)\s*\(",
                _read(trade_mqh),
            )
        )
        uncovered = sorted(
            name
            for name in declared
            if name not in self.EXPOSURE_CREATING
            and not re.search(rf"bool\s+{name}\s*\(", self.adapter)
        )
        self.assertEqual(
            uncovered,
            [],
            f"CTrade declares exposure-creating {uncovered} that the adapter "
            "does not shadow; add them to the adapter and to EXPOSURE_CREATING",
        )

    def test_exposure_maintenance_methods_cannot_open_new_exposure(self) -> None:
        """Inherited-by-design methods must only reduce or modify exposure."""
        for method in ("OrderDelete", "PositionClose", "PositionClosePartial", "PositionModify", "OrderModify"):
            with self.subTest(method=method):
                body = re.search(
                    rf"bool\s+{method}\s*\([^)]*\)\s*\{{(.*?)\n   \}}",
                    self.adapter,
                    re.S,
                )
                self.assertIsNotNone(body, f"{method} body not found")
                self.assertNotRegex(
                    body.group(1),
                    r"_Market\(|_Pending\(",
                    f"{method} is a maintenance call and must not create exposure",
                )


class ProductionIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _read(TRADE_ENGINE)
        self.production_ea = _read(PRODUCTION_EA)
        self.harness_ea = _read(HARNESS_EA)

    def test_production_ea_does_not_define_the_adapter_macro(self) -> None:
        self.assertNotIn("PO3_TEST_ORDER_ADAPTER", self.production_ea)

    def test_harness_ea_defines_macro_before_including_production_ea(self) -> None:
        define_at = self.harness_ea.index("#define PO3_TEST_ORDER_ADAPTER")
        include_at = self.harness_ea.index("PO3_AIGate_ScannerEA.mq5")
        self.assertLess(
            define_at,
            include_at,
            "the macro must be defined before TradeEngine.mqh is pulled in",
        )

    def test_harness_ea_includes_production_ea_rather_than_forking_it(self) -> None:
        """A forked copy would drift from what actually ships."""
        self.assertRegex(self.harness_ea, r'#include\s+"PO3_AIGate_ScannerEA\.mq5"')
        # The harness must not redefine the production entry points.
        for symbol in ("int OnInit", "void OnTimer", "void OnTick", "void OnDeinit"):
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, self.harness_ea)

    def test_engine_selects_trade_class_by_macro(self) -> None:
        self.assertIn("#ifdef PO3_TEST_ORDER_ADAPTER", self.engine)
        self.assertIn("#define PO3_TRADE_CLASS CPO3TestTrade", self.engine)
        self.assertIn("#define PO3_TRADE_CLASS CTrade", self.engine)
        self.assertRegex(self.engine, r"\n\s*PO3_TRADE_CLASS m_trade;")

    def test_no_unguarded_adapter_reference_in_engine(self) -> None:
        """Every adapter mention must sit inside an #ifdef block."""
        guarded_spans: list[tuple[int, int]] = []
        depth = 0
        start = 0
        for match in re.finditer(
            r"^\s*#(ifdef\s+PO3_TEST_ORDER_ADAPTER|if|ifdef|ifndef|else|endif)",
            self.engine,
            re.M,
        ):
            token = match.group(1)
            if token.startswith("ifdef PO3_TEST_ORDER_ADAPTER"):
                if depth == 0:
                    start = match.start()
                depth += 1
            elif depth > 0:
                if token.startswith(("if", "ifdef", "ifndef")):
                    depth += 1
                elif token == "endif":
                    depth -= 1
                    if depth == 0:
                        guarded_spans.append((start, match.end()))

        for match in re.finditer(r"CPO3TestTrade|HarnessBindOrderAdapter|BindHarness", self.engine):
            pos = match.start()
            inside = any(lo <= pos <= hi for lo, hi in guarded_spans)
            self.assertTrue(
                inside,
                f"unguarded adapter reference at offset {pos}: {match.group(0)}",
            )


class HarnessFunnelReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness_ea = _read(HARNESS_EA)
        self.engine = _read(TRADE_ENGINE)

    def test_harness_reports_the_required_acceptance_markers(self) -> None:
        for marker in ("watchlist_added=", "order_attempted=", "positive_path_reached="):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.harness_ea)

    def test_positive_verdict_requires_both_conditions(self) -> None:
        self.assertRegex(
            self.harness_ea,
            r"positive_path_reached\s*=\s*\(watchlist_added\s*>\s*0\s*&&\s*order_attempts\s*>\s*0\)",
        )

    def test_engine_exposes_counters_used_by_the_report(self) -> None:
        for accessor in (
            "HarnessWatchlistAdded",
            "HarnessOrderAttempts",
            "HarnessOrdersAccepted",
            "HarnessOrdersRejected",
            "HarnessOrderCountersJson",
        ):
            with self.subTest(accessor=accessor):
                self.assertIn(accessor, self.engine)
                self.assertIn(accessor, self.harness_ea)


if __name__ == "__main__":
    unittest.main()

