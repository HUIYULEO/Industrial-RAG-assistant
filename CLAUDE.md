# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Warehouse Automation Design Decision Assistant - an enterprise RAG (Retrieval-Augmented Generation) application that helps engineers with warehouse automation design decisions. Built with FastAPI backend, Streamlit frontend, and Supabase (PostgreSQL + pgvector) for vector storage.

## Common Commands

### Development
```bash
# Run backend (requires .env with OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run frontend (in separate terminal)
streamlit run app/frontend.py --server.port 3000

# Docker deployment
docker-compose up --build
```

### Testing
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_chat_engine.py -v

# Run single test
pytest tests/test_chat_engine.py::test_calculate_confidence_score -v
```

### Document Ingestion
```bash
# Ingest PDF documents to vector store (run once per document)
python scripts/ingest.py
```

## Architecture

### Data Flow
1. User sends question to FastAPI `/chat` endpoint
2. `chat_engine.py` embeds query via OpenAI and retrieves similar documents from Supabase using `match_whdocuments` RPC function
3. HybridRetriever (optional) falls back to DuckDuckGo web search if local confidence < 0.5
4. Retrieved docs + conversation history injected into LLM prompt
5. Response includes answer, sources, confidence score, and metadata

### Key Components

**Retrieval Layer (`app/services/chat_engine.py`)**
- `SupabaseRPCRetriever`: Custom LangChain retriever that calls Supabase RPC function for vector similarity search
- `HybridRetriever` (`app/services/hybrid_retriever.py`): Combines local vector search with DuckDuckGo web search fallback

**Session Management (`app/main.py`)**
- In-memory session storage (swap for Redis in production)
- Sliding window keeps last 10 exchanges per session
- Last 3 exchanges injected into LLM context

**Exception Hierarchy (`app/core/exceptions.py`)**
- `IndustrialRAGException` (base) → `VectorStoreException`, `EmbeddingException`, `RetrievalException`, `LLMException`, `ConfigurationException`, `ValidationException`

### Database Schema
Documents stored in Supabase `whdocuments` table with pgvector embeddings. The `match_whdocuments` RPC function performs similarity search (schema in `data/cmd.sql`).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for embeddings and LLM |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | Supabase service role key |
| `LOG_LEVEL` | No | DEBUG, INFO, WARNING, ERROR (default: INFO) |
| `ENABLE_WEB_SEARCH` | No | Enable DuckDuckGo fallback (default: true) |
| `MAX_WEB_SEARCHES_PER_SESSION` | No | Web search limit per session (default: 5) |
| `API_URL` | No | Backend URL for frontend (default: http://127.0.0.1:8000/chat) |

## Configuration

Retrieval parameters in `chat_engine.py`:
- `k=5`: Number of documents to retrieve
- `match_threshold=0.0`: Minimum similarity score
- `temperature=0`: LLM determinism

Hybrid retriever threshold: `min_confidence_threshold=0.5` triggers web search fallback.

---

## Planned Improvements (See IMPROVEMENT_PLAN.md)

### Phase 1: Agent Flow + Evaluation
- [ ] **LangGraph Agent** - Replace simple RAG chain with multi-tool agent
  - Tools: RAG search, Web search, Calculator
  - Files: `app/services/agent_graph.py`, `app/services/tools/*.py`
- [ ] **RAGAS Evaluation Pipeline** - Add metrics for RAG quality
  - Metrics: Faithfulness, Answer Relevancy, Context Precision/Recall
  - Files: `app/services/evaluation/`, `scripts/run_evaluation.py`
- [ ] **Evaluation Dataset** - Ground truth Q&A pairs
  - File: `data/eval_dataset.json`

### Phase 2: Observability + Architecture
- [ ] **OpenTelemetry Tracing** - Distributed tracing
  - File: `app/core/telemetry.py`
- [ ] **Structured JSON Logging** - Upgrade logging
  - File: `app/core/logging_config.py`
- [ ] **Light Architecture Refactor** - Add interfaces and DI
  - Files: `app/core/interfaces.py`, `app/core/dependencies.py`

### Phase 3: DevOps + Polish
- [ ] **GitHub Actions CI/CD** - Automated testing and linting
  - Files: `.github/workflows/ci.yml`, `Makefile`
- [ ] **Evaluation Dashboard** - Visualization in Streamlit
  - Files: `app/frontend.py`, `app/services/evaluation/visualizer.py`
- [ ] **Documentation Update** - README and docstrings

### Deferred: Azure-Based Project
- [ ] New project with Azure AI Search + Azure OpenAI (on hold)

### New Dependencies to Add
```
langgraph>=0.2.0
ragas>=0.1.0
opentelemetry-api>=1.20.0
opentelemetry-sdk>=1.20.0
opentelemetry-instrumentation-fastapi>=0.41b0
prometheus-client>=0.19.0
pydantic-settings>=2.0.0
```

### New Commands (After Implementation)
```bash
# Run RAG evaluation
python scripts/run_evaluation.py

# Run with tracing enabled
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 uvicorn app.main:app

# View metrics
curl http://localhost:8000/metrics

# Lint code
make lint

# Run CI locally
make ci
```
