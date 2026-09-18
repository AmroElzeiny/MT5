"""Contract tests for the per-magic instance ownership lease.

The behaviour being pinned: two EA instances attached with different presets
must each own only the trades they opened.  Ownership in this engine is the
magic number -- every position/order filter, the runtime-state directory and
the terminal global variables are keyed by InpMagicNumber -- so two live
instances sharing one magic are a single owner with two managers.  That is how
a swing trade received 342 duplicated penalty proposals from a scalp instance.

The lease makes that configuration impossible to run silently:
  * a second live instance with the same account + magic is refused at startup,
    before it reads or reattaches any runtime state;
  * an instance that loses the lease stops before touching anything;
  * an instance that never owned the lease never persists state over the owner's.

All tests were falsified against the pre-change include tree.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_governance_contracts import MQL_STAGE, _function_body


def _read_include(name: str) -> str:
    return (MQL_STAGE / name).read_text(encoding="utf-8", errors="replace")


def _read_expert() -> str:
    candidates = (
        MQL_STAGE.parent.parent / "Experts" / "MT5_PO3_Codex" / "PO3_AIGate_ScannerEA.mq5",
        MQL_STAGE.parent / "MT5_PO3_Codex Experts" / "PO3_AIGate_ScannerEA.mq5",
    )
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(f"EA source not found next to {MQL_STAGE}")


class InstanceOwnershipLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _read_include("TradeEngine.mqh")
        self.claim = _function_body(self.engine, "_ClaimInstanceOwnership")
        self.maintain = _function_body(self.engine, "MaintainInstanceOwnership")
        self.release = _function_body(self.engine, "_ReleaseInstanceOwnership")

    def test_lease_is_scoped_to_account_and_magic_number(self):
        self.assertIn("ACCOUNT_LOGIN", self.claim)
        self.assertIn("InpMagicNumber", self.claim)

    def test_claim_is_an_atomic_compare_and_swap_on_a_temporary_variable(self):
        """Temporary so a terminal restart clears it; CAS so two starts cannot both win."""

        self.assertIn("GlobalVariableTemp(", self.claim)
        self.assertIn("GlobalVariableSetOnCondition(m_owner_lease_key", self.claim)

    def test_a_live_holder_refuses_the_second_instance(self):
        self.assertIn("magic_number_owned_by_another_live_instance", self.claim)
        self.assertIn("_OwnerLeaseStaleSeconds()", self.claim)
        refusal = self.claim.index("magic_number_owned_by_another_live_instance")
        self.assertIn("return false;", self.claim[refusal:refusal + 900])

    def test_a_lease_left_by_this_same_chart_is_reclaimed_immediately(self):
        """Live 2026-09-15 19:46: an OnInit aborted with code -1 kept the lease,
        and the swing chart was refused as its own "second instance" on every retry.
        """

        self.assertIn("ChartID()", self.claim)
        self.assertNotIn("GetMicrosecondCount()", self.claim, "token must be stable per chart")
        self.assertIn("same_chart_predecessor", self.claim)
        refusal = self.claim.index("magic_number_owned_by_another_live_instance")
        guard = self.claim.rindex("!same_chart_predecessor", 0, refusal)
        self.assertLess(guard, refusal)

    def test_heartbeat_uses_the_wall_clock_not_server_time(self):
        """Server time freezes when the market is closed; a weekend must not look stale."""

        for body in (self.claim, self.maintain):
            self.assertIn("TimeLocal()", body)
            self.assertNotIn("TimeCurrent()", body)

    def test_release_only_clears_this_instances_own_token(self):
        self.assertIn(
            "GlobalVariableSetOnCondition(m_owner_lease_key, 0.0, m_owner_lease_token)",
            self.release,
        )
        self.assertNotIn("GlobalVariableDel(m_owner_lease_key", self.release)

    def test_lost_lease_is_reported_and_not_retaken(self):
        self.assertIn("action=lost", self.maintain)
        self.assertIn("m_owner_lease_held = false;", self.maintain)


class InitDeinitOwnershipOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _read_include("TradeEngine.mqh")
        self.init = _function_body(self.engine, "Init")
        self.deinit = _function_body(self.engine, "Deinit")

    def test_init_claims_before_any_runtime_state_is_read_or_reattached(self):
        claim = self.init.index("_ClaimInstanceOwnership(")
        self.assertLess(self.init.index("SetRuntimeScope("), claim)
        for reader in (
            "m_state.LoadPlans(m_state.WatchlistPath()",
            "m_state.LoadPlans(m_state.PendingAiPath()",
            "LoadPenaltyStates(",
            "_RecoverManagedExposureOnInit()",
        ):
            self.assertLess(claim, self.init.index(reader), f"{reader} runs before the lease")
        self.assertRegex(
            self.init,
            r"if\(!_ClaimInstanceOwnership\(\w+\)\)\{[^}]*return false;",
        )

    def test_restored_state_is_marked_only_after_exposure_recovery(self):
        self.assertLess(
            self.init.index("_RecoverManagedExposureOnInit()"),
            self.init.index("m_live_state_restored = true;"),
        )

    def test_deinit_without_ownership_never_writes_state(self):
        """A refused or displaced instance holds empty state; saving it would erase the owner's."""

        guard = self.deinit.index("!m_owner_lease_held")
        self.assertIn("return;", self.deinit[guard:guard + 900])
        for writer in (
            "_FinalizeClosedTrades()",
            "m_state.SavePlans(m_state.WatchlistPath()",
            "_PersistPendingAiState()",
            "_PersistPenaltyStates()",
        ):
            self.assertLess(guard, self.deinit.index(writer), f"{writer} runs before the guard")

    def test_deinit_releases_the_lease_after_persisting(self):
        self.assertLess(
            self.deinit.index("_PersistPenaltyStates()"),
            self.deinit.rindex("_ReleaseInstanceOwnership()"),
        )

    def test_trade_transactions_are_ignored_without_ownership(self):
        body = _function_body(self.engine, "HandleTradeTransaction")
        self.assertIn("!m_owner_lease_held", body)
        self.assertLess(body.index("!m_owner_lease_held"), body.index("HistoryDealGetInteger("))


class ExpertTimerOwnershipTests(unittest.TestCase):
    def test_timer_stops_before_any_action_when_the_lease_is_lost(self):
        timer = _function_body(_read_expert(), "OnTimer")
        check = timer.index("g_engine.MaintainInstanceOwnership()")
        self.assertIn("ExpertRemove();", timer[check:check + 200])
        for action in (
            "EnforceGlobalStops(",
            "g_engine.ProcessPendingAI()",
            "g_engine.MaintainPositions()",
        ):
            self.assertLess(check, timer.index(action), f"{action} runs before the lease check")


if __name__ == "__main__":
    unittest.main()
