"""Python's half of the target-menu contract.

Two things have to hold on this side.  ``_compact_target_candidates`` is a
whitelist -- a route missing from it is dropped before the model ever sees it,
so adding a route to the MQL payload without adding it here is a silent no-op.
And ``_validate_ai_target_choice_against_feasibility`` used to wave the
incumbent through unchecked, which was safe only while "keep the current plan"
meant "no arbitration was required".  Now that the incumbent is an advertised
arbitration route it has to be validated like any other choice.
"""

from __future__ import annotations

import ai_gate
from ai_gate import (
    Decision,
    _compact_target_candidates,
    _target_option_feasible,
    _validate_ai_target_choice_against_feasibility,
)


# EURCHF candidate 1, 2026-09-04 17:15.  entry 0.938315, sl 0.93741,
# risk 0.000905, cap 0.004525 (= risk x InpMaxPlanRR2 5.0).
ENTRY = 0.938315
CAP = 0.004525
LIQUIDITY_TP = 0.9435       # reward 0.005185 -> 5.7293R, over the cap
INCUMBENT_TP = 0.94232      # reward 0.004005 -> 4.4254R, inside the cap


def _route(**over):
    base = {
        "available": True,
        "model": "liquidity_or_synthetic_rr",
        "tp2": LIQUIDITY_TP,
        "rr2": 5.7293,
        "reward_distance_price": 0.005185,
        "max_allowed_distance": CAP,
        "exceeds_max_target_distance": False,
        "min_rr_pass": True,
        "max_rr_pass": True,
        "feasible_for_tp2": True,
        "infeasible_reason": "",
    }
    base.update(over)
    return base


def _menu(**routes):
    base = {
        "arbitration_required": True,
        "current_target_source": "prev_day_high",
        "current_tp_model": "prev_day_high",
        "current_tp2": INCUMBENT_TP,
        "current_rr2": 4.4254,
        "obstacle_kind": "crossed_opposing_imbalance",
        "blocker_features": {"obstacle_kind": "crossed_opposing_imbalance", "severity": 5.5},
    }
    base.update(routes)
    return base


# --------------------------------------------------------------------------
# the whitelist
# --------------------------------------------------------------------------


def test_the_incumbent_route_survives_compaction():
    """Without this the new MQL route is stripped in Python and the model is
    handed the same all-infeasible menu it had before."""
    compact = _compact_target_candidates(
        _menu(keep_current_target=_route(tp2=INCUMBENT_TP, rr2=4.4254, model="keep_current"))
    )
    assert "keep_current_target" in compact
    assert compact["keep_current_target"]["model"] == "keep_current"
    assert compact["keep_current_target"]["tp2"] == INCUMBENT_TP


def test_compaction_preserves_the_max_distance_evidence():
    """reward and cap must both reach the model, or it cannot audit the verdict
    it is being given."""
    compact = _compact_target_candidates(
        _menu(liquidity_target=_route(exceeds_max_target_distance=True, max_rr_pass=False))
    )
    option = compact["liquidity_target"]
    assert option["reward_distance_price"] == 0.005185
    assert option["max_allowed_distance"] == CAP
    assert option["exceeds_max_target_distance"] is True
    assert option["max_rr_pass"] is False


def test_every_advertised_route_is_whitelisted(subtests):
    routes = {
        "liquidity_target",
        "partial_before_obstacle_then_liquidity",
        "capped_before_obstacle",
        "synthetic_rr_fallback",
        "synthetic_rr_capped_to_max_distance",
        "keep_current_target",
    }
    compact = _compact_target_candidates(_menu(**{name: _route() for name in routes}))
    for name in sorted(routes):
        with subtests.test(route=name):
            assert name in compact


# --------------------------------------------------------------------------
# an over-cap route is not feasible
# --------------------------------------------------------------------------


def test_an_over_cap_route_is_not_feasible():
    assert _target_option_feasible(_route()) is True
    assert (
        _target_option_feasible(
            _route(
                available=False,
                feasible_for_tp2=False,
                exceeds_max_target_distance=True,
                max_rr_pass=False,
                infeasible_reason="exceeds_max_target_distance",
            )
        )
        is False
    )


# --------------------------------------------------------------------------
# the incumbent choice is validated, not waved through
# --------------------------------------------------------------------------


def _decision(model: str) -> Decision:
    d = Decision(allow=True, score=7.4)
    d.chosen_target_model = model
    d.chosen_tp2 = INCUMBENT_TP
    d.chosen_rr2 = 4.4254
    return d


def _payload(menu):
    return {"plan": {"target_arbitration_required": True, "target_candidates": menu}, "candidates": []}


def test_a_feasible_incumbent_is_accepted_unchanged():
    """The EURCHF outcome the fix exists to produce: the plan's own 4.4254R
    target is inside the cap, so keeping it is a valid answer."""
    menu = _menu(
        keep_current_target=_route(
            model="keep_current", tp2=INCUMBENT_TP, rr2=4.4254,
            reward_distance_price=0.004005,
        ),
        liquidity_target=_route(
            available=False, feasible_for_tp2=False,
            exceeds_max_target_distance=True, max_rr_pass=False,
            infeasible_reason="exceeds_max_target_distance",
        ),
    )
    out = _validate_ai_target_choice_against_feasibility(_payload(menu), _decision("keep_current"), 0)
    assert out.allow is True
    assert out.chosen_tp2 == INCUMBENT_TP
    assert not (out.rejection_codes or [])


def test_an_infeasible_incumbent_is_rejected(subtests):
    """Advertising the incumbent must not become a way past the cap, whichever
    spelling the model answers with."""
    menu = _menu(
        keep_current_target=_route(
            model="keep_current", available=False, feasible_for_tp2=False,
            exceeds_max_target_distance=True, max_rr_pass=False,
            infeasible_reason="exceeds_max_target_distance",
        )
    )
    for token in ("keep_current", "current_plan", "current"):
        with subtests.test(token=token):
            out = _validate_ai_target_choice_against_feasibility(
                _payload(menu), _decision(token), 0
            )
            assert out.allow is False
            assert "no_feasible_target" in (out.rejection_codes or [])


def test_a_payload_without_the_route_keeps_the_old_behaviour():
    """Replayed and cached cohorts built before the route existed carry no
    keep_current_target entry.  They must behave exactly as they did."""
    menu = _menu(liquidity_target=_route())
    out = _validate_ai_target_choice_against_feasibility(_payload(menu), _decision("current_plan"), 0)
    assert out.allow is True
    assert not (out.rejection_codes or [])


# --------------------------------------------------------------------------
# the model is told the route exists
# --------------------------------------------------------------------------


def test_the_prompt_names_the_incumbent_route():
    """A route the model is never told about is a route it cannot choose."""
    source = ai_gate.__file__
    text = open(source, encoding="utf-8").read()
    rule = next(
        line for line in text.splitlines() if line.startswith("Target arbitration rule:")
    )
    assert "keep_current_target" in rule
    assert "chosen_target_model=keep_current" in rule
