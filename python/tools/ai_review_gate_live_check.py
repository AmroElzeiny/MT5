"""Real-provider end-to-end check of the AI review gate (QA traffic, isolated).

Runs one archived LIVE_FORWARD request twice through the production bus
processor ``ai_gate.process_one`` on a scratch bus:

  A. the request as MQL would now send it (with ``ai_review_context``)
     -> the gate has no prior, so the real provider panel runs and the
        authoritative decision becomes the prior;
  B. the same market state one scan (10 minutes) later
     -> if A was a decisive non-approval, the gate reuses it: zero provider
        calls and an identity-bound RULE_ONLY_NON_TRADING response.

Isolation reuses the stage-C benchmark recipe: every writer (decision cache,
trade memory, cost report, fingerprints, repeatability artifact, shadow ledger,
bus, review state) points into the scratch directory, re-applied after the
credential loader, and the run refuses to start if any still points outside.
No MT5 terminal, no live bus, no order path is touched.  Provider traffic is
labelled AI_TRAFFIC_CLASS=QA.

``--provider production`` uses the configured selection (OpenCode Muse primary);
``--provider luna`` injects the GPT-5.6 Luna low/flex leg only, without changing
any production setting.  Luna token counts are never used as Muse economics.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

REPO_PYTHON = Path(__file__).resolve().parents[1]
if str(REPO_PYTHON) not in sys.path:
    sys.path.insert(0, str(REPO_PYTHON))
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _isolate(scratch: Path) -> None:
    from opencode_reasoning_benchmark import configure_child_env

    configure_child_env(scratch, mode="live", bus=scratch / "bus")
    os.environ["AI_REVIEW_GATE_MODE"] = "enforce"
    os.environ["AI_REVIEW_STATE_FILE"] = str(scratch / "ai_review_state.json")
    os.environ["AI_TRAFFIC_CLASS"] = "QA"


def _request_variant(payload: dict, shift_sec: int) -> dict:
    variant = copy.deepcopy(payload)
    parts = str(variant["id"]).split("_")
    parts[3] = str(int(parts[3]) + shift_sec)
    variant["id"] = "_".join(parts)
    variant["request_created_sim_time"] = int(variant["request_created_sim_time"]) + shift_sec
    variant["request_created_wall_time"] = int(variant.get("request_created_wall_time") or 0) + shift_sec * 1000
    variant["ai_review_context"]["server_time"] = variant["request_created_sim_time"]
    for key in ("request_identity_hash", "ordered_candidate_identities", "candidate_count", "request_identity"):
        variant.pop(key, None)
    return variant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--provider", choices=("production", "luna"), default="production")
    parser.add_argument("--offset-hours", type=int, default=3)
    args = parser.parse_args(argv)

    scratch = args.scratch.resolve()
    (scratch / "bus" / "requests").mkdir(parents=True, exist_ok=True)
    (scratch / "bus" / "responses").mkdir(parents=True, exist_ok=True)
    (scratch / "bus" / "stale").mkdir(parents=True, exist_ok=True)
    _isolate(scratch)
    import po3_env

    po3_env.bootstrap_provider_env()
    _isolate(scratch)  # the credential loader re-applies .env with override=True

    import ai_gate
    from opencode_reasoning_benchmark import child_isolation_violations, neutralize_inprocess_writers
    from ai_call_efficiency_replay import historical_killzone_code, historical_session_code

    # ai_gate's own import runs the credential bootstrap again (override=True),
    # so every module-level object built from the environment is rebuilt after
    # the isolation is re-applied, and only then checked.
    _isolate(scratch)
    ai_gate.AI_CONFIG = ai_gate.AIGateRuntimeConfig.from_env()
    ai_gate.AI_DECISION_CACHE = ai_gate.AIDecisionCache(
        ai_gate.AI_CONFIG.decision_cache_file, ai_gate.AI_CONFIG.decision_cache_ttl_sec
    )
    ai_gate.AI_REVIEW_GATE = ai_gate.ReviewGate(
        ai_gate.ReviewGateConfig.from_env(os.environ, resolve_path=ai_gate.resolve_project_path),
        logger=lambda message: ai_gate.log(message),
    )
    ai_gate.AI_PROVIDER = None
    violations = child_isolation_violations(ai_gate.AI_CONFIG, scratch)
    state_file = ai_gate.AI_REVIEW_GATE.config.state_file
    if state_file is None or scratch not in Path(state_file).resolve().parents:
        violations.append("ai_review_state_file")
    if violations:
        print(json.dumps({"refused": "isolation_guard", "violations": violations}))
        return 2

    log_lines: list[str] = []
    neutralize_inprocess_writers(ai_gate)
    ai_gate.log = log_lines.append

    if args.provider == "luna":
        from opencode_reasoning_benchmark import build_arm_leg

        leg = build_arm_leg("luna_low_flex", ai_gate.AI_CONFIG, mode="live")
        ai_gate._provider = lambda *_a, **_k: leg
    provider = ai_gate._provider()
    calls: list[str] = []
    original = provider.generate_structured

    def counting(**kwargs):
        calls.append(str(kwargs.get("role") or ""))
        return original(**kwargs)

    provider.generate_structured = counting

    payload = ai_gate.read_json_any_encoding(args.payload)
    server_time = int(payload["request_created_sim_time"])
    runtime_inputs = payload.get("runtime_inputs") or {}
    payload["ai_review_context"] = {
        "context_version": "20260914_ai_review_context_v1",
        "server_time": server_time,
        "utc_offset_sec": args.offset_hours * 3600,
        "session_name": "",
        "session_code": historical_session_code(server_time, args.offset_hours),
        "killzone_code": historical_killzone_code(server_time, runtime_inputs, args.offset_hours),
        "scan_interval_min": 10,
    }
    for key in ("request_identity_hash", "ordered_candidate_identities", "candidate_count", "request_identity"):
        payload.pop(key, None)

    bus = scratch / "bus"
    runs = []
    for label, document in (("A_first_scan", payload), ("B_next_scan_unchanged", _request_variant(payload, 600))):
        before = len(calls)
        req_path = bus / "requests" / f"{document['id']}.json"
        req_path.write_text(json.dumps(document), encoding="utf-8")
        started = time.time()
        error = ""
        try:
            ai_gate.process_one(req_path, bus / "responses", bus / "stale")
        except Exception as exc:  # recorded, never hidden
            error = f"{type(exc).__name__}:{exc}"
        response_path = bus / "responses" / f"{document['id']}.json"
        response = ai_gate.read_json_any_encoding(response_path) if response_path.is_file() else {}
        runs.append({
            "label": label,
            "request_id": document["id"],
            "provider_calls": calls[before:],
            "elapsed_sec": round(time.time() - started, 1),
            "error": error,
            "response_written": bool(response),
            "decision_quality_tier": response.get("decision_quality_tier"),
            "decision_source": response.get("decision_source"),
            "decision_state": response.get("decision_state"),
            "python_final_allow": response.get("python_final_allow"),
            "rejection_codes": response.get("rejection_codes"),
            "request_identity_hash": response.get("request_identity_hash"),
            "actual_model_id": response.get("actual_model_id"),
            "candidate_assessments": len(response.get("candidate_assessments") or []),
            "gate_lines": [line for line in log_lines if line.startswith(("[ai_review_gate", "[identity_validation]", "[ai_schema_validation]", "[response_written]", "[final_response_validation]", "[provider_call_completed]"))][-12:],
        })
        log_lines.clear()
    summary = {
        "provider_mode": args.provider,
        "configured_model": getattr(provider, "model_for_role", lambda r: "")("analyst"),
        "traffic_class": os.environ.get("AI_TRAFFIC_CLASS"),
        "review_state_file": str(state_file),
        "runs": runs,
        "gate_counters": dict(ai_gate.AI_REVIEW_GATE.counters),
    }
    (scratch / "live_check_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
