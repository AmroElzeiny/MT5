"""Append-only experiment registry with explicit holdout contamination checks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


EXPERIMENT_REGISTRY_SCHEMA_VERSION = "20260717_experiment_registry_v2"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_state(repo_root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "-c", f"safe.directory={repo_root.as_posix()}", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "-c", f"safe.directory={repo_root.as_posix()}", "status", "--porcelain"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip())
        return commit, dirty
    except Exception:
        return "unknown", True


def _range_key(value: Any) -> str:
    if isinstance(value, Mapping):
        return f"{value.get('start', '')}|{value.get('end', '')}"
    return str(value or "")


@dataclass(frozen=True)
class HoldoutStatus:
    untouched: bool
    contaminated: bool
    reason: str


class ExperimentRegistry:
    def __init__(self, path: Path):
        self.path = path

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        output: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                output.append(value)
        return output

    def _append(self, event: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(event), sort_keys=True, separators=(",", ":")) + "\n")

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        created = next(
            (
                event
                for event in self.events()
                if event.get("event") == "EXPERIMENT_CREATED" and event.get("experiment_id") == experiment_id
            ),
            None,
        )
        if created is None:
            return None
        state = dict(created)
        state["events"] = [event for event in self.events() if event.get("experiment_id") == experiment_id]
        return state

    def holdout_status(self, final_holdout_range: Any, *, exclude_experiment_id: str = "") -> HoldoutStatus:
        key = _range_key(final_holdout_range)
        if not key or key == "|":
            return HoldoutStatus(False, True, "final_holdout_range_missing")
        for event in self.events():
            if event.get("experiment_id") == exclude_experiment_id:
                continue
            inspected = event.get("event") == "PERIOD_INSPECTED"
            created_with_inspected_holdout = (
                event.get("event") == "EXPERIMENT_CREATED"
                and bool(event.get("final_holdout_previously_inspected"))
            )
            if (inspected and _range_key(event.get("period")) == key) or (
                created_with_inspected_holdout and _range_key(event.get("final_untouched_holdout_range")) == key
            ):
                return HoldoutStatus(False, True, "final_holdout_already_inspected")
        return HoldoutStatus(True, False, "uninspected_period")

    def create(self, spec: Mapping[str, Any], *, repo_root: Path | None = None) -> dict[str, Any]:
        required = (
            "experiment_id",
            "hypothesis",
            "engine_version",
            "set_file_path",
            "runtime_input_hash",
            "prompt_contract_version",
            "decision_schema_version",
            "target_schema_version",
            "ai_model_version",
            "policy_id",
            "setup_taxonomy_version",
            "management_version",
            "hierarchical_prior_schema_version",
            "hierarchical_prior_artifact_hash",
            "symbol_universe_hash",
            "tested_date_range",
            "training_range",
            "calibration_range",
            "validation_range",
            "final_untouched_holdout_range",
        )
        missing = [name for name in required if not spec.get(name)]
        if missing:
            raise ValueError("missing_experiment_fields:" + ",".join(missing))
        experiment_id = str(spec["experiment_id"])
        if self.get(experiment_id) is not None:
            raise ValueError("experiment_id_already_exists")
        holdout = self.holdout_status(spec["final_untouched_holdout_range"])
        if not holdout.untouched:
            raise ValueError("final_holdout_contaminated:" + holdout.reason)
        root = repo_root or Path.cwd()
        commit, dirty = _git_state(root)
        set_path = Path(str(spec["set_file_path"]))
        event = {
            "registry_schema_version": EXPERIMENT_REGISTRY_SCHEMA_VERSION,
            "event": "EXPERIMENT_CREATED",
            "created_at": _utc_now(),
            **dict(spec),
            "code_commit": spec.get("code_commit") or commit,
            "dirty_working_tree": bool(spec.get("dirty_working_tree", dirty)),
            "set_file_hash": file_sha256(set_path) if set_path.exists() else "missing",
            "policy_hash": str(spec.get("policy_hash") or ""),
            "hierarchical_prior_schema_version": str(spec["hierarchical_prior_schema_version"]),
            "hierarchical_prior_artifact_hash": str(spec["hierarchical_prior_artifact_hash"]),
            "symbol_universe_hash": str(spec["symbol_universe_hash"]),
            "final_holdout_previously_inspected": False,
            "decision_taken": str(spec.get("decision_taken") or "registered"),
            "parent_experiment_id": str(spec.get("parent_experiment_id") or ""),
        }
        self._append(event)
        return event

    def mark_period_inspected(self, experiment_id: str, period: Any, *, purpose: str) -> dict[str, Any]:
        if self.get(experiment_id) is None:
            raise ValueError("experiment_not_found")
        event = {
            "registry_schema_version": EXPERIMENT_REGISTRY_SCHEMA_VERSION,
            "event": "PERIOD_INSPECTED",
            "created_at": _utc_now(),
            "experiment_id": experiment_id,
            "period": period,
            "purpose": purpose,
        }
        self._append(event)
        return event

    def record_result(self, experiment_id: str, metrics: Mapping[str, Any], *, result: str, decision_taken: str) -> dict[str, Any]:
        if self.get(experiment_id) is None:
            raise ValueError("experiment_not_found")
        event = {
            "registry_schema_version": EXPERIMENT_REGISTRY_SCHEMA_VERSION,
            "event": "EXPERIMENT_RESULT",
            "created_at": _utc_now(),
            "experiment_id": experiment_id,
            "metrics": dict(metrics),
            "result": result,
            "decision_taken": decision_taken,
        }
        self._append(event)
        return event

    def optimization_authorized(self, experiment_id: str, hypothesis: str) -> tuple[bool, str]:
        if not experiment_id or not hypothesis.strip():
            return False, "registered_hypothesis_and_experiment_id_required"
        experiment = self.get(experiment_id)
        if experiment is None:
            return False, "experiment_not_found"
        if experiment.get("registry_schema_version") != EXPERIMENT_REGISTRY_SCHEMA_VERSION:
            return False, "experiment_registry_schema_incompatible"
        if not experiment.get("hierarchical_prior_schema_version") or not experiment.get("hierarchical_prior_artifact_hash"):
            return False, "experiment_prior_identity_missing"
        if str(experiment.get("hypothesis") or "").strip() != hypothesis.strip():
            return False, "hypothesis_mismatch"
        holdout = self.holdout_status(experiment.get("final_untouched_holdout_range"), exclude_experiment_id=experiment_id)
        if not holdout.untouched:
            return False, holdout.reason
        return True, "registered_uncontaminated_experiment"
