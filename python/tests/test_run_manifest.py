"""Regressions for correlated test evidence.

The eleventh incident report mixed at least three sessions: an MT5 log holding
lines from both ``PO3_AIGate_PositivePath_Harness`` and
``PO3_AIGate_ScannerEA``, and a Python log that opened with ``REMOTE_API`` /
``openai_remote_api`` while several MT5 approvals in the same evidence set
identified ``LOCAL_OPENAI_COMPATIBLE`` / ``harness-deterministic-v1``.

Conclusions drawn across unrelated sessions are not evidence about any of them.
A ``test_run_id`` makes the mixing detectable instead of invisible.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from run_manifest import (  # noqa: E402
    RUN_MANIFEST_FILENAME,
    RUN_MANIFEST_VERSION,
    RunManifest,
    absorb_mql_identity,
    assert_single_run,
    build_manifest,
    mint_test_run_id,
    runs_in,
    stamp,
    write_manifest,
)


def _manifest(**overrides) -> RunManifest:
    kwargs = dict(
        python_session_id="python_23588_1785456413",
        provider_mode="LOCAL_OPENAI_COMPATIBLE",
        provider_id="local_openai_compatible",
        model="harness-deterministic-v1",
        decision_schema_version="20260724_canonical_frozen_request_v10",
        contract_manifest_hash="1024838206",
    )
    kwargs.update(overrides)
    return build_manifest(**kwargs)


class RunIdentityTests(unittest.TestCase):
    def test_run_id_is_stable_for_one_session(self) -> None:
        first = mint_test_run_id(
            python_session_id="s1", provider_id="local_openai_compatible", model="m"
        )
        second = mint_test_run_id(
            python_session_id="s1", provider_id="local_openai_compatible", model="m"
        )
        self.assertEqual(first, second)

    def test_run_id_differs_across_sessions(self) -> None:
        self.assertNotEqual(
            mint_test_run_id(python_session_id="s1", provider_id="p", model="m"),
            mint_test_run_id(python_session_id="s2", provider_id="p", model="m"),
        )

    def test_run_id_differs_across_providers(self) -> None:
        """The exact contamination in the incident: remote vs local provider."""
        self.assertNotEqual(
            mint_test_run_id(
                python_session_id="s1", provider_id="openai_remote_api", model="gpt"
            ),
            mint_test_run_id(
                python_session_id="s1",
                provider_id="local_openai_compatible",
                model="harness-deterministic-v1",
            ),
        )

    def test_manifest_carries_every_required_field(self) -> None:
        manifest = _manifest()
        for name in (
            "test_run_id",
            "python_session_id",
            "provider_mode",
            "provider_id",
            "provider_process_id",
            "model",
            "contract_manifest_hash",
        ):
            with self.subTest(field=name):
                self.assertTrue(getattr(manifest, name), f"{name} is empty")
        self.assertEqual(manifest.run_manifest_version, RUN_MANIFEST_VERSION)

    def test_log_line_names_the_run_and_flags_unknowns(self) -> None:
        line = _manifest().log_line()
        self.assertIn("[test_run_manifest]", line)
        self.assertIn("test_run_id=run_", line)
        self.assertIn("provider_mode=LOCAL_OPENAI_COMPATIBLE", line)
        # The MQL half is not known until the first request arrives; say so
        # rather than printing an empty field that reads as "none".
        self.assertIn("mql_session_id=awaiting_mql", line)
        self.assertIn("ea_name=awaiting_mql", line)


class MqlBindingTests(unittest.TestCase):
    def test_first_request_completes_the_run_identity(self) -> None:
        manifest = _manifest()
        changed = absorb_mql_identity(
            manifest,
            {
                "session_id": "mql_5830_1782698459",
                "ea_name": "PO3_AIGate_PositivePath_Harness",
                "runtime_input_hash": "1383015757",
                "tester_ai_mode": 2,
            },
        )
        self.assertTrue(changed)
        self.assertEqual(manifest.mql_session_id, "mql_5830_1782698459")
        self.assertEqual(manifest.ea_name, "PO3_AIGate_PositivePath_Harness")
        self.assertEqual(manifest.tester_mode, "2")
        self.assertIn("ea_name=PO3_AIGate_PositivePath_Harness", manifest.log_line())

    def test_absorb_is_idempotent_so_it_logs_once(self) -> None:
        manifest = _manifest()
        payload = {"session_id": "mql_1", "ea_name": "EA"}
        self.assertTrue(absorb_mql_identity(manifest, payload))
        self.assertFalse(absorb_mql_identity(manifest, payload))

    def test_a_second_ea_does_not_overwrite_the_first(self) -> None:
        """One run has one EA; a second EA means a second run."""
        manifest = _manifest()
        absorb_mql_identity(manifest, {"ea_name": "PO3_AIGate_ScannerEA"})
        absorb_mql_identity(manifest, {"ea_name": "PO3_AIGate_PositivePath_Harness"})
        self.assertEqual(manifest.ea_name, "PO3_AIGate_ScannerEA")


class StampingTests(unittest.TestCase):
    def test_requests_and_responses_are_stamped(self) -> None:
        manifest = _manifest()
        request = stamp({"id": "req-1"}, manifest)
        response = stamp({"request_id": "req-1"}, manifest)
        self.assertEqual(request["test_run_id"], manifest.test_run_id)
        self.assertEqual(response["test_run_id"], manifest.test_run_id)

    def test_single_run_evidence_is_accepted(self) -> None:
        manifest = _manifest()
        records = [stamp({"i": i}, manifest) for i in range(5)]
        self.assertEqual(assert_single_run(records), manifest.test_run_id)

    def test_mixed_run_evidence_is_rejected(self) -> None:
        """A report must not silently merge two sessions."""
        a = _manifest(python_session_id="s_a")
        b = _manifest(python_session_id="s_b", provider_id="openai_remote_api", model="gpt")
        records = [stamp({"i": 0}, a), stamp({"i": 1}, b)]
        self.assertEqual(len(runs_in(records)), 2)
        with self.assertRaises(ValueError) as ctx:
            assert_single_run(records)
        self.assertIn("evidence_spans_multiple_runs", str(ctx.exception))

    def test_unstamped_records_do_not_invent_a_run(self) -> None:
        self.assertEqual(runs_in([{"i": 0}]), set())
        self.assertEqual(assert_single_run([{"i": 0}]), "")


class ManifestFileTests(unittest.TestCase):
    def test_manifest_is_published_where_mql_can_read_it(self) -> None:
        manifest = _manifest()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(manifest, tmp)
            self.assertEqual(path.name, RUN_MANIFEST_FILENAME)
            self.assertEqual(path.parent.name, "logs")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["test_run_id"], manifest.test_run_id)
            self.assertEqual(data["run_manifest_version"], RUN_MANIFEST_VERSION)

    def test_write_is_atomic_leaving_no_partial_file(self) -> None:
        manifest = _manifest()
        with tempfile.TemporaryDirectory() as tmp:
            write_manifest(manifest, tmp)
            leftovers = list((Path(tmp) / "logs").glob("*.tmp"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
