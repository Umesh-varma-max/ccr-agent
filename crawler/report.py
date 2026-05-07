from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from crawler.checkpoint import read_jsonl
from crawler.config import COVERAGE_REPORT_PATH, DISCOVERED_URLS_PATH, FAILURES_PATH, FETCH_MANIFEST_PATH, SECTIONS_PATH, TITLE_NAMES


def pct(part: int, whole: int) -> str:
    if whole == 0:
        return "0.0%"
    return f"{(part / whole) * 100:.1f}%"


def title_from_row(row: dict) -> int | None:
    value = row.get("title_number")
    return int(value) if isinstance(value, int) or (isinstance(value, str) and value.isdigit()) else None


def generate_report(output_path: Path = COVERAGE_REPORT_PATH) -> None:
    discovered = read_jsonl(DISCOVERED_URLS_PATH)
    fetched = read_jsonl(FETCH_MANIFEST_PATH)
    sections = read_jsonl(SECTIONS_PATH)
    failures = read_jsonl(FAILURES_PATH)

    discovered_by_title = Counter(filter(None, (title_from_row(row) for row in discovered)))
    fetched_urls = {row.get("url") for row in fetched}
    fetched_by_title = Counter()
    for row in discovered:
        if row.get("url") in fetched_urls:
            title = title_from_row(row)
            if title:
                fetched_by_title[title] += 1

    extracted_by_title = Counter(filter(None, (title_from_row(row) for row in sections)))
    failed_by_title: dict[int | str, int] = defaultdict(int)
    discovered_url_to_title = {row.get("url"): title_from_row(row) for row in discovered}
    for failure in failures:
        failed_by_title[discovered_url_to_title.get(failure.get("url")) or "unknown"] += 1

    total_words = sum(int(row.get("word_count") or 0) for row in sections)
    all_times = [row.get("retrieved_at") for row in fetched + sections if row.get("retrieved_at")]
    started = min(all_times) if all_times else "not yet run"
    finished = max(all_times) if all_times else "not yet run"

    lines = [
        "# CCR Coverage Report",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Summary",
        "",
        f"- Official CCR titles expected: 28 title numbers, with Title 24 commonly redirecting to building-code material outside the same Westlaw section-page flow.",
        f"- Titles discovered in this dataset: {len(discovered_by_title)}",
        f"- Section URLs discovered: {len(discovered)}",
        f"- Pages fetched: {len(fetched)}",
        f"- Sections extracted: {len(sections)}",
        f"- Failed records logged: {len(failures)}",
        f"- Total extracted word count: {total_words}",
        f"- Crawl timestamp range: {started} to {finished}",
        f"- Completeness estimate against discovered URLs: {pct(len(sections), len(discovered))}",
        "",
        "## Per-Title Coverage",
        "",
        "| Title | Title Name | Sections Discovered | Fetched | Extracted | Failed | Coverage |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    titles = sorted(set(discovered_by_title) | set(fetched_by_title) | set(extracted_by_title))
    for title in titles:
        lines.append(
            f"| {title} | {TITLE_NAMES.get(title, 'Unknown')} | {discovered_by_title[title]} | "
            f"{fetched_by_title[title]} | {extracted_by_title[title]} | {failed_by_title[title]} | "
            f"{pct(extracted_by_title[title], discovered_by_title[title])} |"
        )

    lines.extend(["", "## Failed URLs", ""])
    if failures:
        for failure in failures[:500]:
            lines.append(f"- `{failure.get('stage')}` {failure.get('url')}: {failure.get('reason')}")
        if len(failures) > 500:
            lines.append(f"- ... {len(failures) - 500} more failures omitted from this readable report.")
    else:
        lines.append("No failures have been logged yet.")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "This report is generated from local JSONL artifacts. If only a smoke crawl has been run, the percentages describe that local crawl, not the full CCR corpus.",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CCR crawler coverage report.")
    parser.add_argument("--output", default=str(COVERAGE_REPORT_PATH))
    args = parser.parse_args()
    generate_report(Path(args.output))


if __name__ == "__main__":
    main()
