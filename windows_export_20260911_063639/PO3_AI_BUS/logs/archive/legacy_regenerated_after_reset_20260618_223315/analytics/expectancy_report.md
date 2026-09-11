# Expectancy Report

Trades analyzed: 30
Avg R: 0.152
R 90% CI: [-0.412, 0.940]

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
- Global avg train/test gap: 0.513

## Adaptive Readiness

- Activate adaptive policy: False
- total_trades_ge_500: False
- family_trades_ge_50: False
- symbol_trades_ge_30: False
- profit_factor_after_costs_gt_1_10: True
- walk_forward_positive_fold_rate_ge_60pct: False

## Family Expectancy

- micro_continuation_fvg: action=allow, n=6, pf=1.60, avg_r=1.144, avg_cost_r=0.019, duration_min=715.3
- micro_bisi_sibi_edge: action=allow, n=22, pf=0.79, avg_r=-0.043, avg_cost_r=0.080, duration_min=461.2
- micro_po3_reversal: action=allow, n=2, pf=0.00, avg_r=-0.678, avg_cost_r=0.056, duration_min=82.5

## Symbol Expectancy

- AUDUSD: action=allow, n=3, pf=12.25, avg_r=3.119, reason=exploration_sample_lt_30
- CHFJPY: action=allow, n=3, pf=0.00, avg_r=-1.444, reason=exploration_sample_lt_30
- EURCAD: action=allow, n=1, pf=0.00, avg_r=-0.290, reason=exploration_sample_lt_30
- EURJPY: action=allow, n=1, pf=0.00, avg_r=-1.619, reason=exploration_sample_lt_30
- EURUSD: action=allow, n=14, pf=1.13, avg_r=0.151, reason=exploration_sample_lt_30
- GBPAUD: action=allow, n=1, pf=0.00, avg_r=-1.065, reason=exploration_sample_lt_30
- GBPJPY: action=allow, n=1, pf=0.00, avg_r=-1.051, reason=exploration_sample_lt_30
- GBPUSD: action=allow, n=1, pf=0.00, avg_r=3.799, reason=exploration_sample_lt_30
- NZDJPY: action=allow, n=4, pf=0.00, avg_r=-0.294, reason=exploration_sample_lt_30
- USDCAD: action=allow, n=1, pf=0.00, avg_r=-1.176, reason=exploration_sample_lt_30
