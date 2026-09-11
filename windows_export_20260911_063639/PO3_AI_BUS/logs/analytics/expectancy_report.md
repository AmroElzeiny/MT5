# Expectancy Report

Trades analyzed: 550
Avg R: -0.505
R 90% CI: [-0.648, -0.338]
Virtual balance: 104384.16
Virtual net %: 4.384%

## Data Quality

- Result files found: 619
- Records analyzed after filters: 550
- Records ignored: 69
- Ignored missing_entry_branch: 69
- Ignored missing_planned_entry: 69
- Ignored missing_planned_sl: 69
- Ignored zero_realized_r_with_nonzero_pnl: 69

## Input Recommendations

- Mode: advisory_only
- Auto-apply: False
- First weak-signal action after: 100 valid trades
- Governed activation after: 400 valid trades
- Bucket action threshold: 50 valid trades
- high global_edge: tighten_selectivity_and_risk -> Reduce risk 25-50%, raise minimum live RR by about +0.10R, lower trade-cost ceiling by 0.03-0.05R, and raise weak-family setup floors by 2-5 points. [inputs: InpRiskPerTradePct, InpMinLiveRR2, InpStandardTradeCostRCeiling, InpSetupFloorFullPO3 / InpSetupFloorMicroPO3 / family floors]
- high risk_path: reduce_exposure_until_drawdown_recovers -> Keep shadow mode on, cut per-trade risk, reduce concurrent exposure, and enable aggregate risk caps before allowing adaptive activation. [inputs: InpRiskPerTradePct, InpMaxOpenPositions, InpMaxTradesPerScan, InpMaxTotalRiskEnable, InpMaxTotalRiskMoney, InpPolicyShadowMode]
- high setup_family: suppress -> Disable the branch/family where an input exists, or raise its setup floor by 5-8 points and set risk multiplier to 0 in policy. [inputs: InpEnableContinuationReentry, InpSetupFloorContinuationFamily, InpMinRRContinuation]
- high setup_family: suppress -> Disable the branch/family where an input exists, or raise its setup floor by 5-8 points and set risk multiplier to 0 in policy. [inputs: InpEnableFvgEdge / InpEnableBreakerRetest, InpSetupFloorMicroBisiSibi, InpMinRRMicroPO3]
- high stability: do_not_live_activate_unstable_learning -> Keep shadow mode on. Do not lower walk-forward gates to force activation; tighten the weak families instead. [inputs: InpPolicyShadowMode, InpPolicyMinPositiveFoldRate, InpPolicyMaxTrainTestGapR]
- high symbol_selection: suppress -> Remove or pause this symbol from Market Watch until its bucket improves. [inputs: Market Watch symbol list, InpSkipIfSymbolOpen, InpMaxTradesPerScan]
- high symbol_selection: suppress -> Remove or pause this symbol from Market Watch until its bucket improves. [inputs: Market Watch symbol list, InpSkipIfSymbolOpen, InpMaxTradesPerScan]
- medium target_quality: tighten_target_acceptance -> Demand better real target quality before entry; avoid long synthetic targets where capture ratio stays weak. [inputs: InpMinLiveRR2, InpStandardTradeLiquidityRRFloor, InpMaxTargetAtrMult, InpMaxTargetAdrFrac]
- medium target_quality: tighten_target_acceptance -> Demand better real target quality before entry; avoid long synthetic targets where capture ratio stays weak. [inputs: InpMinLiveRR2, InpStandardTradeLiquidityRRFloor, InpMaxTargetAtrMult, InpMaxTargetAdrFrac]
- medium target_quality: tighten_target_acceptance -> Demand better real target quality before entry; avoid long synthetic targets where capture ratio stays weak. [inputs: InpMinLiveRR2, InpStandardTradeLiquidityRRFloor, InpMaxTargetAtrMult, InpMaxTargetAdrFrac]
- medium target_quality: tighten_target_acceptance -> Demand better real target quality before entry; avoid long synthetic targets where capture ratio stays weak. [inputs: InpMinLiveRR2, InpStandardTradeLiquidityRRFloor, InpMaxTargetAtrMult, InpMaxTargetAdrFrac]

## Decision Maker Edit Scope

- active_policy.json: soft_setup_floor, hard_setup_floor, setup_floor_penalty_mult, ote_softness_frac, default_risk_multiplier, runner_sequence_floor, runner_liquidity_rr_floor, runner_cost_r_ceiling, runner_alignment_floor, runner_adverse_ceiling, runner_ev_floor
- context_policy.ndjson: action, score_bias, risk_multiplier, expected_value_bias
- subtype_policy.ndjson: action, score_penalty, risk_multiplier
- session_weekday_policy.ndjson: action, risk_multiplier, rr_floor_delta, score_bias
- MT5 inputs are advisory recommendations only; the report does not rewrite input files.

## Governance

- Evidence passed: False
- Walk-forward passed: False
- Change-rate passed: True
- AI review passed: True
- AI audit status: skipped
- Auto-activate allowed: False

## AI Audit

- Status: skipped
- Model: gpt-5.4-nano
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
- Global avg train/test gap: 0.705

## Session Weekday Policy

- Mon|ASIA: action=monitor, n=33, avg_r=-2.303, pf=2.30, risk_mult=1.00, rr_delta=0.00
- Wed|OFF_HOURS: action=monitor, n=13, avg_r=-1.387, pf=0.13, risk_mult=1.00, rr_delta=0.00
- Fri|OFF_HOURS: action=monitor, n=12, avg_r=-1.251, pf=7.04, risk_mult=1.00, rr_delta=0.00
- Tue|ASIA: action=monitor, n=67, avg_r=-1.117, pf=0.18, risk_mult=1.00, rr_delta=0.00
- Thu|OFF_HOURS: action=monitor, n=34, avg_r=-1.086, pf=1.32, risk_mult=1.00, rr_delta=0.00
- Mon|NEW_YORK: action=monitor, n=28, avg_r=-1.058, pf=1.25, risk_mult=1.00, rr_delta=0.00
- Wed|LONDON: action=monitor, n=17, avg_r=-1.045, pf=0.20, risk_mult=1.00, rr_delta=0.00
- Mon|LONDON: action=monitor, n=11, avg_r=-0.498, pf=1.22, risk_mult=1.00, rr_delta=0.00
- Fri|NEW_YORK: action=monitor, n=43, avg_r=-0.480, pf=1.88, risk_mult=1.00, rr_delta=0.00
- Wed|ASIA: action=monitor, n=6, avg_r=-0.428, pf=0.10, risk_mult=1.00, rr_delta=0.00
- Tue|NEW_YORK: action=monitor, n=27, avg_r=-0.401, pf=0.10, risk_mult=1.00, rr_delta=0.00
- Fri|ASIA: action=monitor, n=23, avg_r=-0.273, pf=0.03, risk_mult=1.00, rr_delta=0.00

## Adaptive Readiness

- Activate adaptive policy: False
- total_trades_ge_400: True
- family_trades_ge_50: True
- symbol_trades_ge_100: False
- profit_factor_after_costs_gt_1_10: False
- walk_forward_positive_fold_rate_ge_60pct: False

## Family Expectancy

- micro_range_reentry: action=allow, n=35, pf=0.67, avg_r=-0.284, avg_cost_r=0.043, duration_min=272.0
- micro_po3_reversal: action=allow, n=22, pf=0.23, avg_r=-1.810, avg_cost_r=0.032, duration_min=982.9
- micro_continuation_fvg: action=suppress, n=139, pf=0.95, avg_r=-0.284, avg_cost_r=0.036, duration_min=663.1
- micro_bisi_sibi_edge: action=suppress, n=354, pf=1.50, avg_r=-0.533, avg_cost_r=0.048, duration_min=341.2

## Symbol Expectancy

- #Germany40: action=allow, n=1, pf=0.00, avg_r=-5.268, reason=exploration_sample_lt_100
- #UK100: action=allow, n=1, pf=0.00, avg_r=-1.065, reason=exploration_sample_lt_100
- #US30: action=allow, n=4, pf=0.21, avg_r=-0.957, reason=exploration_sample_lt_100
- #USNDAQ100: action=allow, n=2, pf=1.17, avg_r=0.098, reason=exploration_sample_lt_100
- #USSPX500: action=allow, n=1, pf=0.00, avg_r=-1.077, reason=exploration_sample_lt_100
- 100GBP: action=allow, n=9, pf=0.20, avg_r=-0.499, reason=exploration_sample_lt_100
- 225JPY: action=allow, n=6, pf=0.63, avg_r=-0.282, reason=exploration_sample_lt_100
- AUDCAD: action=allow, n=7, pf=4.14, avg_r=0.145, reason=exploration_sample_lt_100
- AUDJPY: action=allow, n=19, pf=1.48, avg_r=-0.344, reason=exploration_sample_lt_100
- AUDNZD: action=allow, n=7, pf=0.00, avg_r=1.608, reason=exploration_sample_lt_100
- AUDUSD: action=allow, n=18, pf=28.02, avg_r=-0.260, reason=exploration_sample_lt_100
- BITCOIN: action=allow, n=2, pf=0.00, avg_r=1.745, reason=exploration_sample_lt_100
