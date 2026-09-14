# Mission

Front-end: Claude
Tier: Deep
RuntimeMode: Offline

## Outcome

OpenCode Go cost work, **Phase 2, stage C1: live reasoning-effort non-inferiority benchmark for
Muse** (`muse-spark-1.3-contributor`) on real archived requests, with GPT-5.6 Luna low/flex as a
quality reference.

The deliverable is **evidence**, not a configuration change. Production stays on
`OPENCODE_MUSE_REASONING_EFFORT=high`. The front-end decides from this evidence.

Paid provider authorization: YES. The user authorized paid provider calls without a spend cap on
2026-09-13 (~21:05 local). Spend sensibly anyway, and report actual spend.

Runs **in parallel** with stage A (run `20260913T155650Z-5fa598c0`, which is building
`python/tools/opencode_token_attribution.py` and `OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md`).
Do not touch either of those files or that run directory, except to read
`.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/wp2/sample_manifest.json` and the
`WP2_SPEC.md` capture recipe.

## Confirmed baseline (front-end verified 2026-09-13, do not rediscover)

- **Muse is reachable now.** Front-end probe at ~21:04 local with the gate's configured key:
  HTTP 200 in 2.7 s. The new-account OpenCode CLI answers Muse too. The earlier weekly 429 no
  longer blocks.
- `python/.env` (never read, print or copy it; the gate loads it itself):
  `AI_PROVIDER_SELECT=opencode`, `OPENCODE_MUSE_MODEL=muse-spark-1.3-contributor`,
  `OPENCODE_MUSE_REASONING_EFFORT=high`, `OPENCODE_MUSE_REASONING_TOKEN_RESERVE=24000`,
  `OPENCODE_MAX_OUTPUT_TOKENS=25000`, `OPENCODE_TIMEOUT_SEC=900`, `OPENCODE_PARALLELISM=25`,
  `OPENCODE_CALL_DIRECTING=false`, secondary `deepseek-v4.1-flash`,
  fallback `OPENCODE_FALLBACK_MODEL=gpt-5.6-luna` / effort `low` / tier `flex` (OpenAI key present).
- Accepted effort values (`ai_gate.py:765-779`): `minimal`, `low`, `medium`, `high`, `xhigh`
  (`xhigh` is not advertised for Muse), plus `auto`/`none`/empty, which omit the key.
- Stage A measured on 36 real requests (median, Muse rows, from its `output_attribution.json`):
  analyst output 12,221 tokens = 8,748 reasoning (70%) + 3,473 visible;
  critic output 3,783 = 3,668 reasoning (95%) + 114 visible. The visible output is fixed by the
  schema and does not track reasoning much (Pearson r analyst 0.21, critic 0.46), so reasoning
  effort is the main output-cost lever.
- **Why HIGH was chosen:** CLAUDE.md §4ac. Muse at `effort=high` spent the entire 25,000
  `max_output_tokens` on reasoning and returned an empty message, so the 24,000 reserve was added.
  HIGH itself was never benchmarked against lower levels. Record this history; do not re-derive it.
- **Validated offline capture recipe:** `.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/WP2_SPEC.md`
  (section "VALIDATED capture recipe"). It drives the real `ai_gate._score_setup_impl` path with an
  injected provider (`ai_gate._provider = lambda: leg`), neutralizes `ai_gate.LOG_FILE` and
  `ai_gate._write_ai_cost_report`, patches `RequestDeadline` because archived deadlines are in the
  past, and runs each payload in a fresh subprocess. Reuse it; the only change is that the
  injected leg now has a **real** client.
- Archived request payloads: `<bus>\completed\python_*__<request_id>.json` and `<bus>\rejected\`
  (UTF-16 or UTF-8; load with `ai_gate.read_json_any_encoding`). Real outputs for comparison:
  `<bus>\response_debug\<request_id>.json`
  (bus = `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS`).
- The routed provider falls back Muse -> DeepSeek -> Luna. **A benchmark arm must not fall back**,
  or it would silently measure another model.
- Direction (LONG/SHORT) is fixed per candidate by the deterministic engine; the AI does not
  choose it. The user's ENTER / WATCH / NO_TRADE map to this system's
  `decision_state` APPROVE / ABSTAIN / REJECT plus `python_final_allow`. Report it in those terms.

## Requirements

- BEN-001 Request set: at least 60 real archived requests that reach the provider. Start from
  stage A's 36 in `sample_manifest.json`, then add stratified ones: candidate count 1/2/3+, symbol
  class FX-major/FX-cross/metal/index/crypto, and every archived request whose `response_debug`
  shows `python_final_allow=true` or `decision_state=APPROVE`. Include AUDJPY_32618 and
  USDCHF_19925 (CLAUDE.md §4ac). Freeze the set as
  `<run>/benchmark_manifest.json`: request id, archive path, stratum, historical decision.
- BEN-002 Arms, each running the **full production decision pipeline** (analyst -> critic ->
  adjudicator when reached -> all Python validation, veto, threshold and authority gates), on the
  identical frozen request, differing only in the Muse leg's reasoning effort:
  `muse_high` run **twice** (`high_a`, `high_b`, to measure HIGH's own run-to-run noise),
  `muse_medium`, `muse_low`, `muse_minimal`, and `luna_low_flex` (the production fallback leg as
  the gate builds it). Build each leg from the gate's own builder with only the effort changed,
  e.g. `dataclasses.replace(ai_gate.AI_CONFIG, opencode_muse_reasoning_effort=...)` and the gate's
  provider builders. Inject the Muse leg (or the Luna leg) directly so there is **no fallback
  chain**. A failed call is recorded as a failure of that arm. Keep production values for session
  scope, schema, prompt, `max_output_tokens`/reserve, timeout and admission retry.
- BEN-003 Effort verification: for every arm, confirm from the provider response (`reasoning`
  echo and/or `reasoning_tokens`) that the requested effort was applied. If a level is rejected or
  silently ignored, record that exact evidence, drop the arm, and do not substitute another level.
- BEN-004 Pre-registered metrics. Write them to `<run>/benchmark_protocol.md` **before** the
  first paid call. Per request and per candidate:
  - `decision_state` (APPROVE/ABSTAIN/REJECT); `python_final_allow`; selected candidate index;
    veto enabled and veto code; `hard_veto` / blocked-by-gate reasons; `llm_quality_score`;
    `llm_self_reported_confidence`; `decision_quality_tier`; schema valid; semantic valid
    (identity, evidence-reference and authoritative-envelope validation); `decision_source`
    (surfacing infrastructure outcomes); latency per role; input, cached, reasoning and visible
    output tokens per role; cost at published Go rates via `python/opencode_go_accounting.py`.
  - Agreement of each arm with `high_a`, with the **HIGH-vs-HIGH** agreement (`high_a` vs `high_b`)
    as the noise floor: decision-state agreement, final-allow agreement, selected-candidate
    agreement, veto-code agreement, and quality-score mean absolute difference.
  - Safety counters: (S1) arm APPROVE / final_allow=true on a candidate that **both** HIGH runs
    REJECTED with a veto; (S2) arm final_allow=true where both HIGH runs had final_allow=false;
    (S3) arm REJECT/ABSTAIN where both HIGH runs approved (missed trades, reported but not a
    safety failure).
  - Wilson 95% intervals on every agreement rate. State the sample size honestly; do not claim
    significance the sample cannot support.
- BEN-005 Pre-registered non-inferiority rule, stated in the protocol before running. An arm is
  non-inferior to HIGH only if **all** hold:
  (a) S1 = 0;
  (b) S2 is no more than the HIGH-vs-HIGH count of the same kind;
  (c) schema-valid and semantic-valid rates are each >= HIGH's minus 2 percentage points;
  (d) decision-state agreement with `high_a` is >= the `high_a`-vs-`high_b` agreement minus
      5 percentage points;
  (e) infrastructure-failure rate (non-FULL_STRUCTURED, transport, deadline) is no higher than HIGH's.
  The report gives each arm's verdict, its token and cost saving versus HIGH, and the latency
  change. **Do not change `.env` or any production default, whatever the verdict.**
- BEN-006 Isolation. Every run uses a fresh temp bus (for example `%TEMP%\po3bench\<arm>\<req>`).
  Decision cache disabled. No writes to the live bus, `python/data/*` (trade memory, repeatability,
  taxonomy files), `python/logs/*` (`ai_cost_report.jsonl`, fingerprints) or the shadow ledger:
  neutralize the writers in-process as the capture recipe does, or point them at temp copies, and
  prove it with before/after mtimes and sizes of those files. Never start, stop or signal a real
  `ai_gate.py` process or the MT5 terminal.
- BEN-007 Throughput and robustness. Run arms and requests with bounded concurrency (start at 6
  concurrent pipelines; lower it if 429s appear). Rely on the gate's own admission retry; add no
  resubmission of your own. Randomize arm order per request so time-of-day and caching do not
  favour one arm. Make it resumable: a per-(request, arm) result file, and skip completed pairs on
  restart. On a Go usage-limit 429 (`GoUsageLimitError`), stop cleanly, report what completed, and
  do not enable balance usage.
- BEN-008 Tool: `python/tools/opencode_reasoning_benchmark.py` with subcommands `manifest`,
  `run --arms ... --concurrency N`, `report`. Outputs under the run directory: `results/*.json`,
  `benchmark_results.csv`, `benchmark_summary.json`, `BENCHMARK_REPORT.md` (tables, verdict per
  arm, spend, caveats). Before any paid call: a dry-run on 2 requests with a fake client proving
  the pipeline path, arm effort injection, no-fallback behaviour and isolation. Then a paid pilot on
  3 requests x all arms. Then the full run.
- TST-001: MAJOR for the new tool only. One focused test file
  `python/tests/test_opencode_reasoning_benchmark.py`, fully offline with fake clients. It covers:
  the arm effort reaches the wire; a Muse failure is recorded as a failure with no fallback call;
  the agreement / safety counters on a hand-built fixture; resumability skips completed pairs.
  Final verification once: full Python suite (`cd python; .\.venv\Scripts\python.exe -m pytest tests -q`),
  counts recorded. No MQL change, so no MQL compile; say so.
- SAF-001: no change to `ai_gate.py`, `ai_provider.py`, `decision_*.py`, `evidence_catalog.py`,
  `structured_models.py`, schemas, prompts, contract versions, `.env`, MQL or existing tests.
  No trades (no MT5 interaction at all). Never print, log or persist any API key or workspace id.

## Work packages

### WP1 -- Harness, protocol, dry-run, pilot
Objective: BEN-001, BEN-002, BEN-003, BEN-004, BEN-005 (protocol written first), BEN-006,
BEN-008 (dry-run + 3-request paid pilot), TST-001 focused test.
Change size: MAJOR (new tool + focused test)
Acceptance evidence: protocol file timestamped before the first paid call; dry-run proof;
pilot results with effort verification for every arm; isolation proof.
Escalation triggers: the full pipeline cannot run with an injected leg without editing product
code; an effort level cannot be verified; the pilot shows a systematic infrastructure failure on
some arm (report it, do not work around it by changing production code).

### WP2 -- Full run, report, review
Objective: BEN-007, the full run, `BENCHMARK_REPORT.md`, final full suite; one logic review
(`minimax-m3`) of the statistics and verdicts against the raw result files, and one adversarial
review (`deepseek-v4.1-flash`) of isolation and of whether any arm fell back or cached.
Change size: SMALL
Escalation triggers: usage-limit 429; per-arm failure rate > 20%.

## Budgets

- target wall clock: 180 minutes, of which the paid run is expected to take 60-120
- material model invocations: <= 12 (agent invocations; benchmark provider calls are not agent
  invocations and are governed by the paid authorization above)
- repair attempts per approach: <= 2

## Runtime safety

- RuntimeMode: Offline (no trading, no MT5, no live bus). Paid provider calls: authorized.
- Demo authorization: NO
- Real-money/live mutation: forbidden

## Final proof

- requirement coverage per ID; protocol-before-run proof; manifest; per-arm effort verification;
  per-arm tables (agreement with Wilson CIs, safety counters S1-S3, validity, infrastructure
  failures, latency, tokens, cost); verdict per arm under the pre-registered rule; actual total
  spend; isolation proof (mtimes and sizes); focused test and full-suite counts; diff audit (only
  the new tool and the new test added); reviewer findings; model IDs; explicit uncertainty
  (sample size, non-determinism, time-of-day effects).
