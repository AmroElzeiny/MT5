from __future__ import annotations

import json
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jsonschema import Draft202012Validator
from .job_store import job_store
from .registry import load_profiles, profile_by_alias
from .jsonutil import strict_json_object
from .config import settings
from .browser import browser_manager

router = APIRouter()


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
              .replace('"', "&quot;").replace("'", "&#39;"))


@router.get("/", response_class=HTMLResponse)
def dashboard():
    jobs = job_store.list_pending()
    cards = []
    for j in jobs:
        p = profile_by_alias(j["chat_alias"])
        model_note = f"{p.desired_model} / effort {p.desired_effort}" if p else "unregistered"
        seconds = max(0, int(j["deadline_at"] - __import__("time").time()))
        cards.append(f"""
        <section class='card'>
          <div class='row'><strong>{esc(j['request_id'] or j['id'])}</strong><span class='pill'>{seconds}s left</span></div>
          <div class='muted'>route: {esc(j['chat_alias'])} · configured ChatGPT model: {esc(model_note)}</div>
          <div class='buttons'>
            <button onclick="copyPrompt('{j['id']}')">Copy prompt</button>
            <button onclick="openChat('{j['chat_alias']}')">Open registered chat</button>
          </div>
          <textarea id='prompt-{j['id']}' class='prompt' readonly>{esc(j['prompt'])}</textarea>
          <form method='post' action='/jobs/{j['id']}/submit'>
            <label>Paste the assistant JSON response after you have reviewed it:</label>
            <textarea name='response_text' class='response' required spellcheck='false'></textarea>
            <label class='review'><input type='checkbox' name='reviewed' value='true' required> I reviewed this response before sending it back to the calling system.</label>
            <button type='submit' class='submit'>Validate & return</button>
          </form>
        </section>
        """)
    profile_rows = "".join(
        f"<li><b>{esc(p.alias)}</b> → {esc(p.model_id)} · {esc(p.desired_model)} / {esc(p.desired_effort)}</li>"
        for p in load_profiles()
    ) or "<li>No chat is registered yet. Run register_chat.bat.</li>"
    body = f"""<!doctype html><html><head><meta charset='utf-8'><title>Local AI Review Bridge</title>
    <style>
      body{{font-family:Segoe UI,Arial,sans-serif;background:#111318;color:#e9edf3;margin:0;padding:24px;max-width:1200px;margin:auto}}
      h1{{margin:0 0 8px}} .muted{{color:#9ca7b8;font-size:13px}} .card{{background:#1a1e26;border:1px solid #303744;border-radius:14px;padding:18px;margin:18px 0}}
      .row{{display:flex;justify-content:space-between;gap:12px}} .pill{{background:#263243;padding:4px 9px;border-radius:999px;font-size:12px}}
      textarea{{width:100%;box-sizing:border-box;background:#0e1116;color:#dfe7f1;border:1px solid #394353;border-radius:9px;padding:12px;margin:9px 0;font-family:Consolas,monospace}}
      .prompt{{height:180px}} .response{{height:260px}} button{{background:#2b70e4;color:white;border:0;border-radius:8px;padding:9px 13px;cursor:pointer;margin-right:7px}}
      .submit{{background:#2e9d62}} .buttons{{margin:12px 0}} .review{{display:block;margin:7px 0 12px}} ul{{line-height:1.7}}
    </style>
    <script>
      async function copyPrompt(id){{ const el=document.getElementById('prompt-'+id); await navigator.clipboard.writeText(el.value); }}
      async function openChat(alias){{ await fetch('/browser/open/'+encodeURIComponent(alias),{{method:'POST'}}); }}
      setInterval(()=>{{ const a=document.activeElement; if(!a || a.tagName !== 'TEXTAREA') location.reload(); }},5000);
    </script></head><body>
    <h1>Local AI Review Bridge</h1>
    <div class='muted'>Loopback only · queue limit {settings.max_pending_jobs} · hard deadline {settings.hard_timeout_sec}s · human review required</div>
    <h3>Registered chats</h3><ul>{profile_rows}</ul>
    <h2>Pending requests ({len(jobs)})</h2>{''.join(cards) if cards else '<div class="card">No pending AI request.</div>'}
    </body></html>"""
    return HTMLResponse(body)


@router.post("/browser/open/{alias}")
def open_browser(alias: str):
    try:
        browser_manager.open_chat(alias)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/submit")
def submit_job(job_id: str, response_text: str = Form(...), reviewed: str | None = Form(default=None)):
    row = job_store.get(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    reviewed_bool = reviewed == "true"
    if settings.require_human_review and not reviewed_bool:
        raise HTTPException(status_code=400, detail="human_review_required")
    if len(response_text.encode("utf-8")) > settings.max_response_bytes:
        raise HTTPException(status_code=400, detail="response_too_large")
    try:
        obj = strict_json_object(response_text)
        schema = json.loads(row["schema_json"]) if row.get("schema_json") else None
        if schema:
            Draft202012Validator(schema).validate(obj)
    except Exception as exc:
        return HTMLResponse(
            f"<h2>Response rejected</h2><pre>{esc(str(exc))}</pre><p><a href='/'>Back</a></p>", status_code=400
        )
    job_store.submit(job_id, json.dumps(obj, ensure_ascii=False, separators=(",", ":")), reviewed_bool)
    return RedirectResponse(url="/", status_code=303)


@router.get("/api/pending")
def pending_api():
    return JSONResponse(job_store.list_pending())
