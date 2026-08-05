"""Tests for the repeatability qualification state machine.

All five states must be reachable and distinguishable.  The incident collapsed
four different situations into one opaque ``UNAVAILABLE``, which is why an
operator could not tell a genuine cold start from a corrupt artifact.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repeatability_state import (  # noqa: E402
    ALL_STATES,
    REPEATABILITY_ARTIFACT_SCHEMA,
    STATE_INCOMPATIBLE,
    STATE_QUALIFIED,
    STATE_SHADOW_COLLECTING,
    STATE_STALE,
    STATE_UNQUALIFIED,
    QualificationObservation,
    RepeatabilityBinding,
    RepeatabilityMetrics,
    RepeatabilityThresholds,
    build_artifact,
    build_group_record,
    evaluate_artifact,
    evaluate_group,
    qualify_observations,
)

GROUP = "group-a"


def _binding(**overrides) -> RepeatabilityBinding:
    base = dict(
        provider_id="local_openai_compatible",
        model_snapshot="harness-deterministic-v1",
        decision_schema_version="schema-v10",
        prompt_contract_version="prompt-v12",
        evidence_catalog_version="catalog-v1",
        generation_settings_hash="gen-abc",
        candidate_count=3,
    )
    base.update(overrides)
    return RepeatabilityBinding(**base)


def _metrics(**overrides) -> RepeatabilityMetrics:
    base = dict(
        sample_count=40,
        decision_agreement=1.0,
        chosen_candidate_agreement=1.0,
        veto_agreement=1.0,
        target_choice_agreement=1.0,
        score_stddev=0.1,
        invalid_output_count=0,
        timeout_count=0,
    )
    base.update(overrides)
    return RepeatabilityMetrics(**base)


class StateReachabilityTests(unittest.TestCase):
    """Each of the five states must be produced by a realistic input."""

    def test_unqualified_when_no_group_recorded(self) -> None:
        ev = evaluate_group(None, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_UNQUALIFIED)
        self.assertEqual(ev.reason, "no_group_recorded")
        self.assertFalse(ev.trading_authority)

    def test_unqualified_when_zero_samples(self) -> None:
        group = build_group_record(_binding(), _metrics(sample_count=0))
        ev = evaluate_group(group, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_UNQUALIFIED)
        self.assertEqual(ev.reason, "no_samples_for_current_binding")

    def test_shadow_collecting_when_below_minimum(self) -> None:
        group = build_group_record(_binding(), _metrics(sample_count=7))
        ev = evaluate_group(group, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_SHADOW_COLLECTING)
        self.assertIn("7/30", ev.reason)
        self.assertEqual(ev.samples_remaining, 23)
        self.assertFalse(ev.trading_authority)

    def test_qualified_when_samples_and_thresholds_met(self) -> None:
        group = build_group_record(_binding(), _metrics())
        ev = evaluate_group(group, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_QUALIFIED)
        self.assertTrue(ev.trading_authority)
        self.assertEqual(ev.samples_remaining, 0)
        self.assertEqual(ev.failed_metrics, ())

    def test_stale_when_binding_changed(self) -> None:
        group = build_group_record(_binding(), _metrics())
        ev = evaluate_group(
            group,
            group_key=GROUP,
            current_binding=_binding(model_snapshot="different-model-v2"),
        )
        self.assertEqual(ev.state, STATE_STALE)
        self.assertIn("binding_changed", ev.reason)
        self.assertFalse(ev.trading_authority)

    def test_incompatible_when_binding_missing(self) -> None:
        ev = evaluate_group({"sample_count": 40}, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_INCOMPATIBLE)
        self.assertEqual(ev.reason, "group_binding_missing")
        self.assertFalse(ev.artifact_compatible)

    def test_all_five_states_are_reachable(self) -> None:
        seen = {
            evaluate_group(None, group_key=GROUP, current_binding=_binding()).state,
            evaluate_group(
                build_group_record(_binding(), _metrics(sample_count=3)),
                group_key=GROUP,
                current_binding=_binding(),
            ).state,
            evaluate_group(
                build_group_record(_binding(), _metrics()),
                group_key=GROUP,
                current_binding=_binding(),
            ).state,
            evaluate_group(
                build_group_record(_binding(), _metrics()),
                group_key=GROUP,
                current_binding=_binding(prompt_contract_version="other"),
            ).state,
            evaluate_group({"sample_count": 1}, group_key=GROUP, current_binding=_binding()).state,
        }
        self.assertEqual(seen, set(ALL_STATES))


class ThresholdEnforcementTests(unittest.TestCase):
    def test_each_agreement_metric_blocks_qualification(self) -> None:
        cases = {
            "decision_agreement": dict(decision_agreement=0.50),
            "chosen_candidate_agreement": dict(chosen_candidate_agreement=0.10),
            "veto_agreement": dict(veto_agreement=0.20),
            "target_choice_agreement": dict(target_choice_agreement=0.30),
            "score_stddev": dict(score_stddev=5.0),
            "invalid_output_count": dict(invalid_output_count=2),
        }
        for name, override in cases.items():
            with self.subTest(metric=name):
                group = build_group_record(_binding(), _metrics(**override))
                ev = evaluate_group(group, group_key=GROUP, current_binding=_binding())
                self.assertEqual(ev.state, STATE_SHADOW_COLLECTING)
                self.assertIn(name, ev.failed_metrics)
                self.assertFalse(ev.trading_authority)

    def test_minimum_sample_size_is_enforced(self) -> None:
        thresholds = RepeatabilityThresholds(min_samples=100)
        group = build_group_record(_binding(), _metrics(sample_count=99))
        ev = evaluate_group(
            group, group_key=GROUP, current_binding=_binding(), thresholds=thresholds
        )
        self.assertEqual(ev.state, STATE_SHADOW_COLLECTING)
        self.assertEqual(ev.samples_remaining, 1)

    def test_only_qualified_has_trading_authority(self) -> None:
        for state_case in (
            (None, _binding()),
            (build_group_record(_binding(), _metrics(sample_count=1)), _binding()),
            (build_group_record(_binding(), _metrics()), _binding(candidate_count=6)),
            ({"bad": True}, _binding()),
        ):
            group, binding = state_case
            ev = evaluate_group(group, group_key=GROUP, current_binding=binding)
            with self.subTest(state=ev.state):
                self.assertNotEqual(ev.state, STATE_QUALIFIED)
                self.assertFalse(ev.trading_authority)


class BindingSensitivityTests(unittest.TestCase):
    def test_every_binding_field_changes_the_hash(self) -> None:
        base = _binding()
        for field_name, value in (
            ("provider_id", "other"),
            ("model_snapshot", "other"),
            ("decision_schema_version", "other"),
            ("prompt_contract_version", "other"),
            ("evidence_catalog_version", "other"),
            ("generation_settings_hash", "other"),
            ("candidate_count", 99),
        ):
            with self.subTest(field=field_name):
                changed = _binding(**{field_name: value})
                self.assertNotEqual(base.binding_hash, changed.binding_hash)

    def test_identical_binding_is_stable(self) -> None:
        self.assertEqual(_binding().binding_hash, _binding().binding_hash)


class ArtifactEvaluationTests(unittest.TestCase):
    def test_missing_artifact_is_unqualified_not_incompatible(self) -> None:
        """A cold start is not a corruption; the operator response differs."""
        ev = evaluate_artifact(None, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_UNQUALIFIED)
        self.assertEqual(ev.reason, "artifact_missing")
        self.assertTrue(ev.artifact_compatible)

    def test_wrong_schema_is_incompatible(self) -> None:
        artifact = {"schema_version": "20260718_old_schema", "groups": {}}
        ev = evaluate_artifact(artifact, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_INCOMPATIBLE)
        self.assertIn("artifact_schema_mismatch", ev.reason)
        self.assertFalse(ev.artifact_compatible)

    def test_unreadable_groups_is_incompatible(self) -> None:
        artifact = {"schema_version": REPEATABILITY_ARTIFACT_SCHEMA, "groups": None}
        ev = evaluate_artifact(artifact, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_INCOMPATIBLE)

    def test_qualified_group_round_trips_through_artifact(self) -> None:
        artifact = build_artifact({GROUP: build_group_record(_binding(), _metrics())})
        ev = evaluate_artifact(artifact, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_QUALIFIED)
        self.assertTrue(ev.trading_authority)

    def test_absent_group_in_present_artifact_is_unqualified(self) -> None:
        artifact = build_artifact({"other-group": build_group_record(_binding(), _metrics())})
        ev = evaluate_artifact(artifact, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_UNQUALIFIED)
        self.assertEqual(ev.reason, "no_group_recorded")


class ObservationQualificationTests(unittest.TestCase):
    """Metrics must come from real observations, never fabricated."""

    def _obs(self, **overrides) -> QualificationObservation:
        base = dict(
            decision_state="APPROVE",
            chosen_candidate_hash="hash-a",
            veto_enabled=False,
            chosen_target_model="liquidity",
            llm_quality_score=8.0,
        )
        base.update(overrides)
        return QualificationObservation(**base)

    def test_unanimous_observations_agree_completely(self) -> None:
        metrics = qualify_observations([self._obs() for _ in range(10)])
        self.assertEqual(metrics.sample_count, 10)
        self.assertEqual(metrics.decision_agreement, 1.0)
        self.assertEqual(metrics.chosen_candidate_agreement, 1.0)
        self.assertEqual(metrics.score_stddev, 0.0)

    def test_disagreement_lowers_the_metric(self) -> None:
        obs = [self._obs() for _ in range(8)] + [
            self._obs(decision_state="REJECT") for _ in range(2)
        ]
        metrics = qualify_observations(obs)
        self.assertEqual(metrics.sample_count, 10)
        self.assertAlmostEqual(metrics.decision_agreement, 0.8)

    def test_invalid_and_timeout_are_counted_and_excluded(self) -> None:
        obs = [
            self._obs(),
            self._obs(invalid=True),
            self._obs(timed_out=True),
        ]
        metrics = qualify_observations(obs)
        self.assertEqual(metrics.sample_count, 1)
        self.assertEqual(metrics.invalid_output_count, 1)
        self.assertEqual(metrics.timeout_count, 1)

    def test_no_observations_produces_zero_samples_not_qualification(self) -> None:
        metrics = qualify_observations([])
        self.assertEqual(metrics.sample_count, 0)
        group = build_group_record(_binding(), metrics)
        ev = evaluate_group(group, group_key=GROUP, current_binding=_binding())
        self.assertEqual(ev.state, STATE_UNQUALIFIED)
        self.assertFalse(ev.trading_authority)

    def test_score_dispersion_is_measured(self) -> None:
        obs = [self._obs(llm_quality_score=s) for s in (5.0, 9.0)]
        metrics = qualify_observations(obs)
        self.assertAlmostEqual(metrics.score_stddev, 2.0)


class StartupReportingTests(unittest.TestCase):
    def test_startup_line_contains_every_required_field(self) -> None:
        group = build_group_record(_binding(), _metrics(sample_count=12))
        ev = evaluate_group(group, group_key=GROUP, current_binding=_binding())
        line = ev.startup_line()
        for token in (
            "repeatability_state=",
            "sample_count=",
            "required_samples=",
            "artifact_compatible=",
            "trading_authority=",
            "reason=",
        ):
            with self.subTest(token=token):
                self.assertIn(token, line)

    def test_startup_line_reports_the_actual_state(self) -> None:
        ev = evaluate_group(None, group_key=GROUP, current_binding=_binding())
        self.assertIn(f"repeatability_state={STATE_UNQUALIFIED}", ev.startup_line())
        self.assertIn("trading_authority=false", ev.startup_line())

    def test_evaluation_serialises(self) -> None:
        import json

        group = build_group_record(_binding(), _metrics())
        ev = evaluate_group(group, group_key=GROUP, current_binding=_binding())
        payload = json.loads(json.dumps(ev.as_dict()))
        self.assertEqual(payload["state"], STATE_QUALIFIED)
        self.assertTrue(payload["trading_authority"])
        self.assertEqual(payload["binding"]["binding_hash"], _binding().binding_hash)


class LegacyArtifactSchemaTests(unittest.TestCase):
    """A recognised earlier schema is a cold start, not corruption.

    The live artifact on disk carries
    ``20260718_provider_neutral_repeatability_v3``.  Reporting that as
    INCOMPATIBLE sends an operator looking for a corrupt file when the correct
    action is simply to collect qualification samples.  Both states are equally
    non-trading, so the distinction weakens no control.
    """

    def _binding(self):
        return RepeatabilityBinding(
            provider_id="local_openai_compatible",
            model_snapshot="harness-deterministic-v1",
            decision_schema_version="d",
            prompt_contract_version="p",
            evidence_catalog_version="e",
            generation_settings_hash="g",
            candidate_count=3,
        )

    def test_legacy_schema_reports_unqualified_not_incompatible(self) -> None:
        result = evaluate_artifact(
            {"schema_version": "20260718_provider_neutral_repeatability_v3", "groups": {}},
            group_key="g1",
            current_binding=self._binding(),
        )
        self.assertEqual(result.state, STATE_UNQUALIFIED)
        self.assertIn("artifact_predates_qualification_schema", result.reason)
        self.assertFalse(result.trading_authority)

    def test_unknown_schema_still_fails_closed_as_incompatible(self) -> None:
        result = evaluate_artifact(
            {"schema_version": "something_nobody_wrote", "groups": {}},
            group_key="g1",
            current_binding=self._binding(),
        )
        self.assertEqual(result.state, STATE_INCOMPATIBLE)
        self.assertFalse(result.artifact_compatible)
        self.assertFalse(result.trading_authority)

    def test_neither_legacy_nor_unknown_grants_trading_authority(self) -> None:
        for schema in ("20260718_provider_neutral_repeatability_v3", "unknown_v9"):
            with self.subTest(schema=schema):
                result = evaluate_artifact(
                    {"schema_version": schema, "groups": {}},
                    group_key="g1",
                    current_binding=self._binding(),
                )
                self.assertFalse(result.trading_authority)


if __name__ == "__main__":
    unittest.main()
