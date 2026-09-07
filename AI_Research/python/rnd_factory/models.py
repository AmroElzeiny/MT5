from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .constants import CONFIDENCE_LEVELS, RECOMMENDATIONS, RESPONSE_SCHEMA_VERSION


@dataclass
class DataQualityReport:
    status: str
    total_records: int
    usable_records: int
    duplicate_records: int = 0
    malformed_records: int = 0
    missing_outcomes: int = 0
    identity_warnings: int = 0
    schema_versions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetricSummary:
    count: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float | None
    win_rate_ci95: list[float] | None
    expectancy_r: float | None
    median_r: float | None
    expectancy_ci95: list[float] | None
    profit_factor: float | None
    avg_mfe_r: float | None
    avg_mae_r: float | None
    avg_execution_cost: float | None
    sample_strength: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CodeSnippet:
    path: str
    start_line: int
    end_line: int
    matched_terms: list[str]
    content: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AIResearchResult:
    role: str
    request_id: str
    research_run_id: str
    request_hash: str
    schema_version: str
    most_likely_explanation: str
    alternative_explanations: list[str]
    evidence_for: list[str]
    evidence_against: list[str]
    uncertainty: list[str]
    more_data_needed: bool
    hypothesis: dict[str, Any] | None
    confidence: str
    production_recommendation: str
    resolved_objection_codes: list[str] = field(default_factory=list)

    @classmethod
    def validate(cls, value: Mapping[str, Any], *, expected_role: str, request_id: str, research_run_id: str, request_hash: str) -> "AIResearchResult":
        required = {
            "role", "request_id", "research_run_id", "request_hash", "schema_version",
            "most_likely_explanation", "alternative_explanations", "evidence_for",
            "evidence_against", "uncertainty", "more_data_needed", "hypothesis",
            "confidence", "production_recommendation",
        }
        missing = sorted(required.difference(value.keys()))
        if missing:
            raise ValueError("ai_response_missing_fields:" + ",".join(missing))
        allowed = required | {"resolved_objection_codes"}
        extras = sorted(set(value.keys()).difference(allowed))
        if extras:
            raise ValueError("ai_response_additional_fields:" + ",".join(extras))
        if str(value["role"]) != expected_role:
            raise ValueError("ai_response_role_mismatch")
        if str(value["request_id"]) != request_id:
            raise ValueError("ai_response_request_id_mismatch")
        if str(value["research_run_id"]) != research_run_id:
            raise ValueError("ai_response_research_run_id_mismatch")
        if str(value["request_hash"]) != request_hash:
            raise ValueError("ai_response_request_hash_mismatch")
        if str(value["schema_version"]) != RESPONSE_SCHEMA_VERSION:
            raise ValueError("ai_response_schema_version_mismatch")
        confidence = str(value["confidence"]).lower()
        if confidence not in CONFIDENCE_LEVELS:
            raise ValueError("ai_response_invalid_confidence")
        recommendation = str(value["production_recommendation"]).upper()
        if recommendation not in RECOMMENDATIONS:
            raise ValueError("ai_response_invalid_recommendation")
        list_fields = ("alternative_explanations", "evidence_for", "evidence_against", "uncertainty")
        for field_name in list_fields:
            if not isinstance(value[field_name], list) or not all(isinstance(x, str) for x in value[field_name]):
                raise ValueError(f"ai_response_invalid_{field_name}")
        hypothesis = value.get("hypothesis")
        if hypothesis is not None and not isinstance(hypothesis, Mapping):
            raise ValueError("ai_response_invalid_hypothesis")
        return cls(
            role=expected_role,
            request_id=request_id,
            research_run_id=research_run_id,
            request_hash=request_hash,
            schema_version=RESPONSE_SCHEMA_VERSION,
            most_likely_explanation=str(value["most_likely_explanation"]),
            alternative_explanations=list(value["alternative_explanations"]),
            evidence_for=list(value["evidence_for"]),
            evidence_against=list(value["evidence_against"]),
            uncertainty=list(value["uncertainty"]),
            more_data_needed=bool(value["more_data_needed"]),
            hypothesis=dict(hypothesis) if hypothesis is not None else None,
            confidence=confidence,
            production_recommendation=recommendation,
            resolved_objection_codes=[str(x) for x in value.get("resolved_objection_codes", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


AI_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "role", "request_id", "research_run_id", "request_hash", "schema_version",
        "most_likely_explanation", "alternative_explanations", "evidence_for",
        "evidence_against", "uncertainty", "more_data_needed", "hypothesis",
        "confidence", "production_recommendation", "resolved_objection_codes",
    ],
    "properties": {
        "role": {"type": "string"},
        "request_id": {"type": "string"},
        "research_run_id": {"type": "string"},
        "request_hash": {"type": "string"},
        "schema_version": {"type": "string", "const": RESPONSE_SCHEMA_VERSION},
        "most_likely_explanation": {"type": "string"},
        "alternative_explanations": {"type": "array", "items": {"type": "string"}},
        "evidence_for": {"type": "array", "items": {"type": "string"}},
        "evidence_against": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "array", "items": {"type": "string"}},
        "more_data_needed": {"type": "boolean"},
        "hypothesis": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "statement", "subsystem", "mechanism", "expected_improvement", "risks", "required_experiment"],
                    "properties": {
                        "title": {"type": "string"},
                        "statement": {"type": "string"},
                        "subsystem": {"type": "string"},
                        "mechanism": {"type": "string"},
                        "expected_improvement": {"type": "string"},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "required_experiment": {"type": "string"},
                    },
                },
            ]
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "production_recommendation": {"type": "string", "enum": sorted(RECOMMENDATIONS)},
        "resolved_objection_codes": {"type": "array", "items": {"type": "string"}},
    },
}
