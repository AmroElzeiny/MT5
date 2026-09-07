from __future__ import annotations
import json, tempfile, threading, time, unittest
from dataclasses import replace
from pathlib import Path
from rnd_factory.constants import RESPONSE_SCHEMA_VERSION
from rnd_factory.factory import ResearchFactory
from rnd_factory.tests.helpers import make_config, seed_memory

class FactoryAIModeTests(unittest.TestCase):
    def test_full_factory_local_quick(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=replace(make_config(Path(d),'local'),local_timeout_sec=3,max_ai_calls=1);cfg.ensure_directories();seed_memory(cfg.trade_memory_path,50)
            def responder():
                bundle=None
                for _ in range(100):
                    ready=list(cfg.local_request_dir.glob('*/READY'))
                    if ready:
                        bundle=ready[0].parent;break
                    time.sleep(.03)
                self.assertIsNotNone(bundle)
                req=json.loads((bundle/'request.json').read_text(encoding='utf-8'))
                value={"role":req["role"],"request_id":req["request_id"],"research_run_id":req["research_run_id"],"request_hash":req["request_hash"],"schema_version":RESPONSE_SCHEMA_VERSION,"most_likely_explanation":"No material anomaly in supplied sample.","alternative_explanations":[],"evidence_for":["Positive deterministic expectancy"],"evidence_against":[],"uncertainty":["Synthetic fixture"],"more_data_needed":False,"hypothesis":None,"confidence":"medium","production_recommendation":"NO_ACTION","resolved_objection_codes":[]}
                (cfg.local_response_dir/f"{req['request_id']}.json").write_text(json.dumps(value),encoding='utf-8')
            t=threading.Thread(target=responder);t.start();result=ResearchFactory(cfg).investigate(last_trades=40,run_mode='quick');t.join()
            self.assertEqual(result['cost']['calls'],1);self.assertIn('researcher',result['ai']);self.assertEqual(result['production_recommendation'],'NO_ACTION')

if __name__=='__main__': unittest.main()
