# Architecture, Cache, and Shadow Outcome Implementation Report

Generated UTC: 2026-07-17T04:16:07Z

Active Python root: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python`

Active MT5 root: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5`

Git commit visible at audit time: `d504ea196fdf80f263bdbcbeaa114ee428d7c308`

Git worktree state: `DIRTY`. This is persisted as a cohort field and is not hidden or treated as clean deployment evidence.

## 1. Executive Summary

This operation audited the active PO3/FVG stack and deployed one coherent provider-grade contract for points 41-54 while preserving the existing protections from points 1-40.

The active code now has:

- one `LIVE_FORWARD` behavior contract for demo, contest, and real destinations;
- semantic cache invalidation in addition to TTL;
- strict, source-aware multiplier semantics;
- startup manifests for all policy classes;
- a machine-readable control-effectiveness manifest;
- three separately persisted decisions: model, Python, and MQL;
- strict JSON and response-binding validation;
- atomic, terminal-state file-bus lifecycle management;
- version-complete cohort metadata and fail-closed compatibility checks;
- explicit deterministic, statistical, LLM, portfolio, and management ownership;
- a complete unmanaged entry target suite with ambiguity and censoring handling;
- independent entry and management shadow artifacts;
- exact shadow-event joins and objective outcomes for rejected as well as approved candidates.

The active deployment was verified after copy, not inferred from a staged tree:

- 26 deployed source files were SHA-256 compared with the verified stage: 0 mismatches;
- the active control manifest was generated directly from the active Python and MT5 roots;
- Python compile passed;
- 117 Python unit tests passed;
- the complete v-next verifier passed;
- MetaEditor compiled the active EA/include tree with 0 errors and 0 warnings.

The operation did not manufacture runtime evidence. The current historical ledger remains unusable for learning: 619 records, 0 clean, 436 quarantined, 183 unattributed. The current shadow and completed-trade files do not exist, so hierarchical, entry, and management artifacts remain `shadow_unavailable`. Cohort analysis remains blocked. No controlled real demo market fill, pending fill, broker-cost capture, holiday/early-close capture, or netting virtual sub-position proof exists.

No `.set`, private `.env`, strategy threshold, RR floor, target-arbitration rule, family default, symbol universe, model name, or Flex setting was changed.

## 2. Exact Active Files Changed

### Python source

- `python\ai_gate.py`
- `python\architecture_contracts.py` (new)
- `python\calibration_pipeline.py`
- `python\decision_integrity.py`
- `python\expectancy_report.py`
- `python\governance_contracts.py`
- `python\runtime_governance.py`

### Python policy and generated control contract

- `python\config\normalized_fvg_policy.v2.json`
- `python\data\control_effectiveness_manifest.json`

### Python tools

- `python\tools\analyze_shadow_outcomes.py` (new)
- `python\tools\audit_cohort_compatibility.py` (new)
- `python\tools\audit_control_effectiveness.py` (new)
- `python\tools\build_shadow_models.py` (new)
- `python\tools\generate_deployment_manifest.py` (new)
- `python\tools\verify_v_next_controls.py`

### Python tests

- `python\tests\test_architecture_contracts.py` (new)
- `python\tests\test_control_effectiveness_manifest.py` (new)
- `python\tests\test_decision_integrity.py`
- `python\tests\test_runtime_governance.py`

### Active MQL includes

- `MQL5\Include\MT5_PO3_Codex\AIGateBridge.mqh`
- `MQL5\Include\MT5_PO3_Codex\Config.mqh`
- `MQL5\Include\MT5_PO3_Codex\FileBus.mqh`
- `MQL5\Include\MT5_PO3_Codex\FVG.mqh`
- `MQL5\Include\MT5_PO3_Codex\JsonLite.mqh`
- `MQL5\Include\MT5_PO3_Codex\StateStore.mqh`
- `MQL5\Include\MT5_PO3_Codex\TradeEngine.mqh`
- `MQL5\Include\MT5_PO3_Codex\Types.mqh`

### Report and compile evidence

- `python\IMPLEMENTATION_REPORT_ARCHITECTURE_CACHE_SHADOW_20260717_041607.md`
- `python\mql_compile_architecture_cache_shadow_20260717_041607.log`

No active `.mq5` content changed in this operation. The EA consumes the changed include contract.

## 3. Audit Table for Points 1-10

Status meanings:

- `VERIFIED_ACTIVE`: active source, deterministic tests, and active compile pass.
- `ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED`: code is active but the required real broker/runtime artifact is absent.
- `SHADOW_UNAVAILABLE`: implementation is active and non-authoritative because eligible data does not exist.

| Point | Requirement | Status | Active implementation | Evidence or remaining gap |
|---:|---|---|---|---|
| 1 | Separate rule, LLM quality, legacy blend, calibrated probability, expected net R, and self-confidence | VERIFIED_ACTIVE | `decision_integrity.py`, `ai_gate.py`, MQL response structures and logs | Calibrated probability and expected net R remain unavailable rather than fabricated |
| 2 | Remove synthetic confidence from authority | VERIFIED_ACTIVE | `legacy_agreement_confidence` is diagnostic only | Tests prove no allow, sizing, candidate-selection, or floor authority |
| 3 | Assess every candidate independently | VERIFIED_ACTIVE | Complete assessment array bound by candidate ID/hash | Cross-candidate attachment tests pass |
| 4 | Remove silent `rule_best` substitution | VERIFIED_ACTIVE | Invalid selected candidate rejects; alternatives require their own assessment | No fallback assessment inheritance remains |
| 5 | Assessed and final execution fingerprints | VERIFIED_ACTIVE | Canonical rounded fingerprints and material drift checks | Real broker drift capture still absent |
| 6 | Strict mandatory schema | VERIFIED_ACTIVE | Python and MQL reject missing, malformed, duplicate, non-finite, out-of-range fields | Legacy incomplete rows are non-trading |
| 7 | Remove minimal live-forward fallback | VERIFIED_ACTIVE | Only `FULL_STRUCTURED` and `CACHE_OF_FULL_STRUCTURED` can trade | Degraded and rule-only paths are non-trading |
| 8 | Explicit `APPROVE`, `REJECT`, `ABSTAIN` | VERIFIED_ACTIVE | `ABSTAIN` cannot watchlist or order | Market and pending regression tests pass |
| 9 | Preserve explicit zero multipliers | VERIFIED_ACTIVE | Missing, zero, reduced, one, negative, and above-max are distinct | Final MQL sizing gate fails closed on zero/invalid |
| 10 | Resolve exact broker position identity | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Exact order/deal/position resolution and quarantine | No controlled real demo market/pending fill evidence exists |

## 4. Audit Table for Points 11-25

| Point | Requirement | Status | Active implementation | Evidence or remaining gap |
|---:|---|---|---|---|
| 11 | Explicit account margin mode governance | VERIFIED_ACTIVE | Hedging and netting policies in `TradeEngine` and `decision_integrity.py` | Deterministic tests pass |
| 12 | Position ID as primary lifecycle identity | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | `DEAL_POSITION_ID`/position identifier persistence and recovery quarantine | No real fill capture proves the full lifecycle |
| 13 | Central ledger integrity gate | VERIFIED_ACTIVE | `governance_contracts.audit_trade_records`, repair/audit tools, completion gate | Runtime audit: 619 total, 0 clean |
| 14 | Separate provider, initial-risk R, and account views | VERIFIED_ACTIVE | Completion and analytics calculations remain distinct | Legacy rows cannot supply valid denominators |
| 15 | Complete deal accounting | VERIFIED_ACTIVE | Profit, commission, swap, partials, and volume reconciliation | Real legacy rows remain dirty |
| 16 | Equal-trade, risk-weighted, and normalized metrics | VERIFIED_ACTIVE | Ledger/report metrics use separate denominators | Eligible live set is empty |
| 17 | Strict setup taxonomy | VERIFIED_ACTIVE | Cross-language taxonomy resolvers | Precedence tests pass |
| 18 | Unknown taxonomy block | VERIFIED_ACTIVE | Unknown setups fail before AI/learning | No permissive unknown fallback |
| 19 | Governed suppression | VERIFIED_ACTIVE | Suppression requires compatible clean evidence | Current ledger prevents activation |
| 20 | Time-aware uncertainty | VERIFIED_ACTIVE | Block bootstrap and purged chronological partitions | Deterministic chronology tests pass |
| 21 | Experiment registry | VERIFIED_ACTIVE | Registry and management experiment tooling | No management policy is promotable |
| 22 | Feature double-count controls | VERIFIED_ACTIVE | Feature lineage and compact LLM payload | Leakage tests pass |
| 23 | Heuristic-quality rename | VERIFIED_ACTIVE | Canonical quality names; migration aliases diagnostic | No alias has empirical authority |
| 24 | Raw score and calibrated authority | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Raw score plus shadow calibration pipeline | No clean data means calibration is unavailable |
| 25 | Canonical decision-quality tier | VERIFIED_ACTIVE | Full-structured-only trade gate in Python/MQL/cache | Old cache schemas cannot trade |

## 5. Audit Table for Points 26-40

| Point | Requirement | Status | Active implementation | Evidence or remaining gap |
|---:|---|---|---|---|
| 26 | Fingerprints and repeatability authority | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Runtime fingerprints and shadow repeat evaluator | No runtime repeatability artifact; status is `UNAVAILABLE` |
| 27 | Hierarchical priors with shrinkage | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Existing multi-level prior builder plus new outcome hierarchy | No clean runtime prior artifact |
| 28 | Module-root path and startup fail-close | VERIFIED_ACTIVE | Module-relative paths and mandatory audit | Missing mandatory prior fails closed |
| 29 | Aggregate original-risk cap | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Original risk includes open and pending exposure | Preserved input may disable authority; no live proof |
| 30 | Stricter percentage/money cap | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Effective cap takes stricter active limit | No runtime startup capture supplied |
| 31 | Factor/cluster caps | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Fixed factor policy and scheduling gate | Preserved default may disable gate; no live proof |
| 32 | Broker-derived stressed costs | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Median/stressed/fallback cost model | No current real broker sample capture |
| 33 | Idempotent management state machine | VERIFIED_ACTIVE | Persistent state transitions and restart guards | Duplicate action tests pass |
| 34 | Management policy experiments | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Experiment registry and shadow authority | Zero clean exact-attribution outcomes prevents promotion |
| 35 | Separate opening/closing volume normalization | VERIFIED_ACTIVE | Independent normalizers and residual handling | Boundary tests pass |
| 36 | Asset-aware invalidation confirmation | VERIFIED_ACTIVE | Asset policy, wick/spread/persistence rules | Policy tests pass |
| 37 | Actual versus full-horizon counterfactual | VERIFIED_ACTIVE | Persistent pending counterfactual resolution | Same-bar ambiguity is excluded |
| 38 | Per-symbol broker sessions and close windows | ACTIVE_BUT_OPERATIONAL_PROOF_REQUIRED | Symbol session schedule and retry state | No real holiday/early-close capture |
| 39 | Shadow candidate outcomes | VERIFIED_ACTIVE_CODE_RUNTIME_DATA_UNAVAILABLE | Persistent candidate, decision, and outcome event lifecycle | Runtime `shadow_candidates.jsonl` does not yet exist |
| 40 | Normalized FVG OFF/SHADOW/ENFORCE | VERIFIED_ACTIVE | `max(ticks, spread, ATR, session noise)`, asset-class evidence gate | Default remains SHADOW; ENFORCE blocks without compatible clean policy |

Point 40 was rechecked in active `FVG.mqh`, `Config.mqh`, `Types.mqh`, and `StateStore.mqh`. The normalized minimum is:

```text
max(
  minimum_ticks,
  spread_multiple,
  ATR_fraction,
  session_noise_fraction
)
```

`OFF`, `SHADOW`, and `ENFORCE` remain distinct. `NORMALIZED_FVG_SHADOW` remains the default. Asset-class policy is supported. Symbol-level enforcement is not activated from small samples. ENFORCE requires schema compatibility, taxonomy compatibility, `ledger_integrity_status=CLEAN`, and the configured minimum asset-class sample. All components and pass states are persisted in candidate/shadow state.

## 6. Implementation Table for Points 41-54

| Point | Requirement | Status | Implementation | Proving test/result |
|---:|---|---|---|---|
| 41 | Global -> asset -> symbol outcome hierarchy | IMPLEMENTED_SHADOW_ONLY | `build_hierarchical_outcome_artifact` with logistic global model, Bayesian logit adjustments, shrinkage, uncertainty, and asset OOS calibration | `test_hierarchical_global_asset_symbol_shrinkage`; current artifact `shadow_unavailable` |
| 42 | Canonical LIVE_FORWARD | VERIFIED_ACTIVE | `workload_mode`, `live_forward_behavior_contract`, MQL `_WorkloadMode` and startup hash | `test_live_forward_unifies_demo_contest_and_real` |
| 43 | Semantic cache invalidation | VERIFIED_ACTIVE | `SemanticCacheState`, all drift/state/version comparisons, cache-row persistence | Every reason test plus TTL override test |
| 44 | Strict multiplier semantics | VERIFIED_ACTIVE | `MultiplierResolution`, chained resolver, strict Python/MQL/policy parsing and execution gate | `test_multiplier_semantics` and prior zero tests |
| 45 | Startup policy manifest | VERIFIED_ACTIVE_FAIL_CLOSED | Python and MQL manifests cover all policy classes; active authority needs clean reconciliation | Dirty-ledger manifest test; active ledger blocks authority |
| 46 | Dead controls | VERIFIED_ACTIVE | Source-effect audit, scoped active MT5 discovery, machine-readable manifest | 566 active, 2 diagnostic-only, 7 removed, 0 dead |
| 47 | Three explicit decisions | VERIFIED_ACTIVE | `model_raw_allow`, `python_final_allow`, `mql_final_allow` persisted separately | `test_three_decision_stages_remain_distinct` |
| 48 | Safe JSON contract | VERIFIED_ACTIVE | Strict Python parser; flattened critical MQL fields; duplicate/non-finite/Unicode/trailing/hash/binding checks | strict JSON and response binding tests |
| 49 | File-bus lifecycle | VERIFIED_ACTIVE_CODE | Atomic writes, ownership/session binding, processing and all terminal archives, startup recovery | lifecycle/atomic archive test and verifier |
| 50 | Homogeneous cohorts | VERIFIED_ACTIVE_FAIL_CLOSED | Complete metadata, cohort ID, compatibility partition/block | Mixed blocks; homogeneous passes; current audit blocked |
| 51 | Layered authority | VERIFIED_ACTIVE | Field owner manifest and invalid owner rejection | LLM probability/expected-R ownership test |
| 52 | Correct prediction targets | VERIFIED_ACTIVE_SHADOW | Complete unmanaged R/path/time target suite, explicit horizon, ambiguity and censoring | target-suite and same-bar tests |
| 53 | Separate entry/management models | IMPLEMENTED_SHADOW_ONLY | Independent feature sets, targets, windows, calibration and artifact versions | independent-artifact tests; both runtime artifacts unavailable |
| 54 | Complete rejected-candidate outcomes | VERIFIED_ACTIVE_CODE_RUNTIME_DATA_UNAVAILABLE | Candidate, decision, outcome events; exact consolidation and group comparison | rejected outcome and exact-join tests; runtime ledger absent |

## 7. Previous Claims Found Incomplete or False

1. Points 10 and 12 were implemented in source but were never operationally proven by real broker fills. They remain partial.
2. Calibration, repeatability, hierarchical priors, and management promotion were sometimes described too close to active learning. They remain unavailable or shadow-only because there are zero clean eligible records.
3. The earlier repeatability contract allowed a score-nonrepeatable group to remain trade-eligible after removing score-floor authority. That was too permissive. Both `SCORE_NON_REPEATABLE` and `DECISION_NON_REPEATABLE` now fail closed; neither can waive the configured family floor.
4. Earlier counterfactual and shadow implementations could stop before the full objective horizon. The active v2 contracts retain pending state and append immutable outcome events.
5. Earlier normalized-FVG mechanics did not fully bind enforcement to asset-class clean evidence. Active v2 does.
6. A Python startup policy manifest without an EA runtime hash could appear compatible. Python pre-request startup now marks runtime compatibility unavailable; the MQL request/runtime manifest remains authoritative.
7. The first staged compile in this operation failed because `_CurrentLedgerIntegrityStatus()` was declared `const` while calling mutable file-bus read state. The declaration was corrected; both staged and active compiles then passed.
8. The first active control audit timed out by traversing `.venv` and the full MT5 installation. Source discovery now prunes environment/generated trees and scopes an MT5 root to `Include\MT5_PO3_Codex` and `Experts\MT5_PO3_Codex`.
9. The first active-root removed-control test searched legacy copies outside the active PO3 project. It now uses the same explicit project scope as the manifest.
10. The previous reports were evidence sources, not deployment proof. This report used active SHA checks, active Python tests, active-root control audit, and an active terminal compile.

## 8. Hierarchical Model Specification

### Eligibility

Rows must be all of:

- ledger status `CLEAN`/verified clean;
- exactly broker-attributed;
- not identity-quarantined;
- homogeneous cohort;
- complete immutable pre-entry features;
- compatible feature/taxonomy/schema versions;
- valid binary target without ambiguity.

### Global level

The global shadow model is L2-regularized logistic regression on standardized immutable pre-entry features. The initial intercept is the global event-rate logit. The persisted artifact includes feature names, centers, scales, intercept, coefficients, L2 value, sample size, window, and hash.

### Asset-class level

Each asset class gets a Bayesian posterior adjustment around the global event rate:

```text
alpha = parent_probability * prior_strength + successes
beta  = (1 - parent_probability) * prior_strength + failures
posterior = alpha / (alpha + beta)
shrinkage_weight = n / (n + prior_strength)
```

Asset adjustment requires the configured minimum sample. Calibration and validation are chronological and separate by asset class. Brier and calibration-gap gates must pass before a shadow artifact can be marked validated.

### Symbol level

Symbol adjustment is centered on the asset posterior and uses twice the base prior strength. It is eligible only when:

- symbol sample size passes the symbol minimum; and
- posterior uncertainty width is at most 0.30.

Small symbol samples therefore shrink strongly toward asset/global evidence and cannot create independent authoritative models.

### Persisted metadata

- global prior/model;
- asset and optional symbol adjustments;
- raw and posterior event rate;
- shrinkage weight and effective sample size;
- uncertainty bounds;
- feature and taxonomy versions;
- cohort ID;
- chronological train/calibration/validation windows;
- calibration metrics;
- activation block reasons;
- artifact hash.

Current runtime result: `shadow_unavailable`, because eligible clean count is zero.

## 9. LIVE_FORWARD Behavior Matrix

| Destination | Workload mode | AI failure | Fallback | Schema/cache/policy | Risk/management/ledger |
|---|---|---|---|---|---|
| Demo | LIVE_FORWARD | Same fail-closed contract | No more permissive demo fallback | Same strict contract | Same caps, management, attribution, output |
| Contest | LIVE_FORWARD | Same fail-closed contract | Same | Same | Same |
| Real | LIVE_FORWARD | Same fail-closed contract | Same | Same | Same |
| Tester RECORD_ONLY | Tester-only | No trading | Export only | Tester cache contract | No trading |
| Tester CACHE_ONLY | Tester-only | Cache miss rejects | No live AI | Strict current full cache only | Backtest trading path |
| Tester LIVE_WAIT_DEBUG | Tester-only | Wall-time/debug rules | Non-trading on unsafe simulated jump by default | Records responses | Not performance-valid by default |

The startup contract hashes account-independent behavior. Account destination is logged but does not alter forward authority.

Required active startup form:

```text
[live_forward_mode] account_trade_mode=<demo|contest|real> workload_mode=LIVE_FORWARD behavior_contract_hash=<hash> demo_real_equivalent=true
```

## 10. Semantic Cache-Invalidation Matrix

TTL is an upper storage bound only. It never overrides a semantic mismatch.

| Change | Invalidation reason |
|---|---|
| New entry-timeframe bar/candle identity | `cache_stale_new_entry_bar` |
| Entry drift over 0.05R | `cache_stale_entry_drift` |
| Stop drift over 0.05R | `cache_stale_stop_drift` |
| Target drift over 0.05R | `cache_stale_target_drift` |
| Spread-risk bucket | `cache_stale_spread_bucket` |
| Structure state | `cache_stale_structure` |
| FVG mitigation state | `cache_stale_fvg_state` |
| Session | `cache_stale_session` |
| Killzone | `cache_stale_killzone` |
| Bucket/hierarchical prior hash | `cache_stale_prior_version` |
| Candidate hash, execution fingerprint, policy, model, prompt, decision schema, or target schema | `cache_stale_contract_version` |

Each cache row persists assessed semantic values. Cache lookup compares them against the current candidate before a cached full-structured decision can trade.

Tester workflow/debug fields remain excluded from the economic decision signature. `runtime_input_hash` may change across tester workflow modes, but the tester replay/AI decision identity remains reusable when economic inputs are unchanged.

## 11. Multiplier-Semantics Audit

| Input state | Result |
|---|---|
| Missing and explicitly optional | Resolve to 1.0 with `value_present=false` |
| Missing and mandatory | Invalid, blocked, `multiplier_missing` |
| Explicit 0.0 | Valid block, never changed to 1.0 |
| 0.0 < value < 1.0 | Risk reduction |
| 1.0 | Normal risk |
| Negative | Invalid, `multiplier_below_zero` |
| Above permitted maximum | Invalid, `multiplier_above_maximum` |
| Boolean/non-numeric/non-finite | Invalid |

The audit covers AI, bucket, context, subtype, session, symbol/portfolio, execution-cost, and management policy paths. Final MQL placement rejects zero or invalid components before sizing.

## 12. Startup Policy Manifest Example

Policy classes covered:

- active analytics policy;
- context policy;
- subtype policy;
- session/weekday policy;
- hierarchical priors;
- risk factors;
- invalidation;
- normalized FVG;
- management;
- calibration;
- repeatability.

Example blocked row under the current dirty ledger:

```json
{
  "policy_type": "normalized_fvg",
  "policy_id": "normalized_fvg_policy",
  "enabled": true,
  "absolute_path": "C:/.../normalized_fvg_policy.v2.json",
  "file_hash": "<sha256>",
  "schema_version": "20260717_normalized_fvg_v2",
  "activation_state": "blocked",
  "shadow_state": false,
  "rows_loaded": 5,
  "rows_rejected": 0,
  "rejection_reasons": ["ledger_not_clean"],
  "code_compatibility": true,
  "runtime_input_compatibility": true,
  "decision_schema_compatibility": true,
  "taxonomy_compatibility": true,
  "ledger_integrity_status": "QUARANTINED",
  "authority": "blocked"
}
```

Python pre-request startup cannot claim runtime-input compatibility before the EA payload supplies the actual runtime hash. MQL persists the request-authoritative manifest in Common Files.

## 13. Dead-Control Audit

Active manifest:

`C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\data\control_effectiveness_manifest.json`

Counts:

- ACTIVE: 566
- DIAGNOSTIC_ONLY: 2
- REMOVED: 7
- DEAD: 0

Diagnostic-only policy metadata:

- `normalized_fvg_policy.v2.json:calibration_status`
- `risk_factor_policy.v1.json:description`

Removed controls:

- `InpAiOverrideConfidence`
- `InpAiOverrideScore`
- `InpAiRequireRawAllow`
- `InpAutoSuppressWeakFamiliesLive`
- `InpMinAiConfidence`
- `InpSyntheticFallbackMinCleanCaptureRatio`
- `InpSyntheticFallbackMinStatsCount`

Each active control row stores declaration, read count, authority path, log signal, and behavioral test. The audit is restricted to the active PO3 Include/Experts projects and does not let legacy copies elsewhere in MT5 masquerade as active controls.

## 14. Three-Stage Decision Flow

```text
Model structured response
    |
    +-- model_raw_allow
    v
Python schema + candidate binding + veto + repeatability + prior + cache contract
    |
    +-- python_final_allow + python_reasons
    v
MQL hash/fingerprint + freshness + target + risk + session + broker + policy gates
    |
    +-- mql_final_allow + mql_reasons
    v
Order placement (only when all three relevant authorities pass)
```

The fields are persisted separately in response JSON, cache, trade plan/state, completed trade, shadow outcomes, and analytics. A model approval is not relabeled as a Python or MQL approval. A Python approval is not final execution authority.

## 15. JSON Parser and Duplicate-Key Handling

Python uses a strict loader that rejects:

- duplicate keys;
- NaN/Infinity/non-finite constants;
- lone surrogate or invalid Unicode;
- trailing/partial JSON;
- non-object roots where an object is required.

MQL keeps the lightweight parser for compatibility, but decision-critical fields are flattened and globally unique. The MQL schema validator requires exact current versions, counts critical key occurrences, rejects ambiguous aliases/duplicates, rejects malformed/non-finite values, and validates:

- request ID;
- nonce;
- run/session ID;
- candidate ID/hash;
- request/assessed execution fingerprint;
- response binding hash/full structured response hash.

Old or incomplete responses are diagnostic/non-trading. They are not silently migrated into approvals.

## 16. File-Bus State Machine

```text
request temp -> atomic request rename -> requests
requests -> owned processing
processing -> response temp -> atomic response rename
response consumed -> completed | rejected | timed_out | stale | quarantined | shutdown
```

Directories:

- `requests`
- `responses`
- `processing`
- `completed`
- `rejected`
- `timed_out`
- `stale`
- `quarantined`
- `shutdown`

Every request has a nonce, run session ID, request fingerprint, ownership identity, and one candidate-bound response. Startup recovery archives abandoned artifacts. Shutdown archives pending requests and writes shadow decision updates. No shared mutable candidate response is valid.

## 17. Cohort Compatibility Rules

Every completed trade and shadow candidate carries:

- engine version;
- git commit;
- dirty-tree status;
- set-file hash;
- runtime-input hash;
- prompt contract version;
- model version;
- reasoning configuration;
- decision schema version;
- target schema version;
- policy ID/hash;
- bucket-prior hash;
- management version;
- taxonomy version;
- feature version;
- calibration artifact ID;
- repeatability artifact ID;
- derived cohort ID.

Records are compatible only when all required fields are complete and equal or an explicit future compatibility policy approves them. Analysis partitions homogeneous cohorts and blocks mixed/incomplete input rather than silently pooling it.

Current cohort audit:

- status: `BLOCKED`
- reasons: `invalid_json_rows`, `no_records`
- exact run `.set` identity was not supplied, so no authoritative deployment manifest/set hash was fabricated.

## 18. Layered Authority Diagram

```text
Deterministic engine
  candidate validity, exact prices, costs, sessions, broker feasibility, fingerprint
        |
        v
Statistical layer (shadow until OOS gates pass)
  path probabilities, expected MFE/MAE/net R, time to event
        |
        v
LLM layer
  anomaly/contradiction/missing-data veto and narrative explanation only
        |
        v
Python final decision
  strict schema, veto, repeatability/prior/policy contract
        |
        v
MQL final decision
  identity, freshness, target, risk, policy, broker safety
        |
        +----> Portfolio layer: aggregate/factor/exposure caps
        |
        +----> Management layer: independent shadow-validated exit policy
```

The LLM cannot own `calibrated_win_probability`, empirical expected net R, risk size, broker feasibility, or final execution. Compatibility fields such as LLM follow-through/risk assessments are veto-only and are not treated as calibrated probabilities.

## 19. Statistical Target Definitions

Business labels remain reporting-only:

- winner: full close percentage greater than +0.1%;
- loser: full close percentage below -0.1%;
- neutral: otherwise.

Primary entry/path targets:

- unmanaged original-plan net realized R;
- original target before original stop;
- favorable 0.25R before adverse 0.50R;
- reached 0.25R before adverse threshold;
- reached 0.50R before adverse threshold;
- unmanaged MFE R;
- unmanaged MAE R;
- time to 0.25R;
- time to 0.50R;
- time to invalidation/adverse threshold.

The configured observation ends at original stop, original target, configured horizon, session close, or data loss. Time-to-event outcomes are right-censored when the event is not observed. Same-bar favorable/adverse or stop/target ordering is marked ambiguous and excluded; the evaluator does not guess. Account percentage is provider/risk reporting only.

## 20. Entry Versus Management Model Separation

### Entry model

- immutable pre-entry features only;
- exact proposed entry/SL/target;
- unmanaged original-plan path outcomes;
- binary, continuous, and time-to-event shadow artifacts;
- no management-altered PnL target.

### Management model

- independent management-time snapshots only;
- MFE/MAE, elapsed time, remaining SL/TP distance, spread/cost, current structure;
- target is management alpha: managed remaining outcome minus original-policy remaining outcome;
- separate version, feature manifest, train/calibration/validation windows, and OOS gate.

Both return `trading_authority=false`. Current active build result is `shadow_unavailable` for both because there are no resolved clean homogeneous rows.

## 21. Rejected-Candidate Shadow Schema and Analysis

Each valid candidate lifecycle records:

- candidate timestamp and exact entry/SL/TP policy;
- candidate and request execution fingerprints;
- target model/source and cost estimate;
- decision stage and all three allow fields;
- rejection/veto reason;
- engine/config/model/policy/cohort versions;
- later target-first/stop-first status;
- MFE/MAE;
- times to 0.25R, 0.50R, stop, target, and adverse threshold;
- horizon result;
- ambiguity and censoring status.

The MQL engine writes an immutable candidate event, decision updates, and an outcome event. Python consolidation requires exact `parent_record_hash`, candidate hash, and execution fingerprint agreement. Completed trades join only by candidate hash plus final execution fingerprint, so same-candidate/different-execution rows cannot cross-attach.

Analysis emits:

- approved versus rejected expectancy;
- abstained versus approved expectancy;
- deterministic baseline versus Python/MQL decisions;
- false-rejection and false-approval cost in R;
- calibration monotonicity only when actual calibrated probabilities exist.

No shadow orders are sent.

Current runtime result:

- `shadow_candidates.jsonl`: missing;
- resolved outcomes: 0;
- comparison/model artifacts: unavailable, non-trading.

## 22. Tests and Exact Commands

### Python compile

```powershell
.\.venv\Scripts\python.exe -m py_compile ai_gate.py architecture_contracts.py calibration_pipeline.py decision_integrity.py expectancy_report.py governance_contracts.py runtime_governance.py tools\analyze_shadow_outcomes.py tools\audit_cohort_compatibility.py tools\audit_control_effectiveness.py tools\build_shadow_models.py tools\generate_deployment_manifest.py tools\verify_v_next_controls.py
```

### Unit suite

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

### Active control audit

```powershell
.\.venv\Scripts\python.exe tools\audit_control_effectiveness.py --python-root . --mql-root C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5 --output data\control_effectiveness_manifest.json --fail-on-dead
```

### Active terminal verifier and MetaEditor compile

```powershell
python tools\verify_v_next_controls.py --mql-root C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5
```

### Ledger repair/audit

```powershell
.\.venv\Scripts\python.exe tools\repair_trade_ledger.py --logs-dir C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\trade_results --out-dir <audit-output>
.\.venv\Scripts\python.exe tools\audit_trade_ledger.py --input C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\analytics\system_trade_history.json --output-dir <audit-output> --dry-run
```

### Shadow and cohort dry runs

```powershell
.\.venv\Scripts\python.exe tools\analyze_shadow_outcomes.py --file C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\shadow_candidates.jsonl --output <audit-output>\shadow_outcomes.json
.\.venv\Scripts\python.exe tools\build_shadow_models.py --file C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\completed_ai_trades.jsonl --shadow-file C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\shadow_candidates.jsonl --output-dir <audit-output>\shadow_models
.\.venv\Scripts\python.exe tools\audit_cohort_compatibility.py --file C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\completed_ai_trades.jsonl --output <audit-output>\cohort.json
```

## 23. Passed, Failed, and Skipped Results

### Passed

- Active Python py_compile: PASS.
- Active Python unit suite: 117 tests, PASS, 0 failures.
- Full v-next verifier: PASS, all controls.
- Active source control audit: PASS, 0 dead controls.
- Active terminal MetaEditor compile: PASS.
- Source deployment hash audit: 26 source files, 0 mismatches.
- Strict JSON duplicate/non-finite/Unicode tests: PASS.
- Semantic cache reason and TTL tests: PASS.
- LIVE_FORWARD equivalence tests: PASS.
- Hierarchy/shrinkage and asset separation tests: PASS.
- Multiplier semantics tests: PASS.
- Cohort split/block tests: PASS.
- Entry/management target and shadow exact-join tests: PASS.
- Prior point 1-40 regression suite: PASS.

### Failures encountered and corrected

1. Staged MQL compile initially failed on a `const` file-bus read. Corrected, then 0/0.
2. Active control audit initially timed out on `.venv`/unrelated MT5 trees. Source traversal now prunes/scopes those trees.
3. Include-only active audit falsely marked eight EA-owned controls dead. Audit now scans both active Include and Experts project roots.
4. Removed-control test initially saw legacy inactive MT5 copies. Test now uses active project scope.

### Skipped/unavailable by evidence gate

- real market-fill verification;
- real pending-fill verification;
- netting virtual sub-position validation;
- live repeatability artifact;
- live hierarchical-prior artifact;
- calibration promotion;
- management-policy promotion;
- broker-cost operational proof;
- broker holiday/early-close operational proof;
- approved/rejected runtime shadow comparison.

## 24. MetaEditor Result

Active terminal source compile:

```text
Result: 0 errors, 0 warnings, 161106 msec elapsed, cpu='X64 Regular'
```

Compile target used the active terminal `MQL5` tree. The verifier passed after compilation.

Compile log deployed with this report:

`C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\mql_compile_architecture_cache_shadow_20260717_041607.log`

## 25. Ledger and Cohort Audit

### Legacy trade-result directory

- files scanned: 619
- clean: 0
- rejected: 619
- quarantined: 436
- unattributed: 183
- learning eligible: 0
- suppression eligible: 0
- optimization eligible: 0
- status: `QUARANTINED`

Primary recurring failures include missing exact attribution, candidate/fingerprint mismatch, missing exact deal/position data, unknown taxonomy, missing risk denominator, price-scale mismatch, partial-volume mismatch, and unsupported/missing account mode.

### Current consolidated history

- total: 1
- clean: 0
- unattributed: 1
- status: `QUARANTINED`

### Cohort

- completed-trade source records: 0
- status: `BLOCKED`
- reasons: `invalid_json_rows`, `no_records`

### Models

- hierarchical outcome: `shadow_unavailable`
- entry model: `shadow_unavailable`
- management model: `shadow_unavailable`
- trading authority: false

## 26. Operational Proof Status and Commands

### Controlled demo market fill

Status: NOT PROVEN.

After exporting a real `MT5_DEMO_BROKER_HISTORY` capture:

```powershell
.\.venv\Scripts\python.exe tools\verify_broker_fill_capture.py --capture data\proof\demo_market_fill.json
```

### Controlled demo pending fill

Status: NOT PROVEN.

```powershell
.\.venv\Scripts\python.exe tools\verify_broker_fill_capture.py --capture data\proof\demo_pending_fill.json
```

Synthetic fixtures are not accepted as operational proof.

### Runtime repeatability collection

Status: NO RUNTIME ARTIFACT.

For a controlled non-authoritative collection run, set process-level values before starting the gate:

```powershell
$env:AI_SHADOW_REPEAT_ENABLE='true'
$env:AI_SHADOW_REPEAT_SAMPLE_RATE='0.02'
$env:AI_SHADOW_REPEAT_COUNT='3'
powershell -ExecutionPolicy Bypass -File .\start_bot_with_transcript.ps1 run
```

The artifact remains partitioned by exact model/prompt/decision/target/tier and cannot waive configured floors.

### Hierarchical artifact after clean rows exist

```powershell
.\.venv\Scripts\python.exe tools\build_shadow_models.py --file logs\completed_ai_trades.jsonl --shadow-file logs\shadow_candidates.jsonl --output-dir data\shadow_models
```

### Aggregate/factor risk startup proof

Status: NOT CAPTURED LIVE.

Capture the active EA startup lines for `[live_forward_mode]`, `[startup_policy_manifest]`, aggregate cap, factor policy, and the first blocked/allowed placement. Do not infer enforcement from input values alone.

### Broker-cost proof

Status: NOT CAPTURED LIVE.

Capture broker commission/swap/spread samples, the selected median/stressed percentile, estimated R burden at entry, and realized prediction error at close.

### Broker holiday/early close proof

Status: NOT CAPTURED LIVE.

Run through a real broker holiday or early-close date and retain symbol-session queries, no-entry transition, flatten attempts, retry timestamps, and final broker state.

## 27. Remaining Risks and TODOs

1. Obtain one real demo market fill and one real demo pending fill with exact order/deal/position evidence.
2. Implement and validate a virtual sub-position ledger before allowing multiple strategy trades per symbol on netting accounts. Current code fails closed or restricts management; it does not pretend netting positions are independent.
3. Generate clean current-version ledger rows. All 619 legacy rows are excluded from learning.
4. Supply the exact `.set` file used for each run and generate a deployment manifest. Candidate `.set` files exist, but choosing one without run evidence would be fabrication.
5. Collect runtime repeatability data. Deterministic tests do not establish production model repeatability.
6. Build hierarchical/calibration artifacts only after sufficient exact, homogeneous, clean rows exist.
7. Keep entry and management artifacts shadow-only until independently chronological-OOS validated.
8. Capture live aggregate/factor risk, broker-cost, and holiday/early-close behavior.
9. Run a fresh forward session to create `shadow_candidates.jsonl` and verify all terminal candidate paths emit objective outcomes.
10. The worktree is dirty; cohort records correctly preserve that state. Create a reviewed commit before treating a cohort as a stable release.

## 28. Confirmation of Unchanged Settings

This operation did not change:

- PO3/FVG definitions;
- strategy-family defaults;
- setup thresholds;
- RR floors/defaults;
- target-arbitration behavior;
- model names;
- Flex configuration;
- symbol universe;
- tester mode defaults/workflow;
- portfolio-risk defaults;
- any `.set` file;
- private `.env` values.

The private `.env` was not read or reproduced. Existing unrelated dirty files were not reverted.

## 29. Requirement-to-Code-and-Test Traceability

| Requirement | Primary active code | Test/evidence | Status |
|---|---|---|---|
| 40 normalized FVG remains active | `FVG.mqh`, `Config.mqh`, `Types.mqh`, policy v2 | normalized-FVG tests, active compile | VERIFIED_ACTIVE |
| 41 hierarchy | `architecture_contracts.build_hierarchical_outcome_artifact` | hierarchy/shrinkage tests | SHADOW_UNAVAILABLE_RUNTIME |
| 42 LIVE_FORWARD | `workload_mode`, `live_forward_behavior_contract`, `AIGateBridge._WorkloadMode` | demo/contest/real equivalence test | VERIFIED_ACTIVE |
| 43 semantic cache | `SemanticCacheState`, `AIDecisionCache` | every-reason and TTL tests | VERIFIED_ACTIVE |
| 44 multipliers | `resolve_multiplier`, Python/MQL policy and sizing gates | missing/zero/range tests | VERIFIED_ACTIVE |
| 45 manifest | `build_startup_policy_manifest`, `_StartupPolicyManifestRow` | dirty-ledger block test | VERIFIED_ACTIVE_FAIL_CLOSED |
| 46 dead controls | `audit_control_effectiveness.py` | active-root manifest: DEAD=0 | VERIFIED_ACTIVE |
| 47 decisions | `DecisionAuthority`, response/plan/ledger fields | distinct-stage test | VERIFIED_ACTIVE |
| 48 JSON | `strict_json_loads`, `JsonLite.mqh`, response binding | fuzz/duplicate/hash tests | VERIFIED_ACTIVE |
| 49 file bus | `FileBusLifecycle`, `FileBus.mqh`, archive/recovery paths | atomic lifecycle test, verifier | VERIFIED_ACTIVE_CODE |
| 50 cohorts | cohort helpers and MQL cohort state | homogeneous pass/mixed block; runtime blocked | VERIFIED_ACTIVE_FAIL_CLOSED |
| 51 layered ownership | authority manifest and Python/MQL gates | LLM ownership rejection test | VERIFIED_ACTIVE |
| 52 targets | `ENTRY_TARGET_DEFINITIONS`, `evaluate_path_targets` | target suite/censoring tests | VERIFIED_ACTIVE_SHADOW |
| 53 models | `build_entry_and_management_shadow_artifacts` | independent target/features test | SHADOW_UNAVAILABLE_RUNTIME |
| 54 rejected outcomes | MQL shadow lifecycle, consolidate/merge/compare tools | complete rejected record and exact join tests | ACTIVE_RUNTIME_DATA_UNAVAILABLE |

## 30. WHAT I NEED CHATGPT TO REVIEW NEXT

Review only new current-version runtime evidence, in this order:

1. A controlled demo market fill capture and pending fill capture proving exact order/deal/position identity.
2. The exact `.set` used for the run plus the generated deployment manifest and startup policy manifest.
3. The first active `shadow_candidates.jsonl` containing candidate, decision-update, and objective-outcome events.
4. A refreshed ledger audit showing whether any rows become CLEAN under the v6 identity contract.
5. Repeatability output partitioned by exact model/prompt/decision/target/tier.
6. Only after clean homogeneous samples exist: hierarchical asset calibration, entry OOS results, management-alpha OOS results, and approved-versus-rejected shadow analysis.

Do not review or promote calibration, priors, suppression, or management authority from the 619 legacy records. They remain fully excluded.
