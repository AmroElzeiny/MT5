# Protocol addendum A2 — production-default rule for the compact wire (written before any compact result)

Written 2026-09-13 ~19:55 UTC. At this moment `takeover/compact_arm/results/` holds **0** files;
the six effort arms hold 83 of 360 pairs. Nothing below may change after the first compact result exists.

## A2.1 What is being decided

Whether `AI_PROVIDER_WIRE_PROJECTION` defaults to `compact_v1` in production (code default), instead of
`canonical`. `.env` is not edited either way.

## A2.2 Rule

`compact_v1` becomes the code default only if `muse_high_compact`, compared with `muse_high_a` and the
`muse_high_a`-vs-`muse_high_b` noise floor over the same 60-request subset, satisfies:

- (a) S1 = 0;
- (b) S2 <= HIGH-vs-HIGH S2 noise count;
- (c) schema-valid and semantic-valid rates >= `muse_high_a` minus 2 pp;
- (d) decision-state agreement with `muse_high_a` >= HIGH-vs-HIGH agreement minus 5 pp;
- (e′) encoding-attributable failures <= `muse_high_a`'s. An encoding-attributable failure is a
  non-FULL_STRUCTURED outcome whose failed attempt is NOT a transport-level provider error. Transport-level
  means HTTP 5xx, `APIConnectionError`, a timeout, or an admission 429. Those are reported per arm with their
  cause but cannot be caused by how a successfully delivered payload is encoded. The literal (e) of BEN-005
  is also reported.
- (f) paired analyst input tokens decrease (median over requests where both arms completed the analyst).

If a condition cannot be evaluated because too few pairs completed inside the session, the default stays
`canonical` and the report says so.

## A2.3 Reasoning-effort arms

Unchanged: the pre-registered BEN-005 (a)–(e) verdict decides, and a lower effort is adopted only if it
passes. Any sensitivity analysis (infra-excluded agreement, pooled HIGH infra rate) is reported as
sensitivity only and cannot change a verdict.
