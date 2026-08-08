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
from .prompting import build_manual_prompt
from .jsonutil import strict_json_object
from .audit import log_event
from .browser import browser_manager

router = APIRouter()


def _auth(authorization: str | None) -> None:
    expected = f"Bearer {settings.api_key}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="invalid_bridge_api_key")


@router.get("/models")
def models(authorization: str | None = Header(default=None)):
    _auth(authorization)
    data = []
    now = int(time.time())
    for p in load_profiles():
        if p.enabled:
            data.append({"id": p.model_id, "object": "model", "created": now, "owned_by": "local-ai-review-bridge"})
    return {"object": "list", "data": data}


@router.post("/chat/completions")
def chat_completions(payload: dict[str, Any], request: Request,
                     authorization: str | None = Header(default=None)):
    _auth(authorization)
    model = str(payload.get("model") or "")
    profile = profile_by_model(model)
    if not profile:
        raise HTTPException(status_code=400, detail=f"model_not_found:{model}")
    prompt, schema, request_id = build_manual_prompt(payload)
    if request_id == "provider_capability_probe":
        probe_obj = {"ok": True}
        if schema:
            try:
                Draft202012Validator(schema).validate(probe_obj)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"capability_probe_schema_invalid:{exc}") from exc
        text = json.dumps(probe_obj, separators=(",", ":"))
        return JSONResponse({
            "id": "chatcmpl-provider-capability-probe",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "system_fingerprint": "local-human-reviewed-browser-bridge-v1",
        })
    if settings.auto_open_browser:
        try:
            browser_manager.open_chat(profile.alias)
        except Exception as exc:
            log_event("browser_open_warning", error=str(exc), chat_alias=profile.alias)
    try:
        job = job_store.create(
            request_id=request_id,
            model_id=model,
            chat_alias=profile.alias,
            prompt=prompt,
            schema=schema,
            timeout_sec=settings.hard_timeout_sec,
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
        "id": "chatcmpl-" + job.id.replace("-", ""),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": json.dumps(obj, ensure_ascii=False, separators=(",", ":"))},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": "local-human-reviewed-browser-bridge-v1",
    }
    log_event("openai_compat_response_returned", job_id=job.id, request_id=job.request_id, model_id=model)
    return JSONResponse(response)
