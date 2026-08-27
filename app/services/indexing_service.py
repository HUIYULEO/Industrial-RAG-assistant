"""Indexes parsed chunks while preserving version-level failure information."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic, sleep

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.models import DocumentChunk, DocumentVersion
from app.domain.ports import DocumentChunkIndex
from app.services.embedding_service import EmbeddingService


@dataclass(frozen=True)
class EmbeddingBatch:
    """Texts grouped for one provider request together with their token estimate."""

    texts: list[str]
    token_count: int


class DocumentIndexingService:
    """Index document chunks without exceeding embedding-provider throughput limits."""

    def __init__(
        self,
        db: Session,
        embeddings: EmbeddingService,
        repository: DocumentChunkIndex,
        *,
        batch_token_budget: int = 10_000,
        tokens_per_minute: int = 30_000,
        max_retries: int = 4,
        retry_base_delay_seconds: float = 2.0,
        token_counter: Callable[[str], int] | None = None,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ):
        if batch_token_budget < 1:
            raise ValueError("batch_token_budget must be at least 1")
        if tokens_per_minute < batch_token_budget:
            raise ValueError("tokens_per_minute must be at least batch_token_budget")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self.db = db
        self.embeddings = embeddings
        self.repository = repository
        self.batch_token_budget = batch_token_budget
        self.tokens_per_minute = tokens_per_minute
        self.max_retries = max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.token_counter = token_counter or self._openai_token_count
        self.clock = clock
        self.sleeper = sleeper
        self._token_reservations: deque[tuple[float, int]] = deque()

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
            vectors = self._embed_texts([chunk.content for chunk in chunks])
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

    def _embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed all texts in bounded batches, preserving the input order."""
        vectors: list[list[float]] = []
        for batch in self._build_batches(texts):
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _build_batches(self, texts: Sequence[str]) -> list[EmbeddingBatch]:
        batches: list[EmbeddingBatch] = []
        current: list[str] = []
        current_tokens = 0
        for text in texts:
            tokens = max(1, self.token_counter(text))
            if tokens > self.batch_token_budget:
                raise ValueError(
                    "A parsed chunk exceeds the configured embedding batch token budget; "
                    "reduce the source chunk size or increase EMBEDDING_BATCH_TOKEN_BUDGET"
                )
            if current and current_tokens + tokens > self.batch_token_budget:
                batches.append(EmbeddingBatch(texts=current, token_count=current_tokens))
                current, current_tokens = [], 0
            current.append(text)
            current_tokens += tokens
        if current:
            batches.append(EmbeddingBatch(texts=current, token_count=current_tokens))
        return batches

    def _embed_batch(self, batch: EmbeddingBatch) -> list[list[float]]:
        for attempt in range(self.max_retries + 1):
            self._reserve_token_capacity(batch.token_count)
            try:
                vectors = self.embeddings.embed_documents(batch.texts)
                if len(vectors) != len(batch.texts):
                    raise ValueError("Embedding provider returned an unexpected number of vectors")
                return vectors
            except Exception as exc:
                if not self._is_rate_limited(exc) or attempt >= self.max_retries:
                    raise
                self.sleeper(self.retry_base_delay_seconds * (2**attempt))
        raise RuntimeError("Embedding retry loop ended unexpectedly")

    def _reserve_token_capacity(self, token_count: int) -> None:
        """Apply a rolling one-minute token budget before a provider request."""
        while True:
            now = self.clock()
            while self._token_reservations and now - self._token_reservations[0][0] >= 60:
                self._token_reservations.popleft()
            reserved = sum(tokens for _, tokens in self._token_reservations)
            if reserved + token_count <= self.tokens_per_minute:
                self._token_reservations.append((now, token_count))
                return
            wait_seconds = max(0.1, 60 - (now - self._token_reservations[0][0]))
            self.sleeper(wait_seconds)

    @staticmethod
    def _is_rate_limited(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) == 429

    @staticmethod
    def _openai_token_count(text: str) -> int:
        """Use the OpenAI tokenizer where available; preserve a safe fallback."""
        try:
            import tiktoken

            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        except Exception:
            # This is conservative for the Latin technical manuals used here,
            # and keeps third-party OpenAI-compatible embedding providers usable.
            return max(1, len(text) // 3)
