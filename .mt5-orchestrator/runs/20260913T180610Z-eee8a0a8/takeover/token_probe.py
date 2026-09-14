"""Exact provider input-token count of captured analyst wires, canonical vs compact (paid, tiny).

Sends each captured (instructions, evidence, schema) exactly as the production analyst call
does, with a minimal output ceiling: only usage.input_tokens is used.  Credentials come from
the gate's own loader (po3_env.bootstrap_provider_env); nothing is printed but token counts.
usage: python token_probe.py <safe_request_id> [...]
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_PY = Path(__file__).resolve().parents[4] / "python"
SAMPLES = HERE / "wire_samples"
OUT = HERE / "token_probe_results.jsonl"


def main(ids):
    sys.path.insert(0, str(REPO_PY))
    import po3_env
    from openai import OpenAI

    po3_env.bootstrap_provider_env()
    client = OpenAI(
        base_url=os.environ["OPENCODE_GO_BASE_URL"],
        api_key=os.environ["OPENCODE_GO_API_KEY"],
        timeout=300.0,
        max_retries=0,
    )
    model = os.environ.get("OPENCODE_MUSE_MODEL") or "muse-spark-1.3-contributor"
    for safe in ids:
        for projection in ("canonical", "compact_v1"):
            base = SAMPLES / f"{safe}__{projection}"
            instructions = Path(str(base) + "__instructions.txt").read_text(encoding="utf-8")
            text = Path(str(base) + "__input.json").read_text(encoding="utf-8")
            schema = json.loads(Path(str(base) + "__schema.json").read_text(encoding="utf-8"))
            session = "po3-token-probe-" + hashlib.sha256(f"{safe}|{projection}".encode()).hexdigest()[:24]
            started = time.time()
            row = {"request": safe, "projection": projection, "input_chars": len(text),
                   "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest()}
            try:
                resp = client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=[{"role": "user", "content": [{"type": "input_text", "text": text}]}],
                    text={"format": {"type": "json_schema", "name": "ModelAIGateOutput", "strict": True,
                                     "schema": schema}},
                    reasoning={"effort": "minimal"},
                    max_output_tokens=64,
                    store=False,
                    truncation="disabled",
                    extra_headers={"x-opencode-session": session},
                )
                usage = resp.usage
                row.update({
                    "status": getattr(resp, "status", ""),
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "cached_tokens": getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                })
            except Exception as exc:  # noqa: BLE001 - recorded, never retried
                row.update({"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            row["latency_sec"] = round(time.time() - started, 2)
            with OUT.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
