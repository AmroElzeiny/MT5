import asyncio
import json
from dataclasses import replace

import bridge.browser_worker as browser_worker
import bridge.response_extract as response_extract
from bridge.browser_worker import AutonomousBrowserWorker, _schema_repair_prompt
from bridge.config import settings
from bridge.prompting import response_filename


def test_gemini_upload_demands_identity_bound_download(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browser_worker,
        "settings",
        replace(settings, upload_dir=tmp_path),
    )
    job = {
        "id": "ab12cd34-rest-of-job-id",
        "request_id": "po3-request-7",
        "prompt": "Evaluate the supplied candidate.",
        "schema_json": json.dumps({
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"type": "string"}},
            "additionalProperties": False,
        }),
    }

    path = AutonomousBrowserWorker()._prepare_gemini_upload(job)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    expected = response_filename(job["id"])

    assert envelope["bridge_request_id"] == job["request_id"]
    assert envelope["required_response_filename"] == expected
    assert expected in envelope["response_requirement"]
    assert "DOWNLOADABLE FILE" in envelope["response_requirement"]
    assert "No Markdown, file attachment" not in envelope["response_requirement"]


def test_gemini_schema_repair_keeps_download_delivery_contract():
    job = {"id": "feedbeef-job", "request_id": "po3-request-9"}
    schema = {
        "type": "object",
        "required": ["decision"],
        "properties": {"decision": {"type": "string"}},
    }
    prompt = _schema_repair_prompt(
        job,
        schema,
        [],
        response_mode="download",
    )

    assert response_filename(job["id"]) in prompt
    assert "DOWNLOADABLE FILE" in prompt


def test_strict_inline_gemini_json_is_materialized_as_identity_bound_file(
    tmp_path, monkeypatch
):
    configured = replace(settings, download_dir=tmp_path)
    monkeypatch.setattr(browser_worker, "settings", configured)
    monkeypatch.setattr(response_extract, "settings", configured)
    monkeypatch.setattr(browser_worker, "log_event", lambda *args, **kwargs: None)
    job = {
        "id": "1234abcd-job",
        "request_id": "po3-gemini-inline",
    }

    obj, normalized = asyncio.run(
        AutonomousBrowserWorker()._a_gemini_materialize_inline_response(
            job, '{"ok":true}'
        )
    )

    assert obj == {"ok": True}
    assert normalized == '{"ok":true}'
    files = list(tmp_path.glob("po3-gemini-inline-gemini-1234abcd.json"))
    assert len(files) == 1
