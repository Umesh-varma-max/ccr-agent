# CCR Compliance Agent Examiner Guide

## Project Summary

CCR Compliance Agent is a retrieval-augmented compliance advisory system built around the California Code of Regulations. It collects regulation pages, extracts structured section content, indexes those sections in a vector database, retrieves relevant CCR sections for a user question, and presents the result as cited compliance guidance through both an API and a web interface.

## Project Goals

- Convert complex CCR source material into searchable structured data
- Provide facility-specific compliance guidance with citations
- Keep infrastructure cost low by using local embeddings with FastEmbed
- Separate crawling, indexing, retrieval, and presentation into clear modules
- Make the system reviewable by exposing both the answer and the referenced CCR context

## End-to-End Workflow

### 1. Discovery

The crawler starts from the CCR index and discovers regulation document URLs.

Key file:
- `crawler/discover.py`

Output:
- `data/urls/discovered_urls.jsonl`

### 2. Fetching

The fetch stage downloads raw source pages and stores a manifest of what was collected.

Key file:
- `crawler/fetch.py`

Output:
- `data/raw/pages_raw.jsonl`
- raw HTML files under `data/raw/html/`

### 3. Extraction

The extractor converts raw pages into structured section records. Each record includes title number, citation, heading, section number, source URL, and the extracted text body.

Key file:
- `crawler/extract.py`

Important behavior:
- rejects clearly invalid or non-CCR HTML captures
- keeps section metadata for retrieval and explanation
- preserves subsection-style legal structure where possible

Output:
- `data/raw/sections.jsonl`

### 4. Indexing

The indexer chunks section text, embeds each chunk, and upserts the vectors into Qdrant.

Key files:
- `indexer/embed.py`
- `indexer/upsert.py`
- `indexer/schema.py`

Important behavior:
- supports low-cost embedding modes
- skips invalid or contaminated extracted rows
- keeps chunk-level metadata for later answer explanations

### 5. Retrieval

The retriever turns the user question into an embedding query and searches the indexed CCR collection.

Key file:
- `agent/retriever.py`

Important behavior:
- filters suspicious non-CCR hits
- supports title and section filters
- returns document text plus metadata needed for the answer layer

### 6. Answer Generation

The answer layer transforms retrieved sections into human-readable compliance advice.

Key files:
- `agent/agent.py`
- `agent/prompts.py`

Important behavior:
- expands facility-style queries such as restaurant, farm, and theater
- reranks hits based on domain overlap
- uses Groq when available for cleaner structured language
- falls back to an internal structured answer path when Groq is unavailable

### 7. API Delivery

FastAPI exposes the agent through clean endpoints.

Key file:
- `api.py`

Endpoints:
- `GET /health`
- `POST /ask`
- `POST /ask-detailed`

### 8. Frontend Delivery

The React frontend presents the answer and the referenced CCR sections in a simple examiner-friendly interface.

Key files:
- `frontend/src/App.tsx`
- `frontend/src/styles.css`

Important behavior:
- shows the compliance advice in a clean card
- separates the cited CCR context into a collapsible section
- displays follow-up questions when the query needs more specificity

## Frontend Explanation

The frontend is intentionally simple and task-focused.

Main responsibilities:
- collect the user’s compliance question
- call `/ask-detailed`
- display the main answer clearly
- show cited CCR sections below the answer
- preserve a clear distinction between advice and source context

The answer card is optimized for examiner review:
- a short heading
- short advisory paragraphs
- numbered points when appropriate
- a separate context section for traceability

## Backend Explanation

The backend coordinates retrieval, answer shaping, and delivery.

Main responsibilities:
- receive and validate requests
- connect to the retriever
- build a structured answer response
- expose health and answer endpoints
- serve the built frontend in hosted deployment

The backend intentionally returns both:
- a user-facing answer
- a structured section list for explainability

## Agent Design Concepts

This project uses retrieval-augmented generation rather than full model training.

That means:
- the model is not expected to know CCR material by itself
- the system first finds relevant CCR sections
- the answer is generated only from those retrieved sections

This reduces hallucination risk and makes the answer easier to audit.

### Why Retrieval-First Matters

Legal and compliance questions require grounded answers. A pure chat model could answer fluently but incorrectly. This project lowers that risk by making the answer depend on retrieved CCR sections and by exposing those sections in the interface.

### Why Query Expansion and Reranking Matter

A user may ask for “restaurant rules,” but the indexed corpus may not contain the exact same wording. Query expansion and overlap-based reranking help the system prefer food-related or facility-relevant sections over merely semantically similar but irrelevant results.

## Deployment Workflow

The intended hosted path is:

1. Crawl and extract data offline or locally
2. Index the extracted sections into Qdrant
3. Deploy only the API and frontend
4. Connect the hosted API to Qdrant Cloud and Groq

Important deployment files:
- `render.yaml`
- `Procfile`
- `.python-version`

The Python version is pinned because hosted FastEmbed compatibility was important for stable deployment.

## Safety and Cleanliness Choices

The repository was cleaned and organized to support examiner review:

- environment secrets are excluded from version control
- generated local data artifacts are excluded from version control
- invalid extraction paths are filtered
- suspicious frontend-content contamination is blocked from indexing
- the answer format is consistent and readable

## Testing Approach

Tests focus on the areas most likely to break:

- extraction correctness
- follow-up behavior
- answer formatting behavior
- retrieval ranking regressions

Key test files:
- `tests/test_extractor.py`
- `tests/test_agent.py`

## Known Limitations

- coverage depends on the quality and completeness of the crawl
- some facility domains may still need broader CCR indexing
- highly specific legal interpretation still requires professional review

## Final Assessment

This project demonstrates a full-stack retrieval-based AI workflow:

- legal-source crawling
- data extraction
- vector indexing
- retrieval-driven reasoning
- API serving
- frontend explanation

Its strongest feature is that the answer is not presented as a black box. The interface shows both the compliance advice and the referenced CCR sections used to support that advice.
