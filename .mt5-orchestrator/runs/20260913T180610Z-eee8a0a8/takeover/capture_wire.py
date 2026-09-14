"""Offline capture of the exact analyst provider wire for archived requests (no network).

usage: python capture_wire.py <projection> <request_id_or_suffix> [...]
Each request is captured in a fresh subprocess (WP2 recipe); outputs go to takeover/wire_samples/.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_PY = Path(__file__).resolve().parents[4] / "python"
RUN = HERE.parent
OUT = HERE / "wire_samples"


def _rows():
    manifest = json.loads((RUN / "benchmark_manifest.json").read_text(encoding="utf-8"))
    return manifest["rows"]


def child(projection: str, request_id: str) -> int:
    sys.path.insert(0, str(REPO_PY))
    sys.path.insert(0, str(REPO_PY / "tools"))
    import opencode_reasoning_benchmark as bench

    safe = bench.safe_component(request_id)
    scratch = OUT / f"_scratch_{safe}_{projection}"
    bench.configure_child_env(scratch, mode="dry", bus=Path(bench.DEFAULT_BUS))
    os.environ[bench.WIRE_PROJECTION_ENV] = projection
    import ai_gate

    bench.neutralize_inprocess_writers(ai_gate)
    fake = bench.DryFakeClient()
    leg = bench.build_arm_leg("muse_high_a", ai_gate.AI_CONFIG, mode="dry", client_factory=fake.factory)
    ai_gate._provider = lambda *_a, **_k: leg
    row = next(r for r in _rows() if r["request_id"] == request_id)
    payload = ai_gate.read_json_any_encoding(Path(row["archive_path"]))
    sealed = ai_gate._apply_live_candidate_budget(dict(payload))
    try:
        ai_gate._score_setup_impl(dict(sealed))
    except BaseException:  # noqa: BLE001 - the dry fake always fails the call
        pass
    if not fake.calls:
        print(f"[capture] {request_id} no provider call reached")
        return 2
    kw = fake.calls[0]
    text = kw["input"][0]["content"][0]["text"]
    base = OUT / f"{safe}__{projection}"
    (Path(str(base) + "__input.json")).write_text(text, encoding="utf-8")
    (Path(str(base) + "__instructions.txt")).write_text(kw["instructions"], encoding="utf-8")
    (Path(str(base) + "__schema.json")).write_text(
        json.dumps(kw["text"]["format"]["schema"], sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    print(f"[capture] {request_id} input_chars={len(text)} instructions_chars={len(kw['instructions'])}")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "_child":
        return child(sys.argv[2], sys.argv[3])
    projection = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = sys.argv[2:]
    ids = [r["request_id"] for r in _rows() if any(r["request_id"] == w or r["request_id"].endswith(w) for w in wanted)]
    rc = 0
    for request_id in ids:
        proc = subprocess.run([sys.executable, __file__, "_child", projection, request_id],
                              cwd=str(REPO_PY), capture_output=True, text=True)
        tail = (proc.stdout or "").strip().splitlines()[-1:] or (proc.stderr or "").strip().splitlines()[-3:]
        print("\n".join(tail))
        rc = rc or proc.returncode
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
