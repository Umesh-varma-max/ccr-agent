from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from crawler.checkpoint import append_jsonl, load_state, read_seen_urls, save_state
from crawler.config import (
    BASE_URL,
    BROWSER_PROFILE_DIR,
    BROWSER_STATE_PATH,
    CHECKPOINT_DIR,
    CCR_BROWSER_CHANNEL,
    CCR_CDP_URL,
    CCR_PROXY,
    DISCOVERED_URLS_PATH,
    MAX_CONCURRENT_REQUESTS,
    REQUEST_TIMEOUT_MS,
    START_URL,
    USER_AGENT,
)


STATE_PATH = CHECKPOINT_DIR / "discover_state.json"
LOG_PATH = CHECKPOINT_DIR / "discover.log"
BLOCKED_MARKERS = (
    "performing security verification",
    "verify you are human",
    "just a moment",
    "cloudflare",
    "challenge-platform",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def setup_logging() -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def normalize_url(url: str) -> str:
    parsed = urlparse(urljoin(BASE_URL, url))
    path = re.sub(r"/+$", "", parsed.path)
    keep_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {"guid", "viewtype"}:
            keep_params.append((key, value))
    query = urlencode(keep_params)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", query, ""))


def is_ccr_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("westlaw.com") and "/calregs/" in parsed.path


def is_browse_url(url: str) -> bool:
    return "/calregs/Browse" in url or url.rstrip("/") == normalize_url(START_URL)


def is_document_url(url: str) -> bool:
    return "/calregs/Document/" in url or "/calregs/Link/Document/" in url


def is_section_url(url: str, anchor_text: str = "") -> bool:
    text = anchor_text.lower()
    return is_document_url(url) and "refs & annos" not in text and "references" not in text


def infer_title_number(url: str, text: str = "") -> int | None:
    haystack = f"{url} {text}"
    match = re.search(r"(?:Title|title|titlenum=|TitleNum=|titleNumber=)\D{0,5}(\d{1,2})", haystack)
    return int(match.group(1)) if match else None


def extract_links(html: str, current_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    links: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        anchor_text = " ".join(anchor.get_text(" ", strip=True).split())
        if anchor_text.lower() in {"skip to navigation", "skip to main content", "home", "help"}:
            continue
        url = normalize_url(urljoin(current_url, anchor["href"]))
        if is_ccr_url(url):
            links.append((url, anchor_text))
    return links


def looks_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in BLOCKED_MARKERS) or "attention required" in lowered


async def crawl_html(crawler, url: str) -> str:
    from crawl4ai import CacheMode, CrawlerRunConfig

    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=REQUEST_TIMEOUT_MS,
        wait_until="domcontentloaded",
        delay_before_return_html=2.0,
        simulate_user=True,
        override_navigator=True,
        magic=True,
    )
    result = await crawler.arun(url=url, config=config)
    if not result.success:
        raise RuntimeError(result.error_message or "crawl failed")
    return result.html or ""


def is_antibot_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in ("anti-bot", "cloudflare", "security verification", "js challenge"))


async def get_playwright_html(page, url: str) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass
    await page.wait_for_timeout(2_000)
    html = await page.content()
    while looks_blocked(html):
        title = await page.title()
        print(
            "\nSecurity verification is still visible in the Playwright browser. "
            f"Current tab: {title!r} {page.url}. "
            "Complete it there, wait for the real CCR page, then press Enter here."
        )
        await asyncio.to_thread(input)
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_000)
        html = await page.content()
    return html


async def discover_with_playwright(
    output: str,
    max_pages: int | None = None,
    target_sections: int | None = None,
    reset: bool = False,
    start_url: str = START_URL,
    storage_state: str = str(BROWSER_STATE_PATH),
    headless: bool = False,
    cdp_url: str | None = None,
) -> None:
    from playwright.async_api import async_playwright

    output_path = DISCOVERED_URLS_PATH if output is None else DISCOVERED_URLS_PATH.parent / output if "/" not in output and "\\" not in output else None
    if output_path is None:
        output_path = Path(output)
    if reset and output_path.exists():
        output_path.unlink()

    if reset:
        state = {"queue": [start_url], "visited": []}
    else:
        state = load_state(STATE_PATH, {"queue": [start_url], "visited": []})
    queue: deque[str] = deque(state.get("queue") or [start_url])
    visited: set[str] = set(state.get("visited") or [])
    discovered: set[str] = read_seen_urls(output_path)
    if not discovered and not queue:
        queue = deque([start_url])
        visited = set()
    title_counts: Counter[int] = Counter()

    storage_state_path = Path(storage_state)
    async with async_playwright() as playwright:
        if cdp_url:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await playwright.chromium.launch(channel=CCR_BROWSER_CHANNEL, headless=headless)
            context_kwargs = {
                "viewport": {"width": 1366, "height": 900},
                "user_agent": USER_AGENT,
            }
            if storage_state_path.exists():
                context_kwargs["storage_state"] = str(storage_state_path)
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
        pages_processed = 0
        while queue:
            if max_pages is not None and pages_processed >= max_pages:
                break
            if target_sections is not None and len(discovered) >= target_sections:
                break
            url = normalize_url(queue.popleft())
            if url in visited:
                continue
            try:
                html = await get_playwright_html(page, url)
            except Exception as exc:
                logging.warning("failed discovery page %s: %s", url, exc)
                continue

            visited.add(url)
            pages_processed += 1
            links = extract_links(html, url)
            logging.info("extracted %s CCR links from %s", len(links), url)
            if not links:
                logging.warning("no CCR links found on %s; current page may still be blocked or not the CCR table of contents", url)
            for link, anchor_text in links:
                if is_section_url(link, anchor_text) and link not in discovered:
                    title_number = infer_title_number(link, anchor_text)
                    if title_number:
                        title_counts[title_number] += 1
                    discovered.add(link)
                    append_jsonl(
                        output_path,
                        {
                            "url": link,
                            "title_number": title_number,
                            "anchor_text": anchor_text,
                            "discovered_from": url,
                            "discovered_at": utc_now(),
                        },
                    )
                elif (is_browse_url(link) or is_document_url(link)) and link not in visited and link not in discovered:
                    queue.append(link)
            if pages_processed % 10 == 0:
                save_state(STATE_PATH, {"queue": list(queue), "visited": sorted(visited), "updated_at": utc_now()})
                logging.info("checkpoint visited=%s queued=%s discovered=%s", len(visited), len(queue), len(discovered))
        if not cdp_url:
            await context.storage_state(path=str(storage_state_path))
            await context.close()
        await browser.close()

    save_state(
        STATE_PATH,
        {
            "queue": list(queue),
            "visited": sorted(visited),
            "discovered_count": len(discovered),
            "title_counts": dict(title_counts),
            "updated_at": utc_now(),
        },
    )
    logging.info("Playwright discovery complete enough for this run: %s URLs", len(discovered))


async def discover(
    output: str,
    max_pages: int | None = None,
    target_sections: int | None = None,
    reset: bool = False,
    start_url: str = START_URL,
    headless: bool = False,
    use_profile: bool = False,
    cdp_url: str | None = CCR_CDP_URL,
) -> None:
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    output_path = DISCOVERED_URLS_PATH if output is None else DISCOVERED_URLS_PATH.parent / output if "/" not in output and "\\" not in output else None
    if output_path is None:
        from pathlib import Path

        output_path = Path(output)
    if reset and output_path.exists():
        output_path.unlink()

    if reset:
        state = {"queue": [start_url], "visited": []}
    else:
        state = load_state(STATE_PATH, {"queue": [start_url], "visited": []})
    queue: deque[str] = deque(state.get("queue") or [start_url])
    visited: set[str] = set(state.get("visited") or [])
    discovered: set[str] = read_seen_urls(output_path)
    if not discovered and not queue:
        queue = deque([start_url])
        visited = set()
    title_counts: Counter[int] = Counter()

    browser_kwargs = {
        "headless": headless,
        "user_agent": USER_AGENT,
        "enable_stealth": True,
        "viewport_width": 1366,
        "viewport_height": 900,
        "channel": CCR_BROWSER_CHANNEL,
        "chrome_channel": CCR_BROWSER_CHANNEL,
        "proxy_config": CCR_PROXY,
    }
    if use_profile:
        browser_kwargs.update(
            {
                "use_persistent_context": True,
                "user_data_dir": str(BROWSER_PROFILE_DIR),
            }
        )
    if cdp_url:
        browser_kwargs.update(
            {
                "browser_mode": "custom",
                "cdp_url": cdp_url,
                "use_persistent_context": False,
            }
        )
    browser_config = BrowserConfig(**browser_kwargs)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        pages_processed = 0
        while queue:
            if max_pages is not None and pages_processed >= max_pages:
                break
            if target_sections is not None and len(discovered) >= target_sections:
                break
            url = queue.popleft()
            url = normalize_url(url)
            if url in visited:
                continue
            try:
                html = await crawl_html(crawler, url)
            except Exception as exc:
                if cdp_url and is_antibot_error(str(exc)):
                    print(
                        "\nSecurity verification is open in Chrome. Complete it there, "
                        "wait for the real CCR page, then press Enter here to retry."
                    )
                    await asyncio.to_thread(input)
                    try:
                        html = await crawl_html(crawler, url)
                    except Exception as retry_exc:
                        logging.warning("failed discovery page %s after verification retry: %s", url, retry_exc)
                        continue
                else:
                    logging.warning("failed discovery page %s: %s", url, exc)
                    continue
            if looks_blocked(html):
                if cdp_url:
                    print(
                        "\nSecurity verification is still visible in Chrome. Complete it there, "
                        "wait for the real CCR page, then press Enter here to retry."
                    )
                    await asyncio.to_thread(input)
                    html = await crawl_html(crawler, url)
                    if looks_blocked(html):
                        logging.error("still blocked by anti-bot page at %s after verification retry", url)
                        continue
                logging.error("blocked by anti-bot page at %s; try another network/VPN or run with a manually captured seed list", url)
                continue

            visited.add(url)
            pages_processed += 1
            links = extract_links(html, url)
            if not links:
                logging.warning("no CCR links found on %s; page title/html may not be the expected table of contents", url)
            for link, anchor_text in links:
                if is_section_url(link, anchor_text) and link not in discovered:
                    title_number = infer_title_number(link, anchor_text)
                    if title_number:
                        title_counts[title_number] += 1
                    discovered.add(link)
                    append_jsonl(
                        output_path,
                        {
                            "url": link,
                            "title_number": title_number,
                            "anchor_text": anchor_text,
                            "discovered_from": url,
                            "discovered_at": utc_now(),
                        },
                    )
                elif (is_browse_url(link) or is_document_url(link)) and link not in visited and link not in discovered:
                    queue.append(link)

            if pages_processed % 25 == 0:
                save_state(STATE_PATH, {"queue": list(queue), "visited": sorted(visited), "updated_at": utc_now()})
                logging.info("visited=%s queued=%s discovered=%s", len(visited), len(queue), len(discovered))

    save_state(
        STATE_PATH,
        {
            "queue": list(queue),
            "visited": sorted(visited),
            "discovered_count": len(discovered),
            "title_counts": dict(title_counts),
            "updated_at": utc_now(),
        },
    )
    logging.info("Discovery complete enough for this run: %s URLs", len(discovered))


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover CCR section URLs from Westlaw navigation pages.")
    parser.add_argument("--output", default=str(DISCOVERED_URLS_PATH))
    parser.add_argument("--max-pages", type=int, default=None, help="Optional cap for smoke tests or staged crawling.")
    parser.add_argument("--target-sections", type=int, default=None, help="Stop after at least this many section URLs have been discovered.")
    parser.add_argument("--start-url", default=START_URL)
    parser.add_argument("--reset", action="store_true", help="Ignore the saved discovery checkpoint and start fresh.")
    parser.add_argument("--headless", action="store_true", help="Run Chromium without a visible window after the flow is stable.")
    parser.add_argument("--use-profile", action="store_true", help="Reuse the Playwright profile captured with crawler/auth_session.py.")
    parser.add_argument("--cdp-url", default=CCR_CDP_URL, help="Connect to a running Chrome DevTools endpoint, for example http://localhost:9222.")
    parser.add_argument("--playwright", action="store_true", help="Use direct Playwright navigation and storage_state instead of Crawl4AI for discovery.")
    parser.add_argument("--storage-state", default=str(BROWSER_STATE_PATH), help="Path to the verified Playwright storage state JSON.")
    args = parser.parse_args()
    setup_logging()
    if args.playwright:
        asyncio.run(
            discover_with_playwright(
                args.output,
                args.max_pages,
                args.target_sections,
                args.reset,
                args.start_url,
                args.storage_state,
                args.headless,
                args.cdp_url,
            )
        )
    else:
        asyncio.run(
            discover(
                args.output,
                args.max_pages,
                args.target_sections,
                args.reset,
                args.start_url,
                args.headless,
                args.use_profile,
                args.cdp_url,
            )
        )


if __name__ == "__main__":
    main()
