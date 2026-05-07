from __future__ import annotations

import hashlib
import math
import os
from functools import lru_cache
from typing import Iterable

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass


LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
FASTEMBED_MODEL = os.getenv("FASTEMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "auto").strip().lower()
ALLOW_HASH_EMBEDDINGS = os.getenv("ALLOW_HASH_EMBEDDINGS", "true").strip().lower() in {"1", "true", "yes", "on"}


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class EmbeddingProvider:
    provider_name = "unknown"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class FastEmbedProvider(EmbeddingProvider):
    provider_name = "fastembed"

    def __init__(self, model_name: str = FASTEMBED_MODEL):
        from fastembed import TextEmbedding

        self.model_name = model_name
        self.model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = list(self.model.embed(texts))
        return [_normalize(vector.tolist() if hasattr(vector, "tolist") else list(vector)) for vector in vectors]


class SentenceTransformerProvider(EmbeddingProvider):
    provider_name = "local"

    def __init__(self, model: str = LOCAL_EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


class HashEmbeddingProvider(EmbeddingProvider):
    provider_name = "hash"

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


def _provider_error(provider_label: str, exc: Exception) -> RuntimeError:
    error = RuntimeError(f"Embedding provider '{provider_label}' failed: {exc}")
    error.__cause__ = exc
    return error


def _build_fastembed_provider() -> EmbeddingProvider:
    try:
        provider = FastEmbedProvider()
        provider.embed_query("embedding readiness check")
        return provider
    except Exception as exc:
        raise _provider_error("fastembed", exc)


def _build_local_provider(model: str) -> EmbeddingProvider:
    try:
        return SentenceTransformerProvider(model)
    except Exception as exc:
        raise _provider_error("local", exc)


def _build_hash_provider() -> EmbeddingProvider:
    return HashEmbeddingProvider()


@lru_cache(maxsize=4)
def _get_cached_embedding_provider(model: str, provider_name: str, allow_hash: bool, fastembed_model: str) -> EmbeddingProvider:
    if provider_name == "fastembed":
        return _build_fastembed_provider()
    if provider_name == "local":
        return _build_local_provider(model)
    if provider_name == "hash":
        return _build_hash_provider()
    if provider_name != "auto":
        raise RuntimeError(
            f"Unknown EMBEDDING_PROVIDER '{provider_name}'. Expected one of: auto, fastembed, local, hash."
        )

    fastembed_error: Exception | None = None
    local_error: Exception | None = None

    try:
        return _build_fastembed_provider()
    except Exception as exc:
        fastembed_error = exc

    try:
        return _build_local_provider(model)
    except Exception as exc:
        local_error = exc

    if allow_hash:
        return _build_hash_provider()

    raise RuntimeError(
        "No usable embedding provider is available. "
        f"FastEmbed error: {fastembed_error}; local provider error: {local_error}."
    )


def get_embedding_provider(model: str = LOCAL_EMBEDDING_MODEL) -> EmbeddingProvider:
    return _get_cached_embedding_provider(model, EMBEDDING_PROVIDER, ALLOW_HASH_EMBEDDINGS, FASTEMBED_MODEL)


def batched(items: list, batch_size: int) -> Iterable[list]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]
