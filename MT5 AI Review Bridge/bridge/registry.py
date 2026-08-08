from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from .config import settings
from .models import ChatProfile

_lock = RLock()


def load_profiles() -> list[ChatProfile]:
    with _lock:
        path = settings.chat_registry_file
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("chats", []) if isinstance(data, dict) else []
        out: list[ChatProfile] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(ChatProfile(
                alias=str(row.get("alias", "")).strip(),
                model_id=str(row.get("model_id", "")).strip(),
                conversation_url=str(row.get("conversation_url", "")).strip(),
                desired_model=str(row.get("desired_model", "")).strip(),
                desired_effort=str(row.get("desired_effort", "")).strip(),
                enabled=bool(row.get("enabled", True)),
            ))
        return [p for p in out if p.alias and p.model_id and p.conversation_url]


def save_profiles(profiles: list[ChatProfile]) -> None:
    with _lock:
        payload = {"schema_version": 1, "chats": [p.to_dict() for p in profiles]}
        tmp = settings.chat_registry_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(settings.chat_registry_file)


def profile_by_model(model_id: str) -> ChatProfile | None:
    for p in load_profiles():
        if p.enabled and p.model_id == model_id:
            return p
    return None


def profile_by_alias(alias: str) -> ChatProfile | None:
    for p in load_profiles():
        if p.enabled and p.alias == alias:
            return p
    return None
