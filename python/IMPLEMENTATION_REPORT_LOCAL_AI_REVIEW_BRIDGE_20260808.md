# Local AI Review Bridge as PO3's sole AI transport — 2026-08-08

## 1. Confirmed root causes

### RC-1 (critical) — the local provider ignored the absolute request deadline

`RemoteAPIProvider.generate_structured` reads `request_metadata["deadline"]`,
narrows the SDK timeout to `deadline.provider_timeout_sec(...)`, refuses to start
an attempt that cannot finish, and logs `[provider_deadline_exceeded]`.

`LocalOpenAICompatibleProvider.generate_structured` did **none** of that. It
handed the SDK the timeout fixed at construction (`self.timeout_sec`) and never
consulted the deadline at all, even though `ai_gate` passes the same
`request_metadata["deadline"]` to both providers
([ai_gate.py:2988](ai_gate.py#L2988), [ai_gate.py:3539](ai_gate.py#L3539)).

The comment at [ai_gate.py:1661-1664](ai_gate.py#L1661-L1664) — *"The absolute
deadline narrows the real SDK timeout inside `generate_structured`"* — was only
true for the remote branch.

With the bridge deployment (`AI_MT5_TERMINAL_TIMEOUT_SEC=180`,
`AI_RESPONSE_WRITE_MARGIN_SEC=15` → Python response deadline 165s) a
`LOCAL_AI_TIMEOUT_SEC=180` browser call could block the full 180s: past the
Python deadline, past the MT5 terminal deadline, with zero margin left to write
an authoritative response.

This is latent on a GPU server that answers in seconds. It is a guaranteed
failure on a transport whose whole design is a human reading a chat.

### RC-2 (blocker) — the capability probe was invisible at the HTTP boundary

`healthcheck(probe_structured=True)` runs at startup and every 60s
([ai_gate.py:907-925](ai_gate.py#L907-L925)) and is the gate that decides whether
live trading has AI authority ([ai_gate.py:7226](ai_gate.py#L7226)).

The bridge answers that probe itself instead of queueing a browser review, but it
identifies the probe by reading `request_id` out of the **message bodies**
(`bridge/prompting.py`). PO3 declared the probe only in `request_metadata`, which
never crosses the wire on the chat-completions transport — the probe evidence was
literally `{"probe": "return ok=true"}`.

Result: every startup health probe would have been queued as a real browser
review and blocked until the bridge's 180s job deadline expired, then reported
`local_structured_output_probe_failed`. AI authority would never come up.

### RC-3 — the model-listing GET inherited the generation timeout

`_fetch_health` used `self.timeout_sec` (180s for the bridge). `healthcheck`
re-runs while requests are in flight, so one unresponsive bridge could consume an
entire MT5 terminal window before the gate could even decide it was unhealthy.

### RC-4 — imprecise health failure reason

A rejected bearer key, a 404 healthcheck path, and a dead port all reported
`local_healthcheck_failed:HTTPError`. Section 11 requires the precise reason.

### RC-5 (latent) — `UnboundLocalError` instead of a fail-closed category

Both providers referenced `preflight.schema_fingerprint` in their terminal
`ProviderCallError` even though `preflight` is bound inside the model loop. With
no model configured for a role the loop never runs and a configuration defect
surfaced as an unrelated Python crash rather than a categorised fail-closed
answer.

### RC-6 (bridge) — real jobs were labelled with a random uuid

`build_manual_prompt` read only a top-level `request_id`/`id`. PO3's analyst
evidence is the canonical decision-evidence envelope, which nests it under
`identity.request_id` ([decision_evidence.py:234](decision_evidence.py#L234)).
Every real analyst job therefore got a uuid, breaking the bridge's own queue
de-duplication and making the dashboard impossible to correlate with a PO3
request.

## 2. Why the visible symptom was shallower than the cause

The stated task is "point PO3 at the bridge", and the `.env` was already pointed
at it. Nothing about the *configuration* was wrong. The defects were in the code
path that configuration selects: the local transport had been written for a fast
local model and silently lacked the deadline discipline the remote transport
already had, and the two halves of the capability-probe handshake disagreed about
where the probe identifies itself.

## 3. Files changed

| File | Reason |
|---|---|
| `python/ai_provider.py` | RC-1, RC-2, RC-3, RC-4, RC-5 |
| `python/ai_gate.py` | `[hard_pre_gate]` pass/fail diagnostic; log wording `skipped_openai=` → `skipped_provider_calls=` |
| `python/.env` | `AI_RESPONSE_WRITE_MARGIN_SEC` 10 → 15 (secrets only, gitignored) |
| `python/.env.example` | Documents the bridge deployment block; no default changed |
| `python/tests/test_local_ai_review_bridge.py` | **New.** 45 tests, requirements A–O |
| `MT5 AI Review Bridge/bridge/prompting.py` | RC-6 |
| `MT5 AI Review Bridge/bridge/config.py` | Structural PO3-bus boundary guard |
| `MT5 AI Review Bridge/tests/test_po3_boundary.py` | **New.** 11 tests |

**No `.mq5` or `.mqh` file was modified** (`git status` shows zero MQL changes),
and no MQL change was required — see §9.

## 4. Functions changed

`ai_provider.py`
- `LocalOpenAICompatibleProvider.generate_structured` — absolute-deadline
  enforcement: `[provider_deadline]` log, pre-semaphore fail-fast, per-attempt
  `can_start_attempt()` re-check after queueing, `with_options(timeout=…,
  max_retries=0)` narrowing, deadline-aware retry stop, `last_preflight` guard.
- `LocalOpenAICompatibleProvider.healthcheck` — probe evidence carries
  `request_id=provider_capability_probe`; `ProviderCallError.category` used for
  the probe failure reason; `_health_failure_reason` for transport failures.
- `LocalOpenAICompatibleProvider._health_timeout_sec` — **new**, bounds the
  model-listing GET.
- `LocalOpenAICompatibleProvider._fetch_health` — uses the bounded timeout.
- `RemoteAPIProvider.generate_structured` — `last_preflight` guard (RC-5).
- `_health_failure_reason` — **new** module function.
- New constants `PROVIDER_CAPABILITY_PROBE_ID`,
  `LOCAL_HEALTHCHECK_MAX_TIMEOUT_SEC`.

`ai_gate.py`
- `_hard_pretrade_decision` — emits `[hard_pre_gate] passed=true|false`.
- `hard_pre_gate` reject logs — `skipped_provider_calls=`.

`bridge/prompting.py`
- `extract_request_id` — **new**; `build_manual_prompt` delegates to it.

`bridge/config.py`
- `PO3BusBoundaryError`, `assert_not_po3_bus` — **new**, run at import.

## 5. Schema / contract changes

**None.** Verified by recomputing the compatibility manifest from the pre-change
sources restored out of git:

```
BASELINE contract_manifest_hash = 1024838206
CURRENT  contract_manifest_hash = 1024838206
decision_schema_version = 20260724_canonical_frozen_request_v10
engine_version          = 5.5-version-z-canonical-request-20260724-v8
provider_contract       = 20260723_provider_neutral_transport_v2
deadline_contract       = 20260730_absolute_request_deadline_v1
prompt_contract         = 20260724_canonical_frozen_request_v12
target_schema           = 20260717_target_fingerprint_authority_v6
```

No `_generation_settings` field changed, so `generation_settings_hash`, request
identity, cache cohorts, and repeatability groups are all unchanged.

## 6. Provider / request flow

```text
MT5 EA (AIGateBridge.mqh, FileBus.mqh)  [unchanged]
  → PO3_AI_BUS/requests/<request_id>.json
  → ai_gate.py: stable-file check → claim → [request_claimed]
  → idempotency ledger: REUSE → [idempotency_hit] provider_call=false
                        ACTIVE/COLLISION → [duplicate_request_blocked]
  → absolute deadline started once  [provider_deadline]
  → canonicalize → taxonomy → freeze → identity hash → [request_created]
  → deterministic hard pre-gate     [hard_pre_gate] passed=true|false
        └─ failed → local reject, NO bridge call
  → provider health (cached 60s)    [ai_provider_health]
        └─ unhealthy + live → degraded non-trading, NO bridge call
  → live candidate cohort ≤ AI_LIVE_CANDIDATE_BUDGET (3)
  → LocalOpenAICompatibleProvider  [semaphore = 1]
        analyst → critic → adjudicator, one at a time, strict json_schema each
        SDK timeout = min(LOCAL_AI_TIMEOUT_SEC, deadline.remaining)
  → POST http://127.0.0.1:1234/v1/chat/completions
  → Local AI Review Bridge job → registered browser conversation → human review
  → bridge: strict JSON + caller JSON Schema → OpenAI-compatible reply
  → PO3 untrusted-output validation, unchanged:
        strict JSON (no trailing text, no duplicate keys)
        ModelAIGateOutput schema  [raw_model_schema_validation]
        request id / identity hash / candidate count / order / ids / hashes
        execution fingerprints, evidence refs, immutable entry+SL, targets
        [identity_validation] [evidence_reference_validation]
        [python_owned_field_echo] [authoritative_envelope_constructed]
        [ai_schema_validation]
  → deadline re-checked; late result → [provider_deadline_exceeded] quarantine
  → REQUEST_TERMINAL_REGISTRY.claim_terminal (exactly one terminal outcome)
  → atomic_write_json → PO3_AI_BUS/responses/<request_id>.json  [response_written]
  → MT5 strict response validation + candidate binding   [unchanged]
```

## 7. `.env` variables required

```env
AI_USE_REMOTE_API=false
LOCAL_AI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_AI_API_KEY=<BRIDGE_API_KEY from the bridge's own .env>
LOCAL_AI_MODEL=chatgpt-browser-review
LOCAL_AI_ANALYST_MODEL=chatgpt-browser-review
LOCAL_AI_CRITIC_MODEL=chatgpt-browser-review
LOCAL_AI_ADJUDICATOR_MODEL=chatgpt-browser-review
LOCAL_AI_FALLBACK_MODELS=
LOCAL_AI_HEALTHCHECK_PATH=/models
LOCAL_AI_REQUIRE_JSON_SCHEMA=true
LOCAL_AI_PARALLELISM=1
LOCAL_AI_MAX_RETRIES=0
LOCAL_AI_TIMEOUT_SEC=180
AI_MT5_TERMINAL_TIMEOUT_SEC=180
AI_RESPONSE_WRITE_MARGIN_SEC=15
AI_LIVE_CANDIDATE_BUDGET=3
AI_ENABLE_SNAPSHOTS=false
AI_HARD_PRE_GATE_BEFORE_OPENAI=true
AI_REQUIRE_RUNTIME_INPUTS_LIVE=true
AI_REJECT_ON_MISSING_RUNTIME_INPUTS_LIVE=true
AI_SHADOW_COMPARE_PROVIDERS=false
```

No key, chat URL, or credential is hardcoded or committed. `python/.gitignore`
excludes `.env` and `*.env`; the bridge key lives only in the bridge's own `.env`
and is copied into PO3's `.env` by `setup_po3_env.ps1`.

## 8. Commands and results

```powershell
# PO3 suite
cd "…\MT5\python"; .\.venv\Scripts\python.exe -m pytest tests -q
# baseline (before any change):  8 failed, 470 passed, 315 subtests
# after the change:              8 failed, 515 passed, 315 subtests

# new bridge contract tests only
.\.venv\Scripts\python.exe -m pytest tests/test_local_ai_review_bridge.py -q
# 45 passed

# bridge package suite
cd "…\MT5\MT5 AI Review Bridge"; .\.venv\Scripts\python.exe -m pytest tests -q
# 13 passed  (2 pre-existing + 11 new)
```

The same **8 pre-existing failures** before and after — all in
`tests/test_order_adapter_contract.py`, all caused by
`MT5_PO3_Codex Experts/PO3_AIGate_PositivePath_Harness.mq5` being absent from the
repository (`git log` for that path is empty; it was never committed). Unrelated
to AI transport. See §12.

### Regression tests proven to fail on the pre-fix code

The pre-fix `LocalOpenAICompatibleProvider` was reconstructed and the new suite
re-run against it — **9 of 45 failed**, and all 9 pass after the fix:

```
test_a_healthy_bridge_reports_healthy_and_probe_costs_no_review   (RC-2)
test_a_probe_identifies_itself_inside_the_wire_payload            (RC-2)
test_health_timeout_is_bounded_below_the_generation_timeout       (RC-3)
test_k_sdk_receives_the_remaining_budget_not_the_configured_timeout (RC-1)
test_k_full_budget_is_still_capped_by_the_python_response_deadline  (RC-1)
test_k_exhausted_budget_refuses_to_start_a_browser_call             (RC-1)
test_k_deadline_contract_is_logged_before_the_call                  (RC-1)
test_k_no_deadline_metadata_preserves_the_configured_timeout        (RC-1)
test_n_queued_role_call_respects_the_shared_absolute_deadline       (RC-1)
```

### Real-bridge integration proof (no stub on either side)

The **real** bridge was started on an isolated port 18234 (`AUTO_OPEN_BROWSER=false`,
`FOLDER_GATEWAY_ENABLED=false`) and the **real** `LocalOpenAICompatibleProvider`
was pointed at it from the PO3 venv:

```
/health → ok=true max_pending_jobs=3 hard_timeout_sec=180 human_review_required=true

A: healthy=True model_available=True structured_output_available=True
   endpoint_class=loopback model_id=chatgpt-browser-review reason=none
C: healthy=False reason=local_healthcheck_failed:authentication_failed:http_401
D: healthy=False reason=configured_local_model_not_available
K: [provider_deadline] … python_deadline_ms=165000 write_margin_ms=15000
      configured_provider_timeout_ms=180000
   [provider_call_started] … sdk_timeout=104.984 sdk_max_retries=0 remaining_ms=104984

GET /api/pending → []            (no browser review was consumed)
logs/audit.jsonl → no job_created event for the whole run
```

`sdk_timeout=104.984` against `configured_provider_timeout_ms=180000` is RC-1
fixed on real sockets: 165s budget − 60s already elapsed.

## 9. MQL compilation

Not applicable and not run: **zero** `.mq5`/`.mqh` files were modified. The
request/response contract is proven unchanged by the identical
`contract_manifest_hash=1024838206` in §5, which is the value MQL validates
against in `[contract_compatibility]`. Compiling unchanged sources against an
unchanged contract would produce no new evidence.

## 10. Proof of the required isolation properties

**No bridge → `responses/` bypass.** The bridge's only file writer is
`folder_gateway.process_file`, which writes to `settings.folder_response_dir`
(its own `folder_bus/responses`). `bridge/config.py` now refuses at import to
start if either folder-bus directory resolves inside a path containing a
`PO3_AI_BUS` component, including absolute overrides in `.env` and
case-insensitively. Tests: `test_po3_boundary.py` (4 boundary tests) and
`ResponseFolderOwnershipTests.test_no_bridge_source_references_the_po3_bus`.
PO3's authoritative write remains the single
`atomic_write_json(resp_path, resp, encoding=RESP_ENCODING)` behind
`REQUEST_TERMINAL_REGISTRY.claim_terminal`.

**No remote-API or local-LLM call in bridge mode.** `ai_gate` pops
`OPENAI_API_KEY`/`OPENAI_BASE_URL` from the process environment at import when
the selector is not `true` ([ai_gate.py:230-232](ai_gate.py#L230-L232)) and
excludes them from `.env` loading. `_build_ai_provider` reaches the remote branch
only when `use_remote_api` is `True`. `_build_non_selected_shadow_provider`
returns `UnavailableProvider("remote_shadow_forbidden_by_local_privacy_contract")`
whenever the selected provider is local. No local model is loaded:
`LOCAL_AI_MODEL_PATH` is empty and the provider is an HTTP client only.
Tests: `BridgeProviderIsolationTests` (6 tests), including one that plants
`OPENAI_API_KEY` in the environment and proves `RemoteAPIProvider` is still never
constructed and the model stays `chatgpt-browser-review`.

**Serialized transport.** `LOCAL_AI_PARALLELISM` is hard-clamped to exactly 1
(`_env_int(..., min_value=1, max_value=1)`), the provider is a process singleton
holding one `BoundedSemaphore(1)`, and `ai_gate` forces `worker_count = 1`
whenever the selected provider mode is local
([ai_gate.py:10044-10045](ai_gate.py#L10044-L10045)). Analyst, critic, and
adjudicator keep their own prompts and schemas and queue through that one
semaphore. Tests: `BridgeSerializationTests` — 4 threads and 3 roles both observe
`max_in_flight == 1` at the server.

**Candidate ceiling ≠ concurrency.** `AI_LIVE_CANDIDATE_BUDGET=3` with
`LOCAL_AI_PARALLELISM=1`; candidates beyond the ceiling are deferred by
`select_live_candidate_cohort`, never parallelised.
Tests: `BridgeCandidateBudgetTests` (3 tests).

## 11. Live / demo smoke-test procedure

1. **Start the bridge.**
   ```powershell
   cd "…\MT5 AI Review Bridge"; .\run_bridge.bat
   ```
2. **Verify health.** `http://127.0.0.1:1234/health` →
   `{"ok":true,"max_pending_jobs":3,"hard_timeout_sec":180,"human_review_required":true}`
3. **Verify the model registry.**
   ```powershell
   curl.exe -s -H "Authorization: Bearer $env:BRIDGE_API_KEY" http://127.0.0.1:1234/v1/models
   ```
   must list `chatgpt-browser-review`.
4. **Sign in.** The bridge's Chrome profile must be logged in with the registered
   PO3 conversation open.
5. **Start the gate.**
   ```powershell
   cd "…\MT5\python"; .\.venv\Scripts\python.exe ai_gate.py
   ```
   Expect at startup:
   ```
   [ai_gate] Selected provider: mode=LOCAL_OPENAI_COMPATIBLE id=local_openai_compatible endpoint_class=loopback analyst_model=chatgpt-browser-review cross_provider_fallback=false
   [ai_gate] Request workers:    1
   [ai_provider_health] healthy=true provider_mode=LOCAL_OPENAI_COMPATIBLE endpoint_class=loopback model_id=chatgpt-browser-review model_available=true structured_output_available=true reason=none
   ```
   The bridge dashboard must still show **0 pending jobs** — the probe costs no
   review.
6. **Start MT5 on a demo account** and attach `PO3_AIGate_ScannerEA` with
   `InpAiWaitTimeoutRealMin` matching `AI_MT5_TERMINAL_TIMEOUT_SEC=180`.
7. **Allow one real PO3 candidate** and confirm each step from the logs:

| Step | Log line | Where |
|---|---|---|
| MT5 created request | `[request_created] request_id=… request_identity_hash=…` | gate log |
| gate claimed request | `[request_claimed] request_id=… worker_id=…` | gate log |
| absolute deadline started | `[provider_deadline] … python_deadline_ms=165000 write_margin_ms=15000` | gate log |
| deterministic pre-gate passed | `[hard_pre_gate] … passed=true provider_call_permitted=true` | gate log |
| bridge provider called | `[provider_call_started] provider=local_openai_compatible model=chatgpt-browser-review role=analyst sdk_timeout=…` | gate log |
| bridge received the request | `{"event":"job_created","request_id":…}` | `bridge/logs/audit.jsonl` + dashboard |
| structured reply returned | `{"event":"job_completed","reviewed":true}` then `{"event":"openai_compat_response_returned"}` | bridge audit |
| provider completed | `[provider_call_completed] … quality_tier=FULL_STRUCTURED latency_sec=…` | gate log |
| PO3 schema validation passed | `[raw_model_schema_validation] valid=true` and `[ai_schema_validation] valid=true` | gate log |
| identity validation passed | `[identity_validation] … valid=true` | gate log |
| candidate binding passed | `[authoritative_envelope_constructed] …` | gate log |
| authoritative response written | `[response_written] request_id=… quality_tier=FULL_STRUCTURED path=<request_id>.json` | gate log |
| MQL consumed matching response | MT5 Experts tab: response schema valid + candidate binding valid for the same `request_id` | MT5 journal |

**Fail-closed spot checks (run all on demo before real money):**
stop the bridge → `[ai_provider_health] healthy=false … reason=local_healthcheck_failed:unreachable…`, no trade;
wrong `LOCAL_AI_API_KEY` → `reason=local_healthcheck_failed:authentication_failed:http_401`, no trade;
paste malformed JSON in the dashboard → bridge `structured_response_invalid`, gate `[provider_call_failed]`, no authoritative response;
let the 180s job expire → `[provider_deadline_exceeded] … late_result_action=quarantine`, no trade.

## 12. Remaining limitations (with evidence)

1. **8 pre-existing test failures, unrelated to this task.**
   `tests/test_order_adapter_contract.py` reads
   `MT5_PO3_Codex Experts/PO3_AIGate_PositivePath_Harness.mq5`, which does not
   exist in the repository and has no git history (`git log -- <path>` is empty;
   the Experts directory contains only `PO3_AIGate_ScannerEA.mq5/.ex5`). They fail
   identically before and after this change. Writing that harness EA is a separate
   piece of work about the test order adapter, not the AI transport.

2. **Human-review latency is the real budget.** All three roles share one 165s
   Python budget on one conversation. If the analyst review takes 120s, the
   critic gets ~45s and the adjudicator may be refused with
   `PROVIDER_DEADLINE_EXCEEDED`. That is correct fail-closed behaviour, not a
   defect, but it means the operator must answer promptly or accept ABSTAIN/
   degraded outcomes. Raising `AI_MT5_TERMINAL_TIMEOUT_SEC` (and the matching
   MQL `InpAiWaitTimeoutRealMin`) is the only way to buy more review time; it was
   left at the specified 180s.

3. **The bridge's own single-conversation guarantee is a deployment property.**
   PO3 serializes its side; if a second, unrelated client posted to the same
   bridge model id concurrently, the bridge would allow up to
   `MAX_PENDING_JOBS=3`. Keep port 1234 loopback-only and single-tenant.

4. **Runtime artifacts are written by the existing test suite.**
   `data/unknown_setup_taxonomy.jsonl` and `logs/ai_cost_report.jsonl` gain rows
   on every `pytest` run (request id `identity-e2e`, from
   `test_python_owned_identity_lifecycle`). Pre-existing test hygiene, present at
   the baseline run, untouched here.

5. **No live browser round trip was executed** during this work — that requires a
   signed-in operator. Every layer either side of the human paste was exercised
   against the real bridge process (§8) and against a bridge-shaped server over
   real sockets.

6. **Repeated deadline expiries open the provider circuit.** A deadline stop
   calls `_record_failure()`, so three consecutive expiries trip the existing
   breaker (`AI_PROVIDER_CIRCUIT_FAILURE_THRESHOLD=3`,
   `AI_PROVIDER_CIRCUIT_COOLDOWN_SEC=60`) and further requests fail closed with
   `provider_circuit_open` until a healthcheck succeeds. This is exactly the
   behaviour `RemoteAPIProvider` already had and was kept identical rather than
   forked; on a human-paced transport it simply means that three missed reviews
   in a row cost a 60s cooldown. It self-recovers: `_circuit_check` re-probes
   health and clears the circuit as soon as the bridge answers.

## 13. Potential issues reviewed and their state

| Reviewed | State |
|---|---|
| Provider timeout not applied to the real HTTP client | **Fixed** (RC-1) |
| Long calls surviving the caller timeout | **Fixed** — SDK gets remaining budget; late results quarantined |
| Retry loops exceeding the absolute deadline | **Fixed** — retry stop on `can_start_attempt()`; `LOCAL_AI_MAX_RETRIES=0` |
| Duplicate browser jobs from retries/restart | Ledger `REUSE`/`ACTIVE`/`COLLISION` + `max_retries=0`; tested |
| Four-worker congestion in live-wait | `worker_count = 1` forced for local mode |
| Interleaved role calls in one conversation | `BoundedSemaphore(1)`, parallelism clamped to 1; tested |
| Late response overwriting a terminal outcome | `claim_terminal` → `[late_result_quarantined] response_written=false` |
| Second response-writer implementation | Bridge folder gateway structurally barred from any PO3 bus |
| Silent provider fallback | `_build_non_selected_shadow_provider` returns `UnavailableProvider` in local mode; no cross-provider fallback models |
| Remote key read in bridge mode | Popped from `os.environ` at import; tested with a planted key |
| Non-loopback bridge URL | `provider_config_errors` → `UnavailableProvider`; tested |
| Fields calculated after hashing | Untouched; `generation_settings_hash` and manifest hash byte-identical |
| Error envelope validity | Untouched `_write_error_response` path |
| Secrets in logs | Asserted: bridge key never appears in a reason string or provider log |
| `UnboundLocalError` masking a config defect | **Fixed** in both providers (RC-5) |
| Diagnostic values used as authority | Untouched |

## 14. Required deployment steps

1. Ensure `python/.env` contains the block in §7 with the real `LOCAL_AI_API_KEY`
   copied from the bridge's `.env` (or re-run `setup_po3_env.ps1`). The one value
   that changed here is `AI_RESPONSE_WRITE_MARGIN_SEC=15`.
2. Confirm the MQL `InpAiWaitTimeoutRealMin` still matches
   `AI_MT5_TERMINAL_TIMEOUT_SEC=180`. No EA recompilation is needed — no MQL
   source changed.
3. Restart `ai_gate.py` so the new deadline enforcement and health bounds take
   effect.
4. Run the §11 procedure on **demo** — including all four fail-closed spot checks
   — before enabling real money.
