# WP1 SPEC — `python/tools/opencode_reasoning_benchmark.py` + focused test

RuntimeMode: Offline. Paid provider calls are run by the **supervisor**, not by the
implementer. The implementer must never make a real network/provider call. All implementer
verification uses fake clients.

## Deliverables

1. NEW `python/tools/opencode_reasoning_benchmark.py` (stdlib + repo imports; no new deps).
2. NEW `python/tests/test_opencode_reasoning_benchmark.py` (fully offline, fake clients).
3. `python -m py_compile` must pass on both.

Do NOT edit any existing file. In particular do not touch `ai_gate.py`, `ai_provider.py`,
`decision_*.py`, `evidence_catalog.py`, `structured_models.py`, `opencode_go_accounting.py`,
schemas, prompts, `.env`, MQL, or any existing test.

## Hard safety

- Never read, print, copy or persist `.env`, API keys or workspace ids. The tool obtains
  credentials **only** by calling `po3_env.bootstrap_provider_env()` (the gate's own loader)
  and reading `ai_gate.AI_CONFIG` / `os.environ` at build time, never by opening the file.
- Never write to the live bus, `python/data/*`, `python/logs/*` or the shadow ledger.
- Never start/stop/signal `ai_gate.py` or MT5.
- No MQL changes.

## CLI

```
python tools/opencode_reasoning_benchmark.py manifest --run-dir <dir> [--bus <bus>] [--min-requests 60] [--seed 20260913]
python tools/opencode_reasoning_benchmark.py run --run-dir <dir> [--arms a,b,c] [--requests id1,id2] [--concurrency 6] [--mode dry|live] [--dry-requests 2]
python tools/opencode_reasoning_benchmark.py report --run-dir <dir>
```

- `--run-dir` defaults to the run directory containing this spec's parent
  `.mt5-orchestrator/runs/20260913T180610Z-eee8a0a8`.
- `--mode dry` (default for safety) uses a **fake client** and must not touch the network.
  `--mode live` uses real credentials via `bootstrap_provider_env()`. The supervisor runs
  live; the test/dry-run always uses dry.
- `--mode dry` must be refused if `OPENCODE_BENCH_ALLOW_DRY` unset? No: default dry is fine.
  Simply: `dry` never builds a real API key; it passes `api_key="offline-benchmark-not-a-secret"`
  and injects a `client_factory` returning a fake OpenAI-compatible client whose
  `responses.create` raises a sentinel after recording the kwargs, exactly like the stage A
  capture recipe. Actually for the benchmark we want a *successful* canned structured
  response so the pipeline completes; implement a fake client that returns a valid
  `ModelAIGateOutput`-shaped JSON (see "Fake dry client" below).

## Architecture — parent/child

`run` is the parent. For every `(request, arm)` pair it spawns a **fresh child process**
(`sys.executable -m tools.opencode_reasoning_benchmark _child --request-file ... --arm ... --out ...`)
with cwd = `python/`, bounded to `--concurrency` workers. Each child:

1. Sets isolation env **before importing ai_gate**:
   - `AI_USAGE_LOG_ENABLE=false`, `OPENAI_USAGE_LOG_ENABLE=false`
   - `AI_DECISION_CACHE_ENABLE=false`
   - `PO3_SHADOW_LEDGER_PATH=<temp run scratch>/shadow.jsonl`
   - `AI_TRADE_MEMORY_FILE=<copy of python/data/ai_trade_memory.sqlite3 in temp scratch>` if
     the source exists, else `<scratch>/trade_memory.sqlite3`
   - `AI_REQUEST_RESPONSE_FINGERPRINT_FILE=<scratch>/fingerprints.jsonl`
   - `AI_COST_REPORT_FILE=<scratch>/ai_cost_report.jsonl`
   - `PO3_AI_BUS_PATH=<temp run scratch>/bus`
   Do NOT set `PO3_DOTENV_FILE` in live mode (it must resolve to `python/.env`); in dry mode
   set it to a nonexistent path so no real key is loaded.
2. Calls `po3_env.bootstrap_provider_env()` (live only) so `.env` is loaded by the gate's own
   loader into `os.environ` **before** `import ai_gate`.
3. `import ai_gate`.
4. Neutralizes in-process writers:
   - `ai_gate.LOG_FILE = None`
   - `ai_gate.log = lambda _m: None`
   - `ai_gate._write_ai_cost_report = lambda *a, **k: None`
   - `ai_gate._append_fingerprint_record = lambda *a, **k: None` (guard if present)
   - `ai_gate._refresh_provider_health = lambda **k: {"healthy": True, "reason": "benchmark_injected"}`
   - `ai_gate._load_live_bucket_priors` left as-is (read-only)
   - `RequestDeadline.can_start_attempt = lambda self, now=None: True`
   - `RequestDeadline.provider_timeout_sec = lambda self, configured, now=None: float(configured)`
5. Builds the arm's leg (below) and sets `ai_gate._provider = lambda leg=leg: leg`.
6. Reads the archived payload with `ai_gate.read_json_any_encoding(path)`.
7. Applies the production cohort seal `ai_gate._apply_live_candidate_budget(dict(payload))`
   inside `contextlib.suppress`, for fidelity with what was sent historically.
8. Calls `ai_gate._score_setup_impl(sealed_payload)` inside a broad `try/except`; a raised
   exception is recorded as `status="error"` with `{type, message}`.
9. Writes the result JSON to `--out`. Exit 0 unless an unrecoverable internal error.

Each child sets `leg.attempt_observer = recorder` where `recorder(record: Mapping)` appends
the dict produced by the provider (it already contains role, model, outcome, http_status,
latency_sec, token categories, `expected_go_usage_usd`, `reasoning_effort_sent`,
`max_output_tokens_sent`, `billing_state`, `error_category`). This is how per-role tokens,
latency, effort and cost are captured without editing product code. The child must **not**
set `OpenCodeGoAttemptLedger` (that would write the live bus).

## Arm leg construction

Use `dataclasses.replace(ai_gate.AI_CONFIG, opencode_muse_reasoning_effort=<effort>)` and the
exact construction of `ai_gate._build_ai_provider`'s OpenCode Muse leg (`ai_gate.py:1497-1507`):

```python
from ai_provider import OpenCodeResponsesProvider
leg = OpenCodeResponsesProvider(
    base_url=cfg.opencode_base_url,
    api_key=<OPENCODE_GO_API_KEY from env/AI_CONFIG; in dry mode a dummy>,
    model=cfg.opencode_muse_model,
    reasoning_effort=effort,
    timeout_sec=cfg.opencode_timeout_sec,
    max_output_tokens=cfg.opencode_max_output_tokens,
    reasoning_token_reserve=cfg.opencode_muse_reasoning_token_reserve,
    session_scope=cfg.opencode_session_scope,
    circuit_failure_threshold=cfg.provider_circuit_failure_threshold,
    circuit_cooldown_sec=cfg.provider_circuit_cooldown_sec,
    admission_retry_enable=cfg.admission_retry_enable,
    admission_max_retries=cfg.admission_max_retries,
    admission_backoff_initial_sec=cfg.admission_backoff_initial_sec,
    admission_backoff_max_sec=cfg.admission_backoff_max_sec,
    log=lambda _m: None,
)
```

Luna arm mirror `ai_gate.py:1527-1544` with `RemoteAPIProvider(primary_model=cfg.opencode_fallback_model,
fallback_models=[], analytics_model=cfg.opencode_fallback_model,
reasoning_effort="low", service_tier=cfg.opencode_fallback_service_tier ("flex"),
prompt_cache_enable=cfg.prompt_cache_enable, prompt_cache_key=..., prompt_cache_retention=...,
flex_unavailable_retry_enable=..., flex_unavailable_max_retries=..., flex_unavailable_cooldown_sec=...,
api_key=<OPENAI_API_KEY>, base_url=os.environ.get("OPENAI_BASE_URL","").strip(), ...)`.

The Muse arm's leg (`OpenCodeResponsesProvider`) is a single model with **no fallback**.

### Fake dry client

In `--mode dry`, build the same leg with `client_factory=` a callable returning an object
with `.responses.create(**kwargs)` that records kwargs and returns an object shaped like the
real response so the provider's parsing succeeds and the pipeline runs to a Decision. The
simplest robust fake is a `class _FakeClient: responses=_FakeResponses(); with_options=...`
whose `create` returns a dict/obj with `.output`/`.output_text`/`.usage`/`.id`/`.model`
matching what `OpenCodeResponsesProvider` expects; the schema-valid body must be a valid
`ModelAIGateOutput`/`ModelCriticDecision`/`ModelAdjudicatorDecision` depending on the
requested `text.format.name`. **Study `tests/test_harness_provider_integration.py` and
`tests/test_provider_neutral_ai.py` for the exact fake-response contract**; reuse the
existing fake helpers rather than inventing a new shape. If a fully-valid canned analyst
output is too complex, the dry-run may instead capture the wire and then raise the stage A
`_CaptureStop`; but for isolation and pipeline-path proof the dry-run must at minimum prove
the arm effort reaches the wire kwargs (`kwargs["reasoning"]["effort"]`) and that no fallback
call is made. Document precisely what the dry fake proves.

## Result JSON schema (`results/<safe_request_id>__<arm>.json`)

```json
{
  "request_id": "...", "arm": "muse_high_a",
  "status": "ok" | "error",
  "started_utc": "...", "elapsed_sec": 12.3,
  "error": {"type": "...", "message": "..."} | null,
  "config": {"effort": "high", "model": "muse-spark-1.3-contributor", "provider_mode": "OPENCODE_API",
             "provider_id": "opencode_go_responses", "reasoning_token_reserve": 24000,
             "max_output_tokens": 25000, "timeout_sec": 900, "session_scope": "..."},
  "wire_payload": {"archive_candidates": 3, "sealed_candidates": 3},
  "decision": { ...subset of the Decision dataclass... },
  "attempts": [ { ...attempt record dicts, sanitized... } ]
}
```

`decision` must include at least: `decision_state`, `python_final_allow`, `raw_allow`,
`allow`, `chosen_index`, `selected_candidate_id`, `selected_candidate_hash`,
`decision_quality_tier`, `decision_source`, `rejection_codes`, `veto_enabled`, `veto_code`,
`veto_reason`, `llm_quality_score`, `llm_self_reported_confidence`,
`suggested_risk_multiplier`, `mandatory_fields_complete`, `missing_mandatory_fields`,
`invalid_mandatory_fields`, `provider_mode`, `provider_id`, `actual_model_id`,
`model_fingerprint`, `final_resolver_reason`, `role_latencies`, `provider_retry_counts`,
`provider_usage`, `analyst_output`, `critic_output`, `adjudicator_output`.
Use `dataclasses.asdict` only if JSON-safe; otherwise read attributes explicitly. Never
include any env/secret.

Attempt records contain no secrets, but strip any `http_request_id` if worried; it is a
provider-side id, safe to keep.

## `manifest` subcommand

Reuse the stage A module (`python/tools/opencode_token_attribution.py`, importable with no
side effects) for `BusReader`, `read_json_any_encoding`, `symbol_class`, `candidate_stratum`
— or re-implement equivalently. Build `benchmark_manifest.json` per the protocol's selection
rule. For each request record:

```json
{"request_id","archive_path","archive_kind","symbol","symbol_class","candidate_count_archive",
 "candidate_stratum","strata":[...],"has_response_debug":true,
 "historical_decision":{"decision_state","python_final_allow","chosen_index","veto_code",
                        "llm_quality_score","decision_quality_tier","decision_source"}}
```

Also record `generated_utc`, `bus`, `request_count`, `seed`, `selection_rule`, `anchors`,
and a stratum summary. It must be deterministic for a fixed seed. Target N >= 60; if the
eligible pool cannot reach 60, record the honest shortfall and the reason.

## `run` subcommand

- Loads the manifest; builds the arm list (default all six).
- Randomizes arm order per request with the fixed seed.
- Skips a pair when its result file already exists and has `status == "ok"`.
- Runs pairs with `--concurrency` (default 6, start there).
- `--mode dry`: uses fake clients; `--dry-requests N` limits to N requests.
- Writes `selfcheck.json`: per arm, the effort observed on the wire and a boolean
  `no_fallback` (only one provider id / no fallback record), plus `isolation` before/after
  snapshots (path, size, mtime) of the protected files/dirs.
- On a Go usage-limit 429 detected in an attempt record (`http_status == 429` or error text
  contains `GoUsageLimitError`), stop launching new pairs cleanly and record the event.
- Prints a compact progress line per completed pair.

## `report` subcommand

Reads `results/*.json` and `benchmark_manifest.json`; computes and writes:

- `benchmark_results.csv` — one row per `(request_id, arm)`.
- `benchmark_summary.json` — per-arm aggregates (decision-state distribution, final-allow
  rate, schema/semantic validity rates, infra-failure rate, latency per role, tokens per
  role, cost, S1/S2/S3, agreement with `high_a`, Wilson CIs, verdict vs BEN-005, savings).
- `BENCHMARK_REPORT.md` — tables, verdict per arm, spend, caveats.

Wilson 95 % interval: `p_hat = k/n`, `z = 1.959964`; `center = (p + z^2/(2n)) / (1 + z^2/n)`;
`half = z*sqrt(p(1-p)/n + z^2/(4n^2)) / (1 + z^2/n)`.

## Focused test `python/tests/test_opencode_reasoning_benchmark.py`

Fully offline, fake clients only. Must cover:

1. **Arm effort reaches the wire** — driving the leg builder + `_score_setup_ai` with a fake
   client, assert `kwargs["reasoning"]["effort"] == <arm effort>` for `minimal/low/medium/high`.
2. **A Muse failure is recorded as a failure with no fallback call** — the fake client raises
   a transport/schema error; assert the result has `status == "error"` (or the decision is a
   provider-failure Decision), and that no second provider/model was invoked.
3. **Agreement / safety counters on a hand-built fixture** — construct synthetic result files
   for `high_a`, `high_b`, and one test arm; call the report/aggregation functions and assert
   the computed decision-state agreement, S1/S2/S3 and Wilson interval bounds.
4. **Resumability skips completed pairs** — with a pre-existing `status=="ok"` result file,
   the parent's pair scheduler does not spawn a child for that pair.

Run only this file during implementation:
`cd python; .\.venv\Scripts\python.exe -m pytest tests/test_opencode_reasoning_benchmark.py -q`

## Acceptance to return

- Both new files, exact paths.
- `py_compile` results.
- Focused test output (pass count).
- The exact `manifest` command used on the real bus and the resulting request count and
  stratum summary.
- A `--mode dry` run on 2 requests proving: effort on the wire per arm, no fallback, and
  isolation snapshots unchanged.
- Any deviation from this spec, with the exact error.
