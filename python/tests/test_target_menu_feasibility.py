"""The target menu must never offer a route the target sanitizer will refuse.

Live evidence, 2026-09-04 17:16:27. EURCHF was the only AI approval of the run.
The arbitration menu shipped to the model contained, in one object::

    "liquidity_target": {
        "available": true, "tp2": 0.9435, "rr2": 5.7293,
        "reward_distance_price": 0.005185,
        "max_allowed_distance":  0.004525,
        "feasible_for_tp2": true, "infeasible_reason": ""
    }

Both numbers were present and neither was compared to the other.  The model
took the route it was told was feasible, Python wrote final_allow=true, and
seven milliseconds later::

    [target_sanitizer] substitution_refused=true reason=assessed_plan_locked
      reason=ai_chosen_target_exceeds_max_distance
      reward=0.00518500 max_allowed=0.00452500
    EURCHF watchlist add skipped: ai_chosen_target_exceeds_max_distance

Only ``liquidity_target`` omitted the check; ``synthetic_rr_fallback`` in the
same payload computed ``exceeds_max_target_distance`` correctly.  These tests
pin three things: the comparison has exactly one definition, every route that
can breach the cap performs it, and the incumbent target -- which the engine
already accepts as ``keep_current`` -- is actually on the menu, because with
the cap now enforced a plan whose own target is feasible would otherwise be
handed a menu on which nothing is choosable.
"""

from __future__ import annotations

import json
import re

from test_governance_contracts import MQL_STAGE, _function_body


def _bridge() -> str:
    return (MQL_STAGE / "AIGateBridge.mqh").read_text(encoding="utf-8", errors="ignore")


def _engine() -> str:
    return (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8", errors="ignore")


def _contract() -> str:
    return (MQL_STAGE / "ExecutionAdjustmentContract.mqh").read_text(
        encoding="utf-8", errors="ignore"
    )


def _target_menu() -> str:
    return _function_body(_bridge(), "_TargetCandidatesJson")


def _route(menu: str, key: str) -> str:
    """The serialization block for one route of the menu."""
    start = menu.index(f'\\"{key}\\":{{')
    tail = menu[start:]
    end = tail.index('j += "},"') if 'j += "},"' in tail else len(tail)
    return tail[:end]


# --------------------------------------------------------------------------
# one definition of the comparison
# --------------------------------------------------------------------------


def test_the_max_distance_test_has_a_single_definition():
    """The payload and the sanitizer disagreeing is the whole defect.  They can
    only agree structurally if they call the same function."""
    contract = _contract()
    assert re.search(
        r"bool\s+RewardWithinMaxDistance\(\s*const double reward,",
        contract,
    ), "RewardWithinMaxDistance is not defined in ExecutionAdjustmentContract.mqh"

    # Defined once, used by both parties.
    assert len(re.findall(r"bool\s+RewardWithinMaxDistance\s*\(", contract)) == 1
    assert "RewardWithinMaxDistance" in _bridge(), "the payload does not use it"
    assert "RewardWithinMaxDistance" in _engine(), "the sanitizer does not use it"


def test_a_zero_cap_still_means_no_cap_on_both_sides():
    """TicksWithinCap already defines cap<=0 as unconstrained.  If the payload
    were stricter than the enforcement it would suppress tradeable routes."""
    body = _function_body(_contract(), "RewardWithinMaxDistance")
    assert re.search(r"if\(max_allowed_distance <= 0\.0\)\s*return true;", body)


def test_the_enforcer_uses_the_shared_test():
    body = _function_body(_engine(), "_EvaluateTargetFeasibility")
    assert re.search(
        r"out\.max_distance_pass\s*=\s*RewardWithinMaxDistance\(\s*out\.reward,"
        r"\s*out\.max_allowed_distance,\s*tick\s*\)",
        body,
    ), "the sanitizer no longer measures through the shared definition"


def test_the_bridge_measures_with_the_same_tick_size():
    """Comparing in ticks is only equivalent if both sides resolve the tick the
    same way; _PlanTickSize must not be a second implementation."""
    menu = _target_menu()
    assert "PlanTickSize(p.symbol)" in menu
    engine_body = _function_body(_engine(), "_PlanTickSize")
    assert "return PlanTickSize(symbol);" in engine_body, (
        "_PlanTickSize re-implements tick resolution instead of delegating"
    )


# --------------------------------------------------------------------------
# every route that can breach the cap performs the check
# --------------------------------------------------------------------------


def test_every_cap_breaching_route_reports_the_max_distance_verdict(subtests):
    """liquidity_target was the one route missing this.  capped_before_obstacle
    and the partial's runner leg share the same shape and must not become the
    next instance of it."""
    menu = _target_menu()
    for key in (
        "liquidity_target",
        "capped_before_obstacle",
        "partial_before_obstacle_then_liquidity",
        "keep_current_target",
    ):
        with subtests.test(route=key):
            block = _route(menu, key)
            assert "exceeds_max_target_distance" in block, (
                f"{key} does not report a max-distance verdict"
            )
            assert "max_rr_pass" in block, f"{key} does not report max_rr_pass"


def test_liquidity_feasibility_requires_the_cap_not_only_the_floor(subtests):
    """The exact regression: feasible_for_tp2 was `rr >= InpMinLiveRR2` alone."""
    menu = _target_menu()
    for name, flag in (
        ("liquidity", "liquidity_feasible"),
        ("capped", "capped_feasible"),
        ("current", "current_feasible"),
    ):
        with subtests.test(route=name):
            decl = re.search(rf"bool\s+{flag}\s*=\s*\((.*?)\);", menu, re.S)
            assert decl is not None, f"{flag} is no longer computed"
            condition = decl.group(1)
            assert f"{name}_rr_ok" in condition, f"{flag} dropped the RR floor"
            assert f"{name}_within_max" in condition, (
                f"{flag} does not consult the max-distance cap -- this is the "
                "EURCHF defect"
            )


def test_the_advertised_flags_are_the_ones_feasibility_was_computed_from():
    """available and feasible_for_tp2 must both carry the verdict.  The model
    reads `available` first, so a route left available=true while infeasible is
    still an invitation into a dead approval."""
    block = _route(_target_menu(), "liquidity_target")
    assert re.search(r'JsonKVBool\("available",\s*liquidity_feasible\)', block)
    assert re.search(r'JsonKVBool\("feasible_for_tp2",\s*liquidity_feasible\)', block)


def test_the_partial_runner_inherits_the_liquidity_verdict():
    """Its tp2 IS the liquidity target, so it breaches the cap in exactly the
    same cases."""
    block = _route(_target_menu(), "partial_before_obstacle_then_liquidity")
    assert "liquidity_within_max" in block
    assert "tp2_exceeds_max_target_distance" in block


def test_infeasible_reason_follows_the_enforcers_precedence():
    """The sanitizer decides max distance before the RR floor.  If the payload
    reported rr_below_min for an over-cap target the two would name different
    causes for the same refusal."""
    block = _route(_target_menu(), "liquidity_target")
    reason = re.search(r'JsonKVStr\("infeasible_reason",(.*?)\)\)\)\)', block, re.S)
    assert reason is not None, "infeasible_reason is no longer computed"
    text = reason.group(1)
    assert text.index("exceeds_max_target_distance") < text.index("rr_below_min"), (
        "max distance must be decided before the RR floor"
    )


# --------------------------------------------------------------------------
# the incumbent route
# --------------------------------------------------------------------------


def test_the_incumbent_target_is_on_the_menu():
    """CTradeEngine has always honoured keep_current; the menu never listed it.
    With the cap now enforced, EURCHF's own feasible 4.4254R prev_day_high would
    otherwise leave the model with five infeasible routes and no way to say the
    plan was already correct."""
    menu = _target_menu()
    assert '\\"keep_current_target\\":{' in menu
    block = _route(menu, "keep_current_target")
    assert re.search(r'JsonKVStr\("model",\s*"keep_current"\)', block), (
        "the advertised token must be one CTradeEngine accepts"
    )
    assert re.search(r'JsonKVNum\("tp2",\s*p\.tp2,\s*8\)', block), (
        "the incumbent route must carry the plan's own target, not a rebuilt one"
    )


def test_the_engine_still_accepts_the_advertised_token():
    """The menu and the applier must agree on the spelling, or a chosen
    incumbent becomes invalid_ai_target_arbitration_response."""
    body = _function_body(_engine(), "_ApplyAiTargetArbitration")
    assert '"keep_current"' in body, (
        "CTradeEngine no longer accepts the token the payload advertises"
    )
    assert re.search(r'chosen_tp\s*=\s*p\.tp2;', body), (
        "keep_current must bind to the plan's existing target"
    )


def test_the_incumbent_route_is_feasibility_gated_like_every_other():
    """Advertising it must not become a way past the cap."""
    block = _route(_target_menu(), "keep_current_target")
    assert re.search(r'JsonKVBool\("available",\s*current_feasible\)', block)
    assert re.search(r'JsonKVBool\("feasible_for_tp2",\s*current_feasible\)', block)


# --------------------------------------------------------------------------
# the diagnostic that named checks it never ran
# --------------------------------------------------------------------------


def _rendered_menu() -> dict:
    """Render _TargetCandidatesJson with placeholder values.

    Adding a route means adding a brace and a comma by hand.  MQL compiles a
    missing comma perfectly happily and the damage only appears at runtime, as
    a request Python cannot parse -- so the structure is checked here instead.
    """
    statements, buf = [], ""
    for line in _target_menu().splitlines():
        s = line.strip()
        if s.startswith("//"):
            continue
        if not buf and not (s.startswith("j +=") or s.startswith("string j =")):
            continue
        buf = (buf + " " + s).strip() if buf else s
        if buf.endswith(";"):
            statements.append(buf)
            buf = ""

    placeholder = {"Str": '"x"', "Num": "1.5", "Bool": "true", "Int": "7"}
    out: list[str] = []
    for s in statements:
        if s.startswith("string j ="):
            out.append("{")
            continue
        literal = re.match(r'j \+= "(.*?)"\s*;$', s)
        if literal:
            out.append(literal.group(1).replace('\\"', '"'))
            continue
        field = re.match(r'j \+= JsonKV(Str|Num|Bool|Int)\("([^"]+)"', s)
        assert field is not None, f"unrecognised serialization statement: {s[:120]}"
        out.append(f'"{field.group(2)}":{placeholder[field.group(1)]}')
        if re.search(r'\+\s*","\s*;$', s):
            out.append(",")
    return json.loads("".join(out))


def test_the_emitted_menu_is_well_formed_json():
    rendered = _rendered_menu()
    routes = {
        key
        for key, value in rendered.items()
        if isinstance(value, dict) and "feasible_for_tp2" in value
    }
    assert routes == {
        "liquidity_target",
        "capped_before_obstacle",
        "partial_before_obstacle_then_liquidity",
        "synthetic_rr_fallback",
        "synthetic_rr_capped_to_max_distance",
        "keep_current_target",
    }


def test_every_reported_pass_flag_was_actually_measured():
    """The EURCHF rejection printed rr_floor_pass=false beside rr=5.72928 and
    min_rr=0.90000, because the function returned before evaluating them and the
    flags kept their Reset() default.  All three must be assigned before any of
    them decides the outcome."""
    body = _function_body(_engine(), "_EvaluateTargetFeasibility")
    assigns = {
        flag: body.index(f"out.{flag}")
        for flag in ("max_distance_pass", "min_distance_pass", "rr_floor_pass")
    }
    first_return = body.index('out.reason = "ai_chosen_target_exceeds_max_distance"')
    for flag, at in assigns.items():
        assert at < first_return, (
            f"out.{flag} is still assigned after the max-distance early return, "
            "so a rejection can report a verdict for a check it never ran"
        )
