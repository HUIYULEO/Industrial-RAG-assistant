"""Stable retrieval boundary for the RAG-first Design Review.

Only this module knows about Milvus. Chat and coverage-analysis services use
the protocol, which keeps them testable and makes later enterprise-search
integration an adapter change instead of an application rewrite.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.evidence import EvidenceChunk, RetrievalFilters
from app.repositories.milvus_repository import MilvusChunkRepository
from app.services.embedding_service import EmbeddingService


class RetrievalService(Protocol):
    """Search citable chunks in an explicitly selected document-version scope."""

    def retrieve(self, query: str, filters: RetrievalFilters, limit: int = 8) -> list[EvidenceChunk]: ...


class MilvusRetrievalService:
    """Hybrid search guarded by immutable document-version filters."""

    def __init__(self, *, repository: MilvusChunkRepository, embeddings: EmbeddingService):
        self.repository = repository
        self.embeddings = embeddings

    def retrieve(self, query: str, filters: RetrievalFilters, limit: int = 8) -> list[EvidenceChunk]:
        if not query.strip():
            raise ValueError("A retrieval query is required")
        if not filters.document_version_ids:
            raise ValueError("Retrieval requires at least one selected document version")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return self.repository.hybrid_search(
            query_text=query,
            query_vector=self.embeddings.embed_query(query),
            filters=filters,
            limit=limit,
        )
