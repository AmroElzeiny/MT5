from __future__ import annotations

import re
from .models import ChatProfile
from .registry import load_profiles, save_profiles
from .browser import persistent_context
from .config import settings


def valid_url(url: str) -> bool:
    return bool(re.match(r"^https?://", url.strip(), re.IGNORECASE))


def main() -> None:
    print("Conversation registration")
    print("This opens the installed Chrome/Edge HEADFUL even when runtime BROWSER_HEADLESS=true.")
    print("Sign in first with manual_login.bat: this browser runs under automation")
    print("control, which some identity providers refuse credentials under.")
    print("Use only a provider/UI you are authorized to automate.")
    start = settings.browser_start_url or input("Provider start/login URL: ").strip()
    while not valid_url(start):
        start = input("Enter a full http:// or https:// URL: ").strip()
    with persistent_context(headless=False, start_url=start) as (_context, page, name, executable):
        print(f"Browser: {name} ({executable})")
        input("Open the exact conversation/workspace to use, then press Enter here... ")
        detected = page.url if page.url and page.url != "about:blank" else ""
        url = input(f"Conversation URL [{detected}]: ").strip() or detected
        while not valid_url(url):
            url = input("Enter the exact conversation URL: ").strip()
        alias = input("Chat alias [po3]: ").strip() or "po3"
        model_id = input("Local model ID exposed to EAs [chatgpt-browser-review]: ").strip() or "chatgpt-browser-review"
        desired_model = input("Provider model label (optional): ").strip()
        effort = input("Reasoning/effort label (optional): ").strip()
        profiles = [p for p in load_profiles() if p.alias != alias and p.model_id != model_id]
        profiles.append(ChatProfile(alias, model_id, url, desired_model, effort, True))
        save_profiles(profiles)
    print("Saved. Session data remains under state/browser_profile.")
    print("Now fill the provider's selectors in .env, then run diagnose_selectors.bat.")
