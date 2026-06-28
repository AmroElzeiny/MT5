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
        "execution_reject_cost_r": 0.10,
        "execution_reduce_risk_cost_r": 0.07,
        "micro_scalp_max_cost_frac_of_planned_r": 0.10,
        "require_displacement": True,
        "allow_synthetic_rr_target": True,
        "min_live_rr2": 0.85,
        "standard_trade_liquidity_rr_floor": 1.10,
        "max_target_atr_mult": 5.0,
        "max_target_adr_frac": 0.80,
        "obstacle_reject_r": 0.70,
        "use_ai": True,
        "ai_strict": True,
        "min_ai_score_trend": 7.0,
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
    })
    _assert(cfg.use_batch_api is False, "invalid batch bool should fail safe false")
    _assert(cfg.use_flex is False, "invalid flex bool should fail safe false")
    _assert(cfg.service_tier == "auto", "invalid service tier should become auto")
    _assert(cfg.max_output_tokens >= 1024, "invalid token cap should be clamped")


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
        ("hard pre-gate", test_hard_pre_gate),
        ("decision cache", test_decision_cache),
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
