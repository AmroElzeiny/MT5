"""Calibration spike: reconstruct real wires and compare char/pretoken proxies to
provider-reported input_tokens and cached prefix counts. Read-only."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(r"C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5")
sys.path.insert(0, str(REPO / "python"))
BUS = Path(r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS")

try:
    import regex as _re

    PRETOKEN = _re.compile(
        r"""'(?:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"""
    )
    HAVE_REGEX = True
except Exception:
    PRETOKEN = re.compile(r"'s|'t|'re|'ve|'m|'ll|'d|[A-Za-z]+|[0-9]+|[^\sA-Za-z0-9]+|\s+")
    HAVE_REGEX = False


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


def usage_index(role="analyst"):
    idx = {}
    with (BUS / "logs" / "openai_usage.ndjson").open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("model") != "muse-spark-1.3-contributor":
                continue
            if row.get("provider_mode") != "OPENCODE_API":
                continue
            if role != "analyst":
                if str((row.get("extra") or {}).get("role") or "") != role:
                    continue
            else:
                op = str(row.get("operation") or "")
                if str((row.get("extra") or {}).get("role") or "") not in ("", "analyst"):
                    continue
                if op and not op.endswith("analyst"):
                    continue
            idx[str(row.get("request_id"))] = row
    return idx


def payload_index():
    idx = {}
    for sub in ("completed", "rejected"):
        for p in (BUS / sub).glob("python_*.json"):
            if p.name.endswith(".meta.json"):
                continue
            name = p.name
            if "__" not in name:
                continue
            rid = name.split("__", 1)[1][:-5]
            idx.setdefault(rid, p)
    return idx


def main():
    import ai_gate
    from ai_provider import OpenCodeResponsesProvider

    role = sys.argv[1] if len(sys.argv) > 1 else "analyst"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    usage = usage_index(role)
    payloads = payload_index()
    common = [rid for rid in usage if rid in payloads]
    print(f"role={role} usage_rows={len(usage)} payloads={len(payloads)} common={len(common)}")

    for rid in common[:limit]:
        path = payloads[rid]
        raw = path.read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
        payload = json.loads(text)
        rec = Recorder()
        leg = OpenCodeResponsesProvider(
            base_url="https://opencode.ai/zen/go/v1", api_key="x", model="muse-spark-1.3-contributor",
            reasoning_effort="high", timeout_sec=60.0, max_output_tokens=25000,
            circuit_failure_threshold=10_000, circuit_cooldown_sec=1.0,
            log=lambda _m: None, client_factory=rec.factory,
        )
        ai_gate.LOG_FILE = None
        ai_gate._provider = lambda: leg
        ai_gate._write_ai_cost_report = lambda *a, **k: None
        from provider_deadline import RequestDeadline as _RD

        _RD.can_start_attempt = lambda self, now=None: True
        _RD.provider_timeout_sec = lambda self, configured, now=None: float(configured)
        try:
            ai_gate._score_setup_impl(payload)
        except Exception:
            pass
        if not rec.calls:
            print(rid, "NO CAPTURE")
            continue
        kw = rec.calls[0]
        instr = kw.get("instructions") or ""
        schema = json.dumps((kw.get("text") or {}).get("format", {}).get("schema", {}), separators=(",", ":"))
        inp = json.dumps(kw.get("input"), separators=(",", ":"), ensure_ascii=False)
        total_chars = len(instr) + len(schema) + len(inp)
        static_chars = len(instr) + len(schema)
        row = usage[rid]
        it, ct = int(row.get("input_tokens") or 0), int(row.get("cached_input_tokens") or 0)
        n_cand = len(payload.get("candidates") or [])
        print(f"--- {rid} cand={n_cand}")
        print(f"  provider input_tokens={it} cached={ct} output={row.get('output_tokens')} reason={row.get('reasoning_output_tokens')}")
        print(f"  chars: instr={len(instr)} schema={len(schema)} input={len(inp)} total={total_chars} static={static_chars}")
        print(f"  chars/token total={total_chars/it:.3f} static/cached={static_chars/ct if ct else 0:.3f}")
        if HAVE_REGEX:
            pt_total = len(PRETOKEN.findall(instr + schema + inp))
            print(f"  pretokens={pt_total} pretok/token={pt_total/it:.3f}")


if __name__ == "__main__":
    main()
