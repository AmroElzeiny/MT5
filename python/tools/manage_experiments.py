#!/usr/bin/env python3
"""Manage the append-only PO3 experiment and holdout registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiment_registry import ExperimentRegistry


def _load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("experiment_payload_must_be_object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=Path("data/experiment_registry.jsonl"))
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--spec", type=Path, required=True)
    inspect = sub.add_parser("inspect-period")
    inspect.add_argument("--experiment-id", required=True)
    inspect.add_argument("--period", type=Path, required=True)
    inspect.add_argument("--purpose", required=True)
    result = sub.add_parser("record-result")
    result.add_argument("--experiment-id", required=True)
    result.add_argument("--metrics", type=Path, required=True)
    result.add_argument("--result", required=True)
    result.add_argument("--decision", required=True)
    status = sub.add_parser("status")
    status.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    registry = ExperimentRegistry(args.registry)
    if args.command == "create":
        output = registry.create(_load_object(args.spec), repo_root=ROOT)
    elif args.command == "inspect-period":
        output = registry.mark_period_inspected(
            args.experiment_id, _load_object(args.period), purpose=args.purpose
        )
    elif args.command == "record-result":
        output = registry.record_result(
            args.experiment_id,
            _load_object(args.metrics),
            result=args.result,
            decision_taken=args.decision,
        )
    else:
        output = registry.get(args.experiment_id) or {"error": "experiment_not_found"}
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
