"""Regression coverage for the four infrastructure-rejection defects.

The 2026-08-12 evening live session produced 29 decisions, 0 approvals and 11
(38%) infrastructure rejections on healthy infrastructure:

    5  structured_response_invalid          -> one bad candidate killed the whole
                                               response via deterministic RR
    5  decision_evidence_reference_reject   -> mis-scoped evidence IDs, plus a
                                               wasted repair provider call
    1  local_pipeline_error                 -> a local ValueError logged as a
                                               provider call failure

Each test below fails on the pre-fix code.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import ai_gate
from decision_evidence import build_decision_evidence_envelope
from evidence_catalog import build_evidence_catalog

BUS = Path(
    r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS"
)


def _load(path: Path) -> dict:
    for encoding in ("utf-16", "utf-8-sig", "utf-8", "utf-16-le"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    raise ValueError(f"unreadable_fixture:{path}")


def _multi_candidate_request() -> dict | None:
    """A real captured request whose catalog has >= 2 candidate-scoped rows."""

    directory = BUS / "rejected"
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("python_*.json"), reverse=True):
        if path.name.endswith(".meta.json"):
            continue
        try:
            payload = _load(path)
        except ValueError:
            continue
        if len(payload.get("candidates") or []) >= 2:
            return payload
    return None


class EvidenceReferenceNormalizationTests(unittest.TestCase):
    """Fix #3: a mis-scoped citation must not cost a provider call."""

    def setUp(self) -> None:
        payload = _multi_candidate_request()
        self.assertIsNotNone(payload, "no captured multi-candidate request available")
        envelope = build_decision_evidence_envelope(payload).envelope
        self.catalog = build_evidence_catalog(envelope)
        # Partition catalog IDs by the candidate they are scoped to.  Only
        # ``catalog.items`` carries the scope; provider_rows() deliberately
        # exposes just id/path/value to the model.
        #
        # Restricted to the CITABLE rows.  Identity rows (candidate_hash,
        # execution fingerprints) are catalogued with authority
        # ``internal_identity``, withheld from provider_rows and refused by
        # resolve(), so an id taken from ``items`` alone can be one the model is
        # never offered -- and those sort first in each candidate scope, so the
        # unrestricted version tested "unknown id" while claiming to test
        # "mis-scoped id".
        citable = {int(row["id"]) for row in self.catalog.provider_rows()}
        self.by_candidate: dict[int, list[int]] = {}
        for item in self.catalog.items:
            if item.candidate_index is None:
                continue
            if int(item.evidence_id) not in citable:
                continue
            self.by_candidate.setdefault(
                int(item.candidate_index), []
            ).append(int(item.evidence_id))
        self.assertGreaterEqual(
            len(self.by_candidate), 2, "catalog must expose >= 2 candidate scopes"
        )
        self.owner, self.sibling = sorted(self.by_candidate)[:2]

    def test_cross_candidate_ids_are_dropped_not_rejected(self) -> None:
        own_ids = self.by_candidate[self.owner][:3]
        foreign_id = self.by_candidate[self.sibling][0]

        paths, diagnostics = ai_gate._resolve_evidence_reference_ids(
            catalog=self.catalog,
            raw_ids=[*own_ids, foreign_id],
            legacy_refs=None,
            candidate_index=self.owner,
            field_name="evidence_refs",
        )

        self.assertTrue(paths, "correctly scoped evidence must survive")
        self.assertTrue(diagnostics["valid"])
        self.assertEqual(
            diagnostics.get("normalization"), "cross_candidate_ids_dropped"
        )
        self.assertIn(foreign_id, diagnostics["dropped_ids"])
        self.assertEqual(len(paths), len(own_ids))

    def test_unknown_ids_still_fail_closed(self) -> None:
        own_ids = self.by_candidate[self.owner][:2]
        bogus = 10_000_000

        with self.assertRaises(ai_gate.EvidenceReferenceError):
            ai_gate._resolve_evidence_reference_ids(
                catalog=self.catalog,
                raw_ids=[*own_ids, bogus],
                legacy_refs=None,
                candidate_index=self.owner,
                field_name="evidence_refs",
            )

    def test_only_foreign_ids_fail_closed(self) -> None:
        """Dropping everything must never yield an evidence-free acceptance."""

        foreign_ids = self.by_candidate[self.sibling][:2]

        with self.assertRaises(ai_gate.EvidenceReferenceError):
            ai_gate._resolve_evidence_reference_ids(
                catalog=self.catalog,
                raw_ids=list(foreign_ids),
                legacy_refs=None,
                candidate_index=self.owner,
                field_name="evidence_refs",
            )


class LocalErrorLabellingTests(unittest.TestCase):
    """Fix #5: a local validation failure must not be named a provider failure."""

    def test_local_validation_is_not_labelled_as_provider_call_failure(self) -> None:
        import inspect

        source = inspect.getsource(ai_gate._score_setup_ai)
        self.assertIn("selected_local_validation_failed", source)
        self.assertIn("failure_domain=local_validation", source)
        # The provider label must now be reachable only under ProviderCallError.
        provider_label_at = source.index("selected_provider_call_failed")
        guard_at = source.index("isinstance(e, ProviderCallError)")
        self.assertLess(
            guard_at,
            provider_label_at,
            "provider label must be guarded by a real ProviderCallError check",
        )


class DeterministicRrDemotionTests(unittest.TestCase):
    """Fix #2: a wrong-side TP1 must scope its failure to its own candidate."""

    def test_rr_violation_demotes_candidate_instead_of_raising(self) -> None:
        import inspect

        source = inspect.getsource(ai_gate)
        start = source.index("rr_violations: list[str] = []")
        window = source[start : start + 3000]
        # The violation is recorded, not raised.
        self.assertIn("rr_violations.append", window)
        self.assertIn("deterministic_rr_non_positive", window)
        self.assertIn("demoted_to_abstain", window)
        self.assertIn("[deterministic_rr_demotion]", window)
        # And it must still fail closed for that candidate.
        self.assertIn("analytical[\"raw_allow\"] = False", window)
        self.assertIn("analytical[\"suggested_risk_multiplier\"] = 0.0", window)
        self.assertNotIn(
            'raise ValueError(\n                        f"deterministic_rr_non_positive',
            window,
        )


if __name__ == "__main__":
    unittest.main()
