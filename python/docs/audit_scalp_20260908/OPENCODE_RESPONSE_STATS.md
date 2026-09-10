# OpenCode routing-path response statistics

Source: `logs/ai_request_response_fingerprints.jsonl`, filtered to records whose request or response identifies the OpenCode route (`OPENCODE_API` / `opencode_*`). Snapshot: 101 routed responses across sessions `903871_1788983285_8075437` (96) and `591813800_1788925102_208218984` (5).

## Decision outcome

| Outcome | Responses | Share |
|---|---:|---:|
| Accepted / APPROVE | 1 | 1.0% |
| Rejected / REJECT | 37 | 36.6% |
| Abstained / ABSTAIN | 63 | 62.4% |
| Total | 101 | 100.0% |

There was no OpenCode-native approval. The single approval was returned by the configured OpenAI Luna fallback after the request had entered the OpenCode routing policy.

## Routing attribution

| Actual response route | Responses | APPROVE | REJECT | ABSTAIN |
|---|---:|---:|---:|---:|
| OpenCode Go Responses (`muse-spark-1.3-contributor`) | 82 | 0 | 22 | 60 |
| OpenAI fallback (`gpt-5.6-luna`) | 4 | 1 | 0 | 3 |
| Pre-gate / no provider response | 15 | 0 | 15 | 0 |

Thus 100 of 101 routed requests did not become an approved trade decision. Of those 100 no-trades, 82 were OpenCode decisions (60 abstentions and 22 rejections), 15 were blocked before a provider was called, and 3 were fallback abstentions.

## Why OpenCode did not trade

Reason counts below are text/code-derived and overlap: one decision can have several reasons. They are calculated only from the selected assessment for OpenCode-native responses, not from alternate candidates.

| Reason theme | OpenCode abstentions (n=60) | OpenCode rejections (n=22) |
|---|---:|---:|
| Historical evidence/prior/sample insufficient or adverse | 58 | 14 |
| Obstacle, clearance, or target-path issue | 55 | 21 |
| Retest, sequence, structure, or entry confirmation weak/unresolved | 55 | 9 |
| Regime/thesis contradiction | 13 | 22 |
| Session or timing weakness | 32 | 4 |
| RR, liquidity, or target feasibility | 14 | 14 |

### Rejections

All 22 native OpenCode rejections carried the structural-contradiction veto (`ai_veto_structural_contradiction`). The dominant pattern was a range-reentry thesis conflicting with an expansion regime and/or an obstacle at entry or in the target path.

Selected rejected setup families:

| Family | Rejections |
|---|---:|
| `MICRO_RANGE_REENTRY` | 17 |
| `MICRO_SESSION_REENTRY` | 4 |
| `MICRO_NESTED_CONTINUATION` | 1 |

### Abstentions

The abstentions were evidence-quality holds rather than hard structural vetoes: missing applicable history, unclear obstacle clearance, weak retest/structure confirmation, and often weak session timing occurred together.

Selected abstained setup families:

| Family | Abstentions |
|---|---:|
| `MICRO_BREAKER_RETEST` | 23 |
| `MICRO_OTE_REVERSAL` | 18 |
| `MICRO_FVG_MID_REVERSAL` | 8 |
| `MICRO_SESSION_REENTRY` | 5 |
| `MICRO_CONTINUATION_FVG` | 4 |
| `MICRO_NESTED_CONTINUATION` | 2 |

## Interpretation

The zero OpenCode approvals are not chiefly a transport problem. The strongest recurring blockers are decision-quality evidence: no reliable same-context prior, inadequate clearance through opposing obstacles, and insufficient confirmation that the setup's structural thesis remains valid. The 15 pre-gate rejections should be investigated separately from model behavior because OpenCode never evaluated them.

## Why the one model approval did not trade

The sole `APPROVE` was the OpenAI fallback's EURUSD `MICRO_BREAKER_RETEST` assessment for request `903871_1788983285_8075437_1788987005_EURUSD_24595`; it was not an OpenCode-native approval. Although its model/Python decision was `APPROVE` with a 0.55 risk multiplier, the selected `keep_current` target was the same synthetic fallback route and crossed an opposing imbalance.

The runtime input `reject_synthetic_fallback_after_crossed_obstacle` was enabled. The objective pre-trade gate therefore added `synthetic_fallback_crossed_obstacle_blocked` and `objective_pre_trade_gate_failed`, which prevents execution regardless of the model approval. The downstream MQL result is absent (`mql_final_allow = null`), and there is no matching order or broker-fill record in the available trade ledger.
