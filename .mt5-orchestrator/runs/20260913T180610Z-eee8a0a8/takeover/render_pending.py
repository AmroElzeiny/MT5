"""Render the report's pending markdown blocks from benchmark_summary.json + extras_summary.json."""
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
S = json.loads((RUN / "benchmark_summary.json").read_text(encoding="utf-8"))
X = json.loads((HERE / "extras_summary.json").read_text(encoding="utf-8"))
PROBE = [json.loads(l) for l in (HERE / "token_probe_results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
ARMS = ["muse_high_a", "muse_high_b", "muse_medium", "muse_low", "muse_minimal", "luna_low_flex", "muse_high_compact"]
EFFORT_ARMS = ["muse_medium", "muse_low", "muse_minimal", "luna_low_flex"]
IN, OUT, CACHED = 0.10, 0.20, 0.002
TRANSPORT_CATS = {"InternalServerError", "APIConnectionError", "APITimeoutError", "Timeout", "ReadTimeout",
                  "ServiceUnavailableError", "BadGatewayError", "GatewayTimeoutError"}


def g(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def rate(block, ci=True):
    if not isinstance(block, dict) or block.get("rate") is None:
        return "n/a"
    txt = f"{100 * block['rate']:.1f}% ({block['numerator']}/{block['denominator']})"
    lo_hi = block.get("wilson_95_ci")
    if ci and lo_hi:
        txt += f" [{100 * lo_hi[0]:.0f}–{100 * lo_hi[1]:.0f}]"
    return txt


def num(v, nd=0):
    return "n/a" if not isinstance(v, (int, float)) else f"{v:,.{nd}f}"


def arms_present():
    return [a for a in ARMS if a in S]


def quality():
    out = ["Pre-registered BEN-004/005 metrics (`benchmark_summary.json`). Rates show numerator/denominator and "
           "the Wilson 95% interval in brackets.", "",
           "| Arm | Effort | Completed | APPROVE / ABSTAIN / REJECT | Final allow | Schema-valid | Semantic-valid | Infra outcome (literal e) |",
           "|---|---|---|---|---|---|---|---|"]
    for a in arms_present():
        b = S[a]
        dist = b.get("decision_state_distribution") or {}
        out.append(f"| {a} | {b.get('effort_requested')} | {b.get('n_completed_ok')}/{b.get('n_planned')} | "
                   f"{dist.get('APPROVE', 0)} / {dist.get('ABSTAIN', 0)} / {dist.get('REJECT', 0)} | {rate(b.get('final_allow_rate'), False)} | "
                   f"{rate(b.get('schema_valid_rate'), False)} | {rate(b.get('semantic_valid_rate'), False)} | {rate(b.get('infra_failure_rate'), False)} |")
    out += ["", "Agreement with `muse_high_a`. The `muse_high_b` row is the HIGH-vs-HIGH noise floor.", "",
            "| Arm | Decision state | Final allow | Selected candidate | Veto code | Quality-score MAD | S1 | S2 (noise) | S3 | a | b | c-schema | c-semantic | d | e | Non-inferior |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in arms_present():
        if a == "muse_high_a":
            continue
        b = S[a]
        ag = b.get("agreement_vs_high_a") or {}
        sf = b.get("safety") or {}
        c = g(b, "ben005", "conditions", default={}) or {}
        mark = lambda k: "✓" if c.get(k) else ("✗" if k in c else "—")  # noqa: E731
        verdict = g(b, "ben005", "non_inferior")
        verdict_txt = "noise floor" if a == "muse_high_b" else ("**yes**" if verdict else "**no**")
        out.append(f"| {a} | {rate(ag.get('decision_state_agreement'))} | {rate(ag.get('final_allow_agreement'))} | "
                   f"{rate(ag.get('selected_candidate_agreement'))} | {rate(ag.get('veto_code_agreement'))} | {num(ag.get('quality_score_mad'), 2)} | "
                   f"{sf.get('S1', 'n/a')} | {sf.get('S2', 'n/a')} ({sf.get('S2_high_vs_high_noise_count', 'n/a')}) | {sf.get('S3', 'n/a')} | "
                   f"{mark('a_S1_zero')} | {mark('b_S2_within_high_vs_high_noise')} | {mark('c_schema_valid_ge_high_minus_2pp')} | "
                   f"{mark('c_semantic_valid_ge_high_minus_2pp')} | {mark('d_state_agreement_ge_noise_minus_5pp')} | {mark('e_infra_failure_le_high')} | {verdict_txt} |")
    sens = g(X, "sensitivity", "per_arm", default={}) or {}
    out += ["", "Sensitivity only (cannot change a verdict): agreement over pairs where neither side had an "
            f"infrastructure outcome; pooled HIGH infra rate {num(100 * (g(X, 'sensitivity', 'pooled_high_infra_rate') or 0), 1)}%.", "",
            "| Arm | Clean pairs | Clean state agreement | Infra outcomes | Causes (role:http:category) |", "|---|---|---|---|---|"]
    for a in arms_present():
        s = sens.get(a) or {}
        cl = s.get("state_agreement_clean")
        out.append(f"| {a} | {s.get('clean_pairs_vs_high_a') if s.get('clean_pairs_vs_high_a') is not None else '—'} | "
                   f"{'—' if cl is None else f'{100 * cl:.1f}%'} | {s.get('infra_outcomes', 0)} | "
                   f"{', '.join(f'{k}×{v}' for k, v in (s.get('infra_causes') or {}).items()) or '—'} |")
    return "\n".join(out)


def role(b, r, k):
    return g(b, "roles", r, k)


def rsn():
    out = ["| Arm | Analyst reasoning avg | Analyst output avg | Critic output avg | Tokens / request (Δ vs HIGH) | "
           "Go USD / request (Δ) | Latency / request s (Δ) | Verdict |", "|---|---|---|---|---|---|---|---|"]
    for a in ["muse_high_a", "muse_high_b", *EFFORT_ARMS]:
        if a not in S:
            continue
        b = S[a]
        sv = b.get("savings_vs_high") or {}
        tok, cost, lat = sv.get("tokens_per_request_avg") or {}, sv.get("cost_usd_per_request_avg") or {}, sv.get("latency_sec_per_request_avg") or {}
        v = g(b, "ben005", "non_inferior")
        vt = "reference" if a in ("muse_high_a", "muse_high_b") else ("non-inferior" if v else "**not non-inferior**")
        cost_txt = "unpriced (OpenAI account)" if a == "luna_low_flex" else f"{num(cost.get('arm'), 5)} ({num(cost.get('delta_pct'), 1)}%)"
        out.append(f"| {a} | {num(role(b, 'analyst', 'reasoning_tokens_avg'))} | {num(role(b, 'analyst', 'output_tokens_avg'))} | "
                   f"{num(role(b, 'critic', 'output_tokens_avg'))} | {num(tok.get('arm'))} ({num(tok.get('delta_pct'), 1)}%) | {cost_txt} | "
                   f"{num(lat.get('arm'), 1)} ({num(lat.get('delta_pct'), 1)}%) | {vt} |")
    return "\n".join(out)


def encoding_attributable(arm):
    causes = g(X, "sensitivity", "per_arm", arm, "infra_causes", default={}) or {}
    n = 0
    for key, count in causes.items():
        parts = [p for p in key.split(",") if p]
        transport = parts and all(
            (p.split(":")[1].startswith("5") or p.split(":")[2] in TRANSPORT_CATS or p.split(":")[1] == "429")
            for p in parts if p.count(":") >= 2
        ) and key != "no_attempt_record"
        if not transport:
            n += count
    return n


def ab():
    out = []
    for base in ("muse_high_a", "muse_high_b"):
        blk = g(X, "paired_vs_compact", base, default={}) or {}
        out += [f"Paired provider input tokens, `muse_high_compact` vs `{base}`, on {blk.get('common_requests', 0)} common requests "
                "(first successful call per role):", "",
                "| Role | Paired n | Input canonical (mean) | Input compact (mean) | Δ mean | Δ median | Δ % of mean | Calls canonical / compact |",
                "|---|---|---|---|---|---|---|---|"]
        for r in ("analyst", "critic", "adjudicator"):
            row = (blk.get("roles") or {}).get(r)
            if not row:
                continue
            out.append(f"| {r} | {row['paired_n']} | {num(row['input_base_mean'])} | {num(row['input_compact_mean'])} | {num(row['delta_mean'])} | "
                       f"{num(row['delta_median'])} | {num(row['delta_pct_of_mean'], 1)}% | {row['calls_base']} / {row['calls_compact']} |")
        out.append("")
    b = S.get("muse_high_compact") or {}
    c = g(b, "ben005", "conditions", default={}) or {}
    n_high = max(1, int(g(X, "arms", "muse_high_a", "pairs", default=1)))
    n_cmp = max(1, int(g(X, "arms", "muse_high_compact", "pairs", default=1)))
    e_prime = encoding_attributable("muse_high_compact") / n_cmp <= encoding_attributable("muse_high_a") / n_high + 1e-12
    f_ok = (g(X, "paired_vs_compact", "muse_high_a", "roles", "analyst", "delta_median") or 0) < 0
    sv = b.get("savings_vs_high") or {}
    out += ["Addendum A2 decision rule for the production default:", "",
            "| Condition | Result |", "|---|---|",
            f"| (a) S1 = 0 | {c.get('a_S1_zero')} |",
            f"| (b) S2 within HIGH-vs-HIGH noise | {c.get('b_S2_within_high_vs_high_noise')} |",
            f"| (c) schema-valid ≥ HIGH − 2 pp | {c.get('c_schema_valid_ge_high_minus_2pp')} |",
            f"| (c) semantic-valid ≥ HIGH − 2 pp | {c.get('c_semantic_valid_ge_high_minus_2pp')} |",
            f"| (d) state agreement ≥ noise − 5 pp | {c.get('d_state_agreement_ge_noise_minus_5pp')} |",
            f"| (e′) encoding-attributable failures ≤ HIGH ({encoding_attributable('muse_high_compact')}/{n_cmp} vs {encoding_attributable('muse_high_a')}/{n_high}) | {e_prime} |",
            f"| (e) literal BEN-005 infra rate ≤ HIGH (reported) | {c.get('e_infra_failure_le_high')} |",
            f"| (f) paired analyst input decreases | {f_ok} |",
            f"| **Adopt compact_v1 as default** | **{all(c.get(k) for k in ('a_S1_zero', 'b_S2_within_high_vs_high_noise', 'c_schema_valid_ge_high_minus_2pp', 'c_semantic_valid_ge_high_minus_2pp', 'd_state_agreement_ge_noise_minus_5pp')) and e_prime and f_ok}** |",
            "", f"Whole-request averages vs mean of the two HIGH arms: tokens {num(g(sv, 'tokens_per_request_avg', 'arm'))} "
            f"({num(g(sv, 'tokens_per_request_avg', 'delta_pct'), 1)}%), Go USD {num(g(sv, 'cost_usd_per_request_avg', 'arm'), 5)} "
            f"({num(g(sv, 'cost_usd_per_request_avg', 'delta_pct'), 1)}%), latency {num(g(sv, 'latency_sec_per_request_avg', 'arm'), 1)} s "
            f"({num(g(sv, 'latency_sec_per_request_avg', 'delta_pct'), 1)}%). Output tokens are model-driven and vary run to run; "
            "the input delta is the encoding effect."]
    return "\n".join(out)


def cache():
    out = ["| Arm | Token-weighted cached-read ratio | Analyst cached avg | Critic cached avg | Adjudicator cached avg |", "|---|---|---|---|---|"]
    for a in arms_present():
        if a == "luna_low_flex":
            continue
        b = S[a]
        ratio = g(X, "arms", a, "token_weighted_cached_read_ratio")
        out.append(f"| {a} | {'n/a' if ratio is None else f'{100 * ratio:.2f}%'} | {num(role(b, 'analyst', 'cached_read_tokens_avg'))} | "
                   f"{num(role(b, 'critic', 'cached_read_tokens_avg'))} | {num(role(b, 'adjudicator', 'cached_read_tokens_avg'))} |")
    out.append("")
    out.append("Reference before the Phase-1 session change (per-request sessions, 2,454 responses, `OPENCODE_GO_BILLING_EVIDENCE.md` §7): **0.37%**.")
    return "\n".join(out)


def ranked():
    def per_request_calls(arm):
        t = g(X, "arms", arm, "per_role_totals", default={}) or {}
        pairs = max(1, int(g(X, "arms", arm, "pairs", default=1)))
        return {r: (t.get(r) or {}).get("calls", 0) / pairs for r in ("analyst", "critic", "adjudicator")}

    calls = per_request_calls("muse_high_a")
    roles = g(X, "paired_vs_compact", "muse_high_a", "roles", default={}) or {}
    ab_saved = sum(-(roles.get(r) or {}).get("delta_mean", 0) * calls[r] for r in calls)
    probe_pairs = {}
    for row in PROBE:
        probe_pairs.setdefault(row["request"], {})[row["projection"]] = row.get("input_tokens")
    deltas = [p["canonical"] - p["compact_v1"] for p in probe_pairs.values() if p.get("canonical") and p.get("compact_v1")]
    rows = []
    rows.append(("Lossless compact catalog encoding (`compact_v1`)", f"{num(ab_saved)} input (A/B, all roles); analyst exact probe median {num(statistics.median(deltas) if deltas else None)}",
                 ab_saved * IN / 1e6 * 1000, "measured", "see §9 / A2"))
    b = S.get("muse_high_a") or {}
    cached = sum((role(b, r, "cached_read_tokens_avg") or 0) for r in ("analyst", "critic", "adjudicator"))
    rows.append(("Phase-1 prefix-scoped session (cache reads)", f"{num(cached)} input billed at cached rate", cached * (IN - CACHED) / 1e6 * 1000, "measured", "in production since Phase 1"))
    for a in ("muse_medium", "muse_low", "muse_minimal"):
        sv = g(S, a, "savings_vs_high", default={}) or {}
        cost = sv.get("cost_usd_per_request_avg") or {}
        saved = (cost.get("high") or 0) - (cost.get("arm") or 0)
        tok = sv.get("tokens_per_request_avg") or {}
        v = g(S, a, "ben005", "non_inferior")
        rows.append((f"Reasoning effort → {a.split('_')[1]}", f"{num((tok.get('high') or 0) - (tok.get('arm') or 0))} tokens", saved * 1000, "measured",
                     "adopt" if v else "rejected: not non-inferior"))
    rows.append(("Dereference duplicated catalog values (class a)", "≈1,070 (≈481 per role call × 2.2 calls)", 1070 * IN / 1e6 * 1000, "estimated", "not implemented: citation-accuracy risk"))
    rows.append(("Move critic/adjudicator id list out of instructions", "≈300–400 (duplicate of evidence list) + larger cached prefix", 350 * IN / 1e6 * 1000, "estimated", "not implemented: prompt change needs its own A/B"))
    rows.append(("Numeric precision trimming", "≈38 per payload (hypothetical, 0 provable)", 38 * 2.2 * IN / 1e6 * 1000, "estimated", "not implemented: not provable"))
    rows.append(("Output field trimming", "0 (no unconsumed field)", 0.0, "measured", "nothing to trim"))
    rows.sort(key=lambda r: -r[2])
    out = ["| Rank | Optimization | Tokens / request | USD / 1,000 requests (published rates) | Basis | Status |", "|---|---|---|---|---|---|"]
    for i, (name, tok, usd, basis, status) in enumerate(rows, 1):
        out.append(f"| {i} | {name} | {tok} | ${usd:,.3f} | {basis} | {status} |")
    return "\n".join(out)


if __name__ == "__main__":
    blocks = {"QUALITY": quality(), "RSN": rsn(), "AB": ab(), "CACHE-TABLE": cache(), "RANKED": ranked()}
    (HERE / "pending_blocks.json").write_text(json.dumps(blocks, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, v in blocks.items():
        print(f"\n===== {k}\n{v}")
