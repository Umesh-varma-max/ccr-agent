from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REQUIRED_FIELDS = {
    "title_number",
    "title_name",
    "division",
    "division_name",
    "chapter",
    "chapter_name",
    "subchapter",
    "article",
    "section_number",
    "section_heading",
    "citation",
    "breadcrumb_path",
    "source_url",
    "content_markdown",
    "retrieved_at",
    "word_count",
    "has_subsections",
}


@dataclass(frozen=True)
class CCRSection:
    title_number: int
    title_name: str
    division: str | None
    division_name: str | None
    chapter: str | None
    chapter_name: str | None
    subchapter: str | None
    article: str | None
    section_number: str
    section_heading: str
    citation: str
    breadcrumb_path: str
    source_url: str
    content_markdown: str
    retrieved_at: str
    word_count: int
    has_subsections: bool


def validate_section(row: dict[str, Any]) -> CCRSection:
    missing = REQUIRED_FIELDS - set(row)
    if missing:
        raise ValueError(f"missing schema fields: {sorted(missing)}")
    if not isinstance(row["title_number"], int):
        raise TypeError("title_number must be an integer")
    if not row["citation"] or "CCR §" not in row["citation"]:
        raise ValueError("citation must use format 'N CCR § X'")
    if not row["content_markdown"]:
        raise ValueError("content_markdown cannot be empty")
    return CCRSection(**{field: row[field] for field in REQUIRED_FIELDS})


def metadata_for_vector_store(section: dict[str, Any], chunk_index: int) -> dict[str, Any]:
    metadata = {k: v for k, v in section.items() if k != "content_markdown"}
    metadata["chunk_index"] = chunk_index
    return {k: ("" if v is None else v) for k, v in metadata.items()}
