from __future__ import annotations
import json, tempfile, threading, time, unittest
from dataclasses import replace
from pathlib import Path
from rnd_factory.ai.budget import AIBudget, BudgetExceeded
from rnd_factory.ai.local_bridge import LocalBridgeClient
from rnd_factory.constants import RESPONSE_SCHEMA_VERSION
from rnd_factory.evidence import EvidenceBuilder
from rnd_factory.tests.helpers import make_config

class BridgeTests(unittest.TestCase):
    def _payload(self,cfg):
        eb=EvidenceBuilder(cfg); case={"question":"q","scope":{},"data_quality":{},"summary":{},"grouped_findings":{},"recent_vs_prior":{},"execution_research":{},"ai_value_research":{},"observations":[],"target_trade":None,"comparable_trades":[],"code_evidence":[],"reproducibility":{},"evidence_policy":"data"}
        return eb.build_ai_request(role="researcher",request_id="REQ1",research_run_id="RUN1",question="q",case_pack=case)
    def _valid_response(self,p):
        return {"role":"researcher","request_id":"REQ1","research_run_id":"RUN1","request_hash":p["request_hash"],"schema_version":RESPONSE_SCHEMA_VERSION,"most_likely_explanation":"x","alternative_explanations":[],"evidence_for":[],"evidence_against":[],"uncertainty":[],"more_data_needed":True,"hypothesis":None,"confidence":"low","production_recommendation":"MORE_DATA_REQUIRED","resolved_objection_codes":[]}
    def test_local_bridge_round_trip_and_archive(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=replace(make_config(Path(d),"local"),local_timeout_sec=3); cfg.ensure_directories(); p=self._payload(cfg)
            def responder():
                folder=cfg.local_request_dir/"REQ1"
                for _ in range(50):
                    if (folder/"READY").exists():break
                    time.sleep(.05)
                (cfg.local_response_dir/"REQ1.json").write_text(json.dumps(self._valid_response(p)),encoding="utf-8")
            t=threading.Thread(target=responder);t.start(); value,_=LocalBridgeClient(cfg).call(p);t.join();self.assertEqual(value["request_hash"],p["request_hash"]);self.assertTrue((cfg.local_archive_dir/"REQ1"/"response.json").exists())
    def test_local_bridge_mismatch_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=replace(make_config(Path(d),"local"),local_timeout_sec=2);cfg.ensure_directories();p=self._payload(cfg)
            def responder():
                folder=cfg.local_request_dir/"REQ1"
                while not (folder/"READY").exists():time.sleep(.02)
                bad=self._valid_response(p);bad["request_id"]="OTHER";(cfg.local_response_dir/"REQ1.json").write_text(json.dumps(bad),encoding="utf-8")
            t=threading.Thread(target=responder);t.start()
            with self.assertRaises(ValueError):LocalBridgeClient(cfg).call(p)
            t.join();self.assertTrue((cfg.local_quarantine_dir/"REQ1"/"response.json").exists())
    def test_local_bridge_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=replace(make_config(Path(d),"local"),local_timeout_sec=.15,local_poll_interval_ms=50);cfg.ensure_directories()
            with self.assertRaises(TimeoutError):LocalBridgeClient(cfg).call(self._payload(cfg))

    def test_local_bridge_stale_response_quarantined(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            cfg=replace(make_config(Path(d),"local"),local_timeout_sec=1);cfg.ensure_directories();p=self._payload(cfg)
            stale=cfg.local_response_dir/"REQ1.json"; stale.write_text(json.dumps(self._valid_response(p)),encoding="utf-8")
            old=time.time()-60; os.utime(stale,(old,old))
            with self.assertRaises(ValueError): LocalBridgeClient(cfg).call(p)
            self.assertTrue((cfg.local_quarantine_dir/"REQ1"/"response.json").exists())

    def test_local_bridge_duplicate_request_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=replace(make_config(Path(d),"local"),local_timeout_sec=.1);cfg.ensure_directories();p=self._payload(cfg)
            (cfg.local_archive_dir/"REQ1").mkdir(parents=True)
            with self.assertRaises(RuntimeError): LocalBridgeClient(cfg).call(p)

    def test_budget_limits(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=replace(make_config(Path(d),"none"),ai_mode="remote",remote_api_key="x",remote_model="m",max_ai_calls=1,max_context_tokens=1000,max_cost_usd=.001,input_usd_per_million=100,output_usd_per_million=100)
            b=AIBudget(cfg)
            with self.assertRaises(BudgetExceeded):b.preflight(900,900)
            cfg=replace(cfg,max_cost_usd=1);b=AIBudget(cfg);b.preflight(100,100);b.record(input_tokens=100,output_tokens=100)
            with self.assertRaises(BudgetExceeded):b.preflight(100,100)
if __name__=="__main__":unittest.main()
