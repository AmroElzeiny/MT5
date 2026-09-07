from __future__ import annotations
import re
from typing import Any

_PATTERNS = [
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|authorization)\b\s*[:=]\s*['\"]?[^\s,'\"}]+"), r"\1=[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\b(?:[a-zA-Z]+\s+){11,23}[a-zA-Z]+\b"), "[REDACTED_POSSIBLE_SEED_PHRASE]"),
]

def redact_text(text: str) -> str:
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out

def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return [redact(v) for v in value]
    return value
