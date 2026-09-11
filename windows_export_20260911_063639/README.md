# Windows runtime export — 2026-09-11 06:36 (local, UTC+3)

Read-only export from the original Windows FxPro MT5 install (terminal
`0148BD5691B65B0F2157627A4231F3DE`), for merging into the Linux instance.
Nothing on the Windows side was modified; the AI gate was live during the copy.

`MANIFEST.json` lists every file with its Windows source path, source size,
stored size, source mtime and the **sha256 of the uncompressed source bytes**.

## Layout

| Folder | Windows source |
|---|---|
| `PO3_AI_BUS/logs/**` | `%APPDATA%\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS\logs` — all of it: shadow tracking (`shadow_candidates.jsonl`, `runtime_state/**/shadow_*`), analytics, runtime_state, execution_identity_quarantine, trade_results, tester_ai_cache, archive, `ai_gate.log`, `openai_usage.*`, `completed_ai_trades.jsonl` |
| `PO3_AI_BUS/{completed,request_ledger,responses,response_debug,config,quarantined,timed_out,shutdown,stale,archive}` | same-named bus folders |
| `MT5_terminal/MQL5_Logs` | EA journal `MQL5\Logs\20260909-11.log` (UTF-16) |
| `MT5_terminal/Logs` | terminal journal `Logs\20260909-11.log` + `metaeditor.log` |
| `MT5_terminal/MQL5_Experts_MT5_PO3_Codex` | EA compile logs |
| `python_data/` | `python\data` (incl. `cache_quarantine/`) |
| `python_logs/` | `python\logs\*.jsonl` |

## Notes

- Files over 50 MB were gzipped (`.gz` suffix). None needed splitting.
- **Encoding:** files written by the EA (the shadow `.jsonl`/`.ndjson` files under
  `PO3_AI_BUS/logs`, and the MT5 journals) are **UTF-16LE**, MQL5's default text
  encoding — ~50% of their bytes are `\x00`. Decode as `utf-16-le` (strip the BOM),
  not UTF-8. Python-written files (`ai_gate.log`, `openai_usage.*`, `python_data/*`)
  are UTF-8.
- `python_data/ai_trade_memory.sqlite3` was taken with the SQLite online-backup
  API (the gate was writing to it). `PRAGMA integrity_check = ok`; rows:
  pending_decisions 1667, completed_memory 83, quarantine 29, ingest_state 2.
- `python_data/ai_decision_cache.jsonl.gz` is the live 2026-09-11 cache. The older
  2026-09-10 `.gz` is already committed at `python/data/` and was not duplicated.
- Terminal journals limited to 9–11 Sep by user decision (4 Sep alone is 1.6 GB).
- Excluded: `PO3_AI_BUS/rejected` (2 GB, 18k files), in-flight
  `requests`/`processing`/`locks`, all `.env` files, venvs, `__pycache__`.
- Scanned for every credential value in `python/.env` and for key-shaped
  strings: no credential found. `AI_PROMPT_CACHE_KEY` appears in logs — it is a
  cache routing tag, not a credential, and already present in the public repo.
- `PO3_AI_BUS/logs/tester_ai_cache` artifacts were recorded under a superseded
  contract manifest (see `MT5/CLAUDE.md` §4ab) and will not be replay hits.
