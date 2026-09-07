from __future__ import annotations

import json
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jsonschema import Draft202012Validator
from .job_store import job_store
from .registry import load_profiles, profile_by_alias
from .jsonutil import strict_json_object
from .config import settings
from .browser import status as browser_status, open_url

router = APIRouter()


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
              .replace('"', "&quot;").replace("'", "&#39;"))


@router.get("/", response_class=HTMLResponse)
def dashboard():
    jobs = job_store.list_jobs(50)
    state = browser_status()
    rows = []
    for j in jobs:
        rows.append(
            f"<tr><td>{esc(j['request_id'])}</td><td>{esc(j['chat_alias'])}</td><td>{esc(j['status'])}</td>"
            f"<td>{esc(j.get('error') or '')}</td></tr>"
        )
    profiles = "".join(
        f"<li><b>{esc(p.alias)}</b> → {esc(p.model_id)} · {esc(p.desired_model)} / {esc(p.desired_effort)} "
        f"<button onclick=\"openChat('{esc(p.alias)}')\">Open</button></li>" for p in load_profiles()
    ) or "<li>No chat registered. Run register_chat.bat.</li>"
    config_state = "READY" if settings.browser_configured else "BLOCKED: configure selectors in .env"
    body = f"""<!doctype html><html><head><meta charset='utf-8'><title>Autonomous AI Browser Bridge</title>
    <style>body{{font-family:Segoe UI,Arial;background:#111318;color:#e9edf3;max-width:1200px;margin:auto;padding:24px}}.card{{background:#1a1e26;border:1px solid #303744;border-radius:12px;padding:16px;margin:14px 0}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #303744;text-align:left}}code{{color:#b9d4ff}}button{{padding:6px 10px}}</style>
    <script>async function openChat(alias){{await fetch('/browser/open/'+encodeURIComponent(alias),{{method:'POST'}});}}</script>
    </head><body><h1>Autonomous AI Browser Bridge</h1>
    <div class='card'><b>Automation:</b> {esc(config_state)}<br><b>Browser:</b> {esc(state)}<br>
    <b>Mode:</b> {'headless' if settings.browser_headless else 'headful'} · <b>delivery:</b> {esc(settings.prompt_delivery_mode)} · <b>response:</b> {esc(settings.response_mode)}</div>
    <div class='card'><h3>Registered chats</h3><ul>{profiles}</ul></div>
    <div class='card'><h3>Recent jobs</h3><table><tr><th>Request</th><th>Route</th><th>Status</th><th>Error</th></tr>{''.join(rows)}</table></div>
    </body></html>"""
    return HTMLResponse(body)


@router.post("/browser/open/{alias}")
def open_browser(alias: str):
    profile = profile_by_alias(alias)
    if not profile:
        raise HTTPException(status_code=404, detail="chat_profile_not_found")
    open_url(profile.conversation_url)
    return {"ok": True}


@router.post("/jobs/{job_id}/submit")
def submit_job(job_id: str, response_text: str = Form(...)):
    if not settings.allow_manual_submit:
        raise HTTPException(status_code=403, detail="manual_submit_disabled")
    row = job_store.get(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    try:
        obj = strict_json_object(response_text)
        schema = json.loads(row["schema_json"]) if row.get("schema_json") else None
        if schema:
            Draft202012Validator(schema).validate(obj)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"structured_response_invalid:{exc}") from exc
    job_store.submit(job_id, json.dumps(obj, ensure_ascii=False, separators=(",", ":")), reviewed=True)
    return RedirectResponse(url="/", status_code=303)


@router.get("/api/pending")
def pending_api():
    return JSONResponse(job_store.list_pending())


@router.get("/api/browser")
def browser_api():
    return JSONResponse(browser_status())
