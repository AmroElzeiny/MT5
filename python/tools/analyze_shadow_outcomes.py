from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from architecture_contracts import (
    atomic_write_json,
    compare_shadow_decision_groups,
    consolidate_shadow_event_rows,
    strict_json_loads,
    utc_now,
)


def consolidate(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not path.is_file():
        return rows, [{"line": 0, "reason": "shadow_ledger_missing"}]
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="strict").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = dict(strict_json_loads(line))
        except Exception as exc:
            rejected.append({"line": line_number, "reason": f"invalid_json:{exc}"})
            continue
        row["_source_line"] = line_number
        rows.append(row)
    outcomes, contract_rejected = consolidate_shadow_event_rows(rows)
    rejected.extend(contract_rejected)
    return outcomes, rejected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="logs/shadow_candidates.jsonl")
    parser.add_argument("--output", default="data/shadow_outcome_analysis.json")
    args = parser.parse_args()
    root = ROOT
    source = Path(args.file)
    if not source.is_absolute():
        source = root / source
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    outcomes, rejected = consolidate(source)
    report = {
        "schema_version": "20260717_shadow_outcome_analysis_v1",
        "generated_at": utc_now(),
        "source": str(source),
        "resolved_outcome_count": len(outcomes),
        "invalid_row_count": len(rejected),
        "trading_authority": False,
        "comparison": compare_shadow_decision_groups(outcomes),
        "invalid_rows": rejected,
    }
    atomic_write_json(output, report)
    print(json.dumps({"output": str(output), "resolved_outcome_count": len(outcomes), "invalid_row_count": len(rejected)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
