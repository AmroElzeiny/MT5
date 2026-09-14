"""Losslessness and coupling contract for the compact provider-wire projection (Phase 2)."""

import json
import re
import sys
import unittest
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

import provider_wire_projection as wire  # noqa: E402
from evidence_catalog import (  # noqa: E402
    AUTHORITY_DETERMINISTIC,
    AUTHORITY_INTERNAL_IDENTITY,
    EvidenceCatalog,
    EvidenceItem,
)

PHASE1_ANALYST_SENTENCE = (
    "The payload contains evidence_catalog.items, where each item has id, p (the Python-owned "
    "canonical path), v (the observed value), and optionally c (the candidate index it belongs "
    "to). Items without c are global."
)


def _catalog(candidates: int = 3) -> EvidenceCatalog:
    items: list[EvidenceItem] = []

    def add(scope, path, value, authority=AUTHORITY_DETERMINISTIC):
        items.append(
            EvidenceItem(
                evidence_id=len(items),
                candidate_index=scope,
                canonical_path=path,
                value=value,
                value_hash="h",
                authority=authority,
            )
        )

    for name, value in (("bos_level", 1.2345), ("context_tier", "B"), ("developing_bos", True), ("t_follow", 0)):
        add(None, f"sequence.{name}", value)
    add(None, "market_regime.regime", "EXPANSION")
    add(None, "provider_decision_context.version", "v3")
    for index in range(candidates):
        base = f"entry_and_invalidation.candidates.{index}."
        add(index, base + "candidate_id", "EURUSD|1|2", AUTHORITY_INTERNAL_IDENTITY)
        add(index, base + "obstacle_kind", "crossed_opposing_imbalance")
        add(index, base + "authoritative_numbers.obstacle_distance_r.value", 0.27 + index)
        add(index, base + "authoritative_numbers.rr2.value", 1.0)
        add(index, base + "shadow_historical_evidence.tp1_before_sl_rate", None)
        add(index, base + "target_candidates.liquidity_target.feasible_for_tp2", False)
        add(index, base + "family_requirement_contract.required_event_sequence", ["dealing range", "BOS"])
        add(index, base + "target_semantics", {"keep_current": "liquidity_target", "n": 2})
        add(index, base + "note", "x" * 40 + "—")
    add(None, "validation.hard_blockers", [])
    return EvidenceCatalog(
        catalog_version="evidence_catalog_test_v1",
        items=tuple(items),
        catalog_hash="c" * 64,
        _by_id={item.evidence_id: item for item in items},
        _by_path={item.canonical_path: item for item in items},
    )


def _evidence(catalog: EvidenceCatalog) -> dict:
    rows = catalog.provider_rows()
    return {
        "request_id": "req-1",
        "evidence_catalog": {
            "catalog_version": catalog.catalog_version,
            "catalog_hash": catalog.catalog_hash,
            "items": rows,
            "usage": "Cite evidence only by these integer ids in evidence_ref_ids.",
        },
        "entry_and_invalidation": {
            "candidates": [
                {
                    "candidate_index": index,
                    "allowed_evidence_ref_ids": [
                        row["id"] for row in rows if row.get("c") in (None, index)
                    ],
                }
                for index in range(3)
            ]
        },
    }


def _facts_from_canonical(evidence: dict) -> set:
    return {
        (row["id"], row.get("c"), "c" in row, row["p"], wire.canonical_wire_json(row["v"]))
        for row in evidence["evidence_catalog"]["items"]
    }


def _facts_from_compact(evidence: dict) -> set:
    facts = set()
    for group in evidence["evidence_catalog"]["items"]:
        for evidence_id, suffix, value in group["rows"]:
            facts.add(
                (evidence_id, group.get("c"), "c" in group, group["p_prefix"] + suffix, wire.canonical_wire_json(value))
            )
    return facts


class CompactWireLosslessnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()
        self.evidence = _evidence(self.catalog)
        self.projected = wire.project_evidence_for_wire(self.evidence, wire.WIRE_PROJECTION_COMPACT_V1)

    def test_round_trip_restores_the_byte_identical_canonical_wire(self):
        self.assertIsNot(self.projected, self.evidence)
        self.assertEqual(
            wire.canonical_wire_json(wire.expand_evidence_from_wire(self.projected)),
            wire.canonical_wire_json(self.evidence),
        )

    def test_every_id_scope_path_and_typed_value_survives(self):
        self.assertEqual(_facts_from_compact(self.projected), _facts_from_canonical(self.evidence))

    def test_value_types_are_not_coerced(self):
        values = {row[0]: row[2] for group in self.projected["evidence_catalog"]["items"] for row in group["rows"]}
        by_id = {row["id"]: row["v"] for row in self.evidence["evidence_catalog"]["items"]}
        for evidence_id, value in by_id.items():
            self.assertIs(type(values[evidence_id]), type(value), evidence_id)

    def test_compact_wire_is_strictly_shorter(self):
        self.assertLess(
            len(wire.canonical_wire_json(self.projected)),
            len(wire.canonical_wire_json(self.evidence)),
        )

    def test_everything_outside_the_catalog_rows_is_untouched(self):
        for key, value in self.evidence.items():
            if key != "evidence_catalog":
                self.assertIs(self.projected[key], value)
        catalog = self.projected["evidence_catalog"]
        for key in ("catalog_version", "catalog_hash", "usage"):
            self.assertEqual(catalog[key], self.evidence["evidence_catalog"][key])
        self.assertEqual(catalog["wire_format"], wire.COMPACT_V1_WIRE_FORMAT)

    def test_internal_identity_rows_stay_withheld(self):
        withheld = {item.evidence_id for item in self.catalog.items if item.authority == AUTHORITY_INTERNAL_IDENTITY}
        sent = {row[0] for group in self.projected["evidence_catalog"]["items"] for row in group["rows"]}
        self.assertTrue(withheld)
        self.assertFalse(withheld & sent)

    def test_citation_scope_and_validation_are_identical(self):
        canonical_rows = self.evidence["evidence_catalog"]["items"]
        groups = self.projected["evidence_catalog"]["items"]
        for index in range(3):
            canonical_ids = [row["id"] for row in canonical_rows if row.get("c") in (None, index)]
            compact_ids = sorted(
                row[0] for group in groups if group.get("c") in (None, index) and ("c" not in group or group["c"] == index)
                for row in group["rows"]
            )
            self.assertEqual(sorted(canonical_ids), compact_ids)
            allowed = self.projected["entry_and_invalidation"]["candidates"][index]["allowed_evidence_ref_ids"]
            self.assertEqual(allowed, canonical_ids)
            resolution = self.catalog.resolve(allowed, candidate_index=index)
            self.assertTrue(resolution.valid)
        foreign = next(row["id"] for row in canonical_rows if row.get("c") == 1)
        self.assertEqual(self.catalog.resolve([foreign], candidate_index=0).cross_candidate_ids, (foreign,))

    def test_every_path_keeps_a_non_empty_suffix(self):
        for group in self.projected["evidence_catalog"]["items"]:
            for _id, suffix, _value in group["rows"]:
                self.assertTrue(suffix)
            if group["p_prefix"]:
                self.assertTrue(group["p_prefix"].endswith("."))

    def test_candidate_groups_factor_the_candidate_path_head(self):
        heads = {group["p_prefix"] for group in self.projected["evidence_catalog"]["items"] if "c" in group}
        self.assertEqual(heads, {f"entry_and_invalidation.candidates.{i}." for i in range(3)})

    def test_projection_is_idempotent(self):
        self.assertIs(wire.project_evidence_for_wire(self.projected, wire.WIRE_PROJECTION_COMPACT_V1), self.projected)
        self.assertEqual(
            wire.canonical_wire_json(wire.expand_evidence_from_wire(self.evidence)),
            wire.canonical_wire_json(self.evidence),
        )


class CanonicalAndFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = _evidence(_catalog())

    def test_canonical_empty_and_unknown_projections_return_the_same_object(self):
        for projection in ("canonical", "", None, "CANONICAL", "bogus_v9"):
            self.assertIs(wire.project_evidence_for_wire(self.evidence, projection), self.evidence, projection)
        self.assertIsNone(wire.normalize_wire_projection("bogus_v9"))
        self.assertEqual(wire.normalize_wire_projection(" Compact_V1 "), wire.WIRE_PROJECTION_COMPACT_V1)

    def test_non_canonical_rows_are_never_projected(self):
        mutations = {
            "extra_key": lambda rows: rows[0].__setitem__("x", 1),
            "descending_ids": lambda rows: rows.reverse(),
            "bool_id": lambda rows: rows[0].__setitem__("id", True),
            "null_scope": lambda rows: rows[0].__setitem__("c", None),
            "missing_value": lambda rows: rows[0].pop("v"),
            "non_string_path": lambda rows: rows[0].__setitem__("p", 7),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                evidence = json.loads(json.dumps(self.evidence))
                mutate(evidence["evidence_catalog"]["items"])
                self.assertIs(wire.project_evidence_for_wire(evidence, "compact_v1"), evidence)

    def test_payload_without_catalog_is_untouched(self):
        payload = {"request_id": "r", "candidate": {"x": 1}}
        self.assertIs(wire.project_evidence_for_wire(payload, "compact_v1"), payload)

    def test_analyst_sentence_for_canonical_is_the_phase1_text(self):
        self.assertEqual(wire.analyst_catalog_description("canonical"), PHASE1_ANALYST_SENTENCE)
        self.assertEqual(wire.analyst_catalog_description("bogus"), PHASE1_ANALYST_SENTENCE)
        compact = wire.analyst_catalog_description("compact_v1")
        for token in ("p_prefix", "rows", "[id, p, v]", "Groups without c are global"):
            self.assertIn(token, compact)
        for token in ("p_prefix", "rows", "[id, p, v]", "without c is global"):
            self.assertIn(token, wire.COMPACT_V1_WIRE_FORMAT)


class CallSiteCouplingTests(unittest.TestCase):
    """Every provider call must encode evidence through the projection, and the analyst
    prompt must describe the encoding that is sent; no call is added or removed."""

    def _calls(self, filename: str) -> list[str]:
        source = (PYTHON_ROOT / filename).read_text(encoding="utf-8")
        return [source[m.start(): m.start() + 900] for m in re.finditer(r"\.generate_structured\(", source)]

    def test_every_ai_gate_and_pipeline_provider_call_uses_the_projection(self):
        expected_counts = {"ai_gate.py": 3, "decision_pipeline.py": 3}
        for filename, count in expected_counts.items():
            calls = self._calls(filename)
            self.assertEqual(len(calls), count, filename)
            for block in calls:
                evidence_arg = re.search(r"evidence=([^\n]*)", block)
                self.assertIsNotNone(evidence_arg, filename)
                self.assertIn("project_evidence_for_wire(", evidence_arg.group(1), filename)

    def test_analyst_prompt_sentence_follows_the_configured_projection(self):
        source = (PYTHON_ROOT / "ai_gate.py").read_text(encoding="utf-8")
        self.assertNotIn(PHASE1_ANALYST_SENTENCE, source)
        self.assertIn("analyst_catalog_description(", source)


if __name__ == "__main__":
    unittest.main()
