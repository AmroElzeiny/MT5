"""Deterministic local OpenAI-compatible server for integration harnesses.

Why a real HTTP server instead of a stub provider class
-------------------------------------------------------
``AIGateBridge.mqh`` validates ``provider_mode`` against a hardcoded allowlist of
exactly ``REMOTE_API`` and ``LOCAL_OPENAI_COMPATIBLE`` (AIGateBridge.mqh:1616).
Inventing a third "harness" provider mode would either be rejected by MQL --
correct fail-closed behaviour we must not weaken -- or force us to widen a
production allowlist for test convenience, which CLAUDE.md forbids.

This server is therefore not a fake provider: it *is* a local
OpenAI-compatible endpoint, which is exactly what ``LOCAL_OPENAI_COMPATIBLE``
already means.  Pointing ``AI_LOCAL_BASE_URL`` at it exercises the entire real
path with **zero production code changes**:

    real httpx client -> real timeout -> real retry budget -> real
    json_schema strict request -> real pydantic validation -> real evidence
    catalog resolution -> real Python-owned envelope construction -> real
    final envelope validation -> real atomic response write -> real MQL
    schema validation -> real candidate binding

Only the *model's judgement* is deterministic.  Every deterministic field, every
hash, every validator, and every gate remains the production implementation.

What this deliberately does NOT do
----------------------------------
* It does not bypass any validator.  If Python or MQL rejects its output, that
  is a genuine finding about the contract, not something to be worked around.
* It does not force a trade.  ``--decision reject`` and the malformation modes
  exist precisely so the negative paths are exercised too, and an APPROVE from
  this server still has to survive every downstream deterministic gate.
* It is never reachable from a production run: it must be started explicitly and
  ``AI_LOCAL_BASE_URL`` must be pointed at it on purpose.

Usage
-----
    python tools/harness_local_provider.py --port 8099 --decision approve

    set AI_PROVIDER_MODE=LOCAL_OPENAI_COMPATIBLE
    set AI_LOCAL_BASE_URL=http://127.0.0.1:8099/v1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import types
import typing
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel  # noqa: E402

import structured_models  # noqa: E402
from structured_models import (  # noqa: E402
    ADJUDICATOR_VERDICTS,
    CRITIC_VERDICTS,
    QUALITATIVE_VETO_CODES,
)

HARNESS_PROVIDER_VERSION = "20260731_local_openai_compatible_harness_v2"

# Values the deterministic pipeline actually accepts.  decision_integrity
# requires verdict == decision_state and both in DECISION_STATES.
DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"
DECISION_ABSTAIN = "ABSTAIN"

# Per-role verdict vocabularies.  These are imported, never restated: the whole
# point of the canonical enum in ``structured_models`` is that the harness
# cannot drift from the schema the production validator enforces.
CRITIC_PASS, CRITIC_BLOCK, CRITIC_ABSTAIN = CRITIC_VERDICTS
ADJ_UPHOLD_APPROVE, ADJ_UPHOLD_BLOCK, ADJ_ABSTAIN = ADJUDICATOR_VERDICTS

# Must be a member of the canonical objection/veto vocabulary; an unrecognised
# code now fails strict schema validation at the provider boundary instead of
# surfacing later as critic_objection_code_unknown.
HARNESS_VETO_CODE = "ai_veto_missing_mandatory_evidence"
assert HARNESS_VETO_CODE in QUALITATIVE_VETO_CODES


class HarnessMalformation:
    """Named ways to make the harness emit contract-violating output.

    These drive the negative fixtures.  Each one targets a *specific* validator
    so a passing negative test proves that validator actually fires, rather than
    proving only that some unrelated check happened to reject the payload.
    """

    NONE = "none"
    UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
    CROSS_CANDIDATE_EVIDENCE = "cross_candidate_evidence"
    EMPTY_EVIDENCE = "empty_evidence"
    CANDIDATE_COUNT_SHORT = "candidate_count_short"
    CANDIDATE_COUNT_LONG = "candidate_count_long"
    BAD_CANDIDATE_INDEX = "bad_candidate_index"
    SELECTED_INDEX_OUT_OF_RANGE = "selected_index_out_of_range"
    VERDICT_STATE_MISMATCH = "verdict_state_mismatch"
    INVALID_CONFIDENCE_BAND = "invalid_confidence_band"
    CONTRACT_VERSION_INJECTION = "contract_version_injection"
    INVALID_TARGET_MODEL = "invalid_target_model"
    NON_JSON_CONTENT = "non_json_content"
    # Reproduces the two failures the eleventh zero-trade run hit: a critic
    # answering with the analyst vocabulary, and an objection code outside the
    # canonical set.  Both must now be caught by strict schema validation.
    INVALID_CRITIC_VERDICT = "invalid_critic_verdict"
    UNKNOWN_OBJECTION_CODE = "unknown_objection_code"

    ALL = (
        NONE,
        UNKNOWN_EVIDENCE_ID,
        CROSS_CANDIDATE_EVIDENCE,
        EMPTY_EVIDENCE,
        CANDIDATE_COUNT_SHORT,
        CANDIDATE_COUNT_LONG,
        BAD_CANDIDATE_INDEX,
        SELECTED_INDEX_OUT_OF_RANGE,
        VERDICT_STATE_MISMATCH,
        INVALID_CONFIDENCE_BAND,
        CONTRACT_VERSION_INJECTION,
        INVALID_TARGET_MODEL,
        NON_JSON_CONTENT,
        INVALID_CRITIC_VERDICT,
        UNKNOWN_OBJECTION_CODE,
    )


@dataclass
class HarnessPolicy:
    """Deterministic 'judgement' the harness applies to every request."""

    decision: str = DECISION_APPROVE
    malformation: str = HarnessMalformation.NONE
    selected_candidate_index: int | None = None
    confidence_band: str = "HIGH"
    quality_score: float = 8.2
    risk_multiplier: float = 1.0
    latency_sec: float = 0.0
    fail_after_n_calls: int = 0
    model_name: str = "harness-deterministic-v1"
    # Directory to write each received evidence payload to. Diagnosing why a
    # harness answer was rejected requires seeing exactly what the model was
    # given, which is otherwise invisible.
    dump_payload_dir: str = ""
    # Independent role overrides.  ``run_qualitative_consensus`` only reaches the
    # adjudicator when the critic does not PASS, so the four multi-role branches
    # (critic block + uphold block, critic block + resolved, abstention, ...)
    # are unreachable while every role is derived from a single decision.  An
    # empty string means "derive from ``decision``".
    critic_verdict: str = ""
    adjudicator_verdict: str = ""

    def state_for(self, candidate_index: int) -> str:
        if self.decision == DECISION_APPROVE and self.selected_candidate_index is not None:
            return (
                DECISION_APPROVE
                if candidate_index == self.selected_candidate_index
                else DECISION_REJECT
            )
        return self.decision

    def critic_verdict_for(self, candidate_index: int) -> str:
        if self.critic_verdict:
            return self.critic_verdict
        return CRITIC_PASS if self.state_for(candidate_index) == DECISION_APPROVE else CRITIC_BLOCK

    def adjudicator_verdict_for(self, candidate_index: int) -> str:
        if self.adjudicator_verdict:
            return self.adjudicator_verdict
        return (
            ADJ_UPHOLD_APPROVE
            if self.state_for(candidate_index) == DECISION_APPROVE
            else ADJ_UPHOLD_BLOCK
        )


@dataclass
class CallRecord:
    schema_name: str
    role_hint: str
    candidate_count: int
    catalog_size: int
    request_id: str
    decision: str
    malformation: str
    elapsed_sec: float


@dataclass
class HarnessState:
    policy: HarnessPolicy
    calls: list[CallRecord] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, rec: CallRecord) -> int:
        with self.lock:
            self.calls.append(rec)
            return len(self.calls)


# --------------------------------------------------------------------------
# Evidence catalog reading
# --------------------------------------------------------------------------


def extract_catalog(evidence: Any) -> tuple[dict[int, list[int]], list[int], str]:
    """Return (ids_by_candidate, all_ids, catalog_hash) from a model payload.

    ``ai_gate._compact_model_evidence_payload`` embeds the Python-owned catalog
    as ``evidence_catalog.items``, each row ``{"id":..,"p":..,"v":..,"c":..}``
    where ``c`` is the owning candidate index (absent/None => shared/global).
    The harness cites only IDs it was actually given, which is precisely what a
    well-behaved model must do.
    """
    ids_by_candidate: dict[int, list[int]] = {}
    all_ids: list[int] = []
    catalog_hash = ""
    if not isinstance(evidence, dict):
        return ids_by_candidate, all_ids, catalog_hash

    catalog = evidence.get("evidence_catalog")
    if not isinstance(catalog, dict):
        return ids_by_candidate, all_ids, catalog_hash
    catalog_hash = str(catalog.get("catalog_hash") or "")

    for row in catalog.get("items") or []:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id")
        if not isinstance(raw_id, int):
            continue
        all_ids.append(raw_id)
        cand = row.get("c")
        if isinstance(cand, int):
            ids_by_candidate.setdefault(cand, []).append(raw_id)

    return ids_by_candidate, all_ids, catalog_hash


def candidate_values(evidence: Any) -> dict[int, dict[str, Any]]:
    """Per-candidate leaf values, keyed by candidate index then leaf name.

    Target arbitration is a choice *among supplied options*, not a free
    invention: ``decision_integrity`` requires the chosen target price to match
    the candidate's own ``tp2`` within two ticks, and the chosen target model to
    match the candidate's ``selected_target_identity``.  Emitting placeholder
    zeros produced ``selected target price does not match target arbitration``
    on every request.  The values here come from the catalog the model was
    actually given, so the harness chooses a real, offered target.

    Leaf names come from both plain candidate fields
    (``...candidates.0.target_model``) and authoritative numbers
    (``...candidates.0.authoritative_numbers.tp2.value`` -> ``tp2``).
    """
    values: dict[int, dict[str, Any]] = {}
    if not isinstance(evidence, dict):
        return values
    catalog = evidence.get("evidence_catalog")
    if not isinstance(catalog, dict):
        return values

    for row in catalog.get("items") or []:
        if not isinstance(row, dict):
            continue
        cand = row.get("c")
        if not isinstance(cand, int):
            continue
        path = str(row.get("p") or "")
        if ".authoritative_numbers." in path and path.endswith(".value"):
            name = path.split(".authoritative_numbers.", 1)[1][: -len(".value")]
        else:
            name = path.rsplit(".", 1)[-1]
        if name:
            values.setdefault(cand, {})[name] = row.get("v")
    return values


def candidate_count_from(evidence: Any, ids_by_candidate: dict[int, list[int]]) -> int:
    """Determine how many candidates the analyst must assess."""
    if isinstance(evidence, dict):
        for key in ("candidates", "ordered_candidates", "candidate_plans"):
            value = evidence.get(key)
            if isinstance(value, list) and value:
                return len(value)
        ordered = evidence.get("ordered_candidate_identities")
        if isinstance(ordered, list) and ordered:
            return len(ordered)
    if ids_by_candidate:
        return max(ids_by_candidate) + 1
    return 1


def evidence_ids_for(
    ids_by_candidate: dict[int, list[int]],
    all_ids: list[int],
    candidate_index: int,
    *,
    count: int,
    malformation: str,
) -> list[int]:
    own = ids_by_candidate.get(candidate_index) or []
    pool = own or all_ids

    if malformation == HarnessMalformation.EMPTY_EVIDENCE:
        return []
    if malformation == HarnessMalformation.UNKNOWN_EVIDENCE_ID:
        # An ID that provably cannot exist in the catalog.
        return [max(all_ids) + 9999 if all_ids else 999999]
    if malformation == HarnessMalformation.CROSS_CANDIDATE_EVIDENCE:
        for other, other_ids in sorted(ids_by_candidate.items()):
            if other != candidate_index and other_ids:
                return other_ids[:1]
        return pool[:1]

    return pool[:count] if pool else []


# --------------------------------------------------------------------------
# Generic, constraint-aware instance construction
# --------------------------------------------------------------------------


@dataclass
class BuildContext:
    policy: HarnessPolicy
    ids_by_candidate: dict[int, list[int]]
    all_ids: list[int]
    candidate_index: int = 0
    malformation: str = HarnessMalformation.NONE
    # Which role this output is for. Verdict vocabularies differ per role:
    # analyst APPROVE/REJECT/ABSTAIN, critic PASS/BLOCK/ABSTAIN, adjudicator
    # UPHOLD_APPROVE/UPHOLD_BLOCK/ABSTAIN. Emitting the analyst vocabulary for
    # every role produced ValueError:critic_verdict_invalid.
    role: str = "analyst"
    # Real values offered for this candidate (tp1/tp2/target_model/...).
    values: dict = field(default_factory=dict)
    # A disabled veto must carry no payload at all -- no code, no reason, and
    # no evidence references. Nested builds need to know they are inside one.
    in_veto: bool = False


def _constraint(metadata: list[Any], name: str) -> Any:
    for item in metadata:
        value = getattr(item, name, None)
        if value is not None:
            return value
    return None


def _clamp_number(ctx: BuildContext, metadata: list[Any], preferred: float) -> float:
    lo = _constraint(metadata, "ge")
    if lo is None:
        lo = _constraint(metadata, "gt")
    hi = _constraint(metadata, "le")
    if hi is None:
        hi = _constraint(metadata, "lt")
    value = preferred
    if lo is not None:
        value = max(float(lo), value)
    if hi is not None:
        value = min(float(hi), value)
    return round(value, 4)


def _string_for(name: str, metadata: list[Any], ctx: BuildContext) -> str:
    max_len = _constraint(metadata, "max_length") or 64
    min_len = _constraint(metadata, "min_length") or 0

    text = _NAMED_STRINGS.get(name)
    if text is None:
        text = f"harness_{name}"
    if len(text) > max_len:
        text = text[:max_len]
    while len(text) < min_len:
        text = (text + "_x")[:max_len] if max_len else text + "_x"
        if len(text) >= max_len:
            break
    return text


_NAMED_STRINGS: dict[str, str] = {
    "summary": "Deterministic harness assessment bound to the Python-owned evidence catalog.",
    "reasons": "Deterministic harness decision; no model judgement is claimed.",
    "reason": "harness deterministic reason",
    "narrative_state": "harness_deterministic",
    # decision_integrity accepts only these four states.
    "historical_evidence_state": "INSUFFICIENT_SAMPLE",
    "code": "",
    "resolution_reason": "Harness adjudication: no unresolved blocking objections.",
    "chosen_target_model": "LIQUIDITY_TARGET",
    "blocker_kind": "NONE",
    "blocker_class": "NONE",
    "target_decision_reason": "harness selected the primary liquidity target",
    "why_not_liquidity_target": "not applicable, liquidity target chosen",
    "why_not_partial_before_obstacle": "no obstacle before target",
    "why_not_capped_before_obstacle": "no obstacle before target",
    "why_not_synthetic_fallback": "structural target available",
    "bucket_prior_override_justification": "",
    "decision_quality_tier": "FULL_STRUCTURED",
    "response_quality": "FULL_STRUCTURED",
}


def _is_optional(annotation: Any) -> tuple[bool, Any]:
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return len(args) < len(get_args(annotation)), (args[0] if args else str)
    return False, annotation


def build_instance(model_cls: type[BaseModel], ctx: BuildContext) -> dict[str, Any]:
    """Construct a schema-valid dict for any StrictStructuredModel.

    Driven by pydantic field introspection rather than a hardcoded template, so
    adding a field to the contract does not silently produce an invalid harness
    payload -- it produces a valid one with a deterministic default.
    """
    out: dict[str, Any] = {}
    for name, fld in model_cls.model_fields.items():
        out[name] = _build_field(name, fld.annotation, list(fld.metadata or []), ctx)
    return out


def _build_field(name: str, annotation: Any, metadata: list[Any], ctx: BuildContext) -> Any:
    _, annotation = _is_optional(annotation)
    origin = get_origin(annotation)

    if origin is Literal:
        return _literal_value(name, get_args(annotation), ctx)

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        nested = ctx
        if "veto" in annotation.__name__.lower() or name == "veto":
            nested = replace(ctx, in_veto=True)
        return build_instance(annotation, nested)

    if origin in (list, typing.List):
        return _build_list(name, get_args(annotation), metadata, ctx)

    if annotation is bool:
        return _bool_value(name, ctx)
    if annotation is int:
        return _int_value(name, metadata, ctx)
    if annotation is float:
        return _float_value(name, metadata, ctx)
    if annotation is str:
        return _str_value(name, metadata, ctx)

    return None


def _literal_value(name: str, options: tuple[Any, ...], ctx: BuildContext) -> Any:
    """Choose an enum member using the same logic free strings use.

    Now that verdicts, decision states, objection codes, and the historical
    evidence state are ``Literal`` enums rather than free strings, this is the
    branch that produces them.  It must not become a second, divergent decision
    path: it asks ``_str_value`` what the harness intends and only falls back to
    the first enum member when that intent is not a member.  Malformations that
    deliberately emit an out-of-enum value are handled first, because the point
    of those fixtures is to be rejected.
    """

    if name == "confidence_band":
        if ctx.malformation == HarnessMalformation.INVALID_CONFIDENCE_BAND:
            return "VERY_HIGH"  # deliberately outside the strict enum
        if ctx.policy.confidence_band in options:
            return ctx.policy.confidence_band
        return options[0] if options else None
    if name == "code" and ctx.malformation == HarnessMalformation.UNKNOWN_OBJECTION_CODE:
        return "harness_not_a_real_veto_code"
    if (
        name == "verdict"
        and ctx.role == "critic"
        and ctx.malformation == HarnessMalformation.INVALID_CRITIC_VERDICT
    ):
        # The exact eleventh-run defect: analyst vocabulary in the critic slot.
        return DECISION_APPROVE

    intended = _str_value(name, [], ctx)
    if intended in options:
        return intended
    return options[0] if options else None


def _bool_value(name: str, ctx: BuildContext) -> bool:
    state = ctx.policy.state_for(ctx.candidate_index)
    approve = state == DECISION_APPROVE
    if name == "ok":
        # StructuredCapabilityProbe. The provider healthcheck asserts
        # parsed.ok is True before declaring structured output available, so a
        # generic False default made the harness report -- truthfully by its own
        # logic, but wrongly -- that it could not emit structured output, and
        # the gate refused to use it.
        return True
    if name in ("raw_allow", "thesis_supported"):
        return approve
    if name == "enabled":  # veto.enabled
        return not approve
    if name == "arbitration_required":
        return True
    if name == "blocker_is_trade_killer":
        return False
    return False


def _int_value(name: str, metadata: list[Any], ctx: BuildContext) -> int:
    if name == "candidate_index":
        if ctx.malformation == HarnessMalformation.BAD_CANDIDATE_INDEX:
            return ctx.candidate_index + 1000
        return ctx.candidate_index
    if name == "selected_candidate_index":
        if ctx.malformation == HarnessMalformation.SELECTED_INDEX_OUT_OF_RANGE:
            return 9999
        if ctx.policy.selected_candidate_index is not None:
            return ctx.policy.selected_candidate_index
        return 0
    return int(_clamp_number(ctx, metadata, 0.0))


def _float_value(name: str, metadata: list[Any], ctx: BuildContext) -> float:
    state = ctx.policy.state_for(ctx.candidate_index)
    approve = state == DECISION_APPROVE

    risk_like = name.endswith("_risk") or name in ("invalidation_risk", "chop_risk")
    if risk_like:
        return _clamp_number(ctx, metadata, 0.15 if approve else 0.75)
    if name.endswith("_score") or name == "final_trade_expectancy_score":
        return _clamp_number(ctx, metadata, ctx.policy.quality_score if approve else 2.5)
    if name in ("llm_self_reported_confidence", "follow_through_probability"):
        return _clamp_number(ctx, metadata, 0.85 if approve else 0.2)
    if name == "suggested_risk_multiplier":
        return _clamp_number(ctx, metadata, ctx.policy.risk_multiplier if approve else 0.0)
    if name == "blocker_severity":
        return _clamp_number(ctx, metadata, 0.0)
    if name in ("chosen_tp1", "chosen_tp2"):
        offered = ctx.values.get(name[len("chosen_"):])
        if isinstance(offered, (int, float)):
            return float(offered)
        return _clamp_number(ctx, metadata, 0.0)
    if name == "chosen_rr1":
        offered = ctx.values.get("net_rr")
        return float(offered) if isinstance(offered, (int, float)) else _clamp_number(ctx, metadata, 1.0)
    if name == "chosen_rr2":
        offered = ctx.values.get("liquidity_rr")
        return float(offered) if isinstance(offered, (int, float)) else _clamp_number(ctx, metadata, 2.0)
    return _clamp_number(ctx, metadata, 0.0)


def _str_value(name: str, metadata: list[Any], ctx: BuildContext) -> str:
    state = ctx.policy.state_for(ctx.candidate_index)
    approve = state == DECISION_APPROVE

    if name == "decision_state":
        return state
    if name == "verdict":
        if ctx.role == "critic":
            return ctx.policy.critic_verdict_for(ctx.candidate_index)
        if ctx.role == "adjudicator":
            return ctx.policy.adjudicator_verdict_for(ctx.candidate_index)
        if ctx.malformation == HarnessMalformation.VERDICT_STATE_MISMATCH:
            return DECISION_REJECT if state == DECISION_APPROVE else DECISION_APPROVE
        return state
    if name == "chosen_target_model":
        if ctx.malformation == HarnessMalformation.INVALID_TARGET_MODEL:
            return "HARNESS_NOT_A_REAL_TARGET_MODEL"
        offered = ctx.values.get("target_model")
        if isinstance(offered, str) and offered:
            return offered
        return _NAMED_STRINGS["chosen_target_model"]
    if name == "code":
        # A critic objection only exists when the critic BLOCKs, and it must
        # name a real code -- the adjudicator's resolved_objection_codes has to
        # match it exactly or _validate_adjudication rejects UPHOLD_APPROVE.
        if ctx.role == "critic":
            return HARNESS_VETO_CODE
        # The analyst's veto code must come from the canonical vocabulary when
        # the veto is enabled, and must be empty when it is not --
        # decision_integrity rejects any leftover payload on a disabled veto as
        # veto.disabled_payload.
        return "" if approve else HARNESS_VETO_CODE
    if name == "reason":
        # ModelCriticObjection.reason is min_length=1; an empty string here
        # fails the client-side pydantic parse as a schema error.
        if ctx.role == "critic":
            return "harness critic objection: mandatory evidence not established"
        return "" if approve else "harness deterministic rejection"

    return _string_for(name, metadata, ctx)


def _build_list(
    name: str,
    args: tuple[Any, ...],
    metadata: list[Any],
    ctx: BuildContext,
) -> list[Any]:
    min_len = int(_constraint(metadata, "min_length") or 0)
    max_len = int(_constraint(metadata, "max_length") or 8)
    item_type = args[0] if args else str

    if name == "evidence_ref_ids":
        if (
            ctx.in_veto
            and ctx.policy.state_for(ctx.candidate_index) == DECISION_APPROVE
            and ctx.malformation == HarnessMalformation.NONE
        ):
            return []  # disabled veto carries no payload
        want = max(min_len, 1) if min_len else min(2, max_len)
        ids = evidence_ids_for(
            ctx.ids_by_candidate,
            ctx.all_ids,
            ctx.candidate_index,
            count=max(want, 1),
            malformation=ctx.malformation,
        )
        return ids[:max_len]

    if name in ("blocking_objections", "non_blocking_objections"):
        # _validate_objections rejects PASS-with-blocking-objections and
        # BLOCK-without-objection, so the list follows the critic verdict, not
        # the analyst decision.  ABSTAIN carries no blocking objection.
        verdict = ctx.policy.critic_verdict_for(ctx.candidate_index)
        want_block = (name == "blocking_objections") and verdict == CRITIC_BLOCK
        if not want_block:
            return []
        item_cls = args[0] if args else None
        if isinstance(item_cls, type) and issubclass(item_cls, BaseModel):
            # NOT in_veto: ``in_veto`` means "inside the analyst's disabled
            # veto", whose payload must be empty.  A critic objection is the
            # opposite -- it exists precisely to carry evidence, and its
            # evidence_ref_ids is min_length=1.
            return [build_instance(item_cls, replace(ctx, in_veto=False))]
        return []

    if name in ("resolved_objection_codes", "unresolved_objection_codes"):
        # _validate_adjudication requires UPHOLD_APPROVE to resolve *every*
        # critic blocking code and leave nothing unresolved.  The critic's
        # blocking code is deterministically HARNESS_VETO_CODE, so the
        # adjudicator can state exactly which code it resolved.
        adj_verdict = ctx.policy.adjudicator_verdict_for(ctx.candidate_index)
        critic_blocked = ctx.policy.critic_verdict_for(ctx.candidate_index) == CRITIC_BLOCK
        if not critic_blocked:
            return []
        if adj_verdict == ADJ_UPHOLD_APPROVE:
            return [HARNESS_VETO_CODE] if name == "resolved_objection_codes" else []
        return [HARNESS_VETO_CODE] if name == "unresolved_objection_codes" else []

    if min_len <= 0:
        return []

    if isinstance(item_type, type) and issubclass(item_type, BaseModel):
        return [build_instance(item_type, ctx) for _ in range(min_len)]
    if item_type is int:
        return [0] * min_len
    if item_type is float:
        return [0.0] * min_len
    return [f"harness_{name}_{i}" for i in range(min_len)]


# --------------------------------------------------------------------------
# Role dispatch
# --------------------------------------------------------------------------


def build_analyst_output(
    model_cls: type[BaseModel],
    evidence: Any,
    policy: HarnessPolicy,
) -> dict[str, Any]:
    ids_by_candidate, all_ids, _ = extract_catalog(evidence)
    count = candidate_count_from(evidence, ids_by_candidate)

    emitted = count
    if policy.malformation == HarnessMalformation.CANDIDATE_COUNT_SHORT and count > 1:
        emitted = count - 1
    elif policy.malformation == HarnessMalformation.CANDIDATE_COUNT_LONG:
        emitted = count + 1

    assessment_cls = _list_item_model(model_cls, "candidate_assessments")
    offered = candidate_values(evidence)
    assessments = []
    for index in range(emitted):
        ctx = BuildContext(
            policy=policy,
            ids_by_candidate=ids_by_candidate,
            all_ids=all_ids,
            candidate_index=index,
            malformation=policy.malformation,
            values=offered.get(index, {}),
            role="analyst",
        )
        assessments.append(build_instance(assessment_cls, ctx))

    root_ctx = BuildContext(
        policy=policy,
        ids_by_candidate=ids_by_candidate,
        all_ids=all_ids,
        candidate_index=0,
        malformation=policy.malformation,
        values=offered.get(0, {}),
        role="analyst",
    )
    out = build_instance(model_cls, root_ctx)
    out["candidate_assessments"] = assessments
    out["decision_quality_tier"] = "FULL_STRUCTURED"

    if policy.malformation == HarnessMalformation.CONTRACT_VERSION_INJECTION:
        # A model that tries to own Python-owned contract identity.  Strict
        # schemas must reject the extra fields outright.
        for item in out["candidate_assessments"]:
            arb = item.get("target_arbitration")
            if isinstance(arb, dict):
                arb["prompt_contract_version"] = "HARNESS_FORGED"
                arb["target_arbitration_schema_version"] = "HARNESS_FORGED"
    return out


def _list_item_model(model_cls: type[BaseModel], field_name: str) -> type[BaseModel]:
    annotation = model_cls.model_fields[field_name].annotation
    _, annotation = _is_optional(annotation)
    args = get_args(annotation)
    return args[0]


def build_role_output(schema_name: str, evidence: Any, policy: HarnessPolicy) -> dict[str, Any]:
    model_cls = getattr(structured_models, schema_name, None)
    if model_cls is None or not (
        isinstance(model_cls, type) and issubclass(model_cls, BaseModel)
    ):
        raise KeyError(f"unknown response schema: {schema_name}")

    if "candidate_assessments" in model_cls.model_fields:
        return build_analyst_output(model_cls, evidence, policy)

    ids_by_candidate, all_ids, _ = extract_catalog(evidence)
    index = policy.selected_candidate_index or 0
    ctx = BuildContext(
        policy=policy,
        ids_by_candidate=ids_by_candidate,
        all_ids=all_ids,
        candidate_index=index,
        malformation=policy.malformation,
        values=candidate_values(evidence).get(index, {}),
        role=_role_hint(schema_name),
    )
    return build_instance(model_cls, ctx)


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------


class HarnessHandler(BaseHTTPRequestHandler):
    server_version = f"PO3HarnessProvider/{HARNESS_PROVIDER_VERSION}"
    state: HarnessState  # injected on the server instance

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        if self.server.verbose:  # type: ignore[attr-defined]
            sys.stderr.write("[harness] " + (fmt % args) + "\n")

    # -- helpers ---------------------------------------------------------
    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _state(self) -> HarnessState:
        return self.server.state  # type: ignore[attr-defined]

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/models"):
            policy = self._state().policy
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": policy.model_name, "object": "model", "owned_by": "po3-harness"}
                    ],
                },
            )
            return
        if path.endswith("/harness/calls"):
            st = self._state()
            with st.lock:
                self._send_json(
                    200,
                    {
                        "harness_version": HARNESS_PROVIDER_VERSION,
                        "policy": {
                            "decision": st.policy.decision,
                            "malformation": st.policy.malformation,
                            "selected_candidate_index": st.policy.selected_candidate_index,
                        },
                        "call_count": len(st.calls),
                        "calls": [vars(c) for c in st.calls],
                    },
                )
            return
        self._send_json(404, {"error": {"message": f"no route {path}"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"

        if path.endswith("/harness/policy"):
            self._update_policy(raw)
            return
        if not path.endswith("/chat/completions"):
            self._send_json(404, {"error": {"message": f"no route {path}"}})
            return

        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": {"message": f"invalid request json: {exc}"}})
            return

        self._handle_chat(body)

    def _update_policy(self, raw: bytes) -> None:
        st = self._state()
        try:
            patch = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": {"message": str(exc)}})
            return
        with st.lock:
            for key, value in patch.items():
                if hasattr(st.policy, key):
                    setattr(st.policy, key, value)
            if patch.get("reset_calls"):
                st.calls.clear()
            snapshot = vars(st.policy).copy()
        self._send_json(200, {"policy": snapshot})

    def _handle_chat(self, body: dict[str, Any]) -> None:
        st = self._state()
        policy = st.policy
        started = time.perf_counter()

        response_format = body.get("response_format") or {}
        schema_block = response_format.get("json_schema") or {}
        schema_name = str(schema_block.get("name") or "")

        evidence: Any = {}
        for message in body.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "user":
                try:
                    evidence = json.loads(message.get("content") or "{}")
                except (ValueError, TypeError):
                    evidence = {}

        request_id = ""
        if isinstance(evidence, dict):
            request_id = str(evidence.get("request_id") or "")

        if policy.dump_payload_dir and isinstance(evidence, dict):
            try:
                out_dir = Path(policy.dump_payload_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                stem = request_id or f"call{len(st.calls) + 1}"
                (out_dir / f"{stem}__{schema_name}.json").write_text(
                    json.dumps(evidence, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except OSError:
                pass  # diagnostics must never break the run

        # Deterministic failure injection for deadline/transport tests.
        with st.lock:
            call_number = len(st.calls) + 1
        if policy.fail_after_n_calls and call_number > policy.fail_after_n_calls:
            self._send_json(503, {"error": {"message": "harness injected upstream failure"}})
            return

        if policy.latency_sec > 0:
            time.sleep(policy.latency_sec)

        if policy.malformation == HarnessMalformation.NON_JSON_CONTENT:
            content = "This harness response is deliberately not JSON."
        else:
            try:
                payload = build_role_output(schema_name, evidence, policy)
            except KeyError as exc:
                self._send_json(400, {"error": {"message": str(exc)}})
                return
            content = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        ids_by_candidate, all_ids, _ = extract_catalog(evidence)
        elapsed = time.perf_counter() - started
        st.record(
            CallRecord(
                schema_name=schema_name,
                role_hint=_role_hint(schema_name),
                candidate_count=candidate_count_from(evidence, ids_by_candidate),
                catalog_size=len(all_ids),
                request_id=request_id,
                decision=policy.decision,
                malformation=policy.malformation,
                elapsed_sec=round(elapsed, 4),
            )
        )

        prompt_chars = sum(
            len(str((m or {}).get("content") or "")) for m in (body.get("messages") or [])
        )
        self._send_json(
            200,
            {
                "id": f"harness-{call_number}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model") or policy.model_name,
                "system_fingerprint": HARNESS_PROVIDER_VERSION,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": max(1, prompt_chars // 4),
                    "completion_tokens": max(1, len(content) // 4),
                    "total_tokens": max(2, (prompt_chars + len(content)) // 4),
                },
            },
        )


def _role_hint(schema_name: str) -> str:
    lowered = re.sub(r"(?<!^)(?=[A-Z])", "_", schema_name).lower()
    if "critic" in lowered:
        return "critic"
    if "adjudicator" in lowered:
        return "adjudicator"
    return "analyst"


class HarnessServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], state: HarnessState, verbose: bool) -> None:
        super().__init__(addr, HarnessHandler)
        self.state = state
        self.verbose = verbose


def serve(
    policy: HarnessPolicy,
    *,
    host: str = "127.0.0.1",
    port: int = 8099,
    verbose: bool = False,
) -> tuple[HarnessServer, threading.Thread]:
    """Start the harness in a background thread; returns (server, thread)."""
    state = HarnessState(policy=policy)
    server = HarnessServer((host, port), state, verbose)
    thread = threading.Thread(target=server.serve_forever, name="harness-provider", daemon=True)
    thread.start()
    return server, thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument(
        "--decision",
        default="approve",
        choices=["approve", "reject", "abstain"],
        help="Deterministic judgement applied to every candidate.",
    )
    parser.add_argument(
        "--malformation",
        default=HarnessMalformation.NONE,
        choices=list(HarnessMalformation.ALL),
        help="Emit contract-violating output to exercise a specific validator.",
    )
    parser.add_argument("--selected-index", type=int, default=None)
    parser.add_argument("--confidence-band", default="HIGH", choices=["LOW", "MEDIUM", "HIGH"])
    parser.add_argument(
        "--critic-verdict",
        default="",
        choices=["", *CRITIC_VERDICTS],
        help="Override the critic verdict independently of --decision.",
    )
    parser.add_argument(
        "--adjudicator-verdict",
        default="",
        choices=["", *ADJUDICATOR_VERDICTS],
        help="Override the adjudicator verdict independently of --decision.",
    )
    parser.add_argument("--latency-sec", type=float, default=0.0)
    parser.add_argument("--fail-after", type=int, default=0)
    parser.add_argument("--dump-payload", default="", help="Directory to write each received evidence payload to.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    policy = HarnessPolicy(
        decision={
            "approve": DECISION_APPROVE,
            "reject": DECISION_REJECT,
            "abstain": DECISION_ABSTAIN,
        }[args.decision],
        malformation=args.malformation,
        selected_candidate_index=args.selected_index,
        confidence_band=args.confidence_band,
        latency_sec=args.latency_sec,
        fail_after_n_calls=args.fail_after,
        dump_payload_dir=args.dump_payload,
        critic_verdict=args.critic_verdict,
        adjudicator_verdict=args.adjudicator_verdict,
    )

    server, _ = serve(policy, host=args.host, port=args.port, verbose=args.verbose)
    base = f"http://{args.host}:{args.port}/v1"
    print(f"[harness] {HARNESS_PROVIDER_VERSION}")
    print(f"[harness] listening on {base}")
    print(f"[harness] decision={policy.decision} malformation={policy.malformation}")
    print("[harness] point the gate at it with:")
    print("    set AI_PROVIDER_MODE=LOCAL_OPENAI_COMPATIBLE")
    print(f"    set AI_LOCAL_BASE_URL={base}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[harness] shutting down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())








