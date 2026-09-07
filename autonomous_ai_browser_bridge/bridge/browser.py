from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings
from .audit import log_event


class BrowserState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "ready": False,
            "running": False,
            "error": "",
            "browser": "",
            "executable": "",
            "profile_dir": str(settings.browser_profile_dir),
            "automation": settings.browser_automation_enabled,
            "headless": settings.browser_headless,
            "configured": settings.browser_configured,
            "current_url": "",
            "active_request_id": "",
            "active_request_ids": {},
            "busy_chat_tabs": 0,
            "chat_tab_count": settings.browser_chat_tab_count,
            "open_chat_tabs": 0,
            "active_chat_tab": 0,
            "requests_on_active_tab": 0,
            "requests_per_tab": settings.browser_requests_per_tab,
            "total_tab_submissions": 0,
            "gemini_fallback_enabled": settings.gemini_fallback_enabled,
            "gemini_fallback_ready": False,
            "gemini_open_tabs": 0,
            "gemini_profile_dir": str(settings.gemini_profile_dir),
            "last_event": "not_started",
        }
        self.commands: queue.Queue[tuple[str, str]] = queue.Queue()

    def update(self, **fields: Any) -> None:
        with self._lock:
            self._data.update(fields)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def request_open(self, url: str) -> None:
        self.commands.put(("open", url))


browser_state = BrowserState()


def candidate_paths() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if settings.browser_executable:
        candidates.append(("configured", Path(settings.browser_executable).expanduser()))
    if os.name == "nt":
        local = os.getenv("LOCALAPPDATA", "")
        pf = os.getenv("PROGRAMFILES", "")
        pfx86 = os.getenv("PROGRAMFILES(X86)", "")
        for name, base, rel in (
            ("Google Chrome", local, r"Google\Chrome\Application\chrome.exe"),
            ("Google Chrome", pf, r"Google\Chrome\Application\chrome.exe"),
            ("Google Chrome", pfx86, r"Google\Chrome\Application\chrome.exe"),
            ("Microsoft Edge", pf, r"Microsoft\Edge\Application\msedge.exe"),
            ("Microsoft Edge", pfx86, r"Microsoft\Edge\Application\msedge.exe"),
            ("Microsoft Edge", local, r"Microsoft\Edge\Application\msedge.exe"),
        ):
            if base:
                candidates.append((name, Path(base) / rel))
    elif sys.platform == "darwin":
        candidates.extend([
            ("Google Chrome", Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")),
            ("Microsoft Edge", Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")),
        ])
    else:
        for cmd, name in (
            ("google-chrome", "Google Chrome"),
            ("google-chrome-stable", "Google Chrome"),
            ("microsoft-edge", "Microsoft Edge"),
            ("microsoft-edge-stable", "Microsoft Edge"),
            ("chromium", "Chromium"),
        ):
            found = shutil.which(cmd)
            if found:
                candidates.append((name, Path(found)))
    return candidates


def discover_browser() -> tuple[str, Path]:
    for name, path in candidate_paths():
        try:
            if path.is_file():
                return name, path.resolve()
        except OSError:
            continue
    raise RuntimeError("no_supported_installed_browser_found:set_BROWSER_EXECUTABLE")


@contextmanager
def persistent_context(*, headless: bool, start_url: str = "about:blank") -> Iterator[tuple[Any, Any, str, Path]]:
    """Launch a normal installed Chrome/Edge under Playwright control.

    This is provider-agnostic automation for UIs the operator is permitted to
    automate. No stealth, CAPTCHA bypass, anti-bot evasion, or credential
    extraction is implemented.
    """
    from playwright.sync_api import sync_playwright

    name, executable = discover_browser()
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir.resolve()),
            executable_path=str(executable),
            headless=headless,
            accept_downloads=True,
            viewport=None if not headless else {"width": 1440, "height": 1000},
            args=["--no-first-run"],
        )
        context.set_default_timeout(settings.selector_timeout_ms)
        context.set_default_navigation_timeout(settings.navigation_timeout_ms)
        page = context.pages[0] if context.pages else context.new_page()
        if start_url and start_url != "about:blank":
            page.goto(start_url, wait_until="domcontentloaded")
        try:
            yield context, page, name, executable
        finally:
            context.close()


def status() -> dict[str, Any]:
    return browser_state.snapshot()


def open_url(url: str) -> None:
    browser_state.request_open(url)
    log_event("browser_navigation_requested", url=url)
