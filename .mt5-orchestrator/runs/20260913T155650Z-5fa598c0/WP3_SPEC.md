# WP3 SPEC -- design, SES-001, RSN-001, report (stage A)

RuntimeMode Offline. No provider/network inference calls. The ONLY new product file you may create is
`OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md` at the repo root. You may also write under
`.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/wp3/`. Do not touch any other product file, test,
schema, `.env`, or the live bus.

## Inputs to read
- `.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/MISSION.md`
- `.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/WP1_ACC001_RECONCILIATION.md`
- `.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/wp2/*.json` + `wp2/*.csv` + `wp2/tool_stdout.txt`
  (these are the raw measurements; use them, do not re-measure)
- `python/tools/opencode_token_attribution.py` (the tool; cite its `--stage` interface)
- `python/ai_provider.py` around `build_provider_exchange_contract` (line ~225) and
  `_wire_responses_kwargs` (1446, 3413) -- for WIRE-001 placement/contract arguments.
- `CLAUDE.md` sections 4ab/4ac/4y/4z (grep them).

## SES-001 -- already fetched by the supervisor (use these, do NOT re-fetch)
Source: https://opencode.ai/docs/go/ retrieved 2026-09-13 UTC; page footer "Last updated: Sep 13, 2026".
Exact quotes:
- `x-opencode-session`: "Send a stable session ID in `x-opencode-session` for each conversation so we
  can optimize routing and prompt caching."
- Usage limits: "Each model has the following usage limits: 5-hour — 20% of the monthly limit;
  weekly — 50%; and monthly — 100%." and "Muse Spark 1.3 Contributor ... Monthly limit $60".
- Pricing: Muse Spark 1.3 Contributor Input $0.10 / Output $0.20 / Cached Read $0.002 / Cached Write -.
- Endpoint: Muse Spark 1.3 Contributor -> `https://opencode.ai/zen/go/v1/responses`, `@ai-sdk/openai`.
- Estimated per-request profile: "Muse Spark 1.3 Contributor — 620 input, 71,400 cached, 300 output
  tokens per request".
- Privacy: "Muse Spark 1.3 Contributor ... Yes [training] ... Not ZDR".
- NOT documented on the Go page: `previous_response_id`, `store`, or conversation objects. There is no
  linked Responses/session page in the Go docs; the page only documents the stable session header and
  the console. State this absence explicitly (no undocumented mechanism may be asserted).
Judgement required: is "one session per model+schema" consistent with the documented meaning of a
conversation, including concurrency across 2 workers and unrelated symbols? Also measure
token-weighted cached-read ratio from the attempt ledger / usage rows since the Phase-1 change
(`<bus>/logs/opencode_go_attempts.ndjson` does NOT exist -> zero post-change rows; say so; do not
fabricate). The Phase-1 evidence file `OPENCODE_GO_BILLING_EVIDENCE.md` section 7 has the pre-change
ratio (0.37%).

## RSN-001 (stage C preparation ONLY -- make NO provider call)
1. Document why HIGH was chosen: evidence from `CLAUDE.md` 4ac, `git log -S OPENCODE_MUSE_REASONING_EFFORT`,
   and the 25,000-token incident (the `max_output_tokens` + reasoning-reserve trap). Cite commits/§.
2. Freeze a benchmark request set: write `wp3/benchmark_manifest.json` with **>= 40 real archived
   request ids** (from `<bus>\response_debug` / `logs\openai_usage.ndjson` / archived payloads),
   including the known approvals AUDJPY_32618 and USDCHF_19925 (CLAUDE.md 4ac), known REJECT/ABSTAIN
   outcomes, 1/2/3+ candidates, and the existing Luna decisions on the same ids where available.
   Each row: request_id, symbol, role(s), candidate count, archived outcome/decision_state, whether a
   Luna row exists, and the source file. If AUDJPY_32618/USDCHF_19925 have no Muse row, record that.
3. Define the Part-7 metrics precisely (decision state, allow, veto, tokens/latency/cost per role,
   non-inferiority rule, safety counters, uncertainty intervals) -- definition only, no calls.

## WIRE-001 (design only, do not implement)
Design a deterministic provider-wire projection `canonical envelope -> compact wire -> model` that
keeps the internal frozen contract, `exchange.contract_hash` semantics and evidence-id validation
intact. For each proposed transformation give: tokens saved (measured on the wp2 sample), losslessness
argument, the round-trip/equivalence test that would prove it, where it must live (before or after
`build_provider_exchange_contract`), whether it changes `instructions` text and therefore prompt
contract identity / MQL manifest / replay cache keys, and static-prefix vs dynamic-suffix placement
(use `wp2/cache_layout.json`: static prefix tokens, first volatile byte offset, `sort_keys=True`
interleave finding). Rank by safe saving. Use the wp2 duplication classes and precise top-subtree
numbers.

## REP-001 -- `OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md` (repo root), exactly 13 sections
1. Executive summary and scope (stage A only; no production change)
2. Accounting reconciliation (ACC-001 / Part 1) -- from WP1
3. Input token attribution (IN-001 / Part 2) -- from wp2
4. Duplication and repeated values (DUP-001 / Part 3) -- from wp2
5. Deterministic wire projection design (WIRE-001 / Part 4 + 9)
6. Numeric precision (PREC-001 / Part 5) -- from wp2
7. Output attribution (OUT-001 / Part 6) -- from wp2
8. Changes made -- mark `PENDING STAGE B`
9. Before/after -- mark `PENDING STAGE B`
10. Decision quality -- mark `PENDING STAGE C`
11. Tests -- mark `PENDING STAGE B/C`
12. Session semantics and cache verification (SES-001 / Part 8)
13. Reasoning-effort benchmark preparation (RSN-001 / Part 7) + ranked optimization table +
    theoretical ceiling (input-lossless / output-unconsumed / reasoning-unknown-until-C) + risks and
    explicit uncertainties.

Rules: every number labelled `measured` (source file + sample size) or `estimated` (method). The ranked
optimization table must give measured tokens/request and projected USD per 1,000 intended calls at the
published rates (Muse 0.10/0.20/0.002). Do not assert any provider billing defect. Do not claim
deployment/live verification. Keep the frozen-contract constraints (AI_PROMPT_CONTRACT_VERSION, MQL
manifest hash, replay cache keys, evidence-id validation) explicit wherever a transformation could
touch them.

## Acceptance evidence to return
- `git status --short` showing only `OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md` (new) and run-dir files.
- The 13 section headings in order.
- `wp3/benchmark_manifest.json` count and its stratum breakdown.
- The ranked optimization table (top 6 rows) with measured tokens/request.
- Any number you could not source, and what you wrote instead.
