"""Embedding-provider boundary; only the adapter reads model credentials."""

from __future__ import annotations

from typing import Protocol


class EmbeddingService(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbeddingService:
    """Thin adapter around LangChain's supported OpenAI embedding client."""

    def __init__(self, model: str, dimensions: int):
        from langchain_openai import OpenAIEmbeddings

        self._client = OpenAIEmbeddings(model=model, dimensions=dimensions)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)
