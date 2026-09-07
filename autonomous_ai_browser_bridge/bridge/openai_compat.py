from __future__ import annotations

import json
import time
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from .config import settings
from .registry import load_profiles, profile_by_model
from .job_store import job_store
from .prompting import build_automation_prompt
from .jsonutil import strict_json_object
from .audit import log_event

router = APIRouter()


def _auth(authorization: str | None) -> None:
    expected = f"Bearer {settings.api_key}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="invalid_bridge_api_key")


@router.get("/models")
def models(authorization: str | None = Header(default=None)):
    _auth(authorization)
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": p.model_id, "object": "model", "created": now, "owned_by": "autonomous-browser-bridge"}
            for p in load_profiles() if p.enabled
        ],
    }


@router.post("/chat/completions")
def chat_completions(payload: dict[str, Any], request: Request,
                     authorization: str | None = Header(default=None)):
    _auth(authorization)
    model = str(payload.get("model") or "")
    profile = profile_by_model(model)
    if not profile:
        raise HTTPException(status_code=400, detail=f"model_not_found:{model}")
    prompt, schema, request_id = build_automation_prompt(payload)
    if request_id == "provider_capability_probe":
        probe_obj = {"ok": True}
        if schema:
            Draft202012Validator(schema).validate(probe_obj)
        text = json.dumps(probe_obj, separators=(",", ":"))
        return JSONResponse({
            "id": "chatcmpl-provider-capability-probe", "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "system_fingerprint": "autonomous-browser-bridge-v2",
        })
    if not settings.browser_automation_enabled:
        raise HTTPException(status_code=503, detail="browser_automation_disabled")
    if not settings.browser_configured:
        raise HTTPException(status_code=503, detail="browser_selectors_not_configured")
    try:
        deadline_at = None
        raw_deadline_ms = request.headers.get("x-po3-deadline-epoch-ms", "").strip()
        if raw_deadline_ms:
            try:
                deadline_at = int(raw_deadline_ms) / 1000.0
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid_po3_deadline_header") from exc
        job = job_store.create(
            request_id=request_id, model_id=model, chat_alias=profile.alias,
            prompt=prompt, schema=schema, timeout_sec=settings.hard_timeout_sec,
            deadline_at=deadline_at,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    try:
        text = job_store.wait(job.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    if len(text.encode("utf-8")) > settings.max_response_bytes:
        raise HTTPException(status_code=400, detail="response_too_large")
    try:
        obj = strict_json_object(text)
        if schema:
            Draft202012Validator(schema).validate(obj)
    except Exception as exc:
        log_event("bridge_response_validation_failed", job_id=job.id, request_id=job.request_id, error=str(exc))
        raise HTTPException(status_code=400, detail=f"structured_response_invalid:{exc}") from exc
    prompt_tokens = max(1, len(prompt.encode("utf-8")) // 4)
    completion_tokens = max(1, len(text.encode("utf-8")) // 4)
    response = {
        "id": "chatcmpl-" + job.id.replace("-", ""), "object": "chat.completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(obj, ensure_ascii=False, separators=(",", ":"))}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
        "system_fingerprint": "autonomous-browser-bridge-v2",
    }
    log_event("openai_compat_response_returned", job_id=job.id, request_id=job.request_id, model_id=model)
    return JSONResponse(response)
