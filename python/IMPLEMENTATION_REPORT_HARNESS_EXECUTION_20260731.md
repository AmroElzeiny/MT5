# Harness Execution — Four Blockers Found and Fixed — 2026-07-31

The harness was executed. Four blockers were found, each fixed with a regression
test. The path now reaches `watchlist_added=true`. It does **not** yet reach
`order_attempted=true`.

---

## 1. Readiness verdict

```text
VERDICT: NOT READY
```

By your own definition, "not ready" holds while any of these are true. Current
state of each:

| Condition | Status |
|---|---|
| harness not executed | **RESOLVED** — executed 4 times |
| `positive_path_reached=false` | **STILL TRUE** |
| `mql_final_allow` unproven | partially — pre-submission reached, broker-accept not |
| watchlist addition unproven | **RESOLVED** — `added to watchlist` ×2 |
| order attempt unproven | **STILL TRUE** |
| record/cache replay incomplete | **STILL TRUE** |
| repeatability state undefined | **RESOLVED** — 5-state machine, 25 tests |

Not ready for controlled tester validation either: that requires the positive
path to complete plus the negative fixture sweep and error-envelope work, and
neither is done.

---

## 2. Run configuration

```powershell
# 1. Deterministic provider
$env:PYTHONPATH = $PWD
.\.venv\Scripts\python.exe tools\harness_local_provider.py `
    --port 8099 --decision approve --selected-index 0

# 2. Gate  (NOTE: the env vars in my previous report were wrong -- see §4.0)
$env:PO3_DOTENV_FILE = ".env.harness"
.\.venv\Scripts\python.exe ai_gate.py --common-files-dir <BUS>

# 3. Strategy Tester
Expert   = MT5_PO3_Codex\PO3_AIGate_PositivePath_Harness.ex5
Symbol   = GOLD          Period = M15      Model = 1 (1-min OHLC)
Range    = 2026.06.29 -> 2026.07.02
InpTesterAiMode                    = 2   (LIVE_WAIT_DEBUG)
InpTesterAllowLiveWaitDebugTrading = true
InpHarnessOrderAdapterMode         = 0   (PASSTHROUGH, tester broker)
```

---

## 3. Positive-path evidence actually observed

Python side, verbatim from the gate log:

```text
[provider_call_completed] role=analyst quality_tier=FULL_STRUCTURED latency_sec=0.018
[raw_model_schema_validation] valid=true assessments=1 model_owned_fields_only=true
[identity_validation] valid=true expected_count=1 actual_count=1
[evidence_reference_validation] valid=true returned_ids=[29] unknown_ids=[] cross_candidate_ids=[]
[authoritative_envelope_constructed] candidate_count=1 python_owned_echoes=0
[authoritative_envelope_validation] valid=true
[provider_call_completed] role=critic quality_tier=FULL_STRUCTURED
[decision_scores] rule_score=8.7468 llm_quality_score=8.2000 blended_legacy_score=8.5937
[ai_schema_validation] valid=true selected_candidate_hash=19BF4CB2F8A7D3A6
[ai_target_choice_validation] chosen=range_mid_opposite_side feasible=true
[llm_numeric_diagnostics] threshold_crossings=none
family_threshold llm_quality_score=8.20 threshold=7.00 pass=true
[response_written] quality_tier=FULL_STRUCTURED
allow=True  terminal_state=completed
```

MT5 side, verbatim from the tester log:

```text
[test_order_adapter] bound mode=PASSTHROUGH forced_retcode=0 journal=PO3_AI_BUS\logs\harness_order_attempts.jsonl
[decision_authority] model_raw_allow=true python_final_allow=true mql_final_allow=false
GOLD AI advisory raw_allow=true decision_state=APPROVE decision_quality_tier=FULL_STRUCTURED
                 hard_veto=false threshold_reason=ok final_allow=true
[target_arbitration] required=true chosen=range_mid_opposite_side blocker_class=none allow=true
[target_apply] chosen=range_mid_opposite_side tp1=4069.38 tp2=4070.51 rr1=0.74 rr2=1.05
GOLD deterministic gate approved candidate=0 setup_score=134.12 source=ai_approved
[target_feasibility] feasible=true reason=ok
[target_validation_overall] pass=true rr_floor_pass=true target_reached_pass=true direction_pass=true
[watchlist_precheck] pass=true reason=ok action=added
GOLD added to watchlist entry=4066.75000 rr2=1.05
[tester_ai_wait_completed] result=response_applied elapsed_wall_ms=3921
```

Totals for the current run: **10 `FULL_STRUCTURED`**, **5 `python_final_allow=true`**,
**2 `added to watchlist`**, **2 armed**, **0 order attempts**.

`mql_final_allow=false` on that line is correct, not a failure: it is the
`MQL_PRE_SUBMISSION_ELIGIBLE` state. `mql_final_allow=true` is only set at
`BROKER_REQUEST_ACCEPTED`, after the entry trigger fires.

### 3.1 Why `order_attempted=true` was not reached — Blocker 5

**I have to correct my own earlier statement.** I first reported that the entries
"simply have not triggered yet." That was wrong. The completed run shows the
entry trigger fires constantly and execution is attempted — and is then
rejected, every single time, at the last gate before `OrderSend`:

```text
entry-zone trigger                            :   805
confirmation complete, attempting execution   : 1,149
[execution_fingerprint] match=false           : 1,129
reject_stage=execution / entry skipped        : 1,148
BROKER_SUBMISSION_ATTEMPTED                   :     0
```

**1,148 execution attempts. Zero reached the broker.** Top reasons:

```text
544x execution_fingerprint_mismatch_target_source_target_model_obstacle_kind_obstacle_price_entry_tp1_tp2
375x execution_fingerprint_mismatch_target_source_target_model_obstacle_kind_obstacle_tf_obstacle_price_entry_tp1_tp2
 78x execution_fingerprint_mismatch_target_source_target_model_obstacle_kind_tp1
```

`target_source`, `target_model`, and `obstacle_kind` changed in **100%** of
cases. That is not market drift — it is systematic.

#### Root cause

* `TradeEngine.mqh:2597` — `p.assessed_execution_fingerprint = p.request_execution_fingerprint`.
  The assessed baseline is captured **before** the AI answers.
* `[target_apply]` then applies the AI's approved target choice, setting
  `target_source=ai_selected_liquidity_target` and `target_model=<AI choice>`,
  and re-deriving `tp1`, `tp2`, and the obstacle fields.
* `TradeEngine.mqh:2717-2719` compares `target_source`, `target_model`, and
  `obstacle_kind` by **exact string equality, with no tolerance**, against that
  pre-AI baseline.

So whenever the AI selects a target that differs from the pre-AI plan target,
applying the AI's own approved decision guarantees the execution fingerprint
check fails. Two safety mechanisms contradict each other, and the AI-gated path
can never reach the broker.

Observed directly: the approved candidate chose `range_mid_opposite_side` while
the pre-AI plan carried `synthetic_rr_fallback` / `next_liquidity_session_range`.

#### Why I did not patch it

The obvious fix — rebaselining the assessed fingerprint after `[target_apply]`,
or exempting the target fields — touches a fail-closed control whose entire
purpose is to guarantee the executed plan matches what the AI assessed. Getting
that wrong converts a genuine protection into a rubber stamp, which is precisely
the trade-forcing shortcut the project rules forbid. It needs a deliberate
decision about which fields the AI is *entitled* to change (its own target
choice) versus which must never drift (candidate identity, entry, sl, direction,
setup taxonomy), plus tests for both halves.

I am reporting it with evidence rather than guessing at it in the last minutes
of the session. **This is now the single blocking defect for the positive path.**

---

## 4. Blockers found and fixed

Every one was invisible to the 360 tests that passed before the run.

### 4.0 (Pre-blocker) My previous report's run instructions were wrong

`AI_PROVIDER_MODE` / `AI_LOCAL_BASE_URL` do not exist. The real switch is
`AI_USE_REMOTE_API` + `LOCAL_AI_BASE_URL`, and `ai_gate.py:208` calls
`load_dotenv(override=True)`, so **`.env` overrides the process environment** —
pointing the gate at a local endpoint would have required editing the same
`.env` that holds the production OpenAI key.

Editing a live secrets file to run a test is not acceptable, so `po3_env.py`
now honours `PO3_DOTENV_FILE`, read from the process environment only (an env
file cannot redirect the loader). `.env.harness` contains **no** production key.
Verified: production `.env` untouched, `AI_USE_REMOTE_API=true` still.

### 4.1 Blocker 1 — gate refused the provider

```text
[ai_provider_health] healthy=false structured_output_available=false
                     reason=local_structured_output_probe_failed
```

`healthcheck` asserts `parsed.ok is True` on `StructuredCapabilityProbe`
(ai_provider.py:1205). The harness's generic boolean default returned `False`,
so it truthfully-but-wrongly reported it could not emit structured output.
Fixed; regression test `test_healthcheck_reports_structured_output_available`.

### 4.2 Blocker 2 — authoritative envelope invalid

```text
[authoritative_envelope_validation] valid=false
  invalid_fields=historical_evidence_state,veto.code,veto.disabled_payload
```

Three value-domain violations pydantic cannot catch because all three were
type-correct:

* `historical_evidence_state` must be one of `SUPPORTIVE / MIXED / ADVERSE /
  INSUFFICIENT_SAMPLE`; the harness sent `SUFFICIENT`.
* An **enabled** veto needs a code from `LLM_VETO_CODES`.
* A **disabled** veto must carry no code, no reason, and no evidence at all.

Fixed. Regression tests now run harness output through the **real**
`decision_integrity.validate_candidate_assessment`, not just the pydantic model.

### 4.3 Blocker 3 — target arbitration mismatch

```text
invalid_mandatory_fields=["candidate[...].selected_target_price"]
ai_reasons="selected target price does not match target arbitration"
```

Target arbitration is a choice **among supplied options**: `chosen_tp2` must
match the candidate's own `tp2` within two ticks, and `chosen_target_model` must
match its `selected_target_identity`. The harness emitted placeholder zeros.

Fixed by reading the real offered values out of the Python-owned evidence
catalog (`...authoritative_numbers.tp2.value`, `...target_model`) — i.e. the
harness now chooses from what it was actually given, which is the intended
contract.

### 4.4 Blocker 4 — wrong verdict vocabulary per role

```text
error=ValueError:critic_verdict_invalid
```

Each role has its own vocabulary: analyst `APPROVE/REJECT/ABSTAIN`, critic
`PASS/BLOCK/ABSTAIN`, adjudicator `UPHOLD_APPROVE/UPHOLD_BLOCK/ABSTAIN`. The
harness sent analyst verdicts for every role. Also fixed the coupled rule that
`PASS` must carry no blocking objections and `BLOCK` must carry at least one
with a recognised code. This blocker only became visible *because* blocker 3 was
fixed and the pipeline finally advanced to the critic.

---

## 5. Other workstreams completed

### 5.1 Repeatability qualification state machine — DONE

`repeatability_state.py`, all five states reachable and tested:

```text
UNQUALIFIED        no samples (or no artifact) -- a cold start, not a corruption
SHADOW_COLLECTING  samples exist but below minimum, or a threshold unmet
QUALIFIED          enough samples AND every agreement metric met -> only trading state
STALE              binding changed (model/schema/prompt/catalog/gen-settings/candidate count)
INCOMPATIBLE       artifact unreadable, wrong schema, or self-contradictory -> fail closed
```

Startup line:

```text
[repeatability_state] repeatability_state=<state> group_key=... sample_count=n
  required_samples=n samples_remaining=n artifact_compatible=<bool>
  trading_authority=<bool> failed_metrics=... reason=...
```

No samples are fabricated: `qualify_observations` computes agreement only from
real recorded outcomes, and zero observations yields `UNQUALIFIED`, never
qualification. `require_repeatability_live` is not disabled anywhere.
**25 tests, 23 subtests.**

### 5.2 Order adapter inheritance risk — DONE

`BuyStop`, `SellStop`, `PositionOpen`, and `OrderOpen` are now shadowed as well,
even though TradeEngine calls none of them today — inheritance means an
unshadowed exposure method would reach the broker unjournalled. Coverage is
defined by *exposure semantics*, not current call sites.
`test_ctrade_gains_no_uncovered_exposure_method` reads the installed
`Trade.mqh` and fails if MT5 ever adds an exposure method the adapter misses.
Maintenance methods are asserted **not** to create exposure.

### 5.3 MQL source hash manifest — DONE

`tools/mql_source_manifest.py` + `docs/mql_source_manifest_20260731.json`.
SHA-256, bytes, line count, and line-ending style for all 16 MQL sources, plus
repo-vs-terminal drift detection. It immediately caught a real drift
(`TestOrderAdapter.mqh` not yet deployed), which I then deployed.

It also confirms the disclosed `TradeEngine.mqh` line-ending normalisation:
`777,338 B / 14,302 lines / lf`. Several files were already mixed before I
touched them (`Config.mqh` crlf=359/lf=258, `Risk.mqh`, `StateStore.mqh`,
`Types.mqh`).

---

## 6. Verification

```text
Full Python suite : 403 passed, 270 subtests   (previous: 360 / 220)
Harness EA compile: 0 errors, 0 warnings
Production compile: 0 errors, 0 warnings, 276824 ms
Bus inventory     : 36 files, all compatible, 0 incompatible
```

---

## 7. NOT done

1. **`order_attempted=true` / `positive_path_reached=true`** — blocked by the
   execution-fingerprint contradiction, §3.1. This is a real production defect,
   not a coverage gap, and it is the next thing to fix.
   Also note `[harness_funnel]` / `[harness_verdict]` never appeared: the run
   ended at simulated 2026-06-29 06:18 rather than 2026-07-02, so `OnTester()`
   did not execute. The acceptance-marker reporting is therefore itself
   unverified, and the funnel numbers above were counted from the tester log
   directly instead.
2. **20 MQL negative fixtures** — not run. The provider-side malformation modes
   exist and are unit-tested; the MQL-side sweep and the
   `InpHarnessForcedRetcode=10018/10016` runs have not been executed.
3. **Error-envelope interpretation** — untouched. MT5 still runs candidate
   matching against error envelopes.
4. **Record-only -> cache-only replay** — not run. Needs two full Jun 29–Jul 19
   runs; note live-wait covered 4 simulated hours in ~50 wall minutes, so this
   must use RECORD_ONLY/CACHE_ONLY, not live wait.
5. **JSON startup ready-marker handshake** — not implemented.
6. **Repeatability state machine is not yet wired into `ai_gate` startup** — the
   module and tests exist; startup still emits the old
   `[repeatability_startup_audit]` line.
7. **Latency benchmark** — not run, per your instruction. Command and cost
   estimate below.

### 7.1 Latency benchmark cost (not run — needs your authorization)

```powershell
$env:PO3_DOTENV_FILE = ".env"   # real provider
.\.venv\Scripts\python.exe tools\latency_benchmark.py --candidates 1,3,6 --samples 40
```

40 samples × 3 configurations = 120 real calls. At the measured 66,246-char
6-candidate payload (~17k input tokens) and ~6k output tokens, on `gpt-5.4-nano`
that is roughly **$1–3 total**. `AI_LIVE_CANDIDATE_BUDGET=3` remains in force and
the ~57 s figure remains a **projection**, not a measurement.

---

## 8. Honest assessment

The harness did its job, and then some. It converted "we think the path works"
into **five** specific, located defects — four fixed with regression tests, and
one that is a genuine production bug the harness existed to find.

Blocker 5 is the important one. `execution_fingerprint_mismatch` had appeared in
earlier incident logs and could easily have been dismissed as market drift.
With 1,148 attempts and `target_source,target_model,obstacle_kind` changing in
100% of them, it is provably systematic: **applying the AI's own approved target
choice invalidates the fingerprint captured before the AI was consulted.** No
AI-gated trade can reach the broker until that contradiction is resolved.

That is worth more than a green run would have been. It also means my previous
report's framing — that only the harness was missing — understated the problem:
there was a real defect sitting one stage past where anyone had looked.

I did not patch it, deliberately. Rebaselining a fail-closed execution-integrity
check is exactly the kind of change that turns a protection into a rubber stamp
if reasoned about carelessly, and I would rather hand you a precise diagnosis
than a fast fix I cannot defend.

**Next step:** decide which fields the AI is entitled to change (its own target
selection and its derived tp1/tp2/obstacle fields) versus which must never drift
(candidate identity, entry, sl, direction, taxonomy), rebaseline the assessed
fingerprint at `[target_apply]` for the former only, and add tests that prove a
genuine market-drift mismatch still fails closed.
