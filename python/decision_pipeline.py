"""Provider-neutral multi-role qualitative audit and deterministic consensus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_provider import AIProvider, ProviderResult
from decision_integrity import (
    AI_ROLE_CONTRACT_VERSION,
    DECISION_ABSTAIN,
    DECISION_APPROVE,
    DECISION_REJECT,
    LLM_VETO_CODES,
)
from structured_models import (
    AdjudicatorDecision,
    CriticDecision,
    ModelAdjudicatorDecision,
    ModelCriticDecision,
)


ROLE_CONTRACT_VERSION = AI_ROLE_CONTRACT_VERSION
CONSENSUS_RESOLVER_VERSION = "20260718_deterministic_consensus_v1"


@dataclass(frozen=True)
class ConsensusResult:
    decision_state: str
    python_allow: bool
    reason: str
    critic: dict[str, Any]
    adjudicator: dict[str, Any]
    critic_result: ProviderResult
    adjudicator_result: ProviderResult | None


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return dict(value) if isinstance(value, Mapping) else {}


def evidence_path_exists(root: Any, path: str) -> bool:
    current = root
    normalized = str(path or "").replace("[", ".").replace("]", "")
    for token in (part for part in normalized.split(".") if part):
        if isinstance(current, Mapping):
            if token not in current:
                return False
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index < 0 or index >= len(current):
                return False
            current = current[index]
        else:
            return False
    return True


def _validate_objections(critic: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    role = str(critic.get("role") or "").lower()
    verdict = str(critic.get("verdict") or "").upper()
    if role != "critic" or critic.get("role_contract_version") != ROLE_CONTRACT_VERSION:
        raise ValueError("critic_role_contract_invalid")
    if verdict not in {"PASS", "BLOCK", "ABSTAIN"}:
        raise ValueError("critic_verdict_invalid")
    blocking = critic.get("blocking_objections")
    if not isinstance(blocking, list):
        raise ValueError("critic_blocking_objections_invalid")
    if verdict == "PASS" and blocking:
        raise ValueError("critic_pass_with_blocking_objections")
    if verdict == "BLOCK" and not blocking:
        raise ValueError("critic_block_without_objection")
    for objection in [*blocking, *(critic.get("non_blocking_objections") or [])]:
        if not isinstance(objection, Mapping):
            raise ValueError("critic_objection_not_object")
        if str(objection.get("code") or "") not in LLM_VETO_CODES:
            raise ValueError("critic_objection_code_unknown")
        refs = objection.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("critic_objection_missing_evidence_refs")
        if any(not evidence_path_exists(evidence, str(ref)) for ref in refs):
            raise ValueError("critic_objection_evidence_ref_invalid")
    top_level_refs = critic.get("evidence_refs")
    if not isinstance(top_level_refs, list) or not top_level_refs:
        raise ValueError("critic_evidence_refs_missing")
    if any(not evidence_path_exists(evidence, str(ref)) for ref in top_level_refs):
        raise ValueError("critic_evidence_refs_invalid")


def _validate_role_binding(
    role_output: Mapping[str, Any],
    *,
    role: str,
    request_metadata: Mapping[str, Any],
    expected_provider_id: str,
    expected_model_id: str,
) -> None:
    expected_request_id = str(request_metadata.get("request_id") or "")
    expected_identity_hash = str(request_metadata.get("request_identity_hash") or "")
    if not expected_request_id or not expected_identity_hash:
        raise ValueError(f"{role}_request_identity_missing")
    if str(role_output.get("request_id") or "") != expected_request_id:
        raise ValueError(f"{role}_request_id_mismatch")
    if str(role_output.get("request_identity_hash") or "") != expected_identity_hash:
        raise ValueError(f"{role}_request_identity_hash_mismatch")
    if str(role_output.get("provider_id") or "") != str(expected_provider_id):
        raise ValueError(f"{role}_provider_id_mismatch")
    if str(role_output.get("model_id") or "") != str(expected_model_id):
        raise ValueError(f"{role}_model_id_mismatch")
    if str(role_output.get("role_schema_version") or "") != ROLE_CONTRACT_VERSION:
        raise ValueError(f"{role}_schema_version_mismatch")


def _validate_adjudication(
    adjudicator: Mapping[str, Any],
    critic: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    if str(adjudicator.get("role") or "").lower() != "adjudicator":
        raise ValueError("adjudicator_role_invalid")
    if adjudicator.get("role_contract_version") != ROLE_CONTRACT_VERSION:
        raise ValueError("adjudicator_role_contract_invalid")
    verdict = str(adjudicator.get("verdict") or "").upper()
    if verdict not in {"UPHOLD_APPROVE", "UPHOLD_BLOCK", "ABSTAIN"}:
        raise ValueError("adjudicator_verdict_invalid")
    refs = adjudicator.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not evidence_path_exists(evidence, str(ref)) for ref in refs):
        raise ValueError("adjudicator_evidence_refs_invalid")
    blocking_codes = {
        str(item.get("code") or "")
        for item in (critic.get("blocking_objections") or [])
        if isinstance(item, Mapping)
    }
    resolved = {str(value) for value in (adjudicator.get("resolved_objection_codes") or [])}
    unresolved = {str(value) for value in (adjudicator.get("unresolved_objection_codes") or [])}
    if verdict == "UPHOLD_APPROVE" and (not blocking_codes.issubset(resolved) or unresolved):
        raise ValueError("adjudicator_approve_did_not_resolve_all_objections")


def _selected_candidate_evidence(evidence: Mapping[str, Any], candidate_hash: str) -> dict[str, Any]:
    section = evidence.get("entry_and_invalidation")
    rows = section.get("candidates") if isinstance(section, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("evidence_candidates_missing")
    selected = next(
        (dict(row) for row in rows if isinstance(row, Mapping) and str(row.get("candidate_hash") or "") == candidate_hash),
        None,
    )
    if selected is None:
        raise ValueError("selected_candidate_evidence_missing")
    return selected


def run_qualitative_consensus(
    *,
    provider: AIProvider,
    evidence: Mapping[str, Any],
    analyst_assessment: Mapping[str, Any],
    request_metadata: Mapping[str, Any],
    near_deterministic_boundary: bool = False,
) -> ConsensusResult:
    candidate_id = str(analyst_assessment.get("candidate_id") or "")
    candidate_hash = str(analyst_assessment.get("candidate_hash") or "")
    candidate_index = int(analyst_assessment.get("candidate_index", -1))
    if candidate_index < 0:
        raise ValueError("analyst_candidate_index_invalid")
    analyst_state = str(analyst_assessment.get("decision_state") or "").upper()
    selected_evidence = _selected_candidate_evidence(evidence, candidate_hash)
    critic_evidence = {
        "request_id": str(request_metadata.get("request_id") or ""),
        "request_identity_hash": str(request_metadata.get("request_identity_hash") or ""),
        "candidate": selected_evidence,
        "market_regime": evidence.get("market_regime"),
        "sequence": evidence.get("sequence"),
        "liquidity": evidence.get("liquidity"),
        "targets_and_obstacles": evidence.get("targets_and_obstacles"),
        "correlations": evidence.get("correlations"),
        "validation": evidence.get("validation"),
        "historical_analogues": evidence.get("historical_analogues"),
        "authority_manifest": evidence.get("authority_manifest"),
    }
    critic_prompt = (
        "You are the independent Critic. You have not received the Analyst verdict. "
        "Audit only the supplied canonical evidence. Do not calculate prices, risk, probability, expectancy, SL, TP, or size. "
        "Use PASS, BLOCK, or ABSTAIN. BLOCK requires an allowed qualitative veto code and exact evidence paths. "
        "Use ABSTAIN for uncertainty or insufficient evidence. Return strict JSON only and do not expose hidden reasoning. "
        "Reference only the supplied candidate_index; Python owns all request, "
        "candidate, provider, model, and schema identity."
    )
    critic_result = provider.generate_structured(
        role="critic",
        system_prompt=critic_prompt,
        evidence=critic_evidence,
        response_schema=ModelCriticDecision,
        request_metadata=request_metadata,
    )
    model_critic = _dump(critic_result.parsed)
    if int(model_critic.get("candidate_index", -1)) != candidate_index:
        raise ValueError("critic_candidate_index_mismatch")
    critic = CriticDecision.model_validate(
        {
            **model_critic,
            "request_id": str(request_metadata.get("request_id") or ""),
            "request_identity_hash": str(
                request_metadata.get("request_identity_hash") or ""
            ),
            "provider_id": str(critic_result.provider_id),
            "model_id": str(critic_result.actual_model),
            "role_schema_version": ROLE_CONTRACT_VERSION,
            "role_contract_version": ROLE_CONTRACT_VERSION,
            "role": "critic",
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
        }
    ).model_dump()
    _validate_role_binding(
        critic,
        role="critic",
        request_metadata=request_metadata,
        expected_provider_id=critic_result.provider_id,
        expected_model_id=critic_result.actual_model,
    )
    if str(critic.get("candidate_id") or "") != candidate_id or str(critic.get("candidate_hash") or "") != candidate_hash:
        raise ValueError("critic_candidate_identity_mismatch")
    _validate_objections(critic, critic_evidence)
    critic_verdict = str(critic.get("verdict") or "").upper()
    analyst_band = str(analyst_assessment.get("confidence_band") or "").upper()
    analyst_missing = analyst_assessment.get("missing_required_evidence") or []
    requires_adjudication = bool(
        critic_verdict != "PASS"
        or analyst_state == DECISION_ABSTAIN
        or analyst_band == "LOW"
        or analyst_missing
        or near_deterministic_boundary
    )
    if not requires_adjudication:
        if analyst_state == DECISION_APPROVE and critic_verdict == "PASS":
            return ConsensusResult(DECISION_APPROVE, True, "analyst_approve_critic_pass", critic, {}, critic_result, None)
        if analyst_state == DECISION_REJECT:
            return ConsensusResult(DECISION_REJECT, False, "analyst_reject", critic, {}, critic_result, None)
        return ConsensusResult(DECISION_ABSTAIN, False, "unresolved_analyst_state", critic, {}, critic_result, None)

    adjudicator_evidence = {
        "request_id": str(request_metadata.get("request_id") or ""),
        "request_identity_hash": str(request_metadata.get("request_identity_hash") or ""),
        "candidate": selected_evidence,
        "validation": evidence.get("validation"),
        "authority_manifest": evidence.get("authority_manifest"),
        "analyst": dict(analyst_assessment),
        "critic": critic,
    }
    adjudicator_prompt = (
        "You are the conditional Adjudicator. Resolve only qualitative disputes using exact supplied evidence paths. "
        "You cannot override hard blockers, missing required fields, invalid taxonomy, data-lineage failure, risk limits, "
        "broker constraints, target infeasibility, or repeatability authority. You cannot invent numerical probability, expectancy, "
        "prices, SL, TP, or size. Use UPHOLD_APPROVE only when every Critic blocking objection is explicitly resolved; otherwise "
        "use UPHOLD_BLOCK or ABSTAIN. Return strict JSON only and no hidden reasoning. "
        "Reference only the supplied candidate_index; Python owns all request, "
        "candidate, provider, model, and schema identity."
    )
    adjudicator_result = provider.generate_structured(
        role="adjudicator",
        system_prompt=adjudicator_prompt,
        evidence=adjudicator_evidence,
        response_schema=ModelAdjudicatorDecision,
        request_metadata=request_metadata,
    )
    model_adjudicator = _dump(adjudicator_result.parsed)
    if int(model_adjudicator.get("candidate_index", -1)) != candidate_index:
        raise ValueError("adjudicator_candidate_index_mismatch")
    adjudicator = AdjudicatorDecision.model_validate(
        {
            **model_adjudicator,
            "request_id": str(request_metadata.get("request_id") or ""),
            "request_identity_hash": str(
                request_metadata.get("request_identity_hash") or ""
            ),
            "provider_id": str(adjudicator_result.provider_id),
            "model_id": str(adjudicator_result.actual_model),
            "role_schema_version": ROLE_CONTRACT_VERSION,
            "role_contract_version": ROLE_CONTRACT_VERSION,
            "role": "adjudicator",
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
        }
    ).model_dump()
    _validate_role_binding(
        adjudicator,
        role="adjudicator",
        request_metadata=request_metadata,
        expected_provider_id=adjudicator_result.provider_id,
        expected_model_id=adjudicator_result.actual_model,
    )
    if str(adjudicator.get("candidate_id") or "") != candidate_id or str(adjudicator.get("candidate_hash") or "") != candidate_hash:
        raise ValueError("adjudicator_candidate_identity_mismatch")
    _validate_adjudication(adjudicator, critic, adjudicator_evidence)
    adjudicator_verdict = str(adjudicator.get("verdict") or "").upper()

    # An adjudicator may preserve an Analyst APPROVE after resolving a critic's
    # qualitative objection. It cannot create approval from Analyst REJECT or
    # ABSTAIN and cannot alter any deterministic plan field.
    if analyst_state == DECISION_APPROVE and adjudicator_verdict == "UPHOLD_APPROVE":
        return ConsensusResult(DECISION_APPROVE, True, "adjudicator_resolved_qualitative_dispute", critic, adjudicator, critic_result, adjudicator_result)
    if analyst_state == DECISION_REJECT or adjudicator_verdict == "UPHOLD_BLOCK":
        return ConsensusResult(DECISION_REJECT, False, "qualitative_block_upheld", critic, adjudicator, critic_result, adjudicator_result)
    return ConsensusResult(DECISION_ABSTAIN, False, "qualitative_dispute_unresolved", critic, adjudicator, critic_result, adjudicator_result)
