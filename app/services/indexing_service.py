"""Indexes parsed chunks while preserving version-level failure information."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.models import DocumentChunk, DocumentVersion
from app.repositories.milvus_repository import MilvusChunkRepository
from app.services.embedding_service import EmbeddingService


class DocumentIndexingService:
    def __init__(self, db: Session, embeddings: EmbeddingService, repository: MilvusChunkRepository):
        self.db = db
        self.embeddings = embeddings
        self.repository = repository

    def index_document_version(self, document_version_id: str) -> DocumentVersion:
        statement = (
            select(DocumentVersion)
            .options(selectinload(DocumentVersion.document), selectinload(DocumentVersion.chunks))
            .where(DocumentVersion.id == document_version_id)
        )
        version = self.db.scalar(statement)
        if version is None:
            raise LookupError("Document version not found")
        if not version.chunks:
            raise ValueError("Document must be parsed before it can be indexed")

        version.ingestion_status = "indexing"
        version.ingestion_error = None
        self.db.commit()
        try:
            chunks = sorted(version.chunks, key=lambda item: item.chunk_index)
            vectors = self.embeddings.embed_documents([chunk.content for chunk in chunks])
            if len(vectors) != len(chunks):
                raise ValueError("Embedding provider returned an unexpected number of vectors")
            records = [
                {
                    "chunk_id": chunk.id,
                    "document_version_id": version.id,
                    "document_title": version.document.title,
                    "document_type": version.document.document_type,
                    "version": version.version,
                    "system": version.document.system,
                    "page": chunk.page,
                    "section": chunk.section or "",
                    "content": chunk.content,
                    "dense_vector": vector,
                }
                for chunk, vector in zip(chunks, vectors)
            ]
            self.repository.replace_document_version(records)
            version.ingestion_status = "indexed"
            self.db.commit()
        except Exception as exc:
            version.ingestion_status = "index_failed"
            version.ingestion_error = str(exc)
            self.db.commit()
            raise
        self.db.refresh(version)
        return version
