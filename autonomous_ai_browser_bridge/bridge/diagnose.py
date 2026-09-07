from __future__ import annotations

import json
from .browser import persistent_context
from .config import settings
from .registry import profile_by_alias


def main() -> None:
    profile = profile_by_alias(settings.default_chat_alias)
    if not profile:
        raise SystemExit("No default chat profile. Run register_chat.bat first.")
    checks = {
        "BROWSER_CHAT_INPUT_SELECTOR": settings.chat_input_selector,
        "BROWSER_ASSISTANT_MESSAGE_SELECTOR": settings.assistant_message_selector,
        "BROWSER_SEND_SELECTOR": settings.send_selector,
        "BROWSER_STOP_GENERATING_SELECTOR": settings.stop_generating_selector,
        "BROWSER_SCROLL_CONTAINER_SELECTOR": settings.scroll_container_selector,
        "BROWSER_FILE_INPUT_SELECTOR": settings.file_input_selector,
        "BROWSER_DOWNLOAD_LINK_SELECTOR": settings.download_link_selector,
        "BROWSER_LOGIN_READY_SELECTOR": settings.login_ready_selector,
    }
    with persistent_context(headless=False, start_url=profile.conversation_url) as (_ctx, page, name, executable):
        # Navigation only waits for domcontentloaded. Single-page UIs render
        # the composer and the transcript after that, so counting straight
        # away reports zero for selectors that are merely late.
        try:
            page.wait_for_load_state("networkidle", timeout=settings.navigation_timeout_ms)
        except Exception:
            pass
        for settle in (settings.chat_input_selector, settings.assistant_message_selector):
            if not settle:
                continue
            try:
                page.locator(settle).last.wait_for(state="visible", timeout=settings.selector_timeout_ms)
            except Exception:
                pass
        report = {"browser": name, "executable": str(executable), "url": page.url, "selectors": {}}
        for key, selector in checks.items():
            if not selector:
                report["selectors"][key] = {"configured": False, "count": None}
                continue
            try:
                report["selectors"][key] = {"configured": True, "count": page.locator(selector).count()}
            except Exception as exc:
                report["selectors"][key] = {"configured": True, "error": f"{type(exc).__name__}:{exc}"}
        print(json.dumps(report, indent=2))
        input("Press Enter to close diagnostics browser...")


if __name__ == "__main__":
    main()
