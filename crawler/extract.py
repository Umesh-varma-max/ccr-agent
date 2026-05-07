from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from crawler.checkpoint import append_jsonl, read_jsonl, write_jsonl
from crawler.config import FAILURES_PATH, FETCH_MANIFEST_PATH, SECTIONS_PATH, TITLE_NAMES


SECTION_RE = re.compile(r"[§Â]+§*\s*([\w.\-]+)\.?\s*(.*)")
TITLE_RE = re.compile(r"Title\s+(\d{1,2})\s*[-–: ]\s*([^\n|]+)", re.I)


NON_CCR_MARKERS = (
    "/@vite/client",
    "data-vite-dev-id",
    "CalReg Compass",
    "CCR Compliance Agent",
    "Supporting CCR links",
    "indexed records available in the current CCR dataset",
    "This section appears relevant based on its heading:",
    "Use this section as a practical checklist, focusing on:",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_obviously_non_ccr_html(html: str, title_text: str = "") -> bool:
    haystack = f"{title_text}\n{html}".lower()
    return any(marker.lower() in haystack for marker in NON_CCR_MARKERS)


def infer_title(text: str, url: str = "") -> tuple[int | None, str | None]:
    match = TITLE_RE.search(text)
    if match:
        number = int(match.group(1))
        return number, compact(match.group(2)).title()
    url_match = re.search(r"(?:title|titlenum)\D{0,5}(\d{1,2})", url, re.I)
    if url_match:
        number = int(url_match.group(1))
        return number, TITLE_NAMES.get(number)
    return None, None


def find_main_container(soup: BeautifulSoup):
    candidates = [
        {"role": "main"},
        {"id": re.compile("document|content|main", re.I)},
        {"class": re.compile("document|content|co_document|main", re.I)},
    ]
    for attrs in candidates:
        found = soup.find(attrs=attrs)
        if found and len(found.get_text(" ", strip=True)) > 80:
            return found
    body = soup.body or soup
    return body


def element_to_lines(container) -> list[str]:
    for tag in container.find_all(["script", "style", "noscript", "svg", "nav", "header", "footer"]):
        tag.decompose()

    lines: list[str] = []
    for element in container.find_all(["h1", "h2", "h3", "p", "li", "div"], recursive=True):
        text = compact(element.get_text(" ", strip=True))
        if not text:
            continue
        if len(text) < 3 or text.lower() in {"next", "previous", "search", "help"}:
            continue
        if element.name in {"h1", "h2"}:
            line = f"## {text}"
        elif element.name == "h3":
            line = f"### {text}"
        elif element.name == "li":
            line = f"- {text}"
        else:
            line = text
        if not lines or lines[-1] != line:
            lines.append(line)
    return lines


def parse_hierarchy(text: str) -> dict:
    fields: dict[str, str | None] = {
        "division": None,
        "division_name": None,
        "chapter": None,
        "chapter_name": None,
        "subchapter": None,
        "article": None,
    }
    patterns = {
        "division": r"Division\s+([\w.\-]+)\.?\s*([^\n>]+)?",
        "chapter": r"Chapter\s+([\w.\-]+)\.?\s*([^\n>]+)?",
        "subchapter": r"Subchapter\s+([\w.\-]+)",
        "article": r"Article\s+([\w.\-]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            fields[key] = compact(match.group(1)).rstrip(".")
            if key in {"division", "chapter"} and match.lastindex and match.lastindex >= 2:
                fields[f"{key}_name"] = compact(match.group(2) or "") or None
    return fields


def extract_westlaw_section(soup: BeautifulSoup, source_url: str, retrieved_at: str | None = None) -> dict | None:
    header = soup.select_one("#co_docHeaderTitleLine #title, #co_docHeaderTitleLine, .co_title")
    body = soup.select_one("#co_document")
    if not header or not body:
        return None

    header_text = compact(header.get_text(" ", strip=True))
    section_match = SECTION_RE.search(header_text)
    if not section_match:
        return None
    section_number = section_match.group(1).rstrip(".")
    heading = compact(section_match.group(2)) or header_text

    citation_element = soup.select_one("#co_docHeaderCitation #titleDesc") or soup.select_one(".co_cites")
    citation_text = compact(citation_element.get_text(" ", strip=True)) if citation_element else ""
    title_number = None
    cite_match = re.search(r"\b(\d{1,2})\s+(?:CA\s+ADC|CCR)\b", citation_text, re.I)
    if cite_match:
        title_number = int(cite_match.group(1))

    prelim_text = compact((soup.select_one("#co_prelimContainer") or body).get_text(" ", strip=True))
    title_name = None
    title_match = re.search(r"Title\s+(\d{1,2})\.\s*([^>]+?)(?=\s+Division|\s+Chapter|\s+Article|$)", prelim_text, re.I)
    if title_match:
        title_number = title_number or int(title_match.group(1))
        title_name = compact(title_match.group(2))
    if title_number is None:
        raise ValueError("no CCR title number found")

    hierarchy = parse_hierarchy(prelim_text)
    paragraphs = [compact(p.get_text(" ", strip=True)) for p in soup.select(".co_paragraphText")]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        paragraphs = [compact(body.get_text(" ", strip=True))]

    content_lines = [f"## § {section_number}. {heading}", *paragraphs]
    currency = soup.select_one(".co_includeCurrencyBlock, .co_currencyNotice")
    if currency:
        content_lines.append(compact(currency.get_text(" ", strip=True)))
    content_markdown = "\n\n".join(dict.fromkeys(content_lines))
    words = re.findall(r"\b\w+\b", content_markdown)

    citation = f"{title_number} CCR § {section_number}"
    breadcrumb_parts = [f"Title {title_number}"]
    if hierarchy.get("division"):
        breadcrumb_parts.append(f"Div {hierarchy['division']}")
    if hierarchy.get("chapter"):
        breadcrumb_parts.append(f"Chapter {hierarchy['chapter']}")
    if hierarchy.get("article"):
        breadcrumb_parts.append(f"Article {hierarchy['article']}")
    breadcrumb_parts.append(f"§ {section_number}")

    return {
        "title_number": title_number,
        "title_name": title_name or TITLE_NAMES.get(title_number) or "Unknown",
        "division": hierarchy["division"],
        "division_name": hierarchy["division_name"],
        "chapter": hierarchy["chapter"],
        "chapter_name": hierarchy["chapter_name"],
        "subchapter": hierarchy["subchapter"],
        "article": hierarchy["article"],
        "section_number": section_number,
        "section_heading": heading,
        "citation": citation,
        "breadcrumb_path": " > ".join(breadcrumb_parts),
        "source_url": source_url,
        "content_markdown": content_markdown,
        "retrieved_at": retrieved_at or utc_now(),
        "word_count": len(words),
        "has_subsections": bool(re.search(r"\([a-z0-9]+\)", content_markdown)),
    }


def extract_section(html: str, source_url: str, retrieved_at: str | None = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title_text = compact(soup.title.get_text(" ", strip=True)) if soup.title else ""
    if is_obviously_non_ccr_html(html, title_text):
        raise ValueError("non-CCR HTML captured instead of a regulation page")

    westlaw_section = extract_westlaw_section(soup, source_url, retrieved_at)
    if westlaw_section:
        return westlaw_section
    if "govt.westlaw.com/calregs/document/" in source_url.lower() and "california code of regulations" not in title_text.lower():
        raise ValueError("unexpected document HTML: missing CCR page title")
    container = find_main_container(soup)
    lines = element_to_lines(container)
    joined = "\n".join(lines)
    searchable = f"{title_text}\n{joined}"

    section_match = SECTION_RE.search(searchable)
    if not section_match:
        raise ValueError("no section number found")

    section_number = section_match.group(1).rstrip(".")
    heading = compact(section_match.group(2))
    if not heading:
        first_line = next((line for line in lines if section_number in line), title_text)
        heading = compact(re.sub(r"^#+\s*", "", SECTION_RE.sub(r"\2", first_line))) or "Untitled Section"

    title_number, title_name = infer_title(searchable, source_url)
    if title_number is None:
        raise ValueError("no CCR title number found")

    hierarchy = parse_hierarchy(searchable)
    citation = f"{title_number} CCR § {section_number}"
    breadcrumb_parts = [f"Title {title_number}"]
    if hierarchy.get("division"):
        breadcrumb_parts.append(f"Div {hierarchy['division']}")
    if hierarchy.get("chapter"):
        breadcrumb_parts.append(f"Chapter {hierarchy['chapter']}")
    if hierarchy.get("article"):
        breadcrumb_parts.append(f"Article {hierarchy['article']}")
    breadcrumb_parts.append(f"§ {section_number}")

    content_lines = lines or [compact(container.get_text(" ", strip=True))]
    if not any(line.startswith("##") and section_number in line for line in content_lines):
        content_lines.insert(0, f"## § {section_number}. {heading}")
    content_markdown = "\n\n".join(dict.fromkeys(content_lines))
    words = re.findall(r"\b\w+\b", content_markdown)

    return {
        "title_number": title_number,
        "title_name": title_name or TITLE_NAMES.get(title_number) or "Unknown",
        "division": hierarchy["division"],
        "division_name": hierarchy["division_name"],
        "chapter": hierarchy["chapter"],
        "chapter_name": hierarchy["chapter_name"],
        "subchapter": hierarchy["subchapter"],
        "article": hierarchy["article"],
        "section_number": section_number,
        "section_heading": heading,
        "citation": citation,
        "breadcrumb_path": " > ".join(breadcrumb_parts),
        "source_url": source_url,
        "content_markdown": content_markdown,
        "retrieved_at": retrieved_at or utc_now(),
        "word_count": len(words),
        "has_subsections": bool(re.search(r"\([a-z0-9]+\)", content_markdown)),
    }


def extract(input_path: Path, output_path: Path) -> None:
    rows = read_jsonl(input_path)
    sections: list[dict] = []
    for row in rows:
        html_path = Path(row["html_path"])
        try:
            section = extract_section(html_path.read_text(encoding="utf-8"), row["url"], row.get("retrieved_at"))
            sections.append(section)
        except Exception as exc:
            append_jsonl(FAILURES_PATH, {"url": row.get("url"), "stage": "extract", "reason": str(exc), "failed_at": utc_now()})
    write_jsonl(output_path, sections)
    print(f"Extracted {len(sections)} sections to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract canonical CCR section JSON from fetched HTML.")
    parser.add_argument("--input", default=str(FETCH_MANIFEST_PATH))
    parser.add_argument("--output", default=str(SECTIONS_PATH))
    args = parser.parse_args()
    extract(Path(args.input), Path(args.output))


if __name__ == "__main__":
    main()
