# WP1 / ACC-001 — OpenCode Go limit-window reconciliation from local evidence

Run: `20260913T155650Z-5fa598c0` · Tier Deep · RuntimeMode Offline · Author: explorer (read-only)
Scope: **accounting forensics only.** No product file changed, no test run, no network, no bus write.
This is a reconciliation of *local observability*, not a billing determination.

Bus root (`$bus`) = `C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS`.

## 0. Sources inspected (streamed, never dumped)

| Source | Size / shape | Timestamps |
|---|---|---|
| `$bus\logs\openai_usage.ndjson` | 13,927,516 B; 15,208 rows | `ts_utc` (UTC) |
| `$bus\logs\ai_gate.log` | 64,006,800 B; 210,999 lines | **none** (no per-line clock) |
| `$bus\logs\opencode_go_attempts.ndjson` | **absent** (Phase-1 ledger, 0 rows) | n/a |
| `python\logs\ai_cost_report.jsonl` | 34,301,879 B; 7,380 rows | `timestamp` (epoch UTC) |
| `python\logs\ai_request_response_fingerprints.jsonl` | 103 MB | epoch |
| MT5 Experts journal `%APPDATA%\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Logs\YYYYMMDD.log` | UTF-16; 09-09 162 MB, 09-10 46 MB, 09-11 52 MB | **local UTC+3** `HH:MM:SS.mmm` |
| `%TEMP%\po3rep1..5\PO3_AI_BUS\logs\{openai_usage.ndjson,ai_gate.log}` | small | `ts_utc` |
| `python\opencode_go_accounting.py` | reprice engine (Phase 1) | — |

Safeguards honoured: no `.env` read; no process start/stop/signal; no bus write; only run-dir artifacts written.

## 1. Exact filter commands used (reproducible)

Helper scripts written under the run dir only:
`_wp1_usage_scan.py`, `_wp1_muse_detail.py`, `_wp1_muse_ops.py`, `_wp1_log_muse.py`, `_wp1_mt5log.py`.

```powershell
# 1. Bus inventory + Phase-1 attempt ledger presence + temp buses + processes
Get-ChildItem -LiteralPath "$bus\logs" | Select-Object Name,Length,LastWriteTime
Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter "po3rep*" | Select-Object FullName,LastWriteTime
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ai_gate' -or $_.Name -match 'python' } |
    Select-Object ProcessId,Name,CreationDate,CommandLine | Format-List

# 2. Whole-ledger model/provider_mode census (streaming)
python .mt5-orchestrator\runs\20260913T155650Z-5fa598c0\_wp1_usage_scan.py "$bus\logs\openai_usage.ndjson"

# 3. Strict Muse rows (model == muse-spark-1.3-contributor): first/last, per-day, roles
python .mt5-orchestrator\runs\20260913T155650Z-5fa598c0\_wp1_muse_detail.py "$bus\logs\openai_usage.ndjson"

# 4. Muse per-operation first/last + late-result boundary
python .mt5-orchestrator\runs\20260913T155650Z-5fa598c0\_wp1_muse_ops.py "$bus\logs\openai_usage.ndjson"

# 5. Reprice with the Phase-1 engine (run from python\)
python -c "import json,sys; from pathlib import Path; import opencode_go_accounting as a; \
o=a.summarize_legacy_usage(a._read_ndjson(Path(sys.argv[1]))); print(json.dumps(o,default=sorted))" \
  "$bus\logs\openai_usage.ndjson"

# 6. Muse-only ai_gate.log classifier (tags, statuses, roles)
python .mt5-orchestrator\runs\20260913T155650Z-5fa598c0\_wp1_log_muse.py "$bus\logs\ai_gate.log"

# 7. limitName / reset extraction (streaming)
#   (inline: iterate lines, regex limitName[:=]('?)(\w+) and "Resets in ...")

# 8. Temp-bus ledgers
foreach ($n in 1..5) { python ...\_wp1_usage_scan.py "$env:TEMP\po3rep$n\PO3_AI_BUS\logs\openai_usage.ndjson" }

# 9. MT5 Experts journal (local UTC+3), UTF-16
python ...\_wp1_mt5log.py "%APPDATA%\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE\MQL5\Logs\20260911.log" muse-spark
```

## 2. Muse activity inventory, 2026-08-31 → 2026-09-14 UTC

All main-ledger rows are `provider_mode=OPENCODE_API`, `provider_id=opencode_go_responses`, `pricing_status=unpriced_model`.

| Kind | Earliest | Latest | Count | Source |
|---|---|---|---|---|
| Successful (main gate) | 2026-09-09T16:50:38Z | 2026-09-13T12:31:27Z | 2,474 rows | `openai_usage.ndjson` |
| Success (temp-bus replay, 09-09) | 2026-09-09T16:15:04Z | 2026-09-09T16:29:47Z | 4 + 4 rows | `po3rep2`, `po3rep4` ledgers |
| Success (temp-bus live probe, 09-13) | 2026-09-13T03:37:25Z | 2026-09-13T03:38:02Z | 3 rows | `po3rep5` ledger |
| 429-refused, weekly body captured | 2026-09-13 ~12:28Z | 2026-09-13 ~12:33Z | 128 lines `limitName='weekly'` ("Resets in 11hr 2Xmin" → 2026-09-14T00:00Z) | `ai_gate.log` |
| 429-refused, body truncated (09-11) | 2026-09-11T16:50:40Z (last MQL Muse echo) | same cluster | 411 lines contain "429", no `http_status`, no `limitName` | `ai_gate.log` + Experts 20260911.log |
| Failed `[provider_call_failed]` | within 09-09..09-13 (untimed) | — | 30 (20×403; 4 deadline; 3 circuit-open; 3 connection) | `ai_gate.log` |
| Fallback attempts (any reason) | within 09-09..09-13 (untimed) | — | 1,041; latency p50 11.8 s / max 405.8 s | `ai_gate.log` |
| Late results | 2026-09-13T12:28:00Z | 2026-09-13T12:31:27Z | 19 successes after the weekly 429 onset (36 rows ≥ 12:27:14) | `openai_usage.ndjson` |
| Correction pass (logged) | 2026-09-11T01:32:39Z | 2026-09-11T01:32:39Z | 1 (`provider_neutral_analyst_evidence_repair`) | `openai_usage.ndjson` |
| Capability/health probe that invokes inference | — | — | **0 for Muse** (see §3) | code + log |
| Other `ai_gate.py` processes | — | — | **0** at recon time | `Get-CimInstance` |

Per-day main-ledger successes: **09-09 = 371, 09-10 = 996, 09-11 = 429, 09-13 = 678**; none on 09-07/08/12.
Per-role: analyst 1,105 (16:50:38Z→12:31:27Z), critic 1,116 (16:51:01Z→12:29:25Z), adjudicator 252 (18:57:45Z→12:28:27Z).
Reprice (all-time, published 0.10/0.20/0.002 per 1M): 71,542,993 uncached · 260,468 cached · 20,547,063 output (16,128,171 reasoning) → **USD 11.26423289**. Windowed: `since 09-07T00:00Z, until 12:27:00Z` = 2,438 rows / **$11.09738**; `until 12:28:00Z` = 2,455 rows / **$11.16513**. (Front-end's 2,454 / $11.153 sits between the two cut points.)

Chronology anchors (local = UTC+3):
- MT5 `20260909.log`: first `muse-spark` decision echo **19:51:02.541 local = 16:51:02Z**; `20260910.log` 01:16:57→23:37:48 local; `20260911.log` **last Muse echo 19:50:40.443 local = 16:50:40Z**, nothing after.
- Repo `ai_cost_report.jsonl` (logical calls): first **2026-09-09T16:25:42Z** (po3rep4), last **2026-09-13T12:31:27Z**; 09-11 last **16:50:11Z**; 09-13 first **03:37:25Z** (po3rep5).

### Temp-bus isolation (confirmed)
`po3rep2/4/5` each own an `openai_usage.ndjson`; the main ledger has **no** row at 16:15Z, 16:25Z or 03:37Z. So replay/probe **token usage went to a different ledger**. Nuance: `python\logs\ai_cost_report.jsonl` is a repo-fixed path shared across bus overrides — it *does* contain po3rep4 (16:25:42Z) and po3rep5 (03:37:25Z) but not po3rep2; thus logical-call cost rows and token-usage rows are split across different files.

## 3. Health / capability probe paths — do they send a Muse generation and are they logged?

- `_refresh_provider_health` (`python/ai_gate.py:1668`) computes `probe = provider.provider_mode in {PROVIDER_MODE_LOCAL, PROVIDER_MODE_OPENROUTER}` (**line 1686**) and calls `healthcheck(probe_structured=probe)` (**line 1687**). `OPENCODE_API` is not in that set → **no probe generation for Muse**.
- `OpenCodeRoutedProvider.healthcheck` forces `probe_structured=False` on both legs (`python/ai_provider.py:3735-3752`).
- `OpenCodeResponsesProvider` extends `RemoteAPIProvider` (`python/ai_provider.py:3329`), whose `healthcheck` is **configuration-only** (`python/ai_provider.py:1476-1490`). `OpenCodeMessagesProvider.healthcheck` is likewise config-only (`python/ai_provider.py:3595-3621`).
- `StructuredCapabilityProbe` generation fires only in `LocalOpenAICompatibleProvider.healthcheck` (`python/ai_provider.py:2201-2225`, `generate_structured(... response_schema=StructuredCapabilityProbe ...)`) — i.e. LOCAL/OpenRouter only. Shadow analytics uses `probe_structured=(provider_mode == LOCAL)` (`python/ai_gate.py:8995`).
- **Ledger logging:** inference probes are **not** written to `openai_usage.ndjson`. The ledger is written by `log_ai_usage` (`python/openai_usage_logger.py:638`) only from the gate role paths (`python/ai_gate.py:4385, 4562, 4733, 5338`); the probe calls `provider.generate_structured` directly. Evidence: `request_id=provider_capability_probe` rows = **0**; no ledger operation contains "capab"/"health" (verified streaming). Meanwhile `ai_gate.log` has 934 `provider_capability_probe` lines and 531 `StructuredCapabilityProbe` preflights, all `provider=local_openai_compatible`, none Muse.
- Muse `[ai_provider_health]` lines = 646, all config-only, no generation and no usage row.

## 4. Three-way reconciliation

| Window (USD) | Documented allowance | Locally reconstructed usage (this host) | Unobservable (VPS / other keys / console) | OpenCode console |
|---|---|---|---|---|
| 5-hour | 20% of monthly = **$12.00** | Front-end §5: 09-11 11:50→16:50 = $0.633; 09-13 07:27→12:27 = $3.354 (not re-derived here) | VPS/Linux deployment (commit `25cca426`, `python/docs/runtime_env_template_opencode.txt`); any other process on the same workspace/key; direct probes that wrote no bus ledger | **not available locally — user must read the OpenCode console** |
| Weekly | 50% of monthly = **$30.00** | Muse this host, main ledger: **$11.264** all-time; $11.097–$11.165 within 09-07→12:27/12:28. Temp-bus replays: **+$0.0077 + $0.0158 + $0.0140 = $0.0375**. DeepSeek secondary: $0.54 off-peak / $1.08 peak. Failed attempts: unobserved — upper bound ≈ **+$0.36** if all 60 unknowns billed as full analyst (billing evidence §4) | same as above | same as above |
| Monthly | 100% of monthly = **$60.00** | Not reconstructed: local observations cover 09-09→09-13 only; no earlier-September Muse rows exist on this host | same as above; prior months/other clients unobservable | same as above |

Published rates/limits: billing evidence §2–§3; page `https://opencode.ai/docs/go/` "Last updated Sep 11, 2026", retrieved 2026-09-13.

## 5. Verdict on the 2026-09-11 16:50Z 429s

**Cannot be proven weekly, 5-hour or monthly from local evidence.**
- Every log line that carries a `limitName` (128) says `weekly`, but **all 128 are the 09-13 12:28–12:33Z cluster** (`Resets in 11hr 2Xmin` → 2026-09-14T00:00Z).
- The 09-11 cluster (411 Muse fallback lines containing "429") has **no `http_status` and no `limitName`** — the field is absent/truncated. No local artifact pins it to a window.
- Corroborating observation (inference, not proof): Muse succeeded again **2026-09-13T10:30:37Z**, which is *before* the 09-13 weekly reset at 2026-09-14T00:00Z. If the 09-11 429 were the same fixed weekly window, recovery before 09-14 would be inconsistent; but the gate may simply have been idle on 09-12, so this does not prove a 5-hour window.
- No provider billing defect is asserted. The weekly 429 body explicitly links to "enable usage from your available balance".

## 6. Tests that could reach the network (static review, not run)

OpenCode/transport tests inject doubles: `python/tests/test_opencode_go_cost_correctness.py:48,112` (real BASE_URL, recorded transport), `python/tests/test_opencode_provider_contract.py:1040` (`transport=double`), `python/tests/test_configuration_circuit_recovery.py:158,173` (double base URLs). The only real sockets in tests are loopback (`python/tests/test_harness_provider_integration.py:151,633` → `127.0.0.1`). **No test-sourced Muse usage row is present in the ledger.**

## 7. Explicit uncertainty

1. `ai_gate.log` has **no per-line timestamp**, so failure/429 wall-clock times are bounded by the Muse success interval (09-09T16:15/16:50 → 09-13T12:31Z) and the Experts-journal echoes, not directly measured.
2. 429 count: 126 explicit `http_status=429` + 411 status-absent "429" lines = 537; the front-end's inferred 475 is bracketed but not exactly reproduced.
3. The baseline's 403/500/connection medians (51 s / 94 s / 19 s) were not found in Muse `[provider_call_failed]` lines (no elapsed field). Only `[opencode_fallback] opencode_latency_sec` is available: 403 p50 74.3 s, 500 p50 128.8 s, 429 p50 47.7 s.
4. `ai_cost_report.jsonl` mixes main-bus and temp-bus runs (repo-fixed path); po3rep2 is absent from it for an unknown reason.
5. Direct front-end endpoint probes that wrote no ledger are unobservable; §4ac documents such probes for the Qwen/`/messages` dialect (not Muse).
6. VPS/other-key/console consumption cannot be observed from this host; only the console settles the meter.
