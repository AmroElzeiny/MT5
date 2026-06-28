# V-Next Safety And Analytics Fixes

## Applied Changes

- Applied `june-prop-2k.set` values into `Config.mqh` defaults before adding new controls.
- Added minute-level killzone inputs and optional trade-only-killzones mode.
- Added hard pre-AI/pre-order gates for:
  - non-killzone trades when `InpTradeOnlyKillzones=true`
  - stale/touched continuation suppressions
  - synthetic fallback targets crossing opposing imbalance/obstacle
  - execution cost at or above `InpExecutionRejectCostR`
- Added cost-risk trimming when cost is high but still below the reject ceiling.
- Changed broker order comments to the compact pattern:
  `MODEL-SESSION-K/NK-SYMBOL-SHORTID`
  while preserving full internal `trade_key` metadata via alias files.
- Added runtime input snapshots and `runtime_input_hash` to AI payloads.
- Added Python-side hard pre-gate before OpenAI calls.
- Added closed-trade ledger integrity tags:
  `data_integrity_status` and `data_integrity_reasons`.
- Updated expectancy reporting to ignore suspicious rows by default and add
  `--include-suspicious` / `EXPECTANCY_INCLUDE_SUSPICIOUS=true` for forensic runs.
- Left adaptive policy in shadow/no-auto-activation mode by default:
  `InpAnalyticsAutoActivate=false`, `InpPolicyShadowMode=true`, and Python
  `ANALYTICS_AUTO_ACTIVATE` defaults to false.
- Added `tools/repair_trade_ledger.py` for non-mutating trade ledger audit/repair copies.

## Important Inputs

- `InpTradeOnlyKillzones`
- `InpLondonKillzoneStartMinute`
- `InpLondonKillzoneEndMinute`
- `InpNewYorkKillzoneStartMinute`
- `InpNewYorkKillzoneEndMinute`
- `InpEnableAsiaKillzone`
- `InpAsiaKillzoneStartHour`
- `InpAsiaKillzoneStartMinute`
- `InpAsiaKillzoneEndHour`
- `InpAsiaKillzoneEndMinute`
- `InpRejectSyntheticFallbackAfterCrossedObstacle`
- `InpExecutionRejectCostR`
- `InpExecutionReduceRiskCostR`
- `InpMicroScalpMaxCostFracOfPlannedR`
- `InpSuppressMicroBisiSibiEdge`
- `InpSuppressStaleFvgBranches`
- `InpSuppressTouchedContinuationUnlessRetested`
- `InpSuppressContinuationTouchedFvg`
- `InpSuppressContinuationStaleFvg`

## Verification

- Python syntax check:
  `python -m py_compile ai_gate.py expectancy_report.py tools\repair_trade_ledger.py tools\verify_v_next_controls.py`
- Full control verification:
  `python tools\verify_v_next_controls.py`
- MetaEditor compile:
  `Result: 0 errors, 0 warnings`

## Follow-up completion

- Added `.env.example` with the AI cost/safety variables for model chain, prompt cache, decision cache, Batch API, Flex, live runtime-input safety, snapshots, and cost reporting.
- Parsed those values in `ai_gate.py` through `AIGateRuntimeConfig`; startup logs the active config without secrets.
- Safe defaults: Batch off, Flex off, snapshots off, hard pre-gate on, live runtime-input requirement on, missing live runtime inputs rejected, cost reporting on.
- Batch is disabled for live by `score_setup_live()` and `score_setup_batch_research()` refuses live payloads with `batch_api_disabled_for_live`.
- Flex uses `service_tier="flex"` only for live requests when `AI_USE_FLEX=true`, `AI_ALLOW_FLEX_FOR_LIVE=true`, and `AI_FLEX_LIVE_ACK=true`; otherwise it logs `flex_disabled_for_live`.
- Prompt caching is controlled by `AI_PROMPT_CACHE_ENABLE`, `AI_PROMPT_CACHE_KEY`, and `AI_PROMPT_CACHE_RETENTION`; these kwargs are passed to OpenAI calls when supported.
- Setup-signature cache uses `AI_DECISION_CACHE_*` and includes symbol, direction, setup family/class/branch, source sweep/disp/BOS times, FVG, entry/SL/TP2, target/obstacle, session/killzone, runtime hash, execution-cost bucket, and spread bucket.
- Runtime inputs are enforced from the EA payload, not `.mqh` defaults. Live payloads missing the critical runtime-input contract are rejected before OpenAI.
- Hard pre-gate rejects objective blocks before OpenAI and writes skipped-call cost rows with `openai_called=false`.
- MQL now calls `CanPlaceOrderHardSafety()` immediately before market orders, pending limit orders, and pending-entry relaxation modifies.
- Ledger repair now writes `repaired_system_trade_history.json`, `rejected_deals.json`, `ledger_integrity_report.md`, and `ledger_integrity_report.json`.
- Verify skipped AI calls in `logs/ai_cost_report.jsonl` where `openai_called=false` and `skip_reason` is a hard-gate or cache reason.
