"""Contract tests for the engine-era floor and the historical-evidence ladder.

Every test here was falsified against the pre-change tree: the era tests fail
without the floor, the ladder tests fail without ``historical_evidence_ladder``,
and the prompt-rule tests fail against the previous rule list.

The properties being defended are the three that make widening a query safe:

* a widened match must never pool decision states into one rate;
* a widened match must never manufacture the sample nisab by addition;
* a widened match must never gain trading authority.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import decision_evidence
from shadow_outcome_ledger import (
    AI_DECIDED_STATES,
    EVIDENCE_ERA_FLOOR_TS,
    EVIDENCE_LADDER_RUNGS,
    EVIDENCE_MIN_CLEAN_SAMPLES,
    EVIDENCE_MIN_SWEEPS,
    EvidencePolicy,
    VariantRecord,
    _classify_sample,
    compact_candidate_evidence,
    historical_evidence,
    historical_evidence_ladder,
)

DECISION_AT = EVIDENCE_ERA_FLOOR_TS + 30 * 86_400


def variant(
    index: int,
    *,
    family: str = "micro_continuation_fvg",
    taxonomy: str = "MICRO_BREAKER_RETEST",
    state: str = "ABSTAIN",
    observed_at: int | None = None,
    won: bool = True,
) -> VariantRecord:
    """One clean, resolved variant on its own sweep.

    A distinct ``sweep_opportunity_id`` per record matters: the summariser is
    sweep-weighted, so variants sharing a sweep collapse to one sample.
    """

    observed = EVIDENCE_ERA_FLOOR_TS + 86_400 if observed_at is None else observed_at
    record = VariantRecord(
        candidate_variant_id=f"V{index}",
        sweep_opportunity_id=f"OPP{index}",
        family=family,
        setup_taxonomy=taxonomy,
        decision_state=state,
        observed_at=observed,
        entry_activated=True,
        terminal_event="TP2_BEFORE_SL" if won else "SL_BEFORE_TP1",
        terminal_event_at=observed + 3_600,
        tp1_before_sl=won,
        tp2_before_sl=won,
        sl_before_tp1=not won,
        result_r_unmanaged=2.0 if won else -1.0,
    )
    # Derived, never hardcoded: the fixture must bucket the way production
    # buckets, so a future terminal value cannot be mislabelled CLEAN here.
    record.sample_class = _classify_sample(record)
    return record


def enough(**kwargs) -> list[VariantRecord]:
    """A sample comfortably over both halves of the nisab."""

    return [variant(i, **kwargs) for i in range(EVIDENCE_MIN_CLEAN_SAMPLES + 5)]


class EngineEraFloorTests(unittest.TestCase):
    def test_outcomes_observed_before_the_era_floor_are_excluded_and_counted(self):
        stale = [
            variant(i, observed_at=EVIDENCE_ERA_FLOOR_TS - 86_400)
            for i in range(EVIDENCE_MIN_CLEAN_SAMPLES + 5)
        ]
        result = historical_evidence(
            stale, decision_timestamp=DECISION_AT, family="micro_continuation_fvg"
        )
        self.assertEqual(result["state"], "INSUFFICIENT_SAMPLE")
        self.assertEqual(result["stats"]["clean"], 0)
        self.assertEqual(result["excluded_before_era_floor"], len(stale))
        self.assertEqual(result["era_policy"], "observed_at_at_or_after_era_floor")

    def test_the_floor_is_read_from_observation_time_not_resolution_time(self):
        """A pre-era plan that resolved after the floor is still pre-era.

        What dates a sample to an engine build is when the plan was built, not
        when price finished walking it out.
        """

        row = variant(0, observed_at=EVIDENCE_ERA_FLOOR_TS - 3_600)
        row.terminal_event_at = EVIDENCE_ERA_FLOOR_TS + 86_400
        result = historical_evidence(
            [row], decision_timestamp=DECISION_AT, family="micro_continuation_fvg"
        )
        self.assertEqual(result["excluded_before_era_floor"], 1)
        self.assertEqual(result["stats"]["clean"], 0)

    def test_post_era_outcomes_survive_the_floor(self):
        result = historical_evidence(
            enough(), decision_timestamp=DECISION_AT, family="micro_continuation_fvg"
        )
        self.assertEqual(result["excluded_before_era_floor"], 0)
        self.assertEqual(result["state"], "SUFFICIENT_SAMPLE")

    def test_a_zero_floor_disables_the_filter_without_disabling_evidence(self):
        policy = EvidencePolicy(era_floor_ts=0)
        stale = [
            variant(i, observed_at=EVIDENCE_ERA_FLOOR_TS - 86_400)
            for i in range(EVIDENCE_MIN_CLEAN_SAMPLES + 5)
        ]
        result = historical_evidence(
            stale,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            policy=policy,
        )
        self.assertEqual(result["excluded_before_era_floor"], 0)
        self.assertEqual(result["era_policy"], "no_engine_era_floor")
        self.assertEqual(result["state"], "SUFFICIENT_SAMPLE")

    def test_the_env_override_can_lower_the_floor_but_never_below_zero(self):
        self.assertEqual(
            EvidencePolicy.from_env({"AI_SHADOW_EVIDENCE_ERA_FLOOR_TS": "-5"}).era_floor_ts, 0
        )
        self.assertEqual(
            EvidencePolicy.from_env({"AI_SHADOW_EVIDENCE_ERA_FLOOR_TS": "0"}).era_floor_ts, 0
        )
        self.assertEqual(
            EvidencePolicy.from_env({}).era_floor_ts, EVIDENCE_ERA_FLOOR_TS
        )


class EvidenceLadderTests(unittest.TestCase):
    def test_an_exact_match_answers_without_widening(self):
        result = historical_evidence_ladder(
            enough(),
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "SUFFICIENT_SAMPLE")
        self.assertEqual(result["match_specificity"], "EXACT_FAMILY_TAXONOMY_STATE")
        self.assertEqual(result["ladder_broadest_rung_tried"], "EXACT_FAMILY_TAXONOMY_STATE")

    def test_the_ladder_widens_the_decision_state_before_the_family(self):
        """History exists for REJECT only; the taxonomy must not be widened."""

        records = enough(state="REJECT")
        result = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "SUFFICIENT_SAMPLE")
        self.assertEqual(result["match_specificity"], "FAMILY_TAXONOMY_ANY_DECIDED_STATE")
        self.assertEqual(result["query"]["decision_state"], "REJECT")
        self.assertEqual(result["query"]["setup_taxonomy"], "MICRO_BREAKER_RETEST")

    def test_the_family_rung_is_only_reached_when_the_taxonomy_rung_cannot_answer(self):
        records = enough(taxonomy="MICRO_OTE_REVERSAL", state="REJECT")
        result = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "SUFFICIENT_SAMPLE")
        self.assertEqual(result["match_specificity"], "FAMILY_ANY_DECIDED_STATE")
        self.assertEqual(result["query"]["setup_taxonomy"], "")
        self.assertEqual(result["query"]["family"], "micro_continuation_fvg")

    def test_rates_are_never_pooled_across_decision_states(self):
        """Half-and-half samples with opposite outcomes must not average.

        Ten winning ABSTAINs and ten losing REJECTs are below the nisab apart
        and above it together.  Pooling would report a 50% rate on 20 samples;
        the contract requires INSUFFICIENT_SAMPLE, because neither population
        on its own can support a rate.
        """

        records = [variant(i, state="ABSTAIN", won=True) for i in range(10)]
        records += [variant(100 + i, state="REJECT", won=False) for i in range(10)]
        result = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "INSUFFICIENT_SAMPLE")
        for rung in result["ladder_rungs_tried"]:
            self.assertLess(
                rung["clean_samples"],
                EVIDENCE_MIN_CLEAN_SAMPLES,
                msg=f"rung {rung} reached the nisab by pooling decision states",
            )

    def test_never_assessed_rows_are_never_evidence_at_any_rung(self):
        """PENDING_DECISION is an attribution gap, not a quieter decision."""

        records = enough(state="PENDING_DECISION") + enough(state="NOT_ASSESSED")
        result = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "INSUFFICIENT_SAMPLE")
        self.assertEqual(result["stats"]["clean"], 0)
        for rung in result["ladder_rungs_tried"]:
            self.assertIn(rung["decision_state"], AI_DECIDED_STATES)

    def test_every_rung_reports_a_single_decision_state(self):
        result = historical_evidence_ladder(
            enough(state="REJECT"),
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        for rung in result["ladder_rungs_tried"]:
            self.assertTrue(rung["decision_state"])
            self.assertIn(rung["decision_state"], AI_DECIDED_STATES)

    def test_widening_never_grants_trading_authority(self):
        """The promotion gate is constants; no amount of widening satisfies it."""

        records = enough(taxonomy="MICRO_OTE_REVERSAL", state="REJECT")
        result = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["match_specificity"], "FAMILY_ANY_DECIDED_STATE")
        self.assertEqual(result["authority"], "DIAGNOSTIC_SHADOW_ONLY")
        self.assertFalse(result["trading_authority"])
        self.assertFalse(result["promotion_gate"]["satisfied"])

    def test_leakage_safety_holds_on_a_widened_rung(self):
        """Widening must not become a back door around the look-ahead rule."""

        records = enough(state="REJECT")
        earliest = min(row.terminal_event_at or 0 for row in records)
        result = historical_evidence_ladder(
            records,
            decision_timestamp=earliest,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "INSUFFICIENT_SAMPLE")
        self.assertEqual(result["stats"]["clean"], 0)

    def test_the_era_floor_still_applies_on_a_widened_rung(self):
        records = enough(state="REJECT", observed_at=EVIDENCE_ERA_FLOOR_TS - 86_400)
        result = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "INSUFFICIENT_SAMPLE")

    def test_the_sweep_half_of_the_nisab_cannot_be_bypassed_by_widening(self):
        """Many variants on few sweeps stay insufficient however wide we go."""

        records = []
        for index in range(EVIDENCE_MIN_CLEAN_SAMPLES + 20):
            row = variant(index, state="REJECT")
            row.sweep_opportunity_id = f"SHARED{index % (EVIDENCE_MIN_SWEEPS - 1)}"
            records.append(row)
        result = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        self.assertEqual(result["state"], "INSUFFICIENT_SAMPLE")

    def test_a_disabled_ladder_is_exactly_the_pre_ladder_behaviour(self):
        policy = EvidencePolicy(ladder_enabled=False)
        records = enough(state="REJECT")
        ladder = historical_evidence_ladder(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
            policy=policy,
        )
        direct = historical_evidence(
            records,
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
            policy=policy,
        )
        self.assertEqual(ladder["state"], direct["state"])
        self.assertEqual(ladder["stats"]["clean"], direct["stats"]["clean"])
        self.assertFalse(ladder["ladder_enabled"])

    def test_the_compacted_payload_carries_the_match_scope(self):
        """An uncitable field is a field the model may not rely on."""

        result = historical_evidence_ladder(
            enough(taxonomy="MICRO_OTE_REVERSAL", state="REJECT"),
            decision_timestamp=DECISION_AT,
            family="micro_continuation_fvg",
            setup_taxonomy="MICRO_BREAKER_RETEST",
            decision_state="ABSTAIN",
        )
        compact = compact_candidate_evidence(result)
        self.assertEqual(compact["match_specificity"], "FAMILY_ANY_DECIDED_STATE")
        self.assertEqual(compact["decision_state_compared"], "REJECT")
        self.assertEqual(compact["match_family"], "micro_continuation_fvg")
        self.assertEqual(compact["match_setup_taxonomy"], "")
        self.assertEqual(compact["era_floor_ts"], EVIDENCE_ERA_FLOOR_TS)
        for key, value in compact.items():
            self.assertNotIsInstance(
                value, (dict, list), msg=f"{key} is not a citable scalar"
            )

    def test_the_ladder_rung_order_widens_by_exactly_one_axis_at_a_time(self):
        axes = [set(axis) for _name, axis in EVIDENCE_LADDER_RUNGS]
        self.assertEqual(axes[0], {"family", "setup_taxonomy", "decision_state"})
        for narrower, wider in zip(axes, axes[1:]):
            self.assertTrue(wider < narrower, "a rung must strictly widen its predecessor")
            self.assertEqual(len(narrower) - len(wider), 1)


class AbsenceIsNotAdverseRuleTests(unittest.TestCase):
    """The third instance of the absence-as-adverse defect, closed generically.

    HTF BOS and follow-through were both fields the model down-weighted because
    nothing told it that a resolved absence is not a finding.  Shadow history
    was the same shape: 58 of 60 live abstentions cited missing history while no
    rule forbade doing so.
    """

    def _rules(self) -> list[str]:
        envelope = decision_evidence.build_decision_evidence_envelope(
            {"symbol": "EURUSD", "candidates": []}
        ).envelope
        context = envelope.get("provider_decision_context") or {}
        return [str(rule) for rule in context.get("rules") or []]

    def test_a_rule_forbids_abstaining_solely_on_insufficient_history(self):
        joined = " ".join(self._rules()).lower()
        self.assertIn("insufficient_sample", joined)
        self.assertIn("resolved absence", joined)
        for verb in ("reject", "abstain", "lower a score"):
            self.assertIn(verb, joined)

    def test_the_rule_does_not_forbid_relying_on_history_that_is_present(self):
        """The rule must scope to ABSENCE, or it would silence real evidence."""

        joined = " ".join(self._rules()).lower()
        self.assertIn("sufficient_sample", joined)
        self.assertIn("real evidence", joined)

    def test_a_rule_explains_the_match_specificity_values(self):
        joined = " ".join(self._rules())
        for rung, _axes in EVIDENCE_LADDER_RUNGS:
            self.assertIn(rung, joined)

    def test_a_rule_states_that_rates_are_never_pooled_across_states(self):
        joined = " ".join(self._rules()).lower()
        self.assertIn("never pooled", joined)

    def test_the_context_version_moved_with_the_rules(self):
        self.assertEqual(
            decision_evidence.PROVIDER_DECISION_CONTEXT_VERSION,
            "20260909_provider_decision_context_v6",
        )


if __name__ == "__main__":
    unittest.main()
