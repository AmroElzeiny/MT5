"""Hard pre-gate parity between the EA and Python, and the gate lease evidence.

Live evidence, 2026-09-14 04:21.  The EA queued five requests; Python's hard
pre-gate refused three of them in under a second with
``synthetic_fallback_crossed_obstacle_blocked``.  All 13 candidates carried
``target_arbitration_required=true`` and ``liquidity_target.available=false``:
Python waives the crossed-obstacle rule only when a real liquidity route is
available, while the EA waived it on the arbitration flag alone.  The EA then
journaled the deterministic refusal as ``degraded_ai_response_non_trading`` and
counted it as a decision-integrity failure.

The lease: back-to-back ``[single_instance] acquired=true`` lines from two pids
could not be told apart from two concurrent holders, and the Windows mutex lived
only in the per-session ``Local\\`` namespace.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

import ai_gate
from test_governance_contracts import MQL_STAGE, _function_body


def _engine() -> str:
    return (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")


def _bridge() -> str:
    return (MQL_STAGE / "AIGateBridge.mqh").read_text(encoding="utf-8", errors="ignore")


def _config() -> str:
    return (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="ignore")


def _crossed_synthetic_candidate(liquidity_available: bool) -> dict:
    return {
        "target_source": "synthetic_rr_fallback",
        "tp_model": "synthetic_rr_fallback",
        "obstacle_kind": "crossed_opposing_imbalance",
        "target_arbitration_required": True,
        "target_candidates": {
            "arbitration_required": True,
            "obstacle_kind": "crossed_opposing_imbalance",
            "liquidity_target": {"available": liquidity_available, "tp2": 207.095},
        },
    }


class HardPreGateParityTests(unittest.TestCase):
    def test_python_waives_a_crossed_synthetic_only_when_liquidity_is_available(self) -> None:
        """The rule the EA must mirror, pinned on the Python side."""

        payload = {"runtime_inputs": {"reject_synthetic_fallback_after_crossed_obstacle": True}}
        self.assertEqual(
            ai_gate._candidate_hard_block_reason(_crossed_synthetic_candidate(False), payload),
            "synthetic_fallback_crossed_obstacle_blocked",
        )
        self.assertNotEqual(
            ai_gate._candidate_hard_block_reason(_crossed_synthetic_candidate(True), payload),
            "synthetic_fallback_crossed_obstacle_blocked",
        )

    def test_ea_pre_ai_waiver_requires_the_liquidity_verdict(self) -> None:
        engine = _engine()
        body = _function_body(engine, "_HardSuppressionGate")
        self.assertIn("bool arbitration_waiver = p.target_arbitration_required;", body)
        self.assertRegex(
            body,
            r'if\(stage == "pre_ai_hard_gate" && _PythonSeesSyntheticFallback\(p\) && '
            r"!_LiquidityTargetFeasible\(p\)\)\s*arbitration_waiver = false;",
        )
        self.assertIn("!arbitration_waiver", body)
        self.assertIn('reason = "synthetic_fallback_crossed_obstacle_blocked"', body)
        self.assertIn(
            "PlanLiquidityTargetVerdict(",
            _function_body(engine, "_LiquidityTargetFeasible"),
        )
        # Python's synthetic test is a substring of target_source or of the first
        # non-empty target_model/tp_model; capped synthetics are not included.
        synthetic = _function_body(engine, "_PythonSeesSyntheticFallback")
        self.assertIn("StringLen(p.target_model) > 0 ? p.target_model : p.tp_model", synthetic)
        self.assertEqual(synthetic.count('"synthetic_rr_fallback"'), 2)
        self.assertNotIn("capped", synthetic.replace("synthetic_rr_capped_*", ""))

    def test_menu_and_gate_share_one_liquidity_definition(self) -> None:
        menu = _function_body(_bridge(), "_TargetCandidatesJson")
        self.assertIn(
            "PlanLiquidityTargetVerdict(p, liquidity_tp, liquidity_rr, liquidity_rr_ok, liquidity_within_max)",
            menu,
        )
        self.assertNotRegex(menu, r"liquidity_within_max\s*=\s*RewardWithinMaxDistance")
        self.assertNotRegex(menu, r"liquidity_rr_ok\s*=\s*\(")
        verdict = _function_body(_bridge(), "PlanLiquidityTargetVerdict")
        self.assertIn("RewardWithinMaxDistance(", verdict)
        self.assertIn("InpMinLiveRR2", verdict)
        self.assertIn("p.fallback_max_allowed_distance", verdict)

    def test_hard_pre_gate_refusal_has_its_own_label_and_stage(self) -> None:
        self.assertRegex(_config(), r'const string AI_PYTHON_HARD_PRE_GATE_SOURCE = "hard_pre_gate";')
        self.assertIn('decision_source="hard_pre_gate",', Path(ai_gate.__file__).read_text(encoding="utf-8"))
        self.assertRegex(
            _bridge(),
            r"out\.llm_quality_reject_reason = \(out\.decision_source == AI_PYTHON_HARD_PRE_GATE_SOURCE"
            r'\s*\?\s*LLM_QUALITY_REJECT_PYTHON_HARD_PRE_GATE\s*:\s*"degraded_ai_response_non_trading"\)',
        )
        engine = _engine()
        self.assertIn(
            "bool python_hard_pre_gate = (!strict_response_quality && "
            "dec.decision_source == AI_PYTHON_HARD_PRE_GATE_SOURCE);",
            engine,
        )
        self.assertRegex(
            engine,
            r'python_hard_pre_gate \? LLM_QUALITY_REJECT_PYTHON_HARD_PRE_GATE\s*:\s*"degraded_ai_response_non_trading"',
        )
        self.assertIn('(python_hard_pre_gate ? "python_hard_pre_gate"', engine)
        # Still a genuine rejection for cooldown purposes, not an infrastructure one.
        self.assertNotIn(
            "python_hard_pre_gate",
            _function_body(engine, "_InfrastructureAiRejection"),
        )


class GateLeaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bus = Path(self._tmp.name) / "PO3_AI_BUS"

    def tearDown(self) -> None:
        ai_gate._release_gate_single_instance()
        self._tmp.cleanup()

    def _holder(self) -> dict:
        return json.loads(ai_gate._gate_instance_holder_path(self.bus).read_text(encoding="utf-8"))

    def test_acquire_records_the_holder_and_release_marks_it(self) -> None:
        acquired, identity = ai_gate._acquire_gate_single_instance(self.bus)
        self.assertTrue(acquired)
        record = self._holder()
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["lease"], identity)
        self.assertIsNone(record["released_at"])
        self.assertIn("previous_holder_released=none", ai_gate._gate_instance_acquire_evidence())

        ai_gate._release_gate_single_instance()
        self.assertIsNotNone(self._holder()["released_at"])

        reacquired, _ = ai_gate._acquire_gate_single_instance(self.bus)
        self.assertTrue(reacquired)
        evidence = ai_gate._gate_instance_acquire_evidence()
        self.assertIn(f"previous_holder_pid={os.getpid()}", evidence)
        self.assertIn("previous_holder_released=true", evidence)
        self.assertEqual(ai_gate._gate_instance_anomaly(), "")

    def test_unreleased_live_previous_holder_is_reported_but_not_enforced(self) -> None:
        path = ai_gate._gate_instance_holder_path(self.bus)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"pid": os.getppid(), "started_at": 1.0, "released_at": None}),
            encoding="utf-8",
        )
        acquired, _ = ai_gate._acquire_gate_single_instance(self.bus)
        self.assertTrue(acquired, "the OS lease stays authoritative")
        self.assertIn("previous_holder_alive=true", ai_gate._gate_instance_acquire_evidence())
        self.assertIn("[single_instance_anomaly]", ai_gate._gate_instance_anomaly())

    def test_dead_previous_holder_is_not_an_anomaly(self) -> None:
        dead_pid = 4194300
        if ai_gate._gate_holder_process_state(dead_pid) != "false":
            self.skipTest("chosen pid is not provably absent on this host")
        path = ai_gate._gate_instance_holder_path(self.bus)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"pid": dead_pid, "started_at": 1.0, "released_at": None}),
            encoding="utf-8",
        )
        acquired, _ = ai_gate._acquire_gate_single_instance(self.bus)
        self.assertTrue(acquired)
        self.assertIn("previous_holder_alive=false", ai_gate._gate_instance_acquire_evidence())
        self.assertEqual(ai_gate._gate_instance_anomaly(), "")

    @unittest.skipUnless(os.name == "nt", "Windows named mutex namespaces")
    def test_windows_lease_takes_the_global_and_the_legacy_local_name(self) -> None:
        acquired, identity = ai_gate._acquire_gate_single_instance(self.bus)
        self.assertTrue(acquired)
        self.assertEqual(
            ai_gate._GATE_INSTANCE_LEASE["mutex_names"],
            [f"Global\\PO3_AI_GATE_{identity}", f"Local\\PO3_AI_GATE_{identity}"],
        )

    @unittest.skipUnless(os.name == "nt", "Windows named mutex namespaces")
    def test_a_gate_holding_only_the_legacy_local_name_still_excludes(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        identity = sha256(str(self.bus.resolve()).lower().encode("utf-8")).hexdigest()[:32]
        legacy = kernel32.CreateMutexW(None, False, f"Local\\PO3_AI_GATE_{identity}")
        self.assertTrue(legacy)
        try:
            acquired, reason = ai_gate._acquire_gate_single_instance(self.bus)
            self.assertFalse(acquired)
            self.assertIn("existing_gate_for_bus", reason)
            self.assertIsNone(ai_gate._GATE_INSTANCE_LEASE)
        finally:
            kernel32.CloseHandle(legacy)
        acquired, _ = ai_gate._acquire_gate_single_instance(self.bus)
        self.assertTrue(acquired, "both names must have been released by the refused attempt")


if __name__ == "__main__":
    unittest.main()
