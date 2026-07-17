#!/usr/bin/env python3
"""Audit and rebuild PO3 trade_result records without identity inference.

Legacy helper functions remain readable for forensic compatibility, but the
authoritative repair path delegates to the central fail-closed ledger gate.
Missing broker position identities are never recovered from comments or keys.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance_contracts import LedgerIntegrityStatus, audit_trade_records


EMPTY_ID_VALUES = {"", "0", "0.0", "none", "null", "nan"}
TRADE_ID_FIELDS = ("position_id", "deal_position_id", "trade_id")
PRICE_FIELDS = ("planned_entry", "filled_entry", "planned_sl", "planned_tp2")


def _default_logs_dir() -> Path:
    return (
        Path.home()
        / "AppData"
        / "Roaming"
        / "MetaQuotes"
        / "Terminal"
        / "Common"
        / "Files"
        / "PO3_AI_BUS"
        / "logs"
        / "trade_results"
    )


def _read_json_any_encoding(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return json.loads(raw.decode("utf-16"))
    if raw.startswith(b"\xef\xbb\xbf"):
        return json.loads(raw.decode("utf-8-sig"))
    return json.loads(raw.decode("utf-8"))


def _valid_identity(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in EMPTY_ID_VALUES else text


def _record_trade_id(record: Dict[str, Any]) -> str:
    for field in TRADE_ID_FIELDS:
        identity = _valid_identity(record.get(field))
        if identity:
            return identity
    return ""


def _float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _load_meta_identity_index(logs_dir: Path) -> Dict[str, Dict[str, Any]]:
    meta_root = logs_dir.parent
    index: Dict[str, Dict[str, Any]] = {}
    if not meta_root.exists():
        return index
    for path in sorted(meta_root.glob("trade_key_*.json")):
        try:
            meta = _read_json_any_encoding(path)
        except Exception:
            continue
        trade_key = str(meta.get("trade_key") or "").strip()
        broker_comment = str(meta.get("broker_comment") or "").strip()
        identity = _record_trade_id(meta)
        compact = {
            "position_id": identity,
            "trade_key": trade_key,
            "broker_comment": broker_comment,
            "symbol": str(meta.get("symbol") or "").strip(),
            "magic_number": meta.get("magic_number"),
            "account_login": meta.get("account_login"),
            "account_server": meta.get("account_server"),
        }
        for key in (trade_key, broker_comment):
            if key:
                index.setdefault(key, compact)
    return index


def _exit_events(record: Dict[str, Any]) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    for key in ("partials", "partial_closes", "exit_deals", "deals", "fills"):
        for item in _as_list(record.get(key)):
            if isinstance(item, dict):
                event = dict(item)
                event["_source"] = key
                events.append(event)
    for key in ("exit_price", "close_price", "tp1_price", "tp2_price", "sl_price"):
        price = _float(record.get(key), 0.0)
        if price > 0.0:
            events.append({"price": price, "_source": key})
    return events


def _record_price_anchor(record: Dict[str, Any]) -> float:
    for field in ("filled_entry", "planned_entry"):
        value = _float(record.get(field), 0.0)
        if value > 0.0:
            return value
    return 0.0


def _price_scale_mismatch(record: Dict[str, Any], event: Dict[str, Any]) -> bool:
    anchor = _record_price_anchor(record)
    price = _float(event.get("price", event.get("exit_price", event.get("close_price"))), 0.0)
    if anchor <= 0.0 or price <= 0.0:
        return False
    lo = anchor * 0.25
    hi = anchor * 4.0
    return price < lo or price > hi


def _account_value(record: Dict[str, Any], key: str) -> str:
    return str(record.get(key) or record.get(f"account_{key}") or "").strip()


def _audit_record(
    record: Dict[str, Any],
    identities_by_key: Dict[str, Dict[str, Any]],
    position_owner: Dict[str, tuple[str, str]],
) -> Tuple[Dict[str, Any], list[str], list[str]]:
    fixed = dict(record)
    repairs: list[str] = []
    reasons: list[str] = []

    trade_key = str(fixed.get("trade_key") or "").strip()
    broker_comment = str(fixed.get("broker_comment") or "").strip()
    if not trade_key:
        reasons.append("missing_trade_key")

    if not _record_trade_id(fixed):
        for key_field in ("trade_key", "broker_comment", "source_deal_comment"):
            meta = identities_by_key.get(str(fixed.get(key_field) or "").strip(), {})
            identity = _valid_identity(meta.get("position_id"))
            if identity:
                fixed["position_id"] = identity
                repairs.append(f"position_id_from_{key_field}")
                break
    trade_id = _record_trade_id(fixed)
    if not trade_id:
        reasons.append("missing_trade_id")

    if "realized_pnl" not in fixed or fixed.get("realized_pnl") in (None, ""):
        reasons.append("missing_realized_pnl")
    elif _float(fixed.get("realized_pnl")) == 0.0:
        reasons.append("zero_realized_pnl")

    symbol = str(fixed.get("symbol") or "").strip().upper()
    meta = identities_by_key.get(trade_key) or identities_by_key.get(broker_comment) or {}
    meta_symbol = str(meta.get("symbol") or "").strip().upper()
    if symbol and meta_symbol and symbol != meta_symbol:
        reasons.append("symbol_mismatch")

    magic = str(fixed.get("magic_number") or fixed.get("magic") or "").strip()
    meta_magic = str(meta.get("magic_number") or "").strip()
    if magic and meta_magic and magic != meta_magic:
        reasons.append("magic_mismatch")

    meta_comment = str(meta.get("broker_comment") or "").strip()
    source_comment = str(fixed.get("source_deal_comment") or fixed.get("deal_comment") or "").strip()
    if source_comment and broker_comment and source_comment != broker_comment and not broker_comment.startswith(source_comment):
        reasons.append("comment_mismatch")
    if meta_comment and broker_comment and meta_comment != broker_comment:
        reasons.append("comment_mismatch")

    record_login = _account_value(fixed, "login")
    record_server = _account_value(fixed, "server")
    meta_login = str(meta.get("account_login") or "").strip()
    meta_server = str(meta.get("account_server") or "").strip()
    if record_login and meta_login and record_login != meta_login:
        reasons.append("account_context_mismatch")
    if record_server and meta_server and record_server != meta_server:
        reasons.append("account_context_mismatch")

    if trade_id:
        owner = (trade_key, symbol)
        prior = position_owner.get(trade_id)
        if prior and prior != owner:
            reasons.append("position_id_reused")
        else:
            position_owner.setdefault(trade_id, owner)

    for field in ("planned_entry", "planned_sl", "filled_entry"):
        if _float(fixed.get(field)) <= 0.0:
            reasons.append(f"missing_{field}")

    is_buy = bool(fixed.get("is_buy")) or str(fixed.get("direction") or "").lower() == "buy"
    entry = _float(fixed.get("planned_entry"))
    sl = _float(fixed.get("planned_sl"))
    tp2 = _float(fixed.get("planned_tp2"))
    if entry > 0.0 and sl > 0.0:
        if is_buy and sl >= entry:
            reasons.append("buy_stop_not_below_entry")
        if not is_buy and sl <= entry:
            reasons.append("sell_stop_not_above_entry")
    if entry > 0.0 and tp2 > 0.0:
        if is_buy and tp2 <= entry:
            reasons.append("buy_target_not_above_entry")
        if not is_buy and tp2 >= entry:
            reasons.append("sell_target_not_below_entry")

    initial_volume = _float(fixed.get("initial_volume") or fixed.get("volume"), 0.0)
    matched_volume = 0.0
    for event in _exit_events(fixed):
        event_symbol = str(event.get("symbol") or event.get("deal_symbol") or "").strip().upper()
        if event_symbol and symbol and event_symbol != symbol:
            reasons.append("symbol_mismatch")
        if _price_scale_mismatch(fixed, event):
            reasons.append("price_scale_mismatch")
        event_magic = str(event.get("magic_number") or event.get("magic") or "").strip()
        if event_magic and magic and event_magic != magic:
            reasons.append("magic_mismatch")
        event_comment = str(event.get("comment") or event.get("deal_comment") or "").strip()
        if event_comment and broker_comment and event_comment != broker_comment and not broker_comment.startswith(event_comment):
            reasons.append("comment_mismatch")
        event_login = str(event.get("account_login") or "").strip()
        event_server = str(event.get("account_server") or "").strip()
        if event_login and record_login and event_login != record_login:
            reasons.append("account_context_mismatch")
        if event_server and record_server and event_server != record_server:
            reasons.append("account_context_mismatch")
        matched_volume += _float(event.get("volume") or event.get("lots"), 0.0)
    if initial_volume > 0.0 and matched_volume > initial_volume * 1.01:
        reasons.append("volume_overmatched")

    if not trade_key and not broker_comment:
        reasons.append("legacy_nonexclusive_history")

    reasons = sorted(set(reasons))
    fixed["data_integrity_status"] = "repaired" if repairs and not reasons else ("suspicious" if reasons else "clean")
    fixed["data_integrity_reasons"] = reasons
    if repairs:
        fixed["data_integrity_repairs"] = repairs
    return fixed, reasons, repairs


def _write_outputs(out_dir: Path, clean_records: list[Dict[str, Any]], rejected: list[Dict[str, Any]], report: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "repaired_system_trade_history.json").write_text(
        json.dumps(clean_records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "rejected_deals.json").write_text(
        json.dumps(rejected, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "ledger_integrity_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    top_reasons = "\n".join(f"- {k}: {v}" for k, v in report.get("reason_counts", {}).items()) or "- none"
    md = (
        "# Ledger integrity report\n\n"
        f"- Logs dir: `{report.get('logs_dir')}`\n"
        f"- Files scanned: {report.get('files_scanned')}\n"
        f"- Clean/repaired records: {report.get('clean_records')}\n"
        f"- Rejected/suspicious records: {report.get('rejected_records')}\n"
        f"- Meta identities loaded: {report.get('meta_identities_loaded')}\n\n"
        "## Rejection reasons\n\n"
        f"{top_reasons}\n"
    )
    (out_dir / "ledger_integrity_report.md").write_text(md, encoding="utf-8")


def run_repair(logs_dir: Path, out_dir: Path) -> Dict[str, Any]:
    paths = sorted(logs_dir.glob("trade_result_*.json")) if logs_dir.exists() else []
    parsed: list[Dict[str, Any]] = []
    source_names: list[str] = []
    parse_rejected: list[Dict[str, Any]] = []
    for path in paths:
        try:
            parsed.append(_read_json_any_encoding(path))
            source_names.append(path.name)
        except Exception as exc:
            parse_rejected.append({"file": path.name, "reasons": ["parse_failure"], "error": str(exc)})

    audited, central_report = audit_trade_records(parsed)
    clean_records = [
        row for row in audited
        if row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value
    ]
    rejected = list(parse_rejected)
    for index, row in enumerate(audited):
        if row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value:
            continue
        rejected.append({
            "file": source_names[index],
            "trade_key": row.get("trade_key", ""),
            "position_id": row.get("position_id", row.get("broker_position_identifier", "")),
            "symbol": row.get("symbol", ""),
            "reasons": list(row.get("ledger_integrity_reasons") or []),
            "record": row,
        })
    reason_counts = Counter(central_report.get("reason_counts") or {})
    reason_counts["parse_failure"] += len(parse_rejected)
    report = {
        "logs_dir": str(logs_dir),
        "output_dir": str(out_dir),
        "files_scanned": len(paths),
        "meta_identities_loaded": 0,
        "identity_repair_policy": "disabled_exact_broker_position_identity_required",
        "clean_records": len(clean_records),
        "rejected_records": len(rejected),
        "reason_counts": dict(sorted(reason_counts.items())),
        "repair_counts": {},
        "central_ledger_report": central_report,
        "examples": [
            {"file": item.get("file"), "symbol": item.get("symbol", ""), "reasons": item.get("reasons", [])}
            for item in rejected[:20]
        ],
        "analytics_default": "exclude suspicious/rejected rows; use --include-suspicious only for forensic analysis",
    }
    _write_outputs(out_dir, clean_records, rejected, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir", type=Path, default=_default_logs_dir(), help="Directory containing trade_result_*.json files.")
    parser.add_argument("--out-dir", type=Path, default=Path("ledger_repair_output"), help="Directory for repaired history and integrity reports.")
    parser.add_argument("--report", type=Path, default=None, help="Backward-compatible copy of ledger_integrity_report.json.")
    parser.add_argument("--repaired-dir", type=Path, default=None, help="Deprecated alias for --out-dir.")
    args = parser.parse_args()

    out_dir = args.repaired_dir or args.out_dir
    report = run_repair(args.logs_dir, out_dir)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
