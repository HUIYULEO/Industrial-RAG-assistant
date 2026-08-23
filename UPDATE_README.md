# Warehouse Automation Design Review Assistant

> A local, version-aware RAG workspace for engineering review of warehouse-automation supplier design documents.

This document describes the current implementation of the controlled design-review workspace.

## 1. Purpose and operating boundary

The assistant helps automation engineers review supplier Functional Specifications (FS) and Design Specifications (DS) against controlled User Requirements Specifications (URS) and Engineering Specifications (ES).

It is an engineering support tool. It does **not** approve, reject, release, or certify a design, supplier, document, or GxP decision. Its outputs are candidate findings and citable evidence that an engineer must review.

The current scope is intentionally local and document-grounded:

- no open-web search;
- no autonomous agent workflow;
- no automatic visual-diagram interpretation;
- English responses, with Chinese or English questions accepted;
- one frozen Review Package at a time for a controlled evidence scope.

## 2. Engineering workflow

```text
Controlled URS / ES requirement table
              |
              v
Requirement Baseline ────────────────┐
                                      |
Supplier FS / DS versions             |
  -> parse -> inspect -> index        |
                                      v
                         Frozen Review Package
                                      |
                    ┌─────────────────┴─────────────────┐
                    v                                   v
             Design Assistant                    Design Review
           Evidence-grounded Q&A              Coverage / finding matrix
```

### 2.1 Import the requirement baseline

For local testing, import a UTF-8 CSV export of the controlled URS or ES table. The application creates the baseline and its traceable requirements directly; it does not require the engineer to manually create an empty baseline first.

The following headers are supported, including the familiar bilingual table form:

```csv
序号,系统,requirement,reasonal/impact,是否critical
1,Fleet Manager,The system shall prevent dispatch to unavailable AGVs.,Patient safety and operational continuity,是
2,Fleet Manager,The system shall retain task status history.,Deviation investigation support,否
```

The importer preserves the source row, system, requirement text, rationale/impact, and critical flag. It generates a stable requirement identifier such as `URS-001` from the serial number when no identifier is supplied.

### 2.2 Register supplier design versions

Register each FS or DS with document title, type, supplier, version, and an optional controlled-repository URL. Upload one of the supported source formats:

- PDF
- DOCX
- CSV

Each version remains distinct. A newer version can explicitly supersede an earlier version without overwriting it.

### 2.3 Parse, inspect, and index

Parsing is deliberately separate from indexing:

1. **Register and parse** stores the source and creates citable chunks.
2. **Preview parsed structure** lets an engineer inspect the extraction.
3. **Create retrieval index** writes the approved parsed chunks to Milvus.

This makes the ingestion path inspectable before material becomes searchable.

### 2.4 Create a Review Package

A Review Package freezes one requirement baseline and one or more FS/DS versions. Design Assistant and Design Review operate only inside the selected package. This prevents an answer from silently mixing supplier versions or unrelated systems.

## 3. Current product surfaces

### Design Assistant

The chat retrieves evidence only from the active package's indexed FS/DS versions. It uses the active system and document-version filters, returns answers in English, and displays the supporting citations in the Answer Inspector.

This is not a general-purpose chatbot. If no Review Package exists, the assistant remains unavailable rather than producing unscoped answers.

### Design Review

The review workspace runs a requirement-by-requirement evidence check across the frozen package. It produces a candidate finding matrix with:

- requirement identifier and source text;
- evidence-backed coverage status;
- rationale and any evidence gap;
- suggested reviewer action;
- frozen citations to supplier document version, section, and page.

Candidate findings are prompts for engineering review, not release or compliance decisions.

### Knowledge & Requirements

The workspace presents the controlled sequence:

1. URS/ES baseline;
2. supplier FS/DS source versions;
3. frozen Review Package.

The left navigation and lower user area are product navigation only. The local account flow supports email/password registration and sign-in; corporate SSO is intentionally not included yet.

## 4. Retrieval and evidence architecture

| Concern | Current implementation |
|---|---|
| Relational source of truth | PostgreSQL |
| Vector and keyword index | Milvus |
| Dense retrieval | OpenAI embeddings |
| Exact terminology retrieval | Milvus BM25 sparse index |
| Fusion | Reciprocal Rank Fusion (RRF) inside Milvus |
| Scope control | Review Package document IDs, system, and FS/DS type metadata |
| Generated answer | OpenAI model with structured response and citation-ID validation |
| Citation provenance | Document title, version, page, section, and excerpt |

The retrieval layer is intentionally scoped. It cannot search documents outside the selected Review Package.

## 5. Document parsing

| Source | Current parsing behaviour |
|---|---|
| PDF | Text by page, with heading-aware splitting where headings are detectable |
| DOCX | Headings, paragraphs, and individual table rows are preserved as structural chunks |
| CSV | Each data row becomes a citable chunk |
| URS/ES CSV | Table fields are imported into structured requirements rather than only embedded as text |

### Visual source pages

PDF pages likely to contain flow diagrams, interface drawings, or data-flow diagrams are locally rendered and retained as source evidence. Engineers can open these pages from the document workspace.

Visual content is **not automatically interpreted or added to retrieval** in the normal configuration. The codebase retains an opt-in visual-analysis path for future experimentation, but it is disabled by default:

```env
ENABLE_VISUAL_ANALYSIS=false
```

The expected current behaviour is therefore: preserve the original diagram page, page number, and document version for human review; do not claim that the system has understood arrows, states, or interface directions.

## 6. Local deployment

### Prerequisites

- Docker Desktop
- An OpenAI API key for embeddings, grounded answers, and coverage analysis

### Configure the environment

Create `.env` from the example and set a local secret before sharing the workspace.

```powershell
Copy-Item .env.example .env
```

Minimum settings:

```env
OPENAI_API_KEY=your-key
AUTH_REQUIRED=true
AUTH_SECRET=use-a-unique-long-random-secret
ALLOW_SELF_REGISTRATION=true
ENABLE_VISUAL_ANALYSIS=false
```

### Start the services

```powershell
docker compose up --build
```

Services:

| Service | URL / port |
|---|---|
| Next.js workspace | http://localhost:3000 |
| FastAPI API | http://localhost:8000 |
| API documentation | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| Milvus | localhost:19530 |
| Redis / RQ queue | localhost:6379 |

Each frozen URS/ES item is enqueued as an independent Redis/RQ job. The
`analysis-worker` service can be scaled for parallel retrieval and judging:

```powershell
docker compose up --build --scale analysis-worker=3
```

The API returns immediately after queueing. Use
`GET /analysis-runs/{id}/progress` for polling or
`GET /analysis-runs/{id}/events` for Server-Sent Events. Failed items retain
their errors and can be requeued without rerunning completed items via
`POST /analysis-runs/{id}/retry`.

## 7. Recommended first data set

Use one coherent system rather than unrelated documents. An AGV Fleet Manager / WCS package is a suitable starting point:

1. one URS or ES requirement-table CSV with 20–50 rows;
2. one supplier FS and one DS for the same system;
3. a few known questions or expected gaps, such as a missing exception-recovery description;
4. the selected document versions entered into one Review Package.

This is enough to exercise version-aware Q&A, evidence citations, and URS/ES-to-FS/DS review without treating the workspace as a formal approval system.

## 8. Testing

Run tests inside the local backend container:

```powershell
docker compose exec -T backend python -m pytest
```

Focused review-workflow tests:

```powershell
docker compose exec -T backend python -m pytest tests/test_review_api.py tests/test_auth.py -q
```

The test suite covers account access, requirement-table import, document versioning, PDF/DOCX/CSV parsing, visual-page preservation, Review Package creation, and candidate-finding persistence.

## 9. Deliberate limitations and next decisions

The following are known boundaries, not hidden behaviour:

- The chat retrieves supplier FS/DS evidence; URS/ES is currently used as the requirement baseline for the review matrix. A future enhancement can make both sides directly citable in chat.
- Scanned PDFs require OCR; the current text parser does not perform OCR.
- The interface preserves diagram-bearing pages but does not claim to semantically understand diagrams.
- There is no terminology glossary service yet. A maintained bilingual/domain glossary is preferred over opaque LLM query translation.
- There is no Cross-Encoder reranker. Dense + BM25 + RRF is the current retrieval baseline.
- Quality-document-library integration and corporate SSO are future enterprise integrations.
- The project does not replace a controlled QA, GxP, or FDA approval workflow.

## 10. Key implementation locations

| Area | Location |
|---|---|
| FastAPI entry point | `app/main.py` |
| Workspace UI | `app/frontend.py` |
| Document parser | `app/services/ingestion_service.py` |
| Requirement and Review Package workflow | `app/services/review_service.py` |
| Coverage analysis | `app/services/coverage_service.py` |
| Scoped evidence chat | `app/services/design_review_chat_service.py` |
| Dense + BM25 hybrid retrieval | `app/repositories/milvus_repository.py` |
| Visual-page preservation / dormant visual analysis | `app/services/visual_evidence_service.py` |
| Local Docker topology | `docker-compose.yml` |
| Next.js frontend | `frontend-next/` |
