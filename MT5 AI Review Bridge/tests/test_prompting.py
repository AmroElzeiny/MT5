from bridge.prompting import build_manual_prompt


def test_extracts_schema_and_request_id():
    payload = {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": '{"request_id":"abc","x":1}'},
        ],
        "response_format": {"type":"json_schema","json_schema":{"name":"X","schema":{"type":"object"}}},
    }
    prompt, schema, rid = build_manual_prompt(payload)
    assert rid == "abc"
    assert schema == {"type": "object"}
    assert "system" in prompt
