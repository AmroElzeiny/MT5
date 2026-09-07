"""Regression coverage for Python-owned evidence references and live latency.

Fixtures are the six real requests captured from the 2026-07-30 tester run.
On the pre-fix code every one of the three successful FULL_STRUCTURED responses
was downgraded with ``decision_evidence_reference_reject`` because the analyst
had to spell exact canonical paths out of a namespace it was never shown --
1,614 leaf paths for the three-candidate request and 3,180 for six candidates.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import ai_gate
from decision_evidence import build_decision_evidence_envelope
from evidence_catalog import (
    EVIDENCE_CATALOG_VERSION,
    build_evidence_catalog,
    normalize_legacy_reference,
    resolve_legacy_references,
)

BUS = Path(
    r"C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS"
)
CAPTURED_PREFIX = "python_23160_1785430030__"

# Observed provider latency from the 2026-07-30 run, by candidate count.
OBSERVED_LATENCY_SEC = {1: 24.383, 3: 64.812, 6: 87.863}
PROVIDER_DEADLINE_SEC = 90.0


def _load(path: Path) -> dict:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    raise ValueError(f"unreadable_fixture:{path}")


def _captured_requests() -> list[dict]:
    directory = BUS / "rejected"
    if not directory.is_dir():
        return []
    payloads = []
    for path in sorted(directory.glob(f"{CAPTURED_PREFIX}*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            payloads.append(_load(path))
        except ValueError:
            continue
    return payloads


def _synthetic_envelope() -> dict:
    """A small envelope so the suite still runs without the captured bus."""

    return {
        "sequence": {"has_sweep": True, "has_displacement": True, "has_bos": False},
        "liquidity": {"session_name": "LON", "in_killzone": True},
        "validation": {"missing_fields": [], "invalid_fields": [], "hard_blockers": []},
        "instrument": {"symbol": "GOLD", "asset_class": "metal"},
        "entry_and_invalidation": {
            "candidates": [
                {
                    "candidate_index": 0,
                    "candidate_id": "cand-A",
                    "candidate_hash": "HASH-A",
                    "structure_state": "confirmed",
                    "authoritative_numbers": {
                        "entry": {"value": 100.0},
                        "net_rr": {"value": 2.4},
                    },
                },
                {
                    "candidate_index": 1,
                    "candidate_id": "cand-B",
                    "candidate_hash": "HASH-B",
                    "structure_state": "forming",
                    "authoritative_numbers": {
                        "entry": {"value": 101.0},
                        "net_rr": {"value": 1.8},
                    },
                },
            ]
        },
    }


class CatalogStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_evidence_catalog(_synthetic_envelope())

    def test_catalog_is_versioned_and_hashed(self) -> None:
        self.assertEqual(self.catalog.catalog_version, EVIDENCE_CATALOG_VERSION)
        self.assertTrue(self.catalog.catalog_hash)

    def test_catalog_is_deterministic(self) -> None:
        again = build_evidence_catalog(_synthetic_envelope())
        self.assertEqual(self.catalog.catalog_hash, again.catalog_hash)
        self.assertEqual(
            [item.canonical_path for item in self.catalog.items],
            [item.canonical_path for item in again.items],
        )

    def test_ids_are_dense_and_zero_based(self) -> None:
        self.assertEqual(
            [item.evidence_id for item in self.catalog.items],
            list(range(len(self.catalog))),
        )

    def test_python_owns_path_hash_and_authority(self) -> None:
        for item in self.catalog.items:
            self.assertTrue(item.canonical_path)
            self.assertEqual(len(item.value_hash), 64)
            self.assertEqual(item.authority, "deterministic")

    def test_provider_rows_omit_hash_and_authority(self) -> None:
        # The model gets id/path/value only; it never sees or supplies hashes.
        for row in self.catalog.provider_rows():
            self.assertNotIn("value_hash", row)
            self.assertNotIn("authority", row)
            self.assertIn("id", row)


class CatalogResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_evidence_catalog(_synthetic_envelope())
        self.global_ids = [i.evidence_id for i in self.catalog.items if i.candidate_index is None]
        self.cand0 = [i.evidence_id for i in self.catalog.items if i.candidate_index == 0]
        self.cand1 = [i.evidence_id for i in self.catalog.items if i.candidate_index == 1]

    def test_valid_global_evidence_id(self) -> None:
        result = self.catalog.resolve([self.global_ids[0]], candidate_index=0)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.resolved_paths), 1)

    def test_valid_candidate_specific_evidence_id(self) -> None:
        result = self.catalog.resolve([self.cand0[0]], candidate_index=0)
        self.assertTrue(result.valid)

    def test_global_evidence_is_citable_by_every_candidate(self) -> None:
        for index in (0, 1):
            with self.subTest(candidate_index=index):
                self.assertTrue(
                    self.catalog.resolve([self.global_ids[0]], candidate_index=index).valid
                )

    def test_unknown_id_fails_closed(self) -> None:
        result = self.catalog.resolve([10_000], candidate_index=0)
        self.assertFalse(result.valid)
        self.assertEqual(result.unknown_ids, (10_000,))

    def test_negative_id_fails_closed(self) -> None:
        result = self.catalog.resolve([-1], candidate_index=0)
        self.assertFalse(result.valid)
        self.assertEqual(result.unknown_ids, (-1,))

    def test_out_of_range_id_fails_closed(self) -> None:
        result = self.catalog.resolve([len(self.catalog)], candidate_index=0)
        self.assertFalse(result.valid)

    def test_cross_candidate_id_fails_closed(self) -> None:
        result = self.catalog.resolve([self.cand1[0]], candidate_index=0)
        self.assertFalse(result.valid)
        self.assertEqual(result.cross_candidate_ids, (self.cand1[0],))

    def test_duplicate_id_fails_closed(self) -> None:
        first = self.global_ids[0]
        result = self.catalog.resolve([first, first], candidate_index=0)
        self.assertFalse(result.valid)
        self.assertEqual(result.duplicate_ids, (first,))

    def test_empty_reference_list_fails_closed(self) -> None:
        self.assertFalse(self.catalog.resolve([], candidate_index=0).valid)

    def test_non_integer_ids_fail_closed(self) -> None:
        for bad in ("0", None, 1.5, True, [], {}):
            with self.subTest(bad=bad):
                self.assertFalse(self.catalog.resolve([bad], candidate_index=0).valid)


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_evidence_catalog(_synthetic_envelope())

    def test_supported_legacy_shapes_normalize(self) -> None:
        target = "entry_and_invalidation.candidates.0.candidate_hash"
        for raw in (
            "entry_and_invalidation.candidates.0.candidate_hash",
            "entry_and_invalidation.candidates[0].candidate_hash",
            "$.entry_and_invalidation.candidates[0].candidate_hash",
            'entry_and_invalidation["candidates"][0]["candidate_hash"]',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_legacy_reference(raw), target)

    def test_legacy_paths_resolve_to_real_catalog_items(self) -> None:
        resolution, _diag = resolve_legacy_references(
            self.catalog,
            ["entry_and_invalidation.candidates[0].candidate_hash", "sequence.has_sweep"],
            candidate_index=0,
        )
        self.assertTrue(resolution.valid)
        self.assertEqual(len(resolution.resolved), 2)

    def test_nonexistent_legacy_path_fails_closed(self) -> None:
        resolution, diagnostics = resolve_legacy_references(
            self.catalog, ["candidates[0].structure_state"], candidate_index=0
        )
        self.assertFalse(resolution.valid)
        entry = diagnostics["unresolved"][0]
        self.assertEqual(entry["normalized_reference"], "candidates.0.structure_state")
        self.assertEqual(entry["first_missing_token"], "candidates")

    def test_diagnostics_name_the_divergence_point(self) -> None:
        resolution, diagnostics = resolve_legacy_references(
            self.catalog,
            ["entry_and_invalidation.candidates.0.not_a_real_field"],
            candidate_index=0,
        )
        self.assertFalse(resolution.valid)
        entry = diagnostics["unresolved"][0]
        self.assertEqual(entry["first_missing_token"], "not_a_real_field")
        self.assertTrue(entry["nearest_valid_paths"])

    def test_legacy_migration_cannot_invent_evidence(self) -> None:
        resolution, _diag = resolve_legacy_references(
            self.catalog, ["totally.made.up.path"], candidate_index=0
        )
        self.assertFalse(resolution.valid)
        self.assertEqual(resolution.resolved, ())


class CapturedRequestFixtureTests(unittest.TestCase):
    """Uses the six real requests from the failing 2026-07-30 run."""

    def setUp(self) -> None:
        self.payloads = _captured_requests()
        if not self.payloads:
            self.skipTest("captured 2026-07-30 request fixtures not present")

    def test_six_requests_were_captured(self) -> None:
        self.assertEqual(len(self.payloads), 6)
        counts = sorted(len(p.get("candidates") or []) for p in self.payloads)
        self.assertEqual(counts, [1, 3, 6, 6, 6, 6])

    def test_catalog_is_orders_of_magnitude_smaller_than_the_leaf_namespace(self) -> None:
        for payload in self.payloads:
            with self.subTest(candidates=len(payload.get("candidates") or [])):
                envelope = build_decision_evidence_envelope(payload).envelope
                catalog = build_evidence_catalog(envelope)

                leaves = []

                def walk(node, prefix=""):
                    if isinstance(node, dict):
                        for key, value in node.items():
                            walk(value, f"{prefix}.{key}" if prefix else str(key))
                    elif isinstance(node, list):
                        for i, value in enumerate(node):
                            walk(value, f"{prefix}.{i}")
                    else:
                        leaves.append(prefix)

                walk(envelope)
                # The guessing surface shrinks by roughly an order of magnitude.
                self.assertLess(len(catalog) * 8, len(leaves))

    def test_every_candidate_has_citable_evidence(self) -> None:
        for payload in self.payloads:
            envelope = build_decision_evidence_envelope(payload).envelope
            catalog = build_evidence_catalog(envelope)
            count = len(payload.get("candidates") or [])
            for index in range(count):
                with self.subTest(candidates=count, candidate_index=index):
                    scoped = [i for i in catalog.items if i.candidate_index == index]
                    self.assertTrue(scoped, "candidate has no citable evidence")
                    self.assertTrue(
                        catalog.resolve([scoped[0].evidence_id], candidate_index=index).valid
                    )

    def test_compaction_reduces_the_provider_payload(self) -> None:
        for payload in self.payloads:
            count = len(payload.get("candidates") or [])
            with self.subTest(candidates=count):
                envelope = build_decision_evidence_envelope(payload).envelope
                catalog = build_evidence_catalog(envelope)
                compact = ai_gate._compact_model_evidence_payload(envelope, catalog)

                def size(obj) -> int:
                    return len(
                        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                    )

                before, after = size(envelope), size(compact)
                self.assertLess(after, before)
                if count >= 3:
                    # Measured 32-35% on the captured cohort; assert a floor.
                    self.assertGreater((before - after) / before, 0.25)

    def test_compaction_preserves_every_citable_item(self) -> None:
        for payload in self.payloads:
            envelope = build_decision_evidence_envelope(payload).envelope
            catalog = build_evidence_catalog(envelope)
            compact = ai_gate._compact_model_evidence_payload(envelope, catalog)
            rows = compact["evidence_catalog"]["items"]
            self.assertEqual(len(rows), len(catalog))
            self.assertEqual(
                compact["evidence_catalog"]["catalog_hash"], catalog.catalog_hash
            )

    def test_compaction_places_exact_allowed_ids_on_each_candidate(self) -> None:
        for payload in self.payloads:
            envelope = build_decision_evidence_envelope(payload).envelope
            catalog = build_evidence_catalog(envelope)
            compact = ai_gate._compact_model_evidence_payload(envelope, catalog)
            rows = compact["entry_and_invalidation"]["candidates"]
            for position, row in enumerate(rows):
                candidate_index = int(row.get("candidate_index", position))
                expected = [
                    item.evidence_id
                    for item in catalog.items
                    if item.candidate_index in (None, candidate_index)
                ]
                self.assertEqual(row["allowed_evidence_ref_ids"], expected)
                self.assertTrue(
                    catalog.resolve(
                        row["allowed_evidence_ref_ids"],
                        candidate_index=candidate_index,
                    ).valid
                )


class LatencyBudgetTests(unittest.TestCase):
    def test_observed_six_candidate_latency_exceeded_the_safe_margin(self) -> None:
        # The measurement that justifies a reduced live cohort.
        self.assertGreater(OBSERVED_LATENCY_SEC[6], 0.95 * PROVIDER_DEADLINE_SEC)
        self.assertLess(OBSERVED_LATENCY_SEC[3], 0.80 * PROVIDER_DEADLINE_SEC)

    def test_output_budget_scales_with_candidate_count(self) -> None:
        budgets = [ai_gate.analyst_output_token_budget(n, 25000) for n in (1, 3, 6)]
        self.assertEqual(budgets, sorted(budgets))
        self.assertTrue(all(b < 25000 for b in budgets))

    def test_output_budget_respects_the_configured_ceiling(self) -> None:
        self.assertLessEqual(ai_gate.analyst_output_token_budget(50, 4096), 4096)

    def test_output_budget_has_a_floor(self) -> None:
        self.assertGreaterEqual(ai_gate.analyst_output_token_budget(1, 1024), 1024)


class LiveCandidateCohortTests(unittest.TestCase):
    def _cohort(self, count: int, budget: int, families: list[str] | None = None):
        families = families or [f"FAM{i}" for i in range(count)]
        candidates = [
            {"candidate_index": i, "setup_family": families[i]} for i in range(count)
        ]
        enriched = [{"rule_score": float(count - i)} for i in range(count)]
        return ai_gate.select_live_candidate_cohort(candidates, enriched, budget=budget)

    def test_cohort_within_budget_keeps_every_candidate(self) -> None:
        chosen, deferred, reason = self._cohort(3, 3)
        self.assertEqual(len(chosen), 3)
        self.assertEqual(deferred, [])
        self.assertEqual(reason, "within_live_candidate_budget")

    def test_six_candidates_are_reduced_to_the_budget(self) -> None:
        chosen, deferred, reason = self._cohort(6, 3)
        self.assertEqual(len(chosen), 3)
        self.assertEqual(len(deferred), 3)
        self.assertIn("live_latency_budget", reason)

    def test_nothing_is_silently_dropped(self) -> None:
        chosen, deferred, _reason = self._cohort(6, 3)
        self.assertEqual(sorted(chosen + deferred), list(range(6)))

    def test_selection_prefers_highest_rule_score(self) -> None:
        chosen, _deferred, _reason = self._cohort(6, 3)
        self.assertIn(0, chosen, "highest rule score must survive")

    def test_selection_prefers_family_diversity(self) -> None:
        # Two families across six candidates: the cohort must not stack one.
        families = ["A", "A", "A", "B", "B", "B"]
        chosen, _deferred, _reason = self._cohort(6, 2, families)
        picked = {families[i] for i in chosen}
        self.assertEqual(picked, {"A", "B"})

    def test_selection_is_deterministic(self) -> None:
        self.assertEqual(self._cohort(6, 3)[0], self._cohort(6, 3)[0])

    def test_offline_cohort_is_not_reduced(self) -> None:
        # budget<=0 means "no live budget": offline keeps every candidate.
        chosen, deferred, _reason = self._cohort(6, 0)
        self.assertEqual(len(chosen), 6)
        self.assertEqual(deferred, [])


if __name__ == "__main__":
    unittest.main()
