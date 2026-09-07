# Autonomous Quant R&D Factory for `AmroElzeiny/MT5`

A research-only Python subsystem designed for the existing PO3/AI-gate MT5 repository. It converts trading history and telemetry into deterministic findings, AI-assisted research hypotheses, experiment specifications, and reproducible reports **without trading authority**.

## Safety boundary

The Factory never places or closes trades, edits live MQL5, changes `.set` files, changes active policies, changes live risk/SL/TP, approves live trades, or deploys experiments. Its only writes are inside `python/rnd_factory/data/` (plus an optional existing AI usage log call) and configured research/bridge directories.

The future connection to MT5 backtest execution and the separate local-AI machine is intentionally outside this package. This package provides complete fail-closed file contracts for those integrations.

## Repository-specific integrations

The Factory understands the current repository architecture and preferentially reads:

- `python/data/ai_trade_memory.sqlite3` / the `completed_memory` schema produced by `trade_memory.py`;
- the PO3 Common/Files bus when configured;
- setup taxonomy/family/entry branch/session/regime fields;
- realized R, MFE, MAE and execution cost;
- stored analyst/critic/adjudicator decisions;
- Python and MQL5 source under the repository for bounded code discovery;
- the existing `experiment_registry.py` holdout check when importable;
- the existing `openai_usage_logger.py` as a compatibility log in addition to the Factory's own SQLite AI-call ledger.

If the trade-memory database is unavailable, bounded ledger discovery scans likely JSONL/NDJSON/CSV trade files in the configured bus/data/log locations.

## Install location

Copy the included folder to:

`<MT5 repo>/python/rnd_factory/`

Run commands from `<MT5 repo>/python/`.

Python 3.11+ is recommended. The Factory itself uses only the standard library.

## First run

```bat
cd <MT5 repo>\python
copy rnd_factory\.env.example rnd_factory\.env
python -m rnd_factory validate-config
python -m rnd_factory init
python -m rnd_factory investigate --last-trades 100 --mode no-ai
```

Reports are written by default to:

`python/rnd_factory/data/reports/`

Each investigation creates both JSON and Markdown.

## Main environment controls

All primary switches live in `rnd_factory/.env`.

### NO_AI

```env
RND_AI_MODE=none
```

Run:

```bat
python -m rnd_factory investigate --last-trades 100 --mode no-ai
```

No API key and no local AI bridge are needed.

### Remote AI

```env
RND_AI_MODE=remote
RND_REMOTE_BASE_URL=https://api.openai.com/v1
RND_REMOTE_API_KEY=<secret>
RND_REMOTE_MODEL=<model-id>
RND_REMOTE_CRITIC_MODEL=<optional-model-id>
RND_REMOTE_SYNTHESIS_MODEL=<optional-model-id>
RND_REMOTE_API_STYLE=responses
RND_REMOTE_REASONING_EFFORT=medium

RND_REQUIRE_KNOWN_PRICING=true
RND_REMOTE_INPUT_USD_PER_MILLION=<current-price>
RND_REMOTE_CACHED_INPUT_USD_PER_MILLION=<current-price>
RND_REMOTE_OUTPUT_USD_PER_MILLION=<current-price>

RND_MAX_AI_CALLS=3
RND_MAX_CONTEXT_TOKENS=12000
RND_MAX_OUTPUT_TOKENS=3500
RND_MAX_COST_USD=0.25
```

Pricing is deliberately an environment value. The package does not hardcode model prices that can become stale. With `RND_REQUIRE_KNOWN_PRICING=true`, remote mode refuses to start until pricing is configured.

`responses` uses the OpenAI Responses-style strict JSON-schema transport. `chat_completions` is available for compatible providers exposing that API shape.

### Local AI file bridge

```env
RND_AI_MODE=local
RND_LOCAL_REQUEST_DIR=data/local_ai_bridge/requests
RND_LOCAL_RESPONSE_DIR=data/local_ai_bridge/responses
RND_LOCAL_PROCESSING_DIR=data/local_ai_bridge/processing
RND_LOCAL_ARCHIVE_DIR=data/local_ai_bridge/archive
RND_LOCAL_QUARANTINE_DIR=data/local_ai_bridge/quarantine
RND_LOCAL_TIMEOUT_SEC=180
RND_LOCAL_POLL_INTERVAL_MS=500
RND_LOCAL_MAX_REQUEST_BYTES=2000000
RND_LOCAL_MAX_EVIDENCE_FILES=8
```

No remote API key is read or required in local mode.

### Local bridge contract

For every AI role the Factory creates:

`requests/<request_id>/request.json`

plus bounded evidence files under:

`requests/<request_id>/evidence/`

and finally:

`requests/<request_id>/READY`

The external local-AI machine should process only a bundle with `READY`, obey the `required_response_schema` in `request.json`, and atomically write exactly one response to:

`responses/<request_id>.json`

The response must echo at least:

- `role`
- `request_id`
- `research_run_id`
- `request_hash`
- `schema_version`

The Factory verifies stable-file state, response freshness, request ID, research run ID, request hash, strict response fields/enums/schema version, and then archives the successful request/response. Invalid responses go to quarantine. Timeout fails closed.

## Why it does not send the whole repository

Evidence construction is deterministic-first:

1. load only relevant trades;
2. calculate statistics in Python;
3. find comparable trades;
4. extract explicit observations;
5. derive search terms from the question/findings;
6. search Python/MQL5 source using `rg` when present or a Python fallback;
7. extract bounded line windows;
8. redact secrets;
9. send only the compact case pack.

Limits are controlled through ENV (`RND_MAX_CODE_SNIPPETS`, `RND_MAX_CODE_SNIPPET_LINES`, `RND_MAX_COMPARABLE_CASES`, context/cost limits, and bridge byte/file limits).

## Research modes

### `no-ai`

Fully deterministic. Produces real quantitative reports without any LLM.

### `quick`

At most one Researcher call.

### `standard`

Researcher plus an independent Critic when the result is uncertain or proposes a hypothesis.

### `deep`

Researcher + Critic + conservative Synthesis, subject to hard configured budgets.

Examples:

```bat
python -m rnd_factory investigate --last-trades 100 --mode no-ai
python -m rnd_factory investigate --setup micro_po3_reversal --last-trades 250 --mode standard
python -m rnd_factory investigate --trade-key <trade-key> --mode deep
python -m rnd_factory research --question "Why is continuation expectancy falling?" --mode standard
```

Optional periodic observation scan (research-only):

```bat
python -m rnd_factory watch --interval-seconds 3600 --mode no-ai
```

## Deterministic analysis

The current implementation calculates where evidence exists:

- win/loss/breakeven count;
- win rate + Wilson 95% interval;
- expectancy R + deterministic bootstrap 95% interval;
- median R;
- profit factor;
- MFE/MAE;
- execution cost;
- grouping by setup taxonomy, setup family, entry branch, session, killzone, regime, direction and asset class;
- recent-vs-prior expectancy drift;
- execution method summaries;
- delay/spread/slippage correlation with realized R when fields exist;
- AI decision / critic / adjudicator outcome summaries for realized trades;
- data-quality and identity warnings.

Rejected/counterfactual trades are never assumed to have realized outcomes. Reports explicitly warn when opportunity-cost conclusions require separate counterfactual evidence.

## Hypotheses

AI-generated hypotheses are stored in the Factory SQLite DB. If sample strength is insufficient, their initial status is `insufficient_evidence`; otherwise `proposed`.

```bat
python -m rnd_factory hypotheses list
python -m rnd_factory hypotheses show H-...
python -m rnd_factory hypotheses status H-... approved_for_experiment
```

No status transition deploys anything to live trading.

## Experiments

Create a registered research experiment from a hypothesis:

```bat
python -m rnd_factory experiments create H-... ^
  --variable entry_delay ^
  --control-json "{\"max_delay_sec\":60}" ^
  --candidate-json "{\"max_delay_sec\":15}" ^
  --training-range "2025-01-01/2025-06-30" ^
  --calibration-range "2025-07-01/2025-07-31" ^
  --validation-range "2025-08-01/2025-08-31" ^
  --final-holdout-range "2025-09-01/2025-09-30" ^
  --metrics "expectancy_r,fill_rate"
```

The Factory checks its own inspected-period registry and, when importable, the repository's existing `ExperimentRegistry` before accepting a final holdout.

Export a future external execution job:

```bat
python -m rnd_factory experiments export-job EXP-...
```

This creates a `RESEARCH_ONLY_NO_LIVE_WRITE` job in the configured pending directory. It does **not** pretend to launch MT5.

After the future Codex integration produces a real result file:

```bat
python -m rnd_factory experiments ingest-result <result.json>
```

## Source index

```bat
python -m rnd_factory source-index build
python -m rnd_factory source-index search ai_chose_infeasible_target
```

The index stores only source identities/symbols/hashes, not secrets.

## Data leakage / overfitting controls

The package keeps experiment and holdout identity explicit. It never auto-promotes a result. Experiment specs require separate training, calibration, validation, and final holdout fields. A final holdout already marked inspected is rejected. Existing repository holdout governance is consulted when available.

All grouped discoveries in ordinary investigations are exploratory observations, not proof of causation. Sample strength is labelled `insufficient`, `moderate`, or `strong` using configured minimums.

## Cost protection

Before every AI call the Factory checks:

- call count;
- context-token estimate;
- expected output tokens;
- estimated remote cost.

If a budget is exceeded, deterministic research is still completed and the AI section records why it was skipped.

Every AI call is recorded in `rnd_factory.sqlite3`. Compatibility logging through the repository's existing `openai_usage_logger.py` is available only when `RND_COMPAT_USAGE_LOG_ENABLE=true`; it is **false by default** so the Factory does not write to production/bus logs during normal research.

## Security

- production trading directories are read-only to this package by design;
- secrets are redacted before evidence is sent;
- evidence is explicitly labelled untrusted data to resist prompt injection from logs/comments/source strings;
- no arbitrary response filename is trusted;
- malformed/mismatched local responses are quarantined;
- no hidden provider fallback exists;
- remote and local modes are mutually selected through `RND_AI_MODE`;
- `no-ai` requires neither.

## Windows helpers

From `python/rnd_factory/scripts/`:

- `init_rnd_factory.bat`
- `validate_config.bat`
- `run_no_ai_100.bat`
- `run_tests.bat`

They do not require changing PowerShell execution policy.

## Run tests

From `<MT5 repo>/python`:

```bat
python -m unittest discover -s rnd_factory\tests -v
```

Tests use temporary directories/test doubles and do not make paid API calls.

## Intentionally left for the later Codex integration task

Only these external boundaries remain deliberately unconnected:

1. automatically feeding MT5/live/tester artifacts into Factory jobs beyond the read adapters already implemented;
2. connecting the separate local AI machine to the Local AI request/response directories;
3. launching MT5 Strategy Tester jobs from exported experiment jobs if your environment requires terminal-specific setup;
4. allowing any approved research result to influence production — which must remain a separately authorized human-controlled integration.

The internal interfaces for Local AI and experiment execution are implemented now so the later task connects them rather than redesigning the Factory.
