# Windows runtime export - 2026-09-14T04:21:48 (incremental)

Read-only export from the Windows FxPro MT5 install (terminal `0148BD5691B65B0F2157627A4231F3DE`).
Only files that are **new or changed** since the earlier `windows_export_*` folders are stored
(same relative layout, so later folders overlay earlier ones). `MANIFEST.json` lists each file with
its source path, sha256 of the uncompressed source bytes, sizes and mtime.

| Group | Files | MB stored |
|---|---:|---:|
| `MT5_terminal/Logs` | 5 | 0.2 |
| `MT5_terminal/MQL5_Logs` | 4 | 3.8 |
| `PO3_AI_BUS/archive` | 52 | 7.2 |
| `PO3_AI_BUS/bybit` | 6 | 0.5 |
| `PO3_AI_BUS/completed` | 247 | 27.4 |
| `PO3_AI_BUS/config` | 2 | 0.0 |
| `PO3_AI_BUS/logs` | 161 | 54.9 |
| `PO3_AI_BUS/logs/runtime_state` | 48 | 42.7 |
| `PO3_AI_BUS/logs/tester_ai_cache` | 522 | 98.2 |
| `PO3_AI_BUS/logs/trade_results` | 10 | 0.3 |
| `PO3_AI_BUS/request_ledger` | 806 | 49.5 |
| `PO3_AI_BUS/response_debug` | 713 | 61.7 |
| `PO3_AI_BUS/responses` | 46 | 1.9 |
| `PO3_AI_BUS/shutdown` | 8 | 0.6 |
| `python_data/ai_decision_cache.jsonl.gz` | 1 | 18.0 |

- Unchanged since previous export (not duplicated): 8512
- Files over 50 MB are gzipped (`.gz`). EA-written files and MT5 journals are UTF-16LE.
- `python/data/ai_trade_memory.sqlite3` is committed in place: integrity `ok`,
  rows {"pending_decisions": 1997, "completed_memory": 83, "quarantine": 39, "ingest_state": 2}.
- Excluded: all `.env` files, `PO3_AI_BUS/rejected` (2.2 GB), `bybit/market` raw candle CSVs
  (448 MB, re-downloadable), in-flight `requests`/`processing`/`locks`, venvs.
- Scanned against 4 local credential values and key-shaped strings;
  refused files: 0.
