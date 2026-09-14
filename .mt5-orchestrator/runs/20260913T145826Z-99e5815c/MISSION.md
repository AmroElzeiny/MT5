# Mission

Front-end: Claude
Tier: Standard
RuntimeMode: Offline

## Outcome

OpenCode Go cost work, **Phase 2, stage A (measurement and design only)**.

Produce a quantitative, evidence-backed account of where every billable token of one intended
Muse (`muse-spark-1.3-contributor`) request goes -- input by payload subtree, output by field --
plus the reconciliation of the "USD 11.15 repriced vs USD 30 weekly limit" gap, and a concrete
lossless compaction design the front-end will judge before any production change.

**This stage changes no production behaviour.** The front-end writes stage B (implementation)
from this stage's measurements. The live reasoning-effort benchmark is stage C and is out of
scope here (see RSN-001).

Phase 1 changes (stable `x-opencode-session` per model+schema, attempt ledger
`opencode_go_attempts.ndjson`, `python/opencode_go_accounting.py`,
`python/tests/test_opencode_go_cost_correctness.py`, `OPENCODE_GO_BILLING_EVIDENCE.md`) are
already in the working tree, uncommitted. Do not undo, rewrite or reformat any of them.

## Confirmed baseline (front-end verified 2026-09-13, do not rediscover)

Accounting (from `OPENCODE_GO_BILLING_EVIDENCE.md`, read it first; it is the Phase-1 result):
- Muse, this Windows host, 2026-09-07 00:00 -> 09-13 12:27 UTC (weekly 429): 2,454 responses,
  70,763,331 uncached input, 260,468 cached read, 20,381,594 output of which 16,015,176 reasoning,
  repriced USD 11.153 at 0.10 / 0.20 / 0.002 per 1M. Published weekly limit USD 30.
- 429 body at ~12:28 UTC: `limitName: 'weekly'`, "Resets in 11hr 32min" -> 2026-09-14 00:00 UTC.
  Earlier Muse 429s from 2026-09-11 16:50 UTC had their `limitName` truncated in logs; at that
  moment local repriced usage was only USD 7.79.
- No Muse traffic from this host on 09-07, 09-08, 09-12. First Muse call 09-09 16:50 UTC.
- `opencode stats --days 14 --models 40 --project ""` run by the front-end at 2026-09-13 14:53 UTC:
  the local OpenCode CLI used only `minimax-m3`, `qwen3.8-flash`, `deepseek-v4.1-flash`
  (USD 1.40 total, one day). **Zero Muse usage from the CLI/TUI on this machine.**
- Commit `25cca426` ("Windows runtime export ... for merge into Linux instance") and
  `python/docs/runtime_env_template_opencode.txt` suggest a Linux/VPS deployment may exist. Its
  usage cannot be observed from this machine.
- Gate log lines are **local time (UTC+3)**; `openai_usage.ndjson` `ts_utc` is UTC. Do not mix them.

Data sources (bus = `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS`):
- `<bus>\logs\openai_usage.ndjson` (13.9 MB): one row per successful provider response;
  fields `request_id, provider_mode, provider_id, model, reasoning_effort, max_output_tokens,
  input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens, raw_usage, extra.role`.
- `<bus>\logs\ai_gate.log` (64 MB): `[provider_call_started]`, `[provider_call_completed]`,
  `[provider_call_failed]`, `[opencode_fallback]`. Filter, never dump.
- `<bus>\response_debug\*.json` (3,458 files, ~140 KB each): keys include `analyst_output`,
  `critic_output`, `adjudicator_output`, `provider_usage`, `candidate_assessments`,
  `target_comparison`, ... -> the real visible model outputs, for output attribution.
- `<bus>\completed\python_*__<request_id>.json` (~197 KB each) and `<bus>\rejected\`: archived MQL
  request payloads, the input for rebuilding real provider requests.
- Luna (`gpt-5.6-luna`, `reasoning_effort=low`, `service_tier=flex`) rows exist in the same usage
  ledger after Muse was rate-limited, e.g. adjudicator `output_tokens=195 reasoning=85`.

Request construction (read these, do not re-map the repo):
- Analyst system prompt: inline f-string `system_msg` in `python/ai_gate.py` ~4260-4297
  (inside `_score_setup_impl`). Critic/adjudicator prompts: `python/decision_pipeline.py`
  ~233-540 (`run_qualitative_consensus`).
- Evidence: `build_decision_evidence_envelope` (`python/decision_evidence.py:580`) ->
  `build_evidence_catalog` (`python/evidence_catalog.py:297`) ->
  `_compact_model_evidence_payload` (`python/ai_gate.py:3446`). Catalog rows are
  `{"id","p"(canonical path),"v"(value),"c"(candidate index)}` via `provider_rows()`
  (`evidence_catalog.py:219`). Each candidate row also gets `allowed_evidence_ref_ids` =
  **all global ids + its own ids** (`ai_gate.py:3521`), i.e. the global id list repeats per candidate.
  Phase 1 measured ~1,010 repeated values out of 1,162 catalog entries (~4K tokens/request):
  catalog `v` duplicates values that also sit in the envelope sections.
- Wire: `build_provider_exchange_contract` (`python/ai_provider.py:225`) freezes
  `evidence_json = json.dumps(evidence, sort_keys=True, separators=(",",":"), ensure_ascii=True)`
  and hashes the whole exchange (`contract_hash`). Responses kwargs built at `ai_provider.py`
  ~1644-1702: `instructions` = system prompt, `input` = one user message with evidence_json
  (+ optional image parts), `text.format` strict json_schema, `text.verbosity="low"`,
  `reasoning.effort`, then `_wire_responses_kwargs` (OpenCode hook ~3424, strips
  `prompt_cache_key`, sets `truncation=disabled`, session header).
  Session id derivation: `_opencode_prefix_session_id` (`ai_provider.py:3001`, hash of model +
  schema name + schema) vs rollback `_opencode_session_id(request_id)`.
- Output schemas: `python/structured_models.py` -- `ModelTargetArbitrationDecision` 163,
  `ModelCandidateAssessment` 224, `ModelAIGateOutput` 265, `ModelCriticObjection` 378,
  `ModelCriticDecision` 394, `ModelAdjudicatorDecision` 425. Output budget:
  `analyst_output_token_budget` (`ai_gate.py:3294`) + `OPENCODE_MUSE_REASONING_TOKEN_RESERVE`.
- Reasoning config: `OPENCODE_MUSE_REASONING_EFFORT` default `high` (`ai_gate.py:765-779`).
  Why HIGH: `CLAUDE.md` §4ac records it; the `.env` itself is off-limits (see SAF-001).
- Provider-reported cached prefix hits were exactly 6,513 tokens (analyst) and 1,265 (critic):
  these are exact calibration points for any local tokenizer count of the static prefix.
- How tests import `ai_gate` safely without network: follow
  `python/tests/test_opencode_go_cost_correctness.py` (lines 14-40, recording transports).

Contract sensitivity the design must respect:
- `AI_PROMPT_CONTRACT_VERSION` and the contract manifest hash are shared with MQL
  (`MT5_PO3_Codex Include/Config.mqh`, `decision_integrity.py`); a manifest drift quarantines
  every request (CLAUDE.md §4ab). Replay caches are keyed on prompt/schema identity (§4y/§4z).
- Evidence-id citation validation (`EvidenceCatalog.resolve`) and `allowed_evidence_ref_ids` are
  decision-integrity controls; they must survive any compaction.

## Requirements

- ACC-001 (Part 1): Reconcile the exact provider limit window. Establish from local evidence the
  earliest and latest Muse activity of every kind on this host for 2026-08-31 .. 2026-09-14 UTC:
  successful, failed (403/500/connection/timeout with elapsed time), late results, correction
  passes, 429-refused, capability/health probes that invoke inference (search the code for
  health/probe paths that send a Muse generation and whether they are logged to the usage
  ledger), front-end live probes on 2026-09-09 and 2026-09-13 (CLAUDE.md §4ac and the configuration
  circuit incident; they may be absent from the ledger), replays run against temporary buses
  (`%TEMP%\po3rep*`; check whether their usage rows went to a different ledger), tests that could
  hit the network, other `ai_gate.py` processes (current `Get-CimInstance Win32_Process`), and the
  local CLI (baseline above). Produce the three-way table: documented allowance vs locally
  reconstructed usage (with an explicit "unobservable" row for VPS/other keys/console) vs console
  (mark "not available locally -- user must read the OpenCode console"). State explicitly whether
  the 09-11 16:50 429s can be proven weekly/5-hour/monthly. Do not call anything a provider billing
  defect.
- IN-001 (Part 2): Build `python/tools/opencode_token_attribution.py` (read-only tool, no network,
  never writes to the bus). From a stratified sample of **at least 30 real archived requests**
  (stratify by candidate count 1/2/3+, symbol class, and role analyst/critic/adjudicator), rebuild
  the exact provider request each role would send (preferred: drive the real code path with an
  injected recording provider that captures `instructions`, `input`, `text.format`, `reasoning`,
  `max_output_tokens` and then aborts; run against a temporary bus copy; if the real path cannot
  be driven offline, reconstruct from the public builders and quantify the approximation). Count
  tokens with `tiktoken` `o200k_base` if importable from `python\.venv`, else a documented proxy;
  calibrate the ratio against the provider's 6,513 / 1,265 cached-prefix counts and against
  `input_tokens` of the same request ids in `openai_usage.ndjson`. Emit per-subtree tokens
  (avg, p50, p95, % of input, static/dynamic, duplicated yes/no) for at least: instructions per
  role, `text.format` schema per role, every top-level envelope section, `evidence_catalog.items`
  split into `id`/`p`/`v`/`c` and JSON punctuation, `allowed_evidence_ref_ids`, per-candidate rows
  and their largest subtrees (target candidates, authoritative numbers, family contract, bucket
  prior, shadow historical evidence, analogues), market/regime/po3/story blocks, timestamps, ids,
  labels/descriptions, repeated key names (count key-name tokens separately from value tokens),
  image parts if any. Include the critic/adjudicator payloads (what evidence subset they get).
- DUP-001 (Part 3): For every catalog `v` and every other repeated value, classify as
  (a) same canonical fact serialized twice, (b) distinct facts with an equal value, (c) alias,
  (d) catalog material, (e) contract-required. Prove (a) by path identity, not by value equality.
  Quantify tokens per class. Also quantify repeated key names in arrays of homogeneous objects
  (columnar candidates) and repeated per-item metadata.
- PREC-001 (Part 5): List every numeric field whose serialized form carries digits beyond its
  authoritative source precision (symbol digits / tick size from the MQL request, float noise
  such as `1.6063600000000001`, derived ratios). For each: field, example, source precision,
  proven-safe canonical form or "not provable". Token effect. No rounding of information-bearing digits.
- WIRE-001 (Part 4 + 9): Design, do not implement, a deterministic provider-wire projection
  `canonical envelope -> compact wire -> model` that keeps the internal frozen contract,
  `exchange.contract_hash` semantics and evidence-id validation intact. For each proposed
  transformation give: tokens saved (measured on the sample), losslessness argument, the
  round-trip/equivalence test that would prove it, where it must live (before or after
  `build_provider_exchange_contract`), whether it changes instructions text and therefore
  prompt-contract identity / MQL manifest / replay cache keys, and the static-prefix vs dynamic
  suffix placement (measure: static prefix tokens per role, first volatile byte position, whether
  `sort_keys=True` interleaves static and dynamic keys). Rank by safe saving.
- OUT-001 (Part 6): Output attribution from `response_debug` + usage ledger for the same sample
  and in aggregate: reasoning vs visible tokens per role; visible tokens per output field
  (tokenize each field of the stored `analyst_output`/`critic_output`/`adjudicator_output`); for
  **every** output schema field: consumer (file:function), whether it alters a trading/risk
  decision, persisted, telemetry-only, or never read. Identify echoed input values and prose not
  consumed downstream. Separately quantify how much of `max_output_tokens` is reasoning reserve vs
  used, and how visible output length correlates with reasoning tokens.
- RSN-001 (Part 7, stage C preparation only): Document why HIGH was chosen (evidence from
  CLAUDE.md §4ac, git log -S on `OPENCODE_MUSE_REASONING_EFFORT`, the 25,000-token incident).
  Freeze a benchmark request set: a manifest of >= 40 real archived request ids (include the known
  approvals AUDJPY_32618 and USDCHF_19925 from §4ac, known REJECT/ABSTAIN, 1/2/3+ candidates, the
  existing Luna decisions on the same ids where available) and a metric definition covering every
  Part-7 metric. **Make no provider call.** Muse is weekly-limited until 2026-09-14 00:00 UTC and
  every benchmark call spends the user's allowance; stage C needs explicit user authorization.
- SES-001 (Part 8): Fetch the current official OpenCode Go docs (https://opencode.ai/docs/go/ and
  any linked Responses/session page) and record retrieval date and exact quotes on
  `x-opencode-session`, caching, and stateful continuation (`previous_response_id`, `store`,
  conversation objects). Judge whether "one session per model+schema" is consistent with the
  documented meaning of a conversation, including concurrency across 2 workers and unrelated
  symbols. Measure from the attempt ledger / usage rows since the Phase-1 change (if any rows exist
  after 2026-09-13 13:00 UTC) the token-weighted cached-read ratio. No undocumented mechanism.
- REP-001: Write `OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md` at the repo root with all 13 sections the
  user asked for. Sections 1-7 and 12-13 filled from this stage's measurements; sections 8-11
  ("changes made", "before/after", "decision quality", "tests") explicitly marked
  `PENDING STAGE B/C` with what will be measured. Every number labelled `measured` (with source
  file and sample size) or `estimated` (with method). Include a ranked optimization table with
  measured tokens/request and projected USD per 1,000 intended calls at published rates, and a
  theoretical ceiling split into input-lossless, output-unconsumed, reasoning (unknown until C).
- TST-001: SMALL change class. `py_compile` the new tool. Run the tool once on the sample and save
  its raw outputs (JSON + CSV) under the run directory. No new tests in this stage. Final
  verification per BUDGET_POLICY: full Python suite once (`cd python; .\.venv\Scripts\python.exe -m
  pytest tests -q`), record counts; **no MQL compile needed** (no MQL change) -- state that.
- SAF-001: Offline. No provider/network inference calls of any kind (docs fetch for SES-001 is the
  only network use). Do not read, print or copy `python/.env` or any key. Do not start, stop or
  signal `ai_gate.py` processes or the MT5 terminal. Never write to the live bus; copy inputs to a
  temp directory. No change to `ai_gate.py`, `ai_provider.py`, `decision_evidence.py`,
  `evidence_catalog.py`, `decision_pipeline.py`, `structured_models.py`, any schema, prompt,
  contract/prompt version, MQL file, `.env`, or existing test. Do not use DeepSeek roles (its Go
  weekly limit was reached on 2026-09-13); use `minimax-m3` for supervision/review and
  `qwen3.8-flash` for the worker.

## Work packages

### WP1 -- Accounting reconciliation
Objective: ACC-001.
Runtime authority: none (read-only forensics).
Allowed scope: read-only commands; notes under the run directory.
Forbidden changes: all product files.
Acceptance evidence: the three-way table with per-row source, and exact filter commands used.
Change size: SMALL
Tests: none
Escalation triggers: evidence of a second consumer of the key on this machine.

### WP2 -- Token attribution tool and measurements
Objective: IN-001, DUP-001, PREC-001, OUT-001, cache-layout measurements for WIRE-001.
Runtime authority: none.
Allowed scope: new `python/tools/opencode_token_attribution.py`; outputs under the run directory.
Forbidden changes: every file named in SAF-001.
Acceptance evidence: raw JSON/CSV outputs, sample manifest (request ids + stratum), tokenizer
calibration numbers, per-field consumer table with file:function references.
Change size: SMALL
Tests: none (py_compile only)
Escalation triggers: the real request path cannot be driven offline without editing product code;
tokenizer calibration error > 15% against provider-reported counts.

### WP3 -- Design, session verification, benchmark set, report, verification, review
Objective: WIRE-001, SES-001, RSN-001, REP-001, TST-001; one independent logic review
(`minimax-m3`) of the report's numbers against the raw outputs and of the losslessness arguments.
Allowed scope: `OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md`, run-directory files.
Change size: SMALL
Escalation triggers: a proposed saving that cannot be proven lossless; any design needing a prompt
contract version bump (report it, the front-end decides).

## Budgets

- target wall clock: 90 minutes
- material model invocations: <= 8
- repair attempts per approach: <= 2
- normal low-cost model map; no vision; no DeepSeek (limit reached)

## Runtime safety

- RuntimeMode: Offline
- Demo authorization: NO
- Real-money/live mutation: forbidden

## Final proof

- requirement coverage per ID; changed/new files; exact commands; sample manifest; tokenizer
  calibration; full-suite counts; diff audit proving no product file changed besides the new tool
  and the report; reviewer findings; actual model IDs and budget used; explicit uncertainty
  (tokenizer proxy error, unobservable consumers, reconstruction approximation).
