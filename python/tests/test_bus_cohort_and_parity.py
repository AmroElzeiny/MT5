"""Bus cohort isolation and Python/MQL JSON byte parity.

The parity tests encode the root cause of ``json_root_not_object``: MQL's
``FileBus::ReadText`` used ``FILE_TXT`` without ``FILE_ANSI``/``FILE_UNICODE``,
which MQL5 defaults to UTF-16.  Responses are written UTF-16 and read fine;
config/policy/deployment artifacts are written UTF-8, so the same reader saw
0x227B where ``{`` was expected.  ``DecodeTextBytes`` now decodes by BOM and
heuristic, mirroring Python's ``read_json_any_encoding``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from bus_cohort_migration import (
    CLASSIFY_COMPATIBLE,
    CLASSIFY_INCOMPATIBLE,
    CLASSIFY_PROTECTED,
    CLASSIFY_UNPARSEABLE,
    CohortIdentity,
    classify_bus_file,
    cohort_identity_of,
    migrate_bus_cohorts,
    read_json_any_encoding,
)

MQL_INCLUDE = Path(
    r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal"
    r"\0148BD5691B65B0F2157627A4231F3DE\MQL5\Include\MT5_PO3_Codex"
)


def _current() -> CohortIdentity:
    return CohortIdentity(
        engine_version="engine-v9",
        decision_schema_version="schema-v9",
        identity_schema_version="identity-v9",
        prompt_contract_version="prompt-v9",
        contract_manifest_hash="manifest-v9",
        session_id="",
    )


def _payload(**overrides) -> dict:
    body = {
        "id": "req-1",
        "session_id": "session-1",
        "engine_version": "engine-v9",
        "decision_schema_version": "schema-v9",
        "identity_schema_version": "identity-v9",
        "prompt_contract_version": "prompt-v9",
        "contract_manifest_hash": "manifest-v9",
        "request_identity_hash": "HASH",
    }
    body.update(overrides)
    return body


class EncodingReaderTests(unittest.TestCase):
    """Python must read every encoding the bus actually contains."""

    def _roundtrip(self, encoding: str, payload: dict) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "artifact.json"
            path.write_bytes(json.dumps(payload).encode(encoding))
            return read_json_any_encoding(path)

    def test_reads_utf8(self) -> None:
        self.assertEqual(self._roundtrip("utf-8", _payload())["id"], "req-1")

    def test_reads_utf8_with_bom(self) -> None:
        self.assertEqual(self._roundtrip("utf-8-sig", _payload())["id"], "req-1")

    def test_reads_utf16_le_with_bom(self) -> None:
        self.assertEqual(self._roundtrip("utf-16", _payload())["id"], "req-1")

    def test_reads_utf16_le_without_bom(self) -> None:
        self.assertEqual(self._roundtrip("utf-16-le", _payload())["id"], "req-1")

    def test_unreadable_bytes_raise(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "bad.json"
            path.write_bytes(b"\x00\x01\x02not json")
            with self.assertRaises(ValueError):
                read_json_any_encoding(path)


class MqlDecoderParityTests(unittest.TestCase):
    """The MQL reader must handle the same encodings, by source inspection."""

    def _filebus(self) -> str:
        path = MQL_INCLUDE / "FileBus.mqh"
        if not path.is_file():
            self.skipTest(f"MQL include not deployed: {path}")
        return path.read_text(encoding="utf-8", errors="ignore")

    def test_readtext_no_longer_uses_encoding_defaulting_file_txt(self) -> None:
        source = self._filebus()
        reader = source.split("bool ReadText(")[1].split("\n   }")[0]
        self.assertNotIn("FILE_TXT", reader, "FILE_TXT defaults to UTF-16 in MQL5")
        self.assertIn("FILE_BIN", reader)

    def test_decoder_handles_every_bus_encoding(self) -> None:
        source = self._filebus()
        self.assertIn("DecodeTextBytes", source)
        # UTF-16LE BOM, UTF-16BE BOM, UTF-8 BOM, and the no-BOM UTF-16 heuristic.
        for marker in ("0xFF", "0xFE", "0xEF", "0xBB", "0xBF", "CP_UTF8"):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_python_and_mql_agree_on_the_root_token_rule(self) -> None:
        # JsonLite requires '{' as the first non-whitespace character; Python's
        # classifier calls the same condition json_root_not_object.
        jsonlite = (MQL_INCLUDE / "JsonLite.mqh")
        if not jsonlite.is_file():
            self.skipTest("JsonLite.mqh not deployed")
        self.assertIn("json_root_not_object", jsonlite.read_text(encoding="utf-8", errors="ignore"))


class ArtifactByteParityTests(unittest.TestCase):
    """Every MQL-consumed artifact must decode to a JSON object."""

    ARTIFACTS = (
        "config/deployment_manifest.json",
        "config/normalized_fvg_policy.v2.json",
        "config/risk_factor_policy.v1.json",
        "config/invalidation_policy.v1.json",
    )

    def setUp(self) -> None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            self.skipTest("APPDATA unavailable")
        self.bus = (
            Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "PO3_AI_BUS"
        )
        if not self.bus.is_dir():
            self.skipTest("bus not present")

    def test_artifacts_decode_to_json_objects(self) -> None:
        for relative in self.ARTIFACTS:
            path = self.bus / relative
            if not path.is_file():
                continue
            with self.subTest(artifact=relative):
                payload = read_json_any_encoding(path)
                self.assertIsInstance(payload, dict)

    def test_first_decoded_character_is_an_object_brace(self) -> None:
        # This is exactly what JsonValidateDocumentStrict checks.
        for relative in self.ARTIFACTS:
            path = self.bus / relative
            if not path.is_file():
                continue
            with self.subTest(artifact=relative):
                raw = path.read_bytes()
                if raw.startswith(b"\xff\xfe"):
                    text = raw.decode("utf-16")
                elif raw.startswith(b"\xef\xbb\xbf"):
                    text = raw.decode("utf-8-sig")
                else:
                    text = raw.decode("utf-8")
                self.assertEqual(text.lstrip()[:1], "{")


class CohortClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bus = Path(self.tmp.name)
        (self.bus / "responses").mkdir(parents=True)
        (self.bus / "stale").mkdir(parents=True)
        (self.bus / "logs").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def _write(self, relative: str, payload, encoding: str = "utf-8") -> Path:
        path = self.bus / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(payload).encode(encoding))
        return path

    def test_current_cohort_is_compatible(self) -> None:
        path = self._write("responses/current.json", _payload())
        result = classify_bus_file(path, self.bus, _current())
        self.assertEqual(result.classification, CLASSIFY_COMPATIBLE)

    def test_old_schema_is_incompatible(self) -> None:
        path = self._write("stale/old.json", _payload(decision_schema_version="schema-v1"))
        result = classify_bus_file(path, self.bus, _current())
        self.assertEqual(result.classification, CLASSIFY_INCOMPATIBLE)
        self.assertIn("decision_schema_version", result.reason)

    def test_2023_era_response_has_no_contract_and_is_incompatible(self) -> None:
        # The real shape found in the bus: {id, allow, score, chosen_index}.
        path = self._write(
            "stale/legacy.json",
            {"id": "1690506000_XAUUSD+_17355", "allow": True, "score": 7.35, "chosen_index": 0},
        )
        result = classify_bus_file(path, self.bus, _current())
        self.assertEqual(result.classification, CLASSIFY_INCOMPATIBLE)
        self.assertEqual(result.identity.decision_schema_version, "legacy_pre_contract")

    def test_utf16_artifact_is_classified_not_treated_as_corrupt(self) -> None:
        path = self._write("responses/utf16.json", _payload(), encoding="utf-16")
        result = classify_bus_file(path, self.bus, _current())
        self.assertEqual(result.classification, CLASSIFY_COMPATIBLE)

    def test_non_object_root_is_unparseable(self) -> None:
        path = self.bus / "stale" / "array.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        result = classify_bus_file(path, self.bus, _current())
        self.assertEqual(result.classification, CLASSIFY_UNPARSEABLE)
        self.assertEqual(result.reason, "json_root_not_object")

    def test_cohort_hash_is_stable_and_discriminating(self) -> None:
        a = cohort_identity_of(_payload())
        b = cohort_identity_of(_payload())
        c = cohort_identity_of(_payload(decision_schema_version="other"))
        self.assertEqual(a.cohort_hash, b.cohort_hash)
        self.assertNotEqual(a.cohort_hash, c.cohort_hash)

    def test_session_id_does_not_change_the_cohort(self) -> None:
        # Cohort is a contract generation, not a run; two runs of the same build
        # share a cohort so a valid cache entry stays reusable.
        a = cohort_identity_of(_payload(session_id="s1"))
        b = cohort_identity_of(_payload(session_id="s2"))
        self.assertEqual(a.cohort_hash, b.cohort_hash)


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bus = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for relative, payload in (
            ("responses/current.json", _payload()),
            ("stale/old_a.json", _payload(decision_schema_version="schema-v1")),
            ("stale/old_b.json", _payload(prompt_contract_version="prompt-v1")),
            ("rejected/legacy.json", {"id": "x", "allow": True, "score": 1.0}),
        ):
            path = self.bus / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        ledger = self.bus / "logs" / "completed_ai_trades.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"trade": 1}\n', encoding="utf-8")

    def test_dry_run_moves_nothing(self) -> None:
        before = sorted(p.name for p in self.bus.rglob("*") if p.is_file())
        summary = migrate_bus_cohorts(self.bus, _current(), dry_run=True)
        after = sorted(p.name for p in self.bus.rglob("*") if p.is_file())
        self.assertEqual(before, after)
        self.assertEqual(summary.moved, 0)
        self.assertGreater(summary.counts.get(CLASSIFY_INCOMPATIBLE, 0), 0)

    def test_archive_moves_only_incompatible(self) -> None:
        summary = migrate_bus_cohorts(
            self.bus, _current(), dry_run=False, archive_date="20260731"
        )
        self.assertEqual(summary.moved, 3)
        self.assertTrue((self.bus / "responses" / "current.json").is_file())
        self.assertFalse((self.bus / "stale" / "old_a.json").is_file())
        archived = list((self.bus / "archive" / "20260731").rglob("*.json"))
        self.assertEqual(len(archived), 3)

    def test_empirical_history_is_never_archived(self) -> None:
        migrate_bus_cohorts(self.bus, _current(), dry_run=False, archive_date="20260731")
        self.assertTrue((self.bus / "logs" / "completed_ai_trades.jsonl").is_file())

    def test_archive_is_grouped_by_cohort(self) -> None:
        summary = migrate_bus_cohorts(
            self.bus, _current(), dry_run=False, archive_date="20260731"
        )
        # Three distinct incompatible cohorts in the fixture.
        self.assertEqual(len(summary.by_cohort), 3)
        for bucket in summary.by_cohort.values():
            self.assertGreaterEqual(bucket["files"], 1)
            self.assertIn("cohort", bucket)

    def test_migration_is_idempotent(self) -> None:
        migrate_bus_cohorts(self.bus, _current(), dry_run=False, archive_date="20260731")
        second = migrate_bus_cohorts(
            self.bus, _current(), dry_run=False, archive_date="20260731"
        )
        self.assertEqual(second.moved, 0)

    def test_after_migration_only_current_cohort_remains_live(self) -> None:
        migrate_bus_cohorts(self.bus, _current(), dry_run=False, archive_date="20260731")
        remaining = migrate_bus_cohorts(self.bus, _current(), dry_run=True)
        self.assertEqual(remaining.counts.get(CLASSIFY_INCOMPATIBLE, 0), 0)

    def test_summary_is_serialisable(self) -> None:
        summary = migrate_bus_cohorts(self.bus, _current(), dry_run=True)
        json.dumps(summary.as_dict())


if __name__ == "__main__":
    unittest.main()
