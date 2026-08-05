from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_provider import PROVIDER_CONTRACT_VERSION
from architecture_contracts import FILE_BUS_LIFECYCLE_VERSION
from decision_evidence import EVIDENCE_ENVELOPE_VERSION
from decision_integrity import (
    AI_DECISION_SCHEMA_VERSION,
    AI_PROMPT_CONTRACT_VERSION,
    AI_REQUEST_IDENTITY_VERSION,
    AI_ROLE_CONTRACT_VERSION,
    AI_TARGET_ARBITRATION_SCHEMA_VERSION,
)
from decision_pipeline import CONSENSUS_RESOLVER_VERSION
from family_context import FAMILY_PROFILE_VERSION
from governance_contracts import SETUP_TAXONOMY_VERSION
from request_lifecycle import REQUEST_LIFECYCLE_VERSION
from runtime_governance import REPEATABILITY_SCHEMA_VERSION
from trade_memory import RETRIEVAL_POLICY_VERSION, TRADE_MEMORY_SCHEMA_VERSION


CONTRACT_MANIFEST_VERSION = "20260724_contract_compatibility_v1"
ENGINE_VERSION = "5.5-version-z-canonical-request-20260724-v8"
ENGINE_INPUT_SCHEMA = "po3-fvg-ai-provider-version-z-20260724-v7"

_FIELD_ORDER = (
    "contract_manifest_version",
    "engine_version",
    "engine_input_schema",
    "decision_schema_version",
    "target_arbitration_schema_version",
    "prompt_contract_version",
    "role_contract_version",
    "provider_contract_version",
    "file_bus_lifecycle_version",
    "request_lifecycle_version",
    "request_identity_version",
    "setup_taxonomy_version",
    "family_profile_version",
    "retrieval_policy_version",
    "consensus_resolver_version",
    "evidence_envelope_version",
    "trade_memory_schema_version",
    "repeatability_schema_version",
)


def fnv1a_decimal(value: str) -> str:
    result = 2166136261
    for byte in value.encode("ascii"):
        result ^= byte
        result = (result * 16777619) & 0xFFFFFFFF
    return str(result % 2147483647)


def compatibility_manifest() -> dict[str, str]:
    return {
        "contract_manifest_version": CONTRACT_MANIFEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "engine_input_schema": ENGINE_INPUT_SCHEMA,
        "decision_schema_version": AI_DECISION_SCHEMA_VERSION,
        "target_arbitration_schema_version": AI_TARGET_ARBITRATION_SCHEMA_VERSION,
        "prompt_contract_version": AI_PROMPT_CONTRACT_VERSION,
        "role_contract_version": AI_ROLE_CONTRACT_VERSION,
        "provider_contract_version": PROVIDER_CONTRACT_VERSION,
        "file_bus_lifecycle_version": FILE_BUS_LIFECYCLE_VERSION,
        "request_lifecycle_version": REQUEST_LIFECYCLE_VERSION,
        "request_identity_version": AI_REQUEST_IDENTITY_VERSION,
        "setup_taxonomy_version": SETUP_TAXONOMY_VERSION,
        "family_profile_version": FAMILY_PROFILE_VERSION,
        "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
        "consensus_resolver_version": CONSENSUS_RESOLVER_VERSION,
        "evidence_envelope_version": EVIDENCE_ENVELOPE_VERSION,
        "trade_memory_schema_version": TRADE_MEMORY_SCHEMA_VERSION,
        "repeatability_schema_version": REPEATABILITY_SCHEMA_VERSION,
    }


def compatibility_manifest_material(
    manifest: Mapping[str, Any] | None = None,
) -> str:
    values = manifest or compatibility_manifest()
    return "|".join(
        f"{field}={str(values.get(field) or '')}" for field in _FIELD_ORDER
    )


def compatibility_manifest_hash(
    manifest: Mapping[str, Any] | None = None,
) -> str:
    return fnv1a_decimal(compatibility_manifest_material(manifest))


@dataclass(frozen=True)
class ContractCompatibilityResult:
    compatible: bool
    expected_hash: str
    actual_hash: str
    mismatches: tuple[str, ...]


def validate_mql_contract(payload: Mapping[str, Any]) -> ContractCompatibilityResult:
    expected = compatibility_manifest()
    supplied = payload.get("contract_manifest")
    supplied_manifest = supplied if isinstance(supplied, Mapping) else {}
    runtime_inputs = payload.get("runtime_inputs")
    runtime = runtime_inputs if isinstance(runtime_inputs, Mapping) else {}

    actual_values = {
        "contract_manifest_version": payload.get("contract_manifest_version")
        or supplied_manifest.get("contract_manifest_version"),
        "engine_version": payload.get("engine_version")
        or supplied_manifest.get("engine_version"),
        "engine_input_schema": payload.get("input_schema_version")
        or runtime.get("engine_input_schema")
        or supplied_manifest.get("engine_input_schema"),
        "decision_schema_version": payload.get("decision_schema_version")
        or runtime.get("ai_decision_schema_version")
        or supplied_manifest.get("decision_schema_version"),
        "target_arbitration_schema_version": runtime.get(
            "ai_target_arbitration_schema_version"
        )
        or supplied_manifest.get("target_arbitration_schema_version"),
        "prompt_contract_version": runtime.get("ai_prompt_contract_version")
        or supplied_manifest.get("prompt_contract_version"),
        "role_contract_version": supplied_manifest.get("role_contract_version"),
        "provider_contract_version": supplied_manifest.get(
            "provider_contract_version"
        ),
        "file_bus_lifecycle_version": supplied_manifest.get(
            "file_bus_lifecycle_version"
        ),
        "request_lifecycle_version": supplied_manifest.get(
            "request_lifecycle_version"
        ),
        "request_identity_version": payload.get("request_identity_version")
        or supplied_manifest.get("request_identity_version"),
        "setup_taxonomy_version": supplied_manifest.get(
            "setup_taxonomy_version"
        ),
        "family_profile_version": supplied_manifest.get("family_profile_version"),
        "retrieval_policy_version": supplied_manifest.get(
            "retrieval_policy_version"
        ),
        "consensus_resolver_version": supplied_manifest.get(
            "consensus_resolver_version"
        ),
        "evidence_envelope_version": supplied_manifest.get(
            "evidence_envelope_version"
        ),
        "trade_memory_schema_version": supplied_manifest.get(
            "trade_memory_schema_version"
        ),
        "repeatability_schema_version": runtime.get(
            "repeatability_schema_version"
        )
        or supplied_manifest.get("repeatability_schema_version"),
    }
    mismatch_fields = [
        field
        for field in _FIELD_ORDER
        if str(actual_values.get(field) or "") != str(expected[field])
    ]
    for field in _FIELD_ORDER:
        if (
            field in supplied_manifest
            and str(supplied_manifest.get(field) or "") != str(expected[field])
        ):
            mismatch_fields.append(field)
    mismatches = tuple(dict.fromkeys(mismatch_fields))
    actual_hash = str(payload.get("contract_manifest_hash") or "")
    expected_hash = compatibility_manifest_hash(expected)
    if actual_hash != expected_hash:
        mismatches = tuple(dict.fromkeys((*mismatches, "contract_manifest_hash")))
    return ContractCompatibilityResult(
        compatible=not mismatches,
        expected_hash=expected_hash,
        actual_hash=actual_hash,
        mismatches=mismatches,
    )
