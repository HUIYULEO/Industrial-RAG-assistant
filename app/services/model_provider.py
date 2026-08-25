"""Provider-neutral OpenAI-compatible chat and embedding client factory.

OpenAI, DeepSeek, and Qwen expose OpenAI-compatible endpoints for the textual
workflows used by this application.  The provider choice is deliberately
separate for chat and embeddings: DeepSeek can be used to judge or answer
against an index produced with OpenAI embeddings, for example.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings


class ModelProviderConfigurationError(ValueError):
    """Raised only when an LLM capability is invoked with incomplete settings."""


@dataclass(frozen=True)
class ProviderConnection:
    name: str
    api_key: str
    base_url: str | None


def resolve_provider(settings: Settings, provider_name: str) -> ProviderConnection:
    """Resolve credentials and endpoint without exposing a provider key."""
    name = provider_name.strip().lower()
    if name == "openai":
        return _connection("openai", settings.openai_api_key, settings.openai_base_url)
    if name == "deepseek":
        return _connection("deepseek", settings.deepseek_api_key, settings.deepseek_base_url)
    if name == "qwen":
        return _connection("qwen", settings.qwen_api_key or settings.dashscope_api_key, settings.qwen_base_url)
    raise ModelProviderConfigurationError(
        "Unsupported provider. Set LLM_PROVIDER or EMBEDDING_PROVIDER to openai, deepseek, or qwen."
    )


def create_chat_model(settings: Settings) -> Any:
    """Create the selected OpenAI-compatible chat model lazily."""
    connection = resolve_provider(settings, settings.llm_provider)
    _ensure_model_matches_provider(settings.chat_model, connection.name, capability="chat")

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.chat_model,
        temperature=0,
        api_key=connection.api_key,
        base_url=connection.base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def create_embedding_model(settings: Settings) -> Any:
    """Create the selected embedding client lazily.

    DeepSeek is intentionally rejected here. Its API key may still be used for
    chat while retrieval keeps an existing OpenAI or Qwen embedding index.
    """
    connection = resolve_provider(settings, settings.embedding_provider)
    if connection.name == "deepseek":
        raise ModelProviderConfigurationError(
            "DeepSeek is configured as an embedding provider, but this deployment supports "
            "DeepSeek for chat only. Use EMBEDDING_PROVIDER=openai or qwen."
        )
    _ensure_model_matches_provider(settings.embedding_model, connection.name, capability="embedding")

    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        api_key=connection.api_key,
        base_url=connection.base_url,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
        # Third-party embedding model names are not guaranteed to exist in
        # tiktoken. Chunks are already bounded by the ingestion pipeline.
        check_embedding_ctx_length=connection.name == "openai",
    )


def _connection(name: str, api_key: str | None, base_url: str | None) -> ProviderConnection:
    if not api_key or not api_key.strip():
        raise ModelProviderConfigurationError(
            f"{name.upper()} API key is required for the selected provider. Check the corresponding environment variable."
        )
    normalised_base_url = base_url.strip().rstrip("/") if base_url and base_url.strip() else None
    # LangChain's OpenAI client treats an explicit ``None`` base_url differently
    # from an omitted base URL in some dependency versions.  Pin the public
    # endpoint for direct OpenAI use so a blank OPENAI_BASE_URL= remains safe.
    if name == "openai" and normalised_base_url is None:
        normalised_base_url = "https://api.openai.com/v1"
    return ProviderConnection(name=name, api_key=api_key.strip(), base_url=normalised_base_url)


def _ensure_model_matches_provider(model: str, provider_name: str, *, capability: str) -> None:
    if not model or not model.strip():
        raise ModelProviderConfigurationError(f"A {capability} model must be configured.")
    if provider_name != "openai" and model.strip().lower().startswith("gpt-"):
        raise ModelProviderConfigurationError(
            f"{capability.title()} model '{model}' belongs to OpenAI. Set a {provider_name} model before switching providers."
        )
