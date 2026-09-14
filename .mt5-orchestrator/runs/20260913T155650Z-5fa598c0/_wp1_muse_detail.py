#!/usr/bin/env python3
"""WP1 ACC-001 strict-Muse detail scan (read-only, streaming).

Usage: python _wp1_muse_detail.py <ledger.ndjson>

Strict Muse = model == 'muse-spark-1.3-contributor'.
Also reports every provider_mode observed for those rows (to catch probes on a
different provider mode).
"""
import json
import sys
import collections

path = sys.argv[1]
rows = 0
per_day = collections.Counter()
per_day_tok = collections.defaultdict(lambda: [0, 0, 0, 0])
per_role = collections.Counter()
per_pmode = collections.Counter()
first = last = None
first_row = last_row = None
after = []
roles = collections.Counter()
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
        rows += 1
        ts = str(r.get("ts_utc", ""))
        per_day[ts[:10]] += 1
        t = per_day_tok[ts[:10]]
        t[0] += int(r.get("input_tokens") or 0)
        t[1] += int(r.get("cached_input_tokens") or 0)
        t[2] += int(r.get("output_tokens") or 0)
        t[3] += int(r.get("reasoning_output_tokens") or 0)
        pm = str(r.get("provider_mode", ""))
        per_pmode[pm] += 1
        role = str((r.get("extra") or {}).get("role", ""))
        roles[role] += 1
        if first is None or (ts and ts < first):
            first = ts
            first_row = r
        if last is None or (ts and ts > last):
            last = ts
            last_row = r
        if ts >= "2026-09-13T12:27":
            after.append(r)

print("file:", path)
print("strict muse rows:", rows)
print("first:", first)
print("  ", json.dumps({k: first_row.get(k) for k in
      ("request_id", "response_id", "provider_mode", "provider_id", "operation",
       "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")},
      ensure_ascii=False) if first_row else None)
print("last:", last)
print("  ", json.dumps({k: last_row.get(k) for k in
      ("request_id", "response_id", "provider_mode", "provider_id", "operation",
       "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")},
      ensure_ascii=False) if last_row else None)
print("provider_mode for muse rows:", dict(per_pmode))
print("roles:", dict(roles))
for d in sorted(per_day):
    t = per_day_tok[d]
    print(f"  {d} rows={per_day[d]:5d} in={t[0]:12d} cached={t[1]:10d} out={t[2]:11d} reason={t[3]:11d}")
print(f"rows with ts >= 2026-09-13T12:27: {len(after)}")
for r in after[:40]:
    print("  ", r.get("ts_utc"), r.get("operation"), r.get("extra"), r.get("output_tokens"), r.get("reasoning_output_tokens"))
