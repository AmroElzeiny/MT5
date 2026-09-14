# OpenCode Go — Phase 2: token efficiency of the Muse decision path

Date: 2026-09-13 · Model: `muse-spark-1.3-contributor` via `https://opencode.ai/zen/go/v1/responses`

Evidence runs:
- `.mt5-orchestrator/runs/20260913T155650Z-5fa598c0` — stage A measurement.
- `.mt5-orchestrator/runs/20260913T180610Z-eee8a0a8` — stage C benchmark, stage B A/B, takeover work in `takeover/`.
  **Read `BENCHMARK_VALIDITY_NOTE.md` there before `BENCHMARK_REPORT.md`.**

Phase 1 (`OPENCODE_GO_BILLING_EVIDENCE.md`, attempt ledger, prefix-scoped session) is kept intact.

Every number is labelled **measured** (with source and sample size) or **estimated** (with method).
USD uses the published Go rates for Muse: input $0.10 / output $0.20 / cached read $0.002 per 1M tokens.
`reasoning_tokens` are part of `output_tokens`.

---

## 1. Executive summary and scope

Goal: fewer billable tokens per intended Muse call, with the same information, decision authority, trading
paths and call frequency, and with non-inferior decision quality.

Not changed, by design: logical AI calls, event triggers, Call Directing, fallback semantics, evidence content,
historical coverage, candidate selection, risk controls, trading logic, output schemas, MQL contracts,
`AI_PROMPT_CONTRACT_VERSION`, the MQL manifest hash, `.env`, the production reasoning effort (HIGH), and the
production wire default (`canonical`).

Findings:

1. **Accounting (§2).** The refusals are the account's OpenCode Go **weekly limit running out** (confirmed by the
   user). The weekly allowance is consumed by all OpenCode Go usage on the account, not only by this gate. This
   host's Muse calls were $11.10–$11.56 on the old account and $1.18 on the new one, while the OpenCode CLI on this
   machine reports **$16.76 today and $51.32 over 7 days** (`opencode stats`, mostly the orchestration runs). So
   the "$11 vs $30" gap is other Go usage drawing on the same allowance, not a metering anomaly. No billing defect.
2. **Input (§3–§5).** The largest input cost is the *structure* of the evidence catalog (row key names and repeated
   `entry_and_invalidation.candidates.<i>.` path heads), not duplicated values. A lossless provider-wire encoding,
   `compact_v1`, is implemented and tested. It cuts **analyst input by 19.6%** (exact provider counts,
   13 requests, 605,737 → 486,831 tokens). In the live A/B it cut critic input by 14.8% and adjudicator input by 19.8%.
3. **Output (§7).** No model output field is unconsumed (0 of 62), so nothing was trimmed. Reasoning is 72% of
   analyst output and 97% of critic output.
4. **Reasoning effort (§10, §13).** Inconclusive. The account's weekly limit ran out mid-run (264 attempts refused,
   not billed), leaving only 12–20 clean pairs per arm, so HIGH stays until the run is completed after the reset.
5. **Wire default (§9, §10).** Also inconclusive for decision quality, for the same reason. `compact_v1` stays
   **opt-in** (`AI_PROVIDER_WIRE_PROJECTION=compact_v1`); the code default remains `canonical`, per the rule
   registered before the A/B (addendum A2).
6. **Precision (§6).** No digit removal is provably information-free. Nothing changed.
7. **Sessions / cache (§12).** No stateful continuation is documented, so none is used. With the Phase-1 prefix
   session, the token-weighted cached-read ratio on HIGH arms is **6.7–9.5%**, against **0.37%** before.
8. **Incident, found and fixed during the takeover.** The benchmark children's Python-side writers escaped
   isolation, because `po3_env.bootstrap_provider_env()` re-applies `.env` with `override=True`. 118 benchmark rows
   in the decision cache and 11 rows in trade memory were removed from production data (backed up). The harness
   now re-applies isolation after the credential load and refuses to run a pair if any writer still points
   outside its scratch dir.

## 2. Accounting reconciliation (Part 1)

Source: `runs/20260913T155650Z-5fa598c0/WP1_ACC001_RECONCILIATION.md`. Inputs: `openai_usage.ndjson`
(15,208 rows), `ai_gate.log` (210,999 lines), `ai_cost_report.jsonl`, temp-bus ledgers and MT5 Experts journals,
repriced with `python/opencode_go_accounting.py`.

Muse inventory on this host (old account), measured:

| Kind | Window | Count |
|---|---|---|
| Successful responses, main gate | 2026-09-09T16:50Z → 09-13T12:31Z | 2,474 (analyst 1,105 · critic 1,116 · adjudicator 252) |
| Successful, temp-bus replays/probes | 09-09 and 09-13 | 11 rows in separate ledgers |
| 429 with `limitName='weekly'` | 09-13 12:28–12:33Z | 128 lines, reset 09-14T00:00Z |
| 429 without `limitName` (body truncated) | 09-11 cluster | 411 lines; window not provable |
| `[provider_call_failed]` | 09-09..09-13 | 30 (20×403, 4 deadline, 3 circuit-open, 3 connection) |
| Health/capability probes generating on Muse | — | **0** |

Three-way table (USD):

| Window | Documented allowance | Local Muse reconstruction (this host) | Not observable here | OpenCode console |
|---|---|---|---|---|
| 5-hour | $12.00 (20% of monthly) | 09-13 07:27→12:27Z $3.354 · 09-11 11:50→16:50Z $0.633 | other runtimes/keys, other Go models or clients | user must read |
| Weekly | $30.00 (50%) | **$11.097–$11.165**; all-time $11.264; temp-bus +$0.0375; unobserved failures ≤ +$0.36 → **≤ $11.56** | same | limit reached 12:28Z |
| Monthly | $60.00 (100%) | not reconstructable (host rows start 09-09) | same | same |

**New-account evidence (measured, this session).**

- The user switched accounts around 12:35Z; `.env` mtime and the gate ledger show 0 Muse rows afterwards.
- Every Muse call from this host since then is recorded per attempt: 265 billed attempts + 5 of unknown billing
  in the benchmark ($1.072), plus 26 exact-token probes ($0.110). **Total $1.18**, window 19:15–20:04Z.
- At ~19:52Z the endpoint began refusing (264 `not_billed_admission_refused`). A direct probe at ~20:14Z
  returned `429 GoUsageLimitError "Weekly usage limit reached. Resets in 3hr 46min"`, which is the same
  09-14T00:00Z reset as the old account.
- Over the same day, `opencode stats` on this machine reports **$16.76** (73 sessions; the stage A/C orchestration
  runs and another project's runs) and **$51.32 over 7 days**.

Conclusion (confirmed by the user): the refusals are simply the account's OpenCode Go weekly limit being
exhausted. The allowance is shared by everything that uses OpenCode Go on the account. On this machine that is
mainly the OpenCode CLI orchestration ($51.32 over 7 days, $16.76 today), next to the gate's Muse calls ($11.26 in
the old account's week, $1.18 today). The gate-only ledger was never the whole meter, which is why it showed $11
against a $30 limit. **No billing defect.**

Practical consequence: delegated OpenCode runs and Muse benchmarking draw on the same weekly allowance as live
trading decisions, so heavy orchestration can starve the gate. Budget them together.

## 3. Input token attribution (Part 2)

Method (measured):
- Sample: 36 archived requests stratified by candidate count (1/2/3+) and symbol class, driven offline through the
  real `_score_setup_impl` and the real critic/adjudicator builders with a recording transport, giving 108 role
  payloads (`python/tools/opencode_token_attribution.py`, outputs in `runs/20260913T155650Z-5fa598c0/wp2/`).
- Token counting: `tiktoken` is unavailable, so a documented proxy was calibrated per request against provider
  `input_tokens`. Leave-one-out error was average 2.9%, p95 9.6%.
- Cross-check: exact provider counts (§9) confirm the catalog estimates (proxy 7,175 vs exact 7,473 on
  AUDJPY_32618; 19,285 vs 19,466 on BITCOIN_9173).

Analyst (calibrated tokens, n=36):

| Component | p50 | p95 | % of input (p50) | Static / dynamic | Duplicated | Compactable |
|---|---:|---:|---:|---|---|---|
| `instructions` | 3,798 | 3,798 | 9.9 | static | no | no (contract text) |
| instructions + strict schema (cached prefix) | 5,154 (provider-observed 6,513) | — | — | static | no | no |
| `evidence_catalog` (whole) | 13,380 | 34,518 | 35.1 | dynamic | §4 | **yes: structure** |
| · catalog paths `p` | 6,845 | 17,911 | 17.9 | dynamic | repeated heads | **yes: prefix factoring** |
| · catalog key names + JSON punctuation | 4,582 | 11,603 | 12.0 | dynamic | per-row keys | **yes: row tuples** |
| `entry_and_invalidation` (candidate rows) | 10,704 | 29,298 | 28.1 | dynamic | class a/c | not in v1 |
| key names, whole input (cross-cut) | 9,767 | 25,230 | 25.6 | dynamic | — | partly |
| labels / descriptions / prose leaves (cross-cut) | 7,987 | 20,534 | 20.9 | dynamic | — | no (information) |

Critic (n=36): catalog 5,421 (33.5%), candidate block 3,748 (23.2%), historical analogues 2,857 (17.7%),
catalog paths 2,656 (16.4%), catalog keys+punctuation 1,939 (12.0%).
Adjudicator (n=36): catalog 6,105 (46.2%), candidate block 4,213 (31.9%), catalog paths 3,000 (22.7%),
catalog keys+punctuation 2,191 (16.6%).

Provider-measured inputs in the benchmark (FULL_STRUCTURED requests): analyst 17K–102K depending on candidate
count (~38K for 3 candidates), critic ~16–18K, adjudicator ~13–14K. No image parts on this path.

## 4. Duplication and repeated values (Part 3)

Every catalog row across the 108 payloads was classified (measured, `wp2/duplication.json`). Class (a) is
proven by path identity, not value equality.

| Class | Rows | Whole-row tokens | Value-only tokens | Meaning | Action |
|---|---:|---:|---:|---|---|
| a — same fact serialized twice | 16,329 | 472,818 | 52,002 | catalog `v` repeats the envelope value at the same canonical path | keep values; remove structural overhead (§5) |
| b — distinct facts, equal value | 188 | 4,818 | 872 | coincidence | none |
| c — alias | 2,023 | 64,405 | 3,717 | row points at a value the Phase-1 compaction renamed | none |
| d — catalog-only | 158 | 6,439 | 1,614 | catalog is the only carrier | none |
| e — contract-required (overlap flag) | 3,971 | 137,243 | 20,745 | path named in a role prompt | must survive |

Removing duplicated *values* would save ~481 tokens per role call. The Phase-1 estimate (~4K tokens) holds only
for the largest, 8-candidate requests. It was **not applied**: the model would have to dereference
id → path → value across a 100–300K-character payload, a citation-accuracy risk for ~1.3% of input. The
*structural* overhead of the same rows is ~20% of input and removable with no information change; that is
`compact_v1`.

## 5. Deterministic provider-wire projection (Parts 4 and 9)

**The frozen internal contract is unchanged.** The following keep their canonical form:
- `model_payload`;
- `EvidenceCatalog` (ids, paths, values, scopes, `catalog_hash`) and `resolve()` (fail-closed citation validation);
- `allowed_evidence_ref_ids` and candidate identity binding;
- `response_debug`, MQL-visible fields, and every cache key.

**`compact_v1` changes only how `evidence_catalog.items` is encoded on the wire** (`python/provider_wire_projection.py`):

```text
canonical : {"c":0,"id":71,"p":"entry_and_invalidation.candidates.0.obstacle_kind","v":"crossed_opposing_imbalance"}, ...
compact_v1: {"c":0,"p_prefix":"entry_and_invalidation.candidates.0.","rows":[[71,"obstacle_kind","crossed_opposing_imbalance"], ...]}
```

- **Grouping.** Rows are grouped by (candidate scope, first path segment). Each group carries its longest shared
  dotted prefix, and every row keeps a non-empty suffix. A group without `c` is global. A self-describing
  `wire_format` sentence is added.
- **Lossless by construction.** `expand_evidence_from_wire()` rebuilds the canonical rows exactly.
- **Lossless by check, on every call.** The projection expands its own output and compares canonical JSON (the
  exact serialization `build_provider_exchange_contract` freezes). On any mismatch, or on non-canonical rows, it
  sends the canonical payload unchanged.
- **Placement.** Only at the six provider call sites: analyst primary, citation repair and mapping repair in
  `ai_gate.py`; critic, critic repair and adjudicator in `decision_pipeline.py`. No call is added or removed.
  `exchange_contract_hash` describes what was actually sent.
- **Prompt coupling.** The analyst's catalog sentence comes from `analyst_catalog_description(...)`. Under
  `canonical` it is the Phase-1 sentence byte for byte: instructions sha256 `c1fc5f80…` in every canonical
  benchmark attempt, before and after the change. Under `compact_v1` the sha is `896047ec…`. The critic and
  adjudicator texts remain true under both encodings.
- **Contract identity.** `AI_PROMPT_CONTRACT_VERSION` and the MQL manifest are not bumped: the MQL↔Python
  decision contract did not change. A source search found no persisted identity or cache key containing the
  instructions text; the only hash of it is the telemetry field `instructions_sha256`.
- **Configuration.** `AI_PROVIDER_WIRE_PROJECTION` = `canonical` (default) | `compact_v1`. It is logged in
  `safe_log_dict`, an invalid value is warned and replaced by the default, and it is documented in
  `python/docs/runtime_env_template_opencode.txt`. The harness pins it per arm.
- **Cache layout.** Instructions stay first and identical across requests, the schema is unchanged, and the
  session id stays model+schema scoped. Switching encoding changes the analyst instructions once.

Canonical invariance was also proven in the running benchmark. Two requests were re-captured after the edit:
instructions and schema are byte-identical, and the evidence differs only in `runtime.observed_at` (capture clock)
and the hashes derived from it (5 of 4,108 and 10 of 10,371 leaves).

Not implemented in v1:
- dropping duplicated `v` (§4);
- hoisting the global ids repeated in each candidate's `allowed_evidence_ref_ids` (a citation guardrail placed per
  candidate on purpose);
- moving the critic/adjudicator per-request id list out of `instructions` (a duplicate that limits their cached
  prefix, but a prompt change that needs its own A/B).

## 6. Numeric precision (Part 5)

Measured (`wp2/precision.json`, 108 payloads): **0 provable** reductions. The hypothetical total is 7,261 proxy
units across 53 field/rule pairs, about 38 tokens per payload (0.1%). Nothing changed: values arrive from MQL
already serialized, so their source precision cannot be recovered and trimming cannot be proven information-free.

## 7. Output attribution (Part 6)

Usage ledger, stage A sample (analyst n=34):

| Role | Output tokens avg (p50 / p95) | Reasoning avg | Visible avg | Reasoning share |
|---|---|---:|---:|---:|
| analyst | 12,772 (12,548 / 17,910) | 9,254 | 3,519 | 72% |
| critic | 3,783 | 3,668 | ~114 | 97% |

`max_output_tokens` sent: analyst median 30,000, critic 49,000 (schema budget + 24,000 reserve). These are
ceilings, not spend.

Consumer trace of every model-owned output field across Python and MQL (`wp2/consumer_table.json`):
decision value 21 · validated or bound 17 · persisted 15 · diagnostic logged 8 · telemetry 1 · **never read 0**.
Largest visible fields: `analyst.target_arbitration` p50 465 tokens (decision value), `analyst.major_risks` 41,
`adjudicator.resolution_reason` 36, `analyst.summary` 33. The 35 input-echoing fields are identity and target
bindings that Python validates fail-closed, not prose. **No output field or schema was trimmed.** The output lever
is reasoning (§13).

## 8. Changes made

| File | Change |
|---|---|
| `python/provider_wire_projection.py` | **new** — `compact_v1` encoder/decoder, per-call round-trip self-check, analyst catalog sentence per encoding |
| `python/ai_gate.py` | `AIGateRuntimeConfig.provider_wire_projection` + `AI_PROVIDER_WIRE_PROJECTION` (default `canonical`, invalid → warned default); `safe_log_dict` entry; analyst catalog sentence coupled to the encoding; encoding at the 3 analyst provider calls; encoding passed to consensus via request metadata |
| `python/decision_pipeline.py` | encoding at the critic, critic-repair and adjudicator provider calls |
| `python/tools/harness_local_provider.py` | reads the catalog after `expand_evidence_from_wire` (either encoding) |
| `python/tests/test_decision_integrity.py` | fake compliant model decodes the wire before reading catalog ids |
| `python/docs/runtime_env_template_opencode.txt` | documents `AI_PROVIDER_WIRE_PROJECTION` |
| `python/tests/test_provider_wire_projection.py` | **new** — losslessness and coupling contract (16 tests) |
| `python/tools/opencode_token_attribution.py` | **new** (stage A) — offline capture + attribution |
| `python/tools/opencode_reasoning_benchmark.py` | **new** (stage C). Takeover changes: deadline predicates neutralized for archived payloads, per-arm wire pin, `muse_high_compact` arm, usage-limit stop and resume (`usage_limit_refused`), isolation re-applied after `.env` load plus a fail-closed guard (`child_isolation_violations`), A3 clean-pair agreement |
| `python/tests/test_opencode_reasoning_benchmark.py` | **new** (stage C) + 5 takeover hardening tests |
| production data | removed 118 benchmark rows from `python/data/ai_decision_cache.jsonl` and 11 benchmark rows from `pending_decisions` in `python/data/ai_trade_memory.sqlite3` (backups: `runs/…eee8a0a8/takeover/isolation_backup/`) |

No MQL file, schema, `.env`, reasoning effort or production default changed.

## 9. Before / after (Part 10)

**Exact provider input tokens.** The same archived analyst wire was encoded both ways, 13 requests (measured,
`takeover/token_probe_results.jsonl`):

| Request | Candidates | Canonical | compact_v1 | Saved | % |
|---|---:|---:|---:|---:|---:|
| GBPCAD_29371 | 1 | 17,330 | 14,631 | 2,699 | 15.6 |
| GBPUSD_22093 | 1 | 18,921 | 16,205 | 2,716 | 14.4 |
| WTI_22682 | 2 | 25,554 | 20,476 | 5,078 | 19.9 |
| US30_16044 | 2 | 28,603 | 23,518 | 5,085 | 17.8 |
| AUDJPY_32618 | 3 | 38,121 | 30,648 | 7,473 | 19.6 |
| UK100_2328 | 3 | 38,397 | 30,915 | 7,482 | 19.5 |
| NAS100_9597 | 3 | 38,417 | 30,935 | 7,482 | 19.5 |
| USDCHF_19925 | 3 | 38,511 | 31,034 | 7,477 | 19.4 |
| EURAUD_5869 | 3 | 38,983 | 31,497 | 7,486 | 19.2 |
| XAUUSD_13096 | 3 | 45,403 | 37,910 | 7,493 | 16.5 |
| BITCOIN_9173 | 8 | 86,796 | 67,330 | 19,466 | 22.4 |
| USDCHF_2988 | 8 | 88,816 | 69,312 | 19,504 | 22.0 |
| GOLD_25455 | 8 | 101,885 | 82,420 | 19,465 | 19.1 |
| **Total** | | **605,737** | **486,831** | **118,906** | **19.6** |

Saving ≈ 2,470 tokens per candidate; median per request 7,482 tokens (19.4%).

**Live A/B on the full pipeline.** `muse_high_compact` vs `muse_high_a`, same 60 requests, same effort and session
scope. First successful call per role, paired where both arms reached that role:

| Role | Paired n | Canonical input (mean) | compact_v1 input (mean) | Δ mean | Δ median | Δ % |
|---|---:|---:|---:|---:|---:|---:|
| analyst | 17 | 52,375 | 41,794 | −10,581 | −7,485 | −20.2 |
| critic | 10 | 18,376 | 15,655 | −2,722 | −2,724 | −14.8 |
| adjudicator | 3 | 13,920 | 11,162 | −2,758 | −2,723 | −19.8 |

Against `muse_high_b`: analyst −20.1% (n=18), critic −15.2% (n=11), adjudicator −20.1% (n=3).

**Logical call count is unchanged by construction.** The projection sits inside existing calls, and a test pins
3 + 3 call sites. Observed provider-attempt counts differ between arms only through usage-limit refusals and
model-triggered repair passes.

Projected effect (estimated from the paired medians and the historical role mix of 1.01 critic and 0.23
adjudicator calls per analyst call): ≈10.9K input tokens per request, **≈$1.09 per 1,000 requests** at the
uncached rate (≈$1.40 using means). Applied to the old account's measured week (1,105 / 1,116 / 252 role calls),
that is ≈13.8M of 71.5M uncached input tokens, **≈$1.38 of $11.26 (≈12%)**.

## 10. Decision quality

**Validity.** The account's Go weekly limit ran out at ~19:52 UTC (confirmed by the user), so 264 Muse attempts
were refused and not billed. Each Muse arm therefore has 40–50/60 degraded outcomes that are not model decisions. The literal pre-registered verdicts
(`BENCHMARK_REPORT.md`) are artifacts of those refusals, as `BENCHMARK_VALIDITY_NOTE.md` explains:

- `muse_low` "non-inferior": agreement inflated by shared refusals.
- `muse_high_compact` "not non-inferior": more refusals because it ran later.

Encoding-attributable failures were 0 in every arm. Effort on the wire was verified in all 420 pairs, there were
no fallback rows, and no arm used a model other than its own.

**Clean-pair comparison.** Addendum A3 requires FULL_STRUCTURED on both sides, with `muse_high_a` as reference:

| Arm | Clean pairs | Decision-state agreement [Wilson 95%] | Final-allow agreement | Selected-candidate agreement | Quality-score MAD | A3 verdict |
|---|---:|---|---:|---:|---:|---|
| `muse_high_b` (noise floor) | 15 | 15/15 = 100% [80–100] | 100% | 86.7% | 0.44 | — |
| `muse_medium` | 12 | 12/12 = 100% [76–100] | 100% | 83.3% | 0.34 | not evaluable (n < 40, refusals left) |
| `muse_low` | 15 | 14/15 = 93.3% [70–99] | 100% | 73.3% | 0.47 | not evaluable (n < 40); agreement below noise − 5 pp |
| `muse_minimal` | 15 | 11/15 = 73.3% [48–89] | 93.3% | 66.7% | 0.74 | fails |
| `luna_low_flex` (reference) | 15 | 9/15 = 60.0% [36–80] | 60.0% | 66.7% | 0.88 | fails |
| `muse_high_compact` | 9 | 8/9 = 88.9% [57–98] | 88.9% | 88.9% | 0.57 | not evaluable (n < 40, refusals left) |

Safety counters over all pairs: S1 = 0 for every arm. One `muse_minimal` and one `muse_high_compact` request
reached `python_final_allow=true` where both HIGH runs did not (S2 = 1 each; the HIGH-vs-HIGH noise count is 0).
S3 (missed approvals) cannot be tested, because no HIGH run approved in this subset.

On the 12 requests that are clean in all five Muse effort arms, HIGH, medium and HIGH-B give 12× ABSTAIN;
low gives 11 ABSTAIN + 1 REJECT; minimal gives 9 ABSTAIN + 3 REJECT.

**Decision:** no change. HIGH remains the production effort and `canonical` the production wire default.
`compact_v1` is lossless (proven) and its token saving is measured, but decision non-inferiority is not
established with 9 clean pairs. It is available as an explicit opt-in.

**To finish.** After the weekly reset, run the resume command in `BENCHMARK_VALIDITY_NOTE.md`. The hardened
harness re-runs only the refused pairs (≈$2–3 estimated). Judge by `non_inferior_a3`, then flip the default with
one line if `compact_v1` passes.

## 11. Tests

`python/tests/test_provider_wire_projection.py` (16 tests, 6 subtests):
1. round trip restores the byte-identical canonical wire;
2. every (id, scope, path, typed value) survives;
3. value types are not coerced;
4. compact wire is strictly shorter;
5. everything outside the catalog rows is untouched, including `catalog_version`, `catalog_hash` and `usage`;
6. internal-identity rows stay withheld;
7. per-candidate citation scope and `EvidenceCatalog.resolve` validation are identical, and a cross-candidate id still fails;
8. every path keeps a non-empty suffix;
9. candidate groups factor the candidate path head;
10. idempotent, and canonical expands to itself;
11. canonical, empty and unknown encodings return the same object;
12. non-canonical rows are never projected (6 malformations);
13. payloads without a catalog are untouched;
14. the canonical analyst sentence is the Phase-1 text, and the compact sentence matches `wire_format`;
15. every `generate_structured` call in `ai_gate.py` (3) and `decision_pipeline.py` (3) encodes through the projection;
16. the analyst sentence follows the configured encoding.

`python/tests/test_opencode_reasoning_benchmark.py`, takeover additions:
- a weekly-limit refusal stops the run and is re-run on resume;
- a congestion 429 recovered by admission retry is not a limit;
- isolation survives a `.env` that overrides the redirects;
- the live child re-applies isolation after the credential load;
- clean agreement ignores shared infrastructure rejects.

Both files: **28 passed, 10 subtests**.

Full Python suite, run after all product-code changes:
- canonical: **1220 passed, 12 skipped, 674 subtests, 0 failed** (80 s);
- repeated with `AI_PROVIDER_WIRE_PROJECTION=compact_v1` forced: **1220 passed, 12 skipped, 674 subtests, 0 failed**.

So enabling compact has no test blast radius. No MQL file changed, so there was no recompile.

## 12. Session semantics and cache verification (Parts 8 and 9)

Documentation (`https://opencode.ai/docs/go/`, retrieved 2026-09-13):
- *"Send a stable session ID in `x-opencode-session` for each conversation so we can optimize routing and prompt caching."*
- Limits: 5-hour 20%, weekly 50%, monthly 100% of the monthly limit (Muse $60).
- Rates: $0.10 / $0.20 / cached read $0.002.
- Not documented: `previous_response_id`, `store`-based continuation, conversation objects.

Decision: no stateful continuation (`store=false`, full evidence on every call). The Phase-1 session id is scoped
to model + response schema. It is a routing and caching key, not conversation state, so concurrent workers and
unrelated symbols cannot leak context through it.

Measured cache behaviour. Benchmark attempts, token-weighted over successful Muse calls:

| Arm | Cached-read ratio | Note |
|---|---:|---|
| `muse_high_a` | 9.50% | analyst cached reads hit exactly 6,513 tokens (instructions + schema) |
| `muse_high_b` | 6.68% | |
| `muse_high_compact` | 9.04% | new instructions hash warmed within the run |
| `muse_medium` / `muse_low` / `muse_minimal` | 3.48% / 4.79% / 4.61% | fewer calls per session in the run |
| Before Phase 1 (per-request sessions, 2,454 responses) | **0.37%** | `OPENCODE_GO_BILLING_EVIDENCE.md` §7 |

Layout:
- Analyst `instructions` (16,880 chars) and the schema are identical across all sampled requests, and the first
  volatile byte is the start of the evidence.
- Critic and adjudicator instructions end with a per-request id list; their cached reads are the static head
  (≈1.1–2.5K tokens).

## 13. Reasoning effort, ranked optimizations, ceilings, risks

### Why HIGH was the production setting

`CLAUDE.md` §4ac and `runtime_env_template_opencode.txt`: `high` was chosen as the strongest level the dialect
accepts. The 24,000 reserve was added after HIGH spent a 25,000 `max_output_tokens` budget on reasoning and
returned `incomplete` on the analyst. HIGH was not chosen by a quality benchmark.

### Benchmark (Part 7): measured cost, unproven quality

Twelve requests are clean in all five Muse effort arms (FULL_STRUCTURED everywhere). Per request:

| Arm | Analyst reasoning | Analyst output | Critic output | Go USD / request | Latency / request |
|---|---:|---:|---:|---:|---:|
| `muse_high_a` | 9,240 | 14,381 | 3,305 | $0.01145 | 401 s |
| `muse_high_b` | 9,802 | 14,776 | 4,159 | $0.01185 | 406 s |
| `muse_medium` | 5,667 | 10,425 | 2,812 | $0.01087 (−6.7%) | 300 s |
| `muse_low` | 1,710 | 6,332 | 1,580 | $0.01002 (−14.0%) | 170 s |
| `muse_minimal` | 648 | 5,211 | 595 | $0.00945 (−18.9%) | 132 s |

Protocol checks:
- Effort on the wire was verified for every arm (`reasoning_effort_sent`), with a distinct reasoning-token level each.
- Luna low/flex (reference) agreed with Muse HIGH on 60% of clean pairs and approved 30/60 requests, while Muse
  HIGH approved 0/60. It is a different decision-maker, not a cheaper Muse.

**Verdict: keep HIGH.** No lower effort met the pre-registered rule on valid data:
- `muse_minimal` fails outright (73% clean agreement).
- `muse_low` is below the noise floor minus 5 pp at n=15.
- `muse_medium` is 12/12 but n=12 cannot establish non-inferiority.

### Ranked optimization table

| Rank | Optimization | Tokens / request | USD / 1,000 requests | Basis | Status |
|---|---|---|---:|---|---|
| 1 | Reasoning effort → minimal | ≈8.6K analyst reasoning fewer | ≈$2.20 | measured (12 requests) | **rejected** — fails agreement |
| 2 | Reasoning effort → low | ≈7.5K analyst reasoning fewer | ≈$1.63 | measured (12 requests) | not adopted — non-inferiority unproven |
| 3 | `compact_v1` lossless catalog encoding | ≈10.9K input fewer (paired medians) | ≈$1.09 (≈$1.40 by means) | measured (exact probe + A/B) | **implemented, opt-in**; default flip awaits A3 |
| 4 | Reasoning effort → medium | ≈3.6K analyst reasoning fewer | ≈$0.78 | measured (12 requests) | not adopted — n too small |
| 5 | Phase-1 prefix session (cache reads) | ≈7.5K of ≈79K input at cached rate | ≈$0.74 | measured ratio × clean input | in production since Phase 1 |
| 6 | Dereference duplicated catalog values (class a) | ≈1,070 | ≈$0.11 | estimated | not implemented — citation risk |
| 7 | Critic/adjudicator id list out of instructions | ≈300–400 + larger cached prefix | ≈$0.04 | estimated | not implemented — needs own A/B |
| 8 | Numeric precision trimming | ≈38 per payload | ≈$0.01 | estimated (0 provable) | not implemented |
| 9 | Output field trimming | 0 | $0 | measured (0 unconsumed fields) | nothing to trim |

### Theoretical ceilings

- **Input, lossless.** `compact_v1` removes ≈20% of analyst input. What remains is prompt-named contract
  structure, informative prose/labels and values. The further candidates (rows 6–8) total ≲3%, and each changes
  what the model reads.
- **Output, unconsumed:** 0 tokens.
- **Reasoning.** Lowering effort to low would cut analyst reasoning ≈80% and per-request cost ≈14% at measured
  rates. That ceiling is only reachable if a completed benchmark proves non-inferiority; on this evidence it is not.

### Risks and explicit uncertainties

- Go usage is budgeted per account: orchestration, benchmarks and the live gate share one weekly allowance.
  Only the console shows the exact split.
- The benchmark is incomplete: 264 refused attempts, and clean N of 9–20 per arm. No decision-quality claim is
  made beyond the intervals shown.
- Stage-A attribution uses a calibrated proxy. Every saving claimed for `compact_v1` uses exact provider counts.
- The benchmark replays archived requests (historical deadline neutralized, isolated shadow ledger), so absolute
  decision rates are not live rates. Arm-vs-arm comparisons are like-for-like. The production shadow-repeat
  sampling (2%) was active inside the children, due to the `.env` override; it applied equally to all arms.
- `compact_v1` changes how the model reads the catalog. Losslessness is proven; non-inferiority is not yet. If
  enabled, `instructions_sha256` in the attempt ledger identifies which encoding each live call used.
- The isolation breach was contained. The live bus and shadow ledger were untouched, and the two affected
  production files were restored by removing exactly the benchmark rows, with backups. Other readers of those
  files were not running.
