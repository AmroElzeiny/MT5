from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from .config import settings
from .audit import log_event


@dataclass
class PendingJob:
    id: str
    request_id: str
    model_id: str
    chat_alias: str
    prompt: str
    schema: dict[str, Any] | None
    created_at: float
    deadline_at: float
    dedupe_key: str


class JobStore:
    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._init_db()
        self.recover_processing()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(settings.sqlite_file, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    chat_alias TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    schema_json TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    deadline_at REAL NOT NULL,
                    response_text TEXT,
                    error TEXT,
                    reviewed INTEGER NOT NULL DEFAULT 0,
                    dedupe_key TEXT,
                    sent_at REAL
                )
            """)
            cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
            if "dedupe_key" not in cols:
                con.execute("ALTER TABLE jobs ADD COLUMN dedupe_key TEXT")
            if "sent_at" not in cols:
                con.execute("ALTER TABLE jobs ADD COLUMN sent_at REAL")
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key)")

    @staticmethod
    def _dedupe(request_id: str, model_id: str, chat_alias: str, prompt: str, schema: dict[str, Any] | None) -> str:
        payload = {
            "request_id": request_id,
            "model_id": model_id,
            "chat_alias": chat_alias,
            "prompt": prompt,
            "schema": schema,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def recover_processing(self) -> None:
        now = time.time()
        with self._connect() as con:
            con.execute("UPDATE jobs SET status='expired', error='deadline_exceeded' WHERE status IN ('pending','processing') AND deadline_at<=?", (now,))
            # A processing job that had already been sent is never automatically
            # resent after restart. It fails closed to avoid duplicate prompts.
            con.execute("UPDATE jobs SET status='failed', error='restart_after_send_no_resubmit' WHERE status='processing' AND sent_at IS NOT NULL")
            con.execute("UPDATE jobs SET status='pending' WHERE status='processing' AND sent_at IS NULL AND deadline_at>?", (now,))

    def _pending_count(self) -> int:
        now = time.time()
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('pending','processing') AND deadline_at>?", (now,)).fetchone()
            return int(row["n"])

    def create(self, request_id: str, model_id: str, chat_alias: str, prompt: str,
               schema: dict[str, Any] | None, timeout_sec: int,
               deadline_at: float | None = None) -> PendingJob:
        with self._cv:
            self.expire_due()
            request_id = request_id or str(uuid.uuid4())
            dedupe_key = self._dedupe(request_id, model_id, chat_alias, prompt, schema)
            with self._connect() as con:
                existing = con.execute(
                    "SELECT * FROM jobs WHERE dedupe_key=? AND status IN ('pending','processing','completed') ORDER BY created_at DESC LIMIT 1",
                    (dedupe_key,),
                ).fetchone()
            if existing:
                log_event("job_deduplicated", job_id=existing["id"], request_id=request_id, status=existing["status"])
                return PendingJob(
                    id=existing["id"], request_id=existing["request_id"], model_id=existing["model_id"],
                    chat_alias=existing["chat_alias"], prompt=existing["prompt"],
                    schema=json.loads(existing["schema_json"]) if existing["schema_json"] else None,
                    created_at=existing["created_at"], deadline_at=existing["deadline_at"], dedupe_key=dedupe_key,
                )
            if self._pending_count() >= settings.max_pending_jobs:
                raise RuntimeError("bridge_queue_full")
            now = time.time()
            relative_deadline = now + min(timeout_sec, settings.hard_timeout_sec)
            effective_deadline = min(
                relative_deadline,
                float(deadline_at) if deadline_at is not None else relative_deadline,
            )
            if effective_deadline <= now:
                raise RuntimeError("deadline_exceeded_before_bridge_queue")
            job = PendingJob(
                id=str(uuid.uuid4()), request_id=request_id, model_id=model_id, chat_alias=chat_alias,
                prompt=prompt, schema=schema, created_at=now,
                deadline_at=effective_deadline, dedupe_key=dedupe_key,
            )
            with self._connect() as con:
                con.execute(
                    "INSERT INTO jobs(id,request_id,model_id,chat_alias,prompt,schema_json,status,created_at,deadline_at,dedupe_key) VALUES(?,?,?,?,?,?, 'pending', ?, ?, ?)",
                    (job.id, job.request_id, job.model_id, job.chat_alias, job.prompt,
                     json.dumps(schema, ensure_ascii=False) if schema else None,
                     job.created_at, job.deadline_at, job.dedupe_key),
                )
            log_event("job_created", job_id=job.id, request_id=job.request_id, model_id=model_id, chat_alias=chat_alias)
            self._cv.notify_all()
            return job

    def claim_next_pending(
        self,
        *,
        preferred_request_id: str | None = None,
        excluded_request_ids: set[str] | None = None,
    ) -> dict[str, Any] | None:
        with self._cv:
            self.expire_due()
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                now = time.time()
                if preferred_request_id:
                    row = con.execute(
                        "SELECT * FROM jobs WHERE status='pending' AND deadline_at>? "
                        "AND request_id=? ORDER BY created_at ASC LIMIT 1",
                        (now, preferred_request_id),
                    ).fetchone()
                else:
                    excluded = sorted(str(value) for value in (excluded_request_ids or set()) if value)
                    if excluded:
                        placeholders = ",".join("?" for _ in excluded)
                        row = con.execute(
                            "SELECT * FROM jobs WHERE status='pending' AND deadline_at>? "
                            f"AND request_id NOT IN ({placeholders}) ORDER BY created_at ASC LIMIT 1",
                            (now, *excluded),
                        ).fetchone()
                    else:
                        row = con.execute(
                            "SELECT * FROM jobs WHERE status='pending' AND deadline_at>? "
                            "ORDER BY created_at ASC LIMIT 1",
                            (now,),
                        ).fetchone()
                if not row:
                    con.commit()
                    return None
                updated = con.execute("UPDATE jobs SET status='processing' WHERE id=? AND status='pending'", (row["id"],)).rowcount
                con.commit()
                if updated != 1:
                    return None
            result = dict(row)
            result["status"] = "processing"
            log_event("job_claimed", job_id=result["id"], request_id=result["request_id"])
            return result

    def mark_sent(self, job_id: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE jobs SET sent_at=? WHERE id=? AND status='processing'", (time.time(), job_id))

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        self.expire_due()
        with self._connect() as con:
            rows = con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [dict(r) for r in rows]

    def list_pending(self) -> list[dict[str, Any]]:
        self.expire_due()
        with self._connect() as con:
            rows = con.execute("SELECT * FROM jobs WHERE status IN ('pending','processing') ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]

    def get(self, job_id: str) -> dict[str, Any] | None:
        self.expire_due()
        with self._connect() as con:
            r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def submit(self, job_id: str, response_text: str, reviewed: bool = False) -> None:
        with self._cv:
            row = self.get(job_id)
            if not row:
                raise KeyError("job_not_found")
            if row["status"] not in {"pending", "processing"}:
                raise RuntimeError(f"job_not_active:{row['status']}")
            if time.time() >= float(row["deadline_at"]):
                self.expire_due()
                raise RuntimeError("job_expired")
            with self._connect() as con:
                con.execute("UPDATE jobs SET status='completed', response_text=?, reviewed=? WHERE id=?", (response_text, 1 if reviewed else 0, job_id))
            log_event("job_completed", job_id=job_id, request_id=row["request_id"], reviewed=reviewed)
            self._cv.notify_all()

    def fail(self, job_id: str, error: str) -> None:
        with self._cv:
            row = self.get(job_id)
            if not row:
                return
            with self._connect() as con:
                con.execute("UPDATE jobs SET status='failed', error=? WHERE id=? AND status IN ('pending','processing')", (error, job_id))
            log_event("job_failed", job_id=job_id, request_id=row["request_id"], error=error)
            self._cv.notify_all()

    def expire_due(self) -> None:
        now = time.time()
        with self._connect() as con:
            due = con.execute("SELECT id,request_id FROM jobs WHERE status IN ('pending','processing') AND deadline_at<=?", (now,)).fetchall()
            con.execute("UPDATE jobs SET status='expired', error='deadline_exceeded' WHERE status IN ('pending','processing') AND deadline_at<=?", (now,))
        for r in due:
            log_event("job_expired", job_id=r["id"], request_id=r["request_id"])

    def wait(self, job_id: str) -> str:
        while True:
            row = self.get(job_id)
            if not row:
                raise RuntimeError("job_disappeared")
            status = row["status"]
            if status == "completed":
                return str(row["response_text"] or "")
            if status in {"expired", "failed"}:
                raise RuntimeError(str(row["error"] or status))
            remaining = max(0.0, float(row["deadline_at"]) - time.time())
            if remaining <= 0:
                self.expire_due()
                continue
            with self._cv:
                self._cv.wait(timeout=min(0.5, remaining))


job_store = JobStore()
