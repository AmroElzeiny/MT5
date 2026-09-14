#!/usr/bin/env python3
"""WP1 ACC-001: Muse-only ai_gate.log classifier (read-only, streaming).

The log has NO per-line timestamps. Request ids embed a Unix epoch as the 4th
underscore field: <account>_<scan_epoch>_<position>_<decision_epoch>_<SYMBOL>_<seq>.
We use that only for chronological ordering and state it as reconstructed.
"""
import sys
import re
import collections
import datetime

path = sys.argv[1]
MUSE = "muse-spark-1.3-contributor"
cat = collections.Counter()
first = {}
last = {}
epochs = collections.defaultdict(list)
fail_status = collections.Counter()
fail_cat = collections.Counter()
fb_status = collections.Counter()
fb_to = collections.Counter()
fb_cat = collections.Counter()
health_flags = collections.Counter()
started_role = collections.Counter()
completed_role = collections.Counter()
failed_samples = []
fb_by_day = collections.Counter()
day_cat = collections.defaultdict(collections.Counter)

def epoch_of(line):
    m = re.search(r"request_id=([0-9]+)_([0-9]{9,})_([0-9]+)_([0-9]{9,})_", line)
    if not m:
        return None
    return int(m.group(4))

def utc(e):
    if e is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(e, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None

def tag_of(line):
    for t in ("provider_call_started", "provider_call_completed", "provider_call_failed",
              "opencode_fallback", "ai_provider_health", "structured_schema_preflight"):
        if line.startswith("[" + t + "]"):
            return t
    return "other"

n = 0
with open(path, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        n += 1
        if MUSE not in line:
            continue
        tag = tag_of(line)
        cat[tag] += 1
        if tag not in first:
            first[tag] = line.rstrip()
        last[tag] = line.rstrip()
        e = epoch_of(line)
        if e:
            epochs[tag].append(e)
            day = utc(e)[:10]
            day_cat[day][tag] += 1
        if tag == "provider_call_started":
            m = re.search(r"role=(\w+)", line)
            started_role[m.group(1) if m else "?"] += 1
        elif tag == "provider_call_completed":
            m = re.search(r"role=(\w+)", line)
            completed_role[m.group(1) if m else "?"] += 1
        elif tag == "provider_call_failed":
            m = re.search(r"http_status=(\d+)", line)
            fail_status[m.group(1) if m else "none"] += 1
            m = re.search(r"error_category=(\w+)", line)
            fail_cat[m.group(1) if m else "none"] += 1
            if len(failed_samples) < 60:
                failed_samples.append(line.rstrip()[:400])
        elif tag == "opencode_fallback":
            m = re.search(r"http_status=(\d+)", line)
            fb_status[m.group(1) if m else "none"] += 1
            m = re.search(r"to_model=([\w.\-]+)", line)
            fb_to[m.group(1) if m else "?"] += 1
            m = re.search(r"error_category=(\w+)", line)
            fb_cat[m.group(1) if m else "none"] += 1
            if e:
                fb_by_day[utc(e)[:10]] += 1
        elif tag == "ai_provider_health":
            m = re.search(r"healthy=(\w+) model_available=(\w+) structured_output_available=(\w+)", line)
            health_flags[m.groups() if m else "?"] += 1

print("log lines:", n)
print("--- Muse-mentioning lines by tag ---")
for k in sorted(cat, key=lambda x: -cat[x]):
    print(f"  {cat[k]:8d}  {k}")
print("--- started roles ---", dict(started_role))
print("--- completed roles ---", dict(completed_role))
print("--- failed http_status ---", dict(fail_status))
print("--- failed error_category ---", dict(fail_cat))
print("--- fallback http_status ---", dict(fb_status))
print("--- fallback to_model ---", dict(fb_to))
print("--- fallback error_category ---", dict(fb_cat))
print("--- health (healthy,model_available,structured_output_available) ---")
for k, v in health_flags.items():
    print("  ", v, k)
print("--- reconstructed UTC min/max per tag (4th request-id field) ---")
for k in ("provider_call_started", "provider_call_completed", "provider_call_failed",
          "opencode_fallback", "ai_provider_health", "structured_schema_preflight"):
    lst = epochs.get(k, [])
    if lst:
        print(f"  {k:32s} n={len(lst):5d} min={utc(min(lst))} max={utc(max(lst))}")
    else:
        print(f"  {k:32s} n=0")
print("--- fallback (429/other) events per reconstructed day ---")
for d in sorted(fb_by_day):
    print(f"  {d}  {fb_by_day[d]}")
print("--- muse per reconstructed day x tag ---")
for d in sorted(day_cat):
    print(f"  {d}  {dict(day_cat[d])}")
print("--- failed samples (first 60, truncated) ---")
for s in failed_samples:
    print("  ", s)
