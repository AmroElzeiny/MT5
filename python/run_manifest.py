"""Shared run manifest binding one MQL session, one Python session, one provider.

The problem it solves
---------------------
The eleventh incident report was assembled from evidence that did not belong to
a single run.  The uploaded MT5 log contained lines from two different EAs::

    PO3_AIGate_PositivePath_Harness
    PO3_AIGate_ScannerEA

while the Python log opened with ``REMOTE_API`` / ``openai_remote_api`` and
several MT5 approvals in the same file identified
``LOCAL_OPENAI_COMPATIBLE`` / ``harness-deterministic-v1``.  Those are at least
three distinct sessions.  Conclusions drawn across them are not evidence about
any one of them.

A ``test_run_id`` is minted once per Python gate start, written to a manifest
file inside the bus, and stamped on every request, response, and log line that
belongs to that run.  Evidence carrying two different ``test_run_id`` values is
two runs, and the report generator can say so instead of silently merging them.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

RUN_MANIFEST_VERSION = "20260731_correlated_run_manifest_v1"
RUN_MANIFEST_FILENAME = "run_manifest.json"


def _hash_file(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()[:32]
    except OSError:
        return ""


@dataclass
class RunManifest:
    """Everything needed to prove two log lines came from the same run."""

    run_manifest_version: str = RUN_MANIFEST_VERSION
    test_run_id: str = ""
    python_session_id: str = ""
    mql_session_id: str = ""
    provider_mode: str = ""
    provider_id: str = ""
    provider_process_id: int = 0
    model: str = ""
    ea_name: str = ""
    ex5_path: str = ""
    ex5_hash: str = ""
    ex5_bytes: int = 0
    runtime_input_hash: str = ""
    contract_manifest_hash: str = ""
    decision_schema_version: str = ""
    prompt_contract_version: str = ""
    test_period_from: str = ""
    test_period_to: str = ""
    tester_mode: str = ""
    host: str = field(default_factory=platform.node)
    started_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    def log_line(self) -> str:
        """The single line every side prints so runs can be told apart."""
        return (
            "[test_run_manifest]"
            f" test_run_id={self.test_run_id}"
            f" python_session_id={self.python_session_id}"
            f" mql_session_id={self.mql_session_id or 'awaiting_mql'}"
            f" provider_mode={self.provider_mode}"
            f" provider_id={self.provider_id}"
            f" provider_pid={self.provider_process_id}"
            f" model={self.model}"
            f" ea_name={self.ea_name or 'awaiting_mql'}"
            f" ex5_hash={self.ex5_hash or 'unknown'}"
            f" runtime_input_hash={self.runtime_input_hash or 'unknown'}"
            f" contract_manifest_hash={self.contract_manifest_hash or 'unknown'}"
            f" test_period={self.test_period_from or 'unset'}..{self.test_period_to or 'unset'}"
            f" tester_mode={self.tester_mode or 'unset'}"
        )


def mint_test_run_id(*, python_session_id: str, provider_id: str, model: str) -> str:
    """Deterministic in its inputs, unique per gate start.

    The Python session id already carries pid and start time, so hashing it with
    the provider identity gives a short id that is stable for the whole run and
    cannot collide with a differently configured run started in the same second.
    """

    seed = f"{python_session_id}|{provider_id}|{model}|{RUN_MANIFEST_VERSION}"
    return "run_" + sha256(seed.encode("utf-8")).hexdigest()[:16]


def build_manifest(
    *,
    python_session_id: str,
    provider_mode: str,
    provider_id: str,
    model: str,
    decision_schema_version: str = "",
    prompt_contract_version: str = "",
    contract_manifest_hash: str = "",
    ex5_path: str | os.PathLike[str] | None = None,
) -> RunManifest:
    manifest = RunManifest(
        test_run_id=mint_test_run_id(
            python_session_id=python_session_id, provider_id=provider_id, model=model
        ),
        python_session_id=python_session_id,
        provider_mode=provider_mode,
        provider_id=provider_id,
        provider_process_id=os.getpid(),
        model=model,
        decision_schema_version=decision_schema_version,
        prompt_contract_version=prompt_contract_version,
        contract_manifest_hash=contract_manifest_hash,
    )
    if ex5_path:
        path = Path(ex5_path)
        manifest.ex5_path = str(path)
        manifest.ea_name = path.stem
        manifest.ex5_hash = _hash_file(path)
        try:
            manifest.ex5_bytes = path.stat().st_size
        except OSError:
            manifest.ex5_bytes = 0
    return manifest


def write_manifest(manifest: RunManifest, bus_root: str | os.PathLike[str]) -> Path:
    """Publish the manifest where MQL can read it and bind to the same run."""
    target = Path(bus_root) / "logs" / RUN_MANIFEST_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(manifest.to_json(), encoding="utf-8")
    os.replace(tmp, target)
    return target


def absorb_mql_identity(manifest: RunManifest, request: Mapping[str, Any]) -> bool:
    """Fill the MQL half of the manifest from the first request that arrives.

    Returns True when something new was learned, so the caller can re-log the
    manifest line once the run is fully identified rather than on every request.
    """

    changed = False
    for field_name, keys in (
        ("mql_session_id", ("mql_session_id", "session_id")),
        ("ea_name", ("ea_name", "expert_name")),
        ("runtime_input_hash", ("runtime_input_hash",)),
        ("contract_manifest_hash", ("contract_manifest_hash",)),
        ("tester_mode", ("tester_ai_mode", "tester_mode")),
        ("test_period_from", ("test_period_from", "tester_from_date")),
        ("test_period_to", ("test_period_to", "tester_to_date")),
    ):
        if getattr(manifest, field_name):
            continue
        for key in keys:
            value = request.get(key)
            if value not in (None, "", 0):
                setattr(manifest, field_name, str(value))
                changed = True
                break
    return changed


def stamp(payload: dict[str, Any], manifest: RunManifest) -> dict[str, Any]:
    """Attach the run id to a request or response payload."""
    payload["test_run_id"] = manifest.test_run_id
    return payload


def runs_in(records: list[Mapping[str, Any]]) -> set[str]:
    """Every distinct ``test_run_id`` present in a set of records.

    A report that draws a conclusion from records spanning more than one id is
    combining unrelated sessions, which is exactly what produced the misleading
    eleventh-run evidence.
    """

    return {str(r.get("test_run_id") or "") for r in records if r.get("test_run_id")}


def assert_single_run(records: list[Mapping[str, Any]]) -> str:
    ids = runs_in(records)
    if len(ids) > 1:
        raise ValueError("evidence_spans_multiple_runs:" + ",".join(sorted(ids)))
    return next(iter(ids), "")
