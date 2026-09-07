from __future__ import annotations

import json
import sys

from .browser import discover_browser
from .config import settings


def main() -> None:
    """Inspect a named saved Gemini conversation without sending a message."""

    from playwright.sync_api import sync_playwright

    title = " ".join(sys.argv[1:]).strip() or "File Response Schema Adherence Test"
    name, executable = discover_browser()
    profile = settings.gemini_profile_dir.resolve()
    result: dict[str, object] = {
        "browser": name,
        "profile_dir": str(profile),
        "requested_title": title,
        "opened": False,
    }
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=str(executable),
            headless=False,
            viewport=None,
            args=["--no-first-run"],
        )
        context.set_default_timeout(15_000)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            settings.gemini_start_url,
            wait_until="domcontentloaded",
            timeout=settings.navigation_timeout_ms,
        )
        try:
            page.locator(settings.gemini_login_ready_selector).last.wait_for(
                state="visible", timeout=15_000
            )
        except Exception:
            pass
        recent = page.get_by_text(title, exact=False)
        if recent.count():
            recent.first.click()
            page.wait_for_timeout(2_000)
            result["opened"] = True
        result["current_url"] = page.url

        messages = page.locator(settings.gemini_assistant_message_selector)
        result["assistant_count"] = messages.count()
        if messages.count():
            result["latest_assistant_text"] = messages.last.inner_text()[:4_000]

        attachment_like = page.locator(
            '[class*="attachment"],[class*="download"],[aria-label*="Download"],'
            '[data-tooltip*="Download"],a[download]'
        )
        controls: list[dict[str, str]] = []
        for index in range(min(80, attachment_like.count())):
            item = attachment_like.nth(index)
            controls.append({
                "tag": item.evaluate("el => el.tagName.toLowerCase()"),
                "text": (item.inner_text() or "")[:200],
                "aria_label": item.get_attribute("aria-label") or "",
                "title": item.get_attribute("title") or "",
                "href": item.get_attribute("href") or "",
                "class": (item.get_attribute("class") or "")[:300],
            })
        result["attachment_like_controls"] = controls
        print(json.dumps(result, indent=2, ensure_ascii=True))
        context.close()


if __name__ == "__main__":
    main()
