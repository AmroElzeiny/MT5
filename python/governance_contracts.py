"""Fail-closed governance contracts for the PO3 decision and trade ledger.

This module deliberately contains no MT5 transport or OpenAI code.  It is the
single Python authority for account-mode governance, setup taxonomy, broker
deal accounting, ledger integrity, time-aware uncertainty, and policy
eligibility.  Trading code may consume its results; it may not reinterpret a
failed result as usable evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


LEDGER_SCHEMA_VERSION = "20260718_trade_ledger_execution_path_v7"
SETUP_TAXONOMY_VERSION = "20260716_setup_taxonomy_v1"
FEATURE_LINEAGE_VERSION = "20260718_tick_path_evidence_v3"
CALIBRATION_CONTRACT_VERSION = "20260716_oos_calibration_v1"


class AccountPositionMode(str, Enum):
    HEDGING_EXACT_POSITION_ID = "HEDGING_EXACT_POSITION_ID"
    NETTING_VIRTUAL_SUBPOSITION_LEDGER = "NETTING_VIRTUAL_SUBPOSITION_LEDGER"
    NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK = "NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK"
    UNSUPPORTED_ACCOUNT_MODE = "UNSUPPORTED_ACCOUNT_MODE"


class NettingPolicy(str, Enum):
    REJECT_STARTUP = "REJECT_STARTUP"
    FORCE_ONE_MANAGED_POSITION_PER_SYMBOL = "FORCE_ONE_MANAGED_POSITION_PER_SYMBOL"


class LedgerIntegrityStatus(str, Enum):
    CLEAN = "CLEAN"
    SUSPICIOUS = "SUSPICIOUS"
    QUARANTINED = "QUARANTINED"
    UNATTRIBUTED = "UNATTRIBUTED"


class SetupTaxonomy(str, Enum):
    MICRO_FVG_MID_REVERSAL = "MICRO_FVG_MID_REVERSAL"
    MICRO_FVG_EDGE_REVERSAL = "MICRO_FVG_EDGE_REVERSAL"
    MICRO_BREAKER_RETEST = "MICRO_BREAKER_RETEST"
    MICRO_OTE_REVERSAL = "MICRO_OTE_REVERSAL"
    MICRO_CONTINUATION_FVG = "MICRO_CONTINUATION_FVG"
    MICRO_NESTED_CONTINUATION = "MICRO_NESTED_CONTINUATION"
    MICRO_RANGE_REENTRY = "MICRO_RANGE_REENTRY"
    MICRO_SESSION_REENTRY = "MICRO_SESSION_REENTRY"
    FAILED_BREAKOUT_RECLAIM = "FAILED_BREAKOUT_RECLAIM"
    FULL_PO3_REVERSAL = "FULL_PO3_REVERSAL"
    FULL_PO3_CONTINUATION = "FULL_PO3_CONTINUATION"
    UNKNOWN_UNCLASSIFIED = "UNKNOWN_UNCLASSIFIED"


TRADING_DECISION_QUALITY_TIERS = {"FULL_STRUCTURED", "CACHE_OF_FULL_STRUCTURED"}


@dataclass(frozen=True)
class AccountModeResolution:
    broker_margin_mode: str
    internal_mode: AccountPositionMode
    netting_policy: NettingPolicy
    virtual_subposition_ledger_available: bool
    startup_allowed: bool
    reason: str


@dataclass(frozen=True)
class TaxonomyResolution:
    taxonomy: SetupTaxonomy
    mapping_source: str
    failure_reason: str = ""


@dataclass(frozen=True)
class LedgerTolerances:
    r_reconciliation: float = 0.02
    money_reconciliation: float = 0.02
    volume_reconciliation: float = 1e-8
    max_mfe_r: float = 50.0
    max_abs_mae_r: float = 50.0
    timestamp_tolerance_sec: int = 10
    price_tick_tolerance: float = 2.0


EMPIRICAL_PRE_ENTRY_FEATURES: tuple[dict[str, str], ...] = (
    {"name": "effective_rr2", "direction": "positive"},
    {"name": "liquidity_rr", "direction": "positive"},
    {"name": "sequence_quality", "direction": "positive"},
    {"name": "htf_alignment_score", "direction": "positive"},
    {"name": "stop_quality_score", "direction": "positive"},
    {"name": "trend_strength", "direction": "positive"},
    {"name": "session_vol_ratio", "direction": "positive"},
    {"name": "fvg_score", "direction": "positive"},
    {"name": "adverse_context_score", "direction": "negative"},
    {"name": "execution_cost_r", "direction": "negative"},
    {"name": "slippage_r", "direction": "negative"},
    {"name": "commission_r", "direction": "negative"},
    {"name": "vwap_dist_atr", "direction": "negative"},
    {"name": "news_risk", "direction": "negative"},
)


FEATURE_LINEAGE: tuple[dict[str, Any], ...] = tuple(
    {
        "feature_name": spec["name"],
        "raw_source": "immutable_pre_entry_trade_plan",
        "calculation_stage": "before_ai_request",
        "used_by_heuristic": False,
        "sent_to_llm": True,
        "used_by_statistical_model": True,
        "decision_authoritative": False,
        "leakage_risk": "low_if_timestamp_and_identity_verified",
    }
    for spec in EMPIRICAL_PRE_ENTRY_FEATURES
) + (
    {
        "feature_name": "diagnostic_legacy_setup_score",
        "raw_source": "mql_deterministic_setup_score",
        "calculation_stage": "pre_ai_diagnostic",
        "used_by_heuristic": True,
        "sent_to_llm": False,
        "used_by_statistical_model": False,
        "decision_authoritative": False,
        "leakage_risk": "double_counting_if_reintroduced",
    },
    {
        "feature_name": "heuristic_quality_estimate",
        "raw_source": "legacy_hand_built_quality_formula",
        "calculation_stage": "pre_ai_diagnostic",
        "used_by_heuristic": True,
        "sent_to_llm": False,
        "used_by_statistical_model": False,
        "decision_authoritative": False,
        "leakage_risk": "not_empirical_probability",
    },
)


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def _finite(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except Exception:
        return False


def _number(value: Any, default: float = 0.0) -> float:
    return float(value) if _finite(value) else default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _direction(value: float, tolerance: float = 1e-12) -> str:
    if value > tolerance:
        return "positive"
    if value < -tolerance:
        return "negative"
    return "flat"


def resolve_account_position_mode(
    broker_margin_mode: Any,
    netting_policy: NettingPolicy | str = NettingPolicy.REJECT_STARTUP,
    *,
    virtual_subposition_ledger_available: bool = False,
) -> AccountModeResolution:
    try:
        policy = netting_policy if isinstance(netting_policy, NettingPolicy) else NettingPolicy(str(netting_policy))
    except ValueError:
        policy = NettingPolicy.REJECT_STARTUP
    raw = str(broker_margin_mode).strip()
    normalized = raw.upper()
    hedging = normalized in {"2", "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", "RETAIL_HEDGING", "HEDGING"}
    netting = normalized in {
        "0",
        "1",
        "ACCOUNT_MARGIN_MODE_RETAIL_NETTING",
        "ACCOUNT_MARGIN_MODE_EXCHANGE",
        "RETAIL_NETTING",
        "EXCHANGE",
        "NETTING",
    }
    if hedging:
        return AccountModeResolution(raw, AccountPositionMode.HEDGING_EXACT_POSITION_ID, policy, False, True, "hedging_exact_position_id")
    if netting and virtual_subposition_ledger_available:
        return AccountModeResolution(raw, AccountPositionMode.NETTING_VIRTUAL_SUBPOSITION_LEDGER, policy, True, True, "netting_virtual_ledger_available")
    if netting and policy == NettingPolicy.FORCE_ONE_MANAGED_POSITION_PER_SYMBOL:
        return AccountModeResolution(raw, AccountPositionMode.NETTING_ONE_POSITION_PER_SYMBOL_FALLBACK, policy, False, True, "explicit_one_position_per_symbol_fallback")
    reason = "netting_virtual_subposition_ledger_unavailable" if netting else "unsupported_account_margin_mode"
    return AccountModeResolution(raw, AccountPositionMode.UNSUPPORTED_ACCOUNT_MODE, policy, False, False, reason)


def classify_setup_taxonomy(fields: Mapping[str, Any]) -> TaxonomyResolution:
    """Map exact internal branch/state values; never infer from substrings/comments."""

    supplied = str(fields.get("setup_taxonomy_enum") or "").strip().upper()
    if supplied:
        try:
            taxonomy = SetupTaxonomy(supplied)
            if taxonomy != SetupTaxonomy.UNKNOWN_UNCLASSIFIED:
                return TaxonomyResolution(taxonomy, "explicit_internal_taxonomy_enum")
        except ValueError:
            pass

    branch = _token(fields.get("entry_branch") or fields.get("entry_model"))
    family = _token(fields.get("setup_family"))
    setup_class = _token(fields.get("setup_class"))
    scope = _token(fields.get("po3_scope"))
    structure = _token(fields.get("structure_state") or fields.get("structure_type"))
    fvg_state = _token(fields.get("fvg_state") or fields.get("fvg_execution_class"))

    known_full_families = {"full_po3", "full_po3_reversal", "full_po3_continuation"}
    known_full_classes = {"full_po3", "full_po3_reversal", "full_po3_continuation"}
    full_scope = family in known_full_families or setup_class in known_full_classes or scope == "institutional_po3"
    continuation = (
        branch == "continuation_reentry"
        or structure in {"continuation_bos", "continuation", "micro_continuation"}
        or fvg_state == "continuation_reentry"
        or family in {"micro_continuation_fvg", "full_po3_continuation"}
        or bool(fields.get("fvg_continuation", False))
    )
    failed_breakout = (
        structure == "micro_failed_breakout_reclaim"
        or family == "micro_failed_breakout_reclaim"
        or setup_class == "failed_breakout_reclaim"
    )

    if failed_breakout and branch in {"fvg_mid", "fvg_edge", "range_reentry"}:
        return TaxonomyResolution(SetupTaxonomy.FAILED_BREAKOUT_RECLAIM, "exact_family_branch_state")
    if full_scope:
        if continuation:
            return TaxonomyResolution(SetupTaxonomy.FULL_PO3_CONTINUATION, "exact_scope_branch_state")
        if branch in {"fvg_mid", "fvg_edge", "breaker_retest", "ote_inside_fvg", "nested_htf_ltf_fvg", "nested_fvg_edge"}:
            return TaxonomyResolution(SetupTaxonomy.FULL_PO3_REVERSAL, "exact_scope_branch_state")
    if branch == "session_reentry":
        return TaxonomyResolution(SetupTaxonomy.MICRO_SESSION_REENTRY, "exact_branch_state")
    if branch == "range_reentry":
        return TaxonomyResolution(SetupTaxonomy.MICRO_RANGE_REENTRY, "exact_branch_state")
    if branch in {"nested_htf_ltf_fvg", "nested_fvg_edge"}:
        return TaxonomyResolution(SetupTaxonomy.MICRO_NESTED_CONTINUATION, "exact_branch_state")
    if continuation:
        return TaxonomyResolution(SetupTaxonomy.MICRO_CONTINUATION_FVG, "exact_branch_state")
    if branch == "breaker_retest":
        return TaxonomyResolution(SetupTaxonomy.MICRO_BREAKER_RETEST, "exact_branch_state")
    if branch == "ote_inside_fvg":
        return TaxonomyResolution(SetupTaxonomy.MICRO_OTE_REVERSAL, "exact_branch_state")
    if branch == "fvg_edge":
        return TaxonomyResolution(SetupTaxonomy.MICRO_FVG_EDGE_REVERSAL, "exact_branch_state")
    if branch == "fvg_mid":
        return TaxonomyResolution(SetupTaxonomy.MICRO_FVG_MID_REVERSAL, "exact_branch_state")
    raw = "|".join((branch or "missing_branch", family or "missing_family", setup_class or "missing_class", scope or "missing_scope", structure or "missing_structure", fvg_state or "missing_fvg_state"))
    return TaxonomyResolution(SetupTaxonomy.UNKNOWN_UNCLASSIFIED, "strict_mapping_failed", raw)


def decision_quality_can_trade(tier: Any) -> bool:
    return str(tier or "").strip().upper() in TRADING_DECISION_QUALITY_TIERS


def calculate_deal_accounting(
    position_id: Any,
    deals: Sequence[Mapping[str, Any]],
    *,
    expected_symbol: str = "",
) -> dict[str, Any]:
    pid = str(position_id or "").strip()
    gross = commission = swap = fees = 0.0
    opened_volume = closed_volume = 0.0
    tickets: list[str] = []
    errors: list[str] = []
    entry_directions: set[str] = set()
    exit_directions: set[str] = set()
    for deal in deals:
        deal_pid = str(deal.get("position_id") or deal.get("deal_position_id") or "").strip()
        if not pid or deal_pid != pid:
            errors.append("deal_position_id_mismatch")
            continue
        symbol = str(deal.get("symbol") or "").strip().upper()
        if expected_symbol and symbol != expected_symbol.strip().upper():
            errors.append("deal_symbol_mismatch")
        entry_type = _token(deal.get("entry") or deal.get("deal_entry"))
        direction = _token(deal.get("direction") or deal.get("deal_type"))
        volume = _number(deal.get("volume"))
        profit = _number(deal.get("profit") if "profit" in deal else deal.get("price_pnl"))
        deal_commission = _number(deal.get("commission"))
        deal_swap = _number(deal.get("swap"))
        deal_fee = _number(deal.get("fee") if "fee" in deal else deal.get("fees"))
        if entry_type in {"in", "deal_entry_in"}:
            opened_volume += volume
            entry_directions.add(direction)
        elif entry_type in {"out", "out_by", "deal_entry_out", "deal_entry_out_by"}:
            closed_volume += volume
            gross += profit
            exit_directions.add(direction)
        elif entry_type in {"inout", "deal_entry_inout"}:
            opened_volume += volume
            closed_volume += volume
            gross += profit
            entry_directions.add(direction)
            exit_directions.add(direction)
        else:
            errors.append("unknown_deal_entry_type")
        commission += deal_commission
        swap += deal_swap
        fees += deal_fee
        tickets.append(str(deal.get("ticket") or deal.get("deal_ticket") or ""))
    internal = gross + commission + swap + fees
    return {
        "position_id": pid,
        "gross_price_pnl": gross,
        "total_commission": commission,
        "total_swap": swap,
        "total_fees": fees,
        "internal_net_pnl": internal,
        "opened_volume": opened_volume,
        "closed_volume": closed_volume,
        "deal_tickets": tickets,
        "entry_directions": sorted(entry_directions),
        "exit_directions": sorted(exit_directions),
        "errors": sorted(set(errors)),
    }


def calculate_outcome_metrics(
    net_pnl: float,
    *,
    fixed_initial_balance: float,
    equity_at_entry: float,
    initial_risk_money: float,
) -> dict[str, Any]:
    values = (net_pnl, fixed_initial_balance, equity_at_entry, initial_risk_money)
    if not all(_finite(value) for value in values):
        raise ValueError("outcome_metric_non_finite")
    if fixed_initial_balance <= 0 or equity_at_entry <= 0 or initial_risk_money <= 0:
        raise ValueError("outcome_metric_invalid_denominator")
    result_r = net_pnl / initial_risk_money
    equity_pct = net_pnl / equity_at_entry * 100.0
    fixed_pct = net_pnl / fixed_initial_balance * 100.0
    broker_direction = _direction(net_pnl)
    r_direction = _direction(result_r)
    equity_direction = _direction(equity_pct)
    match = len({broker_direction, r_direction, equity_direction}) == 1
    return {
        "result_pct_fixed_initial_balance": fixed_pct,
        "result_pct_equity_at_entry": equity_pct,
        "result_r_initial_risk": result_r,
        "outcome_direction_broker": broker_direction,
        "outcome_direction_r": r_direction,
        "outcome_direction_equity_pct": equity_direction,
        "outcome_direction_match": match,
        "outcome_reconciliation_status": "clean" if match else "quarantined",
    }


def _tick_aligned(price: float, tick_size: float, tolerance_ticks: float) -> bool:
    if price <= 0 or tick_size <= 0:
        return False
    nearest = round(price / tick_size) * tick_size
    return abs(price - nearest) <= tick_size * max(tolerance_ticks, 1e-9)


def _audit_single_record(record: Mapping[str, Any], tolerances: LedgerTolerances) -> tuple[dict[str, Any], list[str], LedgerIntegrityStatus]:
    row = dict(record)
    reasons: list[str] = []
    pid = str(row.get("position_id") or row.get("broker_position_identifier") or "").strip()
    attribution = _token(row.get("attribution_status"))
    if not pid or pid in {"0", "none", "null"}:
        reasons.append("missing_exact_position_id")
    if attribution not in {"attributed", "verified"}:
        reasons.append("attribution_not_verified")
    if row.get("execution_identity_verified") is False or bool(row.get("execution_identity_quarantined")):
        reasons.append("execution_identity_not_verified")
    tier = str(row.get("decision_quality_tier") or "").upper()
    if not decision_quality_can_trade(tier):
        reasons.append("non_trading_decision_quality_tier")
    taxonomy = str(row.get("setup_taxonomy_enum") or "").upper()
    if taxonomy not in {item.value for item in SetupTaxonomy if item != SetupTaxonomy.UNKNOWN_UNCLASSIFIED}:
        reasons.append("unknown_setup_taxonomy")
    if not str(row.get("trade_key") or "").strip():
        reasons.append("missing_trade_key")
    if not str(row.get("candidate_id") or "").strip() or not str(row.get("candidate_hash") or "").strip():
        reasons.append("missing_candidate_identity")
    if not str(row.get("assessed_execution_fingerprint") or "").strip() or not str(row.get("final_execution_fingerprint") or "").strip():
        reasons.append("missing_execution_fingerprint")
    if row.get("candidate_hash_match") is not True:
        reasons.append("candidate_hash_mismatch")
    if row.get("execution_fingerprint_match") is not True:
        reasons.append("execution_fingerprint_mismatch")
    if _integer(row.get("magic_number") or row.get("magic")) <= 0:
        reasons.append("missing_magic_number")
    account_mode = str(row.get("account_position_mode") or "").upper()
    if account_mode not in {item.value for item in AccountPositionMode if item != AccountPositionMode.UNSUPPORTED_ACCOUNT_MODE}:
        reasons.append("unsupported_or_missing_account_position_mode")

    for field in ("broker_net_pnl", "internal_net_pnl", "initial_risk_money", "result_r_initial_risk", "mfe_r", "mae_r"):
        if not _finite(row.get(field)):
            reasons.append(f"non_finite_{field}")
    mfe = _number(row.get("mfe_r"), math.nan)
    mae = _number(row.get("mae_r"), math.nan)
    if math.isfinite(mfe) and (mfe < 0 or mfe > tolerances.max_mfe_r):
        reasons.append("mfe_out_of_bounds")
    if math.isfinite(mae) and (mae < 0 or mae > tolerances.max_abs_mae_r):
        reasons.append("mae_out_of_bounds")

    initial_risk = _number(row.get("initial_risk_money"))
    broker_net = _number(row.get("broker_net_pnl"), _number(row.get("realized_pnl")))
    internal_net = _number(row.get("internal_net_pnl"), math.nan)
    recorded_r = _number(row.get("result_r_initial_risk"), _number(row.get("realized_r"), math.nan))
    if initial_risk <= 0:
        reasons.append("initial_risk_money_not_positive")
    elif math.isfinite(recorded_r) and abs(recorded_r - broker_net / initial_risk) > tolerances.r_reconciliation:
        reasons.append("realized_r_reconciliation_mismatch")
    if math.isfinite(internal_net) and abs(broker_net - internal_net) > tolerances.money_reconciliation:
        reasons.append("broker_internal_pnl_mismatch")
    if abs(broker_net) <= tolerances.money_reconciliation:
        reasons.append("zero_or_unresolved_broker_net_pnl")

    deals = row.get("deals") if isinstance(row.get("deals"), list) else []
    if deals:
        accounting = calculate_deal_accounting(pid, deals, expected_symbol=str(row.get("symbol") or ""))
        row.update(accounting)
        reasons.extend(accounting["errors"])
        if abs(accounting["internal_net_pnl"] - broker_net) > tolerances.money_reconciliation:
            reasons.append("broker_deal_sum_mismatch")
        if abs(accounting["opened_volume"] - accounting["closed_volume"]) > tolerances.volume_reconciliation:
            reasons.append("partial_volume_reconciliation_mismatch")
    else:
        reasons.append("missing_exact_position_deals")

    symbol = str(row.get("symbol") or "").upper()
    tick = _number(row.get("symbol_tick_size"))
    digits = _integer(row.get("symbol_digits"), -1)
    if not symbol or tick <= 0 or digits < 0:
        reasons.append("missing_symbol_price_contract")
    for field in ("planned_entry", "planned_sl", "planned_tp1", "planned_tp2", "filled_entry"):
        value = _number(row.get(field))
        if value <= 0 or (tick > 0 and not _tick_aligned(value, tick, tolerances.price_tick_tolerance)):
            reasons.append(f"invalid_or_misaligned_{field}")
    entry = _number(row.get("filled_entry"), _number(row.get("planned_entry")))
    stop = _number(row.get("planned_sl"))
    is_buy = bool(row.get("is_buy")) or _token(row.get("direction")) == "buy"
    if entry > 0 and stop > 0 and ((is_buy and stop >= entry) or (not is_buy and stop <= entry)):
        reasons.append("entry_stop_direction_mismatch")

    exit_events: list[Mapping[str, Any]] = []
    for event_field in ("partials", "partial_closes", "exit_deals", "fills"):
        value = row.get(event_field)
        if isinstance(value, list):
            exit_events.extend(item for item in value if isinstance(item, Mapping))
    matched_exit_volume = 0.0
    for event in exit_events:
        event_symbol = str(event.get("symbol") or event.get("deal_symbol") or "").upper()
        if event_symbol and symbol and event_symbol != symbol:
            reasons.append("symbol_mismatch")
        price = _number(event.get("price"), _number(event.get("exit_price"), _number(event.get("close_price"))))
        if entry > 0 and price > 0 and (price < entry * 0.25 or price > entry * 4.0):
            reasons.append("price_scale_mismatch")
        matched_exit_volume += _number(event.get("volume"), _number(event.get("lots")))
    initial_volume = _number(row.get("initial_volume"), _number(row.get("volume")))
    if initial_volume > 0 and matched_exit_volume > initial_volume + tolerances.volume_reconciliation:
        reasons.append("partial_volume_reconciliation_mismatch")

    opened = _integer(row.get("opened_at") or row.get("filled_at"))
    closed = _integer(row.get("closed_at"))
    if opened <= 0 or closed <= opened:
        reasons.append("invalid_trade_timestamps")

    fixed_balance = _number(row.get("configured_fixed_initial_balance"), _number(row.get("virtual_balance_base")))
    equity_entry = _number(row.get("account_equity_at_entry"))
    if fixed_balance <= 0 or equity_entry <= 0 or initial_risk <= 0:
        reasons.append("missing_outcome_metric_denominator")
    else:
        metrics = calculate_outcome_metrics(
            broker_net,
            fixed_initial_balance=fixed_balance,
            equity_at_entry=equity_entry,
            initial_risk_money=initial_risk,
        )
        row.update(metrics)
        if not metrics["outcome_direction_match"]:
            reasons.append("outcome_direction_mismatch")

    versions = {
        str(row.get("engine_version") or ""),
        str(row.get("runtime_input_hash") or ""),
        str(row.get("prompt_contract_version") or ""),
        str(row.get("decision_schema_version") or ""),
    }
    if "" in versions:
        reasons.append("missing_engine_config_prompt_identity")
    for deal in deals:
        record_magic = _integer(row.get("magic_number") or row.get("magic"))
        if "magic" in deal or "magic_number" in deal:
            deal_magic = _integer(deal.get("magic_number") or deal.get("magic"))
            if record_magic > 0 and deal_magic != record_magic:
                reasons.append("deal_magic_mismatch")
        for key in ("engine_version", "runtime_input_hash", "prompt_contract_version", "decision_schema_version"):
            if key in deal and str(deal.get(key) or "") != str(row.get(key) or ""):
                reasons.append("mixed_engine_config_prompt_identity")

    reasons = sorted(set(reasons))
    if "missing_exact_position_id" in reasons or "attribution_not_verified" in reasons:
        status = LedgerIntegrityStatus.UNATTRIBUTED
    elif any(reason in reasons for reason in ("execution_identity_not_verified", "broker_internal_pnl_mismatch", "broker_deal_sum_mismatch", "mixed_engine_config_prompt_identity")):
        status = LedgerIntegrityStatus.QUARANTINED
    elif reasons:
        status = LedgerIntegrityStatus.SUSPICIOUS
    else:
        status = LedgerIntegrityStatus.CLEAN
    eligible = status == LedgerIntegrityStatus.CLEAN
    row.update(
        ledger_schema_version=LEDGER_SCHEMA_VERSION,
        ledger_integrity_status=status.value,
        ledger_integrity_reasons=reasons,
        attribution_status="attributed" if eligible else ("unattributed" if status == LedgerIntegrityStatus.UNATTRIBUTED else "quarantined"),
        learning_eligible=eligible,
        optimization_eligible=eligible,
        suppression_eligible=eligible,
    )
    return row, reasons, status


def audit_trade_records(
    records: Sequence[Mapping[str, Any]],
    tolerances: LedgerTolerances | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tol = tolerances or LedgerTolerances()
    audited: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    position_counts: Counter[str] = Counter()
    unknown_taxonomy_counts: Counter[tuple[str, ...]] = Counter()
    for record in records:
        row, reasons, status = _audit_single_record(record, tol)
        audited.append(row)
        status_counts[status.value] += 1
        reason_counts.update(reasons)
        pid = str(row.get("position_id") or row.get("broker_position_identifier") or "").strip()
        if pid:
            position_counts[pid] += 1
        if "unknown_setup_taxonomy" in reasons:
            unknown_taxonomy_counts[
                (
                    _token(row.get("entry_branch") or row.get("entry_model")),
                    _token(row.get("setup_family")),
                    _token(row.get("setup_class")),
                    _token(row.get("po3_scope")),
                    _token(row.get("structure_state") or row.get("structure_type")),
                    _token(row.get("fvg_state") or row.get("fvg_execution_class")),
                    _token(row.get("setup_taxonomy_enum")),
                )
            ] += 1
    duplicates = sorted(pid for pid, count in position_counts.items() if count > 1)
    if duplicates:
        for row in audited:
            pid = str(row.get("position_id") or row.get("broker_position_identifier") or "").strip()
            if pid in duplicates:
                reasons = sorted(set(list(row.get("ledger_integrity_reasons") or []) + ["duplicate_final_position_record"]))
                row.update(
                    ledger_integrity_status=LedgerIntegrityStatus.QUARANTINED.value,
                    ledger_integrity_reasons=reasons,
                    learning_eligible=False,
                    optimization_eligible=False,
                    suppression_eligible=False,
                )
        reason_counts["duplicate_final_position_record"] += len(duplicates)
    clean = [row for row in audited if row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value]
    if audited and len(clean) == len(audited):
        global_status = LedgerIntegrityStatus.CLEAN
    elif any(
        row.get("ledger_integrity_status")
        in {LedgerIntegrityStatus.QUARANTINED.value, LedgerIntegrityStatus.UNATTRIBUTED.value}
        for row in audited
    ):
        global_status = LedgerIntegrityStatus.QUARANTINED
    else:
        global_status = LedgerIntegrityStatus.SUSPICIOUS if audited else LedgerIntegrityStatus.QUARANTINED
    equal_expectancy = statistics.fmean(_number(row.get("result_r_initial_risk")) for row in clean) if clean else 0.0
    total_risk = sum(_number(row.get("initial_risk_money")) for row in clean)
    risk_weighted = (
        sum(_number(row.get("result_r_initial_risk")) * _number(row.get("initial_risk_money")) for row in clean) / total_risk
        if total_risk > 0 else 0.0
    )
    report = {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "global_status": global_status.value,
        "total_records": len(audited),
        "clean": len(clean),
        "suspicious": sum(1 for row in audited if row.get("ledger_integrity_status") == LedgerIntegrityStatus.SUSPICIOUS.value),
        "quarantined": sum(1 for row in audited if row.get("ledger_integrity_status") == LedgerIntegrityStatus.QUARANTINED.value),
        "unattributed": sum(1 for row in audited if row.get("ledger_integrity_status") == LedgerIntegrityStatus.UNATTRIBUTED.value),
        "duplicate_position_ids": duplicates,
        "broken_volume_reconciliation": reason_counts.get("partial_volume_reconciliation_mismatch", 0),
        "broker_pnl_mismatches": reason_counts.get("broker_internal_pnl_mismatch", 0) + reason_counts.get("broker_deal_sum_mismatch", 0),
        "r_mismatches": reason_counts.get("realized_r_reconciliation_mismatch", 0),
        "symbol_scale_failures": sum(value for key, value in reason_counts.items() if "misaligned_" in key or "symbol_price_contract" in key),
        "mfe_mae_failures": reason_counts.get("mfe_out_of_bounds", 0) + reason_counts.get("mae_out_of_bounds", 0),
        "learning_eligible_count": sum(bool(row.get("learning_eligible")) for row in audited),
        "suppression_eligible_count": sum(bool(row.get("suppression_eligible")) for row in audited),
        "optimization_eligible_count": sum(bool(row.get("optimization_eligible")) for row in audited),
        "equal_trade_weighted_expectancy_r": equal_expectancy,
        "risk_weighted_expectancy_r": risk_weighted,
        "direction_mismatch_count": reason_counts.get("outcome_direction_mismatch", 0),
        "unknown_taxonomy_combinations": [
            {
                "entry_branch": values[0],
                "setup_family": values[1],
                "setup_class": values[2],
                "po3_scope": values[3],
                "structure_state": values[4],
                "fvg_state": values[5],
                "setup_taxonomy_enum": values[6],
                "count": count,
            }
            for values, count in sorted(unknown_taxonomy_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "reason_counts": dict(sorted(reason_counts.items())),
        "tolerances": asdict(tol),
    }
    return audited, report


def _record_block_key(record: Mapping[str, Any], block_type: str) -> str:
    timestamp = _integer(record.get("opened_at") or record.get("filled_at") or record.get("planned_at") or record.get("closed_at"))
    day = str(record.get("trade_date") or (timestamp // 86400 if timestamp > 0 else "unknown"))
    if block_type == "session":
        return f"{day}|{str(record.get('session') or record.get('session_name') or 'unknown')}"
    return day


def _profit_factor(records: Sequence[Mapping[str, Any]]) -> float:
    values = [_number(row.get("result_r_initial_risk"), _number(row.get("realized_r"))) for row in records]
    wins = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    return wins / losses if losses > 0 else (math.inf if wins > 0 else 0.0)


def block_bootstrap_uncertainty(
    records: Sequence[Mapping[str, Any]],
    *,
    iterations: int = 1000,
    block_type: str = "day",
    seed: int = 1729,
    min_blocks: int = 5,
) -> dict[str, Any]:
    if block_type not in {"day", "session"}:
        raise ValueError("bootstrap_block_type_invalid")
    blocks: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        blocks[_record_block_key(record, block_type)].append(record)
    keys = sorted(blocks)
    if len(keys) < min_blocks or iterations <= 0:
        return {
            "available": False,
            "reason": "insufficient_effective_blocks",
            "block_type": block_type,
            "effective_block_count": len(keys),
            "sample_count": len(records),
            "iterations": iterations,
            "seed": seed,
        }
    rng = random.Random(seed)
    mean_draws: list[float] = []
    pf_draws: list[float] = []
    for _ in range(iterations):
        sample: list[Mapping[str, Any]] = []
        for _ in keys:
            sample.extend(blocks[keys[rng.randrange(len(keys))]])
        values = [_number(row.get("result_r_initial_risk"), _number(row.get("realized_r"))) for row in sample]
        mean_draws.append(statistics.fmean(values) if values else 0.0)
        pf_draws.append(_profit_factor(sample))
    mean_draws.sort()
    finite_pf = sorted(value for value in pf_draws if math.isfinite(value))
    lo = max(0, int((iterations - 1) * 0.05))
    hi = min(iterations - 1, int((iterations - 1) * 0.95))
    return {
        "available": True,
        "block_type": block_type,
        "effective_block_count": len(keys),
        "sample_count": len(records),
        "iterations": iterations,
        "seed": seed,
        "mean_r": statistics.fmean(mean_draws),
        "mean_r_ci_low": mean_draws[lo],
        "mean_r_ci_high": mean_draws[hi],
        "profit_factor": _profit_factor(records),
        "profit_factor_ci_low": finite_pf[min(lo, len(finite_pf) - 1)] if finite_pf else None,
        "profit_factor_ci_high": finite_pf[min(hi, len(finite_pf) - 1)] if finite_pf else None,
        "positive_draw_rate": sum(value > 0 for value in mean_draws) / iterations,
    }


def purged_chronological_folds(
    records: Sequence[Mapping[str, Any]],
    *,
    folds: int = 3,
    embargo_seconds: int = 6 * 3600,
) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda row: _integer(row.get("opened_at") or row.get("filled_at") or row.get("closed_at")))
    if folds < 2 or len(ordered) < folds * 10:
        return []
    segment_size = max(1, len(ordered) // (folds + 1))
    segments = [ordered[index : index + segment_size] for index in range(0, len(ordered), segment_size)]
    if len(segments) > folds + 1:
        segments[folds].extend(row for segment in segments[folds + 1 :] for row in segment)
        segments = segments[: folds + 1]
    output: list[dict[str, Any]] = []
    for index in range(1, len(segments)):
        test = segments[index]
        test_times = [_integer(row.get("opened_at") or row.get("filled_at") or row.get("closed_at")) for row in test]
        test_start = min(test_times)
        train = [
            row
            for segment in segments[:index]
            for row in segment
            if _integer(row.get("closed_at")) < test_start - embargo_seconds
        ]
        if not train or not test:
            continue
        test_values = [_number(row.get("result_r_initial_risk"), _number(row.get("realized_r"))) for row in test]
        output.append(
            {
                "fold": index,
                "train_count": len(train),
                "test_count": len(test),
                "test_avg_r": statistics.fmean(test_values),
                "test_profit_factor": _profit_factor(test),
                "test_start": test_start,
                "embargo_seconds": embargo_seconds,
            }
        )
    return output


def _controlled_effect(records: Sequence[Mapping[str, Any]], dimensions: Sequence[str]) -> tuple[str, list[str]]:
    failures: list[str] = []
    for dimension in dimensions:
        buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in records:
            buckets[str(row.get(dimension) or "unknown")].append(row)
        supported = [rows for rows in buckets.values() if len(rows) >= 5]
        if len(supported) < 2:
            failures.append(f"insufficient_control_strata:{dimension}")
            continue
        negative = sum(statistics.fmean(_number(row.get("result_r_initial_risk"), _number(row.get("realized_r"))) for row in rows) < 0 for rows in supported)
        if negative / len(supported) < 0.60:
            failures.append(f"effect_not_stable_after_control:{dimension}")
    return ("pass" if not failures else "fail", failures)


def suppression_eligibility(
    records: Sequence[Mapping[str, Any]],
    *,
    ledger_status: str,
    min_clean_sample: int = 50,
    max_profit_factor: float = 1.0,
    bootstrap_iterations: int = 1000,
    block_type: str = "day",
    seed: int = 1729,
) -> dict[str, Any]:
    reasons: list[str] = []
    clean = [
        row for row in records
        if row.get("ledger_integrity_status") == LedgerIntegrityStatus.CLEAN.value
        and bool(row.get("attribution_status") in {"attributed", "verified"})
        and bool(row.get("suppression_eligible"))
    ]
    if ledger_status != LedgerIntegrityStatus.CLEAN.value:
        reasons.append("ledger_not_clean")
    if len(clean) < min_clean_sample:
        reasons.append("minimum_clean_sample_not_met")
    values = [_number(row.get("result_r_initial_risk"), _number(row.get("realized_r"))) for row in clean]
    avg_r = statistics.fmean(values) if values else 0.0
    pf = _profit_factor(clean)
    if avg_r >= 0:
        reasons.append("average_net_r_not_negative")
    if pf >= max_profit_factor:
        reasons.append("profit_factor_not_below_threshold")
    uncertainty = block_bootstrap_uncertainty(clean, iterations=bootstrap_iterations, block_type=block_type, seed=seed)
    if not uncertainty.get("available"):
        reasons.append("uncertainty_unavailable")
    elif _number(uncertainty.get("mean_r_ci_high")) >= 0:
        reasons.append("upper_confidence_bound_not_negative")
    folds = purged_chronological_folds(clean)
    if len(folds) < 2:
        reasons.append("insufficient_chronological_folds")
    else:
        negative_folds = sum(_number(row.get("test_avg_r")) < 0 for row in folds)
        if negative_folds < 2:
            reasons.append("oos_failure_not_repeated")
        if statistics.fmean(_number(row.get("test_avg_r")) for row in folds) >= 0:
            reasons.append("oos_expectancy_not_negative")

    version_fields = ("engine_version", "runtime_input_hash", "prompt_contract_version", "policy_id", "management_version")
    version_confound = False
    version_evidence: dict[str, Any] = {}
    for field in version_fields:
        buckets: dict[str, list[float]] = defaultdict(list)
        for row, value in zip(clean, values):
            buckets[str(row.get(field) or "unknown")].append(value)
        supported = {key: vals for key, vals in buckets.items() if len(vals) >= 5}
        version_evidence[field] = {key: statistics.fmean(vals) for key, vals in supported.items()}
        if len(supported) < 2 or sum(statistics.fmean(vals) < 0 for vals in supported.values()) < 2:
            version_confound = True
    if version_confound:
        reasons.append("version_confound_detected")
    controlled_status, control_reasons = _controlled_effect(
        clean,
        ("symbol", "session_name", "regime_bucket", "asset_class", "engine_version"),
    )
    reasons.extend(control_reasons)
    if any("commission" not in row and "total_commission" not in row for row in clean):
        reasons.append("costs_not_proven_included")
    management_versions = Counter(str(row.get("management_version") or "unknown") for row in clean)
    if len(management_versions) < 2:
        reasons.append("management_version_confound_unresolved")
    reasons = sorted(set(reasons))
    return {
        "suppression_eligible": not reasons,
        "suppression_block_reasons": reasons,
        "suppression_evidence": {
            "clean_sample": len(clean),
            "avg_net_r": avg_r,
            "profit_factor": pf,
            "version_evidence": version_evidence,
        },
        "suppression_confidence_interval": uncertainty,
        "suppression_fold_results": folds,
        "version_confound_detected": version_confound,
        "controlled_effect_status": controlled_status,
    }


def enforce_policy_governance(
    rows: Sequence[Mapping[str, Any]],
    *,
    activation_allowed: bool,
    block_reasons: Sequence[str],
) -> list[dict[str, Any]]:
    """Make unsafe adaptation diagnostic without hiding its raw recommendation."""

    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["ungoverned_action"] = str(row.get("action") or row.get("family_policy_action") or row.get("symbol_policy_action") or "allow")
        row["suppression_eligible"] = bool(activation_allowed and row.get("suppression_eligible"))
        row["suppression_block_reasons"] = list(block_reasons)
        if not activation_allowed:
            if "action" in row:
                row["action"] = "diagnostic_only"
            if "family_policy_action" in row:
                row["family_policy_action"] = "diagnostic_only"
            if "symbol_policy_action" in row:
                row["symbol_policy_action"] = "diagnostic_only"
            for key in ("risk_multiplier", "session_weekday_risk_multiplier", "default_risk_multiplier"):
                if key in row:
                    row[key] = 1.0
            for key in ("score_penalty", "score_bias", "rr_floor_delta", "expected_value_bias"):
                if key in row:
                    row[key] = 0.0
        output.append(row)
    return output


def write_feature_lineage(path: Path) -> dict[str, Any]:
    manifest = {
        "feature_lineage_version": FEATURE_LINEAGE_VERSION,
        "features": list(FEATURE_LINEAGE),
        "forbidden_authority": [
            "diagnostic_legacy_setup_score",
            "heuristic_quality_estimate",
            "blended_legacy_score",
            "legacy_agreement_confidence",
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def feature_lineage_violations(manifest: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    seen: Counter[str] = Counter()
    for feature in manifest.get("features", []):
        if not isinstance(feature, Mapping):
            violations.append("invalid_feature_lineage_row")
            continue
        name = str(feature.get("feature_name") or "")
        seen[name] += 1
        if name in {"setup_score", "diagnostic_legacy_setup_score", "heuristic_quality_estimate"} and (
            feature.get("sent_to_llm") or feature.get("used_by_statistical_model") or feature.get("decision_authoritative")
        ):
            violations.append(f"legacy_feature_has_authority:{name}")
    violations.extend(f"duplicate_feature:{name}" for name, count in seen.items() if count > 1)
    return sorted(set(violations))


def stable_data_hash(records: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(list(records), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
