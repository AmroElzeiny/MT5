"""Only the adjudicator may be skipped, and only where it cannot matter.

An earlier version of this optimisation skipped the critic as well. That was
wrong, and the live run proved it: nine decisions came back as
``final_response_contract_invalid`` because a non-empty
``critic_response_fingerprint`` is required in two independent places --
``ai_gate._mql_tester_cache_skip_reason`` (which is also the *final live
response* validator, not just the replay-cache one) and
``AIGateBridge.mqh:1626``. The adjudicator fingerprint is optional in both,
which is precisely why the adjudicator is the only role that may be dropped.

The saving is safe because the resolver only ever demotes: no branch anywhere
turns a non-approving analyst verdict into an approval. When the analyst cannot
approve and its expectancy sits a full margin below the gate, adjudicating
changes the cost of the decision and not the decision.
"""

from __future__ import annotations

import inspect

import decision_pipeline
from decision_pipeline import ConsensusResult, _adjudication_is_pointless


GATE = 6.80
FLOOR = GATE - 2.0


def _meta(**over) -> dict:
    base = {
        "adjudication_skip_enable": True,
        "live_workload": True,
        "analyst_can_approve": False,
        "adjudication_skip_floor": FLOOR,
        "analyst_expectancy_score": 2.90,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# fires only where the adjudicator cannot change the outcome
# --------------------------------------------------------------------------


def test_hopeless_live_abstain_skips_adjudication():
    assert _adjudication_is_pointless(_meta()) is True


def test_an_analyst_that_can_still_approve_is_always_adjudicated():
    assert _adjudication_is_pointless(_meta(analyst_can_approve=True)) is False


def test_expectancy_inside_the_margin_is_always_adjudicated():
    for expectancy in (FLOOR + 0.01, GATE - 0.5, GATE, GATE + 1.0):
        assert _adjudication_is_pointless(
            _meta(analyst_expectancy_score=expectancy)
        ) is False, expectancy


def test_floor_boundary_is_inclusive():
    assert _adjudication_is_pointless(_meta(analyst_expectancy_score=FLOOR)) is True
    assert _adjudication_is_pointless(
        _meta(analyst_expectancy_score=FLOOR + 0.0001)
    ) is False


# --------------------------------------------------------------------------
# never fires where it would break something else
# --------------------------------------------------------------------------


def test_non_live_workloads_are_always_adjudicated():
    """Record-only and replay responses feed caches and analysis that expect a
    complete panel; the saving is a live-cost measure only."""
    assert _adjudication_is_pointless(_meta(live_workload=False)) is False


def test_disabled_config_is_always_adjudicated():
    assert _adjudication_is_pointless(_meta(adjudication_skip_enable=False)) is False


def test_missing_metadata_fails_closed_to_adjudicating():
    """An older caller that does not supply the fields must keep the old path."""
    assert _adjudication_is_pointless({}) is False
    assert _adjudication_is_pointless(_meta(adjudication_skip_floor=None)) is False
    assert _adjudication_is_pointless(_meta(analyst_expectancy_score=None)) is False


# --------------------------------------------------------------------------
# the critic is structurally not skippable
# --------------------------------------------------------------------------


def test_consensus_result_always_carries_a_critic_result():
    """The type is the contract. ``critic_result`` is not Optional, while
    ``adjudicator_result`` is -- that asymmetry is what keeps a response from
    ever being written without a critic fingerprint."""
    hints = ConsensusResult.__annotations__
    assert "None" not in hints["critic_result"]
    assert "None" in hints["adjudicator_result"]


def test_no_code_path_returns_a_consensus_without_a_critic():
    """A regression guard for the defect this file documents: every
    ConsensusResult constructed in the pipeline passes a real critic_result."""
    source = inspect.getsource(decision_pipeline)
    assert "critic_result=None" not in source
    assert "consensus_panel_skipped" not in source


def test_a_skipped_adjudication_names_itself():
    """Reported as skipped, never as 'unresolved' -- a decision that was never
    escalated must not read as one that was escalated and came back empty."""
    result = ConsensusResult(
        "ABSTAIN",
        False,
        "analyst_abstain_below_adjudication_floor",
        {"verdict": "PASS"},
        {},
        object(),
        None,
        adjudication_skipped=True,
    )
    assert result.adjudication_skipped is True
    assert result.adjudicator_result is None
    assert result.critic_result is not None
    assert result.reason == "analyst_abstain_below_adjudication_floor"


def test_a_normal_consensus_is_never_marked_skipped():
    result = ConsensusResult(
        "REJECT", False, "analyst_reject", {"verdict": "BLOCK"}, {}, object(), None
    )
    assert result.adjudication_skipped is False
