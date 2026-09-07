from __future__ import annotations

import subprocess

from .browser import discover_browser
from .config import settings


def main() -> None:
    """Persist a Google/Gemini login without browser automation signals."""

    name, executable = discover_browser()
    profile = settings.gemini_profile_dir.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    start_url = settings.gemini_start_url or "https://gemini.google.com/app"

    print(f"Browser: {name} ({executable})")
    print(f"Gemini profile: {profile}")
    print("Sign in to Gemini, confirm the prompt box is visible, then CLOSE Chrome completely.")
    args = [
        str(executable),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]
    return_code = subprocess.call(args)
    if return_code:
        raise SystemExit(return_code)
    print("Gemini browser closed. The authenticated session is persisted.")


if __name__ == "__main__":
    main()
