from __future__ import annotations
import tempfile,unittest
from dataclasses import replace
from pathlib import Path
from rnd_factory.config import load_config
from rnd_factory.models import AIResearchResult
from rnd_factory.reports import ReportWriter
from rnd_factory.tests.helpers import make_config

class MiscTests(unittest.TestCase):
    def test_remote_config_requires_key(self):
        with tempfile.TemporaryDirectory() as d:
            env=Path(d)/"x.env";env.write_text("RND_AI_MODE=remote\nRND_REQUIRE_KNOWN_PRICING=false\nRND_REMOTE_MODEL=test\n",encoding="utf-8")
            with self.assertRaises(ValueError):load_config(env)
    def test_ai_response_identity_validation(self):
        base={"role":"researcher","request_id":"A","research_run_id":"R","request_hash":"H","schema_version":"20260810_rnd_ai_response_v1","most_likely_explanation":"x","alternative_explanations":[],"evidence_for":[],"evidence_against":[],"uncertainty":[],"more_data_needed":False,"hypothesis":None,"confidence":"medium","production_recommendation":"NO_ACTION"}
        AIResearchResult.validate(base,expected_role="researcher",request_id="A",research_run_id="R",request_hash="H")
        base["request_hash"]="BAD"
        with self.assertRaises(ValueError):AIResearchResult.validate(base,expected_role="researcher",request_id="A",research_run_id="R",request_hash="H")
    def test_report_generation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=make_config(Path(d));cfg.ensure_directories();r={"research_run_id":"R","question":"q","run_mode":"no-ai","ai_mode":"none","data_quality":{"status":"CLEAN","usable_records":10,"total_records":10},"summary":{"count":10,"win_rate":.5,"expectancy_r":.1,"median_r":.1,"profit_factor":1.2,"sample_strength":"insufficient"},"observations":[],"execution_research":{},"ai_value_research":{},"ai":{},"cost":{"calls":0,"estimated_cost_usd":0},"production_recommendation":"MORE_DATA_REQUIRED"}
            jp,mp=ReportWriter(cfg).write("R",r);self.assertTrue(jp.exists());self.assertIn("Production Recommendation",mp.read_text())
if __name__=="__main__":unittest.main()
