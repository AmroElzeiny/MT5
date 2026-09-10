"""Counterfactual (shadow) outcome ledger: consolidation, audit and evidence.

The EA appends an immutable event stream to ``<bus>/logs/shadow_candidates.jsonl``
describing what every eligible setup would have done after APPROVE, REJECT,
ABSTAIN, a policy rejection or an execution rejection.  This module is the only
supported reader of that stream.

Three rules are structural here, not stylistic:

* **One terminal per variant.**  A second terminal for a ``candidate_variant_id``
  is a defect, and is reported as a conflict rather than merged or averaged.
* **One statistical unit per sweep.**  Genuine plan variants of the same sweep
  are children of one ``sweep_opportunity_id``; a family rate that counts them
  as independent trades overstates its sample by the branch fan-out.
* **No look-ahead.**  Historical evidence offered to a decision may only contain
  outcomes whose terminal timestamp is strictly earlier than that decision's
  own timestamp.  Unresolved and future outcomes are excluded, not imputed.

Nothing produced here is trading authority.  ``historical_evidence`` reports
``authority="DIAGNOSTIC_SHADOW_ONLY"`` unless the documented promotion gate has
been satisfied AND explicitly enabled, and even then it never forces a trade.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SHADOW_LEDGER_SCHEMA_VERSION = "20260908_shadow_lifecycle_v4"
SHADOW_LEDGER_ANALYSIS_VERSION = "20260908_shadow_analysis_v1"

# The promotion gate.  Historical shadow statistics stay diagnostic until every
# one of these is satisfied AND the operator has switched them on deliberately.
# They are constants, not tunables, so "we lowered the bar" shows up in a diff.
PROMOTION_MIN_CLEAN_SAMPLES = 200
PROMOTION_MIN_SWEEPS = 120
PROMOTION_MAX_CI_WIDTH = 0.20
PROMOTION_REQUIRES_OUT_OF_SAMPLE = True

EVIDENCE_MIN_CLEAN_SAMPLES = 20
EVIDENCE_MIN_SWEEPS = 12

# Engine-era floor for counterfactual evidence.
#
# Outcomes observed before this instant were produced by superseded target
# arbitration: the asymmetric route ranking corrected by the obstacle-symmetry
# work, and the first legs relocated by the pre-TP1-authority price builder.
# Their entries, first legs and targets are not the ones this build selects, so
# counting them answers "what did a different engine do" -- which is not the
# question the decision is asking.
#
# Enforced by policy, never by editing the ledger: the event stream is an
# immutable audit trail and stays complete on disk.  Raising or lowering the
# floor is therefore reversible and shows up in a diff.
EVIDENCE_ERA_FLOOR_TS = 1788825600  # 2026-09-08 00:00:00 UTC

# Decision states that represent an actual AI judgement about a candidate.
# PENDING_DECISION and NOT_ASSESSED are deliberately absent: they are not a
# quieter kind of decision, they are the absence of one.
AI_DECIDED_STATES: tuple[str, ...] = ("APPROVE", "REJECT", "ABSTAIN")

# The match ladder.  Each rung widens the query by exactly one axis, and the
# first rung that satisfies the sample nisab answers.  There is deliberately no
# rung for never-assessed candidates: see ``historical_evidence_ladder``.
EVIDENCE_LADDER_RUNGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("EXACT_FAMILY_TAXONOMY_STATE", ("family", "setup_taxonomy", "decision_state")),
    ("FAMILY_TAXONOMY_ANY_DECIDED_STATE", ("family", "setup_taxonomy")),
    ("FAMILY_ANY_DECIDED_STATE", ("family",)),
)

EVENT_OPPORTUNITY_OBSERVED = "shadow_opportunity_observed"
EVENT_CANDIDATE_OBSERVED = "shadow_candidate_observed"
EVENT_DECISION_RECORDED = "shadow_decision_recorded"
EVENT_ENTRY_ACTIVATED = "shadow_entry_activated"
EVENT_PATH_PROGRESS = "shadow_path_progress"
EVENT_TP1_REACHED = "shadow_tp1_reached"
EVENT_TERMINAL_RESOLUTION = "shadow_terminal_resolution"
EVENT_DATA_QUALITY_FAILURE = "shadow_data_quality_failure"

KNOWN_EVENT_TYPES = frozenset(
    {
        EVENT_OPPORTUNITY_OBSERVED,
        EVENT_CANDIDATE_OBSERVED,
        EVENT_DECISION_RECORDED,
        EVENT_ENTRY_ACTIVATED,
        EVENT_PATH_PROGRESS,
        EVENT_TP1_REACHED,
        EVENT_TERMINAL_RESOLUTION,
        EVENT_DATA_QUALITY_FAILURE,
    }
)

# Pre-v4 event vocabulary.  Read for provenance, never mixed into statistics:
# those rows have no entry activation, no TP1 ordering and no stable identity,
# so treating them as comparable samples would silently pollute every rate.
LEGACY_EVENT_TYPES = frozenset(
    {"candidate_observed", "candidate_decision_update", "hypothetical_outcome_resolution"}
)

# Terminal events that produce a defined hypothetical R.
RESULT_BEARING_TERMINALS = frozenset(
    {
        "TP2_BEFORE_SL",
        "TP1_THEN_TP2",
        "TP1_THEN_SL",
        "SL_BEFORE_TP1",
        "HORIZON_CENSORED",
        "SESSION_CLOSE_CENSORED",
    }
)

# Terminal events whose ordered TP/SL booleans are decidable.
CLEAN_TERMINALS = frozenset({"TP2_BEFORE_SL", "TP1_THEN_TP2", "TP1_THEN_SL", "SL_BEFORE_TP1"})

CENSORED_TERMINALS = frozenset({"HORIZON_CENSORED", "SESSION_CLOSE_CENSORED"})

EXCLUDED_TERMINALS = frozenset(
    {"ENTRY_NEVER_REACHED", "DATA_LOSS", "UNTRACKABLE"}
)

DECISION_STATES = ("APPROVE", "REJECT", "ABSTAIN")


class ShadowLedgerError(ValueError):
    """Raised when the ledger cannot be read at all."""


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def decode_ledger_bytes(raw: bytes) -> str:
    """Decode a ledger written by MQL (UTF-16, FILE_TXT default) or by a test.

    MQL5's FILE_TXT writer emits UTF-16LE with a BOM.  A reader that assumes
    UTF-8 sees one unreadable blob and reports an empty ledger, which is
    indistinguishable from "the EA never wrote anything".
    """

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "utf-8", "utf-16-le", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ShadowLedgerError("shadow_ledger_encoding_unreadable")


@dataclass(frozen=True)
class LedgerRead:
    rows: list[dict[str, Any]]
    unparseable: list[dict[str, Any]]
    legacy_rows: int
    total_lines: int


def read_shadow_events(path: str | Path) -> LedgerRead:
    """Parse the append-only ledger without discarding evidence of corruption."""

    target = Path(path)
    if not target.is_file():
        return LedgerRead(rows=[], unparseable=[], legacy_rows=0, total_lines=0)
    text = decode_ledger_bytes(target.read_bytes())
    rows: list[dict[str, Any]] = []
    unparseable: list[dict[str, Any]] = []
    legacy = 0
    total = 0
    for index, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        total += 1
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            unparseable.append({"line": index, "reason": f"json_decode_error:{exc.msg}"})
            continue
        if not isinstance(payload, Mapping):
            unparseable.append({"line": index, "reason": "row_not_object"})
            continue
        row = dict(payload)
        row["_line"] = index
        event = str(row.get("event_type") or "")
        if event in LEGACY_EVENT_TYPES:
            legacy += 1
            continue
        rows.append(row)
    return LedgerRead(rows=rows, unparseable=unparseable, legacy_rows=legacy, total_lines=total)


# --------------------------------------------------------------------------
# Consolidation
# --------------------------------------------------------------------------


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _integer(value: Any) -> int | None:
    result = _number(value)
    if result is None:
        return None
    return int(result)


def _tri_bool(value: Any) -> bool | None:
    """Preserve the difference between False and "not decidable"."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return None


@dataclass
class VariantRecord:
    """One consolidated counterfactual: exactly one row per candidate variant."""

    candidate_variant_id: str = ""
    sweep_opportunity_id: str = ""
    parent_record_hash: str = ""
    candidate_hash: str = ""
    execution_fingerprint: str = ""
    symbol: str = ""
    family: str = ""
    setup_class: str = ""
    setup_taxonomy: str = ""
    entry_branch: str = ""
    session: str = ""
    killzone: str = ""
    context_tier: str = ""
    target_model: str = ""
    tp_model: str = ""
    decision_state: str = ""
    decision_source: str = ""
    decision_stage: str = ""
    # The REQUEST's verdict, and whether that request was of trading grade at
    # all.  An error envelope is schema-required to carry APPROVE/REJECT/ABSTAIN
    # with decision_quality_tier=DEGRADED_NON_TRADING and no candidate
    # assessments, so without the tier a pipeline failure is indistinguishable
    # from an AI rejection in every aggregate.
    request_decision_state: str = ""
    decision_quality_tier: str = ""
    trading_tier: bool | None = None
    rejection_reason: str = ""
    narrative_state: str = ""
    python_decision_reasons: str = ""
    mql_decision_reasons: str = ""
    model_version: str = ""
    prompt_contract_version: str = ""
    decision_schema_version: str = ""
    variant_revision: int = 0
    variant_parent_id: str = ""
    observation_count: int = 0
    observed_at: int | None = None
    horizon_at: int | None = None
    observation: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    terminal: dict[str, Any] = field(default_factory=dict)
    progress: list[dict[str, Any]] = field(default_factory=list)
    data_quality_failures: list[dict[str, Any]] = field(default_factory=list)
    entry_activated: bool | None = None
    entry_activated_at: int | None = None
    time_to_entry_sec: int | None = None
    entry_never_reached: bool | None = None
    entry_order_ambiguous: bool | None = None
    tp1_hit: bool | None = None
    tp1_hit_at: int | None = None
    time_to_tp1_sec: int | None = None
    tp1_before_sl: bool | None = None
    sl_before_tp1: bool | None = None
    tp2_hit: bool | None = None
    tp2_hit_at: int | None = None
    time_to_tp2_sec: int | None = None
    tp2_before_sl: bool | None = None
    sl_before_tp2: bool | None = None
    tp1_then_sl: bool | None = None
    tp1_then_tp2: bool | None = None
    neither_target_nor_stop: bool | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    maximum_favorable_price: float | None = None
    maximum_adverse_price: float | None = None
    result_r_unmanaged: float | None = None
    result_r_with_configured_tp1_partial: float | None = None
    terminal_event: str = ""
    terminal_event_at: int | None = None
    censoring_status: str = ""
    ambiguity_status: str = ""
    ambiguity_reason: str = ""
    data_quality_status: str = ""
    ordering_source: str = ""
    sample_class: str = "UNRESOLVED"

    def as_dict(self) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in self.__dict__.items()
            if key not in {"observation", "decision", "terminal", "progress", "data_quality_failures"}
        }
        payload["progress_events"] = len(self.progress)
        payload["data_quality_failure_events"] = len(self.data_quality_failures)
        payload["schema_version"] = SHADOW_LEDGER_SCHEMA_VERSION
        payload["trading_authority"] = False
        return payload


def _classify_sample(record: VariantRecord) -> str:
    """Bucket a variant so no aggregate can silently read an exclusion as a loss."""

    terminal = record.terminal_event
    if not terminal:
        return "UNRESOLVED"
    if terminal == "UNTRACKABLE":
        return "EXCLUDED_INVALID_CONTRACT"
    if terminal == "DATA_LOSS":
        return "EXCLUDED_DATA_LOSS"
    if terminal == "ENTRY_NEVER_REACHED":
        return "ENTRY_NEVER_REACHED"
    if terminal.startswith("AMBIGUOUS") or record.ambiguity_status == "AMBIGUOUS":
        return "EXCLUDED_AMBIGUOUS"
    if terminal in CENSORED_TERMINALS:
        return "RIGHT_CENSORED"
    if terminal in CLEAN_TERMINALS:
        return "CLEAN"
    return "EXCLUDED_UNKNOWN_TERMINAL"


@dataclass(frozen=True)
class Consolidation:
    variants: list[VariantRecord]
    opportunities: dict[str, dict[str, Any]]
    rejected: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]


def consolidate_shadow_lifecycle(rows: Sequence[Mapping[str, Any]]) -> Consolidation:
    """Fold the append-only stream into one record per variant.

    Delta events are joined on ``candidate_variant_id`` and cross-checked
    against the parent observation's ``parent_record_hash``, ``candidate_hash``
    and ``execution_fingerprint``.  A delta whose identity does not match its
    parent exactly is rejected, never guessed into a candidate.
    """

    variants: dict[str, VariantRecord] = {}
    opportunities: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    terminal_seen: dict[str, Mapping[str, Any]] = {}

    def reject(row: Mapping[str, Any], reason: str) -> None:
        rejected.append(
            {
                "line": row.get("_line"),
                "event_type": _text(row.get("event_type")),
                "candidate_variant_id": _text(row.get("candidate_variant_id")),
                "reason": reason,
            }
        )

    ordered = sorted(rows, key=lambda row: (_integer(row.get("event_at")) or 0, _integer(row.get("_line")) or 0))

    for row in ordered:
        event = _text(row.get("event_type"))
        if event not in KNOWN_EVENT_TYPES:
            reject(row, "unknown_shadow_event_type")
            continue
        if _text(row.get("schema_version")) != SHADOW_LEDGER_SCHEMA_VERSION:
            reject(row, "shadow_schema_version_mismatch")
            continue

        opportunity_id = _text(row.get("sweep_opportunity_id"))
        variant_id = _text(row.get("candidate_variant_id"))

        if event == EVENT_OPPORTUNITY_OBSERVED:
            if not opportunity_id:
                reject(row, "opportunity_identity_missing")
                continue
            if opportunity_id in opportunities:
                # Not an error: the EA writes one per sweep, but a replayed or
                # concatenated ledger can legitimately contain the first one
                # twice.  Keep the earliest and count the repeat.
                opportunities[opportunity_id]["duplicate_observations"] += 1
                continue
            opportunities[opportunity_id] = {
                "sweep_opportunity_id": opportunity_id,
                "symbol": _text(row.get("symbol")),
                "direction": _text(row.get("direction")),
                "sweep_side": _text(row.get("sweep_side")),
                "lineage": _text(row.get("sweep_opportunity_lineage")),
                "session": _text(row.get("session")),
                "killzone": _text(row.get("killzone")),
                "context_tier": _text(row.get("context_tier")),
                "po3_state": _text(row.get("po3_state")),
                "source_sweep_timestamp": _integer(row.get("source_sweep_timestamp")),
                "source_displacement_timestamp": _integer(row.get("source_displacement_timestamp")),
                "source_bos_timestamp": _integer(row.get("source_bos_timestamp")),
                "observed_at": _integer(row.get("event_at")),
                "statistical_weight": _number(row.get("statistical_weight")) or 1.0,
                "duplicate_observations": 0,
                "variant_ids": [],
            }
            continue

        if not variant_id:
            reject(row, "variant_identity_missing")
            continue

        if event == EVENT_CANDIDATE_OBSERVED:
            if variant_id in variants:
                reject(row, "duplicate_candidate_variant_observation")
                continue
            record = VariantRecord(
                candidate_variant_id=variant_id,
                sweep_opportunity_id=opportunity_id,
                parent_record_hash=_text(row.get("parent_record_hash")),
                candidate_hash=_text(row.get("candidate_hash")),
                execution_fingerprint=_text(row.get("execution_fingerprint")),
                symbol=_text(row.get("symbol")),
                family=_text(row.get("family")) or _text(row.get("setup_family")),
                setup_class=_text(row.get("setup_class")),
                setup_taxonomy=_text(row.get("setup_taxonomy")) or _text(row.get("setup_taxonomy_enum")),
                entry_branch=_text(row.get("entry_branch")),
                session=_text(row.get("session")),
                killzone=_text(row.get("killzone")),
                context_tier=_text(row.get("context_tier")),
                target_model=_text(row.get("target_model")),
                tp_model=_text(row.get("tp_model")),
                decision_state=_text(row.get("decision_state")),
                decision_source=_text(row.get("decision_source")),
                decision_stage=_text(row.get("decision_stage")),
                rejection_reason=_text(row.get("rejection_reason")),
                variant_revision=_integer(row.get("variant_revision")) or 0,
                variant_parent_id=_text(row.get("variant_parent_id")),
                observation_count=1,
                observed_at=_integer(row.get("observed_at")) or _integer(row.get("event_at")),
                horizon_at=_integer(row.get("horizon_at")),
                observation=dict(row),
                data_quality_status=_text(row.get("data_quality_status")),
            )
            variants[variant_id] = record
            if opportunity_id in opportunities:
                opportunities[opportunity_id]["variant_ids"].append(variant_id)
            continue

        record = variants.get(variant_id)
        if record is None:
            reject(row, "shadow_delta_parent_missing")
            continue
        parent_hash = _text(row.get("parent_record_hash"))
        if parent_hash and record.parent_record_hash and parent_hash != record.parent_record_hash:
            reject(row, "shadow_delta_parent_record_hash_mismatch")
            continue
        candidate_hash = _text(row.get("candidate_hash"))
        if candidate_hash and record.candidate_hash and candidate_hash != record.candidate_hash:
            reject(row, "shadow_delta_candidate_hash_mismatch")
            continue
        fingerprint = _text(row.get("execution_fingerprint"))
        if fingerprint and record.execution_fingerprint and fingerprint != record.execution_fingerprint:
            reject(row, "shadow_delta_execution_fingerprint_mismatch")
            continue

        if event == EVENT_DECISION_RECORDED:
            record.decision = dict(row)
            record.decision_state = _text(row.get("decision_state")) or record.decision_state
            record.decision_source = _text(row.get("decision_source")) or record.decision_source
            record.decision_stage = _text(row.get("decision_stage")) or record.decision_stage
            record.rejection_reason = _text(row.get("rejection_reason")) or record.rejection_reason
            record.model_version = _text(row.get("model_version")) or record.model_version
            record.prompt_contract_version = (
                _text(row.get("prompt_contract_version")) or record.prompt_contract_version
            )
            record.decision_schema_version = (
                _text(row.get("decision_schema_version")) or record.decision_schema_version
            )
            record.python_decision_reasons = _text(row.get("python_reasons")) or record.python_decision_reasons
            record.mql_decision_reasons = _text(row.get("mql_reasons")) or record.mql_decision_reasons
            record.request_decision_state = (
                _text(row.get("request_decision_state")) or record.request_decision_state
            )
            record.decision_quality_tier = (
                _text(row.get("decision_quality_tier")) or record.decision_quality_tier
            )
            if "trading_tier" in row:
                record.trading_tier = bool(row.get("trading_tier"))
            continue

        if event == EVENT_ENTRY_ACTIVATED:
            record.entry_activated = True
            record.entry_activated_at = _integer(row.get("entry_activated_at"))
            record.time_to_entry_sec = _integer(row.get("time_to_entry_sec"))
            record.entry_order_ambiguous = bool(row.get("entry_order_ambiguous"))
            record.progress.append(dict(row))
            continue

        if event in {EVENT_PATH_PROGRESS, EVENT_TP1_REACHED}:
            record.progress.append(dict(row))
            if event == EVENT_TP1_REACHED:
                record.tp1_hit = True
                record.tp1_hit_at = _integer(row.get("tp1_hit_at"))
                record.time_to_tp1_sec = _integer(row.get("time_to_tp1_sec"))
            continue

        if event == EVENT_DATA_QUALITY_FAILURE:
            record.data_quality_failures.append(dict(row))
            continue

        # Terminal resolution.
        if variant_id in terminal_seen:
            conflicts.append(
                {
                    "candidate_variant_id": variant_id,
                    "first_line": terminal_seen[variant_id].get("_line"),
                    "first_terminal_event": _text(terminal_seen[variant_id].get("terminal_event")),
                    "duplicate_line": row.get("_line"),
                    "duplicate_terminal_event": _text(row.get("terminal_event")),
                    "reason": "conflicting_terminal_outcome"
                    if _text(terminal_seen[variant_id].get("terminal_event")) != _text(row.get("terminal_event"))
                    else "duplicate_terminal_outcome",
                }
            )
            continue
        terminal_seen[variant_id] = row
        record.terminal = dict(row)
        record.terminal_event = _text(row.get("terminal_event"))
        record.terminal_event_at = _integer(row.get("terminal_event_at"))
        record.censoring_status = _text(row.get("censoring_status"))
        record.ambiguity_status = _text(row.get("ambiguity_status"))
        record.ambiguity_reason = _text(row.get("ambiguity_reason"))
        record.data_quality_status = _text(row.get("data_quality_status")) or record.data_quality_status
        record.ordering_source = _text(row.get("ordering_source"))
        record.narrative_state = _text(row.get("narrative_state"))
        record.observation_count = _integer(row.get("observation_count")) or record.observation_count
        record.entry_activated = bool(row.get("entry_activated"))
        record.entry_activated_at = _integer(row.get("entry_activated_at")) or record.entry_activated_at
        record.time_to_entry_sec = (
            _integer(row.get("time_to_entry_sec"))
            if row.get("time_to_entry_sec") is not None
            else record.time_to_entry_sec
        )
        record.entry_never_reached = bool(row.get("entry_never_reached"))
        record.entry_order_ambiguous = bool(row.get("entry_order_ambiguous"))
        record.tp1_hit = bool(row.get("tp1_hit"))
        record.tp1_hit_at = _integer(row.get("tp1_hit_at")) or record.tp1_hit_at
        record.time_to_tp1_sec = (
            _integer(row.get("time_to_tp1_sec"))
            if row.get("time_to_tp1_sec") is not None
            else record.time_to_tp1_sec
        )
        record.tp2_hit = bool(row.get("tp2_hit"))
        record.tp2_hit_at = _integer(row.get("tp2_hit_at"))
        record.time_to_tp2_sec = _integer(row.get("time_to_tp2_sec"))
        record.tp1_before_sl = _tri_bool(row.get("tp1_before_sl"))
        record.sl_before_tp1 = _tri_bool(row.get("sl_before_tp1"))
        record.tp2_before_sl = _tri_bool(row.get("tp2_before_sl"))
        record.sl_before_tp2 = _tri_bool(row.get("sl_before_tp2"))
        record.tp1_then_sl = _tri_bool(row.get("tp1_then_sl"))
        record.tp1_then_tp2 = _tri_bool(row.get("tp1_then_tp2"))
        record.neither_target_nor_stop = _tri_bool(row.get("neither_target_nor_stop"))
        record.mfe_r = _number(row.get("mfe_r"))
        record.mae_r = _number(row.get("mae_r"))
        record.maximum_favorable_price = _number(row.get("maximum_favorable_price"))
        record.maximum_adverse_price = _number(row.get("maximum_adverse_price"))
        record.result_r_unmanaged = _number(row.get("result_r_unmanaged"))
        record.result_r_with_configured_tp1_partial = _number(
            row.get("result_r_with_configured_tp1_partial")
        )
        record.family = record.family or _text(row.get("setup_family"))
        record.setup_class = record.setup_class or _text(row.get("setup_class"))
        record.entry_branch = record.entry_branch or _text(row.get("entry_branch"))
        record.session = record.session or _text(row.get("session"))
        record.decision_state = _text(row.get("decision_state")) or record.decision_state
        record.decision_source = _text(row.get("decision_source")) or record.decision_source
        record.decision_stage = _text(row.get("decision_stage")) or record.decision_stage
        record.rejection_reason = _text(row.get("rejection_reason")) or record.rejection_reason
        record.request_decision_state = (
            _text(row.get("request_decision_state")) or record.request_decision_state
        )
        record.decision_quality_tier = (
            _text(row.get("decision_quality_tier")) or record.decision_quality_tier
        )
        if "trading_tier" in row:
            record.trading_tier = bool(row.get("trading_tier"))

    for record in variants.values():
        record.sample_class = _classify_sample(record)

    return Consolidation(
        variants=list(variants.values()),
        opportunities=opportunities,
        rejected=rejected,
        conflicts=conflicts,
    )


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Wilson score interval.

    A normal-approximation interval on a small sample produces bounds outside
    [0, 1] and a zero-width interval at 0/n, which would let a family with three
    observations look certain.  Wilson does neither.
    """

    if total <= 0:
        return (None, None)
    phat = successes / total
    denominator = 1.0 + (z * z) / total
    centre = (phat + (z * z) / (2 * total)) / denominator
    margin = (z / denominator) * math.sqrt(
        (phat * (1.0 - phat) / total) + (z * z) / (4.0 * total * total)
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


@dataclass(frozen=True)
class GroupStats:
    key: str
    variants: int
    unique_sweeps: int
    entry_activated: int
    entry_never_reached: int
    clean: int
    censored: int
    ambiguous: int
    data_loss: int
    untrackable: int
    unresolved: int
    tp1_before_sl: int
    sl_before_tp1: int
    tp2_before_sl: int
    tp1_then_tp2: int
    tp1_then_sl: int
    mean_mfe_r: float | None
    median_mfe_r: float | None
    mean_mae_r: float | None
    median_mae_r: float | None
    mean_result_r_unmanaged: float | None
    mean_result_r_tp1_partial: float | None
    entry_activation_rate: float | None
    entry_activation_rate_ci: tuple[float | None, float | None]
    tp1_before_sl_rate: float | None
    tp1_before_sl_rate_ci: tuple[float | None, float | None]
    tp2_before_sl_rate: float | None
    tp2_before_sl_rate_ci: tuple[float | None, float | None]
    sl_before_tp1_rate: float | None
    sl_before_tp1_rate_ci: tuple[float | None, float | None]
    tp1_then_tp2_rate: float | None
    tp1_then_sl_rate: float | None
    sweep_weighted: bool

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self.__dict__)
        for name in (
            "entry_activation_rate_ci",
            "tp1_before_sl_rate_ci",
            "tp2_before_sl_rate_ci",
            "sl_before_tp1_rate_ci",
        ):
            low, high = payload[name]
            payload[name] = {"low": low, "high": high}
        return payload


def _rate(successes: int, total: int) -> float | None:
    if total <= 0:
        return None
    return successes / total


def _sweep_weighted_variants(records: Sequence[VariantRecord]) -> list[VariantRecord]:
    """Pick one representative variant per sweep.

    Weighting each sweep once is the difference between "84 of 100 setups" and
    "84 of 100 branch permutations of 12 setups".  The representative is the
    resolved variant the AI actually decided on where one exists, then the
    earliest-observed resolved variant, so the choice is deterministic and not a
    function of which branch happened to be written first.
    """

    by_sweep: dict[str, list[VariantRecord]] = defaultdict(list)
    for record in records:
        by_sweep[record.sweep_opportunity_id or record.candidate_variant_id].append(record)
    chosen: list[VariantRecord] = []
    for _, group in sorted(by_sweep.items()):
        decided = [row for row in group if row.decision_state in DECISION_STATES]
        pool = decided or group
        resolved = [row for row in pool if row.terminal_event]
        pool = resolved or pool
        pool = sorted(
            pool,
            key=lambda row: (
                row.observed_at if row.observed_at is not None else 0,
                row.candidate_variant_id,
            ),
        )
        chosen.append(pool[0])
    return chosen


def summarize_group(
    key: str,
    records: Sequence[VariantRecord],
    *,
    weight_by_sweep: bool = True,
) -> GroupStats:
    population = _sweep_weighted_variants(records) if weight_by_sweep else list(records)
    unique_sweeps = len({row.sweep_opportunity_id for row in records if row.sweep_opportunity_id})
    resolved = [row for row in population if row.terminal_event]
    clean = [row for row in resolved if row.sample_class == "CLEAN"]
    censored = [row for row in resolved if row.sample_class == "RIGHT_CENSORED"]
    ambiguous = [row for row in resolved if row.sample_class == "EXCLUDED_AMBIGUOUS"]
    data_loss = [row for row in resolved if row.sample_class == "EXCLUDED_DATA_LOSS"]
    untrackable = [row for row in resolved if row.sample_class == "EXCLUDED_INVALID_CONTRACT"]
    never = [row for row in resolved if row.sample_class == "ENTRY_NEVER_REACHED"]
    unresolved = [row for row in population if not row.terminal_event]

    # The activation denominator is every resolved, trackable variant: a
    # data-loss or invalid-contract row never had a decidable activation.
    activation_population = clean + censored + ambiguous + never
    activated = [row for row in activation_population if row.entry_activated]

    tp1_before_sl = sum(1 for row in clean if row.tp1_before_sl is True)
    sl_before_tp1 = sum(1 for row in clean if row.sl_before_tp1 is True)
    tp2_before_sl = sum(1 for row in clean if row.tp2_before_sl is True)
    tp1_then_tp2 = sum(1 for row in clean if row.tp1_then_tp2 is True)
    tp1_then_sl = sum(1 for row in clean if row.tp1_then_sl is True)

    mfe = [row.mfe_r for row in clean + censored if row.mfe_r is not None]
    mae = [row.mae_r for row in clean + censored if row.mae_r is not None]
    result_unmanaged = [
        row.result_r_unmanaged for row in clean + censored if row.result_r_unmanaged is not None
    ]
    result_partial = [
        row.result_r_with_configured_tp1_partial
        for row in clean + censored
        if row.result_r_with_configured_tp1_partial is not None
    ]

    tp1_denominator = sum(1 for row in clean if row.tp1_before_sl is not None or row.sl_before_tp1 is not None)

    return GroupStats(
        key=key,
        variants=len(records),
        unique_sweeps=unique_sweeps,
        entry_activated=len(activated),
        entry_never_reached=len(never),
        clean=len(clean),
        censored=len(censored),
        ambiguous=len(ambiguous),
        data_loss=len(data_loss),
        untrackable=len(untrackable),
        unresolved=len(unresolved),
        tp1_before_sl=tp1_before_sl,
        sl_before_tp1=sl_before_tp1,
        tp2_before_sl=tp2_before_sl,
        tp1_then_tp2=tp1_then_tp2,
        tp1_then_sl=tp1_then_sl,
        mean_mfe_r=_mean(mfe),
        median_mfe_r=_median(mfe),
        mean_mae_r=_mean(mae),
        median_mae_r=_median(mae),
        mean_result_r_unmanaged=_mean(result_unmanaged),
        mean_result_r_tp1_partial=_mean(result_partial),
        entry_activation_rate=_rate(len(activated), len(activation_population)),
        entry_activation_rate_ci=wilson_interval(len(activated), len(activation_population)),
        tp1_before_sl_rate=_rate(tp1_before_sl, tp1_denominator),
        tp1_before_sl_rate_ci=wilson_interval(tp1_before_sl, tp1_denominator),
        tp2_before_sl_rate=_rate(tp2_before_sl, len(clean)),
        tp2_before_sl_rate_ci=wilson_interval(tp2_before_sl, len(clean)),
        sl_before_tp1_rate=_rate(sl_before_tp1, tp1_denominator),
        sl_before_tp1_rate_ci=wilson_interval(sl_before_tp1, tp1_denominator),
        tp1_then_tp2_rate=_rate(tp1_then_tp2, max(1, tp1_before_sl)) if tp1_before_sl else None,
        tp1_then_sl_rate=_rate(tp1_then_sl, max(1, tp1_before_sl)) if tp1_before_sl else None,
        sweep_weighted=weight_by_sweep,
    )


def _abstain_reason_tokens(record: VariantRecord) -> list[str]:
    """Reason codes attached to an abstain/reject, as separable categories."""

    tokens: list[str] = []
    for source in (record.rejection_reason, record.python_decision_reasons, record.mql_decision_reasons):
        text = source.strip()
        if not text:
            continue
        for piece in text.replace(";", ",").replace("|", ",").split(","):
            token = piece.strip().strip('"').strip("'")
            if not token:
                continue
            if len(token) > 96:
                token = token[:96]
            tokens.append(token)
    return tokens


_MISSING_CONFIRMATION_MARKERS = (
    "missing_mandatory_evidence",
    "missing_confirmation",
    "missing_structure",
    "missing_required_evidence",
    "structural_contradiction",
    "sequence_contradiction",
    "data_integrity_failure",
    "target_arbitration_incoherent",
    "execution_plan_mismatch",
)


def _missing_confirmation_categories(record: VariantRecord) -> list[str]:
    haystack = " ".join(
        (record.rejection_reason, record.python_decision_reasons, record.mql_decision_reasons)
    ).lower()
    return [marker for marker in _MISSING_CONFIRMATION_MARKERS if marker in haystack]


def aggregate_shadow_outcomes(records: Sequence[VariantRecord]) -> dict[str, Any]:
    """Every aggregate the counterfactual study is required to report."""

    def group_by(fn) -> dict[str, GroupStats]:
        buckets: dict[str, list[VariantRecord]] = defaultdict(list)
        for record in records:
            for key in fn(record):
                buckets[key].append(record)
        return {key: summarize_group(key, rows) for key, rows in sorted(buckets.items())}

    by_decision = group_by(lambda row: [row.decision_state or "NO_DECISION"])
    by_family = group_by(lambda row: [row.family or "unknown_family"])
    by_family_decision = group_by(
        lambda row: [f"{row.family or 'unknown_family'}|{row.decision_state or 'NO_DECISION'}"]
    )
    by_family_session = group_by(
        lambda row: [f"{row.family or 'unknown_family'}|{row.session or 'unknown_session'}"]
    )
    by_family_branch = group_by(
        lambda row: [f"{row.family or 'unknown_family'}|{row.entry_branch or 'unknown_branch'}"]
    )
    by_abstain_reason = group_by(
        lambda row: [
            f"ABSTAIN|{token}" for token in _abstain_reason_tokens(row)
        ]
        if row.decision_state == "ABSTAIN"
        else []
    )
    by_missing_confirmation = group_by(_missing_confirmation_categories)

    overall = summarize_group("ALL", records)
    return {
        "analysis_version": SHADOW_LEDGER_ANALYSIS_VERSION,
        "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
        "trading_authority": False,
        "authority": "DIAGNOSTIC_SHADOW_ONLY",
        "total_variants": len(records),
        "total_unique_sweeps": overall.unique_sweeps,
        "overall": overall.as_dict(),
        "by_decision_state": {key: value.as_dict() for key, value in by_decision.items()},
        "by_family": {key: value.as_dict() for key, value in by_family.items()},
        "by_family_decision_state": {key: value.as_dict() for key, value in by_family_decision.items()},
        "by_family_session": {key: value.as_dict() for key, value in by_family_session.items()},
        "by_family_entry_branch": {key: value.as_dict() for key, value in by_family_branch.items()},
        "by_abstain_reason": {key: value.as_dict() for key, value in by_abstain_reason.items()},
        "by_missing_confirmation": {key: value.as_dict() for key, value in by_missing_confirmation.items()},
        "abstain_analysis": analyze_abstain_performance(records),
    }


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def analyze_abstain_performance(records: Sequence[VariantRecord]) -> dict[str, Any]:
    """What the ABSTAIN decisions actually did, and whether the reasons separate."""

    by_state = {state: [row for row in records if row.decision_state == state] for state in DECISION_STATES}
    stats = {state: summarize_group(state, rows) for state, rows in by_state.items()}

    abstained = by_state["ABSTAIN"]
    abstain_clean = [row for row in _sweep_weighted_variants(abstained) if row.sample_class == "CLEAN"]
    missed_tp1 = sum(1 for row in abstain_clean if row.tp1_before_sl is True)
    missed_tp2 = sum(1 for row in abstain_clean if row.tp2_before_sl is True)
    avoided_sl = sum(1 for row in abstain_clean if row.sl_before_tp1 is True)

    by_family: dict[str, GroupStats] = {}
    families = sorted({row.family or "unknown_family" for row in abstained})
    for family in families:
        rows = [row for row in abstained if (row.family or "unknown_family") == family]
        by_family[family] = summarize_group(family, rows)

    reason_separation: dict[str, Any] = {}
    reason_rows: dict[str, list[VariantRecord]] = defaultdict(list)
    for row in abstained:
        for token in set(_abstain_reason_tokens(row)):
            reason_rows[token].append(row)
    baseline = stats["ABSTAIN"]
    for token, rows in sorted(reason_rows.items()):
        group = summarize_group(token, rows)
        # "Separates" means the reason's TP2-before-SL interval does not overlap
        # the abstain baseline's.  Non-overlap on Wilson intervals is a weak but
        # honest signal; it is reported as evidence, never as a threshold to act
        # on automatically.
        separates: bool | None = None
        low, high = group.tp2_before_sl_rate_ci
        base_low, base_high = baseline.tp2_before_sl_rate_ci
        if None not in (low, high, base_low, base_high) and group.clean >= EVIDENCE_MIN_CLEAN_SAMPLES:
            separates = bool(high < base_low or low > base_high)
        reason_separation[token] = {
            "stats": group.as_dict(),
            "separates_favorable_from_unfavorable": separates,
            "separation_basis": "wilson_interval_non_overlap_vs_abstain_baseline",
        }

    return {
        "decision_state_stats": {state: value.as_dict() for state, value in stats.items()},
        "abstain_clean_samples": len(abstain_clean),
        "missed_tp1_opportunities": missed_tp1,
        "missed_tp2_opportunities": missed_tp2,
        "avoided_sl_outcomes": avoided_sl,
        "abstain_by_family": {key: value.as_dict() for key, value in by_family.items()},
        "approve_minus_abstain": {
            "tp2_before_sl_rate": _delta(
                stats["APPROVE"].tp2_before_sl_rate, stats["ABSTAIN"].tp2_before_sl_rate
            ),
            "tp1_before_sl_rate": _delta(
                stats["APPROVE"].tp1_before_sl_rate, stats["ABSTAIN"].tp1_before_sl_rate
            ),
            "mean_result_r_unmanaged": _delta(
                stats["APPROVE"].mean_result_r_unmanaged, stats["ABSTAIN"].mean_result_r_unmanaged
            ),
        },
        "reject_minus_abstain": {
            "tp2_before_sl_rate": _delta(
                stats["REJECT"].tp2_before_sl_rate, stats["ABSTAIN"].tp2_before_sl_rate
            ),
            "tp1_before_sl_rate": _delta(
                stats["REJECT"].tp1_before_sl_rate, stats["ABSTAIN"].tp1_before_sl_rate
            ),
            "mean_result_r_unmanaged": _delta(
                stats["REJECT"].mean_result_r_unmanaged, stats["ABSTAIN"].mean_result_r_unmanaged
            ),
        },
        "abstain_reason_separation": reason_separation,
    }


# --------------------------------------------------------------------------
# Leakage-safe historical evidence
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidencePolicy:
    """Configuration switch and promotion gate for calibrated shadow evidence."""

    enabled: bool = True
    min_clean_samples: int = EVIDENCE_MIN_CLEAN_SAMPLES
    min_sweeps: int = EVIDENCE_MIN_SWEEPS
    trading_authority_enabled: bool = False
    out_of_sample_validated: bool = False
    era_floor_ts: int = EVIDENCE_ERA_FLOOR_TS
    ladder_enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EvidencePolicy":
        source = env if env is not None else os.environ

        def flag(name: str, default: bool) -> bool:
            raw = str(source.get(name, "")).strip().lower()
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
            return default

        def integer(name: str, default: int) -> int:
            raw = str(source.get(name, "")).strip()
            if not raw:
                return default
            try:
                return max(1, int(raw))
            except ValueError:
                return default

        def epoch(name: str, default: int) -> int:
            """An era floor of 0 disables the filter; a negative value is refused.

            ``integer`` above clamps to 1, which would make "0 means no floor"
            unreachable, so the floor parses its own value.
            """

            raw = str(source.get(name, "")).strip()
            if not raw:
                return default
            try:
                parsed = int(raw)
            except ValueError:
                return default
            return max(0, parsed)

        return cls(
            enabled=flag("AI_SHADOW_EVIDENCE_ENABLE", True),
            min_clean_samples=integer("AI_SHADOW_EVIDENCE_MIN_SAMPLES", EVIDENCE_MIN_CLEAN_SAMPLES),
            min_sweeps=integer("AI_SHADOW_EVIDENCE_MIN_SWEEPS", EVIDENCE_MIN_SWEEPS),
            trading_authority_enabled=flag("AI_SHADOW_EVIDENCE_TRADING_AUTHORITY", False),
            out_of_sample_validated=flag("AI_SHADOW_EVIDENCE_OOS_VALIDATED", False),
            era_floor_ts=epoch("AI_SHADOW_EVIDENCE_ERA_FLOOR_TS", EVIDENCE_ERA_FLOOR_TS),
            ladder_enabled=flag("AI_SHADOW_EVIDENCE_LADDER_ENABLE", True),
        )


def promotion_gate_state(stats: GroupStats, policy: EvidencePolicy) -> dict[str, Any]:
    """Why this evidence is (or is not) allowed to carry trading authority."""

    low, high = stats.tp2_before_sl_rate_ci
    ci_width = None if None in (low, high) else float(high) - float(low)
    checks = {
        "min_clean_samples": stats.clean >= PROMOTION_MIN_CLEAN_SAMPLES,
        "min_unique_sweeps": stats.unique_sweeps >= PROMOTION_MIN_SWEEPS,
        "max_ci_width": ci_width is not None and ci_width <= PROMOTION_MAX_CI_WIDTH,
        "out_of_sample_validated": (not PROMOTION_REQUIRES_OUT_OF_SAMPLE) or policy.out_of_sample_validated,
        "operator_switch_enabled": policy.trading_authority_enabled,
    }
    satisfied = all(checks.values())
    return {
        "checks": checks,
        "ci_width": ci_width,
        "requirements": {
            "min_clean_samples": PROMOTION_MIN_CLEAN_SAMPLES,
            "min_unique_sweeps": PROMOTION_MIN_SWEEPS,
            "max_ci_width": PROMOTION_MAX_CI_WIDTH,
            "requires_out_of_sample": PROMOTION_REQUIRES_OUT_OF_SAMPLE,
            "operator_switch": "AI_SHADOW_EVIDENCE_TRADING_AUTHORITY",
        },
        "satisfied": satisfied,
        # Even a satisfied gate only permits the evidence to be labelled
        # authoritative.  It never approves a trade by itself: the AI gate, the
        # deterministic gates and the risk controls are all still in the path.
        "authority": "CALIBRATED_ADVISORY" if satisfied else "DIAGNOSTIC_SHADOW_ONLY",
    }


def historical_evidence(
    records: Sequence[VariantRecord],
    *,
    decision_timestamp: int,
    family: str = "",
    setup_taxonomy: str = "",
    entry_branch: str = "",
    session: str = "",
    symbol: str = "",
    decision_state: str = "ABSTAIN",
    policy: EvidencePolicy | None = None,
) -> dict[str, Any]:
    """Prior outcomes retrievable for one decision, with no look-ahead.

    Only variants whose terminal event is strictly earlier than
    ``decision_timestamp`` are eligible.  Unresolved variants and variants that
    resolved at or after the decision are excluded rather than imputed, because
    "still open" at decision time is not evidence of anything.
    """

    active = policy or EvidencePolicy()
    if not active.enabled:
        return {
            "state": "DISABLED",
            "authority": "DIAGNOSTIC_SHADOW_ONLY",
            "trading_authority": False,
            "reason": "shadow_historical_evidence_disabled_by_configuration",
            "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
        }

    def matches(record: VariantRecord) -> bool:
        if family and (record.family or "") != family:
            return False
        if setup_taxonomy and (record.setup_taxonomy or "") != setup_taxonomy:
            return False
        if entry_branch and (record.entry_branch or "") != entry_branch:
            return False
        if session and (record.session or "") != session:
            return False
        if symbol and (record.symbol or "") != symbol:
            return False
        if decision_state and (record.decision_state or "") != decision_state:
            return False
        # A decision state asked for by name must be an AI decision.  An error
        # envelope reaches MT5 carrying APPROVE/REJECT/ABSTAIN by schema
        # requirement, so a query for "ABSTAIN" would otherwise pick up
        # infrastructure failures and report them as the AI's judgement -- in a
        # field that is fed back into future AI decisions.  ``None`` (a ledger
        # written before the tier was recorded) stays eligible so old rows are
        # not silently dropped; only a row that says it was NOT trading grade is
        # refused.
        if decision_state in DECISION_STATES and record.trading_tier is False:
            return False
        return True

    eligible: list[VariantRecord] = []
    excluded_future = 0
    excluded_unresolved = 0
    excluded_non_trading_tier = 0
    excluded_before_era_floor = 0
    era_floor = max(0, int(active.era_floor_ts))
    for record in records:
        if (
            decision_state in DECISION_STATES
            and record.trading_tier is False
            and (record.decision_state or "") == decision_state
        ):
            excluded_non_trading_tier += 1
        if not matches(record):
            continue
        # The era floor is applied to the OBSERVATION, not the resolution: what
        # dates a sample to an engine build is when the plan was constructed,
        # not when price happened to finish walking it out.
        if era_floor and (record.observed_at is None or record.observed_at < era_floor):
            excluded_before_era_floor += 1
            continue
        if not record.terminal_event or record.terminal_event_at is None:
            excluded_unresolved += 1
            continue
        if record.terminal_event_at >= decision_timestamp:
            excluded_future += 1
            continue
        eligible.append(record)

    stats = summarize_group("historical", eligible)
    gate = promotion_gate_state(stats, active)
    sufficient = stats.clean >= active.min_clean_samples and stats.unique_sweeps >= active.min_sweeps
    state = "SUFFICIENT_SAMPLE" if sufficient else "INSUFFICIENT_SAMPLE"

    narrative = ""
    if sufficient:
        scope_label = "this family and setup taxonomy" if setup_taxonomy else "this family"
        narrative = (
            f"For {scope_label}, comparable {decision_state or 'ANY'} decisions: "
            f"{stats.clean} clean samples, "
            f"TP1-before-SL {_fmt_rate(stats.tp1_before_sl_rate)}, "
            f"TP2-before-SL {_fmt_rate(stats.tp2_before_sl_rate)}, "
            f"SL-first {_fmt_rate(stats.sl_before_tp1_rate)}, "
            f"average unmanaged R {_fmt_number(stats.mean_result_r_unmanaged)}."
        )

    return {
        "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
        "analysis_version": SHADOW_LEDGER_ANALYSIS_VERSION,
        "state": state,
        "authority": gate["authority"],
        "trading_authority": bool(gate["satisfied"]) and active.trading_authority_enabled,
        "decision_timestamp": int(decision_timestamp),
        "leakage_policy": "terminal_event_at_strictly_before_decision_timestamp",
        "query": {
            "family": family,
            "setup_taxonomy": setup_taxonomy,
            "entry_branch": entry_branch,
            "session": session,
            "symbol": symbol,
            "decision_state": decision_state,
        },
        "minimum_clean_samples": active.min_clean_samples,
        "minimum_unique_sweeps": active.min_sweeps,
        "excluded_unresolved": excluded_unresolved,
        "excluded_not_yet_terminal_at_decision_time": excluded_future,
        "excluded_non_trading_tier_decision": excluded_non_trading_tier,
        "excluded_before_era_floor": excluded_before_era_floor,
        "era_floor_ts": era_floor,
        "era_policy": (
            "observed_at_at_or_after_era_floor" if era_floor else "no_engine_era_floor"
        ),
        "stats": stats.as_dict(),
        "promotion_gate": gate,
        "narrative": narrative,
    }


def _ladder_block_scope(
    rung: str,
    *,
    family: str,
    setup_taxonomy: str,
    decision_state: str,
) -> dict[str, str]:
    """The exact query one ladder block ran, for the payload's audit trail."""

    axes = dict(EVIDENCE_LADDER_RUNGS).get(rung, ())
    return {
        "rung": rung,
        "family": family if "family" in axes else "",
        "setup_taxonomy": setup_taxonomy if "setup_taxonomy" in axes else "",
        "decision_state": decision_state,
    }


def historical_evidence_ladder(
    records: Sequence[VariantRecord],
    *,
    decision_timestamp: int,
    family: str = "",
    setup_taxonomy: str = "",
    decision_state: str = "ABSTAIN",
    policy: EvidencePolicy | None = None,
) -> dict[str, Any]:
    """Widen the historical query by one axis at a time until a rung answers.

    Three properties are deliberate, and each of them exists because the naive
    version of this function is wrong:

    * **No pooling across decision states.**  A REJECT sample is a candidate the
      AI refused and an ABSTAIN sample is one it declined to back; averaging them
      into a single "family rate" mixes two differently-selected populations and
      reports the mixture as if it described either.  Every rate returned here is
      computed inside one decision state, and a rung's sufficiency is likewise
      decided per state -- the nisab can never be reached by adding populations
      together.

    * **No rung for never-assessed candidates.**  Rows carrying
      ``PENDING_DECISION`` / ``NOT_ASSESSED`` are the largest pool on disk, and
      they are excluded on purpose: ``decision_source=NOT_YET_DECIDED`` means the
      request's decision was never attributed back to the candidate.  That is an
      attribution defect, and feeding it in as "evidence" would launder a defect
      into a statistic the model then reasons from.

    * **Widening never grants authority.**  Each block carries the promotion gate
      computed from its own sample; the gate's requirements are constants, so a
      broader match cannot make an unpromoted statistic authoritative.
    """

    active = policy or EvidencePolicy()

    def evaluate(rung: str, state: str) -> dict[str, Any]:
        axes = dict(EVIDENCE_LADDER_RUNGS)[rung]
        block = historical_evidence(
            records,
            decision_timestamp=decision_timestamp,
            family=family if "family" in axes else "",
            setup_taxonomy=setup_taxonomy if "setup_taxonomy" in axes else "",
            decision_state=state,
            policy=active,
        )
        block["match_specificity"] = rung
        block["match_scope"] = _ladder_block_scope(
            rung, family=family, setup_taxonomy=setup_taxonomy, decision_state=state
        )
        return block

    exact = evaluate(EVIDENCE_LADDER_RUNGS[0][0], decision_state)
    exact["ladder_enabled"] = bool(active.ladder_enabled)
    if not active.enabled or not active.ladder_enabled:
        # Disabled ladder is exactly the pre-ladder behaviour, reported as such
        # rather than silently looking like a rung that happened to answer.
        exact["ladder_rungs_tried"] = [_ladder_summary(exact)]
        exact["ladder_broadest_rung_tried"] = EVIDENCE_LADDER_RUNGS[0][0]
        return exact

    # The asked-for state is always tried first at every rung, so a widened
    # answer prefers the state the caller actually asked about.
    states: list[str] = [decision_state] + [
        state for state in AI_DECIDED_STATES if state != decision_state
    ]

    tried: list[dict[str, Any]] = []
    answer: dict[str, Any] | None = None
    broadest = EVIDENCE_LADDER_RUNGS[0][0]

    for rung, _axes in EVIDENCE_LADDER_RUNGS:
        broadest = rung
        rung_blocks: list[dict[str, Any]] = []
        for state in states:
            block = exact if (rung == EVIDENCE_LADDER_RUNGS[0][0] and state == decision_state) else evaluate(rung, state)
            rung_blocks.append(block)
            tried.append(_ladder_summary(block))
            if answer is None and block.get("state") == "SUFFICIENT_SAMPLE":
                answer = block
            # The first rung only ever asks about the requested state: widening
            # the state is what rung 2 is for.
            if rung == EVIDENCE_LADDER_RUNGS[0][0]:
                break
        if answer is not None:
            # Sibling blocks from the answering rung travel with the answer so
            # the model sees the other states' rates instead of one number.
            answer["ladder_sibling_blocks"] = [
                _ladder_summary(block) for block in rung_blocks if block is not answer
            ]
            break

    result = answer if answer is not None else exact
    result["ladder_enabled"] = True
    result["ladder_rungs_tried"] = tried
    result["ladder_broadest_rung_tried"] = broadest
    result.setdefault("ladder_sibling_blocks", [])
    result.setdefault("match_specificity", EVIDENCE_LADDER_RUNGS[0][0])
    result.setdefault(
        "match_scope",
        _ladder_block_scope(
            EVIDENCE_LADDER_RUNGS[0][0],
            family=family,
            setup_taxonomy=setup_taxonomy,
            decision_state=decision_state,
        ),
    )
    return result


def _ladder_summary(block: Mapping[str, Any]) -> dict[str, Any]:
    """One rung's outcome, small enough to travel inside the payload."""

    stats = block.get("stats") if isinstance(block.get("stats"), Mapping) else {}
    scope = block.get("match_scope") if isinstance(block.get("match_scope"), Mapping) else {}
    return {
        "match_specificity": block.get("match_specificity", ""),
        "decision_state": scope.get("decision_state", ""),
        "state": block.get("state", ""),
        "clean_samples": stats.get("clean", 0),
        "unique_sweeps": stats.get("unique_sweeps", 0),
        "tp2_before_sl_rate": stats.get("tp2_before_sl_rate"),
        "mean_result_r_unmanaged": stats.get("mean_result_r_unmanaged"),
    }


SHADOW_CANDIDATE_EVIDENCE_FIELDS: tuple[str, ...] = (
    "schema_version",
    "state",
    "authority",
    "trading_authority",
    "decision_state_compared",
    "match_specificity",
    "match_family",
    "match_setup_taxonomy",
    "ladder_broadest_rung_tried",
    "era_floor_ts",
    "era_policy",
    "excluded_before_era_floor",
    "clean_samples",
    "unique_sweeps",
    "censored_samples",
    "ambiguous_samples",
    "entry_never_reached_samples",
    "entry_activation_rate",
    "tp1_before_sl_rate",
    "tp2_before_sl_rate",
    "sl_before_tp1_rate",
    "tp1_then_tp2_rate",
    "tp1_then_sl_rate",
    "tp2_before_sl_rate_ci_low",
    "tp2_before_sl_rate_ci_high",
    "mean_result_r_unmanaged",
    "mean_result_r_with_configured_tp1_partial",
    "mean_mfe_r",
    "mean_mae_r",
    "minimum_clean_samples",
    "leakage_policy",
    "narrative",
)


def compact_candidate_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten one ``historical_evidence`` result into citable scalars.

    The evidence catalog can only issue an evidence id for a scalar, and a model
    is required to cite the id of anything it relies on.  A nested object would
    therefore be visible but uncitable, which is the same failure mode as
    presenting evidence the model is forbidden to reference.
    """

    stats = evidence.get("stats") if isinstance(evidence.get("stats"), Mapping) else {}
    ci = stats.get("tp2_before_sl_rate_ci") if isinstance(stats.get("tp2_before_sl_rate_ci"), Mapping) else {}
    query = evidence.get("query") if isinstance(evidence.get("query"), Mapping) else {}
    compact = {
        "schema_version": evidence.get("schema_version", SHADOW_LEDGER_SCHEMA_VERSION),
        "state": evidence.get("state", "INSUFFICIENT_SAMPLE"),
        "authority": evidence.get("authority", "DIAGNOSTIC_SHADOW_ONLY"),
        "trading_authority": bool(evidence.get("trading_authority", False)),
        "decision_state_compared": query.get("decision_state", ""),
        # How specific the match behind these numbers is.  A widened match is
        # still real evidence, but it is evidence about a wider population, and
        # the model is required to be able to say which it read.
        "match_specificity": evidence.get("match_specificity", EVIDENCE_LADDER_RUNGS[0][0]),
        "match_family": query.get("family", ""),
        "match_setup_taxonomy": query.get("setup_taxonomy", ""),
        "ladder_broadest_rung_tried": evidence.get(
            "ladder_broadest_rung_tried", evidence.get("match_specificity", "")
        ),
        "era_floor_ts": evidence.get("era_floor_ts", EVIDENCE_ERA_FLOOR_TS),
        "era_policy": evidence.get("era_policy", ""),
        "excluded_before_era_floor": evidence.get("excluded_before_era_floor", 0),
        "clean_samples": stats.get("clean", 0),
        "unique_sweeps": stats.get("unique_sweeps", 0),
        "censored_samples": stats.get("censored", 0),
        "ambiguous_samples": stats.get("ambiguous", 0),
        "entry_never_reached_samples": stats.get("entry_never_reached", 0),
        "entry_activation_rate": stats.get("entry_activation_rate"),
        "tp1_before_sl_rate": stats.get("tp1_before_sl_rate"),
        "tp2_before_sl_rate": stats.get("tp2_before_sl_rate"),
        "sl_before_tp1_rate": stats.get("sl_before_tp1_rate"),
        "tp1_then_tp2_rate": stats.get("tp1_then_tp2_rate"),
        "tp1_then_sl_rate": stats.get("tp1_then_sl_rate"),
        "tp2_before_sl_rate_ci_low": ci.get("low"),
        "tp2_before_sl_rate_ci_high": ci.get("high"),
        "mean_result_r_unmanaged": stats.get("mean_result_r_unmanaged"),
        "mean_result_r_with_configured_tp1_partial": stats.get("mean_result_r_tp1_partial"),
        "mean_mfe_r": stats.get("mean_mfe_r"),
        "mean_mae_r": stats.get("mean_mae_r"),
        "minimum_clean_samples": evidence.get("minimum_clean_samples", EVIDENCE_MIN_CLEAN_SAMPLES),
        "leakage_policy": evidence.get("leakage_policy", ""),
        "narrative": evidence.get("narrative", ""),
    }
    return {key: compact[key] for key in SHADOW_CANDIDATE_EVIDENCE_FIELDS if key in compact}


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value * 100.0:.1f}%"


def _fmt_number(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.3f}"


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


def ledger_time_base(read: LedgerRead) -> int:
    """The last moment the tracker is known to have been running.

    Wall-clock time is the wrong base for a ledger produced by the Strategy
    Tester: its timestamps are simulated, so every open tracker would be
    reported as expired simply because the simulated period is in the past.
    The latest event in the file is correct for both a tester ledger and a live
    one, and it makes "expired pending" mean what it should -- the tracker was
    still emitting events at T and this record's horizon had already passed.
    """

    latest = 0
    for row in read.rows:
        value = _integer(row.get("event_at")) or 0
        if value > latest:
            latest = value
    return latest


def audit_ledger(read: LedgerRead, consolidation: Consolidation, *, now: int | None = None) -> dict[str, Any]:
    """Everything a maintainer needs to trust or distrust the ledger."""

    time_base = "explicit" if now is not None else "latest_ledger_event"
    current = int(now if now is not None else ledger_time_base(read))
    duplicates = [row for row in consolidation.rejected if row["reason"] == "duplicate_candidate_variant_observation"]
    orphan_updates = [
        row
        for row in consolidation.rejected
        if row["reason"] == "shadow_delta_parent_missing"
        and row["event_type"] not in {EVENT_TERMINAL_RESOLUTION}
    ]
    orphan_outcomes = [
        row
        for row in consolidation.rejected
        if row["reason"] == "shadow_delta_parent_missing" and row["event_type"] == EVENT_TERMINAL_RESOLUTION
    ]
    fingerprint_mismatches = [
        row
        for row in consolidation.rejected
        if row["reason"]
        in {
            "shadow_delta_execution_fingerprint_mismatch",
            "shadow_delta_candidate_hash_mismatch",
            "shadow_delta_parent_record_hash_mismatch",
        }
    ]
    conflicting = [row for row in consolidation.conflicts if row["reason"] == "conflicting_terminal_outcome"]
    duplicate_terminals = [row for row in consolidation.conflicts if row["reason"] == "duplicate_terminal_outcome"]

    expired_pending = []
    missing_attribution = []
    for record in consolidation.variants:
        if not record.terminal_event:
            if current and record.horizon_at is not None and record.horizon_at < current:
                expired_pending.append(
                    {
                        "candidate_variant_id": record.candidate_variant_id,
                        "symbol": record.symbol,
                        "horizon_at": record.horizon_at,
                        "overdue_seconds": current - record.horizon_at,
                    }
                )
            continue
        if record.decision_state in ("", "PENDING_DECISION", "UNAVAILABLE", "INVALID"):
            missing_attribution.append(
                {
                    "candidate_variant_id": record.candidate_variant_id,
                    "symbol": record.symbol,
                    "decision_stage": record.decision_stage,
                    "decision_state": record.decision_state,
                }
            )

    orphan_variants = [
        record.candidate_variant_id
        for record in consolidation.variants
        if record.sweep_opportunity_id and record.sweep_opportunity_id not in consolidation.opportunities
    ]

    findings = {
        "duplicate_observations": duplicates,
        "orphaned_updates": orphan_updates,
        "orphaned_outcomes": orphan_outcomes,
        "expired_pending_records": expired_pending,
        "conflicting_terminal_outcomes": conflicting,
        "duplicate_terminal_outcomes": duplicate_terminals,
        "fingerprint_mismatches": fingerprint_mismatches,
        "missing_ai_decision_attribution": missing_attribution,
        "variants_without_opportunity_record": orphan_variants,
        "unparseable_lines": read.unparseable,
    }
    problem_count = sum(len(value) for value in findings.values())
    return {
        "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
        "analysis_version": SHADOW_LEDGER_ANALYSIS_VERSION,
        "total_lines": read.total_lines,
        "time_base": time_base,
        "evaluated_at": current,
        "legacy_v3_rows_ignored": read.legacy_rows,
        "events_parsed": len(read.rows),
        "variants": len(consolidation.variants),
        "opportunities": len(consolidation.opportunities),
        "terminal_resolutions": sum(1 for row in consolidation.variants if row.terminal_event),
        "rejected_rows": len(consolidation.rejected),
        "healthy": problem_count == 0,
        "problem_count": problem_count,
        "findings": findings,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def default_ledger_path() -> Path:
    override = os.environ.get("PO3_SHADOW_LEDGER_PATH", "").strip()
    if override:
        return Path(override)
    bus_root = os.environ.get("PO3_BUS_ROOT", "").strip()
    if bus_root:
        return Path(bus_root) / "logs" / "shadow_candidates.jsonl"
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return (
            Path(appdata)
            / "MetaQuotes"
            / "Terminal"
            / "Common"
            / "Files"
            / "PO3_AI_BUS"
            / "logs"
            / "shadow_candidates.jsonl"
        )
    return Path("shadow_candidates.jsonl")


def default_runtime_state_root(ledger: str | Path | None = None) -> Path:
    """The ``runtime_state`` directory that holds the per-scope tracker files."""

    override = os.environ.get("PO3_RUNTIME_STATE_ROOT", "").strip()
    if override:
        return Path(override)
    return (Path(ledger) if ledger else default_ledger_path()).parent / "runtime_state"


# A pre-v4 tracker schema, recognised by its family rather than by a hardcoded
# date: the observed live value is "20260717_shadow_candidate_v3", and pinning
# the exact string would classify the next old cohort as corruption.
LEGACY_PENDING_SCHEMA_MARKERS: tuple[str, ...] = ("shadow_candidate", "shadow_lifecycle")

QUARANTINE_FILENAME = "shadow_tracker_quarantine.ndjson"
PENDING_FILENAME = "shadow_candidate_pending.ndjson"


def _pending_record_state(plan: Mapping[str, Any], *, now: int) -> tuple[str, str]:
    """Classify one persisted tracker: is it addressable by the v4 tracker?"""

    schema = _text(plan.get("shadow_candidate_schema_version"))
    variant = _text(plan.get("shadow_candidate_variant_id"))
    record_hash = _text(plan.get("shadow_candidate_record_hash"))
    entry = _number(plan.get("entry_est"))
    stop = _number(plan.get("sl"))
    target = _number(plan.get("tp2"))

    if schema != SHADOW_LEDGER_SCHEMA_VERSION:
        legacy = any(marker in schema for marker in LEGACY_PENDING_SCHEMA_MARKERS)
        return (
            "LEGACY_SCHEMA" if legacy else "FOREIGN_SCHEMA",
            "incompatible_shadow_schema_version",
        )
    if not variant:
        return ("UNADDRESSABLE", "missing_candidate_variant_identity")
    if not record_hash:
        return ("UNADDRESSABLE", "missing_parent_record_hash")
    if None in (entry, stop, target) or entry == stop or entry <= 0.0:
        return ("UNTRACKABLE", "untrackable_entry_stop_target_contract")
    horizon = _integer(plan.get("shadow_horizon_at")) or 0
    if horizon and horizon < now:
        return ("ADDRESSABLE_OVERDUE", "horizon_passed_awaiting_resolution")
    return ("ADDRESSABLE_PENDING", "within_tracking_horizon")


def reconcile_pending_trackers(
    *,
    runtime_state_root: str | Path | None = None,
    ledger: str | Path | None = None,
    now: int | None = None,
    quarantine: bool = False,
) -> dict[str, Any]:
    """Reconcile every persisted tracker file against the v4 tracker contract.

    Scope orphaning is the failure this exists for: a pending file written under
    one ``runtime_state`` scope (a different magic number, say) is never loaded
    by an EA running under another, so its unresolved candidates are invisible to
    both the tracker and the ledger.  This reports them, and with
    ``quarantine=True`` writes them into that scope's quarantine file in the
    EA's own format so the samples are retained rather than stranded.

    It never deletes a pending file and never fabricates an outcome.
    """

    current = int(now if now is not None else time.time())
    root = Path(runtime_state_root) if runtime_state_root else default_runtime_state_root(ledger)
    scopes: list[dict[str, Any]] = []
    totals: dict[str, int] = defaultdict(int)

    if not root.is_dir():
        return {
            "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
            "runtime_state_root": str(root),
            "state": "RUNTIME_STATE_ROOT_MISSING",
            "scopes": [],
            "totals": {},
            "quarantine_applied": False,
        }

    for scope_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        pending_path = scope_dir / PENDING_FILENAME
        if not pending_path.is_file():
            continue
        raw = pending_path.read_bytes()
        if not raw.strip():
            continue
        try:
            text = decode_ledger_bytes(raw)
        except ShadowLedgerError:
            scopes.append(
                {
                    "scope": scope_dir.name,
                    "pending_path": str(pending_path),
                    "state": "UNREADABLE",
                    "records": 0,
                }
            )
            totals["unreadable_scopes"] += 1
            continue

        records: list[dict[str, Any]] = []
        unparseable = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                plan = json.loads(stripped)
            except json.JSONDecodeError:
                unparseable += 1
                continue
            if not isinstance(plan, Mapping):
                unparseable += 1
                continue
            state, reason = _pending_record_state(plan, now=current)
            records.append(
                {
                    "state": state,
                    "reason": reason,
                    "symbol": _text(plan.get("symbol")),
                    "candidate_variant_id": _text(plan.get("shadow_candidate_variant_id")),
                    "recorded_schema_version": _text(plan.get("shadow_candidate_schema_version")),
                    "observed_at": _integer(plan.get("shadow_observed_at")),
                    "horizon_at": _integer(plan.get("shadow_horizon_at")),
                    "overdue_seconds": max(
                        0, current - (_integer(plan.get("shadow_horizon_at")) or current)
                    ),
                    "plan": plan,
                }
            )

        counts: dict[str, int] = defaultdict(int)
        for record in records:
            counts[record["state"]] += 1
            totals[record["state"]] += 1
        totals["records"] += len(records)
        totals["unparseable"] += unparseable

        retained = [
            record
            for record in records
            if record["state"] in {"LEGACY_SCHEMA", "FOREIGN_SCHEMA", "UNADDRESSABLE", "UNTRACKABLE"}
        ]
        quarantine_path = scope_dir / QUARANTINE_FILENAME
        written = 0
        if quarantine and retained:
            existing = ""
            if quarantine_path.is_file():
                existing = decode_ledger_bytes(quarantine_path.read_bytes())
                if existing and not existing.endswith("\n"):
                    existing += "\n"
            lines = []
            for record in retained:
                plan = record["plan"]
                lines.append(
                    json.dumps(
                        {
                            "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
                            "event_type": "shadow_tracker_quarantined",
                            "quarantined_at": current,
                            "reason": record["reason"],
                            "quarantine_source": "python_reconcile_orphaned_scope",
                            "scope": scope_dir.name,
                            "recorded_schema_version": record["recorded_schema_version"],
                            "candidate_variant_id": record["candidate_variant_id"],
                            "sweep_opportunity_id": _text(plan.get("shadow_sweep_opportunity_id")),
                            "parent_record_hash": _text(plan.get("shadow_candidate_record_hash")),
                            "symbol": record["symbol"],
                            "observed_at": record["observed_at"],
                            "horizon_at": record["horizon_at"],
                            "trading_authority": False,
                            "plan": plan,
                        },
                        sort_keys=True,
                    )
                )
            # Written as UTF-8: the EA reads this file only through
            # CFileBus.ReadText, and the reconciler never deletes the pending
            # file it read, so a partial write costs nothing.
            quarantine_path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")
            written = len(lines)
            totals["quarantined_written"] += written

        scopes.append(
            {
                "scope": scope_dir.name,
                "pending_path": str(pending_path),
                "quarantine_path": str(quarantine_path),
                "state": "READ",
                "records": len(records),
                "unparseable": unparseable,
                "by_state": dict(sorted(counts.items())),
                "retained_for_quarantine": len(retained),
                "quarantine_records_written": written,
                "samples": [
                    {key: value for key, value in record.items() if key != "plan"}
                    for record in records[:5]
                ],
            }
        )

    return {
        "schema_version": SHADOW_LEDGER_SCHEMA_VERSION,
        "runtime_state_root": str(root),
        "state": "RECONCILED",
        "now": current,
        "scopes": scopes,
        "totals": dict(sorted(totals.items())),
        "quarantine_applied": bool(quarantine),
        # Reconciliation is evidence handling, never outcome synthesis: no
        # terminal event is invented for a record that never resolved.
        "terminal_outcomes_written": 0,
        "pending_files_deleted": 0,
    }


def load_and_consolidate(path: str | Path | None = None) -> tuple[LedgerRead, Consolidation]:
    target = Path(path) if path else default_ledger_path()
    read = read_shadow_events(target)
    return read, consolidate_shadow_lifecycle(read.rows)


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shadow_outcome_ledger",
        description="Audit and analyse the PO3 counterfactual (shadow) outcome ledger.",
    )
    parser.add_argument("--ledger", default="", help="Path to shadow_candidates.jsonl")
    parser.add_argument("--out", default="", help="Write the JSON result to this file instead of stdout")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="Report duplicates, orphans, conflicts and expired records")
    sub.add_parser("report", help="Aggregate outcomes by decision state, family, session and branch")
    consolidate = sub.add_parser("consolidate", help="Emit one clean record per candidate variant")
    consolidate.add_argument("--limit", type=int, default=0)
    evidence = sub.add_parser("evidence", help="Leakage-safe historical evidence for one decision")
    evidence.add_argument("--at", type=int, required=True, help="Decision timestamp (epoch seconds)")
    evidence.add_argument("--family", default="")
    evidence.add_argument("--taxonomy", default="")
    evidence.add_argument("--branch", default="")
    evidence.add_argument("--session", default="")
    evidence.add_argument("--symbol", default="")
    evidence.add_argument("--decision-state", default="ABSTAIN")
    reconcile = sub.add_parser(
        "reconcile",
        help="Reconcile persisted pending trackers across every runtime_state scope",
    )
    reconcile.add_argument("--runtime-state", default="", help="runtime_state directory")
    reconcile.add_argument(
        "--quarantine",
        action="store_true",
        help="Write unaddressable records into each scope's quarantine file (never deletes)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "reconcile":
        payload = reconcile_pending_trackers(
            runtime_state_root=args.runtime_state or None,
            ledger=args.ledger or None,
            quarantine=bool(args.quarantine),
        )
        text = _dump(payload)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text + "\n")
        return 0

    read, consolidation = load_and_consolidate(args.ledger or None)

    if args.command == "audit":
        # No explicit ``now``: the audit uses the ledger's own latest event, which
        # is correct for a tester ledger as well as a live one.
        payload: Any = audit_ledger(read, consolidation)
    elif args.command == "report":
        payload = aggregate_shadow_outcomes(consolidation.variants)
    elif args.command == "consolidate":
        rows = [record.as_dict() for record in consolidation.variants]
        if args.limit > 0:
            rows = rows[: args.limit]
        payload = rows
    else:
        payload = historical_evidence(
            consolidation.variants,
            decision_timestamp=args.at,
            family=args.family,
            setup_taxonomy=args.taxonomy,
            entry_branch=args.branch,
            session=args.session,
            symbol=args.symbol,
            decision_state=args.decision_state,
            policy=EvidencePolicy.from_env(),
        )

    text = _dump(payload)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    if args.command == "audit" and not payload.get("healthy", True):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
