import unittest

import ai_gate

from ai_gate import (
    Decision,
    _compact_model_payload,
    _hard_model_rejection_codes,
    _normalize_advisory_metadata,
    _runtime_inputs,
)


class AIGateLogicTests(unittest.TestCase):
    def test_legacy_confidence_setting_is_diagnostic_only(self) -> None:
        payload = {"runtime_inputs": {"min_ai_confidence": 0.35}}
        runtime = _runtime_inputs(payload)
        self.assertEqual(runtime["legacy_min_ai_confidence_diagnostic"], 0.35)
        self.assertFalse(hasattr(ai_gate, "_runtime_min_confidence"))

    def test_optional_snapshot_failures_are_not_trade_rejections(self) -> None:
        payload = {"runtime": {"require_snapshots": False}}
        decision = Decision(
            allow=False,
            score=6.5,
            rejection_codes=["CAPTURE_FAILED", "DISTANT_FROM_FVG"],
            invalidation_risks=["snapshot_capture_failed", "entry_extended"],
            missing_confirmations=["HTF and LTF chart capture", "live mitigation"],
        )
        codes, risks, missing = _normalize_advisory_metadata(payload, decision)
        self.assertEqual(codes, ["DISTANT_FROM_FVG"])
        self.assertEqual(risks, ["entry_extended"])
        self.assertEqual(missing, ["live mitigation"])

    def test_required_snapshot_failures_remain_visible(self) -> None:
        payload = {"runtime": {"require_snapshots": True}}
        decision = Decision(allow=False, score=0.0, rejection_codes=["CAPTURE_FAILED"])
        codes, _, _ = _normalize_advisory_metadata(payload, decision)
        self.assertEqual(codes, ["CAPTURE_FAILED"])

    def test_clearance_score_is_named_unambiguously_for_model(self) -> None:
        compact = _compact_model_payload(
            {
                "fvg": {"opposing_obstruction_score": 9.0},
                "candidates": [{"opposing_obstruction_score": 8.0}],
            }
        )
        self.assertEqual(compact["fvg"]["opposing_clearance_score"], 9.0)
        self.assertEqual(compact["candidates"][0]["opposing_clearance_score"], 8.0)
        self.assertNotIn("opposing_obstruction_score", compact["fvg"])

    def test_only_structural_codes_are_hard_vetoes(self) -> None:
        hard = _hard_model_rejection_codes(
            ["DISTANT_FROM_FVG", "FVG_STRUCTURE_INVALIDATED", "OBSTACLE_NEARBY_RT"]
        )
        self.assertEqual(hard, ["FVG_STRUCTURE_INVALIDATED"])


if __name__ == "__main__":
    unittest.main()
