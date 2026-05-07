from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from crawler.config import QDRANT_COLLECTION, QDRANT_LOCAL_PATH
from indexer.embed import get_embedding_provider
from qdrant_utils import connect_qdrant

NON_CCR_HIT_MARKERS = (
    "CalReg Compass",
    "CCR Compliance Agent",
    "Supporting CCR links",
    "indexed records available in the current CCR dataset",
    "This section appears relevant based on its heading:",
    "Use this section as a practical checklist, focusing on:",
)


def _is_suspicious_hit(document: str, metadata: dict[str, Any]) -> bool:
    combined = "\n".join(
        [
            document or "",
            str(metadata.get("section_heading") or ""),
            str(metadata.get("title_name") or ""),
        ]
    ).lower()
    return any(marker.lower() in combined for marker in NON_CCR_HIT_MARKERS)


class CCRRetriever:
    def __init__(self, db_path: Path = QDRANT_LOCAL_PATH, collection_name: str = QDRANT_COLLECTION):
        self.client = connect_qdrant(db_path)
        self.collection_name = collection_name
        self.embedder = get_embedding_provider()

    def search(self, query: str, top_k: int = 5, title_number: int | None = None, section_number: str | None = None) -> list[dict[str, Any]]:
        if not self.client.has_collection(self.collection_name):
            return []
        filter_parts = []
        if title_number is not None:
            filter_parts.append(f"title_number == {title_number}")
        if section_number is not None:
            filter_parts.append(f"section_number == '{section_number}'")
        filter_expr = " & ".join(filter_parts)
        results = self.client.search(
            collection_name=self.collection_name,
            data=[self.embedder.embed_query(query)],
            anns_field="vector",
            limit=top_k,
            filter=filter_expr,
            output_fields=[
                "text",
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
                "retrieved_at",
                "word_count",
                "has_subsections",
                "chunk_index",
            ],
        )
        hits: list[dict[str, Any]] = []
        for item in results[0] if results else []:
            entity = item.get("entity", {})
            document = entity.pop("text", "")
            if _is_suspicious_hit(document, entity):
                continue
            hits.append({"document": document, "metadata": entity, "distance": item.get("distance")})
        return hits


@lru_cache(maxsize=1)
def get_shared_retriever() -> CCRRetriever:
    return CCRRetriever()
