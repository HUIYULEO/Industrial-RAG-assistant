"""Smoke tests for the current design-review application shell."""

from fastapi.testclient import TestClient


def test_health_check():
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
