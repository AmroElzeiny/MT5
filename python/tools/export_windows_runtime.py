"""Incremental Windows runtime export for the GitHub snapshot.

Read-only on every source. Copies AI-decision history (bus requests/responses,
shadow outcomes, trade results, tester cache, runtime state, gate logs, MT5
journals) that is new or changed since the previous export into
windows_export_<stamp>/, gzipping anything over 50 MB. Any file carrying a
credential value from a local .env file, or a key-shaped string, is refused.
Exits non-zero if a secret or an over-size file would reach the commit.
"""
import datetime as dt, gzip, hashlib, json, os, re, sqlite3, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APPDATA = Path(os.environ["APPDATA"])
BUS = APPDATA / r"MetaQuotes\Terminal\Common\Files\PO3_AI_BUS"
TERM = APPDATA / r"MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE"
GZ_OVER = 50 * 1024 * 1024
GITHUB_LIMIT = 99 * 1024 * 1024
STAMP = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT = REPO / f"windows_export_{STAMP}"


def lp(p):
    s = str(p)
    return s if s.startswith("\\\\?\\") else "\\\\?\\" + s


def read(p):
    with open(lp(p), "rb") as f:
        return f.read()


# ---- credential values from every local env file (never printed) ----
secret_vals = set()
for envf in REPO.rglob("*.env*"):
    if ".venv" in envf.parts or not envf.is_file():
        continue
    for line in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
        if re.search(r"(API_KEY|SECRET|PASSWORD|_TOKEN$)", key) and len(val) >= 16 \
                and "your" not in val.lower() and "xxx" not in val.lower():
            secret_vals.add(val)
NEEDLES = [v.encode("utf-8") for v in secret_vals] + [v.encode("utf-16-le") for v in secret_vals]
KEY_RE = re.compile(rb"sk-(?:or-v1-|or-|proj-)?[A-Za-z0-9_\-]{32,}")


def has_secret(data):
    if any(n in data for n in NEEDLES):
        return True
    if KEY_RE.search(data):
        return True
    return b"s\x00k\x00-\x00" in data and bool(KEY_RE.search(data.replace(b"\x00", b"")))


# ---- what earlier exports already hold ----
prev = {}
for mf in sorted(REPO.glob("windows_export_*/MANIFEST.json")):
    for f in json.loads(mf.read_text(encoding="utf-8"))["files"]:
        prev[re.sub(r"\.gz$", "", f["path"])] = f["source_sha256"]

jobs = []
for d in ["logs", "completed", "request_ledger", "responses", "response_debug", "config",
          "quarantined", "timed_out", "shutdown", "stale", "archive"]:
    jobs += [("PO3_AI_BUS/" + p.relative_to(BUS).as_posix(), p) for p in (BUS / d).rglob("*") if p.is_file()]
for sub in ["state", "trade_outcomes"]:
    jobs += [("PO3_AI_BUS/" + p.relative_to(BUS).as_posix(), p) for p in (BUS / "bybit" / sub).rglob("*") if p.is_file()]
jobs += [("PO3_AI_BUS/bybit/" + p.name, p) for p in (BUS / "bybit").glob("*") if p.is_file()]
for day in ["20260911", "20260912", "20260913", "20260914"]:
    for folder, tag in [(TERM / "MQL5" / "Logs", "MQL5_Logs"), (TERM / "Logs", "Logs")]:
        if (folder / f"{day}.log").exists():
            jobs.append((f"MT5_terminal/{tag}/{day}.log", folder / f"{day}.log"))
jobs.append(("MT5_terminal/Logs/metaeditor.log", TERM / "Logs" / "metaeditor.log"))
jobs += [("MT5_terminal/MQL5_Experts_MT5_PO3_Codex/" + p.name, p)
         for p in (TERM / "MQL5" / "Experts" / "MT5_PO3_Codex").glob("*.log")]
jobs.append(("python_data/ai_decision_cache.jsonl", REPO / "python" / "data" / "ai_decision_cache.jsonl"))

manifest = {"export_folder": OUT.name, "created_local": dt.datetime.now().isoformat(timespec="seconds"),
            "host": "windows_fxpro_original", "mode": "incremental_vs_all_previous_windows_exports",
            "files": [], "skipped_unchanged": 0, "errors": [], "refused_secret": [], "totals": {}}
stored_total = 0
for rel, src in jobs:
    try:
        data = read(src)
        mtime = os.stat(lp(src)).st_mtime
    except OSError as e:
        manifest["errors"].append({"path": rel, "error": str(e)})
        continue
    digest = hashlib.sha256(data).hexdigest()
    if prev.get(rel) == digest:
        manifest["skipped_unchanged"] += 1
        continue
    if has_secret(data):
        manifest["refused_secret"].append(rel)
        continue
    gz = len(data) > GZ_OVER
    dest = OUT / (rel + (".gz" if gz else ""))
    os.makedirs(lp(dest.parent), exist_ok=True)
    if gz:
        with gzip.open(lp(dest), "wb", compresslevel=6) as g:
            g.write(data)
    else:
        with open(lp(dest), "wb") as w:
            w.write(data)
    size = os.stat(lp(dest)).st_size
    stored_total += size
    manifest["files"].append({"path": dest.relative_to(OUT).as_posix(), "source": str(src),
                              "source_bytes": len(data), "stored_bytes": size, "source_sha256": digest,
                              "gzipped": gz, "new_since_previous_export": rel not in prev,
                              "source_mtime": dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")})

db = REPO / "python" / "data" / "ai_trade_memory.sqlite3"
con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
manifest["sqlite"] = {"path": "python/data/ai_trade_memory.sqlite3 (committed in place)",
                      "integrity_check": con.execute("PRAGMA integrity_check").fetchone()[0],
                      "rows": {t: con.execute(f'select count(*) from "{t}"').fetchone()[0]
                               for (t,) in con.execute("select name from sqlite_master where type='table'")}}
con.close()

# ---- guard everything the commit will contain ----
problems = []
status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all", "-z"],
                        capture_output=True).stdout.split(b"\0")
for entry in status:
    if len(entry) < 4:
        continue
    path = REPO / entry[3:].decode("utf-8", "replace")
    if not path.is_file():
        continue
    if os.stat(lp(path)).st_size > GITHUB_LIMIT:
        problems.append(f"over_github_limit:{path.relative_to(REPO)}")
    elif "__pycache__" not in path.parts and has_secret(read(path)):
        problems.append(f"secret:{path.relative_to(REPO)}")

manifest["totals"] = {"files": len(manifest["files"]), "stored_bytes": stored_total,
                      "credential_values_checked": len(secret_vals)}
(OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

groups = {}
for f in manifest["files"]:
    parts = f["path"].split("/")
    g = "/".join(parts[:3] if parts[:2] == ["PO3_AI_BUS", "logs"] and len(parts) > 3 else parts[:2])
    c = groups.setdefault(g, [0, 0])
    c[0] += 1
    c[1] += f["stored_bytes"]
rows = "\n".join(f"| `{g}` | {c[0]} | {c[1] / 1048576:.1f} |" for g, c in sorted(groups.items()))
(OUT / "README.md").write_text(f"""# Windows runtime export - {manifest['created_local']} (incremental)

Read-only export from the Windows FxPro MT5 install (terminal `0148BD5691B65B0F2157627A4231F3DE`).
Only files that are **new or changed** since the earlier `windows_export_*` folders are stored
(same relative layout, so later folders overlay earlier ones). `MANIFEST.json` lists each file with
its source path, sha256 of the uncompressed source bytes, sizes and mtime.

| Group | Files | MB stored |
|---|---:|---:|
{rows}

- Unchanged since previous export (not duplicated): {manifest['skipped_unchanged']}
- Files over 50 MB are gzipped (`.gz`). EA-written files and MT5 journals are UTF-16LE.
- `python/data/ai_trade_memory.sqlite3` is committed in place: integrity `{manifest['sqlite']['integrity_check']}`,
  rows {json.dumps(manifest['sqlite']['rows'])}.
- Excluded: all `.env` files, `PO3_AI_BUS/rejected` (2.2 GB), `bybit/market` raw candle CSVs
  (448 MB, re-downloadable), in-flight `requests`/`processing`/`locks`, venvs.
- Scanned against {len(secret_vals)} local credential values and key-shaped strings;
  refused files: {len(manifest['refused_secret'])}.
""", encoding="utf-8")

print(json.dumps({"out": OUT.name, "groups": {g: f"{c[0]} files / {c[1] / 1048576:.1f} MB" for g, c in groups.items()},
                  "skipped_unchanged": manifest["skipped_unchanged"], "errors": len(manifest["errors"]),
                  "refused_secret": manifest["refused_secret"], "sqlite": manifest["sqlite"],
                  "problems": problems}, indent=1))
sys.exit(1 if problems else 0)
