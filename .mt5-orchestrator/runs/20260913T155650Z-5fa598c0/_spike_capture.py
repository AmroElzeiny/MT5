"""Feasibility spike: capture the real provider wire kwargs for an archived request.

Read-only. Monkeypatches ai_gate in-process only. No product file changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5")
sys.path.insert(0, str(REPO / "python"))

BUS = Path(r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS")


class _Stop(Exception):
    pass


class Recorder:
    def __init__(self):
        self.calls = []

    def factory(self, **kwargs):
        outer = self

        class _Resp:
            def create(self, **kw):
                outer.calls.append(dict(kw))
                raise _Stop("captured")

        class _Client:
            responses = _Resp()

            @staticmethod
            def with_options(**opts):
                return _Client

        return _Client


def main():
    payload_path = Path(sys.argv[1])
    raw = payload_path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    payload = json.loads(text)
    print("payload id:", payload.get("id"), "workload_mode:", payload.get("workload_mode"),
          "candidates:", len(payload.get("candidates") or []))

    import ai_gate
    from ai_provider import OpenCodeResponsesProvider

    rec = Recorder()
    leg = OpenCodeResponsesProvider(
        base_url="https://opencode.ai/zen/go/v1",
        api_key="not-a-real-key",
        model="muse-spark-1.3-contributor",
        reasoning_effort="high",
        timeout_sec=60.0,
        max_output_tokens=25000,
        circuit_failure_threshold=10_000,
        circuit_cooldown_sec=1.0,
        log=lambda _m: None,
        client_factory=rec.factory,
    )
    # Neutralize side effects.
    ai_gate.LOG_FILE = None
    ai_gate._provider = lambda: leg
    ai_gate._write_ai_cost_report = lambda *a, **k: None
    import time as _time

    ai_gate._request_deadline_for = lambda _p: _time.time() + 3600.0

    try:
        decision = ai_gate._score_setup_impl(payload)
        print("decision:", type(decision).__name__, getattr(decision, "decision_source", None))
    except Exception as exc:  # noqa: BLE001
        print("raised:", type(exc).__name__, str(exc)[:300])

    print("captured calls:", len(rec.calls))
    if rec.calls:
        kw = rec.calls[0]
        print("keys:", sorted(kw.keys()))
        instr = kw.get("instructions")
        print("instructions chars:", len(instr) if isinstance(instr, str) else None)
        inp = kw.get("input")
        print("input type:", type(inp).__name__, "len:", len(json.dumps(inp)) if inp is not None else None)
        fmt = (kw.get("text") or {}).get("format") if isinstance(kw.get("text"), dict) else None
        print("schema name:", (fmt or {}).get("name"))
        print("reasoning:", kw.get("reasoning"))
        print("max_output_tokens:", kw.get("max_output_tokens"))
        print("truncation:", kw.get("truncation"))
        print("headers:", kw.get("extra_headers"))


if __name__ == "__main__":
    main()
