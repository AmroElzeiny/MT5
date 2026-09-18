"""Per-candidate direction authority for mixed buy/sell requests.

Live evidence 2026-09-15 01:47-02:47: 7 of 7 EURAUD requests whose candidates
mixed buy and sell died as ``structured_response_invalid`` with
``deterministic_rr_stop_direction_invalid``; 0 of 63 single-direction requests
did.  The request-level ``is_buy`` is ``plans[0].is_buy`` in
``AIGateBridge.BuildRequestJson`` -- it describes candidate 0 only -- yet Python
applied it to every candidate, both in the analyst binder and in the hard
pre-gate, and the MQL candidate serializer never emitted a per-candidate
direction at all.

Tests marked "retention guard" pass on the pre-fix tree by design: they pin
the strictness the fix must keep, not the defect it removes.
"""

from __future__ import annotations

import unittest

import ai_gate
from decision_evidence import build_decision_evidence_envelope
from evidence_catalog import build_evidence_catalog
from tests.test_decision_integrity import candidate, model_assessment
from tests.test_governance_contracts import MQL_STAGE
from tests.test_python_owned_identity_lifecycle import _comparison, _payload


def _without_direction(cand: dict) -> dict:
    cand.pop("is_buy", None)
    cand.pop("direction", None)
    return cand


def _buy_primary() -> dict:
    return _without_direction(candidate(0, "A"))


def _sell_secondary() -> dict:
    cand = _without_direction(candidate(1, "B"))
    cand.update({"entry_est": 100.0, "sl": 101.0, "tp1": 99.0, "tp2": 98.0})
    return cand


def _row(cand: dict, catalog, *, state: str) -> dict:
    index = cand["candidate_index"]
    ids = [
        row["id"]
        for row in catalog.provider_rows()
        if row.get("c") in (None, index)
    ][:2]
    row = model_assessment(cand, state=state, evidence_ref_ids=ids)
    arbitration = row["target_arbitration"]
    arbitration["target_comparison"] = _comparison()
    tp1, tp2 = float(cand["tp1"]), float(cand["tp2"])
    risk = abs(float(cand["entry_est"]) - float(cand["sl"]))
    arbitration.update(
        {
            "chosen_tp1": tp1,
            "chosen_tp2": tp2,
            "chosen_rr1": abs(tp1 - float(cand["entry_est"])) / risk,
            "chosen_rr2": abs(tp2 - float(cand["entry_est"])) / risk,
        }
    )
    return row


def _bind(candidates: list[dict], *, request_is_buy: bool, selected: int = 1):
    payload = _payload(candidates)
    payload["is_buy"] = request_is_buy
    catalog = build_evidence_catalog(
        build_decision_evidence_envelope(payload).envelope
    )
    rows = [
        _row(cand, catalog, state="APPROVE" if cand["candidate_index"] == selected else "ABSTAIN")
        for cand in candidates
    ]
    provider_result = type(
        "R",
        (),
        {
            "provider_id": "direction-test-provider",
            "actual_model": "direction-test-model",
            "schema_fingerprint": "fingerprint",
        },
    )()
    return ai_gate._bind_python_owned_analyst_envelope(
        model_output={
            "decision_quality_tier": "FULL_STRUCTURED",
            "response_quality": "FULL_STRUCTURED",
            "selected_candidate_index": selected,
            "candidate_assessments": rows,
            "reasons": "analysis only",
        },
        candidates=candidates,
        enriched_candidates=[{"rule_score": 7.2} for _ in candidates],
        request_id="request-direction",
        request_identity_hash="REQUESTIDENTITY1234567890",
        ordered_candidate_identities=[
            {
                "candidate_index": cand["candidate_index"],
                "candidate_id": cand["candidate_id"],
                "candidate_hash": cand["candidate_hash"],
                "request_execution_fingerprint": cand["request_execution_fingerprint"],
                "setup_snapshot_time": int(cand.get("setup_snapshot_time") or 0),
                "setup_taxonomy_enum": cand["setup_taxonomy_enum"],
            }
            for cand in candidates
        ],
        provider_result=provider_result,
        evidence_catalog=catalog,
        request_is_buy=request_is_buy,
    )


class BinderDirectionAuthorityTests(unittest.TestCase):
    def test_sell_candidate_in_buy_led_request_binds_with_its_own_direction(self) -> None:
        envelope, _diagnostics = _bind(
            [_buy_primary(), _sell_secondary()], request_is_buy=True
        )
        rows = {row["candidate_index"]: row for row in envelope["candidate_assessments"]}
        self.assertEqual(rows[1]["decision_state"], "APPROVE")
        self.assertEqual(rows[1]["target_arbitration"]["chosen_rr2"], 2.0)
        self.assertEqual(rows[0]["target_arbitration"]["chosen_rr2"], 2.0)

    def test_contradictory_geometry_without_direction_fails_closed(self) -> None:
        sell = _sell_secondary()
        sell.update({"tp1": 101.5, "tp2": 102.0})  # stop and target on the same side
        with self.assertRaisesRegex(
            ValueError, r"deterministic_candidate_direction_unresolved:candidate_index=1"
        ):
            _bind([_buy_primary(), sell], request_is_buy=True)

    def test_primary_candidate_keeps_request_direction_authority(self) -> None:
        """Retention guard: candidate 0 is still checked against the request direction."""

        with self.assertRaisesRegex(
            ValueError, r"deterministic_rr_stop_direction_invalid:candidate_index=0"
        ):
            _bind([_buy_primary(), _sell_secondary()], request_is_buy=False)


class HardPreGateDirectionTests(unittest.TestCase):
    def _payload(self) -> dict:
        payload = _payload([_buy_primary(), _sell_secondary()])
        payload["is_buy"] = True
        return payload

    def test_non_primary_sell_candidate_is_not_read_as_buy(self) -> None:
        payload = self._payload()
        _entry, _sl, _tp2, is_buy = ai_gate._entry_stop_target_values(
            payload["candidates"][1], payload
        )
        self.assertFalse(is_buy)

    def test_primary_candidate_uses_request_direction(self) -> None:
        """Retention guard."""

        payload = self._payload()
        payload["is_buy"] = False
        _entry, _sl, _tp2, is_buy = ai_gate._entry_stop_target_values(
            payload["candidates"][0], payload
        )
        self.assertFalse(is_buy)

    def test_contradictory_non_primary_geometry_still_fails_a_side_check(self) -> None:
        """Retention guard: an unresolvable direction can never pass both side checks."""

        payload = self._payload()
        item = payload["candidates"][1]
        item.update({"tp1": 101.5, "tp2": 102.0})
        entry, sl, tp2, is_buy = ai_gate._entry_stop_target_values(item, payload)
        stop_wrong = (is_buy and sl >= entry) or ((not is_buy) and sl <= entry)
        target_wrong = (is_buy and tp2 <= entry) or ((not is_buy) and tp2 >= entry)
        self.assertTrue(stop_wrong or target_wrong)


class MqlCandidateDirectionSerializationTests(unittest.TestCase):
    def test_every_request_candidate_carries_its_own_direction(self) -> None:
        source = (MQL_STAGE / "AIGateBridge.mqh").read_text(encoding="utf-8")
        start = source.index('j += JsonKVInt("candidate_index", i) + ",";')
        candidate_object = source[start : start + 1500]
        self.assertIn('JsonKVBool("is_buy", c.is_buy)', candidate_object)


if __name__ == "__main__":
    unittest.main()
