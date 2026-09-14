"""OpenCode Go consumption accounting -- observability only.

Nothing in this module can block, delay, reorder, or skip a provider call.
Every public writer swallows its own failures, and no caller branches on a
value computed here.  It exists to answer one question with evidence:

    For the calls this system actually made, what does OpenCode Go's *published*
    token pricing say they should have cost -- and does the provider's own usage
    report, and the Go allowance, agree?

Why a separate ledger from ``openai_usage.ndjson``
---------------------------------------------------
The legacy usage ledger is written by the gate after a *successful, validated*
role result.  Measured on 2026-09-09..13 it therefore never saw:

* an attempt whose answer failed strict validation (billed, then discarded),
* an attempt that timed out, dropped, or returned 5xx after running upstream,
* a result that arrived after the absolute request deadline,
* the first answer of a semantic correction pass (the critic's was never logged),

and it priced every OpenCode row ``unpriced_model`` at $0.00.  This ledger is
written at the one place every outbound HTTP attempt happens -- the Responses
request loop -- so it records each attempt exactly once, whatever happened next.

Pricing source
--------------
https://opencode.ai/docs/go/ (page "Last updated Sep 11, 2026", retrieved
2026-09-13).  Prices are USD per 1M tokens.  The page lists no cached-write
price for either routed OpenCode model and says nothing about reasoning tokens.
On the Responses wire ``usage.output_tokens`` already INCLUDES
``output_tokens_details.reasoning_tokens`` (true for all 2,516 OpenCode rows in
the live ledger: reasoning <= output on every one), so reasoning is priced as
part of output and is never added a second time.  Whether OpenCode's allowance
meter follows the same rule is not published; that is exactly what the
discrepancy report compares.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PRICING_SOURCE_URL = "https://opencode.ai/docs/go/"
PRICING_PAGE_LAST_UPDATED = "2026-09-11"
PRICING_RETRIEVED_UTC = "2026-09-13"
ATTEMPT_LEDGER_VERSION = "20260913_opencode_go_attempt_ledger_v1"
DEFAULT_ATTEMPT_LEDGER_NAME = "opencode_go_attempts.ndjson"

OUTCOME_OK = "ok"
OUTCOME_SCHEMA_INVALID = "schema_invalid"
OUTCOME_TRANSPORT_ERROR = "transport_error"
EVENT_ATTEMPT = "http_attempt"
EVENT_LOGICAL_CALL = "logical_call"


@dataclass(frozen=True)
class GoModelPrice:
    input_per_million: float
    output_per_million: float
    cached_read_per_million: float
    # ``None`` = the published table shows no cached-write price for the model.
    cached_write_per_million: float | None
    monthly_limit_usd: float


# Model id -> pricing variant -> price.  DeepSeek V4.1 Flash publishes separate
# off-peak and peak rows without defining the hours, so both are reported and
# neither is silently chosen.
OPENCODE_GO_PRICES: dict[str, dict[str, GoModelPrice]] = {
    "muse-spark-1.3-contributor": {
        "standard": GoModelPrice(0.10, 0.20, 0.002, None, 60.0),
    },
    "deepseek-v4.1-flash": {
        "off_peak": GoModelPrice(0.15, 0.60, 0.003, None, 15.0),
        "peak": GoModelPrice(0.30, 1.20, 0.006, None, 15.0),
    },
}

# Published limit windows, as fractions of the monthly limit.
GO_LIMIT_WINDOWS = {"5_hour": 0.20, "weekly": 0.50, "monthly": 1.00}


# ---------------------------------------------------------------------------
# Usage extraction
# ---------------------------------------------------------------------------


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def usage_categories(response: Any) -> dict[str, Any]:
    """Provider-reported token categories, without estimation.

    ``usage_exposed`` is False when the response carried no usage block; every
    count is then ``None`` rather than a fabricated zero.
    """

    usage = _field(response, "usage")
    input_tokens = _as_int(_field(usage, "input_tokens"))
    if input_tokens is None:
        input_tokens = _as_int(_field(usage, "prompt_tokens"))
    output_tokens = _as_int(_field(usage, "output_tokens"))
    if output_tokens is None:
        output_tokens = _as_int(_field(usage, "completion_tokens"))
    input_details = _field(usage, "input_tokens_details") or _field(usage, "prompt_tokens_details")
    output_details = _field(usage, "output_tokens_details") or _field(
        usage, "completion_tokens_details"
    )
    cached_read = _as_int(_field(input_details, "cached_tokens"))
    cache_write = _as_int(_field(input_details, "cache_write_tokens"))
    reasoning = _as_int(_field(output_details, "reasoning_tokens"))
    total = _as_int(_field(usage, "total_tokens"))
    exposed = input_tokens is not None or output_tokens is not None
    uncached = None
    if input_tokens is not None:
        uncached = max(0, input_tokens - int(cached_read or 0) - int(cache_write or 0))
    return {
        "usage_exposed": exposed,
        "input_tokens": input_tokens,
        "uncached_input_tokens": uncached,
        "cached_read_tokens": cached_read,
        "cache_write_tokens": cache_write,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
    }


def expected_go_usage_usd(model: str, categories: Mapping[str, Any]) -> dict[str, float] | None:
    """Published-price cost of one attempt, per pricing variant.

    ``None`` when the model has no published Go price here or the response
    exposed no usage.  Reasoning tokens are inside ``output_tokens`` (see the
    module docstring) and are not added again.
    """

    variants = OPENCODE_GO_PRICES.get(str(model or ""))
    if not variants or not categories.get("usage_exposed"):
        return None
    uncached = int(categories.get("uncached_input_tokens") or 0)
    cached = int(categories.get("cached_read_tokens") or 0)
    written = int(categories.get("cache_write_tokens") or 0)
    output = int(categories.get("output_tokens") or 0)
    result: dict[str, float] = {}
    for name, price in variants.items():
        write_rate = (
            price.cached_write_per_million
            if price.cached_write_per_million is not None
            else price.input_per_million
        )
        cost = (
            uncached * price.input_per_million
            + cached * price.cached_read_per_million
            + written * write_rate
            + output * price.output_per_million
        ) / 1_000_000.0
        result[name] = round(cost, 10)
    return result


# ---------------------------------------------------------------------------
# Wire fingerprints (hashes and sizes only -- never content, never headers
# other than the session id, never credentials)
# ---------------------------------------------------------------------------


def _sha(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    return sha256(raw).hexdigest()


def stable_prefix_material(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """The request parts meant to be byte-identical between calls of one kind.

    Everything a provider can serve from a prefix cache precedes the evidence:
    model, instructions, the strict response format, the reasoning setting and
    the fixed shaping keys.  Request ids, deadlines, market data and the session
    header are deliberately NOT part of it.
    """

    text = kwargs.get("text") if isinstance(kwargs.get("text"), Mapping) else {}
    return {
        "model": kwargs.get("model"),
        "instructions": kwargs.get("instructions"),
        "text": text,
        "reasoning": kwargs.get("reasoning"),
        "truncation": kwargs.get("truncation"),
        "store": kwargs.get("store"),
    }


def wire_fingerprint(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    text = kwargs.get("text") if isinstance(kwargs.get("text"), Mapping) else {}
    fmt = text.get("format") if isinstance(text.get("format"), Mapping) else {}
    instructions = kwargs.get("instructions")
    volatile = kwargs.get("input")
    reasoning = kwargs.get("reasoning") if isinstance(kwargs.get("reasoning"), Mapping) else {}
    headers = kwargs.get("extra_headers") if isinstance(kwargs.get("extra_headers"), Mapping) else {}
    return {
        "stable_prefix_sha256": _sha(stable_prefix_material(kwargs)),
        "instructions_sha256": _sha(instructions if isinstance(instructions, str) else ""),
        "instructions_chars": len(instructions) if isinstance(instructions, str) else 0,
        "schema_name": str(fmt.get("name") or ""),
        "schema_sha256": _sha(fmt.get("schema")) if fmt else "",
        "schema_strict": bool(fmt.get("strict")) if fmt else False,
        "response_format_type": str(fmt.get("type") or ""),
        "volatile_input_sha256": _sha(volatile),
        "volatile_input_chars": len(json.dumps(volatile, ensure_ascii=True)) if volatile is not None else 0,
        "reasoning_effort_sent": str(reasoning.get("effort") or ""),
        "max_output_tokens_sent": _as_int(kwargs.get("max_output_tokens")),
        "truncation_sent": str(kwargs.get("truncation") or ""),
        "service_tier_sent": str(kwargs.get("service_tier") or ""),
        "session_id": str(headers.get("x-opencode-session") or ""),
    }


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _error_status(error: BaseException | None) -> int | None:
    for name in ("status_code", "status", "http_status"):
        value = getattr(error, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def build_attempt_record(
    *,
    outcome: str,
    provider_mode: str,
    provider_id: str,
    endpoint: str,
    model: str,
    role: str,
    request_metadata: Mapping[str, Any],
    kwargs: Mapping[str, Any] | None,
    response: Any,
    error: BaseException | None,
    latency_sec: float,
    attempt_index: int,
    retry_counts: Mapping[str, int],
) -> dict[str, Any]:
    categories = usage_categories(response) if response is not None else usage_categories(None)
    status = 200 if (response is not None and error is None) else _error_status(error)
    if response is not None and outcome == OUTCOME_SCHEMA_INVALID:
        status = 200
    retry_kind = "none"
    for key in ("admission_retries", "flex_retries", "transport_retries", "schema_retries"):
        if int(retry_counts.get(key) or 0) > 0:
            retry_kind = key
    # A transport failure that is not a proven admission refusal may have run
    # upstream -- it is the one outcome whose billing is genuinely unknown.
    if outcome == OUTCOME_TRANSPORT_ERROR:
        billing_state = (
            "not_billed_admission_refused"
            if status in {401, 402, 404, 429}
            else "unknown_possibly_billed"
        )
    elif response is None:
        # Raised locally before a response object existed (for example the
        # client rejecting a keyword): nothing is known to have been billed.
        billing_state = "no_response_local_error"
    else:
        billing_state = "billed_usage_reported" if categories["usage_exposed"] else "billed_usage_not_exposed"
    record: dict[str, Any] = {
        "ledger_version": ATTEMPT_LEDGER_VERSION,
        "event": EVENT_ATTEMPT,
        "ts_utc": _utc_now(),
        "logical_request_id": str(request_metadata.get("request_id") or ""),
        "logical_call_id": str(request_metadata.get("opencode_logical_call_id") or ""),
        "role": str(role or ""),
        "route_stage": _as_int(request_metadata.get("opencode_route_stage")),
        "route_importance": str(request_metadata.get("opencode_route_importance") or ""),
        "fallback_reason": str(request_metadata.get("opencode_fallback_reason") or ""),
        "provider_mode": str(provider_mode or ""),
        "provider_id": str(provider_id or ""),
        "endpoint": str(endpoint or ""),
        "model": str(model or ""),
        "provider_attempt": int(attempt_index),
        "retry_kind": retry_kind,
        "retry_counts": {k: int(v or 0) for k, v in retry_counts.items()},
        "outcome": outcome,
        "billing_state": billing_state,
        "http_status": status,
        "http_request_id": str(
            getattr(response, "_request_id", None) or getattr(error, "request_id", None) or ""
        ),
        "provider_response_id": str(_field(response, "id") or ""),
        "actual_model": str(_field(response, "model") or ""),
        "response_status": str(_field(response, "status") or ""),
        "latency_sec": round(float(latency_sec), 3),
        "error_category": type(error).__name__ if error is not None else "",
        **categories,
        "expected_go_usage_usd": expected_go_usage_usd(model, categories)
        if str(provider_mode or "") == "OPENCODE_API"
        else None,
        "pricing_source": PRICING_SOURCE_URL if str(model or "") in OPENCODE_GO_PRICES else "",
    }
    if kwargs is not None:
        record.update(wire_fingerprint(kwargs))
    return record


def build_logical_call_record(
    *,
    request_metadata: Mapping[str, Any],
    role: str,
    importance: str,
    legs: Sequence[str],
    outcome: str,
    final_provider_id: str,
    latency_sec: float,
) -> dict[str, Any]:
    return {
        "ledger_version": ATTEMPT_LEDGER_VERSION,
        "event": EVENT_LOGICAL_CALL,
        "ts_utc": _utc_now(),
        "logical_request_id": str(request_metadata.get("request_id") or ""),
        "logical_call_id": str(request_metadata.get("opencode_logical_call_id") or ""),
        "role": str(role or ""),
        "route_importance": str(importance or ""),
        "legs_invoked": list(legs),
        "outcome": str(outcome),
        "final_provider_id": str(final_provider_id or ""),
        "latency_sec": round(float(latency_sec), 3),
    }


class OpenCodeGoAttemptLedger:
    """Append-only NDJSON sink.  Never raises into the caller."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        path_resolver: Callable[[], Path] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._resolver = path_resolver
        self._lock = threading.Lock()
        self.write_failures = 0

    def path(self) -> Path:
        if self._path is not None:
            return self._path
        if self._resolver is not None:
            return self._resolver()
        return Path(DEFAULT_ATTEMPT_LEDGER_NAME)

    def __call__(self, record: Mapping[str, Any]) -> None:
        self.record(record)

    def record(self, record: Mapping[str, Any]) -> None:
        try:
            enabled = os.getenv("AI_USAGE_LOG_ENABLE", os.getenv("OPENAI_USAGE_LOG_ENABLE", "true"))
            if enabled.strip().lower() not in {"1", "true", "yes", "on"}:
                return
            line = json.dumps(record, ensure_ascii=True, separators=(",", ":"), default=str)
            target = self.path()
            with self._lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:
            self.write_failures += 1


def bus_attempt_ledger_path() -> Path:
    """``<bus>/logs/opencode_go_attempts.ndjson``, beside the usage ledger."""

    import openai_usage_logger  # local import: keeps this module dependency-light

    name = os.getenv("OPENCODE_GO_ATTEMPT_LEDGER", DEFAULT_ATTEMPT_LEDGER_NAME)
    return openai_usage_logger._bus_path() / "logs" / name


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _in_window(ts: str, since: str | None, until: str | None) -> bool:
    if since and ts < since:
        return False
    if until and ts >= until:
        return False
    return True


def summarize_attempts(
    records: Iterable[Mapping[str, Any]],
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    attempts: list[Mapping[str, Any]] = []
    logical_ids: set[str] = set()
    logical_events = 0
    for row in records:
        if not _in_window(str(row.get("ts_utc") or ""), since, until):
            continue
        if row.get("event") == EVENT_LOGICAL_CALL:
            logical_events += 1
            if row.get("logical_call_id"):
                logical_ids.add(str(row["logical_call_id"]))
        elif row.get("event") == EVENT_ATTEMPT:
            attempts.append(row)
            if row.get("logical_call_id"):
                logical_ids.add(str(row["logical_call_id"]))
    logical_calls = len(logical_ids)
    per_model: dict[str, dict[str, Any]] = {}
    luna_attempts = 0
    retried = 0
    for row in attempts:
        model = str(row.get("model") or "")
        bucket = per_model.setdefault(
            model,
            {
                "http_attempts": 0,
                "ok": 0,
                "schema_invalid": 0,
                "transport_error": 0,
                "unknown_possibly_billed": 0,
                "uncached_input_tokens": 0,
                "cached_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "latency_sec_sum": 0.0,
                "expected_go_usage_usd": {},
                "sessions": set(),
            },
        )
        bucket["http_attempts"] += 1
        bucket[str(row.get("outcome") or OUTCOME_TRANSPORT_ERROR)] = (
            bucket.get(str(row.get("outcome") or OUTCOME_TRANSPORT_ERROR), 0) + 1
        )
        if row.get("billing_state") == "unknown_possibly_billed":
            bucket["unknown_possibly_billed"] += 1
        for key in (
            "uncached_input_tokens",
            "cached_read_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            bucket[key] += int(row.get(key) or 0)
        bucket["latency_sec_sum"] += float(row.get("latency_sec") or 0.0)
        for variant, usd in dict(row.get("expected_go_usage_usd") or {}).items():
            bucket["expected_go_usage_usd"][variant] = round(
                bucket["expected_go_usage_usd"].get(variant, 0.0) + float(usd), 8
            )
        if row.get("session_id"):
            bucket["sessions"].add(str(row["session_id"]))
        if str(row.get("provider_mode") or "") == "REMOTE_API":
            luna_attempts += 1
        if str(row.get("retry_kind") or "none") != "none":
            retried += 1
    models_out: dict[str, Any] = {}
    for model, bucket in per_model.items():
        n = bucket["http_attempts"]
        read = bucket["cached_read_tokens"]
        uncached = bucket["uncached_input_tokens"]
        models_out[model] = {
            **{k: v for k, v in bucket.items() if k not in {"sessions", "latency_sec_sum"}},
            "distinct_sessions": len(bucket["sessions"]),
            "http_attempts_per_logical_call": _ratio(n, logical_calls),
            # Cache-write tokens (reported by OpenAI, never by OpenCode so far)
            # are input that was NOT served from cache, so they belong in the
            # denominator; leaving them out overstated the Luna ratio as ~99.9%.
            "token_weighted_cache_read_ratio": _ratio(
                read, read + uncached + int(bucket["cache_write_tokens"] or 0)
            ),
            "avg_uncached_input_per_attempt": _ratio(uncached, n),
            "avg_cached_input_per_attempt": _ratio(read, n),
            "avg_output_per_attempt": _ratio(bucket["output_tokens"], n),
            "avg_latency_sec": _ratio(bucket["latency_sec_sum"], n),
            "expected_go_usage_usd_per_attempt": {
                variant: round(total / n, 8) for variant, total in bucket["expected_go_usage_usd"].items()
            }
            if n
            else {},
        }
    return {
        "window": {"since": since, "until": until},
        "logical_calls": logical_calls,
        "logical_call_events": logical_events,
        "http_attempts": len(attempts),
        "luna_fallback_http_attempts": luna_attempts,
        "fallback_share_of_logical_calls": _ratio(luna_attempts, logical_calls),
        "retry_share_of_attempts": _ratio(retried, len(attempts)),
        "models": models_out,
        "pricing": {
            "source": PRICING_SOURCE_URL,
            "page_last_updated": PRICING_PAGE_LAST_UPDATED,
            "retrieved_utc": PRICING_RETRIEVED_UTC,
        },
    }


def summarize_legacy_usage(
    rows: Iterable[Mapping[str, Any]],
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """The same metrics reconstructed from ``openai_usage.ndjson``.

    That ledger holds successful validated role results only, so every figure
    here is a LOWER bound on what was sent; it is used as the BEFORE baseline
    because it is the only provider-reported record of the pre-fix workload.
    """

    per_model: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _in_window(str(row.get("ts_utc") or ""), since, until):
            continue
        model = str(row.get("model") or "")
        mode = str(row.get("provider_mode") or "")
        if mode not in {"OPENCODE_API", "REMOTE_API"}:
            continue
        categories = {
            "usage_exposed": True,
            "uncached_input_tokens": max(
                0,
                int(row.get("input_tokens") or 0)
                - int(row.get("cached_input_tokens") or 0)
                - int(((row.get("raw_usage") or {}).get("input_tokens_details") or {}).get("cache_write_tokens") or 0),
            ),
            "cached_read_tokens": int(row.get("cached_input_tokens") or 0),
            "cache_write_tokens": int(
                ((row.get("raw_usage") or {}).get("input_tokens_details") or {}).get("cache_write_tokens") or 0
            ),
            "output_tokens": int(row.get("output_tokens") or 0),
        }
        bucket = per_model.setdefault(
            model,
            {
                "provider_mode": mode,
                "rows": 0,
                "request_ids": set(),
                "uncached_input_tokens": 0,
                "cached_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "expected_go_usage_usd": {},
            },
        )
        bucket["rows"] += 1
        bucket["request_ids"].add(str(row.get("request_id") or ""))
        for key in ("uncached_input_tokens", "cached_read_tokens", "cache_write_tokens", "output_tokens"):
            bucket[key] += categories[key]
        bucket["reasoning_tokens"] += int(row.get("reasoning_output_tokens") or 0)
        bucket["total_tokens"] += int(row.get("total_tokens") or 0)
        if mode == "OPENCODE_API":
            for variant, usd in dict(expected_go_usage_usd(model, categories) or {}).items():
                bucket["expected_go_usage_usd"][variant] = round(
                    bucket["expected_go_usage_usd"].get(variant, 0.0) + usd, 8
                )
    out: dict[str, Any] = {}
    for model, bucket in per_model.items():
        n = bucket["rows"]
        read = bucket["cached_read_tokens"]
        uncached = bucket["uncached_input_tokens"]
        out[model] = {
            **{k: v for k, v in bucket.items() if k != "request_ids"},
            "distinct_request_ids": len(bucket["request_ids"]),
            # Cache-write tokens (reported by OpenAI, never by OpenCode so far)
            # are input that was NOT served from cache, so they belong in the
            # denominator; leaving them out overstated the Luna ratio as ~99.9%.
            "token_weighted_cache_read_ratio": _ratio(
                read, read + uncached + int(bucket["cache_write_tokens"] or 0)
            ),
            "avg_uncached_input_per_row": _ratio(uncached, n),
            "avg_cached_input_per_row": _ratio(read, n),
            "avg_output_per_row": _ratio(bucket["output_tokens"], n),
        }
    return {"window": {"since": since, "until": until}, "models": out, "lower_bound": True}


def _read_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="summarize provider-reported OpenCode Go usage")
    report.add_argument("--attempts", type=Path, default=None, help="attempt ledger (default: bus)")
    report.add_argument("--legacy-usage", type=Path, default=None, help="openai_usage.ndjson")
    report.add_argument("--since", default=None, help="ISO-8601 UTC lower bound (inclusive)")
    report.add_argument("--until", default=None, help="ISO-8601 UTC upper bound (exclusive)")
    args = parser.parse_args(argv)
    output: dict[str, Any] = {}
    attempts_path = args.attempts or bus_attempt_ledger_path()
    if attempts_path.is_file():
        output["attempt_ledger"] = summarize_attempts(
            _read_ndjson(attempts_path), since=args.since, until=args.until
        )
        output["attempt_ledger"]["path"] = str(attempts_path)
    else:
        output["attempt_ledger"] = {"path": str(attempts_path), "present": False}
    if args.legacy_usage is not None:
        output["legacy_usage_ledger"] = summarize_legacy_usage(
            _read_ndjson(args.legacy_usage), since=args.since, until=args.until
        )
    json.dump(output, sys.stdout, indent=2, sort_keys=True, default=sorted)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
