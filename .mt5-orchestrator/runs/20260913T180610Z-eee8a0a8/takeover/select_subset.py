"""Deterministic N=60 execution subset of the frozen 187-row manifest (protocol addendum A1).

Rule, fixed before any full-run paid call (only one pilot pair has run):
  1. every row selected as an anchor (CLAUDE.md 4ac) or from the stage-A sample (36 rows);
  2. plus 24 rows from the `historical_allow_or_approve` pool, taken round-robin across
     symbol_class, each class ordered by sha256(f"{seed}|subset_a1|{request_id}").
"""
import hashlib
import json
from collections import OrderedDict
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]
manifest = json.loads((RUN / "benchmark_manifest.json").read_text(encoding="utf-8"))
seed = int(manifest["seed"])
rows = manifest["rows"]

base = [r for r in rows if set(r["selection_reasons"]) & {"anchor_required_by_claude_md_4ac", "stage_a_sample_seed"}]
pool = [r for r in rows if r not in base]


def key(r):
    return hashlib.sha256(f"{seed}|subset_a1|{r['request_id']}".encode()).hexdigest()


by_class = OrderedDict()
for r in sorted(pool, key=lambda r: (r["symbol_class"], key(r))):
    by_class.setdefault(r["symbol_class"], []).append(r)
classes = sorted(by_class)
extra = []
while len(extra) < 60 - len(base) and any(by_class.values()):
    for c in classes:
        if by_class[c] and len(extra) < 60 - len(base):
            extra.append(by_class[c].pop(0))

subset = base + extra
out = {
    "addendum": "A1",
    "rule": __doc__.strip(),
    "seed": seed,
    "n": len(subset),
    "base_rows": len(base),
    "approval_rows": len(extra),
    "historical_decision_mix": {},
    "symbol_class_mix": {},
    "request_ids": [r["request_id"] for r in subset],
}
for r in subset:
    d = r["historical_decision"]["decision_state"]
    out["historical_decision_mix"][d] = out["historical_decision_mix"].get(d, 0) + 1
    c = r["symbol_class"]
    out["symbol_class_mix"][c] = out["symbol_class_mix"].get(c, 0) + 1
(RUN / "takeover" / "subset_a1.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in out.items() if k != "request_ids"}, indent=1))
