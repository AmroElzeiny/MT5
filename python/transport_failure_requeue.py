"""Recovery for requests a transport outage terminalised instead of answering.

Any ``ProviderCallError`` makes the gate write a terminal
``DEGRADED_NON_TRADING`` response and archive the request. For a live request
that is correct fail-closed behaviour: MT5 is waiting on a deadline and must be
told "no". For a RECORD_ONLY cohort nobody is waiting, and the same rule
destroys coverage -- the request is consumed forever and can never be replayed.

Evidence, cohort ``13176437`` (2026-09-05): 591 requests exported, 309 of the
first 373 answers were ``decision_source=provider_transport_error``. 77 came
from real ``Connection error.`` failures; the other 295 came from the circuit
breaker refusing every request claimed inside its 60s cooldown windows. The
breaker amplification is fixed in ``ai_provider._circuit_check``; this module
recovers the requests already burned by it.

What it deliberately will not do:

* it never fabricates a request -- only an archived original can be restored;
* it never touches a ``FULL_STRUCTURED`` answer, so no genuine AI judgement
  (approve, reject or abstain) is ever replayed away;
* it never reclaims a request another worker is actively running;
* it is a manual, bounded, audited command, never an automatic retry loop.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from request_lifecycle import ACTIVE_REQUEST_STATES, RequestIdempotencyLedger


TRANSPORT_REQUEUE_CONTRACT_VERSION = "20260905_transport_failure_requeue_v1"

# Only a transport failure is recoverable. Every other degraded source names a
# decision the gate actually made about this request's content -- a hard
# pre-gate block, an invalid structured response, a local pipeline defect -- and
# replaying those would re-run a verdict, not repair an outage.
RECOVERABLE_DECISION_SOURCES = frozenset({"provider_transport_error"})

# The tiers that carry a real model judgement. Never replayed.
PROTECTED_QUALITY_TIERS = frozenset(
    {"FULL_STRUCTURED", "CACHE_OF_FULL_STRUCTURED"}
)

# Where an archived original request may still be found, most likely first.
ARCHIVE_SEARCH_DIRS = ("rejected", "timed_out", "stale", "quarantined", "completed")


@dataclass(frozen=True)
class RequeueCandidate:
    """One response examined, and what may be done about it."""

    request_id: str
    response_path: Path
    decision_source: str
    decision_quality_tier: str
    request_archive_path: Path | None
    eligible: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "response_path": str(self.response_path),
            "decision_source": self.decision_source,
            "decision_quality_tier": self.decision_quality_tier,
            "request_archive_path": (
                str(self.request_archive_path) if self.request_archive_path else ""
            ),
            "eligible": self.eligible,
            "reason": self.reason,
        }


@dataclass
class RequeueSummary:
    dry_run: bool
    bus_root: str
    contract_version: str = TRANSPORT_REQUEUE_CONTRACT_VERSION
    responses_scanned: int = 0
    eligible: int = 0
    requeued: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    requeued_request_ids: list[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "transport_requeue_contract_version": self.contract_version,
            "dry_run": self.dry_run,
            "bus_root": self.bus_root,
            "responses_scanned": self.responses_scanned,
            "eligible": self.eligible,
            "requeued": self.requeued,
            "skipped": dict(sorted(self.skipped.items())),
            "errors": list(self.errors),
            "requeued_request_ids": sorted(self.requeued_request_ids),
        }


def _read_json(path: Path) -> Any:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
    raise last_error or ValueError("json_read_failed")


def _archive_candidates(bus: Path, request_id: str) -> Iterable[Path]:
    """Archived copies of one request, newest first.

    A claimed request carries a ``<session>__`` prefix, so the archived name is
    matched by suffix rather than by equality.
    """

    plain = f"{request_id}.json"
    suffix = f"__{plain}"
    found: list[Path] = []
    for name in ARCHIVE_SEARCH_DIRS:
        directory = bus / name
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if not path.is_file() or path.name.endswith(".meta.json"):
                continue
            if path.name == plain or path.name.endswith(suffix):
                found.append(path)
    found.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return found


def _is_request_payload(value: Any, request_id: str) -> bool:
    """A request, not a response that happened to be archived under this name."""

    if not isinstance(value, Mapping):
        return False
    if str(value.get("id") or "") != request_id:
        return False
    # A response carries a decision; a request carries the candidates to decide.
    if "decision_quality_tier" in value or "decision_source" in value:
        return False
    return "candidates" in value or "plan" in value


def _ledger_is_active(
    ledger: RequestIdempotencyLedger,
    request_id: str,
    *,
    stale_after_sec: float,
    now: float | None = None,
) -> bool:
    record = ledger.read(request_id)
    if str(record.get("state") or "") not in ACTIVE_REQUEST_STATES:
        return False
    heartbeat_at = float(record.get("heartbeat_at") or 0.0)
    reference = float(time.time() if now is None else now)
    return reference - heartbeat_at <= float(stale_after_sec)


def plan_transport_failure_requeue(
    bus: Path,
    *,
    ledger: RequestIdempotencyLedger,
    stale_after_sec: float,
    cohort: str = "",
    now: float | None = None,
) -> tuple[list[RequeueCandidate], RequeueSummary]:
    """Classify every written response without changing anything."""

    bus = Path(bus)
    summary = RequeueSummary(dry_run=True, bus_root=str(bus))
    candidates: list[RequeueCandidate] = []
    responses_dir = bus / "responses"
    requests_dir = bus / "requests"
    if not responses_dir.is_dir():
        return candidates, summary

    for response_path in sorted(responses_dir.iterdir()):
        if not response_path.is_file() or response_path.suffix.lower() != ".json":
            continue
        if response_path.name.endswith(".meta.json"):
            continue
        summary.responses_scanned += 1
        try:
            response = _read_json(response_path)
        except Exception as exc:
            summary.skip("response_unreadable")
            summary.errors.append(f"{response_path.name}:{exc}")
            continue
        if not isinstance(response, Mapping):
            summary.skip("response_not_an_object")
            continue

        request_id = str(response.get("id") or "")
        tier = str(response.get("decision_quality_tier") or "")
        source = str(response.get("decision_source") or "")

        def reject(reason: str) -> None:
            summary.skip(reason)
            candidates.append(
                RequeueCandidate(
                    request_id=request_id,
                    response_path=response_path,
                    decision_source=source,
                    decision_quality_tier=tier,
                    request_archive_path=None,
                    eligible=False,
                    reason=reason,
                )
            )

        if not request_id:
            reject("response_without_request_id")
            continue
        if cohort and f"_{cohort}_" not in request_id:
            reject("outside_requested_cohort")
            continue
        if tier in PROTECTED_QUALITY_TIERS:
            reject("real_ai_judgement_protected")
            continue
        if source not in RECOVERABLE_DECISION_SOURCES:
            reject("decision_source_not_recoverable")
            continue
        if (requests_dir / f"{request_id}.json").exists():
            reject("request_already_queued")
            continue
        if _ledger_is_active(
            ledger, request_id, stale_after_sec=stale_after_sec, now=now
        ):
            reject("request_active_in_another_worker")
            continue

        archive_path: Path | None = None
        for path in _archive_candidates(bus, request_id):
            try:
                payload = _read_json(path)
            except Exception:
                continue
            if _is_request_payload(payload, request_id):
                archive_path = path
                break
        if archive_path is None:
            reject("original_request_not_archived")
            continue

        summary.eligible += 1
        candidates.append(
            RequeueCandidate(
                request_id=request_id,
                response_path=response_path,
                decision_source=source,
                decision_quality_tier=tier,
                request_archive_path=archive_path,
                eligible=True,
                reason="transport_failure_recoverable",
            )
        )
    return candidates, summary


def _restore_request(archive_path: Path, target: Path) -> None:
    """Copy the archived original back into the queue atomically.

    Written to a temp name in the destination directory and renamed, so a
    partially copied file is never visible to a polling worker.
    """

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    shutil.copyfile(archive_path, temp)
    os.replace(temp, target)


def apply_transport_failure_requeue(
    bus: Path,
    *,
    ledger: RequestIdempotencyLedger,
    stale_after_sec: float,
    cohort: str = "",
    log: Any = None,
    now: float | None = None,
) -> RequeueSummary:
    """Restore every eligible request and retire the answer that replaced it.

    Order matters. The request file is written last: until it exists nothing can
    claim it, so a crash mid-way leaves the bus with one fewer stale artifact
    rather than a request that would be answered from the ledger it still has.
    """

    bus = Path(bus)
    candidates, summary = plan_transport_failure_requeue(
        bus,
        ledger=ledger,
        stale_after_sec=stale_after_sec,
        cohort=cohort,
        now=now,
    )
    summary.dry_run = False
    audit_path = bus / "logs" / "transport_failure_requeue.ndjson"
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    for candidate in candidates:
        if not candidate.eligible or candidate.request_archive_path is None:
            continue
        request_id = candidate.request_id
        try:
            # 1. Retire the degraded answer. While it exists the idempotency
            #    ledger reuses it as a completed response.
            candidate.response_path.unlink(missing_ok=True)
            # 2. Drop the ledger row. An ARCHIVED row stores validated_response
            #    and would hand the same degraded decision straight back.
            ledger_path = ledger._path(request_id)  # noqa: SLF001 - same package contract
            ledger_path.unlink(missing_ok=True)
            ledger_path.with_suffix(".lock").unlink(missing_ok=True)
            # 3. Re-queue the original request.
            _restore_request(
                candidate.request_archive_path,
                bus / "requests" / f"{request_id}.json",
            )
        except Exception as exc:
            summary.errors.append(f"{request_id}:{type(exc).__name__}:{exc}")
            summary.skip("requeue_failed")
            continue

        summary.requeued += 1
        summary.requeued_request_ids.append(request_id)
        record = {
            "transport_requeue_contract_version": TRANSPORT_REQUEUE_CONTRACT_VERSION,
            "requeued_at": time.time(),
            "request_id": request_id,
            "decision_source": candidate.decision_source,
            "decision_quality_tier": candidate.decision_quality_tier,
            "restored_from": str(candidate.request_archive_path),
        }
        with audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
        if log is not None:
            log(
                "[transport_failure_requeue]"
                f" request_id={request_id}"
                f" decision_source={candidate.decision_source}"
                f" restored_from={candidate.request_archive_path.name}"
                " action=requeued"
            )
    return summary
