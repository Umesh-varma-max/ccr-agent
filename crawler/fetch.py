from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from crawler.checkpoint import append_jsonl, load_state, read_jsonl, save_state
from crawler.config import (
    BROWSER_PROFILE_DIR,
    BROWSER_STATE_PATH,
    CHECKPOINT_DIR,
    CHECKPOINT_EVERY,
    CCR_BROWSER_CHANNEL,
    CCR_CDP_URL,
    CCR_PROXY,
    FETCH_MANIFEST_PATH,
    FAILURES_PATH,
    HTML_DIR,
    MAX_CONCURRENT_REQUESTS,
    RANDOM_DELAY_RANGE_SECONDS,
    REQUEST_TIMEOUT_MS,
    RETRY_DELAYS_SECONDS,
    USER_AGENT,
)


STATE_PATH = CHECKPOINT_DIR / "fetch_state.json"
LOG_PATH = CHECKPOINT_DIR / "fetch.log"
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


def html_path_for_url(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return HTML_DIR / f"{digest}.html"


async def run_crawl4ai(crawler, url: str) -> str:
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
        message = result.error_message or "crawl failed"
        if "429" in message:
            raise RuntimeError("HTTP 429 rate limited")
        raise RuntimeError(message)
    return result.html or ""


def is_antibot_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in ("anti-bot", "cloudflare", "security verification", "js challenge"))


def looks_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in BLOCKED_MARKERS)


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


async def fetch_with_playwright(
    input_path: Path,
    output_path: Path,
    max_urls: int | None = None,
    start_offset: int = 0,
    storage_state: str = str(BROWSER_STATE_PATH),
    headless: bool = False,
    cdp_url: str | None = None,
) -> None:
    from playwright.async_api import async_playwright

    rows = read_jsonl(input_path)
    urls = [row["url"] for row in rows if row.get("url")]
    if start_offset:
        urls = urls[start_offset:]
    if max_urls is not None:
        urls = urls[:max_urls]

    manifest_path = output_path / "pages_raw.jsonl" if output_path.is_dir() or output_path.suffix == "" else output_path
    fetched = {row["url"] for row in read_jsonl(manifest_path) if row.get("url")}
    state = load_state(STATE_PATH, {"completed": []})
    completed = set(state.get("completed") or []) | fetched
    pending = [url for url in urls if url not in completed]
    storage_state_path = Path(storage_state)

    success_count = 0
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
        for url in pending:
            await asyncio.sleep(random.uniform(*RANDOM_DELAY_RANGE_SECONDS))
            try:
                html = await get_playwright_html(page, url)
                HTML_DIR.mkdir(parents=True, exist_ok=True)
                html_path = html_path_for_url(url)
                html_path.write_text(html, encoding="utf-8")
                append_jsonl(
                    manifest_path,
                    {
                        "url": url,
                        "html_path": str(html_path),
                        "retrieved_at": utc_now(),
                        "byte_count": len(html.encode("utf-8")),
                    },
                )
                success_count += 1
                completed.add(url)
                if success_count % CHECKPOINT_EVERY == 0:
                    save_state(STATE_PATH, {"completed": sorted(completed), "updated_at": utc_now()})
                    logging.info("fetched=%s remaining_in_batch=%s", len(completed), len(pending) - success_count)
            except Exception as exc:
                append_jsonl(FAILURES_PATH, {"url": url, "stage": "fetch", "reason": str(exc), "failed_at": utc_now()})
        if not cdp_url:
            await context.storage_state(path=str(storage_state_path))
            await context.close()
        await browser.close()

    save_state(STATE_PATH, {"completed": sorted(completed), "updated_at": utc_now()})
    logging.info("Playwright fetch run complete: %s newly fetched, %s total completed", success_count, len(completed))


async def fetch_one(
    crawler,
    semaphore: asyncio.Semaphore,
    url: str,
    manifest_path: Path,
    failures_path: Path,
    wait_on_block: bool = False,
) -> bool:
    async with semaphore:
        await asyncio.sleep(random.uniform(*RANDOM_DELAY_RANGE_SECONDS))
        last_error = None
        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            try:
                html = await run_crawl4ai(crawler, url)
                HTML_DIR.mkdir(parents=True, exist_ok=True)
                html_path = html_path_for_url(url)
                html_path.write_text(html, encoding="utf-8")
                append_jsonl(
                    manifest_path,
                    {
                        "url": url,
                        "html_path": str(html_path),
                        "retrieved_at": utc_now(),
                        "byte_count": len(html.encode("utf-8")),
                    },
                )
                return True
            except Exception as exc:
                last_error = str(exc)
                if wait_on_block and is_antibot_error(last_error):
                    print(
                        "\nSecurity verification is open in Chrome. Complete it there, "
                        "wait for the real CCR page, then press Enter here to retry."
                    )
                    await asyncio.to_thread(input)
                    continue
                if "429" in last_error:
                    await asyncio.sleep(60)
                elif attempt < len(RETRY_DELAYS_SECONDS):
                    await asyncio.sleep(delay)

        append_jsonl(failures_path, {"url": url, "stage": "fetch", "reason": last_error, "failed_at": utc_now()})
        return False


async def fetch(
    input_path: Path,
    output_path: Path,
    max_urls: int | None = None,
    start_offset: int = 0,
    headless: bool = False,
    use_profile: bool = False,
    cdp_url: str | None = CCR_CDP_URL,
) -> None:
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    rows = read_jsonl(input_path)
    urls = [row["url"] for row in rows if row.get("url")]
    if start_offset:
        urls = urls[start_offset:]
    if max_urls is not None:
        urls = urls[:max_urls]

    manifest_path = output_path / "pages_raw.jsonl" if output_path.is_dir() or output_path.suffix == "" else output_path
    failures_path = FAILURES_PATH
    fetched = {row["url"] for row in read_jsonl(manifest_path) if row.get("url")}
    state = load_state(STATE_PATH, {"completed": []})
    completed = set(state.get("completed") or []) | fetched

    pending = [url for url in urls if url not in completed]
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
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

    success_count = 0
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for idx in range(0, len(pending), MAX_CONCURRENT_REQUESTS):
            batch = pending[idx : idx + MAX_CONCURRENT_REQUESTS]
            results = await asyncio.gather(
                *(fetch_one(crawler, semaphore, url, manifest_path, failures_path, wait_on_block=bool(cdp_url)) for url in batch)
            )
            for url, ok in zip(batch, results):
                if ok:
                    success_count += 1
                    completed.add(url)
            if success_count and success_count % CHECKPOINT_EVERY == 0:
                save_state(STATE_PATH, {"completed": sorted(completed), "updated_at": utc_now()})
                logging.info("fetched=%s remaining=%s", len(completed), len(urls) - len(completed))

    save_state(STATE_PATH, {"completed": sorted(completed), "updated_at": utc_now()})
    logging.info("Fetch run complete: %s newly fetched, %s total completed", success_count, len(completed))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch discovered CCR section pages using Crawl4AI.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=str(FETCH_MANIFEST_PATH.parent))
    parser.add_argument("--max-urls", type=int, default=None)
    parser.add_argument("--start-offset", type=int, default=0, help="Skip this many discovered URLs before fetching.")
    parser.add_argument("--headless", action="store_true", help="Run Chromium without a visible window after the flow is stable.")
    parser.add_argument("--use-profile", action="store_true", help="Reuse the Playwright profile captured with crawler/auth_session.py.")
    parser.add_argument("--cdp-url", default=CCR_CDP_URL, help="Connect to a running Chrome DevTools endpoint, for example http://localhost:9222.")
    parser.add_argument("--playwright", action="store_true", help="Use direct Playwright navigation and storage_state instead of Crawl4AI for fetching.")
    parser.add_argument("--storage-state", default=str(BROWSER_STATE_PATH), help="Path to the verified Playwright storage state JSON.")
    args = parser.parse_args()
    setup_logging()
    if args.playwright:
        asyncio.run(
            fetch_with_playwright(
                Path(args.input),
                Path(args.output),
                args.max_urls,
                args.start_offset,
                args.storage_state,
                args.headless,
                args.cdp_url,
            )
        )
    else:
        asyncio.run(
            fetch(
                Path(args.input),
                Path(args.output),
                args.max_urls,
                args.start_offset,
                args.headless,
                args.use_profile,
                args.cdp_url,
            )
        )


if __name__ == "__main__":
    main()
