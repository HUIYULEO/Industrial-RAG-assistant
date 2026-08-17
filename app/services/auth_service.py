"""Small, replaceable local authentication boundary for the review workspace."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.models import User


@dataclass(frozen=True)
class AuthenticatedUser:
    email: str
    display_name: str
    role: str


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash a local password using scrypt; passwords are never stored in plain text."""
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_value, digest_value = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        candidate = hash_password(password, base64.b64decode(salt_value))
        return hmac.compare_digest(candidate, encoded)
    except (ValueError, TypeError):
        return False


class AuthService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def bootstrap_admin(self) -> None:
        """Optionally create a configured local administrator for controlled deployments."""
        if not self.settings.auth_required or not self.settings.local_admin_email:
            return
        if not self.settings.local_admin_password:
            raise RuntimeError("LOCAL_ADMIN_PASSWORD is required when LOCAL_ADMIN_EMAIL is configured")
        email = self.settings.local_admin_email.strip().lower()
        existing = self.db.scalar(select(User).where(User.email == email))
        if existing:
            return
        self.db.add(
            User(
                email=email,
                display_name=email.split("@", 1)[0].replace(".", " ").title(),
                password_hash=hash_password(self.settings.local_admin_password),
                role="admin",
            )
        )
        self.db.commit()

    def register(self, display_name: str, email: str, password: str) -> AuthenticatedUser:
        if not self.settings.allow_self_registration:
            raise PermissionError("Account registration is disabled")
        normalised_email = email.strip().lower()
        if self.db.scalar(select(User).where(User.email == normalised_email)):
            raise ValueError("An account with this email already exists")
        user = User(
            email=normalised_email,
            display_name=display_name.strip(),
            password_hash=hash_password(password),
            role="engineer",
        )
        self.db.add(user)
        self.db.commit()
        return AuthenticatedUser(email=user.email, display_name=user.display_name, role=user.role)

    def login(self, email: str, password: str) -> tuple[str, AuthenticatedUser]:
        if not self.settings.auth_required:
            raise PermissionError("Local authentication is not enabled")
        user = self.db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise PermissionError("Invalid email or password")
        if not self.settings.auth_secret:
            raise RuntimeError("AUTH_SECRET is not configured")
        identity = AuthenticatedUser(email=user.email, display_name=user.display_name, role=user.role)
        token = jwt.encode(
            {
                "sub": user.id,
                "email": identity.email,
                "display_name": identity.display_name,
                "role": identity.role,
                "exp": datetime.now(UTC) + timedelta(minutes=self.settings.access_token_expire_minutes),
            },
            self.settings.auth_secret,
            algorithm="HS256",
        )
        return token, identity

    def authenticate_token(self, token: str) -> AuthenticatedUser:
        if not self.settings.auth_secret:
            raise PermissionError("AUTH_SECRET is not configured")
        try:
            payload = jwt.decode(token, self.settings.auth_secret, algorithms=["HS256"])
            user_id = payload["sub"]
        except (jwt.InvalidTokenError, KeyError) as exc:
            raise PermissionError("Invalid or expired session") from exc
        user = self.db.get(User, user_id)
        if user is None or not user.is_active:
            raise PermissionError("Account is unavailable")
        return AuthenticatedUser(email=user.email, display_name=user.display_name, role=user.role)
