#!/usr/bin/env python3
"""Offline token attribution for one intended Muse provider request (Phase 2, stage A).

Requirement IDs: IN-001 (input attribution by subtree), DUP-001 (duplication
classes), PREC-001 (numeric precision), OUT-001 (output attribution and the
field -> consumer table), WIRE-001 (static-prefix cache-layout measurements).

SAFETY CONTRACT (SAF-001)
    Read-only against the live file bus and against every product file.  No
    network I/O of any kind: the transport is driven with an injected recording
    client whose ``responses.create`` records the wire kwargs and raises before
    a socket can be opened, and the review roles are driven through a capture
    provider that returns canned, schema-valid model decisions.  ``python/.env``
    is never read -- ``PO3_DOTENV_FILE`` is pointed at a non-existent file so the
    product env bootstrap cannot load it, and every configuration value used is
    the documented code default.  No attempt observer and no usage-ledger row is
    ever constructed.  Every mutable state file the driven code path can touch
    (trade-memory SQLite, decision cache, shadow ledger) is redirected to a
    scratch directory under the system temp dir before ``ai_gate`` is imported,
    so a child process cannot write to repository or bus state.

MEASUREMENT MODEL
    Tokens are counted with a documented deterministic proxy (``raw_units``);
    ``tiktoken`` is not installed in any interpreter on this machine and cannot
    be installed offline.  The proxy is calibrated per request against the
    provider's own ``input_tokens`` for the SAME request id in
    ``logs/openai_usage.ndjson``, and additionally against the two exact
    provider-reported cached-prefix anchors (6,513 analyst / 1,265 critic).  Both
    a single global factor and a two-regime (static-prefix / volatile-body)
    factor are emitted, because the anchors prove the proxy is systematically
    biased between prose and dense JSON; see ``calibration.json``.

USAGE
    python/.venv/Scripts/python.exe python/tools/opencode_token_attribution.py \
        --wp-dir .mt5-orchestrator/runs/<run>/wp2 [--sample-size 36] [--limit N]
                                                   [--stage select|capture|analyze|all]

    The parent selects a stratified sample of archived requests, runs one fresh
    child process per payload (``--child``), then aggregates and writes every
    raw output into ``--wp-dir``.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MUSE_MODEL = "muse-spark-1.3-contributor"
ANALYST_STATIC_ANCHOR_TOKENS = 6513
CRITIC_STATIC_ANCHOR_TOKENS = 1265
ESCALATION_ERROR_THRESHOLD = 0.15
# Bump whenever the child's measurement schema changes: a capture file written by
# an older schema is skipped loudly by the parent instead of being silently
# reinterpreted (stale captures would otherwise crash aggregation or, worse,
# aggregate the wrong fields).
MEASUREMENT_VERSION = "wp2.7"
DEFAULT_BUS = r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS"
ROLES = ("analyst", "critic", "adjudicator")

# ---------------------------------------------------------------------------
# Documented token proxy
# ---------------------------------------------------------------------------

_PROXY_PIECE_RE = re.compile(r"'s|'t|'re|'ve|'m|'ll|'d|[A-Za-z]+|[0-9]+|[^\sA-Za-z0-9]+|\s+")


def raw_units(text: str) -> int:
    """Deterministic proxy token count of ``text``.

    Pieces come from ``_PROXY_PIECE_RE``; whitespace-only pieces are skipped.
    A piece containing a letter costs ``ceil(utf8_len/4)``, a digit-only piece
    ``ceil(utf8_len/3)`` and a punctuation piece ``ceil(utf8_len/1.5)``; every
    counted piece costs at least 1.
    """
    if not text:
        return 0
    total = 0
    for piece in _PROXY_PIECE_RE.findall(text):
        if piece.isspace():
            continue
        nbytes = len(piece.encode("utf-8", errors="replace"))
        if _HAS_LETTER.search(piece):
            total += max(1, math.ceil(nbytes / 4))
        elif piece.isdigit():
            total += max(1, math.ceil(nbytes / 3))
        else:
            total += max(1, math.ceil(nbytes / 1.5))
    return total


_HAS_LETTER = re.compile(r"[A-Za-z]")


def tokenizer_environment() -> dict:
    """What tokenizer is actually importable in the interpreter running this tool.

    The spec requires ``tiktoken`` ``o200k_base`` if it imports, otherwise the
    documented proxy.  This is probed at runtime rather than assumed.
    """
    report = {"tiktoken": None, "regex": None}
    try:
        import tiktoken  # type: ignore

        encoding = tiktoken.get_encoding("o200k_base")
        report["tiktoken"] = "importable; o200k_base available"
        report["o200k_probe_len"] = len(encoding.encode("candidate_index"))
    except Exception as exc:  # noqa: BLE001
        report["tiktoken"] = f"NOT importable: {type(exc).__name__}: {exc}"
    try:
        import regex  # type: ignore  # noqa: F401

        report["regex"] = "importable (Unicode \\p{L} pretokeniser available)"
    except Exception as exc:  # noqa: BLE001
        report["regex"] = f"NOT importable: {type(exc).__name__}: {exc}"
    report["proxy_used"] = (
        "tiktoken o200k_base" if report.get("o200k_probe_len") else "documented deterministic proxy"
    )
    return report


def canon(value: Any) -> str:
    """The exact serialization the wire uses for a subtree."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def read_json_any_encoding(path: Path) -> Any:
    """UTF-16/UTF-8 BOM-safe JSON reader, independent of the product module."""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8")
    return json.loads(text)


def percentile(values, pct: float):
    values = [value for value in values if value is not None]
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def stats_block(values) -> dict:
    values = [value for value in values if value is not None]
    if not values:
        return {"n": 0, "avg": None, "p50": None, "p95": None, "min": None, "max": None}
    return {
        "n": len(values),
        "avg": round(sum(values) / len(values), 3),
        "p50": round(percentile(values, 0.50), 3),
        "p95": round(percentile(values, 0.95), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


# ---------------------------------------------------------------------------
# Bus readers (always read-only)
# ---------------------------------------------------------------------------

class BusReader:
    def __init__(self, bus: Path) -> None:
        self.bus = Path(bus)

    def usage_rows(self, wanted_ids: set[str] | None = None) -> tuple[dict, dict]:
        """Stream ``logs/openai_usage.ndjson`` exactly once.

        Returns ``(per_id_rows, muse_by_role)``.  The file is ~14 MB, so it is
        parsed line by line and only the requested rows are kept.
        """
        per_id: dict[str, dict[str, dict]] = {}
        muse: dict[str, list] = {}
        path = self.bus / "logs" / "openai_usage.ndjson"
        if not path.is_file():
            return per_id, muse
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                model = str(row.get("model") or "")
                role = str((row.get("extra") or {}).get("role") or "") or "analyst"
                request_id = str(row.get("request_id") or "")
                if model == MUSE_MODEL:
                    muse.setdefault(role, []).append(row)
                if wanted_ids is not None and request_id in wanted_ids:
                    kept = {
                        key: row.get(key)
                        for key in (
                            "ts_utc", "model", "status", "operation", "input_tokens",
                            "cached_input_tokens", "output_tokens",
                            "reasoning_output_tokens", "max_output_tokens",
                            "reasoning_effort",
                        )
                    }
                    kept["role"] = role
                    bucket = per_id.setdefault(request_id, {})
                    previous = bucket.get(role)
                    if previous is None or (
                        previous.get("model") != MUSE_MODEL and model == MUSE_MODEL
                    ):
                        bucket[role] = kept
        return per_id, muse

    def archived_payload_index(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        for folder in ("completed", "rejected"):
            directory = self.bus / folder
            if not directory.is_dir():
                continue
            for path in directory.glob("python_*.json"):
                if path.name.endswith(".meta.json"):
                    continue
                request_id = path.name.rsplit("__", 1)[-1][: -len(".json")]
                index.setdefault(request_id, path)
        return index

    def response_debug(self, request_id: str) -> dict | None:
        for suffix in ("", ".json"):
            candidate = self.bus / "response_debug" / f"{request_id}{suffix}"
            if candidate.is_file():
                try:
                    return read_json_any_encoding(candidate)
                except Exception:
                    return None
        return None


# ---------------------------------------------------------------------------
# Leaf classification (shared by child measurement and parent aggregation)
# ---------------------------------------------------------------------------

_TIMESTAMP_KEY_RE = re.compile(r"(^|_)(t|ts|time|timestamp|_at|created|updated|expires|as_of)($|_)")
_EPOCH_RE = re.compile(r"^\d{9,11}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_HASH_RE = re.compile(r"^[0-9a-fA-F]{8,}$")
_LABEL_KEY_RE = re.compile(
    r"(_enum|_state|_class|_code|_kind|_name|_source|_branch|_family|_scope|_model|_tier|_mode|"
    r"^usage$|^purpose$|^authority$|justification|reason|summary|narrative)$"
)
_ID_KEY_RE = re.compile(r"(^|_)(id|ids|hash|hashes|fingerprint|nonce|key|signature)($|_)")
_PROSE_KEY_RE = re.compile(r"(summary|reason|reasons|narrative|justification|description|rules|purpose|usage|notes)")


def walk_leaves(value: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{path}.{key}" if path else str(key)
            yield from walk_leaves(value[key], child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_leaves(item, f"{path}[{index}]")
    else:
        yield path, (path.rsplit(".", 1)[-1].split("[")[0] if path else ""), value


def walk_keys(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{path}.{key}" if path else str(key)
            yield path or "<root>", child
            yield from walk_keys(value[key], child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_keys(item, f"{path}[{index}]")


def leaf_class(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "flags_booleans"
    if isinstance(value, (int, float)):
        if (
            _TIMESTAMP_KEY_RE.search(key or "")
            and isinstance(value, int)
            and 1_000_000_000 <= value <= 4_000_000_000
        ):
            return "timestamps"
        return "numbers_int_float"
    text = "" if value is None else str(value)
    if value is None:
        return "nulls"
    if _EPOCH_RE.match(text) or _ISO_RE.match(text):
        return "timestamps"
    if _HASH_RE.match(text) and len(text) >= 8:
        return "ids_hashes"
    if _ID_KEY_RE.search(key or ""):
        return "ids_hashes"
    if len(text) > 40:
        return "labels_descriptions_prose"
    return "labels_short"


# ---------------------------------------------------------------------------
# Child: drive the real request path offline and measure one archived payload
# ---------------------------------------------------------------------------

class _CaptureStop(Exception):
    """Raised inside the injected transport once the wire has been recorded."""


class _WireRecorder:
    """Records ``responses.create(**kwargs)`` and refuses to send anything."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def factory(self, **_factory_kwargs):
        outer = self

        class _Responses:
            def create(self, **call_kwargs):
                outer.calls.append(dict(call_kwargs))
                raise _CaptureStop("wire captured")

        class _Client:
            responses = _Responses()

            @staticmethod
            def with_options(**_options):
                return _Client

        return _Client


class _quiet:
    """Silence every product log line the driven code path can emit."""

    def __enter__(self):
        import io

        self._saved_out = sys.stdout
        self._buffer = io.StringIO()
        sys.stdout = self._buffer
        sys.stderr = self._buffer
        return self

    def __exit__(self, *_exc):
        sys.stdout = self._saved_out
        sys.stderr = sys.stdout
        return False


def _scratch_dir() -> Path:
    path = Path(os.environ.get("TEMP", "/tmp")) / "po3_token_attribution_scratch"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _configure_offline_child(scratch: Path, bus: Path, use_shadow: bool) -> None:
    """Redirect every mutable state file away from the repo and the live bus.

    Must run BEFORE ``ai_gate`` is imported: that module builds ``AI_CONFIG``,
    the decision cache and the trade-memory store at import time.
    """
    os.environ["PO3_DOTENV_FILE"] = str(scratch / "__no_such_env_file__")
    os.environ["AI_DECISION_CACHE_ENABLE"] = "false"
    os.environ["PO3_ATTRIBUTION_BUS"] = str(bus)
    repo_python = Path(__file__).resolve().parents[1]
    memory_source = repo_python / "data" / "ai_trade_memory.sqlite3"
    memory_target = scratch / "ai_trade_memory_copy.sqlite3"
    if memory_source.is_file() and not memory_target.is_file():
        try:
            shutil.copyfile(memory_source, memory_target)
            for extra in repo_python.glob("data/ai_trade_memory.sqlite3-wal"):
                shutil.copyfile(extra, scratch / extra.name)
        except OSError:
            pass
    if memory_target.is_file():
        os.environ["AI_TRADE_MEMORY_FILE"] = str(memory_target)
    if use_shadow:
        # Read-only: _shadow_outcome_records() only opens this path for reading.
        os.environ["PO3_SHADOW_LEDGER_PATH"] = str(bus / "logs" / "shadow_candidates.jsonl")
    else:
        os.environ["PO3_SHADOW_LEDGER_PATH"] = str(scratch / "shadow_disabled.jsonl")


def _volatile_offsets(text: str, needles: list[str]) -> list[int]:
    """Byte offsets of request-specific content inside an instructions string."""
    offsets: list[int] = []
    for pattern in (
        r"[0-9a-f]{40,}",
        r"\b1[0-9]{9}\b",
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}",
        r"allowed id set is:",
    ):
        match = re.search(pattern, text)
        if match:
            offsets.append(match.start())
    for needle in needles:
        needle = str(needle or "")
        if len(needle) < 6:
            continue
        position = text.find(needle)
        if position >= 0:
            offsets.append(position)
    return offsets


_DIAGNOSTIC_DECLARATION_MARKER = " are explicitly uncalibrated diagnostic estimates"
_DIAGNOSTIC_STOPWORDS = {
    "and", "the", "are", "explicitly", "uncalibrated", "diagnostic", "estimates", "field",
    "fields", "every", "fill", "veto", "risk", "expectancy", "they", "has", "no", "direct",
    "positive", "negative", "trade", "authority", "numeric", "ranges", "scores",
}


def declared_diagnostic_fields(instructions: str) -> list[str]:
    """Field names the role prompt itself declares to have no trade authority.

    The analyst instructions say, verbatim, that a listed set of scores "are
    explicitly uncalibrated diagnostic estimates" with "no direct positive or
    negative trade authority".  That is the repository's own authority for which
    numbers are diagnostic, so the consumer table uses it instead of a guess.
    The sentence is taken from the previous full stop up to the marker.
    """
    text = instructions or ""
    at = text.find(_DIAGNOSTIC_DECLARATION_MARKER)
    if at < 0:
        return []
    start = max(text.rfind(". ", 0, at), text.rfind("\n", 0, at))
    clause = text[start + 1 : at]
    names = {
        token
        for token in re.findall(r"\b[a-z][a-z0-9_]{3,}\b", clause)
        if token not in _DIAGNOSTIC_STOPWORDS
    }
    return sorted(names)


def _wire_measure(wire: dict, volatile_needles: list[str]) -> dict:
    """Role-independent measurement of one captured Responses wire."""
    instructions = str(wire.get("instructions") or "")
    content = wire.get("input") or [{}]
    parts = content[0].get("content") or [] if isinstance(content[0], dict) else []
    input_text = ""
    image_parts = 0
    image_inline_bytes = 0
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "input_text":
            input_text = str(part.get("text") or "")
        elif isinstance(part, dict):
            image_parts += 1
            image_inline_bytes += len(str(part.get("image_url") or part.get("data") or ""))
    text_block = wire.get("text") or {}
    fmt = text_block.get("format") or {}
    schema_text = canon(fmt.get("schema"))
    # ``whole_wire_text`` per WP2_SPEC: instructions + schema JSON + the serialized
    # ``input`` value.  The serialization of ``input`` is the one the SDK sends
    # (compact, non-ASCII preserved), which is what the provider tokenizes.
    input_serialized = json.dumps(wire.get("input"), separators=(",", ":"), ensure_ascii=False)
    whole = instructions + schema_text + input_serialized
    body = {key: value for key, value in wire.items() if key != "extra_headers"}
    body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    marker = '"input":'
    volatile_at = body_text.find(marker)
    offsets = _volatile_offsets(instructions, volatile_needles)
    instructions_static_cut = min(offsets) if offsets else len(instructions)
    headers = dict(wire.get("extra_headers") or {})
    return {
        "schema_name": fmt.get("name"),
        "declared_diagnostic_fields": declared_diagnostic_fields(instructions),
        "model": wire.get("model"),
        "max_output_tokens": wire.get("max_output_tokens"),
        "reasoning": wire.get("reasoning"),
        "truncation": wire.get("truncation"),
        "store": wire.get("store"),
        "verbosity": text_block.get("verbosity"),
        "extra_header_keys": sorted(headers),
        "x_opencode_session_sha8": sha256_text(str(headers.get("x-opencode-session") or ""))[:8],
        "n_image_parts": image_parts,
        "image_inline_base64_chars": image_inline_bytes,
        "len_instructions": len(instructions),
        "len_input_text": len(input_text),
        "len_input_serialized": len(input_serialized),
        "len_schema_text": len(schema_text),
        "len_body_text": len(body_text),
        "first_volatile_byte_in_body": volatile_at,
        "first_volatile_byte_in_instructions": instructions_static_cut,
        "instructions_static_tail_units": raw_units(instructions[instructions_static_cut:]),
        "instructions_units": raw_units(instructions),
        "instructions_static_units": raw_units(instructions[:instructions_static_cut]),
        "schema_units": raw_units(schema_text),
        "input_units": raw_units(input_text),
        "input_array_units": raw_units(input_serialized),
        "static_units": raw_units(instructions[:instructions_static_cut]) + raw_units(schema_text),
        "whole_units": raw_units(whole),
        "input_sha8": sha256_text(input_text)[:12],
        "input_array_sha8": sha256_text(input_serialized)[:12],
        "instructions_sha8": sha256_text(instructions)[:12],
        "instructions_static_sha8": sha256_text(instructions[:instructions_static_cut])[:12],
        "schema_sha8": sha256_text(schema_text)[:12],
        "input_sha8": sha256_text(input_text)[:12],
    }


def _envelope_measure(envelope: dict) -> dict:
    """Per-subtree proxy counts for one evidence envelope as it goes on the wire."""
    sections: dict[str, dict] = {}
    for key in sorted(envelope):
        text = canon(envelope[key])
        sections[f"envelope.{key}"] = {
            "units": raw_units(text),
            "sha8": sha256_text(text)[:12],
            "kind": "envelope_section",
        }
    catalog = envelope.get("evidence_catalog") or {}
    items = [row for row in (catalog.get("items") or []) if isinstance(row, dict)]
    id_units = path_units = value_units = cand_units = row_units = 0
    value_counts: dict[str, int] = {}
    for row in items:
        row_units += raw_units(canon(row))
        id_units += raw_units(canon(row.get("id")))
        path_units += raw_units(canon(row.get("p")))
        value_units += raw_units(canon(row.get("v")))
        if "c" in row:
            cand_units += raw_units(canon(row.get("c")))
        key = canon(row.get("v"))
        value_counts[key] = value_counts.get(key, 0) + 1
    scoped_rows = sum(1 for row in items if "c" in row)
    key_units = (raw_units('"id":') + raw_units('"p":') + raw_units('"v":')) * len(items) + raw_units('"c":') * scoped_rows
    sections["evidence_catalog.items"] = {"units": row_units, "kind": "catalog_items"}
    for name, units in (
        ("evidence_catalog.items.id", id_units),
        ("evidence_catalog.items.p", path_units),
        ("evidence_catalog.items.v", value_units),
        ("evidence_catalog.items.c", cand_units),
        ("evidence_catalog.items.keys_punctuation", max(0, row_units - id_units - path_units - value_units - cand_units)),
        ("evidence_catalog.items.key_names_only", key_units),
    ):
        sections[name] = {"units": units, "kind": "catalog_component"}

    allowed_units = 0
    candidate_key_units: dict[str, list] = {}
    largest_names: list[str] = []
    rows = ((envelope.get("entry_and_invalidation") or {}).get("candidates")) or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        allowed_units += raw_units(canon(row.get("allowed_evidence_ref_ids")))
        biggest_units, biggest_name = 0, ""
        for name in sorted(row):
            if name == "allowed_evidence_ref_ids":
                continue
            units = raw_units(canon(row[name]))
            candidate_key_units.setdefault(name, []).append(units)
            if units > biggest_units:
                biggest_units, biggest_name = units, name
        largest_names.append(biggest_name)
    sections["entry_and_invalidation.candidates[].allowed_evidence_ref_ids"] = {
        "units": allowed_units,
        "kind": "allowed_ids",
    }

    class_units: dict[str, int] = {}
    for path, key, value in walk_leaves(envelope):
        cls = leaf_class(key, value)
        class_units[cls] = class_units.get(cls, 0) + raw_units(canon(value))
    key_total = 0
    key_by_path: dict[str, int] = {}
    for parent, child in walk_keys(envelope):
        name = child.rsplit(".", 1)[-1].split("[")[0]
        units = raw_units(canon(name))
        key_total += units
        key_by_path[name] = key_by_path.get(name, 0) + units
    n_candidates = len(rows)
    columnar_key_units = 0
    for name, values in candidate_key_units.items():
        columnar_key_units += raw_units(canon(name)) * max(len(values), n_candidates)
    return {
        "n_envelope_sections": sum(1 for name in sections if sections[name]["kind"] == "envelope_section"),
        "n_catalog_items": len(items),
        "n_candidates": n_candidates,
        "total_input_units": raw_units(canon(envelope)),
        "sections": sections,
        "per_candidate_key_units": {
            name: stats_block(values) for name, values in candidate_key_units.items()
        },
        "per_candidate_largest_subtree_names": sorted(set(largest_names)),
        "leaf_classes": class_units,
        "key_name_total_units": key_total,
        "candidate_array_key_name_units": columnar_key_units,
        "key_name_top": sorted(
            ({"key": name, "units": units} for name, units in key_by_path.items()),
            key=lambda row: -row["units"],
        )[:30],
        "catalog_value_repeat_groups": sum(1 for count in value_counts.values() if count > 1),
        "catalog_unique_values": len(value_counts),
        "echo_index": _echo_index(envelope),
    }


# Documented alias map: the two pointer substitutions
# ``ai_gate._compact_model_evidence_payload`` performs, pre-compact -> compact.
_ALIAS_TARGETS = (
    (
        re.compile(r"^execution_costs\.per_candidate\.(\d+)\."),
        r"entry_and_invalidation.candidates.\1.authoritative_numbers.",
    ),
    (
        re.compile(r"^targets_and_obstacles\.candidate_targets\.(\d+)\."),
        r"entry_and_invalidation.candidates.\1.target_candidates.",
    ),
    (
        re.compile(r"^(entry_and_invalidation\.candidates\.\d+\.[A-Za-z0-9_.]+)\.value$"),
        r"\1",
    ),
)


def _alias_of(path: str) -> str | None:
    for pattern, replacement in _ALIAS_TARGETS:
        if pattern.search(path):
            return pattern.sub(replacement, path)
    return None


_CONTRACT_TERMS = (
    "family_requirement_contract", "family_event_evidence", "target_semantics",
    "target_choice_menu", "allowed_evidence_ref_ids", "evidence_catalog",
    "historical_evidence_state", "bucket_prior", "rule_score", "candidate_index",
    "arbitration_required", "authoritative_numbers", "prior_applicability",
    "cross_asset_fallback_blocked", "opposing_clearance_score", "require_snapshots",
    "missing_required_evidence", "shadow_historical_evidence", "family_profile",
    "target_comparison", "decision_state", "asset_class",
)


def _dup_measure(envelope: dict, instructions: str) -> dict:
    """DUP-001 classes, proven by canonical path identity, not value equality.

    ``evidence_catalog`` canonical paths index lists with dots
    (``entry_and_invalidation.candidates.3.fvg_mid``), so the non-catalog leaves
    are enumerated in the same format; comparing paths that only differ in
    bracket style would silently under-report class (a).
    """
    catalog = envelope.get("evidence_catalog") or {}
    items = [row for row in (catalog.get("items") or []) if isinstance(row, dict)]
    outside: dict[str, Any] = {}
    for key in sorted(envelope):
        if key == "evidence_catalog":
            continue
        for path, _key, value in walk_leaves_dotted(envelope[key], key):
            outside[path] = value
    class_names = (
        "a_same_fact_serialized_twice",
        "b_distinct_equal_value_nontrivial",
        "b2_distinct_equal_value_trivial_scalar",
        "c_alias",
        "d_catalog_material",
        "e_contract_required",
    )
    counts = {name: 0 for name in class_names}
    units = {name: 0 for name in class_names}
    # Whole-row units and value-only units: the removable part of a duplicate is
    # the payload, not the citation.  Stage B needs both numbers -- dropping a
    # class-(a) row saves the row, keeping the row and pointing at the envelope
    # value instead saves only ``v``.
    value_units_by_class = {name: 0 for name in class_names}
    value_groups: dict[str, list[dict]] = {}
    for row in items:
        value_groups.setdefault(canon(row.get("v")), []).append(row)
    named_terms = sorted({term for term in _CONTRACT_TERMS if term in instructions})
    duplicated_paths: list[str] = []
    truncated_matches = 0
    for row in items:
        path = str(row.get("p") or "")
        value_text = canon(row.get("v"))
        row_units = raw_units(canon(row))
        alias_target = _alias_of(path)
        matched_path = None
        if path in outside and canon(outside[path]) == value_text:
            matched_path = path
        elif path in outside and _truncation_of(outside[path]) == value_text:
            # The catalog compacts long strings; the fact is still identical.
            matched_path = path
            truncated_matches += 1
        elif alias_target and alias_target in outside and canon(outside[alias_target]) == value_text:
            matched_path = alias_target
        if matched_path is not None:
            klass = "a_same_fact_serialized_twice" if matched_path == path else "c_alias"
            duplicated_paths.append(f"{path} == {matched_path}")
        elif len(value_groups.get(value_text) or []) > 1:
            klass = (
                "b2_distinct_equal_value_trivial_scalar"
                if value_text in _TRIVIAL_SCALARS
                else "b_distinct_equal_value_nontrivial"
            )
        else:
            klass = "d_catalog_material"
        counts[klass] += 1
        units[klass] += row_units
        value_units_by_class[klass] += raw_units(value_text)
        if any(term in path for term in named_terms):
            counts["e_contract_required"] += 1
            units["e_contract_required"] += row_units
            value_units_by_class["e_contract_required"] += raw_units(value_text)
    contract_units = 0
    for path, key, value in walk_leaves_dotted(envelope):
        if any(term in (key or "") for term in named_terms):
            contract_units += raw_units(canon(value))
    return {
        "rows": len(items),
        "counts": counts,
        "proxy_units": units,
        "proxy_value_units": value_units_by_class,
        "duplicated_catalog_paths": sorted(set(duplicated_paths)),
        "truncated_string_matches": truncated_matches,
        "value_groups_duplicated": sum(1 for group in value_groups.values() if len(group) > 1),
        "duplicate_extra_rows": sum(len(group) - 1 for group in value_groups.values() if len(group) > 1),
        "contract_required_terms_in_prompt": len(named_terms),
        "contract_required_terms": named_terms,
        "contract_required_envelope_units": contract_units,
    }


_TRIVIAL_SCALARS = {"null", "true", "false", "0", "1", "0.0", '""'}


def _truncation_of(value: Any) -> str:
    """The catalog's compacted form of a long string (``_MAX_SCALAR_CHARS``)."""
    from_evidence_catalog = 160
    if isinstance(value, str) and len(value) > from_evidence_catalog:
        return canon(value[:from_evidence_catalog])
    return "\x00never"


def walk_leaves_dotted(value: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    """``walk_leaves`` with dotted list indexes, matching catalog ``p`` format."""
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{path}.{key}" if path else str(key)
            yield from walk_leaves_dotted(value[key], child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_leaves_dotted(item, f"{path}.{index}" if path else str(index))
    else:
        yield path, (path.rsplit(".", 1)[-1] if path else ""), value


_PRICE_KEY_RE = re.compile(
    r"(price|entry|^sl$|^tp|_tp|_sl|high|low|mid|level|target|obstacle|liquidity|fvg|sweep|"
    r"bos|mss|choch|disp|anchor|bound|edge)"
)
# Names that look price-ish but are provably derived quantities (R multiples,
# distances, rates, scores).  These are NOT on the broker tick grid, so
# "digits beyond symbol_digits" says nothing about their precision.
_DERIVED_NAME_RE = re.compile(
    r"(distance|_r$|^r_|ratio|score|prob|rate|mult|frac|weight|pct|slope|band|size|count|"
    r"deviation|error|_ret$|return)"
)


def _exact_shorter_decimal(value: float) -> str | None:
    """Shortest decimal that round-trips to the SAME double, if one exists.

    Python's ``repr`` is already the shortest round-tripping form, so a shorter
    decimal can only exist when the double happens to be exactly representable
    (0.5, 62916.0625, ...).  Anything else is a value-changing rewrite, which is
    a semantic decision and is reported as not provable.
    """
    text = canon(value)
    for places in range(0, 14):
        candidate = canon(round(value, places))
        try:
            if float(candidate) == value and len(candidate) < len(text):
                return candidate
        except ValueError:
            return None
    return None


def _find_first(value: Any, names: tuple[str, ...], path: str = "$") -> tuple[Any, str] | None:
    """Depth-first search of the MQL request for an authoritative precision field."""
    if isinstance(value, dict):
        for key in sorted(value):
            if key in names and value[key] not in (None, ""):
                return value[key], f"{path}.{key}"
        for key in sorted(value):
            found = _find_first(value[key], names, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_first(item, names, f"{path}[{index}]")
            if found:
                return found
    return None


_FIXED_DECIMAL_STRING_RE = re.compile(r"^-?\d+\.\d*$")


def _precision_measure(envelope: dict, payload: dict) -> dict:
    """PREC-001: serialized digits beyond the authoritative source precision."""
    instrument = envelope.get("instrument") or {}
    digits = instrument.get("symbol_digits") if isinstance(instrument.get("symbol_digits"), int) else None
    digits_source = "envelope.instrument.symbol_digits" if digits is not None else None
    if digits is None:
        found = _find_first(payload, ("symbol_digits", "digits"))
        if found and isinstance(found[0], int):
            digits, digits_source = found[0], f"request payload {found[1]}"
    tick = instrument.get("symbol_tick_size") if isinstance(instrument.get("symbol_tick_size"), (int, float)) else None
    tick_source = "envelope.instrument.symbol_tick_size" if tick else None
    if not tick:
        found = _find_first(payload, ("symbol_tick_size", "tick_size"))
        if found and isinstance(found[0], (int, float)) and found[0]:
            tick, tick_source = found[0], f"request payload {found[1]}"
    offenders: dict[str, dict] = {}
    scanned = 0

    def record(
        rule: str,
        key: str,
        path: str,
        text: str,
        source: str,
        canonical: str | None,
        note: str,
        hypothetical: str | None = None,
    ) -> None:
        entry = offenders.setdefault(
            f"{rule}|{key}",
            {
                "rule": rule,
                "field": key,
                "example_path": path,
                "example": text,
                "source_precision": source,
                "proven_safe_canonical_form": canonical if canonical else "not provable",
                "provable": bool(canonical),
                "hypothetical_shortest_form": hypothetical,
                "note": note,
                "occurrences": 0,
                "units_now": 0,
                "units_canonical": 0,
                "units_hypothetical": 0,
            },
        )
        entry["occurrences"] += 1
        entry["units_now"] += raw_units(text)
        entry["units_canonical"] += raw_units(canonical if canonical else text)
        entry["units_hypothetical"] += raw_units(hypothetical if hypothetical else text)

    for path, key, value in walk_leaves(envelope):
        if isinstance(value, bool):
            continue
        if isinstance(value, float):
            scanned += 1
            text = canon(value)
            decimals = len(text.split(".", 1)[1]) if "." in text else 0
            if decimals == 0:
                continue
            significant = len(re.sub(r"[-.]|^0+", "", text))
            exact_shorter = _exact_shorter_decimal(value)
            derived_name = bool(_DERIVED_NAME_RE.search(key or ""))
            price_like = bool(_PRICE_KEY_RE.search(key or "")) and not derived_name
            if price_like and digits is not None and decimals > digits:
                grid = round(value, digits)
                record(
                    "price_digits_gt_symbol_digits", key, path, text,
                    f"{digits_source}={digits}" + (f", tick={tick}" if tick else ""),
                    exact_shorter,
                    "shorter decimal round-trips to the same double"
                    if exact_shorter else
                    "value-changing rewrite: the extra digits are binary residue of an "
                    "arithmetic result, and replacing them alters the frozen value",
                    canon(grid),
                )
            elif decimals >= 12 or significant > 15:
                record(
                    "float_binary_residue", key, path, text,
                    "binary double; Python's repr is already the shortest round-trip form",
                    exact_shorter,
                    "shorter decimal round-trips to the same double" if exact_shorter else
                    "no shorter decimal round-trips: the digits are the double's identity",
                    canon(round(value, 6)),
                )
            elif decimals > 6:
                record(
                    "derived_gt_6dp", key, path, text,
                    "derived value; no declared source precision in the request"
                    + (" (price-shaped key but a derived quantity)" if _PRICE_KEY_RE.search(key or "") else ""),
                    exact_shorter,
                    "digits may be information-bearing (ratios, probabilities, R multiples)",
                    canon(round(value, 4)),
                )
        elif isinstance(value, str) and _FIXED_DECIMAL_STRING_RE.match(value):
            scanned += 1
            trimmed = value.rstrip("0").rstrip(".") or "0"
            decimals = len(value.split(".", 1)[1])
            price = bool(_PRICE_KEY_RE.search(key or ""))
            if price and digits is not None and decimals > digits:
                rule, source = "fixed_decimal_padded_price_string", f"{digits_source}={digits}"
            elif decimals > 3 and trimmed != value:
                rule, source = "fixed_decimal_padded_string", "MQL fixed-point text formatting"
            else:
                continue
            record(
                rule, key, path, canon(value), source, None,
                "the exact text participates in the frozen request hash and may be parsed by "
                "an MQL consumer, so shortening is not provable from this sample alone",
                canon(trimmed),
            )
    for entry in offenders.values():
        entry["units_saved"] = entry["units_now"] - entry["units_canonical"]
        entry["units_saved_hypothetical"] = entry["units_now"] - entry["units_hypothetical"]
    fields = sorted(offenders.values(), key=lambda entry: -entry["units_saved_hypothetical"])
    return {
        "source_precision": {
            "symbol_digits": digits,
            "symbol_digits_source": digits_source,
            "symbol_tick_size": tick,
            "symbol_tick_size_source": tick_source,
        },
        "float_leaves_scanned": scanned,
        "distinct_field_rules": len(fields),
        "total_units_saved_if_applied": sum(entry["units_saved"] for entry in fields),
        "total_units_saved_provable_only": sum(entry["units_saved"] for entry in fields if entry["provable"]),
        "total_units_saved_hypothetically": sum(entry["units_saved_hypothetical"] for entry in fields),
        "fields": fields[:80],
    }


class _CannedReviewProvider:
    """Records the review-role contract and returns schema-valid canned answers."""

    def __init__(self, model: str, canned: dict) -> None:
        self.model = model
        self.provider_mode = "OPENCODE_GO"
        self._canned = canned
        self.calls: list[dict] = []

    def generate_structured(self, **kwargs):
        import types

        self.calls.append(dict(kwargs))
        role = str(kwargs.get("role") or "")
        return types.SimpleNamespace(
            parsed=self._canned.get(role),
            provider_id="opencode_go_responses",
            actual_model=self.model,
            model_id=self.model,
            latency_sec=0.0,
            transport_retry_count=0,
            schema_retry_count=0,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )


def child_capture(payload_path: Path, request_id: str, out_path: Path, want_review: bool, use_shadow: bool) -> int:
    started = time.time()
    bus = Path(os.environ.get("PO3_ATTRIBUTION_BUS", DEFAULT_BUS))
    scratch = _scratch_dir()
    _configure_offline_child(scratch, bus, use_shadow)
    repo_python = Path(__file__).resolve().parents[1]
    if str(repo_python) not in sys.path:
        sys.path.insert(0, str(repo_python))

    with _quiet():
        import ai_gate
        import ai_provider
        import decision_pipeline
        import structured_models
        from evidence_catalog import (
            EvidenceCatalog,
            EvidenceItem,
            EVIDENCE_CATALOG_VERSION,
        )
        from provider_deadline import RequestDeadline

    config = ai_gate.AI_CONFIG

    def make_leg(recorder: _WireRecorder):
        return ai_provider.OpenCodeResponsesProvider(
            base_url=config.opencode_base_url or "https://opencode.ai/zen/go/v1",
            api_key="offline-capture-not-a-secret",
            model=config.opencode_muse_model or MUSE_MODEL,
            reasoning_effort=config.opencode_muse_reasoning_effort or "high",
            timeout_sec=float(config.opencode_timeout_sec or 60.0),
            max_output_tokens=int(config.opencode_max_output_tokens or 25000),
            reasoning_token_reserve=int(config.opencode_muse_reasoning_token_reserve or 0),
            circuit_failure_threshold=1_000_000,
            circuit_cooldown_sec=1.0,
            admission_max_retries=0,
            session_scope=config.opencode_session_scope,
            log=lambda _message: None,
            client_factory=recorder.factory,
        )

    recorder = _WireRecorder()
    leg = make_leg(recorder)
    result: dict[str, Any] = {
        "measurement_version": MEASUREMENT_VERSION,
        "request_id": request_id,
        "payload_path": str(payload_path),
        "errors": [],
        "capture_config": {
            "model": config.opencode_muse_model,
            "reasoning_effort": config.opencode_muse_reasoning_effort,
            "reasoning_token_reserve": config.opencode_muse_reasoning_token_reserve,
            "configured_max_output_tokens": config.opencode_max_output_tokens,
            "session_scope": config.opencode_session_scope,
            "dotenv_file_used": os.environ.get("PO3_DOTENV_FILE"),
            "trade_memory_file": str(os.environ.get("AI_TRADE_MEMORY_FILE") or ""),
            "decision_cache_enable": config.decision_cache_enable,
            "shadow_ledger_used": use_shadow,
            "prompt_contract_version": ai_gate.AI_PROMPT_CONTRACT_VERSION,
            "decision_schema_version": ai_gate.AI_DECISION_SCHEMA_VERSION,
        },
    }

    with _quiet():
        payload = read_json_any_encoding(payload_path)
    result["symbol"] = str(payload.get("symbol") or "")
    result["symbol_class"] = symbol_class(result["symbol"])
    result["n_candidates"] = len(payload.get("candidates") or [])
    result["candidate_stratum"] = candidate_stratum(result["n_candidates"])
    result["workload_mode"] = str(payload.get("workload_mode") or "")

    ai_gate.LOG_FILE = None
    ai_gate.log = lambda _message: None
    ai_gate._provider = lambda: leg
    ai_gate._write_ai_cost_report = lambda *_a, **_k: None
    RequestDeadline.can_start_attempt = lambda self, now=None: True
    RequestDeadline.provider_timeout_sec = lambda self, configured, now=None: float(configured)

    # Production seals the live cohort BEFORE the request is frozen and scored
    # (``ai_gate._apply_live_candidate_budget``, called from the request
    # processor at line ~10708).  Skipping it rebuilds an 8-candidate wire for a
    # request that really sent 3 -- the largest single fidelity risk here, and
    # provable from the ledger: a sealed request's provider ``input_tokens``
    # tracks the 3-candidate size, not the archived one.
    wire_payload = dict(payload)
    with _quiet():
        try:
            wire_payload = ai_gate._apply_live_candidate_budget(dict(payload))
        except BaseException as exc:  # noqa: BLE001
            result["errors"].append(f"live_candidate_seal:{type(exc).__name__}:{exc}")
    sealed_count = len(wire_payload.get("candidates") or [])
    result["candidates"] = {
        "archive": result["n_candidates"],
        "wire": sealed_count,
        "cohort_sealed": sealed_count != result["n_candidates"],
        "wire_candidate_indexes": [
            int(candidate.get("candidate_index", position))
            for position, candidate in enumerate(wire_payload.get("candidates") or [])
            if isinstance(candidate, dict)
        ],
        "live_candidate_budget": int(getattr(config, "live_candidate_budget", 0) or 0),
    }

    with _quiet():
        try:
            ai_gate._score_setup_impl(dict(wire_payload))
        except BaseException as exc:  # noqa: BLE001 - capture abort or pre-gate raise
            if not isinstance(exc, _CaptureStop):
                result["errors"].append(f"analyst_drive:{type(exc).__name__}:{exc}")
        analyst_wire = recorder.calls[0] if recorder.calls else None
        if analyst_wire is None:
            # The hard pre-gates can return before the provider is reached.  Ask
            # the AI stage directly; it is the same request-construction path.
            try:
                ai_gate._score_setup_ai(dict(wire_payload), provider_override=leg)
            except BaseException as exc:  # noqa: BLE001
                if not isinstance(exc, _CaptureStop):
                    result["errors"].append(f"analyst_stage_retry:{type(exc).__name__}:{exc}")
            analyst_wire = recorder.calls[0] if recorder.calls else None
    result["analyst_calls_captured"] = len(recorder.calls)
    if analyst_wire is None:
        result["status"] = "no_provider_call"
        result["elapsed_sec"] = round(time.time() - started, 2)
        out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 2

    needles = [request_id, str(payload.get("session_id") or ""), result["symbol"]]
    for candidate in wire_payload.get("candidates") or []:
        if isinstance(candidate, dict):
            needles.extend([str(candidate.get("candidate_id") or ""), str(candidate.get("fvg_id") or "")])
    needles = [needle for needle in needles if needle]

    result["status"] = "captured"
    result["analyst"] = _wire_measure(analyst_wire, needles)
    try:
        envelope = json.loads(str(analyst_wire["input"][0]["content"][0]["text"]))
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"envelope_parse:{type(exc).__name__}:{exc}")
        envelope = None
    if isinstance(envelope, dict):
        result["envelope_top_keys"] = sorted(envelope)
        result["envelope"] = _envelope_measure(envelope)
        result["duplication"] = _dup_measure(envelope, str(analyst_wire.get("instructions") or ""))
        result["precision"] = _precision_measure(envelope, wire_payload)
    if want_review and isinstance(envelope, dict):
        result.update(_review_capture(envelope, wire_payload, request_id, make_leg, _quiet))
    result["elapsed_sec"] = round(time.time() - started, 2)
    out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def _review_capture(envelope: dict, payload: dict, request_id: str, make_leg, quiet) -> dict:
    """Critic and adjudicator wires through ``run_qualitative_consensus``."""
    import contextlib

    out: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        import ai_gate
        import decision_pipeline
        import structured_models
        from evidence_catalog import EvidenceCatalog, EvidenceItem, EVIDENCE_CATALOG_VERSION

    catalog_block = envelope.get("evidence_catalog") or {}
    rows = [row for row in (catalog_block.get("items") or []) if isinstance(row, dict)]
    if not rows:
        return {"review_status": "no_catalog_items"}
    items = tuple(
        EvidenceItem(
            evidence_id=int(row["id"]),
            candidate_index=row.get("c"),
            canonical_path=str(row.get("p") or ""),
            value=row.get("v"),
            value_hash=sha256_text(canon(row.get("v"))),
            authority="deterministic",
        )
        for row in rows
    )
    catalog = EvidenceCatalog(
        catalog_version=str(catalog_block.get("catalog_version") or EVIDENCE_CATALOG_VERSION),
        items=items,
        catalog_hash=str(catalog_block.get("catalog_hash") or ""),
        _by_id={item.evidence_id: item for item in items},
        _by_path={item.canonical_path: item for item in items},
    )
    candidate_rows = [row for row in ((envelope.get("entry_and_invalidation") or {}).get("candidates")) or [] if isinstance(row, dict)]
    if not candidate_rows:
        return {"review_status": "no_candidate_rows"}
    first = candidate_rows[0]
    candidate_index = int(first.get("candidate_index", 0))
    assessment = _archived_assessment(request_id, candidate_index) or {
        "candidate_index": candidate_index,
        "candidate_id": str((payload.get("candidates") or [{}])[0].get("candidate_id") or "capture-candidate-id"),
        "candidate_hash": str((payload.get("candidates") or [{}])[0].get("candidate_hash") or "capturehash"),
        "decision_state": "ABSTAIN",
        "confidence_band": "MEDIUM",
        "missing_required_evidence": [],
    }
    if not assessment.get("candidate_id") or not assessment.get("candidate_hash"):
        return {"review_status": "no_candidate_identity"}
    allowed = [int(value) for value in (first.get("allowed_evidence_ref_ids") or [])] or [int(items[0].evidence_id)]
    veto_code = structured_models.QUALITATIVE_VETO_CODES[0]
    canned = {
        "critic": structured_models.ModelCriticDecision(
            candidate_index=candidate_index,
            verdict="BLOCK",
            blocking_objections=[
                structured_models.ModelCriticObjection(
                    code=veto_code,
                    evidence_ref_ids=allowed[:1],
                    reason="offline capture probe objection",
                )
            ],
            non_blocking_objections=[],
            missing_required_evidence=[],
            evidence_ref_ids=allowed[:1],
            confidence_band="MEDIUM",
            summary="offline capture probe",
        ),
        "adjudicator": structured_models.ModelAdjudicatorDecision(
            candidate_index=candidate_index,
            verdict="ABSTAIN",
            resolved_objection_codes=[],
            unresolved_objection_codes=[veto_code],
            evidence_ref_ids=allowed[:1],
            resolution_reason="offline capture probe abstain",
        ),
    }
    provider = _CannedReviewProvider(MUSE_MODEL, canned)
    with quiet():
        try:
            deadline = ai_gate._request_deadline_for(dict(payload))
        except Exception:
            deadline = None
    metadata = {
        "request_id": request_id,
        "request_identity_hash": str(
            (envelope.get("identity") or {}).get("request_identity_hash")
            or payload.get("request_identity_hash")
            or "offlinecaptureidentity0123456789abcdef"
        ),
        "symbol": str(payload.get("symbol") or ""),
        "image_parts": [],
        "non_trading_shadow": True,
        "deadline": deadline,
        "workload_mode": str(payload.get("workload_mode") or ""),
        "adjudication_skip_enable": False,
    }
    with quiet():
        try:
            decision_pipeline.run_qualitative_consensus(
                provider=provider,
                evidence=envelope,
                analyst_assessment=assessment,
                request_metadata=metadata,
                evidence_catalog=catalog,
                near_deterministic_boundary=False,
                event_logger=lambda _message: None,
            )
        except Exception as exc:  # noqa: BLE001
            if not provider.calls:
                return {"review_status": f"consensus_failed:{type(exc).__name__}:{exc}"}
    out["review_status"] = "ok"
    out["review_roles_reached"] = [str(call.get("role")) for call in provider.calls]
    # Replay each recorded review contract through the real transport builder so
    # the critic/adjudicator wire comes from the same code path as production.
    for call in provider.calls:
        role = str(call.get("role") or "")
        needles = [request_id, str(metadata["request_identity_hash"]), assessment.get("candidate_id", "")]
        role_recorder = _WireRecorder()
        leg = make_leg(role_recorder)
        with quiet():
            try:
                leg.generate_structured(
                    role=role,
                    system_prompt=call["system_prompt"],
                    evidence=call["evidence"],
                    response_schema=call["response_schema"],
                    request_metadata=dict(metadata),
                )
            except Exception as exc:  # noqa: BLE001
                if not role_recorder.calls:
                    out[f"{role}_wire_error"] = f"{type(exc).__name__}:{exc}"
        if role_recorder.calls:
            out[role] = _wire_measure(role_recorder.calls[0], needles)
            evidence = call.get("evidence")
            if isinstance(evidence, dict):
                out[f"{role}_envelope"] = _envelope_measure(evidence)
                out[f"{role}_evidence_keys"] = sorted(evidence)
                out[f"{role}_prompt_len"] = len(str(call.get("system_prompt") or ""))
    return out


def _archived_assessment(request_id: str, candidate_index: int) -> dict | None:
    bus = BusReader(Path(os.environ.get("PO3_ATTRIBUTION_BUS", DEFAULT_BUS)))
    doc = bus.response_debug(request_id)
    if not isinstance(doc, dict):
        return None
    response = doc.get("response")
    if not isinstance(response, dict):
        return None
    for row in response.get("candidate_assessments") or []:
        if not isinstance(row, dict) or int(row.get("candidate_index", -1)) != candidate_index:
            continue
        return {
            "candidate_index": int(row.get("candidate_index")),
            "candidate_id": str(row.get("candidate_id") or ""),
            "candidate_hash": str(row.get("candidate_hash") or ""),
            "decision_state": str(row.get("decision_state") or "ABSTAIN"),
            "confidence_band": str(row.get("confidence_band") or "MEDIUM"),
            "missing_required_evidence": list(row.get("missing_required_evidence") or []),
        }
    return None


# ---------------------------------------------------------------------------
# Parent: stratified sample selection
# ---------------------------------------------------------------------------

SYMBOL_MAJOR = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}
_SYMBOL_METAL = re.compile(r"(GOLD|SILVER|^XAU|^XAG)")
_SYMBOL_CRYPTO = re.compile(r"(BITCOIN|ETHEREUM|^BTC|^ETH|SOLUSD|XRP|LTCUSD)")
_SYMBOL_INDEX = re.compile(r"(NDAQ|NASDAQ|US30|US500|SPX|N225|225|DAX|GER40|JPN|US100|TECH|USTEC)")
_SYMBOL_ENERGY = re.compile(r"(OIL|WTI|BRENT|NATGAS|^NG)")


def symbol_class(symbol: str) -> str:
    s = str(symbol or "").upper().lstrip("#")
    if _SYMBOL_METAL.search(s):
        return "metal"
    if _SYMBOL_CRYPTO.search(s):
        return "crypto"
    if _SYMBOL_INDEX.search(s) or str(symbol or "").startswith("#") or re.search(r"\d{2,4}", s):
        return "index"
    if _SYMBOL_ENERGY.search(s):
        return "energy"
    if re.fullmatch(r"[A-Z]{6}", s):
        return "FX-major" if s in SYMBOL_MAJOR else "FX-cross"
    if re.fullmatch(r"[A-Z]{6,}", s):
        return "FX-cross"
    return "other"


def candidate_stratum(count: int) -> str:
    if count <= 1:
        return "cand_1"
    if count == 2:
        return "cand_2"
    return "cand_3plus"


KNOWN_IDS = (
    "591813800_1788925102_208218984_1788951947_AUDJPY_32618",
    "591813800_1788925102_208218984_1788951325_USDCHF_19925",
)


def select_sample(bus_reader: BusReader, sample_size: int, seed: int) -> dict:
    _per_id, muse = bus_reader.usage_rows(None)
    archives = bus_reader.archived_payload_index()
    analyst_ids = sorted(
        {
            str(row.get("request_id") or "")
            for row in muse.get("analyst", [])
            if row.get("status") == "ok" and str(row.get("request_id") or "") in archives
        }
    )
    critic_ids = {
        str(row.get("request_id") or "")
        for row in muse.get("critic", [])
        if row.get("status") == "ok"
    }
    adjudicator_ids = {
        str(row.get("request_id") or "")
        for row in muse.get("adjudicator", [])
        if row.get("status") == "ok"
    }
    anchored = {
        str(row.get("request_id") or "")
        for row in muse.get("analyst", [])
        if row.get("cached_input_tokens") == ANALYST_STATIC_ANCHOR_TOKENS
    }
    anchored_crit = {
        str(row.get("request_id") or "")
        for row in muse.get("critic", [])
        if row.get("cached_input_tokens") == CRITIC_STATIC_ANCHOR_TOKENS
    }

    def order(request_id: str) -> str:
        return hashlib.sha256(f"{seed}|{request_id}".encode()).hexdigest()

    strata: dict[tuple[str, str], list[str]] = {}
    detail: dict[str, dict] = {}
    for request_id in analyst_ids:
        try:
            payload = read_json_any_encoding(archives[request_id])
        except Exception:
            continue
        count = len(payload.get("candidates") or [])
        klass = symbol_class(str(payload.get("symbol") or ""))
        stratum = candidate_stratum(count)
        strata.setdefault((stratum, klass), []).append(request_id)
        roles = ["analyst"]
        if request_id in critic_ids:
            roles.append("critic")
        if request_id in adjudicator_ids:
            roles.append("adjudicator")
        detail[request_id] = {
            "request_id": request_id,
            "archive_path": str(archives[request_id]),
            "symbol": str(payload.get("symbol") or ""),
            "symbol_class": klass,
            "n_candidates": count,
            "stratum": f"{stratum}/{klass}",
            "ledger_roles_muse": roles,
            "anchor_6513_analyst": request_id in anchored,
            "anchor_1265_critic": request_id in anchored_crit,
        }
    for rows in strata.values():
        rows.sort(key=order)

    # The two known approvals from CLAUDE.md 4ac are required by the spec when
    # present, even if their ledger rows were answered by the Luna fallback leg
    # rather than by Muse (their factor is then reported under its own model).
    for known in KNOWN_IDS:
        if known in detail or known not in archives:
            continue
        try:
            payload = read_json_any_encoding(archives[known])
        except Exception:
            continue
        count = len(payload.get("candidates") or [])
        klass = symbol_class(str(payload.get("symbol") or ""))
        detail[known] = {
            "request_id": known,
            "archive_path": str(archives[known]),
            "symbol": str(payload.get("symbol") or ""),
            "symbol_class": klass,
            "n_candidates": count,
            "stratum": f"{candidate_stratum(count)}/{klass}",
            "ledger_roles_muse": [],
            "anchor_6513_analyst": False,
            "anchor_1265_critic": False,
            "note": "required-by-spec known id; no Muse row for this request id",
        }

    selected: list[str] = []
    for known in KNOWN_IDS:
        if known in detail and known not in selected:
            selected.append(known)
    for request_id in sorted(anchored, key=order):
        if request_id in detail and request_id not in selected:
            selected.append(request_id)
    cells = sorted(strata)
    cursor = {cell: 0 for cell in cells}
    while len(selected) < sample_size and any(cursor[cell] < len(strata[cell]) for cell in cells):
        for cell in cells:
            rows = strata[cell]
            while cursor[cell] < len(rows) and rows[cursor[cell]] in selected:
                cursor[cell] += 1
            if cursor[cell] < len(rows):
                selected.append(rows[cursor[cell]])
                cursor[cell] += 1
                if len(selected) >= sample_size:
                    break
    counts: dict[str, int] = {}
    for request_id in selected:
        counts[detail[request_id]["stratum"]] = counts.get(detail[request_id]["stratum"], 0) + 1
    return {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bus": str(bus_reader.bus),
        "sample_size": len(selected),
        "meets_minimum_30": len(selected) >= 30,
        "seed": seed,
        "selection_rule": (
            "round-robin over (candidate-count stratum x symbol class) cells built from "
            "request ids that have BOTH a successful Muse row in logs/openai_usage.ndjson "
            "AND an archived python_*.json payload; the two known approvals and every "
            "request carrying the exact 6,513 analyst static-prefix anchor are taken first"
        ),
        "available_population": {f"{cell[0]}/{cell[1]}": len(rows) for cell, rows in strata.items()},
        "selected_strata_summary": counts,
        "anchor_requests_in_sample": sum(1 for rid in selected if detail[rid]["anchor_6513_analyst"]),
        "critic_anchor_requests_in_sample": sum(1 for rid in selected if detail[rid]["anchor_1265_critic"]),
        "muse_ledger_ok_rows_by_role": {
            role: sum(1 for row in rows if row.get("status") == "ok") for role, rows in muse.items()
        },
        "rows": [detail[request_id] for request_id in selected],
    }


# ---------------------------------------------------------------------------
# Parent: calibration
# ---------------------------------------------------------------------------

def calibrate(captures: list[dict], usage: dict) -> dict:
    per_request: list[dict] = []
    for capture in captures:
        request_id = capture["request_id"]
        for role in ROLES:
            wire = capture.get(role)
            if not isinstance(wire, dict):
                continue
            row = (usage.get(request_id) or {}).get(role) or {}
            provider_input = row.get("input_tokens")
            if not isinstance(provider_input, int) or provider_input <= 0 or not wire["whole_units"]:
                continue
            factor = provider_input / wire["whole_units"]
            entry = {
                "request_id": request_id,
                "role": role,
                "ledger_model": row.get("model"),
                "ledger_status": row.get("status"),
                "provider_input_tokens": provider_input,
                "provider_cached_input_tokens": row.get("cached_input_tokens"),
                "proxy_whole_units": wire["whole_units"],
                "proxy_static_units": wire["static_units"],
                "proxy_instructions_units": wire["instructions_units"],
                "proxy_instructions_static_units": wire["instructions_static_units"],
                "proxy_schema_units": wire["schema_units"],
                "proxy_input_array_units": wire["input_array_units"],
                "len_instructions": wire["len_instructions"],
                "len_instructions_static": wire["first_volatile_byte_in_instructions"],
                "len_schema_text": wire["len_schema_text"],
                "len_input_text": wire["len_input_text"],
                "proxy_dynamic_units": max(0, wire["whole_units"] - wire["static_units"]),
                "global_factor": factor,
                "uncalibrated_error_pct": (wire["whole_units"] - provider_input) / provider_input * 100.0,
                "wire_max_output_tokens": wire.get("max_output_tokens"),
                "ledger_max_output_tokens": row.get("max_output_tokens"),
                "wire_reasoning_effort": (wire.get("reasoning") or {}).get("effort"),
                "static_factor": None,
                "dynamic_factor": factor,
                "static_prefix_check": None,
            }
            cached = row.get("cached_input_tokens")
            if cached in (ANALYST_STATIC_ANCHOR_TOKENS, CRITIC_STATIC_ANCHOR_TOKENS) and wire["static_units"]:
                static_factor = float(cached) / wire["static_units"]
                entry["static_factor"] = static_factor
                entry["static_anchor"] = int(cached)
                dynamic_units = entry["proxy_dynamic_units"] or 1
                entry["dynamic_factor"] = max(0, provider_input - int(cached)) / dynamic_units
                entry["static_prefix_check"] = {
                    "anchor_tokens": int(cached),
                    "global_factor_predicts_static_prefix": round(wire["static_units"] * factor, 1),
                    "absolute_error_pct": abs(wire["static_units"] * factor - cached) / cached * 100.0,
                    "instructions_only_estimate": round(static_factor * wire["instructions_static_units"], 1),
                    "implied_tokens_per_char_static": round(
                        float(cached) / max(1, wire["len_instructions"] + wire["len_schema_text"]), 4
                    ),
                    "implied_tokens_per_char_dynamic": round(
                        max(0.0, provider_input - float(cached)) / max(1, wire["len_input_text"]), 4
                    ),
                }
            per_request.append(entry)
    muse = [row for row in per_request if row["ledger_model"] == MUSE_MODEL]
    factors_all = [row["global_factor"] for row in per_request]
    factors_muse = [row["global_factor"] for row in muse]
    static_factors = [row["static_factor"] for row in per_request if row["static_factor"]]
    anchored = [row for row in per_request if row["static_prefix_check"]]
    static_errors = [row["static_prefix_check"]["absolute_error_pct"] for row in anchored]

    def leave_one_out(rows: list[dict], two_regime: bool) -> list[float]:
        """Error of a factor learned from the OTHER requests only.

        The per-request factor is exact on its own request by construction, so the
        only honest generalization test is leave-one-out: predict a request's
        provider ``input_tokens`` from factors measured on the rest of the sample.
        """
        errors: list[float] = []
        for row in rows:
            others = [other for other in rows if other is not row]
            if len(others) < 3:
                continue
            if two_regime:
                static_pool = [other["static_factor"] for other in others if other.get("static_factor")]
                dynamic_pool = [other["dynamic_factor"] for other in others if other.get("static_factor")]
                if not static_pool or not dynamic_pool:
                    continue
                predicted = (
                    row["proxy_static_units"] * (percentile(static_pool, 0.5) or 0)
                    + row["proxy_dynamic_units"] * (percentile(dynamic_pool, 0.5) or 0)
                )
            else:
                pool = [other["global_factor"] for other in others]
                predicted = row["proxy_whole_units"] * (percentile(pool, 0.5) or 0)
            actual = row["provider_input_tokens"]
            if actual:
                errors.append(abs(predicted - actual) / actual * 100.0)
        return errors

    per_role: dict[str, dict] = {}
    for role in ROLES:
        role_rows = [row for row in per_request if row["role"] == role]
        role_muse = [row for row in role_rows if row["ledger_model"] == MUSE_MODEL]
        pool = role_muse or role_rows
        per_role[role] = {
            "n": len(role_rows),
            "n_muse": len(role_muse),
            "global_factor_muse_rows": stats_block([row["global_factor"] for row in role_muse]),
            "global_factor_any_ledger": stats_block([row["global_factor"] for row in role_rows]),
            "global_factor": stats_block([row["global_factor"] for row in pool]),
            "static_factor_from_anchor": stats_block([row["static_factor"] for row in pool if row.get("static_factor")]),
            "dynamic_factor_from_anchor": stats_block(
                [row["dynamic_factor"] for row in pool if row.get("static_factor")]
            ),
            "leave_one_out_error_pct_single_factor": stats_block(leave_one_out(pool, two_regime=False)),
            "leave_one_out_error_pct_two_regime": stats_block(leave_one_out(pool, two_regime=True)),
            "uncalibrated_error_pct": stats_block([row["uncalibrated_error_pct"] for row in pool]),
            "provider_input_tokens": stats_block([float(row["provider_input_tokens"]) for row in pool]),
            "proxy_whole_units": stats_block([float(row["proxy_whole_units"]) for row in pool]),
        }
    muse_pool = muse or per_request
    per_request_factor = {
        "min": round(min(factors_all), 5) if factors_all else None,
        "median": round(percentile(factors_all, 0.5) or 0, 5),
        "max": round(max(factors_all), 5) if factors_all else None,
        "n": len(factors_all),
    }
    per_request_factor_muse_only = {
        "min": round(min(factors_muse), 5) if factors_muse else None,
        "median": round(percentile(factors_muse, 0.5) or 0, 5),
        "max": round(max(factors_muse), 5) if factors_muse else None,
        "n": len(factors_muse),
    }
    static_prefix_factor_from_anchor_only = {
        "min": round(min(static_factors), 5) if static_factors else None,
        "median": round(percentile(static_factors, 0.5) or 0, 5),
        "max": round(max(static_factors), 5) if static_factors else None,
        "n": len(static_factors),
    }

    def hypothesis(what: str, selector, char_selector=None) -> dict:
        per_role_h: dict[str, list[float]] = {}
        per_role_chars: dict[str, list[float]] = {}
        for row in anchored:
            units = selector(row)
            if units:
                per_role_h.setdefault(row["role"], []).append(row["static_anchor"] / units)
            if char_selector:
                chars = char_selector(row)
                if chars:
                    per_role_chars.setdefault(row["role"], []).append(row["static_anchor"] / chars)

        def spread(values: dict[str, list[float]]) -> float | None:
            medians = {role: percentile(vals, 0.5) for role, vals in values.items()}
            if len(medians) > 1 and min(medians.values()) > 0:
                return round(max(medians.values()) / min(medians.values()), 3)
            return None

        return {
            "hypothesis": what,
            "tokens_per_proxy_unit_by_role": {
                role: stats_block(values) for role, values in per_role_h.items()
            },
            "tokens_per_character_by_role": {
                role: stats_block(values) for role, values in per_role_chars.items()
            },
            "roles_observed": sorted(per_role_h),
            "cross_role_spread_ratio_proxy_units": spread(per_role_h),
            "cross_role_spread_ratio_characters": spread(per_role_chars),
            "consistent_across_roles": bool(
                (spread(per_role_chars) or spread(per_role_h) or 9) < 1.5
            ),
        }

    static_prefix_hypotheses = [
        hypothesis(
            "cached prefix = static head of instructions only",
            lambda row: row["proxy_instructions_static_units"],
            lambda row: float(row["len_instructions_static"]),
        ),
        hypothesis(
            "cached prefix = instructions only (full text, per-request tail included)",
            lambda row: row["proxy_instructions_units"],
            lambda row: float(row["len_instructions"]),
        ),
        hypothesis(
            "cached prefix = instructions + strict response schema",
            lambda row: row["proxy_static_units"],
            lambda row: float(row["len_instructions_static"] + row["len_schema_text"]),
        ),
    ]
    dynamic_density = {
        role: stats_block([
            max(0.0, row["provider_input_tokens"] - row["static_anchor"])
            / max(1, row["len_input_text"])
            for row in anchored
            if row["role"] == role
        ])
        for role in ROLES
    }
    static_prefix_hypothesis_verdict = (
        "Reported, not asserted: the only locally available anchors are the two provider "
        "cached-prefix counts, and the three hypotheses cannot be separated to better than "
        "the cross-role spread shown above (instructions+schema is the most consistent on "
        "characters/unit). The dynamic evidence body is measurably denser in characters per "
        "token than the static prefix, which is why one global proxy factor mis-predicts the "
        "anchor by tens of percent. SES-001 must confirm the documented meaning of the cached "
        "prefix against the provider docs before the design relies on it."
    )
    return {
        "proxy": {
            "name": "documented deterministic proxy (WP2_SPEC); tiktoken o200k_base used only if importable",
            "environment_probe": tokenizer_environment(),
            "regex": _PROXY_PIECE_RE.pattern,
            "weights": {
                "whitespace": "skipped",
                "letter_piece": "ceil(utf8_len/4)",
                "digit_piece": "ceil(utf8_len/3)",
                "punctuation_piece": "ceil(utf8_len/1.5)",
                "minimum_per_piece": 1,
            },
            "whole_wire_text_definition": (
                "instructions + canonical schema JSON + json.dumps(wire['input']) with the "
                "SDK's compact separators; the same definition the supervisor spike used"
            ),
            "known_limitation": (
                "one scalar factor cannot fix a per-character-class bias: if the measured "
                "tokens-per-proxy-unit of the anchored static prefix differs from that of the "
                "volatile evidence body, a single global factor systematically mis-weights one "
                "of them.  calibration.json's static_factor vs dynamic_factor columns measure "
                "exactly that spread; per-subtree tokens are emitted under both regimes."
            ),
        },
        "per_request": sorted(per_request, key=lambda row: (row["role"], row["request_id"])),
        "per_role": per_role,
        "per_request_factor": per_request_factor,
        "per_request_factor_muse_only": per_request_factor_muse_only,
        "static_prefix_factor_from_anchor_only": static_prefix_factor_from_anchor_only,
        "uncalibrated_proxy_error_pct": stats_block([row["uncalibrated_error_pct"] for row in per_request]),
        "calibrated_error_pct": {
            "definition": (
                "the per-request factor is exact on its own request by construction, so the "
                "reported calibrated error is leave-one-out: the factor learned from every "
                "OTHER request predicts this request's provider input_tokens"
            ),
            "single_global_factor_all_rows": stats_block(leave_one_out(per_request, two_regime=False)),
            "single_global_factor_muse_rows": stats_block(leave_one_out(muse_pool, two_regime=False)),
            "two_regime_muse_rows": stats_block(leave_one_out(muse_pool, two_regime=True)),
        },
        "any_uncalibrated_error_gt_15pct": any(
            abs(row["uncalibrated_error_pct"]) > ESCALATION_ERROR_THRESHOLD * 100 for row in per_request
        ),
        "any_calibrated_holdout_error_gt_15pct": any(
            value > ESCALATION_ERROR_THRESHOLD * 100 for value in leave_one_out(muse_pool, two_regime=False)
        ),
        "any_two_regime_calibrated_error_gt_15pct": any(
            value > ESCALATION_ERROR_THRESHOLD * 100 for value in leave_one_out(muse_pool, two_regime=True)
        ),
        "static_prefix_hypotheses": static_prefix_hypotheses,
        "static_prefix_hypothesis_verdict": static_prefix_hypothesis_verdict,
        "dynamic_evidence_density_tokens_per_char": dynamic_density,
        "static_prefix_anchor_checks": {
            "anchor_6513_analyst_rows": sum(1 for row in anchored if row["static_anchor"] == ANALYST_STATIC_ANCHOR_TOKENS),
            "anchor_1265_critic_rows": sum(1 for row in anchored if row["static_anchor"] == CRITIC_STATIC_ANCHOR_TOKENS),
            "n_checked": len(anchored),
            "global_factor_error_pct": stats_block(static_errors),
            "detail": [
                {
                    "request_id": row["request_id"],
                    "role": row["role"],
                    "anchor": row["static_anchor"],
                    "proxy_static_units": row["proxy_static_units"],
                    "static_factor": round(row["static_factor"], 5),
                    "global_factor": round(row["global_factor"], 5),
                    "dynamic_factor": round(row["dynamic_factor"], 5),
                    "global_factor_estimate_of_anchor": row["static_prefix_check"]["global_factor_predicts_static_prefix"],
                    "error_pct": round(row["static_prefix_check"]["absolute_error_pct"], 2),
                    "tokens_per_char_static": row["static_prefix_check"]["implied_tokens_per_char_static"],
                    "tokens_per_char_dynamic": row["static_prefix_check"]["implied_tokens_per_char_dynamic"],
                }
                for row in anchored
            ],
        },
        "wire_vs_ledger_max_output_tokens": [
            {
                "request_id": row["request_id"],
                "role": row["role"],
                "wire": row["wire_max_output_tokens"],
                "ledger": row["ledger_max_output_tokens"],
            }
            for row in per_request
        ],
    }


# ---------------------------------------------------------------------------
# Parent: per-subtree input attribution (IN-001)
# ---------------------------------------------------------------------------

# Subtree groups that form a disjoint partition of the wire input, so that
# summing ``pct_of_input`` across the group is meaningful and across groups is
# deliberately not.
PARTITION_GROUPS = {
    "top_level_partition": ("envelope.<section>",),
    "catalog_partition": (
        "evidence_catalog.items.id",
        "evidence_catalog.items.p",
        "evidence_catalog.items.v",
        "evidence_catalog.items.c",
        "evidence_catalog.items.keys_punctuation",
    ),
    "leaf_class_partition": ("leaf_class:<class>",),
}


def attribute_inputs(captures: list[dict], calibration: dict) -> list[dict]:
    factors = {(row["request_id"], row["role"]): row for row in calibration["per_request"]}
    provider_inputs: dict[str, list[float]] = {}
    for row in calibration["per_request"]:
        provider_inputs.setdefault(row["role"], []).append(float(row["provider_input_tokens"]))

    # static = byte-identical serialization across every captured request of the role
    serializations: dict[tuple[str, str], set] = {}
    for capture in captures:
        for role in ROLES:
            wire = capture.get(role)
            if not isinstance(wire, dict):
                continue
            serializations.setdefault((role, "instructions"), set()).add(wire["instructions_sha8"])
            serializations.setdefault((role, "instructions.static_head"), set()).add(
                wire["instructions_static_sha8"]
            )
            serializations.setdefault((role, "text.format.schema"), set()).add(wire["schema_sha8"])
            envelope = capture.get("envelope") if role == "analyst" else capture.get(f"{role}_envelope")
            for name, block in ((envelope or {}).get("sections") or {}).items():
                serializations.setdefault((role, name), set()).add(block.get("sha8"))

    # DUP-001 class (a)/(c): which non-catalog envelope sections host a duplicated twin.
    duplicated_sections: dict[str, set] = {}
    duplicated_rows = {capture["request_id"]: 0 for capture in captures}
    for capture in captures:
        dup = capture.get("duplication") or {}
        counts = dup.get("counts") or {}
        duplicated_rows[capture["request_id"]] = int(
            counts.get("a_same_fact_serialized_twice", 0) + counts.get("c_alias", 0)
        )
        for pair in dup.get("duplicated_catalog_paths") or []:
            twin = pair.split(" == ", 1)[-1]
            duplicated_sections.setdefault(twin.split(".", 1)[0], set()).add(twin)

    # Role-level regime factors.  The anchored requests prove the analyst static
    # prefix is byte-identical across requests (same proxy units, same provider
    # anchor), so its token count is the SAME number for every request of that
    # role and the anchor-derived factor is the right one to use everywhere --
    # including on requests whose own ledger row shows no cache hit.  Roles with
    # no anchor row at all fall back to that role's median global factor and say
    # so in ``factor_source``.
    role_regime: dict[str, dict] = {}
    for role in ROLES:
        rows_role = [row for row in calibration["per_request"] if row["role"] == role]
        anchored_role = [row for row in rows_role if row.get("static_factor")]
        if anchored_role:
            role_regime[role] = {
                "static": percentile([row["static_factor"] for row in anchored_role], 0.5),
                "dynamic": percentile([row["dynamic_factor"] for row in anchored_role], 0.5),
                "source": f"provider_anchored_static_prefix_median_of_{len(anchored_role)}_rows",
            }
        else:
            median_global = percentile([row["global_factor"] for row in rows_role], 0.5)
            role_regime[role] = {
                "static": median_global,
                "dynamic": median_global,
                "source": "role_median_global_factor_no_anchor_observed",
            }
    acc: dict[tuple[str, str], dict] = {}
    for capture in captures:
        request_id = capture["request_id"]
        for role in ROLES:
            wire = capture.get(role)
            cal = factors.get((request_id, role))
            if not isinstance(wire, dict) or cal is None:
                continue
            own_static = cal["static_factor"] or role_regime[role]["static"] or cal["global_factor"]
            own_dynamic = (
                cal["dynamic_factor"] if cal.get("static_factor") else role_regime[role]["dynamic"]
            ) or cal["global_factor"]
            static_factor = own_static
            dynamic_factor = own_dynamic
            envelope = capture.get("envelope") if role == "analyst" else capture.get(f"{role}_envelope")
            n_candidates = max(1, int((envelope or {}).get("n_candidates") or 1))
            _put(acc, role, "instructions", "prompt", wire["instructions_units"] * static_factor,
                 wire["instructions_units"], serializations)
            _put(acc, role, "instructions.static_head", "prompt",
                 (wire["instructions_units"] - wire["instructions_static_tail_units"]) * static_factor,
                 wire["instructions_units"] - wire["instructions_static_tail_units"], serializations)
            _put(acc, role, "instructions.volatile_tail", "prompt",
                 wire["instructions_static_tail_units"] * dynamic_factor,
                 wire["instructions_static_tail_units"], serializations)
            _put(acc, role, "text.format.schema", "schema", wire["schema_units"] * static_factor,
                 wire["schema_units"], serializations)
            for name, block in sorted(((envelope or {}).get("sections") or {}).items()):
                units = int(block.get("units") or 0)
                _put(acc, role, name, block.get("kind") or "envelope_section",
                     units * dynamic_factor, units, serializations)
            for cls, units in sorted(((envelope or {}).get("leaf_classes") or {}).items()):
                _put(acc, role, f"leaf_class:{cls}", "leaf_class", units * dynamic_factor, units, serializations)
            _put(acc, role, "key_names:whole_input", "key_names",
                 int((envelope or {}).get("key_name_total_units", 0)) * dynamic_factor,
                 int((envelope or {}).get("key_name_total_units", 0)), serializations)
            _put(acc, role, "key_names:candidate_array_repetition", "key_names",
                 int((envelope or {}).get("candidate_array_key_name_units", 0)) * dynamic_factor,
                 int((envelope or {}).get("candidate_array_key_name_units", 0)), serializations)
            for name, block in sorted(((envelope or {}).get("per_candidate_key_units") or {}).items()):
                if not isinstance(block, dict) or block.get("p50") is None:
                    continue
                _put(
                    acc, role, f"candidate_subtree.{name}", "candidate_subtree",
                    (block["p50"] * n_candidates) * dynamic_factor, block["p50"] * n_candidates,
                    serializations,
                )

    rows: list[dict] = []
    for (role, name), record in acc.items():
        block = stats_block(record["tokens"])
        median_input = percentile(provider_inputs.get(role) or [], 0.5) or 0
        distinct = len(serializations.get((role, name)) or set())
        head = name.replace("envelope.", "")
        duplicated: Any = None
        if name == "evidence_catalog.items.v":
            duplicated = "yes_class_a_c" if duplicated_rows and any(duplicated_rows.values()) else "no"
        elif name.startswith("evidence_catalog.items"):
            duplicated = None
        elif head in duplicated_sections:
            duplicated = "yes_class_a_c"
        rows.append(
            {
                "role": role,
                "subtree": name,
                "kind": record["kind"],
                "n_requests": block["n"],
                "tokens_avg": block["avg"],
                "tokens_p50": block["p50"],
                "tokens_p95": block["p95"],
                "tokens_min": block["min"],
                "tokens_max": block["max"],
                "pct_of_input_p50": round(100.0 * (block["p50"] or 0) / median_input, 2) if median_input else None,
                "static": bool(distinct == 1 and block["n"] > 1),
                "distinct_serializations": distinct,
                "duplicated": duplicated,
                "factor_source": role_regime[role]["source"],
                "static_factor_used": round(role_regime[role]["static"] or 0, 5),
                "dynamic_factor_used": round(role_regime[role]["dynamic"] or 0, 5),
            }
        )
    rows.sort(key=lambda row: (row["role"], -(row["tokens_p50"] or 0)))
    return rows


def _put(acc, role, name, kind, tokens, units, serializations) -> None:
    record = acc.setdefault((role, name), {"tokens": [], "kind": kind})
    record["tokens"].append(tokens)


# ---------------------------------------------------------------------------
# Parent: WIRE-001 cache layout
# ---------------------------------------------------------------------------

def cache_layout(captures: list[dict], calibration: dict) -> dict:
    factors = {(row["request_id"], row["role"]): row for row in calibration["per_request"]}
    per_role: dict[str, dict] = {}
    for role in ROLES:
        pairs = [
            (capture["request_id"], capture[role])
            for capture in captures
            if isinstance(capture.get(role), dict)
        ]
        if not pairs:
            continue
        calibrations = [factors[(rid, role)] for rid, _wire in pairs if (rid, role) in factors]
        anchored = [row for row in calibrations if row.get("static_prefix_check")]
        per_role[role] = {
            "n_requests": len(pairs),
            "instructions_static_across_requests": len({wire["instructions_static_sha8"] for _rid, wire in pairs}) == 1,
            "instructions_full_identical_across_requests": len({wire["instructions_sha8"] for _rid, wire in pairs}) == 1,
            "schema_identical_across_requests": len({wire["schema_sha8"] for _rid, wire in pairs}) == 1,
            "schema_name": pairs[0][1].get("schema_name"),
            "proxy_units": {
                "instructions_total": stats_block([wire["instructions_units"] for _rid, wire in pairs]),
                "instructions_static_head": stats_block([wire["instructions_static_units"] for _rid, wire in pairs]),
                "instructions_volatile_tail": stats_block([wire["instructions_static_tail_units"] for _rid, wire in pairs]),
                "schema": stats_block([wire["schema_units"] for _rid, wire in pairs]),
                "static_prefix_instructions_plus_schema": stats_block([wire["static_units"] for _rid, wire in pairs]),
                "input_body": stats_block([wire["input_units"] for _rid, wire in pairs]),
            },
            "calibrated_static_prefix_tokens": stats_block([
                wire["static_units"] * factors[(rid, role)]["global_factor"]
                for rid, wire in pairs
                if (rid, role) in factors
            ]),
            "provider_cached_prefix_anchors_observed": sorted({int(row["static_anchor"]) for row in anchored}),
            "static_prefix_error_when_predicted_by_global_factor_pct": stats_block([
                row["static_prefix_check"]["absolute_error_pct"] for row in anchored
            ]),
            "first_volatile_byte_in_body": stats_block([wire["first_volatile_byte_in_body"] for _rid, wire in pairs]),
            "first_volatile_byte_in_instructions": stats_block(
                [wire["first_volatile_byte_in_instructions"] for _rid, wire in pairs]
            ),
            "wire_flags": {
                "store": pairs[0][1].get("store"),
                "truncation": pairs[0][1].get("truncation"),
                "verbosity": pairs[0][1].get("verbosity"),
                "reasoning": pairs[0][1].get("reasoning"),
                "extra_header_keys": pairs[0][1].get("extra_header_keys"),
                "x_opencode_session_sha8_values": sorted({wire["x_opencode_session_sha8"] for _rid, wire in pairs}),
                "session_header_identical_across_requests": len(
                    {wire["x_opencode_session_sha8"] for _rid, wire in pairs}
                ) == 1,
            },
            "sort_keys_interleave": _sort_keys_interleave(captures, role),
        }
    return {
        "meaning": "static prefix = instructions + strict response schema; the provider's own cached_input_tokens is the authority for what it actually caches",
        "note_on_wire_order": (
            "In the reconstructed HTTP body the SDK sends model, instructions, input, then "
            "text.format.schema, so the schema is NOT contiguous with the instructions: the "
            "measured cached prefix (instructions+schema) therefore cannot be a byte prefix "
            "of this body and must come from a provider-side canonical prompt. Measure, do "
            "not assume; SES-001 must confirm the documented meaning."
        ),
        "roles": per_role,
    }


def _sort_keys_interleave(captures: list[dict], role: str) -> dict:
    static_shas: dict[str, set] = {}
    order: list[str] | None = None
    for capture in captures:
        envelope = capture.get("envelope") if role == "analyst" else capture.get(f"{role}_envelope")
        if not isinstance(envelope, dict):
            continue
        sections = envelope.get("sections") or {}
        names = [name for name in sorted(sections) if sections[name]["kind"] == "envelope_section"]
        if order is None:
            order = names
        for name in names:
            static_shas.setdefault(name, set()).add(sections[name].get("sha8"))
    if not order:
        return {"available": False}
    static = {name for name in order if len(static_shas.get(name) or set()) == 1}
    tags = ["static" if name in static else "dynamic" for name in order]
    first_dynamic = next((index for index, tag in enumerate(tags) if tag == "dynamic"), None)
    static_after = sum(
        1 for index, tag in enumerate(tags) if tag == "static" and first_dynamic is not None and index > first_dynamic
    )
    return {
        "available": True,
        "sorted_key_order": [name.replace("envelope.", "") for name in order],
        "static_vs_dynamic_order": tags,
        "static_sections": sorted(name.replace("envelope.", "") for name in static),
        "dynamic_sections": sorted(name.replace("envelope.", "") for name in order if name not in static),
        "alternating_runs": 1 + sum(1 for left, right in zip(tags, tags[1:]) if left != right),
        "static_sections_after_first_dynamic_section": static_after,
        "sort_keys_interleaves_static_and_dynamic": static_after > 0,
    }


# ---------------------------------------------------------------------------
# Parent: OUT-001 output attribution
# ---------------------------------------------------------------------------

OUTPUT_SCHEMA_MODELS = (
    "ModelAIGateOutput", "ModelCandidateAssessment", "ModelVetoDecision",
    "ModelTargetArbitrationDecision", "ModelCriticDecision", "ModelCriticObjection",
    "ModelAdjudicatorDecision",
)
PYTHON_OWNED_MODELS = (
    "CandidateAssessment", "AIGateEnvelope", "CriticDecision", "AdjudicatorDecision",
    "TargetArbitrationDecision", "VetoDecision", "CriticObjection",
)


def schema_field_map() -> dict[str, list[str]]:
    """Provider-facing schema fields, parsed from the source with ``ast``."""
    source = (Path(__file__).resolve().parents[1] / "structured_models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = {node.name: [base.id for base in node.bases if isinstance(base, ast.Name)] for node in tree.body if isinstance(node, ast.ClassDef)}
    fields: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in OUTPUT_SCHEMA_MODELS:
            own = [
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            ]
            inherited: list[str] = []
            for base in parents.get(node.name, []):
                inherited.extend(fields.get(base, []))
            fields[node.name] = inherited + own
    return fields


def authoritative_field_set() -> set[str]:
    source = (Path(__file__).resolve().parents[1] / "structured_models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in PYTHON_OWNED_MODELS:
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    names.add(statement.target.id)
    return names


# A function whose own name says it gates, validates or resolves is a decision
# authority even when the field read is not literally inside an ``if`` test:
# ``_apply_ai_veto_gate(payload, decision)`` reads ``decision["veto"]`` and
# returns a demoted decision, which is a trade-affecting read by construction.
_DECISION_SCOPE_RE = re.compile(
    r"(gate|veto|allow|decision|arbitr|valid|apply|final|select|consensus|reject|abstain|bind|resolve|demote|synchron)"
)
# Validation-only scopes prove a value is present, in range, or bound to the
# request; they do not steer the trade on the value's content.
_VALIDATION_SCOPE_RE = re.compile(
    r"(valid|require|schema|identity|fingerprint|consistency|bind|hash|missing|coerce|normalize|echo|audit|integrity)"
)
_TELEMETRY_SCOPE_RE = re.compile(
    r"^(log_|_log|.*_report|report_|.*usage.*|.*telemetry.*|.*dashboard.*|.*analytics.*|.*diagnostic.*|.*audit.*)"
)
# A bare ``obj.summary`` only counts as a read of the *model field* when the
# receiver is plausibly the model/payload object; otherwise common words such as
# ``code`` or ``enabled`` match unrelated attributes everywhere in the repo.
_MODELISH_NAME_RE = re.compile(
    r"(decision|assess|model|output|payload|response|resp|result|body|json|envelope|record|"
    r"stored|selected|candidate|critic|adjudicat|arbitrat|veto|target|row|item|entry|data|args|"
    r"kwargs|obj|parsed|raw|doc)",
    re.IGNORECASE,
)
_TELEMETRY_FILES = {
    "expectancy_report.py", "openai_usage_logger.py", "opencode_go_accounting.py",
    "snapshot_diagnostics.py", "shadow_outcome_ledger.py", "calibration_pipeline.py",
    "experiment_registry.py", "audit_scalp_20260908.py",
}
_DECISION_FILES = {
    "ai_gate.py", "decision_pipeline.py",
    "execution_adjustment_contract.py", "repeatability_state.py",
}


def build_consumer_table(diagnostic_fields: set[str] | None = None) -> dict:
    """Field -> consumer table, derived from read sites, not from a hand list.

    A field counts as decision-altering when a read site sits in a *test*
    position (an ``if``/``while`` condition, a comparison, a boolean or a
    conditional expression) inside a decision-authority module, or when an MQL
    line that mentions it is itself a value test rather than a schema/parse call.
    Constructor keyword arguments are recorded separately as injection sites:
    they show Python re-writing the value into the authoritative envelope, which
    is persistence, not consumption.  Reads that live only in validation or
    binding helpers get their own class, because "the response is discarded if
    this field is absent" is a different claim from "this field's value decides
    the trade".
    """
    repo_python = Path(__file__).resolve().parents[1]
    repo_root = repo_python.parent
    diagnostic_fields = set(diagnostic_fields or ())
    fields = schema_field_map()
    names = sorted({name for group in fields.values() for name in group})
    reads: dict[str, list[dict]] = {name: [] for name in names}
    injections: dict[str, list[dict]] = {name: [] for name in names}
    definition_files = {"structured_models.py", "decision_integrity.py"}
    python_files = [path for path in sorted(repo_python.glob("*.py")) if path.name not in definition_files]
    scanned: list[str] = []
    failed: dict[str, str] = {}
    for path in python_files:
        try:
            # utf-8-sig: several product modules are authored with a BOM, and
            # ast.parse chokes on it -- skipping silently would drop ai_gate.py,
            # the single most important consumer, from the whole table.
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        except (OSError, SyntaxError, ValueError) as exc:
            failed[path.name] = f"{type(exc).__name__}:{exc}"
            continue
        scanned.append(path.name)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            guards = _guard_node_ids(node)
            for inner in ast.walk(node):
                found = _read_site_field(inner, names)
                if not found:
                    continue
                name, kind = found
                effect = guards.get(id(inner))
                site = {
                    "file": path.name,
                    "function": node.name,
                    "kind": kind,
                    "guard": effect == "gates",
                    "log_only_guard": effect == "log_only",
                    "decision_file": path.name in _DECISION_FILES,
                    "telemetry_file": path.name in _TELEMETRY_FILES,
                }
                (injections if kind == "inject" else reads)[name].append(site)
    mql_dir = repo_root / "MT5_PO3_Codex Include"
    mql_ea_dir = repo_root / "MT5_PO3_Codex Experts"
    mql_files = (
        sorted(mql_dir.glob("*.mqh"))
        + sorted(mql_ea_dir.glob("*.mq5"))
        + sorted(mql_dir.glob("*.mqh.inc"))
    )
    mql_sites: dict[str, list[dict]] = {name: [] for name in names}
    _MQL_PARSE_ONLY = re.compile(
        r"(_SchemaRequire|JsonGet|_AppendSchemaField|JsonKV|SchemaRequire(String|Number|Bool))"
    )
    _MQL_IF = re.compile(r"\bif\s*\(")
    _MQL_VS_IDENTIFIER = re.compile(r"(>=|<=|>|<|==|!=)\s*[A-Za-z_][\w.]*")
    _MQL_VS_LITERAL = re.compile(r"(>=|<=|>|<|==|!=)\s*-?\d")
    mql_scanned: list[str] = []
    for path in mql_files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        mql_scanned.append(path.name)
        current = "<module>"
        # Only a QUOTED key proves MQL is touching this JSON field: a bare
        # ``code``/``summary``/``enabled`` identifier in the EA is an unrelated
        # local variable wearing the same word.
        compiled = {name: re.compile(r'"' + re.escape(name) + r'"\b') for name in names}
        for line in text.splitlines():
            match = re.match(r"\s*[A-Za-z_][\w<>~]*\s+(\w+)\s*\([^;]*\)\s*(?:const)?\s*\{?\s*$", line)
            if match:
                current = match.group(1)
            parse_only = bool(_MQL_PARSE_ONLY.search(line))
            # A comparison against a numeric literal (``x >= 0.0 && x <= 1.0``) is
            # a range validation; a comparison against a named threshold or an
            # explicit ``if`` is a decision.
            range_check = bool(_MQL_VS_LITERAL.search(line)) and not _MQL_VS_IDENTIFIER.search(line)
            value_gate = (not parse_only) and (
                bool(_MQL_IF.search(line)) or (bool(_MQL_VS_IDENTIFIER.search(line)) and not range_check)
            )
            for name, pattern in compiled.items():
                if pattern.search(line):
                    mql_sites[name].append(
                        {
                            "file": path.name,
                            "function": current,
                            "guard": value_gate,
                            "kind": (
                                "schema_or_parse_call" if parse_only
                                else "range_validation" if range_check
                                else "value_test"
                            ),
                        }
                    )
    authoritative = authoritative_field_set()
    rows = []
    for name in names:
        python_read_sites = _dedupe(reads[name])
        inject_sites = _dedupe(injections[name])
        mql = _dedupe(mql_sites[name])
        # Content-driven gating: the read sits in an actual test position inside
        # a decision-authority module (not merely inside a function that happens
        # to be named *_valid*/_bind*).
        python_content_guards = [
            site for site in python_read_sites
            if site["decision_file"] and site["guard"] and not _VALIDATION_SCOPE_RE.search(site["function"])
        ]
        python_log_only_guards = [
            site for site in python_read_sites
            if site["decision_file"] and site.get("log_only_guard")
        ]
        python_validation_reads = [
            site for site in python_read_sites
            if site["decision_file"] and _VALIDATION_SCOPE_RE.search(site["function"])
        ]
        mql_gates = [site for site in mql if site["guard"]]
        guard_sites = python_content_guards + mql_gates
        non_telemetry_reads = [
            site for site in python_read_sites
            if not site["telemetry_file"] and not _TELEMETRY_SCOPE_RE.match(site["function"])
        ]
        declared_diagnostic = name in diagnostic_fields
        if declared_diagnostic and not python_content_guards:
            # The role prompt itself says this value has no trade authority, and
            # no outcome-changing test reads it: its only "gate" appearance is the
            # diagnostic threshold-crossing log line.
            category = "diagnostic_logged_only"
        elif python_content_guards or mql_gates:
            category = "decision_value"
        elif python_validation_reads:
            category = "validated_or_bound"
        elif non_telemetry_reads or (mql and name in authoritative):
            category = "persisted"
        elif any(site["telemetry_file"] or _TELEMETRY_SCOPE_RE.match(site["function"]) for site in python_read_sites):
            category = "telemetry_only"
        elif name in authoritative or inject_sites or mql:
            category = "persisted"
        else:
            category = "never_read"
        python_guards = python_content_guards
        rows.append(
            {
                "schema": sorted(model for model, group in fields.items() if name in group),
                "field": name,
                "category": category,
                "alters_trading_or_risk_decision": category == "decision",
                "gates_in_python": bool(python_guards),
                "gates_in_mql": bool(mql_gates),
                "mql_referenced": bool(mql),
                "persisted_in_authoritative_envelope": bool(name in authoritative or inject_sites),
                "decision_guard_sites": (
                    [
                        {"file": s["file"], "function": s["function"], "side": "python"}
                        for s in python_guards[:4]
                    ]
                    + [
                        {"file": s["file"], "function": s["function"], "side": "mql"}
                        for s in mql_gates[:2]
                    ]
                ),
                "python_read_consumers": [
                    {"file": s["file"], "function": s["function"], "kind": s["kind"]}
                    for s in python_read_sites[:10]
                ],
                "python_inject_sites": [
                    {"file": s["file"], "function": s["function"]} for s in inject_sites[:6]
                ],
                "mql_reference_sites": [
                    {"file": s["file"], "function": s["function"], "conditional_line": s["guard"]}
                    for s in mql[:6]
                ],
                "read_site_count": len(python_read_sites),
                "mql_reference_count": len(mql),
                "decision_named_scope_reads": [
                    {"file": s["file"], "function": s["function"]}
                    for s in python_read_sites
                    if s["decision_file"] and _DECISION_SCOPE_RE.search(s["function"])
                ][:6],
                "validation_scope_reads": [
                    {"file": s["file"], "function": s["function"]}
                    for s in python_validation_reads
                ][:6],
            }
        )
    rows.sort(key=lambda row: (row["category"], row["field"]))
    return {
        "method": (
            "AST scan of every read site (attribute, string subscript, .get/.pop/getattr literal) "
            "of each provider-schema field across python/*.py, excluding structured_models.py and "
            "decision_integrity.py which only define the schemas; constructor keyword arguments "
            "are recorded as injection sites (persistence), not consumption. A read is "
            "content-gating when it sits in an actual test position (if/while test, comparison, "
            "boolean or conditional expression) inside a decision-authority module that is not "
            "itself a validation/binding helper. MQL5 (.mqh includes and Experts/*.mq5 EAs) is "
            "matched by word boundary and split into schema/parse calls (_SchemaRequire*, "
            "JsonGet*, _AppendSchemaField, JsonKV*) versus value tests (if(/&&/|| lines that are "
            "not parse calls); only a value test gates."
        ),
        "categories": {
            "decision_value": "the value's CONTENT steers a trading/risk outcome (an outcome-changing test in a decision module, or an MQL value test)",
            "diagnostic_logged_only": "declared diagnostic by the role prompt and only ever compared to build the threshold-crossing log line",
            "validated_or_bound": "read only by validation/binding helpers: presence, range or identity is enforced fail-closed, the content does not steer",
            "persisted": "read or injected outside any guard: carried into the authoritative response or a log line",
            "telemetry_only": "only referenced from logging/reporting/analytics modules",
            "never_read": "no read or injection site anywhere in Python or MQL",
        },
        "declared_diagnostic_fields": sorted(diagnostic_fields),
        "caveat": (
            "Heuristic, not proof. A value can still be decision-altering through a flow this "
            "scan does not model (copied into a struct, then tested under another name), and an "
            "MQL 'value test' is a line-level approximation. Every row carries its concrete "
            "file:function evidence so the classification can be spot-checked; the token numbers "
            "are the measurement, the category is an interpretation of them."
        ),
        "declared_diagnostic_source": (
            "parsed from the captured analyst instructions: \"... are explicitly "
            "uncalibrated diagnostic estimates. They have no direct positive or "
            "negative trade authority\""
        ),
        "decision_authority_modules": sorted(_DECISION_FILES),
        "telemetry_modules": sorted(_TELEMETRY_FILES),
        "python_files_scanned": scanned,
        "python_files_failed": failed,
        "mql_files_scanned": mql_scanned,
        "schemas": fields,
        "authoritative_fields": sorted(authoritative),
        "fields": rows,
        "summary": {
            category: sum(1 for row in rows if row["category"] == category)
            for category in (
                "decision_value", "diagnostic_logged_only", "validated_or_bound",
                "persisted", "telemetry_only", "never_read",
            )
        },
    }


def _guard_node_ids(function) -> dict:
    """Map each node id inside a test position to the effect that test produces.

    Returning the plain set once made every field look decision-altering,
    because ``_apply_ai_veto_gate`` really does compare ``decision.chop_risk``
    -- and then only appends the crossing to a list that is interpolated into a
    log line labelled ``authority=diagnostic_only_no_direct_trade_authority``.
    A test is a gate only when what happens inside it can change the outcome:
    a raise, a mutation of the decision object, a return, or a write to a name
    that is read anywhere outside that log call.
    """
def _guard_node_ids(function) -> dict:
    """Map each node id inside a test position to the effect that test produces.

    Returning a bare set once made every field look decision-altering, because
    ``_apply_ai_veto_gate`` really does compare ``decision.chop_risk`` -- and then
    only appends the crossing to a list that is interpolated into a log line
    labelled ``authority=diagnostic_only_no_direct_trade_authority``.  A test is
    a gate when what happens inside it can change the outcome: a raise, a
    mutation of the decision object, a return, or a write to a name that is read
    anywhere outside the log lines of this function.
    """
    log_node_ids: set[int] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            callee = node.func
            name = getattr(callee, "id", None) or getattr(callee, "attr", None)
            if name in {"log", "_log", "log_ai_usage", "print"}:
                for sub in ast.walk(node):
                    log_node_ids.add(id(sub))
    decision_attribute_names = {"decision", "dec", "final_decision", "out"}

    roots: list = []
    for node in ast.walk(function):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            roots.append(node)

    def body_effect(root) -> str:
        if isinstance(root, ast.IfExp):
            return "gates"
        statements = getattr(root, "body", []) or []
        if not statements:
            return "gates"
        written: set[str] = set()
        for statement in statements:
            for sub in ast.walk(statement):
                if isinstance(sub, (ast.Raise, ast.Return)):
                    return "gates"
                if isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if isinstance(target, ast.Attribute):
                            base_name = getattr(target.value, "id", None) or getattr(
                                target.value, "attr", ""
                            )
                            if base_name in decision_attribute_names:
                                return "gates"
                            written.add(target.attr)
                        elif isinstance(target, ast.Name):
                            written.add(target.id)
                        elif isinstance(target, ast.Subscript):
                            return "gates"
                elif isinstance(sub, ast.AugAssign) and isinstance(sub.target, ast.Name):
                    written.add(sub.target.id)
                elif (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in {"append", "extend", "add", "update", "setdefault"}
                    and isinstance(sub.func.value, ast.Name)
                ):
                    written.add(sub.func.value.id)
        if not written:
            return "log_only"
        # ``threshold_crossings.append(x)`` reads the list only to grow it: that
        # is a write in disguise, not an escape.  Anything else that reads what
        # the test wrote outside a log line is a real consequence.
        receiver_ids: set[int] = set()
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend", "add", "update", "setdefault"}
                and isinstance(node.func.value, ast.Name)
            ):
                receiver_ids.add(id(node.func.value))
        for node in ast.walk(function):
            if not isinstance(node, ast.Name) or node.id not in written:
                continue
            if id(node) in receiver_ids:
                continue
            if isinstance(node.ctx, ast.Load) and id(node) not in log_node_ids:
                return "gates"
        return "log_only"

    effects: dict[int, str] = {}
    # Every If/While/IfExp test (or is dead code), so attributing the effect to
    # those three node kinds covers all test positions without double counting.
    for root in roots:
        effect = body_effect(root)
        for sub in ast.walk(root.test):
            previous = effects.get(id(sub))
            if previous != "gates":
                effects[id(sub)] = effect
    # One hop of taint: ``score = float(row["llm_quality_score"])`` followed by
    # ``if score < floor`` is a real gate, and without this hop such a field reads
    # like plain persistence.  Only a single assignment hop inside the same
    # function is followed, and only for a small scalar copy -- propagating
    # through ``decision = Decision(...every field...)`` would mark every field in
    # the constructor as gated the moment any attribute of ``decision`` is tested,
    # which is exactly the over-claim this table must avoid.
    tainted: dict[str, list] = {}
    for node in ast.walk(function):
        targets: list[ast.expr] = []
        value = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            targets = [node.target]
            value = getattr(node, "value", None)
        if value is None:
            continue
        if sum(1 for _ in ast.walk(value)) > 8:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id in decision_attribute_names:
                # Never taint through a decision handle itself; that is the
                # whole object, not one field.
                continue
            bucket = tainted.setdefault(target.id, [])
            for sub in ast.walk(value):
                if isinstance(sub, (ast.Attribute, ast.Subscript, ast.Call)):
                    bucket.append(id(sub))
    for name, read_ids in tainted.items():
        for root in roots:
            effect = effects.get(id(root.test))
            if effect is None:
                continue
            for sub in ast.walk(root.test):
                if isinstance(sub, ast.Name) and sub.id == name and isinstance(sub.ctx, ast.Load):
                    for read_id in read_ids:
                        previous = effects.get(read_id)
                        if previous != "gates":
                            effects[read_id] = effect
    return effects


def _read_site_field(node, names: list) -> tuple[str, str] | None:
    """Return ``(field, kind)`` when ``node`` touches one of the schema fields.

    ``kind`` separates the reliable matches from the ambiguous ones:

    * ``read`` -- a string-key access (``row["summary"]``, ``row.get("summary")``,
      ``getattr(row, "summary")``).  The key text is exact.
    * ``read_attribute`` -- ``obj.summary``.  Bare identifiers such as ``code``,
      ``enabled`` or ``summary`` collide with unrelated attributes, so these are
      only accepted when the receiver's own name says it is a model/payload
      object; otherwise the "consumer" is a different thing wearing the same
      word, which is exactly how every field previously looked decision-altering.
    * ``inject`` -- a constructor keyword argument: Python writing the value into
      the authoritative envelope, i.e. persistence, not consumption.
    """
    if isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Store) and node.attr in names:
        base = node.value
        base_name = getattr(base, "id", None) or getattr(base, "attr", None) or ""
        if not _MODELISH_NAME_RE.search(str(base_name)):
            return None
        return node.attr, "read_attribute"
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        if isinstance(node.slice.value, str) and node.slice.value in names and not isinstance(node.ctx, ast.Store):
            return node.slice.value, "read"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "pop"}:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            if node.args[0].value in names:
                return node.args[0].value, "read"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
        if isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str) and node.args[1].value in names:
            return node.args[1].value, "read"
    if isinstance(node, ast.keyword) and node.arg in names:
        return node.arg, "inject"
    return None


def _dedupe(sites: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for site in sites:
        key = (
            site.get("file"),
            site.get("function"),
            site.get("kind"),
            bool(site.get("guard")),
            bool(site.get("log_only_guard")),
            bool(site.get("decision_file")),
            bool(site.get("telemetry_file")),
            bool(site.get("conditional_line")),
        )
        seen.setdefault(key, site)
    return list(seen.values())


def output_attribution(
    bus_reader: BusReader,
    request_ids: list[str],
    calibration: dict,
    usage: dict,
    consumers: dict,
    captures: list[dict],
) -> dict:
    """Reasoning vs visible output and visible tokens per stored output field."""
    muse_field_factor: dict[str, float] = {}
    for role in ROLES:
        rows = [
            row for row in calibration["per_request"]
            if row["role"] == role and row["ledger_model"] == MUSE_MODEL
        ]
        muse_field_factor[role] = percentile([row["global_factor"] for row in rows], 0.5) or 0.35
    per_role: dict[str, dict] = {}
    for role in ROLES:
        def block(rows: list[dict]) -> dict:
            totals, reasonings, visibles = [], [], []
            for row in rows:
                total, reasoning = row.get("output_tokens"), row.get("reasoning_output_tokens")
                if isinstance(total, int):
                    totals.append(total)
                if isinstance(reasoning, int):
                    reasonings.append(reasoning)
                if isinstance(total, int) and isinstance(reasoning, int):
                    visibles.append(max(0, total - reasoning))
            return {
                "n": len(rows),
                "output_tokens": stats_block(totals),
                "reasoning_output_tokens": stats_block(reasonings),
                "visible_output_tokens": stats_block(visibles),
                "reasoning_share_pct": stats_block([
                    reasoning / total * 100.0 for reasoning, total in zip(reasonings, totals) if total
                ]),
            }

        rows_by_role = [
            row for request_id in request_ids for row in [( (usage.get(request_id) or {}).get(role) )]
            if row
        ]
        per_role[role] = {
            "muse_rows": block([row for row in rows_by_role if row.get("model") == MUSE_MODEL]),
            "fallback_leg_rows": block([row for row in rows_by_role if row.get("model") != MUSE_MODEL]),
            "all_rows": block(rows_by_role),
            "note": (
                "reported separately by ledger model: a Muse row and a fallback-leg row for the "
                "same request id are different tokenizers and different generation settings"
            ),
        }
    per_field: dict[str, dict] = {}
    for request_id in request_ids:
        doc = bus_reader.response_debug(request_id)
        if not isinstance(doc, dict):
            continue
        response = doc.get("response")
        if not isinstance(response, dict):
            continue
        usage_row = usage.get(request_id) or {}
        for role, key in (
            ("analyst", "analyst_output"), ("critic", "critic_output"), ("adjudicator", "adjudicator_output")
        ):
            stored = response.get(key)
            if not isinstance(stored, dict):
                continue
            factor = muse_field_factor.get(role) or 0.35
            row = usage_row.get(role) or {}
            visible = None
            if isinstance(row.get("output_tokens"), int) and isinstance(row.get("reasoning_output_tokens"), int):
                visible = max(0, row["output_tokens"] - row["reasoning_output_tokens"])
            for name, value in stored.items():
                text = canon(value)
                units = raw_units(text)
                record = per_field.setdefault(
                    f"{role}.{name}", {"tokens": [], "units": [], "examples": [], "visible_ledger": []}
                )
                record["tokens"].append(units * factor)
                record["units"].append(units)
                if visible:
                    record["visible_ledger"].append(visible)
                if len(record["examples"]) < 2:
                    record["examples"].append({"request_id": request_id, "value": text[:160]})
    field_rows = []
    categories = {row["field"]: row["category"] for row in consumers["fields"]}
    for name, record in sorted(per_field.items()):
        role, _, field = name.partition(".")
        block = stats_block(record["tokens"])
        field_rows.append(
            {
                "role": role,
                "output_field": field,
                "n_samples": block["n"],
                "visible_tokens_avg": block["avg"],
                "visible_tokens_p50": block["p50"],
                "visible_tokens_p95": block["p95"],
                "consumer_category": categories.get(field, "not_in_output_schema"),
                "examples": record["examples"],
            }
        )
    field_rows.sort(key=lambda row: -(row["visible_tokens_p50"] or 0))
    echoed = _echoed_inputs(request_ids, captures)
    correlation = _reasoning_correlation(request_ids, usage)
    configured_reserve = next(
        (
            capture.get("capture_config", {}).get("reasoning_token_reserve")
            for capture in captures
            if capture.get("capture_config")
        ),
        None,
    )
    reserve = {}
    for role in ROLES:
        budgets = [
            row["wire_max_output_tokens"]
            for row in calibration["per_request"]
            if row["role"] == role and row["ledger_model"] == MUSE_MODEL and row["wire_max_output_tokens"]
        ]
        used = [
            float((usage.get(rid) or {}).get(role, {}).get("output_tokens"))
            for rid in request_ids
            if isinstance((usage.get(rid) or {}).get(role, {}).get("output_tokens"), int)
        ]
        reasoning = [
            float((usage.get(rid) or {}).get(role, {}).get("reasoning_output_tokens"))
            for rid in request_ids
            if isinstance((usage.get(rid) or {}).get(role, {}).get("reasoning_output_tokens"), int)
        ]
        median_budget = percentile(budgets, 0.5)
        median_used = percentile(used, 0.5)
        reserve[role] = {
            "wire_max_output_tokens": stats_block(budgets),
            "provider_output_tokens": stats_block(used),
            "provider_reasoning_tokens": stats_block(reasoning),
            "median_budget_minus_median_output": (
                round(median_budget - median_used, 1)
                if median_budget is not None and median_used is not None
                else None
            ),
            "configured_reasoning_token_reserve": configured_reserve,
            "reserve_is_ceiling_not_spend": (
                "max_output_tokens is a ceiling added on top of the schema budget "
                "(ai_provider.OpenCodeResponsesProvider._wire_responses_kwargs); an "
                "answer that reasons less is billed less"
            ),
        }
    return {
        "per_role_from_ledger": per_role,
        "per_field_visible_tokens": field_rows,
        "echoed_inputs_and_unconsumed_prose": echoed,
        "visible_length_vs_reasoning": correlation,
        "output_budget_vs_used": reserve,
        "field_factor_note": "visible tokens per field = proxy units of the stored field x the median Muse global factor for that role",
    }


def _echo_index(envelope: dict, budget_entries: int = 1400, min_chars: int = 12) -> dict:
    """Map a wire value's canonical text to the input paths that already carry it.

    Used by the parent to prove (OUT-001) which model output fields are echoes of
    the request.  The threshold is deliberately 12 canonical characters: a short
    enum word (``"ABSTAIN"`` is 9 with quotes) is bounded vocabulary, and matching
    on it turns every distinct-but-equal fact (DUP-001 class b) into a false
    echo.  Values above the threshold include hashes, ids, prices and prose, where
    an identical serialization really is the same fact.
    """
    index: dict[str, list[str]] = {}
    for path, _key, value in walk_leaves(envelope):
        text = canon(value)
        if len(text) < min_chars:
            continue
        bucket = index.setdefault(text, [])
        if len(bucket) < 3:
            bucket.append(path)
        if len(index) >= budget_entries:
            break
    return index


def _echoed_inputs(request_ids: list[str], captures: list[dict]) -> dict:
    """Which model output values are already present verbatim in the request wire?"""
    by_id = {capture["request_id"]: capture for capture in captures}
    schema = schema_field_map()
    model_fields = sorted({name for group in schema.values() for name in group})
    counts: dict[str, dict] = {}
    documents = 0
    for request_id in request_ids:
        capture = by_id.get(request_id) or {}
        index = (capture.get("envelope") or {}).get("echo_index") or {}
        doc = BusReader(Path(os.environ.get("PO3_ATTRIBUTION_BUS", DEFAULT_BUS))).response_debug(request_id)
        if not isinstance(doc, dict):
            continue
        response = doc.get("response")
        if not isinstance(response, dict):
            continue
        documents += 1
        for role, key in (
            ("analyst", "analyst_output"),
            ("critic", "critic_output"),
            ("adjudicator", "adjudicator_output"),
        ):
            stored = response.get(key)
            if not isinstance(stored, dict):
                continue
            for name, value in stored.items():
                record = counts.setdefault(
                    f"{role}.{name}",
                    {
                        "role": role, "field": name, "n": 0, "echoed": 0,
                        "model_owned": name in model_fields, "examples": [],
                    },
                )
                record["n"] += 1
                text = canon(value)
                paths = index.get(text)
                if paths:
                    record["echoed"] += 1
                    if len(record["examples"]) < 2:
                        record["examples"].append(
                            {"request_id": request_id, "input_paths": paths, "value": text[:120]}
                        )
    echoed_rows = sorted(counts.values(), key=lambda row: (-row["echoed"], row["field"]))
    return {
        "method": (
            "an output field is an echo when its canonical serialization also appears as a "
            "wire input value at the listed path(s); only values >= 12 canonical characters "
            "are indexed so trivial scalars cannot match"
        ),
        "response_debug_documents": documents,
        "model_owned_output_fields": model_fields,
        "fields": echoed_rows,
        "echoed_field_count": sum(1 for row in echoed_rows if row["echoed"]),
        "never_echoed_model_owned_fields": [
            f"{row['role']}.{row['field']}"
            for row in echoed_rows
            if row["model_owned"] and not row["echoed"]
        ],
    }


def _reasoning_correlation(request_ids: list[str], usage: dict) -> dict:
    points = []
    for request_id in request_ids:
        for role in ROLES:
            row = (usage.get(request_id) or {}).get(role)
            if not row:
                continue
            visible = row.get("output_tokens")
            reasoning = row.get("reasoning_output_tokens")
            if isinstance(visible, int) and isinstance(reasoning, int):
                points.append((role, max(0, visible - reasoning), reasoning))
    def pearson(pairs):
        if len(pairs) < 3:
            return None
        xs = [pair[0] for pair in pairs]
        ys = [pair[1] for pair in pairs]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        var_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
        var_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
        return round(cov / (var_x * var_y), 4) if var_x and var_y else None
    return {
        "definition": "pearson(visible_output_tokens, reasoning_output_tokens) per role, sample rows",
        "by_role": {
            role: {"n": len(rows), "r": pearson([(v, r) for _role, v, r in rows])}
            for role, rows in (
                (role, [point for point in points if point[0] == role]) for role in ROLES
            )
        },
    }


# ---------------------------------------------------------------------------
# Parent: writers
# ---------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    keys = list(rows[0].keys())
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in keys})


def _cell(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_parent(args) -> int:
    started = time.time()
    print(f"[command] {' '.join(sys.argv)}")
    wp_dir = Path(args.wp_dir).resolve()
    wp_dir.mkdir(parents=True, exist_ok=True)
    captures_dir = wp_dir / "captures"
    captures_dir.mkdir(exist_ok=True)
    bus_reader = BusReader(Path(args.bus))
    manifest_path = wp_dir / "sample_manifest.json"

    if args.stage in ("select", "all") or not manifest_path.is_file():
        manifest = select_sample(bus_reader, args.sample_size, args.seed)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(
            f"[select] {manifest['sample_size']} requests "
            f"(>=30: {manifest['meets_minimum_30']}) strata={manifest['selected_strata_summary']}"
        )
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rows = manifest["rows"][: args.limit] if args.limit else manifest["rows"]
    if args.stage in ("capture", "all"):
        for index, row in enumerate(rows, 1):
            out = captures_dir / f"{row['request_id']}.json"
            if out.is_file() and not args.force:
                try:
                    existing = json.loads(out.read_text(encoding="utf-8"))
                except Exception:
                    existing = None
                if isinstance(existing, dict) and existing.get("measurement_version") == MEASUREMENT_VERSION:
                    continue
            command = [
                sys.executable, str(Path(__file__).resolve()), "--child",
                "--payload", row["archive_path"], "--request-id", row["request_id"],
                "--out", str(out), "--bus", str(bus_reader.bus),
            ]
            if not args.review:
                command.append("--no-review")
            if args.no_shadow:
                command.append("--no-shadow")
            child_started = time.time()
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=args.child_timeout)
                code, err = completed.returncode, (completed.stderr or "").strip().splitlines()[-3:]
            except subprocess.TimeoutExpired:
                code, err = 124, ["child timeout"]
            print(
                f"[capture {index}/{len(rows)}] {row['request_id']} rc={code} "
                f"{round(time.time() - child_started, 1)}s {' | '.join(err)}"
            )

    captures = []
    stale_captures: list[str] = []
    for path in sorted(captures_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            stale_captures.append(f"{path.name}:unreadable")
            continue
        if document.get("measurement_version") != MEASUREMENT_VERSION:
            stale_captures.append(
                f"{path.name}:measurement_version={document.get('measurement_version')!r}"
            )
            continue
        captures.append(document)
    if stale_captures:
        print(
            f"[captures] skipped {len(stale_captures)} stale capture file(s) written by a "
            f"different measurement schema ({MEASUREMENT_VERSION} required): "
            + "; ".join(stale_captures[:6]),
            file=sys.stderr,
        )
    captured = [capture for capture in captures if capture.get("status") == "captured"]
    captured_ids = [capture["request_id"] for capture in captured]
    by_id = {capture["request_id"]: capture for capture in captures}
    for entry in manifest["rows"]:
        capture = by_id.get(entry["request_id"])
        entry["roles_captured"] = (
            sorted(role for role in ROLES if isinstance(capture.get(role), dict)) if capture else []
        )
        entry["capture_status"] = capture.get("status") if capture else "not_run"
        entry["capture_errors"] = (capture or {}).get("errors", [])[:3]
        entry["review_status"] = (capture or {}).get("review_status")
        entry["review_roles_reached"] = (capture or {}).get("review_roles_reached")
        candidates = (capture or {}).get("candidates") or {}
        entry["n_candidates_archive"] = candidates.get("archive", entry.get("n_candidates"))
        entry["n_candidates_wire"] = candidates.get("wire")
        entry["cohort_sealed_to_live_budget"] = candidates.get("cohort_sealed")
        entry["wire_candidate_indexes"] = candidates.get("wire_candidate_indexes")
        entry["workload_mode"] = (capture or {}).get("workload_mode")
    manifest["captured_requests"] = len(captured_ids)
    manifest["measurement_version"] = MEASUREMENT_VERSION
    manifest["stale_captures_skipped"] = stale_captures
    role_rows = [
        (entry["request_id"], role)
        for entry in manifest["rows"]
        for role in (entry.get("roles_captured") or [])
    ]
    manifest["role_row_count"] = len(role_rows)
    manifest["role_row_summary"] = {
        role: sum(1 for _rid, row_role in role_rows if row_role == role) for role in ROLES
    }
    wire_strata: dict[str, int] = {}
    for entry in manifest["rows"]:
        key = f"{candidate_stratum(int(entry.get('n_candidates_wire') or 0))}/{entry.get('symbol_class')}"
        wire_strata[key] = wire_strata.get(key, 0) + 1
    manifest["selected_strata_summary_by_wire_candidates"] = wire_strata
    manifest["cohort_sealed_requests"] = sum(
        1 for entry in manifest["rows"] if entry.get("cohort_sealed_to_live_budget")
    )
    manifest["analyst_wires"] = sum(1 for capture in captured_ids)
    manifest["critic_wires"] = sum(1 for capture in captured if isinstance(capture.get("critic"), dict))
    manifest["adjudicator_wires"] = sum(1 for capture in captured if isinstance(capture.get("adjudicator"), dict))
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    usage, _muse = bus_reader.usage_rows(set(captured_ids))
    calibration = calibrate(captured, usage)
    (wp_dir / "calibration.json").write_text(json.dumps(calibration, indent=2, ensure_ascii=False), encoding="utf-8")

    attribution = attribute_inputs(captured, calibration)
    closure: dict[str, dict] = {}
    for role in ROLES:
        rows_role = [row for row in attribution if row["role"] == role]
        sections = sum(
            row["pct_of_input_p50"] or 0 for row in rows_role if row["kind"] == "envelope_section"
        )
        catalog = sum(
            row["pct_of_input_p50"] or 0
            for row in rows_role
            if row["subtree"]
            in {
                "evidence_catalog.items.id",
                "evidence_catalog.items.p",
                "evidence_catalog.items.v",
                "evidence_catalog.items.c",
                "evidence_catalog.items.keys_punctuation",
            }
        )
        prompt = sum(
            row["pct_of_input_p50"] or 0
            for row in rows_role
            if row["subtree"] in {"instructions", "text.format.schema"}
        )
        closure[role] = {
            "envelope_sections_pct_of_input": round(sections, 2),
            "catalog_components_pct_of_input": round(catalog, 2),
            "prompt_and_schema_pct_of_input": round(prompt, 2),
            "prompt_plus_body_pct_of_input": round(sections + prompt, 2),
            "note": (
                "envelope_sections + prompt_and_schema should approach 100% of the provider input; "
                "the residual is the JSON wrapper the SDK adds around the three parts and the "
                "serialization difference between the reconstructed body and the counted prompt"
            ),
        }
    (wp_dir / "input_attribution.json").write_text(
        json.dumps(
            {
                "unit": "calibrated proxy tokens (factors and error in calibration.json)",
                "definition": "static = byte-identical serialization across every captured request of that role; duplicated = the same canonical fact also exists at that canonical path outside the catalog (DUP-001 class a)",
                "partition_closure": closure,
                "n_requests": len(captured),
                "subtrees": attribution,
                "per_candidate_largest_subtrees": {
                    capture["request_id"]: (capture.get("envelope") or {}).get("per_candidate_largest_subtree_names")
                    for capture in captured
                    if capture.get("envelope")
                },
                "catalog_value_repetition": [
                    {
                        "request_id": capture["request_id"],
                        "items": (capture.get("envelope") or {}).get("n_catalog_items"),
                        "unique_values": (capture.get("envelope") or {}).get("catalog_unique_values"),
                        "repeat_groups": (capture.get("envelope") or {}).get("catalog_value_repeat_groups"),
                    }
                    for capture in captured
                    if capture.get("envelope")
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_csv(wp_dir / "input_attribution.csv", attribution)

    dup_rows = [
        {"request_id": capture["request_id"], **(capture.get("duplication") or {})}
        for capture in captured
        if capture.get("duplication")
    ]
    totals: dict[str, int] = {}
    total_units: dict[str, int] = {}
    total_value_units: dict[str, int] = {}
    for row in dup_rows:
        for key, value in (row.get("counts") or {}).items():
            totals[key] = totals.get(key, 0) + value
        for key, value in (row.get("proxy_units") or {}).items():
            total_units[key] = total_units.get(key, 0) + value
        for key, value in (row.get("proxy_value_units") or {}).items():
            total_value_units[key] = total_value_units.get(key, 0) + value
    analyst_factors = [row["global_factor"] for row in calibration["per_request"] if row["role"] == "analyst"]
    median_factor = percentile(analyst_factors, 0.5) or 0.0
    duplication = {
        "classes": {
            "a_same_fact_serialized_twice": "catalog row p resolves to an identical value inside the non-catalog envelope (path identity)",
            "b_distinct_equal_value": "different canonical paths whose serialized value happens to be equal",
            "c_alias": "the row points at a value the compaction pass already renamed (per_candidate_reference / candidate_targets_reference / *.value)",
            "d_catalog_material": "the catalog is the only carrier of that fact on the wire",
            "e_contract_required": "overlap flag: the path name is named by the role prompt and must survive any compaction",
        },
        "per_request": [
            {key: value for key, value in row.items() if key != "duplicated_catalog_paths"} for row in dup_rows
        ],
        "totals_row_counts": totals,
        "total_proxy_units": total_units,
        "total_calibrated_tokens": {key: round(value * median_factor, 1) for key, value in total_units.items()},
        "total_value_only_proxy_units": total_value_units,
        "total_value_only_calibrated_tokens": {
            key: round(value * median_factor, 1) for key, value in total_value_units.items()
        },
        "interpretation": (
            "total_calibrated_tokens is the whole catalog row (id+p+v+c+punctuation) per class; "
            "total_value_only_calibrated_tokens is the ``v`` payload only, i.e. what a "
            "pointer/dereference rewrite can remove while keeping every citable id and path"
        ),
        "median_analyst_global_factor": round(median_factor, 5),
        "repeated_key_names": {
            "catalog_key_names_units": stats_block([
                (capture.get("envelope") or {}).get("sections", {}).get("evidence_catalog.items.key_names_only", {}).get("units")
                for capture in captured
            ]),
            "all_input_key_names_units": stats_block([
                (capture.get("envelope") or {}).get("key_name_total_units") for capture in captured
            ]),
            "candidate_array_key_names_units": stats_block([
                (capture.get("envelope") or {}).get("candidate_array_key_name_units") for capture in captured
            ]),
            "top_repeated_keys": _top_keys(captured),
        },
    }
    (wp_dir / "duplication.json").write_text(json.dumps(duplication, indent=2, ensure_ascii=False), encoding="utf-8")

    aggregate: dict[str, dict] = {}
    for capture in captured:
        for field in (capture.get("precision") or {}).get("fields") or []:
            key = f"{field['rule']}|{field['field']}"
            entry = aggregate.get(key)
            if entry is None:
                entry = aggregate[key] = {
                    "rule": field["rule"],
                    "field": field["field"],
                    "example_path": field["example_path"],
                    "example": field["example"],
                    "source_precision": field["source_precision"],
                    "proven_safe_canonical_form": field["proven_safe_canonical_form"],
                    "provable": field["provable"],
                    "hypothetical_shortest_form": field.get("hypothetical_shortest_form"),
                    "note": field["note"],
                    "occurrences": 0,
                    "units_now": 0,
                    "units_canonical": 0,
                    "units_hypothetical": 0,
                    "units_saved": 0,
                    "units_saved_hypothetical": 0,
                    "requests": set(),
                }
            for numeric in (
                "occurrences", "units_now", "units_canonical", "units_hypothetical",
                "units_saved", "units_saved_hypothetical",
            ):
                entry[numeric] += field.get(numeric, 0)
            entry["requests"].add(capture["request_id"])
    precision_rows = []
    for entry in sorted(aggregate.values(), key=lambda item: -item["units_saved_hypothetical"]):
        entry = dict(entry)
        entry["n_requests"] = len(entry.pop("requests"))
        precision_rows.append(entry)
    (wp_dir / "precision.json").write_text(
        json.dumps(
            {
                "unit": "proxy units; multiply by the per-request global_factor in calibration.json",
                "provable": "a canonical form that round-trips to the same value (float) or that "
                            "the field's authoritative source declares; otherwise 'not provable'",
                "hypothetical": "what a shorter form WOULD save, reported so the token effect is "
                                "quantified even where shortening is not provable-safe",
                "per_request": [
                    {key: value for key, value in (capture.get("precision") or {}).items() if key != "fields"}
                    | {"request_id": capture["request_id"], "top_fields": (capture.get("precision") or {}).get("fields", [])[:10]}
                    for capture in captured
                    if capture.get("precision")
                ],
                "aggregate_fields": precision_rows,
                "total_units_saved_if_applied": sum(entry["units_saved"] for entry in precision_rows),
                "total_units_saved_provable_only": sum(entry["units_saved"] for entry in precision_rows if entry["provable"]),
                "total_units_saved_hypothetically": sum(entry["units_saved_hypothetical"] for entry in precision_rows),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8"
    )

    layout = cache_layout(captured, calibration)
    (wp_dir / "cache_layout.json").write_text(
        json.dumps(layout, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    diagnostic_fields: set[str] = set()
    for capture in captured:
        diagnostic_fields.update(
            (capture.get("analyst") or {}).get("declared_diagnostic_fields") or []
        )
    consumers = build_consumer_table(diagnostic_fields)
    (wp_dir / "consumer_table.json").write_text(json.dumps(consumers, indent=2, ensure_ascii=False), encoding="utf-8")
    outputs = output_attribution(bus_reader, captured_ids, calibration, usage, consumers, captured)
    outputs["consumer_summary"] = consumers["summary"]
    outputs["sample_request_ids"] = captured_ids
    (wp_dir / "output_attribution.json").write_text(json.dumps(outputs, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(wp_dir / "output_attribution.csv", outputs["per_field_visible_tokens"])

    print_summary(manifest, calibration, attribution, duplication, precision_rows, layout, consumers, outputs)
    print(f"[done] {len(captured)}/{len(rows)} payloads captured in {round(time.time() - started, 1)}s")
    return 0


def print_summary(
    manifest: dict,
    calibration: dict,
    attribution: list[dict],
    duplication: dict,
    precision_rows: list[dict],
    layout: dict,
    consumers: dict,
    outputs: dict,
) -> None:
    """Human-readable digest of the raw outputs (also written to tool_stdout.txt)."""
    print("\n================ WP2 SUMMARY ================")
    print(
        f"sample: {manifest['sample_size']} archived requests "
        f"(>=30: {manifest['meets_minimum_30']}), role rows: {manifest.get('role_row_count')} "
        f"{manifest.get('role_row_summary')}, live-cohort sealed: {manifest.get('cohort_sealed_requests')}"
    )
    print(f"strata: {manifest.get('selected_strata_summary')}")
    factor = calibration["per_request_factor_muse_only"]
    print(
        f"proxy: {calibration['proxy']['environment_probe']['proxy_used']} "
        f"(tiktoken: {calibration['proxy']['environment_probe']['tiktoken']})"
    )
    print(
        "per-request Muse factor: min={min} median={median} max={max} n={n}".format(**factor)
    )
    print(
        "static-prefix factor from anchor only: min={min} median={median} max={max} n={n}".format(
            **calibration["static_prefix_factor_from_anchor_only"]
        )
    )
    print(f"uncalibrated proxy error pct: {calibration['uncalibrated_proxy_error_pct']}")
    print(f"calibrated (leave-one-out) error pct: {calibration['calibrated_error_pct']}")
    print(
        "escalation >15%: uncalibrated="
        f"{calibration['any_uncalibrated_error_gt_15pct']} calibrated="
        f"{calibration['any_calibrated_holdout_error_gt_15pct']} two_regime="
        f"{calibration['any_two_regime_calibrated_error_gt_15pct']}"
    )
    anchors = calibration["static_prefix_anchor_checks"]
    print(
        f"static prefix anchors checked: {anchors['n_checked']} "
        f"(6,513 analyst rows={anchors['anchor_6513_analyst_rows']}, "
        f"1,265 critic rows={anchors['anchor_1265_critic_rows']}); "
        f"global-factor error on the anchor: {anchors['global_factor_error_pct']}"
    )
    for hypothesis in calibration["static_prefix_hypotheses"]:
        print(
            "  hypothesis: "
            f"{hypothesis['hypothesis']} -> roles={hypothesis['roles_observed']} "
            f"unit_spread={hypothesis['cross_role_spread_ratio_proxy_units']} "
            f"char_spread={hypothesis['cross_role_spread_ratio_characters']}"
        )
    for role in ROLES:
        top = [row for row in attribution if row["role"] == role][:10]
        print(f"top-10 input subtrees by median tokens [{role}]:")
        for row in top:
            print(
                "   {:<58} p50={:>9} p95={:>9} pct_input={:>6} static={} dup={}".format(
                    row["subtree"][:58],
                    row["tokens_p50"],
                    row["tokens_p95"],
                    row["pct_of_input_p50"],
                    row["static"],
                    row["duplicated"],
                )
            )
    print(f"duplication totals (catalog rows): {duplication['totals_row_counts']}")
    print(f"duplication calibrated tokens: {duplication['total_calibrated_tokens']}")
    print(f"precision: provable units={precision_total(precision_rows, 'units_saved')} "
          f"hypothetical units={precision_total(precision_rows, 'units_saved_hypothetical')} "
          f"across {len(precision_rows)} field/rule pairs")
    for role, block in layout["roles"].items():
        interleave = block["sort_keys_interleave"]
        print(
            f"cache[{role}] n={block['n_requests']} static_proxy_units="
            f"{block['proxy_units']['static_prefix_instructions_plus_schema']['p50']} "
            f"first_volatile_byte_body={block['first_volatile_byte_in_body']['p50']} "
            f"first_volatile_byte_instructions={block['first_volatile_byte_in_instructions']['p50']} "
            f"sort_keys_interleaves={interleave.get('sort_keys_interleaves_static_and_dynamic')} "
            f"runs={interleave.get('alternating_runs')}"
        )
    print(f"output roles: {json.dumps(outputs['per_role_from_ledger'])[:400]}")
    print("top-10 output fields by visible tokens:")
    for row in outputs["per_field_visible_tokens"][:10]:
        print(
            "   {:<48} p50={:>8} p95={:>8} consumer={}".format(
                f"{row['role']}.{row['output_field']}"[:48],
                row["visible_tokens_p50"],
                row["visible_tokens_p95"],
                row["consumer_category"],
            )
        )
    print(f"consumer categories: {consumers['summary']}")


def precision_total(rows: list[dict], key: str) -> int:
    return sum(row.get(key, 0) for row in rows)


def _top_keys(captures: list[dict]) -> list[dict]:
    totals: dict[str, int] = {}
    for capture in captures:
        for block in (capture.get("envelope") or {}).get("key_name_top") or []:
            totals[block["key"]] = totals.get(block["key"], 0) + block["units"]
    return [{"key": key, "units": units} for key, units in sorted(totals.items(), key=lambda item: -item[1])[:25]]


def run_child(args) -> int:
    if not args.payload or not args.request_id or not args.out:
        print("--child requires --payload, --request-id and --out", file=sys.stderr)
        return 64
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    return child_capture(
        Path(args.payload), args.request_id, Path(args.out), args.review, not args.no_shadow
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline Muse token attribution (stage A)")
    parser.add_argument("--wp-dir", default=".mt5-orchestrator/runs/20260913T155650Z-5fa598c0/wp2")
    parser.add_argument("--bus", default=os.environ.get("PO3_ATTRIBUTION_BUS", DEFAULT_BUS))
    parser.add_argument("--sample-size", type=int, default=36)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--stage", choices=("select", "capture", "analyze", "all"), default="all")
    parser.add_argument("--force", action="store_true", help="recapture existing capture files")
    parser.add_argument("--review", action=argparse.BooleanOptionalAction, default=True, help="capture critic/adjudicator wires")
    parser.add_argument("--no-shadow", action="store_true", help="omit shadow historical evidence from the rebuilt wire")
    parser.add_argument("--child-timeout", type=float, default=900.0)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--payload")
    parser.add_argument("--request-id")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if args.child:
        os.environ["PO3_ATTRIBUTION_BUS"] = args.bus
        return run_child(args)
    return run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
