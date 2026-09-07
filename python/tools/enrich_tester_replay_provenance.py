"""Add verified request-cost provenance to existing tester cache artifacts.

This is additive metadata only. No decision, price, candidate hash, signature or
request fingerprint changes. Use --apply after reviewing the dry-run report.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tester_replay_provenance import build_replay_provenance


def read(path):
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"))


def enrich(cache_dir: Path, request_root: Path, *, apply=False):
    artifacts = [(path, read(path)) for path in cache_dir.glob("*.json")]
    wanted = {str(data.get("id") or "") for _, data in artifacts}
    requests = defaultdict(list)
    for directory in ("completed", "rejected", "requests", "archive", "stale"):
        for path in (request_root / directory).rglob("*.json"):
            if path.name.startswith("response") or path.name.endswith(".meta.json"):
                continue
            if not any(key in path.name for key in wanted if key):
                continue
            try:
                data = read(path)
                if data.get("id") in wanted and isinstance(data.get("candidates"), list):
                    requests[data["id"]].append((path, data))
            except (OSError, ValueError):
                continue
    report = {"artifacts": len(artifacts), "enrichable": 0, "existing": 0, "missing": [], "applied": apply}
    for path, data in artifacts:
        if data.get("replay_provenance"):
            report["existing"] += 1
            continue
        provenance = None
        for request_path, request in requests.get(data.get("id"), []):
            try:
                provenance = build_replay_provenance(request, data)
                break
            except (KeyError, TypeError, ValueError):
                continue
        if provenance is None:
            report["missing"].append({"cache": path.name, "id": data.get("id"), "approved": bool(data.get("python_final_allow"))})
            continue
        report["enrichable"] += 1
        if apply:
            backup = cache_dir / "provenance_originals" / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(path, backup)
            enriched = dict(data, replay_provenance=provenance)
            assert {k: v for k, v in enriched.items() if k != "replay_provenance"} == data
            temporary = path.with_suffix(".provenance.tmp")
            temporary.write_text(json.dumps(enriched, ensure_ascii=True, separators=(",", ":")), encoding="utf-16")
            temporary.replace(path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--request-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = enrich(args.cache_dir, args.request_root, apply=args.apply)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({**result, "missing": len(result["missing"]), "missing_approved": sum(row["approved"] for row in result["missing"])}))
