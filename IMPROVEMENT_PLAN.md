# Industrial RAG Project Improvement Plan

## Objective
Transform this project into a portfolio-ready demonstration that aligns with common AI Engineer job requirements across multiple companies (based on 7 JD analysis).

---

## Part 1: Upgrade Current Project (industrial-rag)

### Priority 1: Add LangGraph Agent Flow (HIGH IMPACT)
**Why:** Agent flows mentioned in 4/7 JDs (PFA, GF, Leo Pharma, Wonderful)

**What is an Agent?**
Currently your RAG works: `Question → Retrieve → Answer`

An Agent decides WHICH action to take:
```
Question → Agent thinks → [Choose Tool] → Execute → Combine → Answer
```

**Tools to implement (confirmed):**
1. **RAG Tool** - Search PDF documents (existing capability)
2. **Web Search Tool** - Search internet via DuckDuckGo (existing capability)
3. **Calculator Tool** - Math calculations (e.g., "transit time = distance/speed")

**Implementation:**
- Replace simple RAG chain with LangGraph stateful agent
- Add multi-step reasoning with tool use
- Implement router node for query classification

**Files to create/modify:**
- `app/services/agent_graph.py` - LangGraph agent definition
- `app/services/tools/__init__.py` - Tool registry
- `app/services/tools/rag_tool.py` - Document search tool
- `app/services/tools/web_tool.py` - Web search tool
- `app/services/tools/calculator_tool.py` - Math calculations
- `app/services/chat_engine.py` - Integrate agent graph

**Agent Flow Diagram:**
```
User Query
    │
┌───────────────┐
│  Router Node  │ ← Classifies query type
└───────────────┘
    │
┌───────────────┐
│  Tool Select  │ ← Picks appropriate tool(s)
└───────────────┘
    │
┌─────┬─────┬──────────┐
│ RAG │ Web │Calculator│
└─────┴─────┴──────────┘
    │
┌───────────────┐
│   Synthesize  │ ← Combine tool outputs
└───────────────┘
    │
  Final Answer
```

---

### Priority 2: Add RAG Evaluation Pipeline (HIGH IMPACT)
**Why:** Evaluation/monitoring mentioned in 5/7 JDs

**Implementation:**
- Add RAGAS evaluation metrics (faithfulness, answer relevancy, context precision)
- Create evaluation dataset with ground truth Q&A pairs
- Generate evaluation reports with statistical analysis

**Files to create:**
- `app/services/evaluation/` - Evaluation module
  - `metrics.py` - RAGAS metric implementations
  - `evaluator.py` - Evaluation pipeline
  - `dataset.py` - Test dataset management
- `scripts/run_evaluation.py` - CLI for running evaluations
- `data/eval_dataset.json` - Ground truth Q&A pairs

**Metrics to implement:**
- Faithfulness (answer grounded in context)
- Answer Relevancy (answer addresses question)
- Context Precision (retrieved docs are relevant)
- Context Recall (all relevant info retrieved)

---

### Priority 3: Add Observability (Telemetry, Tracing, Logging)
**Why:** Mentioned in 4/7 JDs (PFA, GF, Wonderful, Jyske Bank)

**Implementation:**
- Add OpenTelemetry for distributed tracing
- Implement structured JSON logging
- Add Prometheus metrics endpoint
- Create simple dashboard/visualization

**Files to create/modify:**
- `app/core/telemetry.py` - OpenTelemetry setup
- `app/core/logging_config.py` - Upgrade to structured logging
- `app/core/metrics.py` - Prometheus metrics
- `app/main.py` - Add `/metrics` endpoint

**Metrics to track:**
- Request latency (p50, p95, p99)
- Token usage per request
- Retrieval confidence distribution
- Error rates by type
- Agent step counts

---

### Priority 4: Improve Architecture - Light Refactor (CONFIRMED)
**Why:** Clean Architecture mentioned in 2/7 JDs, good engineering practices in all

**Scope:** Light refactor - add interfaces and DI while keeping existing structure

**Implementation:**
- Add abstract base classes (interfaces) for key services
- Implement dependency injection with FastAPI Depends
- Add Pydantic Settings for configuration management
- Keep existing folder structure mostly intact

**Changes:**
```
app/
├── core/
│   ├── config.py          # ADD: Pydantic Settings class
│   ├── interfaces.py      # ADD: Abstract base classes
│   ├── dependencies.py    # ADD: FastAPI dependency injection
│   └── ...existing...
├── services/
│   ├── chat_engine.py     # MODIFY: Implement interface
│   ├── vector_store.py    # MODIFY: Implement interface
│   └── ...existing...
└── main.py                # MODIFY: Use DI for services
```

**Example interface:**
```python
# app/core/interfaces.py
from abc import ABC, abstractmethod

class ChatServiceInterface(ABC):
    @abstractmethod
    async def get_response(self, question: str, history: list) -> dict:
        pass

class RetrieverInterface(ABC):
    @abstractmethod
    def retrieve(self, query: str, k: int) -> list:
        pass
```

---

### Priority 5: Add CI/CD Pipeline
**Why:** CI/CD mentioned in 5/7 JDs

**Implementation:**
- GitHub Actions workflow for testing
- Automated linting (ruff, mypy)
- Docker build and push
- Evaluation pipeline in CI

**Files to create:**
- `.github/workflows/ci.yml` - Main CI pipeline
- `.github/workflows/evaluation.yml` - Scheduled evaluation runs
- `Makefile` - Common commands

---

### Priority 6: Add Data Visualization Dashboard
**Why:** Data visualization mentioned in JD1, evaluation dashboards useful for all

**Implementation:**
- Streamlit dashboard for evaluation results
- Charts for retrieval performance over time
- Token usage and cost tracking visualization

**Files to create/modify:**
- `app/frontend.py` - Add evaluation dashboard tab
- `app/services/evaluation/visualizer.py` - Chart generation

---

## Part 2: New Azure-Based Project (DEFERRED)

**Status:** On hold until user notification to begin design

**Planned scope:**
- Azure AI Search for vector store
- Azure OpenAI for embeddings and LLM
- Azure Application Insights for monitoring
- Full evaluation pipeline with Azure ML integration

---

## Implementation Order

### Phase 1: Agent Flow + Evaluation (Core differentiators)
1. Implement LangGraph agent with tools
2. Add RAGAS evaluation pipeline
3. Create evaluation dataset

### Phase 2: Observability + Architecture
4. Add OpenTelemetry tracing
5. Upgrade to structured logging
6. Refactor to Clean Architecture

### Phase 3: DevOps + Polish
7. Add GitHub Actions CI/CD
8. Add evaluation dashboard
9. Update documentation

---

## Files to Modify (Existing)

| File | Changes |
|------|---------|
| `app/services/chat_engine.py` | Integrate LangGraph agent |
| `app/core/logging_config.py` | Structured JSON logging |
| `app/main.py` | Add metrics endpoint, DI |
| `app/schema.py` | Add evaluation schemas |
| `requirements.txt` | Add langgraph, ragas, opentelemetry |
| `docker-compose.yml` | Add Prometheus/Grafana (optional) |

## Files to Create (New)

| File | Purpose |
|------|---------|
| `app/services/agent_graph.py` | LangGraph agent definition |
| `app/services/tools/*.py` | Agent tools |
| `app/services/evaluation/metrics.py` | RAGAS metrics |
| `app/services/evaluation/evaluator.py` | Evaluation runner |
| `app/core/telemetry.py` | OpenTelemetry setup |
| `app/core/metrics.py` | Prometheus metrics |
| `app/core/interfaces.py` | Abstract base classes |
| `app/core/dependencies.py` | FastAPI DI |
| `data/eval_dataset.json` | Ground truth Q&A |
| `.github/workflows/ci.yml` | CI pipeline |
| `scripts/run_evaluation.py` | Evaluation CLI |
| `Makefile` | Common commands |

---

## Verification Plan

1. **Agent Flow:** Run example queries showing multi-step reasoning
2. **Evaluation:** Run `python scripts/run_evaluation.py` and verify metrics output
3. **Observability:** Check `/metrics` endpoint returns Prometheus format
4. **CI/CD:** Push to GitHub and verify Actions pass
5. **Tests:** Run `pytest --cov=app` and verify >70% coverage

---

## Technology Stack Additions

| Technology | Purpose | JD Alignment |
|------------|---------|--------------|
| LangGraph | Agent orchestration | PFA, Leo Pharma |
| RAGAS | RAG evaluation | GF, PFA |
| OpenTelemetry | Distributed tracing | PFA, GF, Wonderful |
| Prometheus | Metrics collection | DevOps best practice |
| GitHub Actions | CI/CD | 5/7 JDs |
| Pydantic Settings | Config management | Clean code |

---

## Expected Outcome

After implementation, this project will demonstrate:
- **Agentic AI:** Multi-step reasoning with LangGraph
- **RAG Expertise:** Evaluation metrics, optimization strategies
- **Production Readiness:** Observability, CI/CD, clean architecture
- **Engineering Quality:** Testing, documentation, structured code

This positions you well for all 7 JDs analyzed, with particular strength for:
- PFA (agent flows, RAG, observability)
- GF Forsikring (GenAI production, RAG, monitoring)
- Nykredit (RAG strategies, prompt engineering, CI/CD)
