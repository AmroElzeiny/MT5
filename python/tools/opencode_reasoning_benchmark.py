#!/usr/bin/env python3
"""Muse reasoning-effort non-inferiority benchmark driver (Phase 2, stage C1, WP1).

Requirement IDs (benchmark_protocol.md): BEN-001 (frozen stratified request set),
BEN-003 (effort verified on the wire from the attempt record), BEN-004 (per-request
metrics: decision state, allow, veto, tokens/latency/cost per role), BEN-005
(pre-registered non-inferiority rule incl. S1/S2/S3 safety counters and Wilson 95%
intervals), BEN-006/BEN-007 (fresh subprocess per pair, isolated mutable state,
bounded concurrency, resumability, clean stop on a Go usage-limit 429).

SAFETY CONTRACT
    * ``--mode dry`` (the default) NEVER builds a real API key and NEVER touches
      the network: the leg is constructed with an injected fake client whose
      ``responses.create`` records the wire kwargs and raises a sentinel that the
      production transport classifies as one non-retryable transport failure.
      Credentials are only ever obtained by the gate's own loader
      (``po3_env.bootstrap_provider_env()`` in ``--mode live`` children) plus
      ``os.environ`` at leg-build time; this module never opens ``python/.env``
      and never prints a secret.
    * ``--mode live`` children run the real provider leg; only the supervisor
      runs paid calls.  ``manifest`` reads the live bus strictly READ-ONLY; no
      command writes to the live bus, ``python/data/*``, ``python/logs/*`` or the
      shadow ledger: every mutable path the driven pipeline can touch is
      redirected to the per-pair scratch dir BEFORE ``ai_gate`` is imported, and
      the parent snapshots (path, size, mtime) before/after and records whether
      anything moved.
    * One result JSON per (request, arm); completed ``status == "ok"`` pairs are
      skipped on restart only when the file's recorded ``mode`` matches the
      requested mode -- a dry result never satisfies a live run, and vice versa
      (BEN-007 resumability, mode-aware).

WHAT THE DRY FAKE PROVES / DOES NOT PROVE (WP1_SPEC "Fake dry client")
    Proves: the arm's reasoning effort reaches the wire kwargs of the production
    transport (``kwargs["reasoning"]["effort"]`` and, through the same kwargs,
    the attempt record's ``reasoning_effort_sent``), that exactly one provider
    leg is invoked with no fallback model/provider, that the production pipeline
    is driven end to end up to the provider boundary on real archived payloads,
    and that the isolation env keeps protected files unchanged (sizes/mtimes).
    Does NOT prove: any model-answer-dependent behaviour.  Because the fake
    raises at ``responses.create``, every dry decision is the pipeline's
    fail-closed provider-failure Decision (``decision_source ==
    "provider_transport_error"``, tier DEGRADED_NON_TRADING).  Schema/semantic
    validity, agreement, S1/S2/S3 and cost statistics are only meaningful for
    ``--mode live`` results.  A canned fully-valid ``ModelAIGateOutput`` for
    arbitrary archived envelopes was judged impractical (identity-, evidence-
    and target-arbitration-bound content), so the spec-sanctioned capture-style
    fake is used.

USAGE
    python/.venv/Scripts/python.exe tools/opencode_reasoning_benchmark.py manifest \
        --run-dir ..\\.mt5-orchestrator\runs\20260913T180610Z-eee8a0a8
    ... run --run-dir <dir> [--arms a,b] [--requests id1,id2] [--concurrency 6]
            [--mode dry|live] [--dry-requests 2]
    ... report --run-dir <dir>
    (``_child`` is an internal entry point spawned by ``run``; not for manual use.)
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import csv
import dataclasses
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Paths and constants.  Module import stays side-effect free: NO product
# imports at top level -- ai_gate/po3_env are imported only inside children.
# ---------------------------------------------------------------------------

REPO_PYTHON = Path(__file__).resolve().parents[1]
REPO_ROOT = REPO_PYTHON.parent
DEFAULT_RUN_DIR = REPO_ROOT / ".mt5-orchestrator" / "runs" / "20260913T180610Z-eee8a0a8"
DEFAULT_BUS = r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS"
STAGE_A_SAMPLE_MANIFEST = (
    REPO_ROOT / ".mt5-orchestrator" / "runs" / "20260913T155650Z-5fa598c0" / "wp2"
    / "sample_manifest.json"
)
DEFAULT_SEED = 20260913
DEFAULT_TARGET_N = 60
MUSE_MODEL_DEFAULT = "muse-spark-1.3-contributor"
LUNA_MODEL_DEFAULT = "gpt-5.6-luna"
# Never a secret; exactly the documented stage-A recipe value.
DRY_DUMMY_KEY = "offline-benchmark-not-a-secret"
# CLAUDE.md section 4ac known approvals -- required in the frozen set when present.
ANCHOR_ID_SUFFIXES = ("AUDJPY_32618", "USDCHF_19925")
SCHEMA_FULL_STRUCTURED = "FULL_STRUCTURED"
ROLES = ("analyst", "critic", "adjudicator")
MEASUREMENT_VERSION = "wp1.c1"

# arm -> spec.  A Muse arm is ONE OpenCodeResponsesProvider leg with no fallback
# chain (protocol "Arms": "A failed call is a failure of that arm; it is never
# replaced by another model").  ``service_tier`` is only meaningful for the Luna
# reference arm.
ARMS: dict[str, dict[str, str]] = {
    "muse_high_a": {"leg": "muse", "effort": "high"},
    "muse_high_b": {"leg": "muse", "effort": "high"},
    "muse_medium": {"leg": "muse", "effort": "medium"},
    "muse_low": {"leg": "muse", "effort": "low"},
    "muse_minimal": {"leg": "muse", "effort": "minimal"},
    "luna_low_flex": {"leg": "luna", "effort": "low", "service_tier": "flex"},
    # Phase-2 part 10 A/B arm: production HIGH effort with the lossless compact
    # provider-wire projection.  Every other arm pins the canonical (Phase-1)
    # wire so the reasoning-effort comparison cannot drift while stage B lands.
    "muse_high_compact": {"leg": "muse", "effort": "high", "wire_projection": "compact_v1"},
}
ARM_ORDER = tuple(ARMS)
WIRE_PROJECTION_ENV = "AI_PROVIDER_WIRE_PROJECTION"
HIGH_ARMS = ("muse_high_a", "muse_high_b")
# Addendum A3: minimum number of pairs with no infrastructure outcome on either side
# before a clean-agreement non-inferiority verdict may be issued.
A3_MIN_CLEAN_PAIRS = 40

# decision_source values (lower-cased categories produced by _score_setup_impl's
# provider-failure / degradation paths) that are INFRASTRUCTURE outcomes per
# BEN-004 "decision_source (surfacing infrastructure outcomes)".
INFRA_DECISION_SOURCES = {
    "provider_transport_error",
    "provider_configuration_error",
    "provider_configuration_block",
    "structured_schema_invalid",
    "structured_response_invalid",
    "request_identity_mismatch",
    "response_stale",
    "repeatability_unavailable",
    "provider_health_no_trade",
    "provider_deadline_exceeded",
    "local_pipeline_error",
    "local_request_integrity_error",
    "request_identity_error",
    "candidate_identity_error",
    "frozen_request_mutation",
    "request_contract_reject",
}

# Decision attributes copied into every result JSON (WP1_SPEC "Result JSON
# schema": at least this set; candidate_assessments is added because the S1
# safety counter needs per-candidate REJECT+veto states).
DECISION_FIELDS = (
    "decision_state", "python_final_allow", "raw_allow", "allow", "chosen_index",
    "selected_candidate_id", "selected_candidate_hash", "decision_quality_tier",
    "decision_source", "rejection_codes", "veto_enabled", "veto_code", "veto_reason",
    "llm_quality_score", "llm_self_reported_confidence", "suggested_risk_multiplier",
    "mandatory_fields_complete", "missing_mandatory_fields", "invalid_mandatory_fields",
    "provider_mode", "provider_id", "actual_model_id", "model_fingerprint",
    "final_resolver_reason", "role_latencies", "provider_retry_counts", "provider_usage",
    "analyst_output", "critic_output", "adjudicator_output", "candidate_assessments",
)


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_component(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or ""))[:180]


def result_path(results_dir: Path, request_id: str, arm: str) -> Path:
    return Path(results_dir) / f"{safe_component(request_id)}__{arm}.json"


def read_json_bytes_any_encoding(path: Path) -> Any:
    """UTF-16/BOM-safe JSON reader independent of the product module (manifest
    runs without importing ai_gate).  Same decoding branches as
    ``ai_gate.read_json_any_encoding``."""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8")
    return json.loads(text)


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        with contextlib.suppress(Exception):
            return _json_safe(dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        with contextlib.suppress(Exception):
            return _json_safe(dataclasses.asdict(value))
    return str(value)


def decision_document(decision: Any) -> dict:
    """Explicit attribute extraction (the spec allows ``asdict`` only if
    JSON-safe; the dataclass holds nested model objects, so read attributes)."""
    return {name: _json_safe(getattr(decision, name, None)) for name in DECISION_FIELDS}


def build_result_document(
    *,
    request_id: str,
    arm: str,
    mode: str,
    started_utc: str,
    elapsed_sec: float,
    config: dict,
    archive_candidates: int,
    sealed_candidates: int,
    decision: Any = None,
    attempts: list[dict] | None = None,
    error: BaseException | None = None,
) -> dict:
    """The frozen result schema (WP1_SPEC "Result JSON schema").  Pure function:
    the child builds exactly the document the focused tests assemble.  ``mode``
    is recorded so resumability can never mistake a dry result for a completed
    live pair or vice versa (BEN-007, mode-aware)."""
    base = {
        "request_id": request_id,
        "arm": arm,
        "mode": mode,
        "started_utc": started_utc,
        "elapsed_sec": round(float(elapsed_sec), 3),
        "config": config,
        "wire_payload": {
            "archive_candidates": archive_candidates,
            "sealed_candidates": sealed_candidates,
        },
        "attempts": _json_safe(attempts or []),
        "measurement_version": MEASUREMENT_VERSION,
    }
    if error is not None:
        return {
            **base,
            "status": "error",
            "error": {"type": type(error).__name__, "message": str(error)[:2000]},
            "decision": None,
        }
    return {
        **base,
        "status": "ok",
        "error": None,
        "decision": decision_document(decision) if decision is not None else None,
    }


# ---------------------------------------------------------------------------
# manifest (BEN-001) -- read-only against the live bus
# ---------------------------------------------------------------------------

def stage_a_request_ids(path: Path) -> list[str]:
    if not Path(path).is_file():
        return []
    try:
        doc = read_json_bytes_any_encoding(path)
        return [
            str(row.get("request_id") or "")
            for row in (doc.get("rows") or [])
            if str(row.get("request_id") or "")
        ]
    except Exception:  # noqa: BLE001
        return []


def select_manifest_requests(
    bus: Path,
    *,
    seed: int = DEFAULT_SEED,
    min_requests: int = DEFAULT_TARGET_N,
    stage_a_path: Path = STAGE_A_SAMPLE_MANIFEST,
) -> dict:
    """Build the frozen request set per protocol BEN-001.  Deterministic for a
    fixed seed; never writes anything (the caller writes the manifest)."""

    # Local import: tools/ has no __init__.py; import the stage A module for its
    # proven read-only bus reader and classifiers (module is side-effect free).
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import opencode_token_attribution as stage_a  # noqa: E402

    reader = stage_a.BusReader(Path(bus))
    archives = reader.archived_payload_index()

    def order(request_id: str) -> str:
        return hashlib.sha256(f"{seed}|{request_id}".encode("utf-8")).hexdigest()

    detail: dict[str, dict] = {}
    skipped: dict[str, str] = {}

    def inspect(request_id: str) -> dict | None:
        if request_id in detail:
            return detail[request_id]
        if request_id in skipped:
            return None
        archive = archives.get(request_id)
        if archive is None:
            skipped[request_id] = "no_archived_payload"
            return None
        debug = reader.response_debug(request_id)
        if not isinstance(debug, dict):
            skipped[request_id] = "no_response_debug"
            return None
        response = debug.get("response") if isinstance(debug.get("response"), dict) else {}
        if str(response.get("decision_quality_tier") or "") != SCHEMA_FULL_STRUCTURED:
            skipped[request_id] = (
                "historical_tier_not_full_structured:"
                f"{response.get('decision_quality_tier')}"
            )
            return None
        try:
            payload = read_json_bytes_any_encoding(archive)
        except Exception as exc:  # noqa: BLE001
            skipped[request_id] = f"payload_unreadable:{type(exc).__name__}"
            return None
        candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        symbol = str(payload.get("symbol") or "")
        row = {
            "request_id": request_id,
            "archive_path": str(archive),
            "archive_kind": archive.parent.name,
            "symbol": symbol,
            "symbol_class": stage_a.symbol_class(symbol),
            "candidate_count_archive": len(candidates),
            "candidate_stratum": stage_a.candidate_stratum(len(candidates)),
            "has_response_debug": True,
            "historical_decision": {
                "decision_state": response.get("decision_state"),
                "python_final_allow": response.get("python_final_allow"),
                "chosen_index": response.get("chosen_index"),
                "veto_code": response.get("veto_code"),
                "llm_quality_score": response.get("llm_quality_score"),
                "decision_quality_tier": response.get("decision_quality_tier"),
                "decision_source": response.get("decision_source"),
            },
        }
        row["strata"] = [row["candidate_stratum"], row["symbol_class"]]
        row["stratum"] = f"{row['candidate_stratum']}/{row['symbol_class']}"
        detail[request_id] = row
        return row

    selected: list[str] = []

    def take(request_id: str, why: str) -> None:
        row = inspect(request_id)
        if row is None or request_id in selected:
            return
        row.setdefault("selection_reasons", []).append(why)
        selected.append(request_id)

    # (4) anchors first (stable frozen ordering), then (1) the stage-A sample.
    for suffix in ANCHOR_ID_SUFFIXES:
        for request_id in sorted(archives):
            if request_id.endswith(suffix):
                take(request_id, "anchor_required_by_claude_md_4ac")
    for request_id in stage_a_request_ids(stage_a_path):
        take(request_id, "stage_a_sample_seed")

    # (2) every eligible archived request that historically allowed or approved.
    approvals: list[str] = []
    for request_id in sorted(archives):
        row = inspect(request_id)
        if row is None:
            continue
        historical = row["historical_decision"]
        if historical.get("python_final_allow") is True or historical.get("decision_state") == "APPROVE":
            approvals.append(request_id)
    for request_id in sorted(approvals, key=order):
        take(request_id, "historical_allow_or_approve")

    # (3) stratified coverage over (candidate-count stratum x symbol class).
    strata: dict[tuple[str, str], list[str]] = {}
    for request_id in sorted(archives):
        row = inspect(request_id)
        if row is None:
            continue
        strata.setdefault((row["candidate_stratum"], row["symbol_class"]), []).append(request_id)
    for cell_rows in strata.values():
        cell_rows.sort(key=order)
    cells = sorted(strata)
    cursor = {cell: 0 for cell in cells}
    # one guaranteed pick per populated cell first (coverage), then round-robin.
    for cell in cells:
        if len(selected) >= min_requests:
            break
        rows = strata[cell]
        while cursor[cell] < len(rows) and rows[cursor[cell]] in selected:
            cursor[cell] += 1
        if cursor[cell] < len(rows):
            take(rows[cursor[cell]], f"stratified_coverage:{cell[0]}/{cell[1]}")
            cursor[cell] += 1
    while len(selected) < min_requests and any(cursor[cell] < len(strata[cell]) for cell in cells):
        for cell in cells:
            rows = strata[cell]
            while cursor[cell] < len(rows) and rows[cursor[cell]] in selected:
                cursor[cell] += 1
            if cursor[cell] < len(rows):
                take(rows[cursor[cell]], f"stratified_round_robin:{cell[0]}/{cell[1]}")
                cursor[cell] += 1
                if len(selected) >= min_requests:
                    break

    counts: dict[str, int] = {}
    for request_id in selected:
        key = detail[request_id]["stratum"]
        counts[key] = counts.get(key, 0) + 1
    populated_cells = {f"{cell[0]}/{cell[1]}": len(rows) for cell, rows in strata.items()}
    notes: list[str] = []
    if len(selected) < min_requests:
        notes.append(
            f"HONEST SHORTFALL: eligible pool exhausted at {len(selected)} < target "
            f"{min_requests}; every archived request whose response_debug shows a "
            f"historical decision_quality_tier == {SCHEMA_FULL_STRUCTURED} is already "
            "selected (see skipped_eligible_pool for the excluded-population tally)."
        )
    missing_anchors = [
        suffix for suffix in ANCHOR_ID_SUFFIXES
        if not any(rid.endswith(suffix) for rid in selected)
    ]
    if missing_anchors:
        notes.append(
            "anchors not selected (absent from the bus or ineligible under rule 5): "
            f"{missing_anchors}"
        )
    return {
        "generated_utc": _now_utc(),
        "bus": str(bus),
        "seed": seed,
        "target_requests": min_requests,
        "request_count": len(selected),
        "selection_rule": (
            "BEN-001: anchors first (stable), then every stage-A sample id that is "
            "eligible, then every eligible request whose response_debug shows "
            "python_final_allow=true or decision_state=APPROVE, then stratified "
            "round-robin over (candidate-count stratum x symbol class) cells; "
            "eligible == archived payload (completed|rejected) AND response_debug "
            "present AND historical decision_quality_tier == "
            f"{SCHEMA_FULL_STRUCTURED}; deterministic under the fixed seed "
            "(sha256(seed|request_id) order)"
        ),
        "anchors": {
            suffix: [rid for rid in selected if rid.endswith(suffix)]
            for suffix in ANCHOR_ID_SUFFIXES
        },
        "stratum_summary": counts,
        "covered_cells": sorted({detail[rid]["stratum"] for rid in selected}),
        "available_population": populated_cells,
        "skipped_eligible_pool": _tally(skipped),
        "notes": notes,
        "measurement_version": MEASUREMENT_VERSION,
        "rows": [detail[request_id] for request_id in selected],
    }


def _tally(mapping: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in mapping.values():
        key = str(reason).split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    return counts


def cmd_manifest(args: argparse.Namespace) -> int:
    manifest = select_manifest_requests(
        Path(args.bus), seed=args.seed, min_requests=args.min_requests
    )
    run_dir = Path(args.run_dir)
    out = run_dir / "benchmark_manifest.json"
    write_json(out, manifest)
    print(f"[manifest] wrote {out}")
    print(f"[manifest] request_count={manifest['request_count']} target={manifest['target_requests']}")
    for key, value in sorted(manifest["stratum_summary"].items()):
        print(f"[manifest]   {key}: {value}")
    for note in manifest["notes"]:
        print(f"[manifest] NOTE: {note}")
    return 0


# ---------------------------------------------------------------------------
# Arm legs
# ---------------------------------------------------------------------------

def build_arm_leg(
    arm: str,
    cfg: Any,
    *,
    mode: str,
    client_factory: Callable[..., Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> Any:
    """Build the single injected provider leg for one arm, mirroring the exact
    kwargs of ``ai_gate._build_ai_provider`` (ai_gate.py:1497-1544) for the leg
    kind, with only ``reasoning_effort`` (and, for Luna, the service tier)
    varying.  The Muse leg has NO fallback model (single model,
    ``fallback_models=()`` inside OpenCodeResponsesProvider).

    ``mode="dry"`` substitutes the documented dummy key and neutralizes the
    transport's resilience knobs (circuit/admission/flex retries) so the capture
    fake fails closed exactly once per call instead of sleeping through retries:
    those knobs are irrelevant when no socket exists and must never mask the one
    wire observation this mode makes."""
    from ai_provider import OpenCodeResponsesProvider, RemoteAPIProvider

    spec = ARMS[arm]
    effort = spec["effort"]
    quiet_log = log or (lambda _message: None)
    dry = mode == "dry"

    if spec["leg"] == "muse":
        if dry:
            api_key = DRY_DUMMY_KEY
        else:
            api_key = (
                str(os.environ.get("OPENCODE_GO_API_KEY", "") or "").strip()
                or str(getattr(cfg, "opencode_api_key", "") or "")
            )
        common: dict[str, Any] = dict(
            circuit_failure_threshold=1_000_000 if dry else int(cfg.provider_circuit_failure_threshold),
            circuit_cooldown_sec=1.0 if dry else float(cfg.provider_circuit_cooldown_sec),
            admission_retry_enable=False if dry else bool(cfg.admission_retry_enable),
            admission_max_retries=0 if dry else int(cfg.admission_max_retries),
            admission_backoff_initial_sec=0.0 if dry else float(cfg.admission_backoff_initial_sec),
            admission_backoff_max_sec=0.0 if dry else float(cfg.admission_backoff_max_sec),
        )
        leg = OpenCodeResponsesProvider(
            base_url=str(cfg.opencode_base_url or ""),
            api_key=api_key,
            model=str(cfg.opencode_muse_model or MUSE_MODEL_DEFAULT),
            reasoning_effort=effort,
            timeout_sec=float(cfg.opencode_timeout_sec),
            max_output_tokens=int(cfg.opencode_max_output_tokens),
            reasoning_token_reserve=int(cfg.opencode_muse_reasoning_token_reserve),
            session_scope=str(cfg.opencode_session_scope),
            log=quiet_log,
            **common,
        )
    else:
        if dry:
            api_key = DRY_DUMMY_KEY
            base_url = ""
        else:
            api_key = str(os.environ.get("OPENAI_API_KEY", "") or "").strip()
            base_url = str(os.environ.get("OPENAI_BASE_URL", "") or "").strip()
        fallback_model = str(getattr(cfg, "opencode_fallback_model", "") or LUNA_MODEL_DEFAULT)
        leg = RemoteAPIProvider(
            api_key=api_key,
            base_url=base_url,
            primary_model=fallback_model,
            fallback_models=[],
            analytics_model=fallback_model,
            reasoning_effort=effort,
            timeout_sec=float(cfg.openai_timeout_sec),
            max_output_tokens=int(cfg.max_output_tokens),
            prompt_cache_enable=bool(cfg.prompt_cache_enable),
            prompt_cache_key=str(cfg.prompt_cache_key),
            prompt_cache_retention=str(cfg.prompt_cache_retention),
            service_tier=str(getattr(cfg, "opencode_fallback_service_tier", "") or "flex"),
            flex_unavailable_retry_enable=False if dry else bool(cfg.flex_unavailable_retry_enable),
            flex_unavailable_max_retries=0 if dry else int(cfg.flex_unavailable_max_retries),
            flex_unavailable_cooldown_sec=0.0 if dry else float(cfg.flex_unavailable_cooldown_sec),
            circuit_failure_threshold=1_000_000 if dry else int(cfg.provider_circuit_failure_threshold),
            circuit_cooldown_sec=1.0 if dry else float(cfg.provider_circuit_cooldown_sec),
            admission_retry_enable=False if dry else bool(cfg.admission_retry_enable),
            admission_max_retries=0 if dry else int(cfg.admission_max_retries),
            admission_backoff_initial_sec=0.0 if dry else float(cfg.admission_backoff_initial_sec),
            admission_backoff_max_sec=0.0 if dry else float(cfg.admission_backoff_max_sec),
            log=quiet_log,
        )
    if client_factory is not None:
        # The base transport caches its client; inject BEFORE first use.
        leg._client_factory = client_factory
        leg._client = None
    return leg


def arm_config_block(arm: str, leg: Any) -> dict:
    """Result-JSON ``config`` block -- leg-visible facts only, never a key."""
    return {
        "arm": arm,
        "effort": ARMS[arm]["effort"],
        "model": str(leg.model_for_role("analyst") or ""),
        "provider_mode": str(leg.provider_mode),
        "provider_id": str(leg.provider_id),
        "reasoning_token_reserve": int(getattr(leg, "reasoning_token_reserve", 0) or 0),
        "max_output_tokens": int(getattr(leg, "max_output_tokens", 0) or 0),
        "timeout_sec": float(getattr(leg, "timeout_sec", 0.0) or 0.0),
        "session_scope": str(getattr(leg, "session_scope", "") or ""),
        "service_tier": str(getattr(leg, "service_tier", "") or ""),
    }


# ---------------------------------------------------------------------------
# Dry fake client (capture style -- see the module docstring for the proof)
# ---------------------------------------------------------------------------

class DryWireCaptured(RuntimeError):
    """Sentinel raised by the dry fake AFTER recording the wire kwargs.

    Deliberately NOT a ValueError/TypeError (it must not be absorbed as a schema
    repair) and with no retryable/admission markers in its text or attributes,
    so the production transport classifies it as one non-retryable
    PROVIDER_TRANSPORT_ERROR and makes exactly one call."""

    def __init__(self) -> None:
        super().__init__("offline-benchmark-dry-wire-captured")


class _DryFakeResponses:
    def __init__(self, owner: "DryFakeClient") -> None:
        self._owner = owner

    def create(self, **kwargs):  # noqa: ANN003 - mirrors the SDK signature
        self._owner.calls.append(dict(kwargs))
        raise DryWireCaptured()


class DryFakeClient:
    """Fake OpenAI-compatible client recording every ``responses.create`` call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def factory(self, **_factory_kwargs) -> "DryFakeClient":
        return self

    def with_options(self, **_options) -> "DryFakeClient":
        return self

    @property
    def responses(self) -> _DryFakeResponses:
        return _DryFakeResponses(self)


# ---------------------------------------------------------------------------
# Child: one (request, arm) pair, fresh process, isolated mutable state
# ---------------------------------------------------------------------------

def configure_child_env(scratch: Path, *, mode: str, bus: Path) -> None:
    """Redirect EVERY mutable path the driven pipeline can touch, BEFORE
    ``ai_gate`` is imported (WP1_SPEC step 1; BEN-006)."""
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "bus").mkdir(parents=True, exist_ok=True)
    os.environ["AI_USAGE_LOG_ENABLE"] = "false"
    os.environ["OPENAI_USAGE_LOG_ENABLE"] = "false"
    os.environ["AI_DECISION_CACHE_ENABLE"] = "false"
    os.environ["AI_DECISION_CACHE_FILE"] = str(scratch / "decision_cache.jsonl")
    os.environ["PO3_SHADOW_LEDGER_PATH"] = str(scratch / "shadow.jsonl")
    os.environ["AI_REQUEST_RESPONSE_FINGERPRINT_FILE"] = str(scratch / "fingerprints.jsonl")
    os.environ["AI_COST_REPORT_FILE"] = str(scratch / "ai_cost_report.jsonl")
    os.environ["PO3_AI_BUS_PATH"] = str(scratch / "bus")
    # Safety extension over the literal env list in WP1_SPEC (documented in the
    # WP1 return): if the operator .env enables shadow repeats, the pipeline's
    # repeatability-artifact WRITER would otherwise target python/data.  Point it
    # at a scratch COPY: reads keep production fidelity, writes cannot escape.
    os.environ["AI_SHADOW_REPEAT_ARTIFACT_FILE"] = str(scratch / "repeatability_artifact.json")
    # Controlled harness settings (pre-registered in benchmark_protocol.md,
    # "Benchmark harness controls").  The live repeatability authority gate keys
    # its group by generation_settings_hash, which INCLUDES reasoning_effort, so
    # leaving it on would fail-close every lower-effort arm for a reason that has
    # nothing to do with model quality.  AI_SHADOW_REPEAT_ENABLE is a research
    # feature that would add random extra provider calls per arm.
    # The former is the production template value
    # (docs/runtime_env_template_opencode.txt:455); the latter is an explicit
    # benchmark control (the template enables it at line 456, but shadow repeats
    # are not a decision gate and add random provider calls).
    os.environ["AI_REQUIRE_REPEATABILITY_LIVE"] = "false"
    os.environ["AI_SHADOW_REPEAT_ENABLE"] = "false"
    artifact_source = REPO_PYTHON / "data" / "ai_repeatability_artifact.json"
    artifact_target = scratch / "repeatability_artifact.json"
    if artifact_source.is_file() and not artifact_target.is_file():
        with contextlib.suppress(OSError):
            shutil.copyfile(artifact_source, artifact_target)

    memory_source = REPO_PYTHON / "data" / "ai_trade_memory.sqlite3"
    memory_target = scratch / "ai_trade_memory_copy.sqlite3"
    for suffix, target in (("", memory_target), ("-wal", Path(str(memory_target) + "-wal")),
                           ("-shm", Path(str(memory_target) + "-shm"))):
        source = Path(str(memory_source) + suffix)
        if source.is_file() and not target.is_file():
            with contextlib.suppress(OSError):
                shutil.copyfile(source, target)
    if memory_target.is_file():
        os.environ["AI_TRADE_MEMORY_FILE"] = str(memory_target)
    else:
        os.environ["AI_TRADE_MEMORY_FILE"] = str(scratch / "trade_memory.sqlite3")

    if mode == "dry":
        # A nonexistent path: po3_env resolves no file, load_dotenv loads
        # nothing, and no real credential can enter the process.
        os.environ["PO3_DOTENV_FILE"] = str(scratch / "__no_such_env_file__")
    # live: PO3_DOTENV_FILE deliberately NOT set -> the default python/.env is
    # resolved by the gate's OWN loader via bootstrap_provider_env().


def neutralize_inprocess_writers(ai_gate_mod: Any) -> None:
    """WP1_SPEC step 4: in-process writers and the provider-health probe."""
    ai_gate_mod.LOG_FILE = None
    ai_gate_mod.log = lambda _m: None
    ai_gate_mod._write_ai_cost_report = lambda *a, **k: None
    if hasattr(ai_gate_mod, "_append_fingerprint_record"):
        ai_gate_mod._append_fingerprint_record = lambda *a, **k: None
    # Without this the live-payload path would attempt a real healthcheck call.
    ai_gate_mod._refresh_provider_health = lambda **k: {
        "healthy": True,
        "reason": "benchmark_injected",
    }
    # _load_live_bucket_priors is left as-is: read-only production priors keep
    # the pipeline's authority semantics intact.
    from provider_deadline import RequestDeadline

    RequestDeadline.can_start_attempt = lambda self, now=None: True
    RequestDeadline.provider_timeout_sec = lambda self, configured, now=None: float(configured)
    # Archived LIVE_FORWARD payloads carry MQL's boot-relative
    # request_created_wall_time, so the replayed deadline is backdated to an
    # earlier boot and expired() is true the moment the request starts
    # (measured -127,729,640 ms remaining).  The archived deadline is
    # historical and must not discard a valid replay result; the provider
    # timeout is still bounded by provider_timeout_sec (900 s).  This mirrors
    # the stage-A capture recipe's deadline neutralization
    # (opencode_token_attribution.py), extended to the expiry predicates the
    # full analyst->critic->adjudicator pipeline actually consults.
    RequestDeadline.expired = lambda self, now=None: False
    RequestDeadline.terminal_expired = lambda self, now=None: False


def child_isolation_violations(config: Any, scratch: Path) -> list[str]:
    """Pipeline writers/flags that would escape the pair's scratch dir (BEN-006).

    Checked on the EFFECTIVE gate configuration, not on the environment the
    harness intended: on 2026-09-13 the credential loader re-applied the operator
    .env over the redirects and 118 benchmark decisions reached the production
    decision cache.  A missing attribute is itself a violation, so a renamed
    config field cannot silently disable the guard.
    """
    root = Path(scratch).resolve()
    violations: list[str] = []
    for name in (
        "decision_cache_file", "trade_memory_file", "cost_report_file",
        "request_response_fingerprint_file", "shadow_repeat_artifact_file",
    ):
        if not hasattr(config, name):
            violations.append(f"missing_attr:{name}")
            continue
        try:
            Path(str(getattr(config, name))).resolve().relative_to(root)
        except ValueError:
            violations.append(name)
    for name in ("decision_cache_enable", "shadow_repeat_enable", "require_repeatability_live"):
        if not hasattr(config, name):
            violations.append(f"missing_attr:{name}")
        elif bool(getattr(config, name)):
            violations.append(f"{name}=True")
    return violations


def run_child(request_file: Path, out_path: Path) -> int:
    """The child entry (WP1_SPEC "Architecture" steps 1-9).  A pipeline-level
    provider failure is recorded as data (fail-closed Decision), not as an exit
    error; the exit code is nonzero only if the result file could not be written."""
    spec = read_json_bytes_any_encoding(Path(request_file))
    request_id = str(spec["request_id"])
    arm = str(spec["arm"])
    mode = str(spec.get("mode") or "dry")
    scratch = Path(str(spec["scratch"]))
    bus = Path(str(spec.get("bus") or DEFAULT_BUS))
    started_utc = _now_utc()
    started = time.time()

    configure_child_env(scratch, mode=mode, bus=bus)
    # Addendum A1.2: pin the provider wire per arm BEFORE ai_gate is imported.
    os.environ[WIRE_PROJECTION_ENV] = str(ARMS.get(arm, {}).get("wire_projection") or "canonical")
    if str(REPO_PYTHON) not in sys.path:
        sys.path.insert(0, str(REPO_PYTHON))

    config: dict[str, Any] = {
        "arm": arm,
        "effort": ARMS[arm]["effort"],
        "model": "",
        "provider_mode": "",
        "provider_id": "",
        "reasoning_token_reserve": 0,
        "max_output_tokens": 0,
        "timeout_sec": 0.0,
        "session_scope": "",
        "service_tier": "",
    }
    archive_candidates = sealed_candidates = 0
    attempts: list[dict] = []
    decision = None
    error: BaseException | None = None
    try:
        if mode == "live":
            import po3_env

            po3_env.bootstrap_provider_env()
            # bootstrap_provider_env loads python/.env with override=True, so every
            # redirect above that the operator .env also defines had been replaced
            # by its PRODUCTION value (measured 2026-09-13: decision cache, trade
            # memory, cost report, fingerprints, shadow-repeat and repeatability
            # settings).  Re-apply the isolation and the wire pin AFTER the load.
            configure_child_env(scratch, mode=mode, bus=bus)
            os.environ[WIRE_PROJECTION_ENV] = str(ARMS.get(arm, {}).get("wire_projection") or "canonical")
        import ai_gate

        isolation_violations = child_isolation_violations(ai_gate.AI_CONFIG, scratch)
        if isolation_violations:
            # Fail the pair closed BEFORE any provider call; it is re-run on resume.
            raise RuntimeError("benchmark_isolation_guard:" + ",".join(isolation_violations))

        neutralize_inprocess_writers(ai_gate)

        def recorder(record: Any) -> None:
            try:
                attempts.append(_json_safe(dict(record)))
            except Exception:  # noqa: BLE001 - accounting must never break the call
                attempts.append({"record_unserializable": True})

        fake_client = DryFakeClient() if mode == "dry" else None
        # Protocol "Arms": identical production values, only the effort replaced.
        cfg = dataclasses.replace(
            ai_gate.AI_CONFIG,
            opencode_muse_reasoning_effort=ARMS[arm]["effort"],
        )
        leg = build_arm_leg(
            arm,
            cfg,
            mode=mode,
            client_factory=fake_client.factory if fake_client is not None else None,
        )
        leg.attempt_observer = recorder
        config = arm_config_block(arm, leg)
        # Protocol "Arms": a single injected leg means there is NO fallback chain.
        ai_gate._provider = lambda *_a, **_k: leg

        payload = ai_gate.read_json_any_encoding(Path(str(spec["payload_path"])))
        archive_candidates = len(payload.get("candidates") or [])
        sealed_payload = dict(payload)
        # Production seals the live cohort before the request was frozen
        # (ai_gate._apply_live_candidate_budget) -- replay it for fidelity.
        with contextlib.suppress(BaseException):
            sealed_payload = ai_gate._apply_live_candidate_budget(dict(payload))
        sealed_candidates = len(sealed_payload.get("candidates") or [])

        decision = ai_gate._score_setup_impl(dict(sealed_payload))
    except BaseException as exc:  # noqa: BLE001 - recorded as data, per step 8
        error = exc

    document = build_result_document(
        request_id=request_id,
        arm=arm,
        mode=mode,
        started_utc=started_utc,
        elapsed_sec=time.time() - started,
        config=config,
        archive_candidates=archive_candidates,
        sealed_candidates=sealed_candidates,
        decision=decision,
        attempts=attempts,
        error=error,
    )
    try:
        write_json(Path(out_path), document)
    except Exception as exc:  # noqa: BLE001 - the only genuinely unrecoverable step
        print(f"[child] unrecoverable write failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return 0


# ---------------------------------------------------------------------------
# Parent: scheduling, isolation snapshots, selfcheck (BEN-006/BEN-007)
# ---------------------------------------------------------------------------

def order_arms(request_id: str, arms: Iterable[str], seed: int) -> list[str]:
    """Arm order randomized per request under the fixed seed (BEN-007): no
    time-of-day or cache warming may systematically favour one arm."""
    return sorted(
        arms,
        key=lambda arm: hashlib.sha256(f"{seed}|{request_id}|{arm}".encode("utf-8")).hexdigest(),
    )


def completed_ok(results_dir: Path, request_id: str, arm: str, *, mode: str) -> bool:
    """A pair counts as done only for the mode that produced it: a dry result
    can never be mistaken for a completed live pair, and vice versa (BEN-007).
    Pre-mode result files (no ``mode`` key) are deliberately NOT treated as
    complete and are re-run."""
    path = result_path(results_dir, request_id, arm)
    if not path.is_file():
        return False
    try:
        result = read_json_bytes_any_encoding(path)
        # A pair refused by the Go usage limit never reached the model: it is
        # not a completed observation of the arm and is re-run after the reset.
        return bool(
            result.get("status") == "ok" and result.get("mode") == mode and not usage_limit_refused(result)
        )
    except Exception:  # noqa: BLE001 - a corrupt file is simply re-run
        return False


def build_pair_plan(
    request_ids: list[str],
    arms: list[str],
    results_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    mode: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Resumable pair schedule (BEN-007).  Pure over the filesystem; returns
    ``(todo, skipped_ok)``.  Skip status is mode-aware (see ``completed_ok``)."""
    todo: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for request_id in request_ids:
        for arm in order_arms(request_id, arms, seed):
            if completed_ok(results_dir, request_id, arm, mode=mode):
                skipped.append((request_id, arm))
            else:
                todo.append((request_id, arm))
    return todo, skipped


def snapshot_path(path: Path) -> dict:
    try:
        stat = Path(path).stat()
        return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        return {"exists": False, "size": None, "mtime_ns": None}


def snapshot_tree(path: Path) -> dict:
    """Aggregate metadata digest of a directory (names + sizes + mtimes)."""
    path = Path(path)
    if not path.is_dir():
        return {"exists": False, "entries": None, "digest": None}
    lines: list[str] = []
    for child in sorted(path.rglob("*")):
        if child.is_file():
            try:
                stat = child.stat()
            except OSError:
                continue
            lines.append(f"{child.relative_to(path)}|{stat.st_size}|{stat.st_mtime_ns}")
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest() if lines else "empty"
    return {"exists": True, "entries": len(lines), "digest": digest}


def isolation_snapshot(bus: Path, repo_python: Path) -> dict:
    """Snapshot targets named by BEN-006 / WP1_SPEC ``run`` isolation."""
    bus = Path(bus)
    files = {
        "bus/logs/openai_usage.ndjson": bus / "logs" / "openai_usage.ndjson",
        "bus/logs/opencode_go_attempts.ndjson": bus / "logs" / "opencode_go_attempts.ndjson",
        "bus/logs/shadow_candidates.jsonl": bus / "logs" / "shadow_candidates.jsonl",
        "python/data/ai_trade_memory.sqlite3": repo_python / "data" / "ai_trade_memory.sqlite3",
        "python/data/ai_decision_cache.jsonl": repo_python / "data" / "ai_decision_cache.jsonl",
        "python/data/ai_repeatability_artifact.json": repo_python / "data" / "ai_repeatability_artifact.json",
        "python/logs/ai_cost_report.jsonl": repo_python / "logs" / "ai_cost_report.jsonl",
        "python/logs/ai_request_response_fingerprints.jsonl": repo_python / "logs" / "ai_request_response_fingerprints.jsonl",
    }
    dirs = {
        "bus/completed": bus / "completed",
        "bus/rejected": bus / "rejected",
        "bus/response_debug": bus / "response_debug",
        "bus/requests": bus / "requests",
        "bus/responses": bus / "responses",
        "bus/processing": bus / "processing",
        "python/logs": repo_python / "logs",
    }
    out = {
        "captured_utc": _now_utc(),
        "files": {name: snapshot_path(p) for name, p in files.items()},
        "trees": {name: snapshot_tree(p) for name, p in dirs.items()},
    }
    # The digest folds the per-entry stats: any size/mtime move flips it.
    out["digest"] = hashlib.sha256(
        json.dumps(
            {
                "files": {k: (v["exists"], v["size"], v["mtime_ns"]) for k, v in out["files"].items()},
                "trees": {k: (v["exists"], v["digest"]) for k, v in out["trees"].items()},
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return out


def usage_limit_refused(result: dict) -> bool:
    """True when a role's LAST attempt is a refused, unretried 429.

    The attempt ledger does not carry the response body, so the literal
    ``GoUsageLimitError`` never reaches the record: the 2026-09-13 weekly-limit
    refusals ("Weekly usage limit reached. Resets in ...") arrived as
    ``RateLimitError``, 264 times, and the run did not stop.  The transport
    admission-retries congestion 429s, so a 429 that was refused, not billed and
    never retried, with no later attempt for the role, is a usage-limit refusal.
    """
    last_by_role: dict[str, dict] = {}
    for attempt in result.get("attempts") or []:
        if isinstance(attempt, dict):
            last_by_role[str(attempt.get("role") or "")] = attempt
    for attempt in last_by_role.values():
        retries = attempt.get("retry_counts") or {}
        if (
            attempt.get("http_status") == 429
            and str(attempt.get("billing_state") or "") == "not_billed_admission_refused"
            and int(retries.get("admission_retries") or 0) == 0
        ):
            return True
    return False


def go_limit_hit(result: dict) -> bool:
    """Detect a Go usage-limit 429 in one result's attempt records / error text
    (WP1_SPEC ``run``: exact evidence shape from the ledger: http_status 429 with
    GoUsageLimitError, or the class name in the recorded error text)."""
    if usage_limit_refused(result):
        return True
    for attempt in result.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        blob = json.dumps(attempt, default=str).lower()
        if attempt.get("http_status") == 429 and "gousagelimit" in blob:
            return True
        if "gousagelimiterror" in str(attempt.get("error_category") or "").lower():
            return True
    error = result.get("error") or {}
    if isinstance(error, dict):
        text = f"{error.get('type', '')} {error.get('message', '')}".lower()
    else:
        text = str(error).lower()
    return "gousagelimiterror" in text


def launch_child(pair: dict, repo_python: Path, child_timeout_sec: float) -> int:
    """Spawn one fresh child (BEN-006).  Single seam so the focused test can
    prove the scheduler plans without spawning."""
    request_file = pair["child_request"]
    cmd = [
        sys.executable,
        "-m",
        "tools.opencode_reasoning_benchmark",
        "_child",
        "--request-file", str(request_file),
        "--arm", str(pair["arm"]),
        "--out", str(pair["out"]),
    ]
    completed = subprocess.run(  # fixed argv, no shell
        cmd,
        cwd=str(repo_python),
        capture_output=True,
        text=True,
        timeout=child_timeout_sec,
    )
    with contextlib.suppress(OSError):
        Path(request_file).with_name("child_stderr.txt").write_text(
            (completed.stdout or "") + "\n" + (completed.stderr or ""), encoding="utf-8"
        )
    return completed.returncode


def cmd_run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_file = run_dir / "benchmark_manifest.json"
    if not manifest_file.is_file():
        print(f"[run] missing manifest {manifest_file}; run the manifest subcommand first", file=sys.stderr)
        return 2
    manifest = read_json_bytes_any_encoding(manifest_file)
    seed = int(manifest.get("seed") or DEFAULT_SEED)
    bus = Path(str(manifest.get("bus") or args.bus or DEFAULT_BUS))
    arms = [a.strip() for a in (args.arms or "").split(",") if a.strip()] or list(ARM_ORDER)
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        print(f"[run] unknown arms: {unknown}; valid: {list(ARM_ORDER)}", file=sys.stderr)
        return 2
    rows = {row["request_id"]: row for row in manifest.get("rows") or []}
    request_ids = [row["request_id"] for row in manifest.get("rows") or []]
    if args.requests:
        wanted = [r.strip() for r in args.requests.split(",") if r.strip()]
        request_ids = [
            rid for rid in request_ids
            if rid in wanted or any(rid.endswith(w) for w in wanted)
        ]
    if args.mode == "dry" and args.dry_requests > 0:
        request_ids = request_ids[: args.dry_requests]
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    scratch_root = run_dir / "scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)

    todo, skipped = build_pair_plan(request_ids, arms, results_dir, seed=seed, mode=args.mode)
    print(
        f"[run] mode={args.mode} requests={len(request_ids)} arms={len(arms)} "
        f"pairs_todo={len(todo)} pairs_skipped_ok={len(skipped)}"
    )
    isolation_before = isolation_snapshot(bus, REPO_PYTHON)
    stop_event = threading.Event()
    usage_limit_events: list[dict] = []

    def prepare_pair(request_id: str, arm: str) -> dict:
        row = rows[request_id]
        scratch = scratch_root / f"{safe_component(request_id)}__{arm}"
        scratch.mkdir(parents=True, exist_ok=True)
        child_request = {
            "request_id": request_id,
            "arm": arm,
            "mode": args.mode,
            "payload_path": row["archive_path"],
            "scratch": str(scratch),
            "bus": str(bus),
            "seed": seed,
        }
        request_file = scratch / "child_request.json"
        write_json(request_file, child_request)
        return {
            "request_id": request_id,
            "arm": arm,
            "out": result_path(results_dir, request_id, arm),
            "child_request": request_file,
        }

    def execute(pair: dict) -> dict:
        if stop_event.is_set():
            return {"request_id": pair["request_id"], "arm": pair["arm"], "status": "stopped"}
        try:
            launch_child(pair, REPO_PYTHON, args.child_timeout_sec)
        except subprocess.TimeoutExpired:
            write_json(pair["out"], {
                "request_id": pair["request_id"], "arm": pair["arm"], "status": "error",
                "error": {"type": "ChildTimeout", "message": f"exceeded {args.child_timeout_sec}s"},
                "attempts": [],
            })
        except OSError as exc:
            write_json(pair["out"], {
                "request_id": pair["request_id"], "arm": pair["arm"], "status": "error",
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "attempts": [],
            })
        try:
            result = read_json_bytes_any_encoding(pair["out"])
        except Exception as exc:  # noqa: BLE001 - child produced nothing
            result = {
                "request_id": pair["request_id"], "arm": pair["arm"], "status": "error",
                "error": {"type": type(exc).__name__, "message": "child produced no result file"},
                "attempts": [],
            }
            write_json(pair["out"], result)
        if go_limit_hit(result):
            # BEN-007: stop launching NEW pairs cleanly; already-running children
            # finish (killing them would orphan possibly-billed calls).
            if not stop_event.is_set():
                usage_limit_events.append(
                    {"request_id": pair["request_id"], "arm": pair["arm"], "detected_utc": _now_utc()}
                )
            stop_event.set()
        if not args.keep_scratch:
            shutil.rmtree(Path(pair["child_request"]).parent, ignore_errors=True)
        decision = (result or {}).get("decision") or {}
        print(
            f"[bench] {pair['request_id']} {pair['arm']} status={result.get('status')} "
            f"state={decision.get('decision_state')} elapsed={result.get('elapsed_sec')}s "
            f"attempts={len(result.get('attempts') or [])}",
            flush=True,
        )
        return result

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = [pool.submit(execute, prepare_pair(rid, arm)) for rid, arm in todo]
            for future in concurrent.futures.as_completed(futures):
                with contextlib.suppress(Exception):
                    future.result()

    isolation_after = isolation_snapshot(bus, REPO_PYTHON)
    changed_named: list[str] = []
    for name, before in isolation_before["files"].items():
        after = isolation_after["files"].get(name, {})
        if (before.get("exists"), before.get("size"), before.get("mtime_ns")) != (
            after.get("exists"), after.get("size"), after.get("mtime_ns")
        ):
            changed_named.append(name)
    for name, before in isolation_before["trees"].items():
        after = isolation_after["trees"].get(name, {})
        if before.get("digest") != after.get("digest") or before.get("exists") != after.get("exists"):
            changed_named.append(name)

    arms_selfcheck: dict[str, dict] = {}
    for arm in arms:
        efforts: set[str] = set()
        provider_ids: set[str] = set()
        models: set[str] = set()
        fallback_rows = 0
        pair_count = 0
        for request_id in request_ids:
            path = result_path(results_dir, request_id, arm)
            if not path.is_file():
                continue
            try:
                result = read_json_bytes_any_encoding(path)
            except Exception:  # noqa: BLE001
                continue
            # Mode-aware: a dry result must not attest to the wire behaviour of
            # a live arm run (or vice versa); only current-mode files count.
            if str(result.get("mode")) != args.mode:
                continue
            pair_count += 1
            for attempt in result.get("attempts") or []:
                if not isinstance(attempt, dict):
                    continue
                effort_seen = str(attempt.get("reasoning_effort_sent") or "")
                if effort_seen:
                    efforts.add(effort_seen)
                provider_ids.add(str(attempt.get("provider_id") or ""))
                models.add(str(attempt.get("model") or ""))
                fallback_reason = str(attempt.get("fallback_reason") or "")
                route_stage = str(attempt.get("route_stage"))
                if fallback_reason or route_stage not in {"", "0", "None"}:
                    fallback_rows += 1
        arms_selfcheck[arm] = {
            "effort_requested": ARMS[arm]["effort"],
            "effort_observed_on_wire": sorted(efforts),
            "wire_matches_requested": bool(efforts) and efforts == {ARMS[arm]["effort"]},
            "provider_ids": sorted(provider_ids),
            "models": sorted(models),
            "fallback_rows": fallback_rows,
            "no_fallback": bool(provider_ids) and len(provider_ids) == 1 and fallback_rows == 0 and len(models) <= 1,
            "pairs_with_results": pair_count,
        }
    selfcheck = {
        "generated_utc": _now_utc(),
        "mode": args.mode,
        "harness_controls": {
            "AI_REQUIRE_REPEATABILITY_LIVE": os.environ.get("AI_REQUIRE_REPEATABILITY_LIVE"),
            "AI_SHADOW_REPEAT_ENABLE": os.environ.get("AI_SHADOW_REPEAT_ENABLE"),
        },
        "requests": request_ids,
        "arms": arms,
        "seed": seed,
        "pairs_planned": len(todo),
        "pairs_skipped_ok": [list(pair) for pair in skipped],
        "stopped_after_go_limit": stop_event.is_set(),
        "go_usage_limit_events": usage_limit_events,
        "arm_wire_selfcheck": arms_selfcheck,
        "isolation": {
            "before": isolation_before,
            "after": isolation_after,
            "unchanged": isolation_before["digest"] == isolation_after["digest"],
            "changed_paths": changed_named,
            "note": (
                "A changed path while the production ai_gate/MT5 runtime is live "
                "is NOT proof the benchmark wrote it; the child-side redirections "
                "and in-process writer neutralization are the guarantee, this "
                "snapshot is the observation. Re-run with the gate stopped for a "
                "clean before/after."
            ),
        },
    }
    write_json(run_dir / "selfcheck.json", selfcheck)
    print(
        f"[run] done; selfcheck -> {run_dir / 'selfcheck.json'}; "
        f"isolation_unchanged={selfcheck['isolation']['unchanged']}"
    )
    if args.mode == "dry":
        print(
            "[run] dry proof boundary: effort-on-wire + single-provider/no-fallback "
            "ARE proven; dry decisions are the pipeline's fail-closed "
            "provider-failure outcome and prove nothing about answer quality "
            "(see module docstring)."
        )
    return 0


# ---------------------------------------------------------------------------
# report (BEN-004 / BEN-005)
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, total: int, z: float = 1.959964) -> list[float] | None:
    """95% Wilson score interval, exactly the formula frozen in WP1_SPEC."""
    n = int(total)
    k = int(successes)
    if n <= 0:
        return None
    p = k / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, center - half), 6), round(min(1.0, center + half), 6)]


def _rate(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
        "wilson_95_ci": wilson_interval(numerator, denominator),
    }


def _avg(values: list) -> float | None:
    vals = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(vals) / len(vals), 6) if vals else None


def _p50(values: list) -> float | None:
    vals = sorted(float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    return vals[len(vals) // 2] if vals else None


def _pct_delta(arm_value, high_value):
    if not isinstance(arm_value, (int, float)) or not isinstance(high_value, (int, float)) or not high_value:
        return None
    return round((arm_value - high_value) / abs(high_value) * 100.0, 1)


def result_pair_flags(result: dict) -> dict:
    """BEN-004 derived flags for one (request, arm) result."""
    decision = result.get("decision") or {}
    tier = str(decision.get("decision_quality_tier") or "")
    source = str(decision.get("decision_source") or "")
    status_ok = str(result.get("status")) == "ok"
    schema_valid = bool(
        status_ok and tier == SCHEMA_FULL_STRUCTURED and decision.get("mandatory_fields_complete") is True
    )
    semantic_valid = bool(
        status_ok
        and tier == SCHEMA_FULL_STRUCTURED
        and not (decision.get("missing_mandatory_fields") or [])
        and not (decision.get("invalid_mandatory_fields") or [])
        and source not in INFRA_DECISION_SOURCES
    )
    infra_failed = bool(not status_ok or tier != SCHEMA_FULL_STRUCTURED or source in INFRA_DECISION_SOURCES)
    return {
        "status_ok": status_ok,
        "decision_state": str(decision.get("decision_state") or ""),
        "python_final_allow": bool(decision.get("python_final_allow") is True),
        "selected_candidate_id": str(decision.get("selected_candidate_id") or ""),
        "veto_enabled": bool(decision.get("veto_enabled") is True),
        "veto_code": str(decision.get("veto_code") or ""),
        "llm_quality_score": decision.get("llm_quality_score"),
        "tier": tier,
        "decision_source": source,
        "schema_valid": schema_valid,
        "semantic_valid": semantic_valid,
        "infra_failed": infra_failed,
    }


def role_rollup(result: dict) -> dict:
    """Per-role latency/tokens/cost from the attempt records (provider truth).

    Cost is the published Go rate via ``expected_go_usage_usd`` already computed
    on each attempt record by ``opencode_go_accounting``; when the module does
    not price a model (the Luna arm is an OpenAI-account model and is not
    priced), the rollup says so via ``cost_unpriced`` and the summary reports
    null with a note (protocol Reporting)."""
    roll: dict[str, dict] = {
        role: {
            "latency_sec": 0.0, "attempts": 0, "input_tokens": 0,
            "cached_read_tokens": 0, "reasoning_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "cost_unpriced": False,
        }
        for role in ROLES
    }
    for attempt in result.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        role = str(attempt.get("role") or "analyst")
        bucket = roll.setdefault(role, dict(roll["analyst"]))
        bucket["attempts"] += 1
        bucket["latency_sec"] += float(attempt.get("latency_sec") or 0.0)
        for key in ("input_tokens", "cached_read_tokens", "reasoning_tokens", "output_tokens"):
            value = attempt.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                bucket[key] += value
        pricing = attempt.get("expected_go_usage_usd")
        if isinstance(pricing, dict) and pricing:
            # One variant for muse ("standard"); deepseek's published
            # off-peak/peak ambiguity would never silently fold -- report the
            # first sorted variant and keep the dict on the attempt row.
            bucket["cost_usd"] += float(sorted(pricing.items())[0][1])
        elif pricing is None:
            bucket["cost_unpriced"] = True
    for bucket in roll.values():
        bucket["latency_sec"] = round(bucket["latency_sec"], 3)
        bucket["cost_usd"] = round(bucket["cost_usd"], 8)
    return roll


def total_cost_usd(result: dict) -> float | None:
    roll = role_rollup(result)
    priced = [b for b in roll.values() if not b["cost_unpriced"] or b["cost_usd"]]
    if not priced and roll:
        return None
    if all(b["cost_unpriced"] and not b["cost_usd"] for b in roll.values()):
        return None
    return round(sum(b["cost_usd"] for b in roll.values()), 8)


def vetoed_candidate_ids(result: dict) -> set[str]:
    """Candidates this result REJECTed with an evidence-backed veto, per
    candidate (BEN-004 S1 uses the per-candidate assessment; the top-level
    veto fields are the fallback for older shapes)."""
    decision = result.get("decision") or {}
    out: set[str] = set()
    rows = decision.get("candidate_assessments")
    if isinstance(rows, list) and rows:
        for row in rows:
            if not isinstance(row, dict):
                continue
            veto = row.get("veto") if isinstance(row.get("veto"), dict) else {}
            if str(row.get("decision_state") or "") == "REJECT" and veto.get("enabled") is True:
                ident = str(row.get("candidate_id") or "").strip()
                if ident:
                    out.add(ident)
        return out
    if str(decision.get("decision_state") or "") == "REJECT" and decision.get("veto_enabled") is True:
        ident = str(decision.get("selected_candidate_id") or "").strip()
        if ident:
            out.add(ident)
    return out


def aggregate_results(manifest: dict, results: list[dict]) -> dict:
    """BEN-004/BEN-005 aggregation.  Pure over the given results; the two HIGH
    arms are the reference and the noise floor."""
    by_request: dict[str, dict[str, dict]] = {}
    for result in results:
        by_request.setdefault(str(result.get("request_id") or ""), {})[str(result.get("arm") or "")] = result
    request_ids = [row["request_id"] for row in manifest.get("rows") or [] if row.get("request_id") in by_request]
    arms = sorted({arm for per in by_request.values() for arm in per})
    summary: dict[str, Any] = {}

    for arm in arms:
        rows = [(rid, by_request[rid][arm]) for rid in request_ids if arm in by_request[rid]]
        completed = [(rid, r) for rid, r in rows if str(r.get("status")) == "ok"]
        flags = [result_pair_flags(r) for _rid, r in completed]
        rollups = [role_rollup(r) for _rid, r in completed]
        state_dist: dict[str, int] = {}
        for flag in flags:
            key = flag["decision_state"] or "?"
            state_dist[key] = state_dist.get(key, 0) + 1
        n_ok = len(completed)
        role_agg = {}
        for role in ROLES:
            role_agg[role] = {
                "latency_sec_avg": _avg([roll[role]["latency_sec"] for roll in rollups]),
                "latency_sec_p50": _p50([roll[role]["latency_sec"] for roll in rollups]),
                "input_tokens_avg": _avg([roll[role]["input_tokens"] for roll in rollups]),
                "cached_read_tokens_avg": _avg([roll[role]["cached_read_tokens"] for roll in rollups]),
                "reasoning_tokens_avg": _avg([roll[role]["reasoning_tokens"] for roll in rollups]),
                "output_tokens_avg": _avg([roll[role]["output_tokens"] for roll in rollups]),
                "cost_usd_avg": _avg([roll[role]["cost_usd"] for roll in rollups]),
                "cost_usd_total": round(sum(roll[role]["cost_usd"] for roll in rollups), 8),
                "cost_unpriced_any": any(roll[role]["cost_unpriced"] for roll in rollups),
            }
        summary[arm] = {
            "effort_requested": ARMS.get(arm, {}).get("effort"),
            "n_planned": len(rows),
            "n_completed_ok": n_ok,
            "n_error": len(rows) - n_ok,
            "decision_state_distribution": state_dist,
            "final_allow_rate": _rate(sum(1 for f in flags if f["python_final_allow"]), n_ok),
            "approve_rate": _rate(sum(1 for f in flags if f["decision_state"] == "APPROVE"), n_ok),
            "schema_valid_rate": _rate(sum(1 for f in flags if f["schema_valid"]), n_ok),
            "semantic_valid_rate": _rate(sum(1 for f in flags if f["semantic_valid"]), n_ok),
            "infra_failure_rate": _rate(
                sum(1 for f in flags if f["infra_failed"]) + (len(rows) - n_ok), len(rows)
            ),
            "roles": role_agg,
        }

    def pair_stat(arm_x: str, arm_y: str, clean_only: bool = False) -> dict:
        shared = [
            rid for rid in request_ids
            if arm_x in by_request[rid] and arm_y in by_request[rid]
            and str(by_request[rid][arm_x].get("status")) == "ok"
            and str(by_request[rid][arm_y].get("status")) == "ok"
            and not (
                clean_only
                and (
                    result_pair_flags(by_request[rid][arm_x])["infra_failed"]
                    or result_pair_flags(by_request[rid][arm_y])["infra_failed"]
                )
            )
        ]
        pairs = [(rid, by_request[rid][arm_x], by_request[rid][arm_y]) for rid in shared]
        out: dict[str, Any] = {"n": len(pairs)}
        for name, getter in (
            ("decision_state", lambda r: result_pair_flags(r)["decision_state"]),
            ("final_allow", lambda r: result_pair_flags(r)["python_final_allow"]),
            ("selected_candidate", lambda r: result_pair_flags(r)["selected_candidate_id"]),
            ("veto_code", lambda r: (result_pair_flags(r)["veto_enabled"], result_pair_flags(r)["veto_code"])),
        ):
            agree = sum(1 for _rid, x, y in pairs if getter(x) == getter(y))
            out[f"{name}_agreement"] = _rate(agree, len(pairs))
        diffs = [
            abs(float(result_pair_flags(x)["llm_quality_score"]) - float(result_pair_flags(y)["llm_quality_score"]))
            for _rid, x, y in pairs
            if isinstance(result_pair_flags(x)["llm_quality_score"], (int, float))
            and isinstance(result_pair_flags(y)["llm_quality_score"], (int, float))
        ]
        out["quality_score_mad"] = _avg(diffs)
        return out

    have_both_high = all(arm in arms for arm in HIGH_ARMS)
    noise = pair_stat("muse_high_a", "muse_high_b") if have_both_high else None
    summary["_noise_high_a_vs_high_b"] = noise
    # Addendum A3: agreement over pairs where NEITHER side had an infrastructure
    # outcome.  The literal BEN-004 agreement counts two degraded REJECTs as
    # agreement; on 2026-09-13 shared usage-limit refusals inflated it that way.
    noise_clean = pair_stat("muse_high_a", "muse_high_b", clean_only=True) if have_both_high else None
    summary["_noise_high_a_vs_high_b_clean"] = noise_clean
    for arm in arms:
        if arm != "muse_high_a":
            summary[arm]["agreement_vs_high_a"] = pair_stat(arm, "muse_high_a")
            summary[arm]["agreement_vs_high_a_clean"] = pair_stat(arm, "muse_high_a", clean_only=True)

    # Safety counters S1/S2/S3 over requests where BOTH HIGH runs completed.
    high_pairs = [
        rid for rid in request_ids
        if all(
            arm in by_request[rid] and str(by_request[rid][arm].get("status")) == "ok"
            for arm in HIGH_ARMS
        )
    ]
    high_allows = {
        rid: [result_pair_flags(by_request[rid][arm])["python_final_allow"] for arm in HIGH_ARMS]
        for rid in high_pairs
    }
    # HIGH-vs-HIGH "same kind" noise: how often HIGH itself allows where the
    # other HIGH run does not (report states this definition explicitly).
    s2_noise = sum(1 for rid in high_pairs if len(set(high_allows[rid])) > 1)
    for arm in arms:
        s1 = s2 = s3 = 0
        s1_detail: list[dict] = []
        for rid in high_pairs:
            result = by_request[rid].get(arm)
            if result is None or str(result.get("status")) != "ok":
                continue
            flags = result_pair_flags(result)
            both_veto_rejected = (
                vetoed_candidate_ids(by_request[rid]["muse_high_a"])
                & vetoed_candidate_ids(by_request[rid]["muse_high_b"])
            )
            both_high_allow_false = all(v is False for v in high_allows[rid])
            both_high_allow_true = all(v is True for v in high_allows[rid])
            if (
                arm not in HIGH_ARMS
                and (flags["decision_state"] == "APPROVE" or flags["python_final_allow"])
                and both_veto_rejected
                and flags["selected_candidate_id"] in both_veto_rejected
            ):
                s1 += 1
                s1_detail.append({"request_id": rid, "candidate": flags["selected_candidate_id"]})
            if arm not in HIGH_ARMS and flags["python_final_allow"] and both_high_allow_false:
                s2 += 1
            if arm not in HIGH_ARMS and flags["decision_state"] in {"REJECT", "ABSTAIN"} and both_high_allow_true:
                s3 += 1
        summary[arm]["safety"] = {
            "S1": s1,
            "S2": s2,
            "S3": s3,
            "S1_detail": s1_detail,
            "n_requests_with_both_high_completed": len(high_pairs),
            "S2_high_vs_high_noise_count": s2_noise,
        }
        conditions: dict[str, Any] = {}
        if arm not in HIGH_ARMS and noise is not None and "muse_high_a" in summary:
            high_a = summary["muse_high_a"]
            arm_agree = (summary[arm].get("agreement_vs_high_a") or {}).get("decision_state_agreement") or {}
            noise_agree = noise.get("decision_state_agreement") or {}
            arm_schema = (summary[arm]["schema_valid_rate"] or {}).get("rate")
            high_schema = (high_a["schema_valid_rate"] or {}).get("rate")
            arm_sem = (summary[arm]["semantic_valid_rate"] or {}).get("rate")
            high_sem = (high_a["semantic_valid_rate"] or {}).get("rate")
            arm_infra = (summary[arm]["infra_failure_rate"] or {}).get("rate")
            high_infra = (high_a["infra_failure_rate"] or {}).get("rate")
            conditions = {
                "a_S1_zero": s1 == 0,
                "b_S2_within_high_vs_high_noise": s2 <= s2_noise,
                "c_schema_valid_ge_high_minus_2pp": (
                    arm_schema is not None and high_schema is not None and arm_schema >= high_schema - 0.02
                ),
                "c_semantic_valid_ge_high_minus_2pp": (
                    arm_sem is not None and high_sem is not None and arm_sem >= high_sem - 0.02
                ),
                "d_state_agreement_ge_noise_minus_5pp": (
                    arm_agree.get("rate") is not None and noise_agree.get("rate") is not None
                    and arm_agree["rate"] >= noise_agree["rate"] - 0.05
                ),
                "e_infra_failure_le_high": (
                    arm_infra is not None and high_infra is not None and arm_infra <= high_infra + 1e-12
                ),
            }
            conditions["non_inferior"] = bool(conditions) and all(bool(v) for v in conditions.values())
            clean_agree = summary[arm].get("agreement_vs_high_a_clean") or {}
            clean_noise = summary.get("_noise_high_a_vs_high_b_clean") or {}
            clean_rate = (clean_agree.get("decision_state_agreement") or {}).get("rate")
            clean_noise_rate = (clean_noise.get("decision_state_agreement") or {}).get("rate")
            limit_refused = sum(
                1 for rid in request_ids for other in (arm, "muse_high_a")
                if other in by_request[rid] and usage_limit_refused(by_request[rid][other])
            )
            a3 = {
                "d_clean_state_agreement_ge_clean_noise_minus_5pp": (
                    clean_rate is not None and clean_noise_rate is not None
                    and clean_rate >= clean_noise_rate - 0.05
                ),
                "n_clean_pairs_ge_min": int(clean_agree.get("n") or 0) >= A3_MIN_CLEAN_PAIRS,
                "no_usage_limit_refusals_left": limit_refused == 0,
            }
            literal = {
                k: v for k, v in conditions.items()
                if k not in ("non_inferior", "d_state_agreement_ge_noise_minus_5pp")
            }
            a3["non_inferior_a3"] = all(bool(v) for v in literal.values()) and all(bool(v) for v in a3.values())
            conditions["addendum_a3"] = a3
        summary[arm]["ben005"] = {
            "conditions": conditions,
            "non_inferior": conditions.get("non_inferior", False) if conditions else None,
            "non_inferior_a3": (conditions.get("addendum_a3") or {}).get("non_inferior_a3") if conditions else None,
        }
        if arm not in HIGH_ARMS and "muse_high_a" in summary:
            def role_val(bucket: dict, role: str, key: str) -> float:
                return float(((bucket.get("roles") or {}).get(role) or {}).get(key) or 0.0)

            arm_tok = sum(
                role_val(summary[arm], role, "input_tokens_avg") + role_val(summary[arm], role, "output_tokens_avg")
                for role in ROLES
            )
            arm_cost = sum(role_val(summary[arm], role, "cost_usd_avg") for role in ROLES)
            arm_lat = sum(role_val(summary[arm], role, "latency_sec_avg") for role in ROLES)
            highs = [summary[h] for h in HIGH_ARMS if h in summary]
            high_tok = _avg([
                sum(role_val(h, role, "input_tokens_avg") + role_val(h, role, "output_tokens_avg") for role in ROLES)
                for h in highs
            ])
            high_cost = _avg([sum(role_val(h, role, "cost_usd_avg") for role in ROLES) for h in highs])
            high_lat = _avg([sum(role_val(h, role, "latency_sec_avg") for role in ROLES) for h in highs])
            summary[arm]["savings_vs_high"] = {
                "tokens_per_request_avg": {
                    "arm": round(arm_tok, 1), "high": high_tok, "delta_pct": _pct_delta(arm_tok, high_tok)
                },
                "cost_usd_per_request_avg": {
                    "arm": round(arm_cost, 8), "high": high_cost, "delta_pct": _pct_delta(arm_cost, high_cost)
                },
                "latency_sec_per_request_avg": {
                    "arm": round(arm_lat, 3), "high": high_lat, "delta_pct": _pct_delta(arm_lat, high_lat)
                },
            }
    summary["_meta"] = {
        "generated_utc": _now_utc(),
        "manifest_request_count": manifest.get("request_count"),
        "high_arms": list(HIGH_ARMS),
        "n_requests_with_both_high_completed": len(high_pairs),
        "s2_high_vs_high_noise_count": s2_noise,
        "definitions_note": (
            "S1: arm APPROVE or python_final_allow=true on a candidate BOTH HIGH "
            "runs REJECTed with an evidence-backed per-candidate veto. S2: arm "
            "python_final_allow=true where BOTH HIGH runs' python_final_allow is "
            "false; the BEN-005(b) noise count is the number of requests where the "
            "two HIGH runs themselves disagree on python_final_allow. S3: arm "
            "REJECT/ABSTAIN where both HIGH runs had python_final_allow=true. "
            "selected-candidate agreement compares selected_candidate_id "
            "(empty==empty counts as agreement). schema_valid = tier "
            "FULL_STRUCTURED and mandatory_fields_complete; semantic_valid adds "
            "empty missing/invalid mandatory field lists and a decision_source "
            "that is not an infrastructure outcome; infra_failed = status error "
            "or tier != FULL_STRUCTURED or an infrastructure decision_source."
        ),
    }
    return summary


CSV_COLUMNS = (
    "request_id", "arm", "status", "error_type", "elapsed_sec", "model", "provider_id",
    "effort_requested", "effort_on_wire", "decision_state", "python_final_allow",
    "raw_allow", "allow", "chosen_index", "selected_candidate_id", "veto_enabled",
    "veto_code", "llm_quality_score", "llm_self_reported_confidence",
    "decision_quality_tier", "decision_source", "mandatory_fields_complete",
    "schema_valid", "semantic_valid", "infra_failed", "attempts",
    "analyst_latency_sec", "critic_latency_sec", "adjudicator_latency_sec",
    "analyst_input_tokens", "analyst_reasoning_tokens", "analyst_output_tokens",
    "critic_output_tokens", "adjudicator_output_tokens", "cost_usd",
)


def csv_rows(manifest: dict, results: list[dict]) -> list[dict]:
    rows_by_request = {row["request_id"]: row for row in manifest.get("rows") or []}
    out: list[dict] = []
    for result in sorted(results, key=lambda r: (str(r.get("request_id") or ""), str(r.get("arm") or ""))):
        decision = result.get("decision") or {}
        flags = result_pair_flags(result)
        roll = role_rollup(result)
        efforts = sorted({
            str(a.get("reasoning_effort_sent"))
            for a in (result.get("attempts") or [])
            if isinstance(a, dict) and a.get("reasoning_effort_sent")
        })
        out.append({
            "request_id": result.get("request_id"),
            "arm": result.get("arm"),
            "status": result.get("status"),
            "error_type": (result.get("error") or {}).get("type") if isinstance(result.get("error"), dict) else "",
            "elapsed_sec": result.get("elapsed_sec"),
            "model": (result.get("config") or {}).get("model"),
            "provider_id": (result.get("config") or {}).get("provider_id"),
            "effort_requested": (result.get("config") or {}).get("effort"),
            "effort_on_wire": "|".join(efforts),
            "decision_state": decision.get("decision_state"),
            "python_final_allow": decision.get("python_final_allow"),
            "raw_allow": decision.get("raw_allow"),
            "allow": decision.get("allow"),
            "chosen_index": decision.get("chosen_index"),
            "selected_candidate_id": decision.get("selected_candidate_id"),
            "veto_enabled": decision.get("veto_enabled"),
            "veto_code": decision.get("veto_code"),
            "llm_quality_score": decision.get("llm_quality_score"),
            "llm_self_reported_confidence": decision.get("llm_self_reported_confidence"),
            "decision_quality_tier": decision.get("decision_quality_tier"),
            "decision_source": decision.get("decision_source"),
            "mandatory_fields_complete": decision.get("mandatory_fields_complete"),
            "schema_valid": flags["schema_valid"],
            "semantic_valid": flags["semantic_valid"],
            "infra_failed": flags["infra_failed"],
            "attempts": len(result.get("attempts") or []),
            "analyst_latency_sec": roll["analyst"]["latency_sec"],
            "critic_latency_sec": roll["critic"]["latency_sec"],
            "adjudicator_latency_sec": roll["adjudicator"]["latency_sec"],
            "analyst_input_tokens": roll["analyst"]["input_tokens"],
            "analyst_reasoning_tokens": roll["analyst"]["reasoning_tokens"],
            "analyst_output_tokens": roll["analyst"]["output_tokens"],
            "critic_output_tokens": roll["critic"]["output_tokens"],
            "adjudicator_output_tokens": roll["adjudicator"]["output_tokens"],
            "cost_usd": total_cost_usd(result),
            "archive_candidates": (result.get("wire_payload") or {}).get("archive_candidates"),
            "sealed_candidates": (result.get("wire_payload") or {}).get("sealed_candidates"),
            "historical_decision_state": (
                (rows_by_request.get(str(result.get("request_id") or ""), {}).get("historical_decision") or {}).get("decision_state")
            ),
        })
    return out


def render_report_md(manifest: dict, summary: dict, results: list[dict]) -> str:
    lines = [
        "# Muse reasoning-effort non-inferiority benchmark (stage C1, BEN-001..007)",
        "",
        f"Generated: {summary.get('_meta', {}).get('generated_utc')}",
        f"Manifest: {manifest.get('request_count')} frozen requests, seed {manifest.get('seed')}, generated {manifest.get('generated_utc')}",
        f"Results files: {len(results)}; requests with both HIGH runs completed: {summary.get('_meta', {}).get('n_requests_with_both_high_completed')}",
        "",
        "## Per-arm summary",
        "",
        "| arm | effort | n_ok | APPROVE | allow% | state-agree vs high_a (CI) | schema% | semantic% | infra% | S1 | S2 | S3 | tokens/req | cost/req | latency/req | BEN-005 verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def fmt_rate(block, scale=100.0):
        if not isinstance(block, dict) or block.get("rate") is None:
            return "-"
        return f"{block['rate'] * scale:.1f}% ({block['numerator']}/{block['denominator']})"

    for arm in [a for a in ARM_ORDER if a in summary]:
        s = summary[arm]
        agree = (s.get("agreement_vs_high_a") or {}).get("decision_state_agreement") or {}
        ci = agree.get("wilson_95_ci")
        agree_text = (
            f"{agree.get('rate', 0) * 100:.1f}% [{ci[0] * 100:.1f},{ci[1] * 100:.1f}]"
            if ci else "-"
        )
        sav = s.get("savings_vs_high") or {}
        safety = s.get("safety") or {}
        verdict = s.get("ben005") or {}
        n_inf = verdict.get("non_inferior")
        lines.append(
            f"| {arm} | {s.get('effort_requested')} | {s.get('n_completed_ok')}/{s.get('n_planned')} "
            f"| {s.get('decision_state_distribution', {}).get('APPROVE', 0)} "
            f"| {fmt_rate(s.get('final_allow_rate'))} | {agree_text} "
            f"| {fmt_rate(s.get('schema_valid_rate'))} | {fmt_rate(s.get('semantic_valid_rate'))} "
            f"| {fmt_rate(s.get('infra_failure_rate'))} "
            f"| {safety.get('S1', '-')} | {safety.get('S2', '-')} | {safety.get('S3', '-')} "
            f"| {(sav.get('tokens_per_request_avg') or {}).get('delta_pct', '-')}% "
            f"| {(sav.get('cost_usd_per_request_avg') or {}).get('delta_pct', '-')}% "
            f"| {(sav.get('latency_sec_per_request_avg') or {}).get('delta_pct', '-')}% "
            f"| {'NON-INFERIOR' if n_inf else ('INFERIOR' if n_inf is False else 'n/a')} |"
        )
    noise = summary.get("_noise_high_a_vs_high_b") or {}
    if noise:
        nagree = (noise.get("decision_state_agreement") or {})
        lines += [
            "",
            "## Noise floor (HIGH vs HIGH)",
            "",
            f"- decision-state agreement: {fmt_rate(nagree)} (n={noise.get('n')})",
            f"- final-allow agreement: {fmt_rate(noise.get('final_allow_agreement'))}",
            f"- selected-candidate agreement: {fmt_rate(noise.get('selected_candidate_agreement'))}",
            f"- veto-code agreement: {fmt_rate(noise.get('veto_code_agreement'))}",
            f"- quality-score MAD: {noise.get('quality_score_mad')}",
            f"- S2 noise count: {summary.get('_meta', {}).get('s2_high_vs_high_noise_count')}",
        ]
    total_cost: dict[str, float] = {}
    for arm in [a for a in ARM_ORDER if a in summary]:
        arm_total = 0.0
        for r in results:
            if r.get("arm") != arm:
                continue
            arm_total += sum(b["cost_usd"] for b in role_rollup(r).values())
        total_cost[arm] = round(arm_total, 6)
    lines += [
        "",
        "## Spend",
        "",
        "| arm | total cost USD (published Go rates; null-priced models excluded) |",
        "|---|---|",
    ]
    for arm in [a for a in ARM_ORDER if a in summary]:
        lines.append(f"| {arm} | {total_cost.get(arm)} |")
    unpriced = [a for a in ARM_ORDER if a in summary and any(
        role_rollup(r)[role]["cost_unpriced"]
        for r in results if r.get("arm") == a for role in ROLES
    )]
    if unpriced:
        lines.append(
            f"- cost_unpriced models present for: {unpriced} (Luna is not in the "
            "OpenCode Go published-price table and its attempts run under "
            "REMOTE_API mode, where opencode_go_accounting reports None)."
        )
    notes = manifest.get("notes") or []
    lines += [
        "",
        "## Caveats (mandatory per protocol Reporting)",
        "",
        f"- Sample size: every rate carries its Wilson 95% interval; no significance "
        f"is claimed beyond n ({summary.get('_meta', {}).get('n_requests_with_both_high_completed')} paired requests).",
        "- Muse is a non-deterministic contributor model: identical high_a vs high_b "
        "disagreement IS the noise floor, not an error.",
        "- Time-of-day: arm order is randomized per request under the frozen seed; "
        "provider-side caching and load remain uncontrolled.",
        "- LIVE_FORWARD requests keep the production repeatability authority gate; if "
        "the repeatability artifact has no group for an arm's exact generation settings, "
        "the pipeline fail-closes that arm's allow. This surfaces in decision_source/"
        "rejection_codes and counts toward infra_failure_rate for ALL arms symmetrically.",
        "- If produced from --mode dry results, the decision columns only prove the "
        "fail-closed provider-failure path, NOT answer quality (module docstring).",
    ]
    for note in notes:
        lines.append(f"- manifest: {note}")
    return "\n".join(lines) + "\n"


def load_results(results_dir: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(Path(results_dir).glob("*.json")):
        try:
            doc = read_json_bytes_any_encoding(path)
            if isinstance(doc, dict) and "arm" in doc:
                doc.setdefault("_source_file", str(path))
                out.append(doc)
        except Exception:  # noqa: BLE001 - a corrupt file must not kill the report
            continue
    return out


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "benchmark_manifest.json"
    if not manifest_path.is_file():
        print(f"[report] missing {manifest_path}", file=sys.stderr)
        return 2
    manifest = read_json_bytes_any_encoding(manifest_path)
    results = load_results(run_dir / "results")
    if not results:
        print("[report] no result files under results/ yet", file=sys.stderr)
        return 2
    summary = aggregate_results(manifest, results)
    write_json(run_dir / "benchmark_summary.json", summary)
    rows = csv_rows(manifest, results)
    fieldnames = list(CSV_COLUMNS) + ["archive_candidates", "sealed_candidates", "historical_decision_state"]
    with (run_dir / "benchmark_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (run_dir / "BENCHMARK_REPORT.md").write_text(render_report_md(manifest, summary, results), encoding="utf-8")
    print(f"[report] {len(rows)} rows -> {run_dir / 'benchmark_results.csv'}")
    for arm in [a for a in ARM_ORDER if a in summary]:
        arm_s = summary[arm]
        agree = (arm_s.get("agreement_vs_high_a") or {}).get("decision_state_agreement") or {}
        print(
            f"[report] {arm:<14} ok={arm_s['n_completed_ok']}/{arm_s['n_planned']} "
            f"allow={(arm_s.get('final_allow_rate') or {}).get('rate')} "
            f"agree={(agree.get('rate'))} S1/S2/S3="
            f"{(arm_s.get('safety') or {}).get('S1')}/{(arm_s.get('safety') or {}).get('S2')}/"
            f"{(arm_s.get('safety') or {}).get('S3')} "
            f"non_inferior={(arm_s.get('ben005') or {}).get('non_inferior')}"
        )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="opencode_reasoning_benchmark",
        description="Muse reasoning-effort non-inferiority benchmark (stage C1).",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p_manifest = subs.add_parser("manifest", help="freeze the request set (read-only bus scan)")
    p_manifest.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    p_manifest.add_argument("--bus", default=DEFAULT_BUS)
    p_manifest.add_argument("--min-requests", type=int, default=DEFAULT_TARGET_N)
    p_manifest.add_argument("--seed", type=int, default=DEFAULT_SEED)

    p_run = subs.add_parser("run", help="drive (request, arm) pairs in fresh children")
    p_run.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    p_run.add_argument("--arms", default="", help=f"comma list from {list(ARM_ORDER)}; default all")
    p_run.add_argument("--requests", default="", help="comma-separated request ids or id suffixes")
    p_run.add_argument("--concurrency", type=int, default=6)
    p_run.add_argument("--mode", choices=("dry", "live"), default="dry",
                       help="dry (DEFAULT, fake clients, no network) or live (paid, supervisor only)")
    p_run.add_argument("--dry-requests", type=int, default=2,
                       help="in dry mode, cap at the first N manifest requests (0 = no cap)")
    p_run.add_argument("--child-timeout-sec", type=float, default=4200.0)
    p_run.add_argument("--bus", default=DEFAULT_BUS)
    p_run.add_argument("--keep-scratch", action="store_true")

    p_report = subs.add_parser("report", help="aggregate results into csv/json/md")
    p_report.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))

    p_child = subs.add_parser("_child", help=argparse.SUPPRESS)
    p_child.add_argument("--request-file", required=True)
    p_child.add_argument("--arm", default="")
    p_child.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if args.command == "manifest":
        return cmd_manifest(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "_child":
        return run_child(Path(args.request_file), Path(args.out))
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


