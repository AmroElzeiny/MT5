# READ FIRST — the verdicts in BENCHMARK_REPORT.md are not valid evidence

Written by the front-end (Claude) on takeover, 2026-09-13 ~20:25 UTC.

1. **The account's OpenCode Go weekly limit ran out mid-run (confirmed by the user).** At 2026-09-13 ~19:52 UTC
   the Muse endpoint began refusing calls. A direct probe at ~20:14 UTC returned
   `429 GoUsageLimitError "Weekly usage limit reached. Resets in 3hr 46min"` (reset 2026-09-14T00:00Z).
   264 attempts were refused (`not_billed_admission_refused`, no admission retry). As a result 40–50 of
   60 pairs in every Muse arm are degraded infrastructure outcomes, not model decisions.
2. **The literal BEN-005 verdicts are artifacts of that refusal.**
   - `muse_low non_inferior=True`: its decision-state agreement (95%) counts pairs where both it and
     `muse_high_a` were refused and degraded to REJECT, and its infra rate happened to be lower.
   - `muse_high_compact non_inferior=False`: driven by more refusals (it ran later, into the limit).
     Encoding-attributable failures are 0 in both arms.
3. **The harness did not stop on the limit.** The attempt ledger carries `RateLimitError`, not the body
   type `GoUsageLimitError`, so `go_limit_hit` never fired. Fixed in the takeover:
   `usage_limit_refused()` detects the refusal shape, stops the run, and makes resume re-run those pairs.
4. **Isolation was breached for the pipeline's Python-side writers.** In live children
   `po3_env.bootstrap_provider_env()` re-applies `python/.env` with `override=True` over the harness
   redirects. 118 benchmark decisions reached `python/data/ai_decision_cache.jsonl`, and 11 rows reached
   `pending_decisions` in `python/data/ai_trade_memory.sqlite3`. Both were removed (backups in
   `takeover/isolation_backup/`; cache 118 rows dropped / 3,113 kept; trade memory 2006 → 1995 rows,
   `integrity_check=ok`). The production shadow-repeat setting (2% sample) was also active in children.
   The live bus and the shadow ledger stayed isolated (their keys are not in `.env`; bus tree digests
   unchanged). Fixed: live children re-apply isolation after the credential load and refuse to run
   (`benchmark_isolation_guard`) if the effective config still points outside the pair scratch dir.
   The other changed paths (`python/logs/ai_cost_report.jsonl`, `ai_repeatability_artifact.json`) were
   written by the full test-suite runs in the same window (test request ids / timestamps inside the
   suite window), not by the benchmark.

What remains valid: every successful attempt is a real provider observation (tokens, latency, effort
on the wire, no fallback). Paired input-token deltas between arms are valid. Decision-quality
comparisons are valid only on "clean" pairs (FULL_STRUCTURED on both sides), and N is too small
(12–20 per arm) for any non-inferiority verdict.

To finish after the reset (2026-09-14T00:00Z), with the hardened harness:

```powershell
cd python
$ids = ((Get-Content ..\.mt5-orchestrator\runs\20260913T180610Z-eee8a0a8\takeover\subset_a1.json -Raw | ConvertFrom-Json).request_ids) -join ','
.\.venv\Scripts\python.exe tools\opencode_reasoning_benchmark.py run --run-dir ..\.mt5-orchestrator\runs\20260913T180610Z-eee8a0a8 --mode live --arms muse_high_a,muse_high_b,muse_medium,muse_low,muse_minimal,muse_high_compact --requests $ids --concurrency 8
.\.venv\Scripts\python.exe tools\opencode_reasoning_benchmark.py report --run-dir ..\.mt5-orchestrator\runs\20260913T180610Z-eee8a0a8
```

Resume re-runs only the refused pairs (~264 attempts). Judge with `non_inferior_a3`
(addendum A3: clean-pair agreement, ≥40 clean pairs, no refusals left). At ~$0.011 per HIGH request
the remaining Muse cost is estimated at ≈$2–3.
