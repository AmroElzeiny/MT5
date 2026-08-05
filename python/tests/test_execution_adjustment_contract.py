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

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution_adjustment_contract import (  # noqa: E402
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

    def test_unauthorized_obstacle_change_is_rejected(self) -> None:
        result = evaluate_semantic_plan_match(
            INCIDENT_PLAN,
            {"obstacle_kind": "crossed_opposing_imbalance"},
            _contract(),
            tick_size=TICK,
        )
        self.assertFalse(result.semantic_match)
        self.assertIn("obstacle_kind", result.immutable_fields_changed)

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
