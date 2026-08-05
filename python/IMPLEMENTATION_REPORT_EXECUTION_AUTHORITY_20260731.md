# Execution authority repair — twelfth zero-trade attempt

**Verdict: code complete and unit-verified; Strategy Tester acceptance run BLOCKED by an
environment fault that reproduces with an empty EA containing none of these changes.**

---

## 1. Root-cause map

The eleventh run's dominant failure (`90 execution fingerprint mismatches`,
`0 OrderSend/test-adapter calls`) had **four** distinct causes, not one.

| # | Root cause | First incorrect state | Evidence |
|---|---|---|---|
| RC-A | The live rebuild **re-derived the liquidity target** instead of carrying the approved one forward | `_SelectObstacleAwareTarget` recomputes `liquidity_target_preserved` from the live entry | `[target_candidates] liquidity_tp=4220.90` at rebuild vs approved `tp2=4070.51` |
| RC-B | The target sanitizer **silently substituted a different target model** | `_ApplyFeasibleTargetSanitizer` fallback chain | `[target_sanitizer] from=range_mid_opposite_side to=synthetic_rr_fallback reason=feasible_priority_target` |
| RC-C | An over-cap AI target was **"kept for validation"** rather than classified and stopped | `_BuildPlanPrices` cap branch | `[target_validation] ai_chosen_target_exceeds_cap kept_for_validation reward=154.02 cap=18.55` |
| RC-D | The fingerprint check then compared **a materially different trade** to the assessment | `_ExecutionFingerprintWithinTolerance` | `[execution_fingerprint] match=false changed_components=target_source,target_model,obstacle_kind,tp1,tp2` |

Two further causes, independent of the above:

| # | Root cause | Evidence |
|---|---|---|
| RC-E | `ModelCriticDecision.verdict` and `ModelCriticObjection.code` were **free strings** while `decision_pipeline` enforced closed sets | `ValueError:critic_verdict_invalid` ×46; `critic_objection_code_unknown` |
| RC-F | A schema/vocabulary disagreement was reported as **`PROVIDER_TRANSPORT_ERROR`** | `ai_provider.py` terminal raise |

### Why the symptom looked like an integrity failure

The integrity check was working correctly. It was being handed a different trade.
`assessed_execution_fingerprint` described the trade the AI approved; the live plan
described a trade the *rebuild* invented. Widening the tolerance would have turned a
real protection into a rubber stamp — which is why the fix restores the approved plan
rather than relaxing the comparison.

### Why the root cause was deeper than the symptom

Rebaselining the fingerprint at `[target_apply]` would have made the error string
disappear while leaving the engine free to execute a target the AI never chose. The
actual defect is an **authority** defect: nothing in the model distinguished "the AI's
approved plan" from "a plan the engine happened to rebuild".

---

## 2. Exact execution fields changing before the fix

Captured at GOLD 2026.06.29 02:14:30 (`Agent-127.0.0.1-3001`):

| Field | Assessed (approved) | Live rebuild | Authorized? |
|---|---|---|---|
| `entry` | 4066.75 | 4066.88 | yes — 0.13 on 3.58 R, inside drift tolerance |
| `sl` | 4063.17 | 4063.17 | unchanged |
| `tp1` | 4069.38 | 4070.03 | **no** |
| `tp2` | 4070.51 | 4070.78 | **no** |
| `target_source` | `ai_selected_liquidity_target` | `ai_selected_synthetic_rr_fallback` | **no** |
| `target_model` | `range_mid_opposite_side` | `synthetic_rr_fallback` | **no** |
| `obstacle_kind` | `NONE` | `crossed_opposing_imbalance` | **no** |
| `obstacle_tf`, `obstacle_price` | unset | `entry_tf`, set | **no** (later attempts) |

**The decisive number:** the approved target 4070.51 at the live entry 4066.88 is a
reward of **3.63** against a live cap of **18.55** — comfortably feasible. The trade was
lost only because the rebuild resolved the liquidity level to 4220.90 (reward 154.02).

---

## 3. New assessed / execution authority design

Three explicitly separated objects:

**`AssessedTradePlan`** — frozen once Python approves *and* the approved target has been
applied. Owns request/candidate identity, direction, taxonomy, entry and stop models,
assessed SL, selected target identity/source/model/price, target-arbitration decision,
obstacle identity and class, assessed entry and TP1/TP2, assessment fingerprint.

**`ExecutionAdjustmentContract`** — the deterministic permission. Entry adjustment and
bound (R and ticks), whether SL/TP1/TP2 may change, the target recalculation rule,
identity/source/model preservation requirements, obstacle revalidation, minimum
resulting RR, maximum target distance, maximum cost deterioration, expiry.

**`LiveExecutionPlan`** — derived only through the contract: live bid/ask, live entry,
derived TPs *only when permitted*, current spread/cost, broker-normalized prices.

### Authority rules as implemented

1. A fixed liquidity target keeps the **same identity and the same price**
   (`TARGET_RECALC_PRESERVE_FIXED_PRICE`).
2. A synthetic fixed-RR target is recomputed from the changed entry by the approved
   R multiple, keeping model and source (`TARGET_RECALC_DETERMINISTIC_RR`).
3/4. `next_liquidity_session_range` can no longer become `synthetic_rr_fallback`, and
   `ai_selected_liquidity_target` can no longer become a synthetic source — the
   sanitizer refuses to substitute while the plan is locked.
5. A changed obstacle class, target identity/source/model, or stop model is a semantic
   change → `SEMANTIC_PLAN_CHANGED` → terminal invalidate or requeue for AI.
6/7. The fingerprint tolerance was **not widened** and the check was **not removed**.
8. Immutable semantic fields are compared exactly.
9. Permitted mutable fields are validated against the contract's bounds.
10. A separate `final_execution_fingerprint` is generated for the order.

### New log lines

`[assessed_plan_identity]`, `[execution_adjustment_contract]`, `[live_execution_plan]`,
`[execution_adjustment_validation]`, `[semantic_plan_match]`, `[execution_fingerprint]`
— reporting `immutable_fields_changed`, `authorized_fields_changed`,
`unauthorized_fields_changed`, `adjustment_bounds`, `result`, plus `failure_class` and
`action`.

---

## 4/5/6. Files and functions changed

### MQL5

| File | Change |
|---|---|
| **`ExecutionAdjustmentContract.mqh`** (new, 280 lines) | `EXECUTION_ADJUSTMENT_CONTRACT_VERSION`; `TARGET_RECALC_*`; eight `EXEC_FAIL_*` classes; `EXEC_ACTION_*`; `ExecFailureIsTransient/IsTerminal/Action`; `struct ExecutionAdjustmentContract`, `SemanticPlanMatchResult`, `TargetFeasibilityResult`; `PriceDistanceToTicks`, `TicksWithinCap` |
| `Types.mqh` | `TradePlan` +17 fields: assessed-plan lock, semantic match results, retry-suppression state |
| `TradeEngine.mqh` | `_EvaluateTargetFeasibility` (**the** canonical implementation), `_LogTargetFeasibilityResult`, `_PlanTickSize`, `_TargetModelIsSyntheticRr`, `_LockAssessedPlan`, `_BuildExecutionAdjustmentContract`, `_ContractMaxEntryDrift`, `_ApplyAssessedTargetUnderContract`, `_EvaluateSemanticPlanMatch`, `_LogSemanticPlanMatch`, `_SemanticExecutionCheck`, `_ClassifyExecutionFailure`, `_ExecutionStateFingerprint`, `_SuppressExecutionRetry`, `_RecordExecutionFailure`, `_LogStructuralBreak`; modified `_PrepareDecisionIdentity`, `_BuildPlanPrices`, `_ApplyFeasibleTargetSanitizer`, `_TargetPriceFeasibleForTp`, `_ExecutionFingerprintWithinTolerance`, `_PlaceMarket`, `MaintainWatchlist`, `_WatchlistStillValidEx` |
| `TestOrderAdapter.mqh` | `_EmitVerdictOnFirstAttempt` — emits `[harness_verdict]` at the broker boundary, so an interrupted run still proves what it reached |

### Python

| File | Change |
|---|---|
| **`execution_adjustment_contract.py`** (new) | Python mirror; `evaluate_semantic_plan_match`, `evaluate_target_feasibility`, `derive_live_target`, `contract_for`, `mql_defines` (parity by **reading the MQL header**, not restating it) |
| **`run_manifest.py`** (new) | `test_run_id` minting, manifest publish, `absorb_mql_identity`, `stamp`, `assert_single_run` |
| `structured_models.py` | `QUALITATIVE_VETO_CODES`, `CriticVerdict`, `AdjudicatorVerdict`, `AnalystDecisionState`, `HistoricalEvidenceState`, `OptionalQualitativeVetoCode`, `objection_code_vocabulary_prompt`; every role schema bound to them |
| `decision_integrity.py` | `LLM_VETO_CODES` now derived from the canonical tuple; historical-evidence set imported |
| `decision_pipeline.py` | Validators use the canonical tuples; critic prompt renders the enforced vocabulary |
| `ai_provider.py` | `_terminal_failure_category` — schema-only failures report `STRUCTURED_RESPONSE_INVALID`, not transport |
| `ai_gate.py` | Run manifest minted/published/stamped; `_log_repeatability_state`; `[repeatability_gate]` |
| `repeatability_state.py` | `LEGACY_ARTIFACT_SCHEMAS` — a recognised earlier schema reads `UNQUALIFIED` (cold start), unknown stays `INCOMPATIBLE` |
| `tools/harness_local_provider.py` | Role-specific payloads; `critic_verdict`/`adjudicator_verdict` overrides; `resolved/unresolved_objection_codes`; enum-aware builder; two new malformations |

### Target-feasibility unification

`_EvaluateTargetFeasibility` is the single implementation. Every stage — initial plan
construction, AI candidate construction, the sanitizer, the watchlist precheck, the live
rebuild, and final validation — reports through one `TargetFeasibilityResult`.
The max-distance decision is made in **whole ticks** (`PriceDistanceToTicks`), so a target
capped exactly to the maximum passes deterministically. Diagnostics print full precision
(8 dp), so rounded equality can no longer hide a rejection.

### Retry suppression

`_ClassifyExecutionFailure` assigns one of eight classes; a class already decided by the
detecting code wins over string matching. `_ExecutionStateFingerprint` hashes the inputs
that can change an outcome (bid, ask, sl, tp2, PO3 state, open positions, failure class).
`_SuppressExecutionRetry` skips while that fingerprint is unchanged and the class is not
transient; transient classes get bounded backoff. Terminal classes remove the watchlist
item and log `action=terminal_invalidate_or_requeue_ai`. Order-construction attempts are
counted separately from pre-order execution checks via `m_last_order_construction_attempted`.

---

## 7/8. Harness and enum fixes

Critic: `PASS` → `blocking_objections=[]`; `BLOCK` → one valid objection with real
evidence IDs; `ABSTAIN` → no blocking objection. Adjudicator: `UPHOLD_APPROVE` resolves
*every* critic blocking code; `UPHOLD_BLOCK` leaves them unresolved. The harness imports
the vocabulary from `structured_models` rather than restating it.

Verified at the provider boundary:

```
ModelCriticObjection.code -> {"enum": ["ai_veto_data_integrity_failure", ... 7 codes]}
ModelCriticDecision.verdict -> {"enum": ["PASS","BLOCK","ABSTAIN"]}
Adjudicator.verdict -> {"enum": ["UPHOLD_APPROVE","UPHOLD_BLOCK","ABSTAIN"]}
ok: unknown objection code rejected by schema
ok: analyst verdict rejected as critic verdict
```

---

## 9. Python test totals

```
478 passed, 327 subtests passed in 23.56s        (baseline 403 / 270)
```

New/extended suites: `test_execution_adjustment_contract.py` (41 + 16 subtests),
`test_run_manifest.py` (16), `test_harness_provider_integration.py` (55 + 38),
`test_repeatability_state.py` (28 + 25), `test_order_adapter_contract.py` (20 + 43).

Regression coverage against the required list: 1 assessed semantic plan immutability ✓,
2 authorized live-entry adjustment ✓, 3 unauthorized target-source ✓, 4 unauthorized
target-model ✓, 5 unauthorized obstacle ✓, 6 fixed liquidity target preservation ✓,
7 synthetic-RR deterministic recalculation ✓, 8 canonical target-distance boundary ✓
(below/at/one-tick-below/one-tick-above cap, buy and sell), 9 duplicate execution retry
suppression ✓ (91 ticks → 1 attempt, 90 suppressed), 10 role-specific harness outputs ✓,
11 critic verdict enum ✓, 12 critic objection-code enum ✓, 13 remote critic schema
failure/repair ✓, 14 positive path through the real order adapter — **not run** (§19),
15 negative MQL fixtures — **not run** (§19), 16 record/cache replay — **not run** (§19).

---

## 10. MQL compile results

```
PO3_AIGate_ScannerEA.mq5            Result: 0 errors, 0 warnings   (1,975,638 B)
PO3_AIGate_PositivePath_Harness.mq5 Result: 0 errors, 0 warnings   (1,976,014 B)
```

---

## 11. Correlated test-run manifest

Live from the running gate:

```
[test_run_manifest] test_run_id=run_9efc6a58e614abd6 python_session_id=python_26900_1785502702
  mql_session_id=awaiting_mql provider_mode=LOCAL_OPENAI_COMPATIBLE
  provider_id=local_openai_compatible provider_pid=26900 model=harness-deterministic-v1
  ea_name=awaiting_mql ex5_hash=unknown runtime_input_hash=unknown
  contract_manifest_hash=1024838206 test_period=unset..unset tester_mode=unset
```

`absorb_mql_identity` completes the MQL half from the first request and re-logs once;
`assert_single_run` raises `evidence_spans_multiple_runs` when records carry more than
one id — the exact contamination that produced the eleventh report.

---

## 12–16. Positive path, `mql_final_allow`, `order_attempted`, harness verdict, negative fixtures

**Not produced. The Strategy Tester could not be launched.** See §19.

---

## 17/18. Record-only and cache-only replay

Not run — same blocker.

---

## 19. Newly exposed blocker: the Strategy Tester will not launch any newly compiled EA

Every `/config`-driven tester launch aborts in 7–20 s with exit code `-1000012355`,
writing no tester report, no agent log, and no journal entry past broker authorization.

Bisection (each line is a separate measured run):

| EA under test | Contains my changes? | Result |
|---|---|---|
| `PO3_AIGate_PositivePath_Harness.ex5` | yes | abort, `-1000012355` |
| `PO3_AIGate_ScannerEA.ex5` | yes | abort, `-1000012355` |
| `PO3_LoadProbe.ex5` (include graph only, no logic) | yes | abort, `-1000012355` |
| `PO3_HdrProbe.ex5` (new header only) | yes | abort, `-1000012355` |
| **`PO3_NilProbe.ex5` — `OnInit`/`OnTick`/`OnTester`, no includes, no inputs** | **no** | **abort, `-1000012355`** |
| Same nil probe relocated to `Experts\Advisors\` | no | abort, `-1000012355` |
| `Advisors\ExpertMACD.ex5`, **recompiled by the same MetaEditor 6061** | no | **runs (100 s+)** |

**Correction to an earlier conclusion in this session:** I initially reported that my
rebuilt EA was aborting the terminal. That was wrong. An EA containing none of these
changes aborts identically, so the fault is not attributable to the code.

Ruled out along the way: overlapping terminal instances (killed to zero and verified
before each run), `[TesterInputs]` name mismatches (all 18 verified present as declared
inputs), the `Report=` directive, date ranges, the Expert folder, dirty-shutdown state,
and MetaEditor/terminal version mismatch (both `5.0.0.6061`).

Not ruled out — needs desktop access I do not have from the CLI: the terminal's
tester-agent registration. The last successful runs (`00:10`–`01:29` today) used agents
`Agent-127.0.0.1-3000/3001`, which have received no work since `01:33:58`, and MT5 gives
no diagnostic for this from the command line.

**Suggested operator step:** open MT5 interactively, run any test once from the Strategy
Tester tab (this re-registers the local agents), then re-run the harness. `View → Strategy
Tester → Agents` will also show whether the local agents are disabled.

---

## 20. Readiness verdict

**Not demo-forward ready. The infrastructure defect that caused eleven zero-trade runs is
repaired and unit-verified, but the repair is unproven end to end.**

What is proven:
- The authority model, canonical feasibility, retry suppression, enum unification, run
  manifest, and repeatability state machine are implemented, and 478 Python tests pass
  including the exact incident replayed field-for-field.
- Both EAs compile 0 errors / 0 warnings.
- MQL↔Python contract parity is asserted by parsing the MQL header directly.
- The deterministic provider passes through the real `run_qualitative_consensus` on all
  five branches with no `critic_verdict_invalid` and no `critic_objection_code_unknown`.

What is not proven:
- `semantic_plan_match=true`, `execution_adjustment_validation=true`, `mql_final_allow=true`,
  `order_attempted=true`, `[harness_verdict] positive_path_reached=true`.
- The 15 negative MQL fixtures.
- Record-only → cache-only replay.

Nothing here forces a trade, weakens a fail-closed control, widens the fingerprint
tolerance, or disables `require_repeatability_live` — which currently reports
`state=UNQUALIFIED trading_authority=false action=collect_offline_qualification_samples`
and correctly blocks live trading.

---

## Required user deployment steps

1. Open MT5 interactively and run any Strategy Tester test once, to re-register the local
   tester agents.
2. Start the deterministic provider: `python tools/harness_local_provider.py --port 8099 --decision approve`
3. Start the gate: `set PO3_DOTENV_FILE=.env.harness` then
   `python ai_gate.py run --common-files-dir "<terminal>\MQL5\Files\PO3_AI_BUS" --workers 1`
4. Run the harness config: `terminal64.exe /config:...\harness_positive.ini`
5. Refresh the stale `MT5_PO3_Codex Experts\PO3_AIGate_ScannerEA.ex5` in the repo (1,812,428 B)
   or stop tracking it — it is not what runs.

## Exact expected success logs

```
[test_run_manifest] test_run_id=run_... ea_name=PO3_AIGate_PositivePath_Harness
[provider_call_completed] quality_tier=FULL_STRUCTURED
[ai_schema_validation] valid=true
[response_written] test_run_id=run_... quality_tier=FULL_STRUCTURED
[decision_authority] model_raw_allow=true python_final_allow=true
[assessed_plan_identity] target_identity=... target_source=ai_selected_liquidity_target
[watchlist_precheck] pass=true action=added
GOLD added to watchlist
GOLD confirmation complete, attempting execution
[execution_adjustment_contract] target_recalc=preserve_fixed_price
[live_execution_plan] live_tp2=<equal to assessed_tp2>
[semantic_plan_match] semantic_plan_match=true immutable_fields_changed=none
[execution_adjustment_validation] execution_adjustment_validation=true result=pass
[execution_fingerprint] match=true
[decision_authority] ... authority_state=MQL_PRE_SUBMISSION_ELIGIBLE
[test_order_adapter] ... op=BUY/SELL
[harness_funnel] phase=first_order_attempt order_attempted=true
[harness_verdict] positive_path_reached=true
```

A semantic change must instead show:

```
[semantic_plan_match] semantic_plan_match=false immutable_fields_changed=target_model,...
[execution_failure_class] class=SEMANTIC_PLAN_CHANGED action=terminal_invalidate_or_requeue_ai
[execution_rebuild] duplicate_execution_attempts=0
```
