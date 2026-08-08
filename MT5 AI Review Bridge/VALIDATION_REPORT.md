# Validation Report

Build: Local AI Review Bridge 1.0.0 + browser-login patch

Validated before packaging:

- Python syntax compilation for all bridge modules and tests.
- Strict JSON parser accepts one JSON object and rejects duplicate keys, trailing content, and non-object roots.
- Prompt builder extracts request IDs and the caller-provided JSON Schema.
- `GET /v1/models` returns registered local model IDs with bearer authentication.
- `POST /v1/chat/completions` returns an OpenAI Chat Completions-compatible response.
- PO3's `provider_capability_probe` is answered locally with schema-validated `{"ok":true}` and does not consume a review slot.
- End-to-end queued OpenAI-compatible request -> human-reviewed response -> JSON Schema validation -> caller response passed.
- End-to-end generic folder request -> queued review -> validated response -> atomic response file passed.
- Hard runtime configuration caps pending jobs at 3 and request deadlines at 180 seconds.
- Browser module no longer imports or launches Playwright/Chromium.
- Browser discovery prefers installed Google Chrome, then Microsoft Edge, then the OS default browser.
- Dedicated browser session persistence still uses the unchanged `state/browser_profile` path.
- Installed Chrome/Edge is launched without webdriver, Playwright, remote-debugging, or automation flags.

## Change-isolation check

Only the browser/login-related files were intentionally modified from 1.0.0:

- `bridge/browser.py`
- `bridge/register.py`
- `requirements.txt`
- `install.bat`
- `README.md`
- `VALIDATION_REPORT.md`

All queue, job-store, JSON validation, OpenAI-compatible API, folder gateway, PO3 setup, models, registry, audit, web UI, timeout, and test implementation files are byte-for-byte unchanged from the previous package.

The packaged build intentionally excludes generated `.env`, browser cookies/profile data, SQLite runtime state, chat URLs, and audit logs. `install.bat` creates the runtime environment and a new random local bearer secret on the user's machine.
