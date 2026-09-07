from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from .config import settings

_lock = RLock()


def log_event(event: str, **fields: Any) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(row, ensure_ascii=False, sort_keys=True)
    with _lock:
        with settings.audit_log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
