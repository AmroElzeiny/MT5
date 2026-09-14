"""Single-call AI second approval for a deterministic PenaltyWatcher action.

Authority split
---------------
* MQL ``CPenaltyWatcher`` stays the FIRST authority.  It alone decides that a
  punishment is justified and freezes the exact proposal (action, cut fraction,
  trigger, strikes, evidence).
* This module makes exactly ONE provider call per frozen proposal and returns a
  verdict of APPROVE or DENY for that exact proposal.  The model cannot alter
  the action, the cut fraction, stops, targets or direction: its schema has no
  field that could carry such a change, and every binding field of the
  response envelope is copied from the request by deterministic code.
* ``confidence`` and ``recommended_wait_minutes`` are diagnostic only.  The EA
  owns the cooldown duration (``InpPenaltyCooldownMin``).

At-most-once
------------
A ledger entry is created with ``O_EXCL`` *before* the provider is called.  A
request id that already has a ledger entry is never sent to the provider again:
a resolved entry replays its stored envelope, and an entry left CLAIMED by a
process that died mid-call is answered with a non-trading ERROR envelope,
because whether the first call was billed/answered is unknowable.

Every failure -- malformed request, stale request, provider unavailable,
transport failure, schema-invalid model output -- produces a non-trading ERROR
envelope bound to the request.  Nothing but a schema-valid APPROVE can approve.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import Field

from structured_models import StrictStructuredModel, strict_structured_schema

MANAGEMENT_REVIEW_SCHEMA_VERSION = "20260915_management_ai_review_v1"
MANAGEMENT_REVIEW_REQUEST_KIND = "management_review"
MANAGEMENT_REVIEW_ROLE = "management_review"
MANAGEMENT_REVIEW_WORKLOAD_MODE = "management_review"
MANAGEMENT_REVIEW_DIRNAME = "management_review"

REVIEWABLE_ACTIONS = ("PARTIAL_CLOSE", "FULL_CLOSE")
STATUS_RESOLVED = "RESOLVED"
STATUS_ERROR = "ERROR"
LEDGER_CLAIMED = "CLAIMED"

ManagementVerdict = Literal["APPROVE", "DENY"]
ManagementReasonCode = Literal[
    "THESIS_INVALIDATED",
    "STRUCTURE_BROKEN",
    "ADVERSE_EXCURSION_EXCESSIVE",
    "MOMENTUM_STALLED",
    "PROFIT_GIVEBACK_MATERIAL",
    "RISK_REDUCTION_PRUDENT",
    "THESIS_STILL_VALID",
    "STRUCTURE_INTACT",
    "TRIGGER_NOISE_OR_WICK",
    "RECOVERY_EVIDENCE_PRESENT",
    "INSUFFICIENT_EVIDENCE_FOR_CUT",
    "PREMATURE_CUT",
]

_REQUEST_ID_RE = re.compile(r"^mgmt_(\d+)_(\d+)_(\d+)$")
_DIGITS_RE = re.compile(r"^\d{1,20}$")


class ModelManagementReview(StrictStructuredModel):
    """Provider-facing schema.  Verdict and diagnostics only -- no identity,
    no action, no cut fraction, no price."""

    verdict: ManagementVerdict
    reason_codes: list[ManagementReasonCode] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=400)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_wait_minutes: int = Field(ge=0, le=1440)


MANAGEMENT_REVIEW_SYSTEM_PROMPT = (
    "You are the second approval layer for ONE deterministic position-management "
    "action that the PenaltyWatcher has already decided is justified. "
    "You may only answer APPROVE (execute exactly the proposed action now) or "
    "DENY (do not execute it; the EA applies its own configured cooldown and "
    "re-evaluates deterministically later). "
    "You cannot change the action, the cut fraction, stops, targets, direction or "
    "size, and you cannot propose a new trade. "
    "APPROVE when the evidence supports the deterministic punishment (thesis or "
    "structure invalidated, excessive adverse excursion, stalled trade, material "
    "profit giveback). DENY only when the evidence shows the trigger is noise or "
    "the thesis and structure remain intact. "
    "reason_codes must justify the verdict. confidence and "
    "recommended_wait_minutes are diagnostic only and carry no authority. "
    "Return only the JSON object required by the schema."
)


class ManagementReviewRequestInvalid(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decode_bus_text(raw: bytes) -> str:
    """MQL writes FILE_TXT as UTF-16LE with BOM; config tools write UTF-8."""

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if len(raw) >= 2 and raw[0] != 0 and raw[1] == 0:
        return raw.decode("utf-16-le")
    return raw.decode("utf-8")


def _as_int(doc: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManagementReviewRequestInvalid(f"field_not_integer:{key}")
    if isinstance(value, float) and not value.is_integer():
        raise ManagementReviewRequestInvalid(f"field_not_integer:{key}")
    number = int(value)
    if number < minimum:
        raise ManagementReviewRequestInvalid(f"field_below_minimum:{key}")
    return number


def _as_str(doc: Mapping[str, Any], key: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManagementReviewRequestInvalid(f"field_not_string:{key}")
    return value


def _as_object(doc: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = doc.get(key)
    if not isinstance(value, Mapping):
        raise ManagementReviewRequestInvalid(f"field_not_object:{key}")
    return value


def validate_management_request(doc: Any) -> dict[str, Any]:
    """Strictly validate a request written by the EA and return its binding."""

    if not isinstance(doc, Mapping):
        raise ManagementReviewRequestInvalid("request_root_not_object")
    if doc.get("schema_version") != MANAGEMENT_REVIEW_SCHEMA_VERSION:
        raise ManagementReviewRequestInvalid("request_schema_version_mismatch")
    if doc.get("request_kind") != MANAGEMENT_REVIEW_REQUEST_KIND:
        raise ManagementReviewRequestInvalid("request_kind_mismatch")
    request_id = _as_str(doc, "request_id")
    position_identifier = _as_str(doc, "position_identifier")
    if not _DIGITS_RE.match(position_identifier):
        raise ManagementReviewRequestInvalid("position_identifier_not_digits")
    review_seq = _as_int(doc, "review_seq", minimum=1)
    match = _REQUEST_ID_RE.match(request_id)
    if not match or match.group(1) != position_identifier or int(match.group(2)) != review_seq:
        raise ManagementReviewRequestInvalid("request_id_not_bound_to_position_and_sequence")
    fingerprint = _as_str(doc, "request_fingerprint")
    if not _DIGITS_RE.match(fingerprint):
        raise ManagementReviewRequestInvalid("request_fingerprint_invalid")
    requested_at = _as_int(doc, "requested_at", minimum=1)
    action_id = _as_str(doc, "action_id")
    if not action_id.startswith(position_identifier + ":"):
        raise ManagementReviewRequestInvalid("action_id_not_bound_to_position")
    symbol = _as_str(doc, "symbol")
    proposal = _as_object(doc, "proposal")
    requested_action = _as_str(proposal, "requested_action")
    if requested_action not in REVIEWABLE_ACTIONS:
        raise ManagementReviewRequestInvalid("requested_action_not_reviewable")
    cut = proposal.get("requested_cut_fraction")
    if isinstance(cut, bool) or not isinstance(cut, (int, float)):
        raise ManagementReviewRequestInvalid("field_not_number:requested_cut_fraction")
    cut = float(cut)
    if not (0.0 < cut <= 1.0):
        raise ManagementReviewRequestInvalid("requested_cut_fraction_out_of_range")
    if requested_action == "FULL_CLOSE" and abs(cut - 1.0) > 1e-9:
        raise ManagementReviewRequestInvalid("full_close_cut_fraction_not_one")
    _as_str(proposal, "trigger_reason")
    _as_int(proposal, "strikes", minimum=0)
    for section in ("position", "invalidation", "thesis"):
        _as_object(doc, section)
    return {
        "request_id": request_id,
        "request_fingerprint": fingerprint,
        "position_identifier": position_identifier,
        "action_id": action_id,
        "symbol": symbol,
        "requested_action": requested_action,
        "requested_cut_fraction": cut,
        "requested_at": requested_at,
        "review_seq": review_seq,
    }


def build_envelope(
    binding: Mapping[str, Any],
    *,
    status: str,
    verdict: str = "NONE",
    reason_codes: list[str] | None = None,
    reason: str = "",
    confidence: float = 0.0,
    recommended_wait_minutes: int = 0,
    error_category: str = "",
    error_detail: str = "",
    provider_id: str = "",
    provider_mode: str = "",
    model: str = "",
    provider_call_attempted: bool = False,
    resolved_at_utc: int | None = None,
) -> dict[str, Any]:
    if status not in (STATUS_RESOLVED, STATUS_ERROR):
        raise ValueError("management_review_status_invalid")
    if status == STATUS_RESOLVED and verdict not in ("APPROVE", "DENY"):
        raise ValueError("management_review_resolved_without_verdict")
    if status == STATUS_ERROR:
        verdict = "NONE"
    envelope: dict[str, Any] = {
        "schema_version": MANAGEMENT_REVIEW_SCHEMA_VERSION,
        "response_kind": MANAGEMENT_REVIEW_REQUEST_KIND,
        "status": status,
        "request_id": str(binding.get("request_id") or ""),
        "request_fingerprint": str(binding.get("request_fingerprint") or ""),
        "position_identifier": str(binding.get("position_identifier") or ""),
        "action_id": str(binding.get("action_id") or ""),
        "requested_action": str(binding.get("requested_action") or ""),
        "requested_cut_fraction": float(binding.get("requested_cut_fraction") or 0.0),
        "requested_at": int(binding.get("requested_at") or 0),
        "verdict": verdict,
        "reason_codes": list(reason_codes or []),
        "reason": str(reason)[:400],
        "confidence": float(confidence),
        "confidence_authority": "diagnostic_only",
        "recommended_wait_minutes": int(recommended_wait_minutes),
        "recommended_wait_authority": "diagnostic_only",
        "cooldown_authority": "mql_input_InpPenaltyCooldownMin",
        "error_category": error_category,
        "error_detail": str(error_detail)[:400],
        "provider_id": provider_id,
        "provider_mode": provider_mode,
        "model": model,
        "provider_call_attempted": bool(provider_call_attempted),
        "resolved_at_utc": int(resolved_at_utc if resolved_at_utc is not None else time.time()),
    }
    envelope["response_fingerprint"] = hashlib.sha256(_canonical(envelope).encode("utf-8")).hexdigest()[:32]
    return envelope


def _atomic_write_json(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


class ManagementReviewService:
    """File-bus worker for ``<bus>/management_review``."""

    def __init__(
        self,
        bus: Path,
        provider: Any,
        *,
        log: Callable[[str], None] = print,
        clock: Callable[[], float] = time.time,
        request_max_age_sec: float = 600.0,
        provider_timeout_sec: float = 90.0,
        max_output_tokens: int = 900,
        stable_after_sec: float = 0.2,
    ) -> None:
        self.root = Path(bus) / MANAGEMENT_REVIEW_DIRNAME
        self.requests_dir = self.root / "requests"
        self.processing_dir = self.root / "processing"
        self.responses_dir = self.root / "responses"
        self.ledger_dir = self.root / "ledger"
        self.provider = provider
        self.log = log
        self.clock = clock
        self.request_max_age_sec = float(request_max_age_sec)
        self.provider_timeout_sec = float(provider_timeout_sec)
        self.max_output_tokens = int(max_output_tokens)
        self.stable_after_sec = float(stable_after_sec)
        self.provider_calls = 0
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ layout
    def ensure(self) -> None:
        for directory in (self.requests_dir, self.processing_dir, self.responses_dir, self.ledger_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _ledger_path(self, request_id: str) -> Path:
        return self.ledger_dir / f"{request_id}.json"

    def _read_ledger(self, request_id: str) -> dict[str, Any] | None:
        path = self._ledger_path(request_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # An unreadable CLAIM marker still proves a claim happened.
            return {"state": LEDGER_CLAIMED, "unreadable": True}

    # ---------------------------------------------------------------- recovery
    def recover(self) -> int:
        """Resolve requests a previous process claimed but never finished."""

        self.ensure()
        recovered = 0
        for path in sorted(self.processing_dir.glob("*.json")):
            request_id = path.stem
            ledger = self._read_ledger(request_id)
            if ledger is None:
                # No claim marker: the provider was provably never called.
                path.replace(self.requests_dir / path.name)
                self.log(f"[management_ai_review] stage=recover request_id={request_id} action=requeued provider_call_attempted=false")
            else:
                self.process_claimed(path)
            recovered += 1
        return recovered

    # ------------------------------------------------------------------- claim
    def claim_ready(self, max_claims: int) -> list[Path]:
        claimed: list[Path] = []
        if max_claims <= 0 or not self.requests_dir.is_dir():
            return claimed
        now = self.clock()
        for path in sorted(self.requests_dir.glob("*.json")):
            if len(claimed) >= max_claims:
                break
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if stat.st_size <= 0:
                continue
            # Filesystem mtimes can sit slightly ahead of time.time(); a zero
            # stability window therefore means "no wait", not "age >= 0".
            if self.stable_after_sec > 0 and now - stat.st_mtime < self.stable_after_sec:
                continue
            with self._lock:
                if path.stem in self._inflight:
                    continue
            target = self.processing_dir / path.name
            try:
                self.processing_dir.mkdir(parents=True, exist_ok=True)
                path.replace(target)
            except (FileNotFoundError, PermissionError):
                continue
            claimed.append(target)
        return claimed

    # ----------------------------------------------------------------- process
    def process_claimed(self, path: Path) -> dict[str, Any] | None:
        request_id = path.stem
        try:
            raw = path.read_bytes()
            doc = json.loads(_decode_bus_text(raw))
            binding = validate_management_request(doc)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            detail = str(exc)
            binding = {"request_id": request_id}
            if isinstance(doc_candidate := _safe_mapping(path), Mapping):
                for key in ("request_fingerprint", "position_identifier", "action_id", "requested_at"):
                    if key in doc_candidate:
                        binding[key] = doc_candidate[key]
            envelope = build_envelope(binding, status=STATUS_ERROR, error_category="REQUEST_INVALID", error_detail=detail)
            self._finalize(path, request_id, envelope, provider_call_attempted=False, record_ledger=False)
            self.log(f"[management_ai_review] stage=request_rejected request_id={request_id} reason={detail} provider_call_attempted=false")
            return envelope
        if binding["request_id"] != request_id:
            envelope = build_envelope(binding, status=STATUS_ERROR, error_category="REQUEST_INVALID", error_detail="file_name_request_id_mismatch")
            self._finalize(path, request_id, envelope, provider_call_attempted=False, record_ledger=False)
            return envelope

        with self._lock:
            if request_id in self._inflight:
                return None
            self._inflight.add(request_id)
        try:
            return self._process_bound(path, doc, binding)
        finally:
            with self._lock:
                self._inflight.discard(request_id)

    def _process_bound(self, path: Path, doc: Mapping[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
        request_id = binding["request_id"]
        ledger = self._read_ledger(request_id)
        if ledger is not None:
            return self._answer_from_existing_ledger(path, binding, ledger)

        try:
            age = self.clock() - path.stat().st_mtime
        except OSError:
            age = 0.0
        if age > self.request_max_age_sec:
            envelope = build_envelope(binding, status=STATUS_ERROR, error_category="REQUEST_STALE",
                                      error_detail=f"request_age_sec={age:.1f}")
            self._finalize(path, request_id, envelope, provider_call_attempted=False, record_ledger=True)
            self.log(f"[management_ai_review] stage=request_stale request_id={request_id} age_sec={age:.1f} provider_call_attempted=false")
            return envelope

        if not self._create_claim(binding):
            ledger = self._read_ledger(request_id) or {"state": LEDGER_CLAIMED}
            return self._answer_from_existing_ledger(path, binding, ledger)

        envelope = self._call_provider_once(doc, binding)
        self._finalize(path, request_id, envelope, provider_call_attempted=True, record_ledger=True)
        return envelope

    def _create_claim(self, binding: Mapping[str, Any]) -> bool:
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        marker = {
            "state": LEDGER_CLAIMED,
            "request_id": binding["request_id"],
            "request_fingerprint": binding["request_fingerprint"],
            "claimed_at_utc": int(self.clock()),
            "pid": os.getpid(),
        }
        try:
            fd = os.open(str(self._ledger_path(binding["request_id"])), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_canonical(marker))
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def _answer_from_existing_ledger(self, path: Path, binding: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
        request_id = binding["request_id"]
        stored = ledger.get("envelope")
        if ledger.get("state") in (STATUS_RESOLVED, STATUS_ERROR) and isinstance(stored, Mapping):
            if stored.get("request_fingerprint") == binding["request_fingerprint"]:
                envelope = dict(stored)
                action = "replayed_from_ledger"
            else:
                envelope = build_envelope(binding, status=STATUS_ERROR, error_category="REQUEST_ID_REUSED",
                                          error_detail="request_id_already_resolved_with_different_fingerprint")
                action = "request_id_reuse_rejected"
        else:
            envelope = build_envelope(binding, status=STATUS_ERROR, error_category="PROVIDER_OUTCOME_UNKNOWN",
                                      error_detail="claim_exists_without_terminal_result_no_resubmission")
            action = "claimed_without_result_no_resubmission"
        self._write_response(request_id, envelope)
        self._remove(path)
        self.log(f"[management_ai_review] stage=duplicate_suppressed request_id={request_id} action={action} provider_call_attempted=false")
        return envelope

    def _call_provider_once(self, doc: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
        request_id = binding["request_id"]
        evidence = {
            "review_contract": {
                "schema_version": MANAGEMENT_REVIEW_SCHEMA_VERSION,
                "allowed_verdicts": ["APPROVE", "DENY"],
                "approve_executes": "exact_deterministic_action_unchanged",
                "deny_effect": "no_broker_action_then_ea_configured_cooldown",
                "model_may_modify": [],
            },
            "symbol": binding["symbol"],
            "proposal": doc.get("proposal"),
            "position": doc.get("position"),
            "invalidation": doc.get("invalidation"),
            "thesis": doc.get("thesis"),
            "state": doc.get("state") if isinstance(doc.get("state"), Mapping) else {},
        }
        identity_hash = hashlib.sha256(_canonical(evidence).encode("utf-8")).hexdigest()
        self.provider_calls += 1
        self.log(
            "[management_ai_review] stage=provider_call_started"
            f" request_id={request_id} position_id={binding['position_identifier']}"
            f" action={binding['requested_action']} cut_fraction={binding['requested_cut_fraction']:.4f}"
            f" provider={getattr(self.provider, 'provider_id', '')}"
        )
        try:
            result = self.provider.generate_structured(
                role=MANAGEMENT_REVIEW_ROLE,
                system_prompt=MANAGEMENT_REVIEW_SYSTEM_PROMPT,
                evidence=evidence,
                response_schema=ModelManagementReview,
                request_metadata={
                    "request_id": request_id,
                    "request_identity_hash": identity_hash,
                    "workload_mode": MANAGEMENT_REVIEW_WORKLOAD_MODE,
                    "symbol": binding["symbol"],
                    "timeout_sec": self.provider_timeout_sec,
                    "max_output_tokens": self.max_output_tokens,
                },
            )
        except Exception as exc:  # noqa: BLE001 - every failure is non-approving
            category = str(getattr(exc, "category", "") or "")
            if not category:
                unavailable = str(getattr(self.provider, "provider_mode", "")).upper() == "UNAVAILABLE"
                category = "PROVIDER_UNAVAILABLE" if unavailable else "LOCAL_PIPELINE_ERROR"
            self.log(f"[management_ai_review] stage=provider_call_failed request_id={request_id} error_category={category} exception_type={type(exc).__name__}")
            return build_envelope(binding, status=STATUS_ERROR, error_category=category, error_detail=str(exc),
                                  provider_id=str(getattr(self.provider, "provider_id", "")),
                                  provider_mode=str(getattr(self.provider, "provider_mode", "")),
                                  provider_call_attempted=True)
        provider_id = str(getattr(result, "provider_id", "") or "")
        provider_mode = str(getattr(result, "provider_mode", "") or "")
        model = str(getattr(result, "actual_model", "") or getattr(result, "requested_model", "") or "")
        try:
            parsed = result.parsed
            data = parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
            review = ModelManagementReview.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[management_ai_review] stage=model_output_invalid request_id={request_id} detail={str(exc)[:200]!r}")
            return build_envelope(binding, status=STATUS_ERROR, error_category="STRUCTURED_RESPONSE_INVALID",
                                  error_detail=str(exc), provider_id=provider_id, provider_mode=provider_mode,
                                  model=model, provider_call_attempted=True)
        envelope = build_envelope(
            binding,
            status=STATUS_RESOLVED,
            verdict=review.verdict,
            reason_codes=list(review.reason_codes),
            reason=review.reason,
            confidence=review.confidence,
            recommended_wait_minutes=review.recommended_wait_minutes,
            provider_id=provider_id,
            provider_mode=provider_mode,
            model=model,
            provider_call_attempted=True,
        )
        self.log(
            "[management_ai_review] stage=provider_call_completed"
            f" request_id={request_id} verdict={review.verdict} reason_codes={','.join(review.reason_codes)}"
            f" provider={provider_id} model={model} confidence_diagnostic={review.confidence:.2f}"
        )
        return envelope

    # -------------------------------------------------------------- terminal
    def _write_response(self, request_id: str, envelope: Mapping[str, Any]) -> None:
        _atomic_write_json(self.responses_dir / f"{request_id}.json", envelope)

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _finalize(self, path: Path, request_id: str, envelope: Mapping[str, Any], *,
                  provider_call_attempted: bool, record_ledger: bool) -> None:
        if record_ledger:
            _atomic_write_json(
                self._ledger_path(request_id),
                {
                    "state": envelope["status"],
                    "request_id": request_id,
                    "request_fingerprint": envelope.get("request_fingerprint", ""),
                    "provider_call_attempted": bool(provider_call_attempted),
                    "envelope": dict(envelope),
                },
            )
        self._write_response(request_id, envelope)
        self._remove(path)
        self.log(
            "[management_ai_review] stage=response_written"
            f" request_id={request_id} status={envelope['status']} verdict={envelope['verdict']}"
            f" error_category={envelope.get('error_category') or 'none'}"
            f" provider_call_attempted={str(provider_call_attempted).lower()}"
        )


def _safe_mapping(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(_decode_bus_text(path.read_bytes()))
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, Mapping) else None


def management_review_schema_preflight():
    return strict_structured_schema(ModelManagementReview)
