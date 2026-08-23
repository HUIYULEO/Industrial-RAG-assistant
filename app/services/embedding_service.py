"""Embedding-provider boundary used by indexing and retrieval."""

from __future__ import annotations

from typing import Protocol

from app.core.config import Settings
from app.services.model_provider import create_embedding_model


class EmbeddingService(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class ConfiguredEmbeddingService:
    """Thin adapter around the embedding provider selected in Settings."""

    def __init__(self, settings: Settings):
        self._client = create_embedding_model(settings)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)
