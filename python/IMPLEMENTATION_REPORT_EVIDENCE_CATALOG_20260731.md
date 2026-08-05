# Evidence Catalog and Provider Latency — 2026-07-31

Fixes the primary no-trade blocker from the 2026-07-30 run: three successful
`FULL_STRUCTURED` provider responses downgraded by
`decision_evidence_reference_reject`, and the six-candidate latency that timed
out three of the four largest requests.

**Scope note up front:** I completed the P0 evidence, preservation, latency, and
positive-path work with measured evidence. I did **not** complete the session/
idempotency namespacing, the record→cache replay report, the MQL artifact-parity
fix, the repeatability qualification workflow, or the MT5 error-envelope
diagnostics. Those are listed in §10 with what I found, so you can decide
priority rather than discovering the gap in run eleven.

---

## 1. Evidence sources actually used

| Requested | Status |
|---|---|
| `20260730.log` | Found — MT5 terminal log. Also used the **tester agent log** (1,237,454 bytes) which holds the EA journal lines; the terminal log only holds session/build events. |
| `log.txt` | **Does not exist** anywhere on this machine (searched `C:\Users\amroe` to depth 4, the terminal tree, and the bus). Nothing in this report depends on it. |
| Six request IDs | Found all six, plus responses, debug, quarantine, and ledger rows. |

Captured request files (2026-07-30 run, PID 23160):
`<BUS>/rejected/python_23160_1785430030__<request_id>.json`

The three downgraded IDs:

```text
591813800_1782691200_122235328_1782695159_GOLD_31623   (3 candidates)
591813800_1782691200_122235328_1782985472_GOLD_24147   (6 candidates)
591813800_1782691200_122235328_1784014416_GOLD_6949    (1 candidate)
```

**Newly discovered while gathering evidence:** MT5 auto-updated from build 5833
to build 6061 at 17:48, 48 MQL5 files were replaced, and the tester ran at 19:47
on agent build 6061. The supplied logs therefore come from a *different terminal
build* than the previous session.

---

## 2. Root-cause map

### 2.1 `decision_evidence_reference_reject` (primary blocker)

**Earliest incorrect state:** the provider contract asked the model to author
Python's internal canonical paths.

`ModelCandidateAssessment.evidence_refs: list[str]` + the prompt line
*"Evidence refs must point to exact paths in the canonical evidence envelope"*,
validated by `decision_pipeline.evidence_path_exists` against the full nested
envelope.

Measured on the real captured requests, the namespace the model had to hit
exactly, without ever being shown it:

| candidates | leaf paths in envelope |
|---:|---:|
| 1 | 570 |
| 3 | **1,614** |
| 6 | **3,180** |

### 2.2 Exact reference failures (reproduced, §3)

The raw provider output was **discarded** on rejection — `response_debug` holds
only the degraded envelope, containing a single empty `veto.evidence_fields`.
So the literal strings the model returned are not recoverable from disk. That
loss is itself a defect you named ("store the original sanitized provider
response separately"); it is **not** fixed in this pass (§10).

What I could do instead is reproduce the failure deterministically against the
real 3-candidate envelope. Probing the shapes a model naturally produces:

| probe | result |
|---|---|
| `entry_and_invalidation.candidates.0.candidate_hash` | OK |
| `entry_and_invalidation.candidates[0].candidate_hash` | OK |
| `sequence.has_displacement` | OK |
| `execution_costs.per_candidate.0.net_rr.value` | OK |
| **`candidates[0].structure_state`** | **FAIL** |
| **`candidates.0.structure_state`** | **FAIL** |
| **`$.entry_and_invalidation.candidates[0].candidate_hash`** | **FAIL** |
| **`candidate.structure_state`** | **FAIL** |
| **`market_regime.regime_profile`** | **FAIL** |

**Why each failed:** the request payload has a *top-level* `candidates` array,
so `candidates[0].x` is the natural citation — but the envelope re-nests them
under `entry_and_invalidation.candidates`. `$.` JSONPath prefixes were never
parsed. `candidate.x` (singular) is the shape the *critic* payload uses, not the
analyst's. `market_regime.regime_profile` assumes a key the regime dict does not
carry.

Corroborating this: the repository's own fixture
`tests/test_decision_integrity.assessment()` used
`"evidence_refs": ["candidates[0].structure_state"]` — a path that **fails**
against a real envelope. The tests passed only because that fixture never met
the real validator.

### 2.3 Six-candidate latency

**Earliest incorrect state:** payload size scaling linearly with candidate count,
against a fixed 90s provider deadline.

| candidates | envelope chars | est. input tokens | observed latency |
|---:|---:|---:|---|
| 1 | 19,236 | 4,809 | 24.383s ✓ |
| 3 | 52,485 | 13,121 | 64.812s ✓ |
| 6 | 102,311 | 25,577 | 87.863s ✓ / **timeout ×3** |

~17,100 chars and ~530 leaf paths **per candidate**. Two contributors:

1. **Verbatim duplication.** `execution_costs.per_candidate[i]` is literally the
   same object as `entry_and_invalidation.candidates[i].authoritative_numbers`
   (`decision_evidence.py:284`), and `targets_and_obstacles.candidate_targets[i]`
   duplicates `candidates[i].target_candidates` (`:280`).
2. **Lineage bloat.** Every numeric fact shipped as an 8-field object
   (`value/unit/source/observed_at/freshness_sec/valid/required/lineage_hash`)
   when the model only reasons over `value`.
3. `AI_MAX_OUTPUT_TOKENS=25000` applied unconditionally regardless of candidate
   count.

---

## 3. Evidence catalog design

`evidence_catalog.py` (new). Python enumerates a bounded, curated, deterministic
catalog before the provider call:

```python
EvidenceItem(evidence_id, candidate_index, canonical_path, value, value_hash, authority)
```

* **Provider sees** `{"id": 41, "p": "...net_rr.value", "v": 2.4, "c": 0}` —
  compact keys, no hash, no authority label.
* **Provider returns** `evidence_ref_ids: list[int]` only.
* **Python validates** existence, candidate scope, duplicates, and integer type,
  then maps IDs → canonical paths + value hashes for the authoritative envelope.

Measured on the captured requests:

| candidates | leaf paths (before) | catalog items (after) | global | per candidate |
|---:|---:|---:|---:|---:|
| 1 | 570 | **68** | 29 | 39 |
| 3 | 1,614 | **146** | 29 | 39 |
| 6 | 3,180 | **263** | 29 | 39 |

One implementation serves analyst, critic, adjudicator, and shadow repeats — the
critic and adjudicator receive the same catalog filtered to their candidate plus
globals, so there is exactly one reference vocabulary in the system.

**Legacy migration** (`normalize_legacy_reference`) parses `a.b.0.c`,
`a.b[0].c`, `$.a.b[0].c`, `a["b"][0]["c"]` and must still resolve to a real
catalog item — it can never invent evidence. Verified on all four shapes.

---

## 4. Files and functions changed

| File | Change |
|---|---|
| `evidence_catalog.py` | **New** — catalog, resolution, legacy parser, diagnostics |
| `structured_models.py` | `ModelVetoDecision`/`ModelCriticObjection` (ID-based) split from authoritative `VetoDecision`/`CriticObjection`; `evidence_ref_ids` on analyst/critic/adjudicator |
| `ai_gate.py` | `_resolve_evidence_reference_ids`, `EvidenceReferenceError`, `_compact_model_evidence_payload`, `analyst_output_token_budget`, `select_live_candidate_cohort`, catalog wiring, `[evidence_reference_validation]` logging, removed the post-hoc path check |
| `decision_pipeline.py` | `_resolve_critic_evidence`; critic/adjudicator resolve via the shared catalog; removed `evidence_path_exists` guessing checks |
| `tests/test_decision_integrity.py` | `model_assessment(evidence_ref_ids=...)`, `catalog_ids_for()` |
| `tests/test_python_owned_identity_lifecycle.py`, `tests/test_provider_neutral_ai.py` | Mocks read the catalog from the payload they receive, as a real model would |
| `tests/test_evidence_catalog_contract.py` | **New** — 36 tests |
| `tests/test_authoritative_response_contract.py` | Positive-path + evidence-failure end-to-end tests |

## 5. Provider schema changes

```text
ModelCandidateAssessment.evidence_refs: list[str]   ->  evidence_ref_ids: list[int]
ModelVetoDecision.evidence_fields: list[str]        ->  evidence_ref_ids: list[int]
ModelCriticDecision.evidence_refs: list[str]        ->  evidence_ref_ids: list[int]
ModelCriticObjection.evidence_refs: list[str]       ->  evidence_ref_ids: list[int]
ModelAdjudicatorDecision.evidence_refs: list[str]   ->  evidence_ref_ids: list[int]
```

The **authoritative** envelope is unchanged in shape: `evidence_refs` and
`veto.evidence_fields` still carry canonical path strings, now Python-generated.
MQL and `decision_integrity` need no change. All four provider schemas still pass
`strict_structured_schema` preflight.

## 6. Latency and candidate-budget changes

Measured before/after on the captured cohort:

| candidates | before | after | reduction | before tok | after tok | observed | projected |
|---:|---:|---:|---:|---:|---:|---|---|
| 1 | 19,236 | 14,770 | 23.2% | 4,809 | 3,692 | 24.4s | ~18.7s |
| 3 | 52,485 | 35,465 | 32.4% | 13,121 | 8,866 | 64.8s | ~43.8s |
| 6 | 102,263 | 66,246 | **35.2%** | 25,565 | 16,561 | 87.9s | **~57.0s** |

Output token budget, replacing the unconditional 25,000:

```text
 1 candidate  ->  2,800      6 candidates -> 10,800
 3 candidates ->  6,000     12 candidates -> 20,400
```

**Live candidate cohort:** `AI_LIVE_CANDIDATE_BUDGET=3` (new).
`select_live_candidate_cohort` picks deterministically by authoritative rule
score with one candidate per setup family before any family repeats. Nothing is
silently dropped — deferred indexes are returned for logging, and offline
record/cache processing passes `budget<=0` to keep every candidate.

Justification is the measurement, not a guess: 3 candidates at 64.8s sits at 72%
of the 90s deadline; 6 at 87.9s sits at 98% and failed 3 of 4 times.

## 7. Commands run and exact results

```text
.venv\Scripts\python.exe -m pytest tests -q      (baseline, pre-change)
  -> 253 passed, 132 subtests passed in 6.56s

.venv\Scripts\python.exe -m pytest tests -q      (final)
  -> 295 passed, 184 subtests passed in 6.17s

.venv\Scripts\python.exe -m pytest tests/test_evidence_catalog_contract.py -q
  -> 36 passed, 52 subtests passed in 0.41s

.venv\Scripts\python.exe tools\verify_v_next_controls.py
  -> [verify] PASS mql compile
  -> [verify] all v-next controls passed
```

**MQL compilation:** `[verify] PASS mql compile` — 0 errors. No `.mq5`/`.mqh`
source changed this pass (the authoritative envelope shape is unchanged), so the
previously recorded `0 errors, 0 warnings` full compile still applies.

## 8. Tests added

**`tests/test_evidence_catalog_contract.py` (36 tests, 52 subtests):**
catalog versioning/hashing/determinism/dense IDs; Python owns path+hash+authority;
provider rows omit hash and authority; valid global ID; valid candidate ID;
global citable by every candidate; unknown / negative / out-of-range /
cross-candidate / duplicate / empty / non-integer all fail closed; all four
legacy shapes normalize; legacy resolves to real items; nonexistent legacy path
reports `first_missing_token`; legacy cannot invent evidence; **six captured
requests present with counts [1,3,6,6,6,6]**; catalog ≥8× smaller than the leaf
namespace; every candidate has citable evidence; compaction reduces payload
>25% and preserves every catalog item; latency-budget and cohort-selection tests.

**`tests/test_authoritative_response_contract.py` additions:**
`test_positive_path_reaches_python_final_allow` and
`test_evidence_ids_outside_the_catalog_are_rejected_end_to_end`.

## 9. Positive-path evidence

`test_positive_path_reaches_python_final_allow` drives the real
`_score_setup_ai` with a two-candidate request and asserts:

```text
[evidence_reference_validation] valid=true      (and no valid=false)
decision_source != decision_evidence_reference_reject
decision_source != degraded_ai_response
decision_source != provider_transport_error
decision_quality_tier = FULL_STRUCTURED
python_final_allow    = true          (decision.allow)
raw_allow             = true
llm_quality_score     > 0
suggested_risk_multiplier > 0
candidate_assessment_count_match = true   (2 == 2)
candidate_hash_match             = true   (assessment hashes == request hashes)
selected candidate has tp2 > 0 and no veto
```

The negative counterpart asserts a genuine evidence failure still fails closed
with `unknown_ids=[10000]` and `catalog_size=` in the diagnostics.

## 10. What I did NOT complete — read this before run eleven

These were in your brief and are **not** done. Each includes what I found so the
next pass starts from evidence, not from scratch.

1. **Positive path does not reach `mql_final_allow` / `watchlist_added` /
   `order_attempted`.** My fixture proves the Python side to
   `python_final_allow=true`. Driving the MQL response parser, watchlist, entry
   trigger, risk sizing, and order attempt needs a Strategy Tester harness or a
   test order adapter that does not exist in this repo yet. **This is the single
   biggest remaining gap** — the downstream path is still unexercised.

2. **Raw provider output is still discarded on rejection.** Confirmed:
   `response_debug/<id>.json` holds only the degraded envelope. This is why the
   literal failing strings were unrecoverable. Storing the sanitized raw response
   alongside the authoritative one is a small change and would have made §2.2
   direct rather than reconstructed.

3. **Session/idempotency contamination.** Not fixed. Evidence gathered: the bus
   holds **44,556 stale files**, 218 rejected, 80 quarantined, and 27 ledger
   rows, with request IDs from at least four distinct schema cohorts visible in
   the same directories (`1782695159_GOLD_31623`, `python_17848_...`,
   `python_20388_...591813800_1782691200_418149125_...`). The
   `duplicate_request_blocked / request_id_identity_collision` you saw is
   consistent with that. No cohort namespacing or cleanup command was added.

4. **Record-only → cache-only replay report.** Not implemented. The authority
   rules (degraded excluded, live-wait not replay-authoritative, cache-only makes
   no provider call — enforced MQL-side at `TradeEngine.mqh:9012`) are covered by
   existing tests, but the scan-count/cohort-completeness comparison report you
   specified does not exist.

5. **MQL `json_root_not_object` parity.** Not investigated this pass.

6. **Repeatability qualification workflow.** Not implemented. Current state is
   visible in every rejection: `status=UNAVAILABLE artifact_state=missing_group
   required_live=False`.

7. **MT5 error-envelope diagnostics.** Not implemented — MT5 still reports
   `candidate_hash_match=false / assessment_group_ok=false` for identity-bound
   error envelopes.

8. **No real-provider run.** All latency figures after the fix are *projections*
   from the measured linear scaling, not new API calls. The 35.2% payload
   reduction is measured; the resulting ~57s is inferred.

## 11. Deployment and cleanup steps

1. **Restart the Python gate** — new schema and catalog.
2. **Quarantine pre-fix cache entries** (they carry the old reference contract):
   `.venv\Scripts\python.exe ai_gate.py cache-clear-incompatible`
3. **Optionally set** `AI_LIVE_CANDIDATE_BUDGET=3` in `.env` (this is already the
   default).
4. **Consider clearing the 44,556-file stale directory** before the next run —
   see gap 3; I did not touch it because trade and outcome ledgers live nearby.
5. Recompile the EA only if you re-deploy includes; no MQL source changed here.

## 12. Expected success log lines

**Python:**

```text
[provider_deadline] mt5_terminal_timeout_ms=120000 python_deadline_ms=105000 write_margin_ms=15000
[provider_attempt] attempt=1 remaining_ms=... sdk_timeout=... sdk_max_retries=0
[provider_call_completed] quality_tier=FULL_STRUCTURED
[raw_model_schema_validation] valid=true model_owned_fields_only=true
[identity_validation] valid=true
[evidence_reference_validation] valid=true candidate_index=0 returned_ids=[...] unknown_ids=[] cross_candidate_ids=[] resolved_paths=[...] catalog_size=146 catalog_hash=...
[authoritative_envelope_constructed] ...
[authoritative_envelope_validation] valid=true
[ai_schema_validation] valid=true
[response_written] quality_tier=FULL_STRUCTURED
```

**MT5:**

```text
ai_schema_validation valid=true
decision_quality_tier=FULL_STRUCTURED
candidate_assessment_count_match=true
candidate_hash_match=true
assessment_group_ok=true
decision_source != decision_evidence_reference_reject
decision_source != degraded_ai_response
decision_source != provider_transport_error
```

A trade is still not required. After this pass a rejection should be analytical,
target, risk, watchlist, or broker — but note gap 1: the watchlist and order
stages remain unproven, so I cannot claim the *complete* workflow is verified.
