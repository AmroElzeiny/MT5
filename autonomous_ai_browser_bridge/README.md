# Autonomous AI Browser Bridge v2.1

Provider-agnostic browser automation transport for the existing PO3 `LocalOpenAICompatibleProvider` and for other folder-based callers.

## What changed from the manual bridge

The manual copy/paste operator is removed from the normal path. A single autonomous browser worker now:

1. claims the oldest queued request;
2. opens the registered conversation in a persistent installed Chrome/Edge profile;
3. optionally selects the configured model/effort;
4. delivers the request as text, as a JSON attachment, or automatically chooses based on size;
5. sends through a configured Send selector or keyboard key;
6. scrolls and watches the configured assistant-message selector;
7. waits until generation is complete/stable;
8. accepts direct JSON or one whole fenced JSON object;
9. optionally downloads a `.json/.txt/.md` response into a controlled directory;
10. validates strict JSON + the caller's JSON Schema;
11. submits the normalized JSON to the bridge job;
12. returns the normal OpenAI-compatible completion to `ai_gate.py`.

PO3 remains the authoritative writer of `PO3_AI_BUS/responses`; this bridge never targets that directory.

The browser and official API transport comparison is documented in
`API_TRANSPORT_PARITY.md` and enforced by an executable parity test.

## Important provider boundary

This package intentionally contains **no provider-specific selectors** and no anti-bot/stealth/CAPTCHA bypass. Configure it only against a web UI you control or are authorized to automate. The selectors in `.env` are empty by design.

## Upgrade from the previous manual bridge

If you want to reuse the existing logged-in browser identity, copy **only** `state/browser_profile/` and `state/chats.json` from the old bridge into this package while both bridges are stopped. Do not copy the old SQLite queue. The default exposed model ID remains `chatgpt-browser-review` for PO3 compatibility.

## Install

1. `install.bat`
2. `manual_login.bat` — signs you in using a plain browser run (see "Sign-in" below), persisting the session under `state/browser_profile`.
3. `register_chat.bat` — opens headful, reuses that session, and records the conversation URL.
4. Edit `.env` and fill at minimum:
   - `BROWSER_CHAT_INPUT_SELECTOR`
   - `BROWSER_ASSISTANT_MESSAGE_SELECTOR`
   - optionally `BROWSER_SEND_SELECTOR` (otherwise `BROWSER_KEYBOARD_SEND=Enter`)
5. `diagnose_selectors.bat` — confirms selector counts on the registered conversation.
6. Choose `BROWSER_HEADLESS=false` for visible runtime or `true` for headless runtime.
7. `run_bridge.bat`
8. Dashboard: `http://127.0.0.1:1234/`

## Sign-in

Playwright drives Chrome over the DevTools Protocol and passes
`--enable-automation`, leaving `navigator.webdriver === true`. Some identity
providers — Google among them — refuse to accept credentials under those
conditions and answer with "this browser or app may not be secure". That check
is on the automation signals, not on the browser build: a genuine Chrome under
CDP is rejected just the same.

`manual_login.bat` avoids the problem instead of hiding it. It launches the same
installed browser against the same `state/browser_profile` directory with no
Playwright, no CDP and no automation switches, so the credential entry happens
in an ordinary browser session. Chrome persists the resulting cookies into that
profile, and every later automated run reuses an already-authenticated session
without going back through the sign-in flow.

Close the browser completely when done — Chrome holds a lock on the profile, and
flushes cookies to disk on exit. If the provider later forces a re-auth, run
`manual_login.bat` again.

No stealth, anti-bot or detection-evasion measures are used or supported.

## Prompt delivery

`BROWSER_PROMPT_DELIVERY_MODE=text` fills the composer with the full PO3 prompt.

`file` creates a controlled JSON envelope under `state/uploads/`, uploads it through `BROWSER_FILE_INPUT_SELECTOR`, and types `BROWSER_FILE_PROMPT_INSTRUCTION` into the composer.

`auto` uses a file only when a file-input selector exists and the prompt exceeds `BROWSER_FILE_UPLOAD_THRESHOLD_CHARS`; otherwise it uses text.

## Three independent temporary ChatGPT lanes

The worker keeps three independent tabs and processes up to three PO3 requests
in parallel. A request and its Analyst/Critic/Adjudicator sequence remain pinned
to one tab, and that tab never accepts another request until the current role
has returned or expired.

```env
BROWSER_CHAT_TAB_COUNT=3
BROWSER_REQUESTS_PER_TAB=3
BROWSER_CHAT_NEW_URL=https://chatgpt.com/?temporary-chat=true
BROWSER_CHAT_TAB_URLS=
```

Each completed role submission counts toward the tab's refresh counter. Before
the next PO3 request begins, a tab with three submissions navigates back to the
temporary-chat URL, which deletes that temporary conversation. This usually
aligns one Analyst/Critic/Adjudicator sequence with one temporary chat.

Tab distribution cannot bypass limits enforced globally per account or model.
When the exact configured rate-limit message is visible, the bridge can route
the still identity-bound request to the separately authenticated Gemini browser
fallback described below.

## Gemini browser fallback

Gemini fallback is disabled until its separate profile is authenticated and
the selectors are verified. This is intentional: a logged-out fallback must
not make a trading decision.

1. Run `login_gemini.ps1` while Gemini fallback is still disabled.
2. Sign in at `https://gemini.google.com/app`, verify that the prompt box is
   visible, and close Chrome completely.
3. The script runs `bridge.gemini_diagnose`, then changes
   `GEMINI_FALLBACK_ENABLED=true` only if the login/input selectors pass.
4. The script restarts the bridge. `/health` must show
   `gemini_fallback_ready=true` and `gemini_open_tabs=3`.

Gemini uses `state/gemini_browser_profile`, separate from ChatGPT's profile.
The fallback is activated only for explicit configured ChatGPT rate-limit text,
not for arbitrary timeouts or malformed replies. It preserves the request ID,
strict JSON Schema, absolute deadline, and single-tab request affinity.

The configured Gemini transport mirrors the primary file workflow:
`GEMINI_PROMPT_DELIVERY_MODE=file` uploads the identity-bound request envelope,
and `GEMINI_RESPONSE_MODE=download` opens the newest `.attachment-container`,
downloads the generated JSON from the Drive viewer, verifies the per-job filename
token, and then applies the same strict schema validation. Inline Gemini text is
not accepted while `download` mode is active.

## Response collection

`BROWSER_RESPONSE_MODE=text` reads the newest assistant element from `BROWSER_ASSISTANT_MESSAGE_SELECTOR`.

`download` waits for generation to settle, clicks `BROWSER_DOWNLOAD_LINK_SELECTOR`, saves the browser download to `state/downloads/<request-id>-<random>.<ext>`, and accepts only configured extensions.

`auto` tries strict text JSON first and falls back to the configured download control.

The parser does not hunt through arbitrary prose for braces. It accepts either one strict JSON object or exactly one whole Markdown JSON code fence. Ambiguous responses fail closed.

## Duplicate protection

- One browser worker processes one generation at a time, even with multiple tabs.
- Jobs receive a deterministic dedupe hash.
- The worker marks the job as sent immediately after the send action.
- After send, any timeout/crash/ambiguity **fails the job instead of automatically resending it**.
- On restart, a previously sent in-flight job becomes `restart_after_send_no_resubmit` rather than being sent twice.

## PO3 integration

After registration, use the same model ID in PO3:

```powershell
.\setup_po3_env.ps1 -Po3EnvPath "C:\path\to\MT5\python\.env" -ModelId "chatgpt-browser-review"
```

The live-forward launcher sets local provider mode, loopback URL, strict JSON
Schema, three local workers, no provider retries, a 30-minute terminal deadline,
a 15-second write margin, a three-candidate ceiling, and no snapshots.

## Provider-specific selector placeholders

See `.env.example`. Typical minimum:

```env
BROWSER_CHAT_INPUT_SELECTOR=
BROWSER_ASSISTANT_MESSAGE_SELECTOR=
BROWSER_SEND_SELECTOR=
BROWSER_STOP_GENERATING_SELECTOR=
BROWSER_SCROLL_CONTAINER_SELECTOR=
BROWSER_FILE_INPUT_SELECTOR=
BROWSER_DOWNLOAD_LINK_SELECTOR=
```

CSS selectors are passed directly to Playwright. Use stable `data-*`, `aria-*`, IDs, or other selectors exposed by the UI you are authorized to automate; avoid brittle generated class names where possible.

## Free/self-hosted UI candidates

The bridge is a UI automation layer, not the model itself. The easiest compliance path is a self-hosted UI backed by a local model (for example through Ollama), so both the UI and model execution can stay on your machine.

- **Open WebUI** — self-hosted, supports local models/Ollama and OpenAI-compatible endpoints. Current licensing permits personal/small-team use subject to its branding terms.
- **LibreChat** — self-hosted web app; MIT-licensed; supports custom endpoints including Ollama.
- **AnythingLLM** — local-first/self-hosted; its self-hosted terms state the core is MIT-licensed and can run air-gapped with local providers such as Ollama/LocalAI.

Always check the model's own license separately from the UI license.

## Security

The bridge binds to loopback by default. `.env`, session profile, SQLite DB, chat URLs, downloads/uploads and audit logs are gitignored. Treat `state/browser_profile` like a credential store.
