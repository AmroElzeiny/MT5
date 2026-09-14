"""Apply the stage-B call-site coupling edits atomically (children import these files mid-run)."""
import os
import py_compile
import sys
import tempfile
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[4] / "python"

PHASE1_SENTENCE = (
    "The payload contains evidence_catalog.items, where each item has id, p (the Python-owned "
    "canonical path), v (the observed value), and optionally c (the candidate index it belongs "
    "to). Items without c are global."
)

AI_GATE_EDITS = [
    (
        "    opencode_session_scope: str = OPENCODE_SESSION_SCOPE_PROMPT_PREFIX\n"
        "    validation_warnings: tuple[str, ...] = ()\n",
        "    opencode_session_scope: str = OPENCODE_SESSION_SCOPE_PROMPT_PREFIX\n"
        "    # Provider-wire ENCODING of the evidence payload (provider_wire_projection.py):\n"
        "    # ``canonical`` is the Phase-1 wire byte for byte, ``compact_v1`` the lossless\n"
        "    # grouped catalog encoding.  It cannot change which evidence is sent, how an\n"
        "    # answer is validated, which calls are made, or any identity or cache key.\n"
        "    provider_wire_projection: str = WIRE_PROJECTION_CANONICAL\n"
        "    validation_warnings: tuple[str, ...] = ()\n",
    ),
    (
        "        if opencode_session_scope not in OPENCODE_SESSION_SCOPES:\n"
        "            warnings.append(\"OPENCODE_SESSION_SCOPE=invalid\")\n"
        "            opencode_session_scope = OPENCODE_SESSION_SCOPE_PROMPT_PREFIX\n",
        "        if opencode_session_scope not in OPENCODE_SESSION_SCOPES:\n"
        "            warnings.append(\"OPENCODE_SESSION_SCOPE=invalid\")\n"
        "            opencode_session_scope = OPENCODE_SESSION_SCOPE_PROMPT_PREFIX\n"
        "        # Encoding only, same posture as the session scope above: an invalid value\n"
        "        # is warned and replaced by the Phase-1 canonical wire.\n"
        "        provider_wire_projection = normalize_wire_projection(env.get(WIRE_PROJECTION_ENV))\n"
        "        if provider_wire_projection is None:\n"
        "            warnings.append(f\"{WIRE_PROJECTION_ENV}=invalid\")\n"
        "            provider_wire_projection = WIRE_PROJECTION_CANONICAL\n",
    ),
    (
        "            opencode_session_scope=opencode_session_scope,\n",
        "            opencode_session_scope=opencode_session_scope,\n"
        "            provider_wire_projection=provider_wire_projection,\n",
    ),
    (
        PHASE1_SENTENCE,
        "{analyst_catalog_description(AI_CONFIG.provider_wire_projection)}",
    ),
    (
        "                    system_prompt=system_msg,\n"
        "                    evidence=model_payload,\n",
        "                    system_prompt=system_msg,\n"
        "                    evidence=project_evidence_for_wire(model_payload, AI_CONFIG.provider_wire_projection),\n",
    ),
    (
        "                        system_prompt=repair_prompt,\n"
        "                        evidence=model_payload,\n",
        "                        system_prompt=repair_prompt,\n"
        "                        evidence=project_evidence_for_wire(model_payload, AI_CONFIG.provider_wire_projection),\n",
    ),
    (
        "                                system_prompt=repair_prompt,\n"
        "                                evidence=model_payload,\n",
        "                                system_prompt=repair_prompt,\n"
        "                                evidence=project_evidence_for_wire(model_payload, AI_CONFIG.provider_wire_projection),\n",
    ),
    (
        "                        \"request_id\": request_id,\n"
        "                        \"symbol\": symbol,\n"
        "                        \"non_trading_shadow\": bool(\n",
        "                        \"request_id\": request_id,\n"
        "                        \"symbol\": symbol,\n"
        "                        WIRE_PROJECTION_METADATA_KEY: AI_CONFIG.provider_wire_projection,\n"
        "                        \"non_trading_shadow\": bool(\n",
    ),
]

PIPELINE_PROJECTION = "request_metadata.get(WIRE_PROJECTION_METADATA_KEY)"
PIPELINE_EDITS = [
    (
        "        system_prompt=critic_prompt,\n        evidence=critic_evidence,\n",
        f"        system_prompt=critic_prompt,\n        evidence=project_evidence_for_wire(critic_evidence, {PIPELINE_PROJECTION}),\n",
    ),
    (
        "            system_prompt=repair_prompt,\n            evidence=critic_evidence,\n",
        f"            system_prompt=repair_prompt,\n            evidence=project_evidence_for_wire(critic_evidence, {PIPELINE_PROJECTION}),\n",
    ),
    (
        "        system_prompt=adjudicator_prompt,\n        evidence=adjudicator_evidence,\n",
        f"        system_prompt=adjudicator_prompt,\n        evidence=project_evidence_for_wire(adjudicator_evidence, {PIPELINE_PROJECTION}),\n",
    ),
]


def patch(path: Path, edits) -> str:
    text = path.read_text(encoding="utf-8")
    for old, new in edits:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path.name}: expected exactly 1 match, found {count}: {old[:80]!r}")
        text = text.replace(old, new)
    return text


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    py_compile.compile(tmp, doraise=True)
    os.replace(tmp, path)


def main() -> int:
    targets = [(REPO_PY / "ai_gate.py", AI_GATE_EDITS), (REPO_PY / "decision_pipeline.py", PIPELINE_EDITS)]
    patched = [(path, patch(path, edits)) for path, edits in targets]  # validate everything first
    # decision_pipeline first: its edits only need the import that is already present.
    for path, text in reversed(patched):
        atomic_write(path, text)
        print(f"patched {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
