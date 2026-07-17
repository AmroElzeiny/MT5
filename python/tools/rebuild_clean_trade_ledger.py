#!/usr/bin/env python3
"""Rebuild a clean analytics ledger without guessing missing identities."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance_contracts import LedgerIntegrityStatus, audit_trade_records
from tools.ledger_io import read_records, render_integrity_markdown, write_json, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("ledger_rebuild_output"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    audited, report = audit_trade_records(read_records(args.input))
    clean = [row for row in audited if row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value]
    rejected = [row for row in audited if row.get("ledger_integrity_status") != LedgerIntegrityStatus.CLEAN.value]
    rebuild = {
        **report,
        "rebuild_policy": "exact_identity_and_complete_deals_only_no_inference",
        "clean_output_records": len(clean),
        "rejected_output_records": len(rejected),
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        write_json(args.output_dir / "repaired_system_trade_history.json", clean)
        write_jsonl(args.output_dir / "clean_trade_ledger.jsonl", clean)
        write_json(args.output_dir / "rejected_deals.json", rejected)
        write_json(args.output_dir / "ledger_integrity_report.json", rebuild)
        (args.output_dir / "ledger_integrity_report.md").write_text(
            render_integrity_markdown(rebuild), encoding="utf-8"
        )
    print(json.dumps(rebuild, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
