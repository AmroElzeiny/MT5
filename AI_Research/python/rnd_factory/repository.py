from __future__ import annotations
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import FactoryConfig
from .utils import nested_get, read_json, safe_float, sha256_file


class MT5RepositoryAdapter:
    """Read-only adapter over the existing MT5 repo and PO3 bus artifacts."""
    def __init__(self, config: FactoryConfig):
        self.config = config

    @property
    def bus_path(self) -> Path:
        base = self.config.common_files_dir
        return base if base.name.lower() == self.config.bus_root.lower() else base / self.config.bus_root

    def authoritative_sources(self) -> dict[str, str]:
        return {
            "trade_memory": str(self.config.trade_memory_path),
            "bus": str(self.bus_path),
            "repo": str(self.config.repo_root),
        }

    def load_completed_trades(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self._load_trade_memory(limit=limit)
        if rows:
            return rows[-limit:] if limit else rows
        rows = self._discover_trade_ledgers(limit=limit)
        return rows[-limit:] if limit else rows

    def _load_trade_memory(self, *, limit: int | None) -> list[dict[str, Any]]:
        path = self.config.trade_memory_path
        if not path.is_file():
            return []
        try:
            db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
            db.row_factory = sqlite3.Row
            query = "SELECT * FROM completed_memory ORDER BY resolved_at ASC"
            if limit:
                query = "SELECT * FROM completed_memory ORDER BY resolved_at DESC LIMIT ?"
                raw = db.execute(query, (int(limit),)).fetchall()[::-1]
            else:
                raw = db.execute(query).fetchall()
            db.close()
        except sqlite3.Error:
            return []
        out: list[dict[str, Any]] = []
        for row in raw:
            item = dict(row)
            try:
                payload = json.loads(item.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {"_malformed_payload": True}
            merged = {**item, **payload}
            merged["_source"] = str(path)
            out.append(merged)
        return out

    def _discover_trade_ledgers(self, *, limit: int | None) -> list[dict[str, Any]]:
        roots = [self.bus_path, self.config.python_root / "data", self.config.python_root / "logs"]
        candidates: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for pattern in ("*trade*.jsonl", "*trade*.ndjson", "*ledger*.jsonl", "*result*.jsonl", "*trade*.csv", "*ledger*.csv"):
                candidates.extend(p for p in root.rglob(pattern) if p.is_file() and p.stat().st_size <= self.config.max_scan_file_bytes)
        candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime_ns)
        out: list[dict[str, Any]] = []
        for path in candidates:
            try:
                rows = self._read_tabular(path)
            except Exception:
                continue
            for row in rows:
                if self._looks_like_trade(row):
                    row["_source"] = str(path)
                    out.append(row)
                    if limit and len(out) > limit * 4:
                        out = out[-limit * 2:]
        return out[-limit:] if limit else out

    @staticmethod
    def _read_tabular(path: Path) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
                return [dict(row) for row in csv.DictReader(f)]
        rows = []
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return rows

    @staticmethod
    def _looks_like_trade(row: Mapping[str, Any]) -> bool:
        keys = {str(k).lower() for k in row}
        return bool(keys & {"trade_key", "full_close_r", "net_realized_r", "mfe_r", "mae_r", "entry_price", "candidate_hash"})

    def find_trade(self, trade_key: str) -> dict[str, Any] | None:
        if not trade_key:
            return None
        for row in reversed(self.load_completed_trades()):
            key = str(nested_get(row, "trade_key", "resolved_outcome.trade_key") or "")
            if key == trade_key:
                return row
        return None

    def comparable_trades(self, target: Mapping[str, Any], rows: Iterable[Mapping[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        dimensions = (
            ("setup_taxonomy", 4), ("setup_family", 4), ("entry_branch", 3),
            ("session_code", 2), ("killzone_code", 1), ("regime_profile", 2),
            ("direction", 1), ("asset_class", 1),
        )
        scored: list[tuple[int, dict[str, Any]]] = []
        target_key = str(target.get("trade_key") or "")
        for row0 in rows:
            row = dict(row0)
            if target_key and str(row.get("trade_key") or "") == target_key:
                continue
            score = 0
            for field, weight in dimensions:
                a = str(nested_get(target, field) or "")
                b = str(nested_get(row, field) or "")
                if a and b and a == b:
                    score += weight
            if score > 0:
                row["_similarity_score"] = score
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def evidence_identity(self, paths: Iterable[Path]) -> list[dict[str, Any]]:
        out = []
        for path in paths:
            if path.is_file():
                out.append({"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size})
        return out


def outcome_r(row: Mapping[str, Any]) -> float | None:
    return safe_float(nested_get(row, "resolved_outcome.net_realized_r", "full_close_r", "net_realized_r", "realized_r", "pnl_r"))

def mfe_r(row: Mapping[str, Any]) -> float | None:
    return safe_float(nested_get(row, "resolved_outcome.mfe_r", "mfe_r"))

def mae_r(row: Mapping[str, Any]) -> float | None:
    return safe_float(nested_get(row, "resolved_outcome.mae_r", "mae_r"))

def execution_cost(row: Mapping[str, Any]) -> float | None:
    return safe_float(nested_get(row, "resolved_outcome.execution_cost", "actual_realized_cost", "execution_cost"))
