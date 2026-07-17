#!/usr/bin/env python3
"""Verify a real demo broker fill capture against the exact identity contract.

This utility does not place orders. Export the broker-produced order, entry
deal, and current position fields after a controlled market or pending fill,
then pass that JSON here. A synthetic fixture can exercise the code path but
is not operational broker proof.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision_integrity import validate_broker_execution_identity


CAPTURE_ORIGIN = "MT5_DEMO_BROKER_HISTORY"
ORDER_KINDS = frozenset({"market", "pending"})


def verify_capture(payload: Mapping[str, Any]) -> dict[str, Any]:
    order_kind = str(payload.get("order_kind") or "").strip().lower()
    capture_origin = str(payload.get("capture_origin") or "").strip().upper()
    if order_kind not in ORDER_KINDS:
        return {"verified": False, "operational_proof": False, "reason": "invalid_order_kind"}
    if capture_origin != CAPTURE_ORIGIN:
        return {"verified": False, "operational_proof": False, "reason": "not_mt5_demo_broker_capture"}
    expected = payload.get("expected")
    order = payload.get("order")
    deal = payload.get("deal")
    positions = payload.get("positions")
    if not isinstance(expected, Mapping) or not isinstance(order, Mapping) or not isinstance(deal, Mapping):
        return {"verified": False, "operational_proof": False, "reason": "capture_schema_incomplete"}
    if not isinstance(positions, list) or not all(isinstance(item, Mapping) for item in positions):
        return {"verified": False, "operational_proof": False, "reason": "capture_positions_invalid"}
    result = validate_broker_execution_identity(
        expected,
        order,
        deal,
        positions,
        account_mode=str(payload.get("account_mode") or ""),
        volume_tolerance=float(payload.get("volume_tolerance") or 1e-8),
        open_time_tolerance_sec=float(payload.get("open_time_tolerance_sec") or 10.0),
        existing_managed_same_symbol_positions=int(payload.get("existing_managed_same_symbol_positions") or 0),
        netting_virtual_subposition_ledger=bool(payload.get("netting_virtual_subposition_ledger", False)),
    )
    return {
        "verified": result.verified,
        "operational_proof": result.verified,
        "reason": result.reason,
        "order_kind": order_kind,
        "position_ticket": result.position_ticket,
        "position_identifier": result.position_identifier,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.capture.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise SystemExit("capture_root_must_be_object")
    result = verify_capture(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
