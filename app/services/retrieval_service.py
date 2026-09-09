"""Stable retrieval boundary for the RAG-first Design Review.

Only this module knows about Milvus. Chat and coverage-analysis services use
the protocol, which keeps them testable and makes later enterprise-search
integration an adapter change instead of an application rewrite.
"""

from __future__ import annotations

from typing import Protocol
from time import perf_counter

from app.domain.evidence import EvidenceChunk, RetrievalFilters
from app.domain.ports import DocumentChunkIndex
from app.services.embedding_service import EmbeddingService
from app.services.trace_capture import record, record_snapshot


class RetrievalService(Protocol):
    """Search citable chunks in an explicitly selected document-version scope."""

    def retrieve(self, query: str, filters: RetrievalFilters, limit: int = 8) -> list[EvidenceChunk]: ...


class HybridRetrievalService:
    """Hybrid search guarded by immutable document-version filters."""

    def __init__(self, *, repository: DocumentChunkIndex, embeddings: EmbeddingService):
        self.repository = repository
        self.embeddings = embeddings

    def retrieve(self, query: str, filters: RetrievalFilters, limit: int = 8) -> list[EvidenceChunk]:
        return self._retrieve(query, filters, limit=limit)

    def retrieve_candidates(self, query: str, filters: RetrievalFilters, *, limit: int,
                            pool_limit: int) -> list[EvidenceChunk]:
        """Expose more fused hits without increasing the two branch budgets."""
        if pool_limit < limit:
            raise ValueError("pool_limit must be at least the original result limit")
        return self._retrieve(query, filters, limit=pool_limit, candidate_limit=max(limit * 3, 15))

    def _retrieve(self, query: str, filters: RetrievalFilters, *, limit: int,
                  candidate_limit: int | None = None) -> list[EvidenceChunk]:
        if not query.strip():
            raise ValueError("A retrieval query is required")
        if not filters.document_version_ids:
            raise ValueError("Retrieval requires at least one selected document version")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        started = perf_counter()
        query_vector = self.embeddings.embed_query(query)
        record("embedding", {"query": query,
                             "model_info": getattr(self.embeddings, "model_info", {}),
                             "dimensions": len(query_vector),
                             "duration_ms": round((perf_counter() - started) * 1000, 3)})
        record_snapshot("query_vector", {"query": query, "vector": query_vector})
        return self.repository.hybrid_search(
            query_text=query,
            query_vector=query_vector,
            filters=filters,
            limit=limit,
            **({"candidate_limit": candidate_limit} if candidate_limit is not None else {}),
        )
