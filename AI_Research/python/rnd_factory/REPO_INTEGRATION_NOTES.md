# Repository Integration Notes

The Factory was designed against the current `AmroElzeiny/MT5` repository structure inspected on 2026-08-10.

Relevant existing components observed and intentionally respected:

- `python/ai_gate.py`: PO3-aware AI gate; MT5 remains execution authority.
- `python/ai_provider.py`: provider-neutral structured AI transport with remote/local provider concepts.
- `python/trade_memory.py`: SQLite `completed_memory` plus pending/quarantine storage and historical analogue fields.
- `python/decision_pipeline.py`: independent Critic/Adjudicator and deterministic consensus.
- `python/expectancy_report.py`: governed analytics, conditioned scorecards, policy snapshots and rollback-oriented analytics.
- `python/experiment_registry.py`: append-only experiment governance and final-holdout contamination checks.
- `python/openai_usage_logger.py`: token/cost observability; compatibility logging is disabled by default in the Factory to preserve production read-only behavior.
- `MT5_PO3_Codex Include/TradeEngine.mqh`: execution/watchlist/AI-result pipeline with counterfactual and shadow queues plus funnel telemetry.
- `MT5_PO3_Codex Include/AIGateBridge.mqh`, `FileBus.mqh`, `StateStore.mqh`, `Risk.mqh`, `PO3.mqh`, `FVG.mqh` and related includes remain untouched.

The Factory does not modify any of these files. It reads authoritative evidence through bounded adapters and writes its own state under `python/rnd_factory/data/` unless an operator explicitly overrides research paths in `.env`.
