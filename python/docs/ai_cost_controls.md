# AI cost controls

The Python bridge reads AI runtime settings once at startup through
`AIGateRuntimeConfig` in `ai_gate.py`.

Use `.env.example` as the template. Do not put secrets in committed files.

## Live-safe defaults

- `AI_USE_BATCH_API=false`
- `AI_USE_FLEX=false`
- `AI_ALLOW_FLEX_FOR_LIVE=false`
- `AI_FLEX_LIVE_ACK=false`
- `AI_ENABLE_SNAPSHOTS=false`
- `AI_HARD_PRE_GATE_BEFORE_OPENAI=true`
- `AI_REQUIRE_RUNTIME_INPUTS_LIVE=true`
- `AI_REJECT_ON_MISSING_RUNTIME_INPUTS_LIVE=true`
- `AI_COST_REPORT_ENABLE=true`

## Batch API

`score_setup_live()` never uses Batch. If `AI_USE_BATCH_API=true` and the
payload is live, the bridge logs `batch_api_disabled_for_live` and uses the
normal immediate path.

`score_setup_batch_research()` is only for `backtest`, `replay`, `research`, or
`analytics` payloads. It writes JSONL requests under `AI_BATCH_OUTPUT_DIR` and
may submit a delayed OpenAI Batch job, but delayed results are never used to
execute trades.

## Flex

Live Flex requires all three:

- `AI_USE_FLEX=true`
- `AI_ALLOW_FLEX_FOR_LIVE=true`
- `AI_FLEX_LIVE_ACK=true`

Without all three, live requests log `flex_disabled_for_live` and use
`service_tier=auto`.

## Prompt Cache

When `AI_PROMPT_CACHE_ENABLE=true`, OpenAI calls receive:

- `prompt_cache_key=AI_PROMPT_CACHE_KEY`
- `prompt_cache_retention=AI_PROMPT_CACHE_RETENTION`

The static prompt prefix remains stable and the dynamic setup payload is at the
end of the request.

## Decision Cache

When `AI_DECISION_CACHE_ENABLE=true`, repeated identical setup signatures reuse
the prior decision within `AI_DECISION_CACHE_TTL_SEC` and log `ai_cache_hit`.
Material changes to target, obstacle, cost, spread, or runtime hash miss or
invalidate the cache.

## Cost Report

Rows are appended to `AI_COST_REPORT_FILE` with:

`timestamp`, `request_id`, `symbol`, `setup_family`, `decision_source`, `model`,
`reasoning_effort`, `service_tier`, `prompt_cache_enabled`, `cache_status`,
`batch_used`, `flex_used`, `input_tokens`, `output_tokens`, `estimated_cost`,
`openai_called`, and `skip_reason`.

Hard pre-gate and cache-hit rows have `openai_called=false`.
