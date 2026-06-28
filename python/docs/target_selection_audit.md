# Target Selection Audit

## Finding

The old target selector could detect an opposing imbalance before a synthetic
RR fallback target, then still allow the synthetic target unless older imbalance
blocking inputs happened to be enabled.

That made the rejection look related to the stop model, because changing the
stop changed the stop distance and therefore changed where the synthetic RR
target landed.

## Fix

`InpRejectSyntheticFallbackAfterCrossedObstacle` now defaults to `true`.

When a synthetic fallback target would cross an opposing imbalance/obstacle, the
plan is rejected with:

`synthetic_fallback_crossed_obstacle_blocked`

This is enforced in both MQL target construction and Python hard pre-gating.

## Stricter Target Defaults

- `InpStandardTradeLiquidityRRFloor = 1.10`
- `InpMaxTargetAdrFrac = 0.80`
- `InpMaxTargetAtrMult = 5.00`

