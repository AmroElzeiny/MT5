from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import settings
from .jsonutil import strict_json_object

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*)\n```\s*$", re.IGNORECASE | re.DOTALL)


def extract_json_object_text(text: str) -> tuple[dict[str, Any], str]:
    """Accept one strict JSON object, optionally wrapped by one whole code fence.

    We intentionally do not search arbitrary prose for the first pair of braces;
    doing so can silently bind the wrong object when a UI adds analysis text.
    """
    try:
        obj = strict_json_object(text)
        return obj, json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception as direct_exc:
        match = _FENCE.match(text or "")
        if not match:
            raise direct_exc
        obj = strict_json_object(match.group("body"))
        return obj, json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def read_response_file(path: Path) -> tuple[dict[str, Any], str]:
    resolved = path.resolve()
    try:
        resolved.relative_to(settings.download_dir.resolve())
    except ValueError as exc:
        raise ValueError("download_outside_controlled_directory") from exc
    ext = resolved.suffix.lower()
    if ext not in settings.allowed_download_extensions:
        raise ValueError(f"download_extension_not_allowed:{ext}")
    size = resolved.stat().st_size
    if size > settings.max_response_bytes:
        raise ValueError(f"download_too_large:{size}")
    text = resolved.read_text(encoding="utf-8-sig")
    return extract_json_object_text(text)
