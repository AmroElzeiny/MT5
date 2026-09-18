"""The behavioural circuit-breaker may not re-arm on a streak it already acted on.

Measured 2026-09-16, Strategy Tester agent "Core 01", log 20260916.log:

    2026.09.14 09:19:41  global stop triggered ... behavioral=true
                         behavioral_reason=consecutive_losses=4 balance=99211.95
    2026.09.14 10:49:41  global stop triggered ... consecutive_losses=4 balance=99348.65
    2026.09.14 12:19:41  global stop triggered ... consecutive_losses=4 balance=99348.65
    ... 26 arming events in total, each exactly 90 minutes (InpBehaviorStopCooldownMin)
    after the previous one, every one naming the SAME four losses, with balance
    frozen at 99348.65 throughout -- not one trade closed in between.

The breaker reads the newest closed trades from disk and counts the leading loss
streak.  While it is active no new entry is allowed, so no new trade can close to
break that streak; when the cooldown expires the same historical losses arm it
again.  A 90-minute cooldown therefore became a permanent halt across 39 hours of
simulated time, and its status line -- printed once per timer tick, with neither a
reason nor a remaining time -- was 139,192 of that log's 231,961 lines.

Pinned here:

1. Arming stores the identity of the streak (the newest close time in it) plus the
   trigger code and count.
2. The trigger refuses a streak whose newest close is not newer than that anchor,
   so a NEW losing streak still fires the breaker and an old one cannot re-fire.
3. The active-stop line is emitted on state change only and names its own reason,
   count, remaining minutes and configured cooldown.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_governance_contracts import MQL_STAGE, _function_body  # noqa: E402

RISK = (MQL_STAGE / "Risk.mqh").read_text(encoding="utf-8", errors="ignore")


def _code_only(source: str) -> str:
    """Strip line comments so the incident write-up above the code is never matched."""

    return re.sub(r"//[^\n]*", "", source)


class BehaviorStopDoesNotRearmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trigger = _code_only(_function_body(RISK, "_BehaviorStopTriggered"))
        self.enforce = _code_only(_function_body(RISK, "EnforceGlobalStops"))

    def test_trigger_consults_the_streak_already_acted_on(self) -> None:
        self.assertIn("_BehaviorStopAnchor()", self.trigger)
        self.assertRegex(
            self.trigger,
            r"times\[0\]\s*<=\s*acted_anchor\s*\)\s*return\s+false\s*;",
            "a streak no newer than the one already acted on must not re-arm the stop",
        )

    def test_both_trigger_paths_are_guarded(self) -> None:
        """Losses and slippage both halt trading, so both must carry the guard."""

        self.assertEqual(
            2,
            len(re.findall(r"times\[0\]\s*<=\s*acted_anchor", self.trigger)),
            "each trigger path needs its own already-acted-on check",
        )

    def test_a_new_streak_can_still_fire_the_breaker(self) -> None:
        """The guard must compare against the anchor, never disable the trigger."""

        self.assertRegex(self.trigger, r"consec_losses\s*>=\s*loss_limit")
        self.assertRegex(self.trigger, r"bad_slips\s*>=\s*slip_limit")
        self.assertIn("streak_anchor = times[0]", self.trigger)

    def test_arming_stores_the_streak_identity(self) -> None:
        self.assertIn("_StoreBehaviorStopStreak(", self.enforce)
        store = _code_only(_function_body(RISK, "_StoreBehaviorStopStreak"))
        for key in ("_BehaviorStopAnchorKey()", "_BehaviorStopCodeKey()", "_BehaviorStopCountKey()"):
            with self.subTest(key=key):
                self.assertIn(key, store)

    def test_the_anchor_survives_a_restart(self) -> None:
        """Global variables, like the stop deadline itself -- not process memory."""

        for fn in ("_BehaviorStopAnchor", "_BehaviorStopCode", "_BehaviorStopCount"):
            with self.subTest(fn=fn):
                body = _code_only(_function_body(RISK, fn))
                self.assertIn("GlobalVariableGet", body)
                self.assertIn("GlobalVariableCheck", body)

    def test_active_stop_line_is_deduped_and_carries_its_evidence(self) -> None:
        self.assertIn("[behavior_stop] state=active", self.enforce)
        self.assertIn("journal_mode=once_per_state_change", self.enforce)
        self.assertRegex(
            self.enforce,
            r"if\(stop_log\s*!=\s*g_po3_behavior_stop_last_log\)",
            "the per-tick status line must be emitted on state change only",
        )
        for field in ("reason=", "count=", "remaining_min=", "cooldown_min=", "streak_anchor="):
            with self.subTest(field=field):
                self.assertIn(field, self.enforce)

    def test_the_stop_still_flattens_and_blocks_while_active(self) -> None:
        """Diagnostics changed; fail-closed behaviour did not."""

        active_block = self.enforce[: self.enforce.index("_LoadDailyAnchor")]
        self.assertIn("_FlattenManagedExposure(trade)", active_block)
        self.assertRegex(active_block, r"behavior_stop_until\s*>\s*now")
        self.assertRegex(active_block, r"return\s+true\s*;")


if __name__ == "__main__":
    unittest.main()
