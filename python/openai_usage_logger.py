from __future__ import annotations

import csv
import json
import os
import re
import sys
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
    # OpenRouter prices per *endpoint*, not per model id: the same
    # ``z-ai/glm-5.3-flash`` bills $0.075/Mtok on DeepInfra and $0.15 on
    # Parasail.  ``OPENROUTER_ALLOWED_PROVIDERS`` is an ordered list, so a
    # request can be served by any endpoint in it and there is no single true
    # rate for the model id.
    #
    # This model-level row is therefore the *upper bound*: the field-wise
    # maximum over every endpoint below (note cached_input comes from CoreWeave
    # at 0.05, not from the 0.03 the rest charge).  It is used only when the
    # routed endpoint could not be determined, so an unknown route over-reports
    # rather than under-reports.  Under-reporting is the exact defect this
    # module was just fixed for; over-reporting is visible and safe.
    # ``test_model_row_is_the_upper_bound_of_every_endpoint`` enforces this.
    "z-ai/glm-5.3-flash": {"input": 0.15, "cached_input": 0.05, "output": 0.50},
    # The model actually running the live gate.  Its absence from this table is
    # what recorded 2,648 measured calls at $0.00.
    #
    # One row prices *every* reasoning effort on purpose.  Effort is not a
    # pricing axis: it changes how many tokens the call produces, not the rate
    # per token, and reasoning tokens are already counted inside ``output_tokens``
    # (verified against a real row: input 10525 + output 209 == total 10734,
    # with reasoning_output_tokens 97 a subset of the 209).  ``_canonical_model``
    # folds the per-effort slugs -- ``gpt-5.6-luna-low`` through ``-max`` -- and
    # the ``openai/`` vendor prefix onto this row, so an aggregator that exposes
    # effort in the model id cannot reopen the unpriced hole.
    "gpt-5.6-luna": {"input": 0.20, "cached_input": 0.02, "output": 1.20},
}


# Published rate multipliers per service tier.  ``AI_SERVICE_TIER`` accepts
# ``auto|default|flex`` and ``AI_USE_FLEX`` forces ``flex``, so this is a live
# pricing axis for us, not a hypothetical one: a flex call billed at standard
# rates over-reports by exactly 2x.
#
# ``auto`` is a *request* value that the API resolves server-side; the response
# echoes the tier that actually served the call, which is why
# ``_effective_service_tier_for_pricing`` prefers the response over the request.
# Same lesson as ``_routed_endpoint``: only the response knows what a call cost.
SERVICE_TIER_MULTIPLIER: Dict[str, float] = {
    "": 1.0,
    "auto": 1.0,
    "default": 1.0,
    "standard": 1.0,
    "scale": 1.0,
    "flex": 0.5,
    "batch": 0.5,
    "priority": 2.0,
    "fast": 2.0,
}


# Long-context surcharge: a request whose *input* passes the threshold is
# rebilled entirely at these multipliers.  Kept per model rather than global --
# it is a published GPT-5.6 rule and must not leak onto OpenRouter models.
#
# Our measured analyst prompt is 38.6k tokens, so this never fires today.  It is
# recorded anyway because the row it guards takes 1M context and the evidence
# catalog only grows; an unrecorded surcharge is the same silent understatement
# this module was fixed for.
LONG_CONTEXT_SURCHARGE: Dict[str, Dict[str, float]] = {
    "gpt-5.6-luna": {"threshold_input_tokens": 272_000, "input": 2.0, "output": 1.5},
}


# Effort suffixes that some catalogues append to a model id (Artificial Analysis
# and OpenRouter both publish ``gpt-5.6-luna-low``/``-high``/... as separate
# slugs).  Folded onto the base row only when the base is priced and the full id
# is not itself a priced model, so a future model whose real name happens to end
# in one of these can never be silently mispriced.
REASONING_EFFORT_MODEL_SUFFIXES = ("minimal", "low", "medium", "high", "xhigh", "max")

VENDOR_MODEL_PREFIXES = ("openai/",)

# A dated snapshot is the same billed model as its alias.  Not hypothetical:
# the ledger carries 145 ``gpt-5.4-nano-2026-03-17`` calls on REMOTE_API sitting
# at $0.00 right beside priced ``gpt-5.4-nano`` rows -- one model, logged under
# two ids, only one of which had a rate.
_SNAPSHOT_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


# Exact per-endpoint rates, keyed ``(model, routed_endpoint_lowercased)``.
# OpenRouter returns the endpoint that actually served the request as a
# top-level ``provider`` field on the completion, so the real rate is knowable
# per call rather than assumed from configuration.  Rates are $/Mtok as
# published by ``/api/v1/models/{model}/endpoints``.
PRICE_PER_MILLION_BY_ENDPOINT: Dict[tuple, Dict[str, float]] = {
    ("z-ai/glm-5.3-flash", "deepinfra"): {"input": 0.075, "cached_input": 0.015, "output": 0.250},
    ("z-ai/glm-5.3-flash", "wafer"): {"input": 0.100, "cached_input": 0.020, "output": 0.350},
    ("z-ai/glm-5.3-flash", "morph"): {"input": 0.130, "cached_input": 0.020, "output": 0.450},
    ("z-ai/glm-5.3-flash", "makora"): {"input": 0.140, "cached_input": 0.024, "output": 0.470},
    ("z-ai/glm-5.3-flash", "parasail"): {"input": 0.150, "cached_input": 0.030, "output": 0.500},
    ("z-ai/glm-5.3-flash", "nextbit"): {"input": 0.150, "cached_input": 0.030, "output": 0.500},
    ("z-ai/glm-5.3-flash", "phala"): {"input": 0.150, "cached_input": 0.030, "output": 0.500},
    ("z-ai/glm-5.3-flash", "friendli"): {"input": 0.150, "cached_input": 0.030, "output": 0.500},
    ("z-ai/glm-5.3-flash", "cloudflare"): {"input": 0.150, "cached_input": 0.030, "output": 0.500},
    ("z-ai/glm-5.3-flash", "reka"): {"input": 0.150, "cached_input": 0.030, "output": 0.500},
    ("z-ai/glm-5.3-flash", "sail research"): {"input": 0.150, "cached_input": 0.030, "output": 0.500},
    ("z-ai/glm-5.3-flash", "coreweave"): {"input": 0.150, "cached_input": 0.050, "output": 0.500},
}


# Transports that actually bill.  ``LOCAL_OPENAI_COMPATIBLE`` is self-hosted and
# genuinely free; every other provider mode spends real money and must be
# priced.  Keeping this a set rather than a single ``== "REMOTE_API"`` test is
# the fix for OpenRouter traffic being recorded at $0.00 by construction.
BILLED_PROVIDER_MODES = frozenset({"REMOTE_API", "OPENROUTER_API"})

PRICING_STATUS_PRICED = "priced"
PRICING_STATUS_UPPER_BOUND = "priced_upper_bound"
PRICING_STATUS_UNPRICED = "unpriced_model"
PRICING_STATUS_NOT_BILLED = "not_billed"
# The model is priced but the tier that served the call is not in
# ``SERVICE_TIER_MULTIPLIER``.  The cost is still recorded, at standard rates,
# and the status names the uncertainty instead of hiding it -- a new 2x tier
# silently billed as 1x is the same class of understatement as an unpriced model.
PRICING_STATUS_TIER_UNKNOWN = "priced_tier_unknown"

# Statuses whose ``estimated_cost_usd`` is a real number rather than 0.0.
PRICING_STATUSES_WITH_COST = frozenset(
    {PRICING_STATUS_PRICED, PRICING_STATUS_UPPER_BOUND, PRICING_STATUS_TIER_UNKNOWN}
)

_WARNED_UNPRICED: set = set()


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


def _routed_endpoint(response: Any) -> str:
    """Which OpenRouter endpoint actually served this call.

    ``OPENROUTER_ALLOWED_PROVIDERS`` is an ordered list with
    ``allow_fallbacks=false``: the first entry is preferred but a later one
    serves the request when the first is rate-limited or down.  Endpoints of the
    same model bill at different rates, so the configured list cannot tell us
    what a call cost -- only the response can.  OpenRouter returns the serving
    endpoint as a top-level ``provider`` field on the completion.
    """

    routed = getattr(response, "provider", None)
    if not routed:
        payload = _plain(response)
        if isinstance(payload, dict):
            routed = payload.get("provider")
    return str(routed or "").strip()


def _canonical_model(model: str) -> str:
    """Fold vendor prefixes, effort suffixes and dated snapshots onto the priced row.

    ``gpt-5.6-luna``, ``gpt-5.6-luna-low``, ``openai/gpt-5.6-luna-high`` and
    ``gpt-5.6-luna-2026-06-01`` are the same billed model at the same rate --
    effort changes token counts, not $/Mtok, and a snapshot date changes
    neither.  Every fold is gated on the remainder already being priced, and an
    id that is itself priced short-circuits first, so this can never redirect a
    genuinely distinct model onto someone else's rate.
    """

    key = str(model or "").strip()
    if not key or key in PRICE_PER_MILLION:
        return key
    lowered = key.lower()
    for prefix in VENDOR_MODEL_PREFIXES:
        if lowered.startswith(prefix):
            key = key[len(prefix) :]
            lowered = key.lower()
            break
    if key in PRICE_PER_MILLION:
        return key
    stripped = _SNAPSHOT_DATE_SUFFIX.sub("", key)
    if stripped != key and stripped in PRICE_PER_MILLION:
        return stripped
    # An effort suffix can sit outside a snapshot date (``-high-2026-06-01``),
    # so match against the date-stripped form rather than the raw id.
    candidate = stripped if stripped != key else key
    lowered = candidate.lower()
    for suffix in REASONING_EFFORT_MODEL_SUFFIXES:
        tail = "-" + suffix
        if lowered.endswith(tail):
            base = candidate[: -len(tail)]
            if base in PRICE_PER_MILLION:
                return base
    return key


def _tier_multiplier(service_tier: str) -> tuple[float, bool]:
    """Return ``(multiplier, known)`` for a service tier.

    An unrecognised tier is charged at standard rates and reported as
    ``PRICING_STATUS_TIER_UNKNOWN`` rather than guessed at, so the row says the
    rate is unverified instead of asserting a number nobody published.
    """

    key = str(service_tier or "").strip().lower()
    multiplier = SERVICE_TIER_MULTIPLIER.get(key)
    if multiplier is None:
        return 1.0, False
    return multiplier, True


def _rates(model: str, routed_endpoint: str = "") -> tuple[Optional[Dict[str, float]], bool]:
    """Return ``(rates, exact)`` for a model, preferring the endpoint that served it.

    ``exact`` is False when the per-endpoint rate was unavailable and the
    model-level upper bound was substituted.
    """

    key = _canonical_model(model)
    endpoint = str(routed_endpoint or "").strip().lower()
    if endpoint:
        exact = PRICE_PER_MILLION_BY_ENDPOINT.get((key, endpoint))
        if exact is not None:
            return exact, True
    fallback = PRICE_PER_MILLION.get(key)
    if fallback is None:
        return None, False
    # A model that is not routed per endpoint at all (the official OpenAI
    # models) has exactly one rate, so its model-level row *is* exact.  A model
    # that does have per-endpoint rates but whose route we could not identify
    # is charged at the upper bound instead, so an unknown route over-reports
    # rather than under-reports.
    return fallback, not _model_has_endpoint_rates(key)


def _model_has_endpoint_rates(model: str) -> bool:
    return any(m == model for m, _ in PRICE_PER_MILLION_BY_ENDPOINT)


def _pricing_status(
    model: str,
    provider_mode: str,
    routed_endpoint: str = "",
    service_tier: str = "",
) -> str:
    """Separate "this transport is free" from "we have no price for this model".

    Both used to collapse into ``estimated_cost_usd=0.0``, which is how 948
    ``gpt-5.6-luna`` calls came to be recorded as costing nothing: the model was
    absent from ``PRICE_PER_MILLION`` and an absent price returned zero, which is
    indistinguishable from a genuinely free call.  Naming the third state makes
    an unpriced model a visible gap instead of a silent understatement.
    """

    if str(provider_mode or "").upper() not in BILLED_PROVIDER_MODES:
        return PRICING_STATUS_NOT_BILLED
    rates, exact = _rates(model, routed_endpoint)
    if rates is None:
        return PRICING_STATUS_UNPRICED
    _multiplier, tier_known = _tier_multiplier(service_tier)
    if not tier_known:
        return PRICING_STATUS_TIER_UNKNOWN
    return PRICING_STATUS_PRICED if exact else PRICING_STATUS_UPPER_BOUND


# Each gap gets its own sentence.  Reusing the "reported as 0.0" wording for a
# priced-but-unverified rate would state something untrue about the row and
# point at the wrong table to fix it.
_PRICING_GAP_ADVICE = {
    PRICING_STATUS_UNPRICED: (
        "estimated_cost_usd is reported as 0.0 and understates the real spend; "
        "add the model to PRICE_PER_MILLION"
    ),
    PRICING_STATUS_UPPER_BOUND: (
        "estimated_cost_usd is the model-level upper bound, not this route's rate; "
        "add the route to PRICE_PER_MILLION_BY_ENDPOINT"
    ),
    PRICING_STATUS_TIER_UNKNOWN: (
        "estimated_cost_usd is charged at standard rates because this service tier "
        "has no published multiplier; add it to SERVICE_TIER_MULTIPLIER"
    ),
}


def _warn_unpriced_once(
    model: str, provider_mode: str, status: str = PRICING_STATUS_UNPRICED
) -> None:
    key = f"{provider_mode}:{model}"
    if key in _WARNED_UNPRICED:
        return
    _WARNED_UNPRICED.add(key)
    advice = _PRICING_GAP_ADVICE.get(status, _PRICING_GAP_ADVICE[PRICING_STATUS_UNPRICED])
    print(
        f"[usage_logger] {status} model={model} provider_mode={provider_mode} {advice}",
        file=sys.stderr,
    )


def _estimated_cost(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    routed_endpoint: str = "",
    service_tier: str = "",
) -> float:
    rates, _exact = _rates(model, routed_endpoint)
    if not rates:
        return 0.0
    input_multiplier = 1.0
    output_multiplier = 1.0
    surcharge = LONG_CONTEXT_SURCHARGE.get(_canonical_model(model))
    if surcharge and input_tokens > surcharge["threshold_input_tokens"]:
        # The surcharge is applied to the whole request, not to the overflow.
        input_multiplier = surcharge["input"]
        output_multiplier = surcharge["output"]
    tier_multiplier, _tier_known = _tier_multiplier(service_tier)
    billable_input = max(0, input_tokens - cached_input_tokens)
    cost = (
        billable_input * rates["input"] * input_multiplier
        + cached_input_tokens * rates["cached_input"] * input_multiplier
        + output_tokens * rates["output"] * output_multiplier
    ) / 1_000_000.0
    return round(cost * tier_multiplier, 8)


def _response_service_tier(response: Any) -> str:
    """Which tier actually served the call.

    ``service_tier="auto"`` is a request, not an outcome: the API picks the tier
    and echoes it on the response.  Pricing the request value would bill an
    auto-resolved flex call at standard rates, so the response wins whenever it
    carries one.
    """

    tier = getattr(response, "service_tier", None)
    if not tier:
        payload = _plain(response)
        if isinstance(payload, dict):
            tier = payload.get("service_tier")
    return str(tier or "").strip()


def price_call(
    *,
    model: str,
    provider_mode: str,
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    routed_endpoint: str = "",
    service_tier: str = "",
) -> tuple[Optional[float], str]:
    """Single public entry point for "what did this call cost".

    Returns ``(cost, pricing_status)`` with ``cost=None`` whenever the status
    says no real number is available, so a caller cannot mistake "free" for
    "unknown" -- the distinction this module exists to preserve.  Every cost
    surface in the repository must come through here rather than re-deriving
    rates, or the two surfaces drift.
    """

    status = _pricing_status(model, provider_mode, routed_endpoint, service_tier)
    if status not in PRICING_STATUSES_WITH_COST:
        return None, status
    return (
        _estimated_cost(
            model,
            int(input_tokens or 0),
            int(cached_input_tokens or 0),
            int(output_tokens or 0),
            routed_endpoint,
            service_tier,
        ),
        status,
    )


# Public re-exports of the response readers.  Every cost surface has to pull
# cached tokens and the routed endpoint out of a response the same way, or the
# surfaces disagree about the same call -- so there is one implementation and
# these are the handles onto it, not a second copy.
def response_cached_input_tokens(response: Any) -> int:
    usage = _usage_from_response(response)
    return _int_value(
        _get_nested(usage, "input_tokens_details", "cached_tokens")
        or _get_nested(usage, "prompt_tokens_details", "cached_tokens")
    )


def response_routed_endpoint(response: Any) -> str:
    return _routed_endpoint(response)


def as_token_count(value: Any) -> int:
    return _int_value(value)


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
    service_tier: str = "",
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

    routed_endpoint = _routed_endpoint(response)
    # The tier the response reports beats the tier we asked for; ``auto`` is
    # resolved server-side and only the response knows what it resolved to.
    effective_service_tier = _response_service_tier(response) or str(service_tier or "").strip()
    price_multiplier, _tier_known = _tier_multiplier(effective_service_tier)
    pricing_status = _pricing_status(
        str(model or ""), str(provider_mode or ""), routed_endpoint, effective_service_tier
    )
    if pricing_status == PRICING_STATUS_UNPRICED:
        _warn_unpriced_once(str(model or ""), str(provider_mode or ""), pricing_status)
    elif pricing_status == PRICING_STATUS_TIER_UNKNOWN:
        _warn_unpriced_once(
            f"{model}@tier:{effective_service_tier}",
            str(provider_mode or ""),
            pricing_status,
        )
    elif pricing_status == PRICING_STATUS_UPPER_BOUND and routed_endpoint:
        # A route we have no rate for is not a crisis -- the upper bound keeps
        # the total honest -- but it does mean OpenRouter added or renamed an
        # endpoint, and the exact rate belongs in the table.
        _warn_unpriced_once(
            f"{model}@{routed_endpoint}", str(provider_mode or ""), pricing_status
        )

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
            _estimated_cost(
                str(model or ""),
                input_tokens,
                cached_input_tokens,
                output_tokens,
                routed_endpoint,
                effective_service_tier,
            )
            if pricing_status in PRICING_STATUSES_WITH_COST
            else 0.0
        ),
        # Which tier actually billed, and the multiplier that was applied to the
        # standard rate.  NDJSON only, for the same column-stability reason as
        # ``pricing_status`` below: a flex call and a standard call of the same
        # size differ by 2x and the row has to say which one it was.
        "service_tier": effective_service_tier,
        "price_multiplier": price_multiplier,
        # Which OpenRouter endpoint served the call.  Empty for single-endpoint
        # transports.  NDJSON only, for the same column-stability reason as
        # ``pricing_status`` below.
        "routed_endpoint": routed_endpoint,
        # Deliberately absent from CSV_FIELDS: ``extrasaction="ignore"`` drops it
        # from the CSV so the column layout stays stable for existing readers,
        # while the NDJSON row carries it.  Two incompatible CSV schemas already
        # coexist in the historical file and mis-align every column after the
        # insertion point; this does not add a third.
        "pricing_status": pricing_status,
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
