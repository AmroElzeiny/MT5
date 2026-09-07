from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping

from .config import FactoryConfig
from .utils import atomic_write_json, atomic_write_text


def _fmt(value:Any,digits:int=3)->str:
    if value is None:return "n/a"
    if isinstance(value,float):return f"{value:.{digits}f}"
    return str(value)

class ReportWriter:
    def __init__(self,config:FactoryConfig):self.config=config
    def write(self,run_id:str,result:Mapping[str,Any])->tuple[Path,Path]:
        json_path=self.config.reports_dir/f"{run_id}.json"; md_path=self.config.reports_dir/f"{run_id}.md"
        atomic_write_json(json_path,result); atomic_write_text(md_path,self._markdown(result)); return json_path,md_path
    def _markdown(self,r:Mapping[str,Any])->str:
        q=r.get("data_quality",{}); s=r.get("summary",{}); ai=r.get("ai",{}); obs=r.get("observations",[])
        rr=ai.get("synthesis") or ai.get("researcher") or {}
        critic=ai.get("critic") or {}
        lines=[f"# R&D Investigation {r.get('research_run_id','')}","",f"**Question:** {r.get('question','')}",f"**Run mode:** {r.get('run_mode','')}  ",f"**AI mode:** {r.get('ai_mode','')}  ","**Production authority:** NONE","", "## Research Scope", "```json", json.dumps(r.get("scope",{}),indent=2,ensure_ascii=False), "```", "", "## Data Quality",f"Status: **{q.get('status','UNKNOWN')}** — usable {q.get('usable_records',0)} / {q.get('total_records',0)} records.",""]
        if q.get("warnings"): lines.extend(["Warnings:"]+[f"- {x}" for x in q["warnings"]]+[""])
        lines += ["## What Happened",f"Trades analyzed: **{s.get('count',0)}**",f"Win rate: **{_fmt((s.get('win_rate') or 0)*100,1)}%**" if s.get('win_rate') is not None else "Win rate: n/a",f"Expectancy: **{_fmt(s.get('expectancy_r'))}R**",f"Median: **{_fmt(s.get('median_r'))}R**",f"Profit factor: **{_fmt(s.get('profit_factor'))}**",f"Sample strength: **{s.get('sample_strength','unknown')}**","", "## Key Findings"]
        lines += [f"- {o.get('detail') or json.dumps(o,ensure_ascii=False)}" for o in obs] or ["- No deterministic anomaly crossed the configured reporting thresholds."]
        lines += ["", "## Comparable Cases",f"Comparable cases selected: **{r.get('comparable_case_count',0)}**","", "## Execution Research", "```json", json.dumps(r.get("execution_research",{}),indent=2,ensure_ascii=False), "```", "", "## AI Value Research", "```json", json.dumps(r.get("ai_value_research",{}),indent=2,ensure_ascii=False), "```", "", "## Most Likely Explanation"]
        lines.append(rr.get("most_likely_explanation") or f"AI analysis not used. {ai.get('skipped_reason','')}")
        lines += ["", "## Alternative Explanations"] + ([f"- {x}" for x in rr.get("alternative_explanations",[])] or ["- None supplied."])
        lines += ["", "## Evidence For"] + ([f"- {x}" for x in rr.get("evidence_for",[])] or ["- Deterministic findings only; no AI evidence list supplied."])
        lines += ["", "## Evidence Against"] + ([f"- {x}" for x in rr.get("evidence_against",[])] or ["- None supplied."])
        lines += ["", "## Sample Limitations"] + ([f"- {x}" for x in rr.get("uncertainty",[])] or [f"- Sample strength is {s.get('sample_strength','unknown')}; grouped findings are exploratory and not causal proof."])
        lines += ["", "## AI Assessment",f"Confidence: **{rr.get('confidence','n/a')}**",f"Recommendation: **{rr.get('production_recommendation','n/a')}**"]
        if critic: lines += ["", "## Critic Assessment", critic.get("most_likely_explanation","")]
        hyp=r.get("registered_hypothesis")
        lines += ["", "## Recommended Hypothesis", f"{hyp.get('hypothesis_id')}: {hyp.get('statement')}" if hyp else "No hypothesis was automatically registered from this run.", "", "## Suggested Experiment", hyp.get("required_experiment","") if hyp else "No experiment proposed.", "", "## Cost",f"AI calls: {r.get('cost',{}).get('calls',0)}",f"Estimated cost: ${_fmt(r.get('cost',{}).get('estimated_cost_usd',0.0),6)}","", "## Production Recommendation",f"**{r.get('production_recommendation','NO_ACTION')}**","","> This subsystem is research-only. It does not deploy, modify MT5 inputs, or place trades.",""]
        return "\n".join(lines)
