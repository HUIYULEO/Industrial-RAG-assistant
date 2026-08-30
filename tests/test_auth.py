"""Tests for the optional protected-workspace boundary."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.models import User
from app.repositories import database
from app.services.auth_service import AuthService


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


def test_authentication_uses_a_closed_short_session_before_returning_user(monkeypatch):
    from app.api import auth as auth_api
    from app.services.auth_service import AuthenticatedUser

    calls = {}

    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    class FakeAuthService:
        def __init__(self, db, _settings):
            calls["session"] = db

        def authenticate_token(self, token):
            calls["token"] = token
            return AuthenticatedUser(
                id="user-1",
                organization_id="organization-1",
                email="engineer@example.com",
                display_name="Engineer",
                role="engineer",
            )

    session = FakeSession()
    monkeypatch.setattr(auth_api, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(auth_api, "AuthService", FakeAuthService)

    user = auth_api.require_authenticated_user(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="token-1")
    )

    assert user.id == "user-1"
    assert calls == {"session": session, "token": "token-1"}
    assert session.closed is True


def test_database_dependencies_use_function_scope_before_response_body():
    from app.api.auth import DbSession as AuthDbSession
    from app.api.dependencies import DbSession as WorkspaceDbSession

    assert AuthDbSession.__metadata__[0].scope == "function"
    assert WorkspaceDbSession.__metadata__[0].scope == "function"


def test_function_scoped_database_session_closes_before_response_send():
    from app.api.dependencies import DbSession as WorkspaceDbSession
    from app.repositories.database import get_db

    session = type("TrackedSession", (), {"closed": False})()
    observed = []
    test_app = FastAPI()

    def tracked_db():
        try:
            yield session
        finally:
            session.closed = True

    class ObservedResponse(Response):
        async def __call__(self, scope, receive, send):
            observed.append(session.closed)
            await super().__call__(scope, receive, send)

    @test_app.get("/response")
    def response_with_db(_db: WorkspaceDbSession):
        return ObservedResponse("ok")

    test_app.dependency_overrides[get_db] = tracked_db
    with TestClient(test_app) as client:
        response = client.get("/response")

    assert response.text == "ok"
    assert observed == [True]


def test_user_can_register_and_sign_in_with_email_and_password(protected_client: TestClient):
    config = protected_client.get("/auth/config")
    assert config.status_code == 200
    assert config.json()["departments"] == ["DDIT", "QA"]

    created = protected_client.post(
        "/auth/register",
        json={
            "display_name": "Avery Chen",
            "email": "avery.chen@example.com",
            "password": "a-strong-local-password",
            "department": "QA",
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
            "department": "QA",
        },
    )
    assert duplicate.status_code == 409

    login = protected_client.post(
        "/auth/login",
        json={"email": "avery.chen@example.com", "password": "a-strong-local-password"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "avery.chen@example.com"


def test_registration_rejects_unknown_department(protected_client: TestClient):
    response = protected_client.post(
        "/auth/register",
        json={
            "display_name": "Jordan Lee",
            "email": "jordan.lee@example.com",
            "password": "a-strong-local-password",
            "department": "Engineering",
        },
    )
    assert response.status_code == 400
    assert "DDIT or QA" in response.json()["detail"]


def test_expired_tokens_and_disabled_accounts_remain_rejected(protected_client: TestClient):
    login = protected_client.post(
        "/auth/login",
        json={"email": "review.admin@example.com", "password": "local-test-password"},
    )
    user_id = login.json()["user"]["id"]
    expired_token = jwt.encode(
        {"sub": user_id, "exp": datetime.now(UTC) - timedelta(seconds=1)},
        "test-secret-that-is-long-enough",
        algorithm="HS256",
    )

    expired = protected_client.get(
        "/documents", headers={"Authorization": f"Bearer {expired_token}"}
    )

    assert expired.status_code == 401
    assert expired.json()["detail"] == "Invalid or expired session"

    with database.get_session_factory()() as db:
        user = db.get(User, user_id)
        user.is_active = False
        db.commit()
    disabled = protected_client.get(
        "/documents",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert disabled.status_code == 401
    assert disabled.json()["detail"] == "Account is unavailable"


def test_bootstrap_admin_takes_over_existing_email_password_without_rehashing_again(
    protected_client: TestClient,
):
    email = "promoted.admin@example.com"
    old_password = "engineer-password-before-promotion"
    configured_password = "configured-admin-password"
    registered = protected_client.post(
        "/auth/register",
        json={
            "display_name": "Promoted Admin",
            "email": email,
            "password": old_password,
            "department": "QA",
        },
    )
    assert registered.status_code == 201
    user_id = registered.json()["id"]
    settings = Settings(
        _env_file=None,
        auth_secret="test-secret-that-is-long-enough",
        local_admin_email=email,
        local_admin_password=configured_password,
        local_admin_department="DDIT",
    )

    with database.get_session_factory()() as db:
        AuthService(db, settings).bootstrap_admin()
        promoted = db.get(User, user_id)
        first_admin_hash = promoted.password_hash
        AuthService(db, settings).bootstrap_admin()
        db.refresh(promoted)
        second_admin_hash = promoted.password_hash

    assert first_admin_hash == second_admin_hash
    old_login = protected_client.post(
        "/auth/login", json={"email": email, "password": old_password}
    )
    new_login = protected_client.post(
        "/auth/login", json={"email": email, "password": configured_password}
    )
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    assert new_login.json()["user"]["role"] == "admin"


def test_engineer_cannot_modify_shared_knowledge_but_can_run_a_review(
    protected_client: TestClient,
):
    admin_login = protected_client.post(
        "/auth/login",
        json={"email": "review.admin@example.com", "password": "local-test-password"},
    )
    admin_headers = {
        "Authorization": f"Bearer {admin_login.json()['access_token']}"
    }
    protected_client.post(
        "/auth/register",
        json={
            "display_name": "Review Engineer",
            "email": "review.engineer@example.com",
            "password": "a-strong-local-password",
            "department": "DDIT",
        },
    )
    engineer_login = protected_client.post(
        "/auth/login",
        json={
            "email": "review.engineer@example.com",
            "password": "a-strong-local-password",
        },
    )
    engineer_headers = {
        "Authorization": f"Bearer {engineer_login.json()['access_token']}"
    }
    document_payload = {
        "title": "Shared Functional Specification",
        "document_type": "FS",
        "system": "fleet_manager",
        "version": "1.0",
        "status": "draft",
    }
    document = protected_client.post(
        "/documents", json=document_payload, headers=admin_headers
    )
    baseline = protected_client.post(
        "/requirement-baselines",
        json={"name": "Shared URS", "system": "fleet_manager"},
        headers=admin_headers,
    )
    imported = protected_client.post(
        f"/requirement-baselines/{baseline.json()['id']}/requirements/import",
        files={
            "file": (
                "urs.csv",
                b"requirement_code,requirement_text\nURS-001,Retain audit records\n",
                "text/csv",
            )
        },
        headers=admin_headers,
    )

    assert document.status_code == 201
    assert baseline.status_code == 201
    assert imported.status_code == 200

    document_id = document.json()["id"]
    baseline_id = baseline.json()["id"]
    forbidden_responses = [
        protected_client.post(
            "/documents", json={**document_payload, "version": "2.0"}, headers=engineer_headers
        ),
        protected_client.post(
            f"/documents/{document_id}/upload",
            files={"file": ("source.csv", b"name,value\na,b\n", "text/csv")},
            headers=engineer_headers,
        ),
        protected_client.post(
            f"/documents/{document_id}/reparse", headers=engineer_headers
        ),
        protected_client.post(
            f"/documents/{document_id}/archive",
            json={"reason": "Engineer must not archive shared knowledge"},
            headers=engineer_headers,
        ),
        protected_client.post(
            f"/documents/{document_id}/index", headers=engineer_headers
        ),
        protected_client.post(
            f"/documents/{document_id}/figures/analyse", headers=engineer_headers
        ),
        protected_client.post(
            "/requirement-baselines",
            json={"name": "Engineer baseline", "system": "fleet_manager"},
            headers=engineer_headers,
        ),
        protected_client.post(
            "/requirement-baselines/import",
            files={
                "file": (
                    "engineer.csv",
                    b"requirement_code,requirement_text\nURS-002,Blocked\n",
                    "text/csv",
                )
            },
            headers=engineer_headers,
        ),
        protected_client.post(
            f"/requirement-baselines/{baseline_id}/requirements/import",
            files={
                "file": (
                    "engineer.csv",
                    b"requirement_code,requirement_text\nURS-002,Blocked\n",
                    "text/csv",
                )
            },
            headers=engineer_headers,
        ),
    ]

    assert all(response.status_code == 403 for response in forbidden_responses)
    assert protected_client.get("/documents", headers=engineer_headers).status_code == 200
    assert (
        protected_client.get("/requirement-baselines", headers=engineer_headers).status_code
        == 200
    )
    review = protected_client.post(
        "/review-packages",
        json={
            "name": "Engineer-owned review",
            "system": "fleet_manager",
            "requirement_baseline_id": baseline_id,
            "design_document_version_ids": [document_id],
        },
        headers=engineer_headers,
    )
    assert review.status_code == 201
    analysis = protected_client.post(
        f"/review-packages/{review.json()['id']}/analyses",
        headers=engineer_headers,
    )
    assert analysis.status_code == 202
