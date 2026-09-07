"""A provider outage must cost time, not requests.

Live evidence, RECORD_ONLY cohort ``13176437`` (2026-09-05). 591 requests were
exported; of the first 373 answered, 309 came back
``decision_source=provider_transport_error`` with
``decision_quality_tier=DEGRADED_NON_TRADING``. Splitting them by cause::

    PROVIDER_TRANSPORT_ERROR:remote_provider_models_failed:...Connection error.   77
    PROVIDER_TRANSPORT_ERROR:provider_circuit_open                               295

Only 77 were the outage. The other 295 were the circuit breaker: it opens for
``provider_circuit_cooldown_sec`` (60s) after
``provider_circuit_failure_threshold`` (3) failures, and every request three
workers claimed inside that window was refused instantly. The gate turns any
``ProviderCallError`` into a *terminal* degraded response and archives the
request, so the breaker permanently consumed four times more work than the
network failure it existed to protect against.

Two properties are pinned here:

1. an open breaker is a cooldown, so a request that still has deadline budget
   waits it out instead of being spent on it -- and a request that does *not*
   have the budget still fails closed, honestly labelled;
2. requests already burned this way can be restored, and the restoration can
   never replay a real AI judgement or invent a request that was not archived.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import ai_provider
from ai_provider import ProviderCallError, RemoteAPIProvider
from provider_deadline import DeadlinePolicy, RequestDeadline
from request_lifecycle import RequestIdempotencyLedger
from transport_failure_requeue import (
    apply_transport_failure_requeue,
    plan_transport_failure_requeue,
)


STALE_AFTER_SEC = 900.0


# --------------------------------------------------------------------------
# 1. the breaker
# --------------------------------------------------------------------------


def _provider(logs: list[str], *, cooldown_sec: float) -> RemoteAPIProvider:
    provider = RemoteAPIProvider(
        api_key="test-key",
        base_url="",
        primary_model="model-a",
        fallback_models=(),
        analytics_model="model-a",
        reasoning_effort="low",
        timeout_sec=300.0,
        max_output_tokens=2048,
        prompt_cache_enable=False,
        prompt_cache_key="",
        prompt_cache_retention="24h",
        service_tier="auto",
        flex_unavailable_retry_enable=False,
        flex_unavailable_max_retries=0,
        flex_unavailable_cooldown_sec=0.0,
        circuit_failure_threshold=3,
        circuit_cooldown_sec=cooldown_sec,
        log=logs.append,
    )
    # Slice the wait finely so the test spends milliseconds, not seconds. The
    # behaviour under test is "wait rather than fail", never a duration.
    provider._CIRCUIT_WAIT_SLICE_SEC = 0.005
    # The constructor clamps the cooldown to >= 1s; set it past the clamp so the
    # test measures behaviour rather than a wall clock.
    provider._circuit_cooldown_sec = float(cooldown_sec)
    return provider


def _health(*, healthy: bool, reason: str) -> ai_provider.ProviderHealth:
    return ai_provider.ProviderHealth(
        healthy=healthy,
        provider_mode="REMOTE_API",
        provider_id="openai_remote_api",
        endpoint_class="official_remote",
        model_id="model-a",
        model_available=healthy,
        structured_output_available=healthy,
        reason=reason,
    )


def _deadline(*, terminal_ms: int, min_attempt_ms: int = 100) -> RequestDeadline:
    policy = DeadlinePolicy(
        mt5_terminal_timeout_ms=terminal_ms,
        response_write_margin_ms=0,
        min_attempt_ms=min_attempt_ms,
    )
    return RequestDeadline.start("request-under-test", policy)


def _open_breaker(provider: RemoteAPIProvider) -> None:
    for _ in range(provider._circuit_failure_threshold):
        provider._record_failure()
    assert provider._circuit_open_until > time.monotonic()


def test_open_breaker_waits_out_the_cooldown_instead_of_burning_the_request():
    """The regression itself. Pre-fix this raised immediately and the gate
    turned that into a terminal DEGRADED_NON_TRADING answer."""
    logs: list[str] = []
    provider = _provider(logs, cooldown_sec=0.05)
    _open_breaker(provider)
    # A healthy probe on the far side of the cooldown.
    provider.healthcheck = lambda **_: _health(healthy=True, reason="ok")  # type: ignore[assignment]

    provider._circuit_check(
        request_metadata={
            "request_id": "request-under-test",
            "deadline": _deadline(terminal_ms=60_000),
        }
    )

    joined = "\n".join(logs)
    assert "action=wait_for_cooldown" in joined
    assert "action=cooldown_cleared_proceeding" in joined
    # Waiting is only correct if it actually clears the breaker.
    assert provider._circuit_open_until == 0.0


def test_a_request_without_budget_for_the_cooldown_still_fails_closed():
    """Waiting must never eat the budget the response write needs. When the
    cooldown cannot fit, the old terminal failure is still the right answer."""
    logs: list[str] = []
    provider = _provider(logs, cooldown_sec=600.0)
    _open_breaker(provider)

    with pytest.raises(ProviderCallError) as excinfo:
        provider._circuit_check(
            request_metadata={
                "request_id": "request-under-test",
                "deadline": _deadline(terminal_ms=1_000),
            }
        )

    error = excinfo.value
    assert error.category == "PROVIDER_TRANSPORT_ERROR"
    assert "provider_circuit_open" in str(error)
    assert "action=fail_deadline_cannot_cover_cooldown" in "\n".join(logs)


def test_a_caller_with_no_deadline_never_blocks_a_worker():
    """No absolute deadline means nothing proves there is time to spend, so the
    breaker must refuse rather than park the worker for an unbounded wait."""
    logs: list[str] = []
    provider = _provider(logs, cooldown_sec=600.0)
    _open_breaker(provider)

    with pytest.raises(ProviderCallError):
        provider._circuit_check(request_metadata={"request_id": "no-deadline"})

    assert provider._circuit_wait_budget_ms(None) is None


def test_a_repeatedly_failing_healthcheck_terminates_inside_the_budget():
    """The recovery probe re-opens the breaker on failure. That loop must be
    bounded by the deadline, never spin forever."""
    logs: list[str] = []
    provider = _provider(logs, cooldown_sec=0.05)
    _open_breaker(provider)
    provider.healthcheck = lambda **_: _health(  # type: ignore[assignment]
        healthy=False, reason="probe_refused"
    )

    with pytest.raises(ProviderCallError):
        provider._circuit_check(
            request_metadata={
                "request_id": "request-under-test",
                "deadline": _deadline(terminal_ms=220, min_attempt_ms=100),
            }
        )

    assert "action=reopen_and_recheck_within_budget" in "\n".join(logs)


def test_a_short_circuit_never_claims_an_http_request_was_sent():
    """The gate logged http_request_sent=true http_status=0 for a refusal that
    never touched the transport -- a local certainty dressed as a network fact."""
    logs: list[str] = []
    provider = _provider(logs, cooldown_sec=600.0)
    _open_breaker(provider)

    with pytest.raises(ProviderCallError) as excinfo:
        provider._circuit_check(
            request_metadata={"request_id": "r", "deadline": _deadline(terminal_ms=1_000)}
        )

    assert excinfo.value.provider_call_attempted is False
    assert excinfo.value.http_request_sent is False
    # A genuine call failure keeps reporting as before.
    assert ProviderCallError("PROVIDER_TRANSPORT_ERROR", "boom").http_request_sent is True


def test_the_gate_reports_the_error_s_own_flags_not_a_hardcoded_true():
    """Pinned against the source: the two flags were literal ``True`` for every
    ProviderCallError, so no attribute the error carried could ever be read."""
    source = (Path(__file__).resolve().parents[1] / "ai_gate.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert 'getattr(e, "provider_call_attempted", True)' in source
    assert 'getattr(e, "http_request_sent", True)' in source
    assert "True if provider_failure else local_failure.provider_call_attempted" not in source
    assert "True if provider_failure else local_failure.http_request_sent" not in source


# --------------------------------------------------------------------------
# 2. recovering what the breaker already burned
# --------------------------------------------------------------------------


REQUEST_ID = "591813800_1785715200_13176437_1785719999_EURCAD_31333"
IDENTITY_HASH = "a" * 64


def _request_payload(request_id: str = REQUEST_ID) -> dict:
    return {
        "id": request_id,
        "symbol": "EURCAD",
        "workload_mode": "backtest",
        "plan": {"entry": 1.62728},
        "candidates": [{"candidate_id": "c0"}],
    }


def _degraded_response(
    request_id: str = REQUEST_ID,
    *,
    source: str = "provider_transport_error",
) -> dict:
    return {
        "id": request_id,
        "request_identity_hash": IDENTITY_HASH,
        "decision_state": "REJECT",
        "decision_source": source,
        "decision_quality_tier": "DEGRADED_NON_TRADING",
        "final_allow": False,
    }


def _bus(tmp_path: Path) -> Path:
    bus = tmp_path / "PO3_AI_BUS"
    for name in (
        "requests",
        "responses",
        "rejected",
        "processing",
        "completed",
        "quarantined",
        "stale",
        "timed_out",
        "request_ledger",
        "logs",
    ):
        (bus / name).mkdir(parents=True, exist_ok=True)
    return bus


def _seed(
    bus: Path,
    *,
    request_id: str = REQUEST_ID,
    response: dict | None = None,
    archive: bool = True,
) -> None:
    payload = response if response is not None else _degraded_response(request_id)
    (bus / "responses" / f"{request_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    if archive:
        # Archived under the claim prefix the lifecycle really uses.
        (bus / "rejected" / f"python_1840_1788611869__{request_id}.json").write_text(
            json.dumps(_request_payload(request_id)), encoding="utf-8"
        )


def _ledger(bus: Path) -> RequestIdempotencyLedger:
    return RequestIdempotencyLedger(bus / "request_ledger", worker_id="test-worker")


def _archive_ledger_row(
    ledger: RequestIdempotencyLedger,
    bus: Path,
    request_id: str = REQUEST_ID,
) -> None:
    """The state a burned request is really left in: ARCHIVED, carrying the
    degraded answer as ``validated_response``."""
    ledger.begin(
        request_id=request_id,
        request_identity_hash=IDENTITY_HASH,
        provider_id="openai_remote_api",
        model_id="gpt-5.6-luna",
        prompt_contract_version="v12",
        schema_fingerprint="fp",
        # At claim time the response does not exist yet; that ordering is what
        # lets the row be written at all, and it is how the burned rows on the
        # live bus were really produced.
        response_path=bus / "responses" / f"{request_id}.not-yet-written.json",
        stale_after_sec=STALE_AFTER_SEC,
    )
    ledger.transition(
        request_id,
        "ARCHIVED",
        extra={"validated_response": _degraded_response(request_id)},
    )


def _apply(bus: Path, **kwargs):
    return apply_transport_failure_requeue(
        bus,
        ledger=_ledger(bus),
        stale_after_sec=STALE_AFTER_SEC,
        **kwargs,
    )


def test_a_transport_burned_request_returns_to_the_queue(tmp_path: Path):
    bus = _bus(tmp_path)
    _seed(bus)
    summary = _apply(bus)

    assert summary.requeued == 1
    assert (bus / "requests" / f"{REQUEST_ID}.json").is_file()
    # The degraded answer must go: while it exists the ledger reuses it.
    assert not (bus / "responses" / f"{REQUEST_ID}.json").exists()
    restored = json.loads((bus / "requests" / f"{REQUEST_ID}.json").read_text("utf-8"))
    assert restored == _request_payload()


def test_the_ledger_stops_handing_back_the_degraded_answer(tmp_path: Path):
    """The decisive one. Deleting the response is not enough: an ARCHIVED row
    stores validated_response and reuses it without any provider call."""
    bus = _bus(tmp_path)
    _seed(bus)
    ledger = _ledger(bus)
    _archive_ledger_row(ledger, bus)

    before = ledger.begin(
        request_id=REQUEST_ID,
        request_identity_hash=IDENTITY_HASH,
        provider_id="openai_remote_api",
        model_id="gpt-5.6-luna",
        prompt_contract_version="v12",
        schema_fingerprint="fp",
        response_path=bus / "responses" / f"{REQUEST_ID}.json",
        stale_after_sec=STALE_AFTER_SEC,
    )
    assert before.action == "REUSE"

    _apply(bus)

    after = _ledger(bus).begin(
        request_id=REQUEST_ID,
        request_identity_hash=IDENTITY_HASH,
        provider_id="openai_remote_api",
        model_id="gpt-5.6-luna",
        prompt_contract_version="v12",
        schema_fingerprint="fp",
        response_path=bus / "responses" / f"{REQUEST_ID}.json",
        stale_after_sec=STALE_AFTER_SEC,
    )
    assert after.action == "PROCESS"


def test_a_real_ai_judgement_is_never_replayed_away(tmp_path: Path):
    """An approve, a reject and an abstain are answers. Only an outage is not."""
    bus = _bus(tmp_path)
    full = _degraded_response()
    full["decision_quality_tier"] = "FULL_STRUCTURED"
    full["decision_source"] = "ai_rejected"
    _seed(bus, response=full)

    summary = _apply(bus)

    assert summary.requeued == 0
    assert summary.skipped.get("real_ai_judgement_protected") == 1
    assert (bus / "responses" / f"{REQUEST_ID}.json").is_file()


@pytest.mark.parametrize(
    "source",
    ["hard_pre_gate", "structured_response_invalid", "local_pipeline_error"],
)
def test_other_degraded_sources_are_left_alone(tmp_path: Path, source: str):
    """These name a decision about this request's own content, not an outage."""
    bus = _bus(tmp_path)
    _seed(bus, response=_degraded_response(source=source))

    summary = _apply(bus)

    assert summary.requeued == 0
    assert summary.skipped.get("decision_source_not_recoverable") == 1


def test_a_request_that_was_never_archived_is_not_invented(tmp_path: Path):
    bus = _bus(tmp_path)
    _seed(bus, archive=False)

    summary = _apply(bus)

    assert summary.requeued == 0
    assert summary.skipped.get("original_request_not_archived") == 1
    assert not (bus / "requests" / f"{REQUEST_ID}.json").exists()
    # The answer stays until a replacement can actually be produced.
    assert (bus / "responses" / f"{REQUEST_ID}.json").is_file()


def test_a_response_is_never_mistaken_for_the_archived_request(tmp_path: Path):
    """Responses are archived too. Restoring one as a request would put an
    unprocessable artifact in the queue."""
    bus = _bus(tmp_path)
    _seed(bus, archive=False)
    (bus / "rejected" / f"python_1840_1788611869__{REQUEST_ID}.json").write_text(
        json.dumps(_degraded_response()), encoding="utf-8"
    )

    summary = _apply(bus)

    assert summary.requeued == 0
    assert summary.skipped.get("original_request_not_archived") == 1


def test_a_request_another_worker_is_running_is_not_reclaimed(tmp_path: Path):
    bus = _bus(tmp_path)
    _seed(bus)
    ledger = _ledger(bus)
    ledger.begin(
        request_id=REQUEST_ID,
        request_identity_hash=IDENTITY_HASH,
        provider_id="openai_remote_api",
        model_id="gpt-5.6-luna",
        prompt_contract_version="v12",
        schema_fingerprint="fp",
        response_path=bus / "responses" / "missing.json",
        stale_after_sec=STALE_AFTER_SEC,
    )
    ledger.transition(REQUEST_ID, "PROVIDER_RUNNING")

    summary = _apply(bus)

    assert summary.requeued == 0
    assert summary.skipped.get("request_active_in_another_worker") == 1


def test_an_already_queued_request_is_not_duplicated(tmp_path: Path):
    bus = _bus(tmp_path)
    _seed(bus)
    (bus / "requests" / f"{REQUEST_ID}.json").write_text(
        json.dumps(_request_payload()), encoding="utf-8"
    )

    summary = _apply(bus)

    assert summary.requeued == 0
    assert summary.skipped.get("request_already_queued") == 1


def test_the_cohort_filter_confines_the_blast_radius(tmp_path: Path):
    bus = _bus(tmp_path)
    other_id = "591813800_1785715200_99999999_1785719999_GOLD_11111"
    _seed(bus)
    _seed(bus, request_id=other_id)

    summary = _apply(bus, cohort="13176437")

    assert summary.requeued == 1
    assert summary.requeued_request_ids == [REQUEST_ID]
    assert summary.skipped.get("outside_requested_cohort") == 1
    assert (bus / "responses" / f"{other_id}.json").is_file()


def test_the_inventory_changes_nothing(tmp_path: Path):
    bus = _bus(tmp_path)
    _seed(bus)

    candidates, summary = plan_transport_failure_requeue(
        bus, ledger=_ledger(bus), stale_after_sec=STALE_AFTER_SEC
    )

    assert summary.dry_run is True
    assert summary.eligible == 1
    assert [c.request_id for c in candidates if c.eligible] == [REQUEST_ID]
    assert (bus / "responses" / f"{REQUEST_ID}.json").is_file()
    assert not (bus / "requests" / f"{REQUEST_ID}.json").exists()


def test_every_requeue_leaves_an_audit_record(tmp_path: Path):
    bus = _bus(tmp_path)
    _seed(bus)
    _apply(bus)

    audit = bus / "logs" / "transport_failure_requeue.ndjson"
    record = json.loads(audit.read_text(encoding="utf-8").strip())
    assert record["request_id"] == REQUEST_ID
    assert record["decision_source"] == "provider_transport_error"
    assert record["restored_from"].endswith(f"__{REQUEST_ID}.json")
