"""A semantic plan change must retire the approval without burying the setup.

Live evidence, 2026-09-04 04:28:08. NZDCHF was approved (llm_quality_score 7.50,
rr2 1.42, setup_score 188.30), armed, and reached "confirmation complete,
attempting execution". Five milliseconds later it was gone::

    [semantic_plan_match] semantic_plan_match=false
      immutable_fields_changed=obstacle_kind,obstacle_tf
      authorized_fields_changed=entry,tp1,tp2,obstacle_price
      unauthorized_fields_changed=none

The rejection itself was right: the obstacle really had moved (severity 5.50 at
approval, 8.50 at the rebuild), so the approval rested on evidence that no
longer held. What was wrong is what happened next. The failure is filed under
the action ``terminal_invalidate_or_requeue_ai``, but the code only ever did the
first half -- ``PO3SetState(p.po3, PO3_INVALIDATED, ...)`` buried the whole PO3
sequence, and the setup could never come back to be re-judged.

These tests pin both halves: the approval must always be retired, and a setup
whose change stayed inside the authorised fields must stay eligible.
"""

from __future__ import annotations

import re

from test_governance_contracts import MQL_STAGE, _function_body


def _trade_engine() -> str:
    return (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")


def _maintain_watchlist() -> str:
    return _function_body(_trade_engine(), "MaintainWatchlist")


def test_reassessment_requires_no_unauthorized_change():
    """Corruption is not market movement. Only a change confined to fields the
    contract already authorises may earn a second AI judgement."""
    body = _maintain_watchlist()
    match = re.search(r"bool\s+reassess\s*=\s*\((.*?)\);", body, re.S)
    assert match is not None, "MaintainWatchlist no longer computes a reassess decision"
    condition = match.group(1)

    assert "InpRequeueAiOnSemanticPlanChange" in condition
    assert "EXEC_FAIL_SEMANTIC_PLAN_CHANGED" in condition
    assert "semantic_unauthorized_fields_changed" in condition


def _terminal_branch(body: str) -> str:
    """Just the execution-failure branch.

    MaintainWatchlist invalidates PO3 on several unrelated paths -- structural
    break, expiry, staleness -- and those are correct. Scoping here is what
    keeps this test about the one branch it is written for.
    """
    start = body.index("ExecFailureIsTerminal(p.execution_failure_class)")
    return body[start : body.index("string structural_reason", start)]


def test_po3_context_is_only_buried_when_not_reassessing():
    """PO3_INVALIDATED is what made the setup unrecoverable; on this branch it
    must now be reachable only on the path that is deliberately terminal."""
    branch = _terminal_branch(_maintain_watchlist())
    guarded = re.search(
        r"if\(!reassess\)\s*\n\s*PO3SetState\(p\.po3,\s*PO3_INVALIDATED",
        branch,
    )
    assert guarded is not None, "PO3_INVALIDATED is no longer guarded by !reassess"
    # Count the call, not the token: the surrounding comment names it too.
    calls = re.findall(r"PO3SetState\(\s*p\.po3\s*,\s*PO3_INVALIDATED", branch)
    assert len(calls) == 1, (
        f"expected exactly one guarded invalidation on this branch, found {len(calls)}"
    )


def test_the_approved_plan_leaves_the_watchlist_on_every_path():
    """The safety property. Whatever else changes, a plan whose approval no
    longer matches the market must never remain armed: a stale approval that
    survived the rebuild could still execute."""
    body = _maintain_watchlist()
    terminal = body[body.index("ExecFailureIsTerminal(p.execution_failure_class)") :]
    terminal = terminal[: terminal.index("string structural_reason")]

    removal = re.search(
        r"m_watchlist\[i\]\s*=\s*m_watchlist\[last_terminal\];\s*\n\s*"
        r"ArrayResize\(m_watchlist,\s*last_terminal\);",
        terminal,
    )
    assert removal is not None, "the terminal branch no longer drops the watchlist entry"

    # The removal must not sit inside a reassess conditional: both outcomes drop.
    before_removal = terminal[: removal.start()]
    assert "if(reassess)" not in before_removal, (
        "watchlist removal became conditional on the reassess decision"
    )


def test_the_action_reported_matches_the_action_taken():
    """The defect this file exists for was a name promising a requeue the code
    never performed. A reassessed setup must say so, and say it in both the
    reject record and the journal, so the two can never drift again."""
    body = _maintain_watchlist()
    assert body.count("requeue_ai_next_scan") == 2, (
        "requeue_ai_next_scan must be reported by both the setup reject and the journal"
    )
    assert "po3_preserved=" in body
    assert "unauthorized_changed=" in body


def test_the_input_exists_and_defaults_to_enabled():
    config = (MQL_STAGE / "Config.mqh").read_text(encoding="utf-8", errors="ignore")
    declaration = re.search(
        r"^input\s+bool\s+InpRequeueAiOnSemanticPlanChange\s*=\s*(true|false)\s*;",
        config,
        re.M,
    )
    assert declaration is not None, "InpRequeueAiOnSemanticPlanChange is not declared"
    assert declaration.group(1) == "true"


def test_other_terminal_classes_are_untouched():
    """Structural invalidation, an infeasible target and a permanent broker
    constraint are genuinely dead ends; only the semantic class was ever the
    misfiled one."""
    body = _maintain_watchlist()
    match = re.search(r"bool\s+reassess\s*=\s*\((.*?)\);", body, re.S)
    condition = match.group(1)
    for terminal_class in (
        "EXEC_FAIL_STRUCTURAL_INVALIDATION",
        "EXEC_FAIL_TARGET_NO_LONGER_FEASIBLE",
        "EXEC_FAIL_PERMANENT_BROKER",
    ):
        assert terminal_class not in condition, terminal_class
