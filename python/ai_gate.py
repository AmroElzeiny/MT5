#!/usr/bin/env python3
"""
ai_gate.py
----------
A PO3-aware AI gate for MT5 <-> Python communication via the MT5 Common folder.

- EA writes:   Common/Files/<BUS_ROOT>/requests/<id>.json
- Python reads, scores, and writes: Common/Files/<BUS_ROOT>/responses/<id>.json
- After processing, request/response can be moved to stale/ (bin).

The scorer combines rule-based structure checks with an LLM advisory layer so the
MT5 side can send a richer PO3 narrative: dealing range, sweep/displacement/BOS timing,
session/killzone context, liquidity target, and planned execution levels. MT5 remains
the final execution authority; Python only reranks, explains, or suggests a veto.
"""

from __future__ import annotations
import argparse
import base64
import inspect
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Tuple

from openai_usage_logger import log_openai_usage, set_openai_usage_bus
from po3_env import load_dotenv

# The bot should follow this project's .env even when PowerShell has an older
# OPENAI_API_KEY cached in the parent environment.
load_dotenv(override=True)

try:
    from expectancy_report import run_analytics_suite
    ANALYTICS_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - defensive import fallback
    run_analytics_suite = None
    ANALYTICS_IMPORT_ERROR = str(exc)

DEFAULT_BUS_ROOT = "PO3_AI_BUS"

def _env_lookup(env: Mapping[str, str], names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _env_bool(
    env: Mapping[str, str],
    name: str,
    default: bool,
    warnings: list[str],
    *,
    safe_default: bool | None = None,
) -> bool:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    warnings.append(f"{name}=invalid_bool")
    return default if safe_default is None else safe_default


def _env_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    warnings: list[str],
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except Exception:
            warnings.append(f"{name}=invalid_int")
            value = default
    if min_value is not None and value < min_value:
        warnings.append(f"{name}=below_min")
        value = min_value
    if max_value is not None and value > max_value:
        warnings.append(f"{name}=above_max")
        value = max_value
    return value


def _env_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    warnings: list[str],
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except Exception:
            warnings.append(f"{name}=invalid_float")
            value = default
    if min_value is not None and value < min_value:
        warnings.append(f"{name}=below_min")
        value = min_value
    if max_value is not None and value > max_value:
        warnings.append(f"{name}=above_max")
        value = max_value
    return value


@dataclass(frozen=True)
class AIGateRuntimeConfig:
    model: str
    fallback_models: list[str]
    reasoning_effort: str
    max_output_tokens: int
    min_confidence: float
    prompt_cache_enable: bool
    prompt_cache_key: str
    prompt_cache_retention: str
    decision_cache_enable: bool
    decision_cache_ttl_sec: int
    decision_cache_file: Path
    use_batch_api: bool
    batch_only_for_backtest: bool
    batch_output_dir: Path
    batch_max_pending: int
    use_flex: bool
    allow_flex_for_live: bool
    flex_live_ack: bool
    service_tier: str
    openai_timeout_sec: float
    openai_flex_timeout_sec: float
    flex_unavailable_retry_enable: bool
    flex_unavailable_max_retries: int
    flex_unavailable_cooldown_sec: float
    require_runtime_inputs_live: bool
    reject_on_missing_runtime_inputs_live: bool
    hard_pre_gate_before_openai: bool
    log_skipped_calls: bool
    enable_snapshots: bool
    cost_report_enable: bool
    cost_report_file: Path
    validation_warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AIGateRuntimeConfig":
        env = env or os.environ
        warnings: list[str] = []
        model = _env_lookup(env, ("AI_GATE_MODEL", "OPENAI_MODEL"), "gpt-5.5-mini") or "gpt-5.5-mini"
        fallback_raw = _env_lookup(env, ("AI_GATE_FALLBACK_MODELS", "OPENAI_FALLBACK_MODELS"), "gpt-5.4-mini,gpt-5.4-nano")
        fallback_models: list[str] = []
        for item in fallback_raw.split(","):
            name = item.strip()
            if name and name != model and name not in fallback_models:
                fallback_models.append(name)

        effort = _env_lookup(env, ("AI_GATE_REASONING_EFFORT", "AI_REASONING_EFFORT"), "low").lower()
        if effort not in {"auto", "none", "minimal", "low", "medium", "high", "xhigh"}:
            warnings.append("AI_REASONING_EFFORT=invalid")
            effort = "low"

        service_tier = _env_lookup(env, ("AI_SERVICE_TIER",), "auto").lower()
        if service_tier not in {"auto", "default", "flex"}:
            warnings.append("AI_SERVICE_TIER=invalid")
            service_tier = "auto"

        prompt_key = _env_lookup(env, ("AI_PROMPT_CACHE_KEY",), "po3_live_gate_v2") or "po3_live_gate_v2"
        prompt_retention = _env_lookup(env, ("AI_PROMPT_CACHE_RETENTION",), "24h") or "24h"
        if prompt_retention not in {"ephemeral", "24h"}:
            warnings.append("AI_PROMPT_CACHE_RETENTION=invalid")
            prompt_retention = "24h"

        return cls(
            model=model,
            fallback_models=fallback_models,
            reasoning_effort=effort,
            max_output_tokens=_env_int(env, "AI_MAX_OUTPUT_TOKENS", 25000, warnings, min_value=1024, max_value=128000),
            min_confidence=_env_float(env, "AI_MIN_CONFIDENCE", 0.45, warnings, min_value=0.0, max_value=1.0),
            prompt_cache_enable=_env_bool(env, "AI_PROMPT_CACHE_ENABLE", True, warnings, safe_default=False),
            prompt_cache_key=prompt_key,
            prompt_cache_retention=prompt_retention,
            decision_cache_enable=_env_bool(env, "AI_DECISION_CACHE_ENABLE", True, warnings, safe_default=False),
            decision_cache_ttl_sec=_env_int(env, "AI_DECISION_CACHE_TTL_SEC", 1800, warnings, min_value=30, max_value=86400),
            decision_cache_file=Path(_env_lookup(env, ("AI_DECISION_CACHE_FILE",), "data/ai_decision_cache.jsonl")),
            use_batch_api=_env_bool(env, "AI_USE_BATCH_API", False, warnings, safe_default=False),
            batch_only_for_backtest=_env_bool(env, "AI_BATCH_ONLY_FOR_BACKTEST", True, warnings, safe_default=True),
            batch_output_dir=Path(_env_lookup(env, ("AI_BATCH_OUTPUT_DIR",), "data/ai_batch")),
            batch_max_pending=_env_int(env, "AI_BATCH_MAX_PENDING", 1000, warnings, min_value=1, max_value=100000),
            use_flex=_env_bool(env, "AI_USE_FLEX", False, warnings, safe_default=False),
            allow_flex_for_live=_env_bool(env, "AI_ALLOW_FLEX_FOR_LIVE", False, warnings, safe_default=False),
            flex_live_ack=_env_bool(env, "AI_FLEX_LIVE_ACK", False, warnings, safe_default=False),
            service_tier=service_tier,
            openai_timeout_sec=_env_float(env, "AI_OPENAI_TIMEOUT_SEC", 180.0, warnings, min_value=10.0, max_value=1800.0),
            openai_flex_timeout_sec=_env_float(env, "AI_OPENAI_FLEX_TIMEOUT_SEC", 600.0, warnings, min_value=30.0, max_value=1800.0),
            flex_unavailable_retry_enable=_env_bool(env, "AI_FLEX_UNAVAILABLE_RETRY_ENABLE", True, warnings, safe_default=True),
            flex_unavailable_max_retries=_env_int(env, "AI_FLEX_UNAVAILABLE_MAX_RETRIES", 20, warnings, min_value=0, max_value=100),
            flex_unavailable_cooldown_sec=_env_float(env, "AI_FLEX_UNAVAILABLE_COOLDOWN_SEC", 30.0, warnings, min_value=0.0, max_value=3600.0),
            require_runtime_inputs_live=_env_bool(env, "AI_REQUIRE_RUNTIME_INPUTS_LIVE", True, warnings, safe_default=True),
            reject_on_missing_runtime_inputs_live=_env_bool(env, "AI_REJECT_ON_MISSING_RUNTIME_INPUTS_LIVE", True, warnings, safe_default=True),
            hard_pre_gate_before_openai=_env_bool(env, "AI_HARD_PRE_GATE_BEFORE_OPENAI", True, warnings, safe_default=True),
            log_skipped_calls=_env_bool(env, "AI_LOG_SKIPPED_CALLS", True, warnings, safe_default=True),
            enable_snapshots=_env_bool(env, "AI_ENABLE_SNAPSHOTS", False, warnings, safe_default=False),
            cost_report_enable=_env_bool(env, "AI_COST_REPORT_ENABLE", True, warnings, safe_default=True),
            cost_report_file=Path(_env_lookup(env, ("AI_COST_REPORT_FILE",), "logs/ai_cost_report.jsonl")),
            validation_warnings=tuple(warnings),
        )

    def safe_log_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "fallback_models": self.fallback_models,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
            "min_confidence": self.min_confidence,
            "prompt_cache_enable": self.prompt_cache_enable,
            "prompt_cache_key": self.prompt_cache_key if self.prompt_cache_enable else "",
            "prompt_cache_retention": self.prompt_cache_retention if self.prompt_cache_enable else "",
            "decision_cache_enable": self.decision_cache_enable,
            "decision_cache_ttl_sec": self.decision_cache_ttl_sec,
            "decision_cache_file": str(self.decision_cache_file),
            "use_batch_api": self.use_batch_api,
            "batch_only_for_backtest": self.batch_only_for_backtest,
            "batch_output_dir": str(self.batch_output_dir),
            "batch_max_pending": self.batch_max_pending,
            "use_flex": self.use_flex,
            "allow_flex_for_live": self.allow_flex_for_live,
            "flex_live_ack": self.flex_live_ack,
            "service_tier": self.service_tier,
            "openai_timeout_sec": self.openai_timeout_sec,
            "openai_flex_timeout_sec": self.openai_flex_timeout_sec,
            "flex_unavailable_retry_enable": self.flex_unavailable_retry_enable,
            "flex_unavailable_max_retries": self.flex_unavailable_max_retries,
            "flex_unavailable_cooldown_sec": self.flex_unavailable_cooldown_sec,
            "require_runtime_inputs_live": self.require_runtime_inputs_live,
            "reject_on_missing_runtime_inputs_live": self.reject_on_missing_runtime_inputs_live,
            "hard_pre_gate_before_openai": self.hard_pre_gate_before_openai,
            "log_skipped_calls": self.log_skipped_calls,
            "enable_snapshots": self.enable_snapshots,
            "cost_report_enable": self.cost_report_enable,
            "cost_report_file": str(self.cost_report_file),
            "validation_warnings": list(self.validation_warnings),
        }


# ---------- OpenAI (real AI) ----------
# Set OPENAI_API_KEY in the environment before running this bridge.
AI_CONFIG = AIGateRuntimeConfig.from_env()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
OPENAI_MODEL = AI_CONFIG.model
OPENAI_FALLBACK_MODELS = AI_CONFIG.fallback_models
AI_REASONING_EFFORT = AI_CONFIG.reasoning_effort
SNAPSHOT_MAX_BYTES = max(64_000, int(os.getenv("AI_SNAPSHOT_MAX_BYTES", "350000")))
AI_ENABLE_SNAPSHOTS = AI_CONFIG.enable_snapshots
AI_MIN_OUTPUT_TOKENS = 1024
AI_DEFAULT_OUTPUT_TOKENS = 25000
AI_MAX_OUTPUT_TOKENS = AI_CONFIG.max_output_tokens
AI_PROMPT_CACHE_KEY = AI_CONFIG.prompt_cache_key if AI_CONFIG.prompt_cache_enable else ""
REQUEST_LOCK_STALE_SEC = max(300, int(os.getenv("AI_REQUEST_LOCK_STALE_SEC", "1800")))
REQUEST_STABLE_MS = max(20, int(os.getenv("AI_REQUEST_STABLE_MS", "120")))
RESP_ENCODING = os.getenv("AI_RESPONSE_ENCODING", "utf-16")
ANALYTICS_AUTO_ACTIVATE = os.getenv("ANALYTICS_AUTO_ACTIVATE", "false").strip().lower() in {"1", "true", "yes", "on"}
AI_GATE_MODEL_VERSION = "po3-narrative-auditor-20260615b"
AI_TARGET_ARBITRATION_SCHEMA_VERSION = "20260629_target_rebuild_v2"
AI_PROMPT_CONTRACT_VERSION = "20260629_target_arbitration_explain_v2"
LOG_FILE = None  # will be set in main() once the bus path is known
_UNSUPPORTED_OPENAI_KWARGS_LOGGED: set[str] = set()
_AI_RUNTIME_CONFIG_LOGGED = False

def log(msg: str) -> None:
    print(msg, flush=True)
    global LOG_FILE
    if LOG_FILE:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass


def _payload_workload_mode(payload: Dict[str, Any] | None) -> str:
    payload = payload or {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    explicit = str(
        runtime.get("workload_mode")
        or runtime.get("mode")
        or payload.get("workload_mode")
        or payload.get("mode")
        or ""
    ).strip().lower()
    if explicit in {"backtest", "replay", "research", "analytics", "live"}:
        return explicit
    if str(runtime.get("tester") or "").strip().lower() in {"1", "true", "yes", "on"} or runtime.get("tester") is True:
        return "backtest"
    account_mode = str(runtime.get("account_trade_mode") or "").strip().lower()
    if account_mode in {"real", "demo", "contest", "live"}:
        return "live"
    # Fail safe: file-bus trade approvals are treated as live unless explicitly
    # marked as backtest/replay/research/analytics.
    return "live"


def _is_live_payload(payload: Dict[str, Any] | None) -> bool:
    return _payload_workload_mode(payload) == "live"


def _log_ai_runtime_config_once() -> None:
    global _AI_RUNTIME_CONFIG_LOGGED
    if _AI_RUNTIME_CONFIG_LOGGED:
        return
    _AI_RUNTIME_CONFIG_LOGGED = True
    log(f"[ai_gate] Active AI config: {json.dumps(AI_CONFIG.safe_log_dict(), sort_keys=True)}")
    log(
        "[ai_gate] prompt_cache_enabled="
        + str(AI_CONFIG.prompt_cache_enable).lower()
        + f" prompt_cache_key={AI_CONFIG.prompt_cache_key if AI_CONFIG.prompt_cache_enable else ''}"
        + f" prompt_cache_retention={AI_CONFIG.prompt_cache_retention if AI_CONFIG.prompt_cache_enable else ''}"
    )


def _effective_service_tier(payload: Dict[str, Any] | None) -> tuple[str, bool, str]:
    requested = "flex" if AI_CONFIG.use_flex else AI_CONFIG.service_tier
    requested = (requested or "auto").lower()
    if _is_live_payload(payload) and requested == "flex":
        if AI_CONFIG.use_flex and AI_CONFIG.allow_flex_for_live and AI_CONFIG.flex_live_ack:
            return "flex", True, ""
        return "auto", False, "flex_disabled_for_live"
    return requested, requested == "flex", ""


def _openai_timeout_for_payload(payload: Dict[str, Any] | None) -> float:
    _service_tier, flex_used, _disabled_reason = _effective_service_tier(payload)
    if flex_used:
        return float(AI_CONFIG.openai_flex_timeout_sec)
    return float(AI_CONFIG.openai_timeout_sec)


def _apply_prompt_cache_kwargs(kwargs: Dict[str, Any]) -> None:
    if not AI_CONFIG.prompt_cache_enable:
        return
    kwargs["prompt_cache_key"] = AI_CONFIG.prompt_cache_key
    kwargs["prompt_cache_retention"] = AI_CONFIG.prompt_cache_retention


def _apply_service_tier_kwargs(kwargs: Dict[str, Any], payload: Dict[str, Any] | None) -> tuple[str, bool]:
    service_tier, flex_used, disabled_reason = _effective_service_tier(payload)
    if disabled_reason:
        log(f"[ai_gate] {disabled_reason}")
    if service_tier:
        kwargs["service_tier"] = service_tier
    return service_tier, flex_used

def read_json_any_encoding(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()

    # UTF-16 BOMs
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    # UTF-8 BOM
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        # default (most common)
        text = raw.decode("utf-8")

    return json.loads(text)

def _openai_client(timeout_sec: float | None = None):
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "Missing OpenAI API key. Set OPENAI_API_KEY as an environment variable."
        )
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Missing dependency 'openai'. Install: pip install openai pydantic") from e
    kwargs = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    if timeout_sec is not None and timeout_sec > 0:
        kwargs["timeout"] = float(timeout_sec)
    return OpenAI(**kwargs)

def _candidate_models() -> list[str]:
    models: list[str] = []
    for model in [OPENAI_MODEL, *OPENAI_FALLBACK_MODELS]:
        if model and model not in models:
            models.append(model)
    return models


def _filter_supported_kwargs(func: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        supported = inspect.signature(func).parameters
    except Exception:
        return dict(kwargs)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in supported.values()):
        return dict(kwargs)
    unsupported = [key for key in kwargs if key not in supported]
    for key in unsupported:
        if key in {"prompt_cache_key", "prompt_cache_retention", "service_tier"} and key not in _UNSUPPORTED_OPENAI_KWARGS_LOGGED:
            _UNSUPPORTED_OPENAI_KWARGS_LOGGED.add(key)
            log(f"[ai_gate] openai_kwarg_unsupported key={key}; continuing without it")
    return {key: value for key, value in kwargs.items() if key in supported}


def _openai_error_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if value is not None:
            try:
                return int(value)
            except Exception:
                pass
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if value is not None:
        try:
            return int(value)
        except Exception:
            pass
    return None


def _is_flex_unavailable_retryable(exc: Exception) -> bool:
    status = _openai_error_status_code(exc)
    msg = str(exc or "").lower()
    if any(marker in msg for marker in ("invalid api key", "invalid_api_key", "authentication", "permission_denied")):
        return False
    if any(marker in msg for marker in ("insufficient_quota", "current quota", "billing details", "monthly spend")):
        return False
    if status in {400, 401, 403, 404, 422}:
        return False
    if status in {408, 409, 429, 500, 502, 503, 504}:
        return True
    retryable_markers = (
        "flex unavailable",
        "service tier unavailable",
        "temporarily unavailable",
        "overloaded",
        "capacity",
        "try again later",
        "connection error",
        "timeout",
        "timed out",
        "read timeout",
        "server had an error",
    )
    return any(marker in msg for marker in retryable_markers)


def _call_openai_with_flex_retries(func: Any, kwargs: Dict[str, Any]) -> Any:
    filtered = _filter_supported_kwargs(func, kwargs)
    uses_flex = str(filtered.get("service_tier") or "").strip().lower() == "flex"
    max_retries = int(AI_CONFIG.flex_unavailable_max_retries)
    cooldown = float(AI_CONFIG.flex_unavailable_cooldown_sec)
    retries_done = 0
    while True:
        try:
            return func(**filtered)
        except Exception as exc:
            if not uses_flex or not AI_CONFIG.flex_unavailable_retry_enable or max_retries <= 0:
                raise
            if not _is_flex_unavailable_retryable(exc):
                raise
            if retries_done >= max_retries:
                log(
                    "[ai_gate] flex_unavailable_retries_exhausted "
                    f"retries={retries_done} max_retries={max_retries} "
                    f"status={_openai_error_status_code(exc) or ''} error={exc}"
                )
                raise
            retries_done += 1
            log(
                "[ai_gate] flex_unavailable_retry "
                f"retry={retries_done}/{max_retries} cooldown_sec={cooldown:g} "
                f"status={_openai_error_status_code(exc) or ''} error={exc}"
            )
            if cooldown > 0:
                time.sleep(cooldown)


def _call_responses_parse(client: Any, **kwargs: Any) -> Any:
    parse_fn = client.responses.parse
    return _call_openai_with_flex_retries(parse_fn, kwargs)


def _call_responses_create(client: Any, **kwargs: Any) -> Any:
    create_fn = client.responses.create
    return _call_openai_with_flex_retries(create_fn, kwargs)


def _reasoning_config_for_model(model_name: str) -> Dict[str, str] | None:
    name = str(model_name or "").strip().lower()
    effort = AI_REASONING_EFFORT
    if effort == "auto":
        # Older GPT-5 family models default to medium reasoning, which can consume
        # the entire max_output_tokens budget before any visible JSON is emitted.
        if name.startswith("gpt-5.4-nano") or name.startswith("gpt-5.4-mini") or name.startswith("gpt-5.1"):
            effort = "low"
        elif name.startswith("gpt-5.5"):
            effort = "low"
        elif name.startswith("gpt-5"):
            effort = "minimal"
        else:
            effort = ""
    # Some newer GPT-5 family models reject "minimal"; use the nearest supported
    # effort so one bad parameter does not force a model-family fallback.
    if effort == "minimal" and (name.startswith("gpt-5.5") or name.startswith("gpt-5.4-nano")):
        effort = "low"
    if effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        return {"effort": effort}
    return None


def _token_budgets_for_model(model_name: str) -> list[int]:
    budgets: list[int] = []
    name = str(model_name or "").strip().lower()
    retry_caps = (
        (AI_MAX_OUTPUT_TOKENS, max(AI_MAX_OUTPUT_TOKENS, 12288), max(AI_MAX_OUTPUT_TOKENS, 16384))
        if name.startswith("gpt-5")
        else (AI_MAX_OUTPUT_TOKENS, max(AI_MAX_OUTPUT_TOKENS, 4096))
    )
    for candidate in retry_caps:
        if candidate and candidate not in budgets:
            budgets.append(candidate)
    return budgets


def _snapshot_to_input_part(path_str: str, label: str) -> tuple[Dict[str, Any] | None, str]:
    # Handle empty/null paths gracefully
    if not path_str or not str(path_str).strip():
        return None, f"{label}=not_provided"
    path_str = str(path_str).strip()
    if path_str == "__tester_skipped__":
        return None, f"{label}=tester_skipped"
    if path_str == "__capture_failed__":
        return None, f"{label}=capture_failed"
    try:
        path = Path(path_str)
    except Exception as e:
        return None, f"{label}=bad_path({type(e).__name__})"
    if not path.exists() or not path.is_file():
        return None, f"{label}=file_not_found"
    size = path.stat().st_size
    if size <= 0:
        return None, f"{label}=empty_file"
    if size > SNAPSHOT_MAX_BYTES:
        return None, f"{label}=too_large({size})"
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    try:
        raw = path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"}, f"{label}=ok({size})"
    except Exception as e:
        return None, f"{label}=read_error({type(e).__name__})"


def _snapshot_parts(payload: Dict[str, Any]) -> tuple[list[Dict[str, Any]], list[str]]:
    if not AI_ENABLE_SNAPSHOTS:
        return [], ["snapshots=disabled"]
    snaps = _as_dict(payload.get("snapshots"))
    parts: list[Dict[str, Any]] = []
    notes: list[str] = []
    for key, label in (("htf_path", "htf"), ("ltf_path", "ltf")):
        part, note = _snapshot_to_input_part(str(snaps.get(key, "") or ""), label)
        notes.append(note)
        if part is not None:
            parts.append(part)
    return parts, notes


def _ascii_compact(text: str, max_len: int = 480) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    text = "".join(ch if 32 <= ord(ch) <= 126 else " " for ch in text)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 3].rstrip() + "..."
    return text or "no_reason"


def _compact_model_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    po3 = _as_dict(payload.get("po3"))
    regime = _as_dict(payload.get("regime"))
    plan = _as_dict(payload.get("plan"))
    fvg = _as_dict(payload.get("fvg"))
    watchlist = _as_dict(payload.get("watchlist"))
    root_target_candidates = _compact_target_candidates(_target_candidates(payload, plan))
    compact: Dict[str, Any] = {
        "id": payload.get("id"),
        "symbol": payload.get("symbol"),
        "is_buy": payload.get("is_buy"),
        "configured_stop_model": payload.get("configured_stop_model"),
        "po3": {
            "has_sweep": po3.get("has_sweep"),
            "has_displacement": po3.get("has_displacement"),
            "has_bos": po3.get("has_bos"),
            "po3_state": po3.get("po3_state"),
            "po3_state_reason": po3.get("po3_state_reason"),
            "sweep_side": po3.get("sweep_side"),
            "structure_type": po3.get("structure_type"),
            "htf_structure_type": po3.get("htf_structure_type"),
            "ltf_structure_type": po3.get("ltf_structure_type"),
            "final_setup_class": po3.get("final_setup_class"),
            "po3_scope": po3.get("po3_scope"),
            "t_sweep": po3.get("t_sweep"),
            "t_disp": po3.get("t_disp"),
            "t_bos": po3.get("t_bos"),
            "dr_high": po3.get("dr_high"),
            "dr_low": po3.get("dr_low"),
            "dr_mid": po3.get("dr_mid"),
            "manip_low": po3.get("manip_low"),
            "manip_high": po3.get("manip_high"),
            "bos_level": po3.get("bos_level"),
            "sweep_strength": po3.get("sweep_strength"),
            "session_name": po3.get("session_name"),
            "in_killzone": po3.get("in_killzone"),
            "liquidity_target": po3.get("liquidity_target"),
            "liquidity_target_high": po3.get("liquidity_target_high"),
            "liquidity_kind": po3.get("liquidity_kind"),
            "liquidity_cluster_count": po3.get("liquidity_cluster_count"),
            "htf_mss": po3.get("htf_mss"),
            "htf_choch": po3.get("htf_choch"),
            "ltf_bos": po3.get("ltf_bos"),
            "ltf_mss": po3.get("ltf_mss"),
            "ltf_choch": po3.get("ltf_choch"),
        },
        "regime": {
            "atr_pct": regime.get("atr_pct"),
            "trend_strength": regime.get("trend_strength"),
            "trend_slope_pct": regime.get("trend_slope_pct"),
            "adx_value": regime.get("adx_value"),
            "adr_pct": regime.get("adr_pct"),
            "session_vol_ratio": regime.get("session_vol_ratio"),
            "vwap_dist_atr": regime.get("vwap_dist_atr"),
            "compression_score": regime.get("compression_score"),
            "expansion_score": regime.get("expansion_score"),
            "news_risk": regime.get("news_risk"),
        },
        "plan": {
            "entry_est": plan.get("entry_est"),
            "entry_model": plan.get("entry_model"),
            "entry_branch": plan.get("entry_branch"),
            "configured_stop_model": plan.get("configured_stop_model") or payload.get("configured_stop_model"),
            "stop_model": plan.get("stop_model"),
            "tp_model": plan.get("tp_model"),
            "target_source": plan.get("target_source"),
            "sl": plan.get("sl"),
            "tp1": plan.get("tp1"),
            "tp2": plan.get("tp2"),
            "rr2": plan.get("rr2"),
            "effective_rr2": plan.get("effective_rr2"),
            "setup_score": plan.get("setup_score"),
            "setup_family": plan.get("setup_family"),
            "setup_class": plan.get("setup_class"),
            "fvg_execution_class": plan.get("fvg_execution_class"),
            "management_profile": plan.get("management_profile"),
            "target_model": plan.get("target_model"),
            "analytics_key": plan.get("analytics_key"),
            "execution_cost_r": plan.get("execution_cost_r"),
            "slippage_r": plan.get("slippage_r"),
            "commission_r": plan.get("commission_r"),
            "gross_expected_r": plan.get("gross_expected_r"),
            "net_expected_r": plan.get("net_expected_r"),
            "expected_value_r": plan.get("expected_value_r"),
            "runner_trade": plan.get("runner_trade"),
            "runner_downgraded": plan.get("runner_downgraded"),
            "runner_downgrade_reason": plan.get("runner_downgrade_reason"),
            "obstacle_kind": plan.get("obstacle_kind"),
            "obstacle_price": plan.get("obstacle_price"),
            "obstacle_r": plan.get("obstacle_r"),
            "obstacle_distance_r": plan.get("obstacle_distance_r"),
            "liquidity_rr": plan.get("liquidity_rr"),
            "target_arbitration_required": plan.get("target_arbitration_required"),
            "liquidity_target_preserved": plan.get("liquidity_target_preserved"),
            "liquidity_target_model": plan.get("liquidity_target_model"),
            "liquidity_target_valid_structurally": plan.get("liquidity_target_valid_structurally"),
            "liquidity_target_blocked_by_obstacle": plan.get("liquidity_target_blocked_by_obstacle"),
            "fallback_tp": plan.get("fallback_tp"),
            "fallback_rr": plan.get("fallback_rr"),
            "fallback_source": plan.get("fallback_source"),
            "capped_before_obstacle_tp": plan.get("capped_before_obstacle_tp"),
            "capped_before_obstacle_rr": plan.get("capped_before_obstacle_rr"),
            "capped_before_obstacle_source": plan.get("capped_before_obstacle_source"),
            "original_planned_tp_before_ai": plan.get("original_planned_tp_before_ai"),
            "original_planned_rr_before_ai": plan.get("original_planned_rr_before_ai"),
        },
        "target_candidates": root_target_candidates,
        "watchlist": {
            "armed": watchlist.get("armed"),
            "mid_touched": watchlist.get("mid_touched"),
            "b50_touched": watchlist.get("b50_touched"),
            "bars_waited": watchlist.get("bars_waited"),
            "setup_id": watchlist.get("setup_id"),
            "lineage_version": watchlist.get("lineage_version"),
            "lineage_root_id": watchlist.get("lineage_root_id"),
            "attempt_number_for_sweep": watchlist.get("attempt_number_for_sweep"),
            "narrative_state": watchlist.get("narrative_state"),
            "superseded_by": watchlist.get("superseded_by"),
        },
        "source_story": {
            "context_tier": _get_any(payload, ["source_context_tier"], _as_dict(payload.get("story")).get("source_context_tier")),
            "sweep_side": _get_any(payload, ["source_sweep_side"], _as_dict(payload.get("story")).get("source_sweep_side")),
            "t_sweep": _get_any(payload, ["source_t_sweep"], _as_dict(payload.get("story")).get("source_t_sweep")),
            "t_disp": _get_any(payload, ["source_t_disp"], _as_dict(payload.get("story")).get("source_t_disp")),
            "t_bos": _get_any(payload, ["source_t_bos"], _as_dict(payload.get("story")).get("source_t_bos")),
            "manip_low": _get_any(payload, ["source_manip_low"], _as_dict(payload.get("story")).get("source_manip_low")),
            "manip_high": _get_any(payload, ["source_manip_high"], _as_dict(payload.get("story")).get("source_manip_high")),
            "dr_high": _get_any(payload, ["source_dr_high"], _as_dict(payload.get("story")).get("source_dr_high")),
            "dr_low": _get_any(payload, ["source_dr_low"], _as_dict(payload.get("story")).get("source_dr_low")),
            "liquidity_target": _get_any(payload, ["source_liquidity_target"], _as_dict(payload.get("story")).get("source_liquidity_target")),
            "liquidity_kind": _get_any(payload, ["source_liquidity_kind"], _as_dict(payload.get("story")).get("source_liquidity_kind")),
        },
        "fvg": {
            "t_form": fvg.get("t_form"),
            "lower": fvg.get("lower"),
            "upper": fvg.get("upper"),
            "mid": fvg.get("mid"),
            "score": fvg.get("score"),
            "execution_class": fvg.get("execution_class"),
            "mitigation_state": fvg.get("mitigation_state"),
            "invalidation_reason": fvg.get("invalidation_reason"),
            "entry_invalid": fvg.get("entry_invalid"),
            "structure_invalidated": fvg.get("structure_invalidated"),
            "displacement_candle_score": fvg.get("displacement_candle_score"),
            "middle_candle_body_score": fvg.get("middle_candle_body_score"),
            "volume_impulse_score": fvg.get("volume_impulse_score"),
            "gap_width_atr_score": fvg.get("gap_width_atr_score"),
            "premium_discount_score": fvg.get("premium_discount_score"),
            # This legacy MT5 field is a clearance score: high means no nearby
            # opposing imbalance, low means a close obstruction.
            "opposing_clearance_score": fvg.get("opposing_obstruction_score"),
        },
        "snapshot_metadata": _as_dict(payload.get("snapshot_metadata")),
    }
    compact["candidates"] = []
    for cand in (payload.get("candidates") or [])[:12]:
        if not isinstance(cand, dict):
            continue
        compact["candidates"].append({
            "candidate_index": cand.get("candidate_index"),
            "entry_model": cand.get("entry_model"),
            "entry_branch": cand.get("entry_branch"),
            "configured_stop_model": cand.get("configured_stop_model") or payload.get("configured_stop_model"),
            "stop_model": cand.get("stop_model"),
            "tp_model": cand.get("tp_model"),
            "target_source": cand.get("target_source"),
            "entry_est": cand.get("entry_est"),
            "sl": cand.get("sl"),
            "tp1": cand.get("tp1"),
            "tp2": cand.get("tp2"),
            "rr2": cand.get("rr2"),
            "effective_rr2": cand.get("effective_rr2"),
            "liquidity_rr": cand.get("liquidity_rr"),
            "setup_score": cand.get("setup_score"),
            "setup_family": cand.get("setup_family"),
            "setup_class": cand.get("setup_class"),
            "fvg_execution_class": cand.get("fvg_execution_class"),
            "management_profile": cand.get("management_profile"),
            "target_model": cand.get("target_model"),
            "analytics_key": cand.get("analytics_key"),
            "execution_cost_r": cand.get("execution_cost_r"),
            "slippage_r": cand.get("slippage_r"),
            "commission_r": cand.get("commission_r"),
            "gross_expected_r": cand.get("gross_expected_r"),
            "net_expected_r": cand.get("net_expected_r"),
            "expected_value_r": cand.get("expected_value_r"),
            "runner_trade": cand.get("runner_trade"),
            "runner_downgraded": cand.get("runner_downgraded"),
            "runner_downgrade_reason": cand.get("runner_downgrade_reason"),
            "obstacle_kind": cand.get("obstacle_kind"),
            "obstacle_price": cand.get("obstacle_price"),
            "obstacle_r": cand.get("obstacle_r"),
            "obstacle_distance_r": cand.get("obstacle_distance_r"),
            "target_arbitration_required": cand.get("target_arbitration_required"),
            "liquidity_target_preserved": cand.get("liquidity_target_preserved"),
            "liquidity_target_model": cand.get("liquidity_target_model"),
            "liquidity_target_valid_structurally": cand.get("liquidity_target_valid_structurally"),
            "liquidity_target_blocked_by_obstacle": cand.get("liquidity_target_blocked_by_obstacle"),
            "fallback_tp": cand.get("fallback_tp"),
            "fallback_rr": cand.get("fallback_rr"),
            "fallback_source": cand.get("fallback_source"),
            "capped_before_obstacle_tp": cand.get("capped_before_obstacle_tp"),
            "capped_before_obstacle_rr": cand.get("capped_before_obstacle_rr"),
            "capped_before_obstacle_source": cand.get("capped_before_obstacle_source"),
            "original_planned_tp_before_ai": cand.get("original_planned_tp_before_ai"),
            "original_planned_rr_before_ai": cand.get("original_planned_rr_before_ai"),
            "target_candidates": _compact_target_candidates(_target_candidates(payload, cand)),
            "po3_state": cand.get("po3_state"),
            "sweep_side": cand.get("sweep_side"),
            "structure_type": cand.get("structure_type"),
            "po3_scope": cand.get("po3_scope"),
            "source_context_tier": cand.get("source_context_tier"),
            "source_t_sweep": cand.get("source_t_sweep"),
            "attempt_number_for_sweep": cand.get("attempt_number_for_sweep"),
            "fvg_lower": cand.get("fvg_lower"),
            "fvg_upper": cand.get("fvg_upper"),
            "fvg_mid": cand.get("fvg_mid"),
            "fvg_score": cand.get("fvg_score"),
            "origin_score": cand.get("origin_score"),
            "cleanliness_score": cand.get("cleanliness_score"),
            "nesting_score": cand.get("nesting_score"),
            "htf_overlap_score": cand.get("htf_overlap_score"),
            "retest_score": cand.get("retest_score"),
            "displacement_candle_score": cand.get("displacement_candle_score"),
            "middle_candle_body_score": cand.get("middle_candle_body_score"),
            "volume_impulse_score": cand.get("volume_impulse_score"),
            "gap_width_atr_score": cand.get("gap_width_atr_score"),
            "premium_discount_score": cand.get("premium_discount_score"),
            "opposing_clearance_score": cand.get("opposing_obstruction_score"),
        })
    return compact


def _decision_reason_text(reasons: Dict[str, Any] | str) -> str:
    if isinstance(reasons, dict):
        parts: list[str] = []
        ai_reasons = reasons.get("ai_reasons")
        if ai_reasons:
            parts.append(str(ai_reasons))
        rule_notes = reasons.get("rule_notes")
        if rule_notes:
            parts.append(f"rule_notes={rule_notes}")
        if reasons.get("fallback"):
            parts.append(f"fallback={reasons['fallback']}")
        if reasons.get("rejection_codes"):
            parts.append("reject=" + ",".join(str(x) for x in reasons["rejection_codes"]))
        if reasons.get("narrative_state"):
            parts.append(f"state={reasons['narrative_state']}")
        if reasons.get("decision_source"):
            parts.append(f"source={reasons['decision_source']}")
        if reasons.get("error"):
            parts.append(f"error={reasons['error']}")
        if reasons.get("bridge_error"):
            parts.append(f"bridge_error={reasons['bridge_error']}")
        if reasons.get("rule_score") is not None:
            try:
                parts.append(f"rule_score={float(reasons['rule_score']):.2f}")
            except Exception:
                parts.append(f"rule_score={reasons['rule_score']}")
        if reasons.get("agreement") is not None:
            try:
                parts.append(f"agreement={float(reasons['agreement']):.2f}")
            except Exception:
                parts.append(f"agreement={reasons['agreement']}")
        if reasons.get("best_candidate_score") is not None:
            try:
                parts.append(f"best_candidate_score={float(reasons['best_candidate_score']):.2f}")
            except Exception:
                parts.append(f"best_candidate_score={reasons['best_candidate_score']}")
        text = "; ".join(part for part in parts if part)
    else:
        text = str(reasons or "")
    return _ascii_compact(text)


def _response_output_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    output = getattr(resp, "output", None)
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            block_text = getattr(block, "text", None)
            if block_text is None and isinstance(block, dict):
                block_text = block.get("text")
            if isinstance(block_text, str) and block_text:
                parts.append(block_text)
    return "\n".join(parts).strip()


def _parse_decision_text(output_text: str, schema: Any) -> Any:
    text = str(output_text or "").strip()
    if not text:
        raise RuntimeError("empty_structured_output")
    candidates = [text]
    if text.startswith("```"):
        stripped = text
        if stripped.startswith("```json"):
            stripped = stripped[7:]
        elif stripped.startswith("```"):
            stripped = stripped[3:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
        if stripped and stripped not in candidates:
            candidates.append(stripped)
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        json_slice = text[start : end + 1].strip()
        if json_slice and json_slice not in candidates:
            candidates.append(json_slice)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate)
        except Exception as e:
            last_error = e
    preview = _ascii_compact(text, max_len=260)
    raise RuntimeError(f"structured_output_parse_failed: {last_error}; output_text={preview}") from last_error


def _parse_structured_decision(resp: Any, schema: Any) -> Any:
    parsed = getattr(resp, "output_parsed", None)
    if parsed is not None:
        return parsed
    output_text = _response_output_text(resp)
    return _parse_decision_text(output_text, schema)


def _responses_text_format_param(schema_model: Any) -> Dict[str, Any] | None:
    try:
        from openai.lib._parsing._responses import type_to_text_format_param

        value = type_to_text_format_param(schema_model)
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
    except Exception:
        return None
    return None


def _needs_compact_json_retry(exc: Exception) -> bool:
    msg = str(exc).lower()
    recoverable_markers = (
        "empty_structured_output",
        "structured_output_parse_failed",
        "validation error for aigatedecision",
        "json_invalid",
        "eof while parsing",
        "unterminated string",
        "response ended",
        "max_output_tokens",
        "string_too_long",
    )
    return any(marker in msg for marker in recoverable_markers)


def _request_decision_text_fallback(
    client: Any,
    *,
    model_name: str,
    system_msg: str,
    user_content: list[Dict[str, Any]],
    budget: int,
    schema: Any,
    payload: Dict[str, Any] | None = None,
    request_id: str = "",
) -> Any:
    fallback_system = (
        system_msg
        + " Return only one minified JSON object with keys allow, score, chosen_index, confidence, reasons. "
        "Omit optional arrays. Keep reasons under 80 ASCII characters. No markdown."
    )
    reasoning = _reasoning_config_for_model(model_name)
    request_kwargs: Dict[str, Any] = {
        "model": model_name,
        "instructions": fallback_system,
        "input": [{"role": "user", "content": user_content}],
        "text": {"verbosity": "low"},
        "max_output_tokens": budget,
        "reasoning": reasoning,
        "store": False,
        "truncation": "auto",
    }
    _apply_prompt_cache_kwargs(request_kwargs)
    service_tier, flex_used = _apply_service_tier_kwargs(request_kwargs, payload)
    resp = _call_responses_create(client, **request_kwargs)
    log_openai_usage(
        source="ai_gate",
        operation="trade_gate.json_text_fallback",
        model=model_name,
        response=resp,
        request_id=request_id,
        reasoning_effort=reasoning.get("effort", "") if reasoning else "",
        max_output_tokens=budget,
        extra={"service_tier": service_tier, "flex_used": flex_used},
    )
    return _parse_decision_text(_response_output_text(resp), schema)


def _minimal_fallback_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    po3 = _as_dict(payload.get("po3"))
    regime = _as_dict(payload.get("regime"))
    plan = _as_dict(payload.get("plan"))
    return {
        "symbol": payload.get("symbol"),
        "is_buy": payload.get("is_buy"),
        "candidate_count": len(payload.get("candidates") or []),
        "po3": {
            "has_sweep": po3.get("has_sweep"),
            "has_displacement": po3.get("has_displacement"),
            "has_bos": po3.get("has_bos"),
            "session_name": po3.get("session_name"),
            "liquidity_kind": po3.get("liquidity_kind"),
        },
        "regime": {
            "trend_strength": regime.get("trend_strength"),
            "trend_slope_pct": regime.get("trend_slope_pct"),
            "adx_value": regime.get("adx_value"),
            "vwap_dist_atr": regime.get("vwap_dist_atr"),
        },
        "plan": {
            "entry_est": plan.get("entry_est"),
            "sl": plan.get("sl"),
            "tp2": plan.get("tp2"),
            "rr2": plan.get("rr2"),
            "setup_score": plan.get("setup_score"),
        },
    }


def _request_minimal_decision_text_fallback(
    client: Any,
    *,
    model_name: str,
    payload: Dict[str, Any],
    budget: int,
    schema: Any,
    request_id: str = "",
) -> Any:
    minimal_payload = _minimal_fallback_payload(payload)
    minimal_system = (
        "You are a strict PO3 trade gate. "
        "Return only one minified JSON object with keys allow, score, chosen_index, confidence, reasons. "
        "Omit optional arrays. Keep reasons under 80 ASCII characters. No markdown."
    )
    minimal_user = (
        "Evaluate this PO3 setup summary and return only JSON: "
        + json.dumps(minimal_payload, ensure_ascii=False, separators=(",", ":"))
    )
    reasoning = _reasoning_config_for_model(model_name)
    request_kwargs: Dict[str, Any] = {
        "model": model_name,
        "instructions": minimal_system,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": minimal_user}]}],
        "text": {"verbosity": "low"},
        "max_output_tokens": budget,
        "reasoning": reasoning,
        "store": False,
        "truncation": "auto",
    }
    _apply_prompt_cache_kwargs(request_kwargs)
    service_tier, flex_used = _apply_service_tier_kwargs(request_kwargs, payload)
    resp = _call_responses_create(client, **request_kwargs)
    log_openai_usage(
        source="ai_gate",
        operation="trade_gate.json_minimal_fallback",
        model=model_name,
        response=resp,
        request_id=request_id,
        reasoning_effort=reasoning.get("effort", "") if reasoning else "",
        max_output_tokens=budget,
        extra={"service_tier": service_tier, "flex_used": flex_used},
    )
    return _parse_decision_text(_response_output_text(resp), schema)


def _score_setup_openai(payload: Dict[str, Any]) -> Decision:
    """
    Uses OpenAI Responses API + Structured Outputs to return:
    {allow: bool, score: 0..10, chosen_index: int, reasons: dict}
    """
    try:
        from pydantic import BaseModel, Field
    except ImportError as e:
        raise RuntimeError("Missing dependency 'pydantic'. Install: pip install pydantic") from e

    class TargetComparisonItem(BaseModel):
        usable: bool = Field(default=False)
        reason: str = Field(default="", max_length=180)
        risk: str = Field(default="", max_length=120)
        expected_role: str = Field(default="reject", max_length=32)

    class TargetComparison(BaseModel):
        liquidity_target: TargetComparisonItem = Field(default_factory=TargetComparisonItem)
        partial_before_obstacle_then_liquidity: TargetComparisonItem = Field(default_factory=TargetComparisonItem)
        capped_before_obstacle: TargetComparisonItem = Field(default_factory=TargetComparisonItem)
        synthetic_rr_fallback: TargetComparisonItem = Field(default_factory=TargetComparisonItem)

    class TargetArbitrationDecision(BaseModel):
        target_arbitration_schema_version: str = Field(default=AI_TARGET_ARBITRATION_SCHEMA_VERSION, max_length=48)
        prompt_contract_version: str = Field(default=AI_PROMPT_CONTRACT_VERSION, max_length=48)
        arbitration_required: bool = Field(default=False)
        chosen_target_model: str = Field(default="current_plan", max_length=64)
        chosen_tp1: float = Field(default=0.0)
        chosen_tp2: float = Field(default=0.0)
        chosen_rr1: float = Field(default=0.0)
        chosen_rr2: float = Field(default=0.0)
        rejected_target_models: list[str] = Field(default_factory=list, max_length=4)
        blocker_kind: str = Field(default="", max_length=64)
        blocker_severity: float = Field(default=-1.0, ge=-1.0, le=10.0)
        blocker_class: str = Field(default="unknown", max_length=24)
        blocker_is_trade_killer: bool = Field(default=False)
        why_not_liquidity_target: str = Field(default="", max_length=160)
        why_not_partial_before_obstacle: str = Field(default="", max_length=160)
        why_not_capped_before_obstacle: str = Field(default="", max_length=160)
        why_not_synthetic_fallback: str = Field(default="", max_length=160)
        target_decision_reason: str = Field(default="", max_length=160)
        target_comparison: TargetComparison = Field(default_factory=TargetComparison)

    class AIGateDecision(BaseModel):
        model_config = {"protected_namespaces": ()}
        allow: bool
        score: float = Field(ge=0.0, le=10.0)
        chosen_index: int = Field(ge=0)
        confidence: float = Field(ge=0.0, le=1.0)
        reasons: str = Field(default="", max_length=160)
        rejection_codes: list[str] = Field(default_factory=list, max_length=4)
        narrative_state: str = Field(default="audited", max_length=48)
        invalidation_risks: list[str] = Field(default_factory=list, max_length=4)
        missing_confirmations: list[str] = Field(default_factory=list, max_length=4)
        suggested_risk_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
        model_version: str = Field(default=AI_GATE_MODEL_VERSION)
        target_arbitration: TargetArbitrationDecision = Field(default_factory=TargetArbitrationDecision)

    client = _openai_client(_openai_timeout_for_payload(payload))
    candidates = payload.get("candidates") or []
    model_payload = _compact_model_payload(payload)
    snapshot_parts, snapshot_notes = _snapshot_parts(payload)
    symbol = str(payload.get("symbol", "") or "unknown_symbol")
    request_id = str(payload.get("id") or "")
    if snapshot_notes:
        log(f"[ai_gate] snapshot status for {symbol}: {'; '.join(snapshot_notes)}")

    runtime = _as_dict(payload.get("runtime"))
    snapshots_required = _runtime_bool(runtime.get("require_snapshots"), False)
    system_msg = """You are a discretionary but disciplined narrative auditor for a PO3 + FVG intraday setup. Be intelligent and contextual, but use the same professional checklist each time: real liquidity sweep versus ordinary breakout, displacement quality, BOS/MSS/CHOCH after displacement, FVG location in the impulse leg, mitigation and invalidation state, entry quality, stop placement, RR after spread/slippage/commission, and nearby opposing liquidity or imbalance.

Structured MT5 fields are primary evidence; chart snapshots are supporting evidence. Missing or failed chart captures are not a rejection when runtime.require_snapshots is false. The field opposing_clearance_score is favorable when high and means a nearby obstruction when low.

Do not reject because a configured stop model is named structural_sweep, structural_swing, or fvg_edge. Judge the actual prices: entry, SL, TP, RR, obstacle distance, cost in R, and whether the narrative remains valid. A wider structural stop lowers RR; it is not by itself an invalid pattern. Treat an opposing imbalance before target as a risk/quality issue unless the payload clearly marks it as a hard block or the target/RR/evidence becomes unrealistic.

Target arbitration rule: when target_candidates.arbitration_required is true, do not assume the provisional tp2 is final. Fill target_arbitration_schema_version and prompt_contract_version with the exact current constants. Compare liquidity_target, partial_before_obstacle_then_liquidity, capped_before_obstacle, synthetic_rr_fallback, and reject by filling target_comparison for all four choices with usable, reason, risk, and expected_role. Do not choose synthetic_rr_fallback just because an obstacle exists. If liquidity RR is strong and the blocker is minor or moderate, prefer liquidity_target or partial_before_obstacle_then_liquidity. If TP1 before obstacle is possible and liquidity TP2 remains valid, prefer partial_before_obstacle_then_liquidity. If the blocker is major but capped RR is valid, prefer capped_before_obstacle. Choose synthetic_rr_fallback only if liquidity, partial, and capped choices are all worse. If choosing synthetic fallback, provide specific why_not_liquidity_target, why_not_partial_before_obstacle, why_not_capped_before_obstacle, and target_decision_reason. Use target_candidates.blocker_features as evidence; do not classify crossed_opposing_imbalance or any obstacle as major/killer by name alone, and never use 7.0 as a default severity. Missing blocker evidence means blocker_class=unknown and blocker_severity=-1 unless you can infer from explicit numeric facts. Fill target_arbitration with arbitration_required, chosen_target_model, chosen_tp1/tp2, chosen_rr1/rr2, rejected_target_models, blocker severity/class/kind, blocker_is_trade_killer, all why_not fields, target_comparison, and target_decision_reason. If no arbitration is required, use arbitration_required=false, chosen_target_model=current_plan, blocker_severity=-1, blocker_class=unknown, and chosen_tp2=plan.tp2.

Prefer the candidate with the strongest complete story and execution quality, not merely the highest numeric setup_score. Return one chosen candidate index plus machine-readable rejection_codes, invalidation_risks, and missing_confirmations. If evidence is mixed, lower confidence or suggested_risk_multiplier instead of flipping allow/no on a minor ambiguity. Scores above 8 should be rare and reserved for clean sweep-displacement-structure-FVG-target alignment. Keep reasons plain ASCII and concise."""
    snapshot_status = ", ".join(snapshot_notes) if snapshot_notes else "none"
    user_text = (
        "PO3 gate request. "
        f"candidate_count={len(candidates) if isinstance(candidates, list) else 0}; "
        f"snapshots_required={str(snapshots_required).lower()}; "
        f"snapshot_status={snapshot_status}; "
        f"payload={json.dumps(model_payload, ensure_ascii=False, separators=(',',':'))}"
    )

    user_content: list[Dict[str, Any]] = [{"type": "input_text", "text": user_text}]
    user_content.extend(snapshot_parts)

    errors: list[str] = []
    for model_name in _candidate_models():
        reasoning = _reasoning_config_for_model(model_name)
        if reasoning:
            log(f"[ai_gate] Using model={model_name} reasoning_effort={reasoning['effort']}")
        for budget in _token_budgets_for_model(model_name):
            try:
                text_format = _responses_text_format_param(AIGateDecision)
                request_kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "instructions": system_msg,
                    "input": [{"role": "user", "content": user_content}],
                    "max_output_tokens": budget,
                    "store": False,
                    "truncation": "auto",
                }
                if text_format is not None:
                    request_kwargs["text"] = {"format": text_format, "verbosity": "low"}
                else:
                    request_kwargs["text_format"] = AIGateDecision
                    request_kwargs["text"] = {"verbosity": "low"}
                if reasoning:
                    request_kwargs["reasoning"] = reasoning
                _apply_prompt_cache_kwargs(request_kwargs)
                service_tier, flex_used = _apply_service_tier_kwargs(request_kwargs, payload)
                if text_format is not None:
                    resp = _call_responses_create(
                        client,
                        **request_kwargs,
                    )
                    operation = "trade_gate.create_structured"
                else:
                    resp = _call_responses_parse(
                        client,
                        **request_kwargs,
                    )
                    operation = "trade_gate.structured_parse"
                log_openai_usage(
                    source="ai_gate",
                    operation=operation,
                    model=model_name,
                    response=resp,
                    request_id=request_id,
                    reasoning_effort=reasoning.get("effort", "") if reasoning else "",
                    max_output_tokens=budget,
                    extra={"symbol": symbol, "snapshot_count": len(snapshot_parts), "service_tier": service_tier, "flex_used": flex_used},
                )
                out = _parse_structured_decision(resp, AIGateDecision)
                _write_ai_cost_report(
                    payload,
                    request_id=request_id,
                    decision_source="llm_blended",
                    model=model_name,
                    reasoning_effort=reasoning.get("effort", "") if reasoning else "",
                    service_tier=service_tier,
                    prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
                    cache_status="openai_call",
                    batch_used=False,
                    flex_used=flex_used,
                    response=resp,
                    openai_called=True,
                    skip_reason="",
                )
                return Decision(
                    allow=bool(out.allow),
                    score=float(out.score),
                    chosen_index=int(out.chosen_index),
                    confidence=float(out.confidence),
                    reasons=f"model={model_name}; tokens={budget}; snapshots={len(snapshot_parts)}; {str(out.reasons or '').strip()}",
                    decision_source="llm_blended",
                    rejection_codes=list(out.rejection_codes or []),
                    narrative_state=str(out.narrative_state or "audited"),
                    invalidation_risks=list(out.invalidation_risks or []),
                    missing_confirmations=list(out.missing_confirmations or []),
                    suggested_risk_multiplier=float(out.suggested_risk_multiplier),
                    model_version=str(out.model_version or AI_GATE_MODEL_VERSION),
                    **_target_kwargs_from_model(getattr(out, "target_arbitration", None)),
                )
            except Exception as e:
                if _needs_compact_json_retry(e):
                    try:
                        log(f"[ai_gate] Structured output incomplete/invalid for model {model_name} with max_output_tokens={budget}; trying compact same-model JSON fallback.")
                        out = _request_decision_text_fallback(
                            client,
                            model_name=model_name,
                            system_msg=system_msg,
                            user_content=user_content,
                            budget=budget,
                            schema=AIGateDecision,
                            request_id=request_id,
                            payload=payload,
                        )
                        return Decision(
                            allow=bool(out.allow),
                            score=float(out.score),
                            chosen_index=int(out.chosen_index),
                            confidence=float(out.confidence),
                            reasons=f"model={model_name}; mode=json_text_fallback; tokens={budget}; snapshots={len(snapshot_parts)}; {str(out.reasons or '').strip()}",
                            decision_source="llm_blended",
                            rejection_codes=list(out.rejection_codes or []),
                            narrative_state=str(out.narrative_state or "audited"),
                            invalidation_risks=list(out.invalidation_risks or []),
                            missing_confirmations=list(out.missing_confirmations or []),
                            suggested_risk_multiplier=float(out.suggested_risk_multiplier),
                            model_version=str(out.model_version or AI_GATE_MODEL_VERSION),
                            **_target_kwargs_from_model(getattr(out, "target_arbitration", None)),
                        )
                    except Exception as fallback_error:
                        try:
                            log(
                                f"[ai_gate] JSON-text fallback empty/failed for model {model_name} with max_output_tokens={budget}; "
                                "trying minimal same-model JSON fallback."
                            )
                            out = _request_minimal_decision_text_fallback(
                                client,
                                model_name=model_name,
                                payload=payload,
                                budget=budget,
                                schema=AIGateDecision,
                                request_id=request_id,
                            )
                            return Decision(
                                allow=bool(out.allow),
                                score=float(out.score),
                                chosen_index=int(out.chosen_index),
                                confidence=float(out.confidence),
                                reasons=f"model={model_name}; mode=json_minimal_fallback; tokens={budget}; snapshots={len(snapshot_parts)}; {str(out.reasons or '').strip()}",
                                decision_source="llm_blended",
                                rejection_codes=list(out.rejection_codes or []),
                                narrative_state=str(out.narrative_state or "audited"),
                                invalidation_risks=list(out.invalidation_risks or []),
                                missing_confirmations=list(out.missing_confirmations or []),
                                suggested_risk_multiplier=float(out.suggested_risk_multiplier),
                                model_version=str(out.model_version or AI_GATE_MODEL_VERSION),
                                **_target_kwargs_from_model(getattr(out, "target_arbitration", None)),
                            )
                        except Exception as minimal_error:
                            errors.append(f"{model_name}@{budget}: {e}")
                            errors.append(f"{model_name}@{budget}: json_text_fallback failed: {fallback_error}")
                            errors.append(f"{model_name}@{budget}: json_minimal_fallback failed: {minimal_error}")
                            log(
                                f"[ai_gate] Compact JSON recovery failed for model {model_name} with max_output_tokens={budget}; "
                                "the gate will fall back to rule-only scoring if no later attempt succeeds."
                            )
                            continue
                msg = f"{model_name}@{budget}: {e}"
                errors.append(msg)
                log(f"[ai_gate] OpenAI call failed for model {model_name} with max_output_tokens={budget}: {e}")

    raise RuntimeError("All OpenAI model attempts failed: " + " | ".join(errors))

# ---------- Model placeholders ----------

@dataclass
class Decision:
    allow: bool
    score: float  # 0..10
    chosen_index: int = 0
    confidence: float = 0.0
    reasons: Dict[str, Any] | str = ""
    decision_source: str = ""
    rejection_codes: list[str] | None = None
    narrative_state: str = ""
    invalidation_risks: list[str] | None = None
    missing_confirmations: list[str] | None = None
    suggested_risk_multiplier: float = 1.0
    model_version: str = AI_GATE_MODEL_VERSION
    decision_id: str = ""
    ai_score_threshold: float = 0.0
    ai_threshold_source: str = ""
    global_ai_score_as_hard_floor: bool = False
    ai_threshold_passed: bool = True
    ai_reject_reason: str = ""
    target_arbitration: Dict[str, Any] | None = None
    chosen_target_model: str = ""
    chosen_tp1: float = 0.0
    chosen_tp2: float = 0.0
    chosen_rr1: float = 0.0
    chosen_rr2: float = 0.0
    rejected_target_models: list[str] | None = None
    target_blocker_kind: str = ""
    target_blocker_severity: float = -1.0
    target_blocker_class: str = ""
    target_blocker_is_trade_killer: bool = False
    target_decision_reason: str = ""
    target_blocker_severity_present: bool = False
    target_blocker_class_present: bool = False
    target_blocker_is_trade_killer_present: bool = False
    target_decision_reason_present: bool = False
    why_not_liquidity_target: str = ""
    why_not_partial_before_obstacle: str = ""
    why_not_capped_before_obstacle: str = ""
    why_not_synthetic_fallback: str = ""
    target_arbitration_schema_version: str = AI_TARGET_ARBITRATION_SCHEMA_VERSION
    prompt_contract_version: str = AI_PROMPT_CONTRACT_VERSION
    target_comparison_json: str = "{}"

def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}

def _get_any(d: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None and d[k] != "":
            return d[k]
    return default

def _boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default

def _target_candidates(payload: Dict[str, Any], item: Dict[str, Any] | None = None) -> Dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    plan = _as_dict(payload.get("plan"))
    for obj in (item, plan, _as_dict(payload.get("target_candidates"))):
        tc = obj.get("target_candidates") if isinstance(obj, dict) else None
        if isinstance(tc, dict):
            return tc
    root_tc = payload.get("target_candidates")
    return root_tc if isinstance(root_tc, dict) else {}

def _target_candidate_option(candidates: Dict[str, Any], *names: str) -> Dict[str, Any]:
    for name in names:
        value = candidates.get(name)
        if isinstance(value, dict):
            return value
    return {}

def _target_arbitration_required(payload: Dict[str, Any], item: Dict[str, Any] | None = None) -> bool:
    item = item if isinstance(item, dict) else {}
    plan = _as_dict(payload.get("plan"))
    candidates = _target_candidates(payload, item)
    return (
        _boolish(item.get("target_arbitration_required"), False)
        or _boolish(plan.get("target_arbitration_required"), False)
        or _boolish(candidates.get("arbitration_required"), False)
    )

def _has_real_liquidity_target_candidate(payload: Dict[str, Any], item: Dict[str, Any] | None = None) -> bool:
    item = item if isinstance(item, dict) else {}
    plan = _as_dict(payload.get("plan"))
    candidates = _target_candidates(payload, item)
    liquidity = _target_candidate_option(candidates, "liquidity_target", "real_liquidity", "real_liquidity_target")
    tp = _floatish(
        _get_any(
            liquidity,
            ["tp2", "target", "price"],
            _get_any(item, ["liquidity_target_preserved"], _get_any(plan, ["liquidity_target_preserved"], 0.0)),
        ),
        0.0,
    )
    return tp > 0.0 and _boolish(liquidity.get("available"), True)

def _compact_target_candidates(candidates: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(candidates, dict) or not candidates:
        return {}
    out: Dict[str, Any] = {
        "arbitration_required": candidates.get("arbitration_required"),
        "current_target_source": candidates.get("current_target_source"),
        "current_tp_model": candidates.get("current_tp_model"),
        "current_tp2": candidates.get("current_tp2"),
        "current_rr2": candidates.get("current_rr2"),
        "obstacle_kind": candidates.get("obstacle_kind"),
        "obstacle_price": candidates.get("obstacle_price"),
        "obstacle_r": candidates.get("obstacle_r"),
        "obstacle_distance_r": candidates.get("obstacle_distance_r"),
        "obstacle_tf": candidates.get("obstacle_tf"),
        "obstacle_strength_features": candidates.get("obstacle_strength_features"),
        "effective_fallback_rr": candidates.get("effective_fallback_rr"),
        "blocker_features": candidates.get("blocker_features") if isinstance(candidates.get("blocker_features"), dict) else {},
    }
    for key in ("liquidity_target", "capped_before_obstacle", "synthetic_rr_fallback"):
        option = candidates.get(key)
        if isinstance(option, dict):
            out[key] = {
                "available": option.get("available"),
                "model": option.get("model"),
                "tp2": option.get("tp2"),
                "rr2": option.get("rr2"),
                "effective_rr2": option.get("effective_rr2"),
                "valid_structurally": option.get("valid_structurally"),
                "blocked_by_obstacle": option.get("blocked_by_obstacle"),
                "partial_allowed": option.get("partial_allowed"),
                "crosses_obstacle": option.get("crosses_obstacle"),
            }
    return out

def _floatish(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        if out != out or out in (float("inf"), float("-inf")):
            return default
        return out
    except Exception:
        return default

def _target_kwargs_from_dict(raw: Dict[str, Any] | None, present_fields: set[str] | None = None) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    present = present_fields if present_fields is not None else set(data.keys())
    rejected = data.get("rejected_target_models")
    if not isinstance(rejected, list):
        rejected = []
    comparison = data.get("target_comparison")
    if not isinstance(comparison, dict):
        comparison = {}
    severity_value = data.get("blocker_severity", data.get("target_blocker_severity"))
    class_value = data.get("blocker_class", data.get("target_blocker_class"))
    killer_value = data.get("blocker_is_trade_killer", data.get("target_blocker_is_trade_killer"))
    reason_value = data.get("target_decision_reason", data.get("reason"))
    return {
        "target_arbitration": data,
        "chosen_target_model": str(data.get("chosen_target_model") or data.get("chosen_model") or ""),
        "chosen_tp1": _floatish(data.get("chosen_tp1"), 0.0),
        "chosen_tp2": _floatish(data.get("chosen_tp2"), 0.0),
        "chosen_rr1": _floatish(data.get("chosen_rr1"), 0.0),
        "chosen_rr2": _floatish(data.get("chosen_rr2"), 0.0),
        "rejected_target_models": [str(x) for x in rejected if str(x or "").strip()],
        "target_blocker_kind": str(data.get("blocker_kind") or data.get("target_blocker_kind") or ""),
        "target_blocker_severity": _floatish(severity_value, -1.0),
        "target_blocker_class": str(class_value or ""),
        "target_blocker_is_trade_killer": _boolish(killer_value, False),
        "target_decision_reason": str(reason_value or ""),
        "target_blocker_severity_present": ("blocker_severity" in present or "target_blocker_severity" in present) and severity_value is not None,
        "target_blocker_class_present": ("blocker_class" in present or "target_blocker_class" in present) and class_value is not None,
        "target_blocker_is_trade_killer_present": ("blocker_is_trade_killer" in present or "target_blocker_is_trade_killer" in present) and killer_value is not None,
        "target_decision_reason_present": ("target_decision_reason" in present or "reason" in present) and reason_value is not None,
        "why_not_liquidity_target": str(data.get("why_not_liquidity_target") or ""),
        "why_not_partial_before_obstacle": str(data.get("why_not_partial_before_obstacle") or ""),
        "why_not_capped_before_obstacle": str(data.get("why_not_capped_before_obstacle") or ""),
        "why_not_synthetic_fallback": str(data.get("why_not_synthetic_fallback") or ""),
        "target_arbitration_schema_version": str(data.get("target_arbitration_schema_version") or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
        "prompt_contract_version": str(data.get("prompt_contract_version") or AI_PROMPT_CONTRACT_VERSION),
        "target_comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")) if comparison else "{}",
    }

def _target_kwargs_from_model(model_obj: Any) -> Dict[str, Any]:
    if model_obj is None:
        return _target_kwargs_from_dict({})
    if hasattr(model_obj, "model_dump"):
        try:
            present_fields = set(getattr(model_obj, "model_fields_set", set()) or set())
            return _target_kwargs_from_dict(model_obj.model_dump(), present_fields=present_fields)
        except Exception:
            pass
    if isinstance(model_obj, dict):
        return _target_kwargs_from_dict(model_obj)
    return _target_kwargs_from_dict({})


def _json_object_from_text(text: Any) -> Dict[str, Any]:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip().startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _response_usage_tokens(response: Any) -> tuple[Any, Any]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    def _read(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    input_tokens = _read(usage, "input_tokens")
    output_tokens = _read(usage, "output_tokens")
    if input_tokens is None:
        input_tokens = _read(usage, "prompt_tokens")
    if output_tokens is None:
        output_tokens = _read(usage, "completion_tokens")
    return input_tokens, output_tokens


def _cost_report_path() -> Path:
    path = AI_CONFIG.cost_report_file
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _write_ai_cost_report(
    payload: Dict[str, Any],
    *,
    request_id: str = "",
    decision_source: str = "",
    model: str = "",
    reasoning_effort: str = "",
    service_tier: str = "",
    prompt_cache_enabled: bool | None = None,
    cache_status: str = "",
    batch_used: bool = False,
    flex_used: bool = False,
    response: Any = None,
    openai_called: bool = False,
    skip_reason: str = "",
    input_tokens: Any = None,
    output_tokens: Any = None,
    ai_score_threshold: Any = None,
    ai_threshold_source: str = "",
    ai_threshold_passed: Any = None,
) -> None:
    if not AI_CONFIG.cost_report_enable:
        return
    try:
        plan = _as_dict(payload.get("plan"))
        po3 = _as_dict(payload.get("po3"))
        cands = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        first_cand = cands[0] if cands and isinstance(cands[0], dict) else {}
        if input_tokens is None and output_tokens is None and response is not None:
            input_tokens, output_tokens = _response_usage_tokens(response)
        row = {
            "timestamp": int(time.time()),
            "request_id": str(request_id or payload.get("id") or ""),
            "symbol": str(payload.get("symbol") or ""),
            "setup_family": str(
                _get_any(first_cand, ["setup_family"], _get_any(plan, ["setup_family"], _get_any(po3, ["final_setup_class"], "")))
                or ""
            ),
            "decision_source": str(decision_source or ""),
            "model": str(model or ""),
            "reasoning_effort": str(reasoning_effort or ""),
            "service_tier": str(service_tier or ""),
            "prompt_cache_enabled": bool(AI_CONFIG.prompt_cache_enable if prompt_cache_enabled is None else prompt_cache_enabled),
            "cache_status": str(cache_status or ""),
            "batch_used": bool(batch_used),
            "flex_used": bool(flex_used),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": None,
            "openai_called": bool(openai_called),
            "skip_reason": str(skip_reason or ""),
            "ai_score_threshold": ai_score_threshold,
            "ai_threshold_source": str(ai_threshold_source or ""),
            "ai_threshold_passed": ai_threshold_passed,
            "target_arbitration_required": _target_arbitration_required(payload, first_cand or plan),
            "target_candidates": _compact_target_candidates(_target_candidates(payload, first_cand or plan)),
        }
        path = _cost_report_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        log(f"[ai_gate] cost_report_write_failed error={exc}")


def _runtime_inputs(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _as_dict(payload.get("runtime_inputs"))


AI_GATE_COUNTERS: Dict[str, int] = {
    "hard_pre_gate_checked": 0,
    "hard_pre_gate_rejected": 0,
    "ai_calls_skipped_by_hard_gate": 0,
    "ai_cache_hit": 0,
    "ai_cache_miss": 0,
}


def _inc_counter(name: str) -> None:
    AI_GATE_COUNTERS[name] = AI_GATE_COUNTERS.get(name, 0) + 1


CRITICAL_RUNTIME_INPUT_KEYS = [
    "runtime_input_hash",
    "trade_only_killzones",
    "enable_asia_killzone",
    "asia_killzone_start_hour",
    "asia_killzone_start_minute",
    "asia_killzone_end_hour",
    "asia_killzone_end_minute",
    "london_killzone_start_hour",
    "london_killzone_start_minute",
    "london_killzone_end_hour",
    "london_killzone_end_minute",
    "newyork_killzone_start_hour",
    "newyork_killzone_start_minute",
    "newyork_killzone_end_hour",
    "newyork_killzone_end_minute",
    "suppress_micro_bisi_sibi_edge",
    "suppress_stale_fvg_branches",
    "suppress_touched_continuation_unless_retested",
    "suppress_continuation_touched_fvg",
    "suppress_continuation_stale_fvg",
    "reject_synthetic_fallback_after_crossed_obstacle",
    "require_ai_target_arbitration_on_obstacle",
    "hard_reject_crossed_obstacle_target",
    "allow_ai_to_use_liquidity_target_behind_minor_blocker",
    "allow_partial_before_obstacle",
    "blocker_kill_severity",
    "blocker_major_severity",
    "blocker_minor_max_severity",
    "execution_reject_cost_r",
    "execution_reduce_risk_cost_r",
    "micro_scalp_max_cost_frac_of_planned_r",
    "require_displacement",
    "allow_synthetic_rr_target",
    "min_live_rr2",
    "fallback_rr2",
    "fallback_rr_buffer_r",
    "standard_trade_liquidity_rr_floor",
    "max_target_atr_mult",
    "max_target_adr_frac",
    "obstacle_reject_r",
    "use_ai",
    "ai_strict",
    "min_ai_score_trend",
    "ai_score_full_po3",
    "ai_score_micro_po3",
    "ai_score_continuation",
    "ai_score_range",
    "ai_score_failed_breakout",
    "global_ai_score_as_hard_floor",
    "min_ai_confidence",
    "use_snapshot_ai",
    "require_snapshots",
    "exclusive_trading_enabled",
    "virtual_ledger_mode",
    "backend_pnl_mode",
]


def _bucket_float(value: Any, step: float = 0.0001) -> Any:
    try:
        val = float(value)
        if not (val == val) or val in (float("inf"), float("-inf")):
            return None
        if step <= 0:
            return round(val, 6)
        return round(round(val / step) * step, 6)
    except Exception:
        return None


def _cache_payload_parts(payload: Dict[str, Any], best_index: int) -> tuple[Dict[str, Any], Dict[str, Any]]:
    plan = _as_dict(payload.get("plan"))
    po3 = _as_dict(payload.get("po3"))
    fvg = _as_dict(payload.get("fvg"))
    cands = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    cand = cands[best_index] if 0 <= best_index < len(cands) and isinstance(cands[best_index], dict) else {}
    merged = {**plan, **fvg, **cand}
    return merged, po3


def _decision_cache_signature(payload: Dict[str, Any], best_index: int) -> tuple[str, str, Dict[str, Any]]:
    merged, po3 = _cache_payload_parts(payload, best_index)
    runtime_inputs = _runtime_inputs(payload)
    compact_targets = _compact_target_candidates(_target_candidates(payload, merged))
    blocker_features = compact_targets.get("blocker_features") if isinstance(compact_targets.get("blocker_features"), dict) else {}
    target_blob = json.dumps(compact_targets, sort_keys=True, separators=(",", ":"))
    session_name = _get_any(merged, ["session_name"], _get_any(po3, ["session_name"], payload.get("session_name")))
    in_killzone = _get_any(merged, ["in_killzone"], _get_any(po3, ["in_killzone"], payload.get("in_killzone")))
    direction = _get_any(merged, ["direction"], "buy" if payload.get("is_buy") else "sell")
    execution_cost_r = (
        _floatish(_get_any(merged, ["execution_cost_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["slippage_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["commission_r"], 0.0), 0.0)
    )
    spread_r = _get_any(merged, ["spread_r"], _get_any(payload, ["spread_r"], None))
    fields = {
        "ai_target_arbitration_schema_version": AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        "ai_prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
        "symbol": str(payload.get("symbol") or ""),
        "direction": str(direction or "").lower(),
        "setup_family": str(_get_any(merged, ["setup_family"], "") or "").lower(),
        "setup_class": str(_get_any(merged, ["setup_class"], "") or "").lower(),
        "entry_branch": str(_get_any(merged, ["entry_branch", "entry_model"], "") or "").lower(),
        "source_t_sweep": str(_get_any(merged, ["source_t_sweep"], _get_any(po3, ["t_sweep"], "")) or ""),
        "source_t_disp": str(_get_any(merged, ["source_t_disp"], _get_any(po3, ["t_disp", "t_displacement"], "")) or ""),
        "source_t_bos": str(_get_any(merged, ["source_t_bos"], _get_any(po3, ["t_bos"], "")) or ""),
        "fvg_lower": _bucket_float(_get_any(merged, ["fvg_lower", "lower"], None)),
        "fvg_upper": _bucket_float(_get_any(merged, ["fvg_upper", "upper"], None)),
        "entry_est": _bucket_float(_get_any(merged, ["entry_est", "entry"], None)),
        "sl": _bucket_float(_get_any(merged, ["sl", "stop_loss", "stop"], None)),
        "tp2": _bucket_float(_get_any(merged, ["tp2", "tp", "final_tp"], None)),
        "target_source": str(_get_any(merged, ["target_source"], payload.get("target_source")) or "").lower(),
        "target_model": str(_get_any(merged, ["target_model", "tp_model"], payload.get("target_model")) or "").lower(),
        "chosen_target_model": str(_get_any(merged, ["chosen_target_model", "ai_chosen_target_model"], payload.get("chosen_target_model")) or "").lower(),
        "target_candidates_hash": sha256(target_blob.encode("utf-8")).hexdigest() if target_blob != "{}" else "",
        "liquidity_target_preserved": _bucket_float(_get_any(merged, ["liquidity_target_preserved"], None)),
        "liquidity_target": _bucket_float(_get_any(merged, ["liquidity_target", "liquidity_target_preserved"], _get_any(po3, ["liquidity_target"], None))),
        "liquidity_rr": _bucket_float(_get_any(merged, ["liquidity_rr"], None), 0.01),
        "fallback_tp": _bucket_float(_get_any(merged, ["fallback_tp"], None)),
        "fallback_rr": _bucket_float(_get_any(merged, ["fallback_rr"], None), 0.01),
        "effective_fallback_rr": _bucket_float(compact_targets.get("effective_fallback_rr"), 0.01),
        "capped_before_obstacle_tp": _bucket_float(_get_any(merged, ["capped_before_obstacle_tp"], None)),
        "capped_before_obstacle_rr": _bucket_float(_get_any(merged, ["capped_before_obstacle_rr"], None), 0.01),
        "obstacle_kind": str(_get_any(merged, ["obstacle_kind"], payload.get("obstacle_kind")) or "").lower(),
        "obstacle_tf": str(_get_any(merged, ["obstacle_tf"], payload.get("obstacle_tf")) or "").lower(),
        "obstacle_price": _bucket_float(_get_any(merged, ["obstacle_price"], payload.get("obstacle_price"))),
        "obstacle_distance_r": _bucket_float(_get_any(merged, ["obstacle_distance_r"], None), 0.01),
        "obstacle_width_atr": _bucket_float(blocker_features.get("obstacle_width_atr"), 0.01),
        "obstacle_age_bars": _bucket_float(blocker_features.get("obstacle_age_bars"), 1.0),
        "obstacle_mitigated_percent": _bucket_float(blocker_features.get("obstacle_mitigated_percent"), 1.0),
        "tp1_before_obstacle_possible": _boolish(blocker_features.get("tp1_before_obstacle_possible"), False),
        "distance_from_obstacle_to_liquidity_target_r": _bucket_float(blocker_features.get("distance_from_obstacle_to_liquidity_target_r"), 0.01),
        "target_arbitration_required": _boolish(_get_any(merged, ["target_arbitration_required"], payload.get("target_arbitration_required")), False),
        "inp_require_ai_target_arbitration_on_obstacle": _boolish(runtime_inputs.get("require_ai_target_arbitration_on_obstacle"), False),
        "inp_hard_reject_crossed_obstacle_target": _boolish(runtime_inputs.get("hard_reject_crossed_obstacle_target"), False),
        "inp_reject_synthetic_fallback_after_crossed_obstacle": _boolish(runtime_inputs.get("reject_synthetic_fallback_after_crossed_obstacle"), True),
        "inp_allow_ai_to_use_liquidity_target_behind_minor_blocker": _boolish(runtime_inputs.get("allow_ai_to_use_liquidity_target_behind_minor_blocker"), False),
        "inp_allow_partial_before_obstacle": _boolish(runtime_inputs.get("allow_partial_before_obstacle"), False),
        "inp_min_live_rr2": _bucket_float(runtime_inputs.get("min_live_rr2"), 0.01),
        "inp_fallback_rr2": _bucket_float(runtime_inputs.get("fallback_rr2"), 0.01),
        "inp_fallback_rr_buffer_r": _bucket_float(runtime_inputs.get("fallback_rr_buffer_r"), 0.01),
        "inp_max_target_atr_mult": _bucket_float(runtime_inputs.get("max_target_atr_mult"), 0.01),
        "inp_max_target_adr_frac": _bucket_float(runtime_inputs.get("max_target_adr_frac"), 0.01),
        "inp_standard_trade_liquidity_rr_floor": _bucket_float(runtime_inputs.get("standard_trade_liquidity_rr_floor"), 0.01),
        "ai_score_full_po3": _bucket_float(runtime_inputs.get("ai_score_full_po3"), 0.01),
        "ai_score_micro_po3": _bucket_float(runtime_inputs.get("ai_score_micro_po3"), 0.01),
        "ai_score_continuation": _bucket_float(runtime_inputs.get("ai_score_continuation"), 0.01),
        "ai_score_range": _bucket_float(runtime_inputs.get("ai_score_range"), 0.01),
        "ai_score_failed_breakout": _bucket_float(runtime_inputs.get("ai_score_failed_breakout"), 0.01),
        "session_name": str(session_name or "").lower(),
        "killzone_code": str(_get_any(merged, ["killzone_code"], "K" if _boolish(in_killzone, False) else "NK") or "").upper(),
        "runtime_input_hash": str(payload.get("runtime_input_hash") or _runtime_inputs(payload).get("runtime_input_hash") or ""),
        "execution_cost_r_bucket": _bucket_float(execution_cost_r, 0.01),
        "spread_r_bucket": _bucket_float(spread_r, 0.01),
    }
    full_blob = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    base_fields = dict(fields)
    for key in (
        "tp2",
        "target_source",
        "target_model",
        "chosen_target_model",
        "target_candidates_hash",
        "liquidity_target_preserved",
        "liquidity_target",
        "liquidity_rr",
        "fallback_tp",
        "fallback_rr",
        "effective_fallback_rr",
        "capped_before_obstacle_tp",
        "capped_before_obstacle_rr",
        "obstacle_kind",
        "obstacle_tf",
        "obstacle_price",
        "obstacle_distance_r",
        "obstacle_width_atr",
        "obstacle_age_bars",
        "obstacle_mitigated_percent",
        "tp1_before_obstacle_possible",
        "distance_from_obstacle_to_liquidity_target_r",
        "target_arbitration_required",
        "inp_require_ai_target_arbitration_on_obstacle",
        "inp_hard_reject_crossed_obstacle_target",
        "inp_reject_synthetic_fallback_after_crossed_obstacle",
        "inp_allow_ai_to_use_liquidity_target_behind_minor_blocker",
        "inp_allow_partial_before_obstacle",
        "inp_min_live_rr2",
        "inp_fallback_rr2",
        "inp_fallback_rr_buffer_r",
        "inp_max_target_atr_mult",
        "inp_max_target_adr_frac",
        "inp_standard_trade_liquidity_rr_floor",
        "execution_cost_r_bucket",
        "spread_r_bucket",
    ):
        base_fields.pop(key, None)
    base_blob = json.dumps(base_fields, sort_keys=True, separators=(",", ":"))
    return sha256(full_blob.encode("utf-8")).hexdigest(), sha256(base_blob.encode("utf-8")).hexdigest(), fields


def _cached_decision_schema_miss_reason(dec_raw: Dict[str, Any]) -> str:
    schema = str(dec_raw.get("target_arbitration_schema_version") or "")
    prompt_contract = str(dec_raw.get("prompt_contract_version") or "")
    if schema != AI_TARGET_ARBITRATION_SCHEMA_VERSION or prompt_contract != AI_PROMPT_CONTRACT_VERSION:
        return "cache_miss_due_to_schema_version"
    chosen = str(dec_raw.get("chosen_target_model") or "").strip().lower()
    target_arbitration = dec_raw.get("target_arbitration") if isinstance(dec_raw.get("target_arbitration"), dict) else {}
    comparison = dec_raw.get("target_comparison")
    if not isinstance(comparison, dict):
        comparison = target_arbitration.get("target_comparison") if isinstance(target_arbitration.get("target_comparison"), dict) else {}
    if chosen in {"synthetic_rr_fallback", "fallback", "synthetic"}:
        if not str(dec_raw.get("why_not_liquidity_target") or "").strip():
            return "cache_miss_due_to_schema_version"
        if not str(dec_raw.get("why_not_partial_before_obstacle") or "").strip():
            return "cache_miss_due_to_schema_version"
        if not str(dec_raw.get("why_not_capped_before_obstacle") or "").strip():
            return "cache_miss_due_to_schema_version"
        if not str(dec_raw.get("target_decision_reason") or "").strip():
            return "cache_miss_due_to_schema_version"
    required_comparison_keys = {
        "liquidity_target",
        "partial_before_obstacle_then_liquidity",
        "capped_before_obstacle",
        "synthetic_rr_fallback",
    }
    if chosen and chosen != "current_plan" and not required_comparison_keys.issubset(set(comparison.keys())):
        return "cache_miss_due_to_schema_version"
    return ""


class AIDecisionCache:
    def __init__(self, path: Path, ttl_sec: int) -> None:
        self.path = path
        self.ttl_sec = ttl_sec
        self._lock = Lock()

    def lookup(self, signature: str, base_signature: str) -> tuple[Decision | None, str]:
        now = int(time.time())
        with self._lock:
            if not self.path.exists():
                return None, "miss"
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except Exception:
                return None, "miss_read_error"
            base_seen = False
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                ts = int(item.get("timestamp") or 0)
                if ts and now - ts > self.ttl_sec:
                    continue
                if item.get("signature") == signature:
                    dec_raw = item.get("decision") if isinstance(item.get("decision"), dict) else {}
                    schema_miss = _cached_decision_schema_miss_reason(dec_raw)
                    if schema_miss:
                        log(
                            "[ai_cache] hit=false reason=cache_miss_due_to_schema_version"
                            f" cached_schema={str(dec_raw.get('target_arbitration_schema_version') or '')}"
                            f" required_schema={AI_TARGET_ARBITRATION_SCHEMA_VERSION}"
                        )
                        return None, schema_miss
                    dec = Decision(
                        allow=bool(dec_raw.get("allow")),
                        score=float(dec_raw.get("score") or 0.0),
                        chosen_index=int(dec_raw.get("chosen_index") or 0),
                        confidence=float(dec_raw.get("confidence") or 0.0),
                        reasons={
                            "decision_source": "ai_cache_hit",
                            "cached_source": dec_raw.get("decision_source", ""),
                            "cached_model_version": dec_raw.get("model_version", ""),
                            "cached_at": ts,
                            "cached_reasons": dec_raw.get("reasons", ""),
                        },
                        decision_source="ai_cache_hit",
                        rejection_codes=list(dec_raw.get("rejection_codes") or []),
                        narrative_state=str(dec_raw.get("narrative_state") or "cached"),
                        invalidation_risks=list(dec_raw.get("invalidation_risks") or []),
                        missing_confirmations=list(dec_raw.get("missing_confirmations") or []),
                        suggested_risk_multiplier=float(dec_raw.get("suggested_risk_multiplier") or 1.0),
                        model_version=str(dec_raw.get("model_version") or AI_GATE_MODEL_VERSION),
                        decision_id=str(dec_raw.get("decision_id") or ""),
                        ai_score_threshold=float(dec_raw.get("ai_score_threshold") or 0.0),
                        ai_threshold_source=str(dec_raw.get("ai_threshold_source") or ""),
                        global_ai_score_as_hard_floor=bool(dec_raw.get("global_ai_score_as_hard_floor")),
                        ai_threshold_passed=bool(dec_raw.get("ai_threshold_passed", True)),
                        ai_reject_reason=str(dec_raw.get("ai_reject_reason") or ""),
                        target_arbitration=dec_raw.get("target_arbitration") if isinstance(dec_raw.get("target_arbitration"), dict) else {},
                        chosen_target_model=str(dec_raw.get("chosen_target_model") or ""),
                        chosen_tp1=float(dec_raw.get("chosen_tp1") or 0.0),
                        chosen_tp2=float(dec_raw.get("chosen_tp2") or 0.0),
                        chosen_rr1=float(dec_raw.get("chosen_rr1") or 0.0),
                        chosen_rr2=float(dec_raw.get("chosen_rr2") or 0.0),
                        rejected_target_models=list(dec_raw.get("rejected_target_models") or []),
                        target_blocker_kind=str(dec_raw.get("target_blocker_kind") or ""),
                        target_blocker_severity=float(dec_raw.get("target_blocker_severity", -1.0) if dec_raw.get("target_blocker_severity") is not None else -1.0),
                        target_blocker_class=str(dec_raw.get("target_blocker_class") or ""),
                        target_blocker_is_trade_killer=bool(dec_raw.get("target_blocker_is_trade_killer")),
                        target_decision_reason=str(dec_raw.get("target_decision_reason") or ""),
                        target_blocker_severity_present=bool(dec_raw.get("target_blocker_severity_present")),
                        target_blocker_class_present=bool(dec_raw.get("target_blocker_class_present")),
                        target_blocker_is_trade_killer_present=bool(dec_raw.get("target_blocker_is_trade_killer_present")),
                        target_decision_reason_present=bool(dec_raw.get("target_decision_reason_present")),
                        why_not_liquidity_target=str(dec_raw.get("why_not_liquidity_target") or ""),
                        why_not_partial_before_obstacle=str(dec_raw.get("why_not_partial_before_obstacle") or ""),
                        why_not_capped_before_obstacle=str(dec_raw.get("why_not_capped_before_obstacle") or ""),
                        why_not_synthetic_fallback=str(dec_raw.get("why_not_synthetic_fallback") or ""),
                        target_arbitration_schema_version=str(dec_raw.get("target_arbitration_schema_version") or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
                        prompt_contract_version=str(dec_raw.get("prompt_contract_version") or AI_PROMPT_CONTRACT_VERSION),
                        target_comparison_json=json.dumps(dec_raw.get("target_comparison") or {}, ensure_ascii=False, separators=(",", ":")) if isinstance(dec_raw.get("target_comparison"), dict) else str(dec_raw.get("target_comparison_json") or "{}"),
                    )
                    return dec, "hit"
                if item.get("base_signature") == base_signature:
                    base_seen = True
            return None, "invalidated_material_field_changed" if base_seen else "miss"

    def store(self, signature: str, base_signature: str, fields: Dict[str, Any], decision: Decision) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                target_comparison: Dict[str, Any] = {}
                if decision.target_comparison_json:
                    try:
                        parsed_comparison = json.loads(decision.target_comparison_json)
                        if isinstance(parsed_comparison, dict):
                            target_comparison = parsed_comparison
                    except Exception:
                        target_comparison = {}
                row = {
                    "timestamp": int(time.time()),
                    "signature": signature,
                    "base_signature": base_signature,
                    "fields": fields,
                    "decision": {
                        "allow": bool(decision.allow),
                        "score": float(decision.score),
                        "chosen_index": int(decision.chosen_index),
                        "confidence": float(decision.confidence),
                        "reasons": _decision_reason_text(decision.reasons),
                        "decision_source": str(decision.decision_source or ""),
                        "rejection_codes": list(decision.rejection_codes or []),
                        "narrative_state": str(decision.narrative_state or ""),
                        "invalidation_risks": list(decision.invalidation_risks or []),
                        "missing_confirmations": list(decision.missing_confirmations or []),
                        "suggested_risk_multiplier": float(decision.suggested_risk_multiplier),
                        "model_version": str(decision.model_version or AI_GATE_MODEL_VERSION),
                        "decision_id": str(decision.decision_id or ""),
                        "ai_score_threshold": float(decision.ai_score_threshold),
                        "ai_threshold_source": str(decision.ai_threshold_source or ""),
                        "global_ai_score_as_hard_floor": bool(decision.global_ai_score_as_hard_floor),
                        "ai_threshold_passed": bool(decision.ai_threshold_passed),
                        "ai_reject_reason": str(decision.ai_reject_reason or ""),
                        "target_arbitration": decision.target_arbitration or {},
                        "chosen_target_model": str(decision.chosen_target_model or ""),
                        "chosen_tp1": float(decision.chosen_tp1 or 0.0),
                        "chosen_tp2": float(decision.chosen_tp2 or 0.0),
                        "chosen_rr1": float(decision.chosen_rr1 or 0.0),
                        "chosen_rr2": float(decision.chosen_rr2 or 0.0),
                        "rejected_target_models": list(decision.rejected_target_models or []),
                        "target_blocker_kind": str(decision.target_blocker_kind or ""),
                        "target_blocker_severity": float(decision.target_blocker_severity if decision.target_blocker_severity is not None else -1.0),
                        "target_blocker_class": str(decision.target_blocker_class or ""),
                        "target_blocker_is_trade_killer": bool(decision.target_blocker_is_trade_killer),
                        "target_decision_reason": str(decision.target_decision_reason or ""),
                        "target_blocker_severity_present": bool(decision.target_blocker_severity_present),
                        "target_blocker_class_present": bool(decision.target_blocker_class_present),
                        "target_blocker_is_trade_killer_present": bool(decision.target_blocker_is_trade_killer_present),
                        "target_decision_reason_present": bool(decision.target_decision_reason_present),
                        "why_not_liquidity_target": str(decision.why_not_liquidity_target or ""),
                        "why_not_partial_before_obstacle": str(decision.why_not_partial_before_obstacle or ""),
                        "why_not_capped_before_obstacle": str(decision.why_not_capped_before_obstacle or ""),
                        "why_not_synthetic_fallback": str(decision.why_not_synthetic_fallback or ""),
                        "target_arbitration_schema_version": str(decision.target_arbitration_schema_version or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
                        "prompt_contract_version": str(decision.prompt_contract_version or AI_PROMPT_CONTRACT_VERSION),
                        "target_comparison": target_comparison,
                        "target_comparison_json": str(decision.target_comparison_json or "{}"),
                    },
                }
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            except Exception as exc:
                log(f"[ai_gate] ai_decision_cache_store_failed error={exc}")


AI_DECISION_CACHE = AIDecisionCache(AI_CONFIG.decision_cache_file, AI_CONFIG.decision_cache_ttl_sec)

def _live_payload_requires_runtime_inputs(payload: Dict[str, Any]) -> bool:
    return AI_CONFIG.require_runtime_inputs_live and _is_live_payload(payload)

def _missing_critical_runtime_inputs(payload: Dict[str, Any]) -> list[str]:
    runtime_inputs = _runtime_inputs(payload)
    missing = [key for key in CRITICAL_RUNTIME_INPUT_KEYS if key not in runtime_inputs]
    if not payload.get("runtime_input_hash"):
        missing.append("payload.runtime_input_hash")
    return missing


def _entry_stop_target_values(item: Dict[str, Any], payload: Dict[str, Any]) -> tuple[float, float, float, bool]:
    plan = _as_dict(payload.get("plan"))
    merged = {**plan, **item}
    entry = _floatish(_get_any(merged, ["entry_est", "entry"], 0.0), 0.0)
    sl = _floatish(_get_any(merged, ["sl", "stop_loss", "stop"], 0.0), 0.0)
    tp2 = _floatish(_get_any(merged, ["tp2", "tp", "final_tp"], 0.0), 0.0)
    is_buy = _boolish(_get_any(merged, ["is_buy"], payload.get("is_buy")), False)
    return entry, sl, tp2, is_buy


def _target_already_reached(item: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    plan = _as_dict(payload.get("plan"))
    merged = {**plan, **item}
    if _boolish(_get_any(merged, ["target_already_reached"], payload.get("target_already_reached")), False):
        return True
    entry, _sl, tp2, is_buy = _entry_stop_target_values(item, payload)
    if entry <= 0.0 or tp2 <= 0.0:
        return False
    mkt = _as_dict(payload.get("mkt"))
    meta = _as_dict(payload.get("snapshot_metadata"))
    bid = _floatish(_get_any(mkt, ["bid"], _get_any(meta, ["bid"], payload.get("bid"))), 0.0)
    ask = _floatish(_get_any(mkt, ["ask"], _get_any(meta, ["ask"], payload.get("ask"))), 0.0)
    if is_buy and bid > 0.0 and bid >= tp2:
        return True
    if (not is_buy) and ask > 0.0 and ask <= tp2:
        return True
    return False


def _payload_hard_block_reason(payload: Dict[str, Any]) -> str:
    runtime_inputs = _runtime_inputs(payload)
    po3 = _as_dict(payload.get("po3"))
    mkt = _as_dict(payload.get("mkt"))
    positions = _as_dict(payload.get("positions")) or _as_dict(payload.get("position_state"))
    risk = _as_dict(payload.get("risk")) or _as_dict(payload.get("portfolio"))

    if _boolish(runtime_inputs.get("require_displacement"), False):
        has_disp = _boolish(_get_any(po3, ["has_displacement", "has_disp"], payload.get("has_displacement")), False)
        if not has_disp:
            return "no_displacement_required"

    spread_valid = _get_any(mkt, ["spread_valid"], payload.get("spread_valid"))
    if spread_valid is not None and not _boolish(spread_valid, True):
        return "invalid_spread"
    spread_r = _get_any(mkt, ["spread_r"], payload.get("spread_r"))
    if spread_r is not None and _floatish(spread_r, 0.0) < 0.0:
        return "invalid_spread"

    if _boolish(_get_any(positions, ["max_open_positions_reached"], payload.get("max_open_positions_reached")), False):
        return "max_open_positions_reached"
    open_count = _floatish(_get_any(positions, ["open_positions", "open_positions_count"], payload.get("open_positions_count")), -1.0)
    max_count = _floatish(_get_any(positions, ["max_open_positions"], runtime_inputs.get("max_open_positions")), 0.0)
    if max_count > 0.0 and open_count >= max_count:
        return "max_open_positions_reached"
    if _boolish(_get_any(positions, ["duplicate_symbol_block", "symbol_position_blocked"], payload.get("duplicate_symbol_block")), False):
        return "duplicate_symbol_position_block"
    if _boolish(_get_any(risk, ["portfolio_risk_cap_reached", "risk_cap_reached"], payload.get("portfolio_risk_cap_reached")), False):
        return "portfolio_risk_cap_reached"
    return ""

def _candidate_family_group(item: Dict[str, Any]) -> str:
    text = " ".join(
        _norm_text(item.get(key))
        for key in ("setup_family", "setup_class", "entry_branch", "entry_model")
    )
    if "continuation" in text:
        return "continuation"
    if "range" in text:
        return "range"
    if "failed_breakout" in text or "failed breakout" in text:
        return "failed_breakout"
    if "edge" in text:
        return "edge"
    if "reversal" in text or "full_po3" in text:
        return "reversal"
    return "unknown"

def _candidate_fvg_state(item: Dict[str, Any]) -> tuple[bool, bool]:
    state_text = " ".join(
        _norm_text(item.get(key))
        for key in (
            "fvg_execution_class",
            "fvg_zone_execution_class",
            "fvg_mitigation_state",
            "mitigation_state",
        )
    )
    stale = "stale" in state_text
    touched = (
        _boolish(item.get("fvg_touched"), False)
        or _boolish(item.get("fvg_mid_mitigated"), False)
        or "touched" in state_text
        or "mid_mitigated" in state_text
    )
    return stale, touched

def _candidate_hard_block_reason(item: Dict[str, Any], payload: Dict[str, Any]) -> str:
    runtime_inputs = _runtime_inputs(payload)
    po3 = _as_dict(payload.get("po3"))
    plan = _as_dict(payload.get("plan"))
    merged = {**plan, **item}

    if _boolish(runtime_inputs.get("trade_only_killzones"), False):
        in_killzone = _get_any(merged, ["in_killzone"], _get_any(po3, ["in_killzone"], payload.get("in_killzone")))
        if not _boolish(in_killzone, False):
            return "trade_only_killzone_block"

    family_group = _candidate_family_group(merged)
    branch = _norm_text(merged.get("entry_branch") or merged.get("entry_model"))
    family = _norm_text(merged.get("setup_family"))
    stale, touched = _candidate_fvg_state(merged)

    if _boolish(runtime_inputs.get("suppress_micro_bisi_sibi_edge"), False):
        if "micro_bisi" in family or branch == "fvg_edge":
            return "suppressed_micro_bisi_sibi_edge"
    if _boolish(runtime_inputs.get("suppress_stale_fvg_branches"), False) and stale:
        return "suppressed_stale_fvg_branch"
    if family_group == "continuation":
        if _boolish(runtime_inputs.get("suppress_continuation_stale_fvg"), False) and stale:
            return "suppressed_continuation_stale_fvg"
        if _boolish(runtime_inputs.get("suppress_continuation_touched_fvg"), False) and touched:
            return "suppressed_continuation_touched_fvg"
        if _boolish(runtime_inputs.get("suppress_touched_continuation_unless_retested"), False) and touched:
            retest = max(
                _floatish(merged.get("retest_score"), 0.0),
                _floatish(merged.get("retest_quality_score"), 0.0),
            )
            if retest < 6.0:
                return "suppressed_touched_continuation_not_retested"

    target_source = _norm_text(_get_any(merged, ["target_source"], payload.get("target_source")))
    target_model = _norm_text(_get_any(merged, ["target_model", "tp_model", "chosen_target_model"], payload.get("target_model")))
    obstacle_kind = _norm_text(_get_any(merged, ["obstacle_kind"], payload.get("obstacle_kind")))
    crossed_opposing_obstacle = "crossed" in obstacle_kind and ("opposing" in obstacle_kind or "imbalance" in obstacle_kind)
    synthetic_target = (
        "synthetic_rr_fallback" in target_source
        or "synthetic_rr_fallback" in target_model
        or target_source == "ai_selected_synthetic_rr_fallback"
    )
    if crossed_opposing_obstacle and _boolish(runtime_inputs.get("hard_reject_crossed_obstacle_target"), False):
        return "synthetic_fallback_crossed_obstacle_blocked"
    if _boolish(runtime_inputs.get("reject_synthetic_fallback_after_crossed_obstacle"), True):
        if synthetic_target and crossed_opposing_obstacle:
            if _target_arbitration_required(payload, merged) and _has_real_liquidity_target_candidate(payload, merged):
                return ""
            return "synthetic_fallback_crossed_obstacle_blocked"

    total_cost_r = (
        _floatish(_get_any(merged, ["execution_cost_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["slippage_r"], 0.0), 0.0)
        + _floatish(_get_any(merged, ["commission_r"], 0.0), 0.0)
    )
    reject_cost = _floatish(runtime_inputs.get("execution_reject_cost_r"), 0.0)
    micro_cost_cap = _floatish(runtime_inputs.get("micro_scalp_max_cost_frac_of_planned_r"), 0.0)
    if reject_cost > 0.0 and total_cost_r >= reject_cost:
        return "execution_cost_r_too_high"
    if family_group in {"continuation", "edge"} and micro_cost_cap > 0.0 and total_cost_r >= micro_cost_cap:
        return "micro_scalp_cost_exceeds_10pct_r"

    entry, sl, tp2, is_buy = _entry_stop_target_values(item, payload)
    rr2 = _floatish(_get_any(merged, ["rr2"], 0.0), 0.0)
    min_live_rr2 = _floatish(runtime_inputs.get("min_live_rr2"), 0.0)
    if entry <= 0.0:
        return "no_valid_entry"
    if sl <= 0.0 or sl == entry:
        return "no_valid_stop"
    if is_buy and sl >= entry:
        return "no_valid_stop"
    if (not is_buy) and sl <= entry:
        return "no_valid_stop"
    if tp2 <= 0.0:
        return "invalid_target"
    if is_buy and tp2 <= entry:
        return "invalid_target"
    if (not is_buy) and tp2 >= entry:
        return "invalid_target"
    if rr2 <= 0.0:
        risk_dist = abs(entry - sl)
        rr2 = abs(tp2 - entry) / risk_dist if risk_dist > 0.0 else 0.0
    if rr2 <= 0.0 or (min_live_rr2 > 0.0 and rr2 < min_live_rr2):
        return "invalid_rr"
    if _target_already_reached(item, payload):
        return "target_already_reached"
    return ""

def hard_pre_gate(payload: Dict[str, Any], runtime_config: AIGateRuntimeConfig = AI_CONFIG, best_index: int = 0) -> Decision:
    _inc_counter("hard_pre_gate_checked")
    if _live_payload_requires_runtime_inputs(payload):
        missing = _missing_critical_runtime_inputs(payload)
        if missing and runtime_config.reject_on_missing_runtime_inputs_live:
            _inc_counter("hard_pre_gate_rejected")
            _inc_counter("ai_calls_skipped_by_hard_gate")
            if runtime_config.log_skipped_calls:
                log(f"[ai_gate] runtime_inputs_missing_live_reject missing={','.join(missing)}")
            return Decision(
                allow=False,
                score=0.0,
                chosen_index=best_index,
                confidence=1.0,
                reasons={
                    "decision_source": "hard_pre_gate",
                    "rejection_codes": ["runtime_inputs_missing_live_reject"],
                    "missing_runtime_inputs": missing,
                },
                decision_source="hard_pre_gate",
                rejection_codes=["runtime_inputs_missing_live_reject"],
                narrative_state="runtime_inputs_missing",
                invalidation_risks=["runtime_input_snapshot_missing"],
                missing_confirmations=missing,
                suggested_risk_multiplier=0.0,
                model_version=AI_GATE_MODEL_VERSION,
            )

    payload_reason = _payload_hard_block_reason(payload)
    if payload_reason:
        _inc_counter("hard_pre_gate_rejected")
        _inc_counter("ai_calls_skipped_by_hard_gate")
        if runtime_config.log_skipped_calls:
            log(f"[ai_gate] hard_pre_gate_rejected reason={payload_reason} skipped_openai={AI_GATE_COUNTERS.get('ai_calls_skipped_by_hard_gate', 0)}")
        return Decision(
            allow=False,
            score=0.0,
            chosen_index=best_index,
            confidence=1.0,
            reasons={
                "decision_source": "hard_pre_gate",
                "rejection_codes": [payload_reason],
            },
            decision_source="hard_pre_gate",
            rejection_codes=[payload_reason],
            narrative_state="hard_pre_gate_rejected",
            invalidation_risks=["objective_pre_trade_gate_failed"],
            missing_confirmations=[],
            suggested_risk_multiplier=0.0,
            model_version=AI_GATE_MODEL_VERSION,
        )

    cands = payload.get("candidates") or []
    items = [cand for cand in cands if isinstance(cand, dict)] if isinstance(cands, list) else []
    if not items:
        plan = _as_dict(payload.get("plan"))
        items = [plan] if plan else []
    if not items:
        return None

    reasons: list[str] = []
    for item in items:
        reason = _candidate_hard_block_reason(item, payload)
        if not reason:
            return Decision(
                allow=True,
                score=0.0,
                chosen_index=best_index,
                confidence=1.0,
                reasons={"decision_source": "hard_pre_gate_pass"},
                decision_source="hard_pre_gate_pass",
                rejection_codes=[],
                narrative_state="hard_pre_gate_pass",
                invalidation_risks=[],
                missing_confirmations=[],
                suggested_risk_multiplier=1.0,
                model_version=AI_GATE_MODEL_VERSION,
            )
        reasons.append(reason)

    code = reasons[0] if reasons else "hard_pre_gate_reject"
    _inc_counter("hard_pre_gate_rejected")
    _inc_counter("ai_calls_skipped_by_hard_gate")
    if runtime_config.log_skipped_calls:
        log(f"[ai_gate] hard_pre_gate_rejected reason={code} skipped_openai={AI_GATE_COUNTERS.get('ai_calls_skipped_by_hard_gate', 0)}")
    return Decision(
        allow=False,
        score=0.0,
        chosen_index=best_index,
        confidence=1.0,
        reasons={
            "decision_source": "hard_pre_gate",
            "rejection_codes": [code],
            "candidate_reject_reasons": reasons,
        },
        decision_source="hard_pre_gate",
        rejection_codes=[code],
        narrative_state="hard_pre_gate_rejected",
        invalidation_risks=["objective_pre_trade_gate_failed"],
        missing_confirmations=[],
        suggested_risk_multiplier=0.0,
        model_version=AI_GATE_MODEL_VERSION,
    )


def _hard_pretrade_decision(payload: Dict[str, Any], best_index: int) -> Decision | None:
    if not AI_CONFIG.hard_pre_gate_before_openai:
        return None
    decision = hard_pre_gate(payload, AI_CONFIG, best_index)
    if decision.allow and decision.decision_source == "hard_pre_gate_pass":
        return None
    return decision

def _po3_state_name(value: Any) -> str:
    state = str(value or "").strip().upper()
    if not state:
        return ""
    if state in {"CONFIRMED", "DEVELOPING", "EXPIRED", "INVALIDATED"}:
        return f"PO3_{state}"
    return state

PO3_TERMINAL_REJECT_STATES = {"PO3_INVALIDATED", "PO3_EXPIRED"}
PO3_ACTIONABLE_AUDIT_STATES = {
    "PO3_SWEEP_CONFIRMED",
    "PO3_DISPLACEMENT_CONFIRMED",
    "PO3_STRUCTURE_CONFIRMED",
    "PO3_FVG_CONFIRMED",
    "PO3_ENTRY_WAITING",
    "PO3_CONFIRMED",
    "PO3_DEVELOPING",
}

def _candidate_score(cand: Dict[str, Any]) -> float:
    score = 0.0
    try:
        setup_score = max(0.0, float(cand.get("setup_score", 0.0)))
        score += min(10.0, setup_score / 12.5) * 1.0
    except Exception:
        pass
    try:
        fvg_score = float(cand.get("fvg_score", 0.0))
        if 0.0 <= fvg_score <= 1.0:
            fvg_score *= 10.0
        score += min(10.0, max(0.0, fvg_score)) * 0.7
    except Exception:
        pass
    for key, weight in (
        ("origin_score", 0.5),
        ("cleanliness_score", 0.4),
        ("nesting_score", 0.35),
        ("htf_overlap_score", 0.35),
        ("retest_score", 0.25),
    ):
        try:
            value = min(10.0, max(0.0, float(cand.get(key, 0.0))))
            score += value * weight
        except Exception:
            pass
    return score

def _best_candidate(cands: Any) -> Tuple[Dict[str, Any], int, float]:
    best_candidate: Dict[str, Any] = {}
    best_index = 0
    best_score = -1e18
    if not isinstance(cands, list):
        return best_candidate, best_index, best_score
    for idx, cand in enumerate(cands):
        if not isinstance(cand, dict):
            continue
        score_val = _candidate_score(cand)
        if score_val > best_score:
            best_candidate = cand
            best_index = int(_get_any(cand, ["candidate_index"], idx))
            best_score = score_val
    if best_score < -1e10:
        best_score = 0.0
    return best_candidate, best_index, best_score


def _setup_family_from_payload(payload: Dict[str, Any], plan: Dict[str, Any] | None = None, cands: Any = None) -> str:
    plan = plan if isinstance(plan, dict) else _as_dict(payload.get("plan"))
    if cands is None:
        cands = payload.get("candidates") or []
    best_candidate, _, _ = _best_candidate(cands)
    po3 = _as_dict(payload.get("po3"))
    family_sources = [
        _get_any(best_candidate, ["setup_family", "setup_class", "final_setup_class", "structure_type", "entry_branch", "entry_model"]),
        _get_any(plan, ["setup_family", "setup_class", "entry_branch", "entry_model", "target_model"]),
        _get_any(po3, ["final_setup_class", "structure_type", "htf_structure_type", "ltf_structure_type", "po3_scope"]),
        _get_any(payload, ["setup_family", "setup_class", "entry_branch", "entry_model", "target_model", "po3_scope"]),
    ]
    hint = " ".join(str(value or "").strip().lower() for value in family_sources if value not in (None, ""))

    if "failed_breakout" in hint or "failed-breakout" in hint:
        return "micro_failed_breakout_reclaim"
    if "continuation" in hint or "impulse" in hint:
        return "micro_continuation_fvg"
    if "range_reentry" in hint or "session_reentry" in hint or "range_mid" in hint:
        return "micro_range_reentry"
    if "bisi" in hint or "sibi" in hint:
        return "micro_bisi_sibi_edge"
    if "fvg_edge" in hint or "breaker_retest" in hint:
        po3_scope = str(_get_any(po3, ["po3_scope"], _get_any(payload, ["po3_scope"], "")) or "").lower()
        if "micro" in po3_scope or "intraday" in po3_scope:
            return "micro_bisi_sibi_edge"

    family = str(_get_any(best_candidate, ["setup_family"], _get_any(plan, ["setup_family"], _get_any(payload, ["setup_family"], ""))) or "").strip().lower()
    return family


def _family_requires_sweep_story(setup_family: str, payload: Dict[str, Any] | None = None) -> bool:
    family = str(setup_family or "").strip().lower()
    if family in {
        "micro_continuation_fvg",
        "micro_failed_breakout_reclaim",
        "micro_range_reentry",
        "range_reentry",
        "session_reentry",
    }:
        return False
    if payload is not None:
        po3 = _as_dict(payload.get("po3"))
        plan = _as_dict(payload.get("plan"))
        cands = payload.get("candidates") or []
        best_candidate, _, _ = _best_candidate(cands)
        has_sweep = bool(po3.get("has_sweep"))
        t_sweep = _get_any(po3, ["t_sweep", "sweep_time"], _get_any(payload, ["t_sweep", "source_t_sweep"]))
        flow_hint = " ".join(
            str(value or "").strip().lower()
            for value in (
                _get_any(best_candidate, ["entry_branch", "entry_model", "setup_class", "structure_type"]),
                _get_any(plan, ["entry_branch", "entry_model", "setup_class", "target_model"]),
                _get_any(po3, ["po3_scope", "final_setup_class", "structure_type", "htf_structure_type", "ltf_structure_type"]),
                _get_any(payload, ["po3_scope", "entry_branch", "entry_model", "setup_class", "target_model"]),
            )
            if value not in (None, "")
        )
        if not has_sweep and not t_sweep and (
            "micro" in flow_hint
            or "intraday" in flow_hint
            or "continuation" in flow_hint
            or "failed_breakout" in flow_hint
            or "range_reentry" in flow_hint
            or "session_reentry" in flow_hint
        ):
            return False
    return family in {"", "full_po3_reversal", "full_po3_continuation", "micro_po3_reversal", "micro_bisi_sibi_edge"}

def _rule_score(payload: Dict[str, Any]) -> Tuple[float, str]:
    """
    Rule-based sanity score (0..10) used to calibrate the LLM output.
    This version is reweighted so "normal good" setups land ~4.5-7.0,
    and 8+ requires truly exceptional confluence.
    """
    po3 = _as_dict(payload.get("po3"))
    plan = _as_dict(payload.get("plan"))
    fvg = _as_dict(payload.get("fvg"))
    regime = _as_dict(payload.get("regime"))
    watchlist = _as_dict(payload.get("watchlist"))
    cands = payload.get("candidates") or []
    is_buy = bool(_get_any(payload, ["is_buy"], True))

    best_candidate, _, _ = _best_candidate(cands)

    t_sweep = _get_any(po3, ["t_sweep", "sweep_time"], _get_any(payload, ["t_sweep"]))
    t_disp = _get_any(po3, ["t_disp", "t_displacement", "disp_time"], _get_any(payload, ["t_disp"]))
    t_bos = _get_any(po3, ["t_bos", "bos_time"], _get_any(payload, ["t_bos"]))
    dr_high = _get_any(po3, ["dr_high", "dr_hi"], _get_any(payload, ["dr_high", "dr_hi"]))
    dr_low = _get_any(po3, ["dr_low", "dr_lo"], _get_any(payload, ["dr_low", "dr_lo"]))
    entry_est = _get_any(best_candidate, ["entry_est"], _get_any(plan, ["entry_est", "entry"], _get_any(payload, ["entry_est", "entry"])))
    entry_model = str(_get_any(best_candidate, ["entry_model"], _get_any(plan, ["entry_model"], _get_any(payload, ["entry_model"], ""))) or "")
    setup_family = _setup_family_from_payload(payload, plan, cands)
    micro_non_reversal = setup_family in {
        "micro_failed_breakout_reclaim",
        "micro_continuation_fvg",
        "micro_range_reentry",
        "micro_bisi_sibi_edge",
    }
    continuation_family = setup_family in {"micro_continuation_fvg", "full_po3_continuation"}
    failed_breakout_family = setup_family == "micro_failed_breakout_reclaim"
    full_po3_family = setup_family in {"full_po3_reversal", "full_po3_continuation", ""}
    requires_sweep_story = _family_requires_sweep_story(setup_family, payload)
    tp_model = str(_get_any(best_candidate, ["tp_model"], _get_any(plan, ["tp_model"], _get_any(payload, ["tp_model"], ""))) or "")
    sl = _get_any(best_candidate, ["sl"], _get_any(plan, ["sl", "stop_loss", "stop"], _get_any(payload, ["sl", "stop_loss", "stop"])))
    tp2 = _get_any(best_candidate, ["tp2"], _get_any(plan, ["tp2", "tp", "final_tp"], _get_any(payload, ["tp2", "tp", "final_tp"])))
    liquidity_target = _get_any(po3, ["liquidity_target"], _get_any(payload, ["liquidity_target"]))
    liquidity_kind = str(_get_any(po3, ["liquidity_kind"], _get_any(payload, ["liquidity_kind"], "")) or "")
    liquidity_cluster_count = _get_any(po3, ["liquidity_cluster_count"], _get_any(payload, ["liquidity_cluster_count"], 0))
    liquidity_rr = _get_any(best_candidate, ["liquidity_rr"], _get_any(plan, ["liquidity_rr"], _get_any(payload, ["liquidity_rr"])))
    session_name = str(_get_any(po3, ["session_name"], _get_any(payload, ["session_name"], "")) or "")
    in_killzone = bool(_get_any(po3, ["in_killzone"], _get_any(payload, ["in_killzone"], False)))
    htf_mss = bool(_get_any(po3, ["htf_mss"], _get_any(payload, ["htf_mss"], False)))
    htf_choch = bool(_get_any(po3, ["htf_choch"], _get_any(payload, ["htf_choch"], False)))
    ltf_bos = bool(_get_any(po3, ["ltf_bos"], _get_any(payload, ["ltf_bos"], False)))
    ltf_mss = bool(_get_any(po3, ["ltf_mss"], _get_any(payload, ["ltf_mss"], False)))
    ltf_choch = bool(_get_any(po3, ["ltf_choch"], _get_any(payload, ["ltf_choch"], False)))
    po3_scope = str(_get_any(po3, ["po3_scope"], _get_any(payload, ["po3_scope"], "")) or "")

    crit = []
    if not cands:
        crit.append("candidates")
    if requires_sweep_story and not t_sweep:
        crit.append("po3.t_sweep")
    if requires_sweep_story and (dr_high in (None, 0, "") or dr_low in (None, 0, "")):
        crit.append("po3.dr_high/dr_low")

    has_disp = bool(po3.get("has_displacement") or po3.get("has_disp"))
    has_sweep = bool(po3.get("has_sweep"))
    has_bos = bool(po3.get("has_bos"))
    po3_state = _po3_state_name(po3.get("po3_state") or payload.get("po3_state"))
    fvg_state = str(fvg.get("mitigation_state") or "").lower()
    fvg_exec = str(_get_any(best_candidate, ["fvg_zone_execution_class", "fvg_execution_class"], fvg.get("execution_class") or "") or "").lower()
    if po3_state in PO3_TERMINAL_REJECT_STATES and not micro_non_reversal:
        return 0.0, f"po3_state_{po3_state.lower()}"
    if fvg_exec in {"invalidated_fvg", "fully_mitigated_fvg", "entry_invalid_fvg"}:
        return 0.0, f"fvg_not_tradable_{fvg_exec}"
    liquidity_runner_models = {
        "liquidity_target",
        "weekly_liquidity",
        "daily_liquidity",
        "equal_high_low_liquidity",
        "session_liquidity",
        "session_expansion",
        "htf_liquidity",
        "narrative_liquidity",
        "trend_projection",
    }
    recognized_entry_models = {
        "fvg_mid",
        "fvg_upper",
        "fvg_lower",
        "fvg_edge",
        "bisi_sibi_edge",
        "b50_retrace",
        "breaker_retest",
        "ote_inside_fvg",
        "nested_fvg_edge",
        "nested_htf_ltf_fvg",
        "continuation_reentry",
        "htf_nested_fvg",
        "session_fvg_mid",
        "session_reentry",
        "range_reentry",
    }
    runner_entry_models = {
        "continuation_reentry",
        "htf_nested_fvg",
        "nested_fvg_edge",
        "nested_htf_ltf_fvg",
        "ote_inside_fvg",
        "breaker_retest",
        "range_reentry",
    }
    runner_liquidity_plan = tp_model in liquidity_runner_models
    structured_impulse = has_sweep and has_disp and has_bos
    runner_context = runner_liquidity_plan and (structured_impulse or entry_model in runner_entry_models)
    if has_disp and not t_disp:
        crit.append("po3.t_disp")
    if has_bos and not t_bos:
        crit.append("po3.t_bos")

    if entry_est in (None, 0, ""):
        crit.append("plan.entry_est")
    if sl in (None, 0, ""):
        crit.append("plan.sl")
    if tp2 in (None, 0, ""):
        crit.append("plan.tp2")

    if crit:
        return 0.0, "missing_critical=" + ",".join(crit)

    score = 1.35
    notes = []
    if po3_scope:
        notes.append(po3_scope)

    # PO3 structure (smaller weights, with real penalties)
    if po3_state == "PO3_CONFIRMED":
        score += 0.55
    elif po3_state == "PO3_ENTRY_WAITING":
        score += 0.25
        notes.append("po3_entry_waiting")
    elif po3_state in {"PO3_FVG_CONFIRMED", "PO3_STRUCTURE_CONFIRMED"}:
        score += 0.05
        notes.append("po3_pre_entry")
    elif po3_state == "PO3_DEVELOPING":
        score -= 1.25
        notes.append("po3_developing")
    elif not po3_state:
        score -= 0.5
        notes.append("po3_state_missing")

    if has_sweep:
        score += 0.9
    elif failed_breakout_family:
        score += 0.35
        notes.append("failed_breakout_reclaim")
    elif continuation_family and not full_po3_family:
        notes.append("continuation_no_sweep_required")
    else:
        score -= 1.5
        notes.append("no_sweep")

    if has_disp:
        score += 0.9
    else:
        score -= 1.2
        notes.append("no_displacement")

    if has_bos:
        score += 0.7
    elif (continuation_family and not full_po3_family) or failed_breakout_family:
        notes.append("micro_structure_flow")
    else:
        score -= 0.9
        notes.append("no_bos")

    if htf_mss:
        score += 0.35
    if htf_choch:
        score += 0.25
    if ltf_bos:
        score += 0.35
    if ltf_mss:
        score += 0.2
    if ltf_choch:
        score += 0.15

    try:
        ts = int(t_sweep or 0)
        td = int(t_disp or 0)
        tb = int(t_bos or 0)
        if has_disp and td > 0:
            if requires_sweep_story and ts > 0 and td <= ts:
                score -= 1.0
                notes.append("disp_not_after_sweep")
            else:
                score += 0.25
        if has_bos and tb > 0:
            anchor = td if td > 0 else ts
            if tb <= anchor:
                score -= 1.0
                notes.append("bos_not_after_disp")
            else:
                score += 0.3
    except Exception:
        pass

    # FVG quality: up to +2.2 (not +3.0)
    fvg_score = 0.0
    try:
        if best_candidate and "fvg_score" in best_candidate:
            fvg_score = float(best_candidate.get("fvg_score", 0.0))
        else:
            fvg_score = float(fvg.get("score", 0.0))
    except Exception:
        fvg_score = 0.0

    # accept 0..1 normalized, convert to 0..10
    if 0.0 <= fvg_score <= 1.0:
        fvg_score *= 10.0

    score += max(0.0, min(1.8, (fvg_score / 10.0) * 1.8))
    if fvg_state == "fully_mitigated":
        score -= 1.2
        notes.append("fvg_fully_mitigated")
    elif fvg_state == "mid_mitigated":
        score -= 0.2
    try:
        obstruction_score = float(_get_any(best_candidate, ["opposing_obstruction_score"], fvg.get("opposing_obstruction_score", 5.0)))
        if obstruction_score < 4.5:
            score -= 0.75
            notes.append("opposing_obstruction")
    except Exception:
        pass

    for key, cap in (
        ("origin_score", 0.50),
        ("cleanliness_score", 0.42),
        ("nesting_score", 0.34),
        ("htf_overlap_score", 0.34),
        ("retest_score", 0.24),
    ):
        try:
            value = float(_get_any(best_candidate, [key], 0.0))
            score += min(cap, max(0.0, value) / 10.0 * cap)
        except Exception:
            pass

    # RR sanity (prefer 1.2..4.5, penalize extremes)
    rr2 = _get_any(best_candidate, ["effective_rr2", "rr2"], _get_any(plan, ["effective_rr2", "rr2"], _get_any(payload, ["effective_rr2", "rr2"])))
    if rr2 is None:
        try:
            entry = float(entry_est)
            stop = float(sl)
            target = float(tp2)
            r = abs(entry - stop)
            rr2 = abs(target - entry) / r if r > 0 else None
        except Exception:
            rr2 = None

    if rr2 is not None:
        try:
            rr2 = float(rr2)
            if runner_context:
                if rr2 < 0.8:
                    score -= 1.2; notes.append("rr_too_small")
                elif rr2 < 1.2:
                    score -= 0.3; notes.append("rr_small")
                elif rr2 <= 6.0:
                    score += 0.9
                elif rr2 <= 15.0:
                    score += 0.35; notes.append("rr_runner")
                elif rr2 <= 40.0:
                    score += 0.05; notes.append("rr_runner")
                else:
                    notes.append("rr_runner")
            else:
                if rr2 < 0.8:
                    score -= 1.2; notes.append("rr_too_small")
                elif rr2 < 1.2:
                    score -= 0.3; notes.append("rr_small")
                elif rr2 <= 4.5:
                    score += 0.9
                elif rr2 <= 7.5:
                    score += 0.2; notes.append("rr_high")
                elif rr2 <= 12.0:
                    score -= 0.9; notes.append("rr_very_high")
                elif rr2 <= 25.0:
                    score -= 1.6; notes.append("rr_extreme")
                else:
                    score -= 2.4; notes.append("rr_unrealistic")
        except Exception:
            pass

    try:
        cost_r = float(_get_any(best_candidate, ["execution_cost_r"], _get_any(plan, ["execution_cost_r"], 0.0)))
        slip_r = float(_get_any(best_candidate, ["slippage_r"], _get_any(plan, ["slippage_r"], 0.0)) or 0.0)
        comm_r = float(_get_any(best_candidate, ["commission_r"], _get_any(plan, ["commission_r"], 0.0)) or 0.0)
        total_cost_r = cost_r + slip_r + comm_r
        if total_cost_r >= 0.35:
            score -= 1.2
            notes.append("cost_edge_eroded")
        elif total_cost_r >= 0.22:
            score -= 0.45
            notes.append("cost_heavy")
    except Exception:
        pass

    try:
        net_expected_r = float(_get_any(best_candidate, ["net_expected_r", "expected_value_r"], _get_any(plan, ["net_expected_r", "expected_value_r"], 0.0)))
        if net_expected_r < 0.05:
            score -= 1.4
            notes.append("net_expected_r_too_low")
        elif net_expected_r >= 0.35:
            score += 0.25
    except Exception:
        pass

    # Regime alignment: volatility, signed slope, and trend strength should agree with trade direction.
    try:
        atr_pct = float(_get_any(regime, ["atr_pct"], _get_any(payload, ["atr_pct"], 0.0)))
        if atr_pct <= 0:
            score -= 0.25
            notes.append("atr_pct_missing")
        elif atr_pct > 6.0:
            score -= 0.35
            notes.append("atr_pct_very_high")
        else:
            score += 0.15
    except Exception:
        pass

    try:
        trend_strength = float(_get_any(regime, ["trend_strength"], _get_any(payload, ["trend_strength"], 0.0)))
        if trend_strength >= 0.75:
            score += 0.3
        elif trend_strength < 0.45:
            score -= 0.35
            notes.append("weak_trend")
    except Exception:
        pass

    try:
        trend_slope_pct = float(_get_any(regime, ["trend_slope_pct"], _get_any(payload, ["trend_slope_pct"], 0.0)))
        if is_buy:
            if trend_slope_pct > 0:
                score += 0.45
            else:
                score -= 0.9
                notes.append("trend_against_long")
        else:
            if trend_slope_pct < 0:
                score += 0.45
            else:
                score -= 0.9
                notes.append("trend_against_short")
    except Exception:
        pass

    try:
        adx_value = float(_get_any(regime, ["adx_value"], _get_any(payload, ["adx_value"], 0.0)))
        if adx_value <= 0:
            notes.append("adx_missing")
        elif adx_value < 18.0:
            score -= 0.6
            notes.append("adx_too_low")
        elif adx_value <= 45.0:
            score += 0.35
        else:
            score += 0.15
    except Exception:
        pass

    try:
        adr_pct = float(_get_any(regime, ["adr_pct"], _get_any(payload, ["adr_pct"], 0.0)))
        if adr_pct <= 0:
            notes.append("adr_missing")
        elif adr_pct < 0.002:
            score -= 0.55
            notes.append("adr_too_small")
        elif adr_pct <= 0.05:
            score += 0.2
    except Exception:
        pass

    try:
        session_vol_ratio = float(_get_any(regime, ["session_vol_ratio"], _get_any(payload, ["session_vol_ratio"], 0.0)))
        if session_name != "OFF_HOURS":
            if session_vol_ratio <= 0:
                notes.append("session_vol_missing")
            elif session_vol_ratio < 0.12:
                score -= 0.6
                notes.append("session_underdeveloped")
            elif session_vol_ratio <= 0.75:
                score += 0.3
            else:
                score += 0.1
    except Exception:
        pass

    try:
        vwap_dist_atr = float(_get_any(regime, ["vwap_dist_atr"], _get_any(payload, ["vwap_dist_atr"], 0.0)))
        if vwap_dist_atr > 0:
            if runner_context:
                if vwap_dist_atr > 8.0:
                    score -= 0.35
                    notes.append("vwap_extended_runner")
                elif vwap_dist_atr > 4.0:
                    score -= 0.1
                    notes.append("vwap_extended_runner")
                elif vwap_dist_atr <= 1.1:
                    score += 0.3
            else:
                if vwap_dist_atr > 4.0:
                    score -= 1.8
                    notes.append("too_far_from_vwap")
                elif vwap_dist_atr > 2.0:
                    score -= 1.1
                    notes.append("too_far_from_vwap")
                elif vwap_dist_atr <= 0.9:
                    score += 0.3
    except Exception:
        pass

    try:
        expansion_score = float(_get_any(regime, ["expansion_score"], _get_any(payload, ["expansion_score"], 0.0)))
        compression_score = float(_get_any(regime, ["compression_score"], _get_any(payload, ["compression_score"], 0.0)))
        if expansion_score > 0:
            if expansion_score < 0.7:
                score -= 0.55
                notes.append("compression_regime")
            elif expansion_score <= 2.4:
                score += 0.3
            else:
                score -= 0.7
                notes.append("shock_expansion")
        elif compression_score > 0.75:
            score -= 0.35
            notes.append("compressed_state")
    except Exception:
        pass

    try:
        news_risk = float(_get_any(regime, ["news_risk"], _get_any(payload, ["news_risk"], 0.0)))
        if news_risk >= 1.0:
            score -= 1.3
            notes.append("news_risk_high")
        elif news_risk > 0:
            score -= min(0.8, 0.8 * news_risk)
    except Exception:
        pass

    if in_killzone:
        score += 0.35
    elif session_name == "OFF_HOURS":
        score -= 0.45
        notes.append("off_hours")
    elif session_name:
        score += 0.1

    if entry_model == "fvg_mid":
        score += 0.15
    elif entry_model in {"fvg_upper", "fvg_lower", "fvg_edge", "bisi_sibi_edge"}:
        score += 0.05
    elif entry_model in {"continuation_reentry", "htf_nested_fvg", "nested_fvg_edge", "nested_htf_ltf_fvg", "session_fvg_mid", "session_reentry"}:
        score += 0.18
    elif entry_model in {"ote_inside_fvg", "b50_retrace", "breaker_retest", "range_reentry"}:
        score += 0.12
    elif entry_model in recognized_entry_models:
        score += 0.05
    else:
        notes.append("entry_model_unknown")

    if tp_model == "liquidity_target":
        score += 0.25
    elif tp_model in liquidity_runner_models:
        score += 0.22
    elif (
        tp_model not in {"", "fib_extension", "fixed_rr", "synthetic_rr_fallback", "target_arbitration_pending", "current_plan"}
        and not tp_model.startswith("capped_before_")
        and not tp_model.startswith("runner_downgrade_")
    ):
        notes.append("tp_model_unknown")

    if liquidity_kind in {"equal_high_cluster", "equal_low_cluster"}:
        score += 0.25
    elif liquidity_kind in {"prev_day_high", "prev_day_low", "prev_week_high", "prev_week_low"}:
        score += 0.35
    try:
        cluster_count = int(liquidity_cluster_count or 0)
        if cluster_count >= 2:
            score += min(0.35, 0.1 * cluster_count)
    except Exception:
        pass

    if liquidity_rr is None and liquidity_target not in (None, 0, ""):
        try:
            entry = float(entry_est)
            stop = float(sl)
            liq = float(liquidity_target)
            risk = abs(entry - stop)
            reward = (liq - entry) if is_buy else (entry - liq)
            liquidity_rr = (reward / risk) if risk > 0 and reward > 0 else 0.0
        except Exception:
            liquidity_rr = None

    if liquidity_rr is not None:
        try:
            liquidity_rr = float(liquidity_rr)
            if runner_context:
                if liquidity_rr <= 0.75:
                    score -= 0.7
                    notes.append("liquidity_target_weak")
                elif liquidity_rr <= 8.0:
                    score += 0.45
                elif liquidity_rr <= 20.0:
                    score += 0.2
                    notes.append("liquidity_runner")
                else:
                    score += 0.05
                    notes.append("liquidity_runner")
            else:
                if liquidity_rr <= 0.75:
                    score -= 0.7
                    notes.append("liquidity_target_weak")
                elif liquidity_rr <= 5.0:
                    score += 0.45
                elif liquidity_rr <= 8.0:
                    score += 0.15
                elif liquidity_rr <= 12.0:
                    score -= 0.15
                    notes.append("liquidity_target_stretched")
                else:
                    score -= 0.75
                    notes.append("liquidity_target_far")
        except Exception:
            pass

    try:
        setup_score = float(_get_any(best_candidate, ["setup_score"], _get_any(plan, ["setup_score"], _get_any(payload, ["setup_score"], 0.0))))
        if setup_score > 0:
            score += min(0.35, setup_score / 220.0)
    except Exception:
        pass

    if bool(watchlist.get("armed")):
        score += 0.1

    if score > 7.2:
        score = 7.2 + (score - 7.2) * 0.55

    score = max(0.0, min(10.0, score))
    return score, ";".join(notes)


def _fallback_confidence(rule_score: float, rule_notes: str, error_text: str) -> float:
    notes = {note for note in str(rule_notes or "").split(";") if note}
    confidence = 0.30 + min(0.24, max(0.0, rule_score - 3.0) * 0.05)
    if "rr_extreme" in notes or "rr_unrealistic" in notes:
        confidence -= 0.08
    if "too_far_from_vwap" in notes:
        confidence -= 0.07
    if "liquidity_target_far" in notes or "liquidity_target_stretched" in notes:
        confidence -= 0.04
    if "weak_trend" in notes or "trend_against_long" in notes or "trend_against_short" in notes:
        confidence -= 0.06
    if "no_sweep" in notes or "no_displacement" in notes or "no_bos" in notes:
        confidence -= 0.10
    if error_text:
        confidence -= 0.04
    return max(0.25, min(0.74, round(confidence, 2)))

def _runtime_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_mode(payload: Dict[str, Any]) -> tuple[str, bool, bool, bool, float]:
    runtime = _as_dict(payload.get("runtime"))
    mode = str(runtime.get("account_trade_mode") or os.getenv("AI_GATE_ACCOUNT_MODE", "") or "").strip().lower()
    fail_closed = _runtime_bool(runtime.get("live_fail_closed_on_ai_failure"), os.getenv("AI_LIVE_FAIL_CLOSED_ON_FAILURE", "true").lower() in {"1", "true", "yes", "on"})
    allow_rule_fallback = _runtime_bool(runtime.get("allow_rule_only_fallback"), os.getenv("AI_ALLOW_RULE_ONLY_FALLBACK", "true").lower() in {"1", "true", "yes", "on"})
    allow_rule_live = _runtime_bool(runtime.get("allow_rule_only_live"), os.getenv("AI_ALLOW_RULE_ONLY_LIVE", "false").lower() in {"1", "true", "yes", "on"})
    try:
        fallback_mult = float(runtime.get("fallback_risk_multiplier", os.getenv("AI_FALLBACK_RISK_MULTIPLIER", "0.0")))
    except Exception:
        fallback_mult = 0.0
    return mode, fail_closed, allow_rule_live, allow_rule_fallback, max(0.0, min(1.0, fallback_mult))


def _runtime_min_confidence(payload: Dict[str, Any]) -> float:
    runtime = _as_dict(payload.get("runtime"))
    raw = runtime.get("min_ai_confidence", AI_CONFIG.min_confidence)
    try:
        return max(0.0, min(1.0, float(raw)))
    except Exception:
        return AI_CONFIG.min_confidence


def _threshold_identity(payload: Dict[str, Any], chosen_index: int | None = None) -> tuple[str, str, str]:
    plan = _as_dict(payload.get("plan"))
    cand: Dict[str, Any] = {}
    cands = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    if chosen_index is not None and 0 <= chosen_index < len(cands) and isinstance(cands[chosen_index], dict):
        cand = cands[chosen_index]
    merged = {**plan, **cand}
    family = _norm_text(_get_any(merged, ["setup_family"], payload.get("setup_family")))
    setup_class = _norm_text(_get_any(merged, ["setup_class"], payload.get("setup_class")))
    branch = _norm_text(_get_any(merged, ["entry_branch", "entry_model"], payload.get("entry_branch")))
    return family, setup_class, branch


def effective_ai_score_threshold(payload: Dict[str, Any], chosen_index: int | None = None) -> tuple[float, str]:
    runtime = _runtime_inputs(payload)
    family, setup_class, branch = _threshold_identity(payload, chosen_index)

    fallback = _floatish(runtime.get("min_ai_score_trend"), 7.0)
    threshold = fallback
    source = "min_ai_score_trend"

    if family in {"full_po3", "full_po3_reversal", "full_po3_continuation"} or "full_po3" in setup_class:
        threshold = _floatish(runtime.get("ai_score_full_po3"), fallback)
        source = "ai_score_full_po3"
    elif (
        family in {"micro_po3", "micro_po3_reversal", "micro_bisi_sibi_edge"}
        or "micro_po3" in setup_class
        or "micro_bisi_sibi" in setup_class
    ):
        threshold = _floatish(runtime.get("ai_score_micro_po3"), fallback)
        source = "ai_score_micro_po3"
    elif family in {"micro_continuation_fvg", "continuation"} or branch == "continuation_reentry" or "continuation" in setup_class:
        threshold = _floatish(runtime.get("ai_score_continuation"), fallback)
        source = "ai_score_continuation"
    elif family in {"micro_range_reentry", "range"} or branch == "range_reentry" or "range_reentry" in setup_class:
        threshold = _floatish(runtime.get("ai_score_range"), fallback)
        source = "ai_score_range"
    elif family in {"micro_failed_breakout_reclaim", "failed_breakout"} or "failed_breakout" in setup_class or "reclaim" in setup_class:
        threshold = _floatish(runtime.get("ai_score_failed_breakout"), fallback)
        source = "ai_score_failed_breakout"

    if _boolish(runtime.get("global_ai_score_as_hard_floor"), False):
        threshold = max(fallback, threshold)
        source = source + "+global_floor"

    return max(0.0, min(10.0, threshold)), source


def _apply_family_ai_threshold_gate(payload: Dict[str, Any], decision: Decision, chosen_index: int | None = None) -> Decision:
    threshold, source = effective_ai_score_threshold(payload, chosen_index)
    family, setup_class, branch = _threshold_identity(payload, chosen_index)
    passed = float(decision.score) >= threshold
    decision.ai_score_threshold = threshold
    decision.ai_threshold_source = source
    decision.global_ai_score_as_hard_floor = _boolish(_runtime_inputs(payload).get("global_ai_score_as_hard_floor"), False)
    decision.ai_threshold_passed = passed
    if not passed:
        decision.allow = False
        decision.decision_source = "ai_family_threshold_gate"
        decision.ai_reject_reason = "ai_score_below_family_threshold"
        if decision.rejection_codes is None:
            decision.rejection_codes = []
        if "ai_score_below_family_threshold" not in decision.rejection_codes:
            decision.rejection_codes.append("ai_score_below_family_threshold")
        if isinstance(decision.reasons, dict):
            decision.reasons["ai_score_threshold"] = threshold
            decision.reasons["ai_threshold_source"] = source
            decision.reasons["ai_threshold_passed"] = False
            decision.reasons["ai_reject_reason"] = "ai_score_below_family_threshold"
        else:
            decision.reasons = {
                "previous_reasons": str(decision.reasons or ""),
                "ai_score_threshold": threshold,
                "ai_threshold_source": source,
                "ai_threshold_passed": False,
                "ai_reject_reason": "ai_score_below_family_threshold",
            }
    elif not decision.ai_reject_reason:
        decision.ai_reject_reason = ""
    log(
        f"[ai_gate] family_threshold family={family or 'unknown'} class={setup_class or 'unknown'} "
        f"branch={branch or 'unknown'} score={float(decision.score):.2f} threshold={threshold:.2f} "
        f"source={source} pass={str(passed).lower()}"
    )
    return decision


def _snapshots_required(payload: Dict[str, Any]) -> bool:
    return _runtime_bool(_as_dict(payload.get("runtime")).get("require_snapshots"), False)


def _normalize_advisory_metadata(payload: Dict[str, Any], dec: Decision) -> tuple[list[str], list[str], list[str]]:
    codes = [str(code) for code in (dec.rejection_codes or []) if str(code).strip()]
    risks = [str(risk) for risk in (dec.invalidation_risks or []) if str(risk).strip()]
    missing = [str(item) for item in (dec.missing_confirmations or []) if str(item).strip()]
    if _snapshots_required(payload):
        return codes, risks, missing

    def snapshot_only(text: str) -> bool:
        normalized = text.strip().lower().replace("-", "_").replace(" ", "_")
        return "snapshot" in normalized or "capture_failed" in normalized or "chart_capture" in normalized

    return (
        [code for code in codes if not snapshot_only(code)],
        [risk for risk in risks if not snapshot_only(risk)],
        [item for item in missing if not snapshot_only(item)],
    )


def _hard_model_rejection_codes(codes: list[str]) -> list[str]:
    hard_fragments = (
        "wrong_symbol",
        "structure_invalid",
        "fvg_invalid",
        "target_already_reached",
        "plan_prices_invalid",
        "news_risk_high",
        "spread_cost_too_high",
    )
    return [code for code in codes if any(fragment in code.lower() for fragment in hard_fragments)]

def _snapshot_integrity_rejections(payload: Dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    meta = _as_dict(payload.get("snapshot_metadata"))
    po3 = _as_dict(payload.get("po3"))
    fvg = _as_dict(payload.get("fvg"))
    codes: list[str] = []
    risks: list[str] = []
    missing: list[str] = []
    if not meta:
        return ["snapshot_metadata_missing"], ["snapshot_integrity_unknown"], ["snapshot_metadata"]

    root_symbol = str(payload.get("symbol") or "")
    if not meta.get("symbol"):
        codes.append("snapshot_symbol_missing")
        missing.append("snapshot_metadata.symbol")
    elif root_symbol and str(meta.get("symbol")) != root_symbol:
        codes.append("snapshot_wrong_symbol")
        risks.append("snapshot_symbol_mismatch")

    if meta.get("timeframe") in (None, "", 0):
        codes.append("snapshot_timeframe_missing")
        missing.append("snapshot_metadata.timeframe")
    if meta.get("server_time") in (None, "", 0):
        codes.append("snapshot_server_time_missing")
        missing.append("snapshot_metadata.server_time")
    if meta.get("candle_time") in (None, "", 0):
        codes.append("snapshot_candle_time_missing")
        missing.append("snapshot_metadata.candle_time")
    else:
        try:
            server_time = int(meta.get("server_time") or 0)
            candle_time = int(meta.get("candle_time") or 0)
            if server_time > 0 and candle_time > 0 and server_time - candle_time > 6 * 3600:
                codes.append("snapshot_stale")
                risks.append("snapshot_candle_too_old")
        except Exception:
            codes.append("snapshot_time_invalid")

    if meta.get("bid") in (None, "", 0) or meta.get("ask") in (None, "", 0):
        codes.append("snapshot_bid_ask_missing")
        missing.append("snapshot_metadata.bid_ask")
    if not meta.get("setup_id"):
        codes.append("snapshot_setup_id_missing")
        missing.append("snapshot_metadata.setup_id")
    if _po3_state_name(meta.get("po3_state") or po3.get("po3_state")) not in PO3_ACTIONABLE_AUDIT_STATES:
        codes.append("snapshot_po3_state_invalid")
        risks.append("snapshot_po3_state_not_actionable")
    for key in ("fvg_lower", "fvg_upper"):
        if meta.get(key) in (None, "", 0):
            codes.append(f"snapshot_{key}_missing")
            missing.append(f"snapshot_metadata.{key}")
    if bool(fvg.get("structure_invalidated")):
        codes.append("fvg_structure_invalidated")
        risks.append("fvg_structural_break")
    return codes, risks, missing


def _fatal_snapshot_integrity_codes(codes: list[str]) -> list[str]:
    if not codes:
        return []
    if os.getenv("AI_REQUIRE_SNAPSHOT_INTEGRITY", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return codes
    hard_fatal = {
        "snapshot_wrong_symbol",
        "fvg_structure_invalidated",
    }
    return [code for code in codes if code in hard_fatal]

def _score_setup_impl(payload: Dict[str, Any]) -> Decision:
    """
    Rule-based scoring blended with LLM advisory reranking/veto metadata.
    MT5 remains the final execution authority.
    """
    rule_score, rule_notes = _rule_score(payload)
    cands = payload.get("candidates") or []
    best_candidate, best_index, best_composite = _best_candidate(cands)
    min_confidence = _runtime_min_confidence(payload)
    if AI_CONFIG.use_batch_api and _is_live_payload(payload):
        log("[ai_gate] batch_api_disabled_for_live")
    hard_pre_decision = _hard_pretrade_decision(payload, best_index)
    if hard_pre_decision is not None:
        _write_ai_cost_report(
            payload,
            request_id=str(payload.get("id") or ""),
            decision_source=hard_pre_decision.decision_source,
            model="",
            reasoning_effort=AI_CONFIG.reasoning_effort,
            service_tier="",
            prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
            cache_status="hard_pre_gate",
            batch_used=False,
            flex_used=False,
            openai_called=False,
            skip_reason=(hard_pre_decision.rejection_codes or ["hard_pre_gate"])[0],
        )
        return hard_pre_decision
    integrity_codes, integrity_risks, integrity_missing = _snapshot_integrity_rejections(payload)
    fatal_integrity_codes = _fatal_snapshot_integrity_codes(integrity_codes)
    if fatal_integrity_codes:
        decision = Decision(
            allow=False,
            score=0.0,
            chosen_index=best_index,
            confidence=1.0,
            reasons={
                "decision_source": "snapshot_integrity",
                "rejection_codes": fatal_integrity_codes,
                "snapshot_integrity_codes": integrity_codes,
                "rule_score": rule_score,
                "rule_notes": rule_notes,
            },
            decision_source="snapshot_integrity",
            rejection_codes=fatal_integrity_codes,
            narrative_state="snapshot_rejected",
            invalidation_risks=integrity_risks,
            missing_confirmations=integrity_missing,
            suggested_risk_multiplier=0.0,
            model_version=AI_GATE_MODEL_VERSION,
        )
        _write_ai_cost_report(
            payload,
            request_id=str(payload.get("id") or ""),
            decision_source=decision.decision_source,
            model="",
            reasoning_effort=AI_CONFIG.reasoning_effort,
            service_tier="",
            prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
            cache_status="snapshot_integrity",
            batch_used=False,
            flex_used=False,
            openai_called=False,
            skip_reason=";".join(fatal_integrity_codes),
        )
        return decision

    cache_signature = ""
    cache_base_signature = ""
    cache_fields: Dict[str, Any] = {}
    if AI_CONFIG.decision_cache_enable:
        cache_signature, cache_base_signature, cache_fields = _decision_cache_signature(payload, best_index)
        cached_decision, cache_status = AI_DECISION_CACHE.lookup(cache_signature, cache_base_signature)
        if cached_decision is not None:
            _inc_counter("ai_cache_hit")
            log(f"[ai_gate] ai_cache_hit request_id={str(payload.get('id') or '')} signature={cache_signature[:12]}")
            cached_decision = _apply_family_ai_threshold_gate(payload, cached_decision, cached_decision.chosen_index)
            _write_ai_cost_report(
                payload,
                request_id=str(payload.get("id") or ""),
                decision_source=cached_decision.decision_source,
                model=cached_decision.model_version,
                reasoning_effort=AI_CONFIG.reasoning_effort,
                service_tier="",
                prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
                cache_status="hit",
                batch_used=False,
                flex_used=False,
                openai_called=False,
                skip_reason="ai_cache_hit",
            )
            return cached_decision
        _inc_counter("ai_cache_miss")
        if cache_status == "cache_miss_due_to_schema_version":
            _inc_counter("ai_cache_miss_due_to_schema_version")
            log(f"[ai_gate] ai_cache_miss_due_to_schema_version request_id={str(payload.get('id') or '')} signature={cache_signature[:12]}")
        elif cache_status.startswith("invalidated"):
            log(f"[ai_gate] ai_cache_invalidated_reason={cache_status} request_id={str(payload.get('id') or '')}")
        else:
            log(f"[ai_gate] ai_cache_miss request_id={str(payload.get('id') or '')} signature={cache_signature[:12]}")

    try:
        dec = _score_setup_openai(payload)
    except Exception as e:
        mode, fail_closed, allow_rule_live, allow_rule_fallback, fallback_mult = _runtime_mode(payload)
        if not allow_rule_fallback:
            log(
                f"[ai_gate] AI unavailable for {str(payload.get('symbol', '') or 'unknown_symbol')}; "
                "rule-only fallback is disabled."
            )
            decision = Decision(
                allow=False,
                score=0.0,
                chosen_index=best_index,
                confidence=1.0,
                reasons={
                    "error": str(e),
                    "decision_source": "ai_failure_fallback_disabled",
                    "rejection_codes": ["ai_unavailable_fallback_disabled"],
                    "rule_score": rule_score,
                    "rule_notes": rule_notes,
                },
                decision_source="ai_failure_fallback_disabled",
                rejection_codes=["ai_unavailable_fallback_disabled"],
                narrative_state="ai_failure_fallback_disabled",
                invalidation_risks=["ai_decision_missing"],
                missing_confirmations=["ai_structured_audit"],
                suggested_risk_multiplier=0.0,
                model_version=AI_GATE_MODEL_VERSION,
            )
            _write_ai_cost_report(
                payload,
                request_id=str(payload.get("id") or ""),
                decision_source=decision.decision_source,
                model="",
                reasoning_effort=AI_CONFIG.reasoning_effort,
                service_tier="",
                prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
                cache_status="openai_failure",
                batch_used=False,
                flex_used=False,
                openai_called=True,
                skip_reason="ai_failure_fallback_disabled",
            )
            return decision
        log(
            f"[ai_gate] AI unavailable for {str(payload.get('symbol', '') or 'unknown_symbol')}; "
            "using rule-only fallback."
        )
        if mode == "real" and fail_closed and not allow_rule_live:
            decision = Decision(
                allow=False,
                score=0.0,
                chosen_index=best_index,
                confidence=1.0,
                reasons={
                    "error": str(e),
                    "decision_source": "ai_failure_live_fail_closed",
                    "rejection_codes": ["ai_unavailable_live_fail_closed"],
                    "rule_score": rule_score,
                    "rule_notes": rule_notes,
                },
                decision_source="ai_failure_live_fail_closed",
                rejection_codes=["ai_unavailable_live_fail_closed"],
                narrative_state="ai_failure_fail_closed",
                invalidation_risks=["ai_decision_missing"],
                missing_confirmations=["ai_structured_audit"],
                suggested_risk_multiplier=0.0,
                model_version="rule_only_fallback",
            )
            _write_ai_cost_report(
                payload,
                request_id=str(payload.get("id") or ""),
                decision_source=decision.decision_source,
                model="",
                reasoning_effort=AI_CONFIG.reasoning_effort,
                service_tier="",
                prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
                cache_status="openai_failure",
                batch_used=False,
                flex_used=False,
                openai_called=True,
                skip_reason="ai_failure_live_fail_closed",
            )
            return decision
        fallback_conf = _fallback_confidence(rule_score, rule_notes, str(e))
        fallback_allow = bool(rule_score >= 6.2 and fallback_conf >= min_confidence)
        fallback_source = "rule_only_fallback_tester" if bool(_as_dict(payload.get("runtime")).get("tester")) else ("rule_only_fallback_demo" if mode != "real" else "rule_only_fallback_live_reduced")
        decision = Decision(
            allow=fallback_allow,
            score=round(rule_score, 2),
            chosen_index=best_index,
            confidence=round(fallback_conf, 2),
            reasons={
                "error": str(e),
                "fallback": "rule_only",
                "decision_source": fallback_source,
                "rejection_codes": ["ai_unavailable_rule_only_fallback"],
                "snapshot_integrity_codes": integrity_codes,
                "rule_score": rule_score,
                "rule_notes": rule_notes,
                "best_candidate_score": round(best_composite, 2),
            },
            decision_source=fallback_source,
            rejection_codes=[] if fallback_allow else ["rule_only_score_below_threshold"],
            narrative_state="rule_only_fallback",
            invalidation_risks=["ai_audit_unavailable"] + integrity_risks,
            missing_confirmations=["ai_structured_audit"] + integrity_missing,
            suggested_risk_multiplier=fallback_mult,
            model_version="rule_only_fallback",
        )
        _write_ai_cost_report(
            payload,
            request_id=str(payload.get("id") or ""),
            decision_source=decision.decision_source,
            model="rule_only_fallback",
            reasoning_effort=AI_CONFIG.reasoning_effort,
            service_tier="",
            prompt_cache_enabled=AI_CONFIG.prompt_cache_enable,
            cache_status="openai_failure",
            batch_used=False,
            flex_used=False,
            openai_called=True,
            skip_reason="rule_only_fallback_after_ai_failure",
        )
        return decision


    ai_score = float(dec.score)
    blended = 0.72 * rule_score + 0.28 * ai_score

    # Soft cap / compression instead of hard-forcing 7.50.
    # Goal: 8+ remains rare, but scores don't all collapse to 7.50.
    if blended >= 7.8 and (rule_score < 8.7 or ai_score < 8.5):
        gap = abs(rule_score - ai_score)          # disagreement penalty
        penalty = min(1.20, 0.20 + 0.18 * gap)    # 0.2 .. 1.2
        blended = blended - penalty
        blended = min(blended, 7.95)              # keep sub-8 unless both are strong

    blended = max(0.0, min(10.0, blended))
    blended = round(blended, 2)
    agreement = max(0.0, 1.0 - abs(rule_score - ai_score) / 6.0)
    model_strength = max(0.0, min(1.0, blended / 10.0))
    separation = 0.0
    if isinstance(cands, list) and len(cands) > 1:
        ranked = sorted((_candidate_score(c) for c in cands if isinstance(c, dict)), reverse=True)
        if len(ranked) >= 2:
            separation = max(0.0, min(1.0, (ranked[0] - ranked[1]) / 12.0))
    ai_confidence = 0.0
    try:
        ai_confidence = float(dec.confidence)
    except Exception:
        ai_confidence = 0.0
    ai_confidence = max(0.0, min(1.0, ai_confidence))
    confidence = 0.40 * agreement + 0.25 * model_strength + 0.10 * separation + 0.25 * ai_confidence
    confidence = max(0.0, min(1.0, confidence))
    confidence = round(confidence, 2)
    inherited_codes, inherited_risks, inherited_missing = _normalize_advisory_metadata(payload, dec)
    for risk in integrity_risks:
        if risk not in inherited_risks:
            inherited_risks.append(risk)
    for field in integrity_missing:
        if field not in inherited_missing:
            inherited_missing.append(field)
    hard_model_codes = _hard_model_rejection_codes(inherited_codes)

    allow = bool(dec.allow and confidence >= min_confidence and not hard_model_codes)
    if confidence < min_confidence and "ai_confidence_below_threshold" not in inherited_codes:
        inherited_codes.append("ai_confidence_below_threshold")

    chosen_index = int(dec.chosen_index)
    if chosen_index < 0:
        chosen_index = 0
    if isinstance(cands, list) and chosen_index >= len(cands):
        chosen_index = best_index

    if isinstance(cands, list) and 0 <= chosen_index < len(cands) and isinstance(cands[chosen_index], dict):
        chosen_hard_reason = _candidate_hard_block_reason(cands[chosen_index], payload)
        if chosen_hard_reason:
            allow = False
            if chosen_hard_reason not in inherited_codes:
                inherited_codes.append(chosen_hard_reason)
            if "objective_pre_trade_gate_failed" not in inherited_risks:
                inherited_risks.append("objective_pre_trade_gate_failed")

    final_decision = Decision(
        allow=allow,
        score=blended,
        chosen_index=chosen_index,
        confidence=confidence,
        reasons={
            "ai_reasons": dec.reasons,
            "decision_source": "llm_blended",
            "rule_score": rule_score,
            "rule_notes": rule_notes,
            "agreement": round(agreement, 3),
            "best_candidate_score": round(best_composite, 2),
            "snapshot_integrity_codes": integrity_codes,
            "rejection_codes": inherited_codes,
            "narrative_state": dec.narrative_state or "audited",
        },
        decision_source="llm_blended",
        rejection_codes=inherited_codes,
        narrative_state=dec.narrative_state or "audited",
        invalidation_risks=inherited_risks,
        missing_confirmations=inherited_missing,
        suggested_risk_multiplier=max(0.0, min(1.0, float(dec.suggested_risk_multiplier or 1.0))),
        model_version=dec.model_version or AI_GATE_MODEL_VERSION,
        target_arbitration=dec.target_arbitration or {},
        chosen_target_model=dec.chosen_target_model,
        chosen_tp1=dec.chosen_tp1,
        chosen_tp2=dec.chosen_tp2,
        chosen_rr1=dec.chosen_rr1,
        chosen_rr2=dec.chosen_rr2,
        rejected_target_models=list(dec.rejected_target_models or []),
        target_blocker_kind=dec.target_blocker_kind,
        target_blocker_severity=dec.target_blocker_severity,
        target_blocker_class=dec.target_blocker_class,
        target_blocker_is_trade_killer=dec.target_blocker_is_trade_killer,
        target_decision_reason=dec.target_decision_reason,
        target_blocker_severity_present=dec.target_blocker_severity_present,
        target_blocker_class_present=dec.target_blocker_class_present,
        target_blocker_is_trade_killer_present=dec.target_blocker_is_trade_killer_present,
        target_decision_reason_present=dec.target_decision_reason_present,
        why_not_liquidity_target=dec.why_not_liquidity_target,
        why_not_partial_before_obstacle=dec.why_not_partial_before_obstacle,
        why_not_capped_before_obstacle=dec.why_not_capped_before_obstacle,
        why_not_synthetic_fallback=dec.why_not_synthetic_fallback,
        target_arbitration_schema_version=dec.target_arbitration_schema_version or AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        prompt_contract_version=dec.prompt_contract_version or AI_PROMPT_CONTRACT_VERSION,
        target_comparison_json=dec.target_comparison_json or "{}",
    )
    final_decision = _apply_family_ai_threshold_gate(payload, final_decision, chosen_index)
    if AI_CONFIG.decision_cache_enable and cache_signature:
        AI_DECISION_CACHE.store(cache_signature, cache_base_signature, cache_fields, final_decision)
    return final_decision


def score_setup_live(payload: Dict[str, Any]) -> Decision:
    return _score_setup_impl(payload)


def score_setup(payload: Dict[str, Any]) -> Decision:
    return score_setup_live(payload)


def _batch_output_dir() -> Path:
    path = AI_CONFIG.batch_output_dir
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _batch_request_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    compact = _compact_model_payload(payload)
    instructions = (
        "You are a PO3/FVG research gate. Score this historical setup. "
        "Return concise JSON fields allow, score, chosen_index, confidence, reasons. "
        "Delayed batch results are for analytics only and must never trigger live execution."
    )
    request_kwargs: Dict[str, Any] = {
        "model": AI_CONFIG.model,
        "instructions": instructions,
        "input": [{
            "role": "user",
            "content": [{"type": "input_text", "text": json.dumps(compact, ensure_ascii=False, separators=(",", ":"))}],
        }],
        "max_output_tokens": AI_CONFIG.max_output_tokens,
        "store": False,
        "truncation": "auto",
    }
    reasoning = _reasoning_config_for_model(AI_CONFIG.model)
    if reasoning:
        request_kwargs["reasoning"] = reasoning
    _apply_prompt_cache_kwargs(request_kwargs)
    _apply_service_tier_kwargs(request_kwargs, payload)
    return request_kwargs


def score_setup_batch_research(payloads: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Submit non-live research/backtest setup scoring through OpenAI Batch.

    The function writes a JSONL request file and, when the OpenAI SDK supports it,
    submits a Batch job. It deliberately returns job metadata only; delayed Batch
    outputs are never converted into immediate trade approvals.
    """
    if not AI_CONFIG.use_batch_api:
        return {"batch_used": False, "reason": "batch_api_disabled"}
    if not payloads:
        return {"batch_used": False, "reason": "empty_payloads"}
    live_ids = [str(p.get("id") or "") for p in payloads if _is_live_payload(p)]
    if live_ids:
        log("[ai_gate] batch_api_disabled_for_live")
        return {"batch_used": False, "reason": "batch_api_disabled_for_live", "live_request_ids": live_ids}
    allowed_modes = {"backtest", "replay", "research", "analytics"}
    disallowed = [str(p.get("id") or "") for p in payloads if _payload_workload_mode(p) not in allowed_modes]
    if disallowed:
        return {"batch_used": False, "reason": "batch_api_requires_non_live_mode", "request_ids": disallowed}
    if len(payloads) > AI_CONFIG.batch_max_pending:
        return {"batch_used": False, "reason": "batch_max_pending_exceeded", "count": len(payloads)}

    out_dir = _batch_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    jsonl_path = out_dir / f"batch_requests_{stamp}.jsonl"
    rows = []
    for payload in payloads:
        req_id = str(payload.get("id") or f"payload_{len(rows)}")
        rows.append({
            "custom_id": req_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": _batch_request_body(payload),
        })
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    manifest: Dict[str, Any] = {
        "batch_used": True,
        "status": "jsonl_written",
        "jsonl_path": str(jsonl_path),
        "request_count": len(rows),
        "created_at": stamp,
        "batch_id": "",
        "output_file_id": "",
        "error": "",
    }
    try:
        client = _openai_client(AI_CONFIG.openai_timeout_sec)
        with jsonl_path.open("rb") as batch_file:
            upload = client.files.create(file=batch_file, purpose="batch")
        batch = client.batches.create(
            input_file_id=upload.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"source": "po3_ai_gate_research", "created_by": "score_setup_batch_research"},
        )
        manifest["status"] = str(getattr(batch, "status", "submitted") or "submitted")
        manifest["batch_id"] = str(getattr(batch, "id", "") or "")
        manifest["input_file_id"] = str(getattr(upload, "id", "") or "")
    except Exception as exc:
        manifest["status"] = "jsonl_written_submit_failed"
        manifest["error"] = str(exc)
        log(f"[ai_gate] batch_submit_failed error={exc}")
    manifest_path = out_dir / f"batch_manifest_{stamp}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
# ---------- File bus helpers ----------

def atomic_write_json(path: Path, obj: Dict[str, Any], encoding: str = "utf-8") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding=encoding)
    tmp.replace(path)


def write_debug_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, obj, encoding="utf-8")


def _try_move_to_stale(file_path: Path, stale_dir: Path) -> tuple[bool, str]:
    stale_dir.mkdir(parents=True, exist_ok=True)
    dst = stale_dir / file_path.name
    try:
        shutil.move(str(file_path), str(dst))
        return True, "moved"
    except FileNotFoundError:
        return False, "missing"
    except PermissionError:
        return False, "permission_denied"
    except Exception as e:
        try:
            shutil.copy2(str(file_path), str(dst))
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass
            return True, "copied"
        except FileNotFoundError:
            return False, "missing"
        except PermissionError:
            return False, "permission_denied"
        except Exception as inner:
            return False, str(inner if inner else e)


def move_to_stale(file_path: Path, stale_dir: Path) -> None:
    ok, reason = _try_move_to_stale(file_path, stale_dir)
    if not ok and reason not in {"missing", "permission_denied"}:
        raise RuntimeError(f"move_to_stale_failed:{reason}")


def _job_int(job: Dict[str, Any], key: str, default: int) -> int:
    try:
        value = job.get(key, default)
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _job_float(job: Dict[str, Any], key: str, default: float) -> float:
    try:
        value = job.get(key, default)
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _env_int_any(names: List[str], default: int) -> int:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            try:
                return int(float(raw))
            except Exception:
                return default
    return default


def _env_float_any(names: List[str], default: float) -> float:
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            try:
                return float(raw)
            except Exception:
                return default
    return default


def _policy_job_int(job: Dict[str, Any], key: str, env_names: List[str], default: int) -> int:
    env_value = _env_int_any(env_names, default)
    if any(os.getenv(name, "").strip() for name in env_names):
        return env_value
    return _job_int(job, key, default)


def _policy_job_float(job: Dict[str, Any], key: str, env_names: List[str], default: float) -> float:
    env_value = _env_float_any(env_names, default)
    if any(os.getenv(name, "").strip() for name in env_names):
        return env_value
    return _job_float(job, key, default)


def process_analytics_job(job_path: Path, bus: Path, stale_dir: Path) -> None:
    if run_analytics_suite is None:
        raise RuntimeError(f"analytics_import_failed:{ANALYTICS_IMPORT_ERROR}")
    job = read_json_any_encoding(job_path)
    auto_activate = _runtime_bool(job.get("auto_activate"), ANALYTICS_AUTO_ACTIVATE)
    policy_shadow_mode = _runtime_bool(
        job.get("policy_shadow_mode"),
        os.getenv("POLICY_SHADOW_MODE", "true").strip().lower() in {"1", "true", "yes", "on"},
    )
    policy_config = {
        "auto_activate": auto_activate,
        "policy_shadow_mode": policy_shadow_mode,
        "policy_min_total_closed_trades": max(1, _policy_job_int(job, "policy_min_total_closed_trades", ["EXPECTANCY_MIN_TRADES_BEFORE_ACTION", "POLICY_MIN_TOTAL_CLOSED_TRADES"], 200)),
        "policy_min_bucket_trades": max(1, _policy_job_int(job, "policy_min_bucket_trades", ["EXPECTANCY_MIN_BUCKET_TRADES_BEFORE_ACTION", "POLICY_MIN_BUCKET_TRADES"], 50)),
        "policy_min_profit_factor_after_costs": _policy_job_float(job, "policy_min_profit_factor_after_costs", ["POLICY_MIN_PROFIT_FACTOR_AFTER_COSTS"], 1.15),
        "policy_max_train_test_gap_r": _policy_job_float(job, "policy_max_train_test_gap_r", ["POLICY_MAX_TRAIN_TEST_GAP_R"], 0.25),
        "policy_min_positive_fold_rate": _policy_job_float(job, "policy_min_positive_fold_rate", ["POLICY_MIN_POSITIVE_FOLD_RATE"], 0.60),
        "policy_max_drawdown_r": _policy_job_float(job, "policy_max_drawdown_r", ["POLICY_MAX_DRAWDOWN_R"], 12.0),
    }
    suite = run_analytics_suite(
        logs_dir=bus / "logs" / "trade_results",
        analytics_root=bus / "logs" / "analytics",
        policy_root=bus / "logs" / "policies",
        auto_activate=policy_config["auto_activate"],
        min_bucket_samples=policy_config["policy_min_bucket_trades"],
        min_subtype_samples=policy_config["policy_min_bucket_trades"],
        evidence_min_trades=policy_config["policy_min_total_closed_trades"],
        policy_shadow_mode=policy_config["policy_shadow_mode"],
        policy_min_bucket_trades=policy_config["policy_min_bucket_trades"],
        policy_min_profit_factor_after_costs=policy_config["policy_min_profit_factor_after_costs"],
        policy_max_train_test_gap_r=policy_config["policy_max_train_test_gap_r"],
        policy_min_positive_fold_rate=policy_config["policy_min_positive_fold_rate"],
        policy_max_drawdown_r=policy_config["policy_max_drawdown_r"],
    )
    job_result = {
        "job": job,
        "policy_config": policy_config,
        "processed_at": int(time.time()),
        "governance": suite.get("governance", {}),
        "active_policy": suite.get("active_policy", {}),
        "activation": suite.get("activation"),
        "snapshot_path": suite.get("snapshot_path", ""),
    }
    write_debug_json(bus / "logs" / "analytics" / "last_job.json", job_result)
    move_to_stale(job_path, stale_dir / "analytics_jobs")


def _claim_lock_path(lock_dir: Path, req_path: Path) -> Path:
    return lock_dir / f"{req_path.name}.lock"


def _acquire_request_claim(lock_dir: Path, req_path: Path) -> Path | None:
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _claim_lock_path(lock_dir, req_path)
    now = time.time()

    for attempt in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as f:
                f.write(json.dumps({"pid": os.getpid(), "ts": now}))
            return lock_path
        except FileExistsError:
            try:
                stale = False
                if not req_path.exists():
                    stale = True
                else:
                    age = now - lock_path.stat().st_mtime
                    stale = age > REQUEST_LOCK_STALE_SEC
                if stale:
                    lock_path.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            return None
    return None


def _release_request_claim(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass

def _is_stable_input_file(path: Path) -> bool:
    lowered = path.name.lower()
    if lowered.endswith((".tmp", ".partial", ".lock")):
        return False
    try:
        size1 = path.stat().st_size
        if size1 <= 0:
            return False
        time.sleep(REQUEST_STABLE_MS / 1000.0)
        size2 = path.stat().st_size
        return size1 == size2 and size2 > 0
    except FileNotFoundError:
        return False
    except Exception:
        return False

def _write_error_response(req_id: str, resp_dir: Path, reason: str, payload_summary: Dict[str, Any] | None = None) -> None:
    resp_dir.mkdir(parents=True, exist_ok=True)
    reason_text = _ascii_compact(f"bridge_error={reason}")
    resp: Dict[str, Any] = {
        "id": req_id,
        "allow": False,
        "score": 0.0,
        "chosen_index": 0,
        "confidence": 0.0,
        "decision_source": "bridge_error",
        "reasons": reason_text,
        "decision_id": f"{req_id}:bridge_error:{int(time.time())}",
        "rejection_codes": ["bridge_error"],
        "narrative_state": "bridge_error",
        "invalidation_risks": ["ai_bridge_error"],
        "missing_confirmations": ["ai_structured_audit"],
        "suggested_risk_multiplier": 0.0,
        "model_version": AI_GATE_MODEL_VERSION,
    }
    atomic_write_json(resp_dir / f"{req_id}.json", resp, encoding=RESP_ENCODING)
    debug_obj: Dict[str, Any] = {
        "id": req_id,
        "response": resp,
        "payload_summary": payload_summary or {},
    }
    write_debug_json(resp_dir.parent / "response_debug" / f"{req_id}.json", debug_obj)


def process_one(req_path: Path, resp_dir: Path, stale_dir: Path) -> None:
    log(f"[ai_gate] Processing {req_path.name}")

    last_err: Exception | None = None
    payload = None
    for _ in range(6):
        try:
            payload = read_json_any_encoding(req_path)
            last_err = None
            break
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(0.05)

    if payload is None:
        raise last_err if last_err else RuntimeError("Unreadable request file")

    if isinstance(payload.get("0"), dict) and any(k in payload["0"] for k in ("plan", "po3", "candidates", "mkt")):
        inner = payload["0"]
        merged = dict(payload)
        merged.pop("0", None)
        merged.update(inner)
        payload = merged

    req_id = payload.get("id") or req_path.stem

    dec = score_setup(payload)

    po3 = _as_dict(payload.get("po3"))
    plan = _as_dict(payload.get("plan"))
    regime = _as_dict(payload.get("regime"))
    snaps = _as_dict(payload.get("snapshots"))
    cands = payload.get("candidates") or []
    setup_family = _setup_family_from_payload(payload, plan, cands)
    requires_sweep_story = _family_requires_sweep_story(setup_family, payload)
    source_story = _as_dict(payload.get("story")) or _as_dict(payload.get("source_story"))

    t_sweep = (
        _get_any(po3, ["t_sweep", "sweep_time"])
        or _get_any(payload, ["t_sweep"])
        or _get_any(source_story, ["t_sweep", "source_t_sweep"])
        or _get_any(payload, ["source_t_sweep"])
    )
    t_disp = _get_any(po3, ["t_disp", "t_displacement", "disp_time"]) or _get_any(payload, ["t_disp"])
    t_bos = _get_any(po3, ["t_bos", "bos_time"]) or _get_any(payload, ["t_bos"])
    dr_high = (
        _get_any(po3, ["dr_high", "dr_hi"])
        or _get_any(payload, ["dr_high", "dr_hi"])
        or _get_any(source_story, ["dr_high", "source_dr_high"])
        or _get_any(payload, ["source_dr_high"])
    )
    dr_low = (
        _get_any(po3, ["dr_low", "dr_lo"])
        or _get_any(payload, ["dr_low", "dr_lo"])
        or _get_any(source_story, ["dr_low", "source_dr_low"])
        or _get_any(payload, ["source_dr_low"])
    )
    session_name = _get_any(po3, ["session_name"]) or _get_any(payload, ["session_name"])
    in_killzone = _get_any(po3, ["in_killzone"]) or _get_any(payload, ["in_killzone"])
    liquidity_target = _get_any(po3, ["liquidity_target"]) or _get_any(payload, ["liquidity_target"])

    entry_est = _get_any(plan, ["entry_est", "entry"]) or _get_any(payload, ["entry_est", "entry"])
    sl = _get_any(plan, ["sl", "stop_loss", "stop"]) or _get_any(payload, ["sl", "stop_loss", "stop"])
    tp2 = _get_any(plan, ["tp2", "tp", "final_tp"]) or _get_any(payload, ["tp2", "tp", "final_tp"])

    missing_fields = []
    if requires_sweep_story and not t_sweep:
        missing_fields.append("po3.t_sweep")
    if (po3.get("has_displacement") or po3.get("has_disp")) and not t_disp:
        missing_fields.append("po3.t_disp")
    if po3.get("has_bos") and not t_bos:
        missing_fields.append("po3.t_bos")
    if requires_sweep_story and (dr_high in (None, 0) or dr_low in (None, 0)):
        missing_fields.append("po3.dr_high/dr_low")
    if entry_est in (None, 0) or sl in (None, 0) or tp2 in (None, 0):
        missing_fields.append("plan.entry_est/sl/tp2")
    if not isinstance(cands, list) or len(cands) == 0:
        missing_fields.append("candidates[]")
    if missing_fields:
        dec.allow = False
        if dec.rejection_codes is None:
            dec.rejection_codes = []
        for field in missing_fields:
            code = "missing_" + "".join(ch if ch.isalnum() else "_" for ch in field.lower()).strip("_")
            if code not in dec.rejection_codes:
                dec.rejection_codes.append(code)
        if dec.missing_confirmations is None:
            dec.missing_confirmations = []
        for field in missing_fields:
            if field not in dec.missing_confirmations:
                dec.missing_confirmations.append(field)

    def _snapshot_exists(path_str: Any) -> bool:
        try:
            return bool(path_str) and Path(str(path_str)).exists()
        except Exception:
            return False

    snapshot_summary = {
        "htf_path": snaps.get("htf_path", ""),
        "ltf_path": snaps.get("ltf_path", ""),
        "htf_exists": _snapshot_exists(snaps.get("htf_path")),
        "ltf_exists": _snapshot_exists(snaps.get("ltf_path")),
    }

    payload_summary = {
        "keys": sorted(list(payload.keys())),
        "runtime_input_hash": payload.get("runtime_input_hash"),
        "runtime_inputs": _runtime_inputs(payload),
        "po3": {
            "has_sweep": po3.get("has_sweep"),
            "po3_scope": po3.get("po3_scope"),
            "setup_family": setup_family,
            "requires_sweep_story": requires_sweep_story,
            "t_sweep": t_sweep,
            "t_disp": t_disp,
            "t_bos": t_bos,
            "dr_high": dr_high,
            "dr_low": dr_low,
            "session_name": session_name,
            "in_killzone": in_killzone,
            "liquidity_target": liquidity_target,
        },
        "source_story": source_story or {
            key: payload.get(key)
            for key in (
                "source_t_sweep",
                "source_t_disp",
                "source_t_bos",
                "source_context_tier",
                "source_sweep_side",
                "source_liquidity_target",
                "source_liquidity_kind",
            )
            if key in payload
        },
        "plan": {
            "entry_est": entry_est,
            "entry_model": plan.get("entry_model"),
            "sl": sl,
            "tp1": plan.get("tp1"),
            "tp2": tp2,
            "rr2": plan.get("rr2"),
            "liquidity_rr": plan.get("liquidity_rr"),
            "setup_score": plan.get("setup_score"),
        },
        "target_candidates": _compact_target_candidates(_target_candidates(payload, plan)),
        "regime": {
            "atr_pct": _get_any(regime, ["atr_pct"], _get_any(payload, ["atr_pct"])),
            "trend_strength": _get_any(regime, ["trend_strength"], _get_any(payload, ["trend_strength"])),
            "trend_slope_pct": _get_any(regime, ["trend_slope_pct"], _get_any(payload, ["trend_slope_pct"])),
            "adx_value": _get_any(regime, ["adx_value"], _get_any(payload, ["adx_value"])),
            "adr_pct": _get_any(regime, ["adr_pct"], _get_any(payload, ["adr_pct"])),
            "session_vol_ratio": _get_any(regime, ["session_vol_ratio"], _get_any(payload, ["session_vol_ratio"])),
            "vwap_dist_atr": _get_any(regime, ["vwap_dist_atr"], _get_any(payload, ["vwap_dist_atr"])),
            "compression_score": _get_any(regime, ["compression_score"], _get_any(payload, ["compression_score"])),
            "expansion_score": _get_any(regime, ["expansion_score"], _get_any(payload, ["expansion_score"])),
            "news_risk": _get_any(regime, ["news_risk"], _get_any(payload, ["news_risk"])),
        },
        "snapshots": snapshot_summary,
        "cand_count": len(cands) if isinstance(cands, list) else 0,
        "candidate_indexes": [
            cand.get("candidate_index")
            for cand in cands[: min(5, len(cands))]
            if isinstance(cand, dict)
        ] if isinstance(cands, list) else [],
    }

    reason_text = _decision_reason_text(dec.reasons)
    resp = {
        "id": req_id,
        "allow": bool(dec.allow),
        "score": float(dec.score),
        "chosen_index": int(dec.chosen_index),
        "confidence": float(dec.confidence),
        "decision_source": str(dec.decision_source or ""),
        "reasons": reason_text,
        "decision_id": str(dec.decision_id or f"{req_id}:{dec.decision_source or 'decision'}:{int(time.time())}"),
        "rejection_codes": list(dec.rejection_codes or []),
        "narrative_state": str(dec.narrative_state or "audited"),
        "invalidation_risks": list(dec.invalidation_risks or []),
        "missing_confirmations": list(dec.missing_confirmations or []),
        "suggested_risk_multiplier": float(dec.suggested_risk_multiplier),
        "model_version": str(dec.model_version or AI_GATE_MODEL_VERSION),
        "setup_family": str(setup_family or ""),
        "setup_class": str(_get_any(plan, ["setup_class"], "")),
        "entry_branch": str(_get_any(plan, ["entry_branch", "entry_model"], "")),
        "ai_score": float(dec.score),
        "ai_confidence": float(dec.confidence),
        "ai_score_threshold": float(dec.ai_score_threshold),
        "ai_threshold_source": str(dec.ai_threshold_source or ""),
        "global_ai_score_as_hard_floor": bool(dec.global_ai_score_as_hard_floor),
        "ai_threshold_passed": bool(dec.ai_threshold_passed),
        "ai_reject_reason": str(dec.ai_reject_reason or ""),
        "target_arbitration": dec.target_arbitration or {},
        "chosen_target_model": str(dec.chosen_target_model or ""),
        "chosen_tp1": float(dec.chosen_tp1 or 0.0),
        "chosen_tp2": float(dec.chosen_tp2 or 0.0),
        "chosen_rr1": float(dec.chosen_rr1 or 0.0),
        "chosen_rr2": float(dec.chosen_rr2 or 0.0),
        "rejected_target_models": list(dec.rejected_target_models or []),
        "target_blocker_kind": str(dec.target_blocker_kind or ""),
        "target_blocker_severity": float(dec.target_blocker_severity if dec.target_blocker_severity is not None else -1.0),
        "target_blocker_class": str(dec.target_blocker_class or ""),
        "target_blocker_is_trade_killer": bool(dec.target_blocker_is_trade_killer),
        "target_decision_reason": str(dec.target_decision_reason or ""),
        "target_blocker_severity_present": bool(dec.target_blocker_severity_present),
        "target_blocker_class_present": bool(dec.target_blocker_class_present),
        "target_blocker_is_trade_killer_present": bool(dec.target_blocker_is_trade_killer_present),
        "target_decision_reason_present": bool(dec.target_decision_reason_present),
        "why_not_liquidity_target": str(dec.why_not_liquidity_target or ""),
        "why_not_partial_before_obstacle": str(dec.why_not_partial_before_obstacle or ""),
        "why_not_capped_before_obstacle": str(dec.why_not_capped_before_obstacle or ""),
        "why_not_synthetic_fallback": str(dec.why_not_synthetic_fallback or ""),
        "target_arbitration_schema_version": str(dec.target_arbitration_schema_version or AI_TARGET_ARBITRATION_SCHEMA_VERSION),
        "prompt_contract_version": str(dec.prompt_contract_version or AI_PROMPT_CONTRACT_VERSION),
        "target_comparison": _json_object_from_text(dec.target_comparison_json),
    }

    resp_path = resp_dir / f"{req_id}.json"
    resp_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(resp_path, resp, encoding=RESP_ENCODING)
    write_debug_json(
        resp_dir.parent / "response_debug" / f"{req_id}.json",
        {
            "id": req_id,
            "response": resp,
            "missing_fields": missing_fields,
            "payload_summary": payload_summary,
            "reasons_raw": dec.reasons,
        },
    )
    log(
        f"[ai_gate] OK {req_path.name} -> {resp_path.name} "
        f"allow={resp['allow']} score={resp['score']} conf={resp['confidence']} chosen={resp['chosen_index']} "
        f"source={resp['decision_source']} "
        f"cand={payload_summary['cand_count']} missing={len(missing_fields)}"
    )

    if missing_fields:
        log(f"[ai_gate] {req_path.name} missing critical fields: {', '.join(missing_fields)}")
    if payload_summary["cand_count"] == 0:
        log(f"[ai_gate] {req_path.name} had no candidates")

    moved, move_reason = _try_move_to_stale(req_path, stale_dir)
    if not moved and move_reason == "permission_denied":
        log(f"[ai_gate] request cleanup deferred {req_path.name}: permission_denied")
    elif not moved and move_reason != "missing":
        log(f"[ai_gate] request cleanup deferred {req_path.name}: {move_reason}")


def _process_claimed_request(
    req_path: Path,
    resp_dir: Path,
    stale_dir: Path,
    lock_path: Path,
) -> None:
    req_id = req_path.stem
    try:
        process_one(req_path, resp_dir, stale_dir)
    except Exception as exc:
        log(f"[ai_gate] ERROR {req_path.name}: {exc}")
        payload_summary: Dict[str, Any] = {"bridge_error": str(exc)}
        try:
            raw_payload = read_json_any_encoding(req_path)
            if isinstance(raw_payload.get("0"), dict) and any(
                key in raw_payload["0"] for key in ("plan", "po3", "candidates", "mkt")
            ):
                inner = raw_payload["0"]
                merged = dict(raw_payload)
                merged.pop("0", None)
                merged.update(inner)
                raw_payload = merged
            req_id = raw_payload.get("id") or req_id
            payload_summary["keys"] = sorted(list(raw_payload.keys()))
        except Exception as inner:
            payload_summary["request_read_error"] = str(inner)
        try:
            resp_path = resp_dir / f"{req_id}.json"
            if not resp_path.exists():
                _write_error_response(req_id, resp_dir, str(exc), payload_summary)
                log(f"[ai_gate] Wrote explicit error response for {req_id}")
            else:
                log(f"[ai_gate] Preserved existing response for {req_id} after error")
        except Exception as inner:
            log(f"[ai_gate] Failed to write error response for {req_path.name}: {inner}")
        moved, move_reason = _try_move_to_stale(req_path, stale_dir)
        if not moved and move_reason not in {"missing", "permission_denied"}:
            log(f"[ai_gate] request cleanup deferred {req_path.name}: {move_reason}")
    finally:
        _release_request_claim(lock_path)

# ---------- Main loop ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--common-files-dir", type=str, default="",
                    help="Path to MT5 Common/Files folder. If omitted, uses COMMON_FILES_DIR env var.")
    ap.add_argument("--bus-root", type=str, default=DEFAULT_BUS_ROOT,
                    help="Bus root folder name under Common/Files (default: PO3_AI_BUS).")
    ap.add_argument("--poll-ms", type=int, default=250, help="Polling interval (ms).")
    ap.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("AI_GATE_WORKERS", "4")),
        help="Maximum AI requests processed concurrently (default: 4).",
    )
    args = ap.parse_args()

    # You may provide either:
    #  - the Common\Files folder, OR
    #  - the full bus folder (...\Common\Files\PO3_AI_BUS)
    appdata = os.getenv("APPDATA", "")
    if appdata:
        default_common_or_bus = str(Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files")
    else:
        default_common_or_bus = str(Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files")
    common_files_dir = (
        args.common_files_dir
        or os.getenv("COMMON_FILES_DIR", "").strip()
        or default_common_or_bus
    )
    if not common_files_dir:
        raise SystemExit(
            "Please provide --common-files-dir (the MT5 'Common\\Files' path) "
            "or set COMMON_FILES_DIR env var."
        )

    common_path = Path(common_files_dir)

    # If user provided the full bus dir already, don't append bus_root again
    if common_path.name.lower() == args.bus_root.lower():
        bus = common_path
    else:
        bus = common_path / args.bus_root

    req_dir = bus / "requests"
    resp_dir = bus / "responses"
    stale_dir = bus / "stale"
    lock_dir = bus / "locks"
    analytics_jobs_dir = bus / "logs" / "analytics_jobs"

    global LOG_FILE
    LOG_FILE = bus / "logs" / "ai_gate.log"
    set_openai_usage_bus(bus)

    req_dir.mkdir(parents=True, exist_ok=True)
    resp_dir.mkdir(parents=True, exist_ok=True)
    stale_dir.mkdir(parents=True, exist_ok=True)
    lock_dir.mkdir(parents=True, exist_ok=True)
    analytics_jobs_dir.mkdir(parents=True, exist_ok=True)

    poll_s = max(0.05, args.poll_ms / 1000.0)
    worker_count = max(1, min(16, args.workers))
    request_pool = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ai-gate")

    log(f"[ai_gate] Bus root: {bus}")
    log(f"[ai_gate] Watching requests:  {req_dir}")
    log(f"[ai_gate] Writing responses: {resp_dir}")
    log(f"[ai_gate] Stale/bin:         {stale_dir}")
    log(f"[ai_gate] Model chain:       {', '.join(_candidate_models())}")
    log(f"[ai_gate] Request workers:    {worker_count}")
    log(f"[ai_gate] Analytics jobs:    {analytics_jobs_dir}")
    _log_ai_runtime_config_once()
    while True:
        try:
            for req_path in sorted(req_dir.glob("*.json")):
                # Skip partial writes
                if req_path.name.endswith(".tmp") or not _is_stable_input_file(req_path):
                    continue
                req_id = req_path.stem
                resp_path = resp_dir / f"{req_id}.json"
                if resp_path.exists():
                    moved, move_reason = _try_move_to_stale(req_path, stale_dir)
                    if not moved and move_reason not in {"missing", "permission_denied"}:
                        log(f"[ai_gate] request cleanup deferred {req_path.name}: {move_reason}")
                    continue
                lock_path = _acquire_request_claim(lock_dir, req_path)
                if lock_path is None:
                    continue
                request_pool.submit(
                    _process_claimed_request,
                    req_path,
                    resp_dir,
                    stale_dir,
                    lock_path,
                )
            for job_path in sorted(analytics_jobs_dir.glob("*.json")):
                if job_path.name.endswith(".tmp") or not _is_stable_input_file(job_path):
                    continue
                try:
                    process_analytics_job(job_path, bus, stale_dir)
                    log(f"[ai_gate] analytics refresh complete {job_path.name}")
                except Exception as exc:
                    log(f"[ai_gate] analytics job failed {job_path.name}: {exc}")
                    try:
                        write_debug_json(
                            bus / "logs" / "analytics" / "last_job_error.json",
                            {"job": job_path.name, "error": str(exc), "ts": int(time.time())},
                        )
                    except Exception:
                        pass
                    try:
                        move_to_stale(job_path, stale_dir / "analytics_jobs")
                    except Exception:
                        pass
            time.sleep(poll_s)
        except KeyboardInterrupt:
            print("\n[ai_gate] Stopped.")
            break
    request_pool.shutdown(wait=True)

if __name__ == "__main__":
    main()
