"""Tests for the optional protected-workspace boundary."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.repositories import database


@pytest.fixture
def protected_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'auth.db'}")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-that-is-long-enough")
    monkeypatch.setenv("LOCAL_ADMIN_EMAIL", "review.admin@example.com")
    monkeypatch.setenv("LOCAL_ADMIN_PASSWORD", "local-test-password")
    from app.main import app
    with TestClient(app) as client:
        yield client
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()


def test_protected_workspace_requires_token_and_accepts_bootstrap_admin(protected_client: TestClient):
    assert protected_client.get("/documents").status_code == 401
    login = protected_client.post(
        "/auth/login",
        json={"email": "review.admin@example.com", "password": "local-test-password"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    response = protected_client.get("/documents", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert login.json()["user"]["role"] == "admin"


def test_user_can_register_and_sign_in_with_email_and_password(protected_client: TestClient):
    created = protected_client.post(
        "/auth/register",
        json={
            "display_name": "Avery Chen",
            "email": "avery.chen@example.com",
            "password": "a-strong-local-password",
        },
    )
    assert created.status_code == 201
    assert created.json()["display_name"] == "Avery Chen"
    assert created.json()["role"] == "engineer"

    duplicate = protected_client.post(
        "/auth/register",
        json={
            "display_name": "Avery Chen",
            "email": "avery.chen@example.com",
            "password": "a-strong-local-password",
        },
    )
    assert duplicate.status_code == 409

    login = protected_client.post(
        "/auth/login",
        json={"email": "avery.chen@example.com", "password": "a-strong-local-password"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "avery.chen@example.com"
