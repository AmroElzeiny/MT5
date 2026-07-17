"""Strict ledger input/output helpers shared by operator CLIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def read_json(path: Path) -> Any:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return json.loads(raw.decode("utf-16"))
    return json.loads(raw.decode("utf-8-sig"))


def read_records(source: Path) -> list[dict[str, Any]]:
    paths: list[Path]
    if source.is_dir():
        paths = sorted(source.glob("trade_result_*.json"))
        if not paths:
            paths = sorted(source.glob("*.jsonl"))
    else:
        paths = [source]
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise ValueError(f"ledger_row_not_object:{path.name}")
                records.append(dict(value))
            continue
        value = read_json(path)
        if isinstance(value, list):
            for row in value:
                if not isinstance(row, Mapping):
                    raise ValueError(f"ledger_row_not_object:{path.name}")
                records.append(dict(row))
        elif isinstance(value, Mapping):
            records.append(dict(value))
        else:
            raise ValueError(f"ledger_payload_not_object_or_array:{path.name}")
    return records


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str) + "\n")


def render_integrity_markdown(report: Mapping[str, Any]) -> str:
    reasons = report.get("reason_counts") if isinstance(report.get("reason_counts"), Mapping) else {}
    lines = [
        "# Trade ledger integrity audit",
        "",
        f"- Global status: `{report.get('global_status', 'unknown')}`",
        f"- Total records: {report.get('total_records', 0)}",
        f"- Clean: {report.get('clean', 0)}",
        f"- Suspicious: {report.get('suspicious', 0)}",
        f"- Quarantined: {report.get('quarantined', 0)}",
        f"- Unattributed: {report.get('unattributed', 0)}",
        f"- Unknown taxonomy combinations: {len(report.get('unknown_taxonomy_combinations') or [])}",
        f"- Equal-trade expectancy: {report.get('equal_trade_weighted_expectancy_r', 0)} R",
        f"- Risk-weighted expectancy: {report.get('risk_weighted_expectancy_r', 0)} R",
        "",
        "## Integrity reasons",
        "",
    ]
    lines.extend(f"- `{name}`: {count}" for name, count in sorted(reasons.items()))
    if not reasons:
        lines.append("- none")
    return "\n".join(lines) + "\n"
