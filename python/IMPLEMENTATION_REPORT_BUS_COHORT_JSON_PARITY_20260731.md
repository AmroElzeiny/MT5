# Bus Cohort Isolation and Python/MQL JSON Parity — 2026-07-31

Continues the zero-trade incident. Two of the five outstanding workstreams are
complete with evidence. **Three are not**, and the readiness verdict is
**NOT READY** — see §8.

---

## 1. Readiness verdict (stated first, because it gates everything else)

```text
VERDICT: NOT READY
```

Not ready for a controlled tester run, not ready for demo forward, not ready for
live. The blocking reason is unchanged from the previous report and is the one
thing you asked me not to leave open:

> `mql_final_allow=true`, `watchlist_added=true`, and `order_attempted=true`
> remain **unproven**. No MQL positive-path harness exists.

Everything below is real progress, but none of it substitutes for that.

---

## 2. Completed — bus cohort isolation

### 2.1 The measurement

`bus_cohort_migration.py` (new) classifies every artifact by its true contract
cohort. Dry-run against the live bus:

```text
total files      : 50,867
compatible       : 36
incompatible     : 50,831        (99.93%)
protected        : 0 encountered in migratable dirs
errors           : 0
current cohort   : b24fa48d14c47f31
                   engine   5.5-version-z-canonical-request-20260724-v8
                   schema   20260724_canonical_frozen_request_v10
                   identity 20260724_ai_request_identity_v3
                   prompt   20260724_canonical_frozen_request_v12
                   manifest 1024838206
```

**Only 36 of 50,867 files belong to the build you are running.** My previous
report said "44,556 stale files, at least four schema cohorts"; the accurate
figure is 50,867 files and many more cohorts, including genuinely ancient ones.

Sample of the distinct cohorts found:

| cohort_hash | files | engine | decision schema |
|---|---:|---|---|
| `fa91e38c79b97690` | 9 | legacy_pre_contract | `20260724_python_owned_identity_v9` |
| `fd8e9c326dcb7e65` | 2 | legacy_pre_contract | legacy_pre_contract (prompt `2025-06-30`) |
| (bulk) | ~50,800 | legacy_pre_contract | legacy_pre_contract |

The bulk is 2023-era material — real example found in `stale/`:

```json
{"id": "1690506000_XAUUSD+_17355", "allow": true, "score": 7.35, "chosen_index": 0, ...}
```

`XAUUSD+`, epoch 1690506000 (2023-07-28), and a response schema of four fields:
no `decision_quality_tier`, no `request_identity_hash`, no contract manifest.

### 2.2 Correction to my previous report

My earlier probe reported "289/300 stale files UNREADABLE". **That was my bug,
not the data's**: the probe decoded only `utf-8-sig`, while much of the bus is
UTF-16. The files are readable. This matters because it is the *same* defect
that causes the JSON parity failure in §3 — I had the evidence in hand and
misread it.

### 2.3 What was built

* `read_json_any_encoding` — UTF-8 / UTF-8-BOM / UTF-16-LE / UTF-16-BE.
* `CohortIdentity` / `cohort_identity_of` — cohort hash from engine, decision
  schema, identity schema, prompt contract, and manifest hash. Deliberately
  **not** session-scoped, so two runs of the same build can still share a valid
  cache entry while a different build cannot.
* `classify_bus_file` — `compatible` / `incompatible` / `unparseable` /
  `protected`.
* `migrate_bus_cohorts(dry_run=...)` — archives to
  `archive/<date>/<cohort_hash>/<kind>/`. **Nothing is ever deleted.**
* `PROTECTED_RELATIVE_PATHS` — `completed_ai_trades.jsonl`, `trade_results`,
  `outcomes`, `ledger`, `logs/archive` are never moved or rewritten.
* CLI: `ai_gate.py bus-cohort-inventory` and `ai_gate.py bus-cohort-migrate`.

### 2.4 Known limitation of the CLI

`ai_gate.py bus-cohort-inventory` did not complete in 10 minutes because
`main()` performs full file-bus recovery over all 50,867 files *before* reaching
command dispatch. I obtained the numbers above by calling
`migrate_bus_cohorts` directly. **The command dispatch should be moved ahead of
bus recovery** — not done in this pass.

---

## 3. Completed — Python/MQL JSON parity (`json_root_not_object`)

### 3.1 Root cause, at byte level

`FileBus.mqh::ReadText` opened files with:

```mql5
FileOpen(rel_path, FILE_READ|FILE_TXT|FILE_COMMON|...)
```

`FILE_TXT` with **neither `FILE_ANSI` nor `FILE_UNICODE`** defaults to **UTF-16**
in MQL5. Measured on the live artifacts:

| artifact | bytes | BOM | first bytes | root char |
|---|---:|---|---|---|
| `responses/*.json` | — | UTF-16-LE | `ff fe 7b 00` | `{` ✓ |
| `config/deployment_manifest.json` | 2,292 | none | `7b 22 ...` | `{` |
| `config/normalized_fvg_policy.v2.json` | 1,067 | none | `7b 22 ...` | `{` |
| `config/risk_factor_policy.v1.json` | 2,715 | none | `7b 22 ...` | `{` |
| `config/invalidation_policy.v1.json` | 961 | none | `7b 22 ...` | `{` |

Python writes **responses** as UTF-16 (`RESP_ENCODING = "utf-16"`, ai_gate.py:724)
— MQL reads those fine. Python writes **config/policy/deployment** artifacts as
UTF-8, and hand-authored config is UTF-8 too. The UTF-16-defaulting reader then
interprets `{"` (`0x7B 0x22`) as the single character `0x227B`, which is not
`{`, so `JsonValidateDocumentStrict` (JsonLite.mqh:29) returns
**`json_root_not_object`** on a file Python considers perfectly valid.

Same reader, two writer encodings. That is the whole defect.

### 3.2 Fix

`FileBus.mqh::ReadText` now reads `FILE_BIN` and decodes via new
`DecodeTextBytes`, handling UTF-16-LE/BE with BOM, UTF-8 with BOM, UTF-8
without BOM, and no-BOM UTF-16-LE (ASCII byte followed by a zero high byte).
This mirrors Python's `read_json_any_encoding`, so both sides decode identical
bytes to identical text.

I chose to fix the **reader** rather than rewrite your hand-authored config to
UTF-16: that fixes every artifact including ones Python never writes, and does
not touch files you maintain.

**MQL5 compile: `Result: 0 errors, 0 warnings, 169305 msec`.**

### 3.3 Not done in this area

Items 5, 6, and 7 of your JSON-parity brief — no partial-read guard beyond the
existing atomic write, no pre-MT5-startup generation ordering, and no
ready-marker handshake. Artifacts are still written whenever the gate starts.

---

## 4. Files and functions changed

| File | Change |
|---|---|
| `bus_cohort_migration.py` | **New** — `read_json_any_encoding`, `CohortIdentity`, `cohort_identity_of`, `classify_bus_file`, `iter_bus_files`, `MigrationSummary`, `migrate_bus_cohorts` |
| `ai_gate.py` | `current_bus_cohort_identity()`; `bus-cohort-inventory` / `bus-cohort-migrate` commands |
| `MT5_PO3_Codex Include/FileBus.mqh` | `ReadText` rewritten binary+decode; new `DecodeTextBytes` |
| `tests/test_bus_cohort_and_parity.py` | **New** — 24 tests, 14 subtests |

## 5. Tests

`tests/test_bus_cohort_and_parity.py` — **24 passed, 14 subtests**:

* **Encoding reader (5):** UTF-8, UTF-8-BOM, UTF-16-LE-BOM, UTF-16-LE no BOM,
  unreadable bytes raise.
* **MQL decoder parity (3):** `ReadText` no longer uses encoding-defaulting
  `FILE_TXT`; `DecodeTextBytes` handles every BOM plus `CP_UTF8`; Python and MQL
  agree on the root-token rule.
* **Artifact byte parity (2):** all four live artifacts decode to JSON objects
  and their first decoded character is `{` — the exact `JsonValidateDocumentStrict`
  condition.
* **Cohort classification (7):** current compatible; old schema incompatible;
  the real 2023 `{id,allow,score,chosen_index}` shape classified
  `legacy_pre_contract`; UTF-16 artifact classified, not treated as corrupt;
  non-object root → `json_root_not_object`; cohort hash stable and
  discriminating; session id does not change cohort.
* **Migration (7):** dry-run moves nothing; archive moves only incompatible;
  empirical history never archived; archive grouped by cohort; idempotent;
  after migration nothing incompatible remains live; summary serialisable.

**Full suite: 319 passed, 198 subtests** (previous session ended at 295/184).

**MQL compile: 0 errors, 0 warnings.**

---

## 6. Bus migration report

Dry-run is complete and reported in §2.1. The **real archive run was still
executing when I stopped** — it moves ~50,831 files and takes roughly the same
~10 minutes the dry-run took. Its report is written to
`<BUS>/logs/bus_cohort_migration.json` when it finishes.

I therefore **cannot yet show you** post-migration `recovered=0` or a clean
startup with no `request_id_identity_collision`. That proof step is outstanding.

---

## 7. Deployment and run instructions

```powershell
cd c:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python

# 1. Confirm the archive migration finished, then re-inventory.
#    Expect: incompatible == 0, compatible == small number.
$env:PYTHONPATH = $PWD
.\.venv\Scripts\python.exe -c "import os,json;from pathlib import Path;from bus_cohort_migration import *;from compatibility_manifest import *;from decision_integrity import *;bus=Path(os.environ['APPDATA'])/'MetaQuotes/Terminal/Common/Files/PO3_AI_BUS';print(json.dumps(migrate_bus_cohorts(bus,CohortIdentity(str(compatibility_manifest()['engine_version']),AI_DECISION_SCHEMA_VERSION,AI_REQUEST_IDENTITY_VERSION,AI_PROMPT_CONTRACT_VERSION,compatibility_manifest_hash(),''),dry_run=True).as_dict()['counts']))"

# 2. Recompile the EA (FileBus.mqh changed).
#    Already done here: 0 errors, 0 warnings.

# 3. Start the gate and confirm clean recovery.
.\.venv\Scripts\python.exe ai_gate.py
#    Expect: [file_bus] ... recovered=0
```

Do **not** start a demo-forward or historical run yet — see §8.

---

## 8. What is still NOT done

These were in your brief. None is started beyond investigation.

1. **MQL positive-path harness — the blocking gap.** No test EA, no test order
   adapter, no Strategy Tester fixture. `mql_final_allow`, `watchlist_added`,
   and `order_attempted` remain completely unproven, as do all nine negative
   fixtures you specified. Everything downstream of the Python response write is
   still untested code.

2. **Record-only → cache-only replay.** Not run. Requires two full Jun 29–Jul 19
   Strategy Tester runs plus offline cohort processing between them. No cohort
   manifest, no processing report, no replay report, no funnel comparison.

3. **Repeatability qualification.** The `UNQUALIFIED / SHADOW_COLLECTING /
   QUALIFIED / STALE / INCOMPATIBLE` state machine does not exist. Startup still
   reports `status=UNAVAILABLE artifact_state=missing_group`, and there is no
   offline qualification command.

4. **Real-provider latency benchmark.** Not run. The 35% payload reduction from
   the previous session is measured; the resulting ~57s remains a **projection**.
   Running this costs real API spend across 1/3/6-candidate configurations with
   enough samples for P90/P95 — I did not spend your money without you saying so
   explicitly. Say the word and I will run it.

5. **Error-envelope diagnostics.** MT5 still runs normal candidate-assessment
   matching against identity-bound error envelopes and logs the misleading
   `candidate_hash_match=false` / `assessment_group_ok=false`.

6. **CLI dispatch ordering** (§2.4) — `bus-cohort-inventory` is unusable in
   practice until command dispatch moves ahead of bus recovery.

---

## 9. Honest assessment

Two workstreams are genuinely finished and independently verifiable: the bus is
now classifiable and archivable with a tested, non-destructive tool, and the
`json_root_not_object` divergence has a byte-level root cause, a compiled fix,
and regression tests.

But you asked me not to declare readiness until the complete real execution path
is proven, and it is not. The single highest-value next step is item 1 — the MQL
harness — because until it exists, every run is a gamble on untested code
between `response_written` and `OrderSend`.
