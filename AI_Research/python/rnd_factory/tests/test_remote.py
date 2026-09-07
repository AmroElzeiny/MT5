from __future__ import annotations
import json, tempfile, threading, unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from rnd_factory.ai.remote import RemoteAIClient
from rnd_factory.constants import RESPONSE_SCHEMA_VERSION
from rnd_factory.tests.helpers import make_config

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); body=json.loads(self.rfile.read(n)); req=json.loads(body['input'][0]['content'][0]['text'])
        value={"role":req["role"],"request_id":req["request_id"],"research_run_id":req["research_run_id"],"request_hash":req["request_hash"],"schema_version":RESPONSE_SCHEMA_VERSION,"most_likely_explanation":"test","alternative_explanations":[],"evidence_for":[],"evidence_against":[],"uncertainty":[],"more_data_needed":False,"hypothesis":None,"confidence":"medium","production_recommendation":"NO_ACTION","resolved_objection_codes":[]}
        payload={"model":"test-model","output_text":json.dumps(value),"usage":{"input_tokens":123,"output_tokens":45,"total_tokens":168}}
        data=json.dumps(payload).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self,*args): pass

class RemoteTests(unittest.TestCase):
    def test_remote_responses_transport(self):
        server=HTTPServer(('127.0.0.1',0),Handler); t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg=replace(make_config(Path(d),'none'),ai_mode='remote',remote_base_url=f'http://127.0.0.1:{server.server_port}',remote_api_key='x',remote_model='test-model',require_known_pricing=False)
                request={"role":"researcher","request_id":"A","research_run_id":"R","request_hash":"H"}
                rr=RemoteAIClient(cfg).call(request,role='researcher',model='test-model')
                self.assertEqual(rr.value['request_id'],'A'); self.assertEqual(rr.input_tokens,123); self.assertEqual(rr.output_tokens,45)
        finally: server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
