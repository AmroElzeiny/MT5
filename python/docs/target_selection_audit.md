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

## Follow-up: AI Target Arbitration

The selector now preserves the real liquidity target before any fallback or cap
is applied. If the real target is behind an opposing obstacle and
`InpRequireAITargetArbitrationOnObstacle=true`, the EA sends one normal AI
request with `target_candidates`:

- `liquidity_target`: the original real liquidity target and RR.
- `capped_before_obstacle`: the safer target before the blocker, if available.
- `synthetic_rr_fallback`: the fallback RR target, if available.

`InpHardRejectCrossedObstacleTarget=true` restores the old hard rejection before
AI. Otherwise, Python does not hard-pre-gate this specific arbitration case as
long as a real liquidity target candidate exists.

The AI response now includes `target_arbitration` plus flat MT5-readable fields:

- `chosen_target_model`
- `chosen_tp1`
- `chosen_tp2`
- `chosen_rr2`
- `target_blocker_kind`
- `target_blocker_severity`
- `target_blocker_class`
- `target_blocker_is_trade_killer`
- `target_decision_reason`

MT5 applies that target before adding the setup to the watchlist and applies it
again after live/pending price rebuilds, so a valid AI target choice is not lost
when the order is prepared. If the AI chooses a final synthetic fallback through
a crossed opposing imbalance while strict blocking is enabled, MT5 still rejects
the trade.

## Stricter Target Defaults

- `InpStandardTradeLiquidityRRFloor = 1.10`
- `InpMaxTargetAdrFrac = 0.80`
- `InpMaxTargetAtrMult = 5.00`

## Verification

Run:

```powershell
python tools\verify_v_next_controls.py
```

The verifier checks that ordinary synthetic fallback through a crossed opposing
imbalance is rejected before OpenAI, while an explicit target-arbitration payload
with a real liquidity candidate passes the hard pre-gate so the AI can choose.
