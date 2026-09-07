"""Test isolation from the live MT5/AI runtime artifacts."""

import pytest


@pytest.fixture(autouse=True)
def disable_live_ai_usage_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A unit test must never append synthetic calls to production usage logs."""

    monkeypatch.setenv("AI_USAGE_LOG_ENABLE", "false")
