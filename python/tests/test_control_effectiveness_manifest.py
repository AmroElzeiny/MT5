from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from tools.audit_control_effectiveness import (
    REMOVED_MQL_CONTROLS,
    _mql_project_text_files,
    _text_files,
)


class ControlEffectivenessManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.python_root = Path(__file__).resolve().parents[1]
        cls.manifest_path = cls.python_root / "data" / "control_effectiveness_manifest.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.rows = {row["control_name"]: row for row in cls.manifest["controls"]}

    def test_generic_source_effect_audit_has_no_dead_controls(self) -> None:
        self.assertEqual(self.manifest["counts"]["DEAD"], 0)
        self.assertFalse([row for row in self.rows.values() if row["status"] == "DEAD"])

    def test_removed_controls_absent(self) -> None:
        mql_root = Path(self.manifest["mql_root"])
        source = "\n".join(
            path.read_text(encoding="utf-8-sig", errors="replace")
            for path in _mql_project_text_files(mql_root)
        )
        for name in REMOVED_MQL_CONTROLS:
            self.assertEqual(self.rows[name]["status"], "REMOVED")
            self.assertIsNone(re.search(rf"^\s*input\s+[^;]+\b{re.escape(name)}\b", source, re.MULTILINE), name)

    def test_policy_schema_effect_audit(self) -> None:
        for name in (
            "risk_factor_policy.v1.json:caps_pct.currency",
            "risk_factor_policy.v1.json:caps_pct.macro",
        ):
            self.assertEqual(self.rows[name]["status"], "ACTIVE")
            self.assertTrue(self.rows[name]["authority_path"])

    def test_source_discovery_prunes_generated_and_environment_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "kept.py").write_text("VALUE = 1\n", encoding="utf-8")
            for ignored in (".venv", ".codex_stage_old", ".tmp_report", "__pycache__"):
                directory = root / ignored
                directory.mkdir()
                (directory / "ignored.py").write_text("VALUE = 2\n", encoding="utf-8")
            found = [path.relative_to(root).as_posix() for path in _text_files(root, {".py"})]
            self.assertEqual(found, ["src/kept.py"])

    def test_mql_discovery_scopes_a_terminal_root_to_po3_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            include = root / "Include" / "MT5_PO3_Codex"
            expert = root / "Experts" / "MT5_PO3_Codex"
            unrelated = root / "Experts" / "Examples"
            include.mkdir(parents=True)
            expert.mkdir(parents=True)
            unrelated.mkdir(parents=True)
            (include / "Config.mqh").write_text("input bool InpUsed = true;\n", encoding="utf-8")
            (expert / "EA.mq5").write_text("bool x = InpUsed;\n", encoding="utf-8")
            (unrelated / "Noise.mq5").write_text("input bool InpUnrelated = true;\n", encoding="utf-8")
            found = sorted(path.relative_to(root).as_posix() for path in _mql_project_text_files(root))
            self.assertEqual(
                found,
                ["Experts/MT5_PO3_Codex/EA.mq5", "Include/MT5_PO3_Codex/Config.mqh"],
            )


if __name__ == "__main__":
    unittest.main()
