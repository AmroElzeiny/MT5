# Mission

Front-end: Claude
Tier: Standard
RuntimeMode: Offline

## Outcome

A provider **configuration circuit opened by HTTP 403 `permission_denied` must stop being a
permanent latch for the life of the `ai_gate.py` process.** It must expire after a cooldown and
recover through a single probe call, while every other configuration-block reason keeps exactly
today's permanent, fail-closed behaviour.

## Confirmed baseline (front-end verified 2026-09-13, do not rediscover)

Live incident, gate session `python_376_1789303136` (15:38-15:56 local):

- The first Muse (`muse-spark-1.3-contributor`, provider `opencode_go_responses`) calls returned
  HTTP 403 after 25-151 s: `[provider_call_failed] ... http_status=403 error_category=PROVIDER_CONFIGURATION_ERROR`
  and `fallback_reason=PROVIDER_CONFIGURATION_ERROR:PROVIDER_CONFIGURATION_ERROR:permission_denied`.
- From then on, **159 of 159** Muse calls failed in 0.007-0.96 s with
  `fallback_reason=PROVIDER_CONFIGURATION_ERROR:provider_configuration_block:PROVIDER_CONFIGURATION_ERROR`
  — never sent to the wire. 15 `[provider_configuration_block]` lines (one key per role schema).
- Front-end live probes at ~16:05 against the same endpoint, key and one of the very requests
  that got the 403: Muse answered **HTTP 200, schema-valid, 2 of 2 full-size requests**. So the
  403 was transient, and the gate kept Muse dead only because of the latch below.

Root cause, `python/ai_provider.py` (class `_OpenAICompatibleProviderBase`):

- `self._configuration_circuits: dict[str, str] = {}` (~584).
- `_schema_circuit_key` (~859): key = provider_id + model + decision_schema_version +
  prompt_contract_version + schema_fingerprint.
- `_configuration_circuit_check` (~876) raises `ProviderCallError("PROVIDER_CONFIGURATION_ERROR",
  "provider_configuration_block:"+reason, configuration_block=True, provider_call_attempted=False,
  http_request_sent=False)` whenever the key is present.
- `_open_configuration_circuit` (~888) stores the reason and logs `[provider_configuration_block]`.
- `clear_configuration_circuit` (~896) is called only after a **successful validated** call
  (~1555 Responses loop, ~2220 chat-completions loop).
- `_prepare_schema` (~1013-1037) runs `_configuration_circuit_check(key)` **before** any request
  is sent. Therefore once a key is open, no call can ever succeed, so it is never cleared:
  **a permanent latch until process restart.**
- Openers: `_prepare_schema` for local schema preflight (~1028), the Responses loop (~1613) and
  the chat-completions loop (~2306), both via `self._open_configuration_circuit(circuit_key,
  failure.category)` — note the stored reason is the **category** string
  (`PROVIDER_CONFIGURATION_ERROR`), not `permission_denied`.
- Classifier `_classify_transport_exception` (~920-1011): 403 -> `ProviderCallError(
  "PROVIDER_CONFIGURATION_ERROR", "permission_denied", status_code=403, configuration_block=True)`.
  Other configuration blocks: 400 invalid schema (`STRUCTURED_SCHEMA_INVALID`), 400 unsupported
  response_format, 401 `authentication_failed`, 400 invalid model, 404 OpenRouter
  `router_no_eligible_endpoint`. There is a second status mapper near ~127-130
  (`permission_denied:http_403`) — check whether any path reaching the circuit uses it.
- `OpenCodeRoutedProvider` (~3440-3467) forwards `clear_configuration_circuit` /
  `configuration_circuit_count` to its legs and falls back to the next leg on a
  `configuration_block` error. That fallback behaviour must not change.
- The separate **transport** breaker (`_circuit_check` ~1058-1148, `_record_success`,
  `_record_failure`) is a different mechanism with its own cooldown/health probe. Do not modify it.
- Existing tests pinning today's behaviour: `python/tests/test_zero_trade_reliability.py`
  `test_http_400_invalid_schema_opens_nonretryable_schema_circuit` (~106) and
  `test_http_401_opens_configuration_circuit_without_per_candidate_retry` (~146), both asserting
  `calls == 1` on the second call. They must keep passing unchanged.
- `python/ai_provider.py`, `python/ai_gate.py` and many other files are already dirty in git from
  earlier work. Preserve every existing uncommitted change; edit on top of it.

## Requirements

- CC-001: Recoverability is decided from the **classified failure**, not by string-matching the
  stored category. Exactly one reason is recoverable: HTTP 403 `permission_denied`
  (`status_code == 403`). Pass this explicitly when opening the circuit (e.g. a `recoverable`
  flag or a structured record), for every call site that opens a circuit after a classified
  provider failure (Responses loop and chat-completions loop, plus any other opener found).
- CC-002: A recoverable circuit records its open time and expires after
  `configuration_recovery_cooldown_sec` (new optional constructor kwarg on the base provider,
  default **300.0**, clamped to >= 1.0, threaded through subclass constructors only where they
  already forward `circuit_cooldown_sec`-style kwargs; no env/config/ai_gate plumbing in this
  mission). Use `time.monotonic()`.
- CC-003: Within the cooldown, behaviour is byte-identical to today: immediate
  `ProviderCallError` with the same category, message prefix, `configuration_block=True`,
  `provider_call_attempted=False`, `http_request_sent=False`, and no wire call.
- CC-004: After expiry, **exactly one** caller per key becomes the probe and is allowed to send.
  Any concurrent caller for the same key while that probe is in flight gets the CC-003 block
  immediately (no waiting, no extra wire call). Thread-safe under `self._state_lock`.
- CC-005: Probe outcomes: validated success -> circuit cleared (existing clear path);
  403 again -> circuit re-opened with a fresh cooldown; any other outcome (transport error,
  schema/validation failure, deadline, any exception, including exceptions raised before the
  HTTP request is sent) -> probe slot released **without** clearing, so the next caller may probe.
  Release must be guaranteed on every exit path (try/finally or equivalent). No leaked probe slot.
- CC-006: Every non-recoverable reason stays permanent for the process lifetime exactly as today,
  even after any amount of time: 401, invalid schema, unsupported response_format, invalid model,
  router_no_eligible_endpoint, local schema preflight.
- CC-007: Telemetry. Extend the existing `[provider_configuration_block]` line (keep its prefix and
  existing fields in order) with `recoverable=true|false` and, when recoverable,
  `cooldown_sec=<n>`. Add `[provider_configuration_probe] provider_id=... key=<16 chars>
  action=probe_started|cleared|reopened|released_without_verdict`. No secrets, no request bodies.
- CC-008: `configuration_circuit_count()` keeps counting open circuits (a recoverable circuit
  counts while it exists). `OpenCodeRoutedProvider` fallback, routing, exactly-once-per-leg,
  deadline rule and all telemetry fields other than CC-007 stay unchanged.
- TST-001: New test file `python/tests/test_configuration_circuit_recovery.py` with deterministic
  tests (patch the monotonic clock or use a tiny cooldown; no real sleeps longer than 1 s; no
  network). At least:
  1. 403 opens the circuit; a second call inside the cooldown is blocked with zero extra wire calls.
  2. After the cooldown one call reaches the wire; on success the circuit is cleared and later
     calls go straight to the wire.
  3. A probe that gets 403 again re-opens with a fresh cooldown.
  4. Single probe: while a probe is in flight (fake client blocked on a `threading.Event`), a
     concurrent caller for the same key is blocked immediately without a wire call.
  5. Probe slot released after a non-403 failure (e.g. HTTP 500) and after an exception raised
     before send; the next caller can probe.
  6. 401 stays blocked after the clock is advanced far past the cooldown (wire calls stay 1).
  7. Invalid-schema 400 and local schema preflight stay blocked after the clock advance.
  8. The chat-completions loop (e.g. `LocalOpenAICompatibleProvider`/`OpenRouterProvider`) recovers
     from 403 the same way as the Responses loop.
  9. Incident replay through `OpenCodeRoutedProvider`: Muse 403 -> call falls back to the
     secondary; inside the cooldown Muse is skipped without a wire call; after the cooldown Muse is
     tried again and its answer is the one returned.
  10. Telemetry lines of CC-007 appear with the right `recoverable=` values.
  Falsify every new test against the pre-change `ai_provider.py` (copy the pre-change file to a
  temp directory and import from there, or reason explicitly per test) and record which fail.
- TST-002: Record the pre-change full-suite baseline first (from `python/`). Then run the new file,
  `test_zero_trade_reliability.py`, `test_opencode_provider_contract.py`, every
  `test_openrouter_*` file, any test referencing `ProviderCallError`/`configuration_block`, then the
  full Python suite. Record exact commands and counts.
- SAF-001: Offline only. No real provider calls. Do not read or edit `python/.env`. Do not touch the
  Common Files bus, any `ai_gate.py` process or the MT5 terminal. No MQL change. No change to
  schemas, contract/prompt versions, thresholds, admission-retry logic, the transport breaker, the
  deadline contract, fallback chain order, or the classification of any status code.

## Work packages

### WP1 — Recoverable 403 configuration circuit + tests

Objective: CC-001..CC-008, TST-001.
Runtime authority: provider transport layer only; decision authority unchanged (blocked calls still
fail closed and fall back exactly as today).
Allowed scope: `python/ai_provider.py` (configuration-circuit state and helpers, the two call
sites that open circuits after classified failures, `_prepare_schema`, constructor kwarg
forwarding), `python/tests/test_configuration_circuit_recovery.py` (new).
Forbidden changes: `python/.env`, `python/ai_gate.py`, MQL, schemas, other tests' assertions,
transport breaker, admission retry, deadline logic.
Acceptance evidence: diff within scope; new tests fail pre-change and pass after; the two existing
`test_zero_trade_reliability.py` configuration-circuit tests pass unchanged.
Tests: TST-001.
Escalation triggers: any existing test outside scope must change; the probe-slot design cannot be
made leak-free without touching `ai_gate.py`; a 403 path is found that does not go through
`_classify_transport_exception`.

### WP2 — Verification and independent review

Objective: TST-002, complete diff audit (including preservation of pre-existing dirty changes), one
independent logic review focused on: permanent reasons really stay permanent; no leaked probe
slot on any exit path; single-probe under concurrency; blocked-call error identical to today;
no change to fallback/deadline/admission behaviour; no secret in logs.
Allowed scope: tests and review fixes within WP1 scope.

## Budgets

- target wall clock: 90 minutes
- material model invocations: <= 8
- repair attempts per approach: <= 2
- normal low-cost model map; no vision

## Runtime safety

- RuntimeMode: Offline
- Demo authorization: NO
- Real-money/live mutation: forbidden

## Final proof

- requirement coverage per ID; changed files/functions; exact test commands and counts (baseline,
  focused, related, full); pre-change falsification per new test; final diff audit; independent
  review findings and closure; actual model IDs and budget; unresolved uncertainty (notably: the
  300 s cooldown is a front-end choice, not measured — the observed 403 cleared within ~25 min).
