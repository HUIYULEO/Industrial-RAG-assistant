import pytest

from app.core.config import Settings
from app.services.model_provider import (
    ModelProviderConfigurationError,
    create_chat_model,
    create_embedding_model,
    resolve_provider,
)


def test_resolves_deepseek_connection_from_dedicated_configuration():
    settings = Settings(
        llm_provider="deepseek",
        chat_model="deepseek-model",
        deepseek_api_key="deepseek-test-key",
        deepseek_base_url="https://example.deepseek.test/",
    )

    connection = resolve_provider(settings, settings.llm_provider)

    assert connection.name == "deepseek"
    assert connection.base_url == "https://example.deepseek.test"
    assert connection.api_key == "deepseek-test-key"


def test_deepseek_cannot_be_selected_for_embeddings():
    settings = Settings(embedding_provider="deepseek", deepseek_api_key="deepseek-test-key")

    with pytest.raises(ModelProviderConfigurationError, match="chat only"):
        create_embedding_model(settings)


def test_rejects_openai_model_name_after_switching_provider():
    settings = Settings(
        llm_provider="qwen",
        chat_model="gpt-4o",
        qwen_api_key="qwen-test-key",
    )

    with pytest.raises(ModelProviderConfigurationError, match="belongs to OpenAI"):
        create_chat_model(settings)


def test_constructs_openai_compatible_clients_with_selected_connection(monkeypatch):
    captured: dict[str, object] = {}

    class FakeChatModel:
        def __init__(self, **kwargs):
            captured["chat"] = kwargs

    class FakeEmbeddings:
        def __init__(self, **kwargs):
            captured["embeddings"] = kwargs

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatModel)
    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", FakeEmbeddings)
    settings = Settings(
        llm_provider="qwen",
        chat_model="qwen-test-model",
        qwen_api_key="qwen-test-key",
        embedding_provider="qwen",
        embedding_model="qwen-embedding-test",
        embedding_dimensions=1024,
    )

    create_chat_model(settings)
    create_embedding_model(settings)

    assert captured["chat"]["base_url"] == settings.qwen_base_url
    assert captured["chat"]["api_key"] == "qwen-test-key"
    assert captured["embeddings"]["dimensions"] == 1024
    assert captured["embeddings"]["check_embedding_ctx_length"] is False
