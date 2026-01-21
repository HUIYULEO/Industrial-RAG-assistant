"""
RAG Tool for document search in warehouse automation knowledge base.

This tool performs semantic search against the Supabase vector store
to retrieve relevant documentation for answering user questions.
"""

import os
from typing import Any, Dict, List

from langchain_core.tools import tool
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from supabase import create_client

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Lazy initialization to avoid import-time side effects
_supabase_client = None
_embeddings = None


def _get_supabase_client():
    """Get or create Supabase client (lazy initialization)."""
    global _supabase_client
    if _supabase_client is None:
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
        if supabase_url and supabase_key:
            _supabase_client = create_client(supabase_url, supabase_key)
    return _supabase_client


def _get_embeddings():
    """Get or create embeddings model (lazy initialization)."""
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings()
    return _embeddings


def search_documents(query: str, k: int = 5) -> List[Document]:
    """
    Search warehouse automation documents using vector similarity.

    Args:
        query: Search query
        k: Number of documents to retrieve

    Returns:
        List of relevant documents with metadata
    """
    client = _get_supabase_client()
    embeddings = _get_embeddings()

    if not client or not embeddings:
        logger.error("RAG tool not properly initialized")
        return []

    try:
        # Generate query embedding
        query_vec = embeddings.embed_query(query)

        # Call Supabase RPC function
        params = {
            "query_embedding": query_vec,
            "match_threshold": 0.0,
            "match_count": k,
        }

        res = client.rpc("match_whdocuments", params).execute()
        rows = res.data or []

        documents = []
        for idx, row in enumerate(rows):
            similarity = row.get("similarity", 0.0)
            metadata = {
                "id": row.get("id"),
                "similarity": similarity,
                "rank": idx + 1,
                "source": "local_docs"
            }

            if "metadata" in row and isinstance(row["metadata"], dict):
                metadata.update(row["metadata"])

            doc = Document(
                page_content=row.get("content") or "",
                metadata=metadata
            )
            documents.append(doc)

        logger.info(f"RAG search returned {len(documents)} documents")
        return documents

    except Exception as e:
        logger.error(f"RAG search failed: {str(e)}")
        return []


@tool
def rag_search(query: str) -> str:
    """
    Search the warehouse automation documentation for relevant information.

    Use this tool when the user asks about:
    - Warehouse Control Systems (WCS)
    - Conveyor systems and configurations
    - Material handling equipment
    - Automation protocols and standards
    - Safety guidelines and compliance
    - Integration strategies

    Args:
        query: The search query about warehouse automation topics

    Returns:
        Relevant documentation excerpts with source information
    """
    documents = search_documents(query, k=5)

    if not documents:
        return "No relevant documentation found for this query."

    # Format results
    results = []
    for i, doc in enumerate(documents, 1):
        source = doc.metadata.get("source", "Unknown")
        page = doc.metadata.get("page", doc.metadata.get("page_number", "N/A"))
        similarity = doc.metadata.get("similarity", 0.0)

        results.append(
            f"[Document {i}] (Source: {source}, Page: {page}, Relevance: {similarity:.2f})\n"
            f"{doc.page_content[:1000]}..."
            if len(doc.page_content) > 1000 else
            f"[Document {i}] (Source: {source}, Page: {page}, Relevance: {similarity:.2f})\n"
            f"{doc.page_content}"
        )

    return "\n\n---\n\n".join(results)
