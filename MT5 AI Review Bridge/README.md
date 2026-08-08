# Local AI Review Bridge

A local, loopback-only bridge that lets your existing PO3 Python gate talk to a human-reviewed browser conversation through an OpenAI-compatible endpoint **without changing PO3's decision logic**.

## What this package does

- Exposes `GET /v1/models` and `POST /v1/chat/completions` on `127.0.0.1:1234`.
- Accepts the exact structured request that the current `LocalOpenAICompatibleProvider` sends.
- Preserves the JSON Schema from PO3's `response_format`.
- Creates a queued review job with a hard 180-second deadline.
- Keeps at most 3 pending jobs.
- Opens a dedicated persistent profile in your installed Google Chrome (preferred; Edge fallback) and the registered ChatGPT conversation.
- Gives you one-click **Copy prompt** and **Open registered chat** controls.
- Requires you to review and paste the assistant's JSON response into the local dashboard.
- Strictly rejects duplicate JSON keys, trailing prose, non-object responses, oversized responses, and JSON that fails the supplied schema.
- Returns an OpenAI-compatible chat-completion response to PO3.
- PO3's existing Python layer then performs its own stronger candidate/identity/evidence/immutability validation before MT5 receives an authoritative response.
- Includes an independent common-folder gateway for other EAs/projects.

## Important boundary

This package deliberately does **not** scrape or programmatically copy ChatGPT consumer-interface output. The browser is opened and session state is retained, but the assistant response is manually reviewed and pasted into the local bridge. This keeps the PO3 integration usable while avoiding automated extraction from the ChatGPT consumer UI.

For real-money trading, keep human review enabled. The package defaults to `REQUIRE_HUMAN_REVIEW=true` and the dashboard will not return a response without the review checkbox.

## Browser/login change in this build

The login layer no longer launches Playwright or its bundled Chromium. It launches the normal installed Google Chrome executable with the existing dedicated `state/browser_profile` directory using Chrome's supported `--user-data-dir` mechanism. No webdriver, Playwright, remote-debugging, or automation flags are used. This is intentionally isolated from the queue, schema, API, folder gateway, deadlines, and PO3 validation code.

If Chrome is not installed, Microsoft Edge is tried next; only then does the bridge fall back to the Windows default browser. You may optionally set `BROWSER_EXECUTABLE` in `.env` to an exact browser executable path if Chrome is installed in a non-standard location.

## Installation (Windows)

1. Extract the folder to a stable location, e.g. `C:\AI\local_ai_review_bridge`.
2. Run `install.bat`.
3. Run `register_chat.bat`.
4. A normal installed Google Chrome window opens with a dedicated persistent bridge profile. Sign in manually to ChatGPT, open the PO3 conversation you want to use, return to the terminal, and paste its exact URL. Microsoft Edge is used only if Chrome is unavailable.
5. Keep the default local model ID `chatgpt-browser-review` unless you deliberately want another ID.
6. Run `run_bridge.bat`.
7. The dashboard is `http://127.0.0.1:1234/`.

The first import automatically creates `.env` with a random 256-bit-style URL-safe bridge API key. Browser cookies are **not** stored in `.env`; they remain inside the dedicated installed-browser profile under `state/browser_profile/`. Do not copy that directory to another person or commit it.

## Connect current PO3 without changing its provider code

Your current repository already supports a loopback OpenAI-compatible provider. After registration, run:

```powershell
.\setup_po3_env.ps1 -Po3EnvPath "C:\path\to\your\MT5\python\.env"
```

If your private runtime `.env` is elsewhere, point the command at that actual file.

The script reads the bridge's generated local API key directly from `.env` and applies these PO3 settings:

- `AI_USE_REMOTE_API=false`
- `LOCAL_AI_BASE_URL=http://127.0.0.1:1234/v1`
- `LOCAL_AI_HEALTHCHECK_PATH=/models`
- local analyst/critic/adjudicator model = `chatgpt-browser-review`
- `LOCAL_AI_TIMEOUT_SEC=180`
- `LOCAL_AI_MAX_RETRIES=0`
- strict JSON Schema required
- no screenshots
- max live candidate budget = 3
- MT5 terminal timeout = 180 seconds

The bridge does **not** write PO3's authoritative `responses/` files. It behaves only as the local model transport. Your current `ai_gate.py` receives the bridge output, validates it using the existing strict model schema, binds identity/candidates/evidence, and only then participates in the normal FileBus response lifecycle.

## Request lifecycle

```text
MT5 EA
  -> PO3 Common/Files/.../requests/<unique-id>.json
  -> existing ai_gate.py
  -> LocalOpenAICompatibleProvider
  -> http://127.0.0.1:1234/v1/chat/completions
  -> Local AI Review Bridge job
  -> registered headful Google Chrome/Edge conversation
  -> you review + paste strict JSON into local dashboard
  -> bridge validates the supplied JSON Schema
  -> OpenAI-compatible HTTP response
  -> existing PO3 schema/identity/evidence gate
  -> normal PO3 response file
  -> MT5
```

## Folder gateway for other EAs/projects

The bridge also watches:

- `folder_bus/requests/*.json`
- writes validated raw replies to `folder_bus/responses/<request_id>.json`

Requests are atomic files with a unique `request_id`. See `folder_request_example.json` for the complete supported format. The caller can include its own JSON Schema. If no schema is supplied, the bridge still requires one strict JSON object.

The gateway processes up to 3 pending requests and preserves the per-request timeout, capped at 180 seconds.

For production EAs, write the request to a temporary filename and atomically rename it to `.json` only after the file is complete.

## Chat registry and multiple conversations

Version 1 uses one default conversation, but the registry already supports multiple conversations safely.

Each registered chat has:

```json
{
  "alias": "po3",
  "model_id": "chatgpt-browser-review",
  "conversation_url": "https://chatgpt.com/c/...",
  "desired_model": "GPT-5.6 Sol",
  "desired_effort": "high",
  "enabled": true
}
```

To add another conversation later, rerun `register_chat.bat` and choose a different alias and local `model_id`. `/v1/models` exposes every enabled registry entry. A calling EA/server chooses the conversation by model ID; the generic folder gateway can choose by `chat_alias`.

A fully automated provider that is permitted to automate its web UI could reuse this same registry and replace only the browser handoff layer. The folder bus, queue, schema validation, timeouts, audit log, model routing, and PO3 integration do not need to change.

## Security

- Server binds to `127.0.0.1` by default.
- Every OpenAI-compatible endpoint requires the random bearer key in `.env`.
- Do not expose port 1234 to the LAN or Internet.
- `.env`, browser profile, chat registry, SQLite state, and audit logs are git-ignored.
- The dedicated browser profile contains authentication cookies and should be treated like a password vault.
- The bridge never asks for or stores your ChatGPT password.

## Failure behavior

The bridge fails closed:

- queue > 3: HTTP 429
- request exceeds 180 seconds: HTTP 504
- unknown registered model: HTTP 400
- wrong local bearer key: HTTP 401
- invalid/trailing/duplicate-key JSON: rejected
- supplied JSON Schema violation: rejected
- browser unavailable: request remains reviewable in the local dashboard, but the bridge logs the browser failure
- no reviewed response before the deadline: no provider result is returned

PO3 then applies its own existing degraded/non-trading behavior when the local provider call fails or times out.

## Files that matter

- `.env` — generated local secrets and runtime configuration
- `state/chats.json` — saved conversation URL + configured model/effort metadata
- `state/browser_profile/` — dedicated Google Chrome/Edge session state/cookies
- `state/bridge.sqlite3` — request queue state
- `logs/audit.jsonl` — local audit history
- `folder_bus/requests/` and `folder_bus/responses/` — generic EA integration

