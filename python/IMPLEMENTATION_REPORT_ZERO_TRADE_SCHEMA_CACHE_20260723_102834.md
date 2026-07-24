# Implementation Report: Zero-Trade Schema and Cache Repair

UTC report timestamp: 2026-07-23 10:28:34

## Executive summary

The observed zero-trade chain was an infrastructure failure, not evidence that
the PO3/FVG scanner, target rules, or AI thresholds were too strict.

The active bridge sent a non-compliant strict structured-output schema. The
remote API rejected `AIGateEnvelope` with HTTP 400 because nested objects did
not all declare `additionalProperties: false`. Repeated candidates retried the
same deterministic schema error, produced degraded non-trading responses, and
could not create authoritative cache rows. `TESTER_AI_LIVE_WAIT_DEBUG` then
advanced simulated time and correctly refused to trade the late responses.

This implementation repairs the schema at its source, preflights every nested
object, classifies deterministic provider failures, opens a provider/schema
circuit, binds every response and cache row to an immutable ordered request
identity, quarantines incompatible cache cohorts, and documents the
RECORD_ONLY to CACHE_ONLY workflow.

No strategy threshold, AI family threshold, RR rule, target gate, watchlist
precheck, risk limit, weak-family setting, model name, or `.set` value was
changed.

## Active files changed

### Python

- `.env.example`
- `ai_gate.py`
- `ai_provider.py`
- `architecture_contracts.py`
- `decision_evidence.py`
- `decision_integrity.py`
- `decision_pipeline.py`
- `structured_models.py`
- `tests/test_decision_integrity.py`
- `tests/test_provider_neutral_ai.py`
- `tests/test_zero_trade_reliability.py`
- `tools/verify_v_next_controls.py`
- `docs/zero_trade_schema_cache_recovery.md`

### Active MQL include tree

- `Config.mqh`
- `Types.mqh`
- `AIGateBridge.mqh`
- `TradeEngine.mqh`
- `MarketWatchScanner.mqh`

Active path:

`C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex`

### Active EA

- `PO3_AIGate_ScannerEA.mq5`

Active path:

`C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Experts\MT5_PO3_Codex`

## Principal functions and structures changed

- `structured_models.StrictStructuredModel` and
  `strict_structured_schema()` recursively create and validate provider-safe
  JSON schemas.
- `ai_provider._prepare_schema()` blocks transport before a non-strict schema
  is sent.
- `RemoteAPIProvider.generate_structured()` and
  `LocalOpenAICompatibleProvider.generate_structured()` use the same strict
  schema adapter, bounded same-provider repair, explicit error categories, and
  configuration circuit.
- `decision_integrity.build_ai_request_identity()` builds the immutable ordered
  request identity.
- `decision_integrity.validate_request_identity_echo()` rejects response order,
  provider/model, candidate, fingerprint, and runtime mismatches.
- `decision_pipeline.run_qualitative_consensus()` binds Critic and Adjudicator
  results to the same request identity.
- `ai_gate._score_setup_ai()` validates the full structured Analyst envelope
  and every candidate independently.
- `ai_gate.process_one()` creates request identity, writes bound responses, and
  records exact quality and decision counters.
- `ai_gate._export_mql_tester_replay_cache()` exports only authoritative,
  current-contract rows.
- `ai_gate._audit_runtime_caches()` audits or quarantines incompatible tester
  and Python decision-cache rows.
- `ai_gate._validate_runtime_authority()` checks schemas, provider, bus,
  policies, caches, priors, and the exact required repeatability cohort.
- `AIGateBridge.SendRequestCandidates()` serializes identity and contract
  fields; response parsing verifies root and per-candidate bindings.
- `TradeEngine._RememberTesterAiDecision()` rejects non-authoritative debug,
  degraded, incomplete, mismatched, or old-contract decisions.
- `TradeEngine._QueueTesterCachedDecision()` preserves the original immutable
  request identity.
- `MarketWatchScanner.Build()` supports an explicit tester symbol list and
  exposes detected/eligible/history/exclusion diagnostics.

## Strict structured-output repair

Every authoritative provider model now inherits `StrictStructuredModel`:

- `TargetComparisonItem`
- `TargetComparison`
- `TargetArbitrationDecision`
- `VetoDecision`
- `CandidateAssessment`
- `CandidateIdentityEcho`
- `AIGateEnvelope`
- `CriticObjection`
- `CriticDecision`
- `AdjudicatorDecision`
- `StructuredCapabilityProbe`

The recursive preflight walks the root, `$defs`, inline object properties, and
array items. It rejects:

- missing or true `additionalProperties`;
- missing required declarations;
- defaults that make provider output fields optional;
- unresolved `$ref` targets;
- unsupported schema constructs.

Current schema fingerprints:

- `AIGateEnvelope`: `21ca199ef70e5c07e3776d6bd52017fba9bcb6d003aa832a094b5e56e05ead1f`
- `CriticDecision`: `73254641eb5cad1a7f935923444a8324092c3bdc8bba77ea4861ce9dd34c88d4`
- `AdjudicatorDecision`: `ce9ee055877ebbe596a632fededf953b9c75a1e10096bea04e204227dba02b58`
- `StructuredCapabilityProbe`: `a8ccdd16ef68763cf6c9fac229f32ecbb4709e75361e16695ea16744196aa420`

Startup result:

```text
[structured_schema_preflight] provider=openai_remote_api schema=AIGateEnvelope valid=true
```

## Provider failure and circuit behavior

Provider failure is no longer represented as an AI market opinion. Explicit
non-trading categories include:

- `PROVIDER_CONFIGURATION_ERROR`
- `PROVIDER_TRANSPORT_ERROR`
- `STRUCTURED_SCHEMA_INVALID`
- `STRUCTURED_RESPONSE_INVALID`
- `REQUEST_IDENTITY_MISMATCH`
- `RESPONSE_STALE`
- `REPEATABILITY_UNAVAILABLE`
- `DEGRADED_NON_TRADING`

HTTP 400 invalid schema, HTTP 401/403, unsupported response format, permission
failure, and invalid model open a configuration circuit keyed by provider,
model, decision schema, prompt contract, and schema fingerprint. A second
candidate does not call the known-broken transport.

Temporary 429/5xx and transport failures retain bounded same-provider retry.
There is no remote-to-local or local-to-remote fallback.

The fallback repair path uses the same complete schema and all normal identity,
target-arbitration, mandatory-field, and execution-fingerprint validation. A
minimal or malformed response remains non-trading.

## Request and response identity

`AIRequestIdentity` includes:

- request ID;
- request simulated and wall-clock creation timestamps;
- symbol and direction;
- ordered candidate IDs, hashes, execution fingerprints, snapshot times, and
  taxonomy;
- runtime input hash;
- engine and input schema versions;
- decision, target, prompt, taxonomy, family, and retrieval versions;
- provider mode, provider ID, model identity, model fingerprint, and generation
  settings;
- structured schema fingerprint.

The canonical identity hash is echoed by the root response and every candidate
assessment. Candidate reordering changes the identity and fails closed. Cache
or response application is never authorized by filename, symbol, timestamp,
candidate count, or cache key alone.

## Contract versions

- engine: `5.5-version-z-strict-identity-20260723-v7`
- input schema: `20260723-v6`
- decision schema: `20260723_strict_identity_consensus_v8`
- prompt contract: `20260723_strict_identity_family_memory_v10`
- role contract: `20260723_bound_analyst_critic_adjudicator_v2`
- provider contract: `20260723_provider_neutral_transport_v2`
- request identity: `20260723_ai_request_identity_v1`

Python and MQL constants were updated together.

## Cache authority and migration

A row is cacheable only when it is full structured, mandatory-complete,
schema-valid, identity-bound, candidate-complete, target-valid,
fingerprint-bound, current-provider/current-model, non-stale,
non-diagnostic, non-shadow, and non-degraded.

The following are never authoritative cache rows:

- `RULE_ONLY_NON_TRADING`;
- `DEGRADED_NON_TRADING`;
- invalid or incomplete schemas;
- minimal parser fallbacks;
- identity/fingerprint mismatches;
- simulated-time-jump debug responses;
- shadow-only or diagnostic decisions;
- live decisions without repeatability authority.

Old rows are not silently upgraded. Actual maintenance results:

```text
tester cache: 2350 legacy identity rows quarantined
Python decision cache: 2079 incompatible/malformed rows quarantined
post-quarantine audit: valid=0 invalid=0
```

Tester rows moved to:

`PO3_AI_BUS\logs\tester_ai_cache\quarantined`

Python rows moved to:

`data\cache_quarantine`

No trade ledger or historical-outcome file was deleted.

Maintenance commands:

```powershell
python ai_gate.py cache-audit
python ai_gate.py cache-clear-incompatible
```

## Tester workflow

```text
0 = TESTER_AI_RECORD_ONLY
1 = TESTER_AI_CACHE_ONLY
2 = TESTER_AI_LIVE_WAIT_DEBUG
```

`RECORD_ONLY` exports immutable requests and never trades. Process queued
requests with:

```powershell
python ai_gate.py --fill-tester-cache-once
```

`CACHE_ONLY` makes no live AI call and trades only from an exact current full
structured cache hit.

`LIVE_WAIT_DEBUG` is connectivity-only, non-trading by default, and never
creates an authoritative replay row. A simulated-time jump remains
non-trading even with explicit debug acknowledgement.

Long debug tester runs log a warning and recommend record/cache replay.

`InpTesterSymbols` provides an optional deterministic tester universe when the
tester agent does not expose the intended Market Watch symbols. Startup logs
requested, detected, eligible, tester-available, final, and excluded counts.
Live Market Watch behavior is unchanged.

## Timestamp domains

The request contract distinguishes:

- candidate market timestamp;
- request simulated timestamp;
- request wall-clock timestamp;
- response wall-clock timestamp;
- response simulated observation timestamp;
- cache generation timestamp;
- cache replay timestamp.

Historical cache replay uses exact market-snapshot identity, not current
wall-clock age. Live-wait debug logs both wall and simulated elapsed time and
keeps a material simulated jump non-trading.

## Runtime validation

Command:

```powershell
python ai_gate.py validate-runtime
```

Observed active result:

- provider configuration: valid;
- strict schemas: valid;
- bus directories: valid;
- current cache: clean and empty;
- exact repeatability cohort: unavailable, zero groups;
- live authority: blocked;
- exit code: `2`;
- blocking reason: `repeatability_unavailable`.

This is intentional fail-closed behavior. A schema-valid empty artifact
container does not prove repeatability. No artifact or prior was fabricated.
Tester RECORD_ONLY/CACHE_ONLY research workflow remains available; live
approval requires genuine evidence when `AI_REQUIRE_REPEATABILITY_LIVE=true`.

## Logging

Added or hardened machine-readable events:

- `structured_schema_preflight`
- `provider_call_started`
- `provider_call_failed`
- `provider_call_completed`
- `structured_validation`
- `request_created`
- `identity_validation`
- `cache_write`
- `cache_hit`
- `cache_miss`
- `cache_quarantine`
- `tester_cache_export`
- `scanner_universe`
- `ai_gate_final_summary`
- `file_bus_final_summary`

Events include safe request identity prefixes, provider/model, quality tier,
reason, retry, schema, and latency fields. Secrets and full prompts are not
logged.

## Test results

Commands and actual results:

```powershell
python -m py_compile ai_gate.py ai_provider.py decision_pipeline.py decision_integrity.py decision_evidence.py architecture_contracts.py structured_models.py
# passed

python -m compileall .
# passed

python -u -m unittest discover -s tests -p "test_*.py"
# Ran 171 tests, OK

python -u tools\verify_v_next_controls.py
# all v-next controls passed

python -u tests\run_golden_validators.py
# 10 cases, 0 mismatches
```

Focused tests prove:

- every nested authoritative object is strict;
- a deliberately non-strict model fails preflight;
- nullable fields remain mandatory;
- schema contract changes alter the fingerprint;
- HTTP 400 invalid schema opens an immediate non-retryable circuit;
- HTTP 401 opens a configuration circuit;
- repeated candidates do not re-call a known-broken configuration;
- request/candidate order mismatch rejects;
- workflow flags do not alter economic cache identity;
- LIVE_WAIT_DEBUG cannot create authoritative replay cache;
- RECORD_ONLY produces a current full-structured cache row in the mocked
  end-to-end fixture;
- target, risk, repeatability, decision integrity, and MQL cache safeguards
  remain active.

## MetaEditor result

Active EA compilation:

```text
Result: 0 errors, 0 warnings, 184365 msec elapsed, cpu='X64 Regular'
```

Log:

`mql_compile_v_next_verify.log`

## Requirement status

| Requirement | Status | Evidence |
|---|---|---|
| Strict OpenAI-compatible schema | IMPLEMENTED | `structured_models.py`, 9 focused reliability tests |
| Recursive startup preflight | IMPLEMENTED | `strict_structured_schema()`, runtime preflight logs |
| Same-full-schema bounded repair | IMPLEMENTED | `ai_provider.py` provider methods |
| Deterministic provider circuit | IMPLEMENTED | HTTP 400/401 tests |
| Explicit tester modes | IMPLEMENTED | `TradeEngine.mqh`, verifier |
| Immutable request identity | IMPLEMENTED | `decision_integrity.py`, `AIGateBridge.mqh` |
| Exact response/cache binding | IMPLEMENTED | Python and MQL validation, identity tests |
| Full-structured-only cache | IMPLEMENTED | cache/export gates and verifier |
| Cache audit/quarantine commands | IMPLEMENTED | actual 4,429-row quarantine run |
| Role output binding | IMPLEMENTED | `decision_pipeline.py`, strict role schemas |
| Startup governance diagnostics | IMPLEMENTED | `validate-runtime`, policy manifest |
| Tester symbol diagnostics | IMPLEMENTED | scanner source and clean MQL compile |
| Deterministic filters preserved | IMPLEMENTED | 10 golden fixtures, verifier |
| Timestamp-domain separation | IMPLEMENTED | request identity and MQL cache fields |
| File-bus lifecycle hardening | IMPLEMENTED | lifecycle state directories, atomic claim/write, recovery tests |
| Provider-neutral machine logs | IMPLEMENTED | provider/cache/identity/final summaries |
| Real paid structured decision | NOT PROVEN | no paid trade-gate request was sent in this validation |
| New MT5 cache-only replay run | NOT PROVEN | no new Strategy Tester run was executed |
| Broker order/trade result | NOT PROVEN | no broker order was attempted |
| Live repeatability authority | BLOCKED | exact cohort is `UNAVAILABLE`, zero groups |

## Safety and provider isolation

- No rule-only live approval was added.
- No degraded response may trade.
- No stale or mismatched response may trade.
- No remote/local cross-fallback exists.
- Provider/model/schema/generation identities remain separate cache and
  repeatability cohorts.
- LLM output cannot alter deterministic entry, SL, TP, broker feasibility, or
  final execution authority.
- Existing target validation, watchlist precheck, risk controls, family
  thresholds, FVG rules, and hard gates remain intact.
- No `.set` file was changed.

## Remaining limitations and next operational proof

1. The compatible caches are intentionally empty after legacy quarantine.
   Generate new rows with RECORD_ONLY and the current strict provider contract.
2. `AI_REQUIRE_REPEATABILITY_LIVE=true` currently blocks LIVE_FORWARD because
   the exact provider/model/prompt/schema/quality cohort has no observations.
   Collect real shadow repeats and validate the artifact; do not add a
   placeholder group.
3. The mocked end-to-end fixture proves full structured Python validation and
   tester-cache export, but it is not a paid remote response, Strategy Tester
   replay, or broker execution.
4. Run the same historical period in CACHE_ONLY after generating current cache
   rows. The resulting MQL log must show cache hits and the deterministic
   watchlist/risk reason for each approved or rejected setup.
5. A controlled live/demo test is still required to prove the remote provider,
   MQL deterministic gate, broker request, and exact position attribution
   together.

The code defect that generated the HTTP 400/degraded loop is repaired and the
active source compiles. Operational trade authority remains deliberately
withheld where evidence is still missing.
