#!/usr/bin/env python3
"""WP1 ACC-001: scan an MT5 Experts/MQL5 Logs file (UTF-16) for Muse/OpenCode events.

Each line carries a LOCAL time (UTC+3) at columns after the 2-char tag, e.g.
    PJ  0   16:50:12.345  PO3_AIGate_ScannerEA (GBPUSD,H1)  <message>
The date is the file name (YYYYMMDD.log).

Usage: python _wp1_mt5log.py <YYYYMMDD.log> [pattern ...]
Prints counts and first/last match per pattern. Streaming is not practical on
UTF-16 byte streams here, so the file is decoded once.
"""
import sys
import re
import collections

path = sys.argv[1]
pats = sys.argv[2:] or ["muse-spark", "opencode", "GoUsageLimitError", "429", "403", "500"]
counts = collections.Counter()
first = {}
last = {}
samples = collections.defaultdict(list)

ts_re = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3})")

with open(path, "rb") as f:
    text = f.read().decode("utf-16", errors="replace")
for line in text.splitlines():
    for p in pats:
        if p in line:
            counts[p] += 1
            ts = ts_re.search(line)
            stamp = ts.group(1) if ts else "?"
            if p not in first:
                first[p] = stamp
            last[p] = stamp
            if len(samples[p]) < 6:
                samples[p].append((stamp, line.strip()[:260]))

print("file:", path)
print("lines:", len(text.splitlines()))
for p in pats:
    print(f"  {counts[p]:7d}  {p}  first={first.get(p)} last={last.get(p)}")
for p in pats:
    if samples[p]:
        print("### " + p)
        for stamp, s in samples[p]:
            print(f"   {stamp}  {s}")
