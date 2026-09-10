# Expectancy Report

Trades analyzed: 0
Avg R: 0.000
R 90% CI: [0.000, 0.000]
Virtual balance: 100000.00
Virtual net %: 0.000%

## Data Quality

- Result files found: 17
- Records analyzed after filters: 0
- Records ignored: 17
- Ignored legacy_trade_result_schema: 17
- Ignored missing_trade_id: 17

## Input Recommendations

- Mode: advisory_only
- Auto-apply: False
- critical data_quality: block_input_optimization -> Do not tune performance inputs from this sample. Collect fresh closed trades after the analytics metadata fix. [inputs: trade_result_*.json, position_id, planned_entry, planned_sl, entry_branch, realized_r]

## Governance

- Evidence passed: False
- Walk-forward passed: False
- Change-rate passed: True
- AI review passed: True
- AI audit status: skipped
- Auto-activate allowed: False

## AI Audit

- Status: skipped
- Model: gpt-5.5
- Agree: None
- Weighted confidence: 0.000
- Activation veto: False
- Summary: EXPECTANCY_AI_AUDIT_ENABLE is off.

## Context Policies

- Not enough supported buckets

## Subtype Proof

- Not enough supported subtypes

## Overfitting

- Unstable bucket count: 0
- Global avg train/test gap: 0.000

## Session Weekday Policy

- No supported session-weekday samples yet

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
