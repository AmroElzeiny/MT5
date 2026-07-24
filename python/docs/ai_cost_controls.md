# AI cost controls

Provider selection and local-server configuration are documented in
`docs/ai_provider_configuration.md`. The cost controls below apply to the
startup-selected provider; they never authorize switching providers.

The Python bridge reads AI runtime settings once at startup through
`AIGateRuntimeConfig` in `ai_gate.py`.

Use `.env.example` as the template. Do not put secrets in committed files.

## Live-safe defaults

- `AI_USE_BATCH_API=false`
- `AI_USE_FLEX=false`
- `AI_ALLOW_FLEX_FOR_LIVE=false`
- `AI_FLEX_LIVE_ACK=false`
- `AI_OPENAI_TIMEOUT_SEC=180`
- `AI_OPENAI_FLEX_TIMEOUT_SEC=600`
- `AI_FLEX_UNAVAILABLE_RETRY_ENABLE=true`
- `AI_FLEX_UNAVAILABLE_MAX_RETRIES=20`
- `AI_FLEX_UNAVAILABLE_COOLDOWN_SEC=30`
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
`analytics` payloads and only when `AI_USE_REMOTE_API=true`. It writes JSONL
requests under `AI_BATCH_OUTPUT_DIR` and may submit a delayed remote Batch job,
but delayed results are never used to execute trades. Local mode reports
`batch_api_not_supported_by_selected_provider`; it never switches to remote.

## Flex

Live Flex requires all three:

- `AI_USE_FLEX=true`
- `AI_ALLOW_FLEX_FOR_LIVE=true`
- `AI_FLEX_LIVE_ACK=true`

Without all three, live requests log `flex_disabled_for_live` and use
`service_tier=auto`.

Flex is a remote-provider service tier. Local mode ignores it and stays on the
configured local OpenAI-compatible endpoint.

`AI_OPENAI_TIMEOUT_SEC` controls the normal synchronous Responses API timeout.
`AI_OPENAI_FLEX_TIMEOUT_SEC` controls the timeout when the request is actually
sent with Flex. The default Flex timeout is `600` seconds so slow Flex responses
have room to finish without allowing an infinite hang.

When Flex returns a transient/unavailable failure, the bridge retries the same
OpenAI call when `AI_FLEX_UNAVAILABLE_RETRY_ENABLE=true`. The defaults are
`AI_FLEX_UNAVAILABLE_MAX_RETRIES=20` and
`AI_FLEX_UNAVAILABLE_COOLDOWN_SEC=30`, meaning the bridge can wait about 10
minutes after repeated Flex unavailability before exhausting retries. The retry
loop applies only to Flex requests and only to transient status/error classes
such as 408, 409, 429 rate-limit, 500, 502, 503, 504, connection errors, and
timeouts. It does not retry invalid keys, permission errors, bad requests, or
quota/billing failures.

## Prompt Cache

When `AI_PROMPT_CACHE_ENABLE=true`, OpenAI calls receive:

- `prompt_cache_key=AI_PROMPT_CACHE_KEY`
- `prompt_cache_retention=AI_PROMPT_CACHE_RETENTION`

The static prompt prefix remains stable and the dynamic setup payload is at the
end of the request.

## Decision Cache

When `AI_DECISION_CACHE_ENABLE=true`, repeated identical setup signatures reuse
the prior decision within `AI_DECISION_CACHE_TTL_SEC` and log `ai_cache_hit`.
Material changes to target, obstacle, cost, spread, provider/model identity,
generation settings, family profile, retrieval policy/result, or an economic
runtime input miss or invalidate the cache. Tester workflow/debug flags remain
excluded so RECORD_ONLY responses can replay in CACHE_ONLY under the same
economic and provider contract.

Cached AI decisions are still rechecked against the current runtime family AI
threshold before being returned. Because the EA includes all family threshold
inputs in `runtime_input_hash`, changing `InpAiScoreFullPO3`,
`InpAiScoreMicroPO3`, `InpAiScoreContinuation`, `InpAiScoreRange`,
`InpAiScoreFailedBreakout`, `InpMinAiScoreTrend`, or
`InpGlobalAiScoreAsHardFloor` changes the signature and forces a cache miss.

## Cost Report

Rows are appended to `AI_COST_REPORT_FILE` with:

`timestamp`, `request_id`, `symbol`, `setup_family`, `decision_source`, `model`,
`reasoning_effort`, `service_tier`, `prompt_cache_enabled`, `cache_status`,
`batch_used`, `flex_used`, `input_tokens`, `output_tokens`, `estimated_cost`,
`openai_called`, and `skip_reason`.

Hard pre-gate and cache-hit rows have `openai_called=false`.
