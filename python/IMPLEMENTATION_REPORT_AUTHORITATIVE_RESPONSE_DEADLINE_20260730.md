# Authoritative AI Response, Deadline, and Deployment Integrity — 2026-07-30

Restores the full authoritative AI decision path. Every completed
`FULL_STRUCTURED` provider result was being downgraded to
`DEGRADED_NON_TRADING`; provider calls were outliving the MT5 terminal
deadline; and the startup policy manifest was reporting false incompatibilities.

---

## 1. Confirmed root causes

### 1.1 `prompt_contract_version` / `target_arbitration_schema_version`

**Earliest incorrect state:** the *prompt and provider schema*, not the validator.

- `structured_models.TargetArbitrationDecision` required the model to emit
  `target_arbitration_schema_version` and `prompt_contract_version`, and that
  model was reachable from the provider-facing `ModelAIGateOutput`.
- The analyst prompt (`ai_gate.py`) literally instructed:
  *"Fill target_arbitration_schema_version and prompt_contract_version with the
  exact current constants."*
- `decision_integrity.validate_candidate_assessment` then compared those
  model-generated strings against the Python constants
  `AI_TARGET_ARBITRATION_SCHEMA_VERSION = "20260717_target_fingerprint_authority_v6"`
  and `AI_PROMPT_CONTRACT_VERSION = "20260724_canonical_frozen_request_v12"`.

An LLM cannot reliably reproduce two opaque versioned constants, so
`ai_schema_validation valid=false` was the *expected* outcome of a *successful*
call. The symptom appeared at the validator; the defect was the contract that
asked the model to own deterministic internal identity.

### 1.2 `confidence_band`

**Earliest incorrect state:** an unconstrained provider field.

- Provider schema: `confidence_band: str = Field(min_length=1, max_length=12)` —
  any 12-character string.
- The prompt never stated the allowed values.
- `decision_integrity.py:631` required `{LOW, MEDIUM, HIGH}`.

Any reasonable model answer outside those three (`VERY_HIGH`, `MODERATE`) passed
transport validation and failed authority validation.

### 1.3 Provider calls outliving the terminal deadline (65–87s, 177s, 254s)

**Earliest incorrect state:** `ai_provider._client_instance()` constructed the
OpenAI client **without `max_retries`**.

The SDK default is `DEFAULT_MAX_RETRIES = 2`, and each internal retry receives a
*fresh copy of the full timeout*. With `.env` `AI_SERVICE_TIER=flex` and
`AI_OPENAI_FLEX_TIMEOUT_SEC=90`, one `responses.create` call could consume
`3 x 90s = 270s` plus backoff before raising — which is exactly the observed
177s and 254s. The gate's own `transport_retries` loop and up to 3 flex-retry
cooldowns stacked on top. The configured 60/90s timeout was never the real bound.

### 1.4 Overlapping provider work in live-wait debug

`worker_count` was forced to 1 only for the *local* provider; workload mode was
ignored. In `TESTER_AI_LIVE_WAIT_DEBUG` the terminal blocks on one request, so
the other three workers could only start calls whose results arrive after the
terminal gave up.

### 1.5 Repository MQL build was stale (newly discovered, material)

The repo tree did **not** match the deployed build that actually compiles and
runs. Six includes plus the EA differed:

| File | Repo (stale) | Terminal (authoritative) |
|---|---|---|
| AIGateBridge.mqh | 135,891 | 149,866 |
| Config.mqh | 24,422 | 27,053 |
| MarketWatchScanner.mqh | 1,395 | 4,053 |
| StateStore.mqh | 135,123 | 142,567 |
| TradeEngine.mqh | 756,840 | 780,893 |
| Types.mqh | 32,501 | 33,921 |
| PO3_AIGate_ScannerEA.mq5 | 14,642 | 17,286 |

The repo copy declared `AI_DECISION_SCHEMA_VERSION = "20260718_ai_decision_authority_v6"`
and `AI_PROMPT_CONTRACT_VERSION = "20260718_qualitative_veto_repeatability_v8"` —
**contract-incompatible with current Python**. The terminal copy declared the
current `20260724_...v10 / v12` values. Direction of truth: terminal → repo.

### 1.6 Misleading policy "blocked" status

`ai_gate` passes the sentinel `__EA_RUNTIME_INPUT_HASH_REQUIRED__` as the
expected runtime-input hash because Python starts before any EA payload exists.
`policy_manifest_entry` treated *"cannot be evaluated yet"* identically to
*"proven mismatch"*, emitting `runtime_input_incompatible`,
`decision_schema_incompatible`, `taxonomy_incompatible`, and `ledger_not_clean`.

Verified against the artifacts: `risk_factor_policy.v1.json`,
`invalidation_policy.v1.json`, and `normalized_fvg_policy.v2.json` carry **no**
`runtime_input_hash`, `decision_schema_version`, or `taxonomy_version` fields at
all — they are hand-authored config, never stamped. And with
`logs/completed_ai_trades.jsonl` absent, ledger status `UNKNOWN` is *unverified*,
not *dirty*. `normalized_fvg_policy` read "compatible" in Python only because its
spec passes no expected values, while MQL evaluates different criteria — the
exact Python/MQL parity mismatch reported.

---

## 2. Files changed

| File | Change |
|---|---|
| `structured_models.py` | Split model-facing vs authoritative arbitration; canonical `ConfidenceBand` enum + normalizer |
| `ai_gate.py` | Python-owned injection, staged logs, deadline wiring, worker mode, terminal-state guard, compatibility report, deployment manifest |
| `ai_provider.py` | `max_retries=0` on the SDK client; deadline-bounded attempts, retries, and flex cooldowns |
| `architecture_contracts.py` | Honest compatibility classification (`awaiting_runtime_authority` / `artifact_not_stamped` / `incompatible`) |
| `provider_deadline.py` | **New** — absolute monotonic deadline contract |
| `request_terminal_state.py` | **New** — exactly-one terminal outcome + late-result quarantine |
| `tests/test_authoritative_response_contract.py` | **New** — 56 regression tests |
| `tests/test_decision_integrity.py` | Added `model_assessment()` / `model_target_arbitration()` analysis-only fixtures |
| `tests/test_python_owned_identity_lifecycle.py` | Mock provider no longer returns Python-owned fields |
| `tests/test_provider_neutral_ai.py` | Same |
| `MT5_PO3_Codex Include/*.mqh`, `Experts/*.mq5` | Synced from authoritative terminal build |
| `AIGateBridge.mqh` | Publishes `ai_wait_timeout_ms` / `ai_wait_poll_ms` |

## 3. Functions / classes changed

**New:** `DeadlinePolicy`, `RequestDeadline`, `RequestTerminalRegistry`,
`TerminalOutcome`, `_compatibility_state`, `_resolve_python_owned_arbitration`,
`normalize_confidence_band`, `_mt5_terminal_timeout_sec`,
`_deadline_policy_for_payload`, `_start_request_deadline`,
`_request_deadline_for`, `effective_worker_count`, `_requires_serial_worker`,
`mark_test_end_interrupted`, `_deployment_manifest_state`,
`_write_artifact_compatibility_report`, `ModelTargetArbitrationDecision`.

**Modified:** `_bind_python_owned_analyst_envelope`, `_score_setup_ai`,
`process_one`, `_process_claimed_request`, `main`, `_provider_request_metadata`,
`_write_startup_policy_manifest`, `policy_manifest_entry`,
`build_startup_policy_manifest`, `_client_instance`, `generate_structured`,
`ModelCandidateAssessment`, `CandidateAssessment`, `ModelCriticDecision`,
`CriticDecision`, `TargetArbitrationDecision`.

## 4. Provider-schema changes

Removed from the provider-facing schema (now Python-injected):

```text
target_arbitration.target_arbitration_schema_version
target_arbitration.prompt_contract_version
```

Constrained: `confidence_band` is now `{"enum": ["LOW","MEDIUM","HIGH"]}` in
`ModelCandidateAssessment` and `ModelCriticDecision`.

All four provider schemas still pass `strict_structured_schema` preflight.
`STRUCTURED_SCHEMA_ADAPTER_VERSION` → `20260730_model_owned_analysis_only_v2`.

## 5. Final-envelope changes

`AIGateEnvelope` / `CandidateAssessment` are **unchanged in shape** — they still
carry both contract versions, because MQL re-validates them
(`AIGateBridge.mqh:525-547`, `1637-1641`). They are now written by Python.

Echo handling: if a model or legacy cache entry supplies either field, it is
overwritten with the active constant and reported via
`[python_owned_field_echo] authority=python action=overwritten`. It never
carries authority.

## 6. Timeout and worker-lifecycle changes

Derived hierarchy (from config + the MQL-published value, no scattered literals):

```text
mt5_terminal_deadline    = 120_000 ms   (runtime_inputs.ai_wait_timeout_ms, else AI_MT5_TERMINAL_TIMEOUT_SEC)
python_response_deadline = 105_000 ms   (terminal - write margin)
response_write_margin    =  15_000 ms   (AI_RESPONSE_WRITE_MARGIN_SEC)
min_provider_attempt     =  10_000 ms   (AI_MIN_PROVIDER_ATTEMPT_SEC)
```

- SDK gets `max_retries=0` and `min(configured, remaining budget)` as timeout.
- Deadline starts at **claim time**, is monotonic, and is never reset by a
  retry, model fallback, schema repair, or re-registration.
- No attempt begins without `min_attempt_ms` remaining.
- Flex cooldowns only run if cooldown + min-attempt still fits.
- Results arriving after the Python deadline are quarantined, never authoritative.
- `effective_worker_count` returns 1 for `live_wait_debug`; production and
  live-forward concurrency are unchanged.
- `RequestTerminalRegistry` guarantees exactly one authoritative response per
  request identity; the loser is logged as `[late_result_quarantined]`.
- `TEST_END_INTERRUPTED` is a distinct terminal state, never labelled a
  provider timeout, and request files are preserved.

`timeout_sec` in provider metadata deliberately stays the *configured* value:
it feeds `generation_settings_hash`, which is part of request identity and cache
compatibility. Clamping happens inside `generate_structured`, where it cannot
perturb any hash.

## 7. Policy / deployment reconciliation

Before → after (real regenerated manifest):

```text
counts before: {'active': 0, 'blocked': 2, 'shadow': 9}
counts after : {'active': 0, 'blocked': 0, 'shadow': 11,
                'awaiting_runtime_authority': 2, 'disabled': 7}
```

```text
risk_factor_policy    status=awaiting_runtime_authority authority=shadow
    pending: runtime_input_awaiting_runtime_authority,
             decision_schema_artifact_not_stamped,
             taxonomy_artifact_not_stamped,
             ledger_unverified_no_completed_history
invalidation_policy   status=awaiting_runtime_authority authority=shadow
normalized_fvg_policy status=compatible                 authority=shadow
repeatability_artifact status=compatible                authority=shadow
(7 optional artifacts) status=disabled                  authority=shadow
```

Authority remains **fail-closed** (`active: 0`) — only the labels became honest.
A genuinely dirty ledger (`RECONCILIATION_FAILED`) and a genuine version
mismatch still block, and a missing *mandatory* artifact still blocks with
`policy_file_missing`. No empirical evidence, calibration, repeatability, or
ledger history was fabricated.

**Deployment manifest** is now generated from observable facts (SHA-256 of the
five deployed MQL includes + Python contract constants), written to
`<bus>/config/deployment_manifest.json`:

```text
[deployment_manifest] status=generated written=true mql_components=5
                      mql_deployment_complete=true contract_manifest_hash=1024838206
```

**Compatibility report** written to `<bus>/logs/artifact_compatibility_report.json`
and `data/artifact_compatibility_report_latest.json` with the requested columns.
`mql_authority` is honestly recorded as `not_observed_at_python_startup` rather
than asserting an agreement Python cannot yet observe.

## 8. Commands run and exact results

```text
.venv\Scripts\python.exe -m pytest tests -q          (baseline, pre-change)
  -> 197 passed, 99 subtests passed in 8.91s

.venv\Scripts\python.exe -m pytest tests -q          (final)
  -> 253 passed, 132 subtests passed in 8.77s

.venv\Scripts\python.exe -m pytest tests/test_authoritative_response_contract.py -q
  -> 56 passed, 33 subtests passed in 0.49s

.venv\Scripts\python.exe tools\verify_v_next_controls.py
  -> [verify] all v-next controls passed   (includes "[verify] PASS mql compile")

metaeditor64.exe /compile:PO3_AIGate_ScannerEA.mq5   (final, after all changes)
  -> Result: 0 errors, 0 warnings, 127090 msec elapsed, cpu='X64 Regular'
  (metaeditor returns process exit code 1 on success; the log line is authoritative)
```

### Pre-fix regression proof

With `structured_models.py` and `ai_provider.py` temporarily reverted, the new
suite reproduced the production defect:

```text
11 failed, 32 passed, 27 subtests passed

FAILED ...ProviderSchemaOwnershipTests::test_provider_schema_omits_python_owned_contract_versions
FAILED ...ProviderSchemaOwnershipTests::test_model_arbitration_has_no_version_fields
FAILED ...ProviderSchemaOwnershipTests::test_model_output_without_contract_versions_is_valid
FAILED ...ProviderSchemaOwnershipTests::test_model_output_echoing_contract_versions_is_rejected
FAILED ...ConfidenceBandContractTests::test_authoritative_assessment_uses_the_same_enum
FAILED ...ConfidenceBandContractTests::test_provider_schema_constrains_the_model_to_the_enum
SUBFAILED(band='LOW'/'MEDIUM'/'HIGH') ...test_every_canonical_value_passes_every_role_schema
FAILED ...ProviderSdkBudgetTests::test_sdk_retries_are_disabled_explicitly
FAILED ...EndToEndAuthoritativeResponseTests::test_multi_candidate_response_stays_full_structured
```

## 9. Tests added

56 tests in `tests/test_authoritative_response_contract.py`:

- **ProviderSchemaOwnershipTests** (6) — provider schema omits Python-owned
  constants (recursive property walk), model arbitration lacks them, authoritative
  arbitration keeps them, echo is rejected by the strict schema, prompt no longer
  asks for constants.
- **PythonOwnedInjectionTests** (4) — exact active versions injected, wrong echo
  overwritten *and* recorded, genuine analysis survives, unknown band fails closed.
- **ConfidenceBandContractTests** (6) — canonical set, all values across
  analyst/critic/authoritative, documented variants normalize, arbitrary values
  fail closed, provider enum, authoritative enum parity.
- **DeadlinePolicyTests** (7) — derived hierarchy, margin clamp, arrival
  boundaries (60/90/104/104.999 accepted; 106/120 expired), terminal vs Python
  deadline, shrinking provider timeout with no reset, attempt refusal, monotonic
  elapsed.
- **ProviderSdkBudgetTests** (1) — SDK receives `max_retries=0` and bounded timeout.
- **TerminalStateTests** (7) — first claim wins, late completion cannot overwrite
  timeout, two workers cannot both finish one identity, re-registration never
  restarts the clock, test-end interruption is not a timeout, late result after
  test end is non-authoritative, recovery does not resurrect terminal requests.
- **WorkerModeTests** (3) — live-wait uses 1 worker, other modes keep concurrency,
  clamping.
- **EndToEndAuthoritativeResponseTests** (2) — full multi-candidate path stays
  `FULL_STRUCTURED` with all staged logs and preserved assessments; fixture guard
  that the mock returns no Python-owned fields.
- **ReplayCacheAuthorityTests** (6) — degraded/incomplete/alias-conflict excluded
  from cache, live-wait not replay-authoritative, workflow sources distinguished,
  plus a fixture guard that the quality gate is genuinely reached.
- **PolicyClassificationTests** (8) — pending ≠ incompatible, unstamped ≠ mismatch,
  unknown ledger is unverified not dirty, dirty ledger still blocks, real mismatch
  still blocks, disabled reports disabled, missing optional does not block, missing
  mandatory blocks.
- **DeploymentManifestTests** (1), **MqlParityTests** (4).

## 10. End-to-end evidence

From the passing e2e test (mocked provider returning analysis only, reverse
candidate order):

```text
[raw_model_schema_validation] valid=true ... model_owned_fields_only=true
[identity_validation] valid=true
[authoritative_envelope_constructed] ... prompt_contract_version=20260724_canonical_frozen_request_v12
[authoritative_envelope_validation] valid=true
[ai_schema_validation] valid=true
decision_quality_tier=FULL_STRUCTURED
decision_source != degraded_ai_response
decision_source != provider_transport_error
candidate_assessments = 2 (real analysis, llm_quality_score != 0)
selected_candidate_hash bound correctly through reordering
```

---

## 11. Newly discovered blockers and how they were resolved

1. **Stale repository MQL build** (§1.5) — resolved by syncing terminal → repo
   for 6 includes + the EA, after verifying the terminal copy carries the
   current contract constants. Stale copies backed up to the session scratchpad.
2. **`generation_settings_hash` perturbation risk** — an early version of the
   deadline work clamped `timeout_sec` in provider metadata, which feeds
   `generation_settings_hash` and therefore request identity and cache
   compatibility (`ai_gate.py:4631`, `decision_integrity.py:371`). Caught before
   completion; clamping moved inside `generate_structured`.
3. **Mocked providers returned Python-owned fields** — the reason 197 baseline
   tests passed while production failed on every call. Fixtures now emit the
   strict `ModelAIGateOutput` analytical surface only, with a guard test.
4. **`ledger_not_clean` asserted against an absent ledger** — reclassified as
   `ledger_unverified_no_completed_history`.

## 12. Potential issues inspected

Duplicated version constants; validators running before injection; final
validation accidentally validating raw output; critic/adjudicator contract
echoes (`ModelCriticDecision` / `ModelAdjudicatorDecision` confirmed to carry no
contract versions); degraded results entering caches (`_mql_tester_cache_skip_reason`
already excluded them — verified, not changed); error envelopes entering the
decision cache; hidden SDK retries; timeout applied only to outer futures;
retry loops exceeding the absolute deadline; four-worker congestion; late-result
overwrite; duplicate provider calls per identity; restart recovery resurrecting
terminal requests; fields calculated after hashing; diagnostic values used as
authority; optional policy failures treated as mandatory; cache-only calling the
provider (already correctly enforced MQL-side at `TradeEngine.mqh:9012`, where a
miss is an explicit reject and no request is written).

## 13. Remaining limitations (evidenced)

1. **No live provider run was executed.** All evidence is from the test suite,
   the repo verification tool, and MQL compilation. The acceptance log lines are
   proven against a mocked provider; a real OpenAI call requires your credentials
   and a running terminal.
2. **`mql_authority` in the compatibility report is `not_observed_at_python_startup`.**
   Python has no EA payload at startup, so MQL's verdict genuinely cannot be
   observed then. Recording agreement would be fabricated evidence.
3. **`risk_factor_policy` / `invalidation_policy` remain non-authoritative.**
   They carry no `runtime_input_hash` / `decision_schema_version` /
   `taxonomy_version` stamps, and no completed-trade ledger exists. Making them
   active requires real stamped artifacts and real ledger history — not a code
   change. This is correctly reported rather than bypassed.
4. **`mark_test_end_interrupted` fires on gate shutdown**, not on MT5
   `OnDeinit`. Detecting tester end *from within Python* would need MQL to write
   a deinit marker to the bus. The terminal state, quarantine, and logging are
   implemented and tested; the MQL-side trigger is not.
5. **No 20-day record-only → cache-only run was performed.** The replay
   authority rules are unit-tested; executing the full period needs a Strategy
   Tester session.

## 14. Required deployment steps

1. **Restart the Python gate** so the new schema, deadline, and worker logic load.
2. **Recompile the EA in MetaEditor** — already done here (0 errors, 0 warnings),
   but repeat if you re-deploy the includes.
3. **Optionally tune `.env`** (defaults are already correct):
   ```
   AI_MT5_TERMINAL_TIMEOUT_SEC=120
   AI_RESPONSE_WRITE_MARGIN_SEC=15
   AI_MIN_PROVIDER_ATTEMPT_SEC=10
   ```
   `ai_wait_timeout_ms` published by MQL overrides the first value per request.
4. **Quarantine pre-fix cache entries** — they were written under the old
   contract:
   ```
   .venv\Scripts\python.exe ai_gate.py cache-clear-incompatible
   ```
5. **Run the authoritative backtest workflow** (not live-wait):
   `TESTER_AI_RECORD_ONLY` → `ai_gate.py --fill-tester-cache-once` →
   `TESTER_AI_CACHE_ONLY` on the identical build, inputs, symbol data, and period.

## 15. Exact expected success logs

**Python:**

```text
[provider_deadline] request_id=... mt5_terminal_timeout_ms=120000 python_deadline_ms=105000 write_margin_ms=15000
[provider_attempt] attempt=1 remaining_ms=... sdk_timeout=... sdk_max_retries=0
[provider_call_completed] ... quality_tier=FULL_STRUCTURED
[raw_model_schema_validation] valid=true ... model_owned_fields_only=true
[identity_validation] valid=true
[authoritative_envelope_constructed] ... prompt_contract_version=20260724_canonical_frozen_request_v12
[authoritative_envelope_validation] valid=true
[ai_schema_validation] valid=true
[response_written] ... quality_tier=FULL_STRUCTURED
```

**MT5:**

```text
ai_schema_validation valid=true
decision_quality_tier=FULL_STRUCTURED
candidate_assessment_count_match=true
candidate_hash_match=true
assessment_group_ok=true
decision_source != degraded_ai_response
decision_source != provider_transport_error
```

A trade is not required. `ai_approved`, `ai_rejected`, `ai_abstained`,
`risk_gate_rejected`, or a specific deterministic MQL execution rejection are all
acceptable outcomes. A false infrastructure degradation is not.
