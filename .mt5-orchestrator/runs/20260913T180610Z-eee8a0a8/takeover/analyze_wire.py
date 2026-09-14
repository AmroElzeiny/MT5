"""Offline size study of candidate lossless catalog projections on captured canonical wires."""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_PY = Path(__file__).resolve().parents[4] / "python"
sys.path.insert(0, str(REPO_PY / "tools"))
from opencode_token_attribution import raw_units  # noqa: E402

FACTOR = 0.575  # stage-A median per-request Muse factor (wp2/calibration.json)


def dumps(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def tok(text):
    return round(raw_units(text) * FACTOR)


def common_prefix(paths):
    if not paths:
        return ""
    split = [p.split(".") for p in paths]
    out = []
    for parts in zip(*split):
        if all(x == parts[0] for x in parts):
            out.append(parts[0])
        else:
            break
    # never swallow a whole path
    if out and any(len(s) == len(out) for s in split):
        out = out[:-1]
    return ".".join(out) + ("." if out else "")


def variants(items):
    v = {}
    v["V0_canonical_rows"] = items
    v["V1_tuple_rows"] = {"columns": ["id", "p", "v", "c"],
                          "items": [[r["id"], r["p"], r["v"]] + ([r["c"]] if "c" in r else []) for r in items]}
    groups = defaultdict(list)
    for r in items:
        groups["global" if "c" not in r else str(r["c"])].append(r)
    v["V2_grouped_tuple"] = {"columns": ["id", "p", "v"],
                             "global": [[r["id"], r["p"], r["v"]] for r in groups.get("global", [])],
                             "by_candidate": {k: [[r["id"], r["p"], r["v"]] for r in rows]
                                              for k, rows in groups.items() if k != "global"}}
    v3 = {"columns": ["id", "p_suffix", "v"], "groups": []}
    for k, rows in groups.items():
        # sub-group by first two path segments so each block shares a real prefix
        sub = defaultdict(list)
        for r in rows:
            sub[".".join(r["p"].split(".")[:2])].append(r)
        for key, srows in sub.items():
            pref = common_prefix([r["p"] for r in srows])
            block = {"prefix": pref, "rows": [[r["id"], r["p"][len(pref):], r["v"]] for r in srows]}
            if k != "global":
                block["c"] = int(k)
            v3["groups"].append(block)
    v["V3_grouped_prefix"] = v3
    return v


def main(paths):
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        env = json.loads(text)
        cat = env["evidence_catalog"]
        items = cat["items"]
        print(f"\n=== {Path(path).name}")
        print(f"input_chars={len(text)} input_tok~{tok(text)} catalog_items={len(items)} "
              f"global={sum('c' not in r for r in items)}")
        sec = {k: len(dumps(val)) for k, val in env.items()}
        print("top_sections_chars:", dict(sorted(sec.items(), key=lambda kv: -kv[1])[:10]))
        p_chars = sum(len(dumps(r["p"])) for r in items)
        v_chars = sum(len(dumps(r["v"])) for r in items)
        print(f"items_chars={len(dumps(items))} p_chars={p_chars} v_chars={v_chars}")
        first = Counter(".".join(r["p"].split(".")[:2]) for r in items)
        print("path_heads:", first.most_common(12))
        print("sample_paths:", [r["p"] for r in items[::max(1, len(items) // 12)]][:14])
        allowed = sum(len(dumps(row.get("allowed_evidence_ref_ids", [])))
                      for row in env.get("entry_and_invalidation", {}).get("candidates", []))
        print(f"allowed_ids_chars={allowed}")
        base = None
        for name, body in variants(items).items():
            s = dumps(body)
            t = tok(s)
            base = base or t
            print(f"  {name:22s} chars={len(s):7d} tok~{t:6d} saved_tok~{base - t:6d}")


if __name__ == "__main__":
    main(sys.argv[1:])
