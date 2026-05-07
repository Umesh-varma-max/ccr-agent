from __future__ import annotations

import argparse
import asyncio

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


BLOCKED_MARKERS = (
    "performing security verification",
    "verify you are human",
    "just a moment",
    "cloudflare",
    "challenge-platform",
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a running Chrome CDP session for CCR crawler debugging.")
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--url", default="https://govt.westlaw.com/calregs/Index")
    args = parser.parse_args()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(args.cdp_url)
        print(f"contexts={len(browser.contexts)}")
        for context_index, context in enumerate(browser.contexts):
            print(f"context[{context_index}] pages={len(context.pages)}")
            for page_index, page in enumerate(context.pages):
                title = await page.title()
                print(f"  page[{page_index}] title={title!r} url={page.url}")

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = next((p for p in context.pages if "govt.westlaw.com/calregs" in p.url), None)
        if page is None:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(args.url, wait_until="domcontentloaded", timeout=120_000)

        await page.wait_for_timeout(5_000)
        html = await page.content()
        lowered = html.lower()
        blocked = any(marker in lowered for marker in BLOCKED_MARKERS)
        soup = BeautifulSoup(html, "lxml")
        links = [a.get("href") for a in soup.find_all("a", href=True) if "/calregs/" in a.get("href", "")]
        document_links = [href for href in links if "/Document/" in href]
        browse_links = [href for href in links if "/Browse" in href]

        print("--- active CCR page ---")
        print(f"title={await page.title()!r}")
        print(f"url={page.url}")
        print(f"blocked={blocked}")
        print(f"html_chars={len(html)}")
        print(f"calregs_links={len(links)}")
        print(f"document_links={len(document_links)}")
        print(f"browse_links={len(browse_links)}")
        for href in links[:20]:
            print(f"link={href}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
