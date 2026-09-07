"""Durable, provider-neutral historical setup memory and analogue retrieval."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence


TRADE_MEMORY_SCHEMA_VERSION = "20260718_trade_memory_v1"
RETRIEVAL_POLICY_VERSION = "20260718_hybrid_analogue_retrieval_v1"

_BOOTSTRAP_PRE_ENTRY_FIELDS = (
    "candidate_id",
    "candidate_hash",
    "symbol",
    "direction",
    "setup_code",
    "setup_family",
    "setup_class",
    "setup_taxonomy_enum",
    "setup_taxonomy_version",
    "entry_branch",
    "session",
    "killzone",
    "asset_class",
    "regime_profile",
    "entry_price",
    "sl",
    "tp1",
    "tp2",
    "rr2",
    "target_source",
    "target_model",
    "assessed_execution_fingerprint",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except Exception:
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _quality_clean(row: Mapping[str, Any]) -> tuple[bool, str]:
    if _text(row.get("ledger_integrity_status")).upper() != "CLEAN":
        return False, "ledger_not_clean"
    if row.get("execution_identity_verified") is not True:
        return False, "execution_identity_not_verified"
    if row.get("candidate_hash_match") is not True:
        return False, "candidate_hash_mismatch"
    if row.get("execution_fingerprint_match") is not True:
        return False, "execution_fingerprint_mismatch"
    if row.get("learning_eligible") is not True:
        return False, "learning_not_eligible"
    if not _text(row.get("candidate_hash")):
        return False, "candidate_hash_missing"
    if not _text(row.get("trade_key")):
        return False, "trade_key_missing"
    return True, "clean"


def _is_tester_bootstrap(row: Mapping[str, Any]) -> bool:
    return (
        _text(row.get("decision_quality_tier")) == "BOOTSTRAP_RULE_ONLY"
        and _text(row.get("decision_source")) == "bootstrap_rule_only"
        and _text(row.get("provider_mode")) == "TESTER_BOOTSTRAP_RULE_ONLY"
        and _text(row.get("workload_mode")) == "TESTER_AI_BOOTSTRAP_RULE_ONLY"
    )


def _read_completed_ledger_text(source: Path) -> str:
    """Decode ledgers written by either Python or MetaTrader FILE_TXT.

    MT5 writes Unicode text as UTF-16 with a BOM unless FILE_ANSI is selected,
    while Python-produced fixtures and migrated ledgers are UTF-8.  Detect the
    BOM instead of treating a valid terminal ledger as corrupt input.
    """

    payload = source.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16", errors="strict")
    return payload.decode("utf-8-sig", errors="strict")


def _bootstrap_pending_snapshot(completed: Mapping[str, Any]) -> dict[str, Any]:
    candidate = {
        key: completed.get(key)
        for key in _BOOTSTRAP_PRE_ENTRY_FIELDS
        if completed.get(key) is not None
    }
    candidate["bootstrap_authority"] = "mql_tester_deterministic_only"
    candidate["historical_outcome_fields_excluded"] = True
    candidate_hash = _text(completed.get("candidate_hash"))
    return {
        "memory_schema_version": TRADE_MEMORY_SCHEMA_VERSION,
        "request_id": "bootstrap:" + candidate_hash,
        "lineage_id": "bootstrap:" + candidate_hash,
        "candidate_hash": candidate_hash,
        "immutable_pre_entry_evidence": {"candidate": candidate},
        "provider_observability": {
            "provider_mode": "TESTER_BOOTSTRAP_RULE_ONLY",
            "provider_id": "mql_deterministic_engine",
        },
        "analyst_output": {},
        "critic_output": {},
        "adjudicator_output": {},
        "python_final_decision": {
            "decision_source": "bootstrap_rule_only",
            "python_authority": False,
            "mql_tester_authority": True,
        },
        "recorded_at": int(time.time()),
    }


@dataclass(frozen=True)
class RetrievalResult:
    state: str
    sample_count: int
    analogue_ids: tuple[str, ...]
    analogues: tuple[dict[str, Any], ...]
    retrieval_hash: str
    policy_version: str = RETRIEVAL_POLICY_VERSION


class TradeMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        """Commit or roll back and always release the Windows file handle."""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_decisions (
                    candidate_hash TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    lineage_id TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS completed_memory (
                    memory_id TEXT PRIMARY KEY,
                    trade_key TEXT NOT NULL UNIQUE,
                    candidate_hash TEXT NOT NULL,
                    lineage_id TEXT NOT NULL,
                    setup_taxonomy TEXT NOT NULL,
                    setup_family TEXT NOT NULL,
                    entry_branch TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    session_code TEXT NOT NULL,
                    killzone_code TEXT NOT NULL,
                    regime_profile TEXT NOT NULL,
                    data_quality_status TEXT NOT NULL,
                    resolved_at INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_taxonomy ON completed_memory(setup_taxonomy);
                CREATE INDEX IF NOT EXISTS idx_memory_family_branch ON completed_memory(setup_family, entry_branch);
                CREATE INDEX IF NOT EXISTS idx_memory_asset_session ON completed_memory(asset_class, session_code, killzone_code);
                CREATE TABLE IF NOT EXISTS quarantine (
                    quarantine_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingest_state (
                    source_path TEXT PRIMARY KEY,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )

    def record_pending_decision(
        self,
        *,
        request_id: str,
        lineage_id: str,
        candidate_hash: str,
        immutable_pre_entry_evidence: Mapping[str, Any],
        provider_observability: Mapping[str, Any],
        analyst_output: Mapping[str, Any],
        critic_output: Mapping[str, Any],
        adjudicator_output: Mapping[str, Any] | None,
        python_final_decision: Mapping[str, Any],
    ) -> bool:
        if not candidate_hash:
            raise ValueError("candidate_hash_required")
        row = {
            "memory_schema_version": TRADE_MEMORY_SCHEMA_VERSION,
            "request_id": request_id,
            "lineage_id": lineage_id,
            "candidate_hash": candidate_hash,
            "immutable_pre_entry_evidence": dict(immutable_pre_entry_evidence),
            "provider_observability": dict(provider_observability),
            "analyst_output": dict(analyst_output),
            "critic_output": dict(critic_output),
            "adjudicator_output": dict(adjudicator_output or {}),
            "python_final_decision": dict(python_final_decision),
            "recorded_at": int(time.time()),
        }
        encoded = _canonical_json(row)
        with self._lock, self._connection() as db:
            existing = db.execute(
                "SELECT payload_json FROM pending_decisions WHERE candidate_hash=?",
                (candidate_hash,),
            ).fetchone()
            if existing is not None:
                return existing[0] == encoded
            db.execute(
                "INSERT INTO pending_decisions(candidate_hash,request_id,lineage_id,recorded_at,schema_version,payload_json) VALUES(?,?,?,?,?,?)",
                (candidate_hash, request_id, lineage_id, row["recorded_at"], TRADE_MEMORY_SCHEMA_VERSION, encoded),
            )
        return True

    def ingest_completed_ledger(self, path: str | Path) -> dict[str, int]:
        source = Path(path)
        summary = {"seen": 0, "inserted": 0, "duplicate": 0, "quarantined": 0, "malformed": 0}
        if not source.is_file():
            return summary
        stat = source.stat()
        source_key = str(source.resolve())
        with self._lock, self._connection() as db:
            state = db.execute(
                "SELECT source_size,source_mtime_ns FROM ingest_state WHERE source_path=?",
                (source_key,),
            ).fetchone()
            if state == (stat.st_size, stat.st_mtime_ns):
                return summary

        for line_number, raw_line in enumerate(_read_completed_ledger_text(source).splitlines(), start=1):
            if not raw_line.strip():
                continue
            summary["seen"] += 1
            try:
                completed = json.loads(raw_line)
                if not isinstance(completed, dict):
                    raise ValueError("row_not_object")
            except Exception as exc:
                summary["malformed"] += 1
                self._quarantine(
                    f"{source_key}:{line_number}",
                    "malformed_completed_row:" + type(exc).__name__,
                    {"source": source_key, "line": line_number},
                )
                continue
            clean, reason = _quality_clean(completed)
            trade_key = _text(completed.get("trade_key")) or f"{source_key}:{line_number}"
            candidate_hash = _text(completed.get("candidate_hash"))
            if not clean:
                summary["quarantined"] += 1
                self._quarantine(trade_key, reason, completed)
                continue
            with self._lock, self._connection() as db:
                pending_row = db.execute(
                    "SELECT payload_json,lineage_id FROM pending_decisions WHERE candidate_hash=?",
                    (candidate_hash,),
                ).fetchone()
            if pending_row is None:
                if not _is_tester_bootstrap(completed):
                    summary["quarantined"] += 1
                    self._quarantine(trade_key, "immutable_pre_entry_snapshot_missing", completed)
                    continue
                pending = _bootstrap_pending_snapshot(completed)
                pending_lineage_id = _text(pending.get("lineage_id"))
            else:
                pending = json.loads(pending_row[0])
                pending_lineage_id = pending_row[1]
            memory = {
                "memory_schema_version": TRADE_MEMORY_SCHEMA_VERSION,
                "memory_id": _hash({"trade_key": trade_key, "candidate_hash": candidate_hash}),
                "request_id": _text(pending.get("request_id")),
                "trade_key": trade_key,
                "candidate_hash": candidate_hash,
                "lineage_id": pending_lineage_id,
                "immutable_pre_entry_evidence": pending.get("immutable_pre_entry_evidence") or {},
                "provider_observability": pending.get("provider_observability") or {},
                "analyst_output": pending.get("analyst_output") or {},
                "critic_output": pending.get("critic_output") or {},
                "adjudicator_output": pending.get("adjudicator_output") or {},
                "python_final_decision": pending.get("python_final_decision") or {},
                "executed_action": {
                    "entry": completed.get("entry_price"),
                    "sl": completed.get("sl"),
                    "tp1": completed.get("tp1"),
                    "tp2": completed.get("tp2"),
                    "initial_risk_r": completed.get("rr2"),
                    "volume": completed.get("volume"),
                },
                "resolved_outcome": {
                    "mfe_r": completed.get("mfe_r"),
                    "mae_r": completed.get("mae_r"),
                    "net_realized_r": completed.get("full_close_r"),
                    "net_realized_money": completed.get("full_close_pnl"),
                    "execution_cost": completed.get("actual_realized_cost"),
                    "exit_reason": completed.get("full_close_reason"),
                    "target_before_stop": completed.get("target_before_stop"),
                    "thesis_remained_valid": completed.get("thesis_remained_valid"),
                },
                "completed_trade": completed,
                "data_quality_status": "CLEAN",
                "resolved_at": int(time.time()),
            }
            inserted = self._insert_completed(memory)
            summary["inserted" if inserted else "duplicate"] += 1

        with self._lock, self._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO ingest_state(source_path,source_size,source_mtime_ns,updated_at) VALUES(?,?,?,?)",
                (source_key, stat.st_size, stat.st_mtime_ns, int(time.time())),
            )
        return summary

    def _insert_completed(self, memory: Mapping[str, Any]) -> bool:
        completed = memory.get("completed_trade") if isinstance(memory.get("completed_trade"), Mapping) else {}
        evidence = memory.get("immutable_pre_entry_evidence") if isinstance(memory.get("immutable_pre_entry_evidence"), Mapping) else {}
        candidate = evidence.get("candidate") if isinstance(evidence.get("candidate"), Mapping) else {}
        values = (
            _text(memory.get("memory_id")),
            _text(memory.get("trade_key")),
            _text(memory.get("candidate_hash")),
            _text(memory.get("lineage_id")),
            _text(completed.get("setup_taxonomy_enum") or candidate.get("setup_taxonomy_enum")),
            _text(completed.get("setup_family") or candidate.get("setup_family")),
            _text(completed.get("entry_branch") or candidate.get("entry_branch") or candidate.get("entry_model")),
            _text(completed.get("direction") or candidate.get("direction")),
            _text(completed.get("asset_class") or candidate.get("asset_class")),
            _text(completed.get("session") or candidate.get("session_code")),
            _text(completed.get("killzone") or candidate.get("killzone_code")),
            _text(completed.get("regime_profile") or candidate.get("regime_profile")),
            "CLEAN",
            int(memory.get("resolved_at") or time.time()),
            TRADE_MEMORY_SCHEMA_VERSION,
            _canonical_json(memory),
        )
        with self._lock, self._connection() as db:
            before = db.total_changes
            db.execute(
                "INSERT OR IGNORE INTO completed_memory(memory_id,trade_key,candidate_hash,lineage_id,setup_taxonomy,setup_family,entry_branch,direction,asset_class,session_code,killzone_code,regime_profile,data_quality_status,resolved_at,schema_version,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            return db.total_changes > before

    def _quarantine(self, source_id: str, reason: str, payload: Mapping[str, Any]) -> None:
        row = {
            "source_id": source_id,
            "reason": reason,
            "payload": dict(payload),
            "recorded_at": int(time.time()),
            "memory_schema_version": TRADE_MEMORY_SCHEMA_VERSION,
        }
        quarantine_id = _hash({"source_id": source_id, "reason": reason})
        with self._lock, self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO quarantine(quarantine_id,source_id,reason,recorded_at,schema_version,payload_json) VALUES(?,?,?,?,?,?)",
                (quarantine_id, source_id, reason, row["recorded_at"], TRADE_MEMORY_SCHEMA_VERSION, _canonical_json(row)),
            )

    def retrieve_analogues(
        self,
        candidate: Mapping[str, Any],
        *,
        request_id: str,
        lineage_id: str,
        top_k: int = 7,
        minimum_similarity: float = 0.45,
    ) -> RetrievalResult:
        taxonomy = _text(candidate.get("setup_taxonomy_enum"))
        family = _text(candidate.get("setup_family"))
        branch = _text(candidate.get("entry_branch") or candidate.get("entry_model"))
        with self._lock, self._connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM completed_memory WHERE data_quality_status='CLEAN' AND (setup_taxonomy=? OR setup_family=?) ORDER BY resolved_at DESC LIMIT 500",
                (taxonomy, family),
            ).fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for (encoded,) in rows:
            memory = json.loads(encoded)
            if _text(memory.get("lineage_id")) == lineage_id:
                continue
            if _text(memory.get("candidate_hash")) == _text(candidate.get("candidate_hash")):
                continue
            if _text(memory.get("request_id")) == request_id:
                continue
            completed = memory.get("completed_trade") if isinstance(memory.get("completed_trade"), Mapping) else {}
            score = 0.0
            weight = 0.0
            comparisons = (
                (taxonomy, _text(completed.get("setup_taxonomy_enum")), 4.0),
                (family, _text(completed.get("setup_family")), 2.5),
                (branch, _text(completed.get("entry_branch")), 1.5),
                (_text(candidate.get("direction")), _text(completed.get("direction")), 1.0),
                (_text(candidate.get("asset_class")), _text(completed.get("asset_class")), 1.5),
                (_text(candidate.get("session_code")), _text(completed.get("session")), 0.8),
                (_text(candidate.get("killzone_code")), _text(completed.get("killzone")), 0.6),
                (_text(candidate.get("regime_profile")), _text(completed.get("regime_profile")), 0.8),
                (_text(candidate.get("target_model")), _text(completed.get("target_model")), 0.8),
            )
            for current, historical, item_weight in comparisons:
                if current and historical:
                    weight += item_weight
                    if current.lower() == historical.lower():
                        score += item_weight
            numeric_pairs = (
                (candidate.get("effective_rr2"), completed.get("rr2"), 1.0, 2.0),
                (candidate.get("execution_cost_r"), completed.get("execution_cost_r"), 0.8, 0.25),
                (candidate.get("obstacle_distance_r"), completed.get("obstacle_distance_r"), 0.6, 2.0),
            )
            for current, historical, item_weight, scale in numeric_pairs:
                if current is None or historical is None:
                    continue
                weight += item_weight
                distance = abs(_finite(current) - _finite(historical)) / max(scale, 1e-9)
                score += item_weight * max(0.0, 1.0 - distance)
            similarity = score / weight if weight > 0 else 0.0
            if similarity < minimum_similarity:
                continue
            analogue = {
                "memory_id": memory.get("memory_id"),
                "trade_key": memory.get("trade_key"),
                "candidate_hash": memory.get("candidate_hash"),
                "setup_taxonomy_enum": completed.get("setup_taxonomy_enum"),
                "setup_family": completed.get("setup_family"),
                "entry_branch": completed.get("entry_branch"),
                "asset_class": completed.get("asset_class"),
                "session": completed.get("session"),
                "killzone": completed.get("killzone"),
                "data_quality_status": "CLEAN",
                "similarity_score": round(similarity, 6),
                "historical_outcome": {
                    "net_realized_r": completed.get("full_close_r"),
                    "mfe_r": completed.get("mfe_r"),
                    "mae_r": completed.get("mae_r"),
                    "target_before_stop": completed.get("target_before_stop"),
                    "exit_reason": completed.get("full_close_reason"),
                },
            }
            scored.append((similarity, analogue))
        scored.sort(key=lambda item: (-item[0], _text(item[1].get("memory_id"))))
        selected = [item[1] for item in scored[: max(0, min(10, int(top_k)))]]
        state = "INSUFFICIENT_SAMPLE" if not selected else "AVAILABLE"
        ids = tuple(_text(item.get("memory_id")) for item in selected)
        contract = {
            "policy_version": RETRIEVAL_POLICY_VERSION,
            "request_id": request_id,
            "candidate_hash": _text(candidate.get("candidate_hash")),
            "analogue_ids": ids,
            "similarities": [item["similarity_score"] for item in selected],
            "state": state,
        }
        return RetrievalResult(state, len(selected), ids, tuple(selected), _hash(contract))
