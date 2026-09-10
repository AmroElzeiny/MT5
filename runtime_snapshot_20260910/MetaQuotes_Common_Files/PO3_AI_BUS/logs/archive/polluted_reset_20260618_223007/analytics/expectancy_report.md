# Expectancy Report

Trades analyzed: 78
Avg R: 0.552
R 90% CI: [0.377, 0.743]

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
- Global avg train/test gap: -0.503

## Adaptive Readiness

- Activate adaptive policy: False
- total_trades_ge_500: False
- family_trades_ge_50: False
- symbol_trades_ge_30: False
- profit_factor_after_costs_gt_1_10: True
- walk_forward_positive_fold_rate_ge_60pct: True

## Family Expectancy

- micro_bisi_sibi_edge: action=allow, n=44, pf=10.68, avg_r=0.718, avg_cost_r=0.016, duration_min=329.6
- micro_continuation_fvg: action=allow, n=33, pf=12.51, avg_r=0.357, avg_cost_r=0.003, duration_min=244.8
- micro_po3_reversal: action=allow, n=1, pf=0.00, avg_r=-0.290, avg_cost_r=0.037, duration_min=120.0

## Symbol Expectancy

- AUDUSD: action=allow, n=1, pf=0.00, avg_r=-0.905, reason=exploration_sample_lt_30
- BTCUSD: action=allow, n=2, pf=0.00, avg_r=0.499, reason=exploration_sample_lt_30
- CHFJPY: action=allow, n=9, pf=0.00, avg_r=0.721, reason=exploration_sample_lt_30
- DE30: action=allow, n=3, pf=0.00, avg_r=0.917, reason=exploration_sample_lt_30
- EURCAD: action=allow, n=1, pf=0.00, avg_r=-0.290, reason=exploration_sample_lt_30
- EURUSD: action=allow, n=14, pf=1.13, avg_r=0.151, reason=exploration_sample_lt_30
- GBPJPY: action=allow, n=8, pf=0.00, avg_r=0.754, reason=exploration_sample_lt_30
- GBPNZD: action=allow, n=2, pf=0.00, avg_r=1.306, reason=exploration_sample_lt_30
- NZDJPY: action=allow, n=11, pf=3962.86, avg_r=0.543, reason=exploration_sample_lt_30
- US30: action=allow, n=4, pf=0.00, avg_r=0.186, reason=exploration_sample_lt_30
- US500: action=allow, n=6, pf=0.00, avg_r=0.625, reason=exploration_sample_lt_30
- USOIL: action=allow, n=6, pf=0.00, avg_r=0.296, reason=exploration_sample_lt_30
