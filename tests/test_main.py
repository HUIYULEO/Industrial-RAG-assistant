"""
Tests for FastAPI main application endpoints.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client."""
    # Import here to avoid issues with env vars
    from app.main import app
    return TestClient(app)


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@patch("app.main.get_chat_response")
def test_chat_endpoint_success(mock_get_response, client):
    """Test successful chat endpoint."""
    # Mock the chat response
    mock_get_response.return_value = {
        "answer": "WCS manages warehouse operations",
        "sources": ["test.pdf - Page 1"],
        "confidence_score": 0.89,
        "retrieved_chunks": 3
    }

    response = client.post(
        "/chat",
        json={"question": "What is WCS?", "session_id": "test_123"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert data["answer"] == "WCS manages warehouse operations"
    assert data["confidence_score"] == 0.89
    assert len(data["sources"]) > 0


def test_chat_endpoint_empty_question(client):
    """Test chat endpoint with empty question."""
    response = client.post(
        "/chat",
        json={"question": "", "session_id": "test_123"}
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_chat_endpoint_too_long_question(client):
    """Test chat endpoint with overly long question."""
    long_question = "x" * 1001  # Exceeds 1000 char limit

    response = client.post(
        "/chat",
        json={"question": long_question, "session_id": "test_123"}
    )

    assert response.status_code == 400
    assert "maximum length" in response.json()["detail"].lower()


@patch("app.main.get_chat_response")
def test_chat_endpoint_with_session_history(mock_get_response, client):
    """Test chat endpoint maintains session history."""
    mock_get_response.return_value = {
        "answer": "Test answer",
        "sources": [],
        "confidence_score": 0.8,
        "retrieved_chunks": 2
    }

    # First request
    response1 = client.post(
        "/chat",
        json={"question": "First question", "session_id": "session_001"}
    )
    assert response1.status_code == 200

    # Second request - same session
    response2 = client.post(
        "/chat",
        json={"question": "Second question", "session_id": "session_001"}
    )
    assert response2.status_code == 200

    # Check that history was passed
    assert mock_get_response.call_count == 2
    second_call_args = mock_get_response.call_args
    assert "chat_history" in second_call_args.kwargs
    assert len(second_call_args.kwargs["chat_history"]) == 1


@patch("app.main.get_chat_response")
def test_get_session_history(mock_get_response, client):
    """Test retrieving session history."""
    mock_get_response.return_value = {
        "answer": "Test answer",
        "sources": [],
        "confidence_score": 0.8,
        "retrieved_chunks": 2
    }

    # Create a session with some history
    client.post(
        "/chat",
        json={"question": "Test question", "session_id": "hist_test"}
    )

    # Retrieve history
    response = client.get("/sessions/hist_test/history")
    assert response.status_code == 200
    data = response.json()
    assert "history" in data
    assert len(data["history"]) == 1
    assert data["session_id"] == "hist_test"


def test_get_nonexistent_session_history(client):
    """Test retrieving history for non-existent session."""
    response = client.get("/sessions/nonexistent/history")
    assert response.status_code == 404


@patch("app.main.get_chat_response")
def test_delete_session(mock_get_response, client):
    """Test deleting a session."""
    mock_get_response.return_value = {
        "answer": "Test answer",
        "sources": [],
        "confidence_score": 0.8,
        "retrieved_chunks": 2
    }

    # Create a session
    client.post(
        "/chat",
        json={"question": "Test question", "session_id": "delete_test"}
    )

    # Delete it
    response = client.delete("/sessions/delete_test")
    assert response.status_code == 200

    # Verify it's gone
    response = client.get("/sessions/delete_test/history")
    assert response.status_code == 404


def test_delete_nonexistent_session(client):
    """Test deleting a non-existent session."""
    response = client.delete("/sessions/nonexistent")
    assert response.status_code == 404


@patch("app.main.get_chat_response")
def test_chat_endpoint_handles_exceptions(mock_get_response, client):
    """Test chat endpoint handles exceptions gracefully."""
    from app.core.exceptions import LLMException

    # Simulate an LLM exception
    mock_get_response.side_effect = LLMException("LLM service unavailable", {"error": "timeout"})

    response = client.post(
        "/chat",
        json={"question": "Test question", "session_id": "error_test"}
    )

    assert response.status_code == 500
    assert "LLMException" in response.json()["detail"]
