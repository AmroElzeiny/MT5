import copy
import pytest

from tester_replay_provenance import build_replay_provenance
from test_governance_contracts import MQL_STAGE, _function_body


def sample():
    c = dict(candidate_id="USDCAD|breaker_retest|0", candidate_hash="A1EF1BEA102FCCDE",
             request_execution_fingerprint="CDFFFBF893831F5C", spread_r=0.003003,
             slippage_r=0.006, execution_cost_r=0.003, net_reward_after_cost_r=1.6937)
    request = {"id": "original", "candidates": [c]}
    response = {"id": "original", "selected_candidate_hash": c["candidate_hash"],
                "candidate_assessments": [copy.deepcopy(c)], "python_final_allow": True}
    return request, response


def test_recorded_costs_and_identities_are_preserved_without_mutation():
    req, resp = sample()
    before = copy.deepcopy((req, resp))
    provenance = build_replay_provenance(req, resp)
    assert provenance["candidates"] == req["candidates"]
    assert (req, resp) == before


@pytest.mark.parametrize("field", ["id", "candidate_id", "candidate_hash", "request_execution_fingerprint"])
def test_foreign_request_or_assessment_cannot_supply_cost_provenance(field):
    req, resp = sample()
    if field == "id":
        resp[field] = "foreign"
    else:
        resp["candidate_assessments"][0][field] = "foreign"
    with pytest.raises(ValueError):
        build_replay_provenance(req, resp)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01])
def test_invalid_costs_cannot_authorize_a_rebind(value):
    req, resp = sample()
    req["candidates"][0]["execution_cost_r"] = value
    with pytest.raises(ValueError):
        build_replay_provenance(req, resp)


def test_duplicate_assessments_cannot_supply_provenance():
    req, resp = sample()
    resp["candidate_assessments"] *= 2
    with pytest.raises(ValueError):
        build_replay_provenance(req, resp)


def test_mql_restores_recorded_provenance_before_strict_group_binding():
    engine = (MQL_STAGE / "TradeEngine.mqh").read_text(encoding="utf-8")
    body = _function_body(engine, "_QueueTesterCachedDecision")
    assert "_RestoreTesterRequestProvenance" in body
    assert body.index("_RestoreTesterRequestProvenance") < body.index("ArrayResize(m_pending_ai")
    restore = _function_body(engine, "_RestoreTesterRequestProvenance")
    for guard in ("candidate_hash", "candidate_id", "request_execution_fingerprint",
                  "MathIsValidNumber", "max_cost_deterioration_r", "recorded_request_id"):
        assert guard in restore


def test_historical_policy_identity_requires_tester_and_complete_raw_pins():
    bridge = (MQL_STAGE / "AIGateBridge.mqh").read_text(encoding="utf-8")
    body = _function_body(bridge, "ValidateReplayIdentityMode")
    for token in ("MQL_TESTER", "TESTER_AI_CACHE_ONLY", "_CommonFileContentHash",
                  "InpTesterLegacyNormalizedPolicyRawHash", "InpTesterLegacyInvalidationPolicyRawHash"):
        assert token in body
    legacy = _function_body(bridge, "_PolicyIdentityContentHash")
    assert "FILE_BIN" in legacy
    assert "FileReadString" not in legacy
    assert "i+1<size" in legacy
