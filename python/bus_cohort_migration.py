"""Cohort classification, archival, and isolation for the AI file bus.

The bus accumulated 50,867 files spanning several years and several
incompatible contract generations -- 2023-era ``XAUUSD+`` responses whose whole
schema was ``{id, allow, score, chosen_index}`` sit in the same directory as
2026 canonical-frozen-request responses.  Old artifacts must never be recovered
as current work, and a repeated historical test must not collide with a previous
run just because simulated timestamps repeat.

This module classifies every artifact by its true contract cohort and moves
incompatible ones into a dated archive.  It never deletes anything, and it never
touches trade ledgers, outcome ledgers, or empirical history: those are the only
record of what actually happened and are irreplaceable.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping


BUS_COHORT_MIGRATION_VERSION = "20260731_bus_cohort_isolation_v1"

# Never archived, never rewritten: irreplaceable empirical history.
PROTECTED_RELATIVE_PATHS: tuple[str, ...] = (
    "logs/completed_ai_trades.jsonl",
    "logs/trade_results",
    "logs/outcomes",
    "logs/ledger",
    "logs/archive",
)

# Directories that hold transport artifacts eligible for cohort archival.
MIGRATABLE_DIRS: tuple[str, ...] = (
    "requests",
    "processing",
    "responses",
    "completed",
    "quarantined",
    "rejected",
    "timed_out",
    "stale",
    "response_debug",
    "request_ledger",
    "logs/tester_ai_cache",
)

COHORT_UNKNOWN = "unknown"
COHORT_LEGACY_PRE_CONTRACT = "legacy_pre_contract"

CLASSIFY_COMPATIBLE = "compatible"
CLASSIFY_INCOMPATIBLE = "incompatible"
CLASSIFY_UNPARSEABLE = "unparseable"
CLASSIFY_PROTECTED = "protected"


def read_json_any_encoding(path: Path) -> Any:
    """Read JSON written by either Python (UTF-8) or MQL (UTF-16).

    The bus genuinely contains both.  A reader that assumes one encoding
    silently classifies half the bus as corrupt, which is how the previous
    inventory under-reported the contamination.
    """

    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return json.loads(raw.decode("utf-16"))
    for encoding in ("utf-8-sig", "utf-8", "utf-16-le", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("unreadable_json")


@dataclass(frozen=True)
class CohortIdentity:
    """The contract generation an artifact belongs to."""

    engine_version: str
    decision_schema_version: str
    identity_schema_version: str
    prompt_contract_version: str
    contract_manifest_hash: str
    session_id: str

    @property
    def cohort_hash(self) -> str:
        material = "|".join(
            (
                self.engine_version,
                self.decision_schema_version,
                self.identity_schema_version,
                self.prompt_contract_version,
                self.contract_manifest_hash,
            )
        )
        return sha256(material.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, str]:
        return {
            "engine_version": self.engine_version,
            "decision_schema_version": self.decision_schema_version,
            "identity_schema_version": self.identity_schema_version,
            "prompt_contract_version": self.prompt_contract_version,
            "contract_manifest_hash": self.contract_manifest_hash,
            "session_id": self.session_id,
            "cohort_hash": self.cohort_hash,
        }


@dataclass(frozen=True)
class FileClassification:
    path: Path
    relative: str
    kind: str
    classification: str
    reason: str
    identity: CohortIdentity
    request_id: str
    request_identity_hash: str
    terminal_state: str
    size: int


def _text(value: Any) -> str:
    return str(value) if value not in (None, "") else ""


def cohort_identity_of(payload: Mapping[str, Any]) -> CohortIdentity:
    manifest = payload.get("contract_manifest")
    manifest = manifest if isinstance(manifest, Mapping) else {}
    runtime_inputs = payload.get("runtime_inputs")
    runtime_inputs = runtime_inputs if isinstance(runtime_inputs, Mapping) else {}
    return CohortIdentity(
        engine_version=_text(payload.get("engine_version") or manifest.get("engine_version"))
        or COHORT_LEGACY_PRE_CONTRACT,
        decision_schema_version=_text(
            payload.get("decision_schema_version")
            or manifest.get("decision_schema_version")
            or runtime_inputs.get("ai_decision_schema_version")
        )
        or COHORT_LEGACY_PRE_CONTRACT,
        identity_schema_version=_text(
            payload.get("identity_schema_version")
            or payload.get("request_identity_version")
            or manifest.get("request_identity_version")
        )
        or COHORT_LEGACY_PRE_CONTRACT,
        prompt_contract_version=_text(
            payload.get("prompt_contract_version")
            or manifest.get("prompt_contract_version")
            or runtime_inputs.get("ai_prompt_contract_version")
        )
        or COHORT_LEGACY_PRE_CONTRACT,
        contract_manifest_hash=_text(payload.get("contract_manifest_hash"))
        or COHORT_LEGACY_PRE_CONTRACT,
        session_id=_text(payload.get("session_id")),
    )


def _kind_for(relative: str) -> str:
    head = relative.replace("\\", "/").split("/", 1)[0]
    if head in {"logs"}:
        return "cache" if "tester_ai_cache" in relative.replace("\\", "/") else "logs"
    return head


def _is_protected(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    return any(
        normalized == protected or normalized.startswith(protected + "/")
        for protected in PROTECTED_RELATIVE_PATHS
    )


def classify_bus_file(
    path: Path,
    bus: Path,
    current: CohortIdentity,
) -> FileClassification:
    """Classify one artifact against the currently active contract cohort."""

    relative = str(path.relative_to(bus))
    kind = _kind_for(relative)
    if _is_protected(relative):
        return FileClassification(
            path=path,
            relative=relative,
            kind=kind,
            classification=CLASSIFY_PROTECTED,
            reason="empirical_history_protected",
            identity=current,
            request_id="",
            request_identity_hash="",
            terminal_state="",
            size=path.stat().st_size,
        )

    try:
        payload = read_json_any_encoding(path)
    except Exception:
        return FileClassification(
            path=path,
            relative=relative,
            kind=kind,
            classification=CLASSIFY_UNPARSEABLE,
            reason="unreadable_json",
            identity=CohortIdentity(
                COHORT_UNKNOWN, COHORT_UNKNOWN, COHORT_UNKNOWN, COHORT_UNKNOWN, COHORT_UNKNOWN, ""
            ),
            request_id="",
            request_identity_hash="",
            terminal_state="",
            size=path.stat().st_size,
        )

    if not isinstance(payload, Mapping):
        return FileClassification(
            path=path,
            relative=relative,
            kind=kind,
            classification=CLASSIFY_UNPARSEABLE,
            reason="json_root_not_object",
            identity=CohortIdentity(
                COHORT_UNKNOWN, COHORT_UNKNOWN, COHORT_UNKNOWN, COHORT_UNKNOWN, COHORT_UNKNOWN, ""
            ),
            request_id="",
            request_identity_hash="",
            terminal_state="",
            size=path.stat().st_size,
        )

    identity = cohort_identity_of(payload)
    mismatches = [
        name
        for name, observed, expected in (
            ("decision_schema_version", identity.decision_schema_version, current.decision_schema_version),
            ("identity_schema_version", identity.identity_schema_version, current.identity_schema_version),
            ("prompt_contract_version", identity.prompt_contract_version, current.prompt_contract_version),
            ("contract_manifest_hash", identity.contract_manifest_hash, current.contract_manifest_hash),
        )
        if observed != expected
    ]
    return FileClassification(
        path=path,
        relative=relative,
        kind=kind,
        classification=CLASSIFY_COMPATIBLE if not mismatches else CLASSIFY_INCOMPATIBLE,
        reason="current_cohort" if not mismatches else "cohort_mismatch:" + ",".join(mismatches),
        identity=identity,
        request_id=_text(payload.get("id")),
        request_identity_hash=_text(payload.get("request_identity_hash")),
        terminal_state=_text(payload.get("decision_quality_tier")),
        size=path.stat().st_size,
    )


def iter_bus_files(bus: Path) -> Iterable[Path]:
    for relative in MIGRATABLE_DIRS:
        directory = bus / relative
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and "archive" not in path.parts:
                yield path


@dataclass
class MigrationSummary:
    bus: str
    dry_run: bool
    archive_root: str
    current_cohort: dict[str, str]
    total: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    by_cohort: dict[str, dict[str, Any]] = field(default_factory=dict)
    moved: int = 0
    protected: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "migration_version": BUS_COHORT_MIGRATION_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "bus": self.bus,
            "dry_run": self.dry_run,
            "archive_root": self.archive_root,
            "current_cohort": self.current_cohort,
            "total_files": self.total,
            "counts": dict(sorted(self.counts.items())),
            "by_cohort": self.by_cohort,
            "moved": self.moved,
            "protected": self.protected,
            "errors": self.errors[:50],
        }


def migrate_bus_cohorts(
    bus: Path,
    current: CohortIdentity,
    *,
    dry_run: bool = True,
    archive_date: str | None = None,
) -> MigrationSummary:
    """Archive every artifact that does not belong to the active cohort.

    Nothing is deleted.  Incompatible artifacts move to
    ``archive/<date>/<cohort_hash>/<kind>/`` so a later investigation can still
    read them, while the live directories contain only current-cohort work.
    """

    stamp = archive_date or datetime.now(timezone.utc).strftime("%Y%m%d")
    archive_root = bus / "archive" / stamp
    summary = MigrationSummary(
        bus=str(bus),
        dry_run=dry_run,
        archive_root=str(archive_root),
        current_cohort=current.as_dict(),
    )

    for path in iter_bus_files(bus):
        try:
            result = classify_bus_file(path, bus, current)
        except Exception as exc:  # a single bad file must not stop migration
            summary.errors.append(f"{path.name}:{type(exc).__name__}")
            continue

        summary.total += 1
        summary.counts[result.classification] = summary.counts.get(result.classification, 0) + 1

        if result.classification == CLASSIFY_PROTECTED:
            summary.protected += 1
            continue
        if result.classification == CLASSIFY_COMPATIBLE:
            continue

        cohort_key = (
            result.identity.cohort_hash
            if result.classification == CLASSIFY_INCOMPATIBLE
            else "unparseable"
        )
        bucket = summary.by_cohort.setdefault(
            cohort_key,
            {
                "cohort": result.identity.as_dict(),
                "files": 0,
                "bytes": 0,
                "kinds": {},
                "example_reason": result.reason,
            },
        )
        bucket["files"] += 1
        bucket["bytes"] += result.size
        bucket["kinds"][result.kind] = bucket["kinds"].get(result.kind, 0) + 1

        if dry_run:
            continue

        destination = archive_root / cohort_key / result.kind / path.name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination = destination.with_name(
                    f"{destination.stem}__{os.getpid()}_{summary.moved}{destination.suffix}"
                )
            shutil.move(str(path), str(destination))
            summary.moved += 1
        except Exception as exc:
            summary.errors.append(f"move:{path.name}:{type(exc).__name__}")

    return summary
