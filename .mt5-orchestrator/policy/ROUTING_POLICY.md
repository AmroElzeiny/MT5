# Routing policy

## Principle

Use the cheapest normal model that has the capability to finish correctly.
The live local catalog is authoritative.

Routing is cost-first, speed-aware, capability-gated and evidence-driven.

## Standard

Default supervisor: `opencode-go/minimax-m3`

Use for:
- normal repository work;
- localized bugs;
- routine implementation;
- normal cross-file implementation;
- tests;
- read-only audits;
- ordinary refactors.

Preferred flow:

```text
Supervisor
-> one worker
-> focused tests
-> one logic reviewer
-> done
```

Do not automatically invoke explorer, dedicated test worker, adversarial reviewer or vision.

## Deep

Default supervisor: `opencode-go/deepseek-v4.1-flash`

Use for material architecture/state/concurrency/security/execution risk, difficult cross-layer contracts, or repeated meaningful failure.

Preferred flow:

```text
Deep supervisor
-> explorer only if ownership is unclear
-> worker
-> test/debug if genuinely needed
-> logic reviewer
-> adversarial reviewer only for high risk
-> done
```

Deep tier does not justify expensive models.

## Role defaults

- Explorer: `opencode-go/deepseek-v4.1-flash`
- Fast worker: `opencode-go/qwen3.8-flash`
- Strong worker: `opencode-go/qwen3.8-flash`
- Test/debug: `opencode-go/qwen3.8-flash`
- Logic reviewer: `opencode-go/minimax-m3`
- Adversarial reviewer: `opencode-go/deepseek-v4.1-flash`
- Vision: `opencode-go/deepseek-v4-flash-vision-exp` only for actual visual acceptance

## Cost discipline

Do not automatically route to Kimi K2.7 Code, Qwen Max, DeepSeek V4 Pro, GLM full, or any other high-cost model.

A task is not entitled to a stronger model merely because it is:
- large;
- important;
- cross-file;
- high-risk.

High-risk work gets stronger evidence and reviewer diversity first.

## Repair loops

At most two meaningful attempts using the same model/approach.
Then change approach/model family or escalate.

## Context discipline

Do not make every worker rediscover the repository.
Pass compact evidence and the smallest necessary file/symbol/test scope.
Do not attach full transcripts unless essential.

## Reviewer independence

Normal work: one independent reviewer.

High-risk work: two perspectives from different families where practical:
- logic: MiniMax M3
- adversarial: DeepSeek V4.1 Flash

## Visual

Only invoke vision if UI/screenshots are part of acceptance.
CSS inspection alone is not a visual pass.

## Front-end escalation

If the normal low-cost model set cannot prove the requirement within budget, return `ESCALATE_TO_FRONTEND`.
Do not silently select an expensive model.
