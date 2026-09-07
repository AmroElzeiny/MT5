from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .code_discovery import CodeDiscovery
from .config import load_config
from .database import ResearchDatabase
from .experiments import ExperimentManager
from .factory import ResearchFactory


def _print(value): print(json.dumps(value,indent=2,ensure_ascii=False,default=str))

def main(argv=None)->int:
    p=argparse.ArgumentParser(prog="python -m rnd_factory",description="Research-only Autonomous Quant R&D Factory")
    p.add_argument("--env",default=None,help="Optional R&D env file")
    sp=p.add_subparsers(dest="cmd",required=True)
    sp.add_parser("init"); sp.add_parser("validate-config")
    inv=sp.add_parser("investigate"); inv.add_argument("--question",default=""); inv.add_argument("--last-trades",type=int,default=100); inv.add_argument("--setup",default=""); inv.add_argument("--trade-key",default=""); inv.add_argument("--mode",choices=["no-ai","quick","standard","deep"],default="no-ai")
    res=sp.add_parser("research"); res.add_argument("--question",required=True); res.add_argument("--last-trades",type=int,default=250); res.add_argument("--mode",choices=["no-ai","quick","standard","deep"],default="standard")
    hyp=sp.add_parser("hypotheses"); hsp=hyp.add_subparsers(dest="action",required=True); hsp.add_parser("list"); hs=hsp.add_parser("show"); hs.add_argument("hypothesis_id"); hu=hsp.add_parser("status"); hu.add_argument("hypothesis_id"); hu.add_argument("status")
    exp=sp.add_parser("experiments"); esp=exp.add_subparsers(dest="action",required=True); esp.add_parser("list")
    ec=esp.add_parser("create"); ec.add_argument("hypothesis_id"); ec.add_argument("--variable",required=True); ec.add_argument("--control-json",required=True); ec.add_argument("--candidate-json",required=True); ec.add_argument("--training-range",required=True); ec.add_argument("--calibration-range",required=True); ec.add_argument("--validation-range",required=True); ec.add_argument("--final-holdout-range",required=True); ec.add_argument("--metrics",default="expectancy_r"); ec.add_argument("--minimum-sample",type=int,default=30); ec.add_argument("--success-json",default='{"expectancy_delta_r_min":0.1}'); ec.add_argument("--rejection-json",default='{"expectancy_delta_r_max":0.0}')
    ee=esp.add_parser("export-job"); ee.add_argument("experiment_id"); ei=esp.add_parser("ingest-result"); ei.add_argument("path")
    watch=sp.add_parser("watch"); watch.add_argument("--interval-seconds",type=float,default=3600); watch.add_argument("--cycles",type=int,default=0,help="0 means continuous"); watch.add_argument("--last-trades",type=int,default=200); watch.add_argument("--mode",choices=["no-ai","quick","standard","deep"],default="no-ai")
    src=sp.add_parser("source-index"); ssp=src.add_subparsers(dest="action",required=True); ssp.add_parser("build"); ss=ssp.add_parser("search"); ss.add_argument("query")
    args=p.parse_args(argv)
    try: cfg=load_config(args.env)
    except Exception as exc: print(f"CONFIG ERROR: {exc}",file=sys.stderr); return 2
    if args.cmd=="init": cfg.ensure_directories(); ResearchDatabase(cfg.database_path); CodeDiscovery(cfg).build_index(); _print({"status":"initialized","database":str(cfg.database_path),"reports":str(cfg.reports_dir)}); return 0
    if args.cmd=="validate-config": _print({"status":"valid","ai_mode":cfg.ai_mode,"repo_root":str(cfg.repo_root),"trade_memory":str(cfg.trade_memory_path),"local_request_dir":str(cfg.local_request_dir)}); return 0
    if args.cmd in {"investigate","research"}:
        result=ResearchFactory(cfg).investigate(question=args.question,last_trades=args.last_trades,setup=getattr(args,"setup","") ,trade_key=getattr(args,"trade_key","") ,run_mode=args.mode); _print({"research_run_id":result["research_run_id"],"report_json_path":result["report_json_path"],"report_md_path":result["report_md_path"],"production_recommendation":result["production_recommendation"],"cost":result["cost"]}); return 0
    db=ResearchDatabase(cfg.database_path)
    if args.cmd=="hypotheses":
        if args.action=="list": _print(db.hypotheses())
        elif args.action=="show": _print(db.hypothesis(args.hypothesis_id) or {"error":"not_found"})
        else: db.set_hypothesis_status(args.hypothesis_id,args.status); _print({"status":"updated"})
        return 0
    if args.cmd=="experiments":
        em=ExperimentManager(cfg,db)
        if args.action=="list": _print(db.experiments())
        elif args.action=="create":
            spec=em.create_from_hypothesis(args.hypothesis_id,variable=args.variable,control=json.loads(args.control_json),candidate=json.loads(args.candidate_json),training_range=args.training_range,calibration_range=args.calibration_range,validation_range=args.validation_range,final_holdout_range=args.final_holdout_range,metrics=[x.strip() for x in args.metrics.split(",") if x.strip()],minimum_sample=args.minimum_sample,success_criteria=json.loads(args.success_json),rejection_criteria=json.loads(args.rejection_json)); _print(spec)
        elif args.action=="export-job": _print({"path":str(em.export_job(args.experiment_id))})
        else: _print(em.ingest_result(Path(args.path)))
        return 0
    if args.cmd=="watch":
        import time
        factory=ResearchFactory(cfg); count=0
        while args.cycles==0 or count<args.cycles:
            result=factory.investigate(last_trades=args.last_trades,run_mode=args.mode,question="Automated periodic R&D observation scan")
            _print({"cycle":count+1,"research_run_id":result["research_run_id"],"report_md_path":result["report_md_path"],"recommendation":result["production_recommendation"]})
            count+=1
            if args.cycles==0 or count<args.cycles: time.sleep(max(1.0,args.interval_seconds))
        return 0
    if args.cmd=="source-index":
        cd=CodeDiscovery(cfg)
        if args.action=="build": _print(cd.build_index())
        else: _print([x.to_dict() for x in cd.search(args.query)])
        return 0
    return 1
