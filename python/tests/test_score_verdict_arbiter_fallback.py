"""SCORE_VERDICT_ARBITER fallback chain: Muse -> GLM-5.3-Flash -> Luna (OpenAI).

The arbiter runs on the same routed OpenCode provider as the analyst, critic
and adjudicator, so a failing Muse leg is answered by the same GLM leg and then
by the same Luna leg.  Unlike those roles, the arbiter also owns a semantic
contract (conflict type, coherence table, candidate index, allowed evidence
ids).  That contract is handed to the router as a per-call acceptance check,
so a schema-valid but unusable Muse answer walks the chain too, instead of
ending the call on the first leg.

Driven through the real legs (transport doubles at the wire seam) and the real
``ai_gate._apply_score_verdict_authority``.
"""
from __future__ import annotations

import json
import os
from unittest import mock

import pytest

import ai_gate
import score_verdict_authority as SVA
from ai_provider import (
    OPENCODE_MUSE_FALLBACK_PROVIDER_ID,
    OPENCODE_PRIMARY_PROVIDER_ID,
    PROVIDER_MODE_OPENCODE,
    PROVIDER_MODE_REMOTE,
    ROUTED_RESULT_VALIDATOR_METADATA_KEY,
    OpenCodeRoutedProvider,
    OpenCodeTransportError,
)
from tests.test_opencode_provider_contract import (
    GLM,
    LUNA,
    MALFORMED_ANSWER,
    MUSE,
    _config,
    _routed,
)
from tests.test_score_verdict_authority import _ArbiterProvider, _run


def _answer(**override) -> str:
    """A coherent APPROVE for REJECT_ABOVE_THRESHOLD (matrix case D)."""

    body = {
        "candidate_index": 0,
        "conflict_type": "REJECT_ABOVE_THRESHOLD",
        "reason_codes": ["THESIS_SUPPORTED_BY_EVIDENCE"],
        "evidence_ref_ids": [0, 1],
        "summary": "Current-trade evidence supports the score over the verdict.",
        "score_assessment": "ACCURATE",
        "verdict_assessment": "TOO_STRICT",
        "more_accurate_authority": "SCORE",
        "final_verdict": "APPROVE",
    }
    body.update(override)
    return json.dumps(body)


VALID = _answer()
# Schema-valid, contract-invalid: each must be treated as that leg failing.
INCOHERENT = _answer(more_accurate_authority="VERDICT")
WRONG_CONFLICT = _answer(
    conflict_type="APPROVE_BELOW_THRESHOLD",
    verdict_assessment="TOO_PERMISSIVE",
    final_verdict="REJECT",
)
WITHHELD_EVIDENCE_REF = _answer(evidence_ref_ids=[2])
WRONG_CANDIDATE = _answer(candidate_index=1)


def _status_error(status: int) -> OpenCodeTransportError:
    return OpenCodeTransportError(f"Error code: {status} - upstream failure", status_code=status)


def _arbitrate(tmp_path, routed):
    decision, _selected, _provider = _run(
        tmp_path, verdict="REJECT", component_value=8.5, arbiter=routed
    )
    return decision


def _calls(routed: OpenCodeRoutedProvider) -> tuple[int, int, int, int]:
    return (
        len(routed._muse.transport_double.calls),
        len(routed._muse_fallback.transport_double.calls),
        len(routed._secondary.transport_double.calls),
        len(routed._fallback.transport_double.calls),
    )


def test_the_built_gate_provider_gives_the_arbiter_muse_then_glm_then_luna() -> None:
    secrets = {"OPENCODE_GO_API_KEY": "oc-test-key", "OPENAI_API_KEY": "sk-test-openai"}
    with mock.patch.dict(os.environ, secrets):
        provider = ai_gate._build_ai_provider(_config())
    assert isinstance(provider, OpenCodeRoutedProvider)
    assert provider.supports_result_validator is True
    chain = (provider._muse, *provider._fallback_legs_after(provider._muse))
    assert [leg.model_for_role(SVA.ARBITER_ROLE) for leg in chain] == [MUSE, GLM, LUNA]
    assert chain[2].provider_mode == PROVIDER_MODE_REMOTE
    # Identical to the chain every other panel role gets.
    for role in ("analyst", "critic", "adjudicator"):
        assert [leg.model_for_role(role) for leg in chain] == [MUSE, GLM, LUNA]


def test_a_valid_muse_arbiter_answer_never_reaches_glm_or_luna(tmp_path) -> None:
    routed = _routed(muse_answer=VALID, muse_fallback_answer=VALID, luna_answer=VALID)
    decision = _arbitrate(tmp_path, routed)
    assert _calls(routed) == (1, 0, 0, 0)
    output = decision.score_authority["arbiter_output"]
    assert output["provider_id"] == OPENCODE_PRIMARY_PROVIDER_ID
    assert output["model_id"] == MUSE
    assert decision.allow is True


@pytest.mark.parametrize(
    "muse_failure",
    [
        pytest.param(_status_error(500), id="http_500"),
        pytest.param(TimeoutError("request timed out"), id="timeout"),
        pytest.param(MALFORMED_ANSWER, id="malformed_json"),
        pytest.param(_answer(final_verdict="MAYBE"), id="schema_invalid"),
    ],
)
def test_a_failing_muse_arbiter_call_is_answered_by_glm(tmp_path, muse_failure) -> None:
    routed = _routed(muse_answer=muse_failure, muse_fallback_answer=VALID, luna_answer=VALID)
    decision = _arbitrate(tmp_path, routed)
    assert _calls(routed) == (1, 1, 0, 0)
    output = decision.score_authority["arbiter_output"]
    assert output["provider_id"] == OPENCODE_MUSE_FALLBACK_PROVIDER_ID
    assert output["model_id"] == GLM
    assert decision.score_authority["final_ai_verdict"] == "APPROVE"


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param(INCOHERENT, id="incoherent_assessment"),
        pytest.param(WRONG_CONFLICT, id="conflict_type_mismatch"),
        pytest.param(WITHHELD_EVIDENCE_REF, id="withheld_evidence_ref"),
        pytest.param(WRONG_CANDIDATE, id="candidate_index_mismatch"),
    ],
)
def test_a_contract_invalid_muse_arbiter_answer_falls_back_to_glm(tmp_path, invalid) -> None:
    routed = _routed(muse_answer=invalid, muse_fallback_answer=VALID, luna_answer=VALID)
    decision = _arbitrate(tmp_path, routed)
    assert _calls(routed) == (1, 1, 0, 0)
    assert decision.score_authority["arbiter_output"]["model_id"] == GLM
    rejected = [line for line in routed.log_lines if line.startswith("[opencode_result_rejected]")]
    assert len(rejected) == 1
    assert f"model={MUSE}" in rejected[0] and "reason=score_verdict_arbiter_" in rejected[0]
    hops = [line for line in routed.log_lines if line.startswith("[opencode_fallback] ")]
    assert f"to_model={GLM}" in hops[0]


@pytest.mark.parametrize(
    "glm_failure",
    [
        pytest.param(_status_error(500), id="glm_http_500"),
        pytest.param(MALFORMED_ANSWER, id="glm_malformed_json"),
        pytest.param(INCOHERENT, id="glm_incoherent_assessment"),
    ],
)
def test_muse_then_glm_failing_is_answered_by_luna(tmp_path, glm_failure) -> None:
    routed = _routed(muse_answer=_status_error(500), muse_fallback_answer=glm_failure, luna_answer=VALID)
    decision = _arbitrate(tmp_path, routed)
    assert _calls(routed) == (1, 1, 0, 1)
    output = decision.score_authority["arbiter_output"]
    assert output["model_id"] == LUNA
    assert output["provider_id"] == routed._fallback.provider_id
    assert routed._fallback.provider_mode == PROVIDER_MODE_REMOTE
    # The Luna leg keeps its configured tier.
    assert routed._fallback.transport_double.calls[0].get("service_tier") == "flex"
    assert decision.allow is True


def test_all_three_legs_failing_fails_closed_and_writes_no_memo(tmp_path) -> None:
    routed = _routed(
        muse_answer=INCOHERENT,
        muse_fallback_answer=_status_error(500),
        luna_answer=WRONG_CONFLICT,
    )
    with pytest.raises(ValueError, match="score_verdict_arbiter_conflict_type_mismatch"):
        _arbitrate(tmp_path, routed)
    assert _calls(routed) == (1, 1, 0, 1)
    assert not (tmp_path / "memo.jsonl").exists()


def test_no_leg_ever_receives_the_acceptance_check(tmp_path) -> None:
    routed = _routed(muse_answer=INCOHERENT, muse_fallback_answer=VALID)
    seen: list[dict] = []
    for leg in (routed._muse, routed._muse_fallback):
        original = leg.generate_structured

        def spy(_original=original, **kwargs):
            seen.append(dict(kwargs["request_metadata"]))
            return _original(**kwargs)

        leg.generate_structured = spy
    _arbitrate(tmp_path, routed)
    assert len(seen) == 2
    assert all(ROUTED_RESULT_VALIDATOR_METADATA_KEY not in metadata for metadata in seen)
    assert all(not any(callable(value) for value in metadata.values()) for metadata in seen)


def test_a_provider_without_the_router_contract_never_receives_a_callable(tmp_path) -> None:
    class _Recording(_ArbiterProvider):
        def generate_structured(self, **kwargs):
            self.metadata = dict(kwargs["request_metadata"])
            return super().generate_structured(**kwargs)

    provider = _Recording("APPROVE")
    _run(tmp_path, verdict="REJECT", component_value=8.5, arbiter=provider)
    assert ROUTED_RESULT_VALIDATOR_METADATA_KEY not in provider.metadata


def test_a_non_callable_acceptance_check_is_refused_before_any_leg_runs() -> None:
    routed = _routed()
    with pytest.raises(ValueError, match="routed_result_validator_not_callable"):
        routed.generate_structured(
            role=SVA.ARBITER_ROLE,
            system_prompt="Return JSON.",
            evidence={},
            response_schema=SVA.ModelScoreVerdictArbiterDecision,
            request_metadata={"request_id": "r", ROUTED_RESULT_VALIDATOR_METADATA_KEY: "not-callable"},
        )
    assert _calls(routed) == (0, 0, 0, 0)
