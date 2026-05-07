from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from crawler.config import QDRANT_LOCAL_PATH

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass


def get_qdrant_uri(default_path: Path = QDRANT_LOCAL_PATH) -> str:
    return os.getenv("QDRANT_URL") or str(default_path)


def get_qdrant_api_key() -> str | None:
    api_key = os.getenv("QDRANT_API_KEY")
    return api_key or None


def _filter_from_expr(filter_expr: str):
    if not filter_expr:
        return None

    from qdrant_client import models

    conditions = []
    title_match = re.search(r"title_number\s*==\s*(\d+)", filter_expr)
    if title_match:
        conditions.append(
            models.FieldCondition(
                key="title_number",
                match=models.MatchValue(value=int(title_match.group(1))),
            )
        )
    section_match = re.search(r"section_number\s*==\s*['\"]?([^'\"&]+)['\"]?", filter_expr)
    if section_match:
        conditions.append(
            models.FieldCondition(
                key="section_number",
                match=models.MatchValue(value=section_match.group(1).strip()),
            )
        )
    if not conditions:
        raise ValueError(f"Unsupported filter expression: {filter_expr}")

    return models.Filter(must=conditions)


class QdrantStore:
    def __init__(self, client):
        self._client = client

    def has_collection(self, collection_name: str) -> bool:
        try:
            self._client.get_collection(collection_name)
            return True
        except Exception:
            return False

    def ensure_collection(self, collection_name: str, dimension: int) -> None:
        from qdrant_client import models

        if self.has_collection(collection_name):
            return
        self._client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )

    def upsert(self, collection_name: str, data: list[dict[str, Any]]) -> None:
        from qdrant_client import models

        points = []
        for record in data:
            payload = {k: v for k, v in record.items() if k != "vector"}
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(record["id"])))
            points.append(models.PointStruct(id=point_id, vector=record["vector"], payload=payload))
        self._client.upsert(collection_name=collection_name, points=points, wait=True)

    def search(
        self,
        collection_name: str,
        data: list[list[float]],
        anns_field: str = "vector",
        limit: int = 5,
        filter: str = "",
        output_fields: list[str] | None = None,
    ) -> list[list[dict[str, Any]]]:
        query_vector = data[0]
        query_filter = _filter_from_expr(filter)
        response = self._client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        results = response.points
        rows: list[dict[str, Any]] = []
        for item in results:
            payload = dict(item.payload or {})
            if output_fields:
                entity = {field: payload.get(field) for field in output_fields}
            else:
                entity = payload
            rows.append({"entity": entity, "distance": item.score})
        return [rows]

    def get_collection_stats(self, collection_name: str) -> dict[str, int]:
        count = self._client.count(collection_name=collection_name, exact=True).count
        return {"row_count": count}

    def flush(self, collection_name: str) -> None:
        return None

    def close(self) -> None:
        close_fn = getattr(self._client, "close", None)
        if callable(close_fn):
            close_fn()


def connect_qdrant(default_path: Path = QDRANT_LOCAL_PATH) -> QdrantStore:
    from qdrant_client import QdrantClient

    uri = get_qdrant_uri(default_path)
    api_key = get_qdrant_api_key()
    if uri.startswith(("http://", "https://")):
        client = QdrantClient(url=uri, api_key=api_key)
    else:
        Path(uri).mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=uri)
    return QdrantStore(client)
