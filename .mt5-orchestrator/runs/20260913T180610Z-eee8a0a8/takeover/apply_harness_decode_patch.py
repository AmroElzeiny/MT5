"""Make the local harness provider and the integrity-test fake model read either wire encoding."""
import os
import py_compile
import sys
import tempfile
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[4] / "python"

HARNESS_EDITS = [
    (
        "# --------------------------------------------------------------------------\n"
        "# Evidence catalog reading\n"
        "# --------------------------------------------------------------------------\n",
        "# --------------------------------------------------------------------------\n"
        "# Evidence catalog reading\n"
        "# --------------------------------------------------------------------------\n"
        "\n"
        "\n"
        "def _decoded_wire(evidence: Any) -> Any:\n"
        "    \"\"\"Canonical rows from either provider-wire encoding.\n"
        "\n"
        "    The payload a provider receives may carry ``evidence_catalog.items`` as\n"
        "    canonical rows or as ``compact_v1`` groups (provider_wire_projection.py);\n"
        "    a compliant model reads whichever encoding it was sent.\n"
        "    \"\"\"\n"
        "    from provider_wire_projection import expand_evidence_from_wire\n"
        "\n"
        "    return expand_evidence_from_wire(evidence) if isinstance(evidence, dict) else evidence\n",
    ),
    (
        "    if not isinstance(evidence, dict):\n"
        "        return ids_by_candidate, all_ids, catalog_hash\n"
        "\n"
        "    catalog = evidence.get(\"evidence_catalog\")\n",
        "    if not isinstance(evidence, dict):\n"
        "        return ids_by_candidate, all_ids, catalog_hash\n"
        "\n"
        "    catalog = _decoded_wire(evidence).get(\"evidence_catalog\")\n",
    ),
    (
        "    if not isinstance(evidence, dict):\n"
        "        return values\n"
        "    catalog = evidence.get(\"evidence_catalog\")\n",
        "    if not isinstance(evidence, dict):\n"
        "        return values\n"
        "    catalog = _decoded_wire(evidence).get(\"evidence_catalog\")\n",
    ),
]

TEST_EDITS = [
    (
        "\nimport ai_gate\nfrom decision_integrity import (\n",
        "\nimport ai_gate\nfrom provider_wire_projection import expand_evidence_from_wire\nfrom decision_integrity import (\n",
    ),
    (
        "    items = ((evidence or {}).get(\"evidence_catalog\") or {}).get(\"items\") or []\n",
        "    # Decode first: the wire may carry canonical rows or compact_v1 groups.\n"
        "    items = (expand_evidence_from_wire(evidence or {}).get(\"evidence_catalog\") or {}).get(\"items\") or []\n",
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


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    py_compile.compile(tmp, doraise=True)
    os.replace(tmp, path)


def main() -> int:
    targets = [
        (REPO_PY / "tools" / "harness_local_provider.py", HARNESS_EDITS),
        (REPO_PY / "tests" / "test_decision_integrity.py", TEST_EDITS),
    ]
    patched = [(path, patch(path, edits)) for path, edits in targets]
    for path, text in patched:
        atomic_write(path, text)
        print(f"patched {path.relative_to(REPO_PY)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
