from __future__ import annotations

import json

from .browser import discover_browser
from .config import settings


def main() -> None:
    """Verify the persisted Gemini session and configured selectors."""

    from playwright.sync_api import sync_playwright

    name, executable = discover_browser()
    profile = settings.gemini_profile_dir.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "browser": name,
        "profile_dir": str(profile),
        "url": settings.gemini_chat_new_url,
        "selectors": {},
        "ready": False,
    }
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=str(executable),
            headless=False,
            viewport=None,
            args=["--no-first-run"],
        )
        # Diagnostics must fail fast and print useful controls. Production keeps
        # the longer selector budget for real requests.
        context.set_default_timeout(min(settings.selector_timeout_ms, 10_000))
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            settings.gemini_chat_new_url,
            wait_until="domcontentloaded",
            timeout=settings.navigation_timeout_ms,
        )
        # Gemini is an Angular SPA. The shell reaches domcontentloaded well
        # before the authenticated composer is rendered, so an immediate count
        # produces false zeroes even for a valid persisted login.
        try:
            page.wait_for_load_state(
                "networkidle", timeout=min(settings.navigation_timeout_ms, 5_000)
            )
        except Exception:
            pass
        if settings.gemini_chat_input_selector:
            try:
                page.locator(settings.gemini_chat_input_selector).last.wait_for(
                    state="visible",
                    timeout=min(settings.selector_timeout_ms, 15_000),
                )
            except Exception:
                pass
        selectors = {
            "login_ready": settings.gemini_login_ready_selector,
            "input": settings.gemini_chat_input_selector,
            "assistant": settings.gemini_assistant_message_selector,
            "send": settings.gemini_send_selector,
            "file_input": settings.gemini_file_input_selector,
            "upload_open": settings.gemini_upload_open_selector,
            "download_scope": settings.gemini_download_scope_selector,
            "download_open": settings.gemini_download_open_selector,
            "download_link": settings.gemini_download_link_selector,
        }
        counts: dict[str, int] = {}
        for label, selector in selectors.items():
            counts[label] = page.locator(selector).count() if selector else 0
        result["selectors"] = counts
        result["current_url"] = page.url
        result["ready"] = counts["login_ready"] > 0 and counts["input"] > 0
        if result["ready"]:
            dynamic: dict[str, object] = {}
            try:
                composer = page.locator(settings.gemini_chat_input_selector).last
                composer.fill("selector probe")
                page.wait_for_timeout(500)
                dynamic["send_after_composer_text"] = (
                    page.locator(settings.gemini_send_selector).count()
                    if settings.gemini_send_selector else 0
                )
                composer.fill("")
            except Exception as exc:
                dynamic["send_probe_error"] = f"{type(exc).__name__}:{exc}"
            try:
                if settings.gemini_upload_open_selector:
                    page.locator(settings.gemini_upload_open_selector).last.click()
                    page.wait_for_timeout(750)
                dynamic["file_input_after_upload_open"] = (
                    page.locator(settings.gemini_file_input_selector).count()
                    if settings.gemini_file_input_selector else 0
                )
                menu_controls = page.locator(
                    '[role="menuitem"]:visible,[role="option"]:visible,'
                    'button:visible,input[type="file"]'
                )
                menu_candidates: list[dict[str, str]] = []
                for index in range(min(30, menu_controls.count())):
                    control = menu_controls.nth(index)
                    menu_candidates.append({
                        "tag": control.evaluate("el => el.tagName.toLowerCase()"),
                        "text": (control.inner_text() or "")[:160],
                        "aria_label": control.get_attribute("aria-label") or "",
                        "role": control.get_attribute("role") or "",
                        "type": control.get_attribute("type") or "",
                        "class": (control.get_attribute("class") or "")[:240],
                    })
                dynamic["upload_menu_candidates"] = menu_candidates
                page.keyboard.press("Escape")
            except Exception as exc:
                dynamic["upload_probe_error"] = f"{type(exc).__name__}:{exc}"
            try:
                mode_picker = page.locator(
                    'button[aria-label^="Open mode picker"]'
                ).last
                dynamic["mode_picker_count"] = mode_picker.count()
                if mode_picker.count():
                    dynamic["current_mode"] = mode_picker.inner_text().strip()
                    mode_picker.click()
                    page.wait_for_timeout(500)
                    options = page.locator(
                        '[role="menuitem"]:visible,[role="option"]:visible,'
                        '[role="menuitemradio"]:visible'
                    )
                    option_candidates: list[dict[str, str]] = []
                    for index in range(min(20, options.count())):
                        option = options.nth(index)
                        option_candidates.append({
                            "text": (option.inner_text() or "")[:240],
                            "aria_label": option.get_attribute("aria-label") or "",
                            "role": option.get_attribute("role") or "",
                            "class": (option.get_attribute("class") or "")[:240],
                        })
                    dynamic["mode_options"] = option_candidates
                    page.keyboard.press("Escape")
            except Exception as exc:
                dynamic["mode_probe_error"] = f"{type(exc).__name__}:{exc}"
            result["dynamic_probes"] = dynamic
        # If the configured selectors still miss, report stable attributes from
        # visible controls. This does not expose prompt/history text and makes a
        # future Gemini DOM change diagnosable without guessing from CSS hashes.
        if not result["ready"]:
            controls = page.locator(
                'button:visible,input:visible,textarea:visible,[contenteditable="true"]:visible'
            )
            candidates: list[dict[str, str]] = []
            for index in range(min(40, controls.count())):
                control = controls.nth(index)
                candidates.append({
                    "tag": control.evaluate("el => el.tagName.toLowerCase()"),
                    "aria_label": control.get_attribute("aria-label") or "",
                    "title": control.get_attribute("title") or "",
                    "role": control.get_attribute("role") or "",
                    "data_test_id": control.get_attribute("data-test-id") or "",
                    "class": (control.get_attribute("class") or "")[:240],
                })
            result["visible_control_candidates"] = candidates
        # PowerShell may expose a legacy console code page; escaped JSON keeps
        # localized Gemini labels diagnostic without crashing the probe.
        print(json.dumps(result, indent=2, ensure_ascii=True))
        context.close()
    if not result["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
