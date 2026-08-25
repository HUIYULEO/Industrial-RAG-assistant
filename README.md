# Industrial AI Technical Assistant (RAG Pipeline)

## 🚀 Project Overview

An AI-powered technical support agent designed to assist engineers in querying complex industrial documentation (PDFs). Built with a microservices architecture, this system ingests unstructured data, generates vector embeddings, and serves answers via a RAG (Retrieval-Augmented Generation) pipeline.

**Tech Stack:** Python, FastAPI, Streamlit, LangChain, PostgreSQL (pgvector), Docker.

## 🏗️ Architecture

The system follows a containerized microservices pattern:

- **Ingestion Service:** ETL pipeline that chunks PDF manuals and stores semantic embeddings in Supabase.
- **Vector Database:** PostgreSQL with `pgvector` for cosine similarity search (1536 dimensions).
- **Backend API:** FastAPI service handling asynchronous query processing and LLM context injection.
- **Frontend:** Streamlit interface for real-time user interaction.
- **Infrastructure:** Fully containerized using Docker & Docker Compose.

## 🛠️ How to Run

Prerequisites: Docker Desktop & Git.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/HUIYULEO/Industrial-RAG-assistant.git
   cd industrial-rag
   ```
2. **Set up Environment: Create a .env file with your credentials:**
   ```env
   OPENAI_API_KEY=sk-...
   SUPABASE_URL=...
   SUPABASE_SERVICE_KEY=...
   ```
3. **Deploy:**
   ```bash
   docker-compose up --build
   ```
   Access the UI at http://localhost:3000
   
## What I Learned
Working on this project deepened my understanding of:
- Vector embeddings and similarity search
- LLM prompt engineering and context management
- RAG architecture and retrieval optimization

## V2 Roadmap
Currently working on V2:
- add LangGraph Agent Flow with three tools: RAG Search, Web Search, Calculator
- Improved chunking strategies
- Hybrid search (semantic + keyword)
- Add RAG Evaluation Pipeline 
