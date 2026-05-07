# CCR Compliance Agent

A retrieval-first compliance assistant for the California Code of Regulations (CCR). The project crawls CCR source pages, extracts structured section data, indexes those sections into Qdrant, and answers facility-specific compliance questions through a FastAPI backend and React frontend.

## What This Project Does

- Discovers CCR document URLs from the Westlaw-hosted CCR index
- Fetches and stores raw document pages for traceability
- Extracts structured CCR sections with citations and metadata
- Indexes section chunks into Qdrant using low-cost local embeddings
- Answers compliance questions with cited, human-readable guidance
- Exposes both an API and a browser-based interface

## System Architecture

The system is organized as a staged pipeline:

1. `crawler/`
   Discovers, fetches, and extracts CCR source material.

2. `indexer/`
   Embeds and upserts extracted section chunks into Qdrant.

3. `agent/`
   Retrieves relevant sections and turns them into structured compliance guidance.

4. `api.py`
   Serves the agent through FastAPI endpoints.

5. `frontend/`
   Provides a React interface for asking compliance questions and reviewing cited CCR sections.

## Repository Layout

```text
agent/       Retrieval and answer generation
crawler/     Discovery, fetching, extraction, and reporting
frontend/    React user interface
indexer/     Embedding and Qdrant indexing
tests/       Regression tests for extraction and answer behavior
api.py       FastAPI application
qdrant_utils.py  Qdrant connection wrapper
render.yaml  Render deployment configuration
```

## Core Workflow

### 1. Crawl

Discover CCR document URLs:

```bash
python crawler/discover.py --output data/urls/discovered_urls.jsonl
```

Fetch raw document pages:

```bash
python crawler/fetch.py --input data/urls/discovered_urls.jsonl --output data/raw/
```

Extract structured sections:

```bash
python crawler/extract.py --input data/raw/pages_raw.jsonl --output data/raw/sections.jsonl
```

### 2. Index

Upsert extracted sections into Qdrant:

```bash
python indexer/upsert.py --input data/raw/sections.jsonl
```

Verify the collection:

```bash
python indexer/upsert.py --verify
```

### 3. Ask Questions

Run the CLI agent:

```bash
python agent/agent.py
```

Ask one question directly:

```bash
python agent/agent.py "What CCR sections apply to a California Welcome Center?"
```

### 4. Run the Web App

Start the API:

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

Start the frontend during local development:

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

## API Endpoints

- `GET /health`
- `POST /ask`
- `POST /ask-detailed`

Example request body:

```json
{
  "question": "What CCR sections apply to a California Welcome Center?",
  "top_k": 5
}
```

## Environment Variables

Create `.env` from `.env.example` and set the values required for your environment.

Important variables:

- `GROQ_API_KEY`
- `GROQ_CHAT_MODEL`
- `EMBEDDING_PROVIDER`
- `FASTEMBED_MODEL`
- `LOCAL_EMBEDDING_MODEL`
- `ALLOW_HASH_EMBEDDINGS`
- `QDRANT_URL`
- `QDRANT_API_KEY`
- `QDRANT_COLLECTION`
- `QDRANT_LOCAL_PATH`

## Retrieval and Answer Design

This project uses a retrieval-augmented generation workflow instead of fine-tuning:

- The retriever searches indexed CCR sections using embeddings
- Facility-style queries are expanded and reranked for better domain relevance
- The answer layer summarizes retrieved CCR sections into human-readable advice
- Every answer is grounded in retrieved CCR citations

If Groq is available, the system uses LLM-based answer polishing. If Groq is unavailable, the project falls back to a structured extractive answer path.

## Deployment Notes

The repository is configured for Render deployment through `render.yaml`.

Important deployment points:

- Python is pinned for compatibility with `fastembed`
- Render serves the FastAPI app
- The built frontend in `frontend/dist` is served by `api.py`
- Qdrant Cloud and Groq are expected in hosted deployment

## Testing

Run the regression suite:

```bash
python -m pytest -q
```

The tests cover:

- extractor behavior
- answer formatting behavior
- retrieval ranking safeguards

## Known Limitations

- CCR coverage depends on crawl completeness
- Some highly specific facility queries still depend on better domain coverage in the indexed dataset
- Westlaw page structure can change and may require extractor updates
- The current system is guidance-oriented and not a substitute for legal review

## Examiner Notes

This repository is intentionally structured to show the complete workflow:

- raw legal source acquisition
- structured extraction
- vector indexing
- retrieval-first answer generation
- API delivery
- frontend presentation

For a full narrative explanation of the architecture, components, workflow, and design decisions, see the accompanying examiner guide document delivered with this project.
