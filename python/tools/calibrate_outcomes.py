#!/usr/bin/env python3
"""Fit and validate a shadow-only chronological PO3 calibration artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_pipeline import run_shadow_calibration, write_calibration_reports
from governance_contracts import audit_trade_records
from tools.ledger_io import read_records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("calibration_output"))
    parser.add_argument("--engine-version", required=True)
    parser.add_argument("--decision-schema-version", required=True)
    parser.add_argument("--code-commit", default="unknown")
    parser.add_argument("--min-clean-sample", type=int, default=120)
    args = parser.parse_args()
    audited, ledger_report = audit_trade_records(read_records(args.input))
    artifact = run_shadow_calibration(
        audited,
        ledger_status=str(ledger_report.get("global_status")),
        engine_version=args.engine_version,
        decision_schema_version=args.decision_schema_version,
        code_commit=args.code_commit,
        min_clean_sample=max(20, args.min_clean_sample),
    )
    write_calibration_reports(
        artifact,
        args.output_dir / "calibration_report.json",
        args.output_dir / "calibration_report.md",
    )
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
