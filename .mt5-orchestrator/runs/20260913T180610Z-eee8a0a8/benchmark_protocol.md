# Pre-registered benchmark protocol — Muse reasoning-effort non-inferiority (Phase 2, stage C1)

Run: `20260913T180610Z-eee8a0a8`
Front-end: Claude. Tier: Deep. RuntimeMode: Offline (paid provider calls authorized).
Written before the first paid call. This file is the frozen protocol; results are reported
against it and nothing below is changed after the pilot starts.

## Question

Is a lower reasoning effort for the Muse leg (`muse-spark-1.3-contributor`) non-inferior to
the production `high` setting, judged on the full production decision pipeline over real
archived requests? GPT-5.6 Luna at `low`/`flex` (the production fallback leg) is the quality
reference, not a candidate for the production default.

This is measurement only. **No production default, `.env`, schema, prompt or MQL file is
changed by this run.**

## Frozen request set

`<run>/benchmark_manifest.json`, frozen before the run. Selection rule (BEN-001):

1. Start from the 36 requests in stage A's
   `.mt5-orchestrator/runs/20260913T155650Z-5fa598c0/wp2/sample_manifest.json`.
2. Add every archived request whose `response_debug/<request_id>.json` shows
   `response.python_final_allow == true` OR `response.decision_state == "APPROVE"`.
3. Add stratified requests to cover candidate count 1 / 2 / 3+ and symbol class
   FX-major / FX-cross / metal / index / crypto, using a fixed seed.
4. Always include `AUDJPY_32618` and `USDCHF_19925` (CLAUDE.md §4ac) when present.
5. Only requests that exist as an archived payload (`completed`/`rejected`) AND have a
   `response_debug` file are eligible, and eligible requests must historically have reached
   the provider (`decision_quality_tier == "FULL_STRUCTURED"`), so every selected request is
   one that "reaches the provider".
6. Freeze: request id, archive path, stratum, historical decision. Target N >= 60.

## Arms

Each arm runs the **identical frozen request** through the **full production decision
pipeline** (`ai_gate._score_setup_impl`: candidate sealing, hard pre-gates, analyst ->
critic -> adjudicator when reached, all Python validation / evidence-reference /
authoritative-envelope / veto / threshold / authority gates), differing only in the injected
Muse leg's reasoning effort. A single injected provider leg is used, so there is **no
fallback chain**.

| Arm | Leg | Model | Effort | Tier |
|---|---|---|---|---|
| `muse_high_a` | OpenCode Responses | `muse-spark-1.3-contributor` | `high` | default |
| `muse_high_b` | OpenCode Responses | `muse-spark-1.3-contributor` | `high` | default |
| `muse_medium` | OpenCode Responses | `muse-spark-1.3-contributor` | `medium` | default |
| `muse_low` | OpenCode Responses | `muse-spark-1.3-contributor` | `low` | default |
| `muse_minimal` | OpenCode Responses | `muse-spark-1.3-contributor` | `minimal` | default |
| `luna_low_flex` | Remote API (OpenAI leg as the gate builds it) | `gpt-5.6-luna` | `low` | `flex` |

Production values are kept for session scope, schema, prompt, `max_output_tokens` (25,000),
reasoning token reserve (24,000 for Muse), timeout (900 s) and admission retry. A failed
call is a failure of that arm; it is never replaced by another model.

`high_a`/`high_b` measure HIGH's own run-to-run noise.

## Pre-registered per-request / per-candidate metrics (BEN-004)

- `decision_state` (APPROVE / ABSTAIN / REJECT); `python_final_allow`; `raw_allow`.
- selected candidate index (`chosen_index`); `selected_candidate_id`.
- veto enabled and veto code; `hard_veto` / blocked-by-gate reasons (`rejection_codes`).
- `llm_quality_score`; `llm_self_reported_confidence`; `decision_quality_tier`.
- schema valid (raw model schema reached and accepted; `decision_quality_tier ==
  FULL_STRUCTURED` with `mandatory_fields_complete`); semantic valid (identity, evidence
  reference and authoritative-envelope validation all accepted: `missing_mandatory_fields`
  and `invalid_mandatory_fields` both empty and `decision_source` not an infrastructure
  outcome).
- `decision_source` (surfacing infrastructure outcomes).
- latency per role (analyst / critic / adjudicator).
- input, cached, reasoning and visible output tokens per role (provider usage).
- cost in USD at published OpenCode Go rates via `python/opencode_go_accounting.py`
  (`usage_categories` + `expected_go_usage_usd`). Models the module does not price are
  reported as `null` with a note.

Agreement of each arm with `high_a`, with HIGH-vs-HIGH (`high_a` vs `high_b`) as the noise
floor:

- decision-state agreement;
- final-allow agreement;
- selected-candidate agreement;
- veto-code agreement;
- quality-score mean absolute difference.

Wilson 95 % intervals are reported on every agreement rate. The sample size is stated
honestly; no significance is claimed beyond what N supports.

Safety counters (per arm, over requests where both HIGH runs completed):

- **S1** — arm APPROVE / `python_final_allow = true` on a candidate that **both** HIGH runs
  REJECTED with a veto.
- **S2** — arm `python_final_allow = true` where **both** HIGH runs had
  `python_final_allow = false`.
- **S3** — arm REJECT/ABSTAIN where **both** HIGH runs approved (missed trades; reported,
  not a safety failure).

## Pre-registered non-inferiority rule (BEN-005)

An arm is **non-inferior to HIGH** only if **all** hold:

- (a) S1 = 0;
- (b) S2 count is no more than the HIGH-vs-HIGH count of the same kind;
- (c) schema-valid and semantic-valid rates are each >= HIGH's minus 2 percentage points;
- (d) decision-state agreement with `high_a` is >= the `high_a`-vs-`high_b` agreement minus
  5 percentage points;
- (e) infrastructure-failure rate (non-FULL_STRUCTURED, transport, deadline) is no higher
  than HIGH's.

For each arm the report gives the verdict, the token and cost saving versus HIGH, and the
latency change.

## Effort verification (BEN-003)

For every arm the requested effort is confirmed from the provider interaction: the wire
`reasoning.effort` sent (attempt ledger `reasoning_effort_sent`), the provider-reported
`reasoning_tokens`, and the observed model id. If a level is rejected or silently ignored
(no distinguishable reasoning behaviour), that exact evidence is recorded, the arm is
dropped, and no other level is substituted.

## Benchmark harness controls (frozen before the pilot)

Two process-environment settings are fixed for every arm and every request, and are applied
by the harness itself (`opencode_reasoning_benchmark.py`, `configure_child_env`) so they are
identical across arms:

- `AI_REQUIRE_REPEATABILITY_LIVE=false` — the live repeatability authority gate groups its
  evidence by `generation_settings_hash`, which **includes `reasoning_effort`**. Leaving it
  on would fail-close every lower-effort arm through `repeatability_authority_gate` for a
  reason unrelated to model quality, making the comparison circular. `false` is the value in
  the production environment template (`python/docs/runtime_env_template_opencode.txt:455`).
- `AI_SHADOW_REPEAT_ENABLE=false` — the shadow-repeat research feature would add a random
  extra provider call to a sampled fraction of pairs, confounding per-arm call counts, cost
  and latency. It is not a decision gate.

These are benchmark-harness controls, not production-default changes. `.env` and every
production default are untouched.

## Run conditions (BEN-006, BEN-007)

- Every pair runs in a fresh subprocess with a fresh temp bus; decision cache disabled.
- No writes to the live bus, `python/data/*`, `python/logs/*` or the shadow ledger;
  writers are neutralized in-process and before/after mtimes and sizes are recorded.
- Bounded concurrency, starting at 6; no resubmission beyond the gate's own admission retry.
- Arm order is randomized per request (fixed seed) so time-of-day and caching do not favour
  one arm.
- Resumable: one result file per `(request, arm)`; completed pairs are skipped on restart.
- On a Go usage-limit 429: stop cleanly, report what completed, do not enable balance usage.

## Reporting

`results/*.json`, `benchmark_results.csv`, `benchmark_summary.json`, `BENCHMARK_REPORT.md`.
Uncertainty (sample size, model non-determinism, time-of-day effects) is stated explicitly.
