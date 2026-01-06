# Warehouse Automation Design Decision Assistant 🏭

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128.0-009688.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-0.3.27-00C853.svg)](https://langchain.com/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED.svg)](https://www.docker.com/)

## 🎯 Project Overview

An **enterprise-grade AI-powered technical assistant** that helps engineers and decision-makers with warehouse automation design decisions. Built on a production-ready RAG (Retrieval-Augmented Generation) architecture, this system transforms complex industrial documentation into conversational, intelligent decision support.

### Key Capabilities

- 🔍 **Intelligent Document Search** - Semantic search across warehouse control system manuals
- 💬 **Conversational Memory** - Context-aware responses that remember previous questions
- 📊 **Confidence Scoring** - Answer relevance evaluation with transparency
- 🛡️ **Enterprise Error Handling** - Comprehensive logging and exception management
- 🎨 **Interactive UI** - Real-time chat interface with session management
- 🔒 **Production-Ready** - Docker containerization with microservices architecture

---

## 🚀 Features

### 1. **Advanced Error Handling & Logging**

- Centralized logging configuration with rotating file handlers
- Custom exception hierarchy for different failure modes
- Request tracing with session IDs
- Detailed error responses with diagnostic information

### 2. **Conversation Memory**

- Session-based chat history tracking
- Context-aware responses considering previous exchanges
- Sliding window memory (last 10 exchanges)
- Session management endpoints (view/delete history)

### 3. **Answer Evaluation Metrics**

- **Confidence Scoring**: Weighted similarity scores from retrieved documents
- **Source Attribution**: Automatic citation of source documents and page numbers
- **Retrieval Statistics**: Number of chunks retrieved and relevance metrics

### 4. **Production-Ready API**

- FastAPI with automatic OpenAPI documentation
- Request validation with Pydantic schemas
- Health check endpoints
- Graceful error handling with proper HTTP status codes

### 5. **Specialized for Warehouse Automation**

- Domain-specific prompts for warehouse control systems
- Technical terminology understanding
- Integration recommendations
- Safety and compliance awareness

---

## 📁 Project Structure

```
industrial-rag/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI application & endpoints
│   ├── schema.py                    # Pydantic request/response models
│   ├── frontend.py                  # Streamlit UI
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                # Configuration management
│   │   ├── logging_config.py        # Centralized logging setup
│   │   └── exceptions.py            # Custom exception classes
│   └── services/
│       ├── __init__.py
│       ├── chat_engine.py           # RAG pipeline & LLM orchestration
│       └── vector_store.py          # Supabase vector store interface
├── scripts/
│   ├── __init__.py
│   ├── ingest.py                    # PDF ingestion pipeline
│   ├── test_chat.py                 # CLI testing utility
│   └── debug_env.py                 # Database debugging tool
├── tests/
│   ├── __init__.py
│   ├── test_main.py                 # API endpoint tests
│   ├── test_chat_engine.py          # RAG pipeline tests
│   └── conftest.py                  # Pytest fixtures
├── data/
│   ├── *.pdf                        # Source documentation
│   └── cmd.sql                      # Database schema
├── logs/                            # Application logs (auto-generated)
├── docker-compose.yml               # Service orchestration
├── Dockerfile.backend               # Backend container
├── Dockerfile.frontend              # Frontend container
├── requirements.txt                 # Python dependencies
├── .env                             # Environment variables (not in git)
└── README.md                        # This file
```

---

## ⚙️ Installation & Setup

### Prerequisites

- **Docker Desktop** (recommended) or Python 3.10+
- **Git**
- **OpenAI API Key** ([Get one here](https://platform.openai.com/api-keys))
- **Supabase Account** ([Sign up here](https://supabase.com/))

### Option 1: Docker Deployment (Recommended)

1. **Clone the repository**

   ```bash
   git clone https://github.com/HUIYULEO/Industrial-RAG-assistant.git
   cd industrial-rag
   ```

2. **Configure environment variables**

   Create a `.env` file in the root directory:

   ```env
   # OpenAI Configuration
   OPENAI_API_KEY=sk-your-key-here

   # Supabase Configuration
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_SERVICE_KEY=your-service-key-here

   # Optional: Logging Level (DEBUG, INFO, WARNING, ERROR)
   LOG_LEVEL=INFO

   # Frontend API URL (for container networking)
   API_URL=http://backend:8000/chat
   ```

3. **Set up Supabase database**

   Run the SQL schema in your Supabase SQL editor:

   ```bash
   # The schema is in data/cmd.sql
   # This creates the whdocuments table and match_whdocuments RPC function
   ```

4. **Ingest your documents** (one-time setup)

   ```bash
   # Install dependencies locally or use a temporary container
   python scripts/ingest.py
   ```

5. **Launch the application**

   ```bash
   docker-compose up --build
   ```

6. **Access the application**
   - **Frontend UI**: http://localhost:3000
   - **Backend API**: http://localhost:8000
   - **API Documentation**: http://localhost:8000/docs

### Option 2: Local Development

1. **Create virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables** (same as above)

4. **Run backend**

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

5. **Run frontend** (in another terminal)
   ```bash
   streamlit run app/frontend.py --server.port 3000
   ```

---

## 🧪 Testing

Run the test suite:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_chat_engine.py

# Run with verbose output
pytest -v
```

---

## 📊 API Documentation

### Main Endpoints

#### `POST /chat`

Send a question and receive an AI-generated answer.

**Request:**

```json
{
  "question": "What are the key components of a warehouse control system?",
  "session_id": "user_123"
}
```

**Response:**

```json
{
  "answer": "A warehouse control system (WCS) consists of several key components...",
  "sources": [
    "FS_Warehouse-control-system_EN.pdf - Page 12",
    "FS_Warehouse-control-system_EN.pdf - Page 15"
  ],
  "confidence_score": 0.89,
  "retrieved_chunks": 5
}
```

#### `GET /sessions/{session_id}/history`

Retrieve conversation history for a session.

#### `DELETE /sessions/{session_id}`

Delete a session and its history.

#### `GET /`

Health check endpoint.

**Interactive API Docs**: Visit http://localhost:8000/docs for Swagger UI.

---

## 📝 Logging

Logs are automatically saved to `logs/app_YYYYMMDD.log` with the following levels:

- **DEBUG**: Detailed diagnostic information
- **INFO**: General informational messages
- **WARNING**: Warning messages for potential issues
- **ERROR**: Error messages with stack traces

Configure log level via `LOG_LEVEL` environment variable.

---

## 🔧 Configuration

### Environment Variables

| Variable               | Description                           | Required | Default                      |
| ---------------------- | ------------------------------------- | -------- | ---------------------------- |
| `OPENAI_API_KEY`       | OpenAI API key for embeddings and LLM | ✅ Yes   | -                            |
| `SUPABASE_URL`         | Supabase project URL                  | ✅ Yes   | -                            |
| `SUPABASE_SERVICE_KEY` | Supabase service role key             | ✅ Yes   | -                            |
| `LOG_LEVEL`            | Logging verbosity                     | ❌ No    | `INFO`                       |
| `API_URL`              | Backend API URL for frontend          | ❌ No    | `http://127.0.0.1:8000/chat` |

### Retrieval Configuration

Edit `app/services/chat_engine.py` to adjust:

- `k`: Number of documents to retrieve (default: 5)
- `match_threshold`: Minimum similarity score (default: 0.0)
- `temperature`: LLM creativity (default: 0 for deterministic)

---

## 🎯 Use Cases

### For Engineers

- "What safety protocols should I consider for conveyor system design?"
- "How do I integrate a new WMS with the existing WCS?"
- "What are the load capacity specifications for Zone 3 conveyors?"

### For Project Managers

- "What are the key decision points in warehouse automation design?"
- "What compliance standards apply to automated storage systems?"

### For System Integrators

- "How do I configure communication protocols between WCS and PLC?"
- "What are the recommended redundancy strategies?"

---

## 🛠️ Troubleshooting

### Common Issues

**Issue**: `ConfigurationException: Missing environment variables`

- **Solution**: Ensure `.env` file exists with all required variables

**Issue**: `RetrievalException: Document retrieval failed`

- **Solution**: Verify Supabase credentials and check if documents are ingested

**Issue**: Frontend can't connect to backend

- **Solution**: Check `API_URL` in `.env` (use `http://backend:8000/chat` for Docker)

**Issue**: No documents retrieved for questions

- **Solution**: Run `scripts/ingest.py` to populate the vector database

### Debug Mode

Enable debug logging:

```bash
export LOG_LEVEL=DEBUG
```

Check logs in `logs/app_YYYYMMDD.log` for detailed traces.

---

## 🚀 Deployment Considerations

### For Production

1. **Security**

   - Use environment variable management (AWS Secrets Manager, Vault)
   - Enable HTTPS/TLS
   - Implement rate limiting
   - Add authentication (OAuth2, JWT)

2. **Scalability**

   - Use Redis for session storage instead of in-memory dict
   - Deploy on Kubernetes for auto-scaling
   - Implement caching for frequently asked questions
   - Use connection pooling for Supabase

3. **Monitoring**

   - Integrate with monitoring tools (Prometheus, Grafana)
   - Set up alerts for error rates and latency
   - Track confidence scores and user feedback

4. **Cost Optimization**
   - Monitor OpenAI API usage
   - Implement response caching
   - Use GPT-3.5-turbo for simple queries

---

## 📚 Technology Stack

| Component               | Technology                       | Purpose                  |
| ----------------------- | -------------------------------- | ------------------------ |
| **Backend Framework**   | FastAPI 0.128.0                  | Async REST API           |
| **Frontend**            | Streamlit 1.50.0                 | Interactive UI           |
| **LLM**                 | OpenAI GPT-4o                    | Answer generation        |
| **Embeddings**          | OpenAI text-embedding-ada-002    | Semantic search          |
| **Vector DB**           | Supabase (PostgreSQL + pgvector) | Similarity search        |
| **RAG Framework**       | LangChain 0.3.27                 | Pipeline orchestration   |
| **Document Processing** | PyPDF 6.5.0                      | PDF text extraction      |
| **Containerization**    | Docker + Docker Compose          | Deployment               |
| **Testing**             | Pytest                           | Unit & integration tests |
| **Logging**             | Python logging                   | Centralized logging      |

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 👨‍💻 Author

**HUIYULEO**

- GitHub: [@HUIYULEO](https://github.com/HUIYULEO)
- Project: [Industrial-RAG-assistant](https://github.com/HUIYULEO/Industrial-RAG-assistant)

---

## 🙏 Acknowledgments

- OpenAI for GPT-4o and embedding models
- LangChain for RAG framework
- Supabase for managed PostgreSQL with pgvector
- FastAPI and Streamlit communities

---

**Ready to transform your warehouse automation documentation into intelligent decision support? Get started now! 🚀**
