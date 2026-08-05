"""Hash manifest for MQL production sources that live outside the git root.

The git repository root is the ``python/`` directory; the MQL5 sources sit in
sibling directories and are therefore invisible to ``git status``, ``git diff``,
and code review.  That is a real review gap: an edit to a 13,000-line
``TradeEngine.mqh`` leaves no trace in version control.

This tool produces a deterministic manifest -- SHA-256, size, line count, and
line-ending style for every MQL source, plus the deployed terminal copy -- so a
change can be detected and described even without git.  It also flags drift
between the repository copy and the copy the terminal actually compiles, which
has already caused one incident in this project (the repo held a stale build
while the terminal ran a newer one).

Usage
-----
    python tools/mql_source_manifest.py                 # print manifest
    python tools/mql_source_manifest.py --save out.json # write manifest
    python tools/mql_source_manifest.py --compare old.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MANIFEST_VERSION = "20260731_mql_source_manifest_v1"

REPO_ROOT = Path(__file__).resolve().parent.parent
MQL_TREES = {
    "include": REPO_ROOT.parent / "MT5_PO3_Codex Include",
    "experts": REPO_ROOT.parent / "MT5_PO3_Codex Experts",
}
TERMINAL_ID = "0148BD5691B65B0F2157627A4231F3DE"


def terminal_trees() -> dict[str, Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return {}
    base = Path(appdata) / "MetaQuotes" / "Terminal" / TERMINAL_ID / "MQL5"
    return {
        "include": base / "Include" / "MT5_PO3_Codex",
        "experts": base / "Experts" / "MT5_PO3_Codex",
    }


@dataclass(frozen=True)
class FileRecord:
    tree: str
    name: str
    sha256: str
    bytes: int
    lines: int
    line_endings: str

    @property
    def key(self) -> str:
        return f"{self.tree}/{self.name}"


def _line_endings(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return f"mixed(crlf={crlf},lf={lf})"
    if crlf:
        return "crlf"
    if lf:
        return "lf"
    return "none"


def scan(trees: dict[str, Path], *, suffixes=(".mqh", ".mq5")) -> list[FileRecord]:
    records: list[FileRecord] = []
    for tree, path in trees.items():
        if not path.is_dir():
            continue
        for f in sorted(path.iterdir()):
            if not f.is_file() or f.suffix.lower() not in suffixes:
                continue
            data = f.read_bytes()
            records.append(
                FileRecord(
                    tree=tree,
                    name=f.name,
                    sha256=hashlib.sha256(data).hexdigest(),
                    bytes=len(data),
                    lines=data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0),
                    line_endings=_line_endings(data),
                )
            )
    return records


def build_manifest() -> dict:
    repo = scan(MQL_TREES)
    term = scan(terminal_trees())
    term_by_key = {r.key: r for r in term}

    drift = []
    for record in repo:
        deployed = term_by_key.get(record.key)
        if deployed is None:
            drift.append({"file": record.key, "state": "not_deployed"})
        elif deployed.sha256 != record.sha256:
            drift.append(
                {
                    "file": record.key,
                    "state": "repo_differs_from_terminal",
                    "repo_sha256": record.sha256,
                    "terminal_sha256": deployed.sha256,
                    "repo_bytes": record.bytes,
                    "terminal_bytes": deployed.bytes,
                }
            )

    repo_keys = {r.key for r in repo}
    for record in term:
        if record.key not in repo_keys:
            drift.append({"file": record.key, "state": "terminal_only"})

    return {
        "manifest_version": MANIFEST_VERSION,
        "repo_root": str(REPO_ROOT.parent),
        "terminal_id": TERMINAL_ID,
        "repo_files": [asdict(r) for r in repo],
        "terminal_files": [asdict(r) for r in term],
        "drift": drift,
        "totals": {
            "repo_files": len(repo),
            "terminal_files": len(term),
            "drift_entries": len(drift),
        },
    }


def compare(previous: dict, current: dict) -> list[dict]:
    old = {f"{r['tree']}/{r['name']}": r for r in previous.get("repo_files", [])}
    new = {f"{r['tree']}/{r['name']}": r for r in current.get("repo_files", [])}
    changes: list[dict] = []

    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before is None:
            changes.append({"file": key, "change": "added", "sha256_after": after["sha256"]})
        elif after is None:
            changes.append({"file": key, "change": "removed", "sha256_before": before["sha256"]})
        elif before["sha256"] != after["sha256"]:
            changes.append(
                {
                    "file": key,
                    "change": "modified",
                    "sha256_before": before["sha256"],
                    "sha256_after": after["sha256"],
                    "bytes_before": before["bytes"],
                    "bytes_after": after["bytes"],
                    "lines_before": before["lines"],
                    "lines_after": after["lines"],
                    "line_endings_before": before["line_endings"],
                    "line_endings_after": after["line_endings"],
                }
            )
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", default="")
    parser.add_argument("--compare", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    manifest = build_manifest()

    if args.compare:
        previous = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        changes = compare(previous, manifest)
        print(json.dumps({"changes": changes}, indent=2))
        return 0

    if args.save:
        Path(args.save).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"wrote {args.save}")

    if args.json:
        print(json.dumps(manifest, indent=2))
        return 0

    print(f"{MANIFEST_VERSION}")
    print(f"{'file':<46}{'sha256':<18}{'bytes':>10}{'lines':>8}  endings")
    for record in manifest["repo_files"]:
        print(
            f"{record['tree'] + '/' + record['name']:<46}"
            f"{record['sha256'][:16]:<18}{record['bytes']:>10,}{record['lines']:>8}"
            f"  {record['line_endings']}"
        )
    print()
    print(f"drift entries: {len(manifest['drift'])}")
    for entry in manifest["drift"]:
        print(f"  {entry['file']:<46} {entry['state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
