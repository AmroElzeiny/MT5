from __future__ import annotations
import json, tempfile, unittest
from dataclasses import replace
from pathlib import Path
from rnd_factory.analysis import DeterministicAnalyzer
from rnd_factory.code_discovery import CodeDiscovery
from rnd_factory.database import ResearchDatabase
from rnd_factory.experiments import ExperimentManager
from rnd_factory.factory import ResearchFactory
from rnd_factory.redaction import redact_text
from rnd_factory.repository import MT5RepositoryAdapter
from rnd_factory.tests.helpers import make_config, seed_memory

class CoreTests(unittest.TestCase):
    def test_deterministic_analysis_and_repo_adapter(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=make_config(Path(d)); cfg.ensure_directories(); seed_memory(cfg.trade_memory_path,120)
            rows=MT5RepositoryAdapter(cfg).load_completed_trades(limit=100); self.assertEqual(len(rows),100)
            a=DeterministicAnalyzer(cfg); q=a.data_quality(rows); s=a.summarize(rows)
            self.assertEqual(q.status,"CLEAN"); self.assertEqual(s.count,100); self.assertIsNotNone(s.expectancy_ci95)
    def test_code_discovery_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=make_config(Path(d)); f=cfg.repo_root/"MT5_PO3_Codex Include"/"TradeEngine.mqh"; f.parent.mkdir(parents=True); f.write_text("bool Test(){\n // ai_chose_infeasible_target\n return false;\n}\n",encoding="utf-8")
            items=CodeDiscovery(cfg).search("ai_chose_infeasible_target"); self.assertTrue(items); self.assertIn("TradeEngine.mqh",items[0].path)
    def test_redaction(self):
        self.assertNotIn("secret123",redact_text("API_KEY=secret123"))
        self.assertIn("REDACTED",redact_text("sk-abcdefghijklmnopqrstuv"))
    def test_hypothesis_and_experiment_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=make_config(Path(d)); cfg.ensure_directories(); db=ResearchDatabase(cfg.database_path)
            h={"hypothesis_id":"H-X","source_research_run":"R-X","title":"Late entry","statement":"Late entries reduce R","subsystem":"execution","mechanism":"adverse price movement","evidence_for":[],"evidence_against":[],"sample_size":50,"affected_cohorts":[],"expected_improvement":"higher expectancy","risks":[],"required_experiment":"compare delay buckets","status":"proposed","identity":{}}
            db.create_hypothesis(h); db.set_hypothesis_status("H-X","approved_for_experiment")
            em=ExperimentManager(cfg,db); spec=em.create_from_hypothesis("H-X",variable="delay",control={"max_delay":60},candidate={"max_delay":10},training_range="2025-01/2025-06",calibration_range="2025-07",validation_range="2025-08",final_holdout_range="2025-09",metrics=["expectancy_r"],minimum_sample=30,success_criteria={"delta_r":0.1},rejection_criteria={"delta_r":0})
            p=em.export_job(spec["experiment_id"]); self.assertTrue(p.exists()); self.assertEqual(json.loads(p.read_text())["authority"],"RESEARCH_ONLY_NO_LIVE_WRITE")
            with self.assertRaises(ValueError): em.create_from_hypothesis("H-X",variable="x",control={},candidate={},training_range="a",calibration_range="b",validation_range="c",final_holdout_range="",metrics=[],minimum_sample=1,success_criteria={},rejection_criteria={})
            db.mark_period_inspected(__import__("rnd_factory.utils",fromlist=["sha256_json"]).sha256_json({"range":"2026-01"}),"R-X","test")
            with self.assertRaises(ValueError): em.create_from_hypothesis("H-X",variable="x",control={},candidate={},training_range="a",calibration_range="b",validation_range="c",final_holdout_range="2026-01",metrics=[],minimum_sample=1,success_criteria={},rejection_criteria={})
    def test_no_ai_factory_and_no_production_write(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=make_config(Path(d)); cfg.ensure_directories(); seed_memory(cfg.trade_memory_path,60)
            protected=cfg.repo_root/"MT5_PO3_Codex Include"/"TradeEngine.mqh"; protected.parent.mkdir(parents=True); protected.write_text("ORIGINAL",encoding="utf-8")
            before=protected.read_bytes(); result=ResearchFactory(cfg).investigate(last_trades=50,run_mode="no-ai")
            self.assertEqual(before,protected.read_bytes()); self.assertTrue(Path(result["report_json_path"]).exists()); self.assertEqual(result["cost"]["calls"],0)
if __name__=="__main__":unittest.main()
