from __future__ import annotations

import re
from .models import ChatProfile
from .registry import load_profiles, save_profiles
from .browser import browser_manager


def valid_chat_url(url: str) -> bool:
    return bool(re.match(r"^https://chatgpt\.com/(c|g|project|projects|$)", url.strip()))


def main() -> None:
    print("Opening a dedicated persistent profile in your installed Google Chrome (preferred).")
    print("If Chrome is unavailable, the bridge tries Microsoft Edge, then your system default browser.")
    print("Sign in to ChatGPT manually. The bridge does not read your password or cookies.")
    browser_manager.start()
    status = browser_manager.status()
    if not status.get("ready"):
        raise RuntimeError("browser_start_failed:" + str(status.get("error") or "unknown"))
    print(f"Browser: {status.get('browser')} ({status.get('executable')})")
    browser_manager.open_url("https://chatgpt.com/")
    input("After you are signed in and the desired conversation is open, press Enter here... ")
    url = input("Paste the exact ChatGPT conversation URL: ").strip()
    while not valid_chat_url(url):
        print("That does not look like a chatgpt.com conversation URL.")
        url = input("Paste the exact ChatGPT conversation URL: ").strip()
    alias = input("Chat alias [po3]: ").strip() or "po3"
    model_id = input("Local model ID exposed to EAs [chatgpt-browser-review]: ").strip() or "chatgpt-browser-review"
    desired_model = input("ChatGPT model you will select in that chat [GPT-5.6 Sol]: ").strip() or "GPT-5.6 Sol"
    effort = input("Reasoning effort to keep selected [high]: ").strip() or "high"
    profiles = [p for p in load_profiles() if p.alias != alias and p.model_id != model_id]
    profiles.append(ChatProfile(alias, model_id, url, desired_model, effort, True))
    save_profiles(profiles)
    browser_manager.stop()
    print("Saved.")
    print("The dedicated Chrome/Edge profile is retained under state/browser_profile for future sessions.")
    print(f"For PO3 set LOCAL_AI_MODEL={model_id}, LOCAL_AI_ANALYST_MODEL={model_id}, LOCAL_AI_CRITIC_MODEL={model_id}, LOCAL_AI_ADJUDICATOR_MODEL={model_id}.")


if __name__ == "__main__":
    main()
