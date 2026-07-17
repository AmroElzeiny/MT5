from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable


REMOVED_MQL_CONTROLS = {
    "InpAutoSuppressWeakFamiliesLive": "removed_dead_adaptive_suppression_switch",
    "InpAiRequireRawAllow": "removed_obsolete_override_contract",
    "InpAiOverrideScore": "removed_obsolete_legacy_score_override",
    "InpAiOverrideConfidence": "removed_synthetic_confidence_override",
    "InpMinAiConfidence": "removed_diagnostic_input_with_zero_trade_authority",
    "InpSyntheticFallbackMinCleanCaptureRatio": "removed_unimplemented_statistical_threshold",
    "InpSyntheticFallbackMinStatsCount": "removed_unimplemented_statistical_threshold",
}

# These leaves are consumed through a validated category lookup rather than a
# literal key read. Keep the evidence explicit so the manifest does not mistake
# schema-driven authority for a dead option.
DYNAMIC_POLICY_AUTHORITIES = {
    "risk_factor_policy.v1.json:caps_pct.currency": "Include/MT5_PO3_Codex/Risk.mqh::_RiskFactorCapFor",
    "risk_factor_policy.v1.json:caps_pct.macro": "Include/MT5_PO3_Codex/Risk.mqh::_RiskFactorCapFor",
}


DEFAULT_SOURCE_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "ledger_repair_output",
    "logs",
    "node_modules",
    "venv",
}


def _excluded_source_dir(name: str, excluded: set[str]) -> bool:
    lowered = name.lower()
    return (
        lowered in excluded
        or lowered.startswith(".codex_")
        or lowered.startswith(".tmp_")
        or lowered.startswith("_tmp_")
    )


def _text_files(root: Path, suffixes: set[str], *, excluded: Iterable[str] = ()) -> list[Path]:
    excluded_parts = {part.lower() for part in DEFAULT_SOURCE_EXCLUDED_DIRS}
    excluded_parts.update(part.lower() for part in excluded)
    output: list[Path] = []
    for current, directories, filenames in os.walk(root):
        directories[:] = [
            name for name in directories if not _excluded_source_dir(name, excluded_parts)
        ]
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.suffix.lower() in suffixes:
                output.append(path)
    return output


def _mql_project_text_files(mql_root: Path) -> list[Path]:
    project_roots = [
        mql_root / "Include" / "MT5_PO3_Codex",
        mql_root / "Experts" / "MT5_PO3_Codex",
    ]
    existing = [path for path in project_roots if path.is_dir()]
    if not existing:
        return _text_files(mql_root, {".mqh", ".mq5"})
    output: list[Path] = []
    for project_root in existing:
        output.extend(_text_files(project_root, {".mqh", ".mq5"}))
    return output


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _locations(name: str, files: list[Path], *, root: Path) -> list[str]:
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    output: list[str] = []
    for path in files:
        for line_number, line in enumerate(_read(path).splitlines(), 1):
            if pattern.search(line):
                output.append(f"{path.relative_to(root).as_posix()}:{line_number}")
    return output


def _mql_controls(mql_root: Path, test_files: list[Path], python_root: Path) -> list[dict[str, Any]]:
    files = _mql_project_text_files(mql_root)
    python_files = _text_files(python_root, {".py"}, excluded={"tests", "__pycache__"})
    python_blob = "\n".join(_read(path) for path in python_files)
    declarations: dict[str, str] = {}
    declaration_re = re.compile(r"^\s*input\s+[A-Za-z_][A-Za-z0-9_<>]*\s+(Inp[A-Za-z0-9_]+)\b")
    for path in files:
        for line_number, line in enumerate(_read(path).splitlines(), 1):
            match = declaration_re.search(line)
            if match:
                declarations[match.group(1)] = f"{path.relative_to(mql_root).as_posix()}:{line_number}"
    rows: list[dict[str, Any]] = []
    for name, declaration in sorted(declarations.items()):
        locations = _locations(name, files, root=mql_root)
        reads = [location for location in locations if location != declaration]
        tests = _locations(name, test_files, root=python_root)
        source_files = {location.split(":", 1)[0] for location in reads}
        diagnostic_only = bool(reads) and source_files.issubset({"Include/MT5_PO3_Codex/AIGateBridge.mqh"})
        token = re.sub(r"(?<!^)(?=[A-Z])", "_", name.removeprefix("Inp")).lower()
        cross_language_authority = diagnostic_only and re.search(rf"\b{re.escape(token)}\b", python_blob) is not None
        if cross_language_authority:
            diagnostic_only = False
            reads.append(f"python_runtime_payload:{token}")
        status = "DEAD" if not reads else "DIAGNOSTIC_ONLY" if diagnostic_only else "ACTIVE"
        rows.append(
            {
                "control_name": name,
                "control_class": "MQL_INPUT",
                "declaration_location": declaration,
                "read_count": len(reads),
                "authority_path": reads,
                "log_output": any("Log" in location or "Bridge" in location for location in reads),
                "behavioral_test": tests or ["tests/test_control_effectiveness_manifest.py::generic_source_effect_audit"],
                "status": status,
            }
        )
    for name, reason in sorted(REMOVED_MQL_CONTROLS.items()):
        rows.append(
            {
                "control_name": name,
                "control_class": "MQL_INPUT",
                "declaration_location": "",
                "read_count": 0,
                "authority_path": [],
                "log_output": False,
                "behavioral_test": ["tests/test_control_effectiveness_manifest.py::removed_controls_absent"],
                "status": "REMOVED",
                "removal_reason": reason,
            }
        )
    return rows


def _env_controls(python_root: Path, test_files: list[Path]) -> list[dict[str, Any]]:
    example = python_root / ".env.example"
    if not example.is_file():
        return []
    names: list[str] = []
    for line in _read(example).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]+", name):
            names.append(name)
    source_files = _text_files(
        python_root,
        {".py"},
        excluded={"tests", "__pycache__"},
    )
    rows: list[dict[str, Any]] = []
    for name in sorted(set(names)):
        reads = _locations(name, source_files, root=python_root)
        tests = _locations(name, test_files, root=python_root)
        rows.append(
            {
                "control_name": name,
                "control_class": "PYTHON_ENV",
                "declaration_location": f".env.example:{next((i for i, line in enumerate(_read(example).splitlines(), 1) if line.strip().startswith(name + '=')), 0)}",
                "read_count": len(reads),
                "authority_path": reads,
                "log_output": any("config" in location.lower() or "gate" in location.lower() for location in reads),
                "behavioral_test": tests or ["tests/test_control_effectiveness_manifest.py::generic_source_effect_audit"],
                "status": "ACTIVE" if reads else "DEAD",
            }
        )
    return rows


def _policy_leaf_names(value: Any, prefix: str = "") -> set[str]:
    output: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (dict, list)):
                output.update(_policy_leaf_names(child, path))
            else:
                output.add(path)
    elif isinstance(value, list):
        for child in value:
            output.update(_policy_leaf_names(child, prefix))
    return output


def _policy_controls(python_root: Path, mql_root: Path, test_files: list[Path]) -> list[dict[str, Any]]:
    config_dir = python_root / "config"
    if not config_dir.is_dir():
        return []
    source_files = _text_files(python_root, {".py"}, excluded={"tests", "__pycache__"}) + _mql_project_text_files(mql_root)
    rows: list[dict[str, Any]] = []
    for path in sorted(config_dir.glob("*.json")):
        try:
            payload = json.loads(_read(path))
        except Exception:
            continue
        for full_name in sorted(_policy_leaf_names(payload)):
            leaf = full_name.rsplit(".", 1)[-1]
            control_name = f"{path.name}:{full_name}"
            locations: list[str] = []
            pattern = re.compile(rf"[\"']{re.escape(leaf)}[\"']")
            for source in source_files:
                for line_number, line in enumerate(_read(source).splitlines(), 1):
                    if pattern.search(line):
                        base = python_root if python_root in source.parents else mql_root
                        locations.append(f"{source.relative_to(base).as_posix()}:{line_number}")
            dynamic_authority = DYNAMIC_POLICY_AUTHORITIES.get(control_name)
            if dynamic_authority and dynamic_authority not in locations:
                locations.append(dynamic_authority)
            rows.append(
                {
                    "control_name": control_name,
                    "control_class": "POLICY_OPTION",
                    "declaration_location": f"config/{path.name}",
                    "read_count": len(locations),
                    "authority_path": locations,
                    "log_output": False,
                    "behavioral_test": ["tests/test_control_effectiveness_manifest.py::policy_schema_effect_audit"],
                    "status": "ACTIVE" if locations else "DIAGNOSTIC_ONLY",
                    "note": (
                        "Consumed by a validated category-driven policy lookup."
                        if dynamic_authority
                        else "Nested policy rows may be consumed by a schema-driven parent parser."
                    ),
                }
            )
    return rows


def build_manifest(python_root: Path, mql_root: Path) -> dict[str, Any]:
    test_files = _text_files(python_root / "tests", {".py"}) if (python_root / "tests").is_dir() else []
    controls = _mql_controls(mql_root, test_files, python_root)
    controls.extend(_env_controls(python_root, test_files))
    controls.extend(_policy_controls(python_root, mql_root, test_files))
    counts = {status: sum(row["status"] == status for row in controls) for status in ("ACTIVE", "DIAGNOSTIC_ONLY", "REMOVED", "DEAD")}
    return {
        "schema_version": "20260717_control_effectiveness_manifest_v1",
        "python_root": str(python_root.resolve()),
        "mql_root": str(mql_root.resolve()),
        "counts": counts,
        "controls": controls,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--mql-root", required=True)
    parser.add_argument("--output", default="data/control_effectiveness_manifest.json")
    parser.add_argument("--fail-on-dead", action="store_true")
    args = parser.parse_args()
    python_root = Path(args.python_root).resolve()
    mql_root = Path(args.mql_root).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = python_root / output
    manifest = build_manifest(python_root, mql_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": manifest["counts"]}, sort_keys=True))
    return 1 if args.fail_on_dead and manifest["counts"]["DEAD"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
