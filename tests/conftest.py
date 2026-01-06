"""
Pytest configuration and fixtures for testing.
"""
import pytest
import os
from unittest.mock import Mock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up test environment variables."""
    os.environ["OPENAI_API_KEY"] = "test-key-123"
    os.environ["SUPABASE_URL"] = "https://test.supabase.co"
    os.environ["SUPABASE_SERVICE_KEY"] = "test-service-key"
    os.environ["LOG_LEVEL"] = "DEBUG"


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data = [
        {
            "id": "doc1",
            "content": "Warehouse control systems manage material flow...",
            "similarity": 0.92,
            "metadata": {"source": "test.pdf", "page": 1}
        },
        {
            "id": "doc2",
            "content": "Conveyor systems require load capacity analysis...",
            "similarity": 0.85,
            "metadata": {"source": "test.pdf", "page": 2}
        }
    ]
    mock_client.rpc.return_value.execute.return_value = mock_response
    return mock_client


@pytest.fixture
def mock_embeddings():
    """Mock OpenAI embeddings."""
    mock = MagicMock()
    mock.embed_query.return_value = [0.1] * 1536  # 1536-dim vector
    return mock


@pytest.fixture
def mock_llm():
    """Mock LLM."""
    mock = MagicMock()
    return mock


@pytest.fixture
def sample_chat_history():
    """Sample chat history for testing."""
    return [
        {"question": "What is WCS?", "answer": "Warehouse Control System manages..."},
        {"question": "How does it work?", "answer": "It coordinates material handling..."}
    ]


@pytest.fixture
def sample_documents():
    """Sample documents for testing."""
    from langchain_core.documents import Document
    return [
        Document(
            page_content="Warehouse control systems manage material flow",
            metadata={"id": "doc1", "similarity": 0.92, "source": "test.pdf", "page": 1}
        ),
        Document(
            page_content="Conveyor systems require load capacity analysis",
            metadata={"id": "doc2", "similarity": 0.85, "source": "test.pdf", "page": 2}
        )
    ]
