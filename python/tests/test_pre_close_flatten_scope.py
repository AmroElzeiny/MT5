"""Contract tests for the scoped pre-close flatten.

The behaviour being pinned: the daily pre-close close-out is switchable, and
when it is scoped to forex it must not reach any other product class -- not
through the position sweep, not through the pending-order sweep, and not
through the whole-scan freeze that would stop those products trading anyway.

The last one is the trap.  Scoping only the sweep would leave the scan frozen
book-wide for a close-out that no longer applies to most of the book, which
turns a narrowed rule into a broader outage.

All tests were falsified against the pre-change include tree.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_governance_contracts import MQL_STAGE, _function_body


def _read(name: str) -> str:
    return (MQL_STAGE / name).read_text(encoding="utf-8", errors="replace")


class PreCloseFlattenSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _read("Config.mqh")
        self.risk = _read("Risk.mqh")
        self.engine = _read("TradeEngine.mqh")

    def test_both_inputs_exist_and_default_to_the_requested_posture(self):
        """Off by default, and forex-only when it is switched on."""

        self.assertRegex(
            self.config, r"input\s+bool\s+InpPreCloseFlattenEnable\s*=\s*false\s*;"
        )
        self.assertRegex(
            self.config, r"input\s+bool\s+InpPreCloseFlattenForexOnly\s*=\s*true\s*;"
        )

    def test_the_switch_gates_the_window_independently_of_the_minute_count(self):
        """A configured minute count must not be read as intent to enable."""

        body = _function_body(self.risk, "PO3PreCloseFlattenActive")
        self.assertIn("if(!InpPreCloseFlattenEnable) return false;", body)
        enable = body.index("InpPreCloseFlattenEnable")
        minutes = body.index("InpCloseManagedTradesBeforeMarketCloseMin")
        self.assertLess(
            enable, minutes, "the switch must be checked before the window is sized"
        )


class PreCloseFlattenScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.risk = _read("Risk.mqh")
        self.engine = _read("TradeEngine.mqh")

    def test_forex_classification_reads_broker_metadata_not_the_symbol_text(self):
        body = _function_body(self.risk, "PO3SymbolIsForexPair")
        for probe in (
            "SYMBOL_TRADE_CALC_MODE",
            "SYMBOL_CURRENCY_BASE",
            "SYMBOL_CURRENCY_PROFIT",
            "SYMBOL_CALC_MODE_FOREX",
        ):
            self.assertIn(probe, body, f"{probe} is not consulted")

    def test_metals_and_crypto_cannot_pass_on_calc_mode_alone(self):
        """Brokers routinely set spot metals to forex calc mode.

        The currency test is what excludes them, so it must be an allowlist of
        fiat codes -- an allowlist rejects XAU/XAG/BTC and anything invented
        later, where a denylist would admit whatever it forgot.
        """

        body = _function_body(self.risk, "PO3SymbolIsForexPair")
        self.assertIn("_IsFiatCurrencyCode(base)", body)
        self.assertIn("_IsFiatCurrencyCode(profit)", body)

        allowlist = _function_body(self.risk, "_IsFiatCurrencyCode")
        for excluded in ("XAU", "XAG", "XPT", "XPD", "BTC", "ETH"):
            self.assertNotIn(
                excluded, allowlist, f"{excluded} must not be a recognised fiat code"
            )
        for required in ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"):
            self.assertIn(required, allowlist)

    def test_unresolvable_symbol_metadata_is_not_assumed_to_be_forex(self):
        body = _function_body(self.risk, "PO3SymbolIsForexPair")
        self.assertIn("symbol_metadata_unavailable", body)
        marker = body.index("symbol_metadata_unavailable")
        self.assertIn("return false;", body[marker : marker + 200])

    def test_a_two_fiat_cfd_is_still_refused(self):
        """The tradable must actually be the BASEPROFIT pair."""

        body = _function_body(self.risk, "PO3SymbolIsForexPair")
        self.assertIn("symbol_is_not_base_profit_pair", body)
        self.assertIn('StringFind(upper_symbol, base + profit) != 0', body)

    def test_every_classification_path_writes_a_reason(self):
        """A refusal that cannot say which signal failed is undiagnosable."""

        body = _function_body(self.risk, "PO3SymbolIsForexPair")
        returns = re.findall(r"return (?:false|true);", body)
        assignments = re.findall(r"classification\s*=", body)
        self.assertGreaterEqual(
            len(assignments),
            len(returns),
            "a return path exists that never sets classification",
        )

    def test_the_position_sweep_honours_the_scope_and_journals_what_it_keeps(self):
        body = _function_body(self.risk, "_FlattenManagedExposureScoped")
        self.assertIn("PO3SymbolInPreCloseFlattenScope(sym, classification)", body)
        self.assertIn("out_of_pre_close_scope", body)
        self.assertIn("continue;", body)

    def test_the_pending_order_sweep_honours_the_same_scope(self):
        body = _function_body(self.risk, "_DeleteManagedPendingOrdersScoped")
        self.assertIn("PO3SymbolInPreCloseFlattenScope(sym, classification)", body)
        self.assertIn("out_of_pre_close_scope", body)

    def test_the_global_stop_is_never_scoped(self):
        """A risk emergency must keep flattening the entire managed book."""

        body = _function_body(self.risk, "_FlattenManagedExposureWithReason")
        self.assertIn("false", body)
        self.assertNotIn("true)", body.replace("scope_to_flatten_symbols", ""))

        global_stop = _function_body(self.risk, "_FlattenManagedExposure")
        self.assertIn("global_stop", global_stop)

    def test_only_the_rollover_maintenance_path_asks_for_the_scoped_sweep(self):
        scoped_calls = re.findall(r"_FlattenManagedExposureScoped\([^;]*?,\s*true\s*\)", self.engine)
        self.assertEqual(
            len(scoped_calls), 1, "exactly one caller may request the scoped sweep"
        )
        body = _function_body(self.engine, "MaintainRolloverProtection")
        self.assertIn("_FlattenManagedExposureScoped(m_trade, reason, true)", body)


class PreCloseEntryBlockScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.risk = _read("Risk.mqh")
        self.engine = _read("TradeEngine.mqh")

    def test_the_symbol_scoped_entry_block_exists_and_consults_the_scope(self):
        body = _function_body(self.risk, "PO3EntryBlockedByRolloverForSymbol")
        self.assertIn("PO3PreCloseFlattenActive(now, reason)", body)
        self.assertIn("PO3SymbolInPreCloseFlattenScope(symbol, classification)", body)
        self.assertIn("PO3TradingFreezeActive(now, reason)", body)

    def test_the_trading_freeze_half_stays_global(self):
        """The freeze is about spread and liquidity, not about flattening."""

        body = _function_body(self.risk, "PO3EntryBlockedByRolloverForSymbol")
        freeze = body.index("PO3TradingFreezeActive")
        scope = body.index("PO3SymbolInPreCloseFlattenScope")
        self.assertLess(
            scope, freeze, "the scope test must apply to the pre-close half only"
        )

    def test_placement_and_pending_maintenance_use_the_symbol_scoped_block(self):
        for site in (
            "PO3EntryBlockedByRolloverForSymbol(p.symbol, _NowServerOrLocal(), rollover_reason)",
            "PO3EntryBlockedByRolloverForSymbol(sym, now, rollover_reason)",
        ):
            self.assertIn(site, self.engine, f"call site not scoped: {site}")

    def test_no_engine_call_site_still_uses_the_unscoped_entry_block(self):
        """The unscoped helper may survive for compatibility, unused here."""

        unscoped = re.findall(r"(?<!ForSymbol)\bPO3EntryBlockedByRollover\(", self.engine)
        self.assertEqual(
            unscoped, [], "an engine call site still blocks entries book-wide"
        )

    def test_the_whole_scan_freeze_does_not_fire_on_a_forex_only_pre_close(self):
        """Scoping the sweep but not the scan would be a broader outage.

        A forex-only close-out must not stop the EA scanning crypto, indices or
        metals: those symbols are blocked individually at placement time, which
        is the narrow enforcement the scope asks for.
        """

        body = _function_body(self.engine, "EntryFreezeActive")
        self.assertIn("InpPreCloseFlattenForexOnly", body)
        self.assertRegex(
            body,
            r"if\(!InpPreCloseFlattenForexOnly\s*&&\s*PO3PreCloseFlattenActive\(",
        )
        self.assertIn("PO3TradingFreezeActive(now, reason)", body)


if __name__ == "__main__":
    unittest.main()
