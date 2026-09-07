from bridge.prompting import build_automation_prompt


def test_request_id_and_schema():
    payload = {
        "messages": [{"role":"system","content":"S"},{"role":"user","content":"{\"identity\":{\"request_id\":\"r1\"}}"}],
        "response_format":{"type":"json_schema","json_schema":{"name":"X","strict":True,"schema":{"type":"object"}}},
    }
    prompt, schema, rid = build_automation_prompt(payload)
    assert rid == "r1"
    assert schema == {"type":"object"}
    assert "BRIDGE REQUEST ID: r1" in prompt
