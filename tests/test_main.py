"""Smoke tests for the current design-review application shell."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings


def test_health_check(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "test-secret-that-is-not-a-placeholder")
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    "settings, expected_message",
    [
        (
            Settings(_env_file=None, auth_secret=None),
            "AUTH_SECRET must be set to a non-placeholder value",
        ),
        (
            Settings(
                _env_file=None,
                auth_secret="replace-with-a-unique-long-random-secret-before-sharing",
            ),
            "AUTH_SECRET must be set to a non-placeholder value",
        ),
        (
            Settings(
                _env_file=None,
                auth_required=False,
                auth_secret="test-secret-that-is-not-a-placeholder",
            ),
            "AUTH_REQUIRED=false is not supported",
        ),
    ],
)
def test_startup_rejects_unsafe_auth_configuration(
    monkeypatch, settings: Settings, expected_message: str
):
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match=expected_message):
        with TestClient(main.app):
            pass
