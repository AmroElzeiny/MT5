#!/usr/bin/env python3
"""Audit the PO3 ledger through the central fail-closed integrity gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance_contracts import audit_trade_records
from tools.ledger_io import read_records, render_integrity_markdown, write_json, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="Trade-result directory or JSON/JSONL ledger.")
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    audited, report = audit_trade_records(read_records(args.input))
    if not args.dry_run:
        write_jsonl(args.output_dir / "audited_trade_ledger.jsonl", audited)
        write_json(args.output_dir / "trade_ledger_integrity_report.json", report)
        write_json(args.output_dir / "ledger_integrity_report.json", report)
        (args.output_dir / "ledger_integrity_report.md").write_text(
            render_integrity_markdown(report), encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if args.require_clean and report.get("global_status") != "CLEAN" else 0


if __name__ == "__main__":
    raise SystemExit(main())
