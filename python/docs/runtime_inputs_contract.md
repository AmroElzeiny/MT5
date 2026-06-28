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
