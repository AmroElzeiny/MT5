from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_LOCK = threading.Lock()
_BUS_PATH: Optional[Path] = None


PRICE_PER_MILLION: Dict[str, Dict[str, float]] = {
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.5-pro": {"input": 30.00, "cached_input": 0.0, "output": 180.00},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
}


CSV_FIELDS = [
    "ts_utc",
    "source",
    "operation",
    "status",
    "request_id",
    "response_id",
    "provider_mode",
    "provider_id",
    "endpoint_class",
    "model_fingerprint",
    "model",
    "reasoning_effort",
    "max_output_tokens",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "estimated_cost_usd",
    "tokens_per_second",
    "estimated_context_tokens",
    "error_type",
    "error_message",
]


def set_ai_usage_bus(bus_path: str | Path) -> None:
    global _BUS_PATH
    _BUS_PATH = Path(bus_path)
    os.environ["PO3_AI_BUS_PATH"] = str(_BUS_PATH)


def set_openai_usage_bus(bus_path: str | Path) -> None:
    """Compatibility alias for existing dashboard/report callers."""

    set_ai_usage_bus(bus_path)


def _default_bus_path() -> Path:
    explicit = os.getenv("PO3_AI_BUS_PATH", "").strip()
    if explicit:
        return Path(explicit)

    common = os.getenv("COMMON_FILES_DIR", "").strip()
    if common:
        common_path = Path(common)
    else:
        appdata = os.getenv("APPDATA", "")
        if appdata:
            common_path = Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
        else:
            common_path = Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"

    bus_root = os.getenv("PO3_AI_BUS_ROOT", os.getenv("AI_BUS_ROOT", "PO3_AI_BUS")).strip() or "PO3_AI_BUS"
    if common_path.name.lower() == bus_root.lower():
        return common_path
    return common_path / bus_root


def _bus_path() -> Path:
    return _BUS_PATH or _default_bus_path()


def _plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _plain(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _plain(value.dict())
        except Exception:
            pass
    return str(value)


def _get_nested(mapping: Dict[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _usage_from_response(response: Any) -> Dict[str, Any]:
    usage = getattr(response, "usage", None)
    plain = _plain(usage)
    if plain is None:
        payload = _plain(response)
        if isinstance(payload, dict):
            plain = payload.get("usage")
    return plain if isinstance(plain, dict) else {}


def _response_id(response: Any) -> str:
    rid = getattr(response, "id", "")
    if rid:
        return str(rid)
    payload = _plain(response)
    if isinstance(payload, dict):
        return str(payload.get("id") or "")
    return ""


def _estimated_cost(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    rates = PRICE_PER_MILLION.get(str(model or "").strip())
    if not rates:
        return 0.0
    billable_input = max(0, input_tokens - cached_input_tokens)
    cost = (
        billable_input * rates["input"]
        + cached_input_tokens * rates["cached_input"]
        + output_tokens * rates["output"]
    ) / 1_000_000.0
    return round(cost, 8)


def log_ai_usage(
    *,
    source: str,
    operation: str,
    model: str,
    response: Any = None,
    status: str = "ok",
    request_id: str = "",
    reasoning_effort: str = "",
    max_output_tokens: int | str | None = None,
    error: Exception | str | None = None,
    extra: Optional[Dict[str, Any]] = None,
    provider_mode: str = "REMOTE_API",
    provider_id: str = "openai_remote_api",
    endpoint_class: str = "official_remote",
    model_fingerprint: str = "",
    tokens_per_second: float | None = None,
    estimated_context_tokens: int | None = None,
) -> Dict[str, Any]:
    enabled = os.getenv(
        "AI_USAGE_LOG_ENABLE",
        os.getenv("OPENAI_USAGE_LOG_ENABLE", "true"),
    )
    if enabled.strip().lower() not in {"1", "true", "yes", "on"}:
        return {}

    usage = _usage_from_response(response)
    input_tokens = _int_value(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _int_value(usage.get("output_tokens", usage.get("completion_tokens")))
    total_tokens = _int_value(usage.get("total_tokens")) or input_tokens + output_tokens
    cached_input_tokens = _int_value(
        _get_nested(usage, "input_tokens_details", "cached_tokens")
        or _get_nested(usage, "prompt_tokens_details", "cached_tokens")
    )
    reasoning_output_tokens = _int_value(
        _get_nested(usage, "output_tokens_details", "reasoning_tokens")
        or _get_nested(usage, "completion_tokens_details", "reasoning_tokens")
    )

    error_type = ""
    error_message = ""
    if error is not None:
        error_type = type(error).__name__ if not isinstance(error, str) else "error"
        error_message = str(error)

    now = datetime.now(timezone.utc)
    row: Dict[str, Any] = {
        "ts_utc": now.isoformat(timespec="seconds"),
        "source": str(source or ""),
        "operation": str(operation or ""),
        "status": str(status or ""),
        "request_id": str(request_id or ""),
        "response_id": _response_id(response),
        "provider_mode": str(provider_mode or ""),
        "provider_id": str(provider_id or ""),
        "endpoint_class": str(endpoint_class or ""),
        "model_fingerprint": str(model_fingerprint or ""),
        "model": str(model or ""),
        "reasoning_effort": str(reasoning_effort or ""),
        "max_output_tokens": _int_value(max_output_tokens),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": (
            _estimated_cost(str(model or ""), input_tokens, cached_input_tokens, output_tokens)
            if str(provider_mode or "").upper() == "REMOTE_API"
            else 0.0
        ),
        "tokens_per_second": tokens_per_second,
        "estimated_context_tokens": estimated_context_tokens,
        "error_type": error_type,
        "error_message": error_message[:500],
        "raw_usage": usage,
    }
    if extra:
        row["extra"] = _plain(extra)

    try:
        bus = _bus_path()
        logs_dir = bus / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ndjson_path = logs_dir / os.getenv("OPENAI_USAGE_NDJSON", "openai_usage.ndjson")
        csv_path = logs_dir / os.getenv("OPENAI_USAGE_CSV", "openai_usage.csv")
        with _LOCK:
            with ndjson_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            csv_exists = csv_path.exists() and csv_path.stat().st_size > 0
            with csv_path.open("a", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
                if not csv_exists:
                    writer.writeheader()
                writer.writerow(row)
    except Exception:
        return row

    return row


def log_openai_usage(**kwargs: Any) -> Dict[str, Any]:
    """Compatibility alias that explicitly records remote OpenAI transport."""

    kwargs.setdefault("provider_mode", "REMOTE_API")
    kwargs.setdefault("provider_id", "openai_remote_api")
    kwargs.setdefault("endpoint_class", "official_remote")
    return log_ai_usage(**kwargs)
