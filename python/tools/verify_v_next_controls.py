#!/usr/bin/env python3
"""Deterministic verification for v-next safety/cost controls."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate
from decision_integrity import assessed_execution_fingerprint
from tools import analyze_ai_trade_outcomes
from tools import repair_trade_ledger


MQL_ROOT = Path(r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5")
EA_FILE = MQL_ROOT / "Experts" / "MT5_PO3_Codex" / "PO3_AIGate_ScannerEA.mq5"
TRADE_ENGINE_FILE = MQL_ROOT / "Include" / "MT5_PO3_Codex" / "TradeEngine.mqh"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _test_config(extra: Dict[str, str] | None = None) -> ai_gate.AIGateRuntimeConfig:
    env = {
        "OPENAI_MODEL": "gpt-5.5-mini",
        "OPENAI_FALLBACK_MODELS": "gpt-5.4-mini,gpt-5.4-nano",
        "AI_REASONING_EFFORT": "low",
        "AI_MAX_OUTPUT_TOKENS": "25000",
        "AI_MIN_CONFIDENCE": "0.45",
        "AI_PROMPT_CACHE_ENABLE": "true",
        "AI_PROMPT_CACHE_KEY": "po3_live_gate_v2_test",
        "AI_PROMPT_CACHE_RETENTION": "24h",
        "AI_DECISION_CACHE_ENABLE": "true",
        "AI_DECISION_CACHE_TTL_SEC": "1800",
        "AI_DECISION_CACHE_FILE": str(Path(tempfile.gettempdir()) / "po3_ai_decision_cache_test.jsonl"),
        "AI_USE_BATCH_API": "false",
        "AI_BATCH_ONLY_FOR_BACKTEST": "true",
        "AI_BATCH_OUTPUT_DIR": str(Path(tempfile.gettempdir()) / "po3_ai_batch_test"),
        "AI_BATCH_MAX_PENDING": "1000",
        "AI_USE_FLEX": "false",
        "AI_ALLOW_FLEX_FOR_LIVE": "false",
        "AI_FLEX_LIVE_ACK": "false",
        "AI_SERVICE_TIER": "auto",
        "AI_OPENAI_TIMEOUT_SEC": "180",
        "AI_OPENAI_FLEX_TIMEOUT_SEC": "600",
        "AI_FLEX_UNAVAILABLE_RETRY_ENABLE": "true",
        "AI_FLEX_UNAVAILABLE_MAX_RETRIES": "20",
        "AI_FLEX_UNAVAILABLE_COOLDOWN_SEC": "30",
        "AI_REQUIRE_RUNTIME_INPUTS_LIVE": "true",
        "AI_REJECT_ON_MISSING_RUNTIME_INPUTS_LIVE": "true",
        "AI_HARD_PRE_GATE_BEFORE_OPENAI": "true",
        "AI_LOG_SKIPPED_CALLS": "true",
        "AI_ENABLE_SNAPSHOTS": "false",
        "AI_COST_REPORT_ENABLE": "false",
        "AI_COST_REPORT_FILE": str(Path(tempfile.gettempdir()) / "po3_ai_cost_report_test.jsonl"),
        "AI_LIVE_BUCKET_PRIORS_FILE": str(Path(tempfile.gettempdir()) / "po3_live_bucket_priors_test.json"),
    }
    if extra:
        env.update(extra)
    return ai_gate.AIGateRuntimeConfig.from_env(env)


def _install_config(cfg: ai_gate.AIGateRuntimeConfig) -> None:
    ai_gate.AI_CONFIG = cfg
    ai_gate.OPENAI_MODEL = cfg.model
    ai_gate.OPENAI_FALLBACK_MODELS = cfg.fallback_models
    ai_gate.AI_REASONING_EFFORT = cfg.reasoning_effort
    ai_gate.AI_MAX_OUTPUT_TOKENS = cfg.max_output_tokens
    ai_gate.AI_PROMPT_CACHE_KEY = cfg.prompt_cache_key if cfg.prompt_cache_enable else ""
    ai_gate.AI_ENABLE_SNAPSHOTS = cfg.enable_snapshots
    ai_gate.LIVE_BUCKET_PRIORS_FILE = cfg.live_bucket_priors_file
    ai_gate._LIVE_BUCKET_PRIORS_CACHE = (0.0, {})
    ai_gate.AI_DECISION_CACHE = ai_gate.AIDecisionCache(cfg.decision_cache_file, cfg.decision_cache_ttl_sec)


def _runtime_inputs(**overrides: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {key: True for key in ai_gate.CRITICAL_RUNTIME_INPUT_KEYS}
    values.update({
        "runtime_input_hash": "test_hash",
        "trade_only_killzones": False,
        "enable_asia_killzone": True,
        "asia_killzone_start_hour": 0,
        "asia_killzone_start_minute": 0,
        "asia_killzone_end_hour": 3,
        "asia_killzone_end_minute": 0,
        "london_killzone_start_hour": 7,
        "london_killzone_start_minute": 0,
        "london_killzone_end_hour": 10,
        "london_killzone_end_minute": 0,
        "newyork_killzone_start_hour": 8,
        "newyork_killzone_start_minute": 0,
        "newyork_killzone_end_hour": 11,
        "newyork_killzone_end_minute": 0,
        "enable_mpc_trading": True,
        "enable_bucket_risk_policy": True,
        "bucket_risk_policy_file": "PO3_AI_BUS\\config\\bucket_risk_policy.json",
        "suppress_micro_bisi_sibi_edge": False,
        "suppress_stale_fvg_branches": True,
        "suppress_touched_continuation_unless_retested": True,
        "suppress_continuation_touched_fvg": False,
        "suppress_continuation_stale_fvg": True,
        "reject_synthetic_fallback_after_crossed_obstacle": True,
        "require_ai_target_arbitration_on_obstacle": True,
        "hard_reject_crossed_obstacle_target": False,
        "allow_ai_to_use_liquidity_target_behind_minor_blocker": True,
        "allow_partial_before_obstacle": True,
        "blocker_kill_severity": 8.0,
        "blocker_major_severity": 6.5,
        "blocker_minor_max_severity": 3.5,
        "execution_reject_cost_r": 0.10,
        "execution_reduce_risk_cost_r": 0.07,
        "micro_scalp_max_cost_frac_of_planned_r": 0.10,
        "require_displacement": True,
        "allow_synthetic_rr_target": True,
        "min_live_rr2": 0.85,
        "fallback_rr2": 1.05,
        "fallback_rr_buffer_r": 0.05,
        "standard_trade_liquidity_rr_floor": 1.10,
        "max_target_atr_mult": 5.0,
        "max_target_adr_frac": 0.80,
        "obstacle_reject_r": 0.70,
        "use_ai": True,
        "ai_strict": True,
        "min_llm_quality_score_trend": 7.0,
        "ai_veto_enable": True,
        "ai_min_follow_through_prob": 0.58,
        "ai_max_invalidation_risk": 0.62,
        "ai_max_chop_risk": 0.65,
        "ai_max_post_entry_failure_risk": 0.62,
        "ai_min_final_expectancy_score": 6.80,
        "llm_quality_score_full_po3": 6.8,
        "llm_quality_score_micro_po3": 7.2,
        "llm_quality_score_continuation": 7.3,
        "llm_quality_score_range": 7.0,
        "llm_quality_score_failed_breakout": 7.2,
        "global_llm_quality_as_hard_floor": False,
        "legacy_min_ai_confidence_diagnostic": 0.45,
        "use_snapshot_ai": False,
        "require_snapshots": False,
        "exclusive_trading_enabled": False,
        "virtual_ledger_mode": "fixed_virtual_balance",
        "backend_pnl_mode": "closed_deal_pnl",
        "tester_ai_cache": True,
        "tester_ai_mode": 0,
        "tester_allow_live_wait_debug_trading": False,
    })
    values.update(overrides)
    return values


def _payload(**overrides: Any) -> Dict[str, Any]:
    candidate = {
        "candidate_index": 0,
        "candidate_id": "verify-candidate-0",
        "candidate_hash": "VERIFYHASH00000001",
        "request_execution_fingerprint": "VERIFYREQFP00000001",
        "symbol": "XAUUSD",
        "direction": "buy",
        "is_buy": True,
        "setup_code": "FULL",
        "model_code": "FULL",
        "setup_family": "micro_po3",
        "setup_class": "micro_po3",
        "setup_taxonomy_version": "20260716_setup_taxonomy_v1",
        "setup_taxonomy_enum": "MICRO_FVG_MID_REVERSAL",
        "taxonomy_mapping_source": "explicit_internal_taxonomy_enum",
        "entry_branch": "fvg_mid",
        "entry_est": 2350.0,
        "sl": 2345.0,
        "tp1": 2353.0,
        "tp2": 2356.0,
        "rr2": 1.2,
        "fvg_lower": 2349.0,
        "fvg_upper": 2351.0,
        "execution_cost_r": 0.02,
        "target_source": "liquidity",
        "target_model": "liquidity_target",
        "obstacle_kind": "none",
        "decision_input_hash": "test_decision_hash",
        "strategy_schema_version": "verify_strategy_v1",
        "source_t_sweep": 1779066000,
        "source_t_disp": 1779066060,
        "source_t_bos": 1779066120,
        "symbol_digits": 2,
        "symbol_tick_size": 0.01,
        "target_arbitration_required": False,
        "in_killzone": True,
        "fvg_mitigation_state": "fresh",
    }
    payload = {
        "id": "verify_1",
        "session_id": "verify_session",
        "request_nonce": "verify_nonce",
        "symbol": "XAUUSD",
        "is_buy": True,
        "runtime": {"tester": False, "account_trade_mode": "real"},
        "runtime_input_hash": "test_hash",
        "runtime_inputs": _runtime_inputs(),
        "po3": {
            "has_sweep": True,
            "has_displacement": True,
            "t_sweep": 1779066000,
            "t_disp": 1779066060,
            "dr_high": 2352.0,
            "dr_low": 2346.0,
            "in_killzone": True,
            "session_name": "london",
        },
        "plan": dict(candidate),
        "candidates": [candidate],
        "mkt": {"bid": 2350.0, "ask": 2350.2, "spread_r": 0.02, "spread_valid": True},
    }
    for key, value in overrides.items():
        if key == "candidate":
            payload["candidates"][0].update(value)
            payload["plan"].update(value)
        elif key == "runtime_inputs":
            payload["runtime_inputs"].update(value)
        elif key == "po3":
            payload["po3"].update(value)
        else:
            payload[key] = value
    return payload


def _full_structured_decision(
    payload: Dict[str, Any],
    *,
    chosen_index: int = 0,
    target_model: str = "liquidity_target",
    llm_quality_score: float = 8.2,
    allow: bool = True,
) -> ai_gate.Decision:
    """Create a complete trading-contract fixture, never a legacy minimal response."""
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    candidate = dict(candidates[chosen_index])
    target_candidates = payload.get("target_candidates") if isinstance(payload.get("target_candidates"), dict) else {}
    option = target_candidates.get(target_model) if isinstance(target_candidates.get(target_model), dict) else {}
    tp1 = float(option.get("tp1") or candidate.get("tp1") or 0.0)
    tp2 = float(option.get("tp2") or option.get("tp") or candidate.get("tp2") or 0.0)
    rr1 = float(option.get("rr1") or 0.6)
    rr2 = float(option.get("rr2") or candidate.get("rr2") or 1.2)
    comparison = {
        "liquidity_target": {"usable": target_model == "liquidity_target", "reason": "verification", "risk": "verified", "expected_role": "tp2"},
        "partial_before_obstacle_then_liquidity": {"usable": target_model == "partial_before_obstacle_then_liquidity", "reason": "verification", "risk": "verified", "expected_role": "tp1_plus_tp2"},
        "capped_before_obstacle": {"usable": target_model == "capped_before_obstacle", "reason": "verification", "risk": "verified", "expected_role": "tp2"},
        "synthetic_rr_fallback": {"usable": target_model == "synthetic_rr_fallback", "reason": "verification", "risk": "verified", "expected_role": "fallback_only"},
    }
    arbitration_required = bool(
        candidate.get("target_arbitration_required")
        or target_candidates.get("arbitration_required")
        or target_model != "liquidity_target"
    )
    target_arbitration = {
        "target_arbitration_schema_version": ai_gate.AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        "prompt_contract_version": ai_gate.AI_PROMPT_CONTRACT_VERSION,
        "arbitration_required": arbitration_required,
        "chosen_target_model": target_model,
        "chosen_tp1": tp1,
        "chosen_tp2": tp2,
        "chosen_rr1": rr1,
        "chosen_rr2": rr2,
        "rejected_target_models": [],
        "blocker_kind": str(candidate.get("obstacle_kind") or "none"),
        "blocker_severity": -1.0 if not arbitration_required else 4.0,
        "blocker_class": "unknown" if not arbitration_required else "moderate",
        "blocker_is_trade_killer": False,
        "why_not_liquidity_target": "selected" if target_model == "liquidity_target" else "verification alternative",
        "why_not_partial_before_obstacle": "selected" if target_model == "partial_before_obstacle_then_liquidity" else "verification alternative",
        "why_not_capped_before_obstacle": "selected" if target_model == "capped_before_obstacle" else "verification alternative",
        "why_not_synthetic_fallback": "selected" if target_model == "synthetic_rr_fallback" else "verification alternative",
        "target_decision_reason": "complete verifier target assessment",
        "target_comparison": comparison,
    }
    state = ai_gate.DECISION_APPROVE if allow else ai_gate.DECISION_REJECT
    assessment: Dict[str, Any] = {
        "candidate_index": int(candidate["candidate_index"]),
        "candidate_id": str(candidate["candidate_id"]),
        "candidate_hash": str(candidate["candidate_hash"]),
        "request_execution_fingerprint": str(candidate["request_execution_fingerprint"]),
        "setup_taxonomy_version": str(candidate["setup_taxonomy_version"]),
        "setup_taxonomy_enum": str(candidate["setup_taxonomy_enum"]),
        "taxonomy_mapping_source": str(candidate["taxonomy_mapping_source"]),
        "rule_score": 7.6,
        "llm_quality_score": float(llm_quality_score),
        "blended_legacy_score": 7.8,
        "legacy_agreement_confidence": 0.78,
        "llm_self_reported_confidence": 0.76,
        "calibrated_win_probability": None,
        "expected_net_r": None,
        "oos_predicted_probability": None,
        "calibration_bucket": "",
        "calibration_sample_size": 0,
        "calibration_lower_bound": None,
        "calibration_upper_bound": None,
        "calibration_model_version": "",
        "calibration_data_window_start": "",
        "calibration_data_window_end": "",
        "calibration_available": False,
        "raw_allow": bool(allow),
        "decision_state": state,
        "structure_quality_score": 8.0,
        "entry_timing_score": 7.8,
        "follow_through_probability": 0.72,
        "invalidation_risk": 0.28,
        "chop_risk": 0.24,
        "cost_risk": 0.18,
        "symbol_bucket_risk": 0.30,
        "session_bucket_risk": 0.26,
        "post_entry_failure_risk": 0.29,
        "final_trade_expectancy_score": 7.5,
        "veto": {"enabled": False, "reason": ""},
        "bucket_prior_override_justification": "not required for verifier fixture",
        "reasons": "complete verifier assessment",
        "rejection_codes": [] if allow else ["verification_reject"],
        "narrative_state": "audited",
        "invalidation_risks": [],
        "missing_confirmations": [],
        "suggested_risk_multiplier": 1.0 if allow else 0.0,
        "selected_target_identity": target_model,
        "selected_target_price": tp2,
        "entry": float(candidate.get("entry_est") or candidate.get("entry") or 0.0),
        "sl": float(candidate.get("sl") or 0.0),
        "tp1": tp1,
        "tp2": tp2,
        "assessed_execution_fingerprint": "pending",
        "model_version": "verify_full_structured_v1",
        "target_arbitration": target_arbitration,
    }
    assessment["assessed_execution_fingerprint"] = assessed_execution_fingerprint(candidate, assessment)
    return ai_gate.Decision(
        allow=allow,
        score=float(llm_quality_score),
        raw_allow=allow,
        chosen_index=chosen_index,
        confidence=float(assessment["llm_self_reported_confidence"]),
        decision_state=state,
        decision_quality_tier=ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
        decision_schema_version=ai_gate.AI_DECISION_SCHEMA_VERSION,
        mandatory_fields_complete=True,
        missing_mandatory_fields=[],
        invalid_mandatory_fields=[],
        selected_candidate_id=str(assessment["candidate_id"]),
        selected_candidate_hash=str(assessment["candidate_hash"]),
        request_execution_fingerprint=str(assessment["request_execution_fingerprint"]),
        assessed_execution_fingerprint=str(assessment["assessed_execution_fingerprint"]),
        selected_target_identity=target_model,
        selected_target_price=tp2,
        assessed_entry=float(assessment["entry"]),
        assessed_sl=float(assessment["sl"]),
        assessed_tp1=tp1,
        assessed_tp2=tp2,
        candidate_assessments=[assessment],
        rule_score=float(assessment["rule_score"]),
        llm_quality_score=float(llm_quality_score),
        blended_legacy_score=float(assessment["blended_legacy_score"]),
        legacy_agreement_confidence=float(assessment["legacy_agreement_confidence"]),
        llm_self_reported_confidence=float(assessment["llm_self_reported_confidence"]),
        calibrated_win_probability=None,
        expected_net_r=None,
        oos_predicted_probability=None,
        calibration_available=False,
        reasons={"ai_reasons": "complete verifier assessment"},
        decision_source="llm_full_structured",
        rejection_codes=list(assessment["rejection_codes"]),
        narrative_state="audited",
        invalidation_risks=[],
        missing_confirmations=[],
        suggested_risk_multiplier=float(assessment["suggested_risk_multiplier"]),
        model_version="verify_full_structured_v1",
        decision_id="verify_full_structured_decision",
        structure_quality_score=float(assessment["structure_quality_score"]),
        entry_timing_score=float(assessment["entry_timing_score"]),
        follow_through_probability=float(assessment["follow_through_probability"]),
        invalidation_risk=float(assessment["invalidation_risk"]),
        chop_risk=float(assessment["chop_risk"]),
        cost_risk=float(assessment["cost_risk"]),
        symbol_bucket_risk=float(assessment["symbol_bucket_risk"]),
        session_bucket_risk=float(assessment["session_bucket_risk"]),
        post_entry_failure_risk=float(assessment["post_entry_failure_risk"]),
        final_trade_expectancy_score=float(assessment["final_trade_expectancy_score"]),
        veto_enabled=False,
        veto_reason="",
        bucket_prior_override_justification=str(assessment["bucket_prior_override_justification"]),
        target_arbitration=target_arbitration,
        chosen_target_model=target_model,
        chosen_tp1=tp1,
        chosen_tp2=tp2,
        chosen_rr1=rr1,
        chosen_rr2=rr2,
        rejected_target_models=[],
        target_blocker_kind=str(target_arbitration["blocker_kind"]),
        target_blocker_severity=float(target_arbitration["blocker_severity"]),
        target_blocker_class=str(target_arbitration["blocker_class"]),
        target_blocker_is_trade_killer=False,
        target_decision_reason=str(target_arbitration["target_decision_reason"]),
        target_blocker_severity_present=True,
        target_blocker_class_present=True,
        target_blocker_is_trade_killer_present=True,
        target_decision_reason_present=True,
        why_not_liquidity_target=str(target_arbitration["why_not_liquidity_target"]),
        why_not_partial_before_obstacle=str(target_arbitration["why_not_partial_before_obstacle"]),
        why_not_capped_before_obstacle=str(target_arbitration["why_not_capped_before_obstacle"]),
        why_not_synthetic_fallback=str(target_arbitration["why_not_synthetic_fallback"]),
        target_arbitration_schema_version=ai_gate.AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        prompt_contract_version=ai_gate.AI_PROMPT_CONTRACT_VERSION,
        target_comparison_json=json.dumps(comparison, separators=(",", ":")),
    )


def _full_structured_response(payload: Dict[str, Any], req_id: str) -> Dict[str, Any]:
    decision = _full_structured_decision(payload)
    response = asdict(decision)
    response.update(
        {
            "id": req_id,
            "score": decision.llm_quality_score,
            "confidence": decision.llm_self_reported_confidence,
            "target_comparison": json.loads(decision.target_comparison_json),
            "veto": {"enabled": decision.veto_enabled, "reason": decision.veto_reason},
        }
    )
    return response


def test_env_parsing() -> None:
    cfg = _test_config({
        "AI_USE_BATCH_API": "maybe",
        "AI_USE_FLEX": "maybe",
        "AI_SERVICE_TIER": "warp",
        "AI_MAX_OUTPUT_TOKENS": "-5",
        "AI_OPENAI_TIMEOUT_SEC": "-1",
        "AI_OPENAI_FLEX_TIMEOUT_SEC": "999999",
        "AI_FLEX_UNAVAILABLE_MAX_RETRIES": "9999",
        "AI_FLEX_UNAVAILABLE_COOLDOWN_SEC": "-5",
    })
    _assert(cfg.use_batch_api is False, "invalid batch bool should fail safe false")
    _assert(cfg.use_flex is False, "invalid flex bool should fail safe false")
    _assert(cfg.service_tier == "auto", "invalid service tier should become auto")
    _assert(cfg.max_output_tokens >= 1024, "invalid token cap should be clamped")
    _assert(cfg.openai_timeout_sec >= 10, "invalid normal timeout should be clamped")
    _assert(cfg.openai_flex_timeout_sec <= 1800, "invalid flex timeout should be clamped")
    _assert(cfg.flex_unavailable_max_retries <= 100, "invalid flex retry count should be clamped")
    _assert(cfg.flex_unavailable_cooldown_sec >= 0, "invalid flex retry cooldown should be clamped")


def test_live_batch_flex_safety() -> None:
    _install_config(_test_config({"AI_USE_BATCH_API": "true"}))
    result = ai_gate.score_setup_batch_research([_payload()])
    _assert(result["reason"] == "batch_api_disabled_for_live", "Batch must be disabled for live payloads")

    _install_config(_test_config({"AI_USE_FLEX": "true", "AI_ALLOW_FLEX_FOR_LIVE": "false", "AI_FLEX_LIVE_ACK": "false"}))
    tier, flex_used, reason = ai_gate._effective_service_tier(_payload())
    _assert(tier == "auto" and not flex_used and reason == "flex_disabled_for_live", "Flex must be disabled for live without triple ack")

    _install_config(_test_config({"AI_USE_FLEX": "true", "AI_ALLOW_FLEX_FOR_LIVE": "true", "AI_FLEX_LIVE_ACK": "true"}))
    tier, flex_used, reason = ai_gate._effective_service_tier(_payload())
    _assert(tier == "flex" and flex_used and reason == "", "Flex should require all three live flags")


def test_flex_unavailable_retries() -> None:
    _install_config(_test_config({
        "AI_USE_FLEX": "true",
        "AI_ALLOW_FLEX_FOR_LIVE": "true",
        "AI_FLEX_LIVE_ACK": "true",
        "AI_FLEX_UNAVAILABLE_MAX_RETRIES": "20",
        "AI_FLEX_UNAVAILABLE_COOLDOWN_SEC": "0",
    }))

    class FlexUnavailableError(Exception):
        status_code = 503

    calls = {"count": 0}

    def flaky_call(**kwargs: Any) -> Dict[str, Any]:
        calls["count"] += 1
        if calls["count"] < 3:
            raise FlexUnavailableError("503 - flex unavailable, try again later")
        return {"ok": True, "service_tier": kwargs.get("service_tier")}

    result = ai_gate._call_openai_with_flex_retries(flaky_call, {"service_tier": "flex"})
    _assert(result["ok"] is True, "flex retry should eventually return success")
    _assert(calls["count"] == 3, "flex retry should retry transient failures")

    calls["count"] = 0
    try:
        ai_gate._call_openai_with_flex_retries(flaky_call, {"service_tier": "auto"})
    except FlexUnavailableError:
        pass
    else:
        raise AssertionError("non-flex call should not use flex retry loop")
    _assert(calls["count"] == 1, "non-flex failure should not be retried by flex loop")


def _expect_reject(payload: Dict[str, Any], code: str) -> None:
    dec = ai_gate.hard_pre_gate(payload, ai_gate.AI_CONFIG, 0)
    _assert(not dec.allow, f"expected reject {code}")
    _assert(code in (dec.rejection_codes or []), f"missing rejection code {code}: {dec.rejection_codes}")


def test_hard_pre_gate() -> None:
    _install_config(_test_config())
    _expect_reject(_payload(runtime_inputs={"trade_only_killzones": True}, po3={"in_killzone": False}, candidate={"in_killzone": False}), "trade_only_killzone_block")
    _expect_reject(_payload(runtime_inputs={"suppress_micro_bisi_sibi_edge": True}, candidate={"setup_family": "micro_bisi_sibi", "entry_branch": "fvg_edge"}), "suppressed_micro_bisi_sibi_edge")
    _expect_reject(_payload(candidate={"fvg_mitigation_state": "stale"}), "suppressed_stale_fvg_branch")
    _expect_reject(_payload(candidate={"execution_cost_r": 0.10}), "execution_cost_r_too_high")
    _expect_reject(_payload(candidate={"target_source": "synthetic_rr_fallback", "obstacle_kind": "crossed_opposing_imbalance"}), "synthetic_fallback_crossed_obstacle_blocked")
    arbitrating = _payload(
        candidate={
            "target_source": "synthetic_rr_fallback",
            "obstacle_kind": "crossed_opposing_imbalance",
            "target_arbitration_required": True,
            "liquidity_target_preserved": 2368.0,
            "fallback_tp": 2356.0,
            "fallback_rr": 1.2,
            "capped_before_obstacle_tp": 2354.8,
            "capped_before_obstacle_rr": 0.96,
        },
        target_candidates={
            "arbitration_required": True,
            "obstacle_kind": "crossed_opposing_imbalance",
            "liquidity_target": {"available": True, "model": "liquidity_target", "tp2": 2368.0, "rr2": 3.6, "blocked_by_obstacle": True},
            "capped_before_obstacle": {"available": True, "model": "capped_before_opposing_imbalance", "tp2": 2354.8, "rr2": 0.96},
            "synthetic_rr_fallback": {"available": True, "model": "synthetic_rr_fallback", "tp2": 2356.0, "rr2": 1.2, "crosses_obstacle": True},
        },
    )
    arbitration_decision = ai_gate.hard_pre_gate(arbitrating, ai_gate.AI_CONFIG, 0)
    _assert(arbitration_decision.allow and arbitration_decision.decision_source == "hard_pre_gate_pass", "target arbitration should pass hard pre-gate before OpenAI")
    _expect_reject(
        _payload(
            runtime_inputs={"hard_reject_crossed_obstacle_target": True},
            candidate={
                "target_source": "synthetic_rr_fallback",
                "obstacle_kind": "crossed_opposing_imbalance",
                "target_arbitration_required": True,
                "liquidity_target_preserved": 2368.0,
            },
            target_candidates={
                "arbitration_required": True,
                "obstacle_kind": "crossed_opposing_imbalance",
                "liquidity_target": {"available": True, "model": "liquidity_target", "tp2": 2368.0, "rr2": 3.6},
                "synthetic_rr_fallback": {"available": True, "model": "synthetic_rr_fallback", "tp2": 2356.0, "rr2": 1.2, "crosses_obstacle": True},
            },
        ),
        "synthetic_fallback_crossed_obstacle_blocked",
    )
    _expect_reject(_payload(mkt={"bid": 2356.2, "ask": 2356.4, "spread_r": 0.02, "spread_valid": True}), "target_already_reached")
    missing = _payload(runtime_inputs={})
    missing.pop("runtime_inputs", None)
    _expect_reject(missing, "runtime_inputs_missing_live_reject")

    called = {"openai": False}
    original = ai_gate._score_setup_openai
    def _boom(_payload: Dict[str, Any]) -> ai_gate.Decision:
        called["openai"] = True
        raise AssertionError("OpenAI should not be called")
    ai_gate._score_setup_openai = _boom
    try:
        dec = ai_gate.score_setup_live(_payload(candidate={"execution_cost_r": 0.10}))
        _assert(not dec.allow and "execution_cost_r_too_high" in (dec.rejection_codes or []), "hard pre-gate should reject")
        _assert(not called["openai"], "hard pre-gate should skip OpenAI")
    finally:
        ai_gate._score_setup_openai = original


def test_decision_cache() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _test_config({"AI_DECISION_CACHE_FILE": str(Path(tmp) / "cache.jsonl")})
        _install_config(cfg)
        payload = _payload()
        sig, base_sig, fields = ai_gate._decision_cache_signature(payload, 0)
        decision = _full_structured_decision(payload, llm_quality_score=7.2)
        ai_gate.AI_DECISION_CACHE.store(sig, base_sig, fields, decision)
        hit, status = ai_gate.AI_DECISION_CACHE.lookup(sig, base_sig)
        _assert(hit is not None and status == "hit", "same signature should cache-hit")
        changed = _payload(candidate={"tp2": 2360.0})
        sig2, base2, _fields2 = ai_gate._decision_cache_signature(changed, 0)
        miss, status2 = ai_gate.AI_DECISION_CACHE.lookup(sig2, base2)
        _assert(miss is None and status2 == "invalidated_material_field_changed", "changed target should invalidate")
        changed_obstacle = _payload(candidate={"obstacle_kind": "crossed_htf_opposing_imbalance", "obstacle_price": 2354.0})
        sig_ob, base_ob, _fields_ob = ai_gate._decision_cache_signature(changed_obstacle, 0)
        miss_ob, status_ob = ai_gate.AI_DECISION_CACHE.lookup(sig_ob, base_ob)
        _assert(miss_ob is None and status_ob == "invalidated_material_field_changed", "changed obstacle should invalidate")
        changed_obstacle_distance = _payload(candidate={"obstacle_distance_r": 0.42})
        sig_dist, base_dist, _fields_dist = ai_gate._decision_cache_signature(changed_obstacle_distance, 0)
        miss_dist, status_dist = ai_gate.AI_DECISION_CACHE.lookup(sig_dist, base_dist)
        _assert(miss_dist is None and status_dist == "invalidated_material_field_changed", "changed obstacle distance should invalidate")
        changed_fallback_buffer = _payload(runtime_inputs={"fallback_rr_buffer_r": 0.10})
        sig_buf, base_buf, _fields_buf = ai_gate._decision_cache_signature(changed_fallback_buffer, 0)
        miss_buf, status_buf = ai_gate.AI_DECISION_CACHE.lookup(sig_buf, base_buf)
        _assert(miss_buf is None and status_buf == "invalidated_material_field_changed", "changed fallback buffer should invalidate")
        changed_candidate = _payload(
            candidate={"target_arbitration_required": True, "liquidity_target_preserved": 2368.0},
            target_candidates={
                "arbitration_required": True,
                "liquidity_target": {"available": True, "model": "liquidity_target", "tp2": 2368.0, "rr2": 3.6},
            },
        )
        sig3, base3, _fields3 = ai_gate._decision_cache_signature(changed_candidate, 0)
        miss3, status3 = ai_gate.AI_DECISION_CACHE.lookup(sig3, base3)
        _assert(
            miss3 is None and status3 in {"invalidated_material_field_changed", "miss"},
            "changed target candidates must never reuse the cached assessment",
        )

        record_payload = _payload(
            runtime_input_hash="record_hash",
            runtime_inputs={
                "runtime_input_hash": "record_hash",
                "tester_ai_cache": False,
                "tester_ai_mode": 0,
                "tester_allow_live_wait_debug_trading": False,
            },
        )
        live_wait_payload = _payload(
            runtime_input_hash="live_wait_hash",
            runtime_inputs={
                "runtime_input_hash": "live_wait_hash",
                "tester_ai_cache": True,
                "tester_ai_mode": 2,
                "tester_allow_live_wait_debug_trading": False,
            },
        )
        cache_only_payload = _payload(
            runtime_input_hash="cache_only_hash",
            runtime_inputs={
                "runtime_input_hash": "cache_only_hash",
                "tester_ai_cache": True,
                "tester_ai_mode": 1,
                "tester_allow_live_wait_debug_trading": False,
            },
        )
        live_wait_trade_toggle_payload = _payload(
            runtime_input_hash="live_wait_trade_toggle_hash",
            runtime_inputs={
                "runtime_input_hash": "live_wait_trade_toggle_hash",
                "tester_ai_cache": True,
                "tester_ai_mode": 2,
                "tester_allow_live_wait_debug_trading": True,
                "tester_ai_mode_name": "TESTER_AI_LIVE_WAIT_DEBUG",
            },
            tester_ai_mode_name="live_wait_debug",
        )
        named_cache_only_payload = _payload(
            runtime_input_hash="named_cache_only_hash",
            runtime_inputs={
                "runtime_input_hash": "named_cache_only_hash",
                "tester_ai_cache": True,
                "tester_ai_mode": 1,
                "tester_allow_live_wait_debug_trading": False,
                "tester_ai_mode_name": "TESTER_AI_CACHE_ONLY",
            },
            tester_ai_mode_name="cache_only",
        )
        record_sig, record_base, record_fields = ai_gate._decision_cache_signature(record_payload, 0)
        live_wait_sig, live_wait_base, live_wait_fields = ai_gate._decision_cache_signature(live_wait_payload, 0)
        cache_only_sig, cache_only_base, _ = ai_gate._decision_cache_signature(cache_only_payload, 0)
        toggle_sig, toggle_base, _ = ai_gate._decision_cache_signature(live_wait_trade_toggle_payload, 0)
        named_cache_only_sig, named_cache_only_base, _ = ai_gate._decision_cache_signature(named_cache_only_payload, 0)
        _assert(live_wait_sig == record_sig and live_wait_base == record_base, "LIVE_WAIT_DEBUG recording must reuse RECORD_ONLY cache signature")
        _assert(cache_only_sig == record_sig and cache_only_base == record_base, "CACHE_ONLY replay must hit RECORD_ONLY cache signature")
        _assert(toggle_sig == record_sig and toggle_base == record_base, "InpTesterAllowLiveWaitDebugTrading must not change cache signature")
        _assert(named_cache_only_sig == record_sig and named_cache_only_base == record_base, "tester mode labels must not change cache signature")
        ai_gate.AI_DECISION_CACHE.store(record_sig, record_base, record_fields, decision)
        replay_hit, replay_status = ai_gate.AI_DECISION_CACHE.lookup(cache_only_sig, cache_only_base)
        _assert(replay_hit is not None and replay_status == "hit", "RECORD_ONLY cache entry should replay in CACHE_ONLY")
        live_wait_hit, live_wait_status = ai_gate.AI_DECISION_CACHE.lookup(live_wait_sig, live_wait_base)
        _assert(live_wait_hit is not None and live_wait_status == "hit", "LIVE_WAIT_DEBUG cache signature should replay in CACHE_ONLY")

        cfg2 = _test_config({"AI_DECISION_CACHE_FILE": str(Path(tmp) / "cache_live_wait.jsonl")})
        _install_config(cfg2)
        ai_gate.AI_DECISION_CACHE.store(live_wait_sig, live_wait_base, live_wait_fields, decision)
        live_wait_replay_hit, live_wait_replay_status = ai_gate.AI_DECISION_CACHE.lookup(cache_only_sig, cache_only_base)
        _assert(
            live_wait_replay_hit is not None and live_wait_replay_status == "hit",
            "LIVE_WAIT_DEBUG non-trading recording should replay in CACHE_ONLY",
        )


def test_decision_cache_schema_versioning() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "cache.jsonl"
        cfg = _test_config({"AI_DECISION_CACHE_FILE": str(cache_file)})
        _install_config(cfg)
        payload = _payload(
            candidate={"target_arbitration_required": True},
            target_candidates={
                "arbitration_required": True,
                "liquidity_target": {"available": True, "model": "liquidity_target", "tp2": 2368.0, "rr2": 3.6},
                "synthetic_rr_fallback": {"available": True, "model": "synthetic_rr_fallback", "tp2": 2355.0, "rr2": 1.10},
            },
        )
        sig, base_sig, fields = ai_gate._decision_cache_signature(payload, 0)
        old_row = {
            "timestamp": 9999999999,
            "signature": sig,
            "base_signature": base_sig,
            "fields": fields,
            "decision": {
                "allow": True,
                "score": 7.8,
                "chosen_index": 0,
                "confidence": 0.8,
                "decision_source": "llm_blended",
                "chosen_target_model": "synthetic_rr_fallback",
                "target_decision_reason": "old schema row",
            },
        }
        cache_file.write_text(json.dumps(old_row, separators=(",", ":")) + "\n", encoding="utf-8")
        miss, status = ai_gate.AI_DECISION_CACHE.lookup(sig, base_sig)
        _assert(miss is None and status == "cache_miss_due_to_schema_version", "old cache schema must be a miss, not an invalid arbitration response")

        current = _full_structured_decision(
            payload,
            target_model="synthetic_rr_fallback",
            llm_quality_score=8.0,
        )
        ai_gate.AI_DECISION_CACHE.store(sig, base_sig, fields, current)
        hit, status2 = ai_gate.AI_DECISION_CACHE.lookup(sig, base_sig)
        _assert(hit is not None and status2 == "hit", "current cache schema should be usable")


def test_mql_target_safety_source() -> None:
    _assert(TRADE_ENGINE_FILE.exists(), f"TradeEngine missing: {TRADE_ENGINE_FILE}")
    src = TRADE_ENGINE_FILE.read_text(encoding="utf-8", errors="ignore")
    ea_src = EA_FILE.read_text(encoding="utf-8", errors="ignore")
    bridge_src = (MQL_ROOT / "Include" / "MT5_PO3_Codex" / "AIGateBridge.mqh").read_text(encoding="utf-8", errors="ignore")
    config_src = (MQL_ROOT / "Include" / "MT5_PO3_Codex" / "Config.mqh").read_text(encoding="utf-8", errors="ignore")
    risk_src = (MQL_ROOT / "Include" / "MT5_PO3_Codex" / "Risk.mqh").read_text(encoding="utf-8", errors="ignore")
    required = [
        "ValidateAiChosenTargetBeforeWatchlist",
        "_RRMeetsFloor",
        "_RREps",
        "InpFallbackRRBufferR",
        "_EffectiveFallbackRR",
        "[fallback_target]",
        "[fallback_recalc]",
        "rr2_raw=",
        "[target_validation_context]",
        "ai_chosen_capped_target_rr_too_low",
        "ai_chosen_synthetic_fallback_rr_too_low",
        "ai_chosen_target_already_reached",
        "target_model_unknown",
        "target_arbitration_parse",
        "invalid_ai_target_arbitration_response",
        "why_not_liquidity_target",
        "why_not_partial_before_obstacle",
        "why_not_capped_before_obstacle",
        "legacy_synthetic_fallback_crossed_obstacle_gate_skipped",
        "hard_reject_crossed_obstacle active=true",
        "IsStructuralPlanRebuildFailure",
        "[execution_rebuild] structural_failure=true",
        "[watchlist] invalidated terminal=true",
        "[watchlist_precheck]",
        "[summary] target_choices_by_model=",
        "[summary] target_choice.synthetic_rr_fallback=",
        "[ai_freshness]",
        "InpTesterRejectStaleAiResults",
        "InpTesterMaxAiResultAgeSimMinutes",
        "InpTesterFreezeAiExecutionSnapshot",
        "ai_result_stale_in_tester",
        "AI_TARGET_ARBITRATION_SCHEMA_VERSION",
        "AI_PROMPT_CONTRACT_VERSION",
        "_HasStoredTargetArbitration",
        "_ApplyStoredTargetArbitrationAfterRebuild",
        "pending_relax] using_stored_target_arbitration=true",
        "missing_stored_target_arbitration_for_pending_relax",
        "cache_miss_due_to_schema_version",
        "accept_after_blocking_wait",
        "sim_age_diagnostic_only=",
        "ai_wait_timeout_real_time",
        "ai_transport_error",
        "ai_response_missing_file",
        "[ai_cooldown] skipped reason=",
        "[final_summary] ai_requests_queued_total=",
        "m_total_ai_results_accepted_after_blocking_wait++",
        "!tester_blocking_wait",
        "NoteTesterAiWaitStarted",
        "PendingAIRequestIds",
        "failed_to_rebuild_live_plan_prices:",
        "synthetic_rr_capped_to_max_distance",
        "[target_feasibility]",
        "[target_rebuild_feasibility]",
        "[target_rebuild_downgrade]",
        "[ai_target_choice_validation]",
        "ai_chose_infeasible_target",
        "invalid_target_arbitration_required_flag",
        "[target_validation_rr]",
        "[target_validation_overall]",
        "rr_floor_pass=",
        "max_distance_pass=",
        "target_feasibility.synthetic_infeasible_max_distance",
        "target_feasibility.synthetic_capped_to_max_distance",
        "reject.tester_ai_cache_miss",
        "TESTER_AI_CACHE_ONLY",
        "TESTER_AI_LIVE_WAIT_DEBUG",
        "TESTER_AI_RECORD_ONLY",
        "TESTER_AI_RECORD_ONLY = 0",
        "TESTER_AI_CACHE_ONLY = 1",
        "TESTER_AI_LIVE_WAIT_DEBUG = 2",
        "InpTesterAiMode = TESTER_AI_RECORD_ONLY",
        "InpTesterAllowLiveWaitDebugTrading",
        "[fatal_config] tester_ai_mode=cache_only requires InpTesterAiCache=true",
        "_InvalidTesterCacheOnlyConfig",
        "cache_only_valid=true",
        "tester_ai_cache_miss",
        "tester_live_ai_wait_not_backtest_safe",
        "record_only_requests_exported",
        "tester_ai_cache_miss_total=",
        "plans_valid_total=",
        "po3_context_created_total=",
        "fvg_candidates_created_total=",
        "trades_opened_total=",
        "tester_live_wait_result_not_tradeable_due_to_sim_time_jump",
        "record_response_do_not_trade",
        "stored_from_live_wait_debug",
        "live_wait_debug_response_recorded=true tradable=false",
        "[snapshot_diagnostic]",
        "m_total_tester_live_wait_non_tradeable_sim_jump",
        "m_total_tester_live_wait_debug_trading_disabled",
        "tester_ai.live_wait_debug.non_tradeable_due_to_sim_time_jump",
        "tester_ai.live_wait_debug.trading_disabled",
        "tester_live_wait_debug_trading_disabled",
        "allow_trading=",
        "_IsTesterRuntime",
        "_TesterLiveWaitDebugMode",
        "live_mode_sim_age_ignored=",
        "target_choice.cap_before_opposing_imbalance",
        "target_choice.capped_before_opposing_imbalance",
        "target_choice.capped_before_htf_opposing_imbalance",
        "blocker_class.killer",
        "blocker_class.unknown",
        "watchlist_precheck.reject.price_broke_fvg_high",
        "watchlist_precheck.reject.price_broke_fvg_low",
        "watchlist_precheck.reject.opposite_confirmed_po3",
        "watchlist_precheck.reject.structural_invalidation_high",
        "[ai_cache] hit=true req_id=",
        "clean_backtest_requires_cache_replay=true",
        "tester_cache_signature",
        "tester_cache_key",
        "CACHE_ONLY has no tester cache files. Run RECORD_ONLY + Python cache exporter first.",
        "no_trades_expected=true next_step=run_python_cache_export_then_cache_only",
        "suppression_scope=micro_bisi_sibi_only",
        "_ApplyFeasibleTargetSanitizer",
        "no_feasible_target",
        "InpEnableMPCTrading",
        "InpEnableBucketRiskPolicy",
        "InpBucketRiskPolicyFile",
        "InpAiVetoEnable",
        "InpAiMinFollowThroughProb",
        "InpAiMaxInvalidationRisk",
        "InpAiMaxChopRisk",
        "InpAiMaxPostEntryFailureRisk",
        "InpAiMinFinalExpectancyScore",
        "_FilterOutcomePolicyBeforeAI",
        "_ApplyOutcomePolicy",
        "[mpc_block]",
        "[bucket_policy_block]",
        "[bucket_policy_risk_reduce]",
        "mpc_trading_disabled",
        "mpc_candidates_blocked_total=",
        "mpc_ai_calls_saved_total=",
        "mpc_watchlist_blocks_total=",
        "mpc_execution_blocks_total=",
        "[ai_veto]",
        "follow_through_probability",
        "post_entry_failure_risk",
        "final_trade_expectancy_score",
        "bucket_prior_override_justification",
        "completed_ai_trades.jsonl",
        "[trade_completed]",
        "minutes_to_0_25r_mfe",
        "stuck_no_mfe_triggered",
        "dr_and_structural_invalid_triggered",
        "[portfolio_risk_block]",
        "[daily_loss_block]",
    ]
    for needle in required:
        _assert(needle in src or needle in bridge_src or needle in config_src or needle in risk_src, f"MQL target safety source missing {needle}")
    bridge_required = [
        "blocker_features",
        "effective_fallback_rr",
        "fallback_rr_buffer_r",
        "target_blocker_severity_present",
        "_SchemaRequireBool(txt, \"target_blocker_severity_present\"",
        "target_arbitration_schema_version",
        "prompt_contract_version",
        "target_comparison",
        "synthetic_rr_capped_to_max_distance",
        "tester_ai_cache",
        "tester_ai_mode",
        "tester_allow_live_wait_debug_trading",
        "enable_mpc_trading",
        "enable_bucket_risk_policy",
        "bucket_risk_policy_file",
        "ai_veto_enable",
        "ai_min_follow_through_prob",
        "ai_max_invalidation_risk",
        "ai_max_chop_risk",
        "ai_max_post_entry_failure_risk",
        "ai_min_final_expectancy_score",
        "setup_code",
        "is_mpc",
        "bucket_prior",
        "raw_allow",
        "structure_quality_score",
        "entry_timing_score",
        "follow_through_probability",
        "invalidation_risk",
        "chop_risk",
        "post_entry_failure_risk",
        "final_trade_expectancy_score",
        "veto",
        "bucket_prior_override_justification",
    ]
    for needle in bridge_required:
        _assert(needle in bridge_src, f"AIGateBridge source missing {needle}")
    ea_required = [
        "_EffectivePauseScanWhilePendingAI",
        "_TesterLiveAiWaitMode",
        "forcing_pause_scan_while_pending_ai=true",
        "[ai_mode] tester=",
        "[tester_ai_wait] started req_id=",
        "[tester_ai_wait] poll req_id=",
        "[tester_ai_wait] completed req_id=",
        "[tester_ai_wait] timeout req_id=",
        "[tester_ai_wait] scan_resumed req_id=",
        "g_engine.FinalizeScan();",
        "g_engine.EvaluateSymbol(sym);",
        "_WaitForPendingAIInTester();",
        "raw_pause_for_ai=",
        "[tester_ai_mode] mode=cache_only",
        "[tester_ai_mode] mode=live_wait_debug",
        "[tester_ai_mode] raw_value=",
        "raw_name=",
        "effective_name=",
        "[tester_ai_workflow] Step 1: run RECORD_ONLY to export requests.",
        "[tester_ai_workflow] Step 2: run python ai_gate.py / batch processor to fill cache.",
        "[tester_ai_workflow] Step 3: rerun with CACHE_ONLY and InpTesterAiCache=true.",
        "[runtime_inputs] InpTesterAiCache=",
        "[runtime_inputs] InpTesterAiMode=",
        "[runtime_inputs] InpTesterAllowLiveWaitDebugTrading=",
        "[runtime_inputs] runtime_input_hash=",
    ]
    for needle in ea_required:
        _assert(needle in ea_src, f"EA tester wait source missing {needle}")


def test_live_mode_sim_age_guard_source() -> None:
    src = TRADE_ENGINE_FILE.read_text(encoding="utf-8", errors="ignore")
    _assert("bool _IsTesterRuntime() const" in src, "tester runtime helper missing")
    _assert("bool _TesterLiveWaitDebugMode() const" in src, "tester LIVE_WAIT_DEBUG helper missing")
    _assert("bool tester_runtime = _IsTesterRuntime();" in src, "AI freshness path should bind tester runtime once")
    _assert(
        "bool tester_live_wait_debug_mode = _TesterLiveWaitDebugMode();" in src,
        "LIVE_WAIT_DEBUG sim-age path should be tester-only",
    )
    _assert(
        "bool tester_live_wait_sim_time_jump = (tester_live_wait_debug_mode &&" in src,
        "sim-time jump must depend on tester-only LIVE_WAIT_DEBUG mode",
    )
    _assert(
        "bool tester_stale_ai = (tester_runtime &&" in src,
        "stale simulated-age reject must be tester-only",
    )
    _assert(
        'live_mode_sim_age_ignored=" + (!tester_runtime ? "true" : "false")' in src,
        "live/demo AI freshness log should prove sim age is ignored",
    )
    reject_idx = src.find("tester_live_wait_result_not_tradeable_due_to_sim_time_jump")
    guard_idx = src.rfind("if(tester_live_wait_non_tradeable)", 0, reject_idx)
    _assert(reject_idx > 0 and guard_idx > 0, "tester sim-jump reject should live only under non-tradeable tester branch")


def test_mql_tester_cache_contract_strict_reject_source() -> None:
    src = TRADE_ENGINE_FILE.read_text(encoding="utf-8", errors="ignore")
    required = [
        "_TesterCacheDecisionSourceLoadable",
        "cached_decision_schema != AI_DECISION_SCHEMA_VERSION",
        "cached_schema != AI_TARGET_ARBITRATION_SCHEMA_VERSION",
        "cached_prompt_contract != AI_PROMPT_CONTRACT_VERSION",
        "cache_miss_due_to_schema_version",
        "strict_cached_decision_validation_failed",
    ]
    for needle in required:
        _assert(needle in src, f"MQL strict tester cache contract source missing {needle}")
    _assert("cache_contract_backfilled_from_signature" not in src, "legacy cache decisions must not be silently upgraded")
    _assert("contract_backfilled_from_signature" not in src, "legacy prompt contracts must not be silently upgraded")


def test_entry_time_comment_signature_source() -> None:
    trade_src = TRADE_ENGINE_FILE.read_text(encoding="utf-8", errors="ignore")
    po3_src = (MQL_ROOT / "Include" / "MT5_PO3_Codex" / "PO3.mqh").read_text(encoding="utf-8", errors="ignore")
    po3_required = [
        "SessionNameAt",
        "SessionCodeAt",
        "IsKillzoneAt",
        "KillzoneCodeAt",
    ]
    for needle in po3_required:
        _assert(needle in po3_src, f"PO3 entry-time session helper missing {needle}")
    trade_required = [
        "_SessionCodeForEntryTime",
        "_KillzoneCodeForEntryTime",
        "_BrokerCommentForPlanAtEntryTime",
        "_ApplyEntryTimeCommentIdentity",
        "_ApplyEntryTimeCommentIdentity(live, market_entry_time)",
        "_ApplyEntryTimeCommentIdentity(pending, pending_entry_time)",
        "entry_session=",
        "entry_killzone=",
        "entry_time=",
    ]
    for needle in trade_required:
        _assert(needle in trade_src, f"TradeEngine entry-time comment source missing {needle}")
    old_context_comment = 'string session = (StringLen(p.session_code) > 0 ? p.session_code : _SessionCodeForPlan(p));'
    _assert(old_context_comment not in trade_src, "broker comment must not prefer context session_code over entry-time session")


def test_target_arbitration_metadata() -> None:
    missing = ai_gate._target_kwargs_from_dict({"chosen_target_model": "synthetic_rr_fallback"})
    _assert(missing["target_blocker_severity"] == -1.0, "missing blocker severity should use -1 sentinel")
    _assert(not missing["target_blocker_severity_present"], "missing blocker severity should not be marked present")
    _assert(not missing["target_blocker_class_present"], "missing blocker class should not be marked present")
    complete = ai_gate._target_kwargs_from_dict(
        {
            "chosen_target_model": "synthetic_rr_fallback",
            "blocker_severity": 2.5,
            "blocker_class": "minor",
            "blocker_is_trade_killer": False,
            "why_not_liquidity_target": "minor blocker still lowers target quality",
            "why_not_partial_before_obstacle": "partial path weaker than fallback",
            "why_not_capped_before_obstacle": "cap rr too low",
            "why_not_synthetic_fallback": "chosen fallback has clean rr",
            "target_decision_reason": "fallback is clean enough",
            "target_comparison": {
                "liquidity_target": {"usable": False, "reason": "blocked", "risk": "moderate", "expected_role": "reject"},
                "partial_before_obstacle_then_liquidity": {"usable": False, "reason": "not useful", "risk": "medium", "expected_role": "reject"},
                "capped_before_obstacle": {"usable": False, "reason": "rr low", "risk": "low reward", "expected_role": "reject"},
                "synthetic_rr_fallback": {"usable": True, "reason": "clean", "risk": "known", "expected_role": "fallback_only"},
            },
        }
    )
    _assert(complete["target_blocker_severity_present"], "present severity should be marked")
    _assert(complete["target_blocker_class_present"], "present class should be marked")
    _assert(complete["target_blocker_is_trade_killer_present"], "present killer flag should be marked")
    _assert(complete["target_decision_reason_present"], "present decision reason should be marked")
    _assert(complete["why_not_capped_before_obstacle"], "why-not capped explanation should be preserved")
    _assert(complete["target_arbitration_schema_version"] == ai_gate.AI_TARGET_ARBITRATION_SCHEMA_VERSION, "schema version should be preserved")
    _assert("liquidity_target" in complete["target_comparison_json"], "target comparison should be serialized")


def _feasible_decision(model: str) -> ai_gate.Decision:
    return _full_structured_decision(
        _payload(candidate={"target_arbitration_required": True}),
        target_model=model,
        llm_quality_score=8.1,
    )


def test_target_feasibility_choice_validation() -> None:
    _install_config(_test_config())
    target_candidates = {
        "arbitration_required": True,
        "obstacle_kind": "crossed_opposing_imbalance",
        "liquidity_target": {
            "available": False,
            "model": "liquidity_target",
            "tp2": 2460.0,
            "rr2": 3.6,
            "feasible_for_tp2": False,
            "infeasible_reason": "blocked_by_obstacle",
        },
        "partial_before_obstacle_then_liquidity": {
            "available": True,
            "model": "partial_before_obstacle_then_liquidity",
            "tp1": 2352.0,
            "rr1": 0.40,
            "tp2": 2365.0,
            "rr2": 3.00,
            "feasible_for_tp2": True,
            "feasible_for_tp1_only": True,
            "infeasible_reason": "",
        },
        "synthetic_rr_fallback": {
            "available": True,
            "model": "synthetic_rr_fallback",
            "tp2": 2377.43,
            "rr2": 1.70,
            "configured_rr": 1.70,
            "reward_distance_price": 107.44,
            "max_allowed_distance": 79.78,
            "exceeds_max_target_distance": True,
            "feasible_for_tp2": False,
            "infeasible_reason": "exceeds_max_target_distance",
        },
        "synthetic_rr_capped_to_max_distance": {
            "available": True,
            "model": "synthetic_rr_capped_to_max_distance",
            "tp2": 2365.10,
            "rr2": 1.25,
            "configured_rr": 1.70,
            "feasible_for_tp2": True,
            "infeasible_reason": "",
            "reason": "fallback_capped_by_max_distance",
        },
    }
    payload = _payload(
        candidate={
            "target_arbitration_required": False,
            "target_source": "synthetic_rr_fallback",
            "obstacle_kind": "crossed_opposing_imbalance",
        },
        target_candidates=target_candidates,
    )
    _assert(ai_gate._target_arbitration_required(payload, payload["candidates"][0]), "crossed obstacle plus fallback should infer arbitration_required")

    downgraded = ai_gate._validate_ai_target_choice_against_feasibility(payload, _feasible_decision("synthetic_rr_fallback"), 0)
    _assert(downgraded.allow, "valid capped fallback should keep decision alive")
    _assert(downgraded.chosen_target_model == "partial_before_obstacle_then_liquidity", "partial path should outrank capped fallback when TP2 is feasible")
    _assert(abs(downgraded.chosen_rr2 - 3.00) < 1e-9, "partial rewrite should preserve liquidity TP2 RR")

    capped_only_candidates = dict(target_candidates)
    capped_only_candidates["partial_before_obstacle_then_liquidity"] = {
        "available": True,
        "model": "partial_before_obstacle_then_liquidity",
        "tp1": 2352.0,
        "rr1": 0.40,
        "tp2": 2365.0,
        "rr2": 3.00,
        "feasible_for_tp2": False,
        "infeasible_reason": "runner_invalid",
    }
    capped_only = _payload(
        candidate={
            "target_arbitration_required": True,
            "target_source": "synthetic_rr_fallback",
            "obstacle_kind": "crossed_opposing_imbalance",
        },
        target_candidates=capped_only_candidates,
    )
    capped_rewrite = ai_gate._validate_ai_target_choice_against_feasibility(capped_only, _feasible_decision("synthetic_rr_fallback"), 0)
    _assert(capped_rewrite.allow, "valid capped fallback should keep decision alive when higher-priority targets are infeasible")
    _assert(capped_rewrite.chosen_target_model == "synthetic_rr_capped_to_max_distance", "raw infeasible fallback should downgrade to capped fallback")
    _assert(abs(capped_rewrite.chosen_rr2 - 1.25) < 1e-9, "downgrade should preserve capped RR")

    invalid_candidates = dict(target_candidates)
    invalid_candidates["partial_before_obstacle_then_liquidity"] = {
        "available": True,
        "model": "partial_before_obstacle_then_liquidity",
        "tp1": 2352.0,
        "rr1": 0.40,
        "tp2": 2365.0,
        "rr2": 3.00,
        "feasible_for_tp2": False,
        "infeasible_reason": "runner_invalid",
    }
    invalid_candidates["synthetic_rr_capped_to_max_distance"] = {
        "available": True,
        "model": "synthetic_rr_capped_to_max_distance",
        "tp2": 2353.8,
        "rr2": 0.76,
        "feasible_for_tp2": False,
        "infeasible_reason": "rr_too_low_after_max_distance_cap",
    }
    rejected_payload = _payload(
        candidate={
            "target_arbitration_required": True,
            "target_source": "synthetic_rr_fallback",
            "obstacle_kind": "crossed_opposing_imbalance",
        },
        target_candidates=invalid_candidates,
    )
    rejected = ai_gate._validate_ai_target_choice_against_feasibility(rejected_payload, _feasible_decision("synthetic_rr_fallback"), 0)
    _assert(not rejected.allow, "raw infeasible fallback with no capped alternative must reject")
    _assert("no_feasible_target" in (rejected.rejection_codes or []), "no feasible target rejection code missing")

    partial = ai_gate._validate_ai_target_choice_against_feasibility(payload, _feasible_decision("partial_before_obstacle_then_liquidity"), 0)
    _assert(partial.allow, "partial-before-obstacle should pass when TP2 is feasible even if TP1 RR is small")
    _assert(partial.chosen_target_model == "partial_before_obstacle_then_liquidity", "partial target model should be preserved")


def test_mql_tester_replay_cache_export() -> None:
    _install_config(_test_config())
    with tempfile.TemporaryDirectory() as tmp:
        bus = Path(tmp)
        req_dir = bus / "requests"
        resp_dir = bus / "responses"
        stale_dir = bus / "stale"
        req_dir.mkdir(parents=True)
        payload = _payload(
            id="record_req_1",
            runtime={"tester": True, "account_trade_mode": "tester"},
            runtime_inputs={
                "tester_ai_mode": 0,
                "tester_ai_cache": True,
                "runtime_input_hash": "workflow_hash_changed",
            },
            po3={
                "t_sweep": 1780000000,
                "t_disp": 1780000060,
                "dr_high": 2360.0,
                "dr_low": 2340.0,
            },
            tester_cache_signature="mql_signature_same_in_cache_only",
            tester_cache_key="mqlcache123",
        )
        req_path = req_dir / "record_req_1.json"
        req_path.write_text(json.dumps(payload), encoding="utf-8")

        original_score_setup = ai_gate.score_setup

        def fake_score_setup(_payload_obj: Dict[str, Any]) -> ai_gate.Decision:
            return _full_structured_decision(_payload_obj, llm_quality_score=8.2)

        try:
            ai_gate.score_setup = fake_score_setup
            ai_gate.process_one(req_path, resp_dir, stale_dir)
        finally:
            ai_gate.score_setup = original_score_setup

        cache_path = bus / "logs" / "tester_ai_cache" / "mqlcache123.json"
        _assert(cache_path.exists(), "Python should write MQL tester replay cache file")
        cache_obj = ai_gate.read_json_any_encoding(cache_path)
        _assert(cache_obj.get("cache_signature") == "mql_signature_same_in_cache_only", "MQL cache signature should be preserved exactly")
        _assert(cache_obj.get("allow") is True, "MQL cache should preserve allow=true decisions")
        _assert(cache_obj.get("decision_source") == "llm_full_structured", "MQL cache should preserve decision source")


def _valid_replay_response(req_id: str = "record_req_1", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return _full_structured_response(payload or _payload(), req_id)


def test_existing_response_repairs_mql_tester_replay_cache() -> None:
    _install_config(_test_config())
    with tempfile.TemporaryDirectory() as tmp:
        bus = Path(tmp)
        req_dir = bus / "requests"
        resp_dir = bus / "responses"
        req_dir.mkdir(parents=True)
        resp_dir.mkdir(parents=True)
        payload = _payload(
            id="record_req_existing",
            runtime={"tester": True, "account_trade_mode": "tester"},
            runtime_inputs={"tester_ai_mode": 0, "tester_ai_cache": True},
            tester_cache_signature="mql_signature_existing_response",
            tester_cache_key="mqlcache_existing",
        )
        req_path = req_dir / "record_req_existing.json"
        resp_path = resp_dir / "record_req_existing.json"
        req_path.write_text(json.dumps(payload), encoding="utf-8")
        ai_gate.atomic_write_json(resp_path, _valid_replay_response("record_req_existing", payload), encoding=ai_gate.RESP_ENCODING)

        status = ai_gate._repair_mql_tester_replay_cache_from_existing_response(req_path, resp_path, bus)
        _assert(status == "written", "existing response should repair missing MQL tester cache")
        cache_path = bus / "logs" / "tester_ai_cache" / "mqlcache_existing.json"
        _assert(cache_path.exists(), "repaired MQL tester cache file missing")
        cache_obj = ai_gate.read_json_any_encoding(cache_path)
        _assert(cache_obj.get("cache_signature") == "mql_signature_existing_response", "repaired cache signature mismatch")


def test_tester_cache_export_rejects_unknown_contract() -> None:
    _install_config(_test_config())
    with tempfile.TemporaryDirectory() as tmp:
        bus = Path(tmp)
        payload = _payload(
            id="record_req_unknown_contract",
            runtime={"tester": True, "account_trade_mode": "tester"},
            runtime_inputs={"tester_ai_mode": 0, "tester_ai_cache": True},
            tester_cache_signature="mql_signature_unknown_contract",
            tester_cache_key="mqlcache_unknown_contract",
        )
        cache_path = bus / "logs" / "tester_ai_cache" / "mqlcache_unknown_contract.json"
        cache_path.parent.mkdir(parents=True)
        ai_gate.atomic_write_json(
            cache_path,
            {
                **_valid_replay_response("record_req_unknown_contract", payload),
                "cache_signature": "mql_signature_unknown_contract",
                "target_arbitration_schema_version": "unknown",
                "prompt_contract_version": "unknown",
            },
            encoding=ai_gate.RESP_ENCODING,
        )
        status = ai_gate._export_mql_tester_replay_cache(
            payload,
            {
                **_valid_replay_response("record_req_unknown_contract", payload),
                "target_arbitration_schema_version": "unknown",
                "prompt_contract_version": "unknown",
            },
            bus,
        )
        _assert(status == "skipped:legacy_target_arbitration_schema", "unknown target contract must be rejected, not silently migrated")
        cache_obj = ai_gate.read_json_any_encoding(cache_path)
        _assert(
            cache_obj.get("target_arbitration_schema_version") == "unknown",
            "strict exporter must not rewrite a legacy decision as current",
        )
        _assert(
            cache_obj.get("prompt_contract_version") == "unknown",
            "strict exporter must leave the quarantined legacy fixture untouched",
        )


def test_fill_tester_cache_once_processes_record_only_exports() -> None:
    _install_config(_test_config())
    with tempfile.TemporaryDirectory() as tmp:
        bus = Path(tmp)
        req_dir = bus / "requests"
        req_dir.mkdir(parents=True)
        payload = _payload(
            id="record_req_once",
            runtime={"tester": True, "account_trade_mode": "tester"},
            runtime_inputs={"tester_ai_mode": 0, "tester_ai_cache": True},
            po3={
                "t_sweep": 1780000000,
                "t_disp": 1780000060,
                "dr_high": 2360.0,
                "dr_low": 2340.0,
            },
            tester_cache_signature="mql_signature_fill_once",
            tester_cache_key="mqlcache_once",
        )
        req_path = req_dir / "record_req_once.json"
        req_path.write_text(json.dumps(payload), encoding="utf-8")

        original_score_setup = ai_gate.score_setup

        def fake_score_setup(_payload_obj: Dict[str, Any]) -> ai_gate.Decision:
            return _full_structured_decision(_payload_obj, llm_quality_score=8.4)

        try:
            ai_gate.score_setup = fake_score_setup
            summary = ai_gate.fill_tester_cache_once(bus)
        finally:
            ai_gate.score_setup = original_score_setup

        cache_path = bus / "logs" / "tester_ai_cache" / "mqlcache_once.json"
        _assert(cache_path.exists(), "fill-tester-cache-once should write MQL tester cache")
        _assert(summary["requests_seen"] == 1, "fill-once should see the exported request")
        _assert(summary["processed_new"] == 1, "fill-once should process the exported request")
        _assert(summary["cache_written"] == 1, "fill-once should count the written tester cache")
        _assert(not req_path.exists(), "fill-once should archive processed requests to stale")


def test_scoped_micro_bisi_sibi_suppression() -> None:
    _install_config(_test_config())
    unrelated = _payload(
        runtime_inputs={"suppress_micro_bisi_sibi_edge": True},
        candidate={"setup_family": "micro_po3", "setup_class": "micro_po3", "entry_branch": "fvg_edge"},
    )
    unrelated_reason = ai_gate._candidate_hard_block_reason(unrelated["candidates"][0], unrelated)
    _assert(unrelated_reason != "suppressed_micro_bisi_sibi_edge", "unrelated fvg_edge branch must not be globally suppressed")

    micro_edge = _payload(
        runtime_inputs={"suppress_micro_bisi_sibi_edge": True},
        candidate={"setup_family": "micro_bisi_sibi_edge", "setup_class": "micro_bisi_sibi_edge", "entry_branch": "fvg_edge"},
    )
    micro_reason = ai_gate._candidate_hard_block_reason(micro_edge["candidates"][0], micro_edge)
    _assert(micro_reason == "suppressed_micro_bisi_sibi_edge", "actual micro BISI/SIBI edge should still be suppressed")


def test_decision_cache_signature_ignores_tester_workflow_controls() -> None:
    _install_config(_test_config())
    base = _payload()
    sig, base_sig, _fields = ai_gate._decision_cache_signature(base, 0)
    workflow_changes = [
        {"tester_ai_cache": False},
        {"tester_ai_mode": 1},
        {"tester_ai_mode": 2},
        {"tester_allow_live_wait_debug_trading": True},
        {"runtime_input_hash": "workflow_hash_changed"},
    ]
    for override in workflow_changes:
        changed = _payload(
            runtime_input_hash=str(override.get("runtime_input_hash", "workflow_hash_changed")),
            runtime_inputs=override,
        )
        sig2, base2, _fields2 = ai_gate._decision_cache_signature(changed, 0)
        _assert(sig2 == sig and base2 == base_sig, f"workflow-only input should not change cache signature for {override}")

    material_changes = [
        {"fallback_rr2": 1.70},
        {"max_target_atr_mult": 4.5},
        {"max_target_adr_frac": 0.75},
    ]
    for override in material_changes:
        changed = _payload(runtime_inputs=override)
        sig2, base2, _fields2 = ai_gate._decision_cache_signature(changed, 0)
        _assert(sig2 != sig or base2 != base_sig, f"decision-relevant input should change cache signature for {override}")

    old_schema = ai_gate.AI_TARGET_ARBITRATION_SCHEMA_VERSION
    try:
        ai_gate.AI_TARGET_ARBITRATION_SCHEMA_VERSION = old_schema + "_changed"
        schema_sig, _schema_base, _schema_fields = ai_gate._decision_cache_signature(base, 0)
        _assert(schema_sig != sig, "target arbitration schema version should change cache signature")
    finally:
        ai_gate.AI_TARGET_ARBITRATION_SCHEMA_VERSION = old_schema


def test_mpc_trading_disabled_hard_gate() -> None:
    _install_config(_test_config())
    mpc = _payload(
        runtime_inputs={"enable_mpc_trading": False},
        candidate={
            "model_code": "MPC",
            "setup_code": "MPC",
            "setup_family": "micro_po3",
            "setup_class": "micro_po3",
            "broker_comment": "MPC-ASIA-NK-XAUUSD-12345",
            "is_mpc": True,
        },
    )
    reason = ai_gate._candidate_hard_block_reason(mpc["candidates"][0], mpc)
    _assert(reason == "mpc_trading_disabled", "disabled MPC trading should block before AI")
    _expect_reject(mpc, "mpc_trading_disabled")

    non_mpc = _payload(
        runtime_inputs={"enable_mpc_trading": False},
        candidate={"model_code": "FULL", "setup_code": "FULL", "broker_comment": "FULL-LON-K-XAUUSD-12345"},
    )
    non_mpc_reason = ai_gate._candidate_hard_block_reason(non_mpc["candidates"][0], non_mpc)
    _assert(non_mpc_reason != "mpc_trading_disabled", "disabled MPC trading must not block non-MPC setups")


def test_ai_veto_gate() -> None:
    _install_config(_test_config())
    payload = _payload()
    bad_follow_through = _full_structured_decision(payload, llm_quality_score=8.8)
    bad_follow_through.follow_through_probability = 0.40
    bad_follow_through.invalidation_risk = 0.30
    bad_follow_through.chop_risk = 0.30
    bad_follow_through.post_entry_failure_risk = 0.30
    bad_follow_through.final_trade_expectancy_score = 8.0
    rejected = ai_gate._apply_ai_veto_gate(payload, bad_follow_through)
    _assert(not rejected.allow, "low follow-through probability should veto a high-score trade")
    _assert("ai_veto" in (rejected.rejection_codes or []), "AI veto rejection code missing")

    passing = _full_structured_decision(payload, llm_quality_score=8.8)
    passing.follow_through_probability = 0.72
    passing.invalidation_risk = 0.30
    passing.chop_risk = 0.30
    passing.post_entry_failure_risk = 0.30
    passing.final_trade_expectancy_score = 7.4
    allowed = ai_gate._apply_ai_veto_gate(payload, passing)
    _assert(allowed.allow, "high-score trade with passing veto fields should remain allowed")

    explicit_veto = _full_structured_decision(payload, llm_quality_score=9.1)
    explicit_veto.veto_enabled = True
    explicit_veto.veto_reason = "recent_bucket_no_follow_through"
    explicit_veto.follow_through_probability = 0.80
    explicit_veto.invalidation_risk = 0.20
    explicit_veto.chop_risk = 0.20
    explicit_veto.post_entry_failure_risk = 0.20
    explicit_veto.final_trade_expectancy_score = 8.0
    vetoed = ai_gate._apply_ai_veto_gate(payload, explicit_veto)
    _assert(not vetoed.allow and "ai_veto" in (vetoed.rejection_codes or []), "explicit model veto should reject")


def test_live_bucket_priors_affect_prompt_and_signature() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        priors_path = Path(tmp) / "live_bucket_priors.json"
        cfg = _test_config({"AI_LIVE_BUCKET_PRIORS_FILE": str(priors_path)})
        _install_config(cfg)
        payload = _payload(
            po3={"session_name": "asia", "in_killzone": False},
            candidate={
                "setup_code": "MPC",
                "model_code": "MPC",
                "session_code": "ASIA",
                "killzone_code": "NK",
                "in_killzone": False,
            },
        )
        def clean_rows(results: list[float]) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for index, result_r in enumerate(results):
                rows.append({
                    "trade_key": f"prior-{index}",
                    "symbol": "XAUUSD",
                    "setup_code": "MPC",
                    "setup_family": "micro_po3",
                    "entry_branch": "fvg_mid",
                    "session": "ASIA",
                    "killzone": "NK",
                    "result_r_initial_risk": result_r,
                    "stuck_no_mfe_triggered": index < 3,
                    "dr_and_structural_invalid_triggered": index < 2,
                    "minutes_to_0_25r_mfe": 10 + index,
                    "closed_at": f"2026-06-{index + 1:02d}T12:00:00Z",
                    "ledger_integrity_status": "CLEAN",
                    "attribution_status": "exact_verified",
                    "ledger_schema_version": "test_ledger_v1",
                    "decision_schema_version": "test_decision_v1",
                    "learning_eligible": True,
                    "optimization_eligible": True,
                    "suppression_eligible": True,
                    "execution_identity_quarantined": False,
                })
            return rows

        priors_path.write_text(
            json.dumps(analyze_ai_trade_outcomes.build_priors(clean_rows([-1.0, -0.8, -0.5, -0.25]))),
            encoding="utf-8",
        )
        ai_gate._LIVE_BUCKET_PRIORS_CACHE = (0.0, {})
        compact = ai_gate._compact_model_payload(payload)
        prior = compact["plan"]["bucket_prior"]
        _assert(prior["setup_code"] == "MPC", "bucket prior should identify setup code")
        _assert(prior["session_bucket"] == "MPC-ASIA-NK", "bucket prior should identify session/killzone bucket")
        _assert(prior["available"] is True, "hierarchical prior should be available")
        _assert(
            set(prior["hierarchy"]) == {
                "global", "asset_class", "symbol", "family", "branch",
                "session", "killzone", "family_symbol", "family_session",
            },
            "all hierarchy levels must be transmitted separately",
        )
        family_session = prior["hierarchy"]["family_session"]["prior"]
        _assert(family_session["clean_sample_size"] == 4, "clean hierarchical prior sample count missing")
        _assert(family_session["stuck_no_mfe_rate"] == 0.75, "post-entry prior evidence missing")
        sig1, _base1, fields1 = ai_gate._decision_cache_signature(payload, 0)
        _assert(fields1.get("bucket_prior_hash"), "decision signature should include prior hash when priors are used")

        priors_path.write_text(
            json.dumps(analyze_ai_trade_outcomes.build_priors(clean_rows([0.2, 0.4, 0.8, 1.1]))),
            encoding="utf-8",
        )
        ai_gate._LIVE_BUCKET_PRIORS_CACHE = (0.0, {})
        sig2, _base2, _fields2 = ai_gate._decision_cache_signature(payload, 0)
        _assert(sig2 != sig1, "changing decision-used bucket priors should change AI decision cache signature")


def test_outcome_analyzer_generates_bucket_priors() -> None:
    rows = [
        {
            "trade_key": "t1",
            "symbol": "XAUUSD",
            "setup_code": "MPC",
            "setup_family": "micro_po3",
            "session": "ASIA",
            "killzone": "NK",
            "target_source": "synthetic_rr_fallback",
            "ai_score": 8.3,
            "ai_confidence": 0.72,
            "full_close_pnl": -1000.0,
            "full_close_pct": -0.25,
            "stuck_no_mfe_triggered": True,
            "dr_and_structural_invalid_triggered": True,
            "minutes_to_0_25r_mfe": 0,
        },
        {
            "trade_key": "t2",
            "symbol": "XAUUSD",
            "setup_code": "MPC",
            "setup_family": "micro_po3",
            "session": "ASIA",
            "killzone": "NK",
            "target_source": "liquidity_target",
            "ai_score": 8.0,
            "ai_confidence": 0.68,
            "full_close_pnl": 250.0,
            "full_close_pct": 0.12,
            "minutes_to_0_25r_mfe": 8,
        },
        {
            "trade_key": "t3",
            "symbol": "EURUSD",
            "setup_code": "FULL",
            "setup_family": "full_po3",
            "session": "LON",
            "killzone": "K",
            "target_source": "liquidity_target",
            "ai_score": 7.8,
            "ai_confidence": 0.64,
            "full_close_pnl": 0.0,
            "full_close_pct": 0.0,
        },
    ]
    for index, row in enumerate(rows):
        row.update({
            "result_r_initial_risk": (-1.0, 0.5, 0.0)[index],
            "closed_at": f"2026-06-{index + 1:02d}T12:00:00Z",
            "ledger_integrity_status": "CLEAN",
            "attribution_status": "exact_verified",
            "ledger_schema_version": "test_ledger_v1",
            "decision_schema_version": "test_decision_v1",
            "learning_eligible": True,
            "optimization_eligible": True,
            "suppression_eligible": True,
            "execution_identity_quarantined": False,
        })
    grouped = analyze_ai_trade_outcomes._group(rows, lambda r: str(r.get("setup_code") or "UNK").upper())
    _assert(grouped["MPC"]["winners_gt_0_1pct"] == 1, "winner bucket count mismatch")
    _assert(grouped["MPC"]["losers_lt_minus_0_1pct"] == 1, "loser bucket count mismatch")
    priors = analyze_ai_trade_outcomes.build_priors(rows)
    _assert(priors["ledger_integrity_status"] == "clean_only", "priors must be clean-ledger-only")
    _assert("micro_po3|ASIA" in priors["levels"]["family_session"], "family/session prior missing")
    _assert("XAUUSD" in priors["levels"]["symbol"], "symbol prior missing")
    _assert(
        priors["levels"]["family_session"]["micro_po3|ASIA"]["stuck_no_mfe_rate"] > 0.0,
        "post-entry failure rate should be summarized",
    )

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "completed_ai_trades.jsonl"
        out = Path(tmp) / "live_bucket_priors.json"
        source.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "analyze_ai_trade_outcomes.py"), "--file", str(source), "--priors-out", str(out)],
            cwd=str(ROOT),
            timeout=60,
            capture_output=True,
            text=True,
        )
        _assert(proc.returncode == 0, f"outcome analyzer failed: {proc.stderr}")
        _assert(out.exists(), "outcome analyzer should write live_bucket_priors.json")
        written = json.loads(out.read_text(encoding="utf-8"))
        _assert(
            "micro_po3|ASIA" in written["levels"]["family_session"],
            "written hierarchical priors should include the family/session bucket",
        )


def _threshold_decision(payload: Dict[str, Any], score: float) -> ai_gate.Decision:
    decision = _full_structured_decision(payload, llm_quality_score=score)
    return ai_gate._apply_family_ai_threshold_gate(payload, decision, 0)


def _expect_threshold_case(
    name: str,
    family: str,
    score: float,
    should_allow: bool,
    source: str,
    *,
    runtime_overrides: Dict[str, Any] | None = None,
    branch: str = "fvg_mid",
    setup_class: str = "",
) -> None:
    payload = _payload(
        runtime_inputs=runtime_overrides or {},
        candidate={"setup_family": family, "setup_class": setup_class or family, "entry_branch": branch},
    )
    decision = _threshold_decision(payload, score)
    _assert(decision.allow is should_allow, f"{name}: allow mismatch")
    _assert(decision.llm_quality_threshold_source == source, f"{name}: source {decision.llm_quality_threshold_source} != {source}")
    if not should_allow:
        _assert("llm_quality_score_below_family_threshold" in (decision.rejection_codes or []), f"{name}: missing rejection code")


def test_family_ai_thresholds() -> None:
    _install_config(_test_config())
    _expect_threshold_case("full po3", "full_po3", 6.7, False, "llm_quality_score_full_po3")
    _expect_threshold_case("micro po3", "micro_po3_reversal", 7.1, False, "llm_quality_score_micro_po3")
    _expect_threshold_case("continuation", "micro_continuation_fvg", 7.25, False, "llm_quality_score_continuation", branch="continuation_reentry")
    _expect_threshold_case("range", "micro_range_reentry", 7.05, True, "llm_quality_score_range", branch="range_reentry")
    _expect_threshold_case("failed breakout", "micro_failed_breakout_reclaim", 7.1, False, "llm_quality_score_failed_breakout", setup_class="failed_breakout_reclaim")
    _expect_threshold_case("unknown", "unknown_new_family", 6.9, False, "min_llm_quality_score_trend")
    _expect_threshold_case(
        "global floor disabled",
        "full_po3",
        6.9,
        True,
        "llm_quality_score_full_po3",
        runtime_overrides={"min_llm_quality_score_trend": 7.5, "llm_quality_score_full_po3": 6.8, "global_llm_quality_as_hard_floor": False},
    )
    _expect_threshold_case(
        "global floor enabled",
        "full_po3",
        6.9,
        False,
        "llm_quality_score_full_po3+global_floor",
        runtime_overrides={"min_llm_quality_score_trend": 7.5, "llm_quality_score_full_po3": 6.8, "global_llm_quality_as_hard_floor": True},
    )


def test_ledger_fixture() -> None:
    fixture_logs = ROOT / "tests" / "fixtures" / "ledger_wrong_symbol_price_scale" / "trade_results"
    with tempfile.TemporaryDirectory() as tmp:
        report = repair_trade_ledger.run_repair(fixture_logs, Path(tmp))
        _assert(report["rejected_records"] == 1, "bad fixture should be rejected")
        reasons = set(report["reason_counts"])
        _assert("symbol_mismatch" in reasons, "wrong-symbol exit should be rejected")
        _assert("price_scale_mismatch" in reasons, "wrong price scale should be rejected")
        _assert((Path(tmp) / "repaired_system_trade_history.json").exists(), "clean history output missing")
        _assert((Path(tmp) / "rejected_deals.json").exists(), "rejected deals output missing")
        _assert((Path(tmp) / "ledger_integrity_report.md").exists(), "md report output missing")
        _assert((Path(tmp) / "ledger_integrity_report.json").exists(), "json report output missing")


def test_mql_repeatability_risk_management_wiring() -> None:
    include_root = MQL_ROOT / "Include" / "MT5_PO3_Codex"
    config = (include_root / "Config.mqh").read_text(encoding="utf-8", errors="ignore")
    bridge = (include_root / "AIGateBridge.mqh").read_text(encoding="utf-8", errors="ignore")
    risk = (include_root / "Risk.mqh").read_text(encoding="utf-8", errors="ignore")
    engine = (include_root / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")
    penalty = (include_root / "PenaltyWatcher.mqh").read_text(encoding="utf-8", errors="ignore")
    state = (include_root / "StateStore.mqh").read_text(encoding="utf-8", errors="ignore")
    fvg = (include_root / "FVG.mqh").read_text(encoding="utf-8", errors="ignore")

    for needle in (
        "REPEATABILITY_SCHEMA_VERSION",
        "HIERARCHICAL_PRIOR_SCHEMA_VERSION",
        "RISK_FACTOR_SCHEMA_VERSION",
        "MANAGEMENT_SCHEMA_VERSION",
        "SHADOW_CANDIDATE_SCHEMA_VERSION",
        "NORMALIZED_FVG_SCHEMA_VERSION",
        "InpMaxTotalRiskPct",
        "InpMaxTotalRiskMoney",
        "InpNormalizedFvgMode = NORMALIZED_FVG_SHADOW",
    ):
        _assert(needle in config, f"MQL governance config wiring missing {needle}")

    for needle in (
        "request_fingerprint",
        "response_fingerprint",
        "hierarchical_prior_artifact_hash",
        "repeatability_trading_eligible",
    ):
        _assert(needle in bridge, f"MQL AI fingerprint/prior wiring missing {needle}")

    for needle in (
        "NormalizeOpeningVolume",
        "NormalizeClosingVolume",
        "CurrentInitialRiskBreakdown",
        "PortfolioInitialRiskGate",
        "BrokerCostEstimatePerLot",
        "RiskFactorGate",
        "QueryBrokerSymbolSessionSchedule",
    ):
        _assert(needle in risk, f"MQL risk/session implementation missing {needle}")

    for needle in (
        "_ApplyFinalPortfolioRiskGovernance",
        "_WriteShadowCandidateRecord",
        "_WriteRejectedShadowCandidate",
        "_MaintainBrokerSymbolSessionProtection",
        "_ComputeOriginalPolicyCounterfactual",
        "_MaintainCounterfactualEvaluations",
        "management_counterfactual_updates.jsonl",
        "_MaintainShadowCandidateOutcomes",
        "hypothetical_outcome_resolution",
    ):
        _assert(needle in engine, f"MQL execution governance wiring missing {needle}")
    _assert("[portfolio_initial_risk]" in risk, "aggregate initial-risk audit log is not wired")
    _assert("[risk_factor_gate]" in risk, "fixed risk-factor audit log is not wired")
    _assert(
        engine.count("_ApplyFinalPortfolioRiskGovernance(") >= 4,
        "aggregate/factor risk gate must be wired before market, pending, and rebuild paths",
    )
    _assert("pre_ai_accept" in engine and "pre_ai_reject" in engine, "accepted and rejected shadow labels must both be emitted")

    for needle in (
        '"HEALTHY"',
        '"WARNING"',
        '"THESIS_INVALID"',
        '"EXITED"',
        "_ActionAlreadyExecuted",
        "_TransitionAllowed",
        "[management_transition_blocked]",
        "_ConfirmThesisInvalidation",
        "_ResolveInvalidationPolicy",
        "asset_class_policy_missing_or_incompatible",
        "NormalizeClosingVolume",
    ):
        _assert(needle in penalty, f"MQL management-state wiring missing {needle}")
    _assert("executed_action_ids" in state and "management_version" in state, "restart idempotency state is not persisted")
    for needle in (
        "normalized_min_ticks_price",
        "normalized_min_spread_price",
        "normalized_min_atr_price",
        "normalized_min_session_noise_price",
        "NORMALIZED_FVG_ENFORCE",
        "insufficient_asset_class_policy_evidence",
        "normalized_fvg_asset_class_evidence_sufficient",
    ):
        _assert(needle in fvg, f"normalized FVG implementation missing {needle}")


def _find_metaeditor() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\MetaTrader 5\MetaEditor64.exe"),
        Path(r"C:\Program Files (x86)\MetaTrader 5\MetaEditor64.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which("MetaEditor64.exe") or shutil.which("metaeditor64.exe")
    return Path(found) if found else None


def test_mql_compile(skip: bool) -> None:
    if skip:
        print("[verify] MQL compile skipped by flag")
        return
    metaeditor = _find_metaeditor()
    _assert(metaeditor is not None, "MetaEditor64.exe not found; use --skip-mql-compile if needed")
    _assert(EA_FILE.exists(), f"EA file missing: {EA_FILE}")
    log_path = ROOT / "mql_compile_v_next_verify.log"
    # Compile an exact mirror of active source so a running terminal cannot lock
    # the production EX5 and turn a valid compile into MetaEditor error 119.
    with tempfile.TemporaryDirectory(prefix="po3_active_mql_compile_") as tmp:
        mirror_mql = Path(tmp) / "MQL5"
        mirror_ea_dir = mirror_mql / "Experts" / "MT5_PO3_Codex"
        mirror_include_dir = mirror_mql / "Include" / "MT5_PO3_Codex"
        mirror_ea_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(EA_FILE, mirror_ea_dir / EA_FILE.name)
        shutil.copytree(MQL_ROOT / "Include" / "MT5_PO3_Codex", mirror_include_dir)
        mirror_ea = mirror_ea_dir / EA_FILE.name
        cmd = [str(metaeditor), f"/compile:{mirror_ea}", f"/log:{log_path}"]
        proc = subprocess.run(cmd, cwd=str(ROOT), timeout=360, capture_output=True, text=True)
    log_text = log_path.read_text(encoding="utf-16", errors="ignore") if log_path.exists() else (proc.stdout + proc.stderr)
    zero_errors = "0 error(s)" in log_text.lower() or "0 errors" in log_text.lower()
    _assert(zero_errors, f"MQL compile errors detected; MetaEditor returned {proc.returncode}\n{log_text[-2000:]}")


def main() -> None:
    global MQL_ROOT, EA_FILE, TRADE_ENGINE_FILE
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-mql-compile", action="store_true", help="Skip MetaEditor compile check.")
    parser.add_argument(
        "--mql-root",
        default=str(MQL_ROOT),
        help="MQL5 root to verify; defaults to the active terminal tree.",
    )
    args = parser.parse_args()
    MQL_ROOT = Path(args.mql_root).resolve()
    EA_FILE = MQL_ROOT / "Experts" / "MT5_PO3_Codex" / "PO3_AIGate_ScannerEA.mq5"
    TRADE_ENGINE_FILE = MQL_ROOT / "Include" / "MT5_PO3_Codex" / "TradeEngine.mqh"

    tests = [
        ("env parsing", test_env_parsing),
        ("live Batch/Flex safety", test_live_batch_flex_safety),
        ("Flex unavailable retries", test_flex_unavailable_retries),
        ("hard pre-gate", test_hard_pre_gate),
        ("decision cache", test_decision_cache),
        ("decision cache schema versioning", test_decision_cache_schema_versioning),
        ("MQL target safety source", test_mql_target_safety_source),
        ("live mode simulated-age guard source", test_live_mode_sim_age_guard_source),
        ("MQL tester cache strict contract source", test_mql_tester_cache_contract_strict_reject_source),
        ("entry-time comment signature source", test_entry_time_comment_signature_source),
        ("target arbitration metadata", test_target_arbitration_metadata),
        ("target feasibility choice validation", test_target_feasibility_choice_validation),
        ("MQL tester replay cache export", test_mql_tester_replay_cache_export),
        ("existing response repairs MQL tester replay cache", test_existing_response_repairs_mql_tester_replay_cache),
        ("tester cache export rejects unknown contract", test_tester_cache_export_rejects_unknown_contract),
        ("fill tester cache once", test_fill_tester_cache_once_processes_record_only_exports),
        ("scoped micro BISI/SIBI suppression", test_scoped_micro_bisi_sibi_suppression),
        ("decision cache signature workflow stability", test_decision_cache_signature_ignores_tester_workflow_controls),
        ("MPC trading disabled hard gate", test_mpc_trading_disabled_hard_gate),
        ("AI veto gate", test_ai_veto_gate),
        ("live bucket priors prompt/signature", test_live_bucket_priors_affect_prompt_and_signature),
        ("outcome analyzer bucket priors", test_outcome_analyzer_generates_bucket_priors),
        ("family AI thresholds", test_family_ai_thresholds),
        ("ledger fixture", test_ledger_fixture),
        ("MQL repeatability/risk/management wiring", test_mql_repeatability_risk_management_wiring),
    ]
    for name, fn in tests:
        fn()
        print(f"[verify] PASS {name}")
    test_mql_compile(args.skip_mql_compile)
    print("[verify] PASS mql compile" if not args.skip_mql_compile else "[verify] PASS mql compile skipped")
    print("[verify] all v-next controls passed")


if __name__ == "__main__":
    main()
