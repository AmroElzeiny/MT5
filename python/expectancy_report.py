#!/usr/bin/env python3
"""
expectancy_report.py
--------------------
Governed analytics and adaptation tooling for MT5 PO3 trade-result logs.

Outputs:
- expectancy summaries and grouped analytics
- regime/session/setup-class/volatility conditioned multivariate scorecard policies
- subtype proof / suppression recommendations with shrunk win-rate logic
- target-efficiency, snapshot-feature, and overfitting diagnostics
- versioned policy snapshots with activation and rollback support
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import random
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from openai_usage_logger import log_ai_usage
from po3_env import (
    PROVIDER_SELECT_OPENAI,
    bootstrap_provider_env,
    load_dotenv,
    peek_dotenv_value,
)
from calibration_pipeline import run_shadow_calibration, write_calibration_reports
from experiment_registry import ExperimentRegistry
from architecture_contracts import partition_homogeneous_cohorts
from governance_contracts import (
    EMPIRICAL_PRE_ENTRY_FEATURES,
    FEATURE_LINEAGE_VERSION,
    LedgerIntegrityStatus,
    audit_trade_records,
    block_bootstrap_uncertainty,
    enforce_policy_governance,
    feature_lineage_violations,
    purged_chronological_folds,
    suppression_eligibility,
    write_feature_lineage,
)

# Standalone analytics uses the same explicit provider selector as ai_gate, via
# the one shared resolver in ``po3_env``.
#
# It must not re-derive the selection here.  This module used to read the legacy
# ``AI_USE_REMOTE_API`` boolean alone, which is absent whenever the modern
# ``AI_PROVIDER_SELECT`` key is the one configured.  An absent boolean read as
# "not remote", so under ``AI_PROVIDER_SELECT=openai_remote`` this block took the
# local branch: it re-loaded the whole env file -- re-injecting the OpenRouter
# and local secrets that ``ai_gate``'s bootstrap had just stripped -- and then
# popped ``OPENAI_API_KEY``, the credential the selected provider requires.
# Because ``ai_gate`` imports this module *after* its own bootstrap, this ran
# last and won, and the gate failed closed with ``OPENAI_API_KEY=missing_remote``
# on a correct configuration.
#
# ``bootstrap_provider_env`` is idempotent, so running it again here re-applies
# the same exclusions instead of contradicting them.
_EXPECTANCY_PROVIDER_SELECT, _EXPECTANCY_PROVIDER_SELECT_ERROR = bootstrap_provider_env()
_EXPECTANCY_REMOTE_MODE = _EXPECTANCY_PROVIDER_SELECT == PROVIDER_SELECT_OPENAI

_EXPECTANCY_AI_PROVIDER: Any = None


def set_expectancy_ai_provider(provider: Any) -> None:
    """Inject the single startup-selected provider from ai_gate.

    Analytics has no independent transport authority and cannot instantiate a
    remote client behind the local/remote provider switch.
    """

    global _EXPECTANCY_AI_PROVIDER
    _EXPECTANCY_AI_PROVIDER = provider


# This hand-built scorecard is diagnostic only.  It uses immutable pre-entry
# features and deliberately excludes setup_score and hand-built EV/probability
# fields so they cannot be counted twice or acquire trading authority.
FEATURE_SPECS: List[Dict[str, str]] = [dict(item) for item in EMPIRICAL_PRE_ENTRY_FEATURES]

DECISION_MAKER_EDIT_SCOPE: Dict[str, Any] = {
    "runtime_policy_files": {
        "active_policy.json": [
            "soft_setup_floor",
            "hard_setup_floor",
            "setup_floor_penalty_mult",
            "ote_softness_frac",
            "default_risk_multiplier",
            "runner_sequence_floor",
            "runner_liquidity_rr_floor",
            "runner_cost_r_ceiling",
            "runner_alignment_floor",
            "runner_adverse_ceiling",
            "runner_ev_floor",
        ],
        "context_policy.ndjson": [
            "action",
            "score_bias",
            "risk_multiplier",
            "expected_value_bias",
        ],
        "subtype_policy.ndjson": [
            "action",
            "score_penalty",
            "risk_multiplier",
        ],
        "session_weekday_policy.ndjson": [
            "action",
            "risk_multiplier",
            "rr_floor_delta",
            "score_bias",
        ],
    },
    "advisory_only_mt5_inputs": [
        "InpRiskPerTradePct",
        "InpMinLiveRR2",
        "InpStandardTradeCostRCeiling",
        "InpStandardTradeLiquidityRRFloor",
        "InpRunnerLiquidityRRFloor",
        "InpRunnerSequenceQualityFloor",
        "InpRunnerHtfAlignmentFloor",
        "InpRunnerCostRCeiling",
        "InpRunnerAdverseContextCeiling",
        "InpSetupFloorReversal",
        "InpSetupFloorBreaker",
        "InpSetupFloorContinuation",
        "InpSetupFloorSession",
        "InpSetupFloorRange",
        "InpSetupFloorFullPO3",
        "InpSetupFloorMicroPO3",
        "InpSetupFloorMicroBisiSibi",
        "InpSetupFloorContinuationFamily",
        "InpSetupFloorRangeFamily",
        "InpSetupFloorFailedBreakout",
        "InpMinRRFullPO3",
        "InpMinRRMicroPO3",
        "InpMinRRContinuation",
        "InpMinRRRange",
        "InpMinRRFailedBreakout",
        "InpAiScoreFullPO3",
        "InpAiScoreMicroPO3",
        "InpAiScoreContinuation",
        "InpAiScoreRange",
        "InpAiScoreFailedBreakout",
        "InpMaxOpenPositions",
        "InpMaxTradesPerScan",
        "InpMaxTotalRiskEnable",
        "InpMaxTotalRiskMoney",
        "InpUseSnapshotAI",
        "InpRequireSnapshots",
        "Market Watch symbol list",
    ],
    "env_governance": [
        "EXPECTANCY_MIN_TRADES_BEFORE_ACTION",
        "EXPECTANCY_FIRST_ACTION_MIN_TRADES",
        "POLICY_MIN_TOTAL_CLOSED_TRADES",
        "POLICY_MIN_BUCKET_TRADES",
        "POLICY_MIN_PROFIT_FACTOR_AFTER_COSTS",
        "POLICY_MAX_TRAIN_TEST_GAP_R",
        "POLICY_MIN_POSITIVE_FOLD_RATE",
        "POLICY_MAX_DRAWDOWN_R",
        "POLICY_MAX_STEP_FRACTION",
    ],
    "hard_boundary": "The report writes policy files and advisory recommendations only. It does not directly rewrite MT5 input files.",
}

FUNNEL_KEYS = [
    "scans",
    "closed_sweeps_found",
    "displacement_passed",
    "tier_a_contexts",
    "tier_b_contexts",
    "raw_fvgs",
    "accepted_fvgs",
    "branch_candidates",
    "plan_prices_valid",
    "exclusive_model_filter_checked",
    "exclusive_model_filter_passed",
    "exclusive_model_filter_rejected",
    "exclusive_model_filter_reject_not_breaker",
    "exclusive_model_filter_reject_not_virgin",
    "exclusive_model_filter_reject_not_strong_origin",
    "ai_requests",
    "watchlist_added",
    "market_entries_attempted",
    "pending_orders_placed",
    "pending_orders_filled",
    "pending_orders_expired",
    # Legacy/compatibility counters emitted by older builds.
    "po3_context_created",
    "fvg_candidates_created",
    "pending_entries_attempted",
    "trades_opened",
]


def _default_common_files() -> Path:
    appdata = os.getenv("APPDATA", "")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
    return Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"


def _read_json_any_encoding(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return json.loads(raw.decode("utf-16"))
    if raw.startswith(b"\xef\xbb\xbf"):
        return json.loads(raw.decode("utf-8-sig"))
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(raw.decode("utf-16"))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _env_int_any(names: Iterable[str], default: int, minimum: Optional[int] = None) -> int:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            value = _safe_int(raw, default)
            return max(minimum, value) if minimum is not None else value
    return max(minimum, default) if minimum is not None else default


def _env_float_any(names: Iterable[str], default: float, minimum: Optional[float] = None) -> float:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            value = _safe_float(raw, default)
            return max(minimum, value) if minimum is not None else value
    return max(minimum, default) if minimum is not None else default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _csv_set(raw: str) -> set[str]:
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = _clamp(q, 0.0, 1.0) * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _group_key(record: Dict[str, Any], field: str) -> str:
    value = record.get(field)
    if field == "in_killzone":
        return "killzone" if _safe_bool(value) else "non_killzone"
    if value is None or value == "":
        return "unknown"
    return str(value)


def _derive_volatility_profile(record: Dict[str, Any]) -> str:
    existing = str(record.get("volatility_profile") or "").strip()
    if existing:
        return existing
    if _safe_float(record.get("news_risk")) >= 1.0 or _safe_float(record.get("expansion_score")) > 2.0:
        return "shock"
    if str(record.get("session_name") or "") == "OFF_HOURS":
        return "off_hours"
    if 0 < _safe_float(record.get("expansion_score")) < 0.85:
        return "compressed"
    if _safe_float(record.get("session_vol_ratio")) >= 0.70 or _safe_float(record.get("atr_pct")) >= 0.006:
        return "high"
    if 0 < _safe_float(record.get("session_vol_ratio")) < 0.12:
        return "subdued"
    return "balanced"


def _policy_bucket(record: Dict[str, Any]) -> str:
    existing = str(record.get("policy_bucket") or "").strip()
    if existing:
        return existing
    regime = _group_key(record, "regime_bucket")
    session = _group_key(record, "session_name")
    setup_class = _group_key(record, "setup_class")
    volatility = _derive_volatility_profile(record)
    return f"{regime}|{session}|{setup_class}|{volatility}"


def _subtype_key(record: Dict[str, Any]) -> str:
    po3_subtype = _group_key(record, "po3_subtype")
    fvg_subtype = _group_key(record, "fvg_subtype")
    setup_class = _group_key(record, "setup_class")
    regime = _group_key(record, "regime_bucket")
    return f"{po3_subtype}|{fvg_subtype}|{setup_class}|{regime}"


def _llm_quality_score_bucket(record: Dict[str, Any]) -> str:
    score = _safe_float(record.get("llm_quality_score"), _safe_float(record.get("ai_llm_quality_score")))
    if score <= 0:
        # Explicit read-only migration for pre-integrity analytics. Legacy
        # values never become calibrated probabilities or entry authority.
        for legacy_field in ("ai_score", "score", "ai_decision_score"):
            if record.get(legacy_field) not in (None, ""):
                score = _safe_float(record.get(legacy_field))
                record.setdefault("legacy_llm_quality_alias_source", legacy_field)
                break
    if score <= 0:
        return "unknown"
    if score < 5.0:
        return "<5.0"
    if score < 5.5:
        return "5.0-5.5"
    if score < 6.0:
        return "5.5-6.0"
    if score < 6.5:
        return "6.0-6.5"
    if score < 7.0:
        return "6.5-7.0"
    return ">=7.0"


def _execution_cost_bucket(record: Dict[str, Any]) -> str:
    cost = (
        _safe_float(record.get("execution_cost_r"))
        + _safe_float(record.get("slippage_r"))
        + _safe_float(record.get("commission_r"))
    )
    if cost <= 0:
        return "unknown"
    if cost < 0.10:
        return "<0.10R"
    if cost < 0.20:
        return "0.10-0.20R"
    if cost < 0.30:
        return "0.20-0.30R"
    return ">=0.30R"


def _duration_minutes(record: Dict[str, Any]) -> float:
    start = _safe_int(record.get("filled_at"), _safe_int(record.get("opened_at"), _safe_int(record.get("planned_at"))))
    end = _safe_int(record.get("closed_at"))
    if start <= 0 or end <= start:
        return 0.0
    return (end - start) / 60.0


_EMPTY_ID_VALUES = {"", "0", "0.0", "none", "null", "nan"}
_TRADE_ID_FIELDS = ("position_id", "deal_position_id", "trade_id")


def _valid_identity(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if text.lower() in _EMPTY_ID_VALUES:
        return ""
    return text


def _record_trade_id(record: Dict[str, Any]) -> str:
    for field in _TRADE_ID_FIELDS:
        identity = _valid_identity(record.get(field))
        if identity:
            return identity
    return ""


def _load_trade_identity_index(logs_dir: Path) -> Dict[str, str]:
    meta_root = logs_dir.parent
    if not meta_root.exists():
        return {}
    identities: Dict[str, str] = {}
    for path in sorted(meta_root.glob("trade_key_*.json")):
        try:
            meta = _read_json_any_encoding(path)
        except Exception:
            continue
        if not _safe_bool(meta.get("execution_identity_verified")) or _safe_bool(meta.get("execution_identity_quarantined")):
            continue
        trade_key = str(meta.get("trade_key") or "").strip()
        identity = _record_trade_id(meta)
        if trade_key and identity:
            identities.setdefault(trade_key, identity)
    return identities


def _enrich_trade_identity(record: Dict[str, Any], identities_by_key: Dict[str, str]) -> str:
    if _record_trade_id(record):
        return ""
    trade_key = str(record.get("trade_key") or "").strip()
    identity = identities_by_key.get(trade_key, "")
    if not identity:
        return ""
    record["position_id"] = identity
    return "trade_key_meta"


def _realized_pnl(record: Dict[str, Any]) -> Tuple[bool, float]:
    if "realized_pnl" not in record:
        return False, 0.0
    raw = record.get("realized_pnl")
    if raw is None or raw == "":
        return False, 0.0
    try:
        value = float(raw)
    except Exception:
        return False, 0.0
    if not math.isfinite(value):
        return False, 0.0
    return True, value


def _invalid_trade_record_reasons(record: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    status = str(record.get("data_integrity_status") or "").strip().lower()
    if status and status not in {"clean", "repaired"}:
        reasons.append(status)
    schema_version = _safe_int(record.get("analytics_schema_version"), 0)
    if schema_version < 2 and _safe_float(record.get("virtual_balance_base")) <= 0.0:
        reasons.append("legacy_trade_result_schema")
    if schema_version >= 3:
        if not _safe_bool(record.get("execution_identity_verified")):
            reasons.append("execution_identity_unverified")
        if _safe_bool(record.get("execution_identity_quarantined")):
            reasons.append("execution_identity_quarantined")
        if not _valid_identity(record.get("result_order_ticket")):
            reasons.append("missing_result_order_ticket")
        if not _valid_identity(record.get("result_deal_ticket")):
            reasons.append("missing_result_deal_ticket")
        if not _valid_identity(record.get("broker_position_identifier")):
            reasons.append("missing_broker_position_identifier")
        if not str(record.get("candidate_hash") or "").strip():
            reasons.append("missing_candidate_hash")
        if not str(record.get("final_execution_fingerprint") or "").strip():
            reasons.append("missing_execution_fingerprint")
    if not _record_trade_id(record):
        reasons.append("missing_trade_id")
    has_pnl, pnl = _realized_pnl(record)
    if not has_pnl:
        reasons.append("missing_realized_pnl")
    elif pnl == 0.0:
        reasons.append("zero_realized_pnl")
    if _safe_float(record.get("planned_entry")) <= 0.0:
        reasons.append("missing_planned_entry")
    if _safe_float(record.get("planned_sl")) <= 0.0:
        reasons.append("missing_planned_sl")
    if _safe_float(record.get("realized_r")) == 0.0 and has_pnl and pnl != 0.0:
        reasons.append("zero_realized_r_with_nonzero_pnl")
    entry_branch = str(record.get("entry_branch") or record.get("entry_model") or "").strip().lower()
    if not entry_branch or entry_branch == "unknown":
        reasons.append("missing_entry_branch")
    return reasons


def _record_quality_example(path: Path, record: Dict[str, Any], reasons: List[str]) -> Dict[str, Any]:
    return {
        "file": path.name,
        "reasons": reasons,
        "trade_key": str(record.get("trade_key") or ""),
        "position_id": str(record.get("position_id") or ""),
        "symbol": str(record.get("symbol") or ""),
        "realized_pnl": record.get("realized_pnl"),
        "realized_r": record.get("realized_r"),
    }


def _normalize_loaded_record(record: Dict[str, Any]) -> None:
    # Explicit analytics aliases for pre-v4 report code.  The authoritative
    # fields remain broker_net_pnl and result_r_initial_risk.
    record["realized_pnl"] = _safe_float(record.get("broker_net_pnl"), _safe_float(record.get("realized_pnl")))
    record["realized_r"] = _safe_float(record.get("result_r_initial_risk"), _safe_float(record.get("realized_r")))
    record.setdefault("volatility_profile", _derive_volatility_profile(record))
    record.setdefault("policy_bucket", _policy_bucket(record))
    record.setdefault("subtype_key", _subtype_key(record))
    record.setdefault("setup_family", str(record.get("setup_family") or record.get("setup_class") or "unknown"))
    record.setdefault("entry_branch", str(record.get("entry_branch") or record.get("entry_model") or "unknown"))
    record.setdefault("llm_quality_score_bucket", _llm_quality_score_bucket(record))
    record.setdefault("execution_cost_bucket", _execution_cost_bucket(record))
    record.setdefault("duration_minutes", _duration_minutes(record))
    record.setdefault("runner_trade", _safe_bool(record.get("runner_trade")))


def _env_bool_any(names: Iterable[str], default: bool = False) -> bool:
    for name in names:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            continue
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
    return default


def _load_records_with_quality(logs_dir: Path, include_suspicious: Optional[bool] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if include_suspicious is None:
        include_suspicious = _env_bool_any(("EXPECTANCY_INCLUDE_SUSPICIOUS", "EXPECTANCY_INCLUDE_SUSPICIOUS_TRADES"), False)
    quality: Dict[str, Any] = {
        "loaded_files": 0,
        "used_records": 0,
        "ignored_records": 0,
        "include_suspicious": include_suspicious,
        "ignored_reasons": {},
        "ignored_examples": [],
        "parse_failures": 0,
        "identity_meta_keys": 0,
        "identity_enriched_from_trade_key_meta": 0,
        "identity_recovery_policy": "disabled_exact_position_id_required",
    }
    if not logs_dir.exists():
        return [], quality
    paths = sorted(logs_dir.glob("trade_result_*.json"))
    quality["loaded_files"] = len(paths)
    raw_records: List[Dict[str, Any]] = []
    source_by_index: List[Path] = []
    for path in paths:
        try:
            record = _read_json_any_encoding(path)
        except Exception:
            quality["parse_failures"] += 1
            continue
        raw_records.append(record)
        source_by_index.append(path)

    audited, ledger_report = audit_trade_records(raw_records)
    quality["central_ledger_audit"] = ledger_report
    quality["ledger_integrity_status"] = ledger_report.get("global_status", LedgerIntegrityStatus.QUARANTINED.value)
    quality["ignored_reasons"] = dict(ledger_report.get("reason_counts") or {})
    records: List[Dict[str, Any]] = []
    for index, record in enumerate(audited):
        status = str(record.get("ledger_integrity_status") or LedgerIntegrityStatus.QUARANTINED.value)
        reasons = list(record.get("ledger_integrity_reasons") or [])
        if status != LedgerIntegrityStatus.CLEAN.value:
            if len(quality["ignored_examples"]) < 12:
                quality["ignored_examples"].append(
                    _record_quality_example(source_by_index[index], record, reasons)
                )
            if not include_suspicious:
                continue
        _normalize_loaded_record(record)
        records.append(record)
    records.sort(key=lambda r: _safe_int(r.get("closed_at")))
    cohorts = partition_homogeneous_cohorts(records)
    cohort_counts = {cohort_id: len(rows) for cohort_id, rows in cohorts.items()}
    complete_cohort_ids = [cohort_id for cohort_id in cohorts if cohort_id != "INCOMPLETE"]
    cohort_block_reasons: List[str] = []
    if "INCOMPLETE" in cohorts:
        cohort_block_reasons.append("cohort_metadata_incomplete")
    if len(complete_cohort_ids) > 1:
        cohort_block_reasons.append("mixed_version_cohort_analysis_blocked")
    quality["cohort_counts"] = cohort_counts
    quality["cohort_ids"] = complete_cohort_ids
    quality["cohort_integrity_status"] = "BLOCKED" if cohort_block_reasons else ("COMPATIBLE" if records else "NO_DATA")
    quality["cohort_block_reasons"] = cohort_block_reasons
    if cohort_block_reasons:
        quality["ignored_reasons"]["cohort_integrity_block"] = len(records)
        quality["ignored_records"] = len(audited)
        quality["used_records"] = 0
        return [], quality
    quality["used_records"] = len(records)
    quality["ignored_records"] = len(audited) - len(records)
    return records, quality


def _load_records(logs_dir: Path) -> List[Dict[str, Any]]:
    records, _ = _load_records_with_quality(logs_dir)
    return records


def _collect_ea_log_paths(log_files: Iterable[str], log_dirs: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    seen: set[Path] = set()
    for raw in log_files:
        if not raw:
            continue
        path = Path(raw)
        if path.exists() and path.is_file() and path not in seen:
            paths.append(path)
            seen.add(path)
    for raw in log_dirs:
        if not raw:
            continue
        root = Path(raw)
        if not root.exists():
            continue
        for pattern in ("*.log", "*.txt"):
            for path in sorted(root.glob(pattern)):
                if path.is_file() and path not in seen:
                    paths.append(path)
                    seen.add(path)
    return paths


def _parse_int_after(line: str, key: str) -> int:
    match = re.search(rf"\b{re.escape(key)}=([-+]?\d+)", line)
    return int(match.group(1)) if match else 0


def _setup_funnel_report(log_paths: Iterable[Path]) -> Dict[str, Any]:
    explicit: Counter[str] = Counter()
    inferred: Counter[str] = Counter()
    reject_reasons: Counter[str] = Counter()
    reject_stages: Counter[str] = Counter()
    reject_stage_reasons: Counter[str] = Counter()
    explicit_blockers: Counter[str] = Counter()
    pending_delete_reasons: Counter[str] = Counter()
    files_used: List[str] = []

    for path in log_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        files_used.append(str(path))
        for line in text.splitlines():
            if "setup_funnel" in line:
                for key in FUNNEL_KEYS:
                    explicit[key] += _parse_int_after(line, key)
                match = re.search(r"pending_orders_deleted_by_reason=\{([^}]*)\}", line)
                if match:
                    for item in match.group(1).split(";"):
                        if not item or ":" not in item:
                            continue
                        reason, raw_count = item.split(":", 1)
                        reason = reason.strip()
                        if not reason:
                            continue
                        try:
                            pending_delete_reasons[reason] += int(raw_count.strip())
                        except ValueError:
                            pending_delete_reasons[reason] += 1
                match = re.search(r"top_blocking_stages=\{([^}]*)\}", line)
                if match:
                    for item in match.group(1).split(";"):
                        if not item or ":" not in item:
                            continue
                        key, raw_count = item.split(":", 1)
                        key = key.strip()
                        if not key:
                            continue
                        try:
                            count = int(raw_count.strip())
                        except ValueError:
                            count = 1
                        explicit_blockers[key] += count
                continue

            if "setup_reject" in line and "reject_stage=" in line and "reject_reason=" in line:
                stage_match = re.search(r"\breject_stage=([^ ]+)", line)
                reason_match = re.search(r"\breject_reason=([^ ]+)", line)
                stage = stage_match.group(1).strip() if stage_match else ""
                reason = reason_match.group(1).strip() if reason_match else ""
                if stage:
                    reject_stages[stage] += 1
                if reason:
                    reject_reasons[reason] += 1
                if stage and reason:
                    reject_stage_reasons[f"{stage}/{reason}"] += 1

            if "no valid PO3 context reason=" in line:
                reason = line.split("reason=", 1)[1].split()[0].strip()
                if reason:
                    reject_reasons[reason] += 1

            if "scan started" in line:
                symbols = _parse_int_after(line, "symbols")
                inferred["scans"] += symbols if symbols > 0 else 1
            if "PO3 context created" in line:
                inferred["po3_context_created"] += 1
                if "tier=A" in line:
                    inferred["tier_a_contexts"] += 1
                elif "tier=B" in line:
                    inferred["tier_b_contexts"] += 1
            if "FVG candidates created" in line:
                count = _parse_int_after(line, "count")
                inferred["fvg_candidates_created"] += count if count > 0 else 1
                inferred["accepted_fvgs"] += count if count > 0 else 1
            if " plan accepted" in line:
                inferred["plan_prices_valid"] += 1
            if " branch=" in line and " accepted setup_score=" in line:
                inferred["branch_candidates"] += 1
            if "AI request queued" in line:
                inferred["ai_requests"] += 1
            if "added to watchlist" in line:
                inferred["watchlist_added"] += 1
            if "market entry placed" in line:
                inferred["market_entries_attempted"] += 1
                inferred["trades_opened"] += 1
            if "market order rejected" in line:
                inferred["market_entries_attempted"] += 1
            if "pending limit placed" in line:
                inferred["pending_entries_attempted"] += 1
                inferred["pending_orders_placed"] += 1
            if "pending order rejected" in line:
                inferred["pending_entries_attempted"] += 1
            if "pending order filled" in line:
                inferred["pending_orders_filled"] += 1
            if "deleting expired pending order" in line:
                inferred["pending_orders_expired"] += 1
                pending_delete_reasons["expiry_reached"] += 1
            if "deleting pending order" in line and "reason=" in line:
                reason = line.split("reason=", 1)[1].split()[0].strip()
                if reason:
                    pending_delete_reasons[reason] += 1

    counts = explicit if any(explicit.values()) else inferred
    return {
        key: int(counts.get(key, 0))
        for key in FUNNEL_KEYS
    } | {
        "files_used": files_used,
        "days_observed": _infer_observed_days(files_used),
        "po3_reject_reasons": dict(reject_reasons.most_common(12)),
        "top_blocking_stages": dict((reject_stage_reasons or explicit_blockers).most_common(5)),
        "reject_stages": dict(reject_stages.most_common(12)),
        "pending_orders_deleted_by_reason": dict(pending_delete_reasons.most_common(12)),
    }


def _infer_observed_days(files_used: Iterable[str]) -> int:
    days: set[str] = set()
    for raw in files_used:
        match = re.search(r"(20\d{6})", raw)
        if match:
            days.add(match.group(1))
            continue
        try:
            days.add(time.strftime("%Y%m%d", time.localtime(Path(raw).stat().st_mtime)))
        except Exception:
            pass
    return max(1, len(days))


def _frequency_suggestions() -> List[str]:
    return [
        "increase symbol universe",
        "increase max symbols per tick",
        "allow continuation family",
        "allow failed-breakout family",
        "lower family-specific setup floor slightly",
        "widen entry-zone tolerance",
        "increase market-entry tolerance",
        "reduce AI threshold slightly",
    ]


def _render_setup_funnel(funnel: Dict[str, Any]) -> List[str]:
    lines = ["## Setup Funnel", ""]
    if not funnel.get("files_used"):
        lines.append("- No EA log files were provided or found.")
        return lines
    for key in FUNNEL_KEYS:
        lines.append(f"- {key}: {_safe_int(funnel.get(key))}")
    days = max(1, _safe_int(funnel.get("days_observed"), 1))
    target_min = max(1, _safe_int(os.getenv("TARGET_TRADES_PER_DAY_MIN"), 20))
    target_max = max(target_min, _safe_int(os.getenv("TARGET_TRADES_PER_DAY_MAX"), 60))
    derived_rates = {
        "scans_per_day": _safe_int(funnel.get("scans")),
        "po3_contexts_per_day": _safe_int(funnel.get("po3_context_created")) or (
            _safe_int(funnel.get("tier_a_contexts")) + _safe_int(funnel.get("tier_b_contexts"))
        ),
        "fvg_candidates_per_day": _safe_int(funnel.get("fvg_candidates_created")) or _safe_int(funnel.get("raw_fvgs")),
        "branch_candidates_per_day": _safe_int(funnel.get("branch_candidates")),
        "accepted_plans_per_day": _safe_int(funnel.get("plan_prices_valid")),
        "market_pending_attempts_per_day": (
            _safe_int(funnel.get("pending_entries_attempted"))
            or _safe_int(funnel.get("pending_orders_placed")) + _safe_int(funnel.get("market_entries_attempted"))
        ),
        "fills_per_day": _safe_int(funnel.get("pending_orders_filled")),
        "trades_per_day": _safe_int(funnel.get("trades_opened")),
    }
    lines.append("")
    lines.append("## Frequency Diagnostics")
    lines.append("")
    lines.append(f"- observed_days: {days}")
    lines.append(f"- target_trades_per_day: {target_min}-{target_max}")
    for label, value in derived_rates.items():
        lines.append(f"- {label}: {value / days:.2f}")
    trades_per_day = derived_rates["trades_per_day"] / days
    if trades_per_day < target_min:
        lines.append("")
        lines.append("## Least-Dangerous Loosening")
        lines.append("")
        for item in _frequency_suggestions():
            lines.append(f"- {item}")
    reasons = funnel.get("po3_reject_reasons") or {}
    top_blockers = funnel.get("top_blocking_stages") or {}
    if top_blockers:
        lines.append("")
        lines.append("## Top Blocking Stages")
        lines.append("")
        for stage_reason, count in top_blockers.items():
            lines.append(f"- {stage_reason}: {count}")
    stages = funnel.get("reject_stages") or {}
    if stages:
        lines.append("")
        lines.append("## Reject Stages")
        lines.append("")
        for stage, count in stages.items():
            lines.append(f"- {stage}: {count}")
    if reasons:
        lines.append("")
        lines.append("## PO3 Reject Reasons")
        lines.append("")
        for reason, count in reasons.items():
            lines.append(f"- {reason}: {count}")
    pending_reasons = funnel.get("pending_orders_deleted_by_reason") or {}
    if pending_reasons:
        lines.append("")
        lines.append("## Pending Delete Reasons")
        lines.append("")
        for reason, count in pending_reasons.items():
            lines.append(f"- {reason}: {count}")
    return lines


def _render_no_trade_report(funnel: Dict[str, Any]) -> str:
    lines = ["No trades were taken. Cannot estimate expectancy. First fix setup generation.", ""]
    lines.extend(_render_setup_funnel(funnel))
    return "\n".join(lines)


def _filter_ai_decision_sources(
    records: List[Dict[str, Any]],
    include_sources: set[str],
    exclude_sources: set[str],
) -> List[Dict[str, Any]]:
    if not include_sources and not exclude_sources:
        return records
    filtered: List[Dict[str, Any]] = []
    for record in records:
        source = str(record.get("ai_decision_source") or "unknown")
        if include_sources and source not in include_sources:
            continue
        if exclude_sources and source in exclude_sources:
            continue
        filtered.append(record)
    return filtered


def _virtual_start_balance() -> float:
    return max(1.0, _safe_float(os.getenv("PO3_VIRTUAL_START_BALANCE", "100000"), 100000.0))


def _weekday_name(ts: int) -> str:
    if ts <= 0:
        return "unknown"
    # Python UTC weekday: Monday=0. MT5 server timestamps are treated as the
    # system's own server-time ledger; no account timezone conversion is applied.
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][time.gmtime(ts).tm_wday]


def _trade_date_key(ts: int) -> str:
    if ts <= 0:
        return "unknown"
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _record_ledger_time(record: Dict[str, Any]) -> int:
    return _safe_int(record.get("closed_at"), _record_time(record))


def _decorate_virtual_ledger(records: List[Dict[str, Any]], start_balance: float) -> List[Dict[str, Any]]:
    balance = start_balance
    for index, record in enumerate(sorted(records, key=_record_ledger_time), start=1):
        pnl = _safe_float(record.get("realized_pnl"))
        ts = _record_ledger_time(record)
        before = balance
        after = before + pnl
        record["virtual_ledger_id"] = index
        record["virtual_start_balance"] = start_balance
        record["virtual_balance_before"] = round(before, 2)
        record["virtual_balance_after"] = round(after, 2)
        record["result_pct_of_virtual_start"] = round((pnl / start_balance) * 100.0, 6)
        record["result_pct_of_virtual_balance_before"] = round((pnl / max(before, 1e-9)) * 100.0, 6)
        record["weekday_name"] = _weekday_name(ts)
        record["trade_date"] = _trade_date_key(ts)
        record["session_weekday_key"] = f"{record.get('weekday_name')}|{_group_key(record, 'session_name')}"
        record.setdefault("direction", "buy" if _safe_bool(record.get("is_buy")) else "sell")
        record.setdefault("lot_size", _safe_float(record.get("initial_volume")))
        balance = after
    return records


def _virtual_ledger_summary(records: List[Dict[str, Any]], start_balance: float) -> Dict[str, Any]:
    if not records:
        return {
            "virtual_start_balance": start_balance,
            "virtual_balance": start_balance,
            "virtual_equity": start_balance,
            "gross_balance": start_balance,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "net_pnl": 0.0,
            "net_pct": 0.0,
            "profitable_days": 0,
            "losing_days": 0,
            "best_trade_pct": 0.0,
            "worst_trade_pct": 0.0,
            "best_weekday": "unknown",
            "worst_weekday": "unknown",
        }
    rows = sorted(records, key=_record_ledger_time)
    pnls = [_safe_float(row.get("realized_pnl")) for row in rows]
    pct = [_safe_float(row.get("result_pct_of_virtual_start")) for row in rows]
    day_pnl: Dict[str, float] = defaultdict(float)
    weekday_pnl: Dict[str, List[float]] = defaultdict(list)
    for row, pnl in zip(rows, pnls):
        day_pnl[str(row.get("trade_date") or "unknown")] += pnl
        weekday_pnl[str(row.get("weekday_name") or "unknown")].append(_safe_float(row.get("result_pct_of_virtual_start")))
    gross_profit = sum(p for p in pnls if p > 0.0)
    gross_loss = abs(sum(p for p in pnls if p < 0.0))
    net_pnl = sum(pnls)
    weekday_stats = {
        key: {
            "count": len(values),
            "avg_pct": _mean(values),
            "sum_pct": sum(values),
        }
        for key, values in weekday_pnl.items()
        if key != "unknown" and values
    }
    best_weekday = max(weekday_stats.items(), key=lambda item: (item[1]["avg_pct"], item[1]["count"]))[0] if weekday_stats else "unknown"
    worst_weekday = min(weekday_stats.items(), key=lambda item: (item[1]["avg_pct"], -item[1]["count"]))[0] if weekday_stats else "unknown"
    current = start_balance + net_pnl
    return {
        "virtual_start_balance": round(start_balance, 2),
        "virtual_balance": round(current, 2),
        "virtual_equity": round(current, 2),
        "gross_balance": round(start_balance + gross_profit, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(net_pnl, 2),
        "net_pct": round((net_pnl / start_balance) * 100.0, 6),
        "profitable_days": sum(1 for value in day_pnl.values() if value > 0.0),
        "losing_days": sum(1 for value in day_pnl.values() if value < 0.0),
        "best_trade_pct": max(pct) if pct else 0.0,
        "worst_trade_pct": min(pct) if pct else 0.0,
        "best_weekday": best_weekday,
        "worst_weekday": worst_weekday,
        "weekday_stats": weekday_stats,
    }


def _system_trade_history(records: List[Dict[str, Any]], start_balance: float) -> Dict[str, Any]:
    rows = []
    for record in sorted(records, key=_record_ledger_time):
        rows.append(
            {
                "virtual_ledger_id": record.get("virtual_ledger_id"),
                "trade_key": record.get("trade_key"),
                "position_id": record.get("position_id"),
                "symbol": record.get("symbol"),
                "direction": record.get("direction", "buy" if _safe_bool(record.get("is_buy")) else "sell"),
                "lot_size": _safe_float(record.get("lot_size"), _safe_float(record.get("initial_volume"))),
                "planned_entry": _safe_float(record.get("planned_entry")),
                "filled_entry": _safe_float(record.get("filled_entry")),
                "sl": _safe_float(record.get("planned_sl")),
                "tp1": _safe_float(record.get("planned_tp1")),
                "tp2": _safe_float(record.get("planned_tp2")),
                "partials": record.get("partials", []),
                "result_pnl": _safe_float(record.get("realized_pnl")),
                "result_r": _safe_float(record.get("realized_r")),
                "result_pct_of_virtual_start": _safe_float(record.get("result_pct_of_virtual_start")),
                "virtual_balance_before": _safe_float(record.get("virtual_balance_before")),
                "virtual_balance_after": _safe_float(record.get("virtual_balance_after")),
                "session_name": record.get("session_name"),
                "weekday_name": record.get("weekday_name"),
                "trade_date": record.get("trade_date"),
                "closed_at": _safe_int(record.get("closed_at")),
            }
        )
    return {
        "virtual_start_balance": start_balance,
        "summary": _virtual_ledger_summary(records, start_balance),
        "trades": rows,
    }


def _bootstrap_mean(values: List[float], iterations: int, seed: int) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "ci_low": 0.0, "ci_high": 0.0, "positive_rate": 0.0}
    if len(values) == 1 or iterations <= 0:
        mean = _mean(values)
        return {
            "mean": mean,
            "ci_low": mean,
            "ci_high": mean,
            "positive_rate": 1.0 if mean > 0.0 else 0.0,
        }
    rng = random.Random(seed)
    draws: List[float] = []
    n = len(values)
    for _ in range(iterations):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        draws.append(_mean(sample))
    draws.sort()
    lo_idx = max(0, int(math.floor((iterations - 1) * 0.05)))
    hi_idx = min(iterations - 1, int(math.floor((iterations - 1) * 0.95)))
    positive = sum(1 for value in draws if value > 0.0)
    return {
        "mean": _mean(draws),
        "ci_low": draws[lo_idx],
        "ci_high": draws[hi_idx],
        "positive_rate": positive / iterations if iterations else 0.0,
    }

def _max_drawdown_r(values: List[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd

def _summarize(records: Iterable[Dict[str, Any]], bootstrap_iterations: int = 0) -> Dict[str, Any]:
    rows = list(records)
    count = len(rows)
    if count == 0:
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "avg_effective_rr2": 0.0,
            "avg_heuristic_expected_r_diagnostic": None,
            "avg_expected_value_r_legacy_alias": None,
            "avg_execution_cost_r": 0.0,
            "avg_slippage_r": 0.0,
            "avg_commission_r": 0.0,
            "avg_pnl": 0.0,
            "sum_pnl": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_r": 0.0,
            "avg_fill_slippage_r": 0.0,
            "avg_mfe_r": 0.0,
            "avg_mae_r": 0.0,
            "avg_duration_minutes": 0.0,
            "avg_stop_quality": 0.0,
            "r_ci_low": 0.0,
            "r_ci_high": 0.0,
            "bootstrap_positive_rate": 0.0,
            "uncertainty_available": False,
            "uncertainty_method": "day_block_bootstrap",
            "equal_trade_weighted_expectancy_r": 0.0,
            "risk_weighted_expectancy_r": 0.0,
        }

    pnls = [_safe_float(r.get("broker_net_pnl"), _safe_float(r.get("realized_pnl"))) for r in rows]
    rs = [_safe_float(r.get("result_r_initial_risk"), _safe_float(r.get("realized_r"))) for r in rows]
    eff_rr = [_safe_float(r.get("effective_rr2")) for r in rows]
    execution_costs = [_safe_float(r.get("execution_cost_r")) for r in rows]
    slippage_rs = [_safe_float(r.get("slippage_r")) for r in rows]
    commission_rs = [_safe_float(r.get("commission_r")) for r in rows]
    slips = [_safe_float(r.get("fill_slippage_r")) for r in rows]
    mfe_rs = [_safe_float(r.get("mfe_r")) for r in rows]
    mae_rs = [_safe_float(r.get("mae_r")) for r in rows]
    durations = [_safe_float(r.get("duration_minutes"), _duration_minutes(r)) for r in rows]
    stop_q = [_safe_float(r.get("realized_stop_quality"), _safe_float(r.get("stop_quality_score"))) for r in rows]

    gross_win = sum(p for p in pnls if p > 0.0)
    gross_loss = abs(sum(p for p in pnls if p < 0.0))
    wins = sum(1 for value in rs if value > 0.0)
    losses = sum(1 for value in rs if value < 0.0)
    uncertainty = block_bootstrap_uncertainty(
        rows,
        iterations=bootstrap_iterations,
        block_type="day",
        seed=23 + count,
    )
    uncertainty_available = bool(uncertainty.get("available"))
    total_risk = sum(max(0.0, _safe_float(row.get("initial_risk_money"))) for row in rows)
    risk_weighted_expectancy = (
        sum(
            _safe_float(row.get("result_r_initial_risk"), _safe_float(row.get("realized_r")))
            * max(0.0, _safe_float(row.get("initial_risk_money")))
            for row in rows
        ) / total_risk
        if total_risk > 0.0
        else 0.0
    )

    return {
        "count": count,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / count if count else 0.0,
        "avg_r": _mean(rs),
        "avg_effective_rr2": _mean(eff_rr),
        "avg_heuristic_expected_r_diagnostic": None,
        "avg_expected_value_r_legacy_alias": None,
        "avg_execution_cost_r": _mean(execution_costs),
        "avg_slippage_r": _mean(slippage_rs),
        "avg_commission_r": _mean(commission_rs),
        "avg_pnl": _mean(pnls),
        "sum_pnl": sum(pnls),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0.0 else 0.0,
        "max_drawdown_r": _max_drawdown_r(rs),
        "avg_fill_slippage_r": _mean(slips),
        "avg_mfe_r": _mean(mfe_rs),
        "avg_mae_r": _mean(mae_rs),
        "avg_duration_minutes": _mean(durations),
        "avg_stop_quality": _mean(stop_q),
        "r_ci_low": uncertainty.get("mean_r_ci_low") if uncertainty_available else None,
        "r_ci_high": uncertainty.get("mean_r_ci_high") if uncertainty_available else None,
        "bootstrap_positive_rate": _safe_float(uncertainty.get("positive_draw_rate")) if uncertainty_available else 0.0,
        "uncertainty_available": uncertainty_available,
        "uncertainty_method": "day_block_bootstrap",
        "effective_block_count": _safe_int(uncertainty.get("effective_block_count")),
        "equal_trade_weighted_expectancy_r": _mean(rs),
        "risk_weighted_expectancy_r": risk_weighted_expectancy,
    }


def _group_summary(records: List[Dict[str, Any]], field: str, min_count: int, bootstrap_iterations: int) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[_group_key(record, field)].append(record)
    rows: List[Dict[str, Any]] = []
    for key, subset in buckets.items():
        stats = _summarize(subset, bootstrap_iterations=bootstrap_iterations)
        if stats["count"] < min_count:
            continue
        item = {"bucket": key}
        item.update(stats)
        rows.append(item)
    rows.sort(key=lambda row: (-row["avg_r"], -row["count"], row["bucket"]))
    return rows


def _strict_suppression_evidence(rows: List[Dict[str, Any]], min_sample: int) -> Dict[str, Any]:
    ledger_status = (
        LedgerIntegrityStatus.CLEAN.value
        if rows and all(row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value for row in rows)
        else LedgerIntegrityStatus.SUSPICIOUS.value
    )
    return suppression_eligibility(
        rows,
        ledger_status=ledger_status,
        min_clean_sample=max(1, min_sample),
        bootstrap_iterations=1000,
        block_type="day",
    )


def _pearson(x_values: List[float], y_values: List[float]) -> float:
    if len(x_values) != len(y_values) or len(x_values) < 3:
        return 0.0
    x_mean = _mean(x_values)
    y_mean = _mean(y_values)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    den_x = math.sqrt(sum((x - x_mean) ** 2 for x in x_values))
    den_y = math.sqrt(sum((y - y_mean) ** 2 for y in y_values))
    if den_x <= 1e-12 or den_y <= 1e-12:
        return 0.0
    return num / (den_x * den_y)


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _time_segments(records: List[Dict[str, Any]], segment_count: int) -> List[List[Dict[str, Any]]]:
    if segment_count <= 0 or not records:
        return []
    size = max(1, len(records) // segment_count)
    segments: List[List[Dict[str, Any]]] = []
    start = 0
    for i in range(segment_count):
        end = len(records) if i == segment_count - 1 else min(len(records), start + size)
        segments.append(records[start:end])
        start = end
    return [segment for segment in segments if segment]


def _fit_scorecard(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {"intercept": 0.0, "features": [], "base_win_rate": 0.0}

    outcomes = [_safe_float(record.get("realized_r")) for record in records]
    win_flags = [1.0 if value > 0.0 else 0.0 for value in outcomes]
    base_win_rate = _clamp(_mean(win_flags), 0.05, 0.95)
    intercept = math.log(base_win_rate / (1.0 - base_win_rate))

    features: List[Dict[str, Any]] = []
    for spec in FEATURE_SPECS:
        name = spec["name"]
        direction_sign = 1.0 if spec["direction"] == "positive" else -1.0
        pairs: List[Tuple[float, float, float]] = []
        for record, outcome, win_flag in zip(records, outcomes, win_flags):
            if name not in record:
                continue
            value = _safe_float(record.get(name), math.nan)
            if math.isnan(value):
                continue
            pairs.append((value, outcome, win_flag))
        if len(pairs) < max(8, len(records) // 3):
            continue

        raw_values = [value for value, _, _ in pairs]
        center = statistics.median(raw_values)
        q1 = _quantile(raw_values, 0.25)
        q3 = _quantile(raw_values, 0.75)
        scale = max(abs(q3 - q1), abs(center) * 0.05, 1e-6)
        transformed = [_clamp((value - center) / scale, -3.0, 3.0) * direction_sign for value, _, _ in pairs]
        corr_win = _pearson(transformed, [flag for _, _, flag in pairs])
        corr_r = _pearson(transformed, [outcome for _, outcome, _ in pairs])
        weight = _clamp(corr_win * 1.7 + corr_r * 0.35, -1.8, 1.8)
        if abs(weight) < 0.05:
            continue
        features.append(
            {
                "name": name,
                "direction": spec["direction"],
                "center": center,
                "scale": scale,
                "weight": weight,
            }
        )

    return {
        "intercept": intercept,
        "features": features,
        "base_win_rate": base_win_rate,
    }


def _scorecard_predict(record: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, float]:
    score = _safe_float(model.get("intercept"))
    for feature in model.get("features", []):
        value = _safe_float(record.get(feature["name"]), math.nan)
        if math.isnan(value):
            continue
        centered = (value - _safe_float(feature.get("center"))) / max(_safe_float(feature.get("scale"), 1.0), 1e-6)
        centered = _clamp(centered, -3.0, 3.0)
        if feature.get("direction") == "negative":
            centered *= -1.0
        score += centered * _safe_float(feature.get("weight"))
    heuristic_win_rate = _sigmoid(score)
    effective_rr = max(0.0, _safe_float(record.get("effective_rr2"), _safe_float(record.get("rr2"))))
    heuristic_expected_r = heuristic_win_rate * effective_rr - (1.0 - heuristic_win_rate)
    return {
        "score": score,
        "heuristic_win_rate_diagnostic": heuristic_win_rate,
        "heuristic_expected_r_diagnostic": heuristic_expected_r,
        "decision_authoritative": False,
    }


def _evaluate_scorecard(records: List[Dict[str, Any]], model: Dict[str, Any], allow_threshold: float = 0.0) -> Dict[str, Any]:
    decorated: List[Dict[str, Any]] = []
    selected: List[Dict[str, Any]] = []
    for record in records:
        pred = _scorecard_predict(record, model)
        row = dict(record)
        row["heuristic_score_diagnostic"] = pred["score"]
        row["heuristic_win_rate_diagnostic"] = pred["heuristic_win_rate_diagnostic"]
        row["heuristic_expected_r_diagnostic"] = pred["heuristic_expected_r_diagnostic"]
        decorated.append(row)
        if pred["heuristic_expected_r_diagnostic"] >= allow_threshold:
            selected.append(row)
    stats = _summarize(selected)
    stats.update(
        {
            "selected_count": len(selected),
            "coverage": len(selected) / len(records) if records else 0.0,
            "avg_heuristic_expected_r_diagnostic": _mean(row["heuristic_expected_r_diagnostic"] for row in decorated),
            "heuristic_decision_authoritative": False,
        }
    )
    return stats

def _record_time(record: Dict[str, Any]) -> int:
    return _safe_int(record.get("planned_at"), _safe_int(record.get("filled_at"), _safe_int(record.get("closed_at"), 0)))

def _record_session_key(record: Dict[str, Any]) -> str:
    ts = _record_time(record)
    session = str(record.get("session_name") or "unknown")
    day = ts // 86400 if ts > 0 else 0
    return f"{day}|{session}"

def _record_symbol_cluster(record: Dict[str, Any]) -> str:
    return str(record.get("portfolio_cluster") or record.get("usd_exposure_key") or record.get("symbol") or "unknown")

def _purge_training_rows(train: List[Dict[str, Any]], test: List[Dict[str, Any]], embargo_seconds: int) -> List[Dict[str, Any]]:
    if not train or not test:
        return train
    test_times = [_record_time(row) for row in test if _record_time(row) > 0]
    if not test_times:
        return train
    test_start = min(test_times)
    test_end = max(test_times)
    test_sessions = {_record_session_key(row) for row in test}
    test_clusters = {_record_symbol_cluster(row) for row in test}
    out: List[Dict[str, Any]] = []
    for row in train:
        ts = _record_time(row)
        if ts > 0 and (test_start - embargo_seconds) <= ts <= (test_end + embargo_seconds):
            continue
        if _record_session_key(row) in test_sessions:
            continue
        if _record_symbol_cluster(row) in test_clusters and ts > 0 and abs(ts - test_start) <= embargo_seconds * 3:
            continue
        out.append(row)
    return out


def _walk_forward_scorecard(records: List[Dict[str, Any]], folds: int) -> List[Dict[str, Any]]:
    if folds <= 0 or len(records) < max(24, folds * 10):
        return []
    records_sorted = sorted(records, key=_record_time)
    segments = _time_segments(records_sorted, folds + 1)
    output: List[Dict[str, Any]] = []
    embargo_seconds = 6 * 3600
    for idx in range(1, len(segments)):
        train_raw = [row for segment in segments[:idx] for row in segment]
        test = segments[idx]
        train = _purge_training_rows(train_raw, test, embargo_seconds)
        if len(train) < 12 or len(test) < 6:
            continue
        model = _fit_scorecard(train)
        train_eval = _evaluate_scorecard(train, model)
        test_eval = _evaluate_scorecard(test, model)
        output.append(
            {
                "fold": idx,
                "train_count": len(train),
                "test_count": len(test),
                "train_stats": train_eval,
                "test_stats": test_eval,
                "feature_count": len(model.get("features", [])),
            }
        )
    return output


def _aggregate_walk_forward(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    if not rows:
        return {
            "folds": 0,
            "positive_fold_rate": 0.0,
            "avg_train_r": 0.0,
            "avg_test_r": 0.0,
            "avg_train_ev": 0.0,
            "avg_test_ev": 0.0,
            "avg_gap": 0.0,
        }
    train_r = [_safe_float(row["train_stats"].get("avg_r")) for row in rows]
    test_r = [_safe_float(row["test_stats"].get("avg_r")) for row in rows]
    train_ev = [_safe_float(row["train_stats"].get("avg_heuristic_expected_r_diagnostic")) for row in rows]
    test_ev = [_safe_float(row["test_stats"].get("avg_heuristic_expected_r_diagnostic")) for row in rows]
    gaps = [tr - te for tr, te in zip(train_r, test_r)]
    return {
        "folds": len(rows),
        "positive_fold_rate": sum(1 for value in test_r if value > 0.0) / len(rows),
        "avg_train_r": _mean(train_r),
        "avg_test_r": _mean(test_r),
        "avg_train_ev": _mean(train_ev),
        "avg_test_ev": _mean(test_ev),
        "avg_gap": _mean(gaps),
    }


def _runner_quantile(records: List[Dict[str, Any]], field: str, q: float, default: float) -> float:
    values = [_safe_float(record.get(field), math.nan) for record in records]
    values = [value for value in values if not math.isnan(value)]
    if not values:
        return default
    return _quantile(values, q)


def _build_context_policies(
    records: List[Dict[str, Any]],
    min_bucket_samples: int,
    walk_forward_folds: int,
) -> Dict[str, Any]:
    overall_stats = _summarize(records)
    global_model = _fit_scorecard(records)
    global_walk_forward = _walk_forward_scorecard(records, walk_forward_folds)
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[_policy_bucket(record)].append(record)

    policies: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    for bucket, rows in sorted(buckets.items()):
        if len(rows) < min_bucket_samples:
            continue
        model = _fit_scorecard(rows)
        fitted = _evaluate_scorecard(rows, model)
        walk_forward = _walk_forward_scorecard(rows, max(2, min(walk_forward_folds, len(rows) // max(min_bucket_samples, 1))))
        wf_summary = _aggregate_walk_forward(walk_forward)
        avg_test_r = _safe_float(wf_summary.get("avg_test_r"), fitted["avg_r"])
        positive_fold_rate = _safe_float(wf_summary.get("positive_fold_rate"))
        gap = _safe_float(wf_summary.get("avg_gap"))

        suppression = _strict_suppression_evidence(rows, min_bucket_samples)
        action = "allow"
        if suppression["suppression_eligible"]:
            action = "suppress"
        elif avg_test_r < 0.0 or gap > 0.40:
            action = "downrank"

        strength = _policy_strength(len(rows), min_bucket_samples, min_bucket_samples * 3)
        raw_score_bias = _clamp((avg_test_r - overall_stats["avg_r"]) * 6.0, -4.0, 4.0)
        raw_ev_bias = _clamp(
            _safe_float(wf_summary.get("avg_test_ev"), fitted.get("avg_heuristic_expected_r_diagnostic"))
            - overall_stats["avg_r"],
            -1.5,
            1.5,
        )
        raw_risk_multiplier = _clamp(0.75 + avg_test_r * 0.30 + (positive_fold_rate - 0.5) * 0.25, 0.35, 1.0)
        score_bias = _blend_from_neutral(0.0, raw_score_bias, strength)
        expected_value_bias = _blend_from_neutral(0.0, raw_ev_bias, strength)
        risk_multiplier = _blend_from_neutral(1.0, raw_risk_multiplier, strength)

        policies.append(
            {
                "policy_bucket": bucket,
                "action": action,
                "score_bias": round(score_bias, 4),
                "risk_multiplier": round(risk_multiplier, 4),
                "expected_value_bias": round(expected_value_bias, 4),
                "policy_strength": round(strength, 4),
                "sample_count": len(rows),
                "model": model,
                "fit_stats": fitted,
                "walk_forward": walk_forward,
                "walk_forward_summary": wf_summary,
                "suppression_eligible": suppression["suppression_eligible"],
                "suppression_block_reasons": suppression["suppression_block_reasons"],
                "suppression_confidence_interval": suppression["suppression_confidence_interval"],
                "suppression_fold_results": suppression["suppression_fold_results"],
                "version_confound_detected": suppression["version_confound_detected"],
                "controlled_effect_status": suppression["controlled_effect_status"],
            }
        )
        diagnostics.append(
            {
                "policy_bucket": bucket,
                "sample_count": len(rows),
                "action": action,
                "avg_r": fitted["avg_r"],
                "avg_test_r": avg_test_r,
                "avg_gap": gap,
                "positive_fold_rate": positive_fold_rate,
                "feature_count": len(model.get("features", [])),
            }
        )

    return {
        "global_model": global_model,
        "global_fit": _evaluate_scorecard(records, global_model),
        "global_walk_forward": global_walk_forward,
        "global_walk_forward_summary": _aggregate_walk_forward(global_walk_forward),
        "context_policies": policies,
        "context_diagnostics": diagnostics,
    }


def _build_subtype_backtest(records: List[Dict[str, Any]], min_subtype_samples: int, bootstrap_iterations: int) -> List[Dict[str, Any]]:
    overall = _summarize(records, bootstrap_iterations=bootstrap_iterations)
    base_win_rate = overall["win_rate"]
    prior_strength = 12.0
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[_subtype_key(record)].append(record)

    output: List[Dict[str, Any]] = []
    for key, rows in buckets.items():
        stats = _summarize(rows, bootstrap_iterations=bootstrap_iterations)
        if stats["count"] < min_subtype_samples:
            continue
        wins = stats["wins"]
        sample_count = stats["count"]
        shrunk_win_rate = (wins + prior_strength * base_win_rate) / (sample_count + prior_strength)
        avg_r = stats["avg_r"]
        action = "allow"
        ledger_status = (
            LedgerIntegrityStatus.CLEAN.value
            if all(row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value for row in rows)
            else LedgerIntegrityStatus.SUSPICIOUS.value
        )
        suppression = suppression_eligibility(
            rows,
            ledger_status=ledger_status,
            min_clean_sample=min_subtype_samples,
            bootstrap_iterations=max(200, bootstrap_iterations),
        )
        if suppression["suppression_eligible"]:
            action = "suppress"
        elif shrunk_win_rate < 0.48 or avg_r < 0.0:
            action = "downrank"
        score_penalty = 0.0
        if action == "downrank":
            score_penalty = _clamp((0.50 - shrunk_win_rate) * 12.0 + max(0.0, -avg_r) * 4.0, 0.5, 6.0)
        elif action == "suppress":
            score_penalty = 8.0
        raw_risk_multiplier = _clamp(0.55 + shrunk_win_rate + avg_r * 0.20, 0.35, 1.0)
        strength = _policy_strength(sample_count, min_subtype_samples, min_subtype_samples * 3)
        score_penalty = _blend_from_neutral(0.0, score_penalty, strength)
        risk_multiplier = _blend_from_neutral(1.0, raw_risk_multiplier, strength)
        evidence_score = _clamp(sample_count / 40.0 + stats["bootstrap_positive_rate"], 0.0, 2.0)
        output.append(
            {
                "subtype_key": key,
                "action": action,
                "score_penalty": round(score_penalty, 4),
                "risk_multiplier": round(risk_multiplier, 4),
                "policy_strength": round(strength, 4),
                "shrunk_win_rate": round(shrunk_win_rate, 4),
                "avg_r": round(avg_r, 4),
                "evidence_score": round(evidence_score, 4),
                "sample_count": sample_count,
                "stats": stats,
                "suppression_eligible": suppression["suppression_eligible"],
                "suppression_block_reasons": suppression["suppression_block_reasons"],
                "suppression_confidence_interval": suppression["suppression_confidence_interval"],
                "suppression_fold_results": suppression["suppression_fold_results"],
                "version_confound_detected": suppression["version_confound_detected"],
                "controlled_effect_status": suppression["controlled_effect_status"],
            }
        )
    output.sort(key=lambda row: (row["action"], row["avg_r"], -row["sample_count"]))
    return output


def _target_efficiency_report(records: List[Dict[str, Any]], min_count: int) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        target = _group_key(record, "target_source")
        obstacle = _group_key(record, "obstacle_kind")
        buckets[(target, obstacle)].append(record)

    output: List[Dict[str, Any]] = []
    for (target, obstacle), rows in buckets.items():
        if len(rows) < min_count:
            continue
        capture = []
        for row in rows:
            effective_rr = max(1e-6, _safe_float(row.get("effective_rr2"), 0.0))
            capture.append(_safe_float(row.get("realized_r")) / effective_rr)
        output.append(
            {
                "target_source": target,
                "obstacle_kind": obstacle,
                "count": len(rows),
                "avg_r": _mean(_safe_float(row.get("realized_r")) for row in rows),
                "avg_capture_ratio": _mean(capture),
                "win_rate": _mean(1.0 if _safe_float(row.get("realized_r")) > 0.0 else 0.0 for row in rows),
                "avg_effective_rr2": _mean(_safe_float(row.get("effective_rr2")) for row in rows),
            }
        )
    output.sort(key=lambda row: (-row["avg_r"], -row["count"], row["target_source"], row["obstacle_kind"]))
    return output


def _snapshot_feature_report(records: List[Dict[str, Any]], min_count: int) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        snapshot_state = "with_snapshots" if (record.get("snapshot_htf_path") or record.get("snapshot_ltf_path")) else "no_snapshots"
        buckets[(snapshot_state, _group_key(record, "setup_class"))].append(record)
    output: List[Dict[str, Any]] = []
    for (snapshot_state, setup_class), rows in buckets.items():
        if len(rows) < min_count:
            continue
        output.append(
            {
                "snapshot_state": snapshot_state,
                "setup_class": setup_class,
                "count": len(rows),
                "avg_r": _mean(_safe_float(row.get("realized_r")) for row in rows),
                "avg_heuristic_expected_r_diagnostic": None,
                "avg_stop_quality": _mean(_safe_float(row.get("realized_stop_quality"), _safe_float(row.get("stop_quality_score"))) for row in rows),
                "win_rate": _mean(1.0 if _safe_float(row.get("realized_r")) > 0.0 else 0.0 for row in rows),
            }
        )
    output.sort(key=lambda row: (-row["avg_r"], -row["count"], row["snapshot_state"], row["setup_class"]))
    return output


def _split_recent(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ordered = sorted(rows, key=_record_ledger_time)
    if len(ordered) < 4:
        return ordered, []
    mid = len(ordered) // 2
    return ordered[:mid], ordered[mid:]


def _decision_thresholds() -> Dict[str, int]:
    total_action_trades = _env_int_any(
        ("EXPECTANCY_MIN_TRADES_BEFORE_ACTION", "POLICY_MIN_TOTAL_CLOSED_TRADES"),
        200,
        minimum=1,
    )
    first_action_trades = _env_int_any(
        ("EXPECTANCY_FIRST_ACTION_MIN_TRADES", "POLICY_FIRST_ACTION_MIN_TRADES"),
        30,
        minimum=1,
    )
    bucket_trades = _env_int_any(
        ("EXPECTANCY_MIN_BUCKET_TRADES_BEFORE_ACTION", "POLICY_MIN_BUCKET_TRADES"),
        50,
        minimum=1,
    )
    session_min = _env_int_any(
        ("EXPECTANCY_SESSION_WEEKDAY_MIN_TRADES", "POLICY_SESSION_WEEKDAY_MIN_TRADES"),
        max(first_action_trades, 30),
        minimum=1,
    )
    session_strong = _env_int_any(
        ("EXPECTANCY_SESSION_WEEKDAY_STRONG_TRADES", "POLICY_SESSION_WEEKDAY_STRONG_TRADES"),
        max(bucket_trades, session_min),
        minimum=session_min,
    )
    session_upgrade = _env_int_any(
        ("EXPECTANCY_SESSION_WEEKDAY_UPGRADE_TRADES", "POLICY_SESSION_WEEKDAY_UPGRADE_TRADES"),
        max(session_strong + 10, session_strong),
        minimum=session_strong,
    )
    return {
        "total_action_trades": total_action_trades,
        "first_action_trades": first_action_trades,
        "bucket_trades": bucket_trades,
        "family_weak_trades": _env_int_any(
            ("EXPECTANCY_FAMILY_WEAK_TRADES", "POLICY_FAMILY_WEAK_TRADES"),
            first_action_trades,
            minimum=1,
        ),
        "family_suppress_trades": _env_int_any(
            ("EXPECTANCY_FAMILY_SUPPRESS_TRADES", "POLICY_FAMILY_SUPPRESS_TRADES"),
            max(bucket_trades, 50),
            minimum=1,
        ),
        "symbol_weak_trades": _env_int_any(
            ("EXPECTANCY_SYMBOL_WEAK_TRADES", "POLICY_SYMBOL_WEAK_TRADES"),
            first_action_trades,
            minimum=1,
        ),
        "symbol_suppress_trades": _env_int_any(
            ("EXPECTANCY_SYMBOL_SUPPRESS_TRADES", "POLICY_SYMBOL_SUPPRESS_TRADES"),
            max(bucket_trades, 50),
            minimum=1,
        ),
        "session_weekday_min_trades": session_min,
        "session_weekday_strong_trades": session_strong,
        "session_weekday_upgrade_trades": session_upgrade,
    }


def _policy_strength(sample_count: int, min_count: int, full_count: Optional[int] = None) -> float:
    if sample_count < min_count:
        return 0.0
    full_count = max(min_count + 1, full_count or min_count * 3)
    raw = (sample_count - min_count + 1) / max(1.0, float(full_count - min_count + 1))
    return _clamp(raw, 0.25, 1.0)


def _blend_from_neutral(neutral: float, target: float, strength: float) -> float:
    return neutral + (target - neutral) * _clamp(strength, 0.0, 1.0)


def _session_weekday_policy(records: List[Dict[str, Any]], min_count: int = 10) -> List[Dict[str, Any]]:
    thresholds = _decision_thresholds()
    min_supported_count = max(_safe_int(min_count, thresholds["session_weekday_min_trades"]), thresholds["session_weekday_min_trades"])
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        session = _group_key(record, "session_name")
        weekday = str(record.get("weekday_name") or "unknown")
        if session == "unknown" or weekday == "unknown":
            continue
        buckets[(weekday, session)].append(record)

    rows: List[Dict[str, Any]] = []
    for (weekday, session), subset in sorted(buckets.items()):
        stats = _summarize(subset, bootstrap_iterations=0)
        old_rows, recent_rows = _split_recent(subset)
        old_avg = _summarize(old_rows)["avg_r"] if old_rows else 0.0
        recent_avg = _summarize(recent_rows)["avg_r"] if recent_rows else stats["avg_r"]
        trend_delta = recent_avg - old_avg
        count = stats["count"]
        action = "monitor"
        reason = "insufficient_repeated_evidence"
        risk_multiplier = 1.0
        rr_floor_delta = 0.0
        score_bias = 0.0

        if count >= min_supported_count:
            weak = stats["avg_r"] < -0.10 or stats["profit_factor"] < 0.95
            very_weak = stats["avg_r"] < -0.25 or stats["profit_factor"] < 0.80 or stats["max_drawdown_r"] > 8.0
            improving = trend_delta > 0.15 and recent_avg > old_avg
            strong = stats["avg_r"] > 0.15 and stats["profit_factor"] > 1.15 and stats["win_rate"] >= 0.48
            very_strong = stats["avg_r"] > 0.25 and stats["profit_factor"] > 1.30 and stats["win_rate"] >= 0.52

            if very_weak and not improving and count >= thresholds["session_weekday_strong_trades"]:
                strength = _policy_strength(count, thresholds["session_weekday_strong_trades"], thresholds["session_weekday_strong_trades"] * 3)
                action = "penalize_session"
                reason = "repeated_session_weekday_losses"
                risk_multiplier = _blend_from_neutral(1.0, 0.50, strength)
                rr_floor_delta = _blend_from_neutral(0.0, 0.15, strength)
                score_bias = _blend_from_neutral(0.0, -4.0, strength)
            elif weak:
                strength = _policy_strength(count, min_supported_count, min_supported_count * 3)
                action = "penalize_risk"
                reason = "weak_session_weekday_expectancy"
                risk_multiplier = _blend_from_neutral(1.0, 0.70 if not improving else 0.85, strength)
                rr_floor_delta = _blend_from_neutral(0.0, 0.08 if not improving else 0.04, strength)
                score_bias = _blend_from_neutral(0.0, -2.0 if not improving else -1.0, strength)
            elif very_strong and count >= thresholds["session_weekday_upgrade_trades"]:
                strength = _policy_strength(count, thresholds["session_weekday_upgrade_trades"], thresholds["session_weekday_upgrade_trades"] * 3)
                action = "upgrade"
                reason = "strong_repeated_session_weekday_edge"
                risk_multiplier = _blend_from_neutral(1.0, 1.15, strength)
                rr_floor_delta = _blend_from_neutral(0.0, -0.05, strength)
                score_bias = _blend_from_neutral(0.0, 1.5, strength)
            elif strong and count >= thresholds["session_weekday_strong_trades"]:
                strength = _policy_strength(count, thresholds["session_weekday_strong_trades"], thresholds["session_weekday_strong_trades"] * 3)
                action = "watch_for_upgrade"
                reason = "promising_session_weekday_edge"
                risk_multiplier = _blend_from_neutral(1.0, 1.05, strength)
                rr_floor_delta = 0.0
                score_bias = _blend_from_neutral(0.0, 0.75, strength)

        rows.append(
            {
                "weekday": weekday,
                "session_name": session,
                "session_weekday_key": f"{weekday}|{session}",
                "action": action,
                "reason": reason,
                "risk_multiplier": round(risk_multiplier, 4),
                "rr_floor_delta": round(rr_floor_delta, 4),
                "score_bias": round(score_bias, 4),
                "sample_count": count,
                "avg_r": round(stats["avg_r"], 6),
                "win_rate": round(stats["win_rate"], 6),
                "profit_factor": round(stats["profit_factor"], 6),
                "max_drawdown_r": round(stats["max_drawdown_r"], 6),
                "recent_avg_r": round(recent_avg, 6),
                "previous_avg_r": round(old_avg, 6),
                "trend_delta_r": round(trend_delta, 6),
            }
        )
    rows.sort(
        key=lambda row: (
            {"penalize_session": 0, "penalize_risk": 1, "monitor": 2, "watch_for_upgrade": 3, "upgrade": 4}.get(row["action"], 9),
            row["avg_r"],
            -row["sample_count"],
            row["session_weekday_key"],
        )
    )
    return rows


def _family_rows(rows: List[Dict[str, Any]], min_count: int = 1) -> List[Dict[str, Any]]:
    summaries = _group_summary(rows, "setup_family", min_count=min_count, bootstrap_iterations=0)
    for item in summaries:
        item["setup_family"] = item.pop("bucket")
    return summaries


def _symbol_expectancy_policy(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    thresholds = _decision_thresholds()
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[_group_key(record, "symbol")].append(record)
    output: List[Dict[str, Any]] = []
    for symbol, rows in sorted(buckets.items()):
        stats = _summarize(rows, bootstrap_iterations=0)
        suppression = _strict_suppression_evidence(rows, thresholds["symbol_suppress_trades"])
        family_rows = _family_rows(rows, min_count=1)
        supported = [r for r in family_rows if r["count"] >= 10]
        profitable = [r for r in supported if r["avg_r"] > 0.0 and r["profit_factor"] >= 1.05]
        action = "allow"
        reason = f"exploration_sample_lt_{thresholds['symbol_weak_trades']}"
        if suppression["suppression_eligible"]:
            action = "suppress"
            reason = "strict_all_and_suppression_evidence_passed"
        elif stats["count"] >= thresholds["symbol_weak_trades"] and profitable:
            action = "family_only"
            reason = "profitable_family_supported"
        elif stats["count"] >= thresholds["symbol_weak_trades"] and stats["profit_factor"] < 1.05:
            action = "reduce_risk"
            reason = f"symbol_pf_lt_1_05_after_{thresholds['symbol_weak_trades']}"
        output.append(
            {
                "symbol": symbol,
                "symbol_policy_action": action,
                "symbol_policy_reason": reason,
                "best_setup_families": sorted(profitable, key=lambda r: (-r["avg_r"], -r["count"]))[:3],
                "worst_setup_families": sorted(supported or family_rows, key=lambda r: (r["avg_r"], -r["count"]))[:3],
                "stats": stats,
                "suppression_eligible": suppression["suppression_eligible"],
                "suppression_block_reasons": suppression["suppression_block_reasons"],
            }
        )
    return output


def _family_expectancy_policy(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    thresholds = _decision_thresholds()
    output: List[Dict[str, Any]] = []
    for row in _family_rows(records, min_count=1):
        count = row["count"]
        action = "allow"
        reason = f"exploration_sample_lt_{thresholds['family_weak_trades']}"
        risk_multiplier = 0.50 if count < thresholds["family_weak_trades"] else 1.0
        family_records = [record for record in records if _group_key(record, "setup_family") == row["setup_family"]]
        suppression = _strict_suppression_evidence(family_records, thresholds["family_suppress_trades"])
        if suppression["suppression_eligible"]:
            action = "suppress"
            reason = "strict_all_and_suppression_evidence_passed"
            risk_multiplier = 0.0
        elif count >= thresholds["family_weak_trades"] and row["avg_r"] <= 0.0:
            action = "reduce_risk"
            reason = f"weak_expectancy_after_{thresholds['family_weak_trades']}"
            risk_multiplier = 0.50
        elif count >= thresholds["family_suppress_trades"] and row["avg_r"] > 0.0 and row["profit_factor"] > 1.10:
            action = "allow"
            reason = "positive_after_costs_supported"
        if action in {"reduce_risk", "suppress"}:
            min_count_for_action = thresholds["family_suppress_trades"] if action == "suppress" else thresholds["family_weak_trades"]
            strength = _policy_strength(count, min_count_for_action, min_count_for_action * 3)
            risk_multiplier = _blend_from_neutral(1.0, risk_multiplier, strength)
        row.update(
            {
                "family_policy_action": action,
                "family_policy_reason": reason,
                "risk_multiplier": risk_multiplier,
                "suppression_eligible": suppression["suppression_eligible"],
                "suppression_block_reasons": suppression["suppression_block_reasons"],
            }
        )
        output.append(row)
    output.sort(key=lambda r: (r["family_policy_action"], -r["avg_r"], -r["count"], r["setup_family"]))
    return output


def _adaptive_policy_readiness(records: List[Dict[str, Any]], walk_forward: List[Dict[str, Any]]) -> Dict[str, Any]:
    overall = _summarize(records, bootstrap_iterations=0)
    family_counts = Counter(_group_key(record, "setup_family") for record in records)
    symbol_counts = Counter(_group_key(record, "symbol") for record in records)
    wf_summary = _aggregate_walk_forward(walk_forward)
    thresholds = _decision_thresholds()
    checks = {
        f"total_trades_ge_{thresholds['total_action_trades']}": overall["count"] >= thresholds["total_action_trades"],
        f"family_trades_ge_{thresholds['bucket_trades']}": bool(family_counts) and max(family_counts.values()) >= thresholds["bucket_trades"],
        f"symbol_trades_ge_{thresholds['symbol_weak_trades']}": bool(symbol_counts) and max(symbol_counts.values()) >= thresholds["symbol_weak_trades"],
        "profit_factor_after_costs_gt_1_10": overall["profit_factor"] > 1.10,
        "walk_forward_positive_fold_rate_ge_60pct": wf_summary["positive_fold_rate"] >= 0.60,
    }
    return {
        "activate_adaptive_policy": all(checks.values()),
        "checks": checks,
        "walk_forward_summary": wf_summary,
    }


def _overfitting_diagnostics(context_bundle: Dict[str, Any]) -> Dict[str, Any]:
    global_wf = context_bundle.get("global_walk_forward_summary", {})
    context_diag = context_bundle.get("context_diagnostics", [])
    unstable = [row for row in context_diag if row.get("avg_gap", 0.0) > 0.40 or row.get("positive_fold_rate", 0.0) < 0.40]
    return {
        "global_walk_forward": global_wf,
        "unstable_bucket_count": len(unstable),
        "unstable_buckets": unstable[:20],
        "max_avg_gap": max((_safe_float(row.get("avg_gap")) for row in context_diag), default=0.0),
        "avg_bucket_gap": _mean(_safe_float(row.get("avg_gap")) for row in context_diag),
    }


FAMILY_INPUT_HINTS: Dict[str, Dict[str, str]] = {
    "micro_continuation_fvg": {
        "enable": "InpEnableContinuationReentry",
        "floor": "InpSetupFloorContinuationFamily",
        "rr": "InpMinRRContinuation",
    },
    "continuation": {
        "enable": "InpEnableContinuationReentry",
        "floor": "InpSetupFloorContinuation",
        "rr": "InpMinRRContinuation",
    },
    "micro_range_reentry": {
        "enable": "InpEnableRangeReentry",
        "floor": "InpSetupFloorRangeFamily",
        "rr": "InpMinRRRange",
    },
    "range": {
        "enable": "InpEnableRangeReentry",
        "floor": "InpSetupFloorRange",
        "rr": "InpMinRRRange",
    },
    "micro_po3_reversal": {
        "floor": "InpSetupFloorMicroPO3",
        "rr": "InpMinRRMicroPO3",
        "llm_quality_score_input": "InpAiScoreMicroPO3",
    },
    "full_po3": {
        "floor": "InpSetupFloorFullPO3",
        "rr": "InpMinRRFullPO3",
        "llm_quality_score_input": "InpAiScoreFullPO3",
    },
    "micro_bisi_sibi_edge": {
        "enable": "InpEnableFvgEdge / InpEnableBreakerRetest",
        "floor": "InpSetupFloorMicroBisiSibi",
        "rr": "InpMinRRMicroPO3",
    },
    "failed_breakout": {
        "floor": "InpSetupFloorFailedBreakout",
        "rr": "InpMinRRFailedBreakout",
        "llm_quality_score_input": "InpAiScoreFailedBreakout",
    },
}


def _family_hint(name: str) -> Dict[str, str]:
    key = str(name or "").strip().lower()
    if key in FAMILY_INPUT_HINTS:
        return FAMILY_INPUT_HINTS[key]
    for needle, hint in FAMILY_INPUT_HINTS.items():
        if needle in key:
            return hint
    return {}


def _add_input_recommendation(
    rows: List[Dict[str, Any]],
    *,
    category: str,
    severity: str,
    action: str,
    inputs: List[str],
    suggested_change: str,
    reason: str,
    evidence: Dict[str, Any],
    min_valid_trades: int = 0,
) -> None:
    rows.append(
        {
            "category": category,
            "severity": severity,
            "action": action,
            "inputs": inputs,
            "suggested_change": suggested_change,
            "reason": reason,
            "evidence": evidence,
            "min_valid_trades": min_valid_trades,
            "auto_apply": False,
        }
    )


def _input_recommendations(
    report: Dict[str, Any],
    context_policy: List[Dict[str, Any]],
    subtype_policy: List[Dict[str, Any]],
    target_efficiency: List[Dict[str, Any]],
    snapshot_feature: List[Dict[str, Any]],
    active_policy: Dict[str, Any],
    governance: Dict[str, Any],
    data_quality: Dict[str, Any],
) -> Dict[str, Any]:
    overall = report.get("overall") or {}
    rows: List[Dict[str, Any]] = []
    count = _safe_int(overall.get("count"))
    ignored = _safe_int(data_quality.get("ignored_records"))
    loaded = _safe_int(data_quality.get("loaded_files"))
    pf = _safe_float(governance.get("profit_factor_after_costs"), _safe_float(overall.get("profit_factor")))
    min_pf = _safe_float(governance.get("min_profit_factor_after_costs"), 1.15)
    drawdown = _safe_float(governance.get("max_drawdown_r"), _safe_float(overall.get("max_drawdown_r")))
    max_dd = _safe_float(governance.get("max_allowed_drawdown_r"), 12.0)
    wf = (report.get("adaptive_policy_readiness") or {}).get("walk_forward_summary") or {}
    thresholds = _decision_thresholds()

    if loaded > 0 and count == 0:
        _add_input_recommendation(
            rows,
            category="data_quality",
            severity="critical",
            action="block_input_optimization",
            inputs=["trade_result_*.json", "position_id", "planned_entry", "planned_sl", "entry_branch", "realized_r"],
            suggested_change="Do not tune performance inputs from this sample. Collect fresh closed trades after the analytics metadata fix.",
            reason="All loaded trade-result records were rejected by the quality gate.",
            evidence={
                "loaded_files": loaded,
                "ignored_records": ignored,
                "ignored_reasons": data_quality.get("ignored_reasons", {}),
            },
            min_valid_trades=thresholds["first_action_trades"],
        )
    elif count < thresholds["first_action_trades"]:
        _add_input_recommendation(
            rows,
            category="evidence",
            severity="info",
            action="collect_more_valid_trades",
            inputs=["InpAnalyticsEnable", "InpAutoAnalyticsRefresh", "InpPolicyShadowMode"],
            suggested_change=f"Keep analytics on and shadow mode on. Wait for at least {thresholds['first_action_trades']} valid closed trades before tuning performance inputs.",
            reason=f"Fewer than {thresholds['first_action_trades']} valid trades is exploration only.",
            evidence={"valid_trades": count, "minimum_for_first_weak_signal_action": thresholds["first_action_trades"]},
            min_valid_trades=thresholds["first_action_trades"],
        )

    if count >= thresholds["first_action_trades"] and pf < min_pf:
        _add_input_recommendation(
            rows,
            category="global_edge",
            severity="high" if count >= 200 else "medium",
            action="tighten_selectivity_and_risk",
            inputs=[
                "InpRiskPerTradePct",
                "InpMinLiveRR2",
                "InpStandardTradeCostRCeiling",
                "InpSetupFloorFullPO3 / InpSetupFloorMicroPO3 / family floors",
            ],
            suggested_change="Reduce risk 25-50%, raise minimum live RR by about +0.10R, lower trade-cost ceiling by 0.03-0.05R, and raise weak-family setup floors by 2-5 points.",
            reason="Profit factor is below the governed activation threshold, so frequency should be traded for quality.",
            evidence={"valid_trades": count, "profit_factor": pf, "required_profit_factor": min_pf},
            min_valid_trades=thresholds["first_action_trades"],
        )

    if count >= thresholds["first_action_trades"] and drawdown > max_dd:
        _add_input_recommendation(
            rows,
            category="risk_path",
            severity="high",
            action="reduce_exposure_until_drawdown_recovers",
            inputs=[
                "InpRiskPerTradePct",
                "InpMaxOpenPositions",
                "InpMaxTradesPerScan",
                "InpMaxTotalRiskEnable",
                "InpMaxTotalRiskMoney",
                "InpPolicyShadowMode",
            ],
            suggested_change="Keep shadow mode on, cut per-trade risk, reduce concurrent exposure, and enable aggregate risk caps before allowing adaptive activation.",
            reason="Max drawdown is above the governed safety limit.",
            evidence={"valid_trades": count, "max_drawdown_r": drawdown, "allowed_max_drawdown_r": max_dd},
            min_valid_trades=thresholds["first_action_trades"],
        )

    if count >= thresholds["bucket_trades"] and _safe_float(wf.get("positive_fold_rate")) < _safe_float(governance.get("min_positive_fold_rate"), 0.60):
        _add_input_recommendation(
            rows,
            category="stability",
            severity="high",
            action="do_not_live_activate_unstable_learning",
            inputs=["InpPolicyShadowMode", "InpPolicyMinPositiveFoldRate", "InpPolicyMaxTrainTestGapR"],
            suggested_change="Keep shadow mode on. Do not lower walk-forward gates to force activation; tighten the weak families instead.",
            reason="The edge is not stable enough across time folds.",
            evidence={
                "positive_fold_rate": _safe_float(wf.get("positive_fold_rate")),
                "required_positive_fold_rate": _safe_float(governance.get("min_positive_fold_rate"), 0.60),
                "avg_train_test_gap_r": _safe_float(wf.get("avg_gap")),
            },
            min_valid_trades=thresholds["bucket_trades"],
        )

    for row in report.get("family_expectancy_policy") or []:
        action = str(row.get("family_policy_action") or "")
        if action not in {"reduce_risk", "suppress"}:
            continue
        family = str(row.get("setup_family") or "")
        hint = _family_hint(family)
        inputs = [value for value in (hint.get("enable"), hint.get("floor"), hint.get("rr"), hint.get("llm_quality_score_input")) if value]
        if not inputs:
            inputs = ["active_policy.json family policy", "family-specific setup floors"]
        if action == "suppress":
            suggested = "Disable the branch/family where an input exists, or raise its setup floor by 5-8 points and set risk multiplier to 0 in policy."
            severity = "high"
        else:
            suggested = "Raise the family setup floor by 2-5 points, raise minimum RR by about +0.05-0.10R, and keep exploration risk reduced."
            severity = "medium"
        _add_input_recommendation(
            rows,
            category="setup_family",
            severity=severity,
            action=action,
            inputs=inputs,
            suggested_change=suggested,
            reason=str(row.get("family_policy_reason") or "weak_family_expectancy"),
            evidence={
                "setup_family": family,
                "count": row.get("count"),
                "avg_r": row.get("avg_r"),
                "profit_factor": row.get("profit_factor"),
            },
            min_valid_trades=thresholds["family_weak_trades"] if action == "reduce_risk" else thresholds["family_suppress_trades"],
        )

    for row in report.get("symbol_expectancy_policy") or []:
        action = str(row.get("symbol_policy_action") or "")
        if action not in {"reduce_risk", "suppress", "family_only"}:
            continue
        stats = row.get("stats") or {}
        _add_input_recommendation(
            rows,
            category="symbol_selection",
            severity="high" if action == "suppress" else "medium",
            action=action,
            inputs=["Market Watch symbol list", "InpSkipIfSymbolOpen", "InpMaxTradesPerScan"],
            suggested_change=(
                "Remove or pause this symbol from Market Watch until its bucket improves."
                if action == "suppress"
                else "Keep the symbol only for its profitable families and avoid broad scanning on it."
            ),
            reason=str(row.get("symbol_policy_reason") or "weak_symbol_expectancy"),
            evidence={
                "symbol": row.get("symbol"),
                "count": stats.get("count"),
                "avg_r": stats.get("avg_r"),
                "profit_factor": stats.get("profit_factor"),
            },
            min_valid_trades=thresholds["symbol_weak_trades"],
        )

    for row in context_policy[:20]:
        action = str(row.get("action") or "")
        if action not in {"downrank", "suppress"}:
            continue
        _add_input_recommendation(
            rows,
            category="context_policy",
            severity="high" if action == "suppress" else "medium",
            action=action,
            inputs=["active_policy.json context_policy", "InpPolicyShadowMode", "InpPolicyMinBucketTrades"],
            suggested_change="Keep this as governed policy pressure first; do not convert it into global input changes unless the same weakness repeats across multiple buckets.",
            reason="A specific regime/session/setup bucket has weak walk-forward expectancy.",
            evidence={
                "policy_bucket": row.get("policy_bucket"),
                "sample_count": row.get("sample_count"),
                "score_bias": row.get("score_bias"),
                "risk_multiplier": row.get("risk_multiplier"),
                "expected_value_bias": row.get("expected_value_bias"),
            },
            min_valid_trades=_safe_int(governance.get("min_bucket_trades"), 50),
        )

    for row in subtype_policy[:20]:
        action = str(row.get("action") or "")
        if action not in {"downrank", "suppress"}:
            continue
        _add_input_recommendation(
            rows,
            category="subtype_policy",
            severity="high" if action == "suppress" else "medium",
            action=action,
            inputs=["active_policy.json subtype_policy", "InpPolicyShadowMode"],
            suggested_change="Let subtype policy penalize this pattern. Convert to a hard input disable only after the same branch/family is also weak.",
            reason="Subtype proof is weak after shrinkage and bootstrap checks.",
            evidence={
                "subtype_key": row.get("subtype_key"),
                "sample_count": row.get("sample_count"),
                "shrunk_win_rate": row.get("shrunk_win_rate"),
                "avg_r": row.get("avg_r"),
                "score_penalty": row.get("score_penalty"),
                "risk_multiplier": row.get("risk_multiplier"),
            },
            min_valid_trades=_safe_int(governance.get("min_bucket_trades"), 50),
        )

    weak_targets = [row for row in target_efficiency if _safe_int(row.get("count")) >= 10 and _safe_float(row.get("avg_capture_ratio")) < 0.20]
    for row in weak_targets[:5]:
        _add_input_recommendation(
            rows,
            category="target_quality",
            severity="medium",
            action="tighten_target_acceptance",
            inputs=["InpMinLiveRR2", "InpStandardTradeLiquidityRRFloor", "InpMaxTargetAtrMult", "InpMaxTargetAdrFrac"],
            suggested_change="Demand better real target quality before entry; avoid long synthetic targets where capture ratio stays weak.",
            reason="Target capture is weak for this target/obstacle combination.",
            evidence=row,
            min_valid_trades=10,
        )

    if snapshot_feature:
        with_snap = [row for row in snapshot_feature if row.get("snapshot_state") == "with_snapshots"]
        no_snap = [row for row in snapshot_feature if row.get("snapshot_state") == "no_snapshots"]
        if with_snap and no_snap:
            best_with = max(with_snap, key=lambda row: _safe_float(row.get("avg_r")))
            best_no = max(no_snap, key=lambda row: _safe_float(row.get("avg_r")))
            if _safe_float(best_with.get("avg_r")) > _safe_float(best_no.get("avg_r")) + 0.20:
                _add_input_recommendation(
                    rows,
                    category="ai_context",
                    severity="medium",
                    action="prefer_snapshot_audits",
                    inputs=["InpUseSnapshotAI", "InpRequireSnapshots"],
                    suggested_change="Enable snapshot AI for the setup classes where snapshot-backed decisions outperform non-snapshot decisions; require snapshots only after latency is acceptable.",
                    reason="Snapshot-backed decisions have materially better realized R in the report.",
                    evidence={"best_with_snapshots": best_with, "best_without_snapshots": best_no},
                    min_valid_trades=10,
                )

    rows.sort(
        key=lambda row: (
            {"critical": 0, "high": 1, "medium": 2, "info": 3}.get(str(row.get("severity")), 9),
            str(row.get("category")),
            str(row.get("action")),
        )
    )
    return {
        "mode": "advisory_only",
        "auto_apply": False,
        "valid_trades": count,
        "decision_thresholds": thresholds,
        "editable_scope": DECISION_MAKER_EDIT_SCOPE,
        "recommendation_count": len(rows),
        "recommendations": rows,
        "notes": [
            "Recommendations are generated from valid closed-trade evidence only.",
            "The report never lowers governance safety gates just to force activation.",
            "Apply input changes manually or through a separately governed change workflow.",
        ],
        "active_policy_reference": {
            "policy_id": active_policy.get("policy_id", ""),
            "activation_state": active_policy.get("activation_state", ""),
            "shadow_mode": active_policy.get("shadow_mode", True),
        },
    }


def _load_active_policy(policy_root: Path) -> Optional[Dict[str, Any]]:
    path = policy_root / "active_policy.json"
    if not path.exists():
        return None
    try:
        return _read_json_any_encoding(path)
    except Exception:
        return None


def _load_previous_policy_state(policy_root: Path) -> Optional[Dict[str, Any]]:
    active = _load_active_policy(policy_root)
    if active:
        return active
    snapshots_dir = policy_root / "snapshots"
    if not snapshots_dir.exists():
        return None
    snapshots = sorted(snapshots_dir.glob("policy_*.json"), key=lambda path: path.stat().st_mtime)
    for path in reversed(snapshots):
        try:
            snapshot = _read_json_any_encoding(path)
        except Exception:
            continue
        previous = snapshot.get("active_policy")
        if isinstance(previous, dict):
            return previous
    return None


def _change_rate_check(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> Dict[str, Any]:
    if not previous:
        return {"passed": True, "changed_fields": [], "change_count": 0}
    changed_fields: List[str] = []
    for field in (
        "soft_setup_floor",
        "hard_setup_floor",
        "runner_sequence_floor",
        "runner_liquidity_rr_floor",
        "runner_cost_r_ceiling",
        "runner_alignment_floor",
        "runner_adverse_ceiling",
    ):
        prev = _safe_float(previous.get(field), math.nan)
        cur = _safe_float(current.get(field), math.nan)
        if math.isnan(prev) or math.isnan(cur):
            continue
        scale = max(abs(prev), 1.0)
        if abs(cur - prev) > scale * 0.15:
            changed_fields.append(field)
    return {
        "passed": len(changed_fields) <= 3,
        "changed_fields": changed_fields,
        "change_count": len(changed_fields),
    }


def _active_policy_step_floor(field: str) -> float:
    if field in {"soft_setup_floor", "hard_setup_floor"}:
        return 1.0
    if field in {"runner_sequence_floor", "runner_alignment_floor", "runner_adverse_ceiling"}:
        return 0.20
    if field in {"runner_liquidity_rr_floor", "runner_cost_r_ceiling", "runner_ev_floor"}:
        return 0.03
    if field in {"ote_softness_frac"}:
        return 0.01
    return 0.02


def _step_toward(previous: float, target: float, field: str) -> float:
    if not math.isfinite(previous) or not math.isfinite(target):
        return target
    delta = target - previous
    if abs(delta) <= 1e-12:
        return target
    max_step_frac = _env_float_any(("POLICY_MAX_STEP_FRACTION", "EXPECTANCY_MAX_STEP_FRACTION"), 0.05, minimum=0.001)
    max_step = max(_active_policy_step_floor(field), abs(previous) * max_step_frac)
    return previous + _clamp(delta, -max_step, max_step)


def _apply_gradual_active_policy(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> Dict[str, Any]:
    fields = [
        "soft_setup_floor",
        "hard_setup_floor",
        "setup_floor_penalty_mult",
        "ote_softness_frac",
        "default_risk_multiplier",
        "runner_sequence_floor",
        "runner_liquidity_rr_floor",
        "runner_cost_r_ceiling",
        "runner_alignment_floor",
        "runner_adverse_ceiling",
        "runner_ev_floor",
    ]
    targets = {field: current.get(field) for field in fields if field in current}
    current["policy_targets"] = targets
    current["gradual_change_enabled"] = True
    current["max_step_fraction"] = _env_float_any(("POLICY_MAX_STEP_FRACTION", "EXPECTANCY_MAX_STEP_FRACTION"), 0.05, minimum=0.001)
    if not previous:
        current["gradual_changed_fields"] = []
        return current

    changed: List[str] = []
    for field, target_value in targets.items():
        previous_value = _safe_float(previous.get(field), math.nan)
        target = _safe_float(target_value, math.nan)
        if math.isnan(previous_value) or math.isnan(target):
            continue
        stepped = _step_toward(previous_value, target, field)
        if abs(stepped - target) > 1e-9:
            changed.append(field)
        current[field] = round(stepped, 4)
    current["gradual_changed_fields"] = changed
    return current


def _derive_active_policy(
    records: List[Dict[str, Any]],
    context_bundle: Dict[str, Any],
    subtype_rows: List[Dict[str, Any]],
    previous_active: Optional[Dict[str, Any]],
    evidence_min_trades: int,
    min_bucket_trades: Optional[int] = None,
    min_profit_factor_after_costs: Optional[float] = None,
    max_train_test_gap_r: Optional[float] = None,
    min_positive_fold_rate: Optional[float] = None,
    max_drawdown_r: Optional[float] = None,
) -> Dict[str, Any]:
    overall = _summarize(records, bootstrap_iterations=200)
    winners = [record for record in records if _safe_float(record.get("realized_r")) > 0.0]
    runner_winners = [record for record in winners if _safe_bool(record.get("runner_trade"))]
    if not runner_winners:
        runner_winners = winners

    runner_sequence_floor = _runner_quantile(runner_winners, "sequence_quality", 0.30, 6.6)
    runner_liquidity_rr_floor = _runner_quantile(runner_winners, "liquidity_rr", 0.30, 2.1)
    runner_cost_r_ceiling = _runner_quantile(runner_winners, "execution_cost_r", 0.75, 0.22)
    runner_alignment_floor = _runner_quantile(runner_winners, "htf_alignment_score", 0.30, 6.1)
    runner_adverse_ceiling = _runner_quantile(runner_winners, "adverse_context_score", 0.75, 3.2)

    global_wf = context_bundle.get("global_walk_forward_summary", {})
    max_relevant_bucket = max(
        [0]
        + [_safe_int(row.get("sample_count")) for row in subtype_rows]
        + [_safe_int(row.get("sample_count")) for row in context_bundle.get("context_diagnostics", [])]
    )
    min_total_trades = max(1, _safe_int(evidence_min_trades, 200))
    min_bucket_trades = max(
        1,
        _safe_int(
            min_bucket_trades
            if min_bucket_trades is not None
            else os.getenv("POLICY_MIN_BUCKET_TRADES", "50"),
            50,
        ),
    )
    max_gap_r = _safe_float(
        max_train_test_gap_r
        if max_train_test_gap_r is not None
        else os.getenv("POLICY_MAX_TRAIN_TEST_GAP_R", "0.25"),
        0.25,
    )
    min_positive_fold_rate = _safe_float(
        min_positive_fold_rate
        if min_positive_fold_rate is not None
        else os.getenv("POLICY_MIN_POSITIVE_FOLD_RATE", "0.60"),
        0.60,
    )
    min_pf_after_costs = _safe_float(
        min_profit_factor_after_costs
        if min_profit_factor_after_costs is not None
        else os.getenv("POLICY_MIN_PROFIT_FACTOR_AFTER_COSTS", "1.15"),
        1.15,
    )
    max_drawdown_r = _safe_float(
        max_drawdown_r
        if max_drawdown_r is not None
        else os.getenv("POLICY_MAX_DRAWDOWN_R", "12.0"),
        12.0,
    )
    evidence_score = _clamp(
        len(records) / max(float(min_total_trades), 1.0)
        + overall["bootstrap_positive_rate"]
        + max(0.0, _safe_float(overall.get("r_ci_low"))),
        0.0,
        4.0,
    )
    evidence_passed = (
        len(records) >= min_total_trades
        and max_relevant_bucket >= min_bucket_trades
        and bool(overall.get("uncertainty_available"))
        and _safe_float(overall.get("r_ci_low")) > 0.0
        and overall["profit_factor"] > min_pf_after_costs
        and overall["max_drawdown_r"] <= max_drawdown_r
    )
    walk_forward_passed = (
        _safe_float(global_wf.get("avg_test_r")) > 0.0
        and _safe_float(global_wf.get("positive_fold_rate")) >= min_positive_fold_rate
        and abs(_safe_float(global_wf.get("avg_gap"))) <= max_gap_r
    )

    current = {
        "policy_id": "",
        "version": 0,
        "activated_at": 0,
        "evidence_score": round(evidence_score, 4),
        "evidence_passed": evidence_passed,
        "walk_forward_passed": walk_forward_passed,
        "change_rate_passed": True,
        "legacy_setup_score_authority": False,
        "calibrated_probability_authority": False,
        "expected_net_r_authority": False,
        "setup_floor_penalty_mult": 0.75,
        "ote_softness_frac": 0.08,
        "default_risk_multiplier": round(_clamp(0.9 + overall["avg_r"] * 0.10, 0.75, 1.0), 4),
        "runner_sequence_floor": round(runner_sequence_floor, 4),
        "runner_liquidity_rr_floor": round(runner_liquidity_rr_floor, 4),
        "runner_cost_r_ceiling": round(runner_cost_r_ceiling, 4),
        "runner_alignment_floor": round(runner_alignment_floor, 4),
        "runner_adverse_ceiling": round(runner_adverse_ceiling, 4),
        "runner_ev_floor_available": False,
    }
    current = _apply_gradual_active_policy(previous_active, current)
    change_check = _change_rate_check(previous_active, current)
    current["change_rate_passed"] = bool(change_check["passed"])
    return {
        "active_policy": current,
        "governance": {
            "evidence_score": round(evidence_score, 4),
            "evidence_passed": evidence_passed,
            "walk_forward_passed": walk_forward_passed,
            "change_rate_passed": bool(change_check["passed"]),
            "min_total_trades": min_total_trades,
            "min_bucket_trades": min_bucket_trades,
            "max_relevant_bucket": max_relevant_bucket,
            "r_ci_low": overall.get("r_ci_low"),
            "uncertainty_available": bool(overall.get("uncertainty_available")),
            "profit_factor_after_costs": overall["profit_factor"],
            "max_drawdown_r": overall["max_drawdown_r"],
            "min_profit_factor_after_costs": min_pf_after_costs,
            "max_allowed_drawdown_r": max_drawdown_r,
            "min_positive_fold_rate": min_positive_fold_rate,
            "max_train_test_gap_r": max_gap_r,
            "change_count": change_check["change_count"],
            "changed_fields": change_check["changed_fields"],
            "activate_ok": evidence_passed and walk_forward_passed and bool(change_check["passed"]),
            "subtype_policy_count": len(subtype_rows),
        },
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_ndjson(path: Path, rows: List[Dict[str, Any]]) -> None:
   path.parent.mkdir(parents=True, exist_ok=True)
   with path.open("w", encoding="utf-8") as handle:
       for row in rows:
           handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _snapshot_stub_policy_rows(rows: List[Dict[str, Any]], policy_id: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in {"model", "fit_stats", "walk_forward", "walk_forward_summary", "stats"}}
        item["policy_id"] = policy_id
        output.append(item)
    return output


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _expectancy_ai_config() -> Dict[str, Any]:
    return {
        "enabled": _env_bool("EXPECTANCY_AI_AUDIT_ENABLE", True),
        "model": os.getenv("EXPECTANCY_AI_MODEL", "gpt-5.5").strip() or "gpt-5.5",
        "reasoning_effort": os.getenv("EXPECTANCY_AI_REASONING_EFFORT", "high").strip().lower() or "high",
        "decision_weight": _clamp(_safe_float(os.getenv("EXPECTANCY_AI_DECISION_WEIGHT", "0.75"), 0.75), 0.0, 1.0),
        "max_output_tokens": max(1024, _safe_int(os.getenv("EXPECTANCY_AI_MAX_OUTPUT_TOKENS", "6000"), 6000)),
    }


def _expectancy_ai_audit_model() -> Any:
    try:
        from pydantic import BaseModel, Field
    except ImportError:
        return None

    class MajorDecisionReview(BaseModel):
        decision: str = Field(default="")
        agree: bool = Field(default=False)
        reason: str = Field(default="")
        recommended_change: str = Field(default="")

    class GlobalAdjustments(BaseModel):
        block_live_activation: bool = Field(default=False)
        risk_multiplier_bias: float = Field(default=0.0)
        rr_floor_delta_bias: float = Field(default=0.0)

    class InputRecommendation(BaseModel):
        severity: str = Field(default="medium")
        category: str = Field(default="ai_audit")
        action: str = Field(default="review_ai_audit")
        suggested_change: str = Field(default="")
        reason: str = Field(default="")

    class ExpectancyAIAuditDecision(BaseModel):
        model_config = {"protected_namespaces": ()}
        agree: bool = Field(default=False)
        confidence: float = Field(default=0.0, ge=0.0, le=1.0)
        risk_level: str = Field(default="medium")
        summary: str = Field(default="")
        major_decision_reviews: list[MajorDecisionReview] = Field(default_factory=list)
        global_adjustments: GlobalAdjustments = Field(default_factory=GlobalAdjustments)
        input_recommendations: list[InputRecommendation] = Field(default_factory=list)

    return ExpectancyAIAuditDecision


def _plain_model_dump(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _ai_decision_pack(suite: Dict[str, Any]) -> Dict[str, Any]:
    report = suite.get("report") or {}
    return {
        "generated_at": suite.get("generated_at"),
        "overall": report.get("overall", {}),
        "virtual_ledger": (suite.get("system_trade_history") or {}).get("summary", {}),
        "data_quality": suite.get("data_quality", {}),
        "governance": suite.get("governance", {}),
        "active_policy": suite.get("active_policy", {}),
        "input_recommendations": {
            "mode": (suite.get("input_recommendations") or {}).get("mode"),
            "valid_trades": (suite.get("input_recommendations") or {}).get("valid_trades"),
            "decision_thresholds": (suite.get("input_recommendations") or {}).get("decision_thresholds"),
            "recommendations": (suite.get("input_recommendations") or {}).get("recommendations", [])[:15],
        },
        "decision_maker_edit_scope": DECISION_MAKER_EDIT_SCOPE,
        "context_policy": suite.get("context_policy", [])[:20],
        "subtype_policy": suite.get("subtype_policy", [])[:20],
        "session_weekday_policy": suite.get("session_weekday_policy", [])[:24],
        "symbol_expectancy_policy": suite.get("symbol_expectancy_policy", [])[:12],
        "family_expectancy_policy": suite.get("family_expectancy_policy", [])[:12],
        "adaptive_policy_readiness": suite.get("adaptive_policy_readiness", {}),
    }


def _ai_audit_state_path(policy_root: Path) -> Path:
    return policy_root / "expectancy_ai_audit_state.json"


def _compact_action_rows(rows: Any, key_fields: Tuple[str, ...], limit: int = 30) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    output: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or row.get("symbol_policy_action") or row.get("family_policy_action") or row.get("session_weekday_policy_action") or "")
        if action in {"", "monitor", "none", "neutral"}:
            continue
        item = {"action": action}
        for field in key_fields:
            if field in row:
                item[field] = row.get(field)
        output.append(item)
        if len(output) >= limit:
            break
    return output


def _compact_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    return value


def _actionable_ai_audit_decisions(suite: Dict[str, Any]) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    active = suite.get("active_policy") or {}
    changed_fields = active.get("gradual_changed_fields") if isinstance(active.get("gradual_changed_fields"), list) else []
    policy_targets = active.get("policy_targets") if isinstance(active.get("policy_targets"), dict) else {}
    for field in sorted(str(item) for item in changed_fields if str(item or "").strip()):
        decisions.append(
            {
                "scope": "active_policy",
                "action": "adjust_policy_target",
                "field": field,
                "value": _compact_value(active.get(field)),
                "target": _compact_value(policy_targets.get(field)),
            }
        )

    input_recs = suite.get("input_recommendations") or {}
    rec_rows = input_recs.get("recommendations") if isinstance(input_recs.get("recommendations"), list) else []
    passive_rec_actions = {
        "",
        "collect_more_valid_trades",
        "block_input_optimization",
        "review_ai_audit",
    }
    for row in rec_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") == "expectancy_ai_audit":
            continue
        action = str(row.get("action") or "").strip()
        category = str(row.get("category") or "").strip()
        if action in passive_rec_actions:
            continue
        decisions.append(
            {
                "scope": "input_recommendation",
                "category": category,
                "severity": str(row.get("severity") or ""),
                "action": action,
                "inputs": row.get("inputs") if isinstance(row.get("inputs"), list) else [],
                "suggested_change": str(row.get("suggested_change") or ""),
                "min_valid_trades": _safe_int(row.get("min_valid_trades"), 0),
            }
        )
        if len(decisions) >= 60:
            break

    policy_sources = (
        ("context_policy", suite.get("context_policy"), "action", {"downrank", "suppress"}, ("policy_bucket", "score_bias", "risk_multiplier", "expected_value_bias", "sample_count")),
        ("subtype_policy", suite.get("subtype_policy"), "action", {"downrank", "suppress"}, ("subtype_key", "score_penalty", "risk_multiplier", "sample_count")),
        ("session_weekday_policy", suite.get("session_weekday_policy"), "action", {"penalize_session", "penalize_risk", "upgrade", "watch_for_upgrade"}, ("session_weekday_key", "weekday", "session_name", "risk_multiplier", "rr_floor_delta", "score_bias", "sample_count")),
        ("symbol_expectancy_policy", suite.get("symbol_expectancy_policy"), "symbol_policy_action", {"suppress", "reduce_risk", "family_only"}, ("symbol", "symbol_policy_reason")),
        ("family_expectancy_policy", suite.get("family_expectancy_policy"), "family_policy_action", {"suppress", "reduce_risk"}, ("setup_family", "family_policy_reason", "risk_multiplier", "count")),
    )
    for scope, rows, action_field, actionable, fields in policy_sources:
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            action = str(row.get(action_field) or "").strip()
            if action not in actionable:
                continue
            item: Dict[str, Any] = {"scope": scope, "action": action}
            for field in fields:
                if field in row:
                    item[field] = _compact_value(row.get(field))
            decisions.append(item)
            if len(decisions) >= 100:
                return decisions

    return decisions


def _major_decision_fingerprint(suite: Dict[str, Any]) -> str:
    payload = {"actionable_decisions": _actionable_ai_audit_decisions(suite)}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_ai_audit_state(policy_root: Path) -> Dict[str, Any]:
    path = _ai_audit_state_path(policy_root)
    if path.exists():
        try:
            state = _read_json_any_encoding(path)
            if isinstance(state, dict):
                return state
        except Exception:
            pass

    snapshots_dir = policy_root / "snapshots"
    if not snapshots_dir.exists():
        return {}
    try:
        snapshots = sorted(snapshots_dir.glob("policy_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return {}
    for snapshot_path in snapshots:
        try:
            snapshot = _read_json_any_encoding(snapshot_path)
        except Exception:
            continue
        audit = snapshot.get("ai_audit") if isinstance(snapshot, dict) else {}
        if not isinstance(audit, dict) or audit.get("status") != "ok":
            continue
        report = snapshot.get("report") if isinstance(snapshot.get("report"), dict) else {}
        overall = report.get("overall") if isinstance(report.get("overall"), dict) else {}
        input_recs = snapshot.get("input_recommendations") if isinstance(snapshot.get("input_recommendations"), dict) else {}
        valid_trades = _safe_int(overall.get("count"), _safe_int(input_recs.get("valid_trades"), 0))
        return {
            "last_success_at": _safe_int(snapshot.get("generated_at"), int(snapshot_path.stat().st_mtime)),
            "valid_trades": valid_trades,
            "model": audit.get("model"),
            "reasoning_effort": audit.get("reasoning_effort"),
            "fingerprint": _major_decision_fingerprint(snapshot),
            "actionable_decision_count": len(_actionable_ai_audit_decisions(snapshot)),
            "snapshot_path": str(snapshot_path),
        }
    return {}


def _write_ai_audit_state(policy_root: Path, suite: Dict[str, Any], audit: Dict[str, Any], fingerprint: str, trigger_reason: str) -> None:
    report = suite.get("report") or {}
    overall = report.get("overall") or {}
    input_recs = suite.get("input_recommendations") or {}
    state = {
        "last_success_at": int(time.time()),
        "valid_trades": _safe_int(overall.get("count"), _safe_int(input_recs.get("valid_trades"), 0)),
        "model": audit.get("model"),
        "reasoning_effort": audit.get("reasoning_effort"),
        "fingerprint": fingerprint,
        "actionable_decision_count": len(_actionable_ai_audit_decisions(suite)),
        "trigger_reason": trigger_reason,
        "policy_id": (suite.get("active_policy") or {}).get("policy_id"),
    }
    try:
        policy_root.mkdir(parents=True, exist_ok=True)
        _write_json(_ai_audit_state_path(policy_root), state)
    except Exception:
        pass


def _expectancy_ai_skip_audit(
    cfg: Dict[str, Any],
    *,
    reason: str,
    valid_trades: int,
    fingerprint: str,
    state: Dict[str, Any],
    actionable_decision_count: int,
) -> Dict[str, Any]:
    return {
        "enabled": cfg["enabled"],
        "model": cfg["model"],
        "reasoning_effort": cfg["reasoning_effort"],
        "decision_weight": cfg["decision_weight"],
        "status": "skipped",
        "agree": None,
        "confidence": 0.0,
        "weighted_confidence": 0.0,
        "activation_veto": False,
        "summary": f"Skipped expectancy AI audit: {reason}.",
        "skip_reason": reason,
        "valid_trades": valid_trades,
        "decision_fingerprint": fingerprint,
        "last_success_at": state.get("last_success_at"),
        "last_success_valid_trades": state.get("valid_trades"),
        "last_success_model": state.get("model"),
        "actionable_decision_count": actionable_decision_count,
    }


def _should_run_expectancy_ai_audit(suite: Dict[str, Any], policy_root: Path, cfg: Dict[str, Any]) -> Tuple[bool, str, str, Dict[str, Any]]:
    thresholds = suite.get("decision_thresholds") or _decision_thresholds()
    report = suite.get("report") or {}
    overall = report.get("overall") or {}
    input_recs = suite.get("input_recommendations") or {}
    valid_trades = _safe_int(overall.get("count"), _safe_int(input_recs.get("valid_trades"), 0))
    actionable_decisions = _actionable_ai_audit_decisions(suite)
    actionable_count = len(actionable_decisions)
    min_valid = _env_int_any(
        ("EXPECTANCY_AI_MIN_VALID_TRADES", "EXPECTANCY_FIRST_ACTION_MIN_TRADES", "POLICY_FIRST_ACTION_MIN_TRADES"),
        _safe_int(thresholds.get("first_action_trades"), 30),
        minimum=0,
    )
    min_new_trades = _env_int_any(
        ("EXPECTANCY_AI_AUDIT_MIN_NEW_TRADES", "EXPECTANCY_MIN_TRADES_BEFORE_ACTION", "POLICY_MIN_TOTAL_CLOSED_TRADES"),
        _safe_int(thresholds.get("total_action_trades"), 200),
        minimum=1,
    )
    fingerprint = _major_decision_fingerprint(suite)
    state = _load_ai_audit_state(policy_root)

    if actionable_count <= 0:
        return False, "no_actionable_input_change_penalty_or_upgrade", fingerprint, state
    if valid_trades < min_valid:
        return False, f"valid_trades_below_ai_min:{valid_trades}<{min_valid}", fingerprint, state
    if not state:
        return True, "first_actionable_ai_audit_for_current_history", fingerprint, state
    previous_valid = _safe_int(state.get("valid_trades"), 0)
    new_trades = max(0, valid_trades - previous_valid)
    if str(state.get("fingerprint") or "") == fingerprint:
        return False, f"same_actionable_decision_and_only_{new_trades}_new_trades", fingerprint, state
    if new_trades < min_new_trades:
        return False, f"actionable_decision_changed_but_new_trades_below_ai_cadence:{new_trades}<{min_new_trades}", fingerprint, state
    return True, f"actionable_input_change_penalty_or_upgrade_after_{new_trades}_new_trades", fingerprint, state


def _expectancy_ai_audit(suite: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _expectancy_ai_config()
    audit: Dict[str, Any] = {
        "enabled": cfg["enabled"],
        "model": cfg["model"],
        "reasoning_effort": cfg["reasoning_effort"],
        "decision_weight": cfg["decision_weight"],
        "status": "skipped",
        "agree": None,
        "confidence": 0.0,
        "weighted_confidence": 0.0,
        "activation_veto": False,
        "summary": "",
    }
    if not cfg["enabled"]:
        audit["summary"] = "EXPECTANCY_AI_AUDIT_ENABLE is off."
        return audit

    try:
        provider = _EXPECTANCY_AI_PROVIDER
        if provider is None:
            raise RuntimeError("expectancy_ai_provider_not_injected")
        system_prompt = (
            "You are a conservative quantitative trading risk auditor for a PO3/FVG scalping system. "
            "Review only the provided evidence. You may disagree with mechanical policy decisions, "
            "but you must not recommend live activation when data quality or governance is weak. "
            "Only recommend changes inside decision_maker_edit_scope; if a useful change is outside that scope, flag it as out_of_scope. "
            "Return strict JSON only."
        )
        schema_prompt = {
            "required_json_shape": {
                "agree": "boolean, whether you agree with the major mechanical decisions",
                "confidence": "number 0..1",
                "risk_level": "low|medium|high|critical",
                "summary": "short explanation",
                "major_decision_reviews": [
                    {
                        "decision": "active_policy|context_policy|subtype_policy|session_weekday_policy|input_recommendations|data_quality",
                        "agree": "boolean",
                        "reason": "short evidence-based reason",
                        "recommended_change": "short concrete adjustment or empty string",
                    }
                ],
                "global_adjustments": {
                    "block_live_activation": "boolean",
                    "risk_multiplier_bias": "number, negative reduces risk globally, positive increases cautiously",
                    "rr_floor_delta_bias": "number, positive tightens RR, negative loosens cautiously",
                },
                "input_recommendations": [
                    {
                        "severity": "critical|high|medium|info",
                        "category": "ai_audit",
                        "action": "short action",
                        "suggested_change": "short concrete change",
                        "reason": "evidence-based reason",
                    }
                ],
            }
        }
        user_payload = {
            "instructions": schema_prompt,
            "decision_pack": _ai_decision_pack(suite),
        }
        schema_model = _expectancy_ai_audit_model()
        if schema_model is None:
            raise RuntimeError("expectancy_ai_schema_dependency_unavailable")
        request_id = str(
            (suite.get("active_policy") or {}).get("policy_id")
            or (suite.get("governance") or {}).get("generated_at")
            or suite.get("generated_at")
            or ""
        )
        result = provider.generate_structured(
            role="analytics",
            system_prompt=system_prompt,
            evidence=user_payload,
            response_schema=schema_model,
            request_metadata={
                "request_id": request_id,
                "workload_mode": "analytics",
                "non_trading": True,
                "max_output_tokens": cfg["max_output_tokens"],
            },
        )
        parsed = _plain_model_dump(result.parsed)
        log_ai_usage(
            source="expectancy_report",
            operation="expectancy_ai_audit.provider_neutral_structured",
            model=result.actual_model,
            response=result.raw_response,
            request_id=request_id,
            reasoning_effort=(
                cfg["reasoning_effort"] if result.provider_mode == "REMOTE_API" else ""
            ),
            max_output_tokens=cfg["max_output_tokens"],
            provider_mode=result.provider_mode,
            provider_id=result.provider_id,
            endpoint_class=result.endpoint_class,
            model_fingerprint=result.model_fingerprint,
            tokens_per_second=result.tokens_per_second,
            estimated_context_tokens=result.estimated_context_tokens,
            extra={"role": "analytics", "non_trading": True},
        )
        audit.update(parsed)
        audit["status"] = "ok"
        audit["model"] = result.actual_model
        audit["provider_mode"] = result.provider_mode
        audit["provider_id"] = result.provider_id
        audit["endpoint_class"] = result.endpoint_class
        audit["model_fingerprint"] = result.model_fingerprint or "unavailable"
        audit["generation_settings_hash"] = result.generation_settings_hash
        audit["retry_count"] = result.retry_count
        audit["latency_sec"] = round(result.latency_sec, 6)
        audit["confidence"] = _clamp(_safe_float(audit.get("confidence"), 0.0), 0.0, 1.0)
        audit["weighted_confidence"] = round(audit["confidence"] * cfg["decision_weight"], 6)
        audit["decision_weight"] = cfg["decision_weight"]
        adjustments = audit.get("global_adjustments") if isinstance(audit.get("global_adjustments"), dict) else {}
        disagree_veto = audit.get("agree") is False and audit["weighted_confidence"] >= 0.55
        requested_veto = _safe_bool(adjustments.get("block_live_activation"), False) and audit["weighted_confidence"] >= 0.50
        audit["activation_veto"] = bool(disagree_veto or requested_veto)
    except Exception as exc:
        audit["status"] = "error"
        audit["activation_veto"] = True
        audit["summary"] = f"provider_neutral_expectancy_audit_failed:{type(exc).__name__}:{exc}"
    return audit


def _merge_ai_recommendations(input_recommendations: Dict[str, Any], ai_audit: Dict[str, Any]) -> None:
    rows = input_recommendations.setdefault("recommendations", [])
    additions = ai_audit.get("input_recommendations") if isinstance(ai_audit.get("input_recommendations"), list) else []
    for item in additions[:10]:
        if not isinstance(item, dict):
            continue
        row = {
            "severity": str(item.get("severity") or "medium"),
            "category": str(item.get("category") or "ai_audit"),
            "action": str(item.get("action") or "review_ai_audit"),
            "inputs": item.get("inputs") if isinstance(item.get("inputs"), list) else ["EXPECTANCY_AI_AUDIT_ENABLE", "expectancy_ai_audit.json"],
            "suggested_change": str(item.get("suggested_change") or ""),
            "reason": str(item.get("reason") or ""),
            "evidence": {"ai_model": ai_audit.get("model"), "ai_confidence": ai_audit.get("confidence")},
            "min_valid_trades": _safe_int(input_recommendations.get("valid_trades"), 0),
            "source": "expectancy_ai_audit",
        }
        rows.append(row)
    input_recommendations["recommendation_count"] = len(rows)


def _apply_ai_audit_to_suite(suite: Dict[str, Any], policy_root: Optional[Path] = None) -> Dict[str, Any]:
    cfg = _expectancy_ai_config()
    trigger_reason = ""
    if cfg["enabled"] and policy_root is not None:
        should_run, trigger_reason, fingerprint, state = _should_run_expectancy_ai_audit(suite, policy_root, cfg)
        actionable_count = len(_actionable_ai_audit_decisions(suite))
        if should_run:
            ai_audit = _expectancy_ai_audit(suite)
            ai_audit["trigger_reason"] = trigger_reason
            ai_audit["decision_fingerprint"] = fingerprint
            ai_audit["actionable_decision_count"] = actionable_count
            if ai_audit.get("status") == "ok":
                _write_ai_audit_state(policy_root, suite, ai_audit, fingerprint, trigger_reason)
        else:
            report = suite.get("report") or {}
            overall = report.get("overall") or {}
            input_recs = suite.get("input_recommendations") or {}
            valid_trades = _safe_int(overall.get("count"), _safe_int(input_recs.get("valid_trades"), 0))
            ai_audit = _expectancy_ai_skip_audit(
                cfg,
                reason=trigger_reason,
                valid_trades=valid_trades,
                fingerprint=fingerprint,
                state=state,
                actionable_decision_count=actionable_count,
            )
    else:
        ai_audit = _expectancy_ai_audit(suite)
    suite["ai_audit"] = ai_audit
    _merge_ai_recommendations(suite.get("input_recommendations", {}), ai_audit)
    governance = suite.get("governance") or {}
    active_policy = suite.get("active_policy") or {}
    governance["ai_audit_status"] = ai_audit.get("status")
    governance["ai_audit_trigger_reason"] = ai_audit.get("trigger_reason") or ai_audit.get("skip_reason") or trigger_reason
    governance["ai_review_passed"] = not bool(ai_audit.get("activation_veto"))
    governance["ai_decision_weight"] = ai_audit.get("decision_weight", 0.0)
    if ai_audit.get("activation_veto"):
        governance["activate_ok"] = False
        active_policy["activation_state"] = "blocked_ai_audit"
        active_policy["ai_review_passed"] = False
        active_policy["ai_review_summary"] = str(ai_audit.get("summary") or "")
    else:
        active_policy["ai_review_passed"] = True
    return ai_audit


def _fmt_money(value: Any) -> str:
    return f"{_safe_float(value):,.2f}"


def _fmt_pct(value: Any) -> str:
    return f"{_safe_float(value):.2f}%"


def _render_dashboard_html(suite: Dict[str, Any]) -> str:
    title = os.getenv("PO3_DASHBOARD_TITLE", "PO3 Codex Virtual Ledger").strip() or "PO3 Codex Virtual Ledger"
    report = suite.get("report") or {}
    overall = report.get("overall") or {}
    ledger = (suite.get("system_trade_history") or {}).get("summary") or {}
    ai_audit = suite.get("ai_audit") or {}
    source_paths = suite.get("source_paths") or {}
    cards = [
        ("Virtual Balance", _fmt_money(ledger.get("virtual_balance")), "System-owned ledger, not broker balance"),
        ("Virtual Equity", _fmt_money(ledger.get("virtual_equity")), "Same as balance until open-trade MTM is added"),
        ("Gross Balance", _fmt_money(ledger.get("gross_balance")), "Start balance plus gross winners"),
        ("Total Trades", str(_safe_int(overall.get("count"))), "Valid closed trades only"),
        ("Win Rate", _fmt_pct(_safe_float(overall.get("win_rate")) * 100.0), "Closed winners / valid trades"),
        ("Profit Factor", f"{_safe_float(overall.get('profit_factor')):.2f}", "Gross R winners / gross R losers"),
        ("Avg RR", f"{_safe_float(overall.get('avg_r')):.3f}R", "Mean realized R"),
        ("Profitable Days", str(_safe_int(ledger.get("profitable_days"))), "Virtual daily PnL above zero"),
        ("Losing Days", str(_safe_int(ledger.get("losing_days"))), "Virtual daily PnL below zero"),
        ("Best Trade", _fmt_pct(ledger.get("best_trade_pct")), "Best trade as % of 100,000"),
        ("Worst Trade", _fmt_pct(ledger.get("worst_trade_pct")), "Worst trade as % of 100,000"),
        ("Best Weekday", str(ledger.get("best_weekday") or "unknown"), "By average virtual %"),
        ("Worst Weekday", str(ledger.get("worst_weekday") or "unknown"), "By average virtual %"),
        ("AI Audit", str(ai_audit.get("status") or "not_run"), f"veto={bool(ai_audit.get('activation_veto'))}"),
    ]
    card_html = "\n".join(
        f"""
        <section class="card">
          <div class="card-label">{html.escape(label)}</div>
          <div class="card-value">{html.escape(value)}</div>
          <div class="card-note">{html.escape(note)}</div>
        </section>
        """
        for label, value, note in cards
    )
    session_rows = suite.get("session_weekday_policy") or []
    row_html = "\n".join(
        f"""
        <tr>
          <td>{html.escape(str(row.get('session_weekday_key') or ''))}</td>
          <td><span class="pill {html.escape(str(row.get('action') or 'monitor'))}">{html.escape(str(row.get('action') or 'monitor'))}</span></td>
          <td>{_safe_int(row.get('sample_count'))}</td>
          <td>{_safe_float(row.get('avg_r')):.3f}</td>
          <td>{_safe_float(row.get('profit_factor')):.2f}</td>
          <td>{_safe_float(row.get('risk_multiplier')):.2f}</td>
        </tr>
        """
        for row in session_rows[:10]
    )
    if not row_html:
        row_html = '<tr><td colspan="6">No session-weekday evidence yet.</td></tr>'
    generated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(_safe_int(suite.get("generated_at"), int(time.time()))))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: #f8fbff;
      background:
        radial-gradient(circle at 10% 10%, rgba(0, 209, 255, .34), transparent 28rem),
        radial-gradient(circle at 90% 15%, rgba(255, 64, 129, .32), transparent 24rem),
        linear-gradient(135deg, #0b1020 0%, #172554 42%, #32115f 100%);
    }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 44px 22px 56px; }}
    .hero {{ display: flex; justify-content: space-between; gap: 20px; align-items: end; margin-bottom: 26px; }}
    h1 {{ margin: 0; font-size: clamp(32px, 4vw, 56px); letter-spacing: -0.04em; }}
    .subtitle {{ margin-top: 10px; color: #c8d7ff; font-size: 15px; }}
    .badge {{ padding: 10px 14px; border-radius: 999px; background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.22); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; }}
    .card, .panel {{
      border: 1px solid rgba(255,255,255,.20);
      background: linear-gradient(180deg, rgba(255,255,255,.16), rgba(255,255,255,.08));
      border-radius: 24px;
      box-shadow: 0 24px 70px rgba(0,0,0,.28);
      backdrop-filter: blur(18px);
    }}
    .card {{ padding: 20px; min-height: 120px; }}
    .card-label {{ color: #a8c7ff; font-size: 13px; text-transform: uppercase; letter-spacing: .08em; }}
    .card-value {{ margin-top: 12px; font-size: 30px; font-weight: 800; letter-spacing: -0.03em; }}
    .card-note {{ margin-top: 10px; color: #d9e4ff; font-size: 13px; line-height: 1.35; }}
    .panel {{ margin-top: 18px; padding: 22px; overflow: auto; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 720px; }}
    th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid rgba(255,255,255,.13); }}
    th {{ color: #b9d3ff; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .pill {{ display: inline-block; padding: 5px 9px; border-radius: 999px; background: rgba(255,255,255,.14); }}
    .penalize_session, .penalize_risk {{ background: rgba(255, 92, 122, .25); color: #ffd0d9; }}
    .upgrade, .watch_for_upgrade {{ background: rgba(64, 255, 175, .22); color: #c8ffe8; }}
    .monitor {{ background: rgba(148, 163, 184, .22); color: #e2e8f0; }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div>
        <h1>{html.escape(title)}</h1>
        <div class="subtitle">Virtual system history starts from 100,000 and ignores broker/account balance distortion.</div>
        <div class="subtitle">Data source: {html.escape(str(source_paths.get('logs_dir') or 'MT5 Common Files / PO3_AI_BUS'))}</div>
      </div>
      <div class="badge">Generated {html.escape(generated)}</div>
    </header>
    <div class="grid">{card_html}</div>
    <section class="panel">
      <h2>Session x Weekday Decisions</h2>
      <table>
        <thead><tr><th>Bucket</th><th>Action</th><th>Trades</th><th>Avg R</th><th>PF</th><th>Risk Mult</th></tr></thead>
        <tbody>{row_html}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def _dashboard_output_path() -> Optional[Path]:
    raw = os.getenv("PO3_DASHBOARD_OUTPUT", "dashboard.html").strip()
    if not raw or raw.lower() in {"0", "false", "off", "none"}:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


def _write_dashboard_outputs(analytics_root: Path, suite: Dict[str, Any]) -> None:
    dashboard_html = _render_dashboard_html(suite)
    analytics_path = analytics_root / "dashboard.html"
    analytics_path.write_text(dashboard_html, encoding="utf-8")
    python_path = _dashboard_output_path()
    if python_path:
        try:
            if python_path.resolve() != analytics_path.resolve():
                python_path.parent.mkdir(parents=True, exist_ok=True)
                python_path.write_text(dashboard_html, encoding="utf-8")
        except FileNotFoundError:
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text(dashboard_html, encoding="utf-8")


def _load_snapshot(snapshot_path: Path) -> Dict[str, Any]:
    return _read_json_any_encoding(snapshot_path)


def _activate_snapshot(snapshot_path: Path, policy_root: Path) -> Dict[str, Any]:
    snapshot = _load_snapshot(snapshot_path)
    active_policy = dict(snapshot["active_policy"])
    if active_policy.get("shadow_mode") or active_policy.get("activation_state") == "shadow":
        raise RuntimeError("Refusing to activate shadow-mode policy snapshot.")
    active_policy["policy_id"] = snapshot["policy_id"]
    active_policy["version"] = snapshot["version"]
    active_policy["activated_at"] = int(time.time())
    _write_json(policy_root / "active_policy.json", active_policy)
    _write_ndjson(policy_root / "context_policy.ndjson", _snapshot_stub_policy_rows(snapshot.get("context_policy", []), snapshot["policy_id"]))
    _write_ndjson(policy_root / "subtype_policy.ndjson", _snapshot_stub_policy_rows(snapshot.get("subtype_policy", []), snapshot["policy_id"]))
    _write_ndjson(policy_root / "session_weekday_policy.ndjson", _snapshot_stub_policy_rows(snapshot.get("session_weekday_policy", []), snapshot["policy_id"]))
    activation = {
        "policy_id": snapshot["policy_id"],
        "version": snapshot["version"],
        "activated_at": active_policy["activated_at"],
    }
    _write_json(policy_root / "activation_state.json", activation)
    return activation


def rollback_policy(policy_root: Path, policy_id: str = "", steps: int = 1) -> Dict[str, Any]:
    snapshots_dir = policy_root / "snapshots"
    snapshots = sorted(snapshots_dir.glob("policy_*.json"))
    if not snapshots:
        raise FileNotFoundError("No policy snapshots available for rollback.")
    target: Optional[Path] = None
    if policy_id:
        for path in snapshots:
            if path.stem == policy_id:
                target = path
                break
        if target is None:
            raise FileNotFoundError(f"Policy snapshot '{policy_id}' not found.")
    else:
        index = max(0, len(snapshots) - max(1, steps))
        target = snapshots[index]
    activation = _activate_snapshot(target, policy_root)
    return {"rolled_back_to": target.stem, "activation": activation}


def _render_markdown(suite: Dict[str, Any]) -> str:
    report = suite["report"]
    overall = report["overall"]
    governance = suite["governance"]
    data_quality = suite.get("data_quality") or report.get("data_quality") or {}
    lines: List[str] = []
    lines.append("# Expectancy Report")
    lines.append("")
    lines.append(f"Trades analyzed: {overall['count']}")
    lines.append(f"Avg R: {overall['avg_r']:.3f}")
    if overall.get("uncertainty_available"):
        lines.append(f"R 90% day-block CI: [{_safe_float(overall.get('r_ci_low')):.3f}, {_safe_float(overall.get('r_ci_high')):.3f}]")
    else:
        lines.append("R uncertainty: unavailable (insufficient independent day blocks)")
    history = suite.get("system_trade_history") or {}
    ledger = history.get("summary") or {}
    if ledger:
        lines.append(f"Virtual balance: {ledger.get('virtual_balance', 0.0):.2f}")
        lines.append(f"Virtual net %: {ledger.get('net_pct', 0.0):.3f}%")
    lines.append("")
    if data_quality:
        lines.append("## Data Quality")
        lines.append("")
        lines.append(f"- Result files found: {data_quality.get('loaded_files', 0)}")
        lines.append(f"- Records analyzed after filters: {data_quality.get('analyzed_records', data_quality.get('used_records', 0))}")
        lines.append(f"- Records ignored: {data_quality.get('ignored_records', 0)}")
        enriched = data_quality.get("identity_enriched_from_trade_key_meta", 0)
        if enriched:
            lines.append(f"- IDs recovered from trade metadata: {enriched}")
        reasons = data_quality.get("ignored_reasons") or {}
        for reason, count in reasons.items():
            lines.append(f"- Ignored {reason}: {count}")
        lines.append("")
    input_recs = suite.get("input_recommendations") or {}
    rec_rows = input_recs.get("recommendations") or []
    if input_recs:
        lines.append("## Input Recommendations")
        lines.append("")
        lines.append(f"- Mode: {input_recs.get('mode', 'advisory_only')}")
        lines.append(f"- Auto-apply: {bool(input_recs.get('auto_apply'))}")
        thresholds = input_recs.get("decision_thresholds") or suite.get("decision_thresholds") or {}
        if thresholds:
            lines.append(f"- First weak-signal action after: {thresholds.get('first_action_trades', 30)} valid trades")
            lines.append(f"- Governed activation after: {thresholds.get('total_action_trades', 200)} valid trades")
            lines.append(f"- Bucket action threshold: {thresholds.get('bucket_trades', 50)} valid trades")
        if not rec_rows:
            lines.append("- No input changes recommended.")
        else:
            for row in rec_rows[:12]:
                inputs = ", ".join(str(item) for item in row.get("inputs", []) if item)
                lines.append(
                    f"- {row.get('severity', 'info')} {row.get('category', 'general')}: "
                    f"{row.get('action', 'review')} -> {row.get('suggested_change', '')} "
                    f"[inputs: {inputs}]"
                )
        lines.append("")
    scope = suite.get("decision_maker_edit_scope") or DECISION_MAKER_EDIT_SCOPE
    if scope:
        lines.append("## Decision Maker Edit Scope")
        lines.append("")
        for path_name, fields in (scope.get("runtime_policy_files") or {}).items():
            lines.append(f"- {path_name}: {', '.join(str(field) for field in fields)}")
        lines.append("- MT5 inputs are advisory recommendations only; the report does not rewrite input files.")
        lines.append("")
    lines.append("## Governance")
    lines.append("")
    lines.append(f"- Evidence passed: {governance['evidence_passed']}")
    lines.append(f"- Walk-forward passed: {governance['walk_forward_passed']}")
    lines.append(f"- Change-rate passed: {governance['change_rate_passed']}")
    if "ai_review_passed" in governance:
        lines.append(f"- AI review passed: {governance.get('ai_review_passed')}")
        lines.append(f"- AI audit status: {governance.get('ai_audit_status', 'not_run')}")
    lines.append(f"- Auto-activate allowed: {governance['activate_ok']}")
    lines.append("")
    ai_audit = suite.get("ai_audit") or {}
    if ai_audit:
        lines.append("## AI Audit")
        lines.append("")
        lines.append(f"- Status: {ai_audit.get('status', 'unknown')}")
        lines.append(f"- Model: {ai_audit.get('model', '')}")
        lines.append(f"- Agree: {ai_audit.get('agree')}")
        lines.append(f"- Weighted confidence: {_safe_float(ai_audit.get('weighted_confidence')):.3f}")
        lines.append(f"- Activation veto: {bool(ai_audit.get('activation_veto'))}")
        if ai_audit.get("summary"):
            lines.append(f"- Summary: {ai_audit.get('summary')}")
        lines.append("")
    lines.append("## Context Policies")
    lines.append("")
    context_rows = suite["context_policy"][:12]
    if not context_rows:
        lines.append("- Not enough supported buckets")
    else:
        for row in context_rows:
            lines.append(
                f"- {row['policy_bucket']}: action={row['action']}, n={row['sample_count']}, "
                f"score_bias={row['score_bias']:.2f}, risk_mult={row['risk_multiplier']:.2f}, "
                f"ev_bias={row['expected_value_bias']:.2f}"
            )
    lines.append("")
    lines.append("## Subtype Proof")
    lines.append("")
    subtype_rows = suite["subtype_policy"][:12]
    if not subtype_rows:
        lines.append("- Not enough supported subtypes")
    else:
        for row in subtype_rows:
            lines.append(
                f"- {row['subtype_key']}: action={row['action']}, n={row['sample_count']}, "
                f"shrunk_wr={row['shrunk_win_rate']:.2%}, avg_r={row['avg_r']:.3f}, "
                f"risk_mult={row['risk_multiplier']:.2f}"
            )
    lines.append("")
    lines.append("## Overfitting")
    lines.append("")
    overfit = suite["overfitting_diagnostics"]
    lines.append(f"- Unstable bucket count: {overfit['unstable_bucket_count']}")
    lines.append(f"- Global avg train/test gap: {overfit['global_walk_forward'].get('avg_gap', 0.0):.3f}")
    lines.append("")
    session_rows = suite.get("session_weekday_policy") or []
    lines.append("## Session Weekday Policy")
    lines.append("")
    if not session_rows:
        lines.append("- No supported session-weekday samples yet")
    else:
        for row in session_rows[:12]:
            lines.append(
                f"- {row['session_weekday_key']}: action={row['action']}, n={row['sample_count']}, "
                f"avg_r={row['avg_r']:.3f}, pf={row['profit_factor']:.2f}, "
                f"risk_mult={row['risk_multiplier']:.2f}, rr_delta={row['rr_floor_delta']:.2f}"
            )
    lines.append("")
    readiness = report.get("adaptive_policy_readiness") or {}
    lines.append("## Adaptive Readiness")
    lines.append("")
    lines.append(f"- Activate adaptive policy: {bool(readiness.get('activate_adaptive_policy'))}")
    for key, passed in (readiness.get("checks") or {}).items():
        lines.append(f"- {key}: {bool(passed)}")
    lines.append("")
    family_rows = report.get("family_expectancy_policy") or []
    lines.append("## Family Expectancy")
    lines.append("")
    if not family_rows:
        lines.append("- No family samples yet")
    else:
        for row in family_rows[:12]:
            lines.append(
                f"- {row['setup_family']}: action={row['family_policy_action']}, n={row['count']}, "
                f"pf={row['profit_factor']:.2f}, avg_r={row['avg_r']:.3f}, "
                f"avg_cost_r={row['avg_execution_cost_r']:.3f}, duration_min={row['avg_duration_minutes']:.1f}"
            )
    lines.append("")
    symbol_rows = report.get("symbol_expectancy_policy") or []
    lines.append("## Symbol Expectancy")
    lines.append("")
    if not symbol_rows:
        lines.append("- No symbol samples yet")
    else:
        for row in symbol_rows[:12]:
            stats = row.get("stats") or {}
            lines.append(
                f"- {row['symbol']}: action={row['symbol_policy_action']}, n={stats.get('count', 0)}, "
                f"pf={stats.get('profit_factor', 0.0):.2f}, avg_r={stats.get('avg_r', 0.0):.3f}, "
                f"reason={row['symbol_policy_reason']}"
            )
    lines.append("")
    funnel = suite.get("setup_funnel")
    if funnel:
        lines.extend(_render_setup_funnel(funnel))
        lines.append("")
    return "\n".join(lines)


def build_report(
    records: List[Dict[str, Any]],
    min_count: int,
    top_n: int,
    walk_forward_folds: int,
    min_symbol_samples: int,
    min_session_samples: int,
    min_regime_samples: int,
    nested_validation_folds: int,
    bootstrap_iterations: int,
    max_threshold_changes: int,
    effective_rr_weight: float,
) -> Dict[str, Any]:
    del top_n, min_symbol_samples, min_session_samples, min_regime_samples, nested_validation_folds, max_threshold_changes, effective_rr_weight
    grouped_fields = [
        "symbol",
        "session_name",
        "in_killzone",
        "setup_family",
        "entry_branch",
        "po3_subtype",
        "fvg_subtype",
        "regime_bucket",
        "volatility_profile",
        "setup_class",
        "llm_quality_score_bucket",
        "execution_cost_bucket",
        "ai_decision_source",
        "weekday_name",
        "session_weekday_key",
    ]
    walk_forward = _walk_forward_scorecard(records, walk_forward_folds)
    return {
        "overall": _summarize(records, bootstrap_iterations=bootstrap_iterations),
        "grouped": {
            field: _group_summary(records, field, min_count=min_count, bootstrap_iterations=max(0, bootstrap_iterations // 2))
            for field in grouped_fields
        },
        "family_expectancy_policy": _family_expectancy_policy(records),
        "symbol_expectancy_policy": _symbol_expectancy_policy(records),
        "adaptive_policy_readiness": _adaptive_policy_readiness(records, walk_forward),
        "walk_forward": walk_forward,
    }


def run_analytics_suite(
    logs_dir: Path,
    analytics_root: Path,
    policy_root: Path,
    include_sources: Optional[set[str]] = None,
    exclude_sources: Optional[set[str]] = None,
    auto_activate: bool = False,
    min_bucket_samples: int = 50,
    min_subtype_samples: int = 50,
    min_group_count: int = 5,
    walk_forward_folds: int = 3,
    bootstrap_iterations: int = 200,
    evidence_min_trades: int = 200,
    policy_shadow_mode: Optional[bool] = None,
    policy_min_bucket_trades: Optional[int] = None,
    policy_min_profit_factor_after_costs: Optional[float] = None,
    policy_max_train_test_gap_r: Optional[float] = None,
    policy_min_positive_fold_rate: Optional[float] = None,
    policy_max_drawdown_r: Optional[float] = None,
    include_suspicious: Optional[bool] = None,
    experiment_id: str = "",
    experiment_hypothesis: str = "",
    experiment_registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    include_sources = include_sources or set()
    exclude_sources = exclude_sources or set()
    records, data_quality = _load_records_with_quality(logs_dir, include_suspicious=include_suspicious)
    records_before_source_filter = len(records)
    records = _filter_ai_decision_sources(records, include_sources, exclude_sources)
    data_quality["source_filter_excluded"] = records_before_source_filter - len(records)
    data_quality["analyzed_records"] = len(records)
    ledger_report = dict(data_quality.get("central_ledger_audit") or {})
    ledger_status = str(ledger_report.get("global_status") or LedgerIntegrityStatus.QUARANTINED.value)
    virtual_start_balance = _virtual_start_balance()
    records = _decorate_virtual_ledger(records, virtual_start_balance)
    system_history = _system_trade_history(records, virtual_start_balance)

    report = build_report(
        records,
        min_count=min_group_count,
        top_n=10,
        walk_forward_folds=walk_forward_folds,
        min_symbol_samples=min_bucket_samples,
        min_session_samples=min_bucket_samples,
        min_regime_samples=min_bucket_samples,
        nested_validation_folds=walk_forward_folds,
        bootstrap_iterations=bootstrap_iterations,
        max_threshold_changes=1,
        effective_rr_weight=0.25,
    )
    report["data_quality"] = data_quality
    context_bundle = _build_context_policies(records, min_bucket_samples=min_bucket_samples, walk_forward_folds=walk_forward_folds)
    subtype_rows = _build_subtype_backtest(records, min_subtype_samples=min_subtype_samples, bootstrap_iterations=bootstrap_iterations)
    target_efficiency = _target_efficiency_report(records, min_count=min_group_count)
    snapshot_feature = _snapshot_feature_report(records, min_count=min_group_count)
    session_weekday_policy = _session_weekday_policy(records, min_count=max(10, min_group_count))
    overfitting = _overfitting_diagnostics(context_bundle)
    previous_active = _load_previous_policy_state(policy_root)
    active_bundle = _derive_active_policy(
        records,
        context_bundle=context_bundle,
        subtype_rows=subtype_rows,
        previous_active=previous_active,
        evidence_min_trades=evidence_min_trades,
        min_bucket_trades=policy_min_bucket_trades,
        min_profit_factor_after_costs=policy_min_profit_factor_after_costs,
        max_train_test_gap_r=policy_max_train_test_gap_r,
        min_positive_fold_rate=policy_min_positive_fold_rate,
        max_drawdown_r=policy_max_drawdown_r,
    )

    feature_manifest = write_feature_lineage(analytics_root / "feature_lineage.json")
    lineage_violations = feature_lineage_violations(feature_manifest)
    registry_path = experiment_registry_path or (policy_root / "experiment_registry.jsonl")
    registry = ExperimentRegistry(registry_path)
    experiment_authorized, experiment_authorization_reason = registry.optimization_authorized(
        experiment_id,
        experiment_hypothesis,
    )
    activation_contract_reasons: List[str] = []
    if ledger_status != LedgerIntegrityStatus.CLEAN.value:
        activation_contract_reasons.append("ledger_not_globally_clean")
    if lineage_violations:
        activation_contract_reasons.append("feature_lineage_invalid")
    if not experiment_authorized:
        activation_contract_reasons.append("experiment_not_authorized:" + experiment_authorization_reason)
    activation_contract_ok = not activation_contract_reasons

    engine_versions = sorted({str(row.get("engine_version") or "") for row in records if row.get("engine_version")})
    decision_versions = sorted({str(row.get("decision_schema_version") or "") for row in records if row.get("decision_schema_version")})
    calibration_artifact = run_shadow_calibration(
        records,
        ledger_status=ledger_status,
        engine_version=engine_versions[0] if len(engine_versions) == 1 else "",
        decision_schema_version=decision_versions[0] if len(decision_versions) == 1 else "",
    )

    next_version = _safe_int((previous_active or {}).get("version"), 0) + 1
    policy_id = f"policy_{int(time.time())}_v{next_version}"
    active_policy = dict(active_bundle["active_policy"])
    active_policy["policy_id"] = policy_id
    active_policy["version"] = next_version
    shadow_mode = (
        bool(policy_shadow_mode)
        if policy_shadow_mode is not None
        else os.getenv("POLICY_SHADOW_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    evidence_governance_passed = bool(active_bundle["governance"].get("activate_ok"))
    governance_passed = evidence_governance_passed and activation_contract_ok
    active_policy["shadow_mode"] = shadow_mode
    active_policy["activation_state"] = "shadow" if shadow_mode else ("eligible" if governance_passed else "blocked")
    active_bundle["governance"]["shadow_mode"] = shadow_mode
    active_bundle["governance"]["governance_passed"] = governance_passed
    active_bundle["governance"]["evidence_governance_passed"] = evidence_governance_passed
    active_bundle["governance"]["ledger_integrity_status"] = ledger_status
    active_bundle["governance"]["feature_lineage_version"] = FEATURE_LINEAGE_VERSION
    active_bundle["governance"]["feature_lineage_violations"] = lineage_violations
    active_bundle["governance"]["experiment_id"] = experiment_id
    active_bundle["governance"]["experiment_authorized"] = experiment_authorized
    active_bundle["governance"]["experiment_authorization_reason"] = experiment_authorization_reason
    active_bundle["governance"]["activation_contract_reasons"] = activation_contract_reasons
    active_bundle["governance"]["calibration_trading_activation"] = False
    active_bundle["governance"]["activate_ok"] = governance_passed and not shadow_mode

    policy_authority = governance_passed and not shadow_mode
    context_policy = _snapshot_stub_policy_rows(
        enforce_policy_governance(
            context_bundle["context_policies"],
            activation_allowed=policy_authority,
            block_reasons=activation_contract_reasons,
        ),
        policy_id,
    )
    subtype_policy = _snapshot_stub_policy_rows(
        enforce_policy_governance(
            subtype_rows,
            activation_allowed=policy_authority,
            block_reasons=activation_contract_reasons,
        ),
        policy_id,
    )
    session_weekday_policy = enforce_policy_governance(
        session_weekday_policy,
        activation_allowed=policy_authority,
        block_reasons=activation_contract_reasons,
    )
    report["symbol_expectancy_policy"] = enforce_policy_governance(
        report.get("symbol_expectancy_policy", []),
        activation_allowed=policy_authority,
        block_reasons=activation_contract_reasons,
    )
    report["family_expectancy_policy"] = enforce_policy_governance(
        report.get("family_expectancy_policy", []),
        activation_allowed=policy_authority,
        block_reasons=activation_contract_reasons,
    )
    input_recommendations = _input_recommendations(
        report=report,
        context_policy=context_policy,
        subtype_policy=subtype_policy,
        target_efficiency=target_efficiency,
        snapshot_feature=snapshot_feature,
        active_policy=active_policy,
        governance=active_bundle["governance"],
        data_quality=data_quality,
    )
    suite = {
        "generated_at": int(time.time()),
        "report": report,
        "context_policy": context_policy,
        "subtype_policy": subtype_policy,
        "symbol_expectancy_policy": report.get("symbol_expectancy_policy", []),
        "family_expectancy_policy": report.get("family_expectancy_policy", []),
        "adaptive_policy_readiness": report.get("adaptive_policy_readiness", {}),
        "target_efficiency": target_efficiency,
        "snapshot_feature_report": snapshot_feature,
        "session_weekday_policy": session_weekday_policy,
        "system_trade_history": system_history,
        "overfitting_diagnostics": overfitting,
        "active_policy": active_policy,
        "governance": active_bundle["governance"],
        "data_quality": data_quality,
        "input_recommendations": input_recommendations,
        "decision_maker_edit_scope": DECISION_MAKER_EDIT_SCOPE,
        "decision_thresholds": _decision_thresholds(),
        "ledger_integrity_report": ledger_report,
        "feature_lineage": feature_manifest,
        "shadow_calibration": calibration_artifact,
        "experiment_registry": {
            "path": str(registry_path),
            "experiment_id": experiment_id,
            "hypothesis": experiment_hypothesis,
            "authorized": experiment_authorized,
            "reason": experiment_authorization_reason,
        },
        "purged_chronological_folds": purged_chronological_folds(records, folds=walk_forward_folds),
        "source_paths": {
            "logs_dir": str(logs_dir),
            "analytics_root": str(analytics_root),
            "policy_root": str(policy_root),
            "dashboard_output": str(_dashboard_output_path() or ""),
        },
    }
    _apply_ai_audit_to_suite(suite, policy_root)

    analytics_root.mkdir(parents=True, exist_ok=True)
    policy_root.mkdir(parents=True, exist_ok=True)
    _write_json(analytics_root / "expectancy_report.json", report)
    (analytics_root / "expectancy_report.md").write_text(_render_markdown(suite), encoding="utf-8")
    _write_json(analytics_root / "subtype_backtest.json", {"rows": subtype_rows})
    _write_json(analytics_root / "target_efficiency.json", {"rows": target_efficiency})
    _write_json(analytics_root / "snapshot_feature_report.json", {"rows": snapshot_feature})
    _write_json(analytics_root / "session_weekday_policy.json", {"rows": session_weekday_policy})
    _write_json(analytics_root / "system_trade_history.json", system_history)
    _write_json(analytics_root / "symbol_expectancy_policy.json", {"rows": suite["symbol_expectancy_policy"]})
    _write_json(analytics_root / "family_expectancy_policy.json", {"rows": suite["family_expectancy_policy"]})
    _write_json(analytics_root / "adaptive_policy_readiness.json", suite["adaptive_policy_readiness"])
    _write_json(analytics_root / "overfitting_diagnostics.json", overfitting)
    _write_json(analytics_root / "input_recommendations.json", input_recommendations)
    _write_json(analytics_root / "decision_maker_edit_scope.json", DECISION_MAKER_EDIT_SCOPE)
    _write_json(analytics_root / "expectancy_ai_audit.json", suite["ai_audit"])
    _write_json(analytics_root / "ledger_integrity_report.json", ledger_report)
    write_calibration_reports(
        calibration_artifact,
        analytics_root / "calibration_report.json",
        analytics_root / "calibration_report.md",
    )
    _write_dashboard_outputs(analytics_root, suite)
    _write_json(analytics_root / "analytics_suite.json", suite)

    snapshot = {
        "policy_id": policy_id,
        "version": next_version,
        "generated_at": suite["generated_at"],
        "active_policy": active_policy,
        "governance": suite["governance"],
        "context_policy": context_policy,
        "subtype_policy": subtype_policy,
        "session_weekday_policy": _snapshot_stub_policy_rows(session_weekday_policy, policy_id),
        "symbol_expectancy_policy": suite["symbol_expectancy_policy"],
        "family_expectancy_policy": suite["family_expectancy_policy"],
        "adaptive_policy_readiness": suite["adaptive_policy_readiness"],
        "data_quality": data_quality,
        "input_recommendations": input_recommendations,
        "decision_maker_edit_scope": DECISION_MAKER_EDIT_SCOPE,
        "decision_thresholds": suite["decision_thresholds"],
        "ai_audit": suite["ai_audit"],
        "context_diagnostics": context_bundle["context_diagnostics"],
        "global_walk_forward": context_bundle["global_walk_forward"],
        "ledger_integrity_report": ledger_report,
        "feature_lineage_version": FEATURE_LINEAGE_VERSION,
        "experiment_registry": suite["experiment_registry"],
        "shadow_calibration_status": calibration_artifact.get("status"),
    }
    snapshot_path = policy_root / "snapshots" / f"{policy_id}.json"
    _write_json(snapshot_path, snapshot)

    activation = None
    if auto_activate and suite["governance"]["activate_ok"]:
        activation = _activate_snapshot(snapshot_path, policy_root)
    suite["snapshot_path"] = str(snapshot_path)
    suite["activation"] = activation
    return suite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir", type=str, default="", help="Path to trade_result_*.json files.")
    parser.add_argument("--common-files-dir", type=str, default="", help="MT5 Common/Files folder.")
    parser.add_argument("--bus-root", type=str, default="PO3_AI_BUS", help="Bus root under Common/Files.")
    parser.add_argument("--output", type=str, default="", help="Optional output path (.json or .md).")
    parser.add_argument("--include-ai-decision-sources", type=str, default="", help="Comma-separated ai_decision_source values to include.")
    parser.add_argument("--exclude-ai-decision-sources", type=str, default="", help="Comma-separated ai_decision_source values to exclude.")
    parser.add_argument("--include-suspicious", action="store_true", help="Include records tagged suspicious or failing ledger-integrity checks.")
    parser.add_argument("--analytics-root", type=str, default="", help="Directory for generated analytics outputs.")
    parser.add_argument("--policy-root", type=str, default="", help="Directory for governed policy snapshots.")
    parser.add_argument("--ea-log", action="append", default=[], help="EA/tester journal log file to parse for setup-funnel counts.")
    parser.add_argument("--ea-logs-dir", action="append", default=[], help="Directory containing EA/tester journal logs.")
    parser.add_argument("--min-policy-bucket-samples", type=int, default=_env_int_any(("EXPECTANCY_MIN_BUCKET_TRADES_BEFORE_ACTION", "POLICY_MIN_BUCKET_TRADES"), 50, minimum=1), help="Minimum samples required per multivariate policy bucket.")
    parser.add_argument("--min-subtype-samples", type=int, default=_env_int_any(("EXPECTANCY_MIN_BUCKET_TRADES_BEFORE_ACTION", "POLICY_MIN_BUCKET_TRADES"), 50, minimum=1), help="Minimum samples required per subtype policy.")
    parser.add_argument("--min-trades-per-bucket", type=int, default=5, help="Minimum trades shown in grouped analytics.")
    parser.add_argument("--walk-forward-folds", type=int, default=3, help="Walk-forward folds for scorecard validation.")
    parser.add_argument("--bootstrap-iterations", type=int, default=200, help="Bootstrap iterations for expectancy confidence intervals.")
    parser.add_argument("--evidence-min-trades", type=int, default=_env_int_any(("EXPECTANCY_MIN_TRADES_BEFORE_ACTION", "POLICY_MIN_TOTAL_CLOSED_TRADES"), 200, minimum=1), help="Minimum total trades before governed activation is allowed.")
    parser.add_argument("--policy-min-bucket-trades", type=int, default=_env_int_any(("EXPECTANCY_MIN_BUCKET_TRADES_BEFORE_ACTION", "POLICY_MIN_BUCKET_TRADES"), 50, minimum=1), help="Minimum bucket size before governed activation is allowed.")
    parser.add_argument("--policy-min-profit-factor-after-costs", type=float, default=_env_float_any(("POLICY_MIN_PROFIT_FACTOR_AFTER_COSTS",), 1.15, minimum=0.01), help="Minimum profit factor after costs for governed activation.")
    parser.add_argument("--policy-max-train-test-gap-r", type=float, default=_env_float_any(("POLICY_MAX_TRAIN_TEST_GAP_R",), 0.25, minimum=0.0), help="Maximum allowed train/test expectancy gap in R.")
    parser.add_argument("--policy-min-positive-fold-rate", type=float, default=_env_float_any(("POLICY_MIN_POSITIVE_FOLD_RATE",), 0.60, minimum=0.0), help="Minimum positive walk-forward fold rate for governed activation.")
    parser.add_argument("--policy-max-drawdown-r", type=float, default=_env_float_any(("POLICY_MAX_DRAWDOWN_R",), 12.0, minimum=0.0), help="Maximum allowed drawdown in R for governed activation.")
    parser.add_argument("--auto-activate", action="store_true", help="Activate a newly generated policy snapshot when governance passes.")
    parser.add_argument("--experiment-id", type=str, default="", help="Registered immutable experiment id required for optimization or activation.")
    parser.add_argument("--experiment-hypothesis", type=str, default="", help="Exact pre-registered hypothesis required for optimization or activation.")
    parser.add_argument("--experiment-registry", type=str, default="", help="Append-only experiment registry JSONL path.")
    shadow_group = parser.add_mutually_exclusive_group()
    shadow_group.add_argument("--policy-shadow-mode", dest="policy_shadow_mode", action="store_true", default=None, help="Write policy recommendations without live activation.")
    shadow_group.add_argument("--policy-live-mode", dest="policy_shadow_mode", action="store_false", help="Permit activation when governance and --auto-activate pass.")
    parser.add_argument("--rollback-policy-id", type=str, default="", help="Rollback to a specific snapshot id and exit.")
    parser.add_argument("--rollback-steps", type=int, default=0, help="Rollback N snapshots from the latest and exit.")
    args = parser.parse_args()

    common_root = Path(args.common_files_dir) if args.common_files_dir else _default_common_files()
    bus_root = common_root / args.bus_root
    logs_dir = Path(args.logs_dir) if args.logs_dir else bus_root / "logs" / "trade_results"
    analytics_root = Path(args.analytics_root) if args.analytics_root else bus_root / "logs" / "analytics"
    policy_root = Path(args.policy_root) if args.policy_root else bus_root / "logs" / "policies"
    ea_log_paths = _collect_ea_log_paths(args.ea_log, args.ea_logs_dir)
    funnel = _setup_funnel_report(ea_log_paths)

    if args.rollback_policy_id or args.rollback_steps > 0:
        result = rollback_policy(policy_root, policy_id=args.rollback_policy_id, steps=max(1, args.rollback_steps or 1))
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
        else:
            print(text)
        return

    suite = run_analytics_suite(
        logs_dir=logs_dir,
        analytics_root=analytics_root,
        policy_root=policy_root,
        include_sources=_csv_set(args.include_ai_decision_sources),
        exclude_sources=_csv_set(args.exclude_ai_decision_sources),
        auto_activate=args.auto_activate,
        min_bucket_samples=max(4, args.min_policy_bucket_samples),
        min_subtype_samples=max(4, args.min_subtype_samples),
        min_group_count=max(1, args.min_trades_per_bucket),
        walk_forward_folds=max(2, args.walk_forward_folds),
        bootstrap_iterations=max(0, args.bootstrap_iterations),
        evidence_min_trades=max(1, args.evidence_min_trades),
        policy_shadow_mode=args.policy_shadow_mode,
        policy_min_bucket_trades=max(1, args.policy_min_bucket_trades),
        policy_min_profit_factor_after_costs=args.policy_min_profit_factor_after_costs,
        policy_max_train_test_gap_r=args.policy_max_train_test_gap_r,
        policy_min_positive_fold_rate=args.policy_min_positive_fold_rate,
        policy_max_drawdown_r=args.policy_max_drawdown_r,
        include_suspicious=args.include_suspicious,
        experiment_id=args.experiment_id.strip(),
        experiment_hypothesis=args.experiment_hypothesis.strip(),
        experiment_registry_path=Path(args.experiment_registry) if args.experiment_registry else None,
    )
    suite["setup_funnel"] = funnel

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".md":
            out_path.write_text(_render_markdown(suite), encoding="utf-8")
        else:
            out_path.write_text(json.dumps(suite, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(_render_markdown(suite))


if __name__ == "__main__":
    main()
