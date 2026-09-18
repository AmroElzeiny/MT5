"""AI-side score/verdict authority: weighted score, pattern thresholds,
decision matrix, SCORE_VERDICT_ARBITER, adaptive thresholds and telemetry."""

from __future__ import annotations

import copy
import inspect
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import ai_gate
import decision_pipeline
import score_verdict_authority as SVA
from structured_models import (
    QUALITY_COMPONENT_NAMES,
    ModelAIGateOutput,
    ModelScoreVerdictArbiterDecision,
    strict_structured_schema,
)
from tests.test_decision_integrity import assessment, candidate
from tests.test_provider_neutral_ai import _provider_result


ROOT = Path(__file__).resolve().parents[1]
REQUEST_IDENTITY_HASH = "REQUESTIDENTITYSCORE1234567890"


class _StubCatalog:
    catalog_version = "stub-catalog-v1"
    catalog_hash = "stub-catalog-hash"
    _rows = [
        {"id": 0, "p": "sequence.has_sweep", "v": True},
        {"id": 1, "p": "entry_and_invalidation.candidates[0].fvg_score", "v": 5.0, "c": 0},
        {"id": 2, "p": "entry_and_invalidation.candidates[0].shadow_historical_evidence.win_rate", "v": 0.3, "c": 0},
        {"id": 3, "p": "entry_and_invalidation.candidates[1].fvg_score", "v": 4.0, "c": 1},
    ]

    def provider_rows(self):
        return [dict(row) for row in self._rows]

    def resolve(self, ids, *, candidate_index):
        by_id = {row["id"]: row for row in self._rows}
        valid = bool(ids) and all(
            value in by_id and by_id[value].get("c") in (None, candidate_index) for value in ids
        )
        return SimpleNamespace(valid=valid, resolved_paths=[by_id[v]["p"] for v in ids if v in by_id])


EVIDENCE = {
    "entry_and_invalidation": {
        "candidates": [
            {
                "candidate_index": 0,
                "fvg_score": 5.0,
                "bucket_prior": {"family": {"win_rate": 0.31}},
                "shadow_historical_evidence": {"win_rate": 0.3},
            }
        ]
    },
    "sequence": {"has_sweep": True},
    "market_regime": {"regime": "expansion"},
    "liquidity": {},
    "targets_and_obstacles": {},
    "validation": {"missing_fields": []},
}


class _ArbiterProvider:
    provider_id = "test-provider"
    provider_mode = ai_gate.PROVIDER_MODE_LOCAL

    def __init__(self, final_verdict: str = "REJECT", *, override: dict | None = None) -> None:
        self.final_verdict = final_verdict
        self.override = dict(override or {})
        self.calls: list[dict] = []

    def model_for_role(self, role: str) -> str:
        return "muse-test"

    def generate_structured(self, *, role, system_prompt, evidence, response_schema, request_metadata):
        self.calls.append({"role": role, "evidence": evidence, "system_prompt": system_prompt})
        conflict = evidence["score_verdict_conflict"]["conflict_type"]
        authority, score_assessment, verdict_assessment = SVA.ARBITER_COHERENCE[(conflict, self.final_verdict)]
        body = {
            "candidate_index": evidence["score_verdict_conflict"]["candidate_index"],
            "conflict_type": conflict,
            "reason_codes": ["THESIS_SUPPORTED_BY_EVIDENCE"],
            "evidence_ref_ids": [0, 1],
            "summary": "Current-trade evidence decides the contradiction.",
            "score_assessment": score_assessment,
            "verdict_assessment": verdict_assessment,
            "more_accurate_authority": authority,
            "final_verdict": self.final_verdict,
        }
        body.update(self.override)
        parsed = response_schema.model_construct(**body)
        return _provider_result(parsed, role=role)


def _components(value: float) -> dict:
    return {name: float(value) for name in QUALITY_COMPONENT_NAMES}


def _run(
    tmp_path: Path,
    *,
    verdict: str,
    component_value: float,
    required: float = 7.0,
    arbiter: _ArbiterProvider | None = None,
    critic: str = "PASS",
    memo: SVA.ScoreVerdictArbiterMemo | None = None,
    live: bool = False,
):
    cand = candidate()
    selected = assessment(cand, quality=8.0, state=verdict)
    selected["quality_components"] = _components(component_value)
    score = SVA.compute_authoritative_quality_score(selected["quality_components"])
    matrix = SVA.classify_score_verdict(verdict, score, required)
    threshold = SVA.resolve_required_pattern_score(
        pattern_key="FULL_PO3_REVERSAL",
        base_threshold=required,
        base_source="llm_quality_score_full_po3",
        stats=None,
        policy=SVA.AdaptiveThresholdPolicy(),
    )
    approve = verdict == "APPROVE"
    consensus_allow = approve and critic == "PASS"
    consensus = SimpleNamespace(
        decision_state="APPROVE" if consensus_allow else ("REJECT" if verdict != "ABSTAIN" else "ABSTAIN"),
        python_allow=consensus_allow,
        critic={"verdict": critic},
        adjudicator={},
    )
    decision = ai_gate.Decision(
        allow=consensus_allow,
        score=8.0,
        raw_allow=approve,
        model_raw_allow=approve,
        python_final_allow=consensus_allow,
        decision_state=consensus.decision_state,
        decision_quality_tier=ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
        mandatory_fields_complete=True,
        selected_candidate_id=cand["candidate_id"],
        selected_candidate_hash=cand["candidate_hash"],
        candidate_assessments=[selected],
        analyst_output=copy.deepcopy(selected),
        suggested_risk_multiplier=0.5 if consensus_allow else 0.0,
        veto_enabled=verdict == "REJECT",
        veto_code="ai_veto_structural_contradiction" if verdict == "REJECT" else "",
        rejection_codes=[],
    )
    provider = arbiter or _ArbiterProvider()
    memo = memo or SVA.ScoreVerdictArbiterMemo(lambda: tmp_path / "memo.jsonl")
    with patch.object(ai_gate, "SCORE_ARBITER_MEMO", memo), patch.object(
        ai_gate, "log", lambda *_: None
    ), patch.object(ai_gate, "log_ai_usage", lambda **_: {}), patch.object(
        ai_gate, "_is_live_payload", lambda _payload: live
    ):
        ai_gate._apply_score_verdict_authority(
            payload={"id": "req-score-1", "symbol": "GOLD", "candidates": [cand]},
            provider=provider,
            decision=decision,
            selected=selected,
            matrix=matrix,
            threshold=threshold,
            identity={"pattern_key": "FULL_PO3_REVERSAL", "setup_family": "full_po3"},
            consensus=consensus,
            evidence=EVIDENCE,
            evidence_catalog=_StubCatalog(),
            request_metadata={"request_id": "req-score-1", "request_identity_hash": REQUEST_IDENTITY_HASH},
        )
    return decision, selected, provider


def _arbiter_calls(provider: _ArbiterProvider) -> int:
    return sum(1 for call in provider.calls if call["role"] == SVA.ARBITER_ROLE)


# 1 ---------------------------------------------------------------------------
def test_approve_with_score_above_threshold_makes_no_arbiter_call(tmp_path):
    decision, _, provider = _run(tmp_path, verdict="APPROVE", component_value=8.0)
    assert _arbiter_calls(provider) == 0
    assert decision.score_authority["matrix_case"] == SVA.CASE_A_APPROVE_SCORE_PASS
    assert decision.score_authority["final_ai_verdict"] == "APPROVE"
    assert decision.allow is True


# 2 ---------------------------------------------------------------------------
def test_reject_with_score_below_threshold_makes_no_arbiter_call(tmp_path):
    decision, _, provider = _run(tmp_path, verdict="REJECT", component_value=3.0)
    assert _arbiter_calls(provider) == 0
    assert decision.score_authority["matrix_case"] == SVA.CASE_B_REJECT_SCORE_FAIL
    assert decision.allow is False
    assert "ai_score_below_pattern_threshold" in decision.rejection_codes


# 3 + 7 -----------------------------------------------------------------------
def test_approve_below_threshold_calls_arbiter_once_and_can_reject(tmp_path):
    decision, _, provider = _run(
        tmp_path, verdict="APPROVE", component_value=6.0, arbiter=_ArbiterProvider("REJECT")
    )
    assert _arbiter_calls(provider) == 1
    record = decision.score_authority
    assert record["matrix_case"] == SVA.CASE_C_APPROVE_SCORE_FAIL
    assert record["arbiter_output"]["more_accurate_authority"] == "SCORE"
    assert record["final_ai_verdict"] == "REJECT"
    assert decision.allow is False and decision.python_final_allow is False
    assert decision.suggested_risk_multiplier == 0.0


# 4 + 6 -----------------------------------------------------------------------
def test_reject_above_threshold_calls_arbiter_once_and_can_approve(tmp_path):
    decision, selected, provider = _run(
        tmp_path, verdict="REJECT", component_value=8.5, arbiter=_ArbiterProvider("APPROVE")
    )
    assert _arbiter_calls(provider) == 1
    record = decision.score_authority
    assert record["matrix_case"] == SVA.CASE_D_REJECT_SCORE_PASS
    assert record["final_ai_verdict"] == "APPROVE"
    assert decision.allow is True and decision.python_final_allow is True
    assert decision.decision_state == "APPROVE" and decision.model_raw_allow is True
    assert decision.veto_enabled is False and decision.suggested_risk_multiplier > 0.0
    # The authoritative assessment satisfies the shared APPROVE contract ...
    assert selected["decision_state"] == "APPROVE" and selected["raw_allow"] is True
    assert selected["veto"]["enabled"] is False
    # ... while the analyst's original REJECT is preserved for learning.
    assert decision.analyst_output["decision_state"] == "REJECT"
    assert record["analyst_verdict"] == "REJECT"


# 5 ---------------------------------------------------------------------------
def test_abstain_above_threshold_calls_arbiter_and_below_does_not(tmp_path):
    above, _, provider_above = _run(tmp_path, verdict="ABSTAIN", component_value=8.0)
    assert _arbiter_calls(provider_above) == 1
    assert above.score_authority["conflict_type"] == "ABSTAIN_ABOVE_THRESHOLD"
    below, _, provider_below = _run(tmp_path / "e", verdict="ABSTAIN", component_value=4.0)
    assert _arbiter_calls(provider_below) == 0
    assert below.score_authority["matrix_case"] == SVA.CASE_E_ABSTAIN_SCORE_FAIL
    assert below.allow is False


def test_qualitative_block_still_ands_an_arbiter_approval(tmp_path):
    decision, _, provider = _run(
        tmp_path, verdict="REJECT", component_value=8.5, arbiter=_ArbiterProvider("APPROVE"), critic="BLOCK"
    )
    assert _arbiter_calls(provider) == 1  # non-live workload keeps the learning sample
    assert decision.allow is False
    assert "score_verdict_arbiter_approve_blocked_by_qualitative_gate" in decision.rejection_codes
    live_decision, _, live_provider = _run(
        tmp_path / "live", verdict="REJECT", component_value=8.5, arbiter=_ArbiterProvider("APPROVE"),
        critic="BLOCK", live=True,
    )
    assert _arbiter_calls(live_provider) == 0
    assert live_decision.score_authority["arbiter_skipped_reason"] == "qualitative_gate_blocks_outcome_invariant"


# 8 ---------------------------------------------------------------------------
def test_analyst_cannot_see_required_threshold_or_pattern_history():
    source = inspect.getsource(ai_gate._score_setup_ai)
    start = source.index('system_msg = f"""')
    prompt = source[start : source.index('"""', start + len('system_msg = f"""'))]
    for forbidden in (
        "required_pattern_score",
        "llm_quality_score_threshold",
        "min_llm_quality_score",
        "llm_quality_score_full_po3",
        "{threshold",
        "bucket_prior contains",
    ):
        assert forbidden not in prompt, forbidden
    assert "deliberately not disclosed" in prompt

    from decision_evidence import build_decision_evidence_envelope

    cand = candidate()
    payload = {
        "id": "req-envelope",
        "symbol": "GOLD",
        "is_buy": True,
        "candidates": [cand],
        "plan": copy.deepcopy(cand),
        "runtime": {"require_snapshots": False},
        "runtime_inputs": {
            "llm_quality_score_full_po3": 7.37,
            "min_llm_quality_score_trend": 7.41,
            "ai_min_final_expectancy_score": 6.83,
            "runtime_input_hash": "runtime-x",
        },
        "po3": {"has_sweep": True, "has_displacement": True, "has_bos": True},
        "validation": {"missing_fields": [], "hard_blockers": []},
    }
    envelope = build_decision_evidence_envelope(payload, historical_analogues=[], provider_identity={}).envelope
    wire, _ = SVA.withhold_historical_performance_for_analyst(envelope)
    text = json.dumps(wire, sort_keys=True, default=str)
    for forbidden in ("llm_quality_score_full_po3", "min_llm_quality_score_trend", "ai_min_final_expectancy_score", "7.37", "7.41", "6.83"):
        assert forbidden not in text, forbidden

    history_wire, withheld = SVA.withhold_historical_performance_for_analyst(
        {
            **copy.deepcopy(EVIDENCE),
            "historical_analogues": [{"outcome_r": -1.0}],
            "evidence_catalog": {"items": _StubCatalog().provider_rows()},
            "provider_decision_context": {"rules": ["Use historical evidence only ...", "keep"]},
        }
    )
    history_text = json.dumps(history_wire)
    assert "bucket_prior" not in history_text and "shadow_historical_evidence" not in history_text
    assert "historical_analogues" not in history_text and withheld == [2]
    assert history_wire["provider_decision_context"]["rules"] == ["keep"]


# 9 ---------------------------------------------------------------------------
def test_python_calculates_authoritative_score_exactly_from_weights():
    values = [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0]
    components = dict(zip(QUALITY_COMPONENT_NAMES, values))
    expected = math.fsum(SVA.QUALITY_COMPONENT_WEIGHTS[n] * v for n, v in components.items())
    assert expected == pytest.approx(0.2 * 9 + 0.15 * 8 + 0.15 * 7 + 0.15 * 6 + 0.125 * 5 + 0.075 * 4 + 0.075 * 3 + 0.075 * 2)
    assert SVA.compute_authoritative_quality_score(components) == round(expected, 6)
    assert math.fsum(SVA.QUALITY_COMPONENT_WEIGHTS.values()) == pytest.approx(1.0)
    for bad in ({**components, "thesis_coherence": 10.5}, {k: v for k, v in components.items() if k != "sequence_quality"}, {**components, "extra": 1.0}, {**components, "evidence_coherence": float("nan")}):
        with pytest.raises(ValueError):
            SVA.compute_authoritative_quality_score(bad)


# 10 --------------------------------------------------------------------------
def test_pattern_specific_thresholds_differ():
    runtime_inputs = {
        "min_llm_quality_score_trend": 7.0,
        "llm_quality_score_full_po3": 7.4,
        "llm_quality_score_micro_po3": 6.9,
        "llm_quality_score_continuation": 7.1,
        "llm_quality_score_range": 6.6,
        "llm_quality_score_failed_breakout": 7.8,
    }
    expected = {
        "full_po3": (7.4, "llm_quality_score_full_po3"),
        "micro_po3": (6.9, "llm_quality_score_micro_po3"),
        "micro_continuation_fvg": (7.1, "llm_quality_score_continuation"),
        "micro_range_reentry": (6.6, "llm_quality_score_range"),
        "micro_failed_breakout_reclaim": (7.8, "llm_quality_score_failed_breakout"),
    }
    for family, (value, source) in expected.items():
        cand = {**candidate(), "setup_family": family, "setup_class": "", "entry_branch": "x"}
        payload = {"runtime_inputs": runtime_inputs, "candidates": [cand], "plan": {}}
        with patch.object(ai_gate, "log", lambda *_: None), patch.object(
            ai_gate, "SCORE_PATTERN_STATISTICS", SimpleNamespace(statistics_for=lambda key: None)
        ):
            resolution, identity = ai_gate._resolve_score_threshold(payload, 0)
        assert (resolution.base_threshold, resolution.base_source) == (value, source), family
        assert resolution.required_threshold == value
        assert identity["pattern_key"] == cand["setup_taxonomy_enum"]


# 11 --------------------------------------------------------------------------
def _records(n: int, mean_r: float) -> list[dict]:
    # Alternating +/-1R around ``mean_r`` so every sample size has the same mean.
    return [{"time": i, "r": mean_r + (1.0 if i % 2 == 0 else -1.0)} for i in range(n if n % 2 == 0 else n + 1)]


def test_adaptive_threshold_uses_sample_size_shrinkage():
    policy = SVA.AdaptiveThresholdPolicy()

    def resolve(n: int, mean_r: float) -> SVA.ThresholdResolution:
        stats = SVA.compute_pattern_statistics("P", _records(n, mean_r), folds=policy.folds)
        return SVA.resolve_required_pattern_score(pattern_key="P", base_threshold=7.0, base_source="base", stats=stats, policy=policy)

    tiny = resolve(10, -0.4)
    assert tiny.adjustment == 0.0 and "insufficient_sample" in tiny.source
    small, large = resolve(30, -0.4), resolve(400, -0.4)
    assert 0.0 < small.adjustment < large.adjustment <= policy.max_increase
    assert small.shrinkage_weight < large.shrinkage_weight
    strong = resolve(400, 0.4)
    assert -policy.max_decrease <= strong.adjustment <= 0.0
    # Increases are easier and larger than reductions.
    assert abs(strong.adjustment) < abs(large.adjustment)
    assert strong.required_threshold >= max(policy.absolute_floor, 7.0 - policy.max_decrease)


# 12 --------------------------------------------------------------------------
def test_duplicate_contradictions_never_buy_a_second_arbiter_call(tmp_path):
    memo = SVA.ScoreVerdictArbiterMemo(lambda: tmp_path / "memo.jsonl")
    provider = _ArbiterProvider("APPROVE")
    first, _, _ = _run(tmp_path, verdict="REJECT", component_value=8.5, arbiter=provider, memo=memo)
    second, _, _ = _run(tmp_path, verdict="REJECT", component_value=8.5, arbiter=provider, memo=memo)
    assert _arbiter_calls(provider) == 1
    assert first.score_authority["arbiter_memo_hit"] is False
    assert second.score_authority["arbiter_memo_hit"] is True
    assert second.score_authority["final_ai_verdict"] == "APPROVE"
    # A restarted process reads the persisted memo.
    restarted = SVA.ScoreVerdictArbiterMemo(lambda: tmp_path / "memo.jsonl")
    third, _, _ = _run(tmp_path, verdict="REJECT", component_value=8.5, arbiter=provider, memo=restarted)
    assert _arbiter_calls(provider) == 1 and third.score_authority["arbiter_memo_hit"] is True


# 13 --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "override, reason",
    [
        ({"more_accurate_authority": "VERDICT"}, "score_verdict_arbiter_incoherent_assessment"),
        ({"conflict_type": "APPROVE_BELOW_THRESHOLD"}, "score_verdict_arbiter_conflict_type_mismatch"),
        ({"evidence_ref_ids": [3]}, "score_verdict_arbiter_evidence_refs_invalid"),
        ({"candidate_index": 1}, "score_verdict_arbiter_candidate_index_mismatch"),
        ({"reason_codes": ["NOT_A_CODE"]}, ""),
    ],
)
def test_malformed_arbiter_output_fails_closed(tmp_path, override, reason):
    memo = SVA.ScoreVerdictArbiterMemo(lambda: tmp_path / "memo.jsonl")
    with pytest.raises(Exception) as caught:
        _run(tmp_path, verdict="REJECT", component_value=8.5, arbiter=_ArbiterProvider("APPROVE", override=override), memo=memo)
    if reason:
        assert reason in str(caught.value)
    assert not (tmp_path / "memo.jsonl").exists()


def test_arbiter_result_after_deadline_is_stale(tmp_path):
    cand = candidate()
    selected = assessment(cand, state="REJECT")
    selected["quality_components"] = _components(8.5)
    matrix = SVA.classify_score_verdict("REJECT", 8.5, 7.0)
    threshold = SVA.resolve_required_pattern_score(pattern_key="P", base_threshold=7.0, base_source="b", stats=None, policy=SVA.AdaptiveThresholdPolicy())
    with pytest.raises(ValueError, match="score_verdict_arbiter_result_after_deadline"):
        SVA.run_score_verdict_arbiter(
            provider=_ArbiterProvider("APPROVE"), evidence=EVIDENCE, evidence_catalog=_StubCatalog(),
            analyst_assessment=selected, matrix=matrix, threshold=threshold, critic={"verdict": "PASS"},
            adjudicator={}, setup_identity={}, memo=None,
            request_metadata={"request_id": "r", "request_identity_hash": REQUEST_IDENTITY_HASH, "deadline": SimpleNamespace(expired=lambda: True)},
        )


# 14 --------------------------------------------------------------------------
def test_ai_side_approval_cannot_bypass_deterministic_risk_or_account_gates(tmp_path):
    # Python: the authority is applied inside the provider stage; every
    # deterministic gate in the orchestrator still runs after it and can only
    # remove authority.
    impl = inspect.getsource(ai_gate._score_setup_impl)
    ai_call = impl.index("dec = _score_setup_ai(")
    for gate in ("_hard_pretrade_decision(", "_mandatory_live_prior_rejection(", "_fatal_snapshot_integrity_codes("):
        assert impl.index(gate) < ai_call, gate
    # rindex: the fresh-decision path (a cache hit re-applies the same gates earlier).
    for gate in ("_candidate_hard_block_reason(", "_validate_ai_target_choice_against_feasibility(", "_apply_ai_veto_gate(", "_apply_family_ai_threshold_gate("):
        assert impl.rindex(gate) > ai_call, gate
    assert "allow = True" not in impl
    assert "_flip_to_score_arbiter_approval" in inspect.getsource(ai_gate._apply_score_verdict_authority)
    # A tradeable decision without the authority record, or whose record did
    # not approve, is demoted by the threshold gate.
    cand = candidate()
    for record in (None, {"score_authority_contract_version": SVA.SCORE_AUTHORITY_CONTRACT_VERSION, "final_ai_verdict": "REJECT", "required_pattern_score": 7.0, "score_passed": True}):
        selected = assessment(cand)
        decision = ai_gate.Decision(
            allow=True, score=8.0, raw_allow=True, model_raw_allow=True, python_final_allow=True,
            decision_state="APPROVE", decision_quality_tier=ai_gate.DECISION_QUALITY_FULL_STRUCTURED,
            mandatory_fields_complete=True, selected_candidate_id=cand["candidate_id"],
            selected_candidate_hash=cand["candidate_hash"], candidate_assessments=[selected],
            suggested_risk_multiplier=0.5, score_authority=record, llm_quality_score=8.0,
        )
        with patch.object(ai_gate, "log", lambda *_: None):
            gated = ai_gate._apply_family_ai_threshold_gate({"candidates": [cand], "plan": cand}, decision, 0)
        assert gated.allow is False and gated.python_final_allow is False
    # MQL: the final execution decision still ANDs deterministic, risk and
    # execution gates behind any Python approval.
    mql = (ROOT.parent / "MT5_PO3_Codex Include" / "TradeEngine.mqh").read_text(encoding="utf-8", errors="replace")
    assert "else if(!deterministic_pass) reject_reason = deterministic_reason;" in mql
    assert 'bool allow = (reject_reason == "ok" && full_ai_approval);' in mql


# 15 --------------------------------------------------------------------------
def test_telemetry_records_conflicts_and_joins_eventual_outcomes(tmp_path, monkeypatch):
    telemetry = tmp_path / "score_verdict_telemetry.jsonl"
    monkeypatch.setenv("AI_SCORE_VERDICT_TELEMETRY_PATH", str(telemetry))
    decision, _, _ = _run(tmp_path, verdict="REJECT", component_value=8.5, arbiter=_ArbiterProvider("APPROVE"))
    with patch.object(ai_gate, "log", lambda *_: None):
        ai_gate._write_score_verdict_telemetry(
            {"id": "req-score-1", "symbol": "GOLD", "request_created_sim_time": 1780000000}, decision
        )
    rows = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    for key in (
        "pattern_key", "analyst_verdict", "quality_components", "authoritative_ai_quality_score",
        "required_pattern_score", "threshold_source", "threshold_version", "score_passed", "contradiction",
        "critic_verdict", "adjudicator_verdict", "arbiter_invoked", "arbiter_final_verdict",
        "arbiter_more_accurate_authority",
    ):
        assert key in row, key
    assert row["contradiction"] is True and row["arbiter_invoked"] is True
    assert row["arbiter_more_accurate_authority"] == "SCORE" and row["python_final_allow"] is True
    outcomes = [{"request_id": "req-score-1", "candidate_hash": row["candidate_hash"], "full_close_r": 1.8, "mae_r": 0.4, "mfe_r": 2.1}]
    report = SVA.build_score_verdict_report(rows, outcomes)
    assert report["arbiter"]["sided_with_score_pct"] == 100.0
    joined = report["outcome_by_arbitration_type"]["REJECT_ABOVE_THRESHOLD->APPROVE"]
    assert joined["trades_with_outcome"] == 1 and joined["avg_r"] == 1.8 and joined["avg_mae_r"] == 0.4
    assert report["contradiction_rate_by_pattern"]["FULL_PO3_REVERSAL"]["contradiction_rate"] == 1.0


# end-to-end: real envelope, real catalog, real binding -------------------------
def test_reject_above_threshold_resolves_through_the_real_analyst_pipeline(tmp_path):
    from decision_integrity import validate_candidate_assessment
    from tests.test_python_owned_identity_lifecycle import (
        EmptyMemory,
        ReverseAssessmentProvider,
        _comparison,
        _payload,
        _result,
    )
    from tests.test_decision_integrity import catalog_ids_for, model_assessment

    candidates = [candidate(0, "A"), candidate(1, "B")]

    class ContradictionProvider(ReverseAssessmentProvider):
        def __init__(self, rows):
            super().__init__(rows)
            self.arbiter_evidence: dict = {}

        def generate_structured(self, *, role, response_schema, evidence=None, request_metadata=None, **kwargs):
            if role == "analyst":
                self.calls.append(role)
                rows = []
                for position, cand in enumerate(self.candidates):
                    row = model_assessment(cand, quality=8.0, state="REJECT", evidence_ref_ids=catalog_ids_for(evidence, position))
                    row["veto"]["evidence_ref_ids"] = catalog_ids_for(evidence, position, count=1)
                    row["quality_components"] = _components(8.5 if position == 1 else 2.0)
                    row["target_arbitration"]["target_comparison"] = _comparison()
                    rows.append(row)
                parsed = response_schema.model_validate(
                    {
                        "decision_quality_tier": "FULL_STRUCTURED",
                        "response_quality": "FULL_STRUCTURED",
                        "selected_candidate_index": 1,
                        "candidate_assessments": rows,
                        "reasons": "Analyst rejects both; components disagree on candidate 1.",
                    }
                )
                return _result(parsed, role)
            if role == SVA.ARBITER_ROLE:
                self.calls.append(role)
                self.arbiter_evidence = copy.deepcopy(evidence)
                conflict = evidence["score_verdict_conflict"]
                parsed = response_schema.model_validate(
                    {
                        "candidate_index": conflict["candidate_index"],
                        "conflict_type": conflict["conflict_type"],
                        "reason_codes": ["THESIS_SUPPORTED_BY_EVIDENCE", "VERDICT_OVERWEIGHTED_MINOR_RISK"],
                        "evidence_ref_ids": list(evidence["allowed_evidence_ref_ids"][:2]),
                        "summary": "The cited contradiction is minor against a complete sequence.",
                        "score_assessment": "ACCURATE",
                        "verdict_assessment": "TOO_STRICT",
                        "more_accurate_authority": "SCORE",
                        "final_verdict": "APPROVE",
                    }
                )
                return _result(parsed, role)
            return super().generate_structured(
                role=role, response_schema=response_schema, evidence=evidence, request_metadata=request_metadata, **kwargs
            )

    provider = ContradictionProvider(candidates)
    memo = SVA.ScoreVerdictArbiterMemo(lambda: tmp_path / "memo.jsonl")
    with patch("ai_gate._trade_memory_store", return_value=EmptyMemory()), patch(
        "ai_gate.log_ai_usage", return_value={}
    ), patch("ai_gate._write_ai_cost_report", return_value=None), patch(
        "ai_gate._load_live_bucket_priors", return_value={}
    ), patch.object(ai_gate, "SCORE_ARBITER_MEMO", memo), patch.object(
        ai_gate, "SCORE_PATTERN_STATISTICS", SimpleNamespace(statistics_for=lambda key: None)
    ), patch("ai_gate.log", lambda *_: None):
        decision = ai_gate._score_setup_ai(_payload(candidates), provider_override=provider)

    assert provider.calls.count(SVA.ARBITER_ROLE) == 1
    assert provider.calls.count("analyst") == 1 and provider.calls.count("critic") == 1
    record = decision.score_authority
    assert record["matrix_case"] == SVA.CASE_D_REJECT_SCORE_PASS
    assert record["authoritative_ai_quality_score"] == 8.5 and record["model_estimated_quality_score"] == 8.0
    assert record["final_ai_verdict"] == "APPROVE" and decision.allow is True
    # Arbiter citations resolved against the frozen request's real catalog.
    assert record["arbiter_output"]["evidence_refs"] and all(isinstance(p, str) and p for p in record["arbiter_output"]["evidence_refs"])
    assert record["arbiter_output"]["request_identity_hash"] == decision.candidate_assessments[0]["request_identity_hash"]
    # The arbiter never saw pattern performance history either.
    assert "bucket_prior" not in json.dumps(provider.arbiter_evidence["candidate"])
    # The resolved selected assessment satisfies the shared Python/MQL contract.
    selected = next(a for a in decision.candidate_assessments if a["candidate_hash"] == decision.selected_candidate_hash)
    assert validate_candidate_assessment(selected).valid
    assert decision.analyst_output["decision_state"] == "REJECT"
    synced = ai_gate._synchronize_selected_assessment_contract(_payload(candidates), decision)
    assert synced.decision_state == "APPROVE" and synced.python_final_allow is True


# schema + pipeline wiring ------------------------------------------------------
def test_strict_schemas_emit_components_before_the_verdict():
    assert strict_structured_schema(ModelScoreVerdictArbiterDecision).valid
    analyst = strict_structured_schema(ModelAIGateOutput)
    assert analyst.valid, analyst.errors
    properties = list(analyst.schema["$defs"]["ModelCandidateAssessment"]["properties"])
    assert properties.index("quality_components") < properties.index("verdict")
    arbiter = list(strict_structured_schema(ModelScoreVerdictArbiterDecision).schema["properties"])
    assert arbiter[-1] == "final_verdict"


def test_score_gate_agreement_skips_only_a_pointless_adjudicator():
    meta = {"adjudication_skip_enable": True, "live_workload": True, "analyst_non_approve_score_gate_failed": True}
    assert decision_pipeline._score_gate_makes_adjudication_pointless("REJECT", meta) is True
    assert decision_pipeline._score_gate_makes_adjudication_pointless("ABSTAIN", meta) is True
    assert decision_pipeline._score_gate_makes_adjudication_pointless("APPROVE", meta) is False
    assert decision_pipeline._score_gate_makes_adjudication_pointless("REJECT", {**meta, "live_workload": False}) is False
    assert decision_pipeline._score_gate_makes_adjudication_pointless("REJECT", {**meta, "analyst_non_approve_score_gate_failed": False}) is False
