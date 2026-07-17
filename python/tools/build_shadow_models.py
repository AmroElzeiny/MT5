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
    build_entry_and_management_shadow_artifacts,
    build_hierarchical_outcome_artifact,
    compare_shadow_decision_groups,
    consolidate_shadow_event_rows,
    merge_completed_and_shadow_records,
    strict_json_loads,
    utc_now,
)


def load_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    if not path.is_file():
        return rows, invalid
    for line in path.read_text(encoding="utf-8-sig", errors="strict").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(dict(strict_json_loads(line)))
        except Exception:
            invalid += 1
    return rows, invalid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="logs/completed_ai_trades.jsonl")
    parser.add_argument("--shadow-file", default="logs/shadow_candidates.jsonl")
    parser.add_argument("--output-dir", default="data/shadow_models")
    parser.add_argument("--min-clean-sample", type=int, default=80)
    args = parser.parse_args()
    root = ROOT
    source = Path(args.file)
    if not source.is_absolute():
        source = root / source
    shadow_source = Path(args.shadow_file)
    if not shadow_source.is_absolute():
        shadow_source = root / shadow_source
    output = Path(args.output_dir)
    if not output.is_absolute():
        output = root / output
    output.mkdir(parents=True, exist_ok=True)
    completed_rows, completed_invalid = load_rows(source)
    shadow_events, shadow_invalid_json = load_rows(shadow_source)
    shadow_outcomes, shadow_contract_rejected = consolidate_shadow_event_rows(shadow_events)
    rows, completed_shadow_join_rejected = merge_completed_and_shadow_records(completed_rows, shadow_outcomes)
    hierarchy = build_hierarchical_outcome_artifact(rows, min_clean_sample=max(1, args.min_clean_sample))
    separated = build_entry_and_management_shadow_artifacts(rows, min_clean_sample=max(1, args.min_clean_sample))
    shadow_comparison = compare_shadow_decision_groups(shadow_outcomes)
    artifacts = {
        "hierarchical_outcome_model.json": hierarchy,
        "entry_model_shadow.json": separated["entry_model"],
        "management_model_shadow.json": separated["management_model"],
        "shadow_decision_comparison.json": {
            "schema_version": "20260717_shadow_decision_comparison_v2",
            "generated_at": utc_now(),
            "trading_authority": False,
            "comparison": shadow_comparison,
        },
    }
    for name, payload in artifacts.items():
        atomic_write_json(output / name, payload)
    summary = {
        "schema_version": "20260717_shadow_model_build_summary_v1",
        "generated_at": utc_now(),
        "completed_source": str(source),
        "shadow_source": str(shadow_source),
        "input_rows": len(rows),
        "completed_rows": len(completed_rows),
        "shadow_event_rows": len(shadow_events),
        "resolved_shadow_outcomes": len(shadow_outcomes),
        "completed_invalid_rows": completed_invalid,
        "shadow_invalid_json_rows": shadow_invalid_json,
        "shadow_contract_rejected_rows": len(shadow_contract_rejected),
        "completed_shadow_join_rejected_rows": len(completed_shadow_join_rejected),
        "hierarchical_status": hierarchy["status"],
        "entry_status": separated["entry_model"]["artifact"]["status"],
        "management_status": separated["management_model"]["status"],
        "trading_authority": False,
        "outputs": {name: str(output / name) for name in artifacts},
        "shadow_contract_rejections": shadow_contract_rejected,
        "completed_shadow_join_rejections": completed_shadow_join_rejected,
    }
    atomic_write_json(output / "build_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
