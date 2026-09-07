from __future__ import annotations

import subprocess

from .browser import discover_browser
from .config import settings


def main() -> None:
    """Sign in using a plain, non-automated browser run.

    Playwright drives Chrome through the DevTools Protocol and passes
    --enable-automation, which some identity providers refuse to accept
    credentials under. This launches the same installed browser against the
    same profile directory with no Playwright, no CDP and no automation
    switches, so the login is an ordinary browser session. The persisted
    session is then reused by register_chat.bat and the runtime worker.
    """
    from .register import valid_url

    name, executable = discover_browser()
    profile = settings.browser_profile_dir.resolve()
    profile.mkdir(parents=True, exist_ok=True)

    start = settings.browser_start_url
    if not start:
        start = input("Provider start/login URL: ").strip()
        while not valid_url(start):
            start = input("Enter a full http:// or https:// URL: ").strip()

    print(f"Browser: {name} ({executable})")
    print(f"Profile: {profile}")
    print("Plain browser run: no Playwright, no CDP, no automation switches.")
    print("Sign in, then CLOSE the browser completely to continue.")

    args = [
        str(executable),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        start,
    ]
    subprocess.call(args)

    print("Browser closed. Session persisted under state/browser_profile.")
    print("Next: register_chat.bat (it will reuse this session).")
