# CCR Compliance Agent

An end-to-end Python pipeline for crawling the California Code of Regulations (CCR), extracting section-level Markdown, indexing sections into a vector database, and answering facility compliance questions with citations.

Every stage writes JSONL artifacts, checkpoints, and failures so partial coverage is visible instead of hidden.

## A. Setup

Requirements:

- Python 3.11+
- Crawl4AI browser dependencies installed in the active environment
- Groq API key for LLM answers

Install:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m crawl4ai-setup
```

Create `.env` from `.env.example`:

```bash
copy .env.example .env
```

Environment variables:

- `GROQ_API_KEY`: create at `https://console.groq.com/keys`
- `GROQ_CHAT_MODEL`: defaults to `llama-3.1-8b-instant`
- `LOCAL_EMBEDDING_MODEL`: defaults to `all-MiniLM-L6-v2`
- `QDRANT_URL`: Qdrant Cloud endpoint
- `QDRANT_API_KEY`: Qdrant API key
- `QDRANT_COLLECTION`: defaults to `ccr_sections`
- `QDRANT_LOCAL_PATH`: optional override for local Qdrant storage
- `CCR_BROWSER_CHANNEL`: `chromium` (default) or `chrome`
- `CCR_CDP_URL`: optional Chrome DevTools endpoint (e.g. `http://localhost:9222`)
- `CCR_PROXY`: optional proxy URL

Vector database: Qdrant. For deployment, use Qdrant Cloud with `QDRANT_URL` and `QDRANT_API_KEY`. For local-only development, the default local path is under `%LOCALAPPDATA%\ccr-agent-qdrant\ccr_qdrant`.

Embeddings: `sentence-transformers/all-MiniLM-L6-v2` for local embeddings, with a deterministic hash fallback for offline tests.

LLM: Groq chat completions when `GROQ_API_KEY` is present; extractive snippet answers otherwise.

## B. Run Each Stage

```bash
# Stage 1: Discover all section URLs
python crawler/discover.py --output data/urls/discovered_urls.jsonl

# Optional smoke discovery
python crawler/discover.py --output data/urls/discovered_urls.jsonl --max-pages 25

# Optional: capture a visible browser session first, then reuse it
python crawler/auth_session.py
python crawler/discover.py --output data/urls/discovered_urls.jsonl --use-profile

# If security verification loops, save verified Playwright storage state
python crawler/auth_session.py --storage-state data/browser_state.json
python crawler/discover.py --output data/urls/discovered_urls.jsonl --playwright --storage-state data/browser_state.json --max-pages 25
python crawler/fetch.py --input data/urls/discovered_urls.jsonl --output data/raw/ --playwright --storage-state data/browser_state.json --max-urls 20

# Connect to Chrome remote debugging if Playwright profiles are blocked
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$PWD\data\chrome_cdp"
python crawler/discover.py --output data/urls/discovered_urls.jsonl --cdp-url http://localhost:9222 --max-pages 25

# Stage 2: Fetch all discovered pages
python crawler/fetch.py --input data/urls/discovered_urls.jsonl --output data/raw/

# Stage 3: Extract structured sections
python crawler/extract.py --input data/raw/pages_raw.jsonl --output data/raw/sections.jsonl

# Stage 4: Index into vector database
python indexer/upsert.py --input data/raw/sections.jsonl

# Verify vector DB record count
python indexer/upsert.py --verify

# Generate coverage report
python crawler/report.py

# Stage 5: Run the compliance agent
python agent/agent.py

# Or ask one question directly
python agent/agent.py "What CCR sections apply to a restaurant in California?"

# Run the API server
uvicorn api:app --host 0.0.0.0 --port 8000

# Run the React UI
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

API endpoints:

- `GET /health`
- `POST /ask` with JSON body `{"question": "...", "top_k": 5}`
- `POST /ask-detailed` with the same body for full metadata, citations, and follow-up state

## C. Design Decisions

URL discovery uses breadth-first search from `https://govt.westlaw.com/calregs/Index`. Navigation pages stay in the queue, while `/calregs/Document/` URLs are saved to `data/urls/discovered_urls.jsonl`. URLs are normalized by removing query strings, fragments, and trailing slashes.

Fetching uses Crawl4AI `AsyncWebCrawler` with max concurrency of 5, randomized delays, exponential retries, checkpoints, and a `failures.jsonl` log for resumability.

Extraction uses BeautifulSoup heuristics around main/document containers. It preserves subsection markers like `(a)` and `(1)`, builds the canonical schema, computes word count, and logs unparseable pages instead of crashing.

The agent is retrieval-first: it queries Qdrant before answering, includes citations, and asks follow-up questions for vague prompts. With `GROQ_API_KEY`, Groq answers only from retrieved sections. Without it, the CLI returns an extractive cited answer.

Deployment shape: crawler and indexer are batch jobs; `api.py` is the hosted web service. Crawl and index once, then deploy only the API connected to Qdrant Cloud and Groq.

## D. Known Limitations

- Westlaw pages may change CSS/classes; the extractor is heuristic and should be validated after a real crawl.
- Title 24 involves building-code material and external flows that may need special handling.
- The hash embedding fallback is only for smoke testing; use sentence-transformers for real semantic quality.

## E. Improvements

- Scheduled re-crawling and diff reports for regulatory updates.
- Stronger title/chapter coverage validation against an official table of contents.
- Richer chunking that respects legal subsection boundaries.
- Saved sessions, exportable answer packets, and coverage diagnostics in the reviewer UI.
- Package the React frontend with the API for single-command deployment.
- CI with schema validation and extractor test fixtures.

## F. Example Agent Interactions

**Restaurant**

Question: `What CCR sections apply to a restaurant in California?`

Expected: retrieves food safety, sanitation, public health, labor, or fire safety sections. Answers with citations such as `17 CCR § ...` or `8 CCR § ...`, explains applicability, and ends with the legal disclaimer.

**Movie Theater**

Question: `What regulations should a movie theater operator be aware of?`

Expected: retrieves public safety, occupancy, emergency exit, accessibility, or workplace safety sections. Cites every referenced section and asks follow-up questions if occupancy or venue type matters.

**Farm**

Question: `What laws apply to farms or agricultural facilities?`

Expected: retrieves agricultural, pesticide, worker safety, and environmental sections. Explains relevance to farming and flags missing details such as crop type, pesticides, housing, or processing.

## Self-Evaluation

- Crawler uses Crawl4AI with checkpoints, retries, concurrency control, and failure logs.
- Extractor outputs the canonical section schema.
- Indexer performs idempotent Qdrant upserts using citation-based chunk IDs.
- Agent retrieves before answering, cites retrieved sections, asks follow-ups for vague queries, and always includes the disclaimer.
- Coverage report generation is implemented and honest about crawl results.

## Deployment

Hosted setup: Render/Railway/Fly.io for the API, Qdrant Cloud for vectors, Groq for LLM.

If you are serving the React UI from `api.py` on Render, rebuild `frontend/dist` before pushing UI changes. The current Render Python service installs Python dependencies only; it does not rebuild the frontend during deploy.

Set these environment variables in your hosting dashboard:

```env
GROQ_API_KEY=your_groq_key
GROQ_CHAT_MODEL=llama-3.1-8b-instant
LOCAL_EMBEDDING_MODEL=all-MiniLM-L6-v2
QDRANT_URL=https://your-qdrant-endpoint
QDRANT_API_KEY=your_qdrant_api_key
QDRANT_COLLECTION=ccr_sections
PORT=8000
```

Before deploying, index data into the hosted Qdrant collection:

```bash
python crawler/discover.py --output data/urls/discovered_urls.jsonl
python crawler/fetch.py --input data/urls/discovered_urls.jsonl --output data/raw/
python crawler/extract.py --input data/raw/pages_raw.jsonl --output data/raw/sections.jsonl
python crawler/report.py
python indexer/upsert.py --input data/raw/sections.jsonl
python indexer/upsert.py --verify
```

Start command:

```bash
uvicorn api:app --host 0.0.0.0 --port $PORT
```

The repository includes `Procfile` and `render.yaml` for common deployment platforms.
