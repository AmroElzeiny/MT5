"""Durable exactly-once lifecycle and heartbeats for AI file-bus requests."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


REQUEST_LIFECYCLE_VERSION = "20260724_exactly_once_request_v1"
REQUEST_STATES = (
    "CREATED",
    "CLAIMED",
    "PROVIDER_RUNNING",
    "RESPONSE_VALIDATED",
    "RESPONSE_WRITTEN",
    "COMPLETED",
    "ARCHIVED",
)
ACTIVE_REQUEST_STATES = {"CLAIMED", "PROVIDER_RUNNING"}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            dict(payload),
            handle,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _read_json_any_encoding(path: Path) -> Any:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
    raise last_error or ValueError("json_read_failed")


def process_is_available(pid: int, hostname: str) -> bool | None:
    """True/False locally; None when a remote host cannot be checked."""

    if not pid:
        return False
    if hostname and hostname != socket.gethostname():
        return None
    if pid == os.getpid():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_heartbeat(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, Mapping) else {}
    except Exception:
        return {}


def heartbeat_allows_recovery(
    path: Path,
    *,
    now: float | None = None,
    stale_after_sec: float,
    stale_grace_sec: float,
) -> bool:
    now = float(time.time() if now is None else now)
    heartbeat = read_heartbeat(path)
    heartbeat_at = float(
        heartbeat.get("heartbeat_at")
        or heartbeat.get("claimed_at")
        or (path.stat().st_mtime if path.exists() else 0.0)
    )
    if now - heartbeat_at <= float(stale_after_sec):
        return False
    process_state = process_is_available(
        int(heartbeat.get("process_id") or heartbeat.get("pid") or 0),
        str(heartbeat.get("hostname") or ""),
    )
    if process_state is True:
        return False
    return now - heartbeat_at > float(stale_after_sec) + float(stale_grace_sec)


@dataclass(frozen=True)
class IdempotencyDisposition:
    action: str
    reason: str
    record: dict[str, Any]
    response: dict[str, Any] | None = None


class RequestIdempotencyLedger:
    """One durable state row per request ID with collision detection."""

    def __init__(self, root: Path, *, worker_id: str) -> None:
        self.root = Path(root)
        self.worker_id = str(worker_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()

    def _path(self, request_id: str) -> Path:
        digest = hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def _lock_path(self, request_id: str) -> Path:
        return self._path(request_id).with_suffix(".lock")

    @contextmanager
    def _locked(self, request_id: str):
        lock_path = self._lock_path(request_id)
        deadline = time.monotonic() + 5.0
        while True:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(
                    descriptor,
                    _canonical_json(
                        {
                            "pid": os.getpid(),
                            "hostname": socket.gethostname(),
                            "created_at": time.time(),
                        }
                    ).encode("utf-8"),
                )
                os.close(descriptor)
                break
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > 30.0:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"request_ledger_lock_timeout:{request_id}")
                time.sleep(0.02)
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    def read(self, request_id: str) -> dict[str, Any]:
        path = self._path(request_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return dict(value) if isinstance(value, Mapping) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            return {
                "request_id": request_id,
                "state": "QUARANTINED",
                "reason": "idempotency_ledger_corrupt",
            }

    @staticmethod
    def idempotency_key(
        *,
        request_id: str,
        request_identity_hash: str,
        provider_id: str,
        model_id: str,
        prompt_contract_version: str,
        schema_fingerprint: str,
    ) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "request_id": request_id,
                    "request_identity_hash": request_identity_hash,
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "prompt_contract_version": prompt_contract_version,
                    "schema_fingerprint": schema_fingerprint,
                }
            ).encode("utf-8")
        ).hexdigest()

    def begin(
        self,
        *,
        request_id: str,
        request_identity_hash: str,
        provider_id: str,
        model_id: str,
        prompt_contract_version: str,
        schema_fingerprint: str,
        response_path: Path,
        stale_after_sec: float,
    ) -> IdempotencyDisposition:
        key = self.idempotency_key(
            request_id=request_id,
            request_identity_hash=request_identity_hash,
            provider_id=provider_id,
            model_id=model_id,
            prompt_contract_version=prompt_contract_version,
            schema_fingerprint=schema_fingerprint,
        )
        with self._thread_lock, self._locked(request_id):
            existing = self.read(request_id)
            if existing:
                if str(existing.get("request_identity_hash") or "") != request_identity_hash:
                    return IdempotencyDisposition(
                        "COLLISION",
                        "request_id_identity_collision",
                        existing,
                    )
                if str(existing.get("idempotency_key") or "") not in {"", key}:
                    return IdempotencyDisposition(
                        "COLLISION",
                        "request_id_provider_contract_collision",
                        existing,
                    )
                state = str(existing.get("state") or "")
                saved_response = existing.get("validated_response")
                if state in {"RESPONSE_VALIDATED", "RESPONSE_WRITTEN", "COMPLETED", "ARCHIVED"}:
                    if isinstance(saved_response, Mapping):
                        return IdempotencyDisposition(
                            "REUSE",
                            "validated_response_reused",
                            existing,
                            dict(saved_response),
                        )
                    if response_path.is_file():
                        try:
                            response = _read_json_any_encoding(response_path)
                            if (
                                isinstance(response, Mapping)
                                and str(response.get("id") or "") == request_id
                                and str(response.get("request_identity_hash") or "")
                                == request_identity_hash
                            ):
                                return IdempotencyDisposition(
                                    "REUSE",
                                    "completed_response_reused",
                                    existing,
                                    dict(response),
                                )
                        except Exception:
                            pass
                if state in ACTIVE_REQUEST_STATES:
                    heartbeat_at = float(existing.get("heartbeat_at") or 0.0)
                    if time.time() - heartbeat_at <= float(stale_after_sec):
                        return IdempotencyDisposition(
                            "ACTIVE",
                            "duplicate_request_already_running",
                            existing,
                        )
            elif response_path.is_file():
                try:
                    response = _read_json_any_encoding(response_path)
                    response_matches = (
                        isinstance(response, Mapping)
                        and str(response.get("id") or "") == request_id
                        and str(response.get("request_identity_hash") or "")
                        == request_identity_hash
                        and str(response.get("provider_id") or "") == provider_id
                        and str(response.get("prompt_contract_version") or "")
                        == prompt_contract_version
                        and str(response.get("decision_quality_tier") or "")
                        in {"FULL_STRUCTURED", "CACHE_OF_FULL_STRUCTURED"}
                        and bool(response.get("mandatory_fields_complete"))
                    )
                    if response_matches:
                        migrated = {
                            "request_lifecycle_version": REQUEST_LIFECYCLE_VERSION,
                            "request_id": request_id,
                            "request_identity_hash": request_identity_hash,
                            "idempotency_key": key,
                            "provider_id": provider_id,
                            "model_id": model_id,
                            "prompt_contract_version": prompt_contract_version,
                            "schema_fingerprint": schema_fingerprint,
                            "state": "COMPLETED",
                            "worker_id": self.worker_id,
                            "process_id": os.getpid(),
                            "hostname": socket.gethostname(),
                            "claimed_at": time.time(),
                            "heartbeat_at": time.time(),
                            "updated_at": time.time(),
                            "validated_response": dict(response),
                            "migration_source": "preexisting_identity_bound_response",
                        }
                        _atomic_write_json(self._path(request_id), migrated)
                        return IdempotencyDisposition(
                            "REUSE",
                            "preexisting_response_reused",
                            migrated,
                            dict(response),
                        )
                    if isinstance(response, Mapping):
                        return IdempotencyDisposition(
                            "COLLISION",
                            "preexisting_response_identity_mismatch",
                            dict(response),
                        )
                except Exception:
                    return IdempotencyDisposition(
                        "COLLISION",
                        "preexisting_response_unreadable",
                        {},
                    )

            now = time.time()
            record = {
                "request_lifecycle_version": REQUEST_LIFECYCLE_VERSION,
                "request_id": request_id,
                "request_identity_hash": request_identity_hash,
                "idempotency_key": key,
                "provider_id": provider_id,
                "model_id": model_id,
                "prompt_contract_version": prompt_contract_version,
                "schema_fingerprint": schema_fingerprint,
                "state": "CLAIMED",
                "worker_id": self.worker_id,
                "process_id": os.getpid(),
                "hostname": socket.gethostname(),
                "claimed_at": now,
                "heartbeat_at": now,
                "updated_at": now,
            }
            _atomic_write_json(self._path(request_id), record)
            return IdempotencyDisposition("PROCESS", "new_or_stale_claim", record)

    def transition(
        self,
        request_id: str,
        state: str,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in REQUEST_STATES:
            raise ValueError(f"invalid_request_lifecycle_state:{state}")
        with self._thread_lock, self._locked(request_id):
            record = self.read(request_id)
            if not record:
                raise ValueError(f"request_lifecycle_record_missing:{request_id}")
            record["state"] = state
            record["worker_id"] = self.worker_id
            record["process_id"] = os.getpid()
            record["hostname"] = socket.gethostname()
            record["heartbeat_at"] = time.time()
            record["updated_at"] = time.time()
            if extra:
                record.update(dict(extra))
            _atomic_write_json(self._path(request_id), record)
            return record

    def heartbeat(
        self,
        request_id: str,
        *,
        provider_call_started_at: float,
        expected_max_provider_duration_sec: float,
    ) -> None:
        self.transition(
            request_id,
            "PROVIDER_RUNNING",
            extra={
                "provider_call_started_at": provider_call_started_at,
                "expected_max_provider_duration_sec": (
                    expected_max_provider_duration_sec
                ),
            },
        )


class RequestHeartbeat:
    def __init__(
        self,
        *,
        ledger: RequestIdempotencyLedger,
        request_id: str,
        lock_path: Path,
        expected_max_provider_duration_sec: float,
        interval_sec: float = 15.0,
    ) -> None:
        self.ledger = ledger
        self.request_id = request_id
        self.lock_path = Path(lock_path)
        self.expected_max_provider_duration_sec = float(
            expected_max_provider_duration_sec
        )
        self.interval_sec = max(1.0, float(interval_sec))
        self.started_at = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"ai-heartbeat-{request_id[:24]}",
            daemon=True,
        )

    def _write(self) -> None:
        now = time.time()
        metadata = {
            "request_lifecycle_version": REQUEST_LIFECYCLE_VERSION,
            "request_id": self.request_id,
            "worker_id": self.ledger.worker_id,
            "process_id": os.getpid(),
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "claimed_at": self.started_at,
            "provider_call_started_at": self.started_at,
            "heartbeat_at": now,
            "expected_max_provider_duration_sec": (
                self.expected_max_provider_duration_sec
            ),
        }
        _atomic_write_json(self.lock_path, metadata)
        self.ledger.heartbeat(
            self.request_id,
            provider_call_started_at=self.started_at,
            expected_max_provider_duration_sec=(
                self.expected_max_provider_duration_sec
            ),
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._write()
            except Exception:
                # The processing owner handles terminal failure. A heartbeat
                # error must not crash the file-bus worker thread.
                pass
            self._stop.wait(self.interval_sec)

    def __enter__(self) -> "RequestHeartbeat":
        self._write()
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_sec + 1.0))
        try:
            self._write()
        except Exception:
            pass
