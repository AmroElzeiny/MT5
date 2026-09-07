from __future__ import annotations
import importlib
import json
import uuid
from pathlib import Path
from typing import Any, Mapping

from .config import FactoryConfig
from .database import ResearchDatabase
from .utils import atomic_write_json, git_identity, sha256_json, utc_now

class ExperimentManager:
    def __init__(self, config:FactoryConfig, db:ResearchDatabase): self.config=config; self.db=db
    def create_from_hypothesis(self, hypothesis_id:str, *, variable:str, control:Mapping[str,Any], candidate:Mapping[str,Any], training_range:str, calibration_range:str, validation_range:str, final_holdout_range:str, metrics:list[str], minimum_sample:int, success_criteria:Mapping[str,Any], rejection_criteria:Mapping[str,Any]) -> dict[str,Any]:
        hypothesis=self.db.hypothesis(hypothesis_id)
        if hypothesis is None: raise KeyError("hypothesis_not_found")
        if not final_holdout_range.strip(): raise ValueError("final_holdout_required")
        holdout_key=sha256_json({"range":final_holdout_range})
        if self._existing_registry_contaminated(final_holdout_range) or self.db.period_inspected(holdout_key):
            raise ValueError("final_holdout_contaminated")
        exp_id="EXP-"+uuid.uuid4().hex[:12].upper(); git=git_identity(self.config.repo_root)
        spec={
            "experiment_id":exp_id,"hypothesis_id":hypothesis_id,"created_at":utc_now(),"variable":variable,
            "control_configuration":dict(control),"candidate_configuration":dict(candidate),
            "training_range":training_range,"calibration_range":calibration_range,"validation_range":validation_range,
            "final_untouched_holdout_range":final_holdout_range,"evaluation_metrics":metrics,"minimum_sample":int(minimum_sample),
            "success_criteria":dict(success_criteria),"rejection_criteria":dict(rejection_criteria),
            "overfitting_controls":["chronological split","untouched final holdout","single registered hypothesis","no production promotion"],
            "source_commit":git["commit"],"source_dirty_tree":git["dirty"],"control_hash":sha256_json(control),"candidate_hash":sha256_json(candidate),
            "execution_authority":"RESEARCH_ONLY",
        }
        self.db.create_experiment(exp_id,hypothesis_id,spec)
        return spec
    def export_job(self, experiment_id:str) -> Path:
        exp=next((x for x in self.db.experiments() if x["experiment_id"]==experiment_id),None)
        if exp is None: raise KeyError("experiment_not_found")
        job={"job_schema_version":"20260810_rnd_experiment_job_v1","created_at":utc_now(),"experiment":exp,"required_result":{"experiment_id":experiment_id,"status":"completed|failed","metrics":"object","artifacts":"array","source_hashes":"object"},"authority":"RESEARCH_ONLY_NO_LIVE_WRITE"}
        job["job_hash"]=sha256_json(job)
        path=self.config.experiment_pending_dir/f"{experiment_id}.json"; atomic_write_json(path,job); return path
    def ingest_result(self, path:Path) -> dict[str,Any]:
        value=json.loads(path.read_text(encoding="utf-8-sig")); exp_id=str(value.get("experiment_id") or "")
        if not exp_id: raise ValueError("experiment_result_missing_id")
        status=str(value.get("status") or "").lower()
        if status not in {"completed","failed"}: raise ValueError("experiment_result_invalid_status")
        if not isinstance(value.get("metrics"),dict): raise ValueError("experiment_result_missing_metrics")
        self.db.record_experiment_result(exp_id,value,status)
        exp=next((x for x in self.db.experiments() if x["experiment_id"]==exp_id),None)
        if exp:
            holdout=str(exp["spec"].get("final_untouched_holdout_range") or "")
            if holdout: self.db.mark_period_inspected(sha256_json({"range":holdout}),exp_id,"experiment_result_ingested")
        return value
    def _existing_registry_contaminated(self, final_holdout_range:str) -> bool:
        try:
            module=importlib.import_module("experiment_registry")
            registry=module.ExperimentRegistry(self.config.existing_experiment_registry_path)
            status=registry.holdout_status(final_holdout_range)
            return not bool(status.untouched)
        except Exception:
            return False
