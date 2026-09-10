"""Acceptance tests for the counterfactual (shadow) outcome lifecycle.

Each test name maps to one of the eighteen properties the tracker is required
to prove.  Two surfaces are covered:

*   the Python consolidation/statistics/evidence layer in
    ``shadow_outcome_ledger`` -- exercised through a real ledger file, written
    in the same UTF-16 encoding MQL5's ``FILE_TXT`` writer produces, so the
    reader is tested and not bypassed;
*   the MQL5 tracker itself, asserted against its own source text.  A property
    such as "measurement never starts before entry activation" lives in
    ``TradeEngine.mqh`` and there is no Python object that can be made to
    violate it, so the source is the only honest place to pin it.

Every MQL assertion here was falsified against the pre-change tree recorded at
``scratchpad/TradeEngine.mqh.bak_shadow_v3``; the falsification harness is
``test_mql_assertions_fail_against_the_pre_v4_tracker`` below, which runs only
when that backup is present.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

import shadow_outcome_ledger as ledger
from shadow_outcome_ledger import (
    EVENT_CANDIDATE_OBSERVED,
    EVENT_DATA_QUALITY_FAILURE,
    EVENT_DECISION_RECORDED,
    EVENT_ENTRY_ACTIVATED,
    EVENT_OPPORTUNITY_OBSERVED,
    EVENT_PATH_PROGRESS,
    EVENT_TERMINAL_RESOLUTION,
    EVENT_TP1_REACHED,
    SHADOW_LEDGER_SCHEMA_VERSION,
    EvidencePolicy,
    aggregate_shadow_outcomes,
    analyze_abstain_performance,
    audit_ledger,
    compact_candidate_evidence,
    consolidate_shadow_lifecycle,
    historical_evidence,
    read_shadow_events,
    summarize_group,
)

from test_governance_contracts import MQL_STAGE, _function_body


# --------------------------------------------------------------------------
# Ledger fixtures
# --------------------------------------------------------------------------

# Fixture epoch.  Deliberately at or after EVIDENCE_ERA_FLOOR_TS so every test
# here exercises the DEFAULT evidence policy, engine-era floor included, instead
# of silently testing a configuration nothing runs with.  Tests that are about
# the era filter itself set their own floor explicitly.
BASE_TS = 1_789_000_000


def _envelope(
    event_type: str,
    *,
    variant: str,
    opportunity: str,
    at: int,
    decision_state: str = "PENDING_DECISION",
    **overrides,
) -> dict:
    """One event carrying the full v4 envelope the EA emits."""

    row = {
        "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
        "event_type": event_type,
        "event_id": f"{event_type}:{variant}:{at}",
        "event_at": at,
        "sweep_opportunity_id": opportunity,
        "candidate_variant_id": variant,
        "parent_record_hash": f"REC_{variant}",
        "candidate_hash": f"CAND_{variant}",
        "execution_fingerprint": f"FP_{variant}",
        "symbol": "EURUSD",
        "family": "full_po3_continuation",
        "setup_taxonomy": "FULL_PO3_CONTINUATION",
        "decision_state": decision_state,
        "decision_source": "NOT_YET_DECIDED",
        "tracking_status": "PENDING",
        "trading_authority": False,
        "can_trade": False,
    }
    row.update(overrides)
    return row


def opportunity_event(opportunity: str, *, at: int = BASE_TS, **overrides) -> dict:
    row = _envelope(
        EVENT_OPPORTUNITY_OBSERVED,
        variant="",
        opportunity=opportunity,
        at=at,
        direction="BUY",
        sweep_side="sell_side",
        sweep_opportunity_lineage="lineage|A",
        session="london",
        killzone="london_open",
        context_tier="A",
        po3_state="PO3_FVG_CONFIRMED",
        source_sweep_timestamp=at - 900,
        source_displacement_timestamp=at - 600,
        source_bos_timestamp=at - 300,
        statistical_weight=1.0,
    )
    row.pop("candidate_variant_id")
    row.update(overrides)
    return row


def candidate_event(
    variant: str,
    opportunity: str,
    *,
    at: int = BASE_TS,
    decision_state: str = "PENDING_DECISION",
    entry_branch: str = "fvg_retest",
    session: str = "london",
    horizon_at: int | None = None,
    **overrides,
) -> dict:
    fields = {
        "observed_at": at,
        "horizon_at": horizon_at if horizon_at is not None else at + 86_400,
        "entry_branch": entry_branch,
        "session": session,
        "setup_class": "full_po3_continuation.fvg_retest",
        "target_model": "liquidity_target",
        "tp_model": "partial_then_liquidity",
        "decision_stage": "pre_ai",
        "assessed_entry": 1.1000,
        "assessed_sl": 1.0950,
        "assessed_tp1": 1.1025,
        "assessed_tp2": 1.1150,
        "variant_revision": 0,
        "variant_parent_id": "",
    }
    fields.update(overrides)
    return _envelope(
        EVENT_CANDIDATE_OBSERVED,
        variant=variant,
        opportunity=opportunity,
        at=at,
        decision_state=decision_state,
        **fields,
    )


def decision_event(
    variant: str,
    opportunity: str,
    *,
    at: int,
    decision_state: str = "ABSTAIN",
    rejection_reason: str = "ai_abstain_missing_follow_through",
    **overrides,
) -> dict:
    return _envelope(
        EVENT_DECISION_RECORDED,
        variant=variant,
        opportunity=opportunity,
        at=at,
        decision_state=decision_state,
        decision_source="ai_abstained",
        decision_stage="post_ai",
        rejection_reason=rejection_reason,
        model_version="gpt-5.6-luna",
        prompt_contract_version="prompt_v12",
        decision_schema_version="decision_v10",
        python_reasons=rejection_reason,
        mql_reasons="",
        **overrides,
    )


def terminal_event(
    variant: str,
    opportunity: str,
    *,
    at: int,
    terminal: str,
    decision_state: str = "ABSTAIN",
    entry_activated: bool = True,
    censoring: str = "UNCENSORED_TERMINAL",
    ambiguity: str = "NONE",
    data_quality: str = "RESOLVED_CLEAN_PRICE_PATH",
    ordering_source: str = "m1_bar",
    **overrides,
) -> dict:
    """A terminal row whose flags are internally consistent with ``terminal``."""

    row = _envelope(
        EVENT_TERMINAL_RESOLUTION,
        variant=variant,
        opportunity=opportunity,
        at=at,
        decision_state=decision_state,
        decision_source="ai_abstained",
        terminal_event=terminal,
        terminal_event_at=at,
        censoring_status=censoring,
        ambiguity_status=ambiguity,
        data_quality_status=data_quality,
        ordering_source=ordering_source,
        entry_activated=entry_activated,
        entry_activated_at=(BASE_TS + 300) if entry_activated else None,
        time_to_entry_sec=300 if entry_activated else None,
        entry_never_reached=not entry_activated,
        entry_order_ambiguous=False,
        tp1_hit=False,
        tp2_hit=False,
        tp1_before_sl=None,
        sl_before_tp1=None,
        tp2_before_sl=None,
        sl_before_tp2=None,
        tp1_then_sl=None,
        tp1_then_tp2=None,
        neither_target_nor_stop=None,
        mfe_r=None,
        mae_r=None,
        maximum_favorable_price=None,
        maximum_adverse_price=None,
        result_r_unmanaged=None,
        result_r_with_configured_tp1_partial=None,
        tracking_status=terminal,
    )
    if terminal == "TP2_BEFORE_SL":
        row.update(
            tp2_hit=True,
            tp2_before_sl=True,
            sl_before_tp2=False,
            tp1_hit=True,
            tp1_before_sl=True,
            sl_before_tp1=False,
            mfe_r=3.0,
            mae_r=0.2,
            result_r_unmanaged=3.0,
            result_r_with_configured_tp1_partial=2.5,
        )
    elif terminal == "TP1_THEN_TP2":
        row.update(
            tp1_hit=True,
            tp1_before_sl=True,
            sl_before_tp1=False,
            tp2_hit=True,
            tp2_before_sl=True,
            sl_before_tp2=False,
            tp1_then_tp2=True,
            tp1_then_sl=False,
            mfe_r=3.0,
            mae_r=0.3,
            result_r_unmanaged=3.0,
            result_r_with_configured_tp1_partial=2.6,
        )
    elif terminal == "TP1_THEN_SL":
        row.update(
            tp1_hit=True,
            tp1_before_sl=True,
            sl_before_tp1=False,
            tp2_hit=False,
            tp2_before_sl=False,
            sl_before_tp2=True,
            tp1_then_sl=True,
            tp1_then_tp2=False,
            mfe_r=0.7,
            mae_r=1.0,
            result_r_unmanaged=-1.0,
            result_r_with_configured_tp1_partial=-0.7,
        )
    elif terminal == "SL_BEFORE_TP1":
        row.update(
            tp1_hit=False,
            tp1_before_sl=False,
            sl_before_tp1=True,
            tp2_before_sl=False,
            sl_before_tp2=True,
            mfe_r=0.1,
            mae_r=1.0,
            result_r_unmanaged=-1.0,
            result_r_with_configured_tp1_partial=-1.0,
        )
    elif terminal in ("HORIZON_CENSORED", "SESSION_CLOSE_CENSORED"):
        row.update(
            censoring_status="RIGHT_CENSORED_HORIZON"
            if terminal == "HORIZON_CENSORED"
            else "RIGHT_CENSORED_SESSION_CLOSE",
            data_quality_status="RESOLVED_CENSORED",
            neither_target_nor_stop=True,
            mfe_r=0.4,
            mae_r=0.3,
            result_r_unmanaged=0.15,
            result_r_with_configured_tp1_partial=0.15,
        )
    elif terminal == "ENTRY_NEVER_REACHED":
        row.update(
            entry_activated=False,
            entry_activated_at=None,
            time_to_entry_sec=None,
            entry_never_reached=True,
            data_quality_status="RESOLVED_ENTRY_NOT_ACTIVATED",
            censoring_status="UNCENSORED_TERMINAL",
        )
    elif terminal.startswith("AMBIGUOUS"):
        row.update(
            ambiguity_status="AMBIGUOUS",
            ambiguity_reason="tp1_and_sl_same_m1_bar_without_tick_sequence",
            censoring_status="EXCLUDED_AMBIGUOUS",
            data_quality_status="EXCLUDED_AMBIGUOUS_INTRABAR_ORDER",
            tp1_hit=True,
        )
    elif terminal == "DATA_LOSS":
        row.update(
            censoring_status="EXCLUDED_DATA_LOSS",
            data_quality_status="EXCLUDED_DATA_LOSS",
            ambiguity_status="AMBIGUOUS",
            ambiguity_reason="price_path_unavailable_at_horizon",
        )
    elif terminal == "UNTRACKABLE":
        row.update(
            censoring_status="EXCLUDED_INVALID_CONTRACT",
            data_quality_status="UNTRACKABLE_INVALID_CONTRACT",
            entry_activated=False,
            entry_never_reached=False,
        )
    row.update(overrides)
    return row


def abstain_lifecycle(
    variant: str,
    opportunity: str,
    *,
    observed_at: int,
    terminal: str,
    resolved_at: int | None = None,
    family: str = "full_po3_continuation",
    session: str = "london",
    entry_branch: str = "fvg_retest",
    rejection_reason: str = "ai_abstain_missing_follow_through",
    decision_state: str = "ABSTAIN",
) -> list[dict]:
    """The full observation -> decision -> activation -> terminal chain."""

    end = resolved_at if resolved_at is not None else observed_at + 7_200
    rows = [
        opportunity_event(opportunity, at=observed_at, session=session),
        candidate_event(
            variant,
            opportunity,
            at=observed_at,
            entry_branch=entry_branch,
            session=session,
            family=family,
        ),
        decision_event(
            variant,
            opportunity,
            at=observed_at + 30,
            decision_state=decision_state,
            rejection_reason=rejection_reason,
            family=family,
            session=session,
        ),
    ]
    if terminal != "ENTRY_NEVER_REACHED":
        rows.append(
            _envelope(
                EVENT_ENTRY_ACTIVATED,
                variant=variant,
                opportunity=opportunity,
                at=observed_at + 300,
                decision_state=decision_state,
                entry_activated_at=observed_at + 300,
                time_to_entry_sec=300,
                entry_touch_price=1.1000,
                entry_order_ambiguous=False,
                family=family,
                session=session,
            )
        )
    rows.append(
        terminal_event(
            variant,
            opportunity,
            at=end,
            terminal=terminal,
            decision_state=decision_state,
            entry_activated=terminal != "ENTRY_NEVER_REACHED",
            family=family,
            session=session,
            entry_branch=entry_branch,
            rejection_reason=rejection_reason,
        )
    )
    return rows


def write_ledger(rows, *, encoding: str = "utf-16") -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding=encoding, newline="\n"
    )
    with handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return Path(handle.name)


def consolidate(rows):
    return consolidate_shadow_lifecycle([dict(row, _line=index) for index, row in enumerate(rows, 1)])


def by_id(consolidation):
    return {record.candidate_variant_id: record for record in consolidation.variants}


# --------------------------------------------------------------------------
# Properties 1-9, 12-18: the Python consolidation and analysis layer
# --------------------------------------------------------------------------


class ShadowLifecyclePythonContractTests(unittest.TestCase):
    """The ledger is folded into outcomes without inventing wins or losses."""

    def test_property_01_abstain_candidates_are_tracked_to_a_terminal_outcome(self) -> None:
        """ABSTAIN is a decision, not an exit from the study."""

        rows = abstain_lifecycle("V-ABS", "OPP-1", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        record = by_id(consolidate(rows))["V-ABS"]
        self.assertEqual(record.decision_state, "ABSTAIN")
        self.assertEqual(record.terminal_event, "TP2_BEFORE_SL")
        self.assertEqual(record.sample_class, "CLEAN")
        self.assertTrue(record.entry_activated)
        self.assertIsNotNone(record.result_r_unmanaged)

    def test_property_02_entry_activation_precedes_every_measured_level(self) -> None:
        """A variant that never activated carries no MFE, MAE or R at all."""

        rows = abstain_lifecycle(
            "V-NOENTRY", "OPP-2", observed_at=BASE_TS, terminal="ENTRY_NEVER_REACHED"
        )
        record = by_id(consolidate(rows))["V-NOENTRY"]
        self.assertFalse(record.entry_activated)
        self.assertTrue(record.entry_never_reached)
        for measurement in (
            record.mfe_r,
            record.mae_r,
            record.result_r_unmanaged,
            record.result_r_with_configured_tp1_partial,
        ):
            self.assertIsNone(measurement)

    def test_property_03_tp1_then_tp2_is_recorded_as_an_ordered_pair(self) -> None:
        rows = abstain_lifecycle("V-T1T2", "OPP-3", observed_at=BASE_TS, terminal="TP1_THEN_TP2")
        record = by_id(consolidate(rows))["V-T1T2"]
        self.assertTrue(record.tp1_hit)
        self.assertTrue(record.tp2_hit)
        self.assertTrue(record.tp1_then_tp2)
        self.assertFalse(record.tp1_then_sl)
        self.assertTrue(record.tp1_before_sl)
        self.assertTrue(record.tp2_before_sl)
        self.assertEqual(record.sample_class, "CLEAN")

    def test_property_04_tp1_then_sl_keeps_the_partial_reward_and_loses_the_runner(self) -> None:
        """Entry quality and management quality must not be averaged together."""

        rows = abstain_lifecycle("V-T1SL", "OPP-4", observed_at=BASE_TS, terminal="TP1_THEN_SL")
        record = by_id(consolidate(rows))["V-T1SL"]
        self.assertTrue(record.tp1_hit)
        self.assertTrue(record.tp1_then_sl)
        self.assertFalse(record.tp2_hit)
        self.assertEqual(record.result_r_unmanaged, -1.0)
        self.assertGreater(record.result_r_with_configured_tp1_partial, record.result_r_unmanaged)

    def test_property_05_sl_before_tp1_is_a_clean_loss_with_no_tp1_credit(self) -> None:
        rows = abstain_lifecycle("V-SL", "OPP-5", observed_at=BASE_TS, terminal="SL_BEFORE_TP1")
        record = by_id(consolidate(rows))["V-SL"]
        self.assertTrue(record.sl_before_tp1)
        self.assertFalse(record.tp1_before_sl)
        self.assertFalse(record.tp1_hit)
        self.assertEqual(record.sample_class, "CLEAN")
        self.assertEqual(record.result_r_unmanaged, -1.0)
        self.assertEqual(record.result_r_with_configured_tp1_partial, -1.0)

    def test_property_06_same_bar_tp1_and_sl_is_excluded_not_scored(self) -> None:
        """Without tick ordering the outcome is unknown -- and unknown is not a loss."""

        rows = abstain_lifecycle(
            "V-AMB", "OPP-6", observed_at=BASE_TS, terminal="AMBIGUOUS_TP1_AND_SL_SAME_BAR"
        )
        record = by_id(consolidate(rows))["V-AMB"]
        self.assertEqual(record.sample_class, "EXCLUDED_AMBIGUOUS")
        self.assertEqual(record.ambiguity_status, "AMBIGUOUS")
        self.assertIsNone(record.result_r_unmanaged)

        stats = summarize_group("amb", [record])
        self.assertEqual(stats.ambiguous, 1)
        self.assertEqual(stats.clean, 0)
        self.assertEqual(stats.sl_before_tp1, 0)
        self.assertEqual(stats.tp2_before_sl, 0)
        self.assertIsNone(stats.tp2_before_sl_rate)

    def test_property_07_tick_ordering_turns_the_same_bar_into_a_clean_sample(self) -> None:
        """The only difference is ``ordering_source``; the classification follows it."""

        rows = abstain_lifecycle(
            "V-TICK", "OPP-7", observed_at=BASE_TS, terminal="TP1_THEN_SL"
        )
        rows[-1]["ordering_source"] = "tick_sequence"
        record = by_id(consolidate(rows))["V-TICK"]
        self.assertEqual(record.ordering_source, "tick_sequence")
        self.assertEqual(record.ambiguity_status, "NONE")
        self.assertEqual(record.sample_class, "CLEAN")
        self.assertTrue(record.tp1_then_sl)

    def test_property_08_entry_never_reached_is_neither_a_win_nor_a_loss(self) -> None:
        records = [
            by_id(consolidate(abstain_lifecycle(f"V-N{i}", f"OPP-N{i}", observed_at=BASE_TS, terminal="ENTRY_NEVER_REACHED")))[f"V-N{i}"]
            for i in range(3)
        ]
        stats = summarize_group("never", records, weight_by_sweep=False)
        self.assertEqual(stats.entry_never_reached, 3)
        self.assertEqual(stats.clean, 0)
        self.assertEqual(stats.tp2_before_sl, 0)
        self.assertEqual(stats.sl_before_tp1, 0)
        self.assertIsNone(stats.tp2_before_sl_rate)
        self.assertIsNone(stats.mean_result_r_unmanaged)
        # The activation denominator still counts them, which is the whole point:
        # "entry never reached" is a measurable property of the setup.
        self.assertEqual(stats.entry_activation_rate, 0.0)

    def test_property_09_horizon_and_session_close_are_censored_not_scored_as_losses(self) -> None:
        horizon = by_id(
            consolidate(abstain_lifecycle("V-H", "OPP-H", observed_at=BASE_TS, terminal="HORIZON_CENSORED"))
        )["V-H"]
        session = by_id(
            consolidate(
                abstain_lifecycle("V-S", "OPP-S", observed_at=BASE_TS, terminal="SESSION_CLOSE_CENSORED")
            )
        )["V-S"]
        for record in (horizon, session):
            self.assertEqual(record.sample_class, "RIGHT_CENSORED")
            self.assertTrue(record.neither_target_nor_stop)

        stats = summarize_group("censored", [horizon, session], weight_by_sweep=False)
        self.assertEqual(stats.censored, 2)
        self.assertEqual(stats.clean, 0)
        self.assertEqual(stats.sl_before_tp1, 0)
        self.assertIsNone(stats.tp2_before_sl_rate)
        # Censored rows still carry a measured open R, so the R average uses them
        # while the win/loss rates deliberately do not.
        self.assertIsNotNone(stats.mean_result_r_unmanaged)

    def test_property_10_data_loss_is_one_explicit_terminal_and_is_excluded(self) -> None:
        rows = abstain_lifecycle("V-DL", "OPP-DL", observed_at=BASE_TS, terminal="DATA_LOSS")
        rows.insert(
            -1,
            _envelope(
                EVENT_DATA_QUALITY_FAILURE,
                variant="V-DL",
                opportunity="OPP-DL",
                at=BASE_TS + 7_000,
                failure="copy_rates_unavailable_after_retries",
                detail="retries=30",
            ),
        )
        record = by_id(consolidate(rows))["V-DL"]
        self.assertEqual(record.terminal_event, "DATA_LOSS")
        self.assertEqual(record.sample_class, "EXCLUDED_DATA_LOSS")
        self.assertEqual(len(record.data_quality_failures), 1)
        self.assertIsNone(record.result_r_unmanaged)

        stats = summarize_group("dl", [record])
        self.assertEqual(stats.data_loss, 1)
        self.assertEqual(stats.clean, 0)
        # A data-loss row never had a decidable activation, so it must not sit in
        # the activation denominator either.
        self.assertIsNone(stats.entry_activation_rate)

    def test_property_11_an_overdue_pending_record_is_reported_not_silently_kept(self) -> None:
        """Restart recovery is an MQL behaviour; the audit is what proves it ran."""

        rows = [
            opportunity_event("OPP-P", at=BASE_TS),
            candidate_event("V-PENDING", "OPP-P", at=BASE_TS, horizon_at=BASE_TS + 3_600),
            decision_event("V-PENDING", "OPP-P", at=BASE_TS + 30),
        ]
        path = write_ledger(rows)
        try:
            read = read_shadow_events(path)
            consolidation = consolidate_shadow_lifecycle(read.rows)
            report = audit_ledger(read, consolidation, now=BASE_TS + 100_000)
            self.assertFalse(report["healthy"])
            self.assertEqual(len(report["findings"]["expired_pending_records"]), 1)
            self.assertEqual(
                report["findings"]["expired_pending_records"][0]["candidate_variant_id"], "V-PENDING"
            )

            # Once resolved, the same ledger audits clean.
            rows.append(
                terminal_event("V-PENDING", "OPP-P", at=BASE_TS + 3_600, terminal="HORIZON_CENSORED")
            )
            resolved_path = write_ledger(rows)
            try:
                read2 = read_shadow_events(resolved_path)
                report2 = audit_ledger(
                    read2, consolidate_shadow_lifecycle(read2.rows), now=BASE_TS + 100_000
                )
                self.assertEqual(report2["findings"]["expired_pending_records"], [])
                self.assertTrue(report2["healthy"])
            finally:
                resolved_path.unlink(missing_ok=True)
        finally:
            path.unlink(missing_ok=True)

    def test_property_12_a_second_terminal_is_a_conflict_never_a_second_sample(self) -> None:
        rows = abstain_lifecycle("V-DUP", "OPP-D", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        rows.append(
            terminal_event("V-DUP", "OPP-D", at=BASE_TS + 9_000, terminal="SL_BEFORE_TP1")
        )
        rows.append(
            terminal_event("V-DUP", "OPP-D", at=BASE_TS + 9_100, terminal="TP2_BEFORE_SL")
        )
        consolidation = consolidate(rows)
        self.assertEqual(len(consolidation.variants), 1)
        record = consolidation.variants[0]
        self.assertEqual(record.terminal_event, "TP2_BEFORE_SL")
        reasons = sorted(row["reason"] for row in consolidation.conflicts)
        self.assertEqual(reasons, ["conflicting_terminal_outcome", "duplicate_terminal_outcome"])
        stats = summarize_group("dup", consolidation.variants)
        self.assertEqual(stats.clean, 1)

    def test_property_13_repeated_scans_of_one_sweep_produce_one_opportunity(self) -> None:
        rows = [opportunity_event("OPP-R", at=BASE_TS + step) for step in (0, 900, 1_800, 2_700)]
        rows.append(candidate_event("V-R", "OPP-R", at=BASE_TS))
        rows.append(candidate_event("V-R", "OPP-R", at=BASE_TS + 900))
        consolidation = consolidate(rows)
        self.assertEqual(len(consolidation.opportunities), 1)
        self.assertEqual(consolidation.opportunities["OPP-R"]["duplicate_observations"], 3)
        self.assertEqual(len(consolidation.variants), 1)
        self.assertEqual(
            [row["reason"] for row in consolidation.rejected],
            ["duplicate_candidate_variant_observation"],
        )

    def test_property_14_genuinely_different_plans_stay_separate_children(self) -> None:
        """Same sweep, different entry/SL/TP: two variants, one opportunity."""

        rows = [opportunity_event("OPP-V", at=BASE_TS)]
        rows += [
            candidate_event(
                "V-A",
                "OPP-V",
                at=BASE_TS,
                entry_branch="fvg_retest",
                assessed_entry=1.1000,
                assessed_tp2=1.1150,
            ),
            candidate_event(
                "V-B",
                "OPP-V",
                at=BASE_TS,
                entry_branch="breaker_retest",
                assessed_entry=1.1010,
                assessed_tp2=1.1200,
                variant_parent_id="V-A",
                variant_revision=1,
            ),
        ]
        rows.append(terminal_event("V-A", "OPP-V", at=BASE_TS + 3_600, terminal="TP2_BEFORE_SL"))
        rows.append(terminal_event("V-B", "OPP-V", at=BASE_TS + 3_600, terminal="SL_BEFORE_TP1"))
        consolidation = consolidate(rows)
        self.assertEqual(len(consolidation.opportunities), 1)
        self.assertEqual(len(consolidation.variants), 2)
        records = by_id(consolidation)
        self.assertNotEqual(records["V-A"].terminal_event, records["V-B"].terminal_event)
        self.assertEqual(records["V-B"].variant_parent_id, "V-A")
        self.assertEqual(consolidation.rejected, [])

    def test_property_15_aggregates_weight_each_sweep_once(self) -> None:
        """Ten branches of one sweep must not outvote ten separate sweeps."""

        crowded = [opportunity_event("OPP-CROWD", at=BASE_TS)]
        for index in range(10):
            crowded.append(candidate_event(f"V-C{index}", "OPP-CROWD", at=BASE_TS + index))
            crowded.append(
                terminal_event(
                    f"V-C{index}", "OPP-CROWD", at=BASE_TS + 3_600 + index, terminal="SL_BEFORE_TP1"
                )
            )
        singles = []
        for index in range(3):
            singles += abstain_lifecycle(
                f"V-S{index}", f"OPP-S{index}", observed_at=BASE_TS, terminal="TP2_BEFORE_SL"
            )
        consolidation = consolidate(crowded + singles)
        weighted = summarize_group("all", consolidation.variants)
        unweighted = summarize_group("all", consolidation.variants, weight_by_sweep=False)

        self.assertEqual(weighted.variants, 13)
        self.assertEqual(weighted.unique_sweeps, 4)
        # Sweep-weighted: 1 loss from the crowded sweep + 3 wins = 3/4.
        self.assertEqual(weighted.clean, 4)
        self.assertEqual(weighted.tp2_before_sl, 3)
        self.assertAlmostEqual(weighted.tp2_before_sl_rate, 0.75)
        # Unweighted the same data reads 3/13 -- the number the study must not use.
        self.assertEqual(unweighted.clean, 13)
        self.assertAlmostEqual(unweighted.tp2_before_sl_rate, 3 / 13)

    def test_property_16_no_outcome_at_or_after_the_decision_can_enter_its_evidence(self) -> None:
        """Look-ahead is excluded by timestamp, and the boundary is exclusive."""

        decision_at = BASE_TS + 100_000
        rows = []
        for index in range(30):
            rows += abstain_lifecycle(
                f"V-PAST{index}",
                f"OPP-PAST{index}",
                observed_at=BASE_TS + index,
                resolved_at=decision_at - 10,
                terminal="TP2_BEFORE_SL",
            )
        for index in range(30):
            rows += abstain_lifecycle(
                f"V-FUT{index}",
                f"OPP-FUT{index}",
                observed_at=BASE_TS + index,
                resolved_at=decision_at + 10,
                terminal="SL_BEFORE_TP1",
            )
        # Exactly on the decision instant: still future, because "at" is not "before".
        rows += abstain_lifecycle(
            "V-EDGE", "OPP-EDGE", observed_at=BASE_TS, resolved_at=decision_at, terminal="SL_BEFORE_TP1"
        )
        records = consolidate(rows).variants

        policy = EvidencePolicy(enabled=True, min_clean_samples=20, min_sweeps=12)
        evidence = historical_evidence(
            records,
            decision_timestamp=decision_at,
            family="full_po3_continuation",
            decision_state="ABSTAIN",
            policy=policy,
        )
        self.assertEqual(evidence["state"], "SUFFICIENT_SAMPLE")
        self.assertEqual(evidence["stats"]["clean"], 30)
        self.assertEqual(evidence["excluded_not_yet_terminal_at_decision_time"], 31)
        # Every eligible outcome was a win, so a leaked loss would move this off 1.0.
        self.assertEqual(evidence["stats"]["tp2_before_sl_rate"], 1.0)
        self.assertEqual(
            evidence["leakage_policy"], "terminal_event_at_strictly_before_decision_timestamp"
        )
        self.assertFalse(evidence["trading_authority"])
        self.assertEqual(evidence["authority"], "DIAGNOSTIC_SHADOW_ONLY")

    def test_property_16b_unresolved_variants_are_excluded_not_imputed(self) -> None:
        rows = abstain_lifecycle(
            "V-OK", "OPP-OK", observed_at=BASE_TS, resolved_at=BASE_TS + 100, terminal="TP2_BEFORE_SL"
        )
        rows += [
            opportunity_event("OPP-OPEN", at=BASE_TS),
            candidate_event("V-OPEN", "OPP-OPEN", at=BASE_TS),
            decision_event("V-OPEN", "OPP-OPEN", at=BASE_TS + 30),
        ]
        records = consolidate(rows).variants
        evidence = historical_evidence(
            records, decision_timestamp=BASE_TS + 50_000, decision_state="ABSTAIN"
        )
        self.assertEqual(evidence["excluded_unresolved"], 1)
        self.assertEqual(evidence["stats"]["clean"], 1)

    def test_property_17_every_terminal_joins_to_its_observation_and_decision(self) -> None:
        """An identity that does not match exactly is rejected, never guessed."""

        rows = abstain_lifecycle("V-J", "OPP-J", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        rows.append(
            terminal_event(
                "V-ORPHAN", "OPP-J", at=BASE_TS + 4_000, terminal="TP2_BEFORE_SL"
            )
        )
        rows.append(
            terminal_event(
                "V-J",
                "OPP-J",
                at=BASE_TS + 4_100,
                terminal="SL_BEFORE_TP1",
                execution_fingerprint="FP_SOMETHING_ELSE",
            )
        )
        consolidation = consolidate(rows)
        record = by_id(consolidation)["V-J"]
        self.assertEqual(record.terminal_event, "TP2_BEFORE_SL")
        self.assertEqual(record.parent_record_hash, "REC_V-J")
        self.assertEqual(record.execution_fingerprint, "FP_V-J")
        reasons = sorted(row["reason"] for row in consolidation.rejected)
        self.assertEqual(
            reasons,
            ["shadow_delta_execution_fingerprint_mismatch", "shadow_delta_parent_missing"],
        )

        read = ledger.LedgerRead(rows=[], unparseable=[], legacy_rows=0, total_lines=len(rows))
        report = audit_ledger(read, consolidation)
        self.assertEqual(len(report["findings"]["orphaned_outcomes"]), 1)
        self.assertEqual(len(report["findings"]["fingerprint_mismatches"]), 1)
        self.assertFalse(report["healthy"])

    def test_property_18_a_realistic_ledger_produces_nonzero_clean_resolutions(self) -> None:
        """The end-to-end shape: a written file in, clean statistics out."""

        rows: list[dict] = []
        plan = [
            ("TP2_BEFORE_SL", 9),
            ("TP1_THEN_TP2", 5),
            ("TP1_THEN_SL", 4),
            ("SL_BEFORE_TP1", 8),
            ("HORIZON_CENSORED", 3),
            ("ENTRY_NEVER_REACHED", 6),
            ("AMBIGUOUS_TP1_AND_SL_SAME_BAR", 2),
            ("DATA_LOSS", 1),
        ]
        index = 0
        for terminal, count in plan:
            for _ in range(count):
                rows += abstain_lifecycle(
                    f"V-E{index}",
                    f"OPP-E{index}",
                    observed_at=BASE_TS + index * 60,
                    resolved_at=BASE_TS + index * 60 + 7_200,
                    terminal=terminal,
                )
                index += 1

        path = write_ledger(rows)
        try:
            read = read_shadow_events(path)
            self.assertEqual(read.unparseable, [])
            consolidation = consolidate_shadow_lifecycle(read.rows)
            self.assertEqual(len(consolidation.variants), 38)
            self.assertEqual(consolidation.conflicts, [])
            self.assertEqual(consolidation.rejected, [])

            report = audit_ledger(read, consolidation, now=BASE_TS + 500_000)
            self.assertTrue(report["healthy"], report["findings"])
            self.assertEqual(report["terminal_resolutions"], 38)

            aggregate = aggregate_shadow_outcomes(consolidation.variants)
            overall = aggregate["overall"]
            self.assertEqual(overall["clean"], 26)
            self.assertEqual(overall["censored"], 3)
            self.assertEqual(overall["ambiguous"], 2)
            self.assertEqual(overall["data_loss"], 1)
            self.assertEqual(overall["entry_never_reached"], 6)
            self.assertEqual(overall["unresolved"], 0)
            self.assertEqual(aggregate["total_unique_sweeps"], 38)
            self.assertFalse(aggregate["trading_authority"])
            self.assertEqual(aggregate["authority"], "DIAGNOSTIC_SHADOW_ONLY")

            # 14 of 26 clean samples reached TP2 (9 direct + 5 via TP1).
            self.assertEqual(overall["tp2_before_sl"], 14)
            self.assertEqual(overall["tp1_before_sl"], 18)
            self.assertEqual(overall["sl_before_tp1"], 8)
        finally:
            path.unlink(missing_ok=True)


class ShadowAbstainAnalysisTests(unittest.TestCase):
    """Requirement 7: what ABSTAIN actually cost, stated as separable numbers."""

    def _population(self) -> list:
        rows: list[dict] = []
        index = 0

        def add(state: str, terminal: str, count: int, reason: str) -> None:
            nonlocal index
            for _ in range(count):
                rows.extend(
                    abstain_lifecycle(
                        f"V-{state}{index}",
                        f"OPP-{state}{index}",
                        observed_at=BASE_TS + index * 60,
                        resolved_at=BASE_TS + index * 60 + 3_600,
                        terminal=terminal,
                        decision_state=state,
                        rejection_reason=reason,
                    )
                )
                index += 1

        add("ABSTAIN", "TP2_BEFORE_SL", 10, "ai_abstain_missing_follow_through")
        add("ABSTAIN", "SL_BEFORE_TP1", 20, "ai_abstain_missing_follow_through")
        add("APPROVE", "TP2_BEFORE_SL", 15, "")
        add("APPROVE", "SL_BEFORE_TP1", 5, "")
        add("REJECT", "SL_BEFORE_TP1", 12, "ai_veto_structural_contradiction")
        return consolidate(rows).variants

    def test_abstain_outcomes_report_missed_and_avoided_separately(self) -> None:
        analysis = analyze_abstain_performance(self._population())
        self.assertEqual(analysis["abstain_clean_samples"], 30)
        self.assertEqual(analysis["missed_tp2_opportunities"], 10)
        self.assertEqual(analysis["avoided_sl_outcomes"], 20)

    def test_approve_and_reject_deltas_against_abstain_are_computed(self) -> None:
        analysis = self._population()
        result = analyze_abstain_performance(analysis)
        # APPROVE 15/20 = 0.75 against ABSTAIN 10/30 = 0.3333.
        self.assertAlmostEqual(result["approve_minus_abstain"]["tp2_before_sl_rate"], 0.75 - (1 / 3))
        # REJECT never reached TP2 at all.
        self.assertAlmostEqual(result["reject_minus_abstain"]["tp2_before_sl_rate"], -(1 / 3))

    def test_an_abstain_reason_is_reported_with_its_own_sample_and_separation(self) -> None:
        result = analyze_abstain_performance(self._population())
        separation = result["abstain_reason_separation"]
        self.assertTrue(separation, "abstain reasons must be aggregated individually")
        key = next(iter(separation))
        entry = separation[key]
        self.assertIn("separates_favorable_from_unfavorable", entry)
        self.assertEqual(
            entry["separation_basis"], "wilson_interval_non_overlap_vs_abstain_baseline"
        )


class ShadowDecisionAttributionTests(unittest.TestCase):
    """An infrastructure envelope must never be counted as an AI decision.

    An error envelope is schema-required to carry APPROVE/REJECT/ABSTAIN, so a
    local pipeline failure reaches MT5 as ``decision_state="REJECT"`` with
    ``decision_quality_tier=DEGRADED_NON_TRADING`` and no candidate assessments.
    Every one of the 89 decision events in the 2026-09-08 ledger was such an
    envelope, recorded as an AI REJECT.
    """

    def _degraded(self, variant: str, opportunity: str, *, at: int) -> list[dict]:
        rows = abstain_lifecycle(
            variant,
            opportunity,
            observed_at=at,
            resolved_at=at + 3_600,
            terminal="TP2_BEFORE_SL",
        )
        for row in rows:
            if row["event_type"] in {EVENT_DECISION_RECORDED, EVENT_TERMINAL_RESOLUTION}:
                row["decision_state"] = "NOT_ASSESSED"
                row["request_decision_state"] = "REJECT"
                row["decision_quality_tier"] = "DEGRADED_NON_TRADING"
                row["trading_tier"] = False
        return rows

    def test_a_degraded_envelope_is_not_counted_as_an_ai_decision(self) -> None:
        records = consolidate(self._degraded("V-D1", "OPP-D1", at=BASE_TS)).variants
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.decision_state, "NOT_ASSESSED")
        self.assertEqual(record.request_decision_state, "REJECT")
        self.assertIs(record.trading_tier, False)
        # The outcome itself is still tracked -- only the attribution changed.
        self.assertEqual(record.terminal_event, "TP2_BEFORE_SL")

    def test_a_degraded_envelope_never_reaches_abstain_evidence(self) -> None:
        rows: list[dict] = []
        for index in range(260):
            rows += self._degraded(f"V-D{index}", f"OPP-D{index}", at=BASE_TS + index * 60)
        records = consolidate(rows).variants

        evidence = historical_evidence(
            records, decision_timestamp=BASE_TS + 900_000, decision_state="ABSTAIN"
        )
        self.assertEqual(evidence["state"], "INSUFFICIENT_SAMPLE")
        self.assertEqual(evidence["stats"]["clean"], 0)

    def test_a_reject_query_also_refuses_the_degraded_rows(self) -> None:
        """The mislabel was REJECT, so that is the query that must stay clean."""

        rows: list[dict] = []
        for index in range(40):
            batch = self._degraded(f"V-R{index}", f"OPP-R{index}", at=BASE_TS + index * 60)
            for row in batch:
                if row["event_type"] in {EVENT_DECISION_RECORDED, EVENT_TERMINAL_RESOLUTION}:
                    # The pre-fix ledger wrote the request verdict here verbatim.
                    row["decision_state"] = "REJECT"
            rows += batch
        records = consolidate(rows).variants

        evidence = historical_evidence(
            records, decision_timestamp=BASE_TS + 900_000, decision_state="REJECT"
        )
        self.assertEqual(evidence["stats"]["clean"], 0)
        self.assertEqual(evidence["excluded_non_trading_tier_decision"], 40)

    def test_a_ledger_without_the_tier_field_is_not_silently_dropped(self) -> None:
        """Rows written before the tier existed stay eligible; only False is refused."""

        rows: list[dict] = []
        for index in range(260):
            rows += abstain_lifecycle(
                f"V-L{index}",
                f"OPP-L{index}",
                observed_at=BASE_TS + index * 60,
                resolved_at=BASE_TS + index * 60 + 3_600,
                terminal="TP2_BEFORE_SL" if index % 2 else "SL_BEFORE_TP1",
            )
        records = consolidate(rows).variants
        self.assertTrue(all(row.trading_tier is None for row in records))

        evidence = historical_evidence(
            records, decision_timestamp=BASE_TS + 900_000, decision_state="ABSTAIN"
        )
        self.assertEqual(evidence["state"], "SUFFICIENT_SAMPLE")
        self.assertEqual(evidence["excluded_non_trading_tier_decision"], 0)

    def test_not_assessed_is_not_one_of_the_three_ai_decision_states(self) -> None:
        self.assertNotIn("NOT_ASSESSED", ledger.DECISION_STATES)


class ShadowEvidencePolicyTests(unittest.TestCase):
    """Requirement 8: safe to show the model, never authoritative by itself."""

    def test_the_configuration_switch_can_disable_evidence_entirely(self) -> None:
        records = consolidate(
            abstain_lifecycle("V-X", "OPP-X", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        ).variants
        evidence = historical_evidence(
            records,
            decision_timestamp=BASE_TS + 50_000,
            policy=EvidencePolicy(enabled=False),
        )
        self.assertEqual(evidence["state"], "DISABLED")
        self.assertFalse(evidence["trading_authority"])
        self.assertNotIn("stats", evidence)

    def test_a_small_sample_is_reported_as_insufficient_with_no_narrative(self) -> None:
        records = consolidate(
            abstain_lifecycle("V-Y", "OPP-Y", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        ).variants
        evidence = historical_evidence(
            records, decision_timestamp=BASE_TS + 50_000, decision_state="ABSTAIN"
        )
        self.assertEqual(evidence["state"], "INSUFFICIENT_SAMPLE")
        self.assertEqual(evidence["narrative"], "")
        self.assertFalse(evidence["promotion_gate"]["satisfied"])
        self.assertFalse(evidence["promotion_gate"]["checks"]["min_clean_samples"])

    def test_the_promotion_gate_needs_the_operator_switch_and_out_of_sample(self) -> None:
        rows: list[dict] = []
        for index in range(260):
            rows += abstain_lifecycle(
                f"V-P{index}",
                f"OPP-P{index}",
                observed_at=BASE_TS + index * 60,
                resolved_at=BASE_TS + index * 60 + 3_600,
                terminal="TP2_BEFORE_SL" if index % 2 else "SL_BEFORE_TP1",
            )
        records = consolidate(rows).variants
        decision_at = BASE_TS + 900_000

        default = historical_evidence(
            records, decision_timestamp=decision_at, decision_state="ABSTAIN"
        )
        self.assertEqual(default["state"], "SUFFICIENT_SAMPLE")
        self.assertEqual(default["authority"], "DIAGNOSTIC_SHADOW_ONLY")
        self.assertFalse(default["trading_authority"])
        checks = default["promotion_gate"]["checks"]
        self.assertTrue(checks["min_clean_samples"])
        self.assertTrue(checks["min_unique_sweeps"])
        self.assertFalse(checks["out_of_sample_validated"])
        self.assertFalse(checks["operator_switch_enabled"])

        promoted = historical_evidence(
            records,
            decision_timestamp=decision_at,
            decision_state="ABSTAIN",
            policy=EvidencePolicy(trading_authority_enabled=True, out_of_sample_validated=True),
        )
        self.assertTrue(promoted["promotion_gate"]["satisfied"])
        self.assertEqual(promoted["authority"], "CALIBRATED_ADVISORY")

    def test_the_policy_reads_its_switches_from_the_environment(self) -> None:
        policy = EvidencePolicy.from_env(
            {
                "AI_SHADOW_EVIDENCE_ENABLE": "false",
                "AI_SHADOW_EVIDENCE_MIN_SAMPLES": "77",
                "AI_SHADOW_EVIDENCE_TRADING_AUTHORITY": "true",
            }
        )
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.min_clean_samples, 77)
        self.assertTrue(policy.trading_authority_enabled)
        self.assertFalse(policy.out_of_sample_validated)

    def test_the_compacted_evidence_is_all_scalars_so_every_number_is_citable(self) -> None:
        records = consolidate(
            abstain_lifecycle("V-Z", "OPP-Z", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        ).variants
        compact = compact_candidate_evidence(
            historical_evidence(records, decision_timestamp=BASE_TS + 50_000)
        )
        self.assertTrue(compact)
        for name, value in compact.items():
            self.assertNotIsInstance(value, (dict, list, tuple), name)
        self.assertFalse(compact["trading_authority"])
        self.assertEqual(compact["authority"], "DIAGNOSTIC_SHADOW_ONLY")


class ShadowLedgerReaderTests(unittest.TestCase):
    """The reader must not turn a written ledger into an empty one."""

    def test_the_utf16_ledger_mql_actually_writes_is_read_not_discarded(self) -> None:
        rows = abstain_lifecycle("V-U", "OPP-U", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        for encoding in ("utf-16", "utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                path = write_ledger(rows, encoding=encoding)
                try:
                    read = read_shadow_events(path)
                    self.assertEqual(len(read.rows), len(rows))
                    self.assertEqual(read.unparseable, [])
                finally:
                    path.unlink(missing_ok=True)

    def test_v3_rows_are_counted_as_legacy_and_kept_out_of_the_statistics(self) -> None:
        rows = abstain_lifecycle("V-L", "OPP-L", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        rows.insert(
            0,
            {
                "schema_version": "20260907_shadow_candidate_v3",
                "event_type": "candidate_observed",
                "record_hash": "OLD",
                "symbol": "EURUSD",
            },
        )
        path = write_ledger(rows)
        try:
            read = read_shadow_events(path)
            self.assertEqual(read.legacy_rows, 1)
            self.assertEqual(len(read.rows), len(rows) - 1)
            consolidation = consolidate_shadow_lifecycle(read.rows)
            self.assertEqual(len(consolidation.variants), 1)
            self.assertEqual(consolidation.rejected, [])
        finally:
            path.unlink(missing_ok=True)

    def test_a_corrupt_line_is_quarantined_and_never_silently_dropped(self) -> None:
        path = write_ledger(abstain_lifecycle("V-C", "OPP-C", observed_at=BASE_TS, terminal="SL_BEFORE_TP1"))
        try:
            with path.open("a", encoding="utf-16", newline="\n") as handle:
                handle.write('{"event_type": "shadow_candidate_observed", ')
            read = read_shadow_events(path)
            self.assertEqual(len(read.unparseable), 1)
            report = audit_ledger(read, consolidate_shadow_lifecycle(read.rows))
            self.assertEqual(len(report["findings"]["unparseable_lines"]), 1)
            self.assertFalse(report["healthy"])
        finally:
            path.unlink(missing_ok=True)

    def test_the_audit_clock_is_the_ledger_not_the_wall(self) -> None:
        """A tester ledger's timestamps are simulated and usually in the past.

        Auditing one against wall-clock time reports every open tracker as
        expired, which is a false positive on exactly the finding that matters.
        """

        rows = [
            opportunity_event("OPP-T", at=BASE_TS),
            candidate_event("V-T", "OPP-T", at=BASE_TS, horizon_at=BASE_TS + 86_400),
            decision_event("V-T", "OPP-T", at=BASE_TS + 30),
        ]
        path = write_ledger(rows)
        try:
            read = read_shadow_events(path)
            consolidation = consolidate_shadow_lifecycle(read.rows)

            default = audit_ledger(read, consolidation)
            self.assertEqual(default["time_base"], "latest_ledger_event")
            self.assertEqual(default["evaluated_at"], BASE_TS + 30)
            self.assertEqual(default["findings"]["expired_pending_records"], [])
            self.assertTrue(default["healthy"])

            # A tracker still emitting long after its own horizon IS the defect,
            # and the ledger clock still catches it.
            rows.append(
                _envelope(
                    EVENT_PATH_PROGRESS,
                    variant="V-T",
                    opportunity="OPP-T",
                    at=BASE_TS + 200_000,
                    milestone="r025",
                )
            )
            late_path = write_ledger(rows)
            try:
                late_read = read_shadow_events(late_path)
                late = audit_ledger(late_read, consolidate_shadow_lifecycle(late_read.rows))
                self.assertEqual(late["evaluated_at"], BASE_TS + 200_000)
                self.assertEqual(len(late["findings"]["expired_pending_records"]), 1)
            finally:
                late_path.unlink(missing_ok=True)

            explicit = audit_ledger(read, consolidation, now=BASE_TS + 500_000)
            self.assertEqual(explicit["time_base"], "explicit")
            self.assertEqual(len(explicit["findings"]["expired_pending_records"]), 1)
        finally:
            path.unlink(missing_ok=True)

    def test_a_foreign_schema_version_is_rejected_not_mixed_into_the_study(self) -> None:
        rows = abstain_lifecycle("V-F", "OPP-F", observed_at=BASE_TS, terminal="TP2_BEFORE_SL")
        rows[1] = dict(rows[1], schema_version="20270101_shadow_lifecycle_v9")
        consolidation = consolidate(rows)
        self.assertEqual(consolidation.variants, [])
        self.assertIn(
            "shadow_schema_version_mismatch",
            {row["reason"] for row in consolidation.rejected},
        )


class ShadowPendingReconciliationTests(unittest.TestCase):
    """Requirement 9 / deliverable 4: migrate the trackers already on disk."""

    def _scope(self, root: Path, name: str, plans: list[dict]) -> Path:
        scope = root / name
        scope.mkdir(parents=True)
        (scope / ledger.PENDING_FILENAME).write_text(
            "".join(json.dumps(plan) + "\n" for plan in plans), encoding="utf-8"
        )
        return scope

    @staticmethod
    def _v4_plan(**overrides) -> dict:
        plan = {
            "symbol": "EURUSD",
            "entry_est": 1.1000,
            "sl": 1.0950,
            "tp1": 1.1025,
            "tp2": 1.1150,
            "shadow_candidate_schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
            "shadow_candidate_variant_id": "V-1",
            "shadow_candidate_record_hash": "REC-1",
            "shadow_sweep_opportunity_id": "OPP-1",
            "shadow_observed_at": BASE_TS,
            "shadow_horizon_at": BASE_TS + 86_400,
        }
        plan.update(overrides)
        return plan

    def test_a_legacy_scope_is_reported_without_touching_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = self._v4_plan(shadow_candidate_schema_version="20260717_shadow_candidate_v3")
            legacy.pop("shadow_candidate_variant_id")
            scope = self._scope(root, "live_account_1_magic_5303001", [legacy, legacy])
            before = (scope / ledger.PENDING_FILENAME).read_bytes()

            report = ledger.reconcile_pending_trackers(
                runtime_state_root=root, now=BASE_TS + 200_000
            )
            self.assertEqual(report["totals"]["LEGACY_SCHEMA"], 2)
            self.assertEqual(report["totals"].get("quarantined_written", 0), 0)
            self.assertFalse(report["quarantine_applied"])
            self.assertEqual(report["terminal_outcomes_written"], 0)
            self.assertEqual(report["pending_files_deleted"], 0)
            self.assertEqual((scope / ledger.PENDING_FILENAME).read_bytes(), before)
            self.assertFalse((scope / ledger.QUARANTINE_FILENAME).exists())

    def test_quarantine_retains_every_record_and_deletes_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = self._v4_plan(shadow_candidate_schema_version="20260717_shadow_candidate_v3")
            scope = self._scope(root, "live_account_1_magic_5303001", [legacy, legacy, legacy])
            before = (scope / ledger.PENDING_FILENAME).read_bytes()

            report = ledger.reconcile_pending_trackers(
                runtime_state_root=root, now=BASE_TS + 200_000, quarantine=True
            )
            self.assertEqual(report["totals"]["quarantined_written"], 3)
            self.assertEqual((scope / ledger.PENDING_FILENAME).read_bytes(), before)

            lines = [
                json.loads(line)
                for line in (scope / ledger.QUARANTINE_FILENAME).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 3)
            for line in lines:
                self.assertEqual(line["event_type"], "shadow_tracker_quarantined")
                self.assertEqual(line["reason"], "incompatible_shadow_schema_version")
                self.assertEqual(line["quarantine_source"], "python_reconcile_orphaned_scope")
                self.assertFalse(line["trading_authority"])
                # The plan is retained verbatim so a later build can rebuild it.
                self.assertEqual(line["plan"]["symbol"], "EURUSD")
                self.assertNotIn("terminal_event", line)

    def test_quarantine_appends_rather_than_replacing_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = self._v4_plan(shadow_candidate_schema_version="20260717_shadow_candidate_v3")
            scope = self._scope(root, "live_account_1_magic_5303001", [legacy])
            (scope / ledger.QUARANTINE_FILENAME).write_text(
                json.dumps({"event_type": "shadow_tracker_quarantined", "reason": "earlier"}) + "\n",
                encoding="utf-8",
            )
            ledger.reconcile_pending_trackers(
                runtime_state_root=root, now=BASE_TS + 200_000, quarantine=True
            )
            lines = [
                line
                for line in (scope / ledger.QUARANTINE_FILENAME).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 2)
            self.assertIn("earlier", lines[0])

    def test_an_addressable_tracker_is_never_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = self._scope(
                root,
                "live_account_1_magic_5303191",
                [
                    self._v4_plan(
                        shadow_candidate_variant_id="V-A", shadow_horizon_at=BASE_TS + 900_000
                    ),
                    self._v4_plan(
                        shadow_candidate_variant_id="V-B", shadow_horizon_at=BASE_TS + 10
                    ),
                ],
            )
            report = ledger.reconcile_pending_trackers(
                runtime_state_root=root, now=BASE_TS + 200_000, quarantine=True
            )
            self.assertEqual(report["totals"]["ADDRESSABLE_PENDING"], 1)
            self.assertEqual(report["totals"]["ADDRESSABLE_OVERDUE"], 1)
            self.assertEqual(report["totals"].get("quarantined_written", 0), 0)
            self.assertFalse((scope / ledger.QUARANTINE_FILENAME).exists())

    def test_an_unusable_price_contract_is_reported_as_untrackable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scope(
                root,
                "live_account_1_magic_5303191",
                [self._v4_plan(entry_est=0.0, sl=0.0, tp2=0.0)],
            )
            report = ledger.reconcile_pending_trackers(
                runtime_state_root=root, now=BASE_TS + 200_000
            )
            self.assertEqual(report["totals"]["UNTRACKABLE"], 1)

    def test_a_missing_runtime_state_root_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = ledger.reconcile_pending_trackers(
                runtime_state_root=Path(tmp) / "does_not_exist"
            )
            self.assertEqual(report["state"], "RUNTIME_STATE_ROOT_MISSING")
            self.assertEqual(report["scopes"], [])


# --------------------------------------------------------------------------
# The MQL5 tracker itself
# --------------------------------------------------------------------------

TRADE_ENGINE = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="replace")
STATE_STORE = (MQL_STAGE / "StateStore.mqh").read_text(encoding="utf-8", errors="replace")
CONFIG = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="replace")
TYPES = (MQL_STAGE / "Types.mqh").read_text(encoding="utf-8", errors="replace")
BRIDGE = (MQL_STAGE / "AIGateBridge.mqh").read_text(encoding="utf-8", errors="replace")
FILE_BUS = (MQL_STAGE / "FileBus.mqh").read_text(encoding="utf-8", errors="replace")


def assert_shadow_tracker_source(
    case: unittest.TestCase,
    *,
    trade_engine: str,
    state_store: str,
    config: str,
    types: str,
    bridge: str = "",
    file_bus: str = "",
) -> None:
    """Every MQL-side property, asserted against one set of sources.

    Factored out so the same assertions can be pointed at the pre-v4 backup and
    shown to fail -- a governance test that cannot fail proves nothing.
    """

    # Property 2: no level is measured before the entry is activated.  The
    # activation branch must be the first statement of the price step, and it
    # must return without measuring when the entry was not touched.
    step = _function_body(trade_engine, "_ShadowStepPrice")
    case.assertRegex(
        step,
        r"^\s*\{\s*if\(!p\.shadow_entry_activated\)\s*\{",
        "_ShadowStepPrice must gate on entry activation before anything else",
    )
    case.assertIn("if(!entry_touch) return false;", step)
    case.assertIn("_AppendShadowEntryActivated", step)
    activation_end = step.index("_AppendShadowEntryActivated")
    for measurement in ("shadow_mfe_r", "shadow_mae_r", "sl_hit", "tp2_hit"):
        first = step.find(measurement)
        case.assertGreater(
            first,
            activation_end,
            f"{measurement} is measured before entry activation is established",
        )

    # Properties 3-5: the ordered TP1/TP2/SL lifecycle exists as distinct
    # terminal events, and TP2 implies TP1 rather than losing the first leg.
    for terminal in ("TP1_THEN_TP2", "TP1_THEN_SL", "SL_BEFORE_TP1", "TP2_BEFORE_SL"):
        case.assertIn(terminal, step, f"missing ordered terminal event: {terminal}")
    case.assertIn("if(tp2_hit && tp1_valid) tp1_hit = true;", step)

    # Property 6: a contested bar without tick ordering is ambiguous, not scored.
    case.assertIn("AMBIGUOUS_TP1_AND_SL_SAME_BAR", step)
    case.assertIn("AMBIGUOUS_TP2_AND_SL_SAME_BAR", step)
    case.assertIn("tp1_and_sl_same_m1_bar_without_tick_sequence", step)
    case.assertRegex(step, r"if\(sl_hit && !intrabar_ordered\)")

    # Property 7: ticks are attempted first on exactly the contested bars, and
    # the M1 fallback is used only when the tick replay is unavailable.
    evaluate = _function_body(trade_engine, "_EvaluateShadowCandidate")
    case.assertIn("_ShadowBarIsContested(", evaluate)
    case.assertIn("_ShadowStepBarWithTicks(", evaluate)
    ticks = _function_body(trade_engine, "_ShadowStepBarWithTicks")
    case.assertIn("CopyTicksRange", ticks)
    case.assertIn("COPY_TICKS_ALL", ticks)
    case.assertIn("InpShadowUseTickOrdering", ticks)

    # Property 8 and the R contract: excluded terminals carry no R at all.
    finalize = _function_body(trade_engine, "_ShadowFinalize")
    case.assertIn('terminal_event == "TP1_THEN_SL"', finalize)
    case.assertIn("frac * rr1 + (1.0 - frac) * (-1.0)", finalize)
    case.assertIn("frac * rr1 + (1.0 - frac) * rr2", finalize)
    case.assertIn("InpTP1PartialPct", finalize)
    # ``_ShadowTerminalCarriesResult`` is a whitelist: a terminal that is not in
    # it is written as JSON null, not as a 0.0 that every aggregate would read as
    # a break-even trade.
    carries = _function_body(trade_engine, "_ShadowTerminalCarriesResult")
    for bearing in (
        "TP2_BEFORE_SL",
        "TP1_THEN_TP2",
        "TP1_THEN_SL",
        "SL_BEFORE_TP1",
        "HORIZON_CENSORED",
        "SESSION_CLOSE_CENSORED",
    ):
        case.assertIn(bearing, carries, f"{bearing} must carry a result R")
    for excluded in ("ENTRY_NEVER_REACHED", "DATA_LOSS", "UNTRACKABLE", "AMBIGUOUS"):
        case.assertNotIn(
            excluded, carries, f"{excluded} must never be reported as a numeric R"
        )
    resolution = _function_body(trade_engine, "_AppendShadowOutcomeResolution")
    case.assertIn("_ShadowTerminalCarriesResult(", resolution)
    case.assertIn("null", resolution)

    # Property 9: both censoring terminals exist and are labelled as censored.
    case.assertIn("HORIZON_CENSORED", finalize)
    case.assertIn("SESSION_CLOSE_CENSORED", finalize)

    # Property 10: CopyRates failure retries under a bounded budget and then
    # emits exactly one explicit DATA_LOSS terminal instead of staying pending.
    case.assertIn("int got = CopyRates(", evaluate)
    case.assertRegex(evaluate, r"if\(got <= 0\)\s*\{")
    case.assertIn("p.shadow_data_retry_count++", evaluate)
    case.assertIn("InpShadowMaxDataRetries", evaluate)
    case.assertIn('_ShadowFinalize(p, "DATA_LOSS"', evaluate)
    case.assertIn('reason = "m1_history_not_synchronized_awaiting_download"', evaluate)

    # Property 10b: the M1 series is REQUESTED before its absence is called a
    # data loss.  The engine scans H4/M15, so nothing else asks the terminal for
    # M1 on the other symbols; without this the retry budget was spent waiting
    # for history that had never been ordered.  Measured in the 2026-09-08
    # ledger: 103 of 106 non-BITCOIN terminal resolutions were DATA_LOSS, and
    # BITCOIN -- the one symbol whose M1 the terminal already held -- had none.
    case.assertIn("_ShadowEnsureM1History(", evaluate)
    ensure = _function_body(trade_engine, "_ShadowEnsureM1History")
    case.assertIn("CopyRates(symbol, PERIOD_M1, 0, 2, probe)", ensure)
    case.assertIn("_ShadowM1SeriesAvailable(symbol)", ensure)
    case.assertIn("m_shadow_history_requests_total++", ensure)
    request_at = evaluate.index("_ShadowEnsureM1History(")
    loss_at = evaluate.index('_ShadowFinalize(p, "DATA_LOSS"')
    case.assertLess(
        request_at, loss_at, "history must be requested before DATA_LOSS is declared"
    )
    case.assertIn("m1_series_not_synchronized_after_retries", evaluate)

    # Property 11: init restores the trackers and resolves the overdue ones.
    init = _function_body(trade_engine, "Init")
    case.assertIn("_RestoreShadowPendingTrackers();", init)
    case.assertIn("_ResolveOverdueShadowTrackersOnInit();", init)
    overdue = _function_body(trade_engine, "_ResolveOverdueShadowTrackersOnInit")
    case.assertIn("shadow_horizon_at > now", overdue)
    case.assertIn("_EvaluateShadowCandidate(", overdue)
    case.assertIn("_CommitShadowTerminalResolution(", overdue)
    case.assertIn("m_state.SavePlans(m_state.ShadowPendingPath()", overdue)

    # Property 12: the terminal-once guarantee is a lookup, not a hope.
    commit = _function_body(trade_engine, "_CommitShadowTerminalResolution")
    case.assertIn("_ShadowIndexContains(m_shadow_resolved_variants", commit)
    case.assertIn("_AppendShadowOutcomeResolution(", commit)
    guard = commit.index("_ShadowIndexContains(m_shadow_resolved_variants")
    append = commit.index("_AppendShadowOutcomeResolution(")
    case.assertLess(guard, append, "the resolved-variant guard must precede the append")
    case.assertIn("m_shadow_terminal_duplicate_suppressed_total++", commit)
    case.assertIn("_ShadowIndexAdd(m_shadow_resolved_variants", commit)

    # Properties 13/14: identity is derived from the sweep and the exact plan,
    # never from the observation time -- an observed_at ingredient is what made
    # every re-scan look like a new sample before v4.
    opportunity_id = _function_body(trade_engine, "_ShadowOpportunityId")
    variant_id = _function_body(trade_engine, "_ShadowVariantId")
    for body, name in ((opportunity_id, "_ShadowOpportunityId"), (variant_id, "_ShadowVariantId")):
        case.assertNotIn(
            "shadow_observed_at", body, f"{name} must not mix the observation time into identity"
        )
        case.assertNotIn("_NowServerOrLocal", body, f"{name} must not mix wall-clock into identity")
    for ingredient in ("_ShadowSweepTime", "_ShadowDisplacementTime", "_ShadowBosTime", "_ShadowSweepSide"):
        case.assertIn(ingredient, opportunity_id)
    for ingredient in ("p.entry_est", "p.sl", "p.tp1", "p.tp2", "p.request_execution_fingerprint"):
        case.assertIn(ingredient, variant_id)
    record_hash = _function_body(trade_engine, "_ShadowRecordHashForVariant")
    case.assertNotIn("shadow_observed_at", record_hash)
    case.assertIn("SHADOW_CANDIDATE_SCHEMA_VERSION", record_hash)

    # Property 5 of requirement 5: the assessed plan is never rewritten.
    update = _function_body(trade_engine, "_WriteShadowDecisionUpdate")
    tracker_writes = re.findall(r"m_shadow_pending\[i\]\.(\w+)\s*=", update)
    for field in tracker_writes:
        case.assertFalse(
            field in {"entry_est", "sl", "tp1", "tp2", "is_buy", "candidate_hash"},
            f"_WriteShadowDecisionUpdate rewrote the assessed plan field {field}",
        )
    case.assertTrue(tracker_writes, "the decision must still attach to its tracker")

    # Requirement 7: an infrastructure envelope is not an AI decision.
    #
    # An error envelope is schema-required to carry APPROVE/REJECT/ABSTAIN
    # (AIGateBridge.mqh), so a local pipeline failure arrives as
    # decision_state="REJECT" with DEGRADED_NON_TRADING and zero candidate
    # assessments.  Copying that onto every candidate wrote infrastructure
    # failures into the ledger as AI rejections -- 89 of 89 decision events in
    # the 2026-09-08 ledger.  The request-level verdict must be kept in its own
    # field and the per-candidate attribution must say NOT_ASSESSED.
    case.assertIn("NOT_ASSESSED", update)
    case.assertIn("decision_quality_tier", update)
    case.assertIn("CACHE_OF_FULL_STRUCTURED", update)
    case.assertIn("selected_candidate_hash", update)
    case.assertIn('JsonKVStr("request_decision_state", dec.decision_state)', update)
    case.assertIn('JsonKVStr("decision_state_authority"', update)
    case.assertIn('JsonKVBool("trading_tier"', update)
    case.assertIn('JsonKVBool("candidate_selected_by_python"', update)
    case.assertNotRegex(
        update,
        r"if\(!assessment_found && StringLen\(dec\.decision_state\) > 0 && decision_state == \"UNAVAILABLE\"\)\s*\n\s*decision_state = dec\.decision_state;",
        "the request-level verdict must not be copied onto an unassessed candidate",
    )

    # The terminal resolution has to carry the same distinction, or the join is
    # complete at the decision event and lost again at the outcome.
    resolution = _function_body(trade_engine, "_AppendShadowOutcomeResolution")
    case.assertIn('JsonKVStr("request_decision_state", p.ai.decision_state)', resolution)
    case.assertIn('JsonKVStr("decision_quality_tier", p.ai.decision_quality_tier)', resolution)
    case.assertIn('JsonKVBool("trading_tier"', resolution)

    # Requirement 1/10: every observed variant reaches exactly one terminal.  An
    # untrackable candidate has a known permanent outcome, so leaving it with an
    # observation and no resolution made "one terminal per observation" an
    # invariant nothing could check -- 243 such rows in the 2026-09-08 ledger.
    observe = _function_body(trade_engine, "_WriteShadowCandidateRecord")
    case.assertIn("m_shadow_untrackable_total++", observe)
    untrackable_at = observe.index("m_shadow_untrackable_total++")
    case.assertIn('_ShadowFinalize(p, "UNTRACKABLE"', observe[untrackable_at:])
    case.assertIn("_CommitShadowTerminalResolution(p)", observe[untrackable_at:])

    # The observe-once / terminal-once index has to survive a scan that produced
    # no pending tracker at all, or a restart re-observes those identities.
    maintain = _function_body(trade_engine, "_MaintainShadowCandidateOutcomes")
    # ``[^}]*`` cannot cross the closing brace, so the persist must be inside the
    # empty-queue branch AND before its return -- not merely somewhere after it.
    case.assertRegex(
        maintain,
        r"if\(ArraySize\(m_shadow_pending\) <= 0\)\s*\{[^}]*_PersistShadowTrackerIndex\(\);[^}]*return;",
        "the identity index must be persisted on the empty-queue path, before it returns",
    )

    # Requirement 1: the rejected-candidate writer receives the BUILT plan.  The
    # pre-v4 call sites passed the untouched ``base``, which is why 714 of 787
    # observations were UNTRACKABLE_INVALID_CONTRACT by construction.
    reject_calls = re.findall(r"_WriteRejectedShadowCandidate\(\s*(\w+)\s*,", trade_engine)
    case.assertTrue(reject_calls, "no _WriteRejectedShadowCandidate call sites found")
    for argument in reject_calls:
        case.assertNotEqual(
            argument, "base", "_WriteRejectedShadowCandidate must receive the built plan, not base"
        )

    # Requirement 6: the eight explicit event types exist and the writer only
    # appends.
    for event in (
        "shadow_opportunity_observed",
        "shadow_candidate_observed",
        "shadow_decision_recorded",
        "shadow_entry_activated",
        "shadow_path_progress",
        "shadow_tp1_reached",
        "shadow_terminal_resolution",
        "shadow_data_quality_failure",
    ):
        case.assertIn(f'"{event}"', trade_engine, f"missing shadow event type: {event}")
    writer = _function_body(trade_engine, "_AppendShadowEvent")
    case.assertIn("m_bus.AppendText(", writer)
    case.assertIn("shadow_candidates.jsonl", writer)
    for rewriting in ("m_bus.WriteText(", "FileDelete", "FileWrite("):
        case.assertNotIn(
            rewriting, writer, "the shadow stream must be append-only; no line is ever rewritten"
        )
    if file_bus:
        bus_append = _function_body(file_bus, "AppendText")
        case.assertIn("FileSeek(", bus_append)
        case.assertIn("SEEK_END", bus_append)
    envelope = _function_body(trade_engine, "_ShadowEventEnvelope")
    for field in (
        "schema_version",
        "event_id",
        "event_at",
        "sweep_opportunity_id",
        "candidate_variant_id",
        "parent_record_hash",
        "candidate_hash",
        "execution_fingerprint",
        "decision_state",
        "decision_source",
        "tracking_status",
    ):
        case.assertIn(f'"{field}"', envelope, f"every shadow event must carry {field}")

    # Requirement 9: an unresolved candidate is never deleted.  A tracker this
    # build cannot address is written to the quarantine file with its reason and
    # its plan, not merely counted and dropped.
    restore = _function_body(trade_engine, "_RestoreShadowPendingTrackers")
    case.assertIn("m_state.ShadowQuarantinePath()", restore)
    case.assertIn("_AppendShadowQuarantineLine(", restore)
    case.assertIn("m_state.SaveTextLines(m_state.ShadowQuarantinePath()", restore)
    quarantine_writer = _function_body(trade_engine, "_AppendShadowQuarantineLine")
    case.assertIn("m_state.TradePlanToJson(p)", quarantine_writer)
    case.assertIn('JsonKVStr("reason", reason)', quarantine_writer)
    reasons = _function_body(trade_engine, "_ShadowRestoreQuarantineReason")
    for reason in (
        "incompatible_shadow_schema_version",
        "missing_candidate_variant_identity",
        "missing_parent_record_hash",
        "untrackable_entry_stop_target_contract",
    ):
        case.assertIn(reason, reasons)

    # Requirement 9: pending trackers persist atomically and the index with them.
    case.assertIn("ShadowTrackerIndexPath", state_store)
    case.assertIn("ShadowQuarantinePath", state_store)
    case.assertIn("LoadTextLines", state_store)
    case.assertIn("SaveTextLines", state_store)
    save_lines = _function_body(state_store, "SaveTextLines")
    case.assertIn("m_bus.WriteText", save_lines)

    # Every v4 lifecycle field must round-trip through the state store, or a
    # restarted EA silently reverts to a pre-activation tracker.
    for field in (
        "shadow_sweep_opportunity_id",
        "shadow_candidate_variant_id",
        "shadow_entry_activated",
        "shadow_entry_activated_at",
        "shadow_tp1_hit",
        "shadow_tp1_before_sl",
        "shadow_tp2_before_sl",
        "shadow_tp1_then_sl",
        "shadow_tp1_then_tp2",
        "shadow_result_r_unmanaged",
        "shadow_result_r_tp1_partial",
        "shadow_terminal_event",
        "shadow_scan_cursor",
        "shadow_data_retry_count",
        "shadow_assessed_entry",
        "shadow_assessed_sl",
        "shadow_assessed_tp1",
        "shadow_assessed_tp2",
    ):
        case.assertIn(field, types, f"TradePlan is missing the v4 field {field}")
        case.assertGreaterEqual(
            state_store.count(field), 2, f"{field} must be both serialized and parsed"
        )

    # The schema version participates in provenance only.  Bumping it must not
    # move the decision identity, or the recorded replay cohort is lost (4y/4z).
    case.assertIn('SHADOW_CANDIDATE_SCHEMA_VERSION = "20260908_shadow_lifecycle_v4"', config)
    if bridge:
        decision_hash = _function_body(bridge, "_ComputeDecisionInputHash")
        case.assertNotIn(
            "SHADOW_CANDIDATE_SCHEMA_VERSION",
            decision_hash,
            "the shadow schema must stay out of the decision identity",
        )
        # Provenance is where it belongs: a schema bump must still be visible.
        runtime_hash = _function_body(bridge, "_ComputeRuntimeInputHash")
        case.assertIn("SHADOW_CANDIDATE_SCHEMA_VERSION", runtime_hash)


class ShadowTrackerMqlSourceTests(unittest.TestCase):
    """The tracker properties that only exist in MQL5."""

    def test_the_mql_tracker_satisfies_every_lifecycle_property(self) -> None:
        assert_shadow_tracker_source(
            self,
            trade_engine=TRADE_ENGINE,
            state_store=STATE_STORE,
            config=CONFIG,
            types=TYPES,
            bridge=BRIDGE,
            file_bus=FILE_BUS,
        )

    def test_the_maintenance_loop_is_bounded_and_removes_resolved_trackers(self) -> None:
        body = _function_body(TRADE_ENGINE, "_MaintainShadowCandidateOutcomes")
        self.assertIn("InpShadowMaxEvaluationsPerTick", body)
        self.assertIn("_ShadowTrackerDue(", body)
        self.assertIn("_CommitShadowTerminalResolution(", body)
        self.assertRegex(body, r"for\(int i=ArraySize\(m_shadow_pending\)-1; i>=0; i--\)")
        self.assertIn("ArrayResize(m_shadow_pending, last);", body)

    def test_the_runtime_counters_required_for_diagnosis_all_exist(self) -> None:
        for counter in (
            "m_shadow_opportunities_total",
            "m_shadow_observed_total",
            "m_shadow_observation_dedup_total",
            "m_shadow_restored_total",
            "m_shadow_restored_overdue_resolved_total",
            "m_shadow_resolved_total",
            "m_shadow_censored_total",
            "m_shadow_ambiguous_total",
            "m_shadow_data_loss_total",
            "m_shadow_entry_activated_total",
            "m_shadow_entry_never_reached_total",
            "m_shadow_terminal_duplicate_suppressed_total",
            "m_shadow_quarantined_total",
            "m_shadow_tick_ordered_total",
        ):
            self.assertIn(counter, TRADE_ENGINE, f"missing shadow counter {counter}")
        summary = _function_body(TRADE_ENGINE, "_LogShadowTrackerSummary")
        self.assertIn("[shadow_tracker]", summary)
        self.assertIn("[shadow_tracker_warning]", summary)
        self.assertIn("_ShadowExpiredPendingCount()", summary)
        self.assertIn("expired_pending", summary)

    def test_python_and_mql_agree_on_the_shadow_schema_version(self) -> None:
        import runtime_governance

        match = re.search(
            r'SHADOW_CANDIDATE_SCHEMA_VERSION\s*=\s*"([^"]+)"', CONFIG
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), runtime_governance.SHADOW_CANDIDATE_SCHEMA_VERSION)
        self.assertEqual(match.group(1), SHADOW_LEDGER_SCHEMA_VERSION)


# Each entry reverts exactly one v4 property in the current source.  A
# governance assertion that survives its own mutation is asserting nothing.
MQL_MUTATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "entry_activation_gate_removed",
        "if(!p.shadow_entry_activated){\n         bool entry_touch",
        "if(false){\n         bool entry_touch",
    ),
    (
        "tp2_no_longer_implies_tp1",
        "if(tp2_hit && tp1_valid) tp1_hit = true;",
        "// tp2 no longer implies tp1",
    ),
    (
        "same_bar_ambiguity_removed",
        '_ShadowFinalize(p, "AMBIGUOUS_TP1_AND_SL_SAME_BAR"',
        '_ShadowFinalize(p, "SL_BEFORE_TP1"',
    ),
    (
        "identity_mixes_the_observation_time_again",
        'string material = "shadow_variant|v4|";',
        'string material = "shadow_variant|v4|";\n      material += IntegerToString((int)p.shadow_observed_at) + "|";',
    ),
    (
        "terminal_once_guard_removed",
        "if(_ShadowIndexContains(m_shadow_resolved_variants, variant_id)){",
        "if(false){",
    ),
    (
        "decision_update_rewrites_the_assessed_plan",
        "m_shadow_pending[i].shadow_decision_stage = decision_stage;",
        "m_shadow_pending[i].entry_est = source.entry_est;\n         m_shadow_pending[i].shadow_decision_stage = decision_stage;",
    ),
    (
        "data_loss_terminal_removed",
        '_ShadowFinalize(p, "DATA_LOSS"',
        '_ShadowFinalizeNothing(p, "DATA_LOSS"',
    ),
    (
        "overdue_resolution_not_called_at_init",
        "_ResolveOverdueShadowTrackersOnInit();",
        "// overdue resolution disabled",
    ),
    (
        "tick_ordering_removed",
        "CopyTicksRange",
        "CopyRatesNoTicks",
    ),
    (
        "quarantined_trackers_dropped_instead_of_retained",
        "_AppendShadowQuarantineLine(quarantine_lines, p,",
        "_DiscardQuarantinedTracker(quarantine_lines, p,",
    ),
    (
        "append_only_stream_replaced_by_a_rewrite",
        'm_bus.AppendText(m_bus.LogDir() + "\\\\shadow_candidates.jsonl"',
        'm_bus.WriteText(m_bus.LogDir() + "\\\\shadow_candidates.jsonl"',
    ),
    (
        # Back to only ASKING whether the series exists, never requesting it.
        "m1_history_never_requested",
        "if(_ShadowEnsureM1History(p.symbol)){",
        "if(_ShadowM1SeriesAvailable(p.symbol)){",
    ),
    (
        "history_request_does_not_call_copyrates",
        "CopyRates(symbol, PERIOD_M1, 0, 2, probe);",
        "// history request removed",
    ),
    (
        # The pre-fix attribution: the request-level verdict copied onto every
        # candidate, including candidates Python never assessed.
        "error_envelope_verdict_copied_onto_the_candidate",
        'decision_state = "NOT_ASSESSED";',
        "decision_state = dec.decision_state;",
    ),
    (
        "request_level_verdict_not_recorded_separately",
        'JsonKVStr("request_decision_state", dec.decision_state)',
        'JsonKVStr("decision_state", dec.decision_state)',
    ),
    (
        "terminal_resolution_drops_the_quality_tier",
        'JsonKVStr("decision_quality_tier", p.ai.decision_quality_tier)',
        'JsonKVStr("unused_tier", p.ai.decision_quality_tier)',
    ),
    (
        "untrackable_observation_left_without_a_terminal",
        '_ShadowFinalize(p, "UNTRACKABLE", observed_at',
        '_ShadowFinalizeNothing(p, "UNTRACKABLE_SKIPPED", observed_at',
    ),
    (
        "identity_index_not_persisted_on_the_empty_queue_path",
        "if(ArraySize(m_shadow_pending) <= 0){",
        "if(ArraySize(m_shadow_pending) <= 0){ return; }\n      if(false){",
    ),
)

# Mutations that live in the other includes rather than in TradeEngine.mqh.
BRIDGE_MUTATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "shadow_schema_leaks_into_the_decision_identity",
        "string _ComputeDecisionInputHash() const {",
        'string _ComputeDecisionInputHash() const {\n      string leak = SHADOW_CANDIDATE_SCHEMA_VERSION;',
    ),
    (
        "shadow_schema_dropped_from_provenance",
        "SHADOW_CANDIDATE_SCHEMA_VERSION",
        "PROVENANCE_TOKEN_REMOVED",
    ),
)


class ShadowTrackerFalsificationTests(unittest.TestCase):
    """Prove the MQL assertions above can actually fail."""

    BACKUP_CANDIDATES = (
        os.environ.get("PO3_SHADOW_PRE_V4_TRADEENGINE", ""),
        str(Path(__file__).resolve().parents[2] / "scratchpad" / "TradeEngine.mqh.bak_shadow_v3"),
    )

    def _backup(self) -> Path | None:
        for candidate in self.BACKUP_CANDIDATES:
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        scratch = os.environ.get("CLAUDE_SCRATCHPAD", "")
        if scratch and (Path(scratch) / "TradeEngine.mqh.bak_shadow_v3").is_file():
            return Path(scratch) / "TradeEngine.mqh.bak_shadow_v3"
        return None

    def test_every_mql_property_fails_when_its_own_guarantee_is_reverted(self) -> None:
        """One single-property mutation per guarantee, each of which must be caught."""

        for name, original, replacement in MQL_MUTATIONS:
            with self.subTest(mutation=name):
                self.assertIn(original, TRADE_ENGINE, f"mutation anchor missing: {name}")
                mutated = TRADE_ENGINE.replace(original, replacement)
                self.assertNotEqual(mutated, TRADE_ENGINE)
                with self.assertRaises(AssertionError):
                    assert_shadow_tracker_source(
                        self,
                        trade_engine=mutated,
                        state_store=STATE_STORE,
                        config=CONFIG,
                        types=TYPES,
                        bridge=BRIDGE,
                        file_bus=FILE_BUS,
                    )

        for name, original, replacement in BRIDGE_MUTATIONS:
            with self.subTest(mutation=name):
                self.assertIn(original, BRIDGE, f"mutation anchor missing: {name}")
                mutated = BRIDGE.replace(original, replacement)
                self.assertNotEqual(mutated, BRIDGE)
                with self.assertRaises(AssertionError):
                    assert_shadow_tracker_source(
                        self,
                        trade_engine=TRADE_ENGINE,
                        state_store=STATE_STORE,
                        config=CONFIG,
                        types=TYPES,
                        bridge=mutated,
                        file_bus=FILE_BUS,
                    )

    def test_the_state_store_round_trip_assertion_fails_without_persistence(self) -> None:
        """A field that is written but never parsed reverts on restart."""

        self.assertGreaterEqual(STATE_STORE.count("shadow_result_r_unmanaged"), 2)
        stripped = STATE_STORE.replace("shadow_result_r_unmanaged", "dropped_on_restart")
        with self.assertRaises(AssertionError):
            assert_shadow_tracker_source(
                self,
                trade_engine=TRADE_ENGINE,
                state_store=stripped,
                config=CONFIG,
                types=TYPES,
                bridge=BRIDGE,
                file_bus=FILE_BUS,
            )

    def test_the_append_only_assertion_fails_when_the_bus_stops_seeking_to_the_end(self) -> None:
        truncating = FILE_BUS.replace("FileSeek(h, 0, SEEK_END);", "// truncating write", 1)
        with self.assertRaises(AssertionError):
            assert_shadow_tracker_source(
                self,
                trade_engine=TRADE_ENGINE,
                state_store=STATE_STORE,
                config=CONFIG,
                types=TYPES,
                bridge=BRIDGE,
                file_bus=truncating,
            )

    def test_mql_assertions_fail_against_the_pre_v4_tracker(self) -> None:
        backup = self._backup()
        if backup is None:
            self.skipTest(
                "pre-v4 TradeEngine backup not available; set PO3_SHADOW_PRE_V4_TRADEENGINE"
            )
        pre_v4 = backup.read_text(encoding="utf-8", errors="replace")
        with self.assertRaises(AssertionError):
            assert_shadow_tracker_source(
                self,
                trade_engine=pre_v4,
                state_store=STATE_STORE,
                config=CONFIG,
                types=TYPES,
                bridge=BRIDGE,
                file_bus=FILE_BUS,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
