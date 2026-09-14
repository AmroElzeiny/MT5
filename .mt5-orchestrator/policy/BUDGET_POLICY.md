# Budget and latency policy

The goal is reliable completion without runaway agent chains.

## Standard target
- wall-clock target: 90 minutes
- material model-invocation target: 8 or fewer
- ordinary repair loops: 2 maximum
- reviewers: 1
- explorer: only if ownership is unclear
- dedicated test worker: only when the implementation worker cannot efficiently prove the change

## Deep target
- wall-clock target: 180 minutes
- material model-invocation target: 12 or fewer
- ordinary repair loops: 2 maximum per work package
- reviewers: 2 only for genuinely high-risk work
- explorer: optional, not automatic

If the likely path exceeds the target:
1. stop repeated attempts;
2. summarize evidence and the bottleneck;
3. escalate to the front-end.

Never spend more merely to avoid admitting uncertainty.

## Context budget
- one broad repository map maximum per mission unless new evidence invalidates it;
- reuse authority maps within the same mission;
- pass compact summaries to later agents;
- do not send whole transcripts to every worker;
- avoid duplicate test runs after a stable pass unless code changed or a reviewer found a new risk.

## Test and compile cadence (user decision 2026-09-13)

This section overrides any per-change testing instruction in CLAUDE.md, python/CLAUDE.md,
agent prompts or mission files, for delegated and direct work alike.

### Change size
- MAJOR: a new feature or module; any Python<->MQL contract change (schema, manifest/version
  constant, bus lifecycle, cache key, candidate identity); trade-path execution, risk sizing,
  order submission or position management; state persistence, restart, retry or idempotency.
- SMALL: everything else - a localized bug fix, a log/label/diagnostic change, a config or
  preset value, documentation, a behavior-preserving refactor.
- The front-end declares the size per work package in MISSION.md. When unstated: SMALL.

### While implementing (every work package)
- Do NOT run the full Python suite.
- Do NOT compile MQL5.
- SMALL: no new tests, no test runs. Only `python -m py_compile <changed .py files>`.
- MAJOR: one focused test file (or additions to the owning test file) covering the main
  success path and the main fail-closed path. Run only that file. No exhaustive edge matrices.

### Once, after the last work package
1. Full Python suite: `cd python; .\.venv\Scripts\python.exe -m pytest tests -q`
2. Compile every affected EA/include with `C:\Program Files\FxPro - MetaTrader 5\metaeditor64.exe`:
   0 errors, 0 warnings, `.ex5` newer than every `.mqh`, repo and deployed copies identical.
3. On failure: fix, re-run only the failing tests, then run the full suite and compile once more.
4. Record both results in the supervisor report.

### Unchanged
- Never delete, skip, xfail, loosen or rewrite existing tests to make code pass.
- The final full run is mandatory even for MQL-only edits: governance tests assert on MQL source text.
- No trade forcing, no weakening of fail-closed controls.
