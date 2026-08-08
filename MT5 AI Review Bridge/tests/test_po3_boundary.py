"""The bridge is a transport. It must never own PO3's authoritative responses.

Two properties are enforced here:

* the generic folder gateway cannot be pointed at a PO3 file bus, not even by an
  absolute path in ``.env`` (``ROOT / "<absolute>"`` silently yields the
  absolute path, so the boundary cannot be left to configuration);
* a queued job is labelled with the caller's real request id.  PO3's analyst
  evidence carries it under ``identity.request_id``; reading only the top level
  labelled every real analyst job with a random uuid, which broke queue
  de-duplication and made the dashboard impossible to correlate with a PO3
  request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.config import PO3BusBoundaryError, assert_not_po3_bus, settings
from bridge.prompting import build_manual_prompt, extract_request_id


def test_po3_bus_folder_target_is_refused() -> None:
    with pytest.raises(PO3BusBoundaryError):
        assert_not_po3_bus(Path("C:/MT5/Common/Files/PO3_AI_BUS/responses"))


def test_po3_bus_is_refused_case_insensitively() -> None:
    with pytest.raises(PO3BusBoundaryError):
        assert_not_po3_bus(Path("C:/MT5/Common/Files/po3_ai_bus/responses"))


def test_ordinary_folder_bus_target_is_allowed() -> None:
    assert_not_po3_bus(Path("C:/AI/bridge/folder_bus/responses"))


def test_live_settings_are_outside_any_po3_bus() -> None:
    assert_not_po3_bus(settings.folder_request_dir, settings.folder_response_dir)


def test_analyst_evidence_request_id_is_read_from_the_identity_block() -> None:
    """PO3's canonical decision-evidence envelope nests the request id."""

    evidence = {
        "evidence_envelope_version": "v1",
        "identity": {"request_id": "po3-request-abc", "session_id": "s1"},
    }
    payload = {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": json.dumps(evidence)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "ModelAIGateOutput", "schema": {"type": "object"}},
        },
    }
    _prompt, schema, request_id = build_manual_prompt(payload)
    assert request_id == "po3-request-abc"
    assert schema == {"type": "object"}


def test_top_level_request_id_still_wins_for_role_evidence() -> None:
    """Critic/adjudicator evidence and the capability probe are top level."""

    assert extract_request_id(['{"request_id":"top-level-id"}']) == "top-level-id"
    assert (
        extract_request_id(['{"request_id":"provider_capability_probe"}'])
        == "provider_capability_probe"
    )


def test_top_level_id_is_preferred_over_a_nested_identity() -> None:
    part = json.dumps({"request_id": "outer", "identity": {"request_id": "inner"}})
    assert extract_request_id([part]) == "outer"


def test_unparseable_or_idless_evidence_yields_no_request_id() -> None:
    assert extract_request_id(["not json"]) == ""
    assert extract_request_id(['{"candidate":"A"}']) == ""
    assert extract_request_id(['["a","list"]']) == ""
    assert extract_request_id([]) == ""


def test_blank_identity_request_id_is_not_returned() -> None:
    part = json.dumps({"identity": {"request_id": "   "}})
    assert extract_request_id([part]) == ""
