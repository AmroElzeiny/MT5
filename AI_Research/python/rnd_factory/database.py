from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from .constants import DB_SCHEMA_VERSION, HYPOTHESIS_STATUSES
from .utils import canonical_json, utc_now


class ResearchDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=10.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init(self) -> None:
        with self._lock, self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_runs(
                    research_run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    question TEXT NOT NULL,
                    run_mode TEXT NOT NULL,
                    ai_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    result_json TEXT,
                    report_json_path TEXT,
                    report_md_path TEXT,
                    source_commit TEXT,
                    config_fingerprint TEXT
                );
                CREATE TABLE IF NOT EXISTS observations(
                    observation_id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(research_run_id) REFERENCES research_runs(research_run_id)
                );
                CREATE TABLE IF NOT EXISTS hypotheses(
                    hypothesis_id TEXT PRIMARY KEY,
                    source_research_run TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    subsystem TEXT NOT NULL,
                    mechanism TEXT NOT NULL,
                    evidence_for_json TEXT NOT NULL,
                    evidence_against_json TEXT NOT NULL,
                    sample_size INTEGER NOT NULL,
                    affected_cohorts_json TEXT NOT NULL,
                    expected_improvement TEXT NOT NULL,
                    risks_json TEXT NOT NULL,
                    required_experiment TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parent_hypothesis_id TEXT,
                    identity_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments(
                    experiment_id TEXT PRIMARY KEY,
                    hypothesis_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    result_json TEXT,
                    FOREIGN KEY(hypothesis_id) REFERENCES hypotheses(hypothesis_id)
                );
                CREATE TABLE IF NOT EXISTS ai_calls(
                    call_id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    hypothesis_id TEXT,
                    created_at TEXT NOT NULL,
                    role TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    latency_sec REAL NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY(research_run_id) REFERENCES research_runs(research_run_id)
                );
                CREATE TABLE IF NOT EXISTS inspected_periods(
                    period_key TEXT PRIMARY KEY,
                    first_research_run TEXT NOT NULL,
                    first_inspected_at TEXT NOT NULL,
                    purpose TEXT NOT NULL
                );
                """
            )
            db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)", (str(DB_SCHEMA_VERSION),))

    def start_run(self, *, run_id: str, question: str, run_mode: str, ai_mode: str, scope: Mapping[str, Any], source_commit: str, config_fingerprint: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO research_runs(research_run_id,created_at,question,run_mode,ai_mode,status,scope_json,source_commit,config_fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, utc_now(), question, run_mode, ai_mode, "running", canonical_json(scope), source_commit, config_fingerprint),
            )

    def finish_run(self, run_id: str, result: Mapping[str, Any], *, json_path: str, md_path: str, status: str = "completed") -> None:
        with self._db() as db:
            db.execute(
                "UPDATE research_runs SET completed_at=?, status=?, result_json=?, report_json_path=?, report_md_path=? WHERE research_run_id=?",
                (utc_now(), status, canonical_json(result), json_path, md_path, run_id),
            )

    def add_observation(self, observation_id: str, run_id: str, category: str, severity: str, payload: Mapping[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO observations(observation_id,research_run_id,created_at,category,severity,payload_json) VALUES(?,?,?,?,?,?)",
                (observation_id, run_id, utc_now(), category, severity, canonical_json(payload)),
            )

    def create_hypothesis(self, row: Mapping[str, Any]) -> None:
        status = str(row.get("status") or "proposed")
        if status not in HYPOTHESIS_STATUSES:
            raise ValueError("invalid_hypothesis_status")
        with self._db() as db:
            db.execute(
                """INSERT INTO hypotheses(
                    hypothesis_id,source_research_run,created_at,updated_at,title,statement,subsystem,mechanism,
                    evidence_for_json,evidence_against_json,sample_size,affected_cohorts_json,expected_improvement,
                    risks_json,required_experiment,status,parent_hypothesis_id,identity_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["hypothesis_id"], row["source_research_run"], row.get("created_at") or utc_now(), utc_now(),
                    row["title"], row["statement"], row["subsystem"], row["mechanism"],
                    canonical_json(row.get("evidence_for", [])), canonical_json(row.get("evidence_against", [])), int(row.get("sample_size") or 0),
                    canonical_json(row.get("affected_cohorts", [])), row.get("expected_improvement", ""), canonical_json(row.get("risks", [])),
                    row.get("required_experiment", ""), status, row.get("parent_hypothesis_id"), canonical_json(row.get("identity", {})),
                ),
            )

    def set_hypothesis_status(self, hypothesis_id: str, status: str) -> None:
        if status not in HYPOTHESIS_STATUSES:
            raise ValueError("invalid_hypothesis_status")
        with self._db() as db:
            if db.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone() is None:
                raise KeyError("hypothesis_not_found")
            db.execute("UPDATE hypotheses SET status=?,updated_at=? WHERE hypothesis_id=?", (status, utc_now(), hypothesis_id))

    def hypotheses(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM hypotheses ORDER BY created_at DESC").fetchall()
        return [self._decode_hypothesis(dict(row)) for row in rows]

    def hypothesis(self, hypothesis_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
        return self._decode_hypothesis(dict(row)) if row else None

    @staticmethod
    def _decode_hypothesis(row: dict[str, Any]) -> dict[str, Any]:
        for src, dst in (("evidence_for_json","evidence_for"),("evidence_against_json","evidence_against"),("affected_cohorts_json","affected_cohorts"),("risks_json","risks"),("identity_json","identity")):
            row[dst] = json.loads(row.pop(src))
        return row

    def create_experiment(self, experiment_id: str, hypothesis_id: str, spec: Mapping[str, Any]) -> None:
        if self.hypothesis(hypothesis_id) is None:
            raise KeyError("hypothesis_not_found")
        with self._db() as db:
            db.execute(
                "INSERT INTO experiments(experiment_id,hypothesis_id,created_at,updated_at,status,spec_json) VALUES(?,?,?,?,?,?)",
                (experiment_id, hypothesis_id, utc_now(), utc_now(), "prepared", canonical_json(spec)),
            )

    def record_experiment_result(self, experiment_id: str, result: Mapping[str, Any], status: str) -> None:
        with self._db() as db:
            if db.execute("SELECT 1 FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone() is None:
                raise KeyError("experiment_not_found")
            db.execute("UPDATE experiments SET updated_at=?,status=?,result_json=? WHERE experiment_id=?", (utc_now(), status, canonical_json(result), experiment_id))

    def experiments(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
        out = []
        for row in rows:
            value = dict(row)
            value["spec"] = json.loads(value.pop("spec_json"))
            value["result"] = json.loads(value["result_json"]) if value.pop("result_json") else None
            out.append(value)
        return out

    def log_ai_call(self, row: Mapping[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO ai_calls(call_id,research_run_id,hypothesis_id,created_at,role,provider,model,input_tokens,cached_input_tokens,output_tokens,estimated_cost_usd,latency_sec,status,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["call_id"], row["research_run_id"], row.get("hypothesis_id"), utc_now(), row["role"], row["provider"], row["model"],
                    int(row.get("input_tokens") or 0), int(row.get("cached_input_tokens") or 0), int(row.get("output_tokens") or 0),
                    float(row.get("estimated_cost_usd") or 0.0), float(row.get("latency_sec") or 0.0), row.get("status", "ok"), row.get("error"),
                ),
            )

    def period_inspected(self, key: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM inspected_periods WHERE period_key=?", (key,)).fetchone() is not None

    def mark_period_inspected(self, key: str, run_id: str, purpose: str) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO inspected_periods(period_key,first_research_run,first_inspected_at,purpose) VALUES(?,?,?,?)", (key, run_id, utc_now(), purpose))
