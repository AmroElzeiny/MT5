"""Explicit repeatability qualification state machine.

The incident logs showed ``repeatability_status=UNAVAILABLE
artifact_state=missing_group`` with no way to progress: shadow repeat sampling
only runs on live traffic, live traffic requires repeatability, and
``UNAVAILABLE`` was a single opaque label covering "never measured", "measured
but not enough samples", "measured for a different model", and "artifact
corrupt".  Those four situations need different operator responses, and only one
of them is a genuine cold start.

States
------
``UNQUALIFIED``       No samples for this group. Non-trading. Requires
                      collection to begin.
``SHADOW_COLLECTING`` Samples exist but fewer than the configured minimum.
                      Non-trading, but progressing; reports how many remain.
``QUALIFIED``         Enough samples and every agreement metric meets its
                      threshold. Trading-eligible.
``STALE``             Was qualified, but the binding it was measured under
                      (model / schema / prompt / catalog / generation settings)
                      has since changed. Non-trading until re-measured.
``INCOMPATIBLE``      Artifact cannot be trusted: unreadable, wrong schema, or
                      self-contradictory. Non-trading, fail-closed.

Nothing here fabricates samples, and nothing silently disables
``require_repeatability_live``.  A group becomes ``QUALIFIED`` only from real
recorded observations that met real thresholds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

REPEATABILITY_STATE_VERSION = "20260731_repeatability_state_machine_v1"
REPEATABILITY_ARTIFACT_SCHEMA = "20260731_repeatability_qualification_v4"

# Schemas this state machine recognises but was not measured under.  An artifact
# in one of these is not corrupt -- it simply predates qualification, so the
# correct operator action is "start collecting", not "investigate corruption".
# Both outcomes are equally non-trading, so distinguishing them weakens nothing
# and only makes the required action unambiguous.
LEGACY_ARTIFACT_SCHEMAS: frozenset[str] = frozenset(
    {"20260718_provider_neutral_repeatability_v3"}
)

STATE_UNQUALIFIED = "UNQUALIFIED"
STATE_SHADOW_COLLECTING = "SHADOW_COLLECTING"
STATE_QUALIFIED = "QUALIFIED"
STATE_STALE = "STALE"
STATE_INCOMPATIBLE = "INCOMPATIBLE"

ALL_STATES = (
    STATE_UNQUALIFIED,
    STATE_SHADOW_COLLECTING,
    STATE_QUALIFIED,
    STATE_STALE,
    STATE_INCOMPATIBLE,
)

# Only QUALIFIED may trade when repeatability is required.
TRADING_STATES = frozenset({STATE_QUALIFIED})


@dataclass(frozen=True)
class RepeatabilityBinding:
    """What a measurement was taken under.

    A repeatability result is only meaningful for the exact configuration that
    produced it; any change to these invalidates it.
    """

    provider_id: str
    model_snapshot: str
    decision_schema_version: str
    prompt_contract_version: str
    evidence_catalog_version: str
    generation_settings_hash: str
    candidate_count: int

    @property
    def binding_hash(self) -> str:
        material = "|".join(
            (
                self.provider_id,
                self.model_snapshot,
                self.decision_schema_version,
                self.prompt_contract_version,
                self.evidence_catalog_version,
                self.generation_settings_hash,
                str(self.candidate_count),
            )
        )
        return sha256(material.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_snapshot": self.model_snapshot,
            "decision_schema_version": self.decision_schema_version,
            "prompt_contract_version": self.prompt_contract_version,
            "evidence_catalog_version": self.evidence_catalog_version,
            "generation_settings_hash": self.generation_settings_hash,
            "candidate_count": self.candidate_count,
            "binding_hash": self.binding_hash,
        }


@dataclass(frozen=True)
class RepeatabilityThresholds:
    min_samples: int = 30
    min_decision_agreement: float = 0.90
    min_chosen_candidate_agreement: float = 0.90
    min_veto_agreement: float = 0.90
    min_target_choice_agreement: float = 0.85
    max_score_stddev: float = 0.75


@dataclass
class RepeatabilityMetrics:
    """Observed agreement across repeated calls for one group."""

    sample_count: int = 0
    decision_agreement: float = 0.0
    chosen_candidate_agreement: float = 0.0
    veto_agreement: float = 0.0
    target_choice_agreement: float = 0.0
    score_stddev: float = 0.0
    invalid_output_count: int = 0
    timeout_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "decision_agreement": round(self.decision_agreement, 4),
            "chosen_candidate_agreement": round(self.chosen_candidate_agreement, 4),
            "veto_agreement": round(self.veto_agreement, 4),
            "target_choice_agreement": round(self.target_choice_agreement, 4),
            "score_stddev": round(self.score_stddev, 4),
            "invalid_output_count": self.invalid_output_count,
            "timeout_count": self.timeout_count,
        }


@dataclass(frozen=True)
class RepeatabilityEvaluation:
    state: str
    reason: str
    group_key: str
    metrics: RepeatabilityMetrics
    thresholds: RepeatabilityThresholds
    binding: RepeatabilityBinding | None
    artifact_compatible: bool
    failed_metrics: tuple[str, ...] = ()

    @property
    def trading_authority(self) -> bool:
        return self.state in TRADING_STATES

    @property
    def samples_remaining(self) -> int:
        return max(0, self.thresholds.min_samples - self.metrics.sample_count)

    def startup_line(self) -> str:
        return (
            "[repeatability_state]"
            f" repeatability_state={self.state}"
            f" group_key={self.group_key or 'none'}"
            f" sample_count={self.metrics.sample_count}"
            f" required_samples={self.thresholds.min_samples}"
            f" samples_remaining={self.samples_remaining}"
            f" artifact_compatible={str(self.artifact_compatible).lower()}"
            f" trading_authority={str(self.trading_authority).lower()}"
            f" failed_metrics={','.join(self.failed_metrics) or 'none'}"
            f" reason={self.reason}"
            f" state_version={REPEATABILITY_STATE_VERSION}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "group_key": self.group_key,
            "metrics": self.metrics.as_dict(),
            "thresholds": vars(self.thresholds),
            "binding": self.binding.as_dict() if self.binding else None,
            "artifact_compatible": self.artifact_compatible,
            "trading_authority": self.trading_authority,
            "samples_remaining": self.samples_remaining,
            "failed_metrics": list(self.failed_metrics),
            "state_version": REPEATABILITY_STATE_VERSION,
        }


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # reject NaN


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def metrics_from_group(group: Mapping[str, Any]) -> RepeatabilityMetrics:
    return RepeatabilityMetrics(
        sample_count=_as_int(group.get("sample_count")),
        decision_agreement=_as_float(group.get("decision_agreement")),
        chosen_candidate_agreement=_as_float(group.get("chosen_candidate_agreement")),
        veto_agreement=_as_float(group.get("veto_agreement")),
        target_choice_agreement=_as_float(group.get("target_choice_agreement")),
        score_stddev=_as_float(group.get("score_stddev")),
        invalid_output_count=_as_int(group.get("invalid_output_count")),
        timeout_count=_as_int(group.get("timeout_count")),
    )


def binding_from_group(group: Mapping[str, Any]) -> RepeatabilityBinding | None:
    raw = group.get("binding")
    if not isinstance(raw, Mapping):
        return None
    try:
        return RepeatabilityBinding(
            provider_id=str(raw.get("provider_id") or ""),
            model_snapshot=str(raw.get("model_snapshot") or ""),
            decision_schema_version=str(raw.get("decision_schema_version") or ""),
            prompt_contract_version=str(raw.get("prompt_contract_version") or ""),
            evidence_catalog_version=str(raw.get("evidence_catalog_version") or ""),
            generation_settings_hash=str(raw.get("generation_settings_hash") or ""),
            candidate_count=_as_int(raw.get("candidate_count")),
        )
    except (TypeError, ValueError):
        return None


def _failed_metrics(
    metrics: RepeatabilityMetrics, thresholds: RepeatabilityThresholds
) -> tuple[str, ...]:
    failed: list[str] = []
    if metrics.decision_agreement < thresholds.min_decision_agreement:
        failed.append("decision_agreement")
    if metrics.chosen_candidate_agreement < thresholds.min_chosen_candidate_agreement:
        failed.append("chosen_candidate_agreement")
    if metrics.veto_agreement < thresholds.min_veto_agreement:
        failed.append("veto_agreement")
    if metrics.target_choice_agreement < thresholds.min_target_choice_agreement:
        failed.append("target_choice_agreement")
    if metrics.score_stddev > thresholds.max_score_stddev:
        failed.append("score_stddev")
    if metrics.invalid_output_count:
        failed.append("invalid_output_count")
    return tuple(failed)


def evaluate_group(
    group: Mapping[str, Any] | None,
    *,
    group_key: str,
    current_binding: RepeatabilityBinding,
    thresholds: RepeatabilityThresholds | None = None,
) -> RepeatabilityEvaluation:
    """Classify one repeatability group into exactly one state."""

    thresholds = thresholds or RepeatabilityThresholds()

    if group is None:
        return RepeatabilityEvaluation(
            state=STATE_UNQUALIFIED,
            reason="no_group_recorded",
            group_key=group_key,
            metrics=RepeatabilityMetrics(),
            thresholds=thresholds,
            binding=None,
            artifact_compatible=True,
        )

    if not isinstance(group, Mapping):
        return RepeatabilityEvaluation(
            state=STATE_INCOMPATIBLE,
            reason="group_not_object",
            group_key=group_key,
            metrics=RepeatabilityMetrics(),
            thresholds=thresholds,
            binding=None,
            artifact_compatible=False,
        )

    metrics = metrics_from_group(group)
    binding = binding_from_group(group)

    if binding is None:
        return RepeatabilityEvaluation(
            state=STATE_INCOMPATIBLE,
            reason="group_binding_missing",
            group_key=group_key,
            metrics=metrics,
            thresholds=thresholds,
            binding=None,
            artifact_compatible=False,
        )

    if metrics.sample_count < 0 or any(
        value < 0.0 or value > 1.0
        for value in (
            metrics.decision_agreement,
            metrics.chosen_candidate_agreement,
            metrics.veto_agreement,
            metrics.target_choice_agreement,
        )
    ):
        return RepeatabilityEvaluation(
            state=STATE_INCOMPATIBLE,
            reason="group_metrics_out_of_range",
            group_key=group_key,
            metrics=metrics,
            thresholds=thresholds,
            binding=binding,
            artifact_compatible=False,
        )

    if binding.binding_hash != current_binding.binding_hash:
        return RepeatabilityEvaluation(
            state=STATE_STALE,
            reason=(
                "binding_changed:"
                f"recorded={binding.binding_hash}:current={current_binding.binding_hash}"
            ),
            group_key=group_key,
            metrics=metrics,
            thresholds=thresholds,
            binding=binding,
            artifact_compatible=True,
        )

    if metrics.sample_count <= 0:
        return RepeatabilityEvaluation(
            state=STATE_UNQUALIFIED,
            reason="no_samples_for_current_binding",
            group_key=group_key,
            metrics=metrics,
            thresholds=thresholds,
            binding=binding,
            artifact_compatible=True,
        )

    if metrics.sample_count < thresholds.min_samples:
        return RepeatabilityEvaluation(
            state=STATE_SHADOW_COLLECTING,
            reason=(
                f"insufficient_samples:{metrics.sample_count}/{thresholds.min_samples}"
            ),
            group_key=group_key,
            metrics=metrics,
            thresholds=thresholds,
            binding=binding,
            artifact_compatible=True,
        )

    failed = _failed_metrics(metrics, thresholds)
    if failed:
        return RepeatabilityEvaluation(
            state=STATE_SHADOW_COLLECTING,
            reason="thresholds_not_met",
            group_key=group_key,
            metrics=metrics,
            thresholds=thresholds,
            binding=binding,
            artifact_compatible=True,
            failed_metrics=failed,
        )

    return RepeatabilityEvaluation(
        state=STATE_QUALIFIED,
        reason="qualified",
        group_key=group_key,
        metrics=metrics,
        thresholds=thresholds,
        binding=binding,
        artifact_compatible=True,
    )


def evaluate_artifact(
    artifact: Mapping[str, Any] | None,
    *,
    group_key: str,
    current_binding: RepeatabilityBinding,
    thresholds: RepeatabilityThresholds | None = None,
) -> RepeatabilityEvaluation:
    """Evaluate a whole artifact file, then the requested group inside it."""

    thresholds = thresholds or RepeatabilityThresholds()

    if artifact is None:
        return RepeatabilityEvaluation(
            state=STATE_UNQUALIFIED,
            reason="artifact_missing",
            group_key=group_key,
            metrics=RepeatabilityMetrics(),
            thresholds=thresholds,
            binding=None,
            artifact_compatible=True,
        )

    if not isinstance(artifact, Mapping):
        return RepeatabilityEvaluation(
            state=STATE_INCOMPATIBLE,
            reason="artifact_not_object",
            group_key=group_key,
            metrics=RepeatabilityMetrics(),
            thresholds=thresholds,
            binding=None,
            artifact_compatible=False,
        )

    schema = str(artifact.get("schema_version") or "")
    if schema and schema != REPEATABILITY_ARTIFACT_SCHEMA:
        legacy = schema in LEGACY_ARTIFACT_SCHEMAS
        return RepeatabilityEvaluation(
            # A recognised earlier schema means "never measured under the
            # qualification contract", which is a cold start, not corruption.
            # An unrecognised schema stays INCOMPATIBLE and fails closed.
            state=STATE_UNQUALIFIED if legacy else STATE_INCOMPATIBLE,
            reason=(
                f"artifact_predates_qualification_schema:{schema}"
                if legacy
                else f"artifact_schema_mismatch:{schema}"
            ),
            group_key=group_key,
            metrics=RepeatabilityMetrics(),
            thresholds=thresholds,
            binding=None,
            artifact_compatible=legacy,
        )

    groups = artifact.get("groups")
    if not isinstance(groups, Mapping):
        return RepeatabilityEvaluation(
            state=STATE_INCOMPATIBLE,
            reason="artifact_groups_invalid",
            group_key=group_key,
            metrics=RepeatabilityMetrics(),
            thresholds=thresholds,
            binding=None,
            artifact_compatible=False,
        )

    return evaluate_group(
        groups.get(group_key),
        group_key=group_key,
        current_binding=current_binding,
        thresholds=thresholds,
    )


def load_artifact(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"schema_version": "unreadable", "groups": None}


@dataclass
class QualificationObservation:
    """One repeated-call outcome used to build a group's metrics."""

    decision_state: str
    chosen_candidate_hash: str
    veto_enabled: bool
    chosen_target_model: str
    llm_quality_score: float
    invalid: bool = False
    timed_out: bool = False


def qualify_observations(
    observations: Sequence[QualificationObservation],
) -> RepeatabilityMetrics:
    """Compute agreement metrics from real recorded observations.

    Agreement is the modal fraction: how often the most common answer occurred.
    A single observation is 100% self-consistent, which is why the sample-count
    floor exists separately and is enforced before these metrics are consulted.
    """

    usable = [o for o in observations if not o.invalid and not o.timed_out]
    metrics = RepeatabilityMetrics(
        sample_count=len(usable),
        invalid_output_count=sum(1 for o in observations if o.invalid),
        timeout_count=sum(1 for o in observations if o.timed_out),
    )
    if not usable:
        return metrics

    def modal_fraction(values: Sequence[Any]) -> float:
        counts: dict[Any, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return max(counts.values()) / len(values)

    metrics.decision_agreement = modal_fraction([o.decision_state for o in usable])
    metrics.chosen_candidate_agreement = modal_fraction(
        [o.chosen_candidate_hash for o in usable]
    )
    metrics.veto_agreement = modal_fraction([o.veto_enabled for o in usable])
    metrics.target_choice_agreement = modal_fraction(
        [o.chosen_target_model for o in usable]
    )

    scores = [o.llm_quality_score for o in usable]
    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    metrics.score_stddev = variance**0.5
    return metrics


def build_group_record(
    binding: RepeatabilityBinding,
    metrics: RepeatabilityMetrics,
) -> dict[str, Any]:
    record = {"binding": binding.as_dict()}
    record.update(metrics.as_dict())
    return record


def build_artifact(groups: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": REPEATABILITY_ARTIFACT_SCHEMA,
        "state_version": REPEATABILITY_STATE_VERSION,
        "groups": dict(groups),
    }
