"""Provider-neutral multi-role qualitative audit and deterministic consensus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ai_provider import AIProvider, ProviderResult
from evidence_catalog import EvidenceCatalog, resolve_legacy_references
from decision_integrity import (
    AI_ROLE_CONTRACT_VERSION,
    DECISION_ABSTAIN,
    DECISION_APPROVE,
    DECISION_REJECT,
    LLM_VETO_CODES,
)
from structured_models import (
    ADJUDICATOR_VERDICTS,
    AdjudicatorDecision,
    CRITIC_VERDICTS,
    CriticDecision,
    ModelAdjudicatorDecision,
    ModelCriticDecision,
    objection_code_vocabulary_prompt,
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
    adjudication_skipped: bool = False


def _adjudication_is_pointless(request_metadata: Mapping[str, Any]) -> bool:
    """True when adjudicating a hopeless abstain could only cost money.

    The critic is deliberately NOT skippable. Both
    ``ai_gate._mql_tester_cache_skip_reason`` and ``AIGateBridge.mqh:1626``
    require a non-empty ``critic_response_fingerprint`` on every response, so a
    response without a critic is rejected as an invalid contract on both sides.
    The adjudicator fingerprint is optional on both sides, which is exactly why
    the adjudicator is the only role this may drop.

    The saving is safe because the caller's resolver only ever demotes: there is
    no branch that turns a non-approving analyst verdict into an approval. When
    the analyst cannot approve and its own expectancy sits a full margin under
    the gate, the adjudicator can change the cost of the decision but not the
    decision.
    """
    if not bool(request_metadata.get("adjudication_skip_enable")):
        return False
    if not bool(request_metadata.get("live_workload")):
        return False
    if bool(request_metadata.get("analyst_can_approve")):
        return False
    floor = request_metadata.get("adjudication_skip_floor")
    expectancy = request_metadata.get("analyst_expectancy_score")
    if floor is None or expectancy is None:
        return False
    return float(expectancy) <= float(floor)


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


def _resolve_critic_evidence(
    critic: dict[str, Any],
    catalog: "EvidenceCatalog",
    *,
    candidate_index: int | None,
) -> None:
    """Resolve critic catalog IDs to Python-owned canonical paths, in place.

    The critic shares the analyst's catalog, so a single implementation governs
    every role; there is no second reference vocabulary to drift.
    """

    def resolve(container: dict[str, Any], field: str) -> None:
        ids = container.get("evidence_ref_ids")
        if isinstance(ids, list) and ids:
            resolution = catalog.resolve(ids, candidate_index=candidate_index)
        else:
            resolution, _diag = resolve_legacy_references(
                catalog,
                list(container.get("evidence_refs") or []),
                candidate_index=candidate_index,
            )
        if not resolution.valid:
            raise ValueError(f"critic_{field}_evidence_ref_invalid")
        container.pop("evidence_ref_ids", None)
        container["evidence_refs"] = list(resolution.resolved_paths)

    resolve(critic, "top_level")
    for key in ("blocking_objections", "non_blocking_objections"):
        rows = critic.get(key)
        if not isinstance(rows, list):
            continue
        for objection in rows:
            if isinstance(objection, dict):
                resolve(objection, "objection")


def _validate_objections(critic: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    role = str(critic.get("role") or "").lower()
    verdict = str(critic.get("verdict") or "").upper()
    if role != "critic" or critic.get("role_contract_version") != ROLE_CONTRACT_VERSION:
        raise ValueError("critic_role_contract_invalid")
    if verdict not in set(CRITIC_VERDICTS):
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
        # Paths here are Python-generated by _resolve_critic_evidence, so only
        # presence is checked; no path guessing is possible any more.
        refs = objection.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("critic_objection_missing_evidence_refs")
    top_level_refs = critic.get("evidence_refs")
    if not isinstance(top_level_refs, list) or not top_level_refs:
        raise ValueError("critic_evidence_refs_missing")


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
    if verdict not in set(ADJUDICATOR_VERDICTS):
        raise ValueError("adjudicator_verdict_invalid")
    # Paths are Python-generated from the shared catalog before this point.
    refs = adjudicator.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
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


def _selected_candidate_evidence(evidence: Mapping[str, Any], candidate_index: int) -> dict[str, Any]:
    section = evidence.get("entry_and_invalidation")
    rows = section.get("candidates") if isinstance(section, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("evidence_candidates_missing")
    selected = next(
        (
            dict(row)
            for position, row in enumerate(rows)
            if isinstance(row, Mapping)
            and int(row.get("candidate_index", position)) == candidate_index
        ),
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
    evidence_catalog: "EvidenceCatalog",
    near_deterministic_boundary: bool = False,
    event_logger: Callable[[str], None] | None = None,
) -> ConsensusResult:
    candidate_id = str(analyst_assessment.get("candidate_id") or "")
    candidate_hash = str(analyst_assessment.get("candidate_hash") or "")
    candidate_index = int(analyst_assessment.get("candidate_index", -1))
    if candidate_index < 0:
        raise ValueError("analyst_candidate_index_invalid")
    analyst_state = str(analyst_assessment.get("decision_state") or "").upper()
    selected_evidence = _selected_candidate_evidence(evidence, candidate_index)
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
        # The critic cites evidence from the same Python-owned catalog as the
        # analyst, so there is exactly one reference vocabulary in the system.
        "evidence_catalog": {
            "catalog_version": evidence_catalog.catalog_version,
            "catalog_hash": evidence_catalog.catalog_hash,
            "items": [
                row
                for row in evidence_catalog.provider_rows()
                if row.get("c") in (None, candidate_index)
            ],
        },
    }
    critic_allowed_ids = [
        int(row["id"])
        for row in critic_evidence["evidence_catalog"]["items"]
    ]
    critic_evidence["allowed_evidence_ref_ids"] = critic_allowed_ids
    critic_prompt = (
        "You are the independent Critic. You have not received the Analyst verdict. "
        "Audit only the supplied canonical evidence. Do not calculate prices, risk, probability, expectancy, SL, TP, or size. "
        f"Use {', '.join(CRITIC_VERDICTS)}. BLOCK requires at least one blocking objection whose code is one of: "
        f"{objection_code_vocabulary_prompt()}. PASS requires an empty blocking_objections list. "
        "The top-level evidence_ref_ids list is mandatory, and every objection needs evidence_ref_ids. "
        "Every such list must contain distinct integers copied only from allowed_evidence_ref_ids; "
        "never return a path string, duplicate an id, or invent an id. "
        f"For this candidate the complete allowed id set is: {critic_allowed_ids}. "
        "Use ABSTAIN for uncertainty or insufficient evidence. Return strict JSON only and do not expose hidden reasoning. "
        "Reference only the supplied candidate_index; Python owns all request, "
        "candidate, provider, model, and schema identity."
        " Internal candidate IDs, hashes, fingerprints, and any embedded lineage prices are intentionally absent; "
        "they are not evidence and must never be reconstructed or compared with the executable entry. "
        "Treat target_semantics and family_event_evidence as the authoritative interpretation contracts."
    )
    critic_metadata = dict(request_metadata)
    if request_metadata.get("critic_timeout_sec") is not None:
        critic_metadata["timeout_sec"] = float(request_metadata["critic_timeout_sec"])
    critic_result = provider.generate_structured(
        role="critic",
        system_prompt=critic_prompt,
        evidence=critic_evidence,
        response_schema=ModelCriticDecision,
        request_metadata=critic_metadata,
    )
    model_critic = _dump(critic_result.parsed)
    if int(model_critic.get("candidate_index", -1)) != candidate_index:
        raise ValueError("critic_candidate_index_mismatch")
    try:
        _resolve_critic_evidence(
            model_critic,
            evidence_catalog,
            candidate_index=candidate_index,
        )
    except ValueError as critic_evidence_error:
        error_text = str(critic_evidence_error)
        if not error_text.startswith("critic_") or not error_text.endswith(
            "_evidence_ref_invalid"
        ):
            raise
        # Structured output constrains the ID type but cannot enforce the
        # candidate-specific subset.  Give the same provider one bounded
        # correction pass.  The invalid first answer never reaches consensus
        # and Python still resolves every returned ID against the frozen
        # catalog before it can carry authority.
        repair_prompt = (
            critic_prompt
            + "\n\nCRITIC EVIDENCE CITATION CORRECTION PASS. Your previous "
            "complete Critic response was rejected before consensus because an "
            "evidence_ref_ids list was outside the allowed set. Return the entire "
            "Critic response again, not a patch. Every top-level and objection "
            "evidence_ref_ids list must contain distinct integers copied only "
            f"from this exact allowed set: {critic_allowed_ids}. The detected "
            f"failure was {error_text}."
        )
        if event_logger is not None:
            event_logger(
                "[critic_evidence_reference_repair]"
                f" request_id={request_metadata.get('request_id') or ''}"
                " attempt=1 provider_same=true authoritative_previous=false"
                f" reason={error_text}"
            )
        repaired_critic_result = provider.generate_structured(
            role="critic",
            system_prompt=repair_prompt,
            evidence=critic_evidence,
            response_schema=ModelCriticDecision,
            request_metadata=critic_metadata,
        )
        repaired_model_critic = _dump(repaired_critic_result.parsed)
        if int(repaired_model_critic.get("candidate_index", -1)) != candidate_index:
            if event_logger is not None:
                event_logger(
                    "[critic_evidence_reference_repair]"
                    f" request_id={request_metadata.get('request_id') or ''}"
                    " attempt=1 result=failed reason=critic_candidate_index_mismatch"
                )
            raise ValueError("critic_candidate_index_mismatch")
        try:
            _resolve_critic_evidence(
                repaired_model_critic,
                evidence_catalog,
                candidate_index=candidate_index,
            )
        except ValueError as repaired_evidence_error:
            if event_logger is not None:
                event_logger(
                    "[critic_evidence_reference_repair]"
                    f" request_id={request_metadata.get('request_id') or ''}"
                    " attempt=1 result=failed"
                    f" reason={str(repaired_evidence_error)}"
                )
            raise
        critic_result = repaired_critic_result
        model_critic = repaired_model_critic
        if event_logger is not None:
            event_logger(
                "[critic_evidence_reference_repair]"
                f" request_id={request_metadata.get('request_id') or ''}"
                " attempt=1 result=valid authoritative=true"
            )
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
    skip_adjudication = requires_adjudication and _adjudication_is_pointless(request_metadata)
    if skip_adjudication and event_logger is not None:
        event_logger(
            "[adjudication_skipped]"
            f" request_id={request_metadata.get('request_id') or ''}"
            f" symbol={request_metadata.get('symbol') or ''}"
            f" analyst_state={analyst_state}"
            f" critic_verdict={critic_verdict}"
            f" expectancy={float(request_metadata.get('analyst_expectancy_score') or 0.0):.4f}"
            f" floor={float(request_metadata.get('adjudication_skip_floor') or 0.0):.4f}"
            " role_skipped=adjudicator critic_ran=true outcome_changed=false"
        )

    if not requires_adjudication or skip_adjudication:
        if analyst_state == DECISION_APPROVE and critic_verdict == "PASS":
            return ConsensusResult(DECISION_APPROVE, True, "analyst_approve_critic_pass", critic, {}, critic_result, None)
        if analyst_state == DECISION_REJECT:
            return ConsensusResult(DECISION_REJECT, False, "analyst_reject", critic, {}, critic_result, None)
        if skip_adjudication:
            # Named for what actually happened, so a skipped adjudication is
            # never read back as an unresolved one.
            return ConsensusResult(
                DECISION_ABSTAIN,
                False,
                "analyst_abstain_below_adjudication_floor",
                critic,
                {},
                critic_result,
                None,
                adjudication_skipped=True,
            )
        return ConsensusResult(DECISION_ABSTAIN, False, "unresolved_analyst_state", critic, {}, critic_result, None)

    adjudicator_evidence = {
        "request_id": str(request_metadata.get("request_id") or ""),
        "request_identity_hash": str(request_metadata.get("request_identity_hash") or ""),
        "candidate": selected_evidence,
        "validation": evidence.get("validation"),
        "authority_manifest": evidence.get("authority_manifest"),
        "analyst": dict(analyst_assessment),
        "critic": critic,
        "evidence_catalog": {
            "catalog_version": evidence_catalog.catalog_version,
            "catalog_hash": evidence_catalog.catalog_hash,
            "items": [
                row
                for row in evidence_catalog.provider_rows()
                if row.get("c") in (None, candidate_index)
            ],
        },
    }
    adjudicator_allowed_ids = [
        int(row["id"])
        for row in adjudicator_evidence["evidence_catalog"]["items"]
    ]
    adjudicator_prompt = (
        "You are the conditional Adjudicator. Resolve only qualitative disputes using evidence_catalog ids. "
        "You cannot override hard blockers, missing required fields, invalid taxonomy, data-lineage failure, risk limits, "
        "broker constraints, target infeasibility, or repeatability authority. You cannot invent numerical probability, expectancy, "
        "prices, SL, TP, or size. Use UPHOLD_APPROVE only when every Critic blocking objection is explicitly resolved; otherwise "
        "use UPHOLD_BLOCK or ABSTAIN. Return strict JSON only and no hidden reasoning. "
        "evidence_ref_ids is mandatory and must contain between 1 and 16 distinct integers. "
        "Use only ids visibly supplied in evidence_catalog.items, never duplicate an id, never invent an id, "
        "and never cite evidence belonging to another candidate. "
        f"For this candidate the complete allowed id set is: {adjudicator_allowed_ids}. "
        "Reference only the supplied candidate_index; Python owns all request, "
        "candidate, provider, model, and schema identity."
        " Internal candidate IDs, hashes, fingerprints, and any embedded lineage prices are intentionally absent; "
        "they are not evidence and must never be reconstructed or compared with the executable entry. "
        "Treat target_semantics and family_event_evidence as the authoritative interpretation contracts."
    )
    adjudicator_metadata = dict(request_metadata)
    if request_metadata.get("adjudicator_timeout_sec") is not None:
        adjudicator_metadata["timeout_sec"] = float(
            request_metadata["adjudicator_timeout_sec"]
        )
    adjudicator_result = provider.generate_structured(
        role="adjudicator",
        system_prompt=adjudicator_prompt,
        evidence=adjudicator_evidence,
        response_schema=ModelAdjudicatorDecision,
        request_metadata=adjudicator_metadata,
    )
    model_adjudicator = _dump(adjudicator_result.parsed)
    if int(model_adjudicator.get("candidate_index", -1)) != candidate_index:
        raise ValueError("adjudicator_candidate_index_mismatch")
    adjudicator_ids = model_adjudicator.pop("evidence_ref_ids", None)
    adjudicator_resolution = evidence_catalog.resolve(
        adjudicator_ids if isinstance(adjudicator_ids, list) else [],
        candidate_index=candidate_index,
    )
    if adjudicator_ids and not adjudicator_resolution.valid:
        raise ValueError("adjudicator_evidence_refs_invalid")
    model_adjudicator["evidence_refs"] = list(adjudicator_resolution.resolved_paths)
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
