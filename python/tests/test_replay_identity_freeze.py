"""Regression coverage for the replay-identity drift that voided a whole cohort.

The 2026.09.06 CACHE_ONLY replay (2026.08.03-08.08, 25 symbols) recorded:

    ai_cache_hits_total=89   ai_cache_misses_total=1276
    ai_advisories_total=89   ai_final_allow_true_total=0
    watchlist_added_total=0  orders_placed_total=0  trades_opened_total=0

while the replay cache on disk held 11 distinct APPROVED request ids.  None of
them was reachable.

`_TesterAiCacheSignature` is

    AI_DECISION_SCHEMA_VERSION | AI_TARGET_ARBITRATION_SCHEMA_VERSION
    | AI_PROMPT_CONTRACT_VERSION | DecisionHash() | _GroupSignature(plans) | ...

so `DecisionHash()` -- component 4 -- is shared by every request in a cohort.
It was recomputed on every lookup.  Measured from that run's own journal:

    every one of the 122 `[ai_cache] hit=true` lines carried 671051197
    the last hit was at simulated 2026.08.03 09:19:59
    from 2026.08.03 09:34:59 onward all 1,266 remaining misses carried
        1953583338, a value present in zero cache artifacts on disk
    two further values (570642039, 1211926014) were written by the same run

and two call sites four milliseconds apart in the same OnInit disagreed:
the startup policy manifest recorded runtime_input_hash 1019017657 while the
journal line printed 722861386.

The identity a replay addresses its cohort with must therefore be computed once
and frozen, and any later disagreement must be reported rather than adopted.
A miss must also say which cohort it was addressing: before the fix, a key with
no artifact behind it returned false without emitting a single journal line, so
"this run is addressing the wrong cohort" and "this setup was never recorded"
looked identical.

Each test below fails on the pre-fix MQL sources.
"""

from __future__ import annotations

import re
import unittest

from test_governance_contracts import MQL_STAGE, _function_body

BRIDGE = (MQL_STAGE / "AIGateBridge.mqh").read_text(encoding="utf-8", errors="ignore")
ENGINE = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")


def _code_only(source: str) -> str:
    """Drop // comments so an assertion cannot be satisfied by prose about the code.

    Caught during falsification: the first version of
    test_engine_freezes_identity_during_init passed against a tree where the
    FreezeReplayIdentity call had been deleted, because a comment three lines
    above it still named the function.
    """

    return re.sub(r"//[^\n]*", "", source)


class ReplayIdentityFreezeTests(unittest.TestCase):
    def test_decision_input_hash_accessor_returns_the_frozen_value(self) -> None:
        """The accessor every signature goes through must not recompute."""

        body = _function_body(BRIDGE, "DecisionInputHash")
        self.assertIn(
            "m_frozen_decision_input_hash",
            body,
            "DecisionInputHash() must return the frozen identity; recomputing it is"
            " what re-keyed the cohort mid-run",
        )
        self.assertIn("m_identity_frozen", body)

    def test_runtime_input_hash_accessor_returns_the_frozen_value(self) -> None:
        body = _function_body(BRIDGE, "RuntimeInputHash")
        self.assertIn("m_frozen_runtime_input_hash", body)
        self.assertIn("m_identity_frozen", body)

    def test_the_computing_versions_still_exist_and_are_separate(self) -> None:
        """Freezing must keep a way to recompute, or drift becomes undetectable."""

        for name in ("_ComputeDecisionInputHash", "_ComputeRuntimeInputHash"):
            body = _function_body(BRIDGE, name)
            self.assertIn("_Fnv1a", body, f"{name} must still derive the hash itself")

    def test_freeze_sets_every_identity_member(self) -> None:
        body = _function_body(BRIDGE, "FreezeReplayIdentity")
        for member in (
            "m_frozen_decision_input_hash",
            "m_frozen_runtime_input_hash",
            "m_frozen_policy_content_hash",
            "m_frozen_invalidation_content_hash",
            "m_identity_frozen",
        ):
            self.assertIn(member, body, f"FreezeReplayIdentity must set {member}")

    def test_drift_check_reports_but_never_adopts(self) -> None:
        """Adopting a drifted identity is exactly the defect under repair."""

        body = _function_body(BRIDGE, "CheckIdentityDrift")
        self.assertIn("m_identity_drift_events++", body)
        for member in (
            "m_frozen_decision_input_hash",
            "m_frozen_runtime_input_hash",
            "m_frozen_policy_content_hash",
            "m_frozen_invalidation_content_hash",
        ):
            assignment = re.search(rf"{member}\s*=(?!=)", body)
            self.assertIsNone(
                assignment,
                f"CheckIdentityDrift must not assign {member}; the frozen identity is"
                " the authority and drift is a report, not an update",
            )

    def test_engine_freezes_identity_during_init(self) -> None:
        body = _code_only(_function_body(ENGINE, "Init"))
        self.assertIn(
            "m_ai.FreezeReplayIdentity()",
            body,
            "the replay identity must be frozen in Init, before anything consumes it",
        )
        freeze_at = body.index("m_ai.FreezeReplayIdentity()")
        for consumer in ("_LoadDeploymentManifest", "SetRuntimeScope"):
            if consumer in body:
                self.assertLess(
                    freeze_at,
                    body.index(consumer),
                    f"{consumer} runs before the identity is frozen",
                )

    def test_absent_cache_artifact_is_journalled(self) -> None:
        """The silent miss path is what hid the whole failure for a full run."""

        body = _code_only(_function_body(ENGINE, "_TryLoadTesterAiDecision"))
        self.assertIn("no_cache_artifact", body)
        self.assertIn("m_total_ai_cache_miss_no_artifact++", body)
        self.assertIn("decision_input_hash=", body)
        # The absent-artifact branch must be the one guarded by the failed read,
        # not a dead block left beside a still-silent early return.
        read_guard = re.search(
            r"if\(!m_bus\.ReadText\(_TesterAiCachePath\(signature\), txt\)\)\s*\{([^}]*)",
            body,
        )
        self.assertIsNotNone(
            read_guard, "the failed cache read must open a block, not return silently"
        )
        self.assertIn("no_cache_artifact", read_guard.group(1))

    def test_cache_miss_reject_names_the_cohort_it_addressed(self) -> None:
        self.assertIn("cache_cohort_decision_hash=", ENGINE)
        self.assertIn("tester_ai_cache_cohort", ENGINE)
        probe = _function_body(ENGINE, "_ProbeTesterCacheCohort")
        self.assertIn("cache_signature", probe)
        self.assertIn("_SignatureDecisionHashComponent", probe)

    def test_cohort_probe_reads_component_four(self) -> None:
        """Component 4 is the decision hash; reading 3 or 5 silently misreports."""

        body = _function_body(BRIDGE, "_ComputeDecisionInputHash")
        self.assertIn("ENGINE_INPUT_SCHEMA", body)
        component = _function_body(ENGINE, "_SignatureDecisionHashComponent")
        self.assertIn("field<3", component.replace(" ", ""))

    def test_policy_content_hash_reads_bytes_not_decoded_text(self) -> None:
        """FILE_TXT is UTF-16 here, and both policy files are odd-length ASCII.

        Under scan-time file I/O load the decoded read returned different strings
        for a file whose mtime had not moved since 2026.07.17 -- 49736647 at init,
        then 1990245157 / 1989935653 / 1984261413 -- which is what moved
        DecisionInputHash().  A content hash must hash content, not a decode.
        """

        body = _code_only(_function_body(BRIDGE, "_CommonFileContentHash"))
        self.assertIn("FILE_BIN", body)
        self.assertNotIn("FILE_TXT", body)
        self.assertNotIn(
            "FileReadString",
            body,
            "FileReadString re-enters the UTF-16 decode this fix exists to remove",
        )
        self.assertIn("FileReadArray", body)
        self.assertIn("_Fnv1aBytes", body)
        self.assertIn(
            "!= size",
            body,
            "a short read must fail closed as UNAVAILABLE, not hash as new content",
        )

    def test_execution_identity_names_missing_and_disagreeing_timestamps_apart(self) -> None:
        """One reason string covered two different conditions.

        The #Japan225 entry of 2026.08.03 wrote a terminal
        BROKER_ACCEPTED_IDENTITY_QUARANTINED with
        reason=position_open_time_mismatch and then a terminal
        POSITION_FILLED_IDENTITY_VERIFIED for the same order/deal pair.  Whether
        the first was a real timestamp disagreement or simply an unsettled read
        could not be told from the reason string, because `<= 0` and
        `> tolerance` both reported "mismatch".  Both still fail closed.
        """

        body = _code_only(_function_body(ENGINE, "_ResolveExactExecutionIdentity"))
        self.assertIn("position_open_time_unavailable", body)
        self.assertIn("position_open_time_mismatch", body)
        self.assertIn("delta_sec=", body)
        self.assertIn("tolerance_sec=", body)
        unavailable = body.index("position_open_time_unavailable")
        mismatch = body.index('reason = "position_open_time_mismatch')
        self.assertLess(
            unavailable,
            mismatch,
            "the missing-timestamp guard must run first, or a zero timestamp is"
            " reported as a disagreement with tolerance",
        )

    def test_final_summary_reports_identity_and_cohort(self) -> None:
        for token in (
            "decision_identity_frozen=",
            "decision_identity_drift_events=",
            "cache_cohort=",
            "cache_cohort_match=",
            "ai_cache_miss_no_artifact_total=",
        ):
            self.assertIn(
                token,
                ENGINE,
                f"[final_summary] must report {token} so a voided cohort is visible"
                " without reparsing the journal",
            )


if __name__ == "__main__":
    unittest.main()
