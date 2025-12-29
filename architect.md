┌───────────────────────────────┐
│ Client/UI │
│ Web / App / Slack / API Caller│
└───────────────┬───────────────┘
│ 1) question
▼
┌───────────────────────────────┐
│ Backend API / Service │
│ (FastAPI/Flask/Worker/etc.) │
│ - auth / rate limit / logging │
│ - get_chat_response() │
└───────────────┬───────────────┘
│ 2) embed query
▼
┌───────────────────────────────┐
│ Embeddings Service │
│ OpenAIEmbeddings.embed_query │
└───────────────┬───────────────┘
│ 3) query_embedding (vector)
▼
┌───────────────────────────────┐
│ Retriever (Adapter Layer) │
│ SupabaseRPCRetriever │
│ - calls supabase.rpc(...) │
│ - returns List[Document] │
└───────────────┬───────────────┘
│ 4) RPC call
▼
┌────────────────────────────────────────────────────┐
│ Supabase / PostgREST │
│ POST /rest/v1/rpc/match_whdocuments │
└───────────────┬────────────────────────────────────┘
│ 5) executes SQL function
▼
┌────────────────────────────────────────────────────┐
│ Postgres + pgvector │
│ Table: whdocuments │
│ Function: match_whdocuments(query_embedding, ...) │
│ - vector similarity search │
│ - returns top-k chunks (+ metadata, score) │
└───────────────┬────────────────────────────────────┘
│ 6) rows (chunks)
▼
┌───────────────────────────────┐
│ RAG Chain (LangChain) │
│ create_retrieval_chain( │
│ retriever, │
│ create_stuff_documents_chain│
│ ) │
│ - formats {context} │
│ - prompts LLM │
└───────────────┬───────────────┘
│ 7) answer
▼
┌───────────────────────────────┐
│ Backend API / Service │
│ returns {"answer": "..."} │
└───────────────┬───────────────┘
│ 8) response
▼
┌───────────────────────────────┐
│ Client/UI │
└───────────────────────────────┘
