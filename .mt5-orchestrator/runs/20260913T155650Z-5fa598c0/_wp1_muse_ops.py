#!/usr/bin/env python3
"""WP1 ACC-001: Muse per-operation timing + late-result boundary from the ledger."""
import json
import sys
import collections
import datetime

path = sys.argv[1]
ops = collections.defaultdict(list)
after = []
with open(path, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("model", "")) != "muse-spark-1.3-contributor":
            continue
        ts = str(r.get("ts_utc", ""))
        ops[str(r.get("operation", ""))].append(ts)
        if ts >= "2026-09-13T12:28":
            after.append(ts)
print("per-operation Muse rows: count, first, last")
for k in sorted(ops):
    v = ops[k]
    print(f"  {len(v):6d}  {min(v)} -> {max(v)}  {k}")
print("post-2026-09-13T12:28 Muse success rows:", len(after))
print("  first:", min(after) if after else None, "last:", max(after) if after else None)
# last 09-11 and first 09-13
d11 = [t for v in ops.values() for t in v if t[:10] == "2026-09-11"]
d13 = [t for v in ops.values() for t in v if t[:10] == "2026-09-13"]
d09 = [t for v in ops.values() for t in v if t[:10] == "2026-09-09"]
print("09-11 first/last success:", min(d11) if d11 else None, max(d11) if d11 else None)
print("09-13 first/last success:", min(d13) if d13 else None, max(d13) if d13 else None)
print("09-09 first/last success:", min(d09) if d09 else None, max(d09) if d09 else None)
