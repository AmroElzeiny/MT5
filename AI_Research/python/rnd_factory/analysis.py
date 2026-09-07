from __future__ import annotations
import math
import random
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from .config import FactoryConfig
from .models import DataQualityReport, MetricSummary
from .repository import execution_cost, mae_r, mfe_r, outcome_r
from .utils import nested_get, safe_float


def wilson_interval(wins: int, n: int, z: float = 1.96) -> list[float] | None:
    if n <= 0:
        return None
    p = wins / n
    denom = 1 + z*z/n
    center = (p + z*z/(2*n)) / denom
    margin = z * math.sqrt((p*(1-p) + z*z/(4*n))/n) / denom
    return [max(0.0, center-margin), min(1.0, center+margin)]


def bootstrap_mean_ci(values: list[float], samples: int, seed: int = 20260810) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        means.append(sum(rng.choice(values) for _ in values) / len(values))
    means.sort()
    lo = means[int(0.025 * (len(means)-1))]
    hi = means[int(0.975 * (len(means)-1))]
    return [lo, hi]


class DeterministicAnalyzer:
    def __init__(self, config: FactoryConfig):
        self.config = config

    def data_quality(self, rows: list[dict[str, Any]]) -> DataQualityReport:
        seen = set(); duplicates = malformed = missing = identity_warnings = 0; usable = 0
        versions = set(); warnings = []
        for i, row in enumerate(rows):
            if row.get("_malformed_payload"):
                malformed += 1; continue
            key = str(nested_get(row, "trade_key", "memory_id", "candidate_hash") or f"row:{i}")
            if key in seen:
                duplicates += 1; continue
            seen.add(key)
            if outcome_r(row) is None:
                missing += 1; continue
            usable += 1
            for field in ("schema_version", "memory_schema_version"):
                if row.get(field): versions.add(str(row[field]))
            for field in ("execution_identity_verified", "candidate_hash_match", "execution_fingerprint_match"):
                if field in row and row[field] is not True:
                    identity_warnings += 1
        if len(versions) > 1:
            warnings.append("Multiple trade-memory/schema versions are present; cohort comparisons should be treated cautiously.")
        policy_ids = {str(nested_get(row, "policy_id", "python_final_decision.policy_id") or "") for row in rows}
        policy_ids.discard("")
        if len(policy_ids) > 1:
            warnings.append("Multiple policy identities are present in the analyzed sample; apparent performance differences may be version-related.")
        if not rows:
            status = "NO_DATA"; warnings.append("No completed trade records were discovered.")
        elif usable == 0:
            status = "UNUSABLE"; warnings.append("Trade records were found but none had a usable realized-R outcome.")
        elif malformed or identity_warnings or missing > max(2, len(rows)//10):
            status = "DEGRADED"
        else:
            status = "CLEAN"
        return DataQualityReport(status, len(rows), usable, duplicates, malformed, missing, identity_warnings, sorted(versions), warnings)

    def summarize(self, rows: Iterable[Mapping[str, Any]]) -> MetricSummary:
        values = [x for x in (outcome_r(row) for row in rows) if x is not None]
        wins = sum(1 for x in values if x > 0); losses = sum(1 for x in values if x < 0); breakeven = len(values)-wins-losses
        gains = sum(x for x in values if x > 0); neg = -sum(x for x in values if x < 0)
        n = len(values)
        strength = "insufficient" if n < self.config.min_moderate_sample else "moderate" if n < self.config.min_strong_sample else "strong"
        mfes = [x for x in (mfe_r(r) for r in rows) if x is not None]
        maes = [x for x in (mae_r(r) for r in rows) if x is not None]
        costs = [x for x in (execution_cost(r) for r in rows) if x is not None]
        return MetricSummary(
            count=n, wins=wins, losses=losses, breakeven=breakeven,
            win_rate=(wins/n if n else None), win_rate_ci95=wilson_interval(wins,n),
            expectancy_r=(statistics.fmean(values) if values else None), median_r=(statistics.median(values) if values else None),
            expectancy_ci95=bootstrap_mean_ci(values,self.config.bootstrap_samples) if values else None,
            profit_factor=(gains/neg if neg > 0 else None), avg_mfe_r=(statistics.fmean(mfes) if mfes else None),
            avg_mae_r=(statistics.fmean(maes) if maes else None), avg_execution_cost=(statistics.fmean(costs) if costs else None),
            sample_strength=strength,
        )

    def grouped(self, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        fields = ["setup_taxonomy","setup_family","entry_branch","session_code","killzone_code","regime_profile","direction","asset_class"]
        output: dict[str, list[dict[str, Any]]] = {}
        for field in fields:
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                key = str(nested_get(row, field) or "UNKNOWN")
                groups[key].append(row)
            summaries = [{"value": key, **self.summarize(values).to_dict()} for key, values in groups.items()]
            summaries.sort(key=lambda x: (x["count"], x.get("expectancy_r") or -999), reverse=True)
            output[field] = summaries[:25]
        return output

    def recent_vs_prior(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if len(rows) < 20:
            return {"available": False, "reason": "insufficient_history"}
        split = max(10, min(len(rows)//3, 100))
        recent = rows[-split:]
        prior = rows[:-split]
        a = self.summarize(recent).to_dict(); b = self.summarize(prior).to_dict()
        delta = None if a["expectancy_r"] is None or b["expectancy_r"] is None else a["expectancy_r"] - b["expectancy_r"]
        return {"available": True, "recent_count": len(recent), "prior_count": len(prior), "recent": a, "prior": b, "expectancy_delta_r": delta}

    def execution_research(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        enriched = []
        for row in rows:
            intended = safe_float(nested_get(row, "immutable_pre_entry_evidence.intended_entry", "intended_entry", "planned_entry", "entry_plan.entry"))
            actual = safe_float(nested_get(row, "executed_action.entry", "entry_price", "actual_entry"))
            delay = safe_float(nested_get(row, "execution.entry_delay_sec", "entry_delay_sec", "execution_delay_sec"))
            spread = safe_float(nested_get(row, "immutable_pre_entry_evidence.spread", "spread", "spread_points"))
            slippage = safe_float(nested_get(row, "slippage", "execution.slippage", "slippage_points"))
            method = str(nested_get(row, "execution_method", "order_type", "executed_action.order_type") or "UNKNOWN")
            enriched.append({"r": outcome_r(row), "intended": intended, "actual": actual, "delay": delay, "spread": spread, "slippage": slippage, "method": method})
        methods: dict[str, list[float]] = defaultdict(list)
        for row in enriched:
            if row["r"] is not None: methods[row["method"]].append(row["r"])
        method_stats = []
        for method, values in methods.items():
            method_stats.append({"method": method, "count": len(values), "expectancy_r": statistics.fmean(values) if values else None})
        def corr(name: str) -> float | None:
            pairs = [(r[name], r["r"]) for r in enriched if r[name] is not None and r["r"] is not None]
            if len(pairs) < 5: return None
            xs, ys = zip(*pairs)
            if len(set(xs)) < 2 or len(set(ys)) < 2: return None
            return statistics.correlation(xs, ys)
        entry_deltas = [abs(r["actual"]-r["intended"]) for r in enriched if r["actual"] is not None and r["intended"] is not None]
        return {
            "method_stats": sorted(method_stats, key=lambda x: x["count"], reverse=True),
            "entry_delta_count": len(entry_deltas),
            "avg_abs_entry_delta": statistics.fmean(entry_deltas) if entry_deltas else None,
            "correlation_delay_to_r": corr("delay"),
            "correlation_spread_to_r": corr("spread"),
            "correlation_slippage_to_r": corr("slippage"),
        }

    def ai_value(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        buckets: dict[str, list[float]] = defaultdict(list)
        critic = Counter(); adjudicator = Counter()
        for row in rows:
            r = outcome_r(row)
            decision = str(nested_get(row, "python_final_decision.decision_state", "analyst_output.decision_state", "decision_state") or "UNKNOWN").upper()
            if r is not None: buckets[decision].append(r)
            cv = str(nested_get(row, "critic_output.verdict") or "")
            av = str(nested_get(row, "adjudicator_output.verdict") or "")
            if cv: critic[cv] += 1
            if av: adjudicator[av] += 1
        return {
            "realized_by_ai_decision": [{"decision": k, "count": len(v), "expectancy_r": statistics.fmean(v) if v else None} for k,v in buckets.items()],
            "critic_verdict_counts": dict(critic), "adjudicator_verdict_counts": dict(adjudicator),
            "warning": "Rejected/counterfactual decisions require counterfactual outcome evidence; realized trades alone cannot prove opportunity cost.",
        }

    def observations(self, rows: list[dict[str, Any]], grouped: dict[str, list[dict[str, Any]]], trend: dict[str, Any], execution: dict[str, Any]) -> list[dict[str, Any]]:
        obs = []
        if trend.get("available") and trend.get("expectancy_delta_r") is not None and trend["expectancy_delta_r"] < -0.15:
            obs.append({"category":"expectancy_deterioration","severity":"medium","detail":f"Recent expectancy is lower by {trend['expectancy_delta_r']:.3f}R versus prior history."})
        for field, groups in grouped.items():
            for g in groups:
                if g["count"] >= self.config.min_moderate_sample and g.get("expectancy_r") is not None and g["expectancy_r"] < -0.15:
                    obs.append({"category":"underperforming_cohort","severity":"medium","field":field,"value":g["value"],"count":g["count"],"expectancy_r":g["expectancy_r"]})
        for name in ("correlation_delay_to_r","correlation_spread_to_r","correlation_slippage_to_r"):
            value = execution.get(name)
            if value is not None and value < -0.30:
                obs.append({"category":"execution_signal","severity":"medium","metric":name,"correlation":value})
        return obs[:30]
