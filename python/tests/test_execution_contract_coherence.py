"""Regression coverage for four same-instant self-contradictions in the execution path.

The 2026.09.06 CACHE_ONLY replay (2026.08.03-08.08, 25 symbols, cohort 671051197)
reached the decision layer cleanly -- 1263 cache hits, 9 approvals, 9 armed
watchlist entries -- and then lost 4 of those 9 approvals to disagreements
*inside the engine*, with zero elapsed simulated time and no market movement:

    #Germany40  21:05:00  semantic_plan_match=true   (fingerprint check 1)
                21:05:00  immutable_fields_changed=obstacle_kind  (check 2, 85 ms later)
                assessed crossed_session_high -> live crossed_opposing_imbalance

    GBPCHF      22:50:00  semantic_plan_match=true
                22:50:00  immutable_fields_changed=obstacle_kind  (206 ms later)
                assessed crossed_htf_opposing_imbalance -> live crossed_opposing_imbalance
                -- the same obstacle, spelled at two levels of detail

    EURUSD      07:35:00  execution_adjustment_validation=true
                            max_target_distance=0.00848458
                07:35:00  ai_chosen_target_exceeds_max_distance  (3 ms later)
                entry 1.15321 -> 1.15386 (0.08R of a 0.40R permission) and the
                contract's own deterministic_rr_from_entry rule grew the reward
                0.00822 -> 0.00889 past an entry-independent cap

    #Japan225 / GBPJPY   both trades that DID open quarantined on
                position_open_time_mismatch the instant OrderSend returned, and
                both verified 163 ms / 255 ms later as
                POSITION_FILLED_IDENTITY_VERIFIED

Three shared shapes:

  * a DERIVED label (obstacle_kind) held immutable while every primitive it is
    derived from (obstacle price, entry, obstacle_tf) is authorized to move;
  * a contract that MANDATES a recomputation and then rejects its result;
  * an identity resolved before the terminal has finished registering it, with
    "not yet knowable" recorded as "known to be wrong".

Each test below fails on the pre-fix MQL sources.
"""

from __future__ import annotations

import re
import unittest

from test_governance_contracts import MQL_STAGE, _function_body

ENGINE = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")
STATE = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8", errors="ignore")
TYPES = (MQL_STAGE / "Types.mqh").read_text(encoding="utf-8", errors="ignore")


def _code_only(source: str) -> str:
    """Drop // comments so an assertion cannot be satisfied by prose about the code."""

    return re.sub(r"//[^\n]*", "", source)


class ObstacleIdentityContractTests(unittest.TestCase):
    """The obstacle label must stop being a fingerprint tripwire."""

    def test_locked_plan_keeps_its_approved_obstacle(self) -> None:
        body = _code_only(_function_body(ENGINE, "_PublishObstacleEvidence"))
        self.assertIn(
            "p.assessed_plan_locked",
            body,
            "the live landscape scan must not overwrite the obstacle the AI was shown",
        )
        for field in ("live_obstacle_kind", "live_obstacle_price", "live_obstacle_severity"):
            self.assertIn(field, body, f"the live observation must be recorded in {field}")

        # The guard has to come before the write it is guarding, and has to return.
        guard = body.find("p.assessed_plan_locked")
        write = body.find("p.obstacle_kind =")
        self.assertGreater(write, guard, "the freeze guard must precede the obstacle write")
        self.assertIn("return;", body[guard:write], "the guard must return, not fall through")

    def test_severity_is_published_alongside_the_kind(self) -> None:
        body = _code_only(_function_body(ENGINE, "_PublishObstacleEvidence"))
        self.assertIn(
            "p.obstacle_severity = severity;",
            body,
            "severity must be a number, not only text inside obstacle_strength_features",
        )

    def test_timeframe_qualifier_is_not_part_of_the_immutable_identity(self) -> None:
        stripper = _code_only(_function_body(ENGINE, "_ObstacleKindWithoutTf"))
        self.assertIn('"htf_"', stripper)
        self.assertIn("StringSubstr", stripper)

        preserved = _code_only(_function_body(ENGINE, "_ObstacleIdentityPreserved"))
        self.assertEqual(
            2,
            preserved.count("_ObstacleKindWithoutTf("),
            "both sides of the identity comparison must be stripped, or the check is asymmetric",
        )
        self.assertIn("_BaseObstacleKind(", preserved)

    def test_a_different_obstacle_is_accepted_only_when_it_is_no_worse(self) -> None:
        preserved = _code_only(_function_body(ENGINE, "_ObstacleIdentityPreserved"))
        self.assertIn("label_changed", preserved)
        self.assertRegex(
            preserved,
            r"live_severity\s*<=\s*assessed_severity",
            "a changed label must be gated on severity, not accepted outright",
        )
        # ... and the severity gate must be reached only after the label check.
        self.assertLess(
            preserved.find("if(!label_changed) return true;"),
            preserved.find("live_severity <= assessed_severity"),
            "an unchanged label must short-circuit before the severity comparison",
        )

    def test_missing_severity_fails_closed_rather_than_permissive(self) -> None:
        body = _code_only(_function_body(ENGINE, "_EffectiveObstacleSeverity"))
        self.assertIn("if(stored > 0.0) return stored;", body)
        self.assertIn(
            "_ObstacleSeverity(kind, 0.0)",
            body,
            "a plan restored without a stored severity must be scored from its kind,"
            " not treated as severity zero",
        )

    def test_both_comparison_sites_judge_the_live_observation(self) -> None:
        """Comparing the frozen value against itself would make the check vacuous."""

        for name in ("_EvaluateSemanticPlanMatch", "_ExecutionFingerprintWithinTolerance"):
            body = _code_only(_function_body(ENGINE, name))
            if "_ObstacleIdentityPreserved(" not in body:
                continue
            self.assertIn(
                "_LiveObstacleKindForComparison(p)",
                body,
                f"{name} must judge what the live scan saw, not the frozen copy",
            )
            self.assertIn("_LiveObstacleSeverityForComparison(p)", body)

    def test_obstacle_price_is_not_compared_across_two_different_obstacles(self) -> None:
        body = _code_only(ENGINE)
        matches = re.findall(
            r"if\(!\w*label_changed\s*&&\s*\n?\s*MathAbs\(_LiveObstaclePriceForComparison",
            body,
        )
        self.assertTrue(
            matches,
            "an accepted, no-worse replacement obstacle has a different price; comparing"
            " it would reintroduce the rejection the severity gate just removed",
        )

    def test_lock_freezes_severity_and_clears_stale_live_observation(self) -> None:
        body = _code_only(_function_body(ENGINE, "_LockAssessedPlan"))
        self.assertIn("p.assessed_obstacle_severity", body)
        self.assertIn("_EffectiveObstacleSeverity(", body)
        for field in ("p.live_obstacle_kind", "p.live_obstacle_price", "p.live_obstacle_severity"):
            self.assertIn(
                f'{field}                   = ""' if field.endswith("kind") else f"{field}",
                body,
                f"{field} from a previous lifecycle would be compared against a new assessment",
            )

    def test_new_obstacle_fields_survive_a_state_round_trip(self) -> None:
        code = _code_only(STATE)
        for field in (
            "obstacle_severity",
            "assessed_obstacle_severity",
            "live_obstacle_kind",
            "live_obstacle_price",
            "live_obstacle_severity",
        ):
            self.assertIn(
                f'"{field}"',
                code,
                f"{field} must be persisted or a restored plan reverts to permissive",
            )
            self.assertRegex(
                code,
                rf"p\.{field}\s*=\s*JsonGet\w+\(json,\s*\"{field}\"",
                f"{field} must be restored, not only written",
            )

    def test_types_declare_every_new_obstacle_field(self) -> None:
        code = _code_only(TYPES)
        for decl in (
            "double obstacle_severity;",
            "double assessed_obstacle_severity;",
            "string live_obstacle_kind;",
            "double live_obstacle_price;",
            "double live_obstacle_severity;",
        ):
            self.assertIn(decl, code)

    def test_the_gate_reports_both_sides_of_the_comparison(self) -> None:
        body = _code_only(_function_body(ENGINE, "_EvaluateSemanticPlanMatch"))
        self.assertIn("[obstacle_identity_gate]", body)
        for field in ("assessed_severity=", "live_severity=", "assessed_kind=", "live_kind="):
            self.assertIn(
                field,
                body,
                "the 2026-09-06 rejections named the field and nothing else, so neither"
                " side of the comparison was ever visible in a journal",
            )


class DeterministicRrTargetCapTests(unittest.TestCase):
    """A contract may not mandate a recomputation and then reject its result."""

    def test_deterministic_recomputation_is_capped_not_rejected(self) -> None:
        body = _code_only(_function_body(ENGINE, "_ApplyAssessedTargetUnderContract"))
        self.assertIn("TARGET_RECALC_DETERMINISTIC_RR", body)
        self.assertIn(
            "_MaxPlanTargetDistance(p, p.entry_est, live_risk)",
            body,
            "the cap must come from the one canonical definition, not a second copy",
        )
        self.assertIn("RewardWithinMaxDistance(", body)
        self.assertIn("reward = max_dist;", body)

    def test_capping_still_fails_closed_below_the_contract_minimum(self) -> None:
        body = _code_only(_function_body(ENGINE, "_ApplyAssessedTargetUnderContract"))
        self.assertRegex(
            body,
            r"capped_rr\s*\+\s*0\.0001\s*<\s*c\.min_resulting_rr",
            "a cap that drops the trade under its own contract minimum must still reject",
        )
        self.assertIn("EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE", body)
        reject = body.find("ai_chosen_target_exceeds_max_distance")
        cap = body.find("reward = max_dist;")
        self.assertLess(reject, cap, "the reject branch must be evaluated before capping")

    def test_the_cap_decision_is_journalled_with_its_numbers(self) -> None:
        body = _code_only(_function_body(ENGINE, "_ApplyAssessedTargetUnderContract"))
        self.assertIn("[deterministic_rr_target_cap]", body)
        for field in ("approved_rr=", "live_risk=", "max_target_distance=", "capped_rr="):
            self.assertIn(field, body)

    def test_structural_targets_are_untouched_by_the_cap(self) -> None:
        """preserve_fixed_price owns its price; capping it would move a market level."""

        body = _code_only(_function_body(ENGINE, "_ApplyAssessedTargetUnderContract"))
        deterministic = body.find("TARGET_RECALC_DETERMINISTIC_RR")
        structural = body.find("p.tp2 = p.assessed_selected_target_price;")
        cap = body.find("reward = max_dist;")
        self.assertLess(deterministic, cap)
        self.assertLess(
            cap,
            structural,
            "the cap must live inside the deterministic-RR branch only",
        )


class ExecutionIdentitySettlementTests(unittest.TestCase):
    """"Not yet knowable" must not be recorded as "known to be wrong"."""

    def test_pending_classifier_lists_only_transient_conditions(self) -> None:
        body = _code_only(
            _function_body(ENGINE, "_ExecutionIdentityFailureIsSettlementPending")
        )
        for transient in (
            "result_deal_not_in_history",
            "result_order_not_in_history",
            "missing_deal_position_id",
            "exact_position_not_found_by_deal_position_id",
            "position_open_time_unavailable",
            "position_open_time_mismatch",
        ):
            self.assertIn(transient, body, f"{transient} is transient by construction")

        for contradiction in (
            "deal_magic_mismatch",
            "deal_symbol_mismatch",
            "deal_direction_mismatch",
            "deal_volume_mismatch",
            "order_comment_mismatch",
            "position_magic_mismatch",
            "position_symbol_mismatch",
            "position_direction_mismatch",
            "position_comment_mismatch",
            "position_volume_mismatch",
        ):
            self.assertNotIn(
                contradiction,
                body,
                f"{contradiction} is a real contradiction and must quarantine at once",
            )

    def test_post_send_defers_instead_of_quarantining(self) -> None:
        code = _code_only(ENGINE)
        self.assertIn("BROKER_ACCEPTED_IDENTITY_PENDING", code)
        self.assertIn("[execution_identity_deferred]", code)
        deferred = re.search(
            r"if\(!identity_ok && _ExecutionIdentityFailureIsSettlementPending\(identity_reason\)\)\{"
            r"(?P<body>.*?)\}\s*else if\(!identity_ok\)\{",
            code,
            re.S,
        )
        self.assertIsNotNone(
            deferred,
            "the settlement-pending branch must be tested before the quarantine branch",
        )
        branch = deferred.group("body")
        self.assertNotIn(
            "_QuarantineExecutionIdentity(",
            branch,
            "a deferred identity must not write a quarantine artifact",
        )
        self.assertIn("live.execution_identity_quarantined = false;", branch)
        self.assertIn("live.execution_identity_verified = false;", branch)
        self.assertIn("live.final_execution_success = false;", branch)
        self.assertIn('live.attribution_status = "PENDING_SETTLEMENT";', branch)
        for ineligible in (
            "live.learning_eligible = false;",
            "live.optimization_eligible = false;",
            "live.suppression_eligible = false;",
        ):
            self.assertIn(
                ineligible,
                branch,
                "deferring is not accepting: the trade stays out of learning",
            )

    def test_fill_time_resolution_stays_unconditionally_strict(self) -> None:
        """OnTradeTransaction is the authority; it may never defer.

        This one asserts an ABSENCE, so it passes on the pre-fix tree too: it guards
        the deferral from being widened later, rather than detecting the fix.
        """

        code = _code_only(ENGINE)
        fill = re.search(
            r"if\(!_ResolveExactExecutionIdentity\(meta,\s*order_ticket,\s*trans\.deal,"
            r".*?\)\)\{(?P<body>.*?)\n      \}",
            code,
            re.S,
        )
        self.assertIsNotNone(fill, "entry-deal resolution site not found")
        branch = fill.group("body")
        self.assertNotIn(
            "_ExecutionIdentityFailureIsSettlementPending",
            branch,
            "the authoritative fill-time check must quarantine on any failure",
        )
        self.assertIn("_QuarantineExecutionIdentity(", branch)

    def test_the_two_timestamp_conditions_stay_separately_named(self) -> None:
        """The reason split predates this change set in source but not in the binary.

        `PO3_AIGate_ScannerEA.ex5` compiled 2026-09-06 18:47:26 was older than
        TradeEngine.mqh 19:11:07, so the run that produced the two quarantines was
        still emitting the merged reason.  This test guards the split; it does not
        discriminate against the reconstructed pre-fix tree.
        """

        body = _code_only(_function_body(ENGINE, "_ResolveExactExecutionIdentity"))
        self.assertIn("position_open_time_unavailable:", body)
        self.assertIn("position_open_time_mismatch:", body)
        self.assertIn("delta_sec=", body)
        self.assertIn("tolerance_sec=", body)


class AssessedLegDiagnosticTests(unittest.TestCase):
    """A plan that can never execute must say so when it is frozen, not 12 bars later."""

    def test_lock_reports_a_first_leg_already_under_its_own_floor(self) -> None:
        body = _code_only(_function_body(ENGINE, "_LockAssessedPlan"))
        self.assertIn("[assessed_leg_below_floor]", body)
        self.assertIn("assessed_stop_distance * 0.65", body)
        self.assertIn("detected_at=lock", body)
        self.assertIn(
            "diagnostic_only",
            body,
            "which of the two values is wrong is not established; acting on the guess"
            " would either force a trade or discard a real approval",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
