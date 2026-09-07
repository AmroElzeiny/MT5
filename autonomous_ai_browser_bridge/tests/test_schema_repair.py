from bridge.browser_worker import (
    _schema_error_details,
    _schema_errors,
    _schema_repair_prompt,
    _schema_turn_binding,
)
from bridge.prompting import response_filename


def _schema():
    return {
        "title": "ModelAIGateOutput",
        "type": "object",
        "properties": {
            "decision_quality_tier": {"type": "string"},
            "response_quality": {"type": "string"},
            "selected_candidate_index": {"type": "integer"},
        },
        "required": [
            "decision_quality_tier",
            "response_quality",
            "selected_candidate_index",
        ],
        "additionalProperties": False,
    }


def _job():
    return {
        "id": "12345678-1234-5678-1234-567812345678",
        "request_id": "po3-request-7",
    }


def test_schema_turn_binding_restates_current_role_and_required_keys():
    binding = _schema_turn_binding(_job(), _schema())
    assert "CURRENT BRIDGE REQUEST ID: po3-request-7" in binding
    assert "CURRENT RESPONSE SCHEMA: ModelAIGateOutput" in binding
    assert "selected_candidate_index" in binding
    assert "Ignore every earlier attachment" in binding


def test_schema_repair_is_bound_to_same_job_and_does_not_relax_validation():
    errors = _schema_errors(
        _schema(),
        {"candidate_index": 1, "verdict": "ACCEPT"},
    )
    assert errors
    prompt = _schema_repair_prompt(_job(), _schema(), errors)
    assert "previous object was rejected and has no authority" in prompt
    assert "SAME evidence and substantive judgment" in prompt
    assert response_filename(_job()["id"]) in prompt
    assert "Additional properties are not allowed" in prompt


def test_schema_repair_acceptance_still_requires_exact_schema():
    valid = {
        "decision_quality_tier": "FULL_STRUCTURED",
        "response_quality": "FULL_STRUCTURED",
        "selected_candidate_index": 0,
    }
    assert _schema_errors(_schema(), valid) == []
    assert _schema_errors(_schema(), {**valid, "error": "stale"})


def test_schema_repair_reports_max_length_without_truncating_the_cause():
    schema = {
        "type": "object",
        "properties": {"resolution_reason": {"type": "string", "maxLength": 12}},
        "required": ["resolution_reason"],
    }
    errors = _schema_errors(schema, {"resolution_reason": "x" * 40})
    details = _schema_error_details(errors)
    assert details == [
        "resolution_reason: string length 40 exceeds maxLength 12; shorten it without changing the judgment"
    ]
    prompt = _schema_repair_prompt(_job(), schema, errors)
    assert "exceeds maxLength 12" in prompt
