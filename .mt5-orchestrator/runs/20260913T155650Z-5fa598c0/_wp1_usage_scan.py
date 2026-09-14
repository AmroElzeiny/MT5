#!/usr/bin/env python3
"""WP1 ACC-001 streaming scanner for openai_usage.ndjson (read-only).

Usage:
  python _wp1_usage_scan.py <ledger.ndjson>

Prints:
  - per model counts and per (model, provider_mode) counts
  - Muse identification: model contains "muse" OR provider_mode contains "OPENCODE"
  - first/last Muse row ts_utc + full row
  - per-day Muse row count and token sums
  - token-weighted totals for rows in 2026-08-31..2026-09-14
No line is retained; the file is streamed.
"""
import json
import sys
import collections

if len(sys.argv) < 2:
    print("usage: _wp1_usage_scan.py <ledger.ndjson>", file=sys.stderr)
    sys.exit(2)

path = sys.argv[1]
models = collections.Counter()
combo = collections.Counter()
pmode = collections.Counter()
days = collections.Counter()
day_tok = collections.defaultdict(lambda: [0, 0, 0, 0, 0])  # rows, in, cached, out, reason
n = 0
bad = 0
muse_first = muse_last = None
muse_first_row = muse_last_row = None
oc_first = oc_last = None
for_window = [0, 0, 0, 0, 0]

with open(path, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        n += 1
        try:
            r = json.loads(line)
        except Exception:
            bad += 1
            continue
        m = str(r.get("model", ""))
        pm = str(r.get("provider_mode", ""))
        ts = str(r.get("ts_utc", ""))
        models[m] += 1
        combo[(m, pm)] += 1
        if pm:
            pmode[pm] += 1
        is_muse = ("muse" in m.lower()) or ("OPENCODE" in pm.upper())
        if is_muse:
            day = ts[:10]
            days[day] += 1
            t = day_tok[day]
            t[0] += 1
            t[1] += int(r.get("input_tokens") or 0)
            t[2] += int(r.get("cached_input_tokens") or 0)
            t[3] += int(r.get("output_tokens") or 0)
            t[4] += int(r.get("reasoning_output_tokens") or 0)
            if muse_first is None or (ts and ts < muse_first):
                muse_first = ts
                muse_first_row = r
            if muse_last is None or (ts and ts > muse_last):
                muse_last = ts
                muse_last_row = r
        if "OPENCODE" in pm.upper():
            if oc_first is None or (ts and ts < oc_first):
                oc_first = ts
            if oc_last is None or (ts and ts > oc_last):
                oc_last = ts
        if "2026-08-31" <= ts[:10] <= "2026-09-14" and is_muse:
            for_window[0] += 1
            for_window[1] += int(r.get("input_tokens") or 0)
            for_window[2] += int(r.get("cached_input_tokens") or 0)
            for_window[3] += int(r.get("output_tokens") or 0)
            for_window[4] += int(r.get("reasoning_output_tokens") or 0)

print("file:", path)
print("total_lines:", n, "unparseable:", bad)
print("--- provider_mode counts ---")
for k, v in sorted(pmode.items()):
    print(f"{v:8d}  {k}")
print("--- model counts ---")
for k, v in sorted(models.items(), key=lambda x: -x[1]):
    print(f"{v:8d}  {k}")
print("--- (model, provider_mode) counts ---")
for (m, pm), v in sorted(combo.items(), key=lambda x: -x[1]):
    print(f"{v:8d}  model={m!r} provider_mode={pm!r}")
print("--- Muse rows: first ---")
print(muse_first, json.dumps(muse_first_row, ensure_ascii=False) if muse_first_row else None)
print("--- Muse rows: last ---")
print(muse_last, json.dumps(muse_last_row, ensure_ascii=False) if muse_last_row else None)
print("--- OPENCODE provider_mode first/last ---", oc_first, oc_last)
print("--- Muse rows per day (all time) ---")
for d in sorted(days):
    t = day_tok[d]
    print(f"{d}  rows={t[0]:6d} in={t[1]:12d} cached={t[2]:10d} out={t[3]:11d} reason={t[4]:11d}")
print("--- window 2026-08-31..2026-09-14 Muse totals [rows,in,cached,out,reason] ---")
print(for_window)
