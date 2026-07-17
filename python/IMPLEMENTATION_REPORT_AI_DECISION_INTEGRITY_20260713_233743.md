# AI Decision Integrity Implementation Report

Generated: 2026-07-13 23:37:43 UTC  
Scope: Python AI gate, active MetaTrader 5 EA/includes, decision cache, persistence, execution attribution, and analytics compatibility.

## 1. Executive summary

The candidate-to-execution contract is now fail-closed and candidate-specific.

- Every submitted candidate receives its own complete AI assessment.
- Only the assessment matching `candidate_index`, `candidate_id`, `candidate_hash`, and request fingerprint may be selected.
- The old blended score and synthetic agreement confidence remain diagnostic only and have no entry, sizing, or selection authority.
- Calibration is explicitly unavailable: probability/expected-R fields are `null` and cannot approve a trade.
- `APPROVE`, `REJECT`, and `ABSTAIN` are distinct; `ABSTAIN` cannot trade.
- Incomplete, malformed, degraded, old-cache, duplicated, non-finite, or out-of-range responses cannot trade.
- A watchlist refresh or same-story replacement cannot inherit another candidate's AI decision.
- Final execution is bound to the assessed plan with explicit price/R/cost tolerances.
- `0.0` risk remains zero and is a hard rejection, never a default-to-full-risk path.
- Market and pending fills are attributed through the exact order -> deal -> `DEAL_POSITION_ID` -> broker position chain. Unresolved identity is quarantined and excluded from analytics.
- No strategy threshold, RR floor, family default, `.set` value, model name, Flex setting, or portfolio-risk default was changed.

Final verification:

- Python compile: pass.
- Python unit tests: 30/30 pass.
- Repository verifier: all controls pass.
- MetaEditor: `0 errors, 0 warnings`.

## 2. Changed files

### Python source and tests

1. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\ai_gate.py`
2. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\decision_integrity.py` (new)
3. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\expectancy_report.py`
4. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\tools\analyze_ai_trade_outcomes.py`
5. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\tools\verify_v_next_controls.py`
6. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\tests\test_ai_gate_logic.py`
7. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\tests\test_decision_integrity.py` (new)

### Active MQL source

1. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\Config.mqh`
2. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\Types.mqh`
3. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\JsonLite.mqh`
4. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\AIGateBridge.mqh`
5. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\TradeEngine.mqh`
6. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\StateStore.mqh`
7. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\PenaltyWatcher.mqh`
8. `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Experts\MT5_PO3_Codex\PO3_AIGate_ScannerEA.mq5`

### Generated verification artifacts

1. `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\mql_compile_ai_decision_integrity.log`
2. This report.

No `.set` file was changed. The active `.env` was not read or modified for this task.

## 3. Functions, classes, and structures changed

### Python

- `decision_integrity.py`
  - `SchemaValidationResult`, `FingerprintTolerance`, `BrokerIdentityResolution`
  - `validate_candidate_assessment()`
  - `validate_decision_envelope()`
  - `canonical_execution_components()`
  - `candidate_hash()`, `execution_fingerprint()`, `assessed_execution_fingerprint()`
  - `material_execution_changes()`
  - `resolved_risk_multiplier()`, `response_can_trade()`
  - `validate_broker_execution_identity()`
- `ai_gate.py`
  - strict `CandidateAssessment`, `AIGateEnvelope`, target-arbitration, and veto models in `_score_setup_openai()`
  - `Decision` canonical fields and fail-closed defaults
  - `_decision_cache_signature()` and `_cached_decision_schema_miss_reason()`
  - `AIDecisionCache.lookup()` / `store()`
  - `_apply_family_ai_threshold_gate()`
  - `_degraded_non_trading_decision()`
  - `_mql_tester_cache_skip_reason()` and `_export_mql_tester_replay_cache()`
  - response serialization in `process_one()`
- `expectancy_report.py`
  - `_llm_quality_score_bucket()` and explicit read-only migration aliases
- `tools/analyze_ai_trade_outcomes.py`
  - canonical quality/self-confidence labels and legacy input aliases

### MQL

- `Types.mqh`
  - expanded `AiDecision` and `TradePlan` identity, schema, score, calibration, fingerprint, and broker-attribution fields
- `JsonLite.mqh`
  - strict typed/key-count parsing used by decision validation
- `AIGateBridge.mqh`
  - `RuntimeInputHash()` and tester-independent `DecisionInputHash()`
  - `RuntimeInputsJson()` / request candidate identity payload
  - strict candidate and target-arbitration validators
  - `SendRequestCandidates()` and `TryReadDecision()`
- `TradeEngine.mqh`
  - `_CandidateHash()`, `_ExecutionFingerprint()`, `_PrepareDecisionIdentity()`
  - `_AssessedFingerprintFromDecision()`, `_DecisionAssessmentsMatchGroup()`
  - `_ExecutionFingerprintWithinTolerance()`
  - `ProcessPendingAI()` strict selection/authority flow
  - `_TrySameStoryNewEntry()` and `_RefreshWatchlistPlan()` no-assessment-transfer guards
  - final placement hard gates and sizing multiplier resolution
  - `_ResolveExactExecutionIdentity()`, `_QuarantineExecutionIdentity()`
  - `_LoadAndResolvePositionMeta()` and restart recovery quarantine
  - `HandleTradeTransaction()` pending-fill attribution
- `StateStore.mqh`
  - strict schema/identity persistence and recovery pruning
- `PenaltyWatcher.mqh`
  - exact verified metadata requirement
- `PO3_AIGate_ScannerEA.mq5`
  - forwards `OnTradeTransaction` to the engine

## 4. Before versus after

| Requirement | Before | After |
|---|---|---|
| Score names | `score`/`confidence` mixed several meanings | Canonical six-field score contract; aliases labeled migration-only |
| Synthetic confidence | Agreement formula could appear authoritative | Preserved only as `legacy_agreement_confidence`; zero trade/sizing/selection authority |
| Candidate assessment | One group decision could govern several candidates | One complete assessment per candidate in a single strict array |
| Alternative substitution | Same-story refreshed branch could copy `p.ai` | Assessed replacement is rejected and must re-enter scanning for fresh AI |
| Plan identity | Candidate choice was mainly index-based | Immutable candidate ID/hash plus assessed/final fingerprints |
| Missing fields | Some parser paths supplied defaults | Mandatory fields are validated in Python and MQL; failures are non-trading |
| Minimal fallback | Minimal JSON could look like a decision | Classified `DEGRADED_NON_TRADING`; cannot stage/watchlist/order |
| Uncertainty | Binary allow/reject only | Explicit `ABSTAIN`, always no trade |
| Zero risk | Some generic fallback patterns risked zero-to-one conversion | Missing is invalid; zero rejects; `(0,1)` reduces; `1` normal |
| Position attribution | Legacy metadata could be recovered ambiguously | Exact order/deal/position chain; unresolved records quarantined |

## 5. Decision schema and complete minimal example

### Request

```json
{
  "id": "1780000000_GOLD_1001",
  "mode": "live",
  "symbol": "GOLD",
  "runtime_input_hash": "193447120",
  "decision_input_hash": "718220145",
  "runtime_inputs": {
    "engine_input_schema": "po3-fvg-ai-risk-lineage-20260714-decision-integrity",
    "ai_decision_schema_version": "20260714_ai_decision_integrity_v1",
    "ai_target_arbitration_schema_version": "20260714_target_fingerprint_v3",
    "ai_prompt_contract_version": "20260714_candidate_integrity_v3",
    "runtime_input_hash": "193447120",
    "decision_input_hash": "718220145",
    "min_llm_quality_score_trend": 7.0,
    "llm_quality_score_full_po3": 6.8,
    "fallback_rr2": 1.7,
    "min_live_rr2": 1.05,
    "max_target_atr_mult": 5.0,
    "max_target_adr_frac": 0.8
  },
  "candidates": [
    {
      "candidate_index": 0,
      "candidate_id": "GOLD|FULL|1710000000|fvg_mid|2301.50",
      "candidate_hash": "5FFC6A62A29DF6FE",
      "request_execution_fingerprint": "921EA30EDC079382",
      "symbol": "GOLD",
      "symbol_digits": 2,
      "symbol_tick_size": 0.01,
      "direction": "buy",
      "is_buy": true,
      "setup_code": "FULL",
      "model_code": "FULL",
      "setup_id": "GOLD|FULL|1710000000",
      "fvg_id": "GOLD|fvg|1710000180|2300.00|2303.00",
      "setup_family": "full_po3",
      "entry_branch": "fvg_mid",
      "source_t_sweep": 1710000000,
      "source_t_disp": 1710000060,
      "source_t_bos": 1710000120,
      "fvg_lower": 2300.0,
      "fvg_upper": 2303.0,
      "entry_est": 2301.5,
      "sl": 2296.5,
      "tp1": 2306.5,
      "tp2": 2311.5,
      "net_rr": 2.0,
      "spread_r": 0.03,
      "slippage_r": 0.01,
      "execution_cost_r": 0.05,
      "target_source": "liquidity",
      "target_model": "liquidity_target",
      "obstacle_kind": "",
      "obstacle_tf": "",
      "obstacle_price": 0.0,
      "decision_input_hash": "718220145",
      "strategy_schema_version": "po3-fvg-ai-risk-lineage-20260714-decision-integrity"
    }
  ]
}
```

### Response

```json
{
  "id": "1780000000_GOLD_1001",
  "decision_schema_version": "20260714_ai_decision_integrity_v1",
  "response_quality": "FULL_STRUCTURED",
  "mandatory_fields_complete": true,
  "missing_mandatory_fields": [],
  "invalid_mandatory_fields": [],
  "decision_state": "APPROVE",
  "selected_candidate_id": "GOLD|FULL|1710000000|fvg_mid|2301.50",
  "selected_candidate_hash": "5FFC6A62A29DF6FE",
  "request_execution_fingerprint": "921EA30EDC079382",
  "assessed_execution_fingerprint": "1EEF20DA00D83AC6",
  "selected_target_identity": "liquidity_target",
  "selected_target_price": 2311.5,
  "assessed_entry": 2301.5,
  "assessed_sl": 2296.5,
  "assessed_tp1": 2306.5,
  "assessed_tp2": 2311.5,
  "allow": true,
  "raw_allow": true,
  "chosen_index": 0,
  "rule_score": 7.4,
  "llm_quality_score": 8.0,
  "blended_legacy_score": 7.568,
  "legacy_agreement_confidence": 0.85,
  "llm_self_reported_confidence": 0.68,
  "calibrated_win_probability": null,
  "expected_net_r": null,
  "oos_predicted_probability": null,
  "calibration_bucket": "",
  "calibration_sample_size": 0,
  "calibration_lower_bound": null,
  "calibration_upper_bound": null,
  "calibration_model_version": "",
  "calibration_data_window_start": "",
  "calibration_data_window_end": "",
  "calibration_available": false,
  "reasons": "independent candidate assessment passed",
  "decision_source": "llm_full_structured",
  "decision_id": "1780000000_GOLD_1001:llm_full_structured:1",
  "rejection_codes": [],
  "narrative_state": "audited",
  "invalidation_risks": [],
  "missing_confirmations": [],
  "suggested_risk_multiplier": 0.5,
  "model_version": "po3_ai_gate_integrity_v1",
  "llm_quality_score_threshold": 6.8,
  "llm_quality_threshold_source": "llm_quality_score_full_po3",
  "global_llm_quality_as_hard_floor": false,
  "llm_quality_threshold_passed": true,
  "llm_quality_reject_reason": "",
  "structure_quality_score": 8.2,
  "entry_timing_score": 7.7,
  "follow_through_probability": 0.7,
  "invalidation_risk": 0.25,
  "chop_risk": 0.2,
  "cost_risk": 0.15,
  "symbol_bucket_risk": 0.2,
  "session_bucket_risk": 0.2,
  "post_entry_failure_risk": 0.25,
  "final_trade_expectancy_score": 7.5,
  "veto_enabled": false,
  "veto_reason": "",
  "veto": {"enabled": false, "reason": ""},
  "bucket_prior_override_justification": "",
  "chosen_target_model": "liquidity_target",
  "chosen_tp1": 2306.5,
  "chosen_tp2": 2311.5,
  "chosen_rr1": 1.0,
  "chosen_rr2": 2.0,
  "rejected_target_models": ["synthetic_rr_fallback"],
  "target_blocker_kind": "",
  "target_blocker_severity": -1.0,
  "target_blocker_class": "unknown",
  "target_blocker_is_trade_killer": false,
  "target_decision_reason": "liquidity target is valid",
  "target_blocker_severity_present": true,
  "target_blocker_class_present": true,
  "target_blocker_is_trade_killer_present": true,
  "target_decision_reason_present": true,
  "why_not_liquidity_target": "",
  "why_not_partial_before_obstacle": "not required",
  "why_not_capped_before_obstacle": "not required",
  "why_not_synthetic_fallback": "real liquidity target is feasible",
  "target_arbitration_schema_version": "20260714_target_fingerprint_v3",
  "prompt_contract_version": "20260714_candidate_integrity_v3",
  "target_comparison": {
    "liquidity_target": {"usable": true, "reason": "valid", "risk": "low", "expected_role": "tp2"},
    "partial_before_obstacle_then_liquidity": {"usable": false, "reason": "not needed", "risk": "none", "expected_role": "reject"},
    "capped_before_obstacle": {"usable": false, "reason": "no obstacle", "risk": "none", "expected_role": "reject"},
    "synthetic_rr_capped_to_max_distance": {"usable": false, "reason": "not needed", "risk": "none", "expected_role": "reject"},
    "synthetic_rr_fallback": {"usable": true, "reason": "backup only", "risk": "model risk", "expected_role": "fallback_only"}
  },
  "target_arbitration": {
    "target_arbitration_schema_version": "20260714_target_fingerprint_v3",
    "prompt_contract_version": "20260714_candidate_integrity_v3",
    "arbitration_required": false,
    "chosen_target_model": "liquidity_target",
    "chosen_tp1": 2306.5,
    "chosen_tp2": 2311.5,
    "chosen_rr1": 1.0,
    "chosen_rr2": 2.0,
    "rejected_target_models": ["synthetic_rr_fallback"],
    "blocker_kind": "",
    "blocker_severity": -1.0,
    "blocker_class": "unknown",
    "blocker_is_trade_killer": false,
    "why_not_liquidity_target": "",
    "why_not_partial_before_obstacle": "not required",
    "why_not_capped_before_obstacle": "not required",
    "why_not_synthetic_fallback": "real liquidity target is feasible",
    "target_decision_reason": "liquidity target is valid",
    "target_comparison": {
      "liquidity_target": {"usable": true, "reason": "valid", "risk": "low", "expected_role": "tp2"},
      "partial_before_obstacle_then_liquidity": {"usable": false, "reason": "not needed", "risk": "none", "expected_role": "reject"},
      "capped_before_obstacle": {"usable": false, "reason": "no obstacle", "risk": "none", "expected_role": "reject"},
      "synthetic_rr_capped_to_max_distance": {"usable": false, "reason": "not needed", "risk": "none", "expected_role": "reject"},
      "synthetic_rr_fallback": {"usable": true, "reason": "backup only", "risk": "model risk", "expected_role": "fallback_only"}
    }
  },
  "candidate_assessments": [
    {
      "candidate_index": 0,
      "candidate_id": "GOLD|FULL|1710000000|fvg_mid|2301.50",
      "candidate_hash": "5FFC6A62A29DF6FE",
      "request_execution_fingerprint": "921EA30EDC079382",
      "rule_score": 7.4,
      "llm_quality_score": 8.0,
      "blended_legacy_score": 7.568,
      "legacy_agreement_confidence": 0.85,
      "llm_self_reported_confidence": 0.68,
      "calibrated_win_probability": null,
      "expected_net_r": null,
      "oos_predicted_probability": null,
      "calibration_bucket": "",
      "calibration_sample_size": 0,
      "calibration_lower_bound": null,
      "calibration_upper_bound": null,
      "calibration_model_version": "",
      "calibration_data_window_start": "",
      "calibration_data_window_end": "",
      "calibration_available": false,
      "raw_allow": true,
      "decision_state": "APPROVE",
      "structure_quality_score": 8.2,
      "entry_timing_score": 7.7,
      "follow_through_probability": 0.7,
      "invalidation_risk": 0.25,
      "chop_risk": 0.2,
      "cost_risk": 0.15,
      "symbol_bucket_risk": 0.2,
      "session_bucket_risk": 0.2,
      "post_entry_failure_risk": 0.25,
      "final_trade_expectancy_score": 7.5,
      "veto": {"enabled": false, "reason": ""},
      "bucket_prior_override_justification": "",
      "reasons": "independent candidate assessment passed",
      "rejection_codes": [],
      "narrative_state": "audited",
      "invalidation_risks": [],
      "missing_confirmations": [],
      "selected_target_identity": "liquidity_target",
      "selected_target_price": 2311.5,
      "entry": 2301.5,
      "sl": 2296.5,
      "tp1": 2306.5,
      "tp2": 2311.5,
      "assessed_execution_fingerprint": "1EEF20DA00D83AC6",
      "suggested_risk_multiplier": 0.5,
      "model_version": "po3_ai_gate_integrity_v1",
      "target_arbitration": {
        "target_arbitration_schema_version": "20260714_target_fingerprint_v3",
        "prompt_contract_version": "20260714_candidate_integrity_v3",
        "arbitration_required": false,
        "chosen_target_model": "liquidity_target",
        "chosen_tp1": 2306.5,
        "chosen_tp2": 2311.5,
        "chosen_rr1": 1.0,
        "chosen_rr2": 2.0,
        "rejected_target_models": ["synthetic_rr_fallback"],
        "blocker_kind": "",
        "blocker_severity": -1.0,
        "blocker_class": "unknown",
        "blocker_is_trade_killer": false,
        "why_not_liquidity_target": "",
        "why_not_partial_before_obstacle": "not required",
        "why_not_capped_before_obstacle": "not required",
        "why_not_synthetic_fallback": "real liquidity target is feasible",
        "target_decision_reason": "liquidity target is valid",
        "target_comparison": {
          "liquidity_target": {"usable": true, "reason": "valid", "risk": "low", "expected_role": "tp2"},
          "partial_before_obstacle_then_liquidity": {"usable": false, "reason": "not needed", "risk": "none", "expected_role": "reject"},
          "capped_before_obstacle": {"usable": false, "reason": "no obstacle", "risk": "none", "expected_role": "reject"},
          "synthetic_rr_capped_to_max_distance": {"usable": false, "reason": "not needed", "risk": "none", "expected_role": "reject"},
          "synthetic_rr_fallback": {"usable": true, "reason": "backup only", "risk": "model risk", "expected_role": "fallback_only"}
        }
      }
    }
  ]
}
```

`score` and `confidence` may still be emitted as explicit migration aliases, mapped only to `llm_quality_score` and `llm_self_reported_confidence`. They have no independent authority.

## 6. Candidate-hash algorithm

MQL is the candidate identity source of truth. It:

1. Uppercases symbol and normalizes direction.
2. Canonicalizes prices to symbol tick size and digits.
3. Concatenates symbol, direction, candidate/setup/FVG IDs, setup code/family/branch, source sweep/displacement/BOS times, FVG bounds, entry, SL, TP1, TP2, target source/model, obstacle identity/TF/price, decision-input hash, strategy schema, and decision schema.
4. Computes two 32-bit FNV-1a hashes with independent seeds.
5. Concatenates both as a 16-character uppercase hexadecimal identity.

Python mirrors this for tests and validation, but it does not replace the MQL-provided candidate hash. Candidate hashes are unique within a request; duplicates or unknown hashes reject the entire response.

## 7. Execution fingerprint and tolerances

The canonical execution fingerprint includes:

- symbol/direction
- candidate ID/hash
- setup code/family/entry branch
- source sweep/displacement/BOS times
- entry/SL/TP1/TP2
- net RR, spread R, slippage-estimate R, execution-cost R
- target source/model
- obstacle kind/TF/price
- decision-input hash and strategy/decision schema

Prices are rounded to symbol tick size/digits before hashing. The assessed fingerprint additionally binds the AI-selected target identity/price and exact assessed levels.

Material-change comparison uses:

- price components: `max(2 ticks, 0.05R)`
- net RR: `0.05R`
- spread, slippage, and execution cost: `0.02R`
- all categorical identities/times/hashes: exact equality

Small canonical rounding differences can pass. A material entry, stop, target, RR, cost, category, source time, runtime decision hash, or schema change logs all changed components and rejects with `execution_fingerprint_mismatch`. A different candidate hash always rejects.

## 8. Exact broker resolution flow

### Market order

1. Persist the pre-send trade key, candidate identity, comment, and final fingerprint.
2. Send through `CTrade`.
3. Read `ResultOrder()` and `ResultDeal()`.
4. Select the exact deal and verify its order ticket, magic, symbol, entry type, direction, and volume.
5. Select the exact order and verify magic, symbol, comment, and requested volume.
6. Read `DEAL_POSITION_ID`.
7. Find the open broker position whose `POSITION_IDENTIFIER` exactly equals that ID.
8. Verify position magic, symbol, direction, comment, volume, and open-time tolerance.
9. Persist order ticket, deal ticket, position identifier/ticket, trade key, candidate hash, and execution fingerprint.

### Pending fill

`OnTradeTransaction` receives the fill deal, resolves its order and `DEAL_POSITION_ID`, loads the exact pending metadata, performs the same verification chain, and only then marks the position attributable.

### Failure

No symbol-only or first/last-position guess is used. Failure writes an execution-identity reconciliation JSON, logs `[execution_identity_quarantine]`, marks analytics unavailable, and excludes the record from adaptation. Broker-level safety controls can still see the position, but strategy attribution is not invented.

### Account modes

- Hedging: exact deal/position identity is mandatory.
- Netting: the EA logs the limitation and enforces one managed trade per symbol because no independent virtual sub-position ledger currently exists.

## 9. Cache/schema migration

Current versions:

- `decision_schema_version=20260714_ai_decision_integrity_v1`
- `target_arbitration_schema_version=20260714_target_fingerprint_v3`
- `prompt_contract_version=20260714_candidate_integrity_v3`

Cache behavior:

- Full structured fresh responses and caches of full structured responses may trade.
- Cache lookup revalidates every candidate assessment, candidate-hash uniqueness, selected ID/hash, assessed/request fingerprints, target identity, decision state, and exact risk multiplier.
- Old, incomplete, malformed, degraded, or version-mismatched cache rows become cache misses and cannot trade.
- Writers no longer fill missing contract versions with current versions.
- Tester workflow/debug fields remain in runtime diagnostics but are excluded from the economic decision signature, preserving RECORD_ONLY/LIVE_WAIT_DEBUG -> CACHE_ONLY replay compatibility.
- Real decision inputs, target candidates, bucket-prior hash, obstacle data, thresholds, and contract versions remain signature material.

Legacy `score`, `confidence`, `ai_score`, and `ai_confidence` are read only as explicitly labeled analytics migration aliases. They are not silently upgraded into a tradable strict decision.

## 10. Rejection reasons and logs

Primary new/strengthened reasons:

- `ai_quality_schema_incomplete`
- `degraded_ai_response_non_trading`
- `ai_abstain`
- `candidate_hash_mismatch`
- `execution_fingerprint_mismatch`
- `alternative_candidate_requires_independent_ai_assessment`
- `resolved_risk_multiplier_zero`
- `cache_miss_due_to_schema_version`
- `missing_trade_candidate_or_fingerprint_identity`
- exact identity reasons such as `deal_order_ticket_mismatch`, `deal_magic_mismatch`, `deal_symbol_mismatch`, `deal_direction_mismatch`, `deal_volume_mismatch`, `order_comment_mismatch`, `missing_deal_position_id`, `exact_position_not_found_by_deal_position_id`, and `position_open_time_mismatch`

Important logs:

```text
[decision_scores] rule_score=... llm_quality_score=... blended_legacy_score=... calibrated_win_probability=unavailable expected_net_r=unavailable llm_self_reported_confidence=...
[ai_schema_validation] valid=false decision_schema_version=... missing_fields=... invalid_fields=...
[ai_abstain] candidate_id=... candidate_hash=... reason=...
[setup_reject] reject_stage=decision_integrity reject_reason=candidate_hash_mismatch ...
[setup_reject] reject_stage=decision_integrity reject_reason=execution_fingerprint_mismatch changed_components=...
[execution_identity] order_ticket=... deal_ticket=... position_id=... trade_key=... candidate_hash=... verified=true
[execution_identity_quarantine] reason=... order_ticket=... deal_ticket=... candidate_hash=...
```

## 11. Tests added or strengthened

`tests/test_decision_integrity.py` covers:

1. independent candidate assessments
2. cross-candidate binding rejection
3. no `rule_best`/same-story AI transfer
4. candidate-hash mismatch
5. request/execution-fingerprint mismatch
6. non-material rounding tolerance
7. material entry/SL/TP/RR/cost changes
8. each mandatory field removed independently
9. degraded response non-trading
10. abstention non-trading
11. zero AI multiplier
12. zero policy multiplier
13. `None` versus zero
14. exact hedging identity
15. same-symbol cross-attachment prevention
16. failed attribution quarantine
17. netting governance
18. old-cache rejection
19. strict full-cache binding
20. current-version cache with incomplete assessment rejection
21. diagnostic-only legacy scores/confidence
22. unavailable calibration enforcement
23. tester workflow signature stability
24. prompt-contract signature invalidation

The existing verifier also exercises hard gates, MQL tester cache export/replay contracts, target feasibility, bucket policies, AI vetoes, outcome analytics, and family quality thresholds.

## 12. Commands executed

```powershell
python -m py_compile ai_gate.py decision_integrity.py expectancy_report.py tools\verify_v_next_controls.py tools\analyze_ai_trade_outcomes.py tests\test_ai_gate_logic.py tests\test_decision_integrity.py
python -m unittest discover -s tests -v
python tools\verify_v_next_controls.py
python tools\verify_v_next_controls.py --skip-mql-compile
& 'C:\Program Files\MetaTrader 5\MetaEditor64.exe' '/compile:C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Experts\MT5_PO3_Codex\PO3_AIGate_ScannerEA.mq5' '/log:C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\mql_compile_ai_decision_integrity.log'
```

## 13. Actual test results

- `py_compile`: pass, no output/errors.
- `unittest discover`: `Ran 30 tests ... OK`.
- Full `verify_v_next_controls.py`: every listed v-next control passed, including its MQL compile check.
- Final post-edit `verify_v_next_controls.py --skip-mql-compile`: every non-compile control passed.
- No test failure remains.

## 14. MetaEditor compile result

Final direct compile log:

```text
Result: 0 errors, 0 warnings, 118807 msec elapsed, cpu='X64 Regular'
```

Log: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\mql_compile_ai_decision_integrity.log`

The earlier full verifier compile also returned `0 errors, 0 warnings`.

## 15. Remaining risks and incomplete areas

1. Exact broker attribution is unit-tested and compiled, but this task did not place a new live broker order. A controlled demo order is still the final integration proof for broker-specific order/deal/comment behavior.
2. Netting accounts do not yet have a virtual sub-position ledger; the EA therefore enforces one managed trade per symbol and logs the limitation.
3. Calibration remains intentionally unavailable. No win probability or expected net R is produced until a genuinely out-of-sample model is built and versioned.
4. Existing old cache/state rows are intentionally non-trading and must be regenerated under the new contract.
5. The OpenAI API key was visibly included in the IDE context supplied with this task. It was not copied into this report or read from `.env`; it should be revoked and rotated.

## 16. Unrelated strategy confirmation

No unrelated strategy threshold, setup-family default, RR threshold, target-validation rule, watchlist precheck, model name, Flex configuration, portfolio-risk default, broker execution mode, or `.set` value was changed. PO3/FVG detection logic was not modified.

## 17. Requirement mapping

| # | Requirement | Implementation location | Status | Proof |
|---:|---|---|---|---|
| 1 | Separate score concepts | `ai_gate.py`, `Types.mqh`, `AIGateBridge.mqh`, analytics scripts | Implemented | `test_legacy_scores_and_self_confidence_have_no_threshold_authority` |
| 2 | Remove synthetic confidence authority | `Decision`, family gate, MQL approval flow | Implemented | legacy-authority test plus `[decision_scores]` source checks |
| 3 | Assess candidates independently | `_score_setup_openai()`, `validate_decision_envelope()`, `_DecisionAssessmentsMatchGroup()` | Implemented | independent/cross-binding tests |
| 4 | Delete silent `rule_best` substitution | `ProcessPendingAI()`, `_TrySameStoryNewEntry()`, `_RefreshWatchlistPlan()` | Implemented | `test_invalid_selection_does_not_substitute_rule_best` |
| 5 | Assessed/final fingerprints | `decision_integrity.py`, `_CandidateHash()`, `_ExecutionFingerprintWithinTolerance()` | Implemented | deterministic, rounding, and material-change tests |
| 6 | Strict schema | Python Pydantic/shared validator and MQL strict parser | Implemented | every-field removal test; incomplete-current-cache test |
| 7 | Remove live minimal fallback | `_degraded_non_trading_decision()`, MQL response-quality gate | Implemented | degraded fallback test |
| 8 | Explicit abstention | prompt/schema, `response_can_trade()`, MQL approval gates | Implemented | abstain market/pending test |
| 9 | Preserve zero multiplier | `resolved_risk_multiplier()`, policy parsers, final MQL sizing gate | Implemented | zero AI/policy and `None` tests |
| 10 | Exact broker position | `_ResolveExactExecutionIdentity()`, `HandleTradeTransaction()`, quarantine flow | Implemented | hedging, same-symbol, quarantine, and netting tests |

## 18. WHAT I NEED CHATGPT TO REVIEW NEXT

1. Run one controlled demo market fill and one pending-order fill, then inspect `[execution_identity]` and the persisted order/deal/position metadata.
2. Confirm the broker preserves the compact comment exactly; if it truncates, keep the trade key in pre-send metadata and verify the order/deal chain still resolves without guessing.
3. Regenerate tester/live decision caches under the new schema before any CACHE_ONLY run.
4. Build calibration only from clean, identity-verified, out-of-sample closed trades. Until then, keep `calibration_available=false`.
5. Rotate the API key exposed in the IDE context before restarting Python.
