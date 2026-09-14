# Muse reasoning-effort non-inferiority benchmark (stage C1, BEN-001..007)

Generated: 2026-09-13T20:21:28Z
Manifest: 187 frozen requests, seed 20260913, generated 2026-09-13T18:49:39Z
Results files: 420; requests with both HIGH runs completed: 60

## Per-arm summary

| arm | effort | n_ok | APPROVE | allow% | state-agree vs high_a (CI) | schema% | semantic% | infra% | S1 | S2 | S3 | tokens/req | cost/req | latency/req | BEN-005 verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| muse_high_a | high | 60/60 | 0 | 0.0% (0/60) | - | 25.0% (15/60) | 25.0% (15/60) | 75.0% (45/60) | 0 | 0 | 0 | -% | -% | -% | n/a |
| muse_high_b | high | 60/60 | 0 | 0.0% (0/60) | 100.0% [94.0,100.0] | 26.7% (16/60) | 26.7% (16/60) | 73.3% (44/60) | 0 | 0 | 0 | -% | -% | -% | n/a |
| muse_medium | medium | 60/60 | 0 | 0.0% (0/60) | 93.3% [84.1,97.4] | 23.3% (14/60) | 23.3% (14/60) | 76.7% (46/60) | 0 | 0 | 0 | -17.2% | -18.6% | -31.2% | INFERIOR |
| muse_low | low | 60/60 | 0 | 0.0% (0/60) | 95.0% [86.3,98.3] | 33.3% (20/60) | 33.3% (20/60) | 66.7% (40/60) | 0 | 0 | 0 | -0.4% | -8.4% | -53.6% | NON-INFERIOR |
| muse_minimal | minimal | 60/60 | 1 | 1.7% (1/60) | 90.0% [79.9,95.3] | 33.3% (20/60) | 33.3% (20/60) | 66.7% (40/60) | 0 | 1 | 0 | -3.4% | -13.4% | -66.0% | INFERIOR |
| luna_low_flex | low | 60/60 | 30 | 50.0% (30/60) | 28.3% [18.5,40.8] | 100.0% (60/60) | 100.0% (60/60) | 0.0% (0/60) | 0 | 30 | 0 | 153.5% | -100.0% | -65.6% | INFERIOR |
| muse_high_compact | high | 60/60 | 1 | 1.7% (1/60) | 88.3% [77.8,94.2] | 16.7% (10/60) | 16.7% (10/60) | 83.3% (50/60) | 0 | 1 | 0 | -28.5% | -26.0% | -6.7% | INFERIOR |

## Noise floor (HIGH vs HIGH)

- decision-state agreement: 100.0% (60/60) (n=60)
- final-allow agreement: 100.0% (60/60)
- selected-candidate agreement: 96.7% (58/60)
- veto-code agreement: 98.3% (59/60)
- quality-score MAD: 0.173333
- S2 noise count: 0

## Spend

| arm | total cost USD (published Go rates; null-priced models excluded) |
|---|---|
| muse_high_a | 0.195736 |
| muse_high_b | 0.204306 |
| muse_medium | 0.162861 |
| muse_low | 0.183125 |
| muse_minimal | 0.173084 |
| luna_low_flex | 0.0 |
| muse_high_compact | 0.14807 |
- cost_unpriced models present for: ['muse_high_a', 'muse_high_b', 'muse_medium', 'muse_low', 'muse_minimal', 'luna_low_flex', 'muse_high_compact'] (Luna is not in the OpenCode Go published-price table and its attempts run under REMOTE_API mode, where opencode_go_accounting reports None).

## Caveats (mandatory per protocol Reporting)

- Sample size: every rate carries its Wilson 95% interval; no significance is claimed beyond n (60 paired requests).
- Muse is a non-deterministic contributor model: identical high_a vs high_b disagreement IS the noise floor, not an error.
- Time-of-day: arm order is randomized per request under the frozen seed; provider-side caching and load remain uncontrolled.
- LIVE_FORWARD requests keep the production repeatability authority gate; if the repeatability artifact has no group for an arm's exact generation settings, the pipeline fail-closes that arm's allow. This surfaces in decision_source/rejection_codes and counts toward infra_failure_rate for ALL arms symmetrically.
- If produced from --mode dry results, the decision columns only prove the fail-closed provider-failure path, NOT answer quality (module docstring).
