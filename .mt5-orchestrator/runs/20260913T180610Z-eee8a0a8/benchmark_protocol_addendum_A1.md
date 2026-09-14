# Protocol addendum A1 — execution subset, written before the full live run

Written by the front-end (Claude) on takeover, 2026-09-13 ~19:45 UTC. Before this point
exactly ONE live pair had run (the pilot `AUDJPY_32618 x muse_minimal`), and that pilot
is **invalid**: it ran before the deadline neutralization was complete, so a valid
HTTP 200 analyst answer was discarded as `provider_deadline_exceeded`. It was moved to
`results_pilot_invalid_deadline/` and is excluded from every statistic.

## A1.1 Execution subset (N = 60)

The frozen manifest holds 187 rows because selection step 2 ("add every historical
approval") pulled in 151 approvals. 187 x 6 arms is not executable inside the session
budget, so the run executes a deterministic 60-row subset, fixed before any full-run
paid call:

1. every anchor row (CLAUDE.md 4ac) and every stage-A sample row — 36 rows;
2. plus 24 rows from the `historical_allow_or_approve` pool, round-robin across
   `symbol_class`, each class ordered by `sha256(f"{seed}|subset_a1|{request_id}")`.

Result (`takeover/subset_a1.json`, produced by `takeover/select_subset.py`):
N = 60; historical decisions APPROVE 26 / ABSTAIN 26 / REJECT 8; symbol classes
FX-cross 14, FX-major 14, index 14, crypto 8, metal 8, energy 2.

The pair order is request-major (arms randomized per request under the seed), so if the
session budget ends early, whole requests are complete across all arms and the report
states the achieved N honestly.

## A1.2 Wire pinning

Every reasoning-effort arm pins `AI_PROVIDER_WIRE_PROJECTION=canonical` in its child
environment, i.e. the exact Phase-1 provider wire. Stage B (the lossless compact wire
projection) is implemented in the same working tree during this run; the pin plus the
per-attempt `instructions_sha256` / `stable_prefix_sha256` recorded in every result are
the contamination check: all canonical-arm attempts of one role must carry one
instructions hash.

## A1.3 Added arm (Part 10 A/B, not a reasoning-effort arm)

`muse_high_compact` = production HIGH effort with `AI_PROVIDER_WIRE_PROJECTION=compact_v1`.
It is judged against `muse_high_a` with the SAME pre-registered non-inferiority rule
(BEN-005 a–e) and the same HIGH-vs-HIGH noise floor. It is run after stage B's
losslessness tests pass.

## A1.4 Unchanged

Arms, metrics, safety counters S1–S3, the non-inferiority rule, isolation and the
Go-limit stop are exactly as in `benchmark_protocol.md`.
