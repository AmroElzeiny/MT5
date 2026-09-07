#!/usr/bin/env python3
"""Analyze completed AI trade lifecycle records and refresh live bucket priors."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_governance import (  # noqa: E402
    HIERARCHICAL_PRIOR_SCHEMA_VERSION,
    build_hierarchical_prior_artifact,
    canonical_hash,
    resolve_project_path,
)
from compatibility_manifest import compatibility_manifest_hash  # noqa: E402
from decision_integrity import AI_DECISION_SCHEMA_VERSION  # noqa: E402
from governance_contracts import SETUP_TAXONOMY_VERSION  # noqa: E402


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = payload.decode("utf-16", errors="strict")
    else:
        text = payload.decode("utf-8-sig", errors="strict")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _read_completed_memory(path: Path) -> list[dict[str, Any]]:
    """Read authoritative completed trades from the live memory database.

    Completed memory stores the lifecycle envelope in ``payload_json`` and the
    actual analytics record under ``completed_trade``.  The old analyzer read a
    JSONL export that is not produced by the current bridge, which made startup
    governance report no completed history even while the database was full.
    """

    if not path.is_file():
        return []
    connection = sqlite3.connect(str(path))
    try:
        records = connection.execute(
            """
            SELECT trade_key, candidate_hash, setup_taxonomy, setup_family,
                   entry_branch, direction, asset_class, session_code,
                   killzone_code, data_quality_status, resolved_at,
                   schema_version, payload_json
            FROM completed_memory
            ORDER BY resolved_at, memory_id
            """
        ).fetchall()
    finally:
        connection.close()

    rows: list[dict[str, Any]] = []
    for record in records:
        try:
            payload = json.loads(record[12])
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        completed = payload.get("completed_trade")
        row = dict(completed) if isinstance(completed, dict) else {}
        row.setdefault("trade_key", record[0])
        row.setdefault("candidate_hash", record[1])
        row.setdefault("setup_taxonomy_enum", record[2])
        row.setdefault("setup_family", record[3])
        row.setdefault("entry_branch", record[4])
        row.setdefault("direction", record[5])
        row.setdefault("asset_class", record[6])
        row.setdefault("session", record[7])
        row.setdefault("killzone", record[8])
        row["data_quality_status"] = str(record[9] or "")
        row.setdefault("resolved_at", record[10])
        row.setdefault("memory_schema_version", record[11])
        rows.append(row)
    return rows


def _ledger_report(rows: list[dict[str, Any]], *, source: Path) -> dict[str, Any]:
    clean_states = {"clean", "verified_clean", "reconciled_clean"}
    clean = [
        row
        for row in rows
        if str(row.get("data_quality_status") or "").strip().lower() in clean_states
        and str(row.get("ledger_integrity_status") or "").strip().lower()
        in clean_states
        and bool(row.get("learning_eligible"))
    ]
    material = [
        {
            "trade_key": str(row.get("trade_key") or ""),
            "candidate_hash": str(row.get("candidate_hash") or ""),
            "closed_at": int(row.get("closed_at") or 0),
            "ledger_integrity_status": str(row.get("ledger_integrity_status") or ""),
            "data_quality_status": str(row.get("data_quality_status") or ""),
        }
        for row in rows
    ]
    ledger_hash = canonical_hash(material)
    return {
        "schema_version": "20260812_completed_memory_ledger_audit_v1",
        "source": str(source.resolve()),
        "global_status": "CLEAN" if rows and len(clean) == len(rows) else "QUARANTINED",
        "clean_count": len(clean),
        "completed_count": len(rows),
        "rejected_count": len(rows) - len(clean),
        "ledger_hash": ledger_hash,
    }


def _merge_counterfactual_updates(
    rows: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Overlay immutable late counterfactual resolutions by exact trade key.

    The MQL engine cannot know a future original-policy path at an early managed
    close. It therefore appends a later resolution event. This merge is
    analytics-only and never mutates the original completed-trade ledger.
    """

    latest: dict[str, dict[str, Any]] = {}
    for update in updates:
        key = str(update.get("trade_key") or "").strip()
        if not key or not bool(update.get("counterfactual_complete")):
            continue
        previous = latest.get(key)
        if previous is None or int(update.get("evaluated_at") or 0) >= int(previous.get("evaluated_at") or 0):
            latest[key] = update
    merged: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        key = str(row.get("trade_key") or "").strip()
        update = latest.get(key)
        if update is not None:
            row.update(
                {
                    "counterfactual_status": update.get("counterfactual_status"),
                    "counterfactual_original_sl_tp_result": update.get(
                        "counterfactual_original_sl_tp_result"
                    ),
                    "counterfactual_original_sl_tp_result_r": update.get(
                        "counterfactual_original_sl_tp_result_r"
                    ),
                    "management_alpha": update.get("management_alpha"),
                    "counterfactual_ambiguous": bool(update.get("counterfactual_ambiguous")),
                    "counterfactual_evaluated_at": update.get("evaluated_at"),
                    "management_policy_selection_eligible": bool(
                        update.get("management_policy_selection_eligible")
                    ),
                }
            )
        merged.append(row)
    return merged


def _num(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        val = row.get(key, default)
        if val in (None, ""):
            return default
        out = float(val)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _avg(rows: Iterable[dict[str, Any]], key: str) -> float:
    vals = [_num(r, key) for r in rows if r.get(key) not in (None, "")]
    return round(mean(vals), 6) if vals else 0.0


def _canonical_metric(row: dict[str, Any], key: str) -> float:
    if row.get(key) not in (None, ""):
        return _num(row, key)
    aliases = {
        "llm_quality_score": ("ai_llm_quality_score", "ai_score"),
        "llm_self_reported_confidence": ("ai_llm_self_reported_confidence", "ai_confidence"),
    }
    for legacy in aliases.get(key, ()):
        if row.get(legacy) not in (None, ""):
            return _num(row, legacy)
    return 0.0


def _canonical_avg(rows: Iterable[dict[str, Any]], key: str) -> float:
    values = [_canonical_metric(row, key) for row in rows]
    return round(mean(values), 6) if values else 0.0


def _bucket_key(row: dict[str, Any]) -> str:
    setup = str(row.get("setup_code") or row.get("model_code") or "UNK").upper()
    session = str(row.get("session") or row.get("session_code") or "OFF").upper()
    killzone = str(row.get("killzone") or row.get("killzone_code") or "NK").upper()
    return f"{setup}-{session}-{killzone}"


def _group(rows: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(key_fn(row) or "UNKNOWN")
        groups[key].append(row)
    out: dict[str, dict[str, Any]] = {}
    for key, items in sorted(groups.items()):
        pnl = sum(_num(r, "full_close_pnl") for r in items)
        pct = sum(_num(r, "full_close_pct") for r in items)
        wins = [r for r in items if _num(r, "full_close_pct") > 0.1]
        losses = [r for r in items if _num(r, "full_close_pct") < -0.1]
        out[key] = {
            "trades": len(items),
            "net_pnl": round(pnl, 2),
            "net_pct": round(pct, 6),
            "winner_rate": round(len(wins) / len(items), 6) if items else 0.0,
            "winners_gt_0_1pct": len(wins),
            "losers_lt_minus_0_1pct": len(losses),
            "avg_llm_quality_score": _canonical_avg(items, "llm_quality_score"),
            "avg_llm_self_reported_confidence": _canonical_avg(items, "llm_self_reported_confidence"),
            "calibration_available": False,
            "avg_follow_through_probability": _avg(items, "follow_through_probability"),
            "avg_invalidation_risk": _avg(items, "invalidation_risk"),
            "avg_chop_risk": _avg(items, "chop_risk"),
            "avg_post_entry_failure_risk": _avg(items, "post_entry_failure_risk"),
            "stuck_no_mfe_rate": round(sum(1 for r in items if bool(r.get("stuck_no_mfe_triggered"))) / len(items), 6) if items else 0.0,
            "dr_structural_invalid_rate": round(sum(1 for r in items if bool(r.get("dr_and_structural_invalid_triggered"))) / len(items), 6) if items else 0.0,
            "avg_minutes_to_0_25r_mfe": _avg([r for r in items if _num(r, "minutes_to_0_25r_mfe") > 0], "minutes_to_0_25r_mfe"),
        }
    return out


def _policy_for(stats: dict[str, Any]) -> str:
    trades = int(stats.get("trades") or 0)
    net_pnl = float(stats.get("net_pnl") or 0.0)
    winner_rate = float(stats.get("winner_rate") or 0.0)
    stuck = float(stats.get("stuck_no_mfe_rate") or 0.0)
    dr = float(stats.get("dr_structural_invalid_rate") or 0.0)
    if trades >= 4 and net_pnl < 0 and winner_rate <= 0.25:
        return "block_or_require_exceptional_confirmation"
    if trades >= 8 and net_pnl < 0:
        return "quarantine_or_require_exceptional_confirmation"
    if stuck >= 0.45 or dr >= 0.35:
        return "require_stronger_follow_through_confirmation"
    return "monitor"


def _cost_calibration(rows: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row) or "UNKNOWN")].append(row)
    output: dict[str, dict[str, Any]] = {}
    for key, items in sorted(grouped.items()):
        usable = [row for row in items if row.get("actual_realized_cost") not in (None, "")]
        output[key] = {
            "sample_size": len(usable),
            "predicted_round_turn_cost_mean": _avg(usable, "predicted_round_turn_cost"),
            "actual_realized_cost_mean": _avg(usable, "actual_realized_cost"),
            "prediction_error_mean": _avg(usable, "cost_prediction_error"),
            "fallback_estimate_count": sum(
                1 for row in usable if str(row.get("cost_source") or "").startswith("configured_fallback")
            ),
        }
    return output


def _management_alpha_groups(rows: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row) or "UNKNOWN")].append(row)
    output: dict[str, dict[str, Any]] = {}
    for key, items in sorted(grouped.items()):
        eligible = [
            row
            for row in items
            if not bool(row.get("counterfactual_ambiguous"))
            and row.get("management_alpha") not in (None, "")
            and str(row.get("ledger_integrity_status") or "").strip().lower()
            in {"clean", "verified_clean", "reconciled_clean"}
        ]
        output[key] = {
            "clean_unambiguous_sample_size": len(eligible),
            "mean_management_alpha_r": _avg(eligible, "management_alpha"),
            "policy_selection_eligible": False,
            "status": "shadow_only_requires_registered_chronological_oos_experiment",
        }
    return output


def build_priors(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the clean-only, versioned hierarchical prior artifact.

    The former flat setup/session/symbol map is intentionally retired because it
    allowed tiny narrow buckets to override broad evidence. Policy activation
    remains governed elsewhere; this artifact carries evidence and shrinkage.
    """

    return build_hierarchical_prior_artifact(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="logs/completed_ai_trades.jsonl")
    ap.add_argument("--memory-db", default="data/ai_trade_memory.sqlite3")
    ap.add_argument("--threshold-pct", type=float, default=0.1)
    ap.add_argument("--priors-out", default="data/live_bucket_priors.json")
    ap.add_argument(
        "--counterfactual-updates",
        default="logs/management_counterfactual_updates.jsonl",
    )
    args = ap.parse_args()

    jsonl_rows = _read_jsonl(resolve_project_path(args.file))
    memory_path = resolve_project_path(args.memory_db)
    memory_rows = _read_completed_memory(memory_path)
    # The SQLite lifecycle store is authoritative for the current bridge.  Keep
    # JSONL as a compatibility fallback for archived installations only.
    source_rows = memory_rows if memory_rows else jsonl_rows
    rows = _merge_counterfactual_updates(
        source_rows,
        _read_jsonl(resolve_project_path(args.counterfactual_updates)),
    )
    winners = [r for r in rows if _num(r, "full_close_pct") > args.threshold_pct]
    losers = [r for r in rows if _num(r, "full_close_pct") < -args.threshold_pct]
    neutral = [r for r in rows if r not in winners and r not in losers]

    report = {
        "total_trades": len(rows),
        "winners_gt_0_1pct_count": len(winners),
        "winners_gt_0_1pct_avg_llm_quality_score": _canonical_avg(winners, "llm_quality_score"),
        "winners_gt_0_1pct_avg_llm_self_reported_confidence": _canonical_avg(winners, "llm_self_reported_confidence"),
        "losers_lt_minus_0_1pct_count": len(losers),
        "losers_lt_minus_0_1pct_avg_llm_quality_score": _canonical_avg(losers, "llm_quality_score"),
        "losers_lt_minus_0_1pct_avg_llm_self_reported_confidence": _canonical_avg(losers, "llm_self_reported_confidence"),
        "calibration_available": False,
        "calibrated_win_probability": None,
        "expected_net_r": None,
        "legacy_migration_aliases": {
            "ai_score": "llm_quality_score_diagnostic_only",
            "ai_confidence": "llm_self_reported_confidence_not_probability",
        },
        "neutral_count": len(neutral),
        "group_by_setup_code": _group(rows, lambda r: str(r.get("setup_code") or r.get("model_code") or "UNK").upper()),
        "group_by_setup_session_killzone": _group(rows, _bucket_key),
        "group_by_symbol": _group(rows, lambda r: str(r.get("symbol") or "UNKNOWN").upper()),
        "group_by_setup_family": _group(rows, lambda r: str(r.get("setup_family") or "UNKNOWN")),
        "group_by_target_source": _group(rows, lambda r: str(r.get("target_source") or "UNKNOWN")),
        "group_by_ai_veto_fields": _group(
            rows,
            lambda r: (
                f"veto={bool(r.get('ai_veto_enabled'))};"
                f"stuck={bool(r.get('stuck_no_mfe_triggered'))};"
                f"dr_struct={bool(r.get('dr_and_structural_invalid_triggered'))}"
            ),
        ),
        "cost_calibration_by_symbol": _cost_calibration(
            rows, lambda r: str(r.get("symbol") or "UNKNOWN").upper()
        ),
        "cost_calibration_by_asset_class": _cost_calibration(
            rows, lambda r: str(r.get("asset_class") or "UNKNOWN").lower()
        ),
        "management_alpha_by_version": _management_alpha_groups(
            rows, lambda r: str(r.get("management_version") or "UNKNOWN")
        ),
        "management_alpha_by_family": _management_alpha_groups(
            rows, lambda r: str(r.get("setup_family") or "UNKNOWN")
        ),
        "management_alpha_by_symbol": _management_alpha_groups(
            rows, lambda r: str(r.get("symbol") or "UNKNOWN").upper()
        ),
        "management_alpha_by_asset_class": _management_alpha_groups(
            rows, lambda r: str(r.get("asset_class") or "UNKNOWN").lower()
        ),
        "management_alpha_by_session": _management_alpha_groups(
            rows, lambda r: str(r.get("session") or r.get("session_code") or "UNKNOWN").upper()
        ),
        "management_alpha_by_invalidation_reason": _management_alpha_groups(
            rows, lambda r: str(r.get("management_transition_reason") or "none")
        ),
    }
    ledger_report = _ledger_report(rows, source=memory_path)
    priors = build_priors(rows)
    priors.update(
        {
            "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
            "taxonomy_version": SETUP_TAXONOMY_VERSION,
            "contract_manifest_hash": compatibility_manifest_hash(),
            "ledger_hash": ledger_report["ledger_hash"],
            "completed_trade_count": ledger_report["completed_count"],
        }
    )
    priors.pop("artifact_hash", None)
    priors["artifact_hash"] = canonical_hash(priors)
    out_path = resolve_project_path(args.priors_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(priors, indent=2, sort_keys=True), encoding="utf-8")
    ledger_path = resolve_project_path("data/ledger_integrity_report.json")
    ledger_path.write_text(
        json.dumps(ledger_report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"live_bucket_priors_written={out_path}")
    print(f"live_bucket_priors_schema={HIERARCHICAL_PRIOR_SCHEMA_VERSION}")
    print(f"ledger_integrity_report_written={ledger_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
