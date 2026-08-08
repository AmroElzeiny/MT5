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
HARD_TIMEOUT_SEC=180
CHAT_REGISTRY_FILE=state/chats.json
BROWSER_PROFILE_DIR=state/browser_profile
AUDIT_LOG_FILE=logs/audit.jsonl
SQLITE_FILE=state/bridge.sqlite3
AUTO_OPEN_BROWSER=true
DEFAULT_CHAT_ALIAS=po3
FOLDER_GATEWAY_ENABLED=true
FOLDER_REQUEST_DIR=folder_bus/requests
FOLDER_RESPONSE_DIR=folder_bus/responses
FOLDER_POLL_MS=250
MAX_RESPONSE_BYTES=2000000
REQUIRE_HUMAN_REVIEW=true
"""
    ENV_PATH.write_text(content, encoding="utf-8")


ensure_env()
load_dotenv(ENV_PATH, override=False)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("BRIDGE_HOST", "127.0.0.1")
    port: int = int(os.getenv("BRIDGE_PORT", "1234"))
    api_key: str = os.getenv("BRIDGE_API_KEY", "")
    max_pending_jobs: int = max(1, min(3, int(os.getenv("MAX_PENDING_JOBS", "3"))))
    hard_timeout_sec: int = max(30, min(180, int(os.getenv("HARD_TIMEOUT_SEC", "180"))))
    chat_registry_file: Path = ROOT / os.getenv("CHAT_REGISTRY_FILE", "state/chats.json")
    browser_profile_dir: Path = ROOT / os.getenv("BROWSER_PROFILE_DIR", "state/browser_profile")
    audit_log_file: Path = ROOT / os.getenv("AUDIT_LOG_FILE", "logs/audit.jsonl")
    sqlite_file: Path = ROOT / os.getenv("SQLITE_FILE", "state/bridge.sqlite3")
    auto_open_browser: bool = _bool("AUTO_OPEN_BROWSER", True)
    default_chat_alias: str = os.getenv("DEFAULT_CHAT_ALIAS", "po3").strip() or "po3"
    folder_gateway_enabled: bool = _bool("FOLDER_GATEWAY_ENABLED", True)
    folder_request_dir: Path = ROOT / os.getenv("FOLDER_REQUEST_DIR", "folder_bus/requests")
    folder_response_dir: Path = ROOT / os.getenv("FOLDER_RESPONSE_DIR", "folder_bus/responses")
    folder_poll_ms: int = max(100, int(os.getenv("FOLDER_POLL_MS", "250")))
    max_response_bytes: int = max(65536, int(os.getenv("MAX_RESPONSE_BYTES", "2000000")))
    require_human_review: bool = _bool("REQUIRE_HUMAN_REVIEW", True)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"


class PO3BusBoundaryError(RuntimeError):
    """The bridge was pointed at a PO3 file bus."""


# The PO3 gate is the only trusted writer of ``PO3_AI_BUS/responses``. The
# generic folder gateway is for *other* EAs and must never be able to reach that
# directory, not even by an operator typo or an absolute path in ``.env``
# (``ROOT / "<absolute>"`` silently yields the absolute path). This boundary is
# enforced structurally at import time rather than trusted to configuration.
PO3_BUS_MARKER = "PO3_AI_BUS"


def assert_not_po3_bus(*paths: Path) -> None:
    for path in paths:
        parts = {part.casefold() for part in Path(path).resolve().parts}
        if PO3_BUS_MARKER.casefold() in parts:
            raise PO3BusBoundaryError(
                "folder_gateway_must_not_target_po3_bus:"
                f"{path}. PO3 responses are written only by ai_gate.py after its "
                "own identity, candidate, and schema validation."
            )


settings = Settings()
assert_not_po3_bus(settings.folder_request_dir, settings.folder_response_dir)
for p in (
    settings.chat_registry_file.parent,
    settings.browser_profile_dir,
    settings.audit_log_file.parent,
    settings.sqlite_file.parent,
    settings.folder_request_dir,
    settings.folder_response_dir,
):
    p.mkdir(parents=True, exist_ok=True)
