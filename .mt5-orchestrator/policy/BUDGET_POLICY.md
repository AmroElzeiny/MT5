# Budget and latency policy

The goal is reliable completion without runaway agent chains.

## Standard target
- wall-clock target: 90 minutes
- material model-invocation target: 8 or fewer
- ordinary repair loops: 2 maximum
- reviewers: 1
- explorer: only if ownership is unclear
- dedicated test worker: only when the implementation worker cannot efficiently prove the change

## Deep target
- wall-clock target: 180 minutes
- material model-invocation target: 12 or fewer
- ordinary repair loops: 2 maximum per work package
- reviewers: 2 only for genuinely high-risk work
- explorer: optional, not automatic

If the likely path exceeds the target:
1. stop repeated attempts;
2. summarize evidence and the bottleneck;
3. escalate to the front-end.

Never spend more merely to avoid admitting uncertainty.

## Context budget
- one broad repository map maximum per mission unless new evidence invalidates it;
- reuse authority maps within the same mission;
- pass compact summaries to later agents;
- do not send whole transcripts to every worker;
- avoid duplicate test runs after a stable pass unless code changed or a reviewer found a new risk.
