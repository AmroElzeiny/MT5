"""Rollover suspension and restore of managed pending limits (2026-09-14).

Before: the rollover trading freeze deleted every managed pending order and
nothing ever put it back, so a still-valid setup approved minutes before the
daily rollover was lost for good.

After: an identified pending limit removed by the freeze is recorded and, once
the rollover quote window has ended, re-placed only while

* the expiry fixed at its FIRST placement has not passed (a restore never
  extends the lifetime the original decision was given), and
* the price is still valid, measured on M1 bars from the removal to now with
  every bar inside the rollover quote window (default 23:58-01:05 server time)
  skipped, then re-checked live through the same final placement gates.

These assert on the MQL source the terminal compiles (the resolved include
tree), scoped to single functions so an unrelated function cannot satisfy them,
plus a faithful Python port of the window arithmetic.
"""
from __future__ import annotations

import re
import unittest

from test_governance_contracts import MQL_STAGE, _function_body

TRADE_ENGINE = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="replace")
CONFIG = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="replace")
STATE_STORE = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8", errors="replace")
TYPES = (MQL_STAGE / "Types.mqh").read_text(encoding="utf-8", errors="replace")

RESTORE_FIELDS = (
    "pending_first_placed_at",
    "pending_expires_at",
    "rollover_suspended_at",
    "rollover_suspended_ticket",
    "rollover_suspended_volume",
    "rollover_restore_count",
)


class RolloverInputsTests(unittest.TestCase):
    def test_restore_is_on_and_the_quote_window_is_2358_to_0105(self) -> None:
        self.assertRegex(CONFIG, r"input bool\s+InpRolloverRestorePendingOrders\s*=\s*true;")
        self.assertRegex(
            CONFIG, r'input string\s+InpRolloverQuoteExclusionStartServerTime\s*=\s*"23:58";'
        )
        self.assertRegex(
            CONFIG, r'input string\s+InpRolloverQuoteExclusionEndServerTime\s*=\s*"01:05";'
        )


class SuspensionTests(unittest.TestCase):
    def test_the_freeze_suspends_instead_of_deleting_and_restores_after_it(self) -> None:
        body = _function_body(TRADE_ENGINE, "MaintainRolloverProtection")
        self.assertIn("_SuspendManagedPendingOrdersForRollover(reason)", body)
        self.assertNotIn("_DeleteManagedPendingOrders(", body)
        freeze = body.index("PO3TradingFreezeActive(now, reason)")
        restore = body.index("_MaintainRolloverSuspendedPendingOrders(now)")
        self.assertLess(freeze, restore)
        # Restore is unreachable while the freeze is active.
        self.assertIn("return;", body[freeze:restore])

    def test_maintain_orders_routes_a_freeze_deletion_through_suspension(self) -> None:
        body = _function_body(TRADE_ENGINE, "MaintainOrders")
        block = body[body.index("PO3EntryBlockedByRolloverForSymbol(sym, now, rollover_reason)"):]
        self.assertLess(
            block.index("PO3TradingFreezeActive(now, freeze_reason)"),
            block.index("_SuspendPendingOrderForRollover(ticket, rollover_reason)"),
        )

    def test_suspension_records_only_identified_limits_after_a_confirmed_delete(self) -> None:
        body = _function_body(TRADE_ENGINE, "_SuspendPendingOrderForRollover")
        for token in (
            "missing_trade_metadata",
            "unsupported_order_type",
            "first_placement_expiry_unknown",
            "restore_disabled",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        deleted = body.index("bool deleted = m_trade.OrderDelete(ticket);")
        self.assertLess(deleted, body.index("if(!deleted) return false;"))
        self.assertLess(body.index("if(!deleted) return false;"), body.index("_UpsertRolloverSuspended(meta);"))
        self.assertIn("_PersistRolloverSuspendedPending();", body)
        # The live broker order, not the plan, is what gets re-placed.
        self.assertIn("meta.rollover_suspended_volume = order_volume;", body)
        self.assertIn("meta.entry_est = order_entry;", body)


class RestoreContractTests(unittest.TestCase):
    BODY = _function_body(TRADE_ENGINE, "_TryRestoreRolloverSuspended")

    def _before_placement(self, token: str) -> None:
        self.assertIn(token, self.BODY)
        self.assertLess(self.BODY.index(token), self.BODY.index("m_trade.BuyLimit("))

    def test_the_restored_order_keeps_the_first_placement_expiry(self) -> None:
        self.assertIn(
            "m_trade.BuyLimit(vol, entry, rec.symbol, rec.sl, rec.tp2, ORDER_TIME_SPECIFIED, rec.pending_expires_at, rec.broker_comment)",
            self.BODY,
        )
        self.assertIn(
            "m_trade.SellLimit(vol, entry, rec.symbol, rec.sl, rec.tp2, ORDER_TIME_SPECIFIED, rec.pending_expires_at, rec.broker_comment)",
            self.BODY,
        )
        self.assertNotIn("_PendingExpiryMinutes", self.BODY)
        self.assertNotRegex(self.BODY, r"pending_expires_at\s*=")
        self.assertNotRegex(self.BODY, r"pending_first_placed_at\s*=")

    def test_an_expired_lifetime_is_dropped_before_anything_else(self) -> None:
        expired = self.BODY.index("expired_since_first_placement")
        self.assertLess(expired, self.BODY.index("_RolloverSuspensionPriceVerdict("))
        self._before_placement("expired_since_first_placement")

    def test_price_validity_and_every_final_gate_precede_the_broker_call(self) -> None:
        for token in (
            "_RolloverSuspensionPriceVerdict(",
            "_PendingOrderStillValidEx(rec, gate_reason)",
            "_PlanTargetAlreadyReached(rec, target_reason)",
            "PO3EntryBlockedByRolloverForSymbol(rec.symbol, now, gate_reason)",
            "_ApplyBrokerSessionEntryGate(rec, gate_reason)",
            "_StopsDistanceOk(rec.symbol, rec.is_buy, entry, rec.sl, rec.tp2)",
            "_ManagedExposureWithCommentExists(rec.symbol, rec.broker_comment)",
            "_ApplyFinalPortfolioRiskGovernance(rec, new_risk, gate_reason)",
            "_CorrelatedExposureOk(rec, new_risk, gate_reason)",
            "CanPlaceOrderHardSafety(rec, gate_reason)",
            "_ExecutionFingerprintWithinTolerance(rec, fingerprint_changes)",
            "no_quote_after_rollover_window",
        ):
            with self.subTest(token=token):
                self._before_placement(token)

    def test_a_limit_is_never_placed_on_the_wrong_side_of_the_market(self) -> None:
        self._before_placement("live_price_beyond_limit_entry")

    def test_the_restored_order_is_rebound_to_its_new_ticket(self) -> None:
        after = self.BODY[self.BODY.index("m_trade.BuyLimit("):]
        self.assertIn("rec.result_order_ticket = new_ticket;", after)
        self.assertIn("_WriteTradeMeta(rec, new_ticket);", after)
        self.assertIn("rec.rollover_restore_count++;", after)


class ExcludedQuotesTests(unittest.TestCase):
    BODY = _function_body(TRADE_ENGINE, "_RolloverSuspensionPriceVerdict")

    def test_bars_inside_the_window_are_skipped_before_any_price_check(self) -> None:
        skip = self.BODY.index(
            "_MinuteInsideDailyWindow(_ServerMinuteOfDay(rates[i].time), ex_start, ex_end)"
        )
        cont = self.BODY.index("continue;", skip)
        for token in (
            "stop_touched_during_suspension",
            "target_reached_during_suspension",
            "structural_invalidation_low_during_suspension",
            "structural_invalidation_high_during_suspension",
        ):
            with self.subTest(token=token):
                self.assertLess(cont, self.BODY.index(token))

    def test_unsynchronized_history_waits_instead_of_passing(self) -> None:
        self.assertRegex(self.BODY, r'reason = "m1_history_unavailable";\s*return 0;')
        self.assertRegex(
            self.BODY, r'reason = "m1_history_not_synchronized_past_rollover_window";\s*return 0;'
        )

    def test_a_sell_is_measured_on_the_ask(self) -> None:
        self.assertIn("rates[i].high + spread_px", self.BODY)


class PersistenceTests(unittest.TestCase):
    def test_every_restore_field_is_declared_and_round_tripped(self) -> None:
        for field in RESTORE_FIELDS:
            with self.subTest(field=field):
                self.assertRegex(TYPES, rf"\b{field};")
                self.assertRegex(STATE_STORE, rf'JsonKV(?:Int|Num)\("{field}"')
                self.assertRegex(STATE_STORE, rf'JsonGetNumber\(json, "{field}"')

    def test_first_placement_fixes_the_lifetime(self) -> None:
        self.assertIn("pending.pending_first_placed_at = pending.planned_at;", TRADE_ENGINE)
        self.assertIn("pending.pending_expires_at = expiry;", TRADE_ENGINE)

    def test_records_survive_a_live_restart_and_never_leak_into_the_tester(self) -> None:
        self.assertIn(
            "!MQLInfoInteger(MQL_TESTER) && m_state.LoadPlans(m_state.RolloverSuspendedPendingPath(), tmp)",
            TRADE_ENGINE,
        )
        self.assertIn("_PersistRolloverSuspendedPending();", _function_body(TRADE_ENGINE, "Deinit"))
        self.assertIn('_ScopedPath("rollover_suspended_pending.ndjson")', STATE_STORE)


def _minute_of_day(t: int) -> int:
    return (t % 86400) // 60


def _inside(minute: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _window_end_after(t: int, start: int, end: int) -> int:
    """Port of _RolloverQuoteWindowEndAfter."""

    day_start = t - (t % 86400)
    if not _inside(_minute_of_day(t), start, end):
        start_at = day_start + start * 60
        if start_at < t:
            start_at += 86400
        end_at = start_at - (start_at % 86400) + end * 60
        if end_at <= start_at:
            end_at += 86400
        return end_at
    end_today = day_start + end * 60
    if end_today <= t:
        end_today += 86400
    return end_today


class WindowArithmeticTests(unittest.TestCase):
    DAY = 20_000 * 86400  # an arbitrary server-day boundary

    def at(self, hh: int, mm: int, day: int = 0) -> int:
        return self.DAY + day * 86400 + hh * 3600 + mm * 60

    def test_the_mql_port_is_the_function_in_the_source(self) -> None:
        body = _function_body(TRADE_ENGINE, "_RolloverQuoteWindowEndAfter")
        for token in (
            "long day_start = (long)t - ((long)t % 86400);",
            "if(start_at < (long)t) start_at += 86400;",
            "if(end_at <= start_at) end_at += 86400;",
            "if(end_today <= (long)t) end_today += 86400;",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_window_end_after_the_freeze_removal(self) -> None:
        s, e = 23 * 60 + 58, 1 * 60 + 5
        cases = (
            (self.at(23, 54), self.at(1, 5, 1)),   # removed at freeze start
            (self.at(23, 59), self.at(1, 5, 1)),   # inside, before midnight
            (self.at(0, 30), self.at(1, 5)),       # inside, after midnight
            (self.at(12, 0), self.at(1, 5, 1)),    # midday
        )
        for t, expected in cases:
            with self.subTest(t=t):
                self.assertEqual(_window_end_after(t, s, e), expected)

    def test_non_wrapping_window(self) -> None:
        s, e = 10 * 60, 11 * 60
        self.assertEqual(_window_end_after(self.at(9, 0), s, e), self.at(11, 0))
        self.assertEqual(_window_end_after(self.at(10, 30), s, e), self.at(11, 0))
        self.assertEqual(_window_end_after(self.at(12, 0), s, e), self.at(11, 0, 1))

    def test_excluded_minutes(self) -> None:
        s, e = 23 * 60 + 58, 1 * 60 + 5
        self.assertFalse(_inside(23 * 60 + 57, s, e))
        self.assertTrue(_inside(23 * 60 + 58, s, e))
        self.assertTrue(_inside(0, s, e))
        self.assertTrue(_inside(1 * 60 + 4, s, e))
        self.assertFalse(_inside(1 * 60 + 5, s, e))


if __name__ == "__main__":
    unittest.main()
