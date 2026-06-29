#!/usr/bin/env python3
"""Deterministic verification for v-next safety/cost controls."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_gate
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
        "min_ai_score_trend": 7.0,
        "ai_score_full_po3": 6.8,
        "ai_score_micro_po3": 7.2,
        "ai_score_continuation": 7.3,
        "ai_score_range": 7.0,
        "ai_score_failed_breakout": 7.2,
        "global_ai_score_as_hard_floor": False,
        "min_ai_confidence": 0.45,
        "use_snapshot_ai": False,
        "require_snapshots": False,
        "exclusive_trading_enabled": False,
        "virtual_ledger_mode": "fixed_virtual_balance",
        "backend_pnl_mode": "closed_deal_pnl",
    })
    values.update(overrides)
    return values


def _payload(**overrides: Any) -> Dict[str, Any]:
    candidate = {
        "candidate_index": 0,
        "setup_family": "micro_po3",
        "setup_class": "micro_po3",
        "entry_branch": "fvg_mid",
        "entry_est": 2350.0,
        "sl": 2345.0,
        "tp2": 2356.0,
        "rr2": 1.2,
        "fvg_lower": 2349.0,
        "fvg_upper": 2351.0,
        "execution_cost_r": 0.02,
        "target_source": "liquidity",
        "obstacle_kind": "none",
        "target_arbitration_required": False,
        "in_killzone": True,
        "fvg_mitigation_state": "fresh",
    }
    payload = {
        "id": "verify_1",
        "symbol": "XAUUSD",
        "is_buy": True,
        "runtime": {"tester": False, "account_trade_mode": "real"},
        "runtime_input_hash": "test_hash",
        "runtime_inputs": _runtime_inputs(),
        "po3": {"has_displacement": True, "in_killzone": True, "session_name": "london"},
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
        decision = ai_gate.Decision(True, 7.2, confidence=0.8, decision_source="llm_blended", model_version="test_model")
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
        _assert(miss3 is None and status3 == "invalidated_material_field_changed", "changed target candidates should invalidate")


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

        comparison = {
            "liquidity_target": {"usable": False, "reason": "blocked", "risk": "moderate", "expected_role": "reject"},
            "partial_before_obstacle_then_liquidity": {"usable": False, "reason": "tp1 unavailable", "risk": "medium", "expected_role": "reject"},
            "capped_before_obstacle": {"usable": False, "reason": "rr too low", "risk": "low reward", "expected_role": "reject"},
            "synthetic_rr_fallback": {"usable": True, "reason": "clean fallback rr", "risk": "controlled", "expected_role": "fallback_only"},
        }
        current = ai_gate.Decision(
            True,
            8.0,
            chosen_index=0,
            confidence=0.82,
            decision_source="llm_blended",
            chosen_target_model="synthetic_rr_fallback",
            target_decision_reason="all real target alternatives were weaker",
            why_not_liquidity_target="liquidity target blocked by nontrivial imbalance",
            why_not_partial_before_obstacle="partial path unavailable",
            why_not_capped_before_obstacle="cap rr too low",
            why_not_synthetic_fallback="fallback selected",
            target_comparison_json=json.dumps(comparison, separators=(",", ":")),
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
    ]
    for needle in required:
        _assert(needle in src or needle in bridge_src or needle in config_src, f"MQL target safety source missing {needle}")
    bridge_required = [
        "blocker_features",
        "effective_fallback_rr",
        "fallback_rr_buffer_r",
        "target_blocker_severity_present",
        "JsonGetBool(txt, \"target_blocker_severity_present\"",
        "target_arbitration_schema_version",
        "prompt_contract_version",
        "target_comparison",
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
    ]
    for needle in ea_required:
        _assert(needle in ea_src, f"EA tester wait source missing {needle}")


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


def _threshold_decision(payload: Dict[str, Any], score: float) -> ai_gate.Decision:
    decision = ai_gate.Decision(True, score, chosen_index=0, confidence=0.8, decision_source="llm_blended")
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
    _assert(decision.ai_threshold_source == source, f"{name}: source {decision.ai_threshold_source} != {source}")
    if not should_allow:
        _assert("ai_score_below_family_threshold" in (decision.rejection_codes or []), f"{name}: missing rejection code")


def test_family_ai_thresholds() -> None:
    _install_config(_test_config())
    _expect_threshold_case("full po3", "full_po3", 6.7, False, "ai_score_full_po3")
    _expect_threshold_case("micro po3", "micro_po3_reversal", 7.1, False, "ai_score_micro_po3")
    _expect_threshold_case("continuation", "micro_continuation_fvg", 7.25, False, "ai_score_continuation", branch="continuation_reentry")
    _expect_threshold_case("range", "micro_range_reentry", 7.05, True, "ai_score_range", branch="range_reentry")
    _expect_threshold_case("failed breakout", "micro_failed_breakout_reclaim", 7.1, False, "ai_score_failed_breakout", setup_class="failed_breakout_reclaim")
    _expect_threshold_case("unknown", "unknown_new_family", 6.9, False, "min_ai_score_trend")
    _expect_threshold_case(
        "global floor disabled",
        "full_po3",
        6.9,
        True,
        "ai_score_full_po3",
        runtime_overrides={"min_ai_score_trend": 7.5, "ai_score_full_po3": 6.8, "global_ai_score_as_hard_floor": False},
    )
    _expect_threshold_case(
        "global floor enabled",
        "full_po3",
        6.9,
        False,
        "ai_score_full_po3+global_floor",
        runtime_overrides={"min_ai_score_trend": 7.5, "ai_score_full_po3": 6.8, "global_ai_score_as_hard_floor": True},
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
    cmd = [str(metaeditor), f"/compile:{EA_FILE}", f"/log:{log_path}"]
    proc = subprocess.run(cmd, cwd=str(ROOT), timeout=120, capture_output=True, text=True)
    log_text = log_path.read_text(encoding="utf-16", errors="ignore") if log_path.exists() else (proc.stdout + proc.stderr)
    zero_errors = "0 error(s)" in log_text.lower() or "0 errors" in log_text.lower()
    _assert(zero_errors, f"MQL compile errors detected; MetaEditor returned {proc.returncode}\n{log_text[-2000:]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-mql-compile", action="store_true", help="Skip MetaEditor compile check.")
    args = parser.parse_args()

    tests = [
        ("env parsing", test_env_parsing),
        ("live Batch/Flex safety", test_live_batch_flex_safety),
        ("Flex unavailable retries", test_flex_unavailable_retries),
        ("hard pre-gate", test_hard_pre_gate),
        ("decision cache", test_decision_cache),
        ("decision cache schema versioning", test_decision_cache_schema_versioning),
        ("MQL target safety source", test_mql_target_safety_source),
        ("target arbitration metadata", test_target_arbitration_metadata),
        ("family AI thresholds", test_family_ai_thresholds),
        ("ledger fixture", test_ledger_fixture),
    ]
    for name, fn in tests:
        fn()
        print(f"[verify] PASS {name}")
    test_mql_compile(args.skip_mql_compile)
    print("[verify] PASS mql compile" if not args.skip_mql_compile else "[verify] PASS mql compile skipped")
    print("[verify] all v-next controls passed")


if __name__ == "__main__":
    main()
