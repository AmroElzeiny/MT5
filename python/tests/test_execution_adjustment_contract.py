"""Regressions for the assessed-plan versus execution-plan authority model.

The failure these tests exist for
---------------------------------
Eleventh zero-trade run, GOLD, 2026.06.29 02:14:30.  The AI approved and the
watchlist stored::

    entry=4066.75  sl=4063.17  tp1=4069.38  tp2=4070.51
    target_source=ai_selected_liquidity_target
    target_model=range_mid_opposite_side
    obstacle_kind=NONE

Six seconds later the execution rebuild ran at the live ask (4066.88) and
produced::

    [target_candidates] liquidity_tp=4220.90 ... obstacle_kind=crossed_opposing_imbalance
    [target_validation] ai_chosen_target_exceeds_cap kept_for_validation reward=154.02 cap=18.55
    [target_sanitizer] from=range_mid_opposite_side to=synthetic_rr_fallback
    [execution_fingerprint] match=false
        changed_components=target_source,target_model,obstacle_kind,tp1,tp2

The approved target at the live entry would have been reward 3.63 against a cap
of 18.55 -- comfortably feasible.  The trade was lost purely because the engine
rebuilt a different trade and then rejected it for being different.

Every test below states one rule of the repaired authority model.
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _resolve_mql_include_root() -> Path:
    """Staged or active terminal includes, without binding tests to one layout."""

    override = os.environ.get("PO3_MQL_INCLUDE_ROOT", "").strip()
    if override:
        return Path(override)
    return REPO_ROOT.parent / "MT5_PO3_Codex Include"


MQL_INCLUDE_ROOT = _resolve_mql_include_root()

from execution_adjustment_contract import (  # noqa: E402
    OBSTACLE_CROSSING_PREFIX,
    base_obstacle_kind,
    obstacle_is_crossed,
    ADJUSTABLE_FIELDS,
    EXEC_ACTION_RETRY_BOUNDED,
    EXEC_ACTION_TERMINAL_INVALIDATE,
    EXEC_FAIL_NONE,
    EXEC_FAIL_SEMANTIC_PLAN_CHANGED,
    EXEC_FAIL_STRUCTURAL_INVALIDATION,
    EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE,
    EXEC_FAIL_TRANSIENT_QUOTE,
    EXECUTION_ADJUSTMENT_CONTRACT_VERSION,
    EXECUTION_FAILURE_CLASSES,
    IMMUTABLE_SEMANTIC_FIELDS,
    TARGET_RECALC_DETERMINISTIC_RR,
    TARGET_RECALC_PRESERVE_FIXED_PRICE,
    AssessedTradePlan,
    contract_for,
    derive_live_target,
    evaluate_semantic_plan_match,
    evaluate_target_feasibility,
    failure_action,
    failure_is_terminal,
    failure_is_transient,
    mql_defines,
    price_distance_to_ticks,
    target_model_is_synthetic_rr,
    ticks_within_cap,
)

TICK = 0.01

# The exact plan from the incident.
INCIDENT_PLAN = AssessedTradePlan(
    request_id="591813800_1782691200_138018421_1782698459_GOLD_13874",
    request_identity_hash="d5454a3d2dc40479" * 2,
    candidate_id="GOLD|1782511200|1782513900|1782517500|range_reentry|4065.92|range_reentry|0",
    candidate_hash="19BF4CB2F8A7D3A6",
    symbol="GOLD",
    is_buy=True,
    setup_taxonomy_enum="micro_range_reentry",
    entry_model="range_reentry",
    stop_model="structural_sweep",
    selected_target_identity="range_mid_opposite_side",
    selected_target_source="ai_selected_liquidity_target",
    selected_target_model="range_mid_opposite_side",
    selected_target_price=4070.51,
    obstacle_kind="NONE",
    obstacle_tf="",
    obstacle_price=0.0,
    entry=4066.75,
    sl=4063.17,
    tp1=4069.38,
    tp2=4070.51,
    assessment_fingerprint="F9B25FB774F53783",
)


def _contract(plan: AssessedTradePlan = INCIDENT_PLAN, **overrides):
    kwargs = dict(
        max_entry_drift_r=0.35,
        max_entry_drift_ticks=2.0,
        min_resulting_rr=0.85,
        max_target_distance=18.55,
    )
    kwargs.update(overrides)
    return contract_for(plan, **kwargs)


class MqlParityTests(unittest.TestCase):
    """The two sides must not restate each other's constants."""

    def test_contract_version_matches_mql(self) -> None:
        defines = mql_defines()
        self.assertEqual(
            defines["EXECUTION_ADJUSTMENT_CONTRACT_VERSION"],
            EXECUTION_ADJUSTMENT_CONTRACT_VERSION,
        )

    def test_every_failure_class_matches_mql(self) -> None:
        defines = mql_defines()
        mql_classes = {v for k, v in defines.items() if k.startswith("EXEC_FAIL_")}
        self.assertEqual(mql_classes, set(EXECUTION_FAILURE_CLASSES))

    def test_target_recalculation_rules_match_mql(self) -> None:
        defines = mql_defines()
        self.assertEqual(defines["TARGET_RECALC_PRESERVE_FIXED_PRICE"], TARGET_RECALC_PRESERVE_FIXED_PRICE)
        self.assertEqual(defines["TARGET_RECALC_DETERMINISTIC_RR"], TARGET_RECALC_DETERMINISTIC_RR)

    def test_actions_match_mql(self) -> None:
        defines = mql_defines()
        self.assertEqual(defines["EXEC_ACTION_TERMINAL_INVALIDATE"], EXEC_ACTION_TERMINAL_INVALIDATE)
        self.assertEqual(defines["EXEC_ACTION_RETRY_BOUNDED"], EXEC_ACTION_RETRY_BOUNDED)


class AssessedPlanImmutabilityTests(unittest.TestCase):
    """Rule 1: the approved semantic plan may not change."""

    def test_unchanged_plan_matches(self) -> None:
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN,
            {
                "target_source": INCIDENT_PLAN.selected_target_source,
                "target_model": INCIDENT_PLAN.selected_target_model,
                "obstacle_kind": INCIDENT_PLAN.obstacle_kind,
                "entry": INCIDENT_PLAN.entry,
                "sl": INCIDENT_PLAN.sl,
                "tp1": INCIDENT_PLAN.tp1,
                "tp2": INCIDENT_PLAN.tp2,
            },
            _contract(),
            tick_size=TICK,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.failure_class, EXEC_FAIL_NONE)
        self.assertEqual(result.immutable_fields_changed, [])

    def test_the_exact_incident_rebuild_is_a_semantic_change(self) -> None:
        """The 2026.06.29 02:14:30 rebuild, replayed field for field."""
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN,
            {
                "target_source": "ai_selected_synthetic_rr_fallback",
                "target_model": "synthetic_rr_fallback",
                "obstacle_kind": "crossed_opposing_imbalance",
                "entry": 4066.88,
                "sl": 4063.17,
                "tp1": 4070.03,
                "tp2": 4070.78,
            },
            _contract(),
            tick_size=TICK,
        )
        self.assertFalse(result.semantic_match)
        self.assertEqual(result.failure_class, EXEC_FAIL_SEMANTIC_PLAN_CHANGED)
        self.assertEqual(result.action, EXEC_ACTION_TERMINAL_INVALIDATE)
        for field_name in ("target_source", "target_model", "obstacle_kind", "selected_target_price"):
            self.assertIn(field_name, result.immutable_fields_changed)

    def test_unauthorized_target_source_change_is_rejected(self) -> None:
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN,
            {"target_source": "ai_selected_synthetic_rr_fallback"},
            _contract(),
            tick_size=TICK,
        )
        self.assertFalse(result.semantic_match)
        self.assertIn("target_source", result.immutable_fields_changed)

    def test_unauthorized_target_model_change_is_rejected(self) -> None:
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN,
            {"target_model": "synthetic_rr_fallback"},
            _contract(),
            tick_size=TICK,
        )
        self.assertFalse(result.semantic_match)
        self.assertIn("target_model", result.immutable_fields_changed)

    def test_a_different_obstacle_is_still_a_semantic_change(self) -> None:
        """The obstacle's identity stays immutable.

        The incident plan carried obstacle_kind="NONE"; a rebuild that names a real
        opposing imbalance is a different obstacle and must still fail closed.
        """

        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN,
            {"obstacle_kind": "crossed_opposing_imbalance"},
            _contract(),
            tick_size=TICK,
        )
        self.assertFalse(result.semantic_match)
        self.assertIn("obstacle_kind", result.immutable_fields_changed)

    def test_obstacle_base_identity_is_compared_not_the_crossing_prefix(self) -> None:
        """Regression: 2026-09-05 lost USDCAD, USDCAD and GBPCHF to this.

        "crossed_" records where live price sits relative to the obstacle, and MQL
        re-derives it on every rebuild.  It flips exactly when price travels into
        the entry zone, which is the movement the watchlist is armed to wait for --
        so treating it as identity rejected the plan for doing what it was armed to
        do.  obstacle_price, the primitive it is derived from, has always been
        compared with a tolerance and classified as authorized.
        """

        plan = replace(INCIDENT_PLAN, obstacle_kind="opposing_imbalance", obstacle_tf="entry_tf")
        result = evaluate_semantic_plan_match(
            plan,
            {"obstacle_kind": "crossed_opposing_imbalance", "obstacle_tf": "entry_tf"},
            _contract(),
            tick_size=TICK,
        )
        self.assertTrue(result.semantic_match, result.immutable_fields_changed)
        self.assertNotIn("obstacle_kind", result.immutable_fields_changed)
        self.assertIn("obstacle_crossing_state", result.authorized_fields_changed)
        self.assertEqual(result.failure_class, EXEC_FAIL_NONE)

    def test_uncrossing_an_obstacle_is_also_only_a_crossing_change(self) -> None:
        plan = replace(INCIDENT_PLAN, obstacle_kind="crossed_session_high", obstacle_tf="entry_tf")
        result = evaluate_semantic_plan_match(
            plan, {"obstacle_kind": "session_high"}, _contract(), tick_size=TICK
        )
        self.assertTrue(result.semantic_match)
        self.assertIn("obstacle_crossing_state", result.authorized_fields_changed)

    def test_a_different_base_kind_under_the_same_prefix_is_immutable(self) -> None:
        """USDCAD cycled three obstacle kinds inside one hour; only the base matters."""

        plan = replace(INCIDENT_PLAN, obstacle_kind="crossed_opposing_imbalance")
        result = evaluate_semantic_plan_match(
            plan, {"obstacle_kind": "crossed_session_high"}, _contract(), tick_size=TICK
        )
        self.assertFalse(result.semantic_match)
        self.assertIn("obstacle_kind", result.immutable_fields_changed)
        self.assertEqual(result.failure_class, EXEC_FAIL_SEMANTIC_PLAN_CHANGED)

    def test_obstacle_tf_alone_is_an_authorized_derivation_difference(self) -> None:
        """GBPCHF died on obstacle_kind,obstacle_tf after a two-point entry move."""

        plan = replace(INCIDENT_PLAN, obstacle_kind="htf_opposing_imbalance", obstacle_tf="")
        result = evaluate_semantic_plan_match(
            plan,
            {"obstacle_kind": "crossed_htf_opposing_imbalance", "obstacle_tf": "htf"},
            _contract(),
            tick_size=TICK,
        )
        self.assertTrue(result.semantic_match)
        self.assertIn("obstacle_tf", result.authorized_fields_changed)
        self.assertIn("obstacle_crossing_state", result.authorized_fields_changed)

    def test_taxonomy_and_direction_are_immutable(self) -> None:
        for name, value in (
            ("setup_taxonomy_enum", "micro_failed_breakout_reclaim"),
            ("direction", "SELL"),
            ("candidate_hash", "DIFFERENT"),
            ("symbol", "EURUSD"),
        ):
            with self.subTest(field=name):
                result = evaluate_semantic_plan_match(
                    INCIDENT_PLAN, {name: value}, _contract(), tick_size=TICK
                )
                self.assertFalse(result.semantic_match)
                self.assertIn(name, result.immutable_fields_changed)

    def test_the_partition_covers_the_incident_fields(self) -> None:
        for name in ("target_source", "target_model", "obstacle_kind", "selected_target_price"):
            self.assertIn(name, IMMUTABLE_SEMANTIC_FIELDS)
        for name in ("entry", "sl", "tp1", "tp2"):
            self.assertIn(name, ADJUSTABLE_FIELDS)

    def test_the_live_obstacle_views_are_adjustable_not_immutable(self) -> None:
        for name in ("obstacle_crossing_state", "obstacle_tf", "obstacle_price"):
            self.assertIn(name, ADJUSTABLE_FIELDS)
            self.assertNotIn(name, IMMUTABLE_SEMANTIC_FIELDS)

    def test_base_obstacle_kind_matches_the_mql_helper(self) -> None:
        source = (MQL_INCLUDE_ROOT / "TradeEngine.mqh").read_text(encoding="utf-8")
        self.assertIn("string _BaseObstacleKind(const string obstacle_kind) const {", source)
        # Both sides must strip exactly the same prefix, of exactly the same length.
        self.assertIn('StringFind(obstacle_kind, "crossed_") == 0', source)
        self.assertIn("StringSubstr(obstacle_kind, 8)", source)
        self.assertEqual(len(OBSTACLE_CROSSING_PREFIX), 8)
        for value, expected in (
            ("crossed_opposing_imbalance", "opposing_imbalance"),
            ("opposing_imbalance", "opposing_imbalance"),
            ("", ""),
        ):
            with self.subTest(value=value):
                self.assertEqual(base_obstacle_kind(value), expected)


class AuthorizedAdjustmentTests(unittest.TestCase):
    """Rule 2: the entry may move, inside the contract's bounds."""

    def test_live_entry_drift_within_contract_is_authorized(self) -> None:
        # The incident's actual drift: 4066.75 -> 4066.88, 0.13 on 3.58 risk.
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN, {"entry": 4066.88}, _contract(), tick_size=TICK
        )
        self.assertTrue(result.ok)
        self.assertIn("entry", result.authorized_fields_changed)
        self.assertEqual(result.unauthorized_fields_changed, [])

    def test_entry_drift_outside_contract_is_unauthorized(self) -> None:
        contract = _contract(max_entry_drift_r=0.01, max_entry_drift_ticks=1.0)
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN, {"entry": 4069.50}, contract, tick_size=TICK
        )
        self.assertTrue(result.semantic_match)  # still the same trade...
        self.assertFalse(result.adjustment_valid)  # ...but moved too far
        self.assertIn("entry_drift_exceeds_contract", result.unauthorized_fields_changed)
        self.assertEqual(result.failure_class, EXEC_FAIL_STRUCTURAL_INVALIDATION)

    def test_tp_change_without_permission_is_unauthorized(self) -> None:
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN, {"tp1": 4075.00}, _contract(), tick_size=TICK
        )
        self.assertFalse(result.adjustment_valid)
        self.assertIn("tp1_changed_without_permission", result.unauthorized_fields_changed)

    def test_sl_drift_beyond_bound_is_unauthorized(self) -> None:
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN, {"sl": 4061.00}, _contract(), tick_size=TICK
        )
        self.assertFalse(result.adjustment_valid)
        self.assertIn("sl_drift_exceeds_contract", result.unauthorized_fields_changed)


class FixedTargetPreservationTests(unittest.TestCase):
    """Rule 3/4: a structural target keeps its identity AND its price."""

    def test_liquidity_target_is_preserved_not_recalculated(self) -> None:
        contract = _contract()
        self.assertEqual(contract.target_recalculation, TARGET_RECALC_PRESERVE_FIXED_PRICE)
        self.assertFalse(contract.tp2_adjustment_allowed)

        tp1, tp2 = derive_live_target(
            INCIDENT_PLAN, contract, live_entry=4066.88, live_sl=4063.17
        )
        self.assertAlmostEqual(tp2, 4070.51, places=6)
        self.assertAlmostEqual(tp1, 4069.38, places=6)

    def test_preserved_target_is_feasible_at_the_live_entry(self) -> None:
        """The whole point: the approved trade was executable all along."""
        contract = _contract()
        _, tp2 = derive_live_target(INCIDENT_PLAN, contract, live_entry=4066.88, live_sl=4063.17)
        result = evaluate_target_feasibility(
            is_buy=True,
            entry=4066.88,
            sl=4063.17,
            target_price=tp2,
            tick_size=TICK,
            max_allowed_distance=18.55,
            min_required_rr=0.85,
            current_price=4066.65,
            model="range_mid_opposite_side",
        )
        self.assertTrue(result.feasible, result.reason)
        self.assertAlmostEqual(result.reward, 3.63, places=2)
        self.assertGreater(result.rr, 0.85)

    def test_re_derived_liquidity_price_would_have_blown_the_cap(self) -> None:
        """Documents the defect: the re-derived 4220.90 really was infeasible."""
        result = evaluate_target_feasibility(
            is_buy=True,
            entry=4066.88,
            sl=4063.17,
            target_price=4220.90,
            tick_size=TICK,
            max_allowed_distance=18.55,
            min_required_rr=0.85,
            model="range_mid_opposite_side",
        )
        self.assertFalse(result.feasible)
        self.assertEqual(result.reason, "ai_chosen_target_exceeds_max_distance")


class SyntheticRrRecalculationTests(unittest.TestCase):
    """Rule 2: a synthetic fixed-RR target IS recomputed, deterministically."""

    def _synthetic_plan(self) -> AssessedTradePlan:
        return AssessedTradePlan(
            **{
                **INCIDENT_PLAN.__dict__,
                "selected_target_model": "synthetic_rr_fallback",
                "selected_target_source": "ai_selected_synthetic_rr_fallback",
                "selected_target_identity": "synthetic_rr_fallback",
            }
        )

    def test_synthetic_model_is_detected(self) -> None:
        self.assertTrue(target_model_is_synthetic_rr("synthetic_rr_fallback"))
        self.assertTrue(target_model_is_synthetic_rr("synthetic_rr_capped_to_max_distance"))
        self.assertFalse(target_model_is_synthetic_rr("range_mid_opposite_side"))
        self.assertFalse(target_model_is_synthetic_rr("next_liquidity_session_range"))

    def test_synthetic_target_keeps_its_rr_when_the_entry_moves(self) -> None:
        plan = self._synthetic_plan()
        contract = _contract(plan)
        self.assertEqual(contract.target_recalculation, TARGET_RECALC_DETERMINISTIC_RR)

        _, tp2 = derive_live_target(plan, contract, live_entry=4066.88, live_sl=4063.17)
        live_rr = (tp2 - 4066.88) / abs(4066.88 - 4063.17)
        self.assertAlmostEqual(live_rr, plan.net_rr, places=9)

    def test_synthetic_recalculation_is_deterministic(self) -> None:
        plan = self._synthetic_plan()
        contract = _contract(plan)
        first = derive_live_target(plan, contract, live_entry=4066.88, live_sl=4063.17)
        second = derive_live_target(plan, contract, live_entry=4066.88, live_sl=4063.17)
        self.assertEqual(first, second)

    def test_synthetic_model_still_may_not_change_source_or_model(self) -> None:
        plan = self._synthetic_plan()
        result = evaluate_semantic_plan_match(
            plan, {"target_source": "ai_selected_liquidity_target"}, _contract(plan), tick_size=TICK
        )
        self.assertFalse(result.semantic_match)


class CanonicalTargetDistanceBoundaryTests(unittest.TestCase):
    """One implementation, one answer, decided in whole ticks."""

    def _at(self, reward: float, cap: float = 30.01):
        entry, sl = 4000.0, 3990.0
        return evaluate_target_feasibility(
            is_buy=True,
            entry=entry,
            sl=sl,
            target_price=entry + reward,
            tick_size=TICK,
            max_allowed_distance=cap,
            min_required_rr=0.0,
            model="synthetic_rr_capped_to_max_distance",
        )

    def test_reward_below_cap_passes(self) -> None:
        self.assertTrue(self._at(20.00).feasible)

    def test_reward_exactly_at_cap_passes(self) -> None:
        """The direct contradiction from the log: feasible=true reward==cap."""
        result = self._at(30.01)
        self.assertTrue(result.feasible, result.reason)
        self.assertEqual(result.reward_ticks, result.max_allowed_ticks)

    def test_reward_one_tick_below_cap_passes(self) -> None:
        self.assertTrue(self._at(30.00).feasible)

    def test_reward_one_tick_above_cap_fails(self) -> None:
        result = self._at(30.02)
        self.assertFalse(result.feasible)
        self.assertEqual(result.reason, "ai_chosen_target_exceeds_max_distance")

    def test_positive_direction_buy_target(self) -> None:
        result = evaluate_target_feasibility(
            is_buy=True, entry=4000.0, sl=3990.0, target_price=4020.0,
            tick_size=TICK, max_allowed_distance=30.0,
        )
        self.assertTrue(result.feasible)
        self.assertTrue(result.direction_valid)

    def test_negative_direction_sell_target(self) -> None:
        result = evaluate_target_feasibility(
            is_buy=False, entry=4000.0, sl=4010.0, target_price=3980.0,
            tick_size=TICK, max_allowed_distance=30.0,
        )
        self.assertTrue(result.feasible)
        self.assertTrue(result.direction_valid)

    def test_wrong_side_target_fails_direction(self) -> None:
        result = evaluate_target_feasibility(
            is_buy=True, entry=4000.0, sl=3990.0, target_price=3995.0,
            tick_size=TICK, max_allowed_distance=30.0,
        )
        self.assertFalse(result.feasible)
        self.assertEqual(result.reason, "ai_chosen_target_invalid_direction")

    def test_already_reached_target_fails(self) -> None:
        result = evaluate_target_feasibility(
            is_buy=True, entry=4000.0, sl=3990.0, target_price=4020.0,
            tick_size=TICK, max_allowed_distance=30.0, current_price=4020.0,
        )
        self.assertFalse(result.feasible)
        self.assertEqual(result.reason, "ai_chosen_target_already_reached")

    def test_entry_change_within_allowed_adjustment_keeps_it_feasible(self) -> None:
        contract = _contract()
        _, tp2 = derive_live_target(INCIDENT_PLAN, contract, live_entry=4066.88, live_sl=4063.17)
        self.assertTrue(
            evaluate_target_feasibility(
                is_buy=True, entry=4066.88, sl=4063.17, target_price=tp2,
                tick_size=TICK, max_allowed_distance=18.55, min_required_rr=0.85,
            ).feasible
        )

    def test_entry_change_outside_allowed_adjustment_is_caught_by_the_contract(self) -> None:
        contract = _contract(max_entry_drift_r=0.01, max_entry_drift_ticks=1.0)
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN, {"entry": 4070.00}, contract, tick_size=TICK
        )
        self.assertFalse(result.adjustment_valid)

    def test_the_same_plan_is_never_both_feasible_and_over_distance(self) -> None:
        """No stage may disagree with another about the same plan."""
        for reward in (29.99, 30.00, 30.01, 30.02, 30.10):
            with self.subTest(reward=reward):
                a = self._at(reward)
                b = self._at(reward)
                self.assertEqual(a.feasible, b.feasible)
                self.assertEqual(
                    a.feasible, a.reason == "ok",
                    "feasible and reason must never contradict each other",
                )

    def test_tick_rounding_is_symmetric(self) -> None:
        self.assertEqual(price_distance_to_ticks(0.10, 0.01), 10)
        self.assertEqual(price_distance_to_ticks(30.01, 0.01), 3001)
        self.assertEqual(price_distance_to_ticks(0.0, 0.01), 0)
        self.assertEqual(price_distance_to_ticks(1.0, 0.0), 0)
        self.assertTrue(ticks_within_cap(3001, 3001))
        self.assertFalse(ticks_within_cap(3002, 3001))
        self.assertTrue(ticks_within_cap(999999, 0))  # no cap configured


class Tp1FloorUnderAuthorizedDriftTests(unittest.TestCase):
    """The TP1 floor must not re-litigate a drift the contract already authorised.

    The floor has two halves.  ``0.65 * stop_distance`` asks whether the first leg is
    a meaningful fraction of the risk -- a property of the approved plan.
    ``spread * InpMinTP1SpreadMult`` asks whether the broker can place the leg -- a
    live question.  Measuring BOTH against the rebuilt plan made the floor rise with
    the widened stop while the frozen leg's reward fell with the drifted entry, so a
    plan had to carry ``tp1_reward >= 0.65R + 1.65d`` (TP1 at 1.31R for the permitted
    d = 0.4R) to survive its own execution.  Three of the eleven approvals in the
    2026-09-05 cache replay died here.

    The numbers below are the measured ones from that run's journal.
    """

    GEOMETRY_COEFFICIENT = 0.65
    SPREAD_MULT = 4.0

    @staticmethod
    def _old_rule(live_stop: float, live_leg: float, spread: float) -> bool:
        floor = max(live_stop * 0.65, spread * 4.0)
        return live_leg >= floor

    @staticmethod
    def _new_rule(
        assessed_stop: float, assessed_leg: float, live_leg: float, spread: float
    ) -> bool:
        geometry_ok = assessed_leg >= assessed_stop * 0.65
        spread_ok = live_leg >= spread * 4.0
        return geometry_ok and spread_ok

    def test_audusd_leg_survives_the_rebuilt_stop(self) -> None:
        # [tp1_authority] tp1=0.69936 tp1_reward=0.00171 min_tp1_reward=0.00190
        # SL rebuilt 0.70340 -> 0.70400; live_rr2 1.05 still cleared min_rr 0.90.
        assessed_stop, live_stop = 0.00233, 0.00293
        leg, spread = 0.00171, 0.00002
        self.assertAlmostEqual(live_stop * 0.65, 0.00190, places=5)
        self.assertFalse(self._old_rule(live_stop, leg, spread))
        self.assertTrue(self._new_rule(assessed_stop, leg, leg, spread))

    def test_gbpjpy_leg_survives_the_drifted_entry(self) -> None:
        # Plan floor 0.115 -> execution floor 0.138; leg 0.106 after the entry moved
        # 212.693 -> 212.657.  RR2 would have been 3.35.
        assessed_stop, live_stop = 0.177, 0.213
        assessed_leg, live_leg = 0.142, 0.106
        spread = 0.008
        self.assertAlmostEqual(live_stop * 0.65, 0.138, places=3)
        self.assertAlmostEqual(assessed_stop * 0.65, 0.115, places=3)
        # Anchoring the STOP alone is not enough -- the drifted entry still fails it.
        self.assertLess(live_leg, assessed_stop * 0.65)
        self.assertFalse(self._old_rule(live_stop, live_leg, spread))
        self.assertTrue(self._new_rule(assessed_stop, assessed_leg, live_leg, spread))

    def test_a_leg_that_never_cleared_the_floor_at_approval_still_fails(self) -> None:
        """The approval-time check is unchanged, so a bad plan is still refused."""

        assessed_stop, leg, spread = 0.00233, 0.00090, 0.00002
        self.assertLess(leg, assessed_stop * self.GEOMETRY_COEFFICIENT)
        self.assertFalse(self._new_rule(assessed_stop, leg, leg, spread))

    def test_the_spread_half_is_still_measured_live(self) -> None:
        """A leg the broker cannot place is refused however good its geometry."""

        assessed_stop, leg = 0.00233, 0.00171
        blown_spread = 0.00060  # 4 x 0.00060 = 0.00240 > 0.00171
        self.assertGreaterEqual(leg, assessed_stop * self.GEOMETRY_COEFFICIENT)
        self.assertFalse(self._new_rule(assessed_stop, leg, leg, blown_spread))

    def test_the_arithmetic_contradiction_is_stated_exactly(self) -> None:
        """Under the old rule the requirement was tp1 >= 0.65R + 1.65d."""

        risk = 1.0
        for drift in (0.0, 0.1, 0.2, 0.4):
            with self.subTest(drift=drift):
                # Adverse entry drift d widens the stop by d and shrinks the leg by d.
                required_at_plan_time = 0.65 * (risk + drift) + drift
                self.assertAlmostEqual(required_at_plan_time, 0.65 * risk + 1.65 * drift, places=9)
        # At the contract's permitted drift the demand is TP1 at 1.31R.
        self.assertAlmostEqual(0.65 + 1.65 * 0.4, 1.31, places=9)


class SpreadGuardCalibrationTests(unittest.TestCase):
    """A raw point count cannot guard a universe whose point size spans 1e-5..1e-2."""

    MAX_SPREAD_TICKS = 100
    MAX_SPREAD_PRICE_FRAC = 0.0025
    MAX_SPREAD_RISK_FRAC = 0.22

    def _abs_cap(self, point: float, price: float) -> float:
        return max(self.MAX_SPREAD_TICKS * point, price * self.MAX_SPREAD_PRICE_FRAC)

    def test_japan225_was_rejected_by_a_cap_it_could_never_reach(self) -> None:
        # entry=62679.50 sl=63253.25 -> stop 573.75; spread 800 points at point=0.01.
        point, price, stop = 0.01, 62679.50, 573.75
        spread = 800 * point
        self.assertAlmostEqual(spread, 8.00, places=6)
        # The old raw cap: 100 points = 1.00 index point, an eighth of the real spread.
        self.assertAlmostEqual(self.MAX_SPREAD_TICKS * point, 1.00, places=6)
        self.assertGreater(spread, self.MAX_SPREAD_TICKS * point)
        # It was never economically wide: 1.4% of risk against a 22% allowance.
        self.assertLess(spread / stop, self.MAX_SPREAD_RISK_FRAC)
        self.assertAlmostEqual(spread / stop, 0.0139, places=4)
        # Repaired: the price-fraction floor makes the ceiling reachable.
        self.assertLessEqual(spread, self._abs_cap(point, price))

    def test_the_risk_guard_stays_the_binding_one_on_fx(self) -> None:
        """Raising the absolute ceiling on FX changes nothing that matters."""

        point, price, stop = 0.00001, 1.10, 0.000649   # GBPCHF-like
        abs_cap = self._abs_cap(point, price)
        risk_cap = stop * self.MAX_SPREAD_RISK_FRAC
        self.assertLess(risk_cap, abs_cap, "the risk guard must bind first on FX")
        self.assertAlmostEqual(risk_cap / point, 14.3, places=1)

    def test_a_genuinely_blown_quote_is_still_rejected(self) -> None:
        """On a normal FX plan the risk guard is what catches it -- and it does.

        The absolute ceiling is deliberately not the binding one here: 100 pips on a
        23-pip stop is 43% of the risk against a 22% allowance, so it is refused on
        the metric that actually describes the harm.
        """

        point, price, stop = 0.00001, 1.15, 0.00233    # AUDUSD-like
        blown = 0.0010                                  # 100 pips
        self.assertGreater(blown, stop * self.MAX_SPREAD_RISK_FRAC)
        self.assertLess(blown, self._abs_cap(point, price))

    def test_the_absolute_ceiling_catches_what_the_risk_guard_cannot(self) -> None:
        """A very wide stop makes the risk guard loose; the price cap still binds.

        This is why the absolute ceiling is kept rather than deleted: on a 2%-of-price
        stop, 22% of risk is 440 pips, so an absurd 400-pip quote clears the risk
        guard and is refused only by the price-fraction ceiling.
        """

        point, price, stop = 0.00001, 1.15, 0.0200
        absurd = 0.0040                                 # 400 pips
        self.assertLess(absurd, stop * self.MAX_SPREAD_RISK_FRAC)
        self.assertGreater(absurd, self._abs_cap(point, price))

    def test_the_default_clears_every_measured_healthy_spread(self) -> None:
        """Measured medians from the 2026-09-05 replay, spread as a % of price."""

        measured = {
            "#USSPX500": 0.05921,
            "SILVER": 0.01672,
            "AUDUSD": 0.01368,
            "GBPCAD": 0.00932,
            "CADCHF": 0.00897,
            "GBPCHF": 0.00707,
            "GBPJPY": 0.00610,
            "EURCHF": 0.00564,
            "CADJPY": 0.00530,
            "EURCAD": 0.00511,
            "GBPUSD": 0.00178,
            "USDJPY": 0.00177,
            "GOLD": 0.00170,
            "#Japan225": 0.01276,
        }
        cap_pct = self.MAX_SPREAD_PRICE_FRAC * 100.0
        worst = max(measured.values())
        self.assertLess(worst, cap_pct)
        self.assertGreater(cap_pct / worst, 4.0, "the default must keep real headroom")


class ExecutionFailureClassificationTests(unittest.TestCase):
    """Retry only what retrying can fix."""

    def test_semantic_change_is_terminal_never_retryable(self) -> None:
        self.assertTrue(failure_is_terminal(EXEC_FAIL_SEMANTIC_PLAN_CHANGED))
        self.assertFalse(failure_is_transient(EXEC_FAIL_SEMANTIC_PLAN_CHANGED))
        self.assertEqual(failure_action(EXEC_FAIL_SEMANTIC_PLAN_CHANGED), EXEC_ACTION_TERMINAL_INVALIDATE)

    def test_infeasible_target_is_terminal(self) -> None:
        self.assertTrue(failure_is_terminal(EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE))
        self.assertEqual(
            failure_action(EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE), EXEC_ACTION_TERMINAL_INVALIDATE
        )

    def test_quote_failures_retry_with_backoff(self) -> None:
        self.assertTrue(failure_is_transient(EXEC_FAIL_TRANSIENT_QUOTE))
        self.assertEqual(failure_action(EXEC_FAIL_TRANSIENT_QUOTE), EXEC_ACTION_RETRY_BOUNDED)

    def test_every_class_has_exactly_one_disposition(self) -> None:
        for cls in EXECUTION_FAILURE_CLASSES:
            if cls == EXEC_FAIL_NONE:
                continue
            with self.subTest(cls=cls):
                self.assertNotEqual(
                    failure_is_terminal(cls),
                    failure_is_transient(cls),
                    "a class must be terminal or transient, never both or neither",
                )


class DuplicateExecutionSuppressionTests(unittest.TestCase):
    """One semantic mismatch must not become 90 duplicate attempts."""

    class _Watchlist:
        """The repaired retry policy, in the smallest form that can be tested."""

        def __init__(self) -> None:
            self.failure_class = EXEC_FAIL_NONE
            self.state_fingerprint = ""
            self.attempts = 0
            self.suppressed = 0
            self.terminated = False

        def tick(self, state: str, outcome_class: str) -> None:
            if self.terminated:
                self.suppressed += 1
                return
            if self.failure_class != EXEC_FAIL_NONE and not failure_is_transient(self.failure_class):
                if state == self.state_fingerprint:
                    self.suppressed += 1
                    return
            self.attempts += 1
            self.failure_class = outcome_class
            self.state_fingerprint = state
            if failure_is_terminal(outcome_class):
                self.terminated = True

    def test_ninety_ticks_produce_one_attempt_after_a_semantic_mismatch(self) -> None:
        wl = self._Watchlist()
        for _ in range(91):
            wl.tick("unchanged-state", EXEC_FAIL_SEMANTIC_PLAN_CHANGED)
        self.assertEqual(wl.attempts, 1)
        self.assertEqual(wl.suppressed, 90)
        self.assertTrue(wl.terminated)

    def test_a_changed_state_is_allowed_to_retry(self) -> None:
        wl = self._Watchlist()
        wl.tick("state-a", EXEC_FAIL_TRANSIENT_QUOTE)
        wl.tick("state-b", EXEC_FAIL_TRANSIENT_QUOTE)
        self.assertEqual(wl.attempts, 2)

    def test_order_construction_is_counted_separately_from_prechecks(self) -> None:
        """1,149 'attempting execution' with 0 order constructions is the bug."""
        precheck_attempts = 0
        order_constructions = 0
        for reached_broker_boundary in (False, False, False, True):
            precheck_attempts += 1
            if reached_broker_boundary:
                order_constructions += 1
        self.assertEqual(precheck_attempts, 4)
        self.assertEqual(order_constructions, 1)
        self.assertNotEqual(precheck_attempts, order_constructions)


if __name__ == "__main__":
    unittest.main()
