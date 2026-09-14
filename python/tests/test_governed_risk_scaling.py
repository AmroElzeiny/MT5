"""Governed risk scaling works end to end, downward and upward.

``ResolveGovernedRiskMoney`` is executed from the real Risk.mqh text through
``mql_pure_eval``; the constants come from the real Config.mqh.
"""

from __future__ import annotations

import re
import unittest
from unittest import mock

import expectancy_report
from mql_pure_eval import load_mql_function, mql_constants, read_repo_mql, function_body

EQUITY = 10_000.0


class GovernedRiskScalingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = read_repo_mql("Config.mqh")
        cls.risk = read_repo_mql("Risk.mqh")
        cls.engine = read_repo_mql("TradeEngine.mqh")
        cls.constants = mql_constants(cls.config)
        cls.resolve = staticmethod(load_mql_function(cls.risk, "ResolveGovernedRiskMoney", cls.constants))

    def _run(self, base=100.0, *, cap=False, remaining=0.0, equity=EQUITY, hard_pct=2.5,
             subtype_context_session=1.0, session=1.0, active=1.0, bucket=1.0, ai=1.0,
             exec_reduced=False, exec_mult=1.0):
        ok, out = self.resolve(base, cap, remaining, equity, hard_pct, subtype_context_session, session,
                               active, bucket, ai, exec_reduced, exec_mult)
        return ok, out["out_risk_money"], out["out_multiplier"], out["out_reason"]

    # 16
    def test_neutral_multiplier_keeps_base_risk(self) -> None:
        ok, risk, mult, reason = self._run(100.0)
        self.assertTrue(ok, reason)
        self.assertAlmostEqual(risk, 100.0)
        self.assertAlmostEqual(mult, 1.0)

    # 17
    def test_reducing_multiplier_decreases_risk(self) -> None:
        ok, risk, _mult, _ = self._run(100.0, subtype_context_session=0.5)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 50.0)
        ok, risk, _mult, _ = self._run(100.0, subtype_context_session=0.8, ai=0.5, exec_reduced=True, exec_mult=0.75)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 100.0 * 0.8 * 0.5 * 0.75)

    # 18
    def test_governed_session_upscale_increases_risk(self) -> None:
        ok, risk, mult, reason = self._run(100.0, subtype_context_session=1.15, session=1.15)
        self.assertTrue(ok, reason)
        self.assertAlmostEqual(mult, 1.15)
        self.assertAlmostEqual(risk, 115.0)

    def _generated_session_rows(self, strength_case: str) -> list[dict]:
        stats = {
            "count": 400, "avg_r": 0.40, "profit_factor": 1.60, "win_rate": 0.60, "max_drawdown_r": 2.0,
        } if strength_case == "upgrade" else {
            "count": 400, "avg_r": 0.18, "profit_factor": 1.20, "win_rate": 0.50, "max_drawdown_r": 2.0,
        }
        thresholds = {
            "session_weekday_min_trades": 10, "session_weekday_strong_trades": 20, "session_weekday_upgrade_trades": 30,
        }
        records = [{"session_name": "london", "weekday_name": "Tuesday"} for _ in range(3)]
        with mock.patch.object(expectancy_report, "_summarize", return_value=stats), \
                mock.patch.object(expectancy_report, "_split_recent", return_value=([], [])), \
                mock.patch.object(expectancy_report, "_decision_thresholds", return_value=thresholds), \
                mock.patch.object(expectancy_report, "_group_key", side_effect=lambda r, k: r[k]):
            return expectancy_report._session_weekday_policy(records)

    def _assert_row_reaches_execution(self, row: dict, expected: float) -> None:
        multiplier = float(row["risk_multiplier"])
        self.assertAlmostEqual(multiplier, expected, places=4)
        self.assertLessEqual(multiplier, self.constants["SESSION_WEEKDAY_RISK_MULTIPLIER_MAX"] + 1e-6)
        # Plan subtype multiplier = subtype 1.0 x context 1.0 x session.
        ok, risk, _mult, reason = self._run(100.0, subtype_context_session=multiplier, session=multiplier)
        self.assertTrue(ok, reason)
        self.assertAlmostEqual(risk, 100.0 * multiplier, places=4)

    # 19
    def test_generated_watch_for_upgrade_row_reaches_execution(self) -> None:
        rows = self._generated_session_rows("watch")
        self.assertEqual(rows[0]["action"], "watch_for_upgrade")
        self._assert_row_reaches_execution(rows[0], 1.05)

    # 20
    def test_generated_upgrade_row_reaches_execution(self) -> None:
        rows = self._generated_session_rows("upgrade")
        self.assertEqual(rows[0]["action"], "upgrade")
        self._assert_row_reaches_execution(rows[0], 1.15)

    def test_session_policy_consumers_accept_the_owner_range(self) -> None:
        self.assertAlmostEqual(
            self.constants["SESSION_WEEKDAY_RISK_MULTIPLIER_MAX"], expectancy_report.SESSION_WEEKDAY_RISK_MULTIPLIER_MAX
        )
        _params, parse = function_body(self.engine, "_ParseSessionWeekdayPolicyLine")
        self.assertIn("SESSION_WEEKDAY_RISK_MULTIPLIER_MAX", parse)
        self.assertNotIn("entry.risk_multiplier <= 1.0", parse)
        _params, apply = function_body(self.engine, "_ApplySessionWeekdayPolicy")
        self.assertIn("SESSION_WEEKDAY_RISK_MULTIPLIER_MAX", apply)
        self.assertNotIn("entry.risk_multiplier > 1.0", apply)
        # Reduce-only owners keep their contract.
        for name in ("_ParseSubtypePolicyLine", "_ParseContextPolicyLine"):
            _params, body = function_body(self.engine, name)
            self.assertIn("entry.risk_multiplier <= 1.0", body)

    # 21
    def test_out_of_contract_multipliers_fail_closed(self) -> None:
        cases = {
            "session_above_owner_max": dict(subtype_context_session=1.2, session=1.2),
            "reduce_only_base_above_one": dict(subtype_context_session=1.10, session=1.0),
            "base_hidden_under_session": dict(subtype_context_session=1.30, session=1.15),
            "ai_above_one": dict(ai=1.2),
            "active_above_one": dict(active=1.01),
            "bucket_above_one": dict(bucket=1.5),
            "execution_cost_above_one": dict(exec_mult=1.1),
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                ok, risk, _m, reason = self._run(100.0, **kwargs)
                self.assertFalse(ok)
                self.assertEqual(risk, 0.0)
                self.assertEqual(reason, "resolved_risk_multiplier_invalid")
        for label, kwargs in {"ai_zero": dict(ai=0.0), "bucket_zero": dict(bucket=0.0),
                              "exec_zero": dict(exec_reduced=True, exec_mult=0.0)}.items():
            with self.subTest(label):
                ok, risk, _m, reason = self._run(100.0, **kwargs)
                self.assertFalse(ok)
                self.assertEqual(reason, "resolved_risk_multiplier_zero")

    # 22
    def test_upscale_never_exceeds_hard_max_risk_pct(self) -> None:
        base = EQUITY * 1.0 / 100.0  # InpRiskPerTradePct = 1.0
        ok, risk, _m, _ = self._run(base, hard_pct=1.0, subtype_context_session=1.15, session=1.15)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 100.0)  # capped at 1.0 % of equity, not 1.15 %
        ok, risk, _m, _ = self._run(base, hard_pct=1.3, subtype_context_session=1.15, session=1.15)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 115.0)
        self.assertLessEqual(risk, EQUITY * 1.3 / 100.0)

    # 23
    def test_money_override_keeps_priority_and_is_not_upscaled_past_it(self) -> None:
        _params, body = function_body(self.risk, "CalcDesiredRiskMoney")
        money = body.index("if(InpRiskPerTradeMoney > 0) return InpRiskPerTradeMoney;")
        self.assertLess(money, body.index("InpRiskPerTradePct > 0"))
        # A money override above the pct cap keeps its existing size but cannot be upscaled.
        ok, risk, _m, _ = self._run(500.0, hard_pct=2.5, subtype_context_session=1.15, session=1.15)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 500.0)
        ok, risk, _m, _ = self._run(500.0, hard_pct=2.5, subtype_context_session=0.5)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 250.0)

    # 24
    def test_portfolio_capacity_still_reduces_or_blocks(self) -> None:
        ok, risk, _m, _ = self._run(100.0, cap=True, remaining=60.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 60.0)
        ok, risk, _m, _ = self._run(100.0, cap=True, remaining=60.0, subtype_context_session=1.15, session=1.15)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 60.0)  # upscale cannot breach remaining capacity
        ok, risk, _m, _ = self._run(100.0, cap=True, remaining=60.0, subtype_context_session=0.5)
        self.assertTrue(ok)
        self.assertAlmostEqual(risk, 30.0)  # identical to the previous min(base, rem) * mult
        ok, risk, _m, reason = self._run(100.0, cap=True, remaining=0.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "portfolio risk capacity exhausted")

    # 25
    def test_placement_resolves_once_then_sizes_through_broker_normalization(self) -> None:
        start = self.engine.index("double risk_money = CalcDesiredRiskMoney();")
        resolve = self.engine.index("ResolveGovernedRiskMoney(", start)
        market = self.engine.index("CalcVolumeForRisk(p.symbol, p.is_buy, entry_px, live.sl, target_risk)", resolve)
        pending = self.engine.index("CalcVolumeForRisk(p.symbol, p.is_buy, pending_entry, pending.sl, target_risk)", resolve)
        self.assertLess(resolve, market)
        self.assertLess(resolve, pending)
        window = self.engine[start:market]
        self.assertNotIn("target_risk *= risk_mult", window)
        self.assertEqual(len(re.findall(r"ResolveGovernedRiskMoney\(", self.engine)), 1)
        _params, volume = function_body(self.risk, "CalcVolumeForRisk")
        self.assertIn("NormalizeOpeningVolume(symbol", volume)

    def test_ai_suggested_multiplier_contract_stays_zero_to_one(self) -> None:
        bridge = read_repo_mql("AIGateBridge.mqh")
        self.assertIn('"suggested_risk_multiplier", out.suggested_risk_multiplier, 0.0, 1.0', bridge)
        ok, _risk, _m, reason = self._run(100.0, ai=1.05)
        self.assertFalse(ok)
        self.assertEqual(reason, "resolved_risk_multiplier_invalid")


if __name__ == "__main__":
    unittest.main()
