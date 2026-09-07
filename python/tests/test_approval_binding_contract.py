"""Contract tests for the two bindings that lost approvals in the 2026-09-07 replay.

Both defects share the project's recurring shape: a value that is not identity is
compared as if it were, and the failure is then reported under a name that does not
say what actually moved.

  1. ``request_execution_fingerprint`` is a COST SNAPSHOT.  Every input of
     ``_ExecutionFingerprint`` except ``spread_r``, ``slippage_r``,
     ``execution_cost_r`` and ``net_reward_after_cost_r`` is already an input of
     ``_CandidateHash``, and those four are live broker measurements the execution
     contract authorises to drift by ``max_cost_deterioration_r``.  Using them to
     decide *which* candidate the AI selected can only turn a cost wobble into
     "no candidate was selected".

  2. A locked plan's live blocker must be re-observed on the route the approval was
     granted for.  Scanning from the drifted live entry retires the nearest obstacle
     the moment price walks into the entry zone and reports the next blocker on the
     same route as a brand-new, more severe one -- which killed #Germany40 inside the
     same simulated second as its own approval, with ``bars_waited=0``.

Every assertion here is scoped to a single MQL function body so it cannot pass
because an unrelated part of a 15k-line file happens to contain the token.
"""

from __future__ import annotations

import re
import unittest

from tests.test_governance_contracts import MQL_STAGE, _function_body


def _trade_engine() -> str:
    return (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")


class SelectedCandidateBindingTests(unittest.TestCase):
    """The decision's selected candidate is resolved by identity, not by cost."""

    def setUp(self) -> None:
        self.source = _trade_engine()
        # The resolution loop lives in the decision-consumption block, not in a
        # function of its own, so scope it to the loop itself.
        start = self.source.index("bool have_selected = false;")
        end = self.source.index("string deterministic_reason =", start)
        self.block = self.source[start:end]

    def test_selection_does_not_gate_on_the_cost_snapshot_fingerprint(self) -> None:
        self.assertIn("decision_group[i].candidate_hash != dec.selected_candidate_hash", self.block)
        self.assertIn("decision_group[i].candidate_id != dec.selected_candidate_id", self.block)
        self.assertIn("decision_group[i].candidate_index != dec.chosen_index", self.block)
        self.assertNotIn(
            "decision_group[i].request_execution_fingerprint != dec.request_execution_fingerprint",
            self.block,
            "request_execution_fingerprint carries four drift-tolerated live cost "
            "measurements and must not decide which candidate was selected",
        )

    def test_identity_conditions_are_all_still_enforced(self) -> None:
        """Dropping the cost gate must not drop the three identity gates with it."""

        conditions = re.findall(r"if\(decision_group\[i\]\.(\w+) !=", self.block)
        self.assertEqual(
            ["candidate_index", "candidate_id", "candidate_hash"],
            conditions,
            "the selected candidate must still be resolved by index + id + hash",
        )

    def test_unresolved_selection_prints_the_group_it_failed_against(self) -> None:
        self.assertIn("[selected_candidate_unresolved]", self.block)
        for field in (
            "decision_chosen_index=",
            "decision_candidate_hash=",
            "decision_candidate_id=",
            "group_plan_count=",
            "group=",
        ):
            self.assertIn(field, self.block, f"unresolved-selection diagnostic must print {field}")

    def test_success_sentinel_no_longer_masks_the_reason(self) -> None:
        """``_DecisionAssessmentsMatchGroup`` writes "ok", never "", on success.

        The old guard tested ``StringLen(reason) == 0`` and therefore could never
        fire, so an unresolved selection reached the journal with the reason of a
        check that had actually passed.
        """

        self.assertIn(
            'StringLen(assessment_integrity_reason) == 0 || assessment_integrity_reason == "ok"',
            self.block,
        )
        self.assertIn("selected_candidate_not_resolved_in_group", self.block)


class AssessmentGroupBindingTests(unittest.TestCase):
    """Assessment-to-candidate binding names the field that actually moved."""

    def setUp(self) -> None:
        self.source = _trade_engine()
        self.body = _function_body(self.source, "_DecisionAssessmentsMatchGroup")

    def test_every_identity_field_is_still_compared(self) -> None:
        for field in (
            "candidate_id",
            "setup_taxonomy_version",
            "setup_taxonomy_enum",
            "taxonomy_mapping_source",
        ):
            self.assertIn(
                f"plans[matched_plan].{field}",
                self.body,
                f"{field} must remain a fatal identity comparison",
            )

    def test_each_failure_mode_has_its_own_reason(self) -> None:
        """One reason string for four different failures is undiagnosable."""

        for reason in (
            "assessment_candidate_hash_not_in_group",
            "assessment_candidate_hash_ambiguous_in_group",
            "assessment_request_fingerprint_missing",
        ):
            self.assertIn(reason, self.body)
        self.assertNotIn(
            'reason = "candidate_hash_mismatch";',
            self.body,
            "the unqualified reason hid which of four different failures occurred",
        )

    def test_missing_fingerprint_still_fails_closed(self) -> None:
        """Downgrading the equality must not downgrade the presence requirement."""

        self.assertIn('if(StringLen(request_fp) == 0){', self.body)
        guard = self.body.index("if(StringLen(request_fp) == 0){")
        divergence = self.body.index(
            "if(request_fp != plans[matched_plan].request_execution_fingerprint){"
        )
        self.assertLess(guard, divergence, "presence must be checked before divergence")
        self.assertIn(
            "reason = \"assessment_request_fingerprint_missing\";",
            self.body[guard:divergence],
        )

    def test_fingerprint_divergence_is_reported_not_silently_dropped(self) -> None:
        divergence = self.body.index(
            "if(request_fp != plans[matched_plan].request_execution_fingerprint){"
        )
        tail = self.body[divergence : divergence + 500]
        self.assertIn("_LogAssessmentIdentityMismatch", tail)
        self.assertNotIn("return false;", tail.split("}")[0])

    def test_mismatch_diagnostic_prints_both_values(self) -> None:
        helper = _function_body(self.source, "_LogAssessmentIdentityMismatch")
        for field in ("field=", "assessment_value=", "plan_value=", "group_plan_count=", "chosen_index="):
            self.assertIn(field, helper)
        self.assertIn("[assessment_identity_mismatch]", helper)

    def test_cost_deterioration_is_still_bounded_elsewhere(self) -> None:
        """The tolerance the equality was standing in for must really exist."""

        semantic = _function_body(self.source, "_EvaluateSemanticPlanMatch")
        for field in ("spread_r_deterioration", "slippage_r_deterioration", "execution_cost_r_deterioration"):
            self.assertIn(field, semantic)
        provenance = _function_body(self.source, "_RestoreTesterRequestProvenance")
        self.assertIn("contract.max_cost_deterioration_r", provenance)
        self.assertIn("recorded_cost_deterioration_exceeds_contract", provenance)


class ApprovedRouteObstacleTests(unittest.TestCase):
    """A locked plan's blocker is judged on the route that was approved."""

    def setUp(self) -> None:
        self.source = _trade_engine()
        self.body = _function_body(self.source, "_ObserveLiveObstacleOnApprovedRoute")

    def test_observation_is_anchored_to_the_approved_geometry(self) -> None:
        self.assertIn("if(!p.assessed_plan_locked) return;", self.body)
        self.assertIn("_CollectTargetLevels(p, p.assessed_entry", self.body)
        self.assertIn("p.assessed_entry, p.assessed_tp2", self.body)
        self.assertNotIn(
            "_CollectTargetLevels(p, p.entry_est",
            self.body,
            "re-observing from the drifted live entry is the defect, not the fix",
        )

    def test_observation_uses_the_approved_stop_for_severity(self) -> None:
        """Severity carries a distance bonus, so its denominator matters too."""

        self.assertIn("double approved_stop = p.assessed_stop_distance;", self.body)
        self.assertIn("_PublishObstacleEvidence(p, kind, price, approved_stop, true, p.assessed_entry)", self.body)

    def test_stale_observation_is_cleared_even_when_no_obstacle_remains(self) -> None:
        """Leaving a drifted-entry observation behind would defeat the whole fix."""

        clear = self.body.index('p.live_obstacle_kind = "";')
        found_branch = self.body.index("if(!found){")
        self.assertLess(clear, found_branch, "the observation must be cleared before the no-obstacle exit")
        self.assertIn("p.live_obstacle_severity = 0.0;", self.body)

    def test_it_runs_as_the_last_writer_before_the_comparison(self) -> None:
        """Three call sites publish obstacle evidence; only one may be judged."""

        check = _function_body(self.source, "_SemanticExecutionCheck")
        call = check.index("_ObserveLiveObstacleOnApprovedRoute(p);")
        evaluate = check.index("_EvaluateSemanticPlanMatch(p, contract, out);")
        self.assertLess(call, evaluate)

    def test_publisher_still_defaults_to_the_live_entry(self) -> None:
        """The anchor must be opt-in, so plan construction is untouched."""

        publisher = _function_body(self.source, "_PublishObstacleEvidence")
        self.assertIn("double entry_ref = (anchor_entry > 0.0 ? anchor_entry : p.entry_est);", publisher)
        signature = re.search(
            r"void _PublishObstacleEvidence\([^)]*\)", self.source, re.S
        )
        self.assertIsNotNone(signature)
        self.assertIn("const double anchor_entry = 0.0", signature.group(0))

    def test_severity_comparison_still_fails_closed_on_a_worse_blocker(self) -> None:
        preserved = _function_body(self.source, "_ObstacleIdentityPreserved")
        self.assertIn("return (live_severity <= assessed_severity + 0.0001);", preserved)


class MaskedRouteObstacleTests(unittest.TestCase):
    """The published blocker is the nearest one; the masking is now measurable."""

    def setUp(self) -> None:
        self.source = _trade_engine()

    def test_worst_on_route_ranks_by_severity_then_distance(self) -> None:
        body = _function_body(self.source, "_WorstObstacleBeforeTarget")
        self.assertIn("_ObstacleSeverity(obstacles[i].kind, distance_r)", body)
        self.assertIn("severity > best_severity + 0.0001", body)
        self.assertIn("dist < best_dist", body)

    def test_nearest_selection_is_unchanged(self) -> None:
        """The cap must keep stopping in front of the FIRST obstacle."""

        body = _function_body(self.source, "_NearestObstacleBeforeTarget")
        self.assertIn("if(dist < best_dist){", body)
        self.assertNotIn("_ObstacleSeverity", body)

    def test_masking_is_reported_without_changing_identity(self) -> None:
        report = _function_body(self.source, "_ReportMaskedRouteObstacle")
        self.assertIn("[obstacle_route_scan]", report)
        self.assertIn("worst_severity <= published_severity + 0.0001", report)
        for assignment in ("p.obstacle_kind =", "p.obstacle_price =", "p.obstacle_severity ="):
            self.assertNotIn(
                assignment,
                report,
                "publishing the worst blocker changes _CandidateHash and retires the "
                "recorded replay cohort; this pass reports only",
            )

    def test_report_is_scoped_to_unlocked_plans(self) -> None:
        seed = _function_body(self.source, "_SeedTargetArbitrationCandidates")
        self.assertIn("if(!p.assessed_plan_locked)", seed)
        self.assertIn("_ReportMaskedRouteObstacle(", seed)


class BehaviourWindowScopeTests(unittest.TestCase):
    """A run's circuit-breaker may only see that run's own closed trades.

    ``<bus>\\logs\\trade_results`` is one flat directory shared by every run against
    the same bus.  On 2026-09-07 a CACHE_ONLY replay read the previous replay's
    closed trades and tripped its behavioural stop at simulated 00:00:01 -- balance
    still 100000, not one deal of its own -- then finished the whole five-day period
    with ``scans_total=0`` and a report indistinguishable from a strategy that found
    nothing.
    """

    def setUp(self) -> None:
        self.risk = (MQL_STAGE / "Risk.mqh").read_text(encoding="utf-8")
        self.engine = _trade_engine()

    def test_loader_filters_to_the_current_runtime_scope(self) -> None:
        body = _function_body(self.risk, "_LoadRecentBehaviorTrades")
        self.assertIn('JsonGetString(txt, "runtime_scope", "") != g_po3_behavior_runtime_scope', body)
        self.assertIn("skipped_foreign_scope++", body)
        self.assertIn("continue;", body)

    def test_records_carry_the_scope_that_produced_them(self) -> None:
        self.assertIn('JsonKVStr("runtime_scope", m_state.RuntimeScope())', self.engine)

    def test_engine_publishes_its_scope_at_init(self) -> None:
        self.assertIn("PO3SetBehaviorRuntimeScope(m_state.RuntimeScope());", self.engine)
        publish = self.engine.index("PO3SetBehaviorRuntimeScope(m_state.RuntimeScope());")
        set_scope = self.engine.index("m_state.SetRuntimeScope(runtime_state_scope)")
        self.assertLess(set_scope, publish, "the scope must exist before it is published")

    def test_unscoped_loader_still_reads_everything(self) -> None:
        """An empty scope must not silently blind the breaker."""

        body = _function_body(self.risk, "_LoadRecentBehaviorTrades")
        self.assertIn("StringLen(g_po3_behavior_runtime_scope) > 0 &&", body)

    def test_exclusion_is_reported(self) -> None:
        body = _function_body(self.risk, "_LoadRecentBehaviorTrades")
        self.assertIn("[behavior_window]", body)
        self.assertIn("skipped_other_runs=", body)
        self.assertIn("behavior_window_log != g_po3_behavior_window_last_log", body)

    def test_scope_setter_exists_and_is_the_only_writer(self) -> None:
        setter = _function_body(self.risk, "PO3SetBehaviorRuntimeScope")
        self.assertIn("g_po3_behavior_runtime_scope = scope_key;", setter)
        assignments = re.findall(r"g_po3_behavior_runtime_scope\s*=", self.risk)
        self.assertEqual(2, len(assignments), "declaration plus exactly one setter")


class FirstLegOwnershipTests(unittest.TestCase):
    """The reconciled first leg is claimed, so no later rebuild can move it.

    Measured cost of the claim on the 2026.08.03-08 cohort: ai_cache_hits 1256 ->
    1102, misses 109 -> 254.  It retires the recorded artifacts of exactly the plans
    whose first leg was mis-built, which is correct -- those artifacts recorded
    decisions about plans this engine no longer builds.
    """

    def setUp(self) -> None:
        self.source = _trade_engine()
        self.body = _function_body(self.source, "_FinalizeFirstLeg")

    def test_reconciler_claims_the_leg_it_moves(self) -> None:
        assignment = self.body.index("p.tp1 = (p.is_buy ? p.entry_est+adjusted")
        claim = self.body.index("p.tp1_from_target_model = true;")
        self.assertGreater(claim, assignment, "the claim must accompany the assignment")

    def test_identity_cost_of_the_claim_is_recorded_at_the_site(self) -> None:
        """A change that retires replay artifacts must say so where it is made."""

        self.assertIn("candidate_hash", self.body)
        self.assertIn("RECORD_ONLY", self.body)

    def test_reconciler_never_touches_an_approved_plan(self) -> None:
        self.assertIn(
            "if(allow_generic_adjustment && !p.assessed_plan_locked && !p.tp1_from_target_model){",
            self.body,
        )

    def test_reconciled_leg_lands_inside_the_floor_it_will_be_validated_against(self) -> None:
        self.assertIn("double minimum = MathMax(geometry_floor, spread_floor);", self.body)
        self.assertIn("double adjusted = MathMin(maximum, MathMax(minimum, leg));", self.body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
