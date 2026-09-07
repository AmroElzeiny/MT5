"""The replay cohort re-key is unsound, and this pins the reason in the MQL source.

`tester_cache_rekey` was written on the premise that `DecisionInputHash()` appears
in a cache signature exactly once, as component 4, so a decision-identity
correction could be absorbed by renaming.  The engine says otherwise:

    TradeEngine.mqh:2760  _CandidateHash
        canonical += m_ai.DecisionHash() + "|" + ENGINE_INPUT_SCHEMA + ...
    TradeEngine.mqh:2784  _ExecutionFingerprint
        canonical += m_ai.DecisionHash() + "|" + ENGINE_INPUT_SCHEMA + ...

Every candidate hash therefore moves with the decision identity, and those hashes
are embedded in the per-candidate section of the same signature.  Measured on
2026-09-06: after re-keying 1,346 artifacts 671051197 -> 82474443 the replay
reported `cohort ... match=true verdict=replayable` together with
`ai_cache_hits_total=0 ai_cache_misses_total=1365`.

These tests fail the day someone re-arms the module without removing the
dependency, and the last one fails the day the dependency is removed -- which is
the signal that the module may be re-armed.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tester_cache_rekey import (  # noqa: E402
    REKEY_DISARMED_REASON,
    RekeyUnsound,
    decision_hash_component,
    plan_rekey,
    rekey_signature,
)
# Aliased on import: pytest collects any module-level name starting with "test",
# so importing tester_cache_key directly turns the function into a broken test.
from tester_cache_rekey import tester_cache_key as cache_key  # noqa: E402
from test_governance_contracts import MQL_STAGE, _function_body  # noqa: E402

ENGINE = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")


def _code_only(source: str) -> str:
    return re.sub(r"//[^\n]*", "", source)


class RekeyIsDisarmedTests(unittest.TestCase):
    def test_plan_rekey_refuses_by_default(self) -> None:
        with self.assertRaises(RekeyUnsound) as caught:
            plan_rekey(Path("."), "111", "222")
        self.assertIn("TradeEngine.mqh:2760", str(caught.exception))

    def test_the_refusal_names_the_supported_alternative(self) -> None:
        self.assertIn("RECORD_ONLY", REKEY_DISARMED_REASON)
        self.assertIn("CACHE_ONLY", REKEY_DISARMED_REASON)

    def test_cli_exits_nonzero_instead_of_writing(self) -> None:
        from tester_cache_rekey import _main

        self.assertEqual(2, _main([".", "111", "222"]))
        self.assertEqual(2, _main([".", "111", "222", "--apply"]))


class DecisionIdentityDependencyTests(unittest.TestCase):
    """The reason the re-key cannot work, asserted against the engine itself."""

    def test_candidate_hash_mixes_in_the_decision_identity(self) -> None:
        body = _code_only(_function_body(ENGINE, "_CandidateHash"))
        self.assertIn(
            "m_ai.DecisionHash()",
            body,
            "if this ever leaves _CandidateHash the cohort becomes re-keyable and"
            " tester_cache_rekey may be re-armed",
        )

    def test_execution_fingerprint_mixes_in_the_decision_identity(self) -> None:
        body = _code_only(_function_body(ENGINE, "_ExecutionFingerprint"))
        self.assertIn("m_ai.DecisionHash()", body)

    def test_candidate_hash_is_embedded_in_the_cache_signature(self) -> None:
        """Both halves are needed: a moving hash only matters if the key carries it.

        `_GroupSignature` / `_CandidateSignature` carry only plan prices and times.
        The candidate hashes are appended by `_TesterAiCacheSignature` itself, in
        the per-plan tail after the group signature -- which is precisely where the
        2026-09-06 diff diverged while every printed field stayed equal.
        """

        body = _code_only(_function_body(ENGINE, "_TesterAiCacheSignature"))
        self.assertIn(
            'sig += "|" + plans[i].candidate_hash;',
            body,
            "the signature carries every candidate hash, which is why a moving"
            " DecisionHash() cannot be absorbed by renaming component 4",
        )
        self.assertIn(
            "m_ai.DecisionHash()",
            body,
            "component 4 is the direct occurrence; the candidate hashes are the"
            " indirect ones",
        )


class CacheKeyPortTests(unittest.TestCase):
    """The port stays under test even though the module that uses it is disarmed."""

    def test_key_is_the_signed_32_bit_fnv1a_with_minus_replaced(self) -> None:
        # Reference values recomputed from the algorithm, not from an artifact, so
        # this test does not depend on any file on disk.
        for text in ("", "a", "20260724_canonical_frozen_request_v10|x"):
            h = 2166136261
            for ch in text:
                h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
            signed = h - 0x100000000 if h >= 0x80000000 else h
            self.assertEqual(str(signed).replace("-", "n"), cache_key(text))

    def test_key_never_contains_a_path_hostile_character(self) -> None:
        for text in ("negative-producing-input", "another", "third one"):
            key = cache_key(text)
            self.assertNotIn("-", key)
            self.assertRegex(key, r"^n?\d+$")

    def test_component_extraction_and_replacement_are_inverses(self) -> None:
        signature = "v10|v6|v12|671051197|6#GOLD|BUY|rest"
        self.assertEqual("671051197", decision_hash_component(signature))
        moved = rekey_signature(signature, "82474443")
        self.assertEqual("82474443", decision_hash_component(moved))
        self.assertEqual(signature.split("|")[4:], moved.split("|")[4:])

    def test_planning_logic_still_works_when_the_premise_is_asserted(self) -> None:
        """Exercised through the private flag so the logic itself stays covered."""

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            signature = "v10|v6|v12|111|6#GOLD|BUY|rest"
            (cache / f"{cache_key(signature)}.json").write_text(
                json.dumps({"cache_signature": signature}), encoding="utf-8"
            )
            plan = plan_rekey(cache, "111", "222", _candidate_hash_is_identity_free=True)
            self.assertEqual(1, len(plan.entries))
            self.assertEqual(0, len(plan.conflicts))
            self.assertEqual("222", decision_hash_component(plan.entries[0].new_signature))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
