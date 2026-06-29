# Runtime inputs contract

Every EA AI request must include:

- top-level `runtime_input_hash`
- top-level `runtime_inputs`

Python live decisions use the payload runtime inputs only. They do not read
`.mqh` defaults.

## Critical live fields

- `runtime_input_hash`
- `trade_only_killzones`
- `enable_asia_killzone`
- Asia/London/New York killzone hour/minute start/end inputs
- all FVG/family suppression inputs
- `reject_synthetic_fallback_after_crossed_obstacle`
- `require_ai_target_arbitration_on_obstacle`
- `hard_reject_crossed_obstacle_target`
- `allow_ai_to_use_liquidity_target_behind_minor_blocker`
- `allow_partial_before_obstacle`
- `blocker_kill_severity`
- `blocker_major_severity`
- `blocker_minor_max_severity`
- `execution_reject_cost_r`
- `execution_reduce_risk_cost_r`
- `micro_scalp_max_cost_frac_of_planned_r`
- `require_displacement`
- `allow_synthetic_rr_target`
- `min_live_rr2`
- `standard_trade_liquidity_rr_floor`
- `max_target_atr_mult`
- `max_target_adr_frac`
- `obstacle_reject_r`
- `use_ai`
- `ai_strict`
- `min_ai_score_trend`
- `ai_score_full_po3`
- `ai_score_micro_po3`
- `ai_score_continuation`
- `ai_score_range`
- `ai_score_failed_breakout`
- `global_ai_score_as_hard_floor`
- `min_ai_confidence`
- `use_snapshot_ai`
- `require_snapshots`
- `exclusive_trading_enabled`
- `virtual_ledger_mode`
- `backend_pnl_mode`

If a live payload is missing critical fields and
`AI_REJECT_ON_MISSING_RUNTIME_INPUTS_LIVE=true`, Python rejects before OpenAI
with `runtime_inputs_missing_live_reject`.

## Hard pre-gate blocks

The Python gate rejects before OpenAI for objective cases including non-killzone
trades when killzone-only mode is enabled, suppressed families/branches, stale
or touched continuation FVGs when configured, execution cost over the reject
ceiling, micro scalp cost over cap, synthetic fallback crossing opposing
imbalance, invalid RR, invalid target, target already reached, invalid spread,
missing required displacement, no valid entry, no valid stop, max-position
state, duplicate-symbol state, and portfolio risk-cap state.

MQL enforces the same hard blocks before AI candidate submission and again with
`CanPlaceOrderHardSafety()` immediately before order placement/modification.

## Target Arbitration Payload

When a real liquidity target exists behind an opposing obstacle and
`require_ai_target_arbitration_on_obstacle=true`, the EA must send
`target_candidates` in the request. Python does not read static MQL defaults for
this decision.

Required `target_candidates` shape:

- `arbitration_required`
- `liquidity_target.available/model/tp2/rr2/blocked_by_obstacle`
- `capped_before_obstacle.available/model/tp2/rr2`
- `synthetic_rr_fallback.available/model/tp2/rr2/crosses_obstacle`
- `obstacle_kind`
- `obstacle_distance_r`
- `obstacle_strength_features`

Python hard-pre-gate still rejects a final `synthetic_rr_fallback` through a
crossed opposing imbalance, but it allows the request to reach AI if arbitration
is explicitly required and a real liquidity target candidate is present.

The AI response returns `target_arbitration` and flat MT5 fields:

- `chosen_target_model`
- `chosen_tp1`
- `chosen_tp2`
- `chosen_rr2`
- `rejected_target_models`
- `target_blocker_kind`
- `target_blocker_severity`
- `target_blocker_class`
- `target_blocker_is_trade_killer`
- `target_decision_reason`

## Family AI Thresholds

`InpMinAiScoreTrend` is a fallback only by default. Known families use the
category-specific threshold sent in `runtime_inputs`:

- full PO3: `ai_score_full_po3`
- micro PO3 and micro BISI/SIBI edge: `ai_score_micro_po3`
- continuation: `ai_score_continuation`
- range re-entry: `ai_score_range`
- failed breakout/reclaim: `ai_score_failed_breakout`
- unknown family: `min_ai_score_trend`

`global_ai_score_as_hard_floor=false` means the family threshold is final.
`global_ai_score_as_hard_floor=true` makes the effective threshold:

```text
max(min_ai_score_trend, family_threshold)
```

Threshold decisions log and return:

- `ai_score_threshold`
- `ai_threshold_source`
- `global_ai_score_as_hard_floor`
- `ai_threshold_passed`
- `ai_reject_reason`

The rejection code for a score below the mapped family threshold is
`ai_score_below_family_threshold`.
