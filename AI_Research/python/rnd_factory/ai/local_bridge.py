from __future__ import annotations
import json
import os
import shutil
import time
import hashlib
from pathlib import Path
from typing import Any, Mapping

from ..config import FactoryConfig
from ..utils import atomic_write_json, canonical_json, ensure_dir, sha256_json, strict_json_object, utc_now

class LocalBridgeClient:
    def __init__(self, config:FactoryConfig): self.config=config
    def call(self, request_payload:Mapping[str,Any]) -> tuple[dict[str,Any],float]:
        request_id=str(request_payload["request_id"])
        bundle=self.config.local_request_dir/request_id
        if bundle.exists() or (self.config.local_archive_dir/request_id).exists() or (self.config.local_quarantine_dir/request_id).exists():
            raise RuntimeError("local_request_id_already_exists")
        tmp=self.config.local_processing_dir/(request_id+".building")
        if tmp.exists(): shutil.rmtree(tmp)
        ensure_dir(tmp/"evidence")
        compact=dict(request_payload)
        case_pack=compact.pop("case_pack",{})
        evidence_files={
            "deterministic.json":{k:case_pack.get(k) for k in ("data_quality","summary","grouped_findings","recent_vs_prior","execution_research","ai_value_research","observations")},
            "comparables.json":case_pack.get("comparable_trades",[]),
            "code_evidence.json":case_pack.get("code_evidence",[]),
            "target_trade.json":case_pack.get("target_trade"),
            "reproducibility.json":case_pack.get("reproducibility",{}),
            "role_context.json":{k:case_pack.get(k) for k in ("researcher_assessment","critic_assessment") if case_pack.get(k) is not None},
        }
        evidence_files={k:v for k,v in evidence_files.items() if v not in (None,{},[])}
        if len(evidence_files)>self.config.local_max_evidence_files:
            raise RuntimeError("local_evidence_file_limit_exceeded")
        manifest=[]
        for name,value in evidence_files.items():
            data=json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True)+"\n"
            path=tmp/"evidence"/name
            path.write_text(data,encoding="utf-8")
            manifest.append({"file":f"evidence/{name}","sha256":hashlib.sha256(data.encode("utf-8")).hexdigest(),"bytes":len(data.encode("utf-8"))})
        compact["evidence_files"]=manifest
        compact["case_pack"]={"question":case_pack.get("question"),"scope":case_pack.get("scope"),"evidence_policy":case_pack.get("evidence_policy")}
        raw=json.dumps(compact,indent=2,ensure_ascii=False,sort_keys=True)+"\n"
        if len(raw.encode("utf-8"))+sum(x["bytes"] for x in manifest)>self.config.local_max_request_bytes:
            shutil.rmtree(tmp,ignore_errors=True); raise RuntimeError("local_request_byte_limit_exceeded")
        (tmp/"request.json").write_text(raw,encoding="utf-8")
        (tmp/"READY").write_text(utc_now()+"\n",encoding="utf-8")
        os.replace(tmp,bundle)
        expected=self.config.local_response_dir/f"{request_id}.json"
        request_written_epoch=time.time(); started=time.perf_counter(); deadline=time.monotonic()+self.config.local_timeout_sec
        while time.monotonic()<deadline:
            if expected.exists():
                try:
                    stat1=expected.stat(); time.sleep(0.05); stat2=expected.stat()
                    if (stat1.st_size,stat1.st_mtime_ns)!=(stat2.st_size,stat2.st_mtime_ns): continue
                    if stat2.st_mtime < request_written_epoch - 1.0: raise ValueError("local_response_stale")
                    request_on_disk=strict_json_object((bundle/"request.json").read_text(encoding="utf-8-sig"))
                    if str(request_on_disk.get("request_hash"))!=str(request_payload.get("request_hash")): raise ValueError("local_request_bundle_hash_mismatch")
                    for item in request_on_disk.get("evidence_files",[]):
                        ev=bundle/str(item.get("file") or "")
                        if not ev.is_file(): raise ValueError("local_request_evidence_missing")
                        actual=hashlib.sha256(ev.read_bytes()).hexdigest()
                        if actual!=str(item.get("sha256") or ""): raise ValueError("local_request_evidence_hash_mismatch")
                    value=strict_json_object(expected.read_text(encoding="utf-8-sig"))
                    if str(value.get("request_id"))!=request_id: raise ValueError("local_response_request_id_mismatch")
                    if str(value.get("research_run_id"))!=str(request_payload.get("research_run_id")): raise ValueError("local_response_research_run_id_mismatch")
                    if str(value.get("request_hash"))!=str(request_payload.get("request_hash")): raise ValueError("local_response_request_hash_mismatch")
                    archive_dir=ensure_dir(self.config.local_archive_dir/request_id)
                    shutil.copytree(bundle,archive_dir/"request",dirs_exist_ok=True)
                    shutil.move(str(expected),archive_dir/"response.json")
                    shutil.rmtree(bundle,ignore_errors=True)
                    return value,time.perf_counter()-started
                except Exception as exc:
                    q=ensure_dir(self.config.local_quarantine_dir/request_id)
                    if expected.exists(): shutil.move(str(expected),q/"response.json")
                    if bundle.exists(): shutil.move(str(bundle),q/"request")
                    (q/"reason.txt").write_text(f"{type(exc).__name__}:{exc}\n",encoding="utf-8")
                    raise
            time.sleep(self.config.local_poll_interval_ms/1000.0)
        q=ensure_dir(self.config.local_quarantine_dir/request_id)
        if bundle.exists(): shutil.move(str(bundle),q/"request")
        (q/"reason.txt").write_text("TimeoutError:local_ai_response_timeout\n",encoding="utf-8")
        raise TimeoutError(f"local_ai_response_timeout:{request_id}")
