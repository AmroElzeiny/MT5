# PO3_Codex MT5 — Project Context

> Scope: the whole PO3 AI-gated MT5 trading system (Python bridge + MQL5 EA + file bus + deployed runtime).
> The deep engineering rules live in [python/CLAUDE.md](python/CLAUDE.md) and remain fully authoritative.
> This file adds: the goal, the role, the resource map, and the verified current state.

---

## 0. Reporting language — MANDATORY, overrides everything below

**Every reply and every report to the user must be written in pure Egyptian Arabic
(العامية المصرية), with no English words and no Latin letters at all.**

- This applies to the whole user-facing answer, including the required final-report
  sections defined in [python/CLAUDE.md](python/CLAUDE.md).
- Technical names — files, functions, log tags, providers, inputs, reason strings —
  must be written in Arabic script (transliterated) or described in Arabic. Do not
  fall back to Latin text because a term is technical.
- Digits, prices, percentages and punctuation (0-9, %, $, ., :) are allowed; they are
  not letters.
- The only exception: the user explicitly asks for English, or explicitly asks for a
  raw artifact to be quoted verbatim (a log line, a code block, a file path). Code
  written into files, commit messages, and code comments are unaffected — this rule
  governs the reply text, not the repository content.
- When in doubt, translate. A report that mixes English words in is a failed report,
  regardless of how correct its content is.

---

## 1. Goal

Reach a **fully working EA in the live MT5 terminal (broker: FxPro)** — the EA
`PO3_AIGate_ScannerEA` attached in the FxPro terminal must complete the intended
workflow end to end and be able to place real (demo-forward) trades when a setup is
genuinely approved.

A trade is **never** to be forced. A genuine AI or risk rejection is a success.
An *infrastructure* rejection on healthy infrastructure is a failure.

---

## 2. Role

Act as senior reliability engineer, trading-systems architect, Python engineer,
MQL5 engineer, test engineer, and incident investigator.

Non-negotiables (see `python/CLAUDE.md` for the full contract):

- Root causes, never symptoms. Trace the whole workflow map before editing.
- A newly exposed blocker in the same workflow **stays in scope**. Do not stop
  after moving the failure one stage later.
- Evidence over assumptions — code, logs, tests, reproducible behavior.
- No trade-forcing shortcuts, no weakening of fail-closed behavior, no placeholders,
  no silent fallbacks that change authority.
- Keep Python / MQL5 / schemas / manifests / tests synchronized.
- Deliver the task 100% finished. Handle every error or finding that appears mid-task.

### Standing task list

1. Diagnose why the EA does not trade, per stage of the workflow.
2. Fix root causes across Python + MQL5 together.
3. Add regression tests that fail before the fix.
4. Run the Python suite; recompile affected MQL5; verify with real bus artifacts.
5. Re-inspect logs for the next blocker and continue until the path is healthy.
6. Report in the required final-report format (see `python/CLAUDE.md`).

---

## 3. Resource map

### Source (repository)

| Path | What it is |
|---|---|
| `MT5/python` | Main AI decision backend. `ai_gate.py` (~552 KB) is the core; also `ai_provider.py`, `decision_pipeline.py`, `decision_integrity.py`, `structured_models.py`, `architecture_contracts.py`, `repeatability_state.py`, `request_lifecycle.py`, `evidence_catalog.py`, `trade_memory.py`, `tests/`. |
| `MT5/MT5_PO3_Codex Include` | Repo copy of MQL includes: `AIGateBridge.mqh` (bridge contract, serializer/validator), `TradeEngine.mqh` (execution, partial close, cache binding), `Config.mqh` (contract versions + inputs), `StateStore.mqh`, `PenaltyWatcher.mqh`, `PO3.mqh`, `FVG.mqh`, `Risk.mqh`. |
| `MT5/MT5_PO3_Codex Experts` | Repo copy of `PO3_AIGate_ScannerEA.mq5`. |
| `MT5/autonomous_ai_browser_bridge` | Browser/local bridge layer, launch scripts, browser profiles, `.env`, run presets, log helpers. |

### Deployed runtime (this is what actually runs)

Terminal id: `0148BD5691B65B0F2157627A4231F3DE` (FxPro, build 6090, account `591813800`, demo hedging).

| Path | What it is |
|---|---|
| `…\MQL5\Include\MT5_PO3_Codex` | **Deployed** includes used by FxPro MT5. Compare against repo. |
| `…\MQL5\Experts\MT5_PO3_Codex` | **Deployed** EA source + compiled `.ex5`. Always verify `.ex5` timestamp > all `.mqh` timestamps. |
| `…\MQL5\Logs` | EA journal. Shows whether MT5 received / validated / rejected a response, and order outcomes. |
| `…\Logs` | Terminal-level logs + `metaeditor.log` (compile results). |
| `…\MQL5\Profiles\Presets` | Live/demo `.set` files for manual attach. |
| `…\MQL5\Profiles\Tester` | Strategy Tester `.set` files. |

### Shared file bus

`C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS`

| Folder | Meaning |
|---|---|
| `requests` | Pending MT5 requests. |
| `processing` | Python-owned active requests. |
| `responses` | Completed Python decisions waiting for MT5. |
| `rejected` / `shutdown` / `quarantined` / `stale` / `timed_out` | Terminal + failure archives. |
| `response_debug` | Full model outputs, consensus data, validation detail. |
| `request_ledger` | Exactly-once request lifecycle state. |
| `logs` | `ai_gate.log`, `openai_usage.{csv,ndjson}`, startup policy manifests, artifact compatibility report. |
| `config` | Deployment + compatibility manifests and policy files. |

### Environment notes

- Windows 11, PowerShell is the working shell. **The Bash tool is broken in this
  environment** (`fork: Resource temporarily unavailable`) — use PowerShell.
- MT5 logs are **UTF-16**: read with `Get-Content -Encoding Unicode`.
- Daily EA log can exceed 49 MB / ~100k lines. Filter, don't dump.

---

## 4. Verified state as of 2026-08-12

Evidence: bus artifacts, `ai_gate.log`, EA journal `20260812.log`, terminal log, `metaeditor.log`.

### Build / deployment integrity — HEALTHY

- Deployed `.ex5` compiled `2026.08.12 05:31:05` — **0 errors, 0 warnings**, and it is
  newer than every deployed `.mqh` (latest: `AIGateBridge.mqh` 05:22). Build is current.
- Repo vs deployed MQL: `PO3_AIGate_ScannerEA.mq5` hashes match; all 15 includes match
  except `Config.mqh`, which differs **only by line endings / a trailing newline**
  (same content, 26918 vs 27279 bytes). No functional divergence.
- Contract versions agree on both sides — `AI_DECISION_SCHEMA_VERSION = …_v10` and
  `AI_PROMPT_CONTRACT_VERSION = …_v12` in both `Config.mqh` and `decision_integrity.py`.
  (The v10/v12 pair is two different fields, **not** a mismatch.)

### Transport / provider — HEALTHY

Session 04:31–05:05 produced `ai_requests_queued_total=14`, `ai_advisories_total=13`,
`ai_final_allow_true_total=0`, `orders_placed_total=0`, `trades_opened_total=0`.
No transport failures, no deadline failures. Queues are now empty
(`requests`/`processing`/`responses` = 0).

### Why there were no trades — ranked by volume

1. **The funnel dies pre-AI.** Whole-day reject counts:
   `structural_stop_invalid` **9076**, `synthetic_fallback_exceeds_max_target_distance`
   **3548**, `execution_cost_r_too_high` **2020**, `ote_outside_fvg` 1168,
   `pre_ai_floor_hard` 997, `no_feasible_target` 276.
   A representative scan: `raw_fvgs=5472 → accepted_fvgs=95 → branch_candidates=59 →
   ai_requests=3`. Roughly 99% of setups never reach the AI.
2. **Targets are always synthetic.** Every plan logs `tp_model=synthetic_rr_fallback`
   at `rr2=1.05`, because real targets are infeasible
   (`liquidity_rr=0.52`, `capped_rr=0.08` vs `fallback_rr=1.05`), and the synthetic
   fallback then trips its own max-distance guard 3548 times.
3. **Degraded envelopes carry zero assessments.** Every `DEGRADED_NON_TRADING`
   response has `candidate_count=3` but `candidate_assessments=0`, so MQL reports
   `assessment_integrity_reason=candidate_assessment_count_mismatch` and
   `degraded_ai_response_non_trading` — an *infrastructure* rejection wearing an
   *AI* label. This violates the error-envelope rule in `python/CLAUDE.md`.
4. **All policy artifacts are blocked or invalid.** At startup:
   `management_policy`, `calibration_artifact`, `repeatability_artifact`,
   `hierarchical_priors` = `policy_file_missing`; `normalized_fvg_policy` =
   `runtime_input_incompatible`; `risk_factor_policy` / `invalidation_policy` /
   `normalized_fvg_policy` all `schema_valid=false`; `ledger_integrity_status=UNAVAILABLE`.
   Only 4 files exist in `PO3_AI_BUS\config`, and `bucket_risk_policy.json`
   (referenced by `InpBucketRiskPolicyFile`) is **not** among them.
   Consequence: every decision carries `repeatability_status=UNAVAILABLE`,
   `repeatability_trading_eligible=false`, `historical_evidence_state=INSUFFICIENT_SAMPLE`.
5. **The 18:23 session never reached the AI at all** — see the crash below.

### The APPROVE→ABSTAIN bug — confirmed, and fixed *after* the logged run

Request `591813800_1786509126_394060796_1786510782_GBPNZD_13183` (05:00):

```
analyst.decision_state = APPROVE     analyst.raw_allow = True
critic.verdict         = PASS        blocking_objections = []
final_resolver_reason  = analyst_approve_critic_pass
→ decision_state = ABSTAIN, raw_allow/model_raw_allow/python_final_allow = False
llm_quality_score 8.2 vs threshold 7.2 → passed=True
chosen_rr2 = -0.85145        (correct value is +0.85145)
```

RR check from the file's own prices: entry 2.29954, sl 2.29514, tp2 2.30328638 →
buy, risk 0.00440, reward 0.00374638, **rr2 = +0.85145**. The stored sign is inverted,
i.e. the direction was evaluated as sell. `-1.05` appears the same way in other responses.

Both defects are addressed in the current tree, **after** this run:
`ai_gate.py:4886-4887` now materializes `python_final_allow` before it participates in
state logic, and `ai_gate.py:3016-3034` recomputes `chosen_rr1/chosen_rr2`
deterministically from entry/SL/TP and direction. `ai_gate.py` was saved 05:22:41,
i.e. **22 minutes after** the 05:00 GBPNZD failure — so no logged run has yet exercised
the fix. The `.ex5` (05:31) and Python (05:22) are both post-fix.

Residual asymmetry worth reviewing: `ai_gate.py:4322` only *demotes* on
`not consensus.python_allow`; there is no matching promotion branch.

### EA crash — the current top blocker

```
18:23:24  Terminal FxPro started …  0 / 15 Gb memory      ← zero free RAM
18:23:28  expert PO3_AIGate_ScannerEA loaded successfully
18:23:31  scan started symbols=81
18:23:37.392  Abnormal termination
18:23:37.404  expert PO3_AIGate_ScannerEA (#USSPX500,M1) removed
```

The EA died 6 seconds into its first scan, after ~14 of 81 symbols, with zero AI
requests created. Contrast with 04:31:51, which reported `4 / 15 Gb memory` and ran
34 minutes without incident. Only one `Abnormal termination` exists in the whole
99,907-line journal — it is the last line.

Concurrent load at that moment: VS Code, WSL `vmmem`, Discord, Edge WebView, and
**four `pytest` processes from an unrelated project** (`Trading_assistant`), which are
still running. Free RAM measured during this investigation: 2.15 GB of 15.4 GB.
Memory exhaustion is the leading hypothesis; it is not yet proven by a controlled repro.

Also present in that session: `insufficient_bars` / `no valid PO3 context` for 13 symbols
(USDJPY, SILVER, #UK100, #US30, #USNDAQ100, #USSPX500, #US2000, #Japan225, #Euro50,
#Germany40, #France40, CADCHF, CADJPY) — cold-start history not yet downloaded, and the
EA crashed before it could be.

### Current runtime status (updated 2026-08-12 20:08)

- MT5 terminal: **running** (pid 3388, since 18:23). EA **recovered and running** —
  it was re-attached after the 18:23 crash; the journal has grown to 170,727 lines.
- Python AI gate: **running**, `ai_gate.py run --workers 2`, pid 19296.
- Bus queues: `requests`/`processing`/`responses` all 0; `rejected` 185; `quarantined` 19.
- MCP bind error on terminal start: `127.0.0.1:22346 … (10013)` — port taken.
- Stale lock left behind in `<bus>\locks` from 2026-08-11 21:41
  (`…#US2000_30982.json.lock`) — never cleaned up.

### Verified bus paths (from the gate's own startup banner)

All confirmed present: `<bus>`, `requests`, `responses`, `stale`, `processing`,
`logs`, `logs\analytics_jobs` (empty), `locks`. Nothing missing.

---

## 4b. Live evening session 19:04–20:07 — the current, authoritative evidence

This session supersedes the morning data. 29 decisions, **0 approvals, 0 orders**.

### What is now fixed and proven in a live run

- **RR2 is no longer negative.** Every value is positive (1.05, 1.028986, 1.051976).
  The deterministic recalculation is working end to end.
- Transport stayed healthy: no timeouts, no deadline breaches, no transport retries.
- The EA survived a full 81-symbol scan cycle.

### The blocker has moved: 38% of decisions now die on infrastructure

| Count | `decision_source` | Nature |
|---:|---|---|
| 18 | `ai_abstained` | ✅ genuine AI judgement — acceptable |
| 5 | `structured_response_invalid` | ❌ infrastructure |
| 5 | `decision_evidence_reference_reject` | ❌ infrastructure |
| 1 | `local_pipeline_error` | ❌ infrastructure |

**11 of 29 (38%) are infrastructure rejections on healthy infrastructure** — which
`python/CLAUDE.md` explicitly classifies as *not acceptable*.

### Root causes of the three infrastructure classes

1. **`structured_response_invalid`** — `identity_validation valid=false … reason=
   model_candidate_mapping_invalid error=deterministic_rr_non_positive:
   candidate_index=1:field=chosen_rr1`.
   The RR fix **detects** a non-positive RR and fails the whole response closed, but the
   *upstream* cause (TP1 landing on the wrong side of entry) was never fixed. The fix
   converted a silent wrong number into a hard kill of an otherwise evaluable response.
   Only 2 of the 5 are RR-attributable; the rest need separate tracing.
2. **`decision_evidence_reference_reject`** — the model cites evidence IDs belonging to
   *other* candidates (`cross_candidate_ids=[80, 112, 114, 135, 137]`).
   `evidence_reference_repair … attempt=1 result=failed` — the repair pass runs, costs a
   second provider call, and still fails.
3. **`local_pipeline_error`** — `ValueError:model_abstain_contract_invalid:
   candidate_index=2`, surfaced as `RuntimeError: selected_ai_provider_call_failed`.
   `error_category=LOCAL_PIPELINE_ERROR` is correct, but the log line
   `[ai_gate] selected_provider_call_failed provider=openai_remote_api` **names a local
   validation failure as a provider failure** — a logging-authority violation.

### The pre-AI funnel is unchanged and still the largest loss

```
scans=81  raw_fvgs=7427 → accepted_fvgs=100 → branch_candidates=80 → ai_requests=3
```

### Second gate instance — needs cleanup

Two `ai_gate.py run --workers 2` processes are alive, both started 20:06:22:
pid 19296 (`C:\Program Files\Python311`, WS 102 MB, actively working) and
pid 23308 (`…\python\.venv`, WS 4 MB, idle).
Only pid 19296 logged `[single_instance] acquired=true`; pid 23308 never did, so the
guard appears to have held. The idle process should still be killed to remove doubt.
Note the lease id `e5dc873194c5699f875e53f5aaae02e1` is **constant across all pids** —
it identifies the bus, not the holder, so it cannot be used to tell instances apart.

---

## 4c. Funnel verdict (2026-08-12 22:00) — bug vs over-strict, settled with data

### `structural_stop_invalid` (9,076/day) — a LABELLING BUG, not an over-strict filter

Parsed all 8,120 plan-price rejections that carry prices. Result:

```
stop_dist/entry >  5%  : 8120  (100.0%)   <- every single one
median stop            : 13.96% of price
tp2 == 0               : 8120  (100.0%)   <- target never built
stop_model             : structural_sweep (100%)
crypto share           : 7589 / 8120  (93.5%)
```

**Not one** of them came from the genuine geometry checks. All 8,120 fire at the
`stop_dist > entry * InpStopMaxFracOfPrice` cap (`InpStopMaxFracOfPrice = 0.05`),
which used to report the reason as `structural_stop_invalid`. The stop is
structurally *valid* — it is simply wider than the 5% risk cap.

- **The rejection is correct.** A 14%-of-price stop must be rejected. Loosening
  the cap would be trade-forcing.
- **The label was a defect.** It merged a risk-cap rejection into the geometry
  failures and made the largest bucket in the funnel undiagnosable.
- **The real waste is universe selection.** 93.5% are altcoins whose structural
  sweep levels are inherently 10–20% away. They can never pass a 5% cap, yet the
  EA rebuilds their full plan on every scan. This is a symbol-list decision for
  the user, not a code fix.

Fixed: the cap now reports `stop_distance_exceeds_max_frac_of_price` and emits
`[stop_distance_cap]` with entry, sl, stop_dist, the measured fraction and the
configured cap.

### `synthetic_fallback_exceeds_max_target_distance` (10,130/day) — DESIGN CONTRADICTION

74% are **non-crypto** — this is the real blocker for tradeable symbols.

```
max_target_distance = min(stop x InpMaxPlanRR2 5.0,
                          ADR  x InpMaxTargetAdrFrac 0.80,
                          ATR  x InpMaxTargetAtrMult 5.00)
synthetic fallback demands reward = stop x InpFallbackRR2 1.05
```

Binding constraint is normally `ADR x 0.80`, so the fallback is **mathematically
impossible whenever `stop_dist > 0.762 x ADR`**. The structural-sweep stop is
unbounded while the target is ADR-capped, so wide-stop setups are dead on arrival
but still pay for a full plan build. Not yet changed — resolving it means moving
either the stop model or the target cap, which is a risk-posture decision.

### `execution_cost_r_too_high` (3,284/day) — 0% crypto, majors only. Not investigated further.

---

## 4d. Model A/B: gpt-5-nano vs gpt-5.6-luna (2026-08-12)

Switch happened at 21:09 (`.env` mtime); first luna response 21:17:58.

| | gpt-5-nano | gpt-5.6-luna |
|---|---|---|
| decisions | 42 | 14 |
| FULL_STRUCTURED | 30 (71.4%) | 12 (**85.7%**) |
| infrastructure failures | 12 (**28.6%**) | 2 (**14.3%**) |
| `structured_response_invalid` | 5 | **0** |
| `local_pipeline_error` | 1 | **0** |
| `decision_evidence_reference_reject` | 6 | 2 |
| `ai_rejected` (genuine REJECT) | 0 | 5 |
| approvals / orders | 0 / 0 | 0 / 0 |

The upgrade **eliminated** the RR and abstain-contract failures and roughly halved
the infrastructure rate. Note the evidence-citation failure rate is **unchanged**
(14.3% in both) — that one was a schema/prompt-scoping problem, not model capacity,
and is what fix #3 addresses. Luna sample is only 14 decisions; treat as indicative.

`AI_GATE_REASONING_EFFORT` is still `low`; `.env.example` recommends `high`. Untested.

---

## 4e. Fixes applied 2026-08-12 (all verified: 565 passed, 9 skipped, 287 subtests)

| # | Defect | File | Change |
|---|---|---|---|
| 2 | One bad TP1 killed the whole response | `ai_gate.py` ~3009 | Collect RR violations and **demote that candidate** to ABSTAIN (raw_allow False, size 0) instead of raising and discarding sibling assessments. New `[deterministic_rr_demotion]` log. |
| 3 | Mis-scoped evidence IDs cost a failed repair call | `ai_gate.py` ~2699 | Cross-candidate IDs are real catalog entries already excluded from `resolved_paths`; drop them as a Python-owned normalization. Unknown IDs, duplicate IDs and an empty remainder still fail closed. New `[evidence_reference_normalized]` log. |
| 4 | Degraded envelopes reported `candidate_assessment_count_mismatch` | `TradeEngine.mqh` ~2710 | Zero assessments on a non-trading tier now reports `error_envelope_no_candidate_assessments`. Trading tiers keep the strict check. |
| 5 | Local `ValueError` logged as provider failure | `ai_gate.py` ~4397 | `selected_provider_call_failed` is now reachable only under `ProviderCallError`; local validation logs `selected_local_validation_failed failure_domain=local_validation`. |
| A | `structural_stop_invalid` mislabel | `TradeEngine.mqh` ~9043 | Reports `stop_distance_exceeds_max_frac_of_price` + `[stop_distance_cap]` diagnostics. |

Tests: `python/tests/test_infra_rejection_fixes.py` (5 new). Existing
`test_python_rejects_approval_with_target_on_loss_side` was rewritten to assert the
**invariant** (candidate carries no approval / no positive RR / no size) rather than
the old exception mechanism — the safety property is unchanged, the blast radius is not.

Deployed `.ex5` rebuilt 2026-08-12 22:07:23 — **0 errors, 0 warnings**, newer than
every deployed `.mqh`.

---

## 4f. Swing conversion (2026-08-12 22:0x) — applied, partially verified

User decision: convert from micro-scalp to swing. No code fix to the ADR/target
contradiction — resolved by config instead.

Applied identically to **both** `Config.mqh` and the new
`…\MQL5\Profiles\Presets\Swing_V1.set` (11 values each, both read back and confirmed):

| Config.mqh line | Input | Old | New |
|---|---|---|---|
| 142 | `InpStrategyPreset` | `micro_intraday` | `swing_po3` |
| 143 | `InpScanIntervalMinutes` | 5 | 15 |
| 150 | `InpWatchlistMaxBars` | 90 | 600 |
| 151 | `InpWatchlistMaxMinutes` | 90 | 2880 |
| 174 | `InpHTF` | `PERIOD_M15` | `PERIOD_H4` |
| 175 | `InpEntryTF` | `PERIOD_M1` | `PERIOD_M15` |
| 176 | `InpConfirmTF` | `PERIOD_M1` | `PERIOD_M15` |
| 177 | `InpSessionMapTF` | `PERIOD_M5` | `PERIOD_H1` |
| 326 | `InpMaxTargetAdrFrac` | 0.80 | 2.50 → **retuned to 1.50** (see note) |
| 327 | `InpMaxTargetAtrMult` | 5.00 | 12.00 → **retuned to 8.00** (see note) |
| 498 | `InpMinMinutesBeforePenaltyCuts` | 20 | 30 |

`InpMinMinutesBeforeBE` (448) was already 45 — no change. Risk gates untouched:
`InpMinLiveRR2` 0.85, `InpFallbackRR2` 1.05, `InpStopMaxFracOfPrice` 0.05.

> **Note (verified 2026-08-15):** the two target-cap values were later retuned from the
> 2.50 / 12.00 recorded above to **1.50 / 8.00**. `Config.mqh:326-327`, `Swing_V1.set`, and
> the 2026-08-15 RECORD_ONLY run (`[runtime_inputs] InpMaxTargetAdrFrac=1.5000
> InpMaxTargetAtrMult=8.0000`) all agree — **no drift**. The `||0.8||` / `||5.0||` fields in
> the `.set` are optimizer start values, not applied values.

**`.set` timeframe values are numeric**: M1=1, M5=5, M15=15, M30=30, H1=16385,
H4=16388, D1=16408. (Not the 16385-for-M15 mistake made earlier in that session.)

Rebuilt: **0 errors, 0 warnings**, `.ex5` newer than every `.mqh`.

### Verification status — read this before trusting the conversion

- ✅ Both files carry the new values (read back).
- ✅ Compile clean.
- ✅ Swing inputs **do load into the engine**: a Strategy Tester run with explicit
  `[TesterInputs]` logged `InpEntryTF=15` (agent 3001), vs `InpEntryTF=1` on the
  default run (agent 3000).
- ❌ **`expected_duration_class=intraday_1h_plus` has NOT been observed in a journal.**
  Zero occurrences in either agent log — the short GOLD/M15 window produced no
  accepted plan, and that string is only emitted on the plan-accepted path.

Deterministic basis (`TradeEngine.mqh:6733-6735`):

```
tf_minutes >= 15 && min_target_dist > 0
                 && InpMinMinutesBeforeBE >= 45
                 && InpMinMinutesBeforePenaltyCuts >= 30   -> "intraday_1h_plus"
```

Before: failed on **two** counts (tf_minutes=1, PenaltyCuts=20). After: 15 / 45 / 30
all satisfied. So the class flips as soon as any plan is accepted — but that is a
code reading, **not** an observed log line.

### Blocker for live verification

**No chart has the EA attached.** It was removed after the 18:23 `Abnormal termination`
and never re-attached; scanning every `.chr` in every profile found zero references to
`PO3_AIGate_ScannerEA`. Attaching an EA to a chart is GUI-only and cannot be scripted.

To finish verification: attach `PO3_AIGate_ScannerEA` to a chart, load `Swing_V1.set`
via Inputs → Load, then grep the journal for `expected_duration_class`.

Free RAM at the time was 2.23 GB of 15.4 GB — the same condition as the 18:23 crash,
and H4 history for 81 symbols needs more, not less.

---

## 4g. Plans A + B implemented (2026-08-13) — obstacle-aware target ordering

### The single shared root cause

`_SelectObstacleAwareTarget` treated one obstacle two different ways:

```
liquidity loop      : obstacle CLAMPS the target -> reward shrinks -> fails RR floor -> discarded
synthetic fallback  : obstacle IGNORED           -> full reward    -> clears RR floor -> ACCEPTED
```

The floor bound the safe routes and was waived for the unsafe one, so preference became
*"cross a major obstacle at 1.05R"* over *"stop in front of it at 0.9R"* — which is exactly
what the AI reported as `ai_veto_target_arbitration_incoherent`.

### What changed

| Where | Change |
|---|---|
| `Types.mqh` ~415 | New `TargetRankCandidate` struct — every route scored before selection. |
| `TradeEngine.mqh` (new helpers) | `_RankAddTarget`, `_TargetRankEligible`, `_TargetRankCrossesMajor`, `_TargetRankObstacleInvolved`, `_TargetRankBetter`, `_PickBestRankedTarget`, `_RankedListHasCleanRoute`, `_LogTargetRank`, `_ApplyRankedTarget`. |
| main loop | Collects every route; splits *truncated-by-obstacle* from *genuinely-too-near*. |
| main loop | `partial_before_obstacle_then_liquidity` promoted to a first-class candidate. |
| structural gate | Best structural route is applied **before** the synthetic is even built. |
| synthetic path | A crossing synthetic is refused whenever any clean route exists; `why_not_synthetic_fallback` is populated instead. |
| tail | Ranked fallback, then `liquidity_target_truncated_by_obstacle` as a distinct reason. |

Ordering contract (`_TargetRankBetter`): eligible → non-major-crossing → higher RR *when an
obstacle is involved* → collection order. The last rule keeps no-obstacle behavior identical.

### Two flaws found in my own first implementation, both fixed

1. Ranking by insertion order alone picked a **0.5721R** capped route over a **3.0654R**
   partial route when severity was 5.50 (below `InpBlockerMajorSeverity` 6.5). Rule 3 added.
2. The newly promoted partial route **bypassed `max_target_dist`** (a 9.5354R runner got
   through). Now rejected as `partial_runner_exceeds_max_target_distance`.

### Verified — Strategy Tester GOLD M15, 2026.02.01–02.20, swing inputs

```
plans accepted           66     tp_model = partial_then_liquidity  (66 / 66)
rr_floor_decision        66     binding_constraint = structural_target_selected
synthetic_rr_fallback     0     <- was the dominant target model
synthetic_fallback_exceeds_max_target_distance   0     <- was 10,130/day
```

Remaining rejects are all genuine strategy filters: `too_far_from_vwap` 107,
`no_closed_sweep` 70, `failed_breakout_no_reclaim` 64, `slope_against_trade` 48.
**No target-arbitration failure of any kind remains in the funnel.**

Compile **0 errors, 0 warnings**. Python suite **565 passed, 9 skipped, 287 subtests**.

`ai_veto_target_arbitration_incoherent` could **not** be re-measured: the tester ran
`TESTER_AI_RECORD_ONLY`, which never consumes AI decisions (`trading=false` by design).
The structural cause is removed, but that acceptance number needs a CACHE_ONLY replay
or a live session to confirm.

---

## 4h. Full-scale RECORD_ONLY run 19:13–19:40 (2026-08-13) — Plan A/B confirmed at 11,399 plans

Tester log: `…\Tester\0148…\Agent-127.0.0.1-3000\logs\20260813.log` (222 MB, 365,547 lines).
GOLD M15, 2026.02.01–02.19, swing inputs. `.ex5` compiled 19:11:34 — newer than every
deployed `.mqh` (latest `TradeEngine.mqh` 19:08:57), so the run **did** exercise Plan A/B.

### This run could not trade, by design — two independent reasons

1. `[runtime_inputs] InpTesterAiMode=record_only`, `InpTesterAiCache=false`,
   `[tester_ai_mode] effective_name=TESTER_AI_RECORD_ONLY allow_live_wait_debug_trading=false`.
   The EA printed its own verdict: `[final_summary] no_trades_expected=true
   next_step=run_python_cache_export_then_cache_only`.
2. **`ai_gate.py` was not running.** Newest `ai_gate.log` write = 01:39:59; the only live
   python processes belong to the unrelated `Trading_assistant` project (2× pytest,
   2× uvicorn). Nothing consumed the exported requests.

So `ai_advisories_total=0`, `ai_final_allow_true_total=0`, `orders_placed_total=0` are all
expected. **Zero AI decisions exist for this run** — no AI conclusion can be drawn from it.

### Target arbitration — the real result, and it is decisive

```
plans accepted (plans_valid_total)   11,399
  partial_then_liquidity             11,000   96.5%
  synthetic_rr_capped_to_max_distance   298    2.6%
  synthetic_rr_fallback                  85    0.7%   <- was the dominant model
  htf_opposing_imbalance                  9
  liquidity_target                        7

[target_rank] emitted            177,242
  chosen partial_before_obstacle_then_liquidity   12,968
  chosen htf_opposing_imbalance                      505
                                          (= 13,473 = [target_candidates] count)

synthetic_fallback_exceeds_max_target_distance   ABSENT from the reject table
                                                 (was 10,130/day)
```

`partial_runner_exceeds_max_target_distance` — the guard added after catching my own
flaw — fired **13,223** times. Without it those over-long runners would have shipped.

### Reject table — every remaining blocker is a genuine pre-AI filter

| Count | Reason | Nature |
|---:|---|---|
| 8,421 | `pre_ai_floor_hard` | strategy quality floor, branch stage |
| 4,028 | `no_feasible_target` | e.g. GOLD stop 14,329 pts → sanitizer fails |
| 1,434 | `ote_outside_fvg` | strategy |
| 1,363 | `not_continuation_context` | strategy |
| 1,144 | `slope_against_trade` | strategy |
| 999 | `failed_breakout_no_reclaim` | strategy |
| 680 | `synthetic_fallback_crossed_obstacle_blocked` | **correct fail-closed** |
| 182 | `tester_ai_record_only` | mode artifact |
| 120 | `ai_chosen_target_exceeds_max_distance` | mislabelled — see below |

The 680 fire at `TradeEngine.mqh:8618` / `:8646`, both guarded by
`!_CanAskAiForTargetArbitration(p)`. In RECORD_ONLY the AI can *never* arbitrate, so this
number is an upper bound; with a live gate most of these route to AI arbitration instead.

**No infrastructure rejection of any kind appears in this run.**

### Cohort contamination in the request queue — must be handled before any replay

`<bus>\requests` holds **218** files in **three** cohorts (field 3 of the filename is the
run id):

| Cohort | n | Window | Build |
|---|---:|---|---|
| `6176203` | **182** | 19:13:44 – 19:40:14 | post-Plan-A/B (`.ex5` 19:11:34) ✅ |
| `5799625` | 18 | 19:08:10 – 19:09:13 | aborted run, **pre**-recompile ❌ |
| `32631390` | 18 | 01:22:58 – 01:26:35 | previous day's build ❌ |

Only `6176203` is this cohort. Feeding all 218 to the gate would spend provider budget on
36 requests built by superseded target arbitration and pollute the replay cache.
Dedup is working: `record_only_requests_exported=182`,
`record_only_duplicate_signatures_skipped=490`.

### Pre-existing mislabel, flagged not fixed — an AI label on a pre-AI decision

`p.target_source` is set to the literal `"ai_selected_partial_then_liquidity"` at
`TradeEngine.mqh:7227`, `:7400`, `:8178`, `:9090` — **even when no AI was consulted**, and
the validator at `:7853` / `:8987` then rejects with `ai_chosen_target_exceeds_max_distance`
(120×). Same defect class as the `structural_stop_invalid` mislabel already fixed in §4c.

**Deliberately not renamed.** The token is embedded in `tester_cache_signature` (visible in
the `[tester_ai_mode]` lines), so changing it changes the cache key and invalidates all 182
exported requests. It is a naming fix that must be sequenced with a cache-cohort migration,
not slipped into a verification run.

### Still open

`ai_veto_target_arbitration_incoherent → 0` remains **unverified** for the same reason as
§4g: RECORD_ONLY produces no AI decision to veto. Needs the CACHE_ONLY replay.

---

## 4i. Bus wiped for the live-demo run (2026-08-13 20:0x)

User decision: discard all tester history, go straight to live demo, measure every 60 min.

Deleted — matched on the tester simulated base `_1769904000_`, so **nothing live was touched**:

| Dir | Removed | Left |
|---|---:|---|
| `requests` | 218 (3 cohorts) | 0 |
| `responses` | 18 | 0 |
| `response_debug` | 18 | 108 (live) |
| `request_ledger` | 18 (all `state=ARCHIVED`) | 109 (live) |
| `locks` | 1 (stale from 2026-08-11) | 0 |

Backup: `…\scratchpad\bus_backup_20260813_200247` — requests/responses/response_debug/locks
copied; **the 18 ledger copies failed on Windows MAX_PATH (269 chars)** and exist only as
deletions. No functional loss (terminal archived tester records, deletion was the goal).

`rejected` 338, `quarantined` 19, `shutdown` 9, `completed` 3, `logs`, `config` kept as the
audit trail — filter by timestamp when measuring the live run.

### `Swing_V1.set` — verified live-ready, no edits needed

365 of 365 inputs present, zero drift vs `Config.mqh` (Plan A/B added no new inputs).
Live-safety and Plan A/B inputs already correct:

```
InpUseAI=true              InpAiStrict=true          InpAllowRuleOnlyLive=false
InpLiveFailClosedOnAIFailure=true                    InpAiVetoEnable=true
InpHardRejectCrossedObstacleTarget=false   <- lets AI arbitrate crossed obstacles
InpAllowPartialBeforeObstacle=true         <- required by Plan A
InpCloseManagedTradesBeforeMarketCloseMin=0          InpRiskPerTradePct=1.0
InpHTF=16388 (H4)   InpEntryTF=15   InpConfirmTF=15  InpScanIntervalMinutes=15
```

### Open items carried into the live run

1. ~~`pre_ai_floor_hard` looks like a defect~~ — **investigated and resolved, see §4j.**
   The gate was correct; the *diagnostics* were dead. Fixed.
2. `InpSetupFloorFailedBreakout=999.0` in the `.set` vs compiled default `29.0` — a floor of
   999 permanently disables that family. Only 93 rejections, so low impact; left unchanged
   because enabling/disabling a setup family is a strategy decision.
3. `bucket_risk_policy.json` is still **MISSING** from `<bus>\config` while
   `InpEnableBucketRiskPolicy=true`. Present in every prior run too, so it degrades rather
   than blocks. Not fabricated — the file governs risk sizing.
4. EA is attached to **no chart** (GUI-only action).

---

## 4j. `pre_ai_floor_hard` investigated (2026-08-13) — the gate was right, the log was blind

**My earlier hypothesis was wrong** and is retracted: the setup score is *not* read as zero
at the gate, and the 8,421 rejections are genuine.

### What actually happens

`_TryBuildCandidateFromBranch` built the plan in a **local** copy and published it only on
success:

```mql5
bool _TryBuildCandidateFromBranch(const TradePlan &base, …, TradePlan &out_plan, string &reason) {
   TradePlan p = base;            // local working copy
   …
   p.setup_score = _SetupScore(p);          // computed on the LOCAL
   if(!_ApplyPreAiSetupFloor(p, floor_reason)){
      reason = floor_reason;
      return false;               // <-- out_plan NEVER assigned (17 such exits)
   }
   out_plan = p;                  // only on success
   return true;
}
```

The caller logs the rejection from its own `out_plan`, which is still `ZeroMemory`'d:

```mql5
TradePlan p;  ZeroMemory(p);
if(!_TryBuildCandidateFromBranch(base, fvg_cands[i], branches[b], p, reject_reason)){
   … " setup_score=" + DoubleToString(p.setup_score, 2)      // structurally 0.00
     " setup_floor=" + DoubleToString(p.setup_floor_score,2) // structurally 0.00
     " rr2="         + DoubleToString(_ExecutionRR2(p), 2)   // structurally 0.00
```

So **every field in every branch-rejection line has always been zero** — for the largest
stage in the funnel. The gate compared the real score against the real floor; only the
evidence was missing. Same defect class as the `structural_stop_invalid` mislabel (§4c):
a correct decision made undiagnosable by its own log line.

### Fix

| Where | Change |
|---|---|
| `TradeEngine.mqh:9712` | `TradePlan p = base;` → `out_plan = base;`, and the 63 `p` references in the body renamed, so **all 17 exits publish** the working plan. Final `out_plan = p;` removed as redundant. |
| `TradeEngine.mqh:4018` | On a hard reject, `p.setup_floor_score = hard_floor` — the log used to print the *soft* floor (≥24) beside a *hard*-floor (≥18) rejection, so the arithmetic never explained the reject. |
| `TradeEngine.mqh:4020` | New `[setup_floor_gate]` line: `setup_score`, `hard_floor`, `soft_floor`, `default_floor`, `floor_source` (`active_policy` vs `deterministic_default`), `action`. |

### Safety review of the rename

- `base` is read **exactly once**, as the first statement, before any mutation — so even a
  caller aliasing `base` and `out_plan` is safe. No aliasing hazard.
- All 4 call sites (`:13117`, `:13200`, `:14183`, `:14420`) pass distinct objects, and every
  one of them ignores the out-param on the `false` path (`continue` / `return` without
  reading it). Publishing a partially-built plan on failure changes no behavior.
- `p` was the only single-letter identifier in the range (63/63 occurrences).

### Build

Recompiled with the **FxPro** MetaEditor (`C:\Program Files\FxPro - MetaTrader 5\metaeditor64.exe`)
— **0 errors, 0 warnings**. `.ex5` 20:35:49 > newest `.mqh` 20:19:00. Repo and deployed
`TradeEngine.mqh` hashes match.

### Verification status

Not yet observed in a journal. `[setup_floor_gate]` and non-zero `setup_score`/`setup_floor`
on branch rejects are the acceptance criteria — they will appear in the **live** journal as
soon as the EA is attached, no separate tester run needed.

(An earlier verification attempt launched `C:\Program Files\MetaTrader 5\terminal64.exe` —
the **wrong** installation. It exited without testing; the bus stayed clean at 0 requests.
Always use `C:\Program Files\FxPro - MetaTrader 5\` for this project.)

---

## 4k. Live demo 20:25–21:17 (2026-08-13) — infrastructure clean, one config kill-switch found

Terminal pid 24164 (FxPro) since 20:25; `ai_gate.py` pid 16360 since 20:44 (holds the lease).

### Infrastructure — 100% healthy, first time ever

7 decisions, **all `FULL_STRUCTURED`**, zero infrastructure rejections (was 38% in §4b).
`ai_veto_target_arbitration_incoherent` = **0** — Plan A's acceptance criterion (§4g) is
finally met in a live run. (The 11 occurrences in a raw log tail belong to older sessions.)

### The §4j fix is verified live

1,044 `[setup_floor_gate]` lines, and branch rejects now carry real numbers:

```
[setup_floor_gate] symbol=#Swiss20 branch=breaker_retest family=full_po3_continuation
  setup_score=103.08 hard_floor=992.00 soft_floor=998.00 default_floor=1003.00
  floor_source=deterministic_default action=reject_hard

setup_reject reject_stage=setup_floor reject_reason=pre_ai_floor_hard
  setup_score=102.10 setup_floor=992.00 rr2=2.12      <- all three were always 0.00
```

### What the fix immediately exposed — four floors are 33× too high

`Swing_V1.set` vs compiled defaults:

| Input | `.set` | default |
|---|---:|---:|
| `InpSetupFloorReversal` | **999.0** | 30.0 |
| `InpSetupFloorFullPO3` | **999.0** | 30.0 |
| `InpSetupFloorMicroPO3` | **999.0** | 30.0 |
| `InpSetupFloorFailedBreakout` | **999.0** | 29.0 |

With `InpSetupFloorTierCExtra=99.0` this yields `default_floor=1003`, `hard_floor=992`.
Observed `setup_score` is **102–103**. Those four families — `full_po3`, `micro_po3`,
`reversal`, `failed_breakout` — can **never** pass. This is `pre_ai_floor_hard` (1,044 live).

**Correction to §4i item 2:** I previously called this "only 93 rejections, low impact".
That was wrong — I checked one of the four inputs and attributed impact using the `flow=`
field, which is absent on most of these lines. Left unchanged pending the user's call, since
enabling core PO3 families is a strategy decision, not a defect fix.

### Live funnel

```
scans=88  raw_fvgs=5746 -> accepted_fvgs=191 -> branch_candidates=28 -> ai_requests=2
```

| Count | Reason |
|---:|---|
| 3,189 | `no_feasible_target` |
| 1,358 | `synthetic_fallback_crossed_obstacle_blocked` |
| 1,044 | `pre_ai_floor_hard` (the 999 floors) |
| 604 | `stop_distance_exceeds_max_frac_of_price` |

**Prediction from §4h not borne out:** I expected `synthetic_fallback_crossed_obstacle_blocked`
to shrink live once AI arbitration became possible. It did not (1,358). So
`_CanAskAiForTargetArbitration()` is returning false in live too, for a reason not yet
traced — `InpHardRejectCrossedObstacleTarget=false`, so it is one of the other guards at
`TradeEngine.mqh:1846` / `:8618`. Next item to investigate.

### AI rejections — all genuine and evidence-backed

6 `REJECT` + 1 `ABSTAIN`. `rule_score` 8.6–10.0 but `llm_quality_score` 2.0–5.1 vs
threshold 6.8–7.0 (`authority=diagnostic_only` — the score is not what rejects).
Vetoes: `structural_contradiction` ×2, `missing_mandatory_evidence` ×3,
`execution_plan_mismatch` ×1. Typical reason:

> "Full PO3 continuation requires BOS, but HTF BOS and source BOS are absent; the state
> reports missing structure confirmation."

The deterministic rules like these setups; the AI rejects them for missing structure. That
is the gate working as designed.

### Housekeeping

Two `ai_gate.py` processes again: pid 16360 (Python311, working, holds lease) and pid 9368
(`.venv`, no `[single_instance] acquired=true` line). Kill 9368.

---

## 4l. Setup floors recalibrated from measured score distributions (2026-08-13 21:3x)

All Python gates killed, terminal closed by the user, bus queues 0/0/0 — clean restart point.

### Why the old numbers were meaningless

`_SetupScore` (`TradeEngine.mqh:9659`) is an **unbounded additive** score: base `fvg.score`
(~33–40) plus `trend_strength*20`, sweep strength, killzone, BOS/MSS/CHoCH, ADX, RR band,
origin/cleanliness/nesting/overlap/retest sub-scores. Observed range in the live session:
**76.8 – 182.4**. The compiled defaults of 28–30 date from an era when the score was
roughly `fvg.score` alone, so they are now **no-ops**; 999 was a kill switch.

### Two mechanics that decide the real threshold

1. **`InpAiStrict=true` makes the SOFT floor bind, not the hard one** (`:4023-4030` →
   `pre_ai_floor_strict`). So the effective cut is `input − 5`, not `input − 11`.
2. **`InpSetupFloorReversal` does double duty.** `:9436` computes the stale-FVG floor as
   `MathMax(InpSetupFloorRange, InpSetupFloorReversal) + 4`, so a 999 there was blocking
   stale FVGs in *every* family — that is why `micro_range_reentry` (floor input 28)
   appeared in the reject log with `floor=992`.

Exact family→input map is `_FamilySetupFloor` (`:3204`). Note `:3205` — the
`session_reentry` branch returns `InpSetupFloorSession` **before** the family lookup, which
is why some `full_po3_continuation` candidates were accepted while 903 were rejected.

### Measured distribution (complete population — floor 992 rejected all of them)

| Family | n | min | p10 | p25 | med | p75 | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| `full_po3_continuation` | 903 | 76.8 | 104.5 | 118.5 | 133.3 | 151.3 | 182.4 |
| `full_po3_reversal` | 125 | 87.7 | 112.9 | 130.8 | 152.4 | 157.0 | 179.2 |
| accepted (floor-28 families) | 159 | 105.7 | 109.0 | 115.0 | 129.0 | — | 177.8 |

Blocked fraction of pooled `full_po3` (n=1028) by effective cut:

```
cut  90 -> 3.5%     cut 105 -> 10.4%
cut  95 -> 3.9%     cut 110 -> 16.0%
cut 100 -> 4.5%     cut 115 -> 21.1%
```

There is a **knee between 100 and 105**: flat 3.5→4.5% up to 100, then it climbs steeply.
An effective cut at **100** removes exactly the weak outlier tail (4.5%) and leaves 95.5%
for the AI — a working filter, below the knee, so it is robust to session drift.

### Values applied to `Swing_V1.set` (only this file, per user instruction)

| Input | old | new | effective cut | basis |
|---|---:|---:|---:|---|
| `InpSetupFloorFullPO3` | 999 | **105** | 100 | knee of the n=1028 distribution; blocks 4.5% |
| `InpSetupFloorReversal` | 999 | **105** | 100 (stale: 104) | same posture; also restores the stale-FVG floor for all families |
| `InpSetupFloorMicroPO3` | 999 | **95** | 90 | no direct samples; micro variants run lower — deliberately conservative |
| `InpSetupFloorFailedBreakout` | 999 | **100** | 95 | no samples (dies earlier at `failed_breakout_no_reclaim`); group bonus +3.0 ≈ continuation's +3.2, and `InpMinRRFailedBreakout` was already relaxed to 0.45, so 999 reads as an accident |

Backup: `Swing_V1.set.bak_floors`. Validated: 365/365 inputs, 376 lines, zero drift.

### Deliberately left alone

- **`InpSetupFloorTierCExtra = 99.0`** with `InpRequireConfirmedPO3ForExecution=false`
  (`:9438-9441`) means tier-C contexts get floor `input + 99` → effective 199, above the
  max observed score of 182, so tier C is unreachable.
  **Measured impact: zero.** A scan of the whole live session found **0** tier-C contexts
  (`tier_a_contexts=104`, `tier_b_contexts=180`). This switch blocks nothing in practice —
  it is not a candidate for tuning.
- `Config.mqh` compiled defaults stay 29/30. They are no-ops, so without the `.set` there is
  effectively no floor — a divergence worth knowing, left unchanged per instruction.

---

## 4m. Two Plan A/B regressions found and fixed (2026-08-13 21:4x) — both were mine

Investigating the top two funnel blockers traced **both** back to my own Plan A/B work.

### Bug 1 — `no_feasible_target` (39.7% of all live rejections)

`_SelectObstacleAwareTarget` publishes the synthetic fallback at `p.fallback_tp` (old
`:8594`). My Plan A/B structural early return (old `:8573-8582`) fired **before** that line,
so whenever a clean structural route won — the now-normal case,
`binding_constraint=structural_target_selected` × 1,759 — `p.fallback_tp` stayed 0.

Every later stage re-reads that field as its safety net. `_ApplyFeasibleTargetSanitizer`
(`:8843`) then reported `fallback_infeasible_reason=missing_tp` with
`reward = _RewardToTarget(is_buy, entry, 0) = entry price` — which is why the log showed the
tell-tale `reward=4381.16` equal to the entry, `rr=63.0564`, `direction_valid=false`.

**Fix:** hoisted the fallback *computation and publication* above the structural early
return, keeping *selection* where it was. Plan A only requires that a crossing synthetic
never **outrank** a clean route — not that it go uncomputed. Verified: every `return` in the
function is now after the publish point.

### Bug 2 — `synthetic_fallback_crossed_obstacle_blocked` (16.9%)

My Plan A refusal branch was gated on `_RankedListHasCleanRoute(ranked) || saw_valid_target`.
`saw_valid_target` only records that a route was *evaluated*, not that any is *eligible*. With
zero eligible routes the branch was still entered, `_PickBestRankedTarget` returned −1, and
the plan died as `synthetic_fallback_crossed_obstacle_blocked` — a reason asserting a clean
route existed when none did, with a matching false `why_not_synthetic_fallback` string.

**Fix:** gate on `_RankedListHasCleanRoute(ranked)` alone; with no clean route, fall through
to the normal capped / max-distance synthetic handling at `:8681` instead of rejecting.
`_PickBestRankedTarget` itself was correct (it does skip ineligible routes).

### Verification — Strategy Tester GOLD M15, 2026.02.02–02.06, swing inputs + new floors

Compile **0 errors, 0 warnings**. Run finished in 4:37.

| Metric | Live session (before) | Verify run (after) |
|---|---:|---:|
| `missing_tp` occurrences | the 40% driver | **0** |
| `no_feasible_target` | **39.7%** | **9.2%** (123) |
| `synthetic_fallback_crossed_obstacle_blocked` | **16.9%** | **0 — absent** |
| `pre_ai_floor_hard` | **13.0%** | **0** (→ `pre_ai_floor_strict` 33, 2.5%) |
| plans accepted | — | 362 |
| AI requests queued | 7 (whole session) | **43** |

`tp_model`: `partial_then_liquidity` 302 (83%), `synthetic_rr_fallback` 50,
`liquidity_target` 7, `htf_opposing_imbalance` 3.

The **remaining 123 `no_feasible_target` are legitimate**: 41 carry
`fallback_reason=target_already_reached` — price has already passed the level. Not a defect.

Remaining rejects are all genuine strategy filters: `failed_breakout_no_reclaim` 247,
`slope_against_trade` 204, `suppressed_touched_continuation_not_retested` 138,
`no_closed_sweep` 95.

The new floors are confirmed working: `pre_ai_floor_hard` is gone and the binding gate is now
`pre_ai_floor_strict` (33) — exactly as predicted from `InpAiStrict=true` making the soft
floor bind at `input − 5`.

### Checked, not a regression

`po3_context_created_total=0` in this run, with `closed_sweeps_found=0` and
`displacement_passed=0`, while 362 plans still built. The counter counts **new** context
creations; the 02.02–02.06 window reused GOLD state persisted by the earlier 02.01–02.19 run.
Consistent with all the evidence, though not independently proven.

### Operational note learned the hard way

MT5 **rejects a `/config:` ini written as UTF-8 with BOM** — the `EF BB BF` corrupts the
`[Tester]` header and the terminal starts but silently runs no test (this cost two failed
attempts). Write tester ini files as **ASCII**:
`[IO.File]::WriteAllText($path, $text, [Text.Encoding]::ASCII)`.

### State handed over

Bus wiped again after the run (43 tester requests). `requests`/`processing`/`responses`/
`stale`/`timed_out`/`locks` all **0**. No terminal, no Python running.
`.ex5` 21:52:12 > newest `.mqh` 21:49:54. Repo and deployed `TradeEngine.mqh` hashes match.
`Swing_V1.set` 365/365 inputs with the four recalibrated floors.

---

## 4n. Live run 22:40–23:57 (2026-08-13) — fixes confirmed; new root cause at the AI layer

### The two fixes from §4m are confirmed live

| Reason | Before (20:25–21:17) | After (22:40–23:57) |
|---|---:|---:|
| `missing_tp` | the 40% driver | **0** |
| `synthetic_fallback_crossed_obstacle_blocked` | 16.9% | **0** |
| `no_feasible_target` | 39.7% | 15.5% |
| `pre_ai_floor_hard` | 1,044 | 246 (+160 `_strict`) |
| **`branch_candidates`** | **72** | **1,050** (14.6×) |
| `plan_prices_valid` | 1,523 | 2,955 |
| `ai_requests` | 7 | 10 |

### Honest note on the target mix

`synthetic_rr_fallback` is back to **1,665 of 2,955** accepted plans (56%), vs 0.7% in the
§4m tester verification. Not a regression: that verification ran on a tiny population while
`pre_ai_floor_hard` was killing 8,421 candidates. With 14.6× more candidates surviving, many
are weaker setups with **no clean structural route** (`binding_constraint=structural_routes_below_floor`
× 1,195), so they legitimately fall through to the synthetic — which is exactly what the §4m
bug-2 fix restored. Crossing synthetics are still blocked at `:8683`.

### NEW CONFIRMED DEFECT — `full_po3_*` family assigned without BOS

7 of the 10 AI decisions were vetoed `ai_veto_missing_mandatory_evidence`, all with the same
finding. Verified directly in the archived request payloads (`<bus>\rejected`, n=30):

```
setup_family     = full_po3_continuation      <- claims FULL PO3
has_sweep        = True
has_displacement = True
has_bos          = False                      <- but no BOS
t_bos            = 0
story.source_t_bos = 0
po3_state        = PO3_DISPLACEMENT_CONFIRMED  (not PO3_FVG_CONFIRMED)
po3_state_reason = missing_structure_confirmation
```

8 requests carry `full_po3_continuation` + `has_bos=False`. Full PO3 is *by definition*
sweep + displacement + BOS, so the EA is labelling a **developing** PO3 as a **full** one and
paying for an AI call whose rejection is guaranteed.

`_DeriveSetupFamily` → `_FamilyForTaxonomy(p.setup_taxonomy)` assigns the family from the
taxonomy without consulting `has_bos`, and `InpRequireConfirmedPO3ForExecution=false`
(`Swing_V1.set`) permits unconfirmed contexts to proceed. **The EA is configured to allow
non-BOS setups while the AI contract requires BOS for full-PO3 families — the two settings
contradict each other.** Same mislabel defect class as §4c, §4h and §4j.

Two candidate resolutions, both the user's call:
1. `InpRequireConfirmedPO3ForExecution=true` — stops the contradictory candidates at the gate.
2. Taxonomy fix — a setup without BOS must not be named `full_po3_*`; it belongs in a
   developing/micro family with its own AI contract.

### Pre-AI bottleneck: 2,955 valid plans → 10 AI requests

Dominated by **config switches**, not defects:

| Count | Reason | Input |
|---:|---|---|
| 1,280 | `suppressed_touched_continuation_not_retested` | `InpSuppressTouchedContinuationUnlessRetested` |
| 784 | `execution_cost_r_too_high` | `InpStandardTradeCostRCeiling=0.24` |
| 650 | `suppressed_stale_fvg_branch` | `InpSuppressStaleFvgBranches` |
| 600 | `stop_distance_exceeds_max_frac_of_price` | `InpStopMaxFracOfPrice=0.05` |

### Infrastructure

10/10 decisions `FULL_STRUCTURED`, zero infrastructure rejections, zero
`ai_veto_target_arbitration_incoherent`. GOLD at 23:10 reached `llm_quality_score=7.1`
(above the 7.0 threshold) and abstained with **no veto** — the closest to an approval so far.

---

## 4o. BOS contract gate added (2026-08-14) + synthetic-fallback investigation

### BOS is NOT a "bringing" bug — the request carries it completely and honestly

Dumped the full `po3` block of an archived EURCAD request. Every BOS field is present and
internally consistent:

```
has_bos=False   htf_bos=False   htf_internal_bos=False   htf_swing_bos=False
ltf_bos=True    ltf_internal_bos=True   ltf_swing_bos=True
developing_bos=True     context_tier=B     t_bos=0     bos_level=1.60640
structure_type=htf_displacement_confirmation      ltf_structure_type=internal_ltf_bos
po3_state=PO3_DISPLACEMENT_CONFIRMED              po3_state_reason=missing_structure_confirmation
```

`bos_level` is populated because it is the level that *would* have to break, not a confirmed
break. `ltf_bos=True` is a lower-timeframe BOS. The HTF BOS genuinely does not exist.
`htf_bos_required` / `htf_bos_absence_classification` are Python-derived
(`evidence_catalog.py`, `decision_evidence.py`) — the model was **not** citing invented fields.

**So the only defect was that the request was sent at all.**

### Why it was sent — two independent holes

`_DeterministicExecutionGate` (`:9522`) already had the right check at `:9552`, but:

1. At AI-request time it is called as `_DeterministicExecutionGate(p, rule_reason, false)`,
   and every BOS/sweep/displacement check is guarded by `execution_stage` — so **the BOS
   check was skipped entirely before the provider call**.
2. Even at execution stage, `tier_b_execution_allowed` (`:9526`) waives it whenever
   `InpRequireConfirmedPO3ForExecution=false` and `context_tier=="B"`.

### Fix — pre-AI BOS contract gate (`TradeEngine.mqh:9556`)

```mql5
if(!execution_stage && InpUseAI && requires_full_po3 &&
   (!p.po3.has_bos || p.po3.t_bos <= p.po3.t_disp)){
   reason = "full_po3_family_without_confirmed_bos";
   _Journal("[bos_contract_gate] …");
   return false;
}
```

Deliberately **not** subject to `tier_b_execution_allowed`: that waiver exists to let a
developing context reach *execution* once something approved it, and it cannot waive the AI's
own evidence requirement — the AI is the thing doing the approving.

### Verification

Compile **0 errors, 0 warnings**. `.ex5` 00:40:19 > newest `.mqh` 00:36:00, hashes match.

- **GOLD M15 02.02–02.06:** gate fired **0** times; output byte-identical to the pre-fix run
  (362 plans, same reject table). GOLD's full-PO3 candidates all carry a real BOS → correct
  no-op, proves no regression.
- **EURCAD M15 02.02–02.06 (multi-symbol scan):** gate fired **41** times, e.g.
  ```
  [bos_contract_gate] symbol=USDCAD family=full_po3_reversal has_bos=false htf_bos=false
    ltf_bos=true developing_bos=true t_bos=0 context_tier=B
    po3_state=PO3_DISPLACEMENT_CONFIRMED action=reject_before_provider_call
  ```
  Exactly the profile that produced 7 of 10 live `ai_veto_missing_mandatory_evidence` vetoes.

### Investigation only (no fix): why `synthetic_rr_fallback` returned to 56%

Measured, not inferred. **All 1,195** synthetic selections in the live run carry:

```
binding_constraint = structural_routes_below_floor     (1195 / 1195)
structural_routes_seen = true                          (1195 / 1195)
no_structural_target_existed                           = 0
```

So a structural route **always existed**; none was *eligible*. Why routes were ineligible
(`[target_rank] reject=`):

| Count | Reason |
|---:|---|
| 16,397 | `truncated_by_obstacle_below_floor` ← dominant |
| 13,280 | (eligible) |
| 5,899 | `below_rr_floor` |
| 5,716 | `partial_leg_below_floor` |
| 2,793 | `partial_runner_exceeds_max_target_distance` |

**Causal chain:** §4l unblocked the `full_po3_*` families (dead at floor 999) → those are the
tier-B / developing-BOS contexts → their liquidity targets sit behind obstacles → the route is
clamped back to the obstacle and its shrunken reward no longer clears `InpMinLiveRR2=0.85` →
no eligible clean route → §4m bug-2 fix lets them fall through to the synthetic.

**This is not a regression.** Those same plans previously *died* as
`synthetic_fallback_crossed_obstacle_blocked` (1,358 in the prior session vs 1,195 synthetics
now — the numbers line up). The synthetic replaced deaths, not partials.

`full_po3_*` accounts for **1,490 of the 1,665** synthetics (89.5%), so the new BOS gate should
remove a large share of them as a side effect — to be measured on the next live run.

### Flagged, not investigated (out of scope this turn)

The EURCAD multi-symbol run showed `no_feasible_target` = **22,503** against 4,034 accepted
plans — far higher proportionally than the live run (1,449 of 9,344). Different symbols and
period, so not directly comparable, but it warrants its own look.

---

## 4p. Live run 01:46–02:18 (2026-08-14) — BOS gate works; it exposed a Python bug

### The BOS gate is confirmed working live

Fired **111** times (222 candidate rejections). Effect on the AI decision mix:

| | 22:40–23:57 | 01:46–02:18 |
|---|---|---|
| decisions | 9 REJECT + 1 ABSTAIN | **5 ABSTAIN + 3 REJECT** |
| `missing_mandatory_evidence` | **7 of 10** | **1 of 8** |
| abstain quality scores | — | 7.1, 6.4, 6.2, 6.0, 5.8 |

Rejected candidates used to score 2.0–2.5; the surviving population now abstains at 5.8–7.1.

### NEW BUG — `htf_bos_absence_classification` is not computed from `htf_bos`

`decision_evidence.py:44-46`:

```python
"htf_bos_absence_classification": (
    "MANDATORY_MISSING" if full_po3 else "OPTIONAL_CONTEXT_ONLY"
),
```

`_family_requirement_contract(taxonomy, profile)` **never receives the actual `htf_bos`
value**. The field name promises a classification *of an absence*, but the value is a pure
function of the family — so every full-PO3 candidate is labelled `MANDATORY_MISSING`
**even when `sequence.htf_bos == true`**.

Two of the three vetoes are exactly this contradiction:

```
ai_veto_data_integrity_failure   "Observed HTF BOS=true conflicts with mandatory-missing
                                  HTF BOS classification."
ai_veto_sequence_contradiction   "HTF BOS is observed true, yet the mandatory full-PO3
                                  contract marks HTF BOS as missing; sequence integrity
                                  is unresolved."
```

**Why it was latent:** before the §4o gate, most full-PO3 candidates reaching the provider had
`htf_bos=false`, so `MANDATORY_MISSING` happened to be correct. Now that only BOS-confirmed
candidates get through, the label is wrong on nearly all of them. The gate and this bug
interact badly: the gate admits only candidates *with* BOS, then Python tells the model BOS is
mandatorily missing.

**This is now the top blocker** — a guaranteed veto generator on exactly the candidates we
want approved. Correct semantics need three states: BOS present → not absent; absent and
required → `MANDATORY_MISSING`; absent and not required → `OPTIONAL_CONTEXT_ONLY`.
Not yet fixed.

### Third veto — separate issue

USDJPY `missing_mandatory_evidence`: *"Full PO3 requires the complete declared sequence, but
dealing-range evidence is not supplied"*, citing
`family_requirement_contract.required_event_sequence`. Dealing-range evidence, not BOS.
Not investigated.

### Funnel and target mix

```
raw_fvgs 21,276 -> accepted_fvgs 570 -> branch_candidates 698 -> plan_prices_valid 2,302 -> ai_requests 8
```

| Count | % | Reason | Class |
|---:|---:|---|---|
| 932 | 14.1 | `suppressed_touched_continuation_not_retested` | config switch |
| 900 | 13.6 | `synthetic_fallback_target_already_reached` | legitimate |
| 855 | 13.0 | `no_feasible_target` | legitimate |
| 600 | 9.1 | `suppressed_stale_fvg_branch` | config switch |
| 576 | 8.7 | `synthetic_fallback_exceeds_max_target_distance` | legitimate |
| 560 | 8.5 | `execution_cost_r_too_high` | config (`InpStandardTradeCostRCeiling=0.24`) |
| 468 | 7.1 | `stop_distance_exceeds_max_frac_of_price` | config (`InpStopMaxFracOfPrice=0.05`) |

`tp_model`: `synthetic_rr_fallback` 1,255 / `partial_then_liquidity` 1,046 — still ~55%
synthetic, consistent with the §4o investigation (replaced deaths, not a regression).

Infrastructure: 8/8 `FULL_STRUCTURED`, zero infrastructure rejections.

---

## 4q. `htf_bos_absence_classification` fixed (2026-08-14) + a §4j regression caught late

### The fix

`decision_evidence.py:_family_requirement_contract` now takes the observed value and
classifies three states instead of two:

| observed `htf_bos` | family requires it | classification |
|---|---|---|
| **true** | yes | **`NOT_ABSENT_HTF_BOS_PRESENT`** (new) |
| false | yes | `MANDATORY_MISSING` |
| false | no | `OPTIONAL_CONTEXT_ONLY` |
| unknown / `None` | yes | `MANDATORY_MISSING` (fails closed) |

Threaded `po3.get("htf_bos")` from `build_decision_evidence_envelope` → `_candidate_evidence`
→ `_family_requirement_contract`. Keying off exactly the field the model reads
(`sequence.htf_bos`) makes the two structurally incapable of contradicting each other.

Also added: `htf_bos_observed` to the contract and to both `evidence_catalog.py` whitelists so
the observation and its classification are citable side by side; a rule in
`provider_decision_context.rules` explaining the three values; and
`PROVIDER_DECISION_CONTEXT_VERSION` → `20260814_provider_decision_context_v2` (the constant is
local to `decision_evidence.py`, not cross-checked against MQL).

### Blast radius — measured, not assumed

```
case                               OLD                    NEW
FULL_PO3, htf_bos OBSERVED TRUE    MANDATORY_MISSING      NOT_ABSENT_HTF_BOS_PRESENT  <- the bug
FULL_PO3, htf_bos absent           MANDATORY_MISSING      MANDATORY_MISSING           unchanged
MICRO,    htf_bos absent           OPTIONAL_CONTEXT_ONLY  OPTIONAL_CONTEXT_ONLY       unchanged
FULL_PO3, htf_bos unknown/None     MANDATORY_MISSING      MANDATORY_MISSING           unchanged
```

Only the defective case changed. No validator anywhere constrains the value set
(checked `ai_gate.py`, `structured_models.py`, `decision_pipeline.py`,
`decision_integrity.py`), so the third value is safe to introduce.

### Tests added (5)

In `ProviderDecisionContextTests`: present-BOS → not-absent; required-and-absent still
`MANDATORY_MISSING`; optional-and-absent unchanged; a 4-case subtest asserting the
**invariant** that the classification may never say "missing" while `sequence.htf_bos` is
true; and a catalog test proving both fields are citable.

### A §4j regression I should have caught earlier

`test_governance_contracts.py::test_mql_taxonomy_resets_for_every_branch_candidate` failed —
it hardcoded `'p.entry_model = branch;\n      p.setup_taxonomy = UNKNOWN_UNCLASSIFIED;'`, and
the §4j `p` → `out_plan` rename broke the string match. **The invariant itself was never
broken**; the test had been red since §4j because I compiled after that change but did not
re-run the Python suite. Marker replaced with a backreferenced regex
`(\w+)\.entry_model = branch;\s*\n\s*\1\.setup_taxonomy = UNKNOWN_UNCLASSIFIED;` — verified to
match both the old and new variable names and to **fail** if the reset is removed or applied
to a different object, so it is no weaker and no longer rename-fragile.

**Lesson: run the full Python suite after MQL-only edits too** — the governance tests assert
against MQL source text.

### Results

`570 passed, 9 skipped, 291 subtests` (was 565 / 9 / 287). Python-only change — no MQL edit,
no recompile needed; `.ex5` 00:40:19 remains current and hash-matched.

---

## 4r. Live run 02:55–03:41 (2026-08-14) — §4q fix confirmed; blocker is now follow-through

`decision_evidence.py` was saved 02:43:11; this session ran 02:55–03:41, so it is post-fix.

### Monotonic improvement across four sessions

| session | ABSTAIN | REJECT | dominant veto |
|---|---:|---:|---|
| 22:40–23:57 | 1 | 9 | `missing_mandatory_evidence` ×7 |
| 01:46–02:18 | 5 | 3 | `data_integrity_failure` / `sequence_contradiction` ×2 |
| **02:55–03:41** | **9** | **2** | **none dominant** |

`ai_veto_data_integrity_failure` and `ai_veto_sequence_contradiction` are **both gone** — the
§4q fix is confirmed in a live run. `[bos_contract_gate]` fired 233 times.

### The two surviving vetoes are legitimate

- AUDCAD `ai_veto_structural_contradiction`: *"MICRO_RANGE_REENTRY requires containment, but
  expansion dominates and the supplied analogue record is adverse."* — a correct catch.
- EURUSD `ai_veto_missing_mandatory_evidence`: cites trend context **and** "PO3 reports missing
  structure confirmation" **and** weak trend strength. `trend context` **is** in
  `MICRO_CONTINUATION_FVG.required_event_sequence`, so this one is contract-valid.

### The new binding constraint: `has_follow_through`

All abstains cite the same thing:

> "Complete PO3 sequence and valid FVG support the thesis, **but absent follow-through**…"
> "The mandatory PO3 sequence is observed, **but follow-through is false**…"

**Checked whether it is a plumbing bug — it is not.** Across 72 archived requests:

```
has_follow_through True :  9 / 72  (12.5%)
t_follow > 0            :  9 / 72   (moves together)
displacement_follow_score > 0 : 9 / 72   (moves together)
```

Compare `has_displacement` 72/72, `has_sweep` 51/72, `has_bos` 40/72. The field is computed
and populated correctly; follow-through is genuinely rare. **Not the BOS situation.**

### But there IS a contract gap

`follow_through` is **not** in `required_event_sequence` for **any** family:

```
FULL_PO3_CONTINUATION : ['dealing range','liquidity event','continuation displacement','BOS','entry']
FULL_PO3_REVERSAL     : ['dealing range','liquidity sweep','displacement','BOS','entry']
MICRO_RANGE_REENTRY   : ['range identity','range excursion','range reentry']
MICRO_CONTINUATION_FVG: ['trend context','continuation displacement','continuation FVG entry']
```

The envelope's own `optional_absence_rule` says: *"Do not reject, **abstain**, lower a score, or
add missing evidence solely because an optional global PO3 field is false or absent."*

So the model is down-weighting an optional field in a way the contract explicitly forbids.
BOS got explicit contract fields (`htf_bos_required`, `htf_bos_absence_classification`);
`follow_through` has no equivalent, so the model falls back to its own judgement. Closing that
gap the same way BOS was closed is the obvious next step — **not done, not yet agreed**.

### Funnel

```
branch_candidates 1,021 -> plan_prices_valid 3,256 -> ai_requests 11 -> watchlist_added 0
```

| Count | % | Reason | Class |
|---:|---:|---|---|
| 1,436 | 15.6 | `suppressed_touched_continuation_not_retested` | config switch |
| 1,314 | 14.3 | `no_feasible_target` | legitimate |
| 1,308 | 14.2 | `synthetic_fallback_target_already_reached` | legitimate |
| 834 | 9.1 | `execution_cost_r_too_high` | config |
| 714 | 7.8 | `suppressed_stale_fvg_branch` | config switch |
| 638 | 6.9 | `stop_distance_exceeds_max_frac_of_price` | config |

---

## 4s. Follow-through contract gap closed generically (2026-08-14)

### The plan

BOS and follow-through were the **same defect twice**: a global PO3 sequence flag with no
explicit contract entry, leaving the model to fall back on its own judgement. Rather than add
one-off `follow_through_*` fields and wait for a third instance, the mechanism was generalised.

### What changed

`decision_evidence.py`

- New `_SEQUENCE_FLAG_SPECS` — `(contract_name, sequence_field, regex)` — plus
  `_classify_sequence_flag` and `_sequence_flag_requirements`.
- New `family_requirement_contract.sequence_field_requirements`: per flag
  `{sequence_field, observed, required, required_by, absence_classification}`.
- Requirement is now **derived by matching `required_event_sequence`** instead of a hardcoded
  taxonomy set, and `required_by` records the matched token so it is auditable.
- Flat fields kept and now derived from the same map so they cannot diverge:
  `htf_bos_*` plus new `follow_through_required` / `_observed` / `_absence_classification`.
- Absence vocabulary unified on **`NOT_ABSENT_OBSERVED_PRESENT`** (replaces the
  BOS-specific `NOT_ABSENT_HTF_BOS_PRESENT` introduced in §4q).
- Two new `provider_decision_context.rules`: one explaining the three states, one stating
  that `OPTIONAL_CONTEXT_ONLY` is **resolved, not unresolved**, naming `follow_through`.
- `PROVIDER_DECISION_CONTEXT_VERSION` → `20260814_provider_decision_context_v3`.

`evidence_catalog.py` — the three `follow_through_*` fields added to both whitelists so they
are citable evidence.

### Scope discipline

Only `htf_bos` and `follow_through` are resolved. `sweep`/`displacement` were **deliberately
excluded**: the wording varies across families (`liquidity sweep` vs `liquidity event`), so
inferring them would invent or erase a requirement rather than report one. No veto has ever
cited them as misclassified.

### Verified — the rewrite changes no existing requirement

```
taxonomy                   bos_req  follow_req  required_by(bos)
MICRO_* (9 families)       False    False       None
FULL_PO3_REVERSAL          True     False       bos
FULL_PO3_CONTINUATION      True     False       bos
```

Keyword matching reproduces the old taxonomy-set result exactly, and a test asserts the two
agree for **every** taxonomy so a future profile edit cannot silently diverge.

The live case now resolves as:

```
htf_bos        observed=true  required=true   -> NOT_ABSENT_OBSERVED_PRESENT
follow_through observed=false required=false  -> OPTIONAL_CONTEXT_ONLY
```

### Tests

`574 passed, 9 skipped, 312 subtests` (was 570 / 9 / 291). Four new tests: follow-through
optional for every family; follow-through present reported as present; a 8-case subtest of the
general invariant (**no flag may read `MANDATORY_MISSING` while observed is true**); and the
per-taxonomy equivalence check above.

Python-only change — no MQL edit, no recompile.

### What this does and does not do

It makes the payload state the truth the contract already declared: follow-through is optional
for every family. It does **not** instruct the model to approve, and it removes no veto or
abstain authority. If follow-through *should* gate these families, the correct fix is the
opposite one — add it to `required_event_sequence` — and that is a strategy decision.

---

## 4t. Live run 04:30–05:37 (2026-08-14) + the TP1-authority fix

### The run

67 minutes, 5 cycles × 88 symbols. `plans_valid_total=3781` → **9 AI requests** → 0 approvals,
0 orders. Infrastructure stayed perfect: 9/9 `FULL_STRUCTURED`, `identity_mismatches=0`, zero
transport retries, zero schema repairs.

§4q and §4s **confirmed in the live payload** — the envelope rebuilt from an archived request reads
`htf_bos observed=true required=true → NOT_ABSENT_OBSERVED_PRESENT`,
`follow_through observed=false required=false → OPTIONAL_CONTEXT_ONLY`, `context_version=…v3`.
`missing_mandatory_evidence`, `data_integrity_failure` and `sequence_contradiction` are all **0**.

**8 of the 9 decisions named the same thing**: an opposing imbalance immediately adjacent to entry.
Two were hard vetoes, `ai_veto_target_arbitration_incoherent`, and the model was literally correct:

> "The proposed partial-then-liquidity plan names a TP1 before an obstacle, but the deterministic
> target candidate marks TP1 infeasible and the obstacle is major."

### One root cause with three faces — the first leg was never measured anywhere

USDJPY sell, entry 159.474, SL 159.567 (risk 0.093), obstacle at 159.471 = **0.0323R**, severity 7.00:

| | value |
|---|---|
| `plan.tp_model` | `partial_then_liquidity` |
| `plan.tp1` | **159.381** — 1.0R, *past* the obstacle |
| route `partial_before_obstacle_then_liquidity.tp1` | **159.473** — 0.0108R, in front of it |

`plan.tp1 != route.tp1` in **9/9** archived live requests.

1. **Pricing** — `_ApplyRankedTarget` published the partial only to `capped_before_obstacle_tp`, then
   `_BuildPlanPrices` rebuilt `tp1` from `tp1_r_multiple` with a floor of
   `MathMax(stop_dist*0.65, spread*InpMinTP1SpreadMult)`. For USDJPY that reproduces 159.381 exactly.
   The floor moved the leg **60× further out, across the obstacle the route existed to respect**.
   It also silently overwrote the AI-arbitrated tp1 (`:8302`) and the approved assessed tp1 (`:7625`).
2. **Ranking** — the partial route's admission flags `p_ok_rr`/`p_ok_dist` were computed from
   `full_rr` (the **runner**, 45.67R). The reject reason was named `partial_leg_below_floor` but
   never measured the partial leg, so a 0.0108R first leg passed a gate named after it — and then
   won the ranking, because `_TargetRankBetter` rule 3 ranks on `c.rr` = the runner's RR.
3. **Payload** — `AIGateBridge` computed the option's `available` / `feasible_for_tp2` /
   `infeasible_reason` / `tp1_before_obstacle_possible` from the runner alone, so the model was
   offered a partial the engine could not place, with an empty `infeasible_reason`.

Same defect family as §4c, §4h, §4j: **a name asserting something the code never checks.**

### Changes

| File | Change |
|---|---|
| `Types.mqh` | `TargetRankCandidate` += `partial_rr`, `partial_meets_floor`; `TradePlan` += `tp1_from_target_model`, `min_tp1_reward`. |
| `TradeEngine.mqh` | New `_MinTp1Reward` — **the single definition** of a placeable first leg, published to `p.min_tp1_reward` before any route is scored. `_TargetRankEligible` rejects a route whose declared leg misses it. Partial admission measures the leg and reports `partial_leg_below_tp1_floor`; the runner check is renamed truthfully to `partial_runner_below_floor`. `_ApplyRankedTarget` claims the leg (`p.tp1`, `tp1_from_target_model=true`). `_BuildPlanPrices` honours a route-owned tp1 and **fails closed** (`target_model_tp1_below_min_reward` / `_beyond_target` / `_wrong_side_of_entry`) instead of relocating it. New `[partial_leg_gate]` and `[tp1_authority]` logs. |
| `AIGateBridge.mqh` | `available` / `feasible_for_tp2` / `infeasible_reason` / `tp1_before_obstacle_possible` now consult the first leg via `p.min_tp1_reward`; emits `tp1_reward_price` and `tp1_min_required_reward`. Fails closed when the floor is unknown (0 ⇒ pre-fix state file). |
| `StateStore.mqh` | Persists and restores `tp1_from_target_model` and `min_tp1_reward` — without them a reloaded plan silently reverted to permissive. |
| `ai_gate.py` | `_compact_target_candidates` is a **whitelist**; the two new keys were added or the evidence would be dropped before reaching the model. |

The `else` branch of the TP1 builder is value-identical to the pre-fix code, so **plans with no
route-owned first leg are completely unchanged**.

### Verification — Strategy Tester, EURCAD M15 (multi-symbol), 2026.02.02–02.06, `Swing_V1.set`

Compile **0 errors, 0 warnings**. Python **580 passed, 9 skipped, 312 subtests**.

```
[partial_leg_gate] route_ineligible          64,475
target_rank reject partial_leg_below_tp1_floor  20,949
[tp1_authority] action=preserved              1,542
target_model_tp1_below_min_reward                  0   <- eligibility stops them upstream
```

**Decisive check** — pairing every `[target_rank] kind=partial_before_obstacle_then_liquidity
chosen=true partial_tp=X` with the `[tp1_authority] tp1=Y` that followed:

```
IDENTICAL 1542 / 1542      DIFFERENT 0        (live pre-fix was 0 / 9)
```

Representative refusal — a 0.30R leg against an 8.50-severity obstacle:

```
[partial_leg_gate] symbol=EURCAD entry=1.62728 partial_tp=1.62344 partial_reward=0.00384
  partial_rr=0.3026 min_tp1_reward=0.00825 runner_tp=1.61431 runner_rr=1.0221
  obstacle=htf_opposing_imbalance severity=8.50 action=route_ineligible
```

### Matched pre-fix baseline — same symbol, period, `.set`, one build apart

The pre-fix tree was reconstructed, compiled to `PO3_Baseline.ex5` (0 errors, 0 warnings) and run
on the identical config. It reports `[partial_leg_gate]=0`, `[tp1_authority]=0` and the old
`partial_leg_below_floor`, confirming it is genuinely the pre-fix binary.

| metric | baseline (pre-fix) | fixed | delta |
|---|---:|---:|---|
| `scans_total` | 727 | 726 | — |
| `plans_valid_total` | 4,034 | 3,474 | **−13.9%** |
| **`ai_requests_queued_total`** | 144 | **150** | **+4.2%** |
| `tp_model=partial_then_liquidity` | **2,284** | **46** | −98% |
| `tp_model=synthetic_rr_fallback` | 1,731 | 3,379 | +95% |
| `no_feasible_target` | **22,503** | **12,111** | **−46%** |
| rank reject: partial leg | 8,692 (`_below_floor`, runner) | 20,949 (`_below_tp1_floor`, leg) | now measures the leg |

Readings:

- **2,284 baseline plans shipped `partial_then_liquidity`**, and by the 9/9 live evidence
  essentially all of them carried a TP1 that was not the route's. Post-fix, 46 survive and
  all 1,542 route selections carry the correct leg.
- **Plans fall 560 while partial plans fall 2,238** — so ~1,678 were *re-routed* to a synthetic
  rather than lost. The fix redirects far more than it removes.
- **AI requests went UP (144 → 150) on 14% fewer plans.** No throughput regression at the
  decision stage, and what arrives is now coherent.
- **`no_feasible_target` halved.** This is the anomaly flagged and left uninvestigated at the end
  of §4o (22,503 on this exact symbol/window). The fix removes 46% of it as a side effect. The
  precise mechanism was **not** traced — recorded as a measured correlation, not a causal claim.
- `synthetic_fallback_target_already_reached` rises 1,094 → 7,532: setups that used to become
  bogus partial plans now route to a synthetic whose target price has already been passed. A
  legitimate, correctly-labelled rejection.

### Tests (6 new, all falsified against a reconstructed pre-fix tree)

In `test_governance_contracts.py`: every function assigning `p.tp1` must declare
`tp1_from_target_model` (a **function-scoped scan**, so a future fifth owner is caught);
the guard must precede the generic rebuild and the fail-closed reasons must exist; the partial
admission must measure its own leg; the payload must consult both legs and survive a state
round-trip; `stop_dist * 0.65` must appear exactly once; and Python must forward the two new keys.

Falsification was proven by reverting all three MQL files in a scratch tree and pointing
`PO3_MQL_INCLUDE_ROOT` at it — all six failed, naming the five undeclared owners.

---

## 4u. Live run 20:19–21:07 (2026-08-14) + the obstacle-symmetry fix

### The run

4 cycles × 88 symbols, `plans_valid_total=2,776` → 10 AI requests → 0 approvals. **Infrastructure
was flawless for the first time**: 10/10 `schema_valid`, 0 degraded, 0 hard-gate rejections,
0 identity mismatches (previous run: 1 / 1 / 1). §4t confirmed live — `[partial_leg_gate]` fired
**21,108** times, `[tp1_authority]` **114**, and **zero** requests carried a mismatched TP1 (was 9/9).

Decisions: 7 ABSTAIN + 3 REJECT. `branch_candidates` rose 190 → **383**, `tier_a_contexts` 107 → **142**.

### Defect 1 — the engine shipped a *worse* crossing route than one it had rejected

In **3 of 10** requests a fully feasible liquidity target existed and a crossing synthetic was sent instead:

| symbol | liquidity target | shipped | obstacle |
|---|---|---|---|
| GBPAUD | 4.75R, feasible, blocked | synthetic 1.05R, `crosses=True` | 7.00 major |
| #Euro50 | 4.78R, feasible, blocked | synthetic 1.05R, `crosses=True` | 7.00 major |
| EURJPY | 1.60R, feasible, blocked | synthetic 1.05R, `crosses=True` | **10.00 killer** |

The structural route was scored on its **clamped** reward (0.04R once the obstacle truncated it) and
discarded; the synthetic was scored on its **full** reward while crossing the same obstacle. So
"cross it for 1.05R" beat "cross it for 4.75R". This is **§4g's original asymmetry surviving in the
branch Plan A never covered** — Plan A only refuses a crossing synthetic when a *clean* route exists,
and §4m made the no-clean-route case fall through. §4t did not create it but removed the partial
route that had been masking it. Both AI vetoes named it exactly.

### Defect 2 — the model was asked to judge obstacle strength with the evidence blank

`obstacle_strength_features` was `<EMPTY>` in **6 of 10** requests; the correlation with
`liquidity_target.blocked_by_obstacle=False` was exact. It had a single writer — the branch that runs
when an obstacle sits before the *liquidity* target — so a plan with no valid liquidity target
shipped `obstacle_kind` with the severity missing. AUDUSD was then vetoed for crossing a
"fresh **strong** opposing imbalance" with severity blank, which the prompt explicitly forbids.

### Changes (all in `TradeEngine.mqh`)

| Change | Effect |
|---|---|
| New `_PublishObstacleEvidence` | Single writer for kind + price + distance + tf + **severity**. Used by the liquidity seed, the synthetic branch and `_ApplyRankedTarget`, so kind and severity can never travel apart. |
| Ranking loop | A truncated target now also enters the list **taken in full, marked `crosses_obstacle`** (`through_obstacle_below_floor` / `_exceeds_max_target_distance`), so it is comparable with the synthetic. It ranks below every non-crossing route, so the clean-route path is untouched. |
| New `_PickBestCrossingRoute` | Best eligible crossing route, **excluding killer severity**. |
| Synthetic branch | (1) `synthetic_severity >= InpBlockerKillSeverity` → reject `all_routes_cross_killer_obstacle`; (2) otherwise prefer a structural route through the same obstacle when it pays more. New `[obstacle_crossing_gate]` log. |

### Verification — same EURCAD M15 config, three builds

Compile **0 errors, 0 warnings**. Python **583 passed, 9 skipped, 312 subtests** (3 new tests,
all falsified against the pre-fix tree).

| metric | baseline | §4t only | **both** |
|---|---:|---:|---:|
| `plans_valid_total` | 4,034 | 3,474 | 2,553 |
| `ai_requests_queued` | 144 | 150 | 118 |
| **`synthetic_rr_fallback`** | 1,731 | 3,379 | **264** |
| dominant `tp_model` | `partial_then_liquidity` | `synthetic_rr_fallback` | **real structural levels** |
| `all_routes_cross_killer_obstacle` | — | — | 5,356 |
| `no_feasible_target` | 22,503 | 12,111 | 22,533 |

- **`synthetic_rr_fallback` collapsed 3,379 → 264 (−92%)** and real structural targets
  (`asia_session_high` 813, `prev_week_high` 302, …) now dominate. The synthetic was only winning
  because of the asymmetry.
- **9,529 selected routes now cross an obstacle at full structural value** vs 417 clean, e.g.
  `newyork_session_low rr=1.02 crosses_obstacle=true severity=1.50 chosen=true` — previously clamped
  and discarded in favour of a synthetic.
- `[obstacle_crossing_gate]` fired **2,678** times, all `reject_no_route_without_killer_crossing`,
  all at severity **8.50** (`htf_opposing_imbalance` + close range = 3+2+2+1.5). AI requests fall
  150 → 118 (−21%): that is the intended cost of the killer policy, and
  **`InpBlockerKillSeverity` (8.0) is the knob** if it proves too strict.
- `no_feasible_target` returns to the baseline ~22.5k because full structural targets are selected
  again, exactly as in baseline. Those plans die deterministically **before** any provider call, so
  the cost is CPU, not API budget.

### Not verified by observation

`prefer_structural_through_same_obstacle` — the major-severity branch — **never fired** in this
window because every obstacle reaching that point was severity 8.50 (killer). It is covered by code
review and tests only. The live run contained exactly the major case (GBPAUD / #Euro50 at 7.00), so
a live session will exercise it; that is the acceptance criterion to watch next.

---

## 4v. Live run 21:48–23:55 (2026-08-14) — FIRST APPROVAL. The gate works end to end.

Build `.ex5` 21:28:47 > newest `.mqh` 21:26:50 → this run exercised §4t + §4u. 9 cycles × 88 symbols.

```
scans_total 792   po3_contexts 619   fvg_candidates 1,840   plans_valid_total 5,716
ai_requests_queued_total 20   ai_advisories_total 20
ai_final_allow_true_total   1     <- first ever
watchlist_added_total       1     <- first ever
orders_placed_total 0   trades_opened_total 0
```

### Infrastructure — perfect, and the cleanest run in the project's history

```json
{"requests_processed":20,"schema_valid_responses":20,"schema_invalid_responses":0,
 "degraded_responses":0,"identity_mismatches":0,"hard_pre_gate_rejected":0,
 "ai_approvals":1,"ai_abstentions":12,"ai_rejections":7}
```

Zero infrastructure rejections. All 7 rejections are genuine vetoes: `structural_contradiction` ×6,
`missing_mandatory_evidence` ×1. `transport_retry=0` on every attempt; the `DEGRADED` string in the
log appears only inside `degraded_responses:0`.

### The approval — EURUSD, and §4u is what produced it

```
[decision_authority] model_raw_allow=true python_final_allow=true mql_final_allow=false
                     mql_reasons=pending_final_mql_execution_gates
EURUSD AI advisory decision_state=APPROVE decision_quality_tier=FULL_STRUCTURED hard_veto=false
  final_allow=true llm_quality_score=7.20 required=6.80 candidate_hash_match=true
  assessment_group_ok=true decision_source=ai_approved
[target_arbitration] required=true chosen=liquidity_or_synthetic_rr blocker_class=moderate
                     severity=5.50 allow=true
EURUSD deterministic gate approved candidate=0 setup_score=159.67 target_source=ai_selected_liquidity_target
[target_validation] pass=true stage=pre_watchlist rr2=4.412214 min_rr=0.85 target_reached=false
[semantic_plan_match] result=pass      [execution_adjustment_validation] result=pass
[watchlist_precheck] pass=true reason=ok action=added
EURUSD added to watchlist entry=1.15230 rr2=4.41 setup_class=full_po3_reversal.nested_htf_ltf_fvg...
EURUSD watchlist armed bars_waited=0 entry=1.15230
```

**The §4u through-obstacle route is what made this approvable.** The winning route:

```
[target_rank] kind=prev_week_high tp=1.15808 rr=4.4122 crosses_obstacle=true truncated=false
              obstacle=opposing_imbalance severity=5.50 eligible=true reject=none chosen=true
[target_rank] kind=capped_before_opposing_imbalance tp=1.15253 rr=0.1756
              reject=truncated_by_obstacle_below_floor chosen=false
```

Pre-§4u the clamped route (0.1756R) was the only structural entry and it fails the RR floor, so the
plan fell through to a 1.05R crossing synthetic — the exact shape the AI vetoed as
`target_arbitration_incoherent` for GBPAUD/#Euro50 in §4u. Taking the same obstacle at **full**
value produced a 4.41R real liquidity target instead, and the AI approved it.

So the **ranking half** of §4u is now verified live. `prefer_structural_through_same_obstacle`
(the synthetic-branch half) is **still unfired** — all 701 `[obstacle_crossing_gate]` events were
killer rejections.

### Why no order — normal, not a defect

`entry=1.15230` against `live_bid=1.15679` at approval: a **buy limit 45 pips below market**, into
the FVG left by the displacement that swept sell-side liquidity. Textbook PO3 — the watchlist arms
and waits for the retrace. Price never came back; the last scan at 23:55 still had the plan alive
and valid (`prev_week_high rr=4.0420 eligible=true chosen=true`). Session ended, EA removed.

### The setup is persisted and survives restart — verified

`…\PO3_AI_BUS\logs\runtime_state\live_account_591813800_magic_5303191\watchlist.ndjson`
(116,226 b, written 23:55:48). One record, and it carries the §4t/§4u fields intact:

```
symbol=EURUSD  is_buy=true  entry=1.15230  sl=1.15075  tp1=1.15385  tp2=1.15913893
tp1_from_target_model=true            <- §4t persisted
min_tp1_reward=0.00100750             <- §4t persisted
obstacle_strength_features="severity=5.50;class=moderate"   <- §4u: severity never blank
target_source=ai_selected_liquidity_target   tp_model=liquidity_target   bars_waited=2
```

`InpWatchlistMaxMinutes=2880` (48 h) binds before `InpWatchlistMaxBars=600`, so it had ~47 h left.

### Gate activity

| Gate | Fired | Source |
|---|---:|---|
| `[partial_leg_gate]` | 51,276 | §4t |
| `[obstacle_crossing_gate]` | 701 (all severity 8.50, all `reject_no_route_without_killer_crossing`) | §4u |
| `[stop_distance_cap]` | 684 | §4c |
| `[bos_contract_gate]` | 421 | §4o |
| `[setup_floor_gate]` | 180 | §4j |
| `[tp1_authority]` | 159 | §4t |

### Reject table — every entry is a genuine strategy or risk filter

| Occurrences | Reason | Class |
|---:|---|---|
| 6,194 | `all_routes_cross_killer_obstacle` | **§4u killer policy** — 701 gate decisions; the reason string repeats across branch + plan_price + summary lines |
| 4,092 | `no_feasible_target` | legitimate |
| 2,470 | `suppressed_touched_continuation_not_retested` | config switch |
| 1,406 | `ote_outside_fvg` | strategy |
| 1,368 | `stop_distance_exceeds_max_frac_of_price` | config (`InpStopMaxFracOfPrice=0.05`) |
| 1,056 | `suppressed_stale_fvg_branch` | config switch |
| 1,002 | `synthetic_fallback_exceeds_max_target_distance` | legitimate |
| 982 | `execution_cost_r_too_high` | config (`InpStandardTradeCostRCeiling=0.24`) |
| 421 | `full_po3_family_without_confirmed_bos` | §4o gate |
| 180 / 155 | `pre_ai_floor_hard` / `_strict` | §4l floors |

`all_routes_cross_killer_obstacle` is now the largest deterministic blocker. All 701 fire at exactly
severity **8.50** = htf(3) + opposing(2) + imbalance(2) + {crossed|close}(1.5), against
`InpBlockerKillSeverity=8.0`. That means *every* HTF opposing imbalance that is crossed or close
is treated as a killer. Rejecting these is defensible and was the agreed §4u posture; **8.0 is the
knob** if it proves too strict. Not changed — a risk-posture decision.

### Checked and cleared — not defects

- **`setup_score=0.00` on `price_broke_fvg_low` branch rejects.** Not the §4j blindness class.
  `_WatchlistStillValidEx` is called at `TradeEngine.mqh:10072`, seven lines *before*
  `out_plan.setup_score = _SetupScore(out_plan)` at `:10079` — the score genuinely does not exist
  yet. `rr2=1.05` prints correctly on the same line, proving `out_plan` is published, and the
  `[watchlist_structural_break]` line above it carries live_price / fvg_lower / fvg_upper /
  direction. The diagnosis is complete.
- **`watchlist_precheck.reject.price_broke_fvg_low=0` while those lines fire.** Two distinct call
  sites: `:10072` (branch stage, counted in the branch reject table) and `:10243` (`staged`,
  counted by `m_total_watchlist_precheck_*`). Each counter is accurate for what it names.
- **tp2 recalculated past the liquidity level on watchlist refresh.** As bars formed the structural
  stop widened 0.00131 → 0.00155, and `target_recalc=deterministic_rr_from_entry` moved tp2
  1.15808 → 1.15913893 to hold rr2 = 4.4122 exactly. Within every declared bound:
  `max_target_distance` recomputes to `stop × InpMaxPlanRR2 5.0` = 0.00775 > 0.00683893 reward, and
  `[execution_adjustment_validation] result=pass` against `max_sl_drift_r=0.1000`.
  **Flagged, not a defect:** the price is now ~10.6 pips *past* `prev_week_high`, so a plan labelled
  `tp_model=liquidity_target` no longer exits at the liquidity pool. Whether refresh should re-clamp
  tp2 to the structural level (and let RR fall) instead of holding RR is a design question, not a
  contract violation. Same *naming* family as §4c/§4h/§4j but with no measured harm yet.

---

## 4w. Replay identity frozen (2026-09-06) — a whole cohort had become unaddressable

### The symptom

A CACHE_ONLY replay (2026.08.03–08.08, 25 symbols) reported

```
ai_cache_hits_total=89   ai_cache_misses_total=1276
ai_advisories_total=89   ai_final_allow_true_total=0
watchlist_added_total=0  orders_placed_total=0  trades_opened_total=0
```

while the replay cache on disk held **11 distinct APPROVED request ids**. None was reachable.

### Root cause — the key was recomputed on every lookup, and one of its inputs was not stable

`_TesterAiCacheSignature` is

```
AI_DECISION_SCHEMA_VERSION | AI_TARGET_ARBITRATION_SCHEMA_VERSION
| AI_PROMPT_CONTRACT_VERSION | DecisionHash() | _GroupSignature(plans) | <per-plan fields>
```

so component 4, `DecisionInputHash()`, is shared by every artifact in a cohort. It mixes the engine
inputs with a **content hash of the normalized-FVG policy file**, and `_CommonFileContentHash` opened
that file with `FILE_TXT` (UTF-16 by default in MQL5) and concatenated `FileReadString` results. Both
policy files are odd-length ASCII JSON, so under scan-time file I/O load the same unchanged 1,067-byte
file hashed to **49736647, 1990245157, 1989935653 and 1984261413 within one run**.

Measured from that run's own journal: all 122 `[ai_cache] hit=true` lines carried `671051197`; the last
hit was at simulated 2026.08.03 09:19:59; from 09:34:59 onward all 1,266 remaining misses carried
`1953583338`, a value present in **zero** artifacts on disk. Two call sites four milliseconds apart in
the same `OnInit` also disagreed (manifest 1019017657 vs journal 722861386).

### Fixes

| Where | Change |
|---|---|
| `AIGateBridge.mqh` | `_CommonFileContentHash` reads raw bytes (`FILE_BIN` + `FileReadArray`) through the new `_Fnv1aBytes`. Encoding can no longer change the fingerprint of unchanged bytes. |
| `AIGateBridge.mqh` | `FreezeReplayIdentity()` computes the identity **once**; `RuntimeInputHash()` / `DecisionInputHash()` became frozen accessors, with `_Compute*` kept separate. `CheckIdentityDrift()` **reports and counts** a later disagreement and never adopts it (`action=frozen_identity_retained`). |
| `TradeEngine.mqh` | `Init()` freezes the identity and emits `[decision_identity]`; in tester + cache mode it also probes the artifacts on disk and emits `[tester_ai_cache_cohort] … verdict=replayable | no_recorded_artifact_addressable_by_this_build_or_inputs`. |
| `TradeEngine.mqh` | A failed cache read now journals `[ai_cache] hit=false reason=no_cache_artifact key=… decision_input_hash=… cache_cohort=… cohort_match=…` (capped at 20). Before the fix a key with nothing behind it returned false silently, so "wrong cohort" and "never recorded" looked identical. |
| `TradeEngine.mqh` | `[final_summary]` gained `ai_cache_miss_no_artifact_total`, `decision_input_hash`, `decision_identity_frozen`, `decision_identity_drift_events`, `cache_cohort`, `cache_cohort_match`. |
| `python/tester_cache_rekey.py` | New — and **disarmed the same day, see §4y**. It was written to re-key component 4 of an existing cohort instead of paying for provider calls to reproduce decisions already held. The premise was false. |

Tests: `python/tests/test_replay_identity_freeze.py` (12), each falsified against the pre-fix sources.

### Confirmed in the user's own replay (19:46–20:54, agent 3000, 812.9 MB log)

```
ai_cache_hits_total=1263  ai_cache_misses_total=102  ai_cache_miss_no_artifact_total=102
decision_input_hash=671051197  decision_identity_frozen=true  decision_identity_drift_events=6
cache_cohort=671051197x181,1098270737x19  cache_cohort_match=true
ai_final_allow_true_total=9  watchlist_added_total=9  orders_placed_total=5  trades_opened_total=2
```

The cohort stayed addressable for the whole run. Note `drift_events=6`: the underlying instability was
still happening in that build and was **contained by the freeze, not eliminated** — the byte-exact
reader was written at 19:07 but the `.ex5` was compiled at 18:47, so the run did not contain it.

---

## 4x. The 2026-09-06 replay audited (2026-09-06 22:0x) — 4 of 9 approvals died on same-instant self-contradictions

The user asked directly whether anything was still wrong. It was.

### What was healthy

Both trades that opened were, in the end, fully verified — correcting an earlier reading of mine that
called them quarantined:

```
state=POSITION_FILLED_IDENTITY_VERIFIED attribution_verified=true
quarantine=false final_execution_success=true reason=entry_fill_exact_identity_verified
```

The funnel is fully accounted for: 9 approvals → 9 armed → 5 orders (2 market + 3 buy limits) →
2 fills. The 3 limits were never touched by price; that is a market outcome, not a defect.

### What was not — four approvals lost with zero elapsed simulated time

| symbol | check that passed | contradicting check, same simulated instant |
|---|---|---|
| `#Germany40` | `semantic_plan_match=true` | 85 ms later `immutable_fields_changed=obstacle_kind`; `crossed_session_high` → `crossed_opposing_imbalance` |
| `GBPCHF` | `semantic_plan_match=true` | 206 ms later, same field; `crossed_htf_opposing_imbalance` → `crossed_opposing_imbalance` — the *same* obstacle at two levels of detail |
| `EURUSD` | `execution_adjustment_validation=true max_target_distance=0.00848458` | 3 ms later `ai_chosen_target_exceeds_max_distance` |
| `AUDUSD` | 12 bars of `candle_confirm` | `tp1_reward=0.00171 ≥ min_tp1_reward=0.00151` **and** `geometry_leg_reward=0.00136 < geometry_floor=0.00151` on one log line |

Plus: both filled trades quarantined on `position_open_time_mismatch` the instant `OrderSend` returned
and verified 163 ms / 255 ms later, leaving a quarantine artifact and a
`broker_accepted_identity_quarantined` lineage entry for two sound executions.

### The shared root causes

1. **A derived label held immutable while every primitive it derives from is authorized to move.**
   `obstacle_kind` is re-derived by the live landscape scan; `obstacle_price`, `entry` and `obstacle_tf`
   are all classified as authorized changes, and `obstacle_tf` is itself derived from the kind string —
   so one and the same timeframe was immutable and authorized at once.
2. **A contract that mandates a recomputation and then rejects its result.** With
   `target_recalculation=deterministic_rr_from_entry` and the stop held, an authorized entry drift
   multiplies the reward while the ADR/ATR-derived max-distance cap does not move. EURUSD drifted
   0.08R of a permitted 0.40R and the reward went 0.00822 → 0.00889 against a 0.00848458 cap.
3. **An identity resolved before the terminal has finished registering it,** with "not yet knowable"
   recorded as "known to be wrong".

### Changes

| File | Change |
|---|---|
| `Types.mqh` | `TradePlan` += `obstacle_severity`; assessed block += `assessed_obstacle_severity`, `live_obstacle_kind`, `live_obstacle_price`, `live_obstacle_severity`. |
| `TradeEngine.mqh` | `_PublishObstacleEvidence` publishes the numeric severity and, on a **locked** plan, records the live observation instead of overwriting the approved obstacle — the same ownership the plan already had over its target and its first leg. |
| `TradeEngine.mqh` | New `_ObstacleKindWithoutTf` strips the `htf_`/`ltf_` qualifier for identity; `_EffectiveObstacleSeverity` scores a missing stored value from the kind alone (a lower bound, so it can only be stricter); `_ObstacleIdentityPreserved` accepts a changed label **only when it is no more severe**. New `[obstacle_identity_gate]` prints both sides. |
| `TradeEngine.mqh` | Both comparison sites judge the live observation via `_LiveObstacle*ForComparison`, so freezing the field does not make the check vacuous. `obstacle_price` is compared only while it is the same obstacle. |
| `TradeEngine.mqh` | `_ApplyAssessedTargetUnderContract` caps a deterministic-RR reward to `_MaxPlanTargetDistance` (through the one canonical `RewardWithinMaxDistance`) and rejects only when the capped RR falls under `c.min_resulting_rr`. New `[deterministic_rr_target_cap]`. |
| `TradeEngine.mqh` | New `_ExecutionIdentityFailureIsSettlementPending`; the post-`OrderSend` site defers such failures as `BROKER_ACCEPTED_IDENTITY_PENDING` / `attribution_status=PENDING_SETTLEMENT` with learning, optimization and suppression all false, and writes **no** quarantine artifact. The `OnTradeTransaction` entry-deal site stays unconditionally strict. |
| `TradeEngine.mqh` | `_LockAssessedPlan` freezes the severity, clears any stale live observation, and emits `[assessed_leg_below_floor]` when the frozen first leg is already under its own geometry floor — **diagnostic only**; which of the two values is wrong is not yet established. |
| `StateStore.mqh` | All five new fields persisted and restored; without them a reloaded plan reverts to permissive or vacuous. |

`PENDING_SETTLEMENT` is safe by construction on the Python side: `calibration_pipeline`,
`governance_contracts` and `runtime_governance` all whitelist eligible statuses, so an unknown value is
ineligible rather than trusted.

### Verification

- Compile (FxPro MetaEditor): **0 errors, 0 warnings**; `.ex5` 22:15:06 newer than every `.mqh`
  (newest `TradeEngine.mqh` 22:12:47) — the build-integrity violation that made the audited run use a
  stale binary is closed. Repo and deployed hashes match for all four files.
- Python suite: **796 passed, 9 skipped, 528 subtests** (was 776 / 9 / 528).
- `python/tests/test_execution_contract_coherence.py`: 20 new tests. **18 fail** against a
  reconstructed pre-fix tree; the 2 that do not are absence-guards and say so in their docstrings.
- Identity after the recompile, measured from a real run:
  `decision_input_hash=82474443` (was `671051197`), `normalized_fvg_policy_content=1441715152`
  (was the legacy decode value `49736647`), `drift_events=0` at Init. The cohort probe correctly
  reported `match=false verdict=no_recorded_artifact_addressable_by_this_build_or_inputs`.
- **Behaviour is NOT yet verified by a run.** The intended proof was a 5-day CACHE_ONLY replay; it
  produced 0 cache hits for the reason in §4y, so it exercised none of the four fixes. What stands
  is the compile, the falsified regression tests, and the reasoning above.

### Not resolved

- **`AUDUSD` first-leg floor.** The frozen leg (0.584R) is below the frozen floor (0.65R) at the moment
  it is locked. Fail-closed and therefore safe, but which of `assessed_tp1` and
  `assessed_stop_distance` is the wrong one is not established. Instrumented, not changed.
- **`USDCAD` 20:27:08.182** — a cache hit that produced `decision_state=APPROVE` with
  `candidate_hash_match=false assessment_group_ok=false threshold_reason=ai_quality_schema_incomplete`
  and `final_allow=false`. An infrastructure-class rejection of an approval; not yet traced.
- **`decision_identity_drift_events`** was 6 in the audited run. The byte-exact reader is now compiled
  in and Init reported 0, but a full run has not yet confirmed it stays 0.

---

## 4y. The cohort re-key is unsound — my own premise, falsified by the engine (2026-09-06 22:3x)

### What I claimed, and what actually happened

§4w introduced `tester_cache_rekey.py` on the premise that `DecisionInputHash()` appears in a cache
signature **once**, as component 4, so an identity correction could be absorbed by renaming instead of
re-recording. The re-key was applied cleanly: 1,346 artifacts `671051197` → `82474443`, all 32
approvals preserved, 0 conflicts, every original backed up. The 5-day replay that followed reported:

```
[tester_ai_cache_cohort] decision_input_hash=82474443
    recorded_cohorts=82474443x182,1098270737x18 dominant=82474443
    match=true verdict=replayable
[final_summary] ai_cache_hits_total=0 ai_cache_misses_total=1365
    ai_cache_miss_no_artifact_total=1365
```

**Addressable, and every single lookup missed.** The cohort probe added in §4w was right and the
re-key was wrong — exactly the pair of signals that diagnostic exists to separate.

### The refutation

`TradeEngine.mqh:4712` appends every candidate hash into the signature:

```mql5
for(int i=0; i<ArraySize(plans); i++){
   sig += "|" + plans[i].candidate_hash;
```

and `TradeEngine.mqh:2760`, inside `_CandidateHash`, mixes the decision identity into that hash:

```mql5
canonical += m_ai.DecisionHash() + "|" + ENGINE_INPUT_SCHEMA + "|" + AI_DECISION_SCHEMA_VERSION;
```

(`_ExecutionFingerprint` does the same at `:2784`.) So `DecisionInputHash()` is in the signature
**once directly and once inside every candidate hash**. Correcting it moves all of them together.

Measured, by diffing a signature the new build computed against the closest stored one:

```
[ 0] new=EFD9EAF5BB681E01   stored=47F47916193BD592   <-- DIFFERS
[ 1] full_po3_reversal                                    identical
[ 2] full_po3_reversal.nested_htf_ltf_fvg.virgin_fvg      identical
[ 3..14] taxonomy, enum, scope, entry_branch, prev_week_high,
         crossed_htf_opposing_imbalance, 4120.07, 4076.49675, 4036.53, 1, 10.8851, 7.0233
                                                          all identical
[15] new=F063E2A364128017   stored=D1A83930CE2CD8C4   <-- DIFFERS
```

The whole 875-character plan section and every printed candidate field are equal; only the hashes moved.

### What was done about it

- **The re-key was reverted.** All 1,346 originals restored from the backup by plain byte copy; the
  cache is back to 1,497 artifacts with cohort `671051197` holding 1,346 and all 32 approvals.
  (The first restore attempt crashed on a UTF-16 BOM because it parsed the files; the originals were
  never at risk — they were only ever read from the backup.)
- **`tester_cache_rekey.plan_rekey` now raises `RekeyUnsound`** naming `TradeEngine.mqh:2760` and the
  supported alternative. The CLI prints `refused:` and exits 2. The module is kept because its port of
  `_TesterAiCacheKey` is verified exact — it reproduces the file name of all 74 artifacts it never
  touched — and because it becomes usable the day `DecisionHash()` leaves `_CandidateHash`.
- `python/tests/test_tester_cache_rekey.py` (10 tests) pins both halves of the dependency in MQL
  source, so the day it is removed the test fails and says the module may be re-armed.

### Why recomputing the hashes was rejected rather than attempted

Every moved hash is a deterministic function of data still on disk, so re-deriving them is technically
possible. It is also exactly what `python/CLAUDE.md` forbids — "no hash recomputation from a different
representation", "never silently reinterpret incompatible authoritative schemas". It would mean
rewriting `candidate_hash`, `request_execution_fingerprint`, `assessed_execution_fingerprint`,
`ordered_candidate_identities`, every `candidate_assessments[].candidate_hash` and
`response_binding_hash` — i.e. rewriting the identity a provider response was validated against — and a
subtle error there yields bindings that are self-consistent and wrong. The payoff would have been
saving provider spend on a test cohort.

**A cohort recorded under a superseded decision identity is not re-keyable.** The supported path is the
one already prescribed: `TESTER_AI_RECORD_ONLY` → process the cohort → export → `TESTER_AI_CACHE_ONLY`.

### Consequence for §4x

The four execution-contract fixes are backed by a clean compile and 20 regression tests falsified
against a reconstructed pre-fix tree, but **not by an observed run**. Behavioural proof needs a fresh
recorded cohort, which costs provider calls — a decision for the user, not something to assume.

---

## 4z. Replay 00:06–00:19 (2026-09-07) — zero trades, and it is not a defect

The user re-ran the 5-day CACHE_ONLY replay and reported no trades. **No approval was lost and no
new bug exists.** The run is the predicted consequence of §4y, measured:

```
[tester_ai_cache_cohort] decision_input_hash=82474443
    recorded_cohorts=671051197x181,1098270737x19 dominant=671051197
    match=false verdict=no_recorded_artifact_addressable_by_this_build_or_inputs
[final_summary] scans_total=11375 plans_valid_total=136101
[final_summary] ai_cache_hits_total=0 ai_cache_misses_total=1365 ai_cache_miss_no_artifact_total=1365
[final_summary] ai_advisories_total=0 ai_final_allow_true_total=0 watchlist_added_total=0
[final_summary] decision_input_hash=82474443 decision_identity_frozen=true
    decision_identity_drift_events=0 cache_cohort_match=false
```

Every one of the 1,365 lookups missed with `cache_cohort_decision_hash=671051197` against
`decision_input_hash=82474443`. In CACHE_ONLY a miss is a hard reject, so the funnel never reached a
provider decision: 136,101 valid plans, zero advisories, zero approvals, zero orders.

### The approvals are physically intact

Counted directly in `<bus>\logs\tester_ai_cache` (note: the cache lives under `logs\`, not at the bus
root — `_TesterAiCacheDir()` is `m_bus.LogDir() + "\tester_ai_cache"`):

```
671051197   1346      <- holds all 32 approvals
1098270737   148
570642039      2
1211926014     1
total 1497 artifacts, 32 with python_final_allow=true, 0 unparseable
```

### Why the identity moved — a correct fix, not a regression

`normalized_fvg_policy.v2.json` and `invalidation_policy.v1.json` were both last written
**2026-07-17**, long before the cohort was recorded. The files did not change; the §4w byte-exact
reader (`FILE_BIN` + `_Fnv1aBytes`) corrected how they are fingerprinted, moving
`normalized_fvg_policy_content` `49736647 → 1441715152` and therefore `DecisionInputHash`
`671051197 → 82474443`. Reverting it to recover the cohort would reinstate the instability that made
the cohort unaddressable *mid-run* in the first place.

`decision_identity_drift_events=0` across a full 11,375-scan run — the §4w freeze plus the byte-exact
reader are now both confirmed live (the audited run had 6).

### Fix applied — fail fast instead of burning the run

A CACHE_ONLY replay whose cohort is unaddressable cannot reach one decision, yet it ran 13 minutes
and wrote 846 MB to report zero approvals — which reads exactly like a strategy failure and is not
one. Two changes in `TradeEngine.mqh`:

| Where | Change |
|---|---|
| `_ProbeTesterCacheCohort` | The match is now a **census**, not a sample. The old `if(sampled >= 200) break;` settled an abort-gating fact on the first 200 files, so a cohort of 5 inside 1,497 could be invisible. The histogram stays capped at 200; the loop now walks past the cap **only while the match is still unknown** — i.e. only in the run that is about to be rejected — and publishes `artifacts_total` / `artifacts_sampled`. |
| `Init` | `!cohort_match && _EffectiveTesterAiMode() == TESTER_AI_CACHE_ONLY` → `[startup_reject] reason=tester_cache_cohort_unaddressable`, printing both identities and the recovery path, then `return false`. Scoped to CACHE_ONLY: RECORD_ONLY legitimately starts against a foreign or empty cohort, and blocking it would break the only supported recovery. |

Tests: `python/tests/test_replay_cohort_startup_gate.py` (11). **All 11 falsified** against the
pre-fix deployed source before syncing. Suite **817 passed, 9 skipped, 528 subtests** (was 806).
Compile **0 errors, 0 warnings** (`metaeditor.log` 2026.09.07 00:34:59); `.ex5` 00:34:59 newer than
newest `.mqh` 00:29:14; repo and deployed differ on 0 files.

**Not verified by a run.** The startup gate is backed by the compile and the 11 falsified tests only;
the user's terminal (pid 17404, started 00:05:56) was still open on their completed backtest and was
deliberately not closed to run a verification pass.

### The decision that is still the user's

The cohort is not re-keyable (§4y) and not recoverable by reverting a correct fix. The only sound
path is `TESTER_AI_RECORD_ONLY` → run the Python gate → export → `TESTER_AI_CACHE_ONLY`, which
**costs provider calls**. Nothing in the system is broken; it simply has no recorded decisions
addressable by this build.

---

## 5. Working notes

- Read MT5 logs with `Get-Content <path> -Encoding Unicode`; they are UTF-16 and large.
- The `[setup_funnel]` and `[final_summary]` journal lines are the fastest way to see
  where a scan died — use them before reading anything else.
- Verify `.ex5` mtime against every `.mqh` mtime before trusting a run.
- `ai_gate.log` tags worth grepping: `hard_pre_gate`, `evidence_reference_validation`,
  `ai_abstain`, `provider_call_completed`, `identity_validation`,
  `authoritative_envelope_validation`, `response_written`.
- Do not treat `repeatability_status=UNAVAILABLE` as fatal by itself —
  `repeatability_required_live=False` in the observed responses.
