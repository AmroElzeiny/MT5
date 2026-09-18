"""The setup-score floor is an exact, per-family gate enforced before the AI.

User rule (2026-09-16): "if we say 170 is the floor, then we shouldn't have -5 and -11".

Before this change the configured family number was never the floor that bound:

* ``_ApplyPreAiSetupFloor`` derived soft = floor - 5 and hard = soft - 6 (the -5/-11),
  mutated ``setup_score`` with a penalty, and let ``InpAiStrict`` decide which bound
  rejected.  An active policy file could replace both bounds.
* ``_DeterministicSetupScoreFloor`` replaced every stale FVG's floor with
  ``max(Range, Reversal) + 4`` (a stale micro_po3_reversal needed 175, not 149) and
  added tier B/C extras.
* ``_FamilySetupFloor`` returned the session floor (28) for ANY family on a
  ``session_reentry`` branch, so a full-PO3 continuation could pass at 28.
* An unknown family fell through to the lenient range floor.

Pinned here: the family input is the floor, every family owns one input, nothing can
move it, an unmapped family fails closed, and one chokepoint in
``_QueueCandidateGroup`` enforces it before identity, bootstrap, cache and every
provider request.  The v3 presets are pinned against the Config input set.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_governance_contracts import MQL_STAGE, _function_body  # noqa: E402

PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parent
ENGINE = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")
CONFIG = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="ignore")
TYPES = (MQL_STAGE / "Types.mqh").read_text(encoding="utf-8", errors="ignore")
EXPECTANCY = (PYTHON_ROOT / "expectancy_report.py").read_text(encoding="utf-8", errors="ignore")

REQUIRED_FLOOR_DEFAULTS = {
    "InpSetupFloorReversal": 171.0,
    "InpSetupFloorBreaker": 170.0,
    "InpSetupFloorContinuation": 169.0,
    "InpSetupFloorSession": 28.0,
    "InpSetupFloorRange": 28.0,
    "InpSetupFloorFullPO3": 171.0,
    "InpSetupFloorMicroPO3": 149.0,
    "InpSetupFloorMicroBisiSibi": 170.0,
    "InpSetupFloorContinuationFamily": 169.0,
    "InpSetupFloorRangeFamily": 28.0,
    "InpSetupFloorFailedBreakout": 29.0,
}

FAMILY_FLOOR_INPUTS = {
    "full_po3_reversal": "InpSetupFloorFullPO3",
    "full_po3_continuation": "InpSetupFloorFullPO3",
    "micro_po3_reversal": "InpSetupFloorMicroPO3",
    "micro_continuation_fvg": "InpSetupFloorContinuationFamily",
    "micro_failed_breakout_reclaim": "InpSetupFloorFailedBreakout",
    "micro_bisi_sibi_edge": "InpSetupFloorMicroBisiSibi",
}

REMOVED_POLICY_FIELDS = ("soft_setup_floor", "hard_setup_floor", "setup_floor_penalty_mult")


def _code_only(source: str) -> str:
    """Strip line comments so prose about a token is never mistaken for the token."""

    return re.sub(r"//[^\n]*", "", source)


def _config_inputs() -> dict[str, str]:
    pattern = re.compile(r"^\s*input\s+[\w:]+\s+(Inp\w+)\s*=\s*([^;]*);", re.MULTILINE)
    return {name: value.strip() for name, value in pattern.findall(_code_only(CONFIG))}


def _read_preset(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO_ROOT / name).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.split("||", 1)[0].strip()
    return values


class ExactFloorGateTests(unittest.TestCase):
    def test_pre_ai_floor_has_no_soft_or_hard_derivation(self) -> None:
        body = _code_only(_function_body(ENGINE, "_ApplyPreAiSetupFloor"))
        for forbidden in ("soft_floor", "hard_floor", "InpAiStrict", "m_active_policy",
                          "setup_score -=", "strict_reject", "soft_penalty"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)
        self.assertNotRegex(body, r"floor\s*-\s*\d", "no offset may be subtracted from the floor")
        self.assertRegex(body, r"p\.setup_score\s*<\s*floor\b")
        self.assertIn('"setup_score_below_floor"', body)
        self.assertIn('_Journal("[setup_floor_gate]', body)

    def test_floor_is_never_modified_by_staleness_or_context_tier(self) -> None:
        body = _code_only(_function_body(ENGINE, "_DeterministicSetupScoreFloor"))
        for forbidden in ("stale_fvg", "context_tier", "TierBExtra", "TierCExtra", "+="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)
        self.assertNotIn("InpSetupFloorTierBExtra", CONFIG)
        self.assertNotIn("InpSetupFloorTierCExtra", CONFIG)

    def test_execution_gate_uses_the_same_exact_floor(self) -> None:
        body = _code_only(_function_body(ENGINE, "_DeterministicExecutionGate"))
        self.assertIn("_DeterministicSetupScoreFloor(p, floor_input)", body)
        self.assertNotIn("soft_penalty", body)
        self.assertNotRegex(body, r"setup_floor_score\s*-\s*\d")

    def test_every_family_owns_its_floor_input(self) -> None:
        body = _code_only(_function_body(ENGINE, "_FamilySetupFloor"))
        for family, input_name in FAMILY_FLOOR_INPUTS.items():
            with self.subTest(family=family):
                self.assertRegex(
                    body,
                    rf'"{family}"[^{{}}]*\{{\s*floor_input\s*=\s*"{input_name}"\s*;\s*return\s+{input_name}\s*;',
                )
        range_block = body[body.index('"micro_range_reentry"'):]
        self.assertRegex(
            range_block,
            r"MICRO_SESSION_REENTRY\s*\)\s*\{\s*floor_input\s*=\s*\"InpSetupFloorSession\"\s*;\s*return\s+InpSetupFloorSession\s*;",
        )
        self.assertRegex(range_block, r"return\s+InpSetupFloorRangeFamily\s*;")

    def test_no_entry_branch_can_replace_a_family_floor(self) -> None:
        body = _code_only(_function_body(ENGINE, "_FamilySetupFloor"))
        self.assertNotIn("entry_branch", body)
        self.assertNotIn("session_reentry", body)

    def test_unmapped_family_fails_closed(self) -> None:
        body = _code_only(_function_body(ENGINE, "_FamilySetupFloor"))
        self.assertRegex(
            body,
            r'floor_input\s*=\s*"unmapped_family"\s*;\s*return\s+DBL_MAX\s*;\s*\}\s*$',
        )
        self.assertNotRegex(body, r"return\s+InpSetupFloorRange\s*;\s*\}\s*$")

    def test_pre_ai_chokepoint_precedes_identity_bootstrap_cache_and_requests(self) -> None:
        body = _code_only(_function_body(ENGINE, "_QueueCandidateGroup"))
        gate_at = body.find("[setup_floor_gate] stage=pre_ai_queue")
        self.assertGreater(gate_at, -1, "the group dispatcher must enforce the floor itself")
        self.assertLess(body.find("_FilterOutcomePolicyBeforeAI"), gate_at)
        for later in ("_PrepareDecisionIdentity", "_QueueTesterBootstrapCandidate",
                      "_TryLoadTesterAiDecision", "SendRequestCandidates"):
            with self.subTest(later=later):
                self.assertGreater(body.find(later), gate_at)
        self.assertRegex(body, r"setup_score\s*>=\s*cand_floor")
        self.assertNotRegex(body[:body.find("_PrepareDecisionIdentity")], r"setup_score\s*=\s*_SetupScore")

    def test_active_policy_cannot_move_the_floor(self) -> None:
        engine = _code_only(ENGINE)
        snapshot = re.search(r"struct\s+ActivePolicySnapshot\s*\{(.*?)\};", _code_only(TYPES), re.S)
        self.assertIsNotNone(snapshot, "ActivePolicySnapshot must stay declared in Types.mqh")
        for field in REMOVED_POLICY_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(f"m_active_policy.{field}", engine)
                self.assertNotRegex(snapshot.group(1), rf"\b{field}\s*;")
                self.assertNotIn(f'"{field}"', EXPECTANCY)
        self.assertIn("reason=setup_floor_input_authority", engine)

    def test_config_floor_defaults_match_deterministic_preset(self) -> None:
        inputs = _config_inputs()
        for name, expected in REQUIRED_FLOOR_DEFAULTS.items():
            with self.subTest(input=name):
                self.assertAlmostEqual(float(inputs[name]), expected)


class V3PresetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = _config_inputs()

    def _assert_complete(self, preset: dict[str, str]) -> None:
        self.assertEqual(set(self.inputs), set(preset), "a preset must carry exactly the Config inputs")

    def test_scalp_v3_enables_ai_and_shadow_with_exact_floors(self) -> None:
        preset = _read_preset("scalp_v3.set")
        self._assert_complete(preset)
        expected = {
            "InpUseAI": "true",
            "InpTesterAiMode": "0",
            "InpTesterAiCache": "true",
            "InpShadowCandidateLedgerEnable": "true",
            "InpShadowTrackPreAiRejects": "true",
            "InpShadowMaxEvaluationsPerTick": "25",
            "InpShadowMaxDataRetries": "30",
            "InpAllowRuleOnlyLive": "false",
            "InpStrategyPreset": "scalp_po3",
            "InpMagicNumber": "5303192",
        }
        for name, value in expected.items():
            with self.subTest(input=name):
                self.assertEqual(value, preset[name])
        for name, value in REQUIRED_FLOOR_DEFAULTS.items():
            with self.subTest(floor=name):
                self.assertAlmostEqual(value, float(preset[name]))

    def test_swing_v3_carries_the_deterministic_floors(self) -> None:
        preset = _read_preset("swing_v3.set")
        self._assert_complete(preset)
        self.assertEqual("swing_po3", preset["InpStrategyPreset"])
        self.assertEqual("5303191", preset["InpMagicNumber"])
        for name, value in REQUIRED_FLOOR_DEFAULTS.items():
            with self.subTest(floor=name):
                self.assertAlmostEqual(value, float(preset[name]))


if __name__ == "__main__":
    unittest.main()
