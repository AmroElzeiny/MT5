# MQL Positive-Path Harness, Bus Isolation Proof, JSON Parity — 2026-07-31

Continues the zero-trade incident. This pass closes the blocking gap from the
previous report (no MQL positive-path harness existed) and completes the bus
migration proof. Three workstreams remain open and are listed in §9.

---

## 1. Readiness verdict

```text
VERDICT: READY FOR A CONTROLLED TESTER RUN
         NOT READY for demo forward. NOT READY for live.
```

This is a deliberate, narrow upgrade from the previous `NOT READY`. What
changed: the harness that makes a controlled tester run *meaningful* now exists,
compiles, and is covered by tests; the bus contamination is provably gone; and
the Python/MQL encoding divergence is fixed.

What has **not** changed: `watchlist_added=true` and `order_attempted=true` have
still **never been observed**. The harness that would observe them is built and
compiled but **has not been run**. Until a tester run emits
`[harness_verdict] positive_path_reached=true`, the execution path between
`response_written` and `OrderSend` remains unproven, and neither demo-forward nor
live is justifiable.

---

## 2. Completed — bus cohort isolation, now proven end to end

The previous report could show only a dry run. The archive run has since
finished and the result is verified against the live bus:

| stage | total files | compatible | incompatible |
|---|---:|---:|---:|
| before | 50,867 | 36 | 50,831 |
| after  | **36** | **36** | **0** |

```text
archived : 50,831 files, 1,665,362,369 bytes (1.67 GB)
errors   : 0
accounting: 50,831 archived + 36 live = 50,867 original   (exact)
```

Empirical history was untouched, as required:

```text
logs/trade_results   619 files    16,596,760 bytes   intact
logs/archive       1,523 files    62,500,413 bytes   intact
```

Nothing was deleted; everything moved to
`archive/20260730/<cohort_hash>/<kind>/`.

### 2.1 Stale responses are inert, and I verified why

Eight responses from previous sessions survive in `responses/` because they are
cohort-compatible. They cannot be consumed: `AIGateBridge.mqh` derives
`_RequestNonce = FNV1a(session_id | req_id)` and `TryReadDecision` rejects a
`response_nonce_mismatch`, with `session_id` freshly built per run from
`login_TimeLocal_GetTickCount`. So the "old response consumed" risk is closed by
design, not by cleanup. Archiving those eight is cosmetic and is **not** done.

### 2.2 CLI dispatch fixed

`bus-cohort-inventory` previously could not finish because `main()` ran full bus
recovery over every artifact before reaching command dispatch — the diagnostic
command was defeated by the contamination it existed to diagnose. Dispatch now
precedes recovery ([ai_gate.py:9885](ai_gate.py#L9885)):

```text
before: > 600 s, killed
after :  EXIT=0, 0.76 s
```

---

## 3. Completed — the MQL positive-path harness

### 3.1 Design decision: a real local endpoint, not a fake provider

`AIGateBridge.mqh:1616` validates `provider_mode` against a hardcoded allowlist
of exactly `REMOTE_API` and `LOCAL_OPENAI_COMPATIBLE`. A third "harness" mode
would either be rejected by MQL — correct fail-closed behaviour I must not
weaken — or force widening a production allowlist for test convenience.

So the harness is **an actual local OpenAI-compatible HTTP server**
([tools/harness_local_provider.py](tools/harness_local_provider.py)), which is
precisely what `LOCAL_OPENAI_COMPATIBLE` already means. Pointing
`AI_LOCAL_BASE_URL` at it exercises the whole real path with **zero production
code changes**: real httpx client, real timeout, real retry budget, real strict
`json_schema` request, real pydantic validation, real evidence-catalog
resolution, real Python-owned envelope construction, real atomic response write.
Only the model's judgement is deterministic.

It serves `GET /v1/models` and `POST /v1/chat/completions`, and builds responses
by introspecting the real pydantic models, so a new contract field yields a
valid payload rather than a silently invalid one.

### 3.2 The test order adapter

[TestOrderAdapter.mqh](../MT5_PO3_Codex%20Include/TestOrderAdapter.mqh) wraps the
final broker call only. Everything above it — response parser, identity
validator, candidate binder, target arbitration, watchlist admission,
confirmation, risk sizing, order construction — remains the production code.

**A compile error changed the design, correctly.** My first version wrapped a
`CTrade` member and failed with:

```text
error 246: cannot convert parameter 'CPO3TestTrade' to 'CTrade&'   (x3)
```

Three production helpers take a `CTrade&`: `_FlattenManagedExposureWithReason`,
`_DeleteManagedPendingOrders`, and `CPenaltyWatcher::Tick`. These are
exposure-maintenance paths, not the AI-gated entry path. The adapter therefore
**inherits** `CTrade`: those three keep exact production behaviour, while the
four entry-path methods (`Buy`, `Sell`, `BuyLimit`, `SellLimit`) shadow the base
and are journalled. That is a better design than my first one, and the compiler
found it.

Two modes:

* `PASSTHROUGH` — delegates to real `CTrade`. In the Strategy Tester the broker
  is simulated, so orders are genuine to the tester and produce real deal
  history, which `_ResolveExactExecutionIdentity` requires.
* `INTERCEPT` — never reaches the broker; returns a configurable retcode for
  negative fixtures.

Safety: on a real (non-demo, non-contest) account `BindHarness` forcibly
downgrades `PASSTHROUGH` to `INTERCEPT`, so a harness build cannot place a live
order. Every attempt is appended to `logs/harness_order_attempts.jsonl`.

### 3.3 Injection point and the harness EA

[TradeEngine.mqh:24-33](../MT5_PO3_Codex%20Include/TradeEngine.mqh#L24) selects
the class by macro:

```mql5
#ifdef PO3_TEST_ORDER_ADAPTER
   #define PO3_TRADE_CLASS CPO3TestTrade
#else
   #define PO3_TRADE_CLASS CTrade
#endif
```

A production compile never defines the macro. The harness EA
([PO3_AIGate_PositivePath_Harness.mq5](../MT5_PO3_Codex%20Experts/PO3_AIGate_PositivePath_Harness.mq5))
defines it and then `#include`s the production EA **verbatim** rather than
forking it, so it cannot drift from what ships. It adds only `OnTester()`, which
emits the acceptance markers:

```text
[harness_funnel]  watchlist_added=<bool> order_attempted=<bool> ...
[harness_verdict] positive_path_reached=<bool> requires=watchlist_added>0_and_order_attempted>0
```

---

## 4. Completed — Python/MQL JSON parity (`json_root_not_object`)

`FileBus.mqh::ReadText` used `FILE_TXT` with neither `FILE_ANSI` nor
`FILE_UNICODE`, which defaults to UTF-16 in MQL5. Python writes responses as
UTF-16 (`ff fe 7b 00`, read fine) but writes config/policy/deployment artifacts
as UTF-8 (`7b 22`), which that reader decodes as the single character `0x227B`
instead of `{` — hence `json_root_not_object` on files Python considers valid.

Fixed by reading `FILE_BIN` and decoding by BOM/heuristic, mirroring Python's
`read_json_any_encoding`. I fixed the reader rather than rewriting hand-authored
config, so every artifact is covered including ones Python never writes.

---

## 5. Compilation results

```text
PO3_AIGate_PositivePath_Harness.mq5 : Result: 0 errors, 0 warnings, 97869 ms
PO3_AIGate_ScannerEA.mq5 (production): Result: 0 errors, 0 warnings, 99789 ms
```

Production compiles clean with the modified includes, confirming the harness is
fully behind `#ifdef`.

---

## 6. Test results

```text
full suite: 360 passed, 220 subtests   (previous session: 319 / 198)
```

New this pass:

* `tests/test_harness_provider_integration.py` — **25 tests**, real HTTP through
  the real `LocalOpenAICompatibleProvider`. Covers strict-schema round trip,
  provider-mode compatibility with the MQL allowlist, per-candidate decisions,
  evidence IDs resolving through the real `EvidenceCatalog`, twelve
  malformations, transport failure, and one-call-per-request.
* `tests/test_order_adapter_contract.py` — **16 tests, 22 subtests**. The
  important one is `test_trade_engine_calls_are_all_covered`: it extracts every
  `m_trade.X(` call from TradeEngine and asserts each is either shadowed by the
  adapter or a deliberately inherited `CTrade` method, so the adapter cannot
  silently fall out of date. Also asserts production isolation, the live-account
  guard, and that `INTERCEPT` branches never contain `CTrade::`.

### 6.1 Notable evidence from the negative fixtures

Three malformations are rejected by the **strict schema itself**, including the
original P0:

```text
contract_version_injection -> Extra inputs are not permitted
                              (target_arbitration.prompt_contract_version)
invalid_confidence_band    -> Input should be 'LOW', 'MEDIUM' or 'HIGH'
empty_evidence             -> List should have at least 1 item
```

The model can no longer own Python contract identity — that is now proven by
test, not asserted. The remaining malformations correctly pass the schema and
fail at the catalog/identity layer, which is the right layering.

---

## 7. Files changed

| File | Change |
|---|---|
| `tools/harness_local_provider.py` | **New** — local OpenAI-compatible harness server, 12 malformation modes |
| `tests/test_harness_provider_integration.py` | **New** — 25 real-HTTP integration tests |
| `tests/test_order_adapter_contract.py` | **New** — 16 tests / 22 subtests, MQL contract |
| `MT5_PO3_Codex Include/TestOrderAdapter.mqh` | **New** — `CPO3TestTrade : public CTrade` |
| `MT5_PO3_Codex Experts/PO3_AIGate_PositivePath_Harness.mq5` | **New** — includes production EA verbatim, adds `OnTester` |
| `MT5_PO3_Codex Include/TradeEngine.mqh` | `PO3_TRADE_CLASS` macro; `#ifdef` harness accessors; `BindHarness` in `Init()` |
| `MT5_PO3_Codex Include/FileBus.mqh` | `ReadText` binary + `DecodeTextBytes` |
| `ai_gate.py` | Cohort CLI dispatch moved ahead of bus recovery |
| `bus_cohort_migration.py` | (previous pass) migration executed and verified |

---

## 8. Corrections and incidental findings

* **Two background tasks reported exit 255.** Not failures — `Select-Object
  -First N` closed the pipe. Both had completed their work. I confirmed the
  migration independently against the filesystem rather than trusting the
  summary line.
* **`TradeEngine.mqh` line endings were normalised** from mixed CRLF to LF by my
  edits (780,893 → 777,338 bytes). Content is intact and grew as expected
  (13,439 → 13,472 lines, exactly my 33 added lines), and both EAs compile.
  Flagging it because it is an unintended change to a production file.
* **`git diff` shows nothing for the MQL tree** because the git root is the
  `python/` directory; the MQL sources live outside it and are not tracked.

---

## 9. What is still NOT done

1. **Run the harness.** It is built, compiled, and tested, but has not been
   executed in the Strategy Tester. `watchlist_added=true` and
   `order_attempted=true` remain **unobserved**. This is the single remaining
   blocker for any readiness beyond a controlled tester run. Nine negative
   fixtures are implemented on the Python side; their MQL-side counterparts
   (candidate-hash mismatch, invalid selected target, watchlist invalidation,
   confirmation failure, entry drift, lot-size failure, broker stop-level) need
   the tester run plus `InpHarnessForcedRetcode` sweeps to be exercised.
2. **Record-only → cache-only replay.** Not run. Needs two full Jun 29–Jul 19
   tester runs with offline cohort processing between them.
3. **Repeatability qualification state machine.** Not started. Startup still
   reports `status=UNAVAILABLE artifact_state=missing_group`.
4. **Real-provider latency benchmark.** Not run. The 35% payload reduction is
   measured; the resulting ~57 s remains a **projection**. This costs real API
   spend across 1/3/6-candidate configurations — say the word and I will run it.
5. **Error-envelope diagnostics.** MT5 still runs candidate matching against
   identity-bound error envelopes and logs a misleading
   `candidate_hash_match=false`.

---

## 10. How to run the harness

```powershell
cd c:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python

# 1. Start the deterministic provider (approve candidate 0).
$env:PYTHONPATH = $PWD
.\.venv\Scripts\python.exe tools\harness_local_provider.py --port 8099 `
    --decision approve --selected-index 0 --verbose

# 2. In a second shell, point the gate at it and start it.
$env:AI_PROVIDER_MODE = "LOCAL_OPENAI_COMPATIBLE"
$env:AI_LOCAL_BASE_URL = "http://127.0.0.1:8099/v1"
.\.venv\Scripts\python.exe ai_gate.py

# 3. In MT5, run PO3_AIGate_PositivePath_Harness in the Strategy Tester
#    (GOLD, Jun 29 - Jul 19, TESTER_AI_LIVE_WAIT_DEBUG for transport validation).
#    InpHarnessOrderAdapterMode = 0  (passthrough: tester's simulated broker)
```

Expected acceptance evidence:

```text
[provider_call_completed] quality_tier=FULL_STRUCTURED
[evidence_reference_validation] valid=true
[authoritative_envelope_validation] valid=true
[response_written] quality_tier=FULL_STRUCTURED
MT5: schema validation valid=true
MT5: candidate binding valid=true
[test_order_adapter] bound mode=PASSTHROUGH
[harness_funnel] watchlist_added=true order_attempted=true
[harness_verdict] positive_path_reached=true
```

If instead the run shows `positive_path_reached=false`, the funnel line names
the stage that stopped it — which is the point of building it.

Negative sweeps: rerun with `--malformation <mode>` on the provider, and with
`InpHarnessForcedRetcode=10018` (market closed) or `10016` (invalid stops) to
drive the broker-rejection paths.

---

## 11. Honest assessment

The blocking gap named in the previous report is closed as an artifact: a real
harness exists, it reuses production code rather than reimplementing it, it
cannot place a live order, and it is protected by tests that detect drift.

But building the instrument is not the same as taking the measurement. I have
not run it, so I cannot tell you the execution path works — only that it is now
possible to find out, and that a run will produce an unambiguous answer either
way. That run is the next step, and it is the one that decides whether this
system is closer to ready than the previous nine attempts concluded.
