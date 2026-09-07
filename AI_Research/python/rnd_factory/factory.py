from __future__ import annotations
import uuid
from typing import Any

from .ai import AIOrchestrator
from .ai.budget import BudgetExceeded
from .analysis import DeterministicAnalyzer
from .code_discovery import CodeDiscovery
from .config import FactoryConfig
from .database import ResearchDatabase
from .evidence import EvidenceBuilder
from .reports import ReportWriter
from .repository import MT5RepositoryAdapter
from .utils import git_identity, runtime_identity, sha256_json, utc_now

class ResearchFactory:
    def __init__(self,config:FactoryConfig):
        self.config=config; self.db=ResearchDatabase(config.database_path); self.repo=MT5RepositoryAdapter(config); self.analyzer=DeterministicAnalyzer(config); self.code=CodeDiscovery(config); self.evidence=EvidenceBuilder(config); self.reports=ReportWriter(config)
    def investigate(self,*,question:str="",last_trades:int=100,setup:str="",trade_key:str="",run_mode:str="no-ai") -> dict[str,Any]:
        if not self.config.enabled: raise RuntimeError("rnd_factory_disabled")
        if run_mode not in {"no-ai","quick","standard","deep"}: raise ValueError("invalid_run_mode")
        run_id="RND-"+uuid.uuid4().hex[:16].upper(); ai_mode="none" if run_mode=="no-ai" else self.config.ai_mode
        question=question.strip() or (f"Investigate trade {trade_key}" if trade_key else f"Investigate {setup or 'recent trading'} performance and identify evidence-backed research opportunities.")
        scope={"last_trades":last_trades,"setup":setup,"trade_key":trade_key}; git=git_identity(self.config.repo_root); config_fp=sha256_json({k:(str(v) if hasattr(v, "as_posix") else list(v) if isinstance(v, tuple) else v) for k,v in vars(self.config).items() if k not in {"remote_api_key"}})
        self.db.start_run(run_id=run_id,question=question,run_mode=run_mode,ai_mode=ai_mode,scope=scope,source_commit=git["commit"],config_fingerprint=config_fp)
        try:
            rows=self.repo.load_completed_trades(limit=max(last_trades*3,last_trades) if setup or trade_key else last_trades)
            if setup:
                needle=setup.lower(); rows=[r for r in rows if needle in " ".join(str(r.get(k,"")) for k in ("setup_taxonomy","setup_family","entry_branch")).lower()]
                rows=rows[-last_trades:]
            else: rows=rows[-last_trades:]
            target=self.repo.find_trade(trade_key) if trade_key else None
            if target and all(str(x.get("trade_key"))!=trade_key for x in rows): rows.append(target)
            quality=self.analyzer.data_quality(rows); summary=self.analyzer.summarize(rows); grouped=self.analyzer.grouped(rows); trend=self.analyzer.recent_vs_prior(rows); execution=self.analyzer.execution_research(rows); ai_value=self.analyzer.ai_value(rows); observations=self.analyzer.observations(rows,grouped,trend,execution)
            for obs in observations:
                self.db.add_observation(uuid.uuid4().hex,run_id,obs.get("category","other"),obs.get("severity","info"),obs)
            comparables=self.repo.comparable_trades(target,rows,limit=self.config.max_comparable_cases) if target else []
            extra=[]
            for obs in observations:
                extra += [str(obs.get("metric") or ""),str(obs.get("value") or "")]
            snippets=self.code.search(question+" "+setup,extra_terms=[x for x in extra if x])
            identity={"factory_version":"1.0.0","git":git,"runtime":runtime_identity(),"config_fingerprint":config_fp,"sources":self.repo.authoritative_sources(),"created_at":utc_now()}
            case_pack=self.evidence.build_case_pack(question=question,scope=scope,data_quality=quality.to_dict(),summary=summary.to_dict(),grouped=grouped,trend=trend,execution=execution,ai_value=ai_value,observations=observations,target_trade=target,comparables=comparables,code_snippets=snippets,identity=identity)
            ai_block:dict[str,Any]={}; registered=None; recommendation="MORE_DATA_REQUIRED" if summary.sample_strength=="insufficient" else "NO_ACTION"
            orchestrator=None
            if run_mode!="no-ai" and ai_mode!="none":
                orchestrator=AIOrchestrator(self.config,self.db)
                try:
                    researcher=orchestrator.call(role="researcher",research_run_id=run_id,question=question,case_pack=case_pack); ai_block["researcher"]=researcher.to_dict(); recommendation=researcher.production_recommendation
                    if run_mode=="deep" or (run_mode=="standard" and (researcher.confidence!="high" or researcher.hypothesis)):
                        critic_pack={**case_pack,"researcher_assessment":researcher.to_dict()}; critic=orchestrator.call(role="critic",research_run_id=run_id,question=question,case_pack=critic_pack,hypothesis=researcher.hypothesis); ai_block["critic"]=critic.to_dict()
                        if run_mode=="deep":
                            synthesis_pack={**critic_pack,"critic_assessment":critic.to_dict()}; synthesis=orchestrator.call(role="synthesis",research_run_id=run_id,question=question,case_pack=synthesis_pack,hypothesis=researcher.hypothesis); ai_block["synthesis"]=synthesis.to_dict(); recommendation=synthesis.production_recommendation
                    if researcher.hypothesis and quality.status not in {"NO_DATA","UNUSABLE"}:
                        h=researcher.hypothesis; hid="H-"+uuid.uuid4().hex[:12].upper(); row={"hypothesis_id":hid,"source_research_run":run_id,"title":h.get("title","Research hypothesis"),"statement":h.get("statement",""),"subsystem":h.get("subsystem","unknown"),"mechanism":h.get("mechanism",""),"evidence_for":researcher.evidence_for,"evidence_against":researcher.evidence_against,"sample_size":summary.count,"affected_cohorts":[],"expected_improvement":h.get("expected_improvement",""),"risks":h.get("risks",[]),"required_experiment":h.get("required_experiment",""),"status":("insufficient_evidence" if summary.sample_strength=="insufficient" else "proposed"),"identity":identity}; self.db.create_hypothesis(row); registered=row
                except BudgetExceeded as exc: ai_block["skipped_reason"]=str(exc)
                except Exception as exc: ai_block["skipped_reason"]=f"AI failed closed: {type(exc).__name__}: {exc}"
            else: ai_block["skipped_reason"]="NO_AI run mode or RND_AI_MODE=none."
            cost=vars(orchestrator.budget.state) if orchestrator else {"calls":0,"estimated_cost_usd":0.0,"input_tokens":0,"output_tokens":0}
            result={"research_run_id":run_id,"question":question,"run_mode":run_mode,"ai_mode":ai_mode,"scope":scope,"data_quality":quality.to_dict(),"summary":summary.to_dict(),"grouped_findings":grouped,"recent_vs_prior":trend,"execution_research":execution,"ai_value_research":ai_value,"observations":observations,"target_trade_found":bool(target),"comparable_case_count":len(comparables),"code_evidence":[x.to_dict() for x in snippets],"ai":ai_block,"registered_hypothesis":registered,"cost":cost,"production_recommendation":recommendation,"reproducibility":identity}
            jp,mp=self.reports.write(run_id,result); self.db.finish_run(run_id,result,json_path=str(jp),md_path=str(mp)); result["report_json_path"]=str(jp); result["report_md_path"]=str(mp); return result
        except Exception as exc:
            self.db.finish_run(run_id,{"error":f"{type(exc).__name__}:{exc}"},json_path="",md_path="",status="failed"); raise
