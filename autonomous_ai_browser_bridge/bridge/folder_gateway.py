from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
from .config import settings
from .job_store import job_store
from .registry import profile_by_model, profile_by_alias
from .jsonutil import strict_json_object
from .audit import log_event


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def build_prompt(req: dict[str, Any]) -> str:
    system = req.get("system_prompt")
    content = req.get("input") if "input" in req else req.get("payload")
    return "\n\n".join([
        "You are the structured decision model for an external validated system.",
        "Return ONE JSON object only. No markdown fences and no trailing prose.",
        f"BRIDGE REQUEST ID: {str(req.get('request_id') or '')}",
        "SYSTEM INSTRUCTIONS:\n" + (system if isinstance(system, str) else ""),
        "REQUEST / EVIDENCE:\n" + json.dumps(content, ensure_ascii=False, sort_keys=True),
    ])


def process_file(path: Path) -> None:
    try:
        req = strict_json_object(path.read_text(encoding="utf-8"))
        request_id = str(req.get("request_id") or path.stem)
        model_id = str(req.get("model") or "")
        alias = str(req.get("chat_alias") or settings.default_chat_alias)
        profile = profile_by_model(model_id) if model_id else profile_by_alias(alias)
        if not profile:
            raise RuntimeError("chat_profile_not_found")
        schema = req.get("response_schema") if isinstance(req.get("response_schema"), dict) else None
        timeout_sec = int(req.get("timeout_sec") or settings.hard_timeout_sec)
        job = job_store.create(request_id, profile.model_id, profile.alias, build_prompt(req), schema, timeout_sec)
        response_text = job_store.wait(job.id)
        obj = strict_json_object(response_text)
        if schema:
            Draft202012Validator(schema).validate(obj)
        atomic_write_json(settings.folder_response_dir / f"{request_id}.json", obj)
        path.unlink(missing_ok=True)
        log_event("folder_request_completed", request_id=request_id, source_file=path.name)
    except Exception as exc:
        error_id = path.stem
        atomic_write_json(settings.folder_response_dir / f"{error_id}.error.json", {"request_id": error_id, "status": "error", "error": str(exc)})
        path.unlink(missing_ok=True)
        log_event("folder_request_failed", request_id=error_id, error=str(exc))


def run(stop: threading.Event) -> None:
    if not settings.folder_gateway_enabled:
        return
    active: set[str] = set()
    lock = threading.Lock()
    while not stop.is_set():
        files = sorted(settings.folder_request_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        with lock:
            capacity = settings.max_pending_jobs - len(active)
        for path in files[:max(0, capacity)]:
            key = str(path.resolve())
            with lock:
                if key in active:
                    continue
                active.add(key)
            def worker(p=path, k=key):
                try:
                    process_file(p)
                finally:
                    with lock:
                        active.discard(k)
            threading.Thread(target=worker, name=f"folder-{path.stem}", daemon=True).start()
        stop.wait(settings.folder_poll_ms / 1000.0)
