# IMPLEMENTATION REPORT: REPEATABILITY, PRIORS, RISK, AND MANAGEMENT

Generated: 2026-07-16 23:52:04 UTC

## 1. Executive summary

This operation audited the active Nova Python source, the active MT5 terminal include tree, the active EA, the prior staged tree, both prior implementation reports, the real trade-result ledger, and the merged completion tree.

The points 11-25 implementation and the first version of points 26-40 had already been copied into the active Python and terminal include trees before the final completion pass. The final completion pass corrected four remaining semantic gaps:

1. Counterfactual management outcomes now remain pending after an early exit until the original SL, original target, or configured horizon is reached. Same-bar SL/TP collisions without tick ordering are ambiguous and ineligible.
2. Shadow candidates now receive later immutable outcome events with target/stop/horizon status, MFE, MAE, and time-to-event.
3. Invalidation confirmation supports an optional versioned asset-class policy while preserving the existing global inputs when that policy is disabled.
4. Normalized FVG ENFORCE mode now requires a compatible asset-class policy and sufficient clean asset-class samples. The default remains SHADOW.

Those final changes were implemented and first verified in:

    C:\Users\amroe\Downloads\PO3_Codex\MT5\python\.codex_stage_repeatability_20260717

They were then deployed byte-for-byte to the active Nova and terminal include trees. A timestamped pre-deployment backup is retained at:

    C:\Users\amroe\Downloads\PO3_Codex\MT5\python\.codex_active_backup_repeatability_20260717_002809

Active verification:

- Python compile: PASS.
- Python unit suite: PASS, 89 tests.
- Golden validators: PASS, 10 cases, 0 mismatches.
- Repository verifier: PASS.
- MetaEditor compile of the active EA/include source through an isolated output mirror: PASS, 0 errors and 0 warnings.
- Real ledger audit: QUARANTINED, 619 total, 0 clean, 436 quarantined, 183 unattributed, 0 eligible.
- Real demo market/pending fill attribution: UNVERIFIED.
- Runtime repeatability artifact: UNAVAILABLE.
- Runtime hierarchical prior artifact: MISSING.

The deployment and deterministic compile/test acceptance conditions are met. Operational evidence conditions remain explicitly incomplete where clean data or real broker fills are required.

Status legend:

| Status | Meaning |
|---|---|
| VERIFIED_ACTIVE | Present in current active source and covered by deterministic verification |
| REPAIRED_AND_DEPLOYED | Repaired and copied into active source during the broader operation |
| PARTIAL_OPERATIONAL_PROOF_REQUIRED | Code exists, but live broker/data proof is still unavailable |
| STAGED_NOT_DEPLOYED | Implementation exists only in a detached staging tree |
| MISSING | Required implementation is absent |

## 2. Active versus staged deployment table with hashes

Active roots:

- Python: C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python
- MQL Include: C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex
- EA: C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Experts\MT5_PO3_Codex

Final staged root:

- C:\Users\amroe\Downloads\PO3_Codex\MT5\python\.codex_stage_repeatability_20260717

The table contains every file in the final completion delta plus the active-layout test resolver repaired during active verification. SHA-256 hashes are full hashes.

| Kind | Relative file | Active SHA-256 | Final staged SHA-256 | Match |
|---|---|---|---|---|
| Python | runtime_governance.py | 4a775ae1bda8b5a182ac7f945af8a416fb82cd8882425324cd2ec68d5ad21de6 | 4a775ae1bda8b5a182ac7f945af8a416fb82cd8882425324cd2ec68d5ad21de6 | Yes |
| Python | config\invalidation_policy.v1.json | 4a8f69282f65a440266173e0bb1134cf89717303a5a10ccdf904493d62c963c6 | 4a8f69282f65a440266173e0bb1134cf89717303a5a10ccdf904493d62c963c6 | Yes |
| Python | config\normalized_fvg_policy.v2.json | ff59c0297c15fc0974896126e6d3cba8cd3ba4844d5cec340511fadede6165a7 | ff59c0297c15fc0974896126e6d3cba8cd3ba4844d5cec340511fadede6165a7 | Yes |
| Python | tests\test_governance_contracts.py | 9caff697f47db595517ef4dad4fd838988af1d17535dc75ae30fe44c844bb044 | 9caff697f47db595517ef4dad4fd838988af1d17535dc75ae30fe44c844bb044 | Yes |
| Python | tests\test_runtime_governance.py | 9e41a3ab6ea687d99a36c9bed53435e86cb8199d127bc5b00bbd6d944e9edbc0 | 9e41a3ab6ea687d99a36c9bed53435e86cb8199d127bc5b00bbd6d944e9edbc0 | Yes |
| Python | tools\analyze_ai_trade_outcomes.py | 6c96c6a6bccfa4c6df19b35f5dd8807b533e9d061b06b8416b87fc6d6eb0debc | 6c96c6a6bccfa4c6df19b35f5dd8807b533e9d061b06b8416b87fc6d6eb0debc | Yes |
| Python | tools\verify_v_next_controls.py | abfadc8e4476871bd08648518d172aec19fa8c7ae1401c2408ba3cccd048a70a | abfadc8e4476871bd08648518d172aec19fa8c7ae1401c2408ba3cccd048a70a | Yes |
| MQL | AIGateBridge.mqh | 86a2a29192817eeec64dd40e6e87412468560c7b37cd4592cb86abab8a0abe08 | 86a2a29192817eeec64dd40e6e87412468560c7b37cd4592cb86abab8a0abe08 | Yes |
| MQL | Config.mqh | cd273cf8a09d9f7c0a43ee744579bea1ee0aa97f3b53a679fcdded4b9e9c0c36 | cd273cf8a09d9f7c0a43ee744579bea1ee0aa97f3b53a679fcdded4b9e9c0c36 | Yes |
| MQL | FVG.mqh | e5bd6d6c78c516221c67cb178a48fe262b2e4464640bf9eb158138bb7afc29c1 | e5bd6d6c78c516221c67cb178a48fe262b2e4464640bf9eb158138bb7afc29c1 | Yes |
| MQL | PenaltyWatcher.mqh | c3a598f06cb318a1212d03cd61907f9f2151dcfbe4073a8bef4bb85394970023 | c3a598f06cb318a1212d03cd61907f9f2151dcfbe4073a8bef4bb85394970023 | Yes |
| MQL | StateStore.mqh | 29551e6f5ab72c4772e183576f6a319a1c0afea2d7d668f14e2a86454571a320 | 29551e6f5ab72c4772e183576f6a319a1c0afea2d7d668f14e2a86454571a320 | Yes |
| MQL | TradeEngine.mqh | 4bd9727b2e4f98ff15a7efc7bbb43920503bbf3d1dc2393d1da5677f9330b161 | 4bd9727b2e4f98ff15a7efc7bbb43920503bbf3d1dc2393d1da5677f9330b161 | Yes |
| MQL | Types.mqh | 70128bf24a7b04142cc4870fd3afa15f5f189440b103cdb2b721fc93e8884bfe | 70128bf24a7b04142cc4870fd3afa15f5f189440b103cdb2b721fc93e8884bfe | Yes |

The active and staged completion now carry:

- management schema 20260717_management_state_v2
- management counterfactual schema 20260717_management_counterfactual_v2
- shadow candidate schema 20260717_shadow_candidate_v2
- normalized FVG schema 20260717_normalized_fvg_v2
- invalidation asset-class schema 20260717_invalidation_asset_class_v1

Runtime policy deployment:

| Common Files policy | SHA-256 | Source match |
|---|---|---|
| PO3_AI_BUS\config\invalidation_policy.v1.json | 4a8f69282f65a440266173e0bb1134cf89717303a5a10ccdf904493d62c963c6 | Yes |
| PO3_AI_BUS\config\normalized_fvg_policy.v2.json | ff59c0297c15fc0974896126e6d3cba8cd3ba4844d5cec340511fadede6165a7 | Yes |

## 3. Exact changed active files

The following files contain the deployed points 11-25 and completed points 26-40.

Active terminal include files:

1. AIGateBridge.mqh
2. Config.mqh
3. FVG.mqh
4. PenaltyWatcher.mqh
5. Risk.mqh
6. StateStore.mqh
7. TradeEngine.mqh
8. Types.mqh

Active Python files:

1. .env.example
2. ai_gate.py
3. calibration_pipeline.py
4. decision_integrity.py
5. expectancy_report.py
6. experiment_registry.py
7. governance_contracts.py
8. runtime_governance.py
9. config\risk_factor_policy.v1.json
10. config\invalidation_policy.v1.json
11. config\normalized_fvg_policy.v2.json
12. tests\test_decision_integrity.py
13. tests\test_governance_contracts.py
14. tests\test_runtime_governance.py
15. tools\analyze_ai_trade_outcomes.py
16. tools\audit_trade_ledger.py
17. tools\calibrate_outcomes.py
18. tools\ledger_io.py
19. tools\manage_experiments.py
20. tools\rebuild_clean_trade_ledger.py
21. tools\repair_trade_ledger.py
22. tools\verify_broker_fill_capture.py
23. tools\verify_v_next_controls.py

The EA mq5 file was read, traced, and compiled through the exact-mirror verifier but was not modified. No set file was modified.

The final staged and active hashes match for all 14 completion/verification files in section 2.

Active runtime policy files added under Terminal Common Files:

1. PO3_AI_BUS\config\invalidation_policy.v1.json
2. PO3_AI_BUS\config\normalized_fvg_policy.v2.json

## 4. Audit table for points 1-10

| Point | Requirement | Status | Active evidence | Limitation |
|---|---|---|---|---|
| 1 | Separate rule, LLM quality, legacy blend, calibration, expectancy, and self-confidence | VERIFIED_ACTIVE | strict fields in decision_integrity.py, ai_gate.py, Types.mqh, AIGateBridge.mqh | Calibrated probability and expected net R remain unavailable rather than fabricated |
| 2 | Synthetic confidence has zero authority | VERIFIED_ACTIVE | legacy_agreement_confidence is diagnostic; threshold tests pass | No empirical calibration exists |
| 3 | Independent assessment per candidate | VERIFIED_ACTIVE | candidate assessment array and candidate hash binding | None found in deterministic tests |
| 4 | No silent rule_best substitution | VERIFIED_ACTIVE | invalid selected candidate rejects; alternative needs its own assessment | None found |
| 5 | Assessed/final execution fingerprints | VERIFIED_ACTIVE | canonical candidate and execution fingerprints with tolerance comparison | Live broker drift proof still desirable |
| 6 | Strict mandatory AI schema | VERIFIED_ACTIVE | Python and MQL both reject missing, null, malformed, duplicate, out-of-range fields | Legacy rows are non-trading |
| 7 | Degraded fallback non-trading | VERIFIED_ACTIVE | decision_quality_tier is canonical; only FULL_STRUCTURED and CACHE_OF_FULL_STRUCTURED trade | None found |
| 8 | Explicit ABSTAIN | VERIFIED_ACTIVE | ABSTAIN cannot watchlist, market trade, or pending trade | None found |
| 9 | Zero risk multiplier remains zero | VERIFIED_ACTIVE | None, zero, reduced, and one are distinct in Python, policy, parser, and execution | None found |
| 10 | Exact broker order/deal/position identity | PARTIAL_OPERATIONAL_PROOF_REQUIRED | exact identifiers, quarantine, hedging/netting policies, synthetic tests | No controlled real market and pending fills were captured; real ledger has zero clean rows |

## 5. Audit table for points 11-25

| Point | Requirement | Status | Active implementation | Proof or limitation |
|---|---|---|---|---|
| 11 | Account margin mode governance | VERIFIED_ACTIVE | Config.mqh, Types.mqh, TradeEngine.Init | Unit/static verifier passes |
| 12 | Position ID primary identity | PARTIAL_OPERATIONAL_PROOF_REQUIRED | TradeEngine, StateStore, PenaltyWatcher | Exact real fill capture remains unavailable |
| 13 | Central ledger integrity gate | VERIFIED_ACTIVE | governance_contracts.audit_trade_records and completion gate | Real audit ran: 619 records, 0 clean |
| 14 | Three outcome views | VERIFIED_ACTIVE | MQL completion and Python calculate_outcome_metrics | Unit tests pass |
| 15 | Complete deal accounting | VERIFIED_ACTIVE | deal enumeration and calculate_deal_accounting | Unit tests pass; real records remain dirty |
| 16 | Equal-trade, risk-weighted, and risk-normalized metrics | VERIFIED_ACTIVE | ledger/report metrics | Current real eligible set is empty |
| 17 | Strict setup taxonomy | VERIFIED_ACTIVE | MQL and Python resolvers | Cross-language precedence tests pass |
| 18 | Unknown taxonomy block | VERIFIED_ACTIVE | pre-AI hard gate and audit | Unknowns fail closed |
| 19 | Governed suppression | VERIFIED_ACTIVE | suppression_eligibility and policy governance | Requires clean/OOS evidence before activation |
| 20 | Time-aware uncertainty | VERIFIED_ACTIVE | block bootstrap and purged chronological folds | Deterministic tests pass |
| 21 | Experiment registry | VERIFIED_ACTIVE | experiment_registry.py and management tools | No active management policy promotion |
| 22 | Feature double-count controls | VERIFIED_ACTIVE | feature lineage and compact LLM payload | Tests pass |
| 23 | Heuristic-quality rename | VERIFIED_ACTIVE | canonical naming in MQL/Python/logs | Legacy aliases diagnostic only |
| 24 | Uncapped raw score plus calibrated authority | PARTIAL_OPERATIONAL_PROOF_REQUIRED | raw score and shadow calibration pipeline | Calibration unavailable because clean sample is zero |
| 25 | Canonical decision_quality_tier | VERIFIED_ACTIVE | Python, MQL, cache, persistence | response_quality is equality-checked migration alias only |

## 6. Implementation table for points 26-40

| Point | Requirement | Status | Implementation location | Evidence and qualification |
|---|---|---|---|---|
| 26 | Full fingerprints and repeatability authority | PARTIAL_OPERATIONAL_PROOF_REQUIRED | runtime_governance.request_fingerprint, response_fingerprint, evaluate_repeatability; ai_gate shadow-repeat flow; AIGateBridge | Active code exists and tests pass; no runtime artifact exists, so actual status is UNAVAILABLE |
| 27 | Hierarchical priors with shrinkage | PARTIAL_OPERATIONAL_PROOF_REQUIRED | build_hierarchical_prior_artifact, priors_for_candidate, analyzer | Active code exists; zero clean records means no valid live artifact |
| 28 | Module-root path, startup audit, mandatory fail-close | VERIFIED_ACTIVE | resolve_project_path, audit_prior_artifact, ai_gate startup gate | Missing mandatory prior test passes; private env was not inspected |
| 29 | Aggregate original-risk cap | PARTIAL_OPERATIONAL_PROOF_REQUIRED | Risk.mqh AggregateInitialRiskGate and TradeEngine final placement gates | Active and tested; preserved input default disables it, so no live enforcement proof |
| 30 | Stricter percentage/money caps | PARTIAL_OPERATIONAL_PROOF_REQUIRED | EffectiveAggregateRiskCap in Python/MQL and startup audit | Tests prove stricter cap; no observed active runtime values |
| 31 | Fixed factor/cluster caps | PARTIAL_OPERATIONAL_PROOF_REQUIRED | risk_factor_policy.v1.json, factor_contributions, RiskFactorGate | Active policy exists but gate remains disabled by preserved input default |
| 32 | Broker-derived stressed costs | PARTIAL_OPERATIONAL_PROOF_REQUIRED | BrokerCostEstimatePerLot, estimate_broker_cost, completion cost error fields | Active and tested; broker sample count/source not observed in a current live run |
| 33 | Idempotent management state machine | VERIFIED_ACTIVE | ManagementState, transition_management_state, PenaltyWatcher state transitions | Base v1 active; duplicate action tests pass |
| 34 | Management policy experiments | PARTIAL_OPERATIONAL_PROOF_REQUIRED | management_policy_authority, experiment registry, shadow experiment rows | No policy can promote with zero clean exact-attribution records |
| 35 | Separate opening/closing volume normalization | VERIFIED_ACTIVE | NormalizeOpeningVolume, NormalizeClosingVolume and Python mirrors | Boundary, minimum, residual, partial, and full-close tests pass |
| 36 | Asset-aware invalidation confirmation | REPAIRED_AND_DEPLOYED | resolve_invalidation_policy, Config.mqh, PenaltyWatcher.mqh, invalidation_policy.v1.json | Active asset policy, wick/spread/persistence tests pass |
| 37 | Actual versus full-horizon counterfactual management | REPAIRED_AND_DEPLOYED | evaluate_price_path_contract, TradeEngine pending queue, analyzer update merge | Active no-future-data and same-bar ambiguity tests pass |
| 38 | Per-symbol broker sessions and close windows | PARTIAL_OPERATIONAL_PROOF_REQUIRED | SymbolSessionSchedule, session_action, flatten retry state | Active code and tests exist; holiday/early-close behavior needs broker-session runtime proof |
| 39 | Shadow candidate ledger with later objective outcomes | REPAIRED_AND_DEPLOYED | TradeEngine shadow pending queue and resolution events | Active immutable outcome/static tests pass |
| 40 | Normalized FVG OFF/SHADOW/ENFORCE | REPAIRED_AND_DEPLOYED | FVG.mqh, normalized_fvg_minimum, normalized_fvg_policy.v2.json | Active asset-class evidence gate passes tests; default remains SHADOW |

## 7. Previous claims that were false, incomplete, or not deployed

1. IMPLEMENTATION_REPORT_LEDGER_TAXONOMY_CALIBRATION_20260716_205314.md correctly labeled points 11-25 as staged. It was not proof of deployment. Those files were later merged into active source, but this operation still found zero clean real ledger rows and no real broker attribution proof.
2. Any interpretation that point 10 or point 12 was operationally proven was too strong. Synthetic identity tests pass, but no controlled demo market/pending pair has demonstrated exact order, deal, and position attachment.
3. Any interpretation that calibration, priors, or repeatability were empirically active was false. No live prior artifact or repeatability artifact exists, and the current ledger provides zero clean eligible records.
4. The first version of counterfactual management closed the counterfactual too early in some early-exit paths. The deployed v2 repair keeps it pending and appends a later immutable resolution.
5. The first shadow-candidate implementation persisted pre-AI records but did not complete all hypothetical outcomes. The deployed v2 repair adds later outcome events.
6. The first normalized-FVG implementation had SHADOW/OFF/ENFORCE mechanics but lacked a complete asset-class policy/evidence contract. The deployed v2 repair adds it.
7. The original active unit command exposed five test errors because test_governance_contracts.py resolved only a detached sibling mql tree. The deployed resolver now supports PO3_MQL_INCLUDE_ROOT, the staged layout, and unambiguous active terminal discovery. This was a verification-routing defect, not a trading-code defect.

## 8. MetaEditor compile result for active source

Final staged tree:

- Result: PASS.
- Compiler line: Result: 0 errors, 0 warnings, 126973 msec elapsed, cpu='X64 Regular'.
- Log: C:\Users\amroe\Downloads\PO3_Codex\MT5\python\.codex_stage_repeatability_20260717\mql_compile_after_completion.log
- The repository verifier also compiled an isolated exact mirror and passed.

Active terminal tree:

- Latest v2 active-source compile: PASS.
- The active verifier read the active EA and active terminal include tree, copied them to an isolated output mirror to avoid replacing a locked production EX5, and invoked MetaEditor.
- Compiler line: Result: 0 errors, 0 warnings, 124462 msec elapsed, cpu='X64 Regular'.
- Log: C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\mql_compile_v_next_verify.log

## 9. Real demo market/pending-fill proof

Status: UNVERIFIED.

No real broker order was submitted by this operation. No controlled demo market fill and no controlled pending fill were captured. The exact identity verifier exists, but current real ledger evidence is:

- total_records: 619
- clean: 0
- quarantined: 436
- unattributed: 183
- suspicious: 0
- learning_eligible_count: 0
- optimization_eligible_count: 0
- suppression_eligible_count: 0
- global_status: QUARANTINED

Exact broker attribution remains a code-and-synthetic-test result, not an operational broker proof.

## 10. Request/response fingerprint specification

Canonicalization:

- Structured values are serialized with sorted keys and stable separators.
- SHA-256 is used over canonical UTF-8 JSON.
- Candidate order is explicit and retained.
- Economic decision fields participate; tester workflow/debug flags do not alter economic cache identity.

Request fingerprint fields:

- request ID and session ID
- candidate index, ID, immutable candidate hash, and request execution fingerprint
- candidate order
- runtime-input hash
- prompt-contract version
- decision-schema version
- target-schema version
- model and reasoning effort
- decision-quality tier
- hierarchical-prior artifact hash and version
- canonical full payload hash
- repeatability fingerprint schema version

Response fingerprint fields:

- response ID and model returned
- decision-quality tier and decision state
- complete per-candidate assessments
- selected candidate ID/hash/index
- veto state/reason
- LLM quality score
- exact suggested risk multiplier
- complete target arbitration
- full structured response hash

The request and response records are appended to the configured fingerprint JSONL. Shadow repeats bypass the normal decision cache and cannot place orders, create watchlists, alter primary risk, or overwrite the primary decision.

## 11. Repeatability methodology and actual results

Partition key:

- exact model
- prompt-contract version
- decision-schema version
- target-schema version
- decision-quality tier

Metrics:

- population standard deviation of llm_quality_score
- decision agreement
- chosen-candidate agreement
- veto agreement
- risk-multiplier standard deviation
- target-choice agreement
- response-fingerprint uniqueness

Default evidence thresholds in .env.example:

- minimum evaluated candidates: 30
- maximum score standard deviation: 0.75
- minimum decision agreement: 0.90
- minimum chosen-candidate agreement: 0.90
- minimum veto agreement: 0.90
- minimum target-choice agreement: 0.85

Authority:

- DECISION_NON_REPEATABLE makes the exact model/prompt/schema/tier combination non-trading and converts the decision to ABSTAIN.
- SCORE_NON_REPEATABLE disables score-threshold authority while retaining independent hard/veto gates.
- Statistics are never pooled across contract partitions.

Actual result:

- No active data\ai_repeatability_artifact.json exists.
- No Common Files repeatability artifact exists.
- No evaluated runtime group can be claimed.
- Current repeatability status is UNAVAILABLE.
- The 89-test suite proves the evaluator and authority behavior with deterministic fixtures; it does not establish model repeatability in production.

## 12. Hierarchical-prior model and shrinkage examples

All levels are sent separately:

1. global
2. asset class
3. symbol
4. family
5. branch
6. session
7. killzone
8. family by symbol
9. family by session

Only rows satisfying all of the following can form priors:

- clean/reconciled ledger integrity
- exact verified attribution
- not identity-quarantined
- current ledger and decision schemas
- learning, optimization, and suppression eligibility all true

For a narrow bucket with effective sample n:

    parent_support = prior_strength * uncertainty_penalty * parent_sample_factor
    shrinkage_weight = n / (n + parent_support)
    shrunk_mean = weight * raw_mean + (1 - weight) * parent_mean

The uncertainty penalty rises for infinite or large standard error. Parent support rises with the broader parent sample.

Deterministic example executed in this operation:

- broader evidence: 400 rows near +0.4R
- narrow evidence: 4 rows at -4.0R
- narrow raw mean: -4.000000R
- parent shrunk mean: +0.356436R
- narrow shrinkage weight: 0.070997506
- final narrow shrunk estimate: +0.047140R

The four-row bucket therefore cannot dominate the 400-row parent.

Each prior stores raw prior, parent prior, shrinkage weight, shrunk estimate, posterior uncertainty, effective sample size, clean counts, data window, source versions, hierarchy path, prior hash, and artifact hash.

## 13. Prior startup audit

Path resolution uses Path(__file__).resolve().parent, not the process working directory.

The startup audit logs:

- absolute path
- existence
- file SHA-256
- mtime
- prior/schema version
- data window
- bucket/rejected counts
- ready, missing, stale, incompatible, dirty/empty, or unreadable status
- mandatory state

Observed on disk:

- Active Python data directory contains only ai_decision_cache.jsonl.
- data\live_bucket_priors.json is absent.
- No equivalent prior artifact exists in the Common Files bus.
- Prior status is MISSING.

The private .env was deliberately not read or reproduced because it contains a secret. Therefore the active private value of AI_REQUIRE_LIVE_BUCKET_PRIORS is not asserted here. The example default is false. If the private setting is true for live-forward mode, the missing artifact produces mandatory_live_priors_unavailable before an AI request or trade.

The ledger cannot currently generate an eligible artifact because it has zero clean rows.

## 14. Aggregate and factor-risk formulas and startup values

Original risk:

    original_risk_money = remaining_volume * original_risk_money_per_lot

Aggregate proposed risk:

    post_trade_risk =
        sum(open remaining original risk)
        + sum(pending original risk)
        + proposed original risk

Percentage cap converted to money:

    percentage_cap_money = risk_base_money * InpMaxTotalRiskPct / 100

Effective cap:

    effective_cap_money = min(valid percentage_cap_money, valid money cap)

Zero, negative, missing, unavailable metadata, or a failed account-currency calculation is not silently accepted. The final gate is rerun immediately before market placement, pending placement, and plan rebuild/relaxation.

Configured input defaults were preserved:

- InpMaxTotalRiskEnable = false
- InpMaxTotalRiskPct = 3.0
- InpMaxTotalRiskMoney = 30.0
- InpRequireAggregateRiskCapLive = false
- InpRiskFactorGateEnable = false
- InpRiskFactorPolicyRequiredLive = false

No current live startup log was supplied, so equity/balance base and effective live money cap are not fabricated. With the preserved defaults, aggregate and factor gates are implemented but not active. If InpRequireAggregateRiskCapLive is enabled and no valid enabled cap exists, startup fails closed.

Fixed factors include symbol, asset class, session, FX base/quote currency direction, configured factor, configured cluster, and same-direction macro exposure. Proposed and existing contributions use original-SL risk percentage. The versioned policy currently defines caps but remains inactive unless enabled. Covariance/MCR remains shadow-only with no authority because clean synchronized return evidence is unavailable.

## 15. Commission estimation methodology

MQL selects broker history over the configured lookback and groups matching magic/symbol deals by exact DEAL_POSITION_ID. It sums absolute commission, swap, and fee across the position lifecycle and divides by entry volume. It then calculates:

- median round-turn cost per lot
- configured stressed percentile, bounded to 0.50 through 0.999
- sample size
- history window
- source

If sample count is below InpBrokerCostMinSamples, history selection fails, or costs are absent, the explicit configured nonzero fallback is used. Zero cost is never fabricated.

Python's BrokerCostSample contract additionally supports observed spread and slippage where those measurements are available. Predicted cost, actual realized cost, signed prediction error, and absolute error are retained for calibration.

Observed live broker sample count and source are unavailable in this operation; only deterministic behavior is verified.

## 16. Management state machine and transition table

States:

- HEALTHY
- WARNING
- THESIS_INVALID
- EXITED

| From | Allowed to | Typical action |
|---|---|---|
| HEALTHY | WARNING, THESIS_INVALID, EXITED | warn, configured invalidation action, terminal bookkeeping |
| WARNING | HEALTHY, THESIS_INVALID, EXITED | recover, configured invalidation action, terminal bookkeeping |
| THESIS_INVALID | EXITED | finish the one selected action and exit state |
| EXITED | none | none |

Action ID:

    SHA256(position_id, from, to, reason, management_version)

The state stores previous/current state, transition time/reason, evidence, action, action ID, version, and executed action IDs. Repeated timer/tick/transaction/restart attempts with the same action ID are skipped. The current active policy is preserved unless clean exact OOS evidence authorizes an experiment. Full-exit, partial-exit, and original-SL/TP alternatives remain separate shadow experiments.

## 17. Invalidation confirmation modes

Supported modes:

1. CLOSED_M1_BAR
2. CLOSED_ENTRY_TF_BAR
3. N_SECOND_PERSISTENCE
4. PRICE_SPREAD_BUFFER
5. TICK, only when explicitly selected

Persisted evidence includes mode, reference timeframe, trigger level, spread, buffer, first breach time, confirmed time, and confirming bar.

The deployed v2 optional policy resolves by asset class from invalidation_policy.v1.json. When disabled, it preserves the existing global input values. When enabled but malformed/incompatible, it fails closed. The provided class values are deliberately global-equivalent; they do not fabricate optimized class behavior.

## 18. Opening and closing volume behavior

NormalizeOpeningVolume:

- floors to broker step
- enforces broker min/max
- may raise to broker minimum only when risk remains valid
- otherwise rejects

NormalizeClosingVolume:

- bounds requested close by current position volume
- floors to broker step
- never rounds upward beyond requested volume
- never closes more than current volume
- skips below-minimum partials unless the explicit close-all policy is enabled
- avoids leaving an invalid tiny residual

Structured results retain requested/normalized volume, action, reason, residual, min, step, and max. Tests cover partial TP, penalty reduction, tiny residual, minimum lot, floating boundaries, and full close.

## 19. Counterfactual methodology and ambiguity handling

Actual managed result uses exact realized deals and costs. The counterfactual uses immutable original entry, original SL, original target policy, volume, cost, and horizon.

The deployed v2 behavior:

1. At an early managed exit, append a PENDING counterfactual record.
2. Continue evaluating M1 path data across restart.
3. Resolve only at original stop, original target, or configured horizon/session endpoint.
4. Append an immutable management_counterfactual_updates.jsonl event.
5. Merge updates during analysis without rewriting the closed-trade record.

If stop and target occur within one bar and tick order is unavailable:

- status = AMBIGUOUS
- no outcome is guessed
- management_alpha is null
- policy-selection eligibility is false

Otherwise:

    management_alpha =
        actual_managed_result_r
        - counterfactual_original_sl_tp_result_r

No future path is fabricated at the early close.

## 20. Per-symbol session implementation

The MQL layer queries broker trade sessions per symbol/day, derives session open/close, no-entry start, flatten start, market-closed state, and next tradable time, then logs symbol_session_schedule.

Entries are blocked in the symbol-specific no-entry window. Flattening starts before the symbol's broker close. Retry state stores attempts, failures, last retcode, last attempt, and exposure hash; a repeated close is allowed only after exposure changes or retry time elapses.

The deterministic tests prove different symbols can have different close windows. Holiday/early-close and real broker-session behavior remain PARTIAL_OPERATIONAL_PROOF_REQUIRED.

## 21. Shadow-candidate ledger schema

Initial immutable event:

- schema/version
- candidate ID/hash and execution fingerprint
- decision stage and rejection reason
- setup taxonomy
- objective pre-entry features
- cost estimate/source
- normalized FVG components
- session/killzone
- data-quality/trackability status
- can_trade = false
- authority = RECOMMENDATION_ONLY
- record hash

Staged v2 later outcome event:

- candidate ID/hash
- status: RESOLVED_TARGET, RESOLVED_STOP, RESOLVED_HORIZON, AMBIGUOUS, or UNTRACKABLE
- hypothetical result R
- MFE/MAE R
- time-to-event
- ambiguity reason
- evaluated time
- immutable event hash

The ledger cannot place an order or affect current risk. Promotion to a deterministic veto requires a registered clean chronological OOS experiment and a separately approved policy.

## 22. Normalized-FVG formula and policy modes

Formula:

    normalized_minimum_price = max(
        minimum_ticks * point,
        current_spread_price * spread_multiple,
        ATR_price * ATR_fraction,
        robust_session_noise_price * session_noise_fraction
    )

Session noise is a robust median of recent lower-timeframe intrabar ranges rather than a mean sensitive to outliers.

Modes:

- OFF: preserve legacy authority; record measurements.
- SHADOW: calculate and persist pass/fail but do not block. This remains the default.
- ENFORCE: block only with a compatible versioned asset-class policy and sufficient clean class sample.

Persisted fields include raw width in price/ticks, every component, selected maximum, shadow pass, enforced pass, mode, asset class, policy version/source, sample size, minimum sample, evidence sufficiency, and authority reason.

The deployed policy is explicitly SHADOW_ONLY and UNAVAILABLE_NO_CLEAN_ASSET_CLASS_OUTCOMES. Every class has clean_sample_size 0 and global-equivalent component values. It cannot justify ENFORCE and does not silently tune symbols.

## 23. Tests and exact commands

Active Python compile:

    python -m py_compile ai_gate.py runtime_governance.py tools\analyze_ai_trade_outcomes.py tools\verify_v_next_controls.py

Active unit suite against the exact terminal include root:

    $env:PO3_MQL_INCLUDE_ROOT="C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex"
    python -m unittest discover -s tests -p "test_*.py"

Golden validators:

    python tests\run_golden_validators.py

Active verifier and exact-mirror MetaEditor compile:

    python tools\verify_v_next_controls.py --mql-root "C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5"

The verifier invokes MetaEditor64.exe with a temporary exact mirror:

    MetaEditor64.exe /compile:<temporary-mirror>\PO3_AIGate_ScannerEA.mq5 /log:mql_compile_v_next_verify.log

Real ledger audit:

    python tools\audit_trade_ledger.py --input "C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\trade_results" --output-dir ".codex_validation\ledger_audit_repeatability_20260717"

Hash comparison:

    Get-FileHash -Algorithm SHA256 -LiteralPath <active-or-staged-file>

## 24. Passed, failed, and skipped tests

| Check | Result | Evidence |
|---|---|---|
| Active py_compile | PASS | exit code 0 |
| Initial active unit suite | FAILED, then repaired | 5 FileNotFoundError results exposed a staging-only MQL path in test_governance_contracts.py |
| Final active unit suite | PASS | Ran 89 tests in 1.028s, OK |
| Golden validators | PASS | case_count=10, mismatch_count=0 |
| Repository verifier | PASS | all v-next controls passed |
| Staged MetaEditor exact-mirror compile | PASS | 0 errors, 0 warnings |
| Active real ledger audit | PASS as audit, data status QUARANTINED | 619 total, 0 clean, 436 quarantined, 183 unattributed |
| Latest active deployment | PASS | 14 active/staged hashes match; backup retained |
| Latest active-source MetaEditor compile | PASS | 0 errors, 0 warnings, 124462 msec |
| Real demo market fill | SKIPPED | no broker order authorized |
| Real demo pending fill | SKIPPED | no broker order authorized |
| Runtime repeatability measurement | SKIPPED/UNAVAILABLE | no artifact |
| Runtime hierarchical prior activation | SKIPPED/MISSING | no artifact and zero clean rows |

## 25. Remaining risks and TODOs

1. Capture one controlled demo market fill and one controlled demo pending fill, then run verify_broker_fill_capture.py.
2. Repair/reconcile old ledger identity and scale failures. Do not use the current 619 rows for learning.
3. Generate hierarchical priors only after clean exact-attribution rows exist.
4. Enable shadow-repeat sampling long enough to exceed the minimum exact-contract sample, then review authority status.
5. Aggregate and factor gates are disabled by preserved inputs. Provider-grade deployment must explicitly enable a valid cap/policy; no setting was silently changed here.
6. Netting virtual sub-position ledger is still unavailable. Netting remains governed by the explicit one-position-per-symbol fallback or startup rejection.
7. Broker session holiday/early-close and flatten retry need real broker proof.
8. Management alpha and normalized-FVG enforcement must remain non-authoritative until clean chronological OOS evidence exists.

## 26. Confirmation of unchanged unrelated strategy settings

Confirmed:

- No set file changed.
- No active private .env was edited or disclosed.
- No PO3/FVG concept definition changed.
- No strategy-family default changed.
- No setup threshold changed.
- No RR floor or target-arbitration rule changed.
- No model name changed.
- No Flex setting changed.
- No symbol universe changed.
- No tester mode changed.
- No unrelated execution behavior changed.
- No existing aggregate/factor gate default was silently enabled.
- Normalized FVG defaults to SHADOW.
- Asset-class invalidation policy defaults disabled and uses global-equivalent values if explicitly enabled.

## 27. Requirement-to-code-and-test traceability

| Point | Main code locations | Proving test or evidence | Status |
|---|---|---|---|
| 1 | decision_integrity.py, ai_gate.py, Types.mqh | mandatory score-field tests | VERIFIED_ACTIVE |
| 2 | decision_integrity.py, ai_gate.py | legacy authority test | VERIFIED_ACTIVE |
| 3 | candidate assessments in Python/MQL | independent-candidate tests | VERIFIED_ACTIVE |
| 4 | MQL selected-candidate gate | no-rule-best test | VERIFIED_ACTIVE |
| 5 | decision_integrity fingerprints | rounding/material-change tests | VERIFIED_ACTIVE |
| 6 | strict validators in Python/MQL | every missing-field test | VERIFIED_ACTIVE |
| 7 | decision_quality_tier gates | degraded/old-cache tests | VERIFIED_ACTIVE |
| 8 | decision state handling | ABSTAIN market/pending test | VERIFIED_ACTIVE |
| 9 | multiplier parsers/sizing | zero/None tests | VERIFIED_ACTIVE |
| 10 | TradeEngine identity lifecycle | synthetic identity tests, real proof absent | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 11 | TradeEngine.Init account mode | account-mode verifier | VERIFIED_ACTIVE |
| 12 | TradeEngine, StateStore, PenaltyWatcher | recovery/identity tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 13 | governance_contracts, completion gate | real 619-row audit | VERIFIED_ACTIVE |
| 14 | outcome metrics | outcome-direction tests | VERIFIED_ACTIVE |
| 15 | deal accounting | commission/partial tests | VERIFIED_ACTIVE |
| 16 | report metrics | metric independence tests | VERIFIED_ACTIVE |
| 17 | taxonomy resolvers | precedence tests | VERIFIED_ACTIVE |
| 18 | pre-AI unknown gate | unknown taxonomy tests | VERIFIED_ACTIVE |
| 19 | suppression governance | all-AND/dirty-ledger tests | VERIFIED_ACTIVE |
| 20 | bootstrap/purged folds | chronology tests | VERIFIED_ACTIVE |
| 21 | experiment_registry.py | holdout contamination test | VERIFIED_ACTIVE |
| 22 | feature lineage | leakage/double-count tests | VERIFIED_ACTIVE |
| 23 | migration aliases | no empirical authority tests | VERIFIED_ACTIVE |
| 24 | calibration_pipeline.py | shadow calibration tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 25 | Python/MQL/cache tier | full-tier-only tests | VERIFIED_ACTIVE |
| 26 | runtime_governance and ai_gate | repeatability partition/authority tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 27 | hierarchical prior builder | four-versus-400 and dirty-data tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 28 | module-root prior audit | CWD independence and mandatory missing test | VERIFIED_ACTIVE |
| 29 | AggregateInitialRiskGate | open/pending/partial original-risk tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 30 | EffectiveAggregateRiskCap | stricter percentage/money test | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 31 | RiskFactorGate and policy | factor/cluster/session/macro tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 32 | BrokerCostEstimatePerLot | median/p95/fallback/error tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 33 | management transition functions | transition/idempotency/restart tests | VERIFIED_ACTIVE |
| 34 | management experiment authority | evidence/holdout gate tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 35 | opening/closing normalizers | volume boundary suite | VERIFIED_ACTIVE |
| 36 | invalidation resolver and active MQL policy wiring | asset policy, wick/spread/persistence tests | REPAIRED_AND_DEPLOYED |
| 37 | price-path contract and active persistent queues | pending/horizon/ambiguous tests | REPAIRED_AND_DEPLOYED |
| 38 | symbol session and flatten retry | different-session/retry tests | PARTIAL_OPERATIONAL_PROOF_REQUIRED |
| 39 | active shadow pending outcomes | immutable/non-trading/outcome static tests | REPAIRED_AND_DEPLOYED |
| 40 | active normalized FVG v2 | max/mode/insufficient-sample tests | REPAIRED_AND_DEPLOYED |

## 28. WHAT I NEED CHATGPT TO REVIEW NEXT

1. Review whether UNAVAILABLE repeatability should remain non-blocking, or whether live-forward trading should require a minimum repeatability artifact for each exact model/prompt/schema/tier contract.
2. Review the desired operator policy for InpRequireAggregateRiskCapLive and InpRiskFactorPolicyRequiredLive. They were intentionally not enabled here.
3. Review the deployed invalidation asset-class file. It currently preserves global-equivalent values and should not be treated as calibrated.
4. Review the counterfactual horizon definition, including whether session close or a fixed 1,440-minute horizon should be authoritative for each family.
5. Review the clean-ledger recovery strategy before any prior, suppression, calibration, or management experiment can gain authority.
6. Review one future controlled market fill and one pending fill end-to-end: request fingerprint, decision, final risk gates, order, deal, position ID, completion ledger, and exact attribution verifier.
