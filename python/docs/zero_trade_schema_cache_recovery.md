# Structured AI and tester-cache recovery

This document covers the infrastructure failure that can produce zero trades
even when the deterministic scanner creates valid candidates. It does not
change strategy thresholds, target rules, risk limits, or execution safety.

## Root cause and fixed contract

The affected run reached the AI bridge, but the remote API rejected the
`AIGateEnvelope` response schema because nested JSON objects did not all declare
`additionalProperties: false`. Every failed request then became a degraded,
non-trading response. `LIVE_WAIT_DEBUG` additionally advanced simulated time,
so those responses could not represent a clean backtest.

The current contract:

- recursively preflights the root schema, `$defs`, inline objects, and array
  item objects before the provider is called;
- requires `additionalProperties: false` and every declared property in
  `required`;
- opens a provider/schema circuit immediately for deterministic HTTP 400 schema
  errors instead of retrying every candidate;
- binds every response and cache row to the complete immutable request
  identity, ordered candidate identities, provider/model cohort, schema
  fingerprint, and execution fingerprints;
- permits only `FULL_STRUCTURED` and `CACHE_OF_FULL_STRUCTURED` decisions to
  trade;
- quarantines incompatible cache rows without deleting trade ledgers or
  historical outcomes.

## Validate before starting MT5

Run from the Python directory:

```powershell
python ai_gate.py validate-runtime
```

The command validates provider configuration and health, strict structured
schemas, bus directories, policy diagnostics, cache integrity, repeatability,
and priors. It exits nonzero when live authority is unsafe. In particular,
`AI_REQUIRE_REPEATABILITY_LIVE=true` requires an exact `REPEATABLE` cohort; an
empty artifact container is not sufficient evidence.

Schema success is logged as:

```text
[structured_schema_preflight] provider=... schema=AIGateEnvelope valid=true ...
```

The command never prints API keys, authorization headers, full prompts, account
credentials, or local model paths.

## Cache maintenance

Audit current tester and Python decision-cache cohorts:

```powershell
python ai_gate.py cache-audit
```

Move only incompatible cache rows to cohort-specific quarantine:

```powershell
python ai_gate.py cache-clear-incompatible
```

The maintenance command does not delete completed-trade ledgers, historical
outcomes, or valid entries from compatible schema/provider cohorts.

## Reproducible Strategy Tester workflow

The tester mode values are:

```text
0 = TESTER_AI_RECORD_ONLY
1 = TESTER_AI_CACHE_ONLY
2 = TESTER_AI_LIVE_WAIT_DEBUG
```

### Pass 1: RECORD_ONLY

Use `InpTesterAiMode=0`. The EA exports immutable requests with deterministic
tester cache signatures, candidate hashes, request execution fingerprints, and
request simulated timestamps. It does not wait for AI and does not trade.

Run the bridge to process queued records and create only fully validated tester
cache rows:

```powershell
python ai_gate.py --fill-tester-cache-once
```

The expected successful event is:

```text
[tester_cache_export] written=true ... source=record_only
```

Degraded, stale, mismatched, repeatability-blocked, diagnostic, and
`LIVE_WAIT_DEBUG` responses are not exported as authoritative replay rows.

### Pass 2: CACHE_ONLY

Use `InpTesterAiMode=1` and keep `InpTesterAiCache=true`. The EA makes no live
AI calls and replays an entry only when all current identity, provider/model,
schema, runtime-input, taxonomy, target, and execution-fingerprint contracts
match the cached row.

A cache miss remains fail-closed and logs the exact reason. Wall-clock age does
not invalidate a cache row that exactly binds to its immutable historical
market snapshot.

### LIVE_WAIT_DEBUG

Use `InpTesterAiMode=2` only for a short connectivity check. It is not a clean
accelerated backtest. It is non-trading by default, and even with explicit
debug trading acknowledgement a simulated-time jump beyond the safety limit
remains non-trading. Such responses are not written to authoritative replay
cache.

## Tester symbols

Strategy Tester agents may expose only the chart symbol even when live Market
Watch contains more instruments. Startup now reports detected, eligible,
tester-available, final, and excluded counts.

For a deterministic tester universe, set the optional diagnostic input:

```text
InpTesterSymbols=GOLD,US100,US500
```

Each symbol must exist and have tester history. Live Market Watch behavior is
unchanged.

## Evidence boundaries

Unit and verifier tests use mocked providers and prove that a complete
structured approval can pass Python validation, be cached, and reach the MQL
deterministic gate. They do not prove that a paid remote request, broker order,
or profitable trade occurred. Those require a controlled runtime test and must
be reported separately.
