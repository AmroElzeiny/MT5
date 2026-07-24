from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def peek_dotenv_value(path: str | Path | None, key_name: str) -> str | None:
    """Read one non-secret selector without importing the full environment."""

    env_path = Path(path) if path else Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == key_name:
            return _strip_quotes(value)
    return None


def load_dotenv(
    path: str | Path | None = None,
    *,
    override: bool = False,
    exclude_keys: Iterable[str] = (),
) -> Path:
    env_path = Path(path) if path else Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return env_path
    excluded = {str(key).strip() for key in exclude_keys if str(key).strip()}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in excluded:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value)
    if "OPENAI_BASE_URL" not in excluded and not os.environ.get("OPENAI_BASE_URL", "").strip():
        os.environ.pop("OPENAI_BASE_URL", None)
    return env_path
