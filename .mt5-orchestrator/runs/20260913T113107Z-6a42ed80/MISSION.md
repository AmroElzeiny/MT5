# Mission

Front-end: Claude
Tier: Standard
RuntimeMode: Offline

## Outcome

Replace the Qwen leg of the OpenCode Go routed provider with **DeepSeek `deepseek-v4.1-flash` at reasoning effort `high`**, served over the same OpenCode Go `/responses` endpoint Muse already uses. Muse (`muse-spark-1.3-contributor`) stays the primary. Luna stays the last leg. The chain shape does not change:

- normal route: Muse -> DeepSeek -> Luna (each leg at most once)
- important route: DeepSeek -> Luna (never Muse)
- critical route: Luna only

## Confirmed baseline (front-end verified 2026-09-13, do not rediscover)

- Live wire probe of `deepseek-v4.1-flash` (front-end, one tiny call per endpoint):
  - `POST <base>/responses` with `reasoning={"effort":"high"}`, `truncation="disabled"`, `max_output_tokens=4000`, strict `text.format` json_schema -> **HTTP 200**, `status=completed`, response echoes `reasoning.effort=high`, `reasoning_tokens=60`, message content is schema-conforming JSON. Same headers Muse sends (`user-agent: opencode/1.0.0`, `x-opencode-session`).
  - `POST <base>/chat/completions` with `response_format` json_schema -> HTTP 400 `This response_format type is unavailable now`. Do not use this dialect.
  - `POST <base>/messages` works but has no strict-schema or effort control. Do not use it.
  - Conclusion: the DeepSeek leg must be an `OpenCodeResponsesProvider` (the Muse transport class), not `OpenCodeMessagesProvider`.
- `python/ai_provider.py`:
  - `OpenCodeResponsesProvider` (~2994-3130) already does everything the DeepSeek leg needs: strips `service_tier`/`prompt_cache_key`/`prompt_cache_retention`, forces `truncation=disabled`, adds `reasoning_token_reserve` on top of `max_output_tokens`, sets the session header, `schema_repair_attempts=0`, `resubmit_ambiguous_transport_failures=False`. It hardcodes `self.provider_id = "opencode_go_responses"`.
  - `OpenCodeRoutedProvider` (~3270-3656) takes `muse=`, `qwen=`, `fallback=`, `qwen_fallback_enable=`; attributes `self._qwen`, `self._qwen_fallback_enabled`; `_leg_for`, `identity()` (`opencode_legs["important"]`), `clear_configuration_circuit`, `configuration_circuit_count`, `_fallback_legs_after`, and the `[opencode_routing]` log field `qwen_fallback=` all name Qwen.
  - `OpenCodeMessagesProvider` / `_OpenCodeMessagesClient` (~2685-2992, ~3133-3268): the Anthropic-dialect transport. After this change it is no longer wired into the routed provider.
- `python/ai_gate.py`:
  - Config fields ~529-560: `opencode_qwen_model`, `opencode_qwen_fallback_enable`, `opencode_anthropic_version`, `opencode_enable_thinking`, `opencode_thinking_budget_tokens`, `opencode_context_budget_tokens`. Parsing ~605-626, ~787-802 (validation errors incl. `OPENCODE_QWEN_MODEL=missing`), ~995-1043. Banner ~1203-1228.
  - Provider construction ~1381-1452 builds `qwen=OpenCodeMessagesProvider(...)`.
  - Usage-ledger effort ~4261-4271 picks `opencode_muse_reasoning_effort` only when `provider_id == "opencode_go_responses"`, else `"none"`; comment says "Muse or Qwen".
- MQL knows only the provider mode `OPENCODE_API` (`Config.mqh:42`), never a provider_id or model. **No MQL change.**
- Tests: `python/tests/test_opencode_provider_contract.py` (helpers `_muse`, `_qwen`, `_luna`, `_routed(... qwen_answer=, qwen_as_text=, qwen_fallback_enable=)`, classes `FallbackContractTests`, `QwenFallbackChainTests`, dialect tests for `_OpenCodeMessagesClient`). `python/docs/runtime_env_template_opencode.txt` and `python/.env.example` (~226-319) document the Qwen leg.
- `python/ai_provider.py`, `python/ai_gate.py` and many other files are already dirty in git from earlier work. Preserve every existing uncommitted change; edit on top of it.

## Requirements

- DS-001: The second OpenCode leg is an `OpenCodeResponsesProvider` built with model `cfg.opencode_secondary_model` (default `deepseek-v4.1-flash`), `reasoning_effort=cfg.opencode_secondary_reasoning_effort` (default `high`), `reasoning_token_reserve=cfg.opencode_secondary_reasoning_token_reserve` (default 24000, same bounds as the Muse reserve), and the same base URL, key, timeout, max_output_tokens and `common` kwargs as Muse.
- DS-002: `OpenCodeResponsesProvider` gains an optional `provider_id` constructor kwarg defaulting to `"opencode_go_responses"` (Muse keeps that id exactly, so Muse's identity and decision-cache compatibility do not move). The DeepSeek leg uses `provider_id="opencode_go_responses_secondary"` so fallback telemetry (`from=`/`to=`) and `ProviderResult.provider_id` name the answering leg truthfully.
- DS-003: New settings, parsed like their Muse siblings (same validation, same invalid-value warnings/defaults): `OPENCODE_SECONDARY_MODEL` (default `deepseek-v4.1-flash`; empty -> validation error `OPENCODE_SECONDARY_MODEL=missing`, fail closed), `OPENCODE_SECONDARY_REASONING_EFFORT` (default `high`, same allowed set as `OPENCODE_MUSE_REASONING_EFFORT`, invalid -> warning `OPENCODE_SECONDARY_REASONING_EFFORT=invalid` and `high`), `OPENCODE_SECONDARY_REASONING_TOKEN_RESERVE` (default 24000), `OPENCODE_SECONDARY_FALLBACK_ENABLE` (bool, default true, invalid -> warning `OPENCODE_SECONDARY_FALLBACK_ENABLE=invalid_bool` and default). Wire all through `AIGateRuntimeConfig`, provider construction and the active-config banner (empty/false/0 when not the OpenCode provider, following the neighbouring pattern).
- DS-004: Retire the Qwen settings. `OPENCODE_QWEN_MODEL` and `OPENCODE_QWEN_FALLBACK_ENABLE` are no longer read as values. If either is present in the environment, emit a validation warning `OPENCODE_QWEN_MODEL=ignored_superseded_by_OPENCODE_SECONDARY_MODEL` / `OPENCODE_QWEN_FALLBACK_ENABLE=ignored_superseded_by_OPENCODE_SECONDARY_FALLBACK_ENABLE` — never silently map the Qwen model name onto the `/responses` leg (it is refused there: "not supported for format openai"). Remove config fields and banner keys that fed only the Qwen leg (`opencode_qwen_*`, `opencode_anthropic_version`, `opencode_enable_thinking`, `opencode_thinking_budget_tokens`, `opencode_context_budget_tokens`) after grepping that nothing else reads them; `opencode_parallelism` stays (used ~12459).
- DS-005: Rename the routed provider's Qwen vocabulary to neutral names: kwargs `secondary=` / `secondary_fallback_enable=`, attributes `_secondary` / `_secondary_fallback_enabled`, log field `secondary_fallback=`, docstrings. `identity()["opencode_legs"]["important"]` now reports the DeepSeek model. Routing semantics, the deadline rule, exactly-once-per-leg, `_fallback_metadata`, `_failure_fields` and all other behaviour stay byte-identical.
- DS-006: The usage-ledger effort in `ai_gate.py` (~4261-4271) must record the answering leg's own effort: Muse effort for `opencode_go_responses`, secondary effort for `opencode_go_responses_secondary`. Update the comment.
- DS-007: `OpenCodeMessagesProvider` and `_OpenCodeMessagesClient` stay in the file as a tested transport (their dialect unit tests keep passing); update their docstrings to say they are not wired into `OpenCodeRoutedProvider`. Do not delete them.
- DS-008: Docs: update `python/.env.example` OpenCode block and `python/docs/runtime_env_template_opencode.txt` to the new three-leg description and settings (include the probe facts from the baseline above). **Do not read, open or edit `python/.env`** — it holds live credentials; the front-end handles it.
- TST-001: Update `test_opencode_provider_contract.py` honestly: the second leg double becomes an `OpenCodeResponsesProvider` double for `deepseek-v4.1-flash` (reuse the Muse double pattern). Rename Qwen helpers/classes/params. Add regression tests at least for: default secondary model/effort/reserve; the built provider's secondary leg is an `OpenCodeResponsesProvider` with model `deepseek-v4.1-flash`, `reasoning_effort="high"` and `provider_id="opencode_go_responses_secondary"`; the secondary leg's wire request carries `reasoning.effort=high`, `truncation=disabled`, the session header, the strict schema, and `max_output_tokens` = schema budget + reserve; Muse still reports `provider_id="opencode_go_responses"`; the legacy Qwen settings produce the ignored warnings and do not change the model; invalid effort/bool values; missing secondary model fails closed; banner keys; usage-ledger effort per leg; Muse -> DeepSeek -> Luna chain, DeepSeek-primary important route never falls back to Muse, flag false restores Muse -> Luna. Falsify every new test against the pre-change source (e.g. `git stash`-free: copy the pre-change files to a temp dir and point imports there, or reason explicitly per test) and record which fail.
- TST-002: Run the focused file, every test file referencing `OpenCodeRoutedProvider`, `opencode_` config, `OPENCODE_` settings or the banner (grep for them; include `test_transport_failure_recovery.py`, `test_pending_ai_recovery_lifecycle.py`, runtime env/config tests), then the complete Python suite from `python/`. Record exact commands and counts. Previous full-suite baseline for context: 1053 passed, 12 skipped, 595 subtests (2026-09-09; later work may have changed it — record your own pre-change baseline first).
- SAF-001: Offline only. No real provider calls. Do not touch `python/.env`, the Common Files bus, any running `ai_gate.py` process or the MT5 terminal. No MQL change. No change to schemas, thresholds, contract versions, admission-retry logic, `_admission_rejected`, retry loops, or any non-OpenCode provider.

## Work packages

### WP1 — DeepSeek secondary leg + config + docs + tests

Objective: DS-001..DS-008, TST-001.
Allowed scope: `python/ai_provider.py` (`OpenCodeResponsesProvider.__init__` provider_id kwarg, `OpenCodeRoutedProvider`, the two messages-transport docstrings), `python/ai_gate.py` (config, parsing, validation, banner, construction, usage-ledger effort), `python/.env.example`, `python/docs/runtime_env_template_opencode.txt`, `python/tests/test_opencode_provider_contract.py`, other test files only where they assert the renamed fields/settings.
Forbidden: `python/.env`, MQL, schemas/contract versions, other providers, retry/admission logic.
Acceptance: diff within scope; new tests fail pre-change and pass after; Muse identity unchanged (test).
Escalation triggers: a decision-cache/identity test shows the Muse identity moved; a test outside scope must change for a reason other than the rename.

### WP2 — Verification and independent review

Objective: TST-002, full diff audit (including preservation of pre-existing dirty changes), one independent logic review focused on: Muse identity unchanged, truthful per-leg attribution, exactly-once per leg, deadline rule unchanged, no secret in any log, no silent Qwen->responses mapping.
Allowed scope: tests and review fixes within WP1 scope.

## Budgets

- target wall clock: 90 minutes
- material model invocations: <= 8
- repair attempts per approach: <= 2
- normal low-cost model map; no vision

## Runtime safety

- RuntimeMode: Offline
- Demo authorization: NO
- Real-money/live mutation: forbidden

## Final proof

- requirement coverage per ID; changed files/functions; exact test commands and counts (focused, related, full); pre-change falsification; final diff audit; independent review findings and closure; actual model IDs and budget; unresolved uncertainty (notably: DeepSeek's behaviour on the full-size analyst prompt at effort=high is unmeasured — only a tiny probe was run).
