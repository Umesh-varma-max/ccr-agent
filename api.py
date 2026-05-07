from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.agent import answer, build_agent_response
from agent.retriever import CCRRetriever, get_shared_retriever
from crawler.config import QDRANT_COLLECTION
from qdrant_utils import connect_qdrant, get_qdrant_uri

logger = logging.getLogger("ccr-api")


# ── Request / Response Models ────────────────────────────────────────


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3)
    top_k: int = Field(default=5, ge=1, le=20)


class AskResponse(BaseModel):
    answer: str


class RetrievedSection(BaseModel):
    citation: str | None
    section_heading: str | None
    breadcrumb_path: str | None
    source_url: str | None
    title_number: int | None
    chapter: str | None
    section_number: str | None
    snippet: str
    why_it_applies: str | None
    advice: str | None


class AskDetailedResponse(BaseModel):
    answer: str
    citations: list[str]
    needs_follow_up: bool
    follow_up_question: str | None
    retrieved_sections: int
    used_llm: bool
    has_strong_match: bool
    disclaimer: str
    sections: list[RetrievedSection]


class HealthResponse(BaseModel):
    status: str
    vector_db_uri: str
    collection: str
    indexed_records: int | None


# ── Startup: pre-load embedding model + retriever ────────────────────

HEALTH_CACHE_TTL = int(os.getenv("HEALTH_CACHE_TTL_SECONDS", "300"))

_retriever: CCRRetriever | None = None
_health_cache: HealthResponse | None = None
_health_cache_at: float = 0.0


def _get_retriever() -> CCRRetriever:
    global _retriever
    if _retriever is None:
        _retriever = get_shared_retriever()
    return _retriever


def _get_health() -> HealthResponse:
    global _health_cache, _health_cache_at
    if _health_cache and (time.time() - _health_cache_at) < HEALTH_CACHE_TTL:
        return _health_cache
    client = connect_qdrant()
    try:
        exists = client.has_collection(QDRANT_COLLECTION)
        stats: dict[str, Any] = client.get_collection_stats(QDRANT_COLLECTION) if exists else {}
        _health_cache = HealthResponse(
            status="ok" if exists else "no_collection",
            vector_db_uri=get_qdrant_uri(),
            collection=QDRANT_COLLECTION,
            indexed_records=stats.get("row_count") if exists else 0,
        )
        _health_cache_at = time.time()
        return _health_cache
    finally:
        client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load the embedding model and retriever at startup
    logger.info("Pre-loading embedding model and retriever...")
    try:
        _get_retriever()
        logger.info("Retriever ready")
    except Exception as exc:
        logger.warning("Retriever pre-load failed (will retry on first request): %s", exc)
    # Pre-cache health
    try:
        _get_health()
        logger.info("Health cache primed")
    except Exception:
        pass
    yield


# ── FastAPI app ──────────────────────────────────────────────────────

app = FastAPI(
    title="CCR Compliance Agent API",
    description="RAG API for California Code of Regulations compliance questions.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API Routes ───────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        return _get_health()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant health check failed: {exc}") from exc


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    try:
        return AskResponse(answer=answer(request.question, top_k=request.top_k))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ask-detailed", response_model=AskDetailedResponse)
def ask_detailed(request: AskRequest) -> AskDetailedResponse:
    try:
        retriever = _get_retriever()
        response = build_agent_response(request.question, top_k=request.top_k, retriever=retriever)
        return AskDetailedResponse(
            answer=response["answer"],
            citations=response["citations"],
            needs_follow_up=response["needs_follow_up"],
            follow_up_question=response["follow_up_question"],
            retrieved_sections=len(response["hits"]),
            used_llm=response["used_llm"],
            has_strong_match=response["has_strong_match"],
            disclaimer=response["disclaimer"],
            sections=[
                RetrievedSection(
                    citation=brief.get("citation"),
                    section_heading=brief.get("section_heading"),
                    breadcrumb_path=brief.get("breadcrumb_path"),
                    source_url=brief.get("source_url"),
                    title_number=brief.get("title_number"),
                    chapter=brief.get("chapter"),
                    section_number=brief.get("section_number"),
                    snippet=brief.get("snippet", ""),
                    why_it_applies=brief.get("why_it_applies"),
                    advice=brief.get("advice"),
                )
                for brief in response["section_briefs"]
            ],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Serve React frontend from frontend/dist ──────────────────────────

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend" / "dist"
if FRONTEND_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str):
        file_path = FRONTEND_DIR / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
