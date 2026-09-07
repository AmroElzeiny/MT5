from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def ensure_env() -> None:
    if ENV_PATH.exists():
        return
    token = secrets.token_urlsafe(32)
    content = f"""BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=1234
BRIDGE_API_KEY={token}
MAX_PENDING_JOBS=3
HARD_TIMEOUT_SEC=1800
CHAT_REGISTRY_FILE=state/chats.json
BROWSER_PROFILE_DIR=state/browser_profile
AUDIT_LOG_FILE=logs/audit.jsonl
SQLITE_FILE=state/bridge.sqlite3
DEFAULT_CHAT_ALIAS=po3
FOLDER_GATEWAY_ENABLED=true
FOLDER_REQUEST_DIR=folder_bus/requests
FOLDER_RESPONSE_DIR=folder_bus/responses
FOLDER_POLL_MS=250
MAX_RESPONSE_BYTES=2000000

# Autonomous browser transport. Leave provider-specific selectors blank until
# you configure a web UI whose terms/license permit automated use.
BROWSER_AUTOMATION_ENABLED=true
BROWSER_HEADLESS=false
BROWSER_EXECUTABLE=
BROWSER_START_URL=
BROWSER_CHAT_TAB_COUNT=1
BROWSER_REQUESTS_PER_TAB=3
BROWSER_REQUEST_AFFINITY_GRACE_MS=5000
BROWSER_CHAT_NEW_URL=
BROWSER_CHAT_TAB_URLS=
BROWSER_RATE_LIMIT_TEXT_PATTERNS=Too many requests|making requests too quickly|temporarily limited access|wait a few minutes before trying again
BROWSER_CHAT_INPUT_SELECTOR=
BROWSER_SEND_SELECTOR=
BROWSER_ASSISTANT_MESSAGE_SELECTOR=
BROWSER_STOP_GENERATING_SELECTOR=
BROWSER_SCROLL_CONTAINER_SELECTOR=
BROWSER_FILE_INPUT_SELECTOR=
BROWSER_DOWNLOAD_LINK_SELECTOR=
BROWSER_LOGIN_READY_SELECTOR=
BROWSER_MODEL_CONTROL_SELECTOR=
BROWSER_MODEL_OPTION_SELECTOR_TEMPLATE=
BROWSER_EFFORT_CONTROL_SELECTOR=
BROWSER_EFFORT_OPTION_SELECTOR_TEMPLATE=
BROWSER_KEYBOARD_SEND=Enter
BROWSER_PROMPT_DELIVERY_MODE=text
BROWSER_RESPONSE_MODE=text
BROWSER_FILE_UPLOAD_THRESHOLD_CHARS=24000
BROWSER_RESPONSE_STABLE_MS=1800
BROWSER_POLL_MS=250
BROWSER_SELECTOR_TIMEOUT_MS=75000
BROWSER_NAVIGATION_TIMEOUT_MS=150000
BROWSER_DOWNLOAD_TIMEOUT_MS=75000
BROWSER_DOWNLOAD_DIR=state/downloads
BROWSER_UPLOAD_DIR=state/uploads
BROWSER_ALLOWED_DOWNLOAD_EXTENSIONS=.json,.txt,.md
BROWSER_FILE_PROMPT_INSTRUCTION=Read the attached request envelope and return exactly one JSON object satisfying response_schema. No markdown or prose.

# Optional browser fallback used only after an explicit primary-provider rate
# limit is visible. It has a separate browser profile so Google sign-in can be
# completed manually without exposing credentials to Playwright.
GEMINI_FALLBACK_ENABLED=false
GEMINI_PROFILE_DIR=state/gemini_browser_profile
GEMINI_START_URL=https://gemini.google.com/app
GEMINI_CHAT_NEW_URL=https://gemini.google.com/app
GEMINI_CHAT_INPUT_SELECTOR=.lm-input-redesign [contenteditable="true"]:not(.ql-clipboard),rich-textarea .ql-editor:not(.ql-clipboard),.ql-editor[contenteditable="true"]:not(.ql-clipboard),div[contenteditable="true"][role="textbox"]:not(.ql-clipboard)
GEMINI_SEND_SELECTOR=button[aria-label="Send message"],button[aria-label*="Send"],button[aria-label*="send"],button[aria-label*="إرسال"]
GEMINI_ASSISTANT_MESSAGE_SELECTOR=model-response message-content,model-response .model-response-text,message-content,.model-response-text
GEMINI_STOP_GENERATING_SELECTOR=button[aria-label*="Stop"]
GEMINI_FILE_INPUT_SELECTOR=input[type="file"]
GEMINI_UPLOAD_OPEN_SELECTOR=button[aria-label="Upload & tools"],button[aria-label*="Upload"],button[aria-label*="upload"],button[aria-label*="Add file"],button[aria-label*="Attach"],button[aria-label*="تحميل"],button[aria-label*="إرفاق"]
GEMINI_DOWNLOAD_SCOPE_SELECTOR=model-response,.model-response,.response-container
GEMINI_DOWNLOAD_OPEN_SELECTOR=.attachment-container
GEMINI_DOWNLOAD_LINK_SELECTOR=.drive-viewer-dark-button.drive-viewer-custom-button.goog-inline-block.drive-viewer-button.drive-viewer-custom-button-with-icon
GEMINI_MODEL_CONTROL_SELECTOR=button[aria-label^="Open mode picker"]
GEMINI_MODEL_OPTION_SELECTOR_TEMPLATE=[role="menuitem"]:has-text("{value}")
GEMINI_DESIRED_MODEL=3.1 Pro
GEMINI_LOGIN_READY_SELECTOR=.lm-input-redesign [contenteditable="true"]:not(.ql-clipboard),rich-textarea .ql-editor:not(.ql-clipboard),.ql-editor[contenteditable="true"]:not(.ql-clipboard),div[contenteditable="true"][role="textbox"]:not(.ql-clipboard)
GEMINI_PROMPT_DELIVERY_MODE=file
GEMINI_RESPONSE_MODE=download
GEMINI_FILE_PROMPT_INSTRUCTION=Read the attached authoritative request envelope and obey its instructions and response_schema exactly.
ALLOW_MANUAL_SUBMIT=false
"""
    ENV_PATH.write_text(content, encoding="utf-8")


ensure_env()
load_dotenv(ENV_PATH, override=False)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: str) -> Path:
    raw = os.getenv(name, default).strip() or default
    p = Path(raw).expanduser()
    return p if p.is_absolute() else ROOT / p


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(v.strip().lower() for v in raw.split(",") if v.strip())


def _pipe_list(name: str) -> tuple[str, ...]:
    """Read a pipe-separated list without lower-casing URL/path values."""
    return tuple(value.strip() for value in os.getenv(name, "").split("|") if value.strip())


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("BRIDGE_HOST", "127.0.0.1")
    port: int = int(os.getenv("BRIDGE_PORT", "1234"))
    api_key: str = os.getenv("BRIDGE_API_KEY", "")
    max_pending_jobs: int = max(1, min(3, int(os.getenv("MAX_PENDING_JOBS", "3"))))
    hard_timeout_sec: int = max(30, min(3600, int(os.getenv("HARD_TIMEOUT_SEC", "1800"))))
    chat_registry_file: Path = _path("CHAT_REGISTRY_FILE", "state/chats.json")
    browser_profile_dir: Path = _path("BROWSER_PROFILE_DIR", "state/browser_profile")
    audit_log_file: Path = _path("AUDIT_LOG_FILE", "logs/audit.jsonl")
    sqlite_file: Path = _path("SQLITE_FILE", "state/bridge.sqlite3")
    default_chat_alias: str = os.getenv("DEFAULT_CHAT_ALIAS", "po3").strip() or "po3"
    folder_gateway_enabled: bool = _bool("FOLDER_GATEWAY_ENABLED", True)
    folder_request_dir: Path = _path("FOLDER_REQUEST_DIR", "folder_bus/requests")
    folder_response_dir: Path = _path("FOLDER_RESPONSE_DIR", "folder_bus/responses")
    folder_poll_ms: int = max(100, int(os.getenv("FOLDER_POLL_MS", "250")))
    max_response_bytes: int = max(65536, int(os.getenv("MAX_RESPONSE_BYTES", "2000000")))

    browser_automation_enabled: bool = _bool("BROWSER_AUTOMATION_ENABLED", True)
    browser_headless: bool = _bool("BROWSER_HEADLESS", False)
    browser_executable: str = os.getenv("BROWSER_EXECUTABLE", "").strip()
    browser_start_url: str = os.getenv("BROWSER_START_URL", "").strip()
    # One independent lane is created per tab.  A lane never accepts a second
    # request while its current browser response is outstanding.
    browser_chat_tab_count: int = max(
        1, min(20, int(os.getenv("BROWSER_CHAT_TAB_COUNT", "1")))
    )
    browser_requests_per_tab: int = max(
        1,
        min(
            1000,
            int(
                os.getenv(
                    "BROWSER_REQUESTS_PER_TAB",
                    os.getenv("BROWSER_CHAT_TAB_COUNT", "1"),
                )
            ),
        ),
    )
    browser_request_affinity_grace_ms: int = max(
        250,
        min(30000, int(os.getenv("BROWSER_REQUEST_AFFINITY_GRACE_MS", "5000"))),
    )
    # Repeating a provider's new-chat URL creates one independent conversation
    # per tab after its first prompt.  Explicit URLs are useful when the chats
    # already exist; values are separated by "|".
    browser_chat_new_url: str = os.getenv("BROWSER_CHAT_NEW_URL", "").strip()
    browser_chat_tab_urls: tuple[str, ...] = _pipe_list("BROWSER_CHAT_TAB_URLS")
    browser_rate_limit_text_patterns: tuple[str, ...] = _pipe_list(
        "BROWSER_RATE_LIMIT_TEXT_PATTERNS"
    ) or (
        "Too many requests",
        "making requests too quickly",
        "temporarily limited access",
        "wait a few minutes before trying again",
    )
    chat_input_selector: str = os.getenv("BROWSER_CHAT_INPUT_SELECTOR", "").strip()
    send_selector: str = os.getenv("BROWSER_SEND_SELECTOR", "").strip()
    assistant_message_selector: str = os.getenv("BROWSER_ASSISTANT_MESSAGE_SELECTOR", "").strip()
    stop_generating_selector: str = os.getenv("BROWSER_STOP_GENERATING_SELECTOR", "").strip()
    scroll_container_selector: str = os.getenv("BROWSER_SCROLL_CONTAINER_SELECTOR", "").strip()
    file_input_selector: str = os.getenv("BROWSER_FILE_INPUT_SELECTOR", "").strip()
    download_link_selector: str = os.getenv("BROWSER_DOWNLOAD_LINK_SELECTOR", "").strip()
    # Some UIs need the produced file opened before its download control exists.
    # The open control is looked up inside the newest turn; the download control
    # that follows may live in a panel outside it.
    download_open_selector: str = os.getenv("BROWSER_DOWNLOAD_OPEN_SELECTOR", "").strip()
    download_scope_selector: str = os.getenv("BROWSER_DOWNLOAD_SCOPE_SELECTOR", "").strip()
    login_ready_selector: str = os.getenv("BROWSER_LOGIN_READY_SELECTOR", "").strip()
    model_control_selector: str = os.getenv("BROWSER_MODEL_CONTROL_SELECTOR", "").strip()
    model_option_selector_template: str = os.getenv("BROWSER_MODEL_OPTION_SELECTOR_TEMPLATE", "").strip()
    effort_control_selector: str = os.getenv("BROWSER_EFFORT_CONTROL_SELECTOR", "").strip()
    effort_option_selector_template: str = os.getenv("BROWSER_EFFORT_OPTION_SELECTOR_TEMPLATE", "").strip()
    keyboard_send: str = os.getenv("BROWSER_KEYBOARD_SEND", "Enter").strip() or "Enter"
    prompt_delivery_mode: str = os.getenv("BROWSER_PROMPT_DELIVERY_MODE", "text").strip().lower()
    response_mode: str = os.getenv("BROWSER_RESPONSE_MODE", "text").strip().lower()
    file_upload_threshold_chars: int = max(1000, int(os.getenv("BROWSER_FILE_UPLOAD_THRESHOLD_CHARS", "24000")))
    response_stable_ms: int = max(500, int(os.getenv("BROWSER_RESPONSE_STABLE_MS", "1800")))
    # A valid JSON object can still carry the previous role's schema when one
    # browser conversation is reused for analyst/critic/adjudicator turns.  One
    # tightly-bound correction turn is allowed; every returned object remains
    # subject to the original strict schema and deadline.
    schema_repair_max_attempts: int = max(
        0, min(1, int(os.getenv("BROWSER_SCHEMA_REPAIR_MAX_ATTEMPTS", "1")))
    )
    browser_poll_ms: int = max(100, int(os.getenv("BROWSER_POLL_MS", "250")))
    selector_timeout_ms: int = max(1000, int(os.getenv("BROWSER_SELECTOR_TIMEOUT_MS", "75000")))
    navigation_timeout_ms: int = max(3000, int(os.getenv("BROWSER_NAVIGATION_TIMEOUT_MS", "150000")))
    download_timeout_ms: int = max(1000, int(os.getenv("BROWSER_DOWNLOAD_TIMEOUT_MS", "75000")))
    download_dir: Path = _path("BROWSER_DOWNLOAD_DIR", "state/downloads")
    upload_dir: Path = _path("BROWSER_UPLOAD_DIR", "state/uploads")
    allowed_download_extensions: tuple[str, ...] = _csv("BROWSER_ALLOWED_DOWNLOAD_EXTENSIONS", ".json,.txt,.md")
    # Reject a downloaded file whose name does not carry this turn's nonce.
    # Defaults on: an unverified download can bind an earlier answer to a new
    # request, which is a wrong trade rather than a failed one.
    require_response_filename: bool = _bool("BROWSER_REQUIRE_RESPONSE_FILENAME", True)
    file_prompt_instruction: str = os.getenv(
        "BROWSER_FILE_PROMPT_INSTRUCTION",
        "The attached JSON file is the authoritative request envelope: read it and obey its "
        "instructions and response_schema exactly. YOU MUST ANSWER WITH A DOWNLOADABLE FILE. "
        "Create a real file named response.json whose entire content is exactly one JSON object "
        "satisfying response_schema, and attach it to your reply so the message shows a working "
        "Download control. Do not paste the JSON into the chat, do not wrap it in a code fence, "
        "and do not add any commentary before or after it. An answer typed into the chat cannot "
        "be read by the receiver and fails the request; only the attached .json file counts.",
    ).strip()
    gemini_fallback_enabled: bool = _bool("GEMINI_FALLBACK_ENABLED", False)
    gemini_profile_dir: Path = _path("GEMINI_PROFILE_DIR", "state/gemini_browser_profile")
    gemini_start_url: str = os.getenv(
        "GEMINI_START_URL", "https://gemini.google.com/app"
    ).strip()
    gemini_chat_new_url: str = os.getenv(
        "GEMINI_CHAT_NEW_URL", "https://gemini.google.com/app"
    ).strip()
    gemini_chat_input_selector: str = os.getenv(
        "GEMINI_CHAT_INPUT_SELECTOR",
        '.lm-input-redesign [contenteditable="true"]:not(.ql-clipboard),'
        'rich-textarea .ql-editor:not(.ql-clipboard),'
        '.ql-editor[contenteditable="true"]:not(.ql-clipboard),'
        'div[contenteditable="true"][role="textbox"]:not(.ql-clipboard)',
    ).strip()
    gemini_send_selector: str = os.getenv(
        "GEMINI_SEND_SELECTOR",
        'button[aria-label="Send message"],button[aria-label*="Send"],'
        'button[aria-label*="send"],button[aria-label*="إرسال"]',
    ).strip()
    gemini_assistant_message_selector: str = os.getenv(
        "GEMINI_ASSISTANT_MESSAGE_SELECTOR",
        "model-response message-content,model-response .model-response-text,"
        "message-content,.model-response-text",
    ).strip()
    gemini_stop_generating_selector: str = os.getenv(
        "GEMINI_STOP_GENERATING_SELECTOR", 'button[aria-label*="Stop"]'
    ).strip()
    gemini_file_input_selector: str = os.getenv(
        "GEMINI_FILE_INPUT_SELECTOR", 'input[type="file"]'
    ).strip()
    gemini_upload_open_selector: str = os.getenv(
        "GEMINI_UPLOAD_OPEN_SELECTOR",
        'button[aria-label="Upload & tools"],button[aria-label*="Upload"],'
        'button[aria-label*="upload"],'
        'button[aria-label*="Add file"],button[aria-label*="Attach"],'
        'button[aria-label*="تحميل"],button[aria-label*="إرفاق"]',
    ).strip()
    gemini_download_scope_selector: str = os.getenv(
        "GEMINI_DOWNLOAD_SCOPE_SELECTOR",
        "model-response,.model-response,.response-container",
    ).strip()
    gemini_download_open_selector: str = os.getenv(
        "GEMINI_DOWNLOAD_OPEN_SELECTOR", ".attachment-container"
    ).strip()
    gemini_download_link_selector: str = os.getenv(
        "GEMINI_DOWNLOAD_LINK_SELECTOR",
        '.drive-viewer-dark-button.drive-viewer-custom-button.goog-inline-block.'
        'drive-viewer-button.drive-viewer-custom-button-with-icon',
    ).strip()
    gemini_model_control_selector: str = os.getenv(
        "GEMINI_MODEL_CONTROL_SELECTOR",
        'button[aria-label^="Open mode picker"]',
    ).strip()
    gemini_model_option_selector_template: str = os.getenv(
        "GEMINI_MODEL_OPTION_SELECTOR_TEMPLATE",
        '[role="menuitem"]:has-text("{value}")',
    ).strip()
    gemini_desired_model: str = os.getenv(
        "GEMINI_DESIRED_MODEL", "3.1 Pro"
    ).strip()
    gemini_login_ready_selector: str = os.getenv(
        "GEMINI_LOGIN_READY_SELECTOR",
        '.lm-input-redesign [contenteditable="true"]:not(.ql-clipboard),'
        'rich-textarea .ql-editor:not(.ql-clipboard),'
        '.ql-editor[contenteditable="true"]:not(.ql-clipboard),'
        'div[contenteditable="true"][role="textbox"]:not(.ql-clipboard)',
    ).strip()
    gemini_prompt_delivery_mode: str = os.getenv(
        "GEMINI_PROMPT_DELIVERY_MODE", "file"
    ).strip().lower()
    gemini_response_mode: str = os.getenv(
        "GEMINI_RESPONSE_MODE", "download"
    ).strip().lower()
    gemini_file_prompt_instruction: str = os.getenv(
        "GEMINI_FILE_PROMPT_INSTRUCTION",
        "Read the attached authoritative request envelope and obey its instructions and "
        "response_schema exactly.",
    ).strip()
    allow_manual_submit: bool = _bool("ALLOW_MANUAL_SUBMIT", False)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    @property
    def browser_configured(self) -> bool:
        return bool(self.chat_input_selector and self.assistant_message_selector)

    @property
    def gemini_configured(self) -> bool:
        return bool(
            self.gemini_chat_new_url
            and self.gemini_chat_input_selector
            and self.gemini_assistant_message_selector
        )


class PO3BusBoundaryError(RuntimeError):
    pass


PO3_BUS_MARKER = "PO3_AI_BUS"


def assert_not_po3_bus(*paths: Path) -> None:
    for path in paths:
        parts = {part.casefold() for part in Path(path).resolve().parts}
        if PO3_BUS_MARKER.casefold() in parts:
            raise PO3BusBoundaryError(
                "folder_gateway_must_not_target_po3_bus:"
                f"{path}. PO3 responses remain owned only by ai_gate.py."
            )


settings = Settings()
assert_not_po3_bus(settings.folder_request_dir, settings.folder_response_dir)
for p in (
    settings.chat_registry_file.parent,
    settings.browser_profile_dir,
    settings.gemini_profile_dir,
    settings.audit_log_file.parent,
    settings.sqlite_file.parent,
    settings.folder_request_dir,
    settings.folder_response_dir,
    settings.download_dir,
    settings.upload_dir,
):
    p.mkdir(parents=True, exist_ok=True)
