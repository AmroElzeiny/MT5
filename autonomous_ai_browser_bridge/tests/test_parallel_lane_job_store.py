import time
from types import SimpleNamespace

import pytest

import bridge.job_store as job_store_module
from bridge.browser_worker import AutonomousBrowserWorker


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        job_store_module,
        "settings",
        SimpleNamespace(
            sqlite_file=tmp_path / "lane-jobs.sqlite3",
            hard_timeout_sec=900,
            max_pending_jobs=3,
        ),
    )
    monkeypatch.setattr(job_store_module, "log_event", lambda *args, **kwargs: None)
    return job_store_module.JobStore()


def test_lane_claims_followup_role_for_the_same_request(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.create("request-a", "model", "po3", "analyst-a", {"title": "ModelAIGateOutput"}, 30)
    store.create("request-b", "model", "po3", "analyst-b", {"title": "ModelAIGateOutput"}, 30)

    first = store.claim_next_pending(excluded_request_ids=set())
    assert first["request_id"] == "request-a"
    store.submit(first["id"], "{}")

    store.create("request-a", "model", "po3", "critic-a", {"title": "ModelCriticDecision"}, 30)
    followup = store.claim_next_pending(preferred_request_id="request-a")
    assert followup["request_id"] == "request-a"
    other = store.claim_next_pending(excluded_request_ids={"request-a"})
    assert other["request_id"] == "request-b"


def test_bridge_rejects_an_already_expired_absolute_deadline(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="deadline_exceeded_before_bridge_queue"):
        store.create(
            "expired",
            "model",
            "po3",
            "prompt",
            None,
            900,
            deadline_at=time.time() - 1,
        )


def test_role_schema_names_drive_request_affinity_release():
    worker = AutonomousBrowserWorker()
    assert worker._job_schema_name({"schema_json": '{"title":"ModelAIGateOutput"}'}) == "ModelAIGateOutput"
    assert worker._job_schema_name({"schema_json": '{"title":"ModelCriticDecision"}'}) == "ModelCriticDecision"
    assert worker._job_schema_name({"schema_json": '{"title":"ModelAdjudicatorDecision"}'}) == "ModelAdjudicatorDecision"
