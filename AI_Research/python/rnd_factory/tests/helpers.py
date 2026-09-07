from __future__ import annotations
import json, sqlite3
from dataclasses import replace
from pathlib import Path
from rnd_factory.config import load_config

def make_config(tmp:Path, ai_mode:str="none"):
    env=tmp/"rnd.env"; env.write_text(f"RND_AI_MODE={ai_mode}\nRND_REQUIRE_KNOWN_PRICING=false\n",encoding="utf-8")
    base=load_config(env)
    repo=tmp/"repo"; py=repo/"python"; pkg=py/"rnd_factory"; pkg.mkdir(parents=True,exist_ok=True)
    common=tmp/"common"; common.mkdir()
    return replace(base,repo_root=repo,python_root=py,package_root=pkg,trade_memory_path=py/"data"/"ai_trade_memory.sqlite3",common_files_dir=common,database_path=pkg/"data"/"rnd.sqlite3",reports_dir=pkg/"data"/"reports",source_index_path=pkg/"data"/"source_index.json",local_request_dir=pkg/"data"/"local"/"requests",local_response_dir=pkg/"data"/"local"/"responses",local_processing_dir=pkg/"data"/"local"/"processing",local_archive_dir=pkg/"data"/"local"/"archive",local_quarantine_dir=pkg/"data"/"local"/"quarantine",experiment_pending_dir=pkg/"data"/"jobs"/"pending",experiment_results_dir=pkg/"data"/"jobs"/"results",ai_mode=ai_mode)

def seed_memory(path:Path,n:int=80):
    path.parent.mkdir(parents=True,exist_ok=True); db=sqlite3.connect(path)
    db.execute("CREATE TABLE completed_memory(memory_id TEXT PRIMARY KEY, trade_key TEXT UNIQUE, candidate_hash TEXT,lineage_id TEXT,setup_taxonomy TEXT,setup_family TEXT,entry_branch TEXT,direction TEXT,asset_class TEXT,session_code TEXT,killzone_code TEXT,regime_profile TEXT,data_quality_status TEXT,resolved_at INTEGER,schema_version TEXT,payload_json TEXT)")
    for i in range(n):
        r=1.0 if i%2==0 else -0.7
        payload={"trade_key":f"T{i}","candidate_hash":f"C{i}","setup_taxonomy":"micro_po3_reversal" if i%3 else "continuation","setup_family":"micro_po3","entry_branch":"breaker","direction":"BUY","asset_class":"FX","session_code":"NY","regime_profile":"trend" if i%4 else "range","executed_action":{"entry":1.1},"resolved_outcome":{"net_realized_r":r,"mfe_r":max(r,0.5),"mae_r":min(r,-0.2),"execution_cost":0.02},"python_final_decision":{"decision_state":"APPROVE"},"critic_output":{"verdict":"PASS"}}
        db.execute("INSERT INTO completed_memory VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(f"M{i}",f"T{i}",f"C{i}",f"L{i}",payload["setup_taxonomy"],payload["setup_family"],payload["entry_branch"],"BUY","FX","NY","KZ","trend", "CLEAN",i,"v1",json.dumps(payload)))
    db.commit();db.close()
