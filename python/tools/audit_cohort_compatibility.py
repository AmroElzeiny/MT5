from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from architecture_contracts import COHORT_FIELDS, cohort_metadata, strict_json_loads, utc_now


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not path.is_file():
        return records, [{"line": 0, "reason": "input_file_missing"}]
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="strict").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = strict_json_loads(line)
        except Exception as exc:
            rejected.append({"line": line_number, "reason": f"invalid_json:{exc}"})
            continue
        records.append(dict(row))
    return records, rejected


def audit(records: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> dict[str, Any]:
    cohorts: dict[str, dict[str, Any]] = {}
    incomplete = 0
    for row in records:
        metadata = cohort_metadata(row)
        key = metadata["cohort_id"] if metadata["cohort_complete"] else "INCOMPLETE"
        if not metadata["cohort_complete"]:
            incomplete += 1
        bucket = cohorts.setdefault(
            key,
            {
                "cohort_id": key,
                "count": 0,
                "complete": key != "INCOMPLETE",
                "missing_fields": {},
            },
        )
        bucket["count"] += 1
        for field in metadata["missing_cohort_fields"]:
            bucket["missing_fields"][field] = bucket["missing_fields"].get(field, 0) + 1
    complete_ids = [key for key in cohorts if key != "INCOMPLETE"]
    status = "COMPATIBLE" if records and not rejected and incomplete == 0 and len(complete_ids) == 1 else "BLOCKED"
    reasons: list[str] = []
    if rejected:
        reasons.append("invalid_json_rows")
    if not records:
        reasons.append("no_records")
    if incomplete:
        reasons.append("cohort_metadata_incomplete")
    if len(complete_ids) > 1:
        reasons.append("mixed_version_cohorts")
    return {
        "schema_version": "20260717_cohort_compatibility_audit_v1",
        "generated_at": utc_now(),
        "status": status,
        "analysis_authority": status == "COMPATIBLE",
        "record_count": len(records),
        "invalid_row_count": len(rejected),
        "incomplete_record_count": incomplete,
        "complete_cohort_count": len(complete_ids),
        "required_fields": list(COHORT_FIELDS),
        "block_reasons": reasons,
        "cohorts": sorted(cohorts.values(), key=lambda row: row["cohort_id"]),
        "invalid_rows": rejected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="logs/completed_ai_trades.jsonl")
    parser.add_argument("--output", default="data/cohort_compatibility_audit.json")
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = Path(args.file)
    if not source.is_absolute():
        source = root / source
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    records, rejected = load_jsonl(source)
    report = audit(records, rejected)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": report["status"], "block_reasons": report["block_reasons"]}, sort_keys=True))
    return 1 if args.require_compatible and report["status"] != "COMPATIBLE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
