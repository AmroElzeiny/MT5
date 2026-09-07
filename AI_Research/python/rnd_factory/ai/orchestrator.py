from __future__ import annotations
import json
import time
import uuid
from typing import Any, Mapping

from ..config import FactoryConfig
from ..database import ResearchDatabase
from ..evidence import EvidenceBuilder
from ..models import AIResearchResult
from ..utils import canonical_json
from .budget import AIBudget, BudgetExceeded, estimate_tokens_from_text
from .local_bridge import LocalBridgeClient
from .remote import RemoteAIClient

class AIOrchestrator:
    def __init__(self, config:FactoryConfig, db:ResearchDatabase):
        self.config=config; self.db=db; self.budget=AIBudget(config); self.evidence=EvidenceBuilder(config)
        self.remote=RemoteAIClient(config) if config.ai_mode=="remote" else None
        self.local=LocalBridgeClient(config) if config.ai_mode=="local" else None
    def call(self, *, role:str, research_run_id:str, question:str, case_pack:Mapping[str,Any], hypothesis:Mapping[str,Any]|None=None, experiment_id:str="") -> AIResearchResult:
        if self.config.ai_mode=="none": raise RuntimeError("ai_mode_none")
        request_id=f"rnd-{uuid.uuid4().hex}"
        payload=self.evidence.build_ai_request(role=role,request_id=request_id,research_run_id=research_run_id,question=question,case_pack=case_pack,hypothesis=hypothesis,experiment_id=experiment_id)
        input_tokens=estimate_tokens_from_text(canonical_json(payload)); self.budget.preflight(input_tokens,self.config.max_output_tokens)
        started=time.perf_counter(); status="ok"; err=""; model="local-file-bridge"; out_tokens=0; cached=0
        try:
            if self.config.ai_mode=="remote":
                model=self._model(role); rr=self.remote.call(payload,role=role,model=model); value=rr.value
                input_tokens=rr.input_tokens or input_tokens; out_tokens=rr.output_tokens; cached=rr.cached_input_tokens; latency=rr.latency_sec
            else:
                value,latency=self.local.call(payload); out_tokens=estimate_tokens_from_text(canonical_json(value))
            result=AIResearchResult.validate(value,expected_role=role,request_id=request_id,research_run_id=research_run_id,request_hash=str(payload["request_hash"]))
            self.budget.record(input_tokens=input_tokens,output_tokens=out_tokens,cached_input_tokens=cached)
            return result
        except Exception as exc:
            status="error"; err=f"{type(exc).__name__}:{exc}"; latency=time.perf_counter()-started; raise
        finally:
            cost=(max(0,input_tokens-cached)*self.config.input_usd_per_million+cached*self.config.cached_input_usd_per_million+out_tokens*self.config.output_usd_per_million)/1_000_000.0 if self.config.ai_mode=="remote" else 0.0
            self.db.log_ai_call({"call_id":uuid.uuid4().hex,"research_run_id":research_run_id,"role":role,"provider":self.config.ai_mode,"model":model,"input_tokens":input_tokens,"cached_input_tokens":cached,"output_tokens":out_tokens,"estimated_cost_usd":cost,"latency_sec":latency,"status":status,"error":err})
            self._log_existing_usage(research_run_id, role, model, input_tokens, cached, out_tokens, latency, status, err)

    def _log_existing_usage(self, research_run_id:str, role:str, model:str, input_tokens:int, cached:int, output_tokens:int, latency:float, status:str, error:str) -> None:
        if not self.config.compat_usage_log_enable:
            return
        try:
            from openai_usage_logger import log_ai_usage
            response={"usage":{"input_tokens":input_tokens,"output_tokens":output_tokens,"total_tokens":input_tokens+output_tokens,"input_tokens_details":{"cached_tokens":cached}}}
            log_ai_usage(source="rnd_factory",operation=f"research_{role}",model=model,response=response,status=status,request_id=research_run_id,error=(error or None),provider_mode=("REMOTE_API" if self.config.ai_mode=="remote" else "LOCAL_FILE_BRIDGE"),provider_id=("rnd_remote" if self.config.ai_mode=="remote" else "rnd_local_bridge"),endpoint_class=("remote" if self.config.ai_mode=="remote" else "loopback-file-bridge"),tokens_per_second=None,estimated_context_tokens=input_tokens)
        except Exception:
            # The Factory's own SQLite ledger is authoritative for R&D usage; the legacy logger is best-effort compatibility.
            return

    def _model(self,role:str)->str:
        if role=="critic" and self.config.remote_critic_model:return self.config.remote_critic_model
        if role=="synthesis" and self.config.remote_synthesis_model:return self.config.remote_synthesis_model
        return self.config.remote_model
