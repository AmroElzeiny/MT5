from __future__ import annotations

import json
import re
from typing import Any

from .config import settings

RESPONSE_FILENAME_PREFIX = "response-"
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")


def response_filename_token(job_id: str) -> str:
    """Short per-turn nonce embedded in the response filename."""

    return _NON_ALNUM.sub("", str(job_id)).lower()[:8] or "unknown"


def response_filename(job_id: str) -> str:
    """The exact download name the model is ordered to use for this turn.

    One conversation serves the analyst, critic and adjudicator turns of many
    requests, so every produced file otherwise looks alike.  Naming each answer
    after its own job makes a download self-identifying: the worker can prove
    the artifact it just pulled belongs to the turn it just sent, instead of
    trusting that the newest download control is the newest answer.
    """

    return f"{RESPONSE_FILENAME_PREFIX}{response_filename_token(job_id)}.json"


def filename_matches_job(suggested: str, job_id: str) -> bool:
    """True when a downloaded filename carries this job's nonce.

    Deliberately tolerant: chat UIs append " (1)", change case, or re-encode
    separators.  Only the nonce has to survive.
    """

    return response_filename_token(job_id) in _NON_ALNUM.sub("", str(suggested)).lower()

# One source of truth for how the answer must be delivered.
#
# The transport decides this, not the analyst prompt: in ``download`` mode
# ``BrowserWorker._wait_response`` never reads the chat text, it clicks the
# download control on the newest turn.  A reply typed into the chat therefore
# produces no artifact at all and the job fails.  Every place that talks to the
# model about delivery renders this same text so the model is never given two
# contradictory orders.
def _delivery_download(filename: str) -> str:
    return (
        "OUTPUT DELIVERY - MANDATORY, MACHINE-CHECKED, NOT A STYLE PREFERENCE.\n"
        "Deliver the answer as a DOWNLOADABLE FILE, never as chat text.\n"
        f"- The file MUST be named exactly: {filename}\n"
        "- Copy that name character for character. Do not shorten it, do not translate it,\n"
        "  do not replace it with response.json, output.json, a date, a description or any\n"
        "  name of your own. The name is how the receiver proves this file is the answer to\n"
        "  THIS message and not a file from an earlier message in this conversation.\n"
        "- A file delivered under any other name is treated as a stale answer and rejected,\n"
        "  even if its contents are perfect.\n"
        f"- {filename} must contain the single JSON object as its entire content.\n"
        "- Attach it to your reply so the message carries a working Download control.\n"
        "- Use your file-creation capability to produce a genuine downloadable .json artifact.\n"
        "Do NOT paste the JSON into the chat body. Do NOT wrap it in a code fence. Do NOT\n"
        "summarise, introduce, explain or comment on it. Do NOT offer it as copyable text.\n"
        "Do NOT re-attach, re-send or link a file you produced for an earlier message.\n"
        "An answer typed into the chat instead of attached as a downloadable file cannot be\n"
        "read by the receiver: it is discarded and the whole request fails. Attaching the\n"
        "file under the exact name above is the only way this reply counts. If you are about\n"
        "to answer inline, stop and write the file instead.\n"
        "The file content itself must be exactly one JSON object and nothing else: no\n"
        "markdown, no fences, no leading or trailing text, no duplicate keys."
    )
_DELIVERY_TEXT = (
    "OUTPUT DELIVERY. Return ONE JSON object only, as the entire message body.\n"
    "No markdown fences, no commentary before or after it, and do not attach a file."
)
def _delivery_auto(filename: str) -> str:
    return (
        "OUTPUT DELIVERY. Prefer attaching the answer as a downloadable file named\n"
        f"exactly {filename}, whose entire content is the single JSON object. Copy that\n"
        "name character for character. If you genuinely cannot attach a file, return that\n"
        "same JSON object alone as the entire message body instead. Either way: no markdown\n"
        "fences and no commentary before or after."
    )


def delivery_instruction(mode: str | None = None, filename: str | None = None) -> str:
    """The delivery contract for the active browser response mode.

    ``filename`` is the exact per-turn download name.  It is omitted only where
    the job id is not known yet; the worker restates the contract with the real
    name before the message is sent.
    """

    resolved = (mode or settings.response_mode or "text").strip().lower()
    name = filename or "response.json"
    if resolved == "download":
        return _delivery_download(name)
    if resolved == "auto":
        return _delivery_auto(name)
    return _DELIVERY_TEXT


def delivery_reminder(mode: str | None = None, filename: str | None = None) -> str:
    """One-line delivery restatement for the tail of a prompt.

    The full block is stated once near the top.  Repeating it verbatim at the
    end pushed the schema and role material out of the most recent position and
    the model started re-attaching its previous answer, so the tail stays short
    and spends its recency on what changes per request instead.
    """

    resolved = (mode or settings.response_mode or "text").strip().lower()
    name = filename or "response.json"
    if resolved == "download":
        return f"Deliver it as an attached downloadable file named exactly {name}, not as chat text."
    if resolved == "auto":
        return f"Deliver it as an attached file named exactly {name} if you can, otherwise inline."
    return "Deliver it inline as the entire message body."


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
    for part in user_parts:
        try:
            obj = json.loads(part)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        identity = obj.get("identity")
        for value in (
            obj.get("request_id"),
            obj.get("id"),
            identity.get("request_id") if isinstance(identity, dict) else None,
        ):
            text = str(value or "").strip()
            if text:
                return text
    return ""


def build_automation_prompt(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str]:
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
    # The reply is validated against this schema before it is returned to the
    # caller. A chat UI has no server-side constrained decoding, so the schema
    # has to be stated in the prompt or the model is judged against rules it
    # was never shown.
    if schema:
        schema_block = "\n".join([
            "RESPONSE SCHEMA (JSON Schema 2020-12). Your reply is machine-validated",
            "against this exact schema and discarded on any deviation:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "Schema rules that are enforced literally:",
            "- Emit every property listed in each `required` array, including empty",
            "  strings and empty arrays. A missing required property is a failure.",
            "- Emit no property that the schema does not define. Do not rename,",
            "  abbreviate or pluralise a property name; copy it character for character.",
            "- Respect every maxLength, minLength, minimum, maximum and enum.",
            "  Counts are characters, so keep bounded prose comfortably under its limit",
            "  rather than at it.",
        ])
    else:
        schema_block = "No schema was supplied. Return one JSON object."
    prompt = "\n\n".join([
        "You are the structured decision model for an external validated system.",
        delivery_instruction(),
        f"BRIDGE REQUEST ID: {request_id or 'unknown'}",
        f"Requested schema name: {schema_name}",
        schema_block,
        "SYSTEM INSTRUCTIONS:\n" + "\n\n".join(system_parts),
        "REQUEST / EVIDENCE:\n" + "\n\n".join(user_parts),
        "The receiver rejects duplicate keys, non-finite numbers, trailing text, or JSON that fails its contract.",
        # The tail owns the model's strongest recency, so it is spent on what
        # varies per request: this conversation is reused across the analyst,
        # critic and adjudicator roles, and each expects a different schema.
        # Without this the model re-attaches the previous role's answer.
        "\n".join([
            "FINAL REMINDER - answer THIS message only.",
            f"Produce a NEW answer matching the schema named `{schema_name}` above, for"
            f" BRIDGE REQUEST ID {request_id or 'unknown'}.",
            "Earlier messages in this conversation asked for different schemas. Do not reuse,"
            " repeat, copy or re-attach any answer or file you produced earlier: a response"
            " whose shape belongs to a previous request is discarded.",
            delivery_reminder(),
        ]),
    ])
    return prompt, schema, request_id


# Compatibility alias for older callers/tests.
build_manual_prompt = build_automation_prompt
