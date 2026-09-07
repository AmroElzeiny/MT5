# Component changes from the uploaded manual bridge

## Kept unchanged

- `bridge/audit.py`
- `bridge/jsonutil.py`
- `bridge/models.py`
- `bridge/registry.py`
- PO3 boundary: generic folder gateway cannot target `PO3_AI_BUS`; PO3 `ai_gate.py` remains authoritative.
- OpenAI-compatible public contract: `GET /v1/models`, `POST /v1/chat/completions`.
- strict caller JSON Schema validation and capability-probe behavior.
- max pending jobs remains capped at 3; hard bridge deadline remains capped at 180 seconds.

## Replaced/extended

- `bridge/browser.py`: launcher-only browser → persistent Playwright-controlled installed Chrome/Edge session, with runtime state and headless support.
- `bridge/browser_worker.py`: new single autonomous job worker; text/file prompt delivery, send, scrolling, generation completion, text/download response collection, schema validation, no-resend-after-send invariant.
- `bridge/job_store.py`: adds atomic job claiming, deterministic dedupe hashes, sent marker, safe restart behavior.
- `bridge/openai_compat.py`: manual waiting path now feeds the autonomous browser worker; no manual response is required.
- `bridge/webapp.py`: dashboard is monitoring/diagnostics-first; manual submission is disabled by default.
- `bridge/config.py`: provider-neutral selector placeholders and browser mode settings.
- `bridge/register.py`: manual first login/session persistence remains, but accepts any authorized HTTP(S) provider UI.
- `bridge/diagnose.py`: new selector-count diagnostics before live use.
- `setup_po3_env.ps1`: response-write margin corrected to 15 seconds and defaults to the generic model ID `browser-ai-review`.

No provider-specific CSS selector or credential is shipped.
