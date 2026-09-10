"""Classify the 35 analyst approvals demoted by the critic/adjudicator.

The mapping is deliberately request-level and mutually exclusive.  Objection
codes overlap, so counting raw objections would overstate the number of lost
approvals.  The assertions tie each classification to the archived evidence.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "audit_scalp_20260908"

PRIMARY = {
    # The selected keep_current target is available, but target_model describes
    # the family policy while target_source/tp_model describe the concrete
    # current objective.  The critic treated those distinct fields as aliases.
    "technical_target_semantics": {1, 5, 6, 7, 8, 15, 17, 18, 19, 32},
    # Missing root asset class / mismatched prior, or candidate-id price treated
    # as the executable entry.  Some rows also carry target objections.
    "technical_identity_or_asset_data": {2, 3, 9, 10, 13, 16, 23, 24, 25, 28, 33, 35},
    # The family contract demands a named event, but the evidence envelope only
    # exposes a branch/state/score.  The producer must supply an authoritative
    # event field or refrain from constructing that family candidate.
    "technical_missing_event_representation": {4, 11, 12, 14, 20, 21, 22, 29, 30},
    # Plan construction validates the OTE/FVG geometry before AI review.  The
    # later retracement/touch is an execution trigger enforced by the MQL
    # watchlist, so PO3_ENTRY_WAITING is a valid staged-plan state.
    "technical_deferred_trigger_semantics": {26, 27, 31, 34},
}

TECHNICAL_OBJECTION_ROWS = {
    "target_semantics": {1, 3, 5, 6, 7, 8, 9, 15, 17, 18, 19, 31, 32, 35},
    "asset_class_missing_or_inconsistent": {2, 3, 9, 10, 13, 23, 28, 31, 33},
    "candidate_identity_semantics": {16, 24, 25, 35},
    "missing_family_event_representation": {4, 10, 11, 12, 14, 20, 21, 22, 29, 30},
    "deferred_execution_trigger_treated_as_preapproval_evidence": {26, 27, 31, 34},
    "execution_plan_metadata": {9},
}


def main() -> None:
    rows = list(csv.DictReader((AUDIT / "lost_approvals_compact.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 35
    universe = set(range(1, 36))
    primary_union = set().union(*PRIMARY.values())
    assert primary_union == universe
    assert sum(len(v) for v in PRIMARY.values()) == 35  # mutually exclusive

    result = []
    for number, row in enumerate(rows, 1):
        primary = next(name for name, members in PRIMARY.items() if number in members)
        technical_issues = [name for name, members in TECHNICAL_OBJECTION_ROWS.items() if number in members]
        selected_keep_current = row["selected_target"] == "keep_current"
        current_available = row["keep_current_available"] == "True"
        if primary == "technical_target_semantics":
            assert selected_keep_current and current_available
        result.append(
            {
                "number": number,
                "request_id": row["id"],
                "symbol": row["symbol"],
                "taxonomy": row["tax"],
                "critic_verdict": row["critic"],
                "objection_codes": row["codes"].split("|") if row["codes"] else [],
                "primary_classification": primary,
                "technical_issues_material_to_review": technical_issues,
                "technical_dominant": primary.startswith("technical_"),
                "contains_material_technical_issue": bool(technical_issues),
                "critic_summary": row["critic_summary"],
            }
        )

    summary = {
        "lost_analyst_approvals": 35,
        "primary_counts": {name: len(members) for name, members in PRIMARY.items()},
        "technical_dominant": sum(x["technical_dominant"] for x in result),
        "genuine_market_state_primary": sum(not x["technical_dominant"] for x in result),
        "contains_material_technical_issue": sum(x["contains_material_technical_issue"] for x in result),
        "purely_nontechnical": sum(not x["contains_material_technical_issue"] for x in result),
        "overlapping_technical_issue_counts": {
            name: len(members) for name, members in TECHNICAL_OBJECTION_ROWS.items()
        },
        "caution": (
            "Technical classification means the review was materially affected by payload, identity, "
            "or evidence-contract semantics. It does not prove the setup would have filled or profited."
        ),
    }
    assert summary["technical_dominant"] == 35
    assert summary["contains_material_technical_issue"] == 35
    assert summary["purely_nontechnical"] == 0
    (AUDIT / "lost_approvals_technical_classification.json").write_text(
        json.dumps({"summary": summary, "decisions": result}, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
