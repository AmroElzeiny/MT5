from __future__ import annotations

import json
from typing import Any


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def extract_request_id(user_parts: list[str]) -> str:
    """Read the caller's request id out of the user message bodies.

    PO3's analyst evidence carries the id under ``identity.request_id`` (the
    canonical decision-evidence envelope), while its critic/adjudicator role
    evidence and the non-trading capability probe carry a top-level
    ``request_id``.  Reading only the top level labelled every real analyst job
    with a random uuid, which broke queue de-duplication and made the dashboard
    impossible to correlate with a PO3 request.

    The bridge only *labels* jobs with this value.  PO3 remains the sole
    authority for request identity and binds it independently.
    """

    for part in user_parts:
        try:
            obj = json.loads(part)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        identity = obj.get("identity")
        candidates = (
            obj.get("request_id"),
            obj.get("id"),
            identity.get("request_id") if isinstance(identity, dict) else None,
        )
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def build_manual_prompt(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str]:
    messages = payload.get("messages") or []
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", ""))
        text = message_text(msg.get("content"))
        if role == "system":
            system_parts.append(text)
        elif role == "user":
            user_parts.append(text)
    response_format = payload.get("response_format") or {}
    schema = None
    schema_name = "structured_response"
    if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
        js = response_format.get("json_schema") or {}
        if isinstance(js, dict):
            schema = js.get("schema") if isinstance(js.get("schema"), dict) else None
            schema_name = str(js.get("name") or schema_name)
    request_id = extract_request_id(user_parts)
    prompt = "\n\n".join([
        "You are the structured decision model for an external validated system.",
        "Return ONE JSON object only. No markdown fences, no commentary before or after it.",
        f"Requested schema name: {schema_name}",
        "SYSTEM INSTRUCTIONS:\n" + "\n\n".join(system_parts),
        "REQUEST / EVIDENCE:\n" + "\n\n".join(user_parts),
        "The receiving program will reject duplicate keys, non-finite numbers, trailing text, or any JSON that fails the supplied contract.",
    ])
    return prompt, schema, request_id
