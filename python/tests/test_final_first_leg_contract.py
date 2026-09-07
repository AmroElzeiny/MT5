from test_governance_contracts import MQL_STAGE, _function_body


def test_final_target_sanitizer_cannot_leave_a_first_leg_below_its_floor():
    source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
    body = _function_body(source, "_BuildPlanPrices")
    final_check = body.find('_FinalizeFirstLeg(p, true')
    assert final_check > body.find('ValidateAiChosenTargetBeforeWatchlist')
    assert final_check < body.find('_LogBuildPlanAccepted')


def test_approved_first_leg_is_validated_without_rewriting_approval():
    source = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
    body = _function_body(source, "_ApplyAiTargetArbitration")
    assert '_FinalizeFirstLeg(p, false' in body
    validate = _function_body(source, "_FinalizeFirstLeg")
    for token in ('allow_generic_adjustment', '!p.assessed_plan_locked', '!p.tp1_from_target_model',
                  '_MinTp1GeometryReward', '_MinTp1SpreadReward', 'target_model_tp1_below_min_reward'):
        assert token in validate
