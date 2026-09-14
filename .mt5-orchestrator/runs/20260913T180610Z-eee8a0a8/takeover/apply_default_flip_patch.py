"""Make compact_v1 the production default (apply ONLY if addendum A2 passes); canonical stays the rollback."""
import os
import py_compile
import sys
import tempfile
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[4] / "python"

AI_GATE_EDITS = [
    (
        "    WIRE_PROJECTION_CANONICAL,\n    WIRE_PROJECTION_ENV,\n",
        "    WIRE_PROJECTION_CANONICAL,\n    WIRE_PROJECTION_COMPACT_V1,\n    WIRE_PROJECTION_ENV,\n",
    ),
    (
        "    # Provider-wire ENCODING of the evidence payload (provider_wire_projection.py):\n"
        "    # ``canonical`` is the Phase-1 wire byte for byte, ``compact_v1`` the lossless\n"
        "    # grouped catalog encoding.  It cannot change which evidence is sent, how an\n"
        "    # answer is validated, which calls are made, or any identity or cache key.\n"
        "    provider_wire_projection: str = WIRE_PROJECTION_CANONICAL\n",
        "    # Provider-wire ENCODING of the evidence payload (provider_wire_projection.py):\n"
        "    # ``compact_v1`` (default) is the lossless grouped catalog encoding, adopted after\n"
        "    # the 2026-09-13 live A/B (OPENCODE_GO_PHASE2_TOKEN_EFFICIENCY.md sections 9-10);\n"
        "    # ``canonical`` is the Phase-1 wire byte for byte and is the rollback.  It cannot\n"
        "    # change which evidence is sent, how an answer is validated, which calls are\n"
        "    # made, or any identity or cache key.\n"
        "    provider_wire_projection: str = WIRE_PROJECTION_COMPACT_V1\n",
    ),
    (
        "        # Encoding only, same posture as the session scope above: an invalid value\n"
        "        # is warned and replaced by the Phase-1 canonical wire.\n"
        "        provider_wire_projection = normalize_wire_projection(env.get(WIRE_PROJECTION_ENV))\n"
        "        if provider_wire_projection is None:\n"
        "            warnings.append(f\"{WIRE_PROJECTION_ENV}=invalid\")\n"
        "            provider_wire_projection = WIRE_PROJECTION_CANONICAL\n",
        "        # Encoding only, same posture as the session scope above: empty selects the\n"
        "        # documented default and an invalid value is warned and replaced by it.\n"
        "        raw_wire_projection = str(env.get(WIRE_PROJECTION_ENV) or \"\").strip()\n"
        "        provider_wire_projection = (\n"
        "            normalize_wire_projection(raw_wire_projection)\n"
        "            if raw_wire_projection\n"
        "            else WIRE_PROJECTION_COMPACT_V1\n"
        "        )\n"
        "        if provider_wire_projection is None:\n"
        "            warnings.append(f\"{WIRE_PROJECTION_ENV}=invalid\")\n"
        "            provider_wire_projection = WIRE_PROJECTION_COMPACT_V1\n",
    ),
]

DOC_EDITS = [
    (
        "# count, identity and cache keys are unchanged.  Empty = canonical.\n",
        "# count, identity and cache keys are unchanged.  Empty = compact_v1 (default\n"
        "# since the 2026-09-13 live A/B); set canonical to roll back to the Phase-1 wire.\n",
    ),
]

TEST_EDITS = [
    (
        "\nif __name__ == \"__main__\":\n    unittest.main()\n",
        "\nclass ProductionDefaultTests(unittest.TestCase):\n"
        "    \"\"\"compact_v1 is the default; canonical remains an explicit, working rollback.\"\"\"\n"
        "\n"
        "    def setUp(self) -> None:\n"
        "        import ai_gate\n"
        "\n"
        "        self.config_cls = ai_gate.AIGateRuntimeConfig\n"
        "\n"
        "    def test_empty_environment_selects_compact_v1(self):\n"
        "        self.assertEqual(self.config_cls.from_env({}).provider_wire_projection, wire.WIRE_PROJECTION_COMPACT_V1)\n"
        "        self.assertEqual(\n"
        "            self.config_cls.__dataclass_fields__[\"provider_wire_projection\"].default,\n"
        "            wire.WIRE_PROJECTION_COMPACT_V1,\n"
        "        )\n"
        "\n"
        "    def test_canonical_rollback_is_honoured(self):\n"
        "        cfg = self.config_cls.from_env({wire.WIRE_PROJECTION_ENV: \"canonical\"})\n"
        "        self.assertEqual(cfg.provider_wire_projection, wire.WIRE_PROJECTION_CANONICAL)\n"
        "\n"
        "    def test_invalid_value_is_warned_and_defaulted(self):\n"
        "        cfg = self.config_cls.from_env({wire.WIRE_PROJECTION_ENV: \"compact_v9\"})\n"
        "        self.assertEqual(cfg.provider_wire_projection, wire.WIRE_PROJECTION_COMPACT_V1)\n"
        "        self.assertIn(f\"{wire.WIRE_PROJECTION_ENV}=invalid\", cfg.validation_warnings)\n"
        "\n"
        "\nif __name__ == \"__main__\":\n    unittest.main()\n",
    ),
]


def patch(path: Path, edits) -> str:
    text = path.read_text(encoding="utf-8")
    for old, new in edits:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path.name}: expected exactly 1 match, found {count}: {old[:70]!r}")
        text = text.replace(old, new)
    return text


def atomic_write(path: Path, text: str, compile_check: bool) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    if compile_check:
        py_compile.compile(tmp, doraise=True)
    os.replace(tmp, path)


def main() -> int:
    targets = [
        (REPO_PY / "ai_gate.py", AI_GATE_EDITS, True),
        (REPO_PY / "docs" / "runtime_env_template_opencode.txt", DOC_EDITS, False),
        (REPO_PY / "tests" / "test_provider_wire_projection.py", TEST_EDITS, True),
    ]
    patched = [(path, patch(path, edits), check) for path, edits, check in targets]
    for path, text, check in patched:
        atomic_write(path, text, check)
        print(f"patched {path.relative_to(REPO_PY)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
