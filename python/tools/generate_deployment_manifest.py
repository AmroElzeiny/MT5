from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from architecture_contracts import DEPLOYMENT_MANIFEST_SCHEMA_VERSION, canonical_hash


GitRunner = Callable[[Path, list[str]], str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, args: list[str]) -> str:
    command = [
        "git",
        "-c",
        f"safe.directory={root.as_posix()}",
        "-C",
        str(root),
        *args,
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def build_manifest(source_root: Path, set_file: Path, *, git_runner: GitRunner = _git) -> dict[str, object]:
    source_root = source_root.resolve(strict=True)
    set_file = set_file.resolve(strict=True)
    if not set_file.is_file():
        raise ValueError(f"set_file_not_a_file:{set_file}")
    git_dir = git_runner(source_root, ["rev-parse", "--show-toplevel"])
    if Path(git_dir).resolve() != source_root:
        raise ValueError(f"source_root_must_be_git_toplevel:{git_dir}")
    git_commit = git_runner(source_root, ["rev-parse", "HEAD"])
    if len(git_commit) != 40 or any(char not in "0123456789abcdefABCDEF" for char in git_commit):
        raise ValueError("git_commit_invalid")
    dirty = bool(git_runner(source_root, ["status", "--porcelain", "--untracked-files=normal"]))
    manifest: dict[str, object] = {
        "schema_version": DEPLOYMENT_MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit.lower(),
        "dirty_tree_status": "DIRTY" if dirty else "CLEAN",
        "set_file_hash": _sha256(set_file),
        "set_file_path": str(set_file),
        "source_root": str(source_root),
        "identity_source": "explicit_git_toplevel_and_explicit_set_file",
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    return manifest


def atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the version-cohort deployment manifest consumed by the MT5 EA."
    )
    parser.add_argument("--source-root", required=True, type=Path, help="Exact Git top-level directory.")
    parser.add_argument("--set-file", required=True, type=Path, help="Exact .set file used for the run.")
    parser.add_argument("--output", required=True, type=Path, help="PO3_AI_BUS/config/deployment_manifest.json path.")
    args = parser.parse_args()
    try:
        manifest = build_manifest(args.source_root, args.set_file)
        atomic_write(args.output.resolve(), manifest)
    except Exception as exc:
        print(json.dumps({"written": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "written": True,
                "output": str(args.output.resolve()),
                "git_commit": manifest["git_commit"],
                "dirty_tree_status": manifest["dirty_tree_status"],
                "set_file_hash": manifest["set_file_hash"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
