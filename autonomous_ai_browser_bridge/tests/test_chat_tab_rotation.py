from types import SimpleNamespace

import pytest

import bridge.browser_worker as worker
from bridge.browser_worker import (
    _ChatTabRotation,
    _ChatTabSlot,
    _configured_chat_tab_urls,
)


def _rotation(tab_count: int, requests_per_tab: int) -> _ChatTabRotation:
    slots = [
        _ChatTabSlot(page=object(), number=index + 1, bound_alias="po3")
        for index in range(tab_count)
    ]
    return _ChatTabRotation(slots, requests_per_tab)


class _FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.closed = False
        self.front = False

    def close(self) -> None:
        self.closed = True

    def bring_to_front(self) -> None:
        self.front = True


class _FakeContext:
    def __init__(self, pages):
        self.pages = list(pages)

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page


def test_rotation_sends_a_full_batch_to_each_tab_then_wraps():
    rotation = _rotation(tab_count=3, requests_per_tab=2)
    selected = []
    events = []
    for _ in range(7):
        selected.append(rotation.current.number)
        events.append(rotation.record_submission())

    assert selected == [1, 1, 2, 2, 3, 3, 1]
    assert [event["rotated"] for event in events] == [False, True, False, True, False, True, False]
    assert rotation.state() == {
        "chat_tab_count": 3,
        "open_chat_tabs": 3,
        "active_chat_tab": 1,
        "requests_on_active_tab": 1,
        "requests_per_tab": 2,
        "total_tab_submissions": 7,
    }


def test_worker_opens_exactly_x_managed_tabs_and_closes_surplus(monkeypatch):
    pages = [_FakePage(), _FakePage(), _FakePage(), _FakePage("https://stale.example")]
    context = _FakeContext(pages)
    browser_worker = worker.AutonomousBrowserWorker()
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(default_chat_alias="po3", browser_requests_per_tab=2),
    )
    monkeypatch.setattr(
        browser_worker,
        "_navigate",
        lambda page, url: setattr(page, "url", url),
    )
    monkeypatch.setattr(worker, "log_event", lambda *args, **kwargs: None)

    rotation = browser_worker._open_chat_tabs(
        context,
        pages[0],
        [
            "https://provider.example/new/1",
            "https://provider.example/new/2",
            "https://provider.example/new/3",
        ],
    )

    assert len(rotation.slots) == 3
    assert [slot.page.url for slot in rotation.slots] == [
        "https://provider.example/new/1",
        "https://provider.example/new/2",
        "https://provider.example/new/3",
    ]
    assert pages[3].closed is True
    assert pages[0].front is True


def test_new_chat_url_is_repeated_once_per_configured_tab(monkeypatch):
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(
            browser_chat_tab_urls=(),
            browser_chat_new_url="https://provider.example/new-chat",
        ),
    )
    assert _configured_chat_tab_urls(3, "https://provider.example/seed") == [
        "https://provider.example/new-chat",
        "https://provider.example/new-chat",
        "https://provider.example/new-chat",
    ]


def test_explicit_tab_urls_must_match_configured_tab_count(monkeypatch):
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(
            browser_chat_tab_urls=("https://provider.example/chat/1",),
            browser_chat_new_url="",
        ),
    )
    with pytest.raises(RuntimeError, match="BROWSER_CHAT_TAB_URLS_count_mismatch"):
        _configured_chat_tab_urls(2, "https://provider.example/seed")


def test_multiple_tabs_fail_closed_without_distinct_chat_source(monkeypatch):
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(browser_chat_tab_urls=(), browser_chat_new_url=""),
    )
    with pytest.raises(RuntimeError, match="multi_chat_requires"):
        _configured_chat_tab_urls(2, "https://provider.example/chat/seed")


def test_explicit_tab_urls_must_be_unique(monkeypatch):
    duplicate = "https://provider.example/chat/same"
    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(
            browser_chat_tab_urls=(duplicate, duplicate),
            browser_chat_new_url="",
        ),
    )
    with pytest.raises(RuntimeError, match="must_be_unique"):
        _configured_chat_tab_urls(2, "https://provider.example/chat/seed")


def test_runtime_detects_tabs_that_resolve_to_the_same_conversation():
    shared = "https://provider.example/chat/shared"
    rotation = _ChatTabRotation(
        [
            _ChatTabSlot(
                page=SimpleNamespace(url=shared),
                number=1,
                bound_alias="po3",
            ),
            _ChatTabSlot(
                page=SimpleNamespace(url=shared),
                number=2,
                bound_alias="po3",
            ),
        ],
        requests_per_tab=1,
    )
    rotation.record_submission()
    assert rotation.duplicate_submitted_urls() == {}
    rotation.record_submission()
    assert rotation.duplicate_submitted_urls() == {shared: [1, 2]}
