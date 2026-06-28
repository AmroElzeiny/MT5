# Expectancy Report

Trades analyzed: 236
Avg R: 0.000
R 90% CI: [0.000, 0.000]

## Data Quality

- Result files found: 629
- Records analyzed after filters: 236
- Records ignored: 393
- IDs recovered from trade metadata: 236
- Ignored missing_trade_id: 393

## Governance

- Evidence passed: False
- Walk-forward passed: False
- Change-rate passed: True
- Auto-activate allowed: False

## Context Policies

- weak_trend||micro_continuation_fvg..virgin_fvg|balanced: action=allow, n=236, score_bias=0.00, risk_mult=0.62, ev_bias=-0.95

## Subtype Proof

- institutional_po3+closed_sweep|fvg+virgin_fvg|micro_continuation_fvg..virgin_fvg|weak_trend: action=downrank, n=236, shrunk_wr=0.00%, avg_r=0.000, risk_mult=0.55

## Overfitting

- Unstable bucket count: 1
- Global avg train/test gap: 0.000

## Adaptive Readiness

- Activate adaptive policy: False
- total_trades_ge_500: False
- family_trades_ge_50: True
- symbol_trades_ge_30: False
- profit_factor_after_costs_gt_1_10: True
- walk_forward_positive_fold_rate_ge_60pct: False

## Family Expectancy

- micro_continuation_fvg: action=reduce_risk, n=236, pf=2.22, avg_r=0.000, avg_cost_r=0.000, duration_min=76.8

## Symbol Expectancy

- AUDCAD: action=allow, n=8, pf=1.34, avg_r=0.000, reason=exploration_sample_lt_30
- AUDJPY: action=allow, n=10, pf=2.37, avg_r=0.000, reason=exploration_sample_lt_30
- AUDNZD: action=allow, n=10, pf=3.34, avg_r=0.000, reason=exploration_sample_lt_30
- AUDUSD: action=allow, n=10, pf=1.49, avg_r=0.000, reason=exploration_sample_lt_30
- BTCUSD: action=allow, n=10, pf=0.81, avg_r=0.000, reason=exploration_sample_lt_30
- CHFJPY: action=allow, n=10, pf=2.79, avg_r=0.000, reason=exploration_sample_lt_30
- DE30: action=allow, n=3, pf=0.00, avg_r=0.000, reason=exploration_sample_lt_30
- EURAUD: action=allow, n=10, pf=1.80, avg_r=0.000, reason=exploration_sample_lt_30
- EURCAD: action=allow, n=8, pf=3.07, avg_r=0.000, reason=exploration_sample_lt_30
- EURGBP: action=allow, n=4, pf=2.33, avg_r=0.000, reason=exploration_sample_lt_30
- EURJPY: action=allow, n=6, pf=0.96, avg_r=0.000, reason=exploration_sample_lt_30
- EURNZD: action=allow, n=8, pf=3.70, avg_r=0.000, reason=exploration_sample_lt_30
