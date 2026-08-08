from __future__ import annotations

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


class JobStore:
    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._init_db()

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
                    reviewed INTEGER NOT NULL DEFAULT 0
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)")

    def _pending_count(self) -> int:
        now = time.time()
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status='pending' AND deadline_at>?", (now,)
            ).fetchone()
            return int(row["n"])

    def create(self, request_id: str, model_id: str, chat_alias: str, prompt: str,
               schema: dict[str, Any] | None, timeout_sec: int) -> PendingJob:
        with self._cv:
            self.expire_due()
            if self._pending_count() >= settings.max_pending_jobs:
                raise RuntimeError("bridge_queue_full")
            now = time.time()
            job = PendingJob(
                id=str(uuid.uuid4()),
                request_id=request_id or str(uuid.uuid4()),
                model_id=model_id,
                chat_alias=chat_alias,
                prompt=prompt,
                schema=schema,
                created_at=now,
                deadline_at=now + min(timeout_sec, settings.hard_timeout_sec),
            )
            with self._connect() as con:
                con.execute(
                    "INSERT INTO jobs(id,request_id,model_id,chat_alias,prompt,schema_json,status,created_at,deadline_at) VALUES(?,?,?,?,?,?, 'pending', ?, ?)",
                    (job.id, job.request_id, job.model_id, job.chat_alias, job.prompt,
                     json.dumps(schema, ensure_ascii=False) if schema else None,
                     job.created_at, job.deadline_at),
                )
            log_event("job_created", job_id=job.id, request_id=job.request_id, model_id=model_id, chat_alias=chat_alias)
            self._cv.notify_all()
            return job

    def list_pending(self) -> list[dict[str, Any]]:
        self.expire_due()
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,request_id,model_id,chat_alias,prompt,schema_json,created_at,deadline_at FROM jobs WHERE status='pending' ORDER BY created_at ASC"
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"], "request_id": r["request_id"], "model_id": r["model_id"],
                "chat_alias": r["chat_alias"], "prompt": r["prompt"],
                "schema": json.loads(r["schema_json"]) if r["schema_json"] else None,
                "created_at": r["created_at"], "deadline_at": r["deadline_at"],
            })
        return out

    def get(self, job_id: str) -> dict[str, Any] | None:
        self.expire_due()
        with self._connect() as con:
            r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not r:
            return None
        return dict(r)

    def submit(self, job_id: str, response_text: str, reviewed: bool) -> None:
        with self._cv:
            row = self.get(job_id)
            if not row:
                raise KeyError("job_not_found")
            if row["status"] != "pending":
                raise RuntimeError(f"job_not_pending:{row['status']}")
            if time.time() >= float(row["deadline_at"]):
                self.expire_due()
                raise RuntimeError("job_expired")
            with self._connect() as con:
                con.execute(
                    "UPDATE jobs SET status='completed', response_text=?, reviewed=? WHERE id=?",
                    (response_text, 1 if reviewed else 0, job_id),
                )
            log_event("job_completed", job_id=job_id, request_id=row["request_id"], reviewed=reviewed)
            self._cv.notify_all()

    def fail(self, job_id: str, error: str) -> None:
        with self._cv:
            row = self.get(job_id)
            if not row:
                return
            with self._connect() as con:
                con.execute("UPDATE jobs SET status='failed', error=? WHERE id=? AND status='pending'", (error, job_id))
            log_event("job_failed", job_id=job_id, request_id=row["request_id"], error=error)
            self._cv.notify_all()

    def expire_due(self) -> None:
        now = time.time()
        with self._connect() as con:
            due = con.execute("SELECT id,request_id FROM jobs WHERE status='pending' AND deadline_at<=?", (now,)).fetchall()
            con.execute("UPDATE jobs SET status='expired', error='deadline_exceeded' WHERE status='pending' AND deadline_at<=?", (now,))
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
