from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_float(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def nested_get(value: Any, *paths: str) -> Any:
    for path in paths:
        current = value
        ok = True
        for token in path.split("."):
            if isinstance(current, Mapping) and token in current:
                current = current[token]
            else:
                ok = False
                break
        if ok and current not in (None, ""):
            return current
    return None


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return json.loads(raw.decode("utf-16"))
    if raw.startswith(b"\xef\xbb\xbf"):
        return json.loads(raw.decode("utf-8-sig"))
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(raw.decode("utf-16"))


def strict_json_object(text: str) -> dict[str, Any]:
    duplicates: list[str] = []
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in items:
            if k in out:
                duplicates.append(k)
            out[k] = v
        return out
    decoder = json.JSONDecoder(object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"non_finite:{x}")))
    stripped = text.strip()
    value, end = decoder.raw_decode(stripped)
    if stripped[end:].strip():
        raise ValueError("trailing_json_content")
    if duplicates:
        raise ValueError("duplicate_json_keys:" + ",".join(sorted(set(duplicates))))
    if not isinstance(value, dict):
        raise ValueError("json_root_must_be_object")
    return value


def git_identity(repo_root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=True, timeout=5).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except Exception:
        return {"commit": "unknown", "dirty": True}


def runtime_identity() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
    }


def tokenize_query(text: str) -> list[str]:
    values = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[A-Z][A-Z0-9_]{2,}", text or "")
    stop = {"the", "and", "for", "with", "why", "does", "what", "from", "this", "that", "trade", "trading", "system", "research"}
    out: list[str] = []
    for value in values:
        if value.lower() in stop:
            continue
        if value not in out:
            out.append(value)
    return out[:30]


def bounded(items: Iterable[Any], limit: int) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if len(result) >= max(0, limit):
            break
        result.append(item)
    return result
