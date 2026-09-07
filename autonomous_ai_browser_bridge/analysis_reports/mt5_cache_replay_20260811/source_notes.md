# MT5 cache replay audit notes

Generated from a read-only inspection on 11 August 2026.

## Source inventory

- MT5 report: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\full_ai_2m_stage2_cache_replay_ready_27_report.htm`
- MetaTester agent log: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Tester\0148BD5691B65B0F2157627A4231F3DE\Agent-127.0.0.1-3000\logs\20260811.log`
- Bridge audit log: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\ai_gate.log`
- Tester cache: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\tester_ai_cache`
- Approved decision: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs\tester_ai_cache\n577667844.json`
- Trade memory: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\data\ai_trade_memory.sqlite3`
- Priors: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python\data\live_bucket_priors.json`
- Stage-2 set: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\logs\FULL_AI_GOLD_2M_STAGE2_CACHE_REPLAY_READY_27.set`
- Deployed engine: `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex\TradeEngine.mqh`

## Reconciliation and validation

- Decision counts reconcile: 20 rejects + 6 abstentions + 1 approval = 27 valid tester-cache decisions.
- Family counts reconcile: 12 + 6 + 4 + 4 + 1 = 27.
- Completed-memory family counts reconcile: 30 + 24 + 21 + 6 = 81.
- Cache coverage is 27 / 3,715 = 0.7268%.
- The MT5 report and agent log agree on 10,000 USD final balance, 0 trades, and a technically passed run.
- `python ai_gate.py cache-audit` found tester cache valid=27 and invalid=0. It returned a non-zero process code because the separate Python decision cache contains 12 legacy-schema rows; that cache is not the tester replay source.
- All 27 cached decisions report `historical_evidence_state=AVAILABLE`; the former no-history bootstrap loop is no longer the current blocker.

## Observability limitation

The stage-2 set has `InpVerboseJournal=false`. In the deployed engine, `_LogFinalSummary()` writes through `_Journal()`, and `_Journal()` returns immediately when verbose journaling is disabled. Consequently, the run did not persist its cache-hit/miss totals, watchlist additions, order attempts, or downstream rejection reasons. This prevents a definitive explanation for why the one cached approval did not become a trade.

## Visual choice and report structure

- One simple bar chart shows the 27 cached decisions by outcome because the portable report contract requires chart evidence. It uses a zero baseline, one count measure, no redundant color grouping, and an explicit sample-size subtitle. Exact counts, denominator, and interpretation remain in the adjacent narrative so the chart is not mistaken for a stable performance distribution.
- Chart map: “The AI Sample Is Real but Far Too Small”; question = outcome mix in the reviewed cache; family/type = comparison / vertical bar; fields = decision and count; claim = only one of 27 cached decisions was approved; palette = single-root preferred; delivery = portable HTML report.
- Executive-report structure mapping: Executive Summary; metric/cohort definitions; key findings with metric cards and audit tables; recommended next steps; further questions; caveats and assumptions.
- Quantitative claims use `analysis_snapshot.sql`, a reviewed SQLite-compatible reconstruction of the audited values, as portable provenance. `analysis_snapshot.json` preserves the richer analysis state. Machine-local evidence paths stay in these supporting notes because portable HTML packaging rejects them by design.

## Decision rule used

The run is marked **needs revision / not live-ready**, not failed. It proves that the tester completed and that context exists, but it does not provide enough cache coverage or executed trades to evaluate performance. The next test should be a narrow cache-only diagnostic around 10 June 2026 with verbose telemetry enabled; a second two-month replay should wait until the approved decision is traceable end-to-end.
