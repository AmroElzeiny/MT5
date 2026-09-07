import json
import tempfile
import unittest
from pathlib import Path

import ai_gate

from ai_gate import (
    Decision,
    _compact_model_payload,
    _hard_model_rejection_codes,
    _normalize_advisory_metadata,
    _runtime_inputs,
    _select_tester_cache_requests,
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

    def test_model_rejection_codes_have_no_implicit_trade_authority(self) -> None:
        hard = _hard_model_rejection_codes(
            ["DISTANT_FROM_FVG", "FVG_STRUCTURE_INVALIDATED", "OBSTACLE_NEARBY_RT"]
        )
        self.assertEqual(hard, [])

    def test_tester_cache_selection_is_bounded_balanced_and_deterministic(self) -> None:
        families = {
            "micro_continuation_fvg": 9,
            "micro_bisi_sibi_edge": 4,
            "micro_range_reentry": 3,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            ordinal = 0
            for family, count in families.items():
                for _ in range(count):
                    ordinal += 1
                    path = root / f"request_{ordinal:03d}.json"
                    path.write_text(
                        json.dumps(
                            {
                                "tester_cache_signature": f"signature-{ordinal}",
                                "candidates": [{"setup_family": family}],
                            }
                        ),
                        encoding="utf-8",
                    )
                    paths.append(path)

            selected_a, summary_a = _select_tester_cache_requests(
                paths,
                max_requests=6,
                max_per_family=2,
            )
            selected_b, summary_b = _select_tester_cache_requests(
                list(reversed(paths)),
                max_requests=6,
                max_per_family=2,
            )

            self.assertEqual([path.name for path in selected_a], [path.name for path in selected_b])
            self.assertEqual(summary_a, summary_b)
            self.assertEqual(summary_a["requests_selected"], 6)
            self.assertEqual(summary_a["requests_deferred"], 10)
            self.assertEqual(
                summary_a["selected_primary_families"],
                {
                    "micro_bisi_sibi_edge": 2,
                    "micro_continuation_fvg": 2,
                    "micro_range_reentry": 2,
                },
            )


if __name__ == "__main__":
    unittest.main()
