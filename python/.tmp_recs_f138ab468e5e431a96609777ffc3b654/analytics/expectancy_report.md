# Expectancy Report

Trades analyzed: 0
Avg R: 0.000
R 90% CI: [0.000, 0.000]

## Data Quality

- Result files found: 636
- Records analyzed after filters: 0
- Records ignored: 636
- IDs recovered from trade metadata: 236
- Ignored missing_entry_branch: 282
- Ignored missing_planned_entry: 282
- Ignored missing_planned_sl: 282
- Ignored missing_trade_id: 400
- Ignored zero_realized_r_with_nonzero_pnl: 282

## Input Recommendations

- Mode: advisory_only
- Auto-apply: False
- critical data_quality: block_input_optimization -> Do not tune performance inputs from this sample. Collect fresh closed trades after the analytics metadata fix. [inputs: trade_result_*.json, position_id, planned_entry, planned_sl, entry_branch, realized_r]

## Governance

- Evidence passed: False
- Walk-forward passed: False
- Change-rate passed: True
- Auto-activate allowed: False

## Context Policies

- Not enough supported buckets

## Subtype Proof

- Not enough supported subtypes

## Overfitting

- Unstable bucket count: 0
- Global avg train/test gap: 0.000

## Adaptive Readiness

- Activate adaptive policy: False
- total_trades_ge_500: False
- family_trades_ge_50: False
- symbol_trades_ge_30: False
- profit_factor_after_costs_gt_1_10: False
- walk_forward_positive_fold_rate_ge_60pct: False

## Family Expectancy

- No family samples yet

## Symbol Expectancy

- No symbol samples yet
