from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from crawler.config import BROWSER_PROFILE_DIR, BROWSER_STATE_PATH, CCR_BROWSER_CHANNEL, START_URL, USER_AGENT


BLOCKED_MARKERS = (
    "performing security verification",
    "verify you are human",
    "just a moment",
    "cloudflare",
    "challenge-platform",
)


def page_looks_blocked(page) -> bool:
    try:
        content = page.content().lower()
    except Exception:
        return True
    return any(marker in content for marker in BLOCKED_MARKERS)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open a visible Playwright browser and save a reusable Westlaw session profile."
    )
    parser.add_argument("--url", default=START_URL)
    parser.add_argument("--user-data-dir", default=str(BROWSER_PROFILE_DIR))
    parser.add_argument("--storage-state", default=str(BROWSER_STATE_PATH))
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    profile_dir = Path(args.user_data_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"Opening browser profile: {profile_dir}")
    print("Complete any Cloudflare/human verification in the opened browser.")
    print("When the real CCR page is visible, press Enter in this terminal to save and close.")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1366, "height": 900},
            channel=CCR_BROWSER_CHANNEL,
            user_agent=USER_AGENT,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=120_000)
        while page_looks_blocked(page):
            print("Security verification is still visible. Complete it in the browser, then press Enter here to check again.")
            input()
            page.wait_for_load_state("networkidle", timeout=30_000)
            page.wait_for_timeout(8_000)
        context.storage_state(path=args.storage_state)
        print(f"Saved browser storage state: {args.storage_state}")
        context.close()

    print("Saved browser session. Now rerun discovery with --playwright --storage-state.")


if __name__ == "__main__":
    main()
