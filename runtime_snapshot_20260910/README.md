# PO3 runtime snapshot — 2026-09-10

This directory is a point-in-time copy of PO3 runtime state that normally lives outside the Git repository.

Included sources:

- `MetaQuotes/Terminal/Common/Files/PO3_AI_BUS`
- `MetaQuotes/Terminal/Common/Files/PO3_AGENT_BUS`
- `MetaQuotes/Terminal/Common/Files/PO3_APPROVAL_AUDIT_20260907`
- `MetaQuotes/Terminal/Common/Files/PO3_APPROVAL_AUDIT_FINAL_20260907`
- Active FxPro terminal (`0148BD5691B65B0F2157627A4231F3DE`) PO3 files and journals

The copy excludes environment files and every individual file larger than 100,000,000 bytes. In particular, the live `PO3_AI_BUS/logs/shadow_candidates.jsonl` file was 142,953,865 bytes and was not copied. Other shadow/outcome ledgers and trade-history artifacts below the limit are included.

The EA and Python gate were running while this snapshot was made. A final synchronization pass was performed immediately before the Git commit.

## Refresh 2026-09-10 (second pass)

The snapshot was re-synchronized against the live runtime and extended.

- `PO3_AI_BUS` / `PO3_AGENT_BUS` / both approval-audit trees re-copied, so the
  requests, responses, response_debug, request_ledger, logs and runtime_state
  written after the first pass are now included.
- Added, and missing from the first pass: the deployed
  `MQL5/Experts/MT5_PO3_Codex` (source + compiled `.ex5`),
  `MQL5/Include/MT5_PO3_Codex`, `MQL5/Profiles/Presets` (the live `.set` files,
  including `Swing_V1.set`) and `MQL5/Profiles/Tester`.
- Files above GitHub's 100 MB limit are now published **gzip-compressed** rather
  than dropped, so no runtime history is lost — including the shadow-tracking
  ledgers:

  | file | raw | `.gz` |
  |---|---:|---:|
  | `PO3_AI_BUS/logs/shadow_candidates.jsonl` | 142.3 MB | 7.8 MB |
  | `PO3_AI_BUS/logs/runtime_state/live_account_903871_magic_5303191/shadow_candidate_pending.ndjson` | 105.8 MB | 14.4 MB |
  | `FxPro_active_terminal/MQL5/Logs/20260904.log` | 1618.0 MB | 49.7 MB |
  | `FxPro_active_terminal/MQL5/Logs/20260907.log` | 187.1 MB | 7.9 MB |
  | `FxPro_active_terminal/MQL5/Logs/20260909.log` | 154.6 MB | 4.9 MB |
  | `python/data/ai_decision_cache.jsonl` | 111.9 MB | 13.5 MB |
  | `python/.codex_validation/.../Tester/Agent-127.0.0.1-3000/logs/20260907.log` | 1142.9 MB | 37.2 MB |
  | `python/.codex_validation/.../Tester/logs/20260907.log` | 1002.8 MB | 40.2 MB |

  Decompress with `gzip -d <file>.gz`. The MT5 journals are still UTF-16 after
  decompression — read them with `Get-Content <file> -Encoding Unicode`.

Still excluded on purpose: every `.env` variant (live provider credentials) and
the two MetaTrader vendor binaries `terminal64.exe` / `MetaEditor64.exe`. A
redacted, ready-to-fill copy of the runtime environment is published as
`python/docs/runtime_env_template_opencode.txt`.
