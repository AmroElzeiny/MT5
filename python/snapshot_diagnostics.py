#!/usr/bin/env python3
"""
Snapshot Diagnostics
---
Inspect the real MT5 Common/Files and terminal-local MQL5/Files paths used by the
PO3 snapshot pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable

DEFAULT_BUS_ROOT = "PO3_AI_BUS"


def terminal_root() -> Path:
    appdata = Path(os.getenv("APPDATA", "")).expanduser()
    return appdata / "MetaQuotes" / "Terminal"


def common_files_dir(root: Path) -> Path:
    return root / "Common" / "Files"


def terminal_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    dirs: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name.lower() == "common":
            continue
        if len(child.name) >= 20:
            dirs.append(child)
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def local_files_dir(term_dir: Path) -> Path:
    return term_dir / "MQL5" / "Files"


def resolve_terminal(root: Path, terminal_id: str | None) -> Path | None:
    dirs = terminal_dirs(root)
    if terminal_id:
        for d in dirs:
            if d.name.lower() == terminal_id.lower():
                return d
        return None
    return dirs[0] if dirs else None


def snapshot_dir(base: Path, bus_root: str) -> Path:
    return base / bus_root / "snapshots"


def recent_json_files(path: Path, limit: int = 5) -> Iterable[Path]:
    if not path.exists():
        return []
    return sorted(path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def print_snapshot_dir(label: str, path: Path) -> None:
    print(f"{label}: {path}")
    print(f"  exists: {path.exists()}")
    if path.exists():
        files = sorted(path.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"  readable: {os.access(path, os.R_OK)}")
        print(f"  writable: {os.access(path, os.W_OK)}")
        print(f"  png_count: {len(files)}")
        if files:
            latest = files[0]
            print(f"  latest: {latest.name} ({latest.stat().st_size} bytes)")
    print()


def print_recent_requests(common_files: Path, bus_root: str) -> None:
    req_dir = common_files / bus_root / "requests"
    print(f"Recent Requests: {req_dir}")
    if not req_dir.exists():
        print("  requests dir missing\n")
        return
    for req in recent_json_files(req_dir, limit=3):
        print(f"  {req.name}")
        try:
            data = json.loads(req.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"    unreadable: {exc}")
            continue
        snaps = data.get("snapshots", {})
        meta = data.get("snapshot_metadata", {})
        required_meta = ("symbol", "timeframe", "server_time", "candle_time", "bid", "ask", "setup_id", "po3_state", "fvg_lower", "fvg_upper")
        missing_meta = [key for key in required_meta if meta.get(key) in (None, "", 0)]
        print(f"    snapshot_metadata: {'ok' if not missing_meta else 'missing ' + ','.join(missing_meta)}")
        if meta:
            print(f"      symbol={meta.get('symbol')} tf={meta.get('timeframe')} setup_id={meta.get('setup_id')}")
            print(f"      po3_state={meta.get('po3_state')} fvg=[{meta.get('fvg_lower')},{meta.get('fvg_upper')}] bid/ask={meta.get('bid')}/{meta.get('ask')}")
        for key in ("htf_path", "ltf_path"):
            raw = snaps.get(key, "")
            exists = Path(raw).exists() if raw else False
            print(f"    {key}: {raw or 'N/A'}")
            print(f"      exists: {exists}")
    print()


def print_config_advice() -> None:
    print("Recommended Snapshot Inputs")
    print("  InpUseSnapshotAI=true")
    print("  InpRequireSnapshots=false for debugging, true only after capture is stable")
    print("  InpSnapshotDelayMs=500")
    print("  InpSnapshotReadyTimeoutMs=10000")
    print("  InpSnapshotChartReadyTimeoutMs=8000")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus-root", default=DEFAULT_BUS_ROOT)
    parser.add_argument("--terminal-id", default=os.getenv("MT5_TERMINAL_ID"))
    args = parser.parse_args()

    root = terminal_root()
    common_files = common_files_dir(root)
    term_dir = resolve_terminal(root, args.terminal_id)

    print("\n=== MT5 Snapshot Diagnostics ===\n")
    print(f"Terminal Root: {root}")
    print(f"Common Files: {common_files}")
    print()

    print_snapshot_dir("Common Snapshot Dir", snapshot_dir(common_files, args.bus_root))

    if term_dir is None:
        print("Local Terminal Dir: not found\n")
    else:
        print(f"Local Terminal Dir: {term_dir}\n")
        print_snapshot_dir("Local Snapshot Dir", snapshot_dir(local_files_dir(term_dir), args.bus_root))

    print_recent_requests(common_files, args.bus_root)
    print_config_advice()


if __name__ == "__main__":
    main()
