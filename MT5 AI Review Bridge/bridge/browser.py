from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from threading import Event, Lock

from .config import settings
from .registry import profile_by_alias
from .audit import log_event


class BrowserManager:
    """Open registered chats in a normal installed browser.

    The previous build launched Playwright's bundled Chromium. Some Google
    authentication flows reject that browser environment. This manager instead
    launches the user's installed Google Chrome (preferred) or Microsoft Edge
    as a normal browser process, with the bridge's existing dedicated
    BROWSER_PROFILE_DIR passed through --user-data-dir so the login session is
    persisted between bridge runs.

    No remote-debugging, webdriver, automation, or Playwright flags are used.
    """

    def __init__(self) -> None:
        self._ready = Event()
        self._lock = Lock()
        self._error = ""
        self._executable: Path | None = None
        self._browser_name = ""
        self._fallback_default_browser = False

    @staticmethod
    def _candidate_paths() -> list[tuple[str, Path]]:
        override = os.getenv("BROWSER_EXECUTABLE", "").strip()
        candidates: list[tuple[str, Path]] = []
        if override:
            candidates.append(("configured", Path(override).expanduser()))

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
            ):
                found = shutil.which(cmd)
                if found:
                    candidates.append((name, Path(found)))
        return candidates

    def _discover_browser(self) -> None:
        if self._executable or self._fallback_default_browser:
            return
        for name, path in self._candidate_paths():
            try:
                if path.is_file():
                    self._executable = path.resolve()
                    self._browser_name = name
                    return
            except OSError:
                continue
        self._fallback_default_browser = True
        self._browser_name = "system default browser"

    def start(self) -> None:
        with self._lock:
            if self._ready.is_set() and not self._error:
                return
            try:
                self._discover_browser()
                # Preserve the old behavior: first start opens a headful browser
                # window. Registration/open_chat then opens the desired URL.
                self._launch("about:blank")
                self._error = ""
                self._ready.set()
                log_event(
                    "browser_started",
                    browser=self._browser_name,
                    executable=str(self._executable or "system_default"),
                    profile_dir=str(settings.browser_profile_dir),
                    automation=False,
                )
            except Exception as exc:
                self._error = f"{type(exc).__name__}:{exc}"
                self._ready.set()
                log_event("browser_error", error=self._error)

    def stop(self) -> None:
        # A normal Chrome/Edge process owns its profile lifecycle. We deliberately
        # do not kill it from Python; force-terminating Chrome risks profile
        # corruption and would differ from normal browser behavior. The user may
        # close the browser normally at any time.
        return

    def status(self) -> dict:
        return {
            "ready": bool(self._ready.is_set() and not self._error),
            "error": self._error,
            "browser": self._browser_name,
            "executable": str(self._executable or "system_default"),
            "profile_dir": str(settings.browser_profile_dir),
            "automation": False,
        }

    def _launch(self, url: str) -> None:
        self._discover_browser()
        settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        if self._executable:
            args = [
                str(self._executable),
                f"--user-data-dir={settings.browser_profile_dir.resolve()}",
                "--profile-directory=Default",
                "--no-first-run",
                "--start-maximized",
                url,
            ]
            kwargs: dict = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
            }
            if os.name == "nt":
                creationflags = 0
                creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
                kwargs["creationflags"] = creationflags
            subprocess.Popen(args, **kwargs)
            return

        # Last-resort fallback. This uses the OS default browser/profile, which
        # still avoids Playwright/Chromium and therefore allows normal login.
        if not webbrowser.open(url, new=2, autoraise=True):
            raise RuntimeError("no_supported_browser_found")

    def open_url(self, url: str) -> None:
        if not self._ready.is_set():
            self.start()
        if self._error:
            raise RuntimeError("browser_not_ready:" + self._error)
        self._launch(url)
        log_event("browser_url_opened", browser=self._browser_name, url=url)

    def open_chat(self, alias: str) -> None:
        profile = profile_by_alias(alias)
        if not profile:
            raise RuntimeError(f"chat_profile_not_found:{alias}")
        self.open_url(profile.conversation_url)


browser_manager = BrowserManager()
