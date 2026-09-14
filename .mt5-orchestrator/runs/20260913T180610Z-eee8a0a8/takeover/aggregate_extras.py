"""Part 9/10 extras over live benchmark results: cache ratio, per-role token deltas, contamination, cost."""
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
DIRS = [RUN / "results", HERE / "compact_arm" / "results"]
SUBSET = set(json.loads((HERE / "subset_a1.json").read_text(encoding="utf-8"))["request_ids"])
RATES = {"input": 0.10, "output": 0.20, "cached": 0.002}


def load():
    out = defaultdict(dict)
    for d in DIRS:
        for f in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if r.get("mode") != "live" or r.get("request_id") not in SUBSET:
                continue
            out[r["arm"]][r["request_id"]] = r
    return out


def ok_attempts(r):
    return [a for a in r.get("attempts") or [] if isinstance(a, dict) and a.get("outcome") == "ok"]


def role_tokens(r):
    """Per role: first ok attempt (the primary call) and totals over all ok attempts."""
    first, total = {}, defaultdict(lambda: defaultdict(float))
    for a in ok_attempts(r):
        role = a.get("role")
        first.setdefault(role, a)
        for k in ("input_tokens", "cached_read_tokens", "output_tokens", "reasoning_tokens", "latency_sec"):
            total[role][k] += float(a.get(k) or 0)
        total[role]["calls"] += 1
    return first, total


def pct(a, b):
    return None if not b else round(100.0 * (a - b) / b, 2)


def main():
    data = load()
    summary = {"arms": {}, "paired_vs_compact": {}, "contamination": {}}
    for arm, per in sorted(data.items()):
        roles = defaultdict(lambda: defaultdict(float))
        outcomes = defaultdict(int)
        usd = 0.0
        inst = defaultdict(set)
        for r in per.values():
            for a in r.get("attempts") or []:
                if not isinstance(a, dict):
                    continue
                outcomes[f"{a.get('role')}:{a.get('outcome')}"] += 1
                if a.get("instructions_sha256") and a.get("role") == "analyst":
                    inst[a.get("role")].add(a["instructions_sha256"][:12])
                cost = (a.get("expected_go_usage_usd") or {}).get("standard")
                if isinstance(cost, (int, float)):
                    usd += cost
            _first, total = role_tokens(r)
            for role, t in total.items():
                for k, v in t.items():
                    roles[role][k] += v
        muse_in = sum(roles[x]["input_tokens"] for x in roles)
        muse_cached = sum(roles[x]["cached_read_tokens"] for x in roles)
        summary["arms"][arm] = {
            "pairs": len(per),
            "expected_go_usd_standard": round(usd, 4),
            "token_weighted_cached_read_ratio": round(muse_cached / muse_in, 4) if muse_in else None,
            "per_role_totals": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in roles.items()},
            "attempt_outcomes": dict(outcomes),
        }
        summary["contamination"][arm] = {k: sorted(v) for k, v in inst.items()}

    compact = data.get("muse_high_compact", {})
    for base in ("muse_high_a", "muse_high_b"):
        ref = data.get(base, {})
        common = sorted(set(compact) & set(ref))
        per_role = defaultdict(lambda: {"n": 0, "input_base": [], "input_compact": [], "calls_base": 0, "calls_compact": 0})
        for rid in common:
            fb, tb = role_tokens(ref[rid])
            fc, tc = role_tokens(compact[rid])
            for role in set(fb) & set(fc):
                slot = per_role[role]
                slot["n"] += 1
                slot["input_base"].append(fb[role]["input_tokens"])
                slot["input_compact"].append(fc[role]["input_tokens"])
            for role in set(tb) | set(tc):
                per_role[role]["calls_base"] += int(tb.get(role, {}).get("calls", 0))
                per_role[role]["calls_compact"] += int(tc.get(role, {}).get("calls", 0))
        block = {}
        for role, slot in per_role.items():
            if slot["n"]:
                deltas = [c - b for b, c in zip(slot["input_base"], slot["input_compact"])]
                block[role] = {
                    "paired_n": slot["n"],
                    "input_base_mean": round(statistics.mean(slot["input_base"]), 1),
                    "input_compact_mean": round(statistics.mean(slot["input_compact"]), 1),
                    "delta_mean": round(statistics.mean(deltas), 1),
                    "delta_median": round(statistics.median(deltas), 1),
                    "delta_pct_of_mean": pct(statistics.mean(slot["input_compact"]), statistics.mean(slot["input_base"])),
                    "calls_base": slot["calls_base"],
                    "calls_compact": slot["calls_compact"],
                }
        summary["paired_vs_compact"][base] = {"common_requests": len(common), "roles": block}
    # Sensitivity (NOT the pre-registered rule): decision-state agreement with high_a
    # over pairs where neither side had an infrastructure outcome, and the pooled
    # HIGH infra rate, with each infra attempt's provider-side cause.
    def infra(r):
        d = r.get("decision") or {}
        return r.get("status") != "ok" or d.get("decision_quality_tier") != "FULL_STRUCTURED"

    def state(r):
        return (r.get("decision") or {}).get("decision_state")

    high_a = data.get("muse_high_a", {})
    sens = {}
    for arm, per in sorted(data.items()):
        clean = [rid for rid in per if rid in high_a and not infra(per[rid]) and not infra(high_a[rid])]
        agree = sum(1 for rid in clean if state(per[rid]) == state(high_a[rid]))
        causes = defaultdict(int)
        for r in per.values():
            if infra(r):
                failed = [a for a in r.get("attempts") or [] if isinstance(a, dict) and a.get("outcome") != "ok"]
                key = ",".join(sorted({f"{a.get('role')}:{a.get('http_status') or ''}:{a.get('error_category')}" for a in failed})) or "no_attempt_record"
                causes[key] += 1
        sens[arm] = {
            "clean_pairs_vs_high_a": len(clean) if arm != "muse_high_a" else None,
            "state_agreement_clean": round(agree / len(clean), 4) if clean and arm != "muse_high_a" else None,
            "infra_outcomes": sum(1 for r in per.values() if infra(r)),
            "infra_causes": dict(causes),
        }
    pooled = [r for arm in ("muse_high_a", "muse_high_b") for r in data.get(arm, {}).values()]
    summary["sensitivity"] = {
        "per_arm": sens,
        "pooled_high_infra_rate": round(sum(1 for r in pooled if infra(r)) / len(pooled), 4) if pooled else None,
    }
    (HERE / "extras_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1)[:6000])


if __name__ == "__main__":
    main()
