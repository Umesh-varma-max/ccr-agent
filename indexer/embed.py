from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Iterable

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")


class EmbeddingProvider:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class SentenceTransformerProvider(EmbeddingProvider):
    def __init__(self, model: str = LOCAL_EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline fallback for tests and demos without API keys."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        buckets = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:2], "big") % self.dimensions
            buckets[idx] += 1.0
        norm = sum(value * value for value in buckets) ** 0.5 or 1.0
        return [value / norm for value in buckets]


@lru_cache(maxsize=2)
def _get_cached_embedding_provider(model: str) -> EmbeddingProvider:
    try:
        return SentenceTransformerProvider(model)
    except Exception:
        return HashEmbeddingProvider()


def get_embedding_provider(model: str = LOCAL_EMBEDDING_MODEL) -> EmbeddingProvider:
    return _get_cached_embedding_provider(model)


def batched(items: list, batch_size: int) -> Iterable[list]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]
