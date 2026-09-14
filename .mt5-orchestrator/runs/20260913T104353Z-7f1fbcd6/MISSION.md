# Mission

Front-end: Claude
Tier: Standard
RuntimeMode: Offline

## Outcome

When the primary OpenCode Go model (Muse Spark, `opencode_go_responses`) fails on the normal route, the gate must try the second OpenCode Go model (Qwen, `qwen3.8-flash` on `opencode_go_messages`) once before falling back to OpenAI Luna. The provider failure detail in the fallback telemetry must also be complete enough to diagnose the failure. Today every Muse failure goes directly to Luna and the error body is cut at 200 characters.

## Confirmed baseline and authority map (front-end verified, do not rediscover)

- Routing lives entirely in `python/ai_provider.py` class `OpenCodeRoutedProvider` (about lines 3269-3538). `_leg_for()` maps importance to leg: normal -> Muse, important -> Qwen, critical -> Luna (`self._fallback`). `generate_structured()` catches any exception from the routed leg and, when `is_opencode` is true, makes exactly one call to `self._fallback` (Luna). Qwen is never used as a fallback.
- `_failure_fields()` truncates the failure detail with `str(exc)[:200]`. That is why every live `[opencode_fallback]` line ends mid-body (`Error code: 500 - {'type': 'error', 'error': {'type': 'error', 'mess`). Failed calls are not written to the usage ledger, so the full body is currently not stored anywhere.
- Live evidence (production `ai_gate.log`, read-only). Muse has 1844 successful calls and 655 fallbacks: 404 HTTP 429 (`GoUsageLimitE...`), 120 `RuntimeError missing_dependency_openai` (an old interpreter), 89 `PROVIDER_CONFIGURATION_ERROR` (breaker/403 fast-fail, latency 0.002 s), 19 HTTP 500 (after 55-144 s, i.e. mid-generation), 14 connection errors, 1 HTTP 502, and 8 skipped for deadline. The Qwen leg has 0 calls inside the gate.
- The provider is constructed in `python/ai_gate.py` around lines 1367-1437. Config fields live in `AIGateRuntimeConfig` (`opencode_qwen_model` about 538/603, fallback settings about 549-551/726-746/1023-1027). The active-config banner dict is about 1201-1211. `python/.env.example` documents the OpenCode settings around lines 251-294.
- Tests: `python/tests/test_opencode_provider_contract.py`. Helpers `_muse`, `_qwen`, `_luna`, `_routed(muse_answer=, qwen_answer=, luna_answer=, policy=)` and `_call()` are at lines 183-283. `FallbackContractTests` (about 590-748) currently asserts Muse failure -> Luna directly. Baseline: `72 passed, 8 subtests passed`.
- All five target files are clean in git (no uncommitted edits). The rest of the tree has many unrelated dirty files. Preserve them.

## Requirements

- FB-001: On the normal route (Muse primary), when the Muse call raises for any reason, call Qwen once with the identical role, system prompt, evidence, schema and request metadata. If Qwen also raises, call Luna once, exactly as the existing fallback does, including `_fallback_metadata()`. Each leg is called at most once per `generate_structured()` call, and no model is ever re-asked.
- FB-002: Before each fallback step (Muse->Qwen and Qwen->Luna), apply the existing deadline rule: if `request_metadata["deadline"]` exists and `can_start_attempt()` is false, skip the step with an explicit `[opencode_fallback] ... action=skipped reason=insufficient_remaining_budget stage=<n>` line and raise. The deadline is never extended.
- FB-003: The important route (Qwen primary -> Luna) and the critical route (Luna only, no fallback) keep exactly their current behavior. A failing Qwen primary must never fall back to Muse.
- FB-004: Add the setting `OPENCODE_QWEN_FALLBACK_ENABLE` (bool, default `true`, invalid value -> warning `OPENCODE_QWEN_FALLBACK_ENABLE=invalid_bool` and default). Wire it through `AIGateRuntimeConfig`, the provider construction and the active-config banner (empty or false when not the OpenCode provider, following the neighbouring fields' pattern), and document it in `python/.env.example`. With `false`, behavior must be byte-for-byte the pre-change behavior (Muse -> Luna). Do NOT edit `python/.env`.
- FB-005: Telemetry must name every leg truthfully:
  - Each `[opencode_fallback]` line carries `stage=1` or `stage=2`, `from=`/`from_model=` of the failed leg, `to=`/`to_model=` of the next leg, and `fallback_chain=` (e.g. `muse-spark-1.3-contributor>qwen3.8-flash>gpt-5.6-luna`).
  - `[opencode_fallback_completed]` names the leg that actually answered plus its stage.
  - The returned `ProviderResult` is the answering leg's own result, so `provider_id`, `provider_mode` and `actual_model` stay truthful. Do not relabel.
- FB-006: Replace the 200-character truncation in the fallback detail with a bounded single-line detail of up to 2000 characters. Newlines and control characters are escaped or collapsed. Include `http_status=` when it is knowable from the exception. Never include credentials: tests must assert that the test API keys never appear in any log line.
- FB-007: A Qwen fallback answer passes the same strict schema validation as any leg (it already does inside `OpenCodeMessagesProvider`). A malformed, empty or schema-invalid Qwen fallback answer never reaches the consumer; it goes to Luna.
- TST-001: Add regression tests covering at least:
  - Muse HTTP 500 -> Qwen answers
  - Muse fails + Qwen fails -> Luna answers
  - Muse fails + Qwen schema-invalid -> Luna
  - deadline blocks stage 1
  - deadline blocks stage 2 after a Qwen failure
  - flag false -> Muse -> Luna with 0 Qwen requests
  - each leg called exactly once
  - important and critical routes unchanged
  - telemetry fields
  - long error detail preserved up to the bound, with no secrets
  - config parsing, including the invalid value

  Update existing `FallbackContractTests` honestly. Where a test's intent is "an OpenCode failure ends on a validated Luna answer", make Qwen fail too so it still proves that. Where the intent is "the fallback is used", assert the new chain. State the intent change in each touched test's docstring. Falsify every new test against the pre-change `ai_provider.py`/`ai_gate.py` and record which fail.
- TST-002: Run the focused file, every test file that references `OpenCodeRoutedProvider`, `opencode_fallback` or `OPENCODE_` settings (for example `test_transport_failure_recovery.py` and the runtime env/config tests), and then the complete Python suite from `python/`. Record exact commands and counts.
- SAF-001: Offline only.
  - Make no real provider calls.
  - Do not touch `python/.env`, the Common Files bus, the running `ai_gate.py` process (pid 10236) or the deployed MT5 terminal.
  - No MQL change is expected. Do not change AI thresholds, schemas, admission-retry logic or any other provider's behavior.

## Work packages

### WP1 — Qwen stage in the OpenCode fallback chain + complete failure detail

Objective: Implement FB-001..FB-007 and TST-001.
Runtime authority: repository source and unit-test doubles only.
Allowed scope:
- `python/ai_provider.py`: `OpenCodeRoutedProvider` only, including `_failure_fields`
- `python/ai_gate.py`: config field/parsing, provider construction, banner
- `python/.env.example`
- `python/tests/test_opencode_provider_contract.py`
- a new focused test file, if cleaner

Forbidden changes:
- other provider classes
- `_admission_rejected` and the retry loops
- schemas and contract versions
- `.env`
- MQL
- live/runtime paths

Acceptance evidence: diff limited to the allowed scope; new tests fail on the pre-change source and pass after; flag-off equivalence proven by a test.
Tests: focused file plus the related files listed in TST-002.
Escalation triggers: the chain cannot be added without changing a leg's own retry/validation contract; a decision-cache or identity test shows that reporting the Qwen leg's identity breaks replay semantics.

### WP2 — Verification and independent review

Objective: Run TST-002, audit the complete diff and test integrity, and get one independent logic review focused on exactly-once calls per leg, deadline handling, truthful attribution and secret safety.
Runtime authority: Offline only.
Allowed scope: tests and review fixes within the WP1 scope.
Forbidden changes: unrelated cleanup, deployment, restarting the gate.
Acceptance evidence: full-suite result; reviewer findings and their closure; requirement-by-requirement status.
Escalation triggers: more than two failed attempts with the same approach; projected budget overrun.

## Budgets

- target wall clock: 90 minutes
- material model invocations: <= 8
- repair attempts per approach: <= 2
- normal low-cost model map; no vision

## Runtime safety

- RuntimeMode: Offline
- Demo authorization: NO
- Real-money/live mutation: forbidden
- Production Common Files bus: do not read or write; all needed evidence is quoted above.

## Final proof

- requirement coverage for every stable ID;
- changed files/functions;
- focused and full test commands with exact results;
- pre-change falsification of new tests;
- complete final diff audit, including preservation of unrelated dirty files;
- independent review findings and closure;
- actual model IDs and budget summary;
- explicit unresolved uncertainty (notably: whether an HTTP 429 `GoUsageLimitExceeded` on Muse is account-wide and therefore also blocks Qwen — do not special-case it in code, just report it).
