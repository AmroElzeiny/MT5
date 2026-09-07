"""A CACHE_ONLY replay against an unaddressable cohort must be rejected at startup.

Observed 2026-09-07, agent 3000, a full 5-day replay the user ran by hand:

    [tester_ai_cache_cohort] decision_input_hash=82474443
        recorded_cohorts=671051197x181,1098270737x19 dominant=671051197
        match=false verdict=no_recorded_artifact_addressable_by_this_build_or_inputs
    [final_summary] ai_cache_hits_total=0 ai_cache_misses_total=1365
        ai_cache_miss_no_artifact_total=1365
    [final_summary] ai_advisories_total=0 ai_final_allow_true_total=0
        watchlist_added_total=0 orders_placed_total=0

11,375 scans and 136,101 valid plans produced not one AI decision, because the
recorded cohort was written under decision identity 671051197 and this build
computes 82474443.  The rejection of every lookup is correct fail-closed
behaviour; running the full pass to discover it is not.  A zero-approval run is
indistinguishable from a strategy failure to anyone reading the summary, and it
cost ~13 minutes and 846 MB of journal to produce nothing.

Two properties are pinned here:

1.  The match is decided by a census, not by the 200-file histogram sample.  It
    gates an abort, so a cohort of 5 artifacts inside 1,497 may not be invisible
    merely because the sample cap fell before it.
2.  The abort is scoped to CACHE_ONLY.  RECORD_ONLY starts against a foreign or
    empty cohort by design -- recording a new one is its entire purpose -- so
    aborting there would break the only supported recovery path.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_governance_contracts import MQL_STAGE, _function_body  # noqa: E402

ENGINE = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")


def _code_only(source: str) -> str:
    """Strip line comments so prose about a token is never mistaken for the token."""

    return re.sub(r"//[^\n]*", "", source)


class CohortMatchIsACensusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = _code_only(_function_body(ENGINE, "_ProbeTesterCacheCohort"))

    def test_the_sample_cap_does_not_end_the_scan_outright(self) -> None:
        """`if(sampled >= 200) break;` would settle the match on a partial view."""

        self.assertNotRegex(
            self.body,
            r"if\s*\(\s*sampled\s*>=\s*200\s*\)\s*break\s*;",
            "an unconditional break at the sample cap makes the abort decision"
            " depend on where the cap happened to fall",
        )

    def test_the_match_is_recorded_before_the_cap_is_consulted(self) -> None:
        match_at = self.body.find("m_tester_cache_cohort_matches = true")
        cap_at = self.body.find("sampled >= 200")
        self.assertGreater(match_at, -1, "the probe must record a cohort match")
        self.assertGreater(cap_at, -1, "the histogram must stay bounded")
        self.assertLess(
            match_at,
            cap_at,
            "every artifact must be tested for this build's identity before the"
            " histogram cap can skip or stop it",
        )

    def test_the_scan_only_stops_early_once_the_match_is_settled(self) -> None:
        self.assertRegex(
            self.body,
            r"if\s*\(\s*m_tester_cache_cohort_matches\s*\)\s*break\s*;",
            "past the histogram cap the loop has nothing left to learn once a"
            " match is found, so that is the only permitted early exit",
        )

    def test_the_match_is_not_derived_from_the_histogram(self) -> None:
        """The old code set the flag while summarising `hashes[]` -- the sample."""

        summary_loop = self.body[self.body.find("string summary") :]
        self.assertNotIn(
            "m_tester_cache_cohort_matches = true",
            summary_loop,
            "deriving the match from the histogram reintroduces the sampling bug",
        )

    def test_the_census_total_is_published(self) -> None:
        self.assertIn("m_tester_cache_cohort_total = total", self.body)
        self.assertIn("m_tester_cache_cohort_sampled = sampled", self.body)


class StartupRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = _code_only(_function_body(ENGINE, "Init"))

    def test_cache_only_rejects_an_unaddressable_cohort(self) -> None:
        self.assertRegex(
            self.body,
            r"if\s*\(\s*!\s*cohort_match\s*&&\s*_EffectiveTesterAiMode\(\)\s*=="
            r"\s*TESTER_AI_CACHE_ONLY\s*\)",
            "a cache-only replay with nothing addressable must not start",
        )

    def test_the_rejection_actually_fails_init(self) -> None:
        guard = re.search(
            r"if\s*\(\s*!\s*cohort_match\s*&&[^{]*\{(.*?)\n\s{9}\}",
            self.body,
            re.S,
        )
        self.assertIsNotNone(guard, "the cohort guard block was not found")
        block = guard.group(1)
        self.assertIn("tester_cache_cohort_unaddressable", block)
        self.assertRegex(
            block,
            r"return\s+false\s*;",
            "logging the verdict without failing Init still burns the whole run",
        )

    def test_the_rejection_names_the_supported_recovery_path(self) -> None:
        """Scoped to the guard: both tokens occur in Init's mode logging anyway."""

        guard_at = self.body.find("tester_cache_cohort_unaddressable")
        self.assertGreater(guard_at, -1, "the cohort guard was not found")
        block = self.body[guard_at : guard_at + 900]
        self.assertIn("RECORD_ONLY", block)
        self.assertIn("CACHE_ONLY", block)

    def test_the_rejection_reports_both_identities(self) -> None:
        """Without both sides the operator cannot tell drift from a stale cache."""

        guard_at = self.body.find("tester_cache_cohort_unaddressable")
        self.assertGreater(guard_at, -1)
        block = self.body[guard_at : guard_at + 900]
        self.assertIn("decision_input_hash=", block, "the identity this build wants")
        self.assertIn("recorded_cohorts=", block, "the identities on disk")

    def test_record_only_is_not_blocked(self) -> None:
        """The abort must be conditional on the mode, not on the match alone."""

        guard = re.search(r"if\s*\(\s*!\s*cohort_match\s*&&([^{]*)\{", self.body)
        self.assertIsNotNone(guard)
        self.assertIn(
            "TESTER_AI_CACHE_ONLY",
            guard.group(1),
            "an unconditional abort would break RECORD_ONLY, which is the only"
            " supported way to recover from this state",
        )

    def test_the_startup_verdict_still_reports_the_census(self) -> None:
        self.assertIn("artifacts_total=", self.body)
        self.assertIn("artifacts_sampled=", self.body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
