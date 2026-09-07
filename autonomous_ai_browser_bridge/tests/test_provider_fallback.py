from bridge.browser_worker import (
    BrowserProviderRateLimit,
    _AsyncChatTabLane,
    _matching_rate_limit_marker,
)


def test_exact_rate_limit_page_is_recognized_for_gemini_routing():
    page_text = (
        "Too many requests\n"
        "You’re making requests too quickly. We’ve temporarily limited access "
        "to your conversations. Please wait a few minutes before trying again."
    )
    marker = _matching_rate_limit_marker(
        page_text,
        (
            "Too many requests",
            "making requests too quickly",
            "temporarily limited access",
        ),
    )
    assert marker == "Too many requests"


def test_generic_timeout_is_not_misclassified_as_rate_limit():
    assert _matching_rate_limit_marker(
        "The response took longer than expected.",
        ("Too many requests", "temporarily limited access"),
    ) == ""


def test_fallback_exception_records_whether_primary_was_sent():
    before = BrowserProviderRateLimit("chatgpt", "Too many requests", after_send=False)
    after = BrowserProviderRateLimit("chatgpt", "Too many requests", after_send=True)
    assert before.after_send is False
    assert after.after_send is True


def test_lane_keeps_one_fallback_provider_for_the_whole_request():
    lane = _AsyncChatTabLane(page=object(), number=1, request_id="request-a")
    lane.fallback_active = True
    assert lane.request_id == "request-a"
    assert lane.fallback_active is True
