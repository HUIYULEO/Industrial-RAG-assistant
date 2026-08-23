"""Small, replaceable local authentication boundary for the review workspace."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.models import Organization, ReviewPackage, User


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    organization_id: str
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
    DEPARTMENTS = ("DDIT", "QA")

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def bootstrap_admin(self) -> None:
        """Create fixed department tenants and backfill the pre-department workspace."""
        departments = self._ensure_departments()
        ddit = departments["DDIT"]
        legacy_organization = self.db.scalar(select(Organization).where(Organization.name == "Local Workspace"))
        if legacy_organization:
            self.db.execute(
                update(User)
                .where(User.organization_id == legacy_organization.id)
                .values(organization_id=ddit.id)
            )
            self.db.execute(
                update(ReviewPackage)
                .where(ReviewPackage.organization_id == legacy_organization.id)
                .values(organization_id=ddit.id)
            )
        self.db.execute(
            update(User)
            .where(User.organization_id.is_(None))
            .values(organization_id=ddit.id)
        )
        self.db.execute(
            update(ReviewPackage)
            .where(ReviewPackage.organization_id.is_(None))
            .values(organization_id=ddit.id)
        )
        legacy_owner = self.db.scalar(
            select(User).where(User.organization_id == ddit.id).order_by(User.created_at, User.id)
        )
        if legacy_owner:
            self.db.execute(
                update(ReviewPackage)
                .where(ReviewPackage.owner_user_id.is_(None))
                .values(owner_user_id=legacy_owner.id, organization_id=ddit.id)
            )

        if self.settings.auth_required:
            self._bootstrap_configured_admin(
                self.settings.local_admin_email,
                self.settings.local_admin_password,
                self.settings.local_admin_department,
                "LOCAL_ADMIN",
            )
            self._bootstrap_configured_admin(
                self.settings.ddit_admin_email,
                self.settings.ddit_admin_password,
                "DDIT",
                "DDIT_ADMIN",
            )
            self._bootstrap_configured_admin(
                self.settings.qa_admin_email,
                self.settings.qa_admin_password,
                "QA",
                "QA_ADMIN",
            )
        self.db.commit()

    def _ensure_departments(self) -> dict[str, Organization]:
        departments: dict[str, Organization] = {}
        for name in self.DEPARTMENTS:
            organization = self.db.scalar(select(Organization).where(Organization.name == name))
            if organization is None:
                organization = Organization(name=name)
                self.db.add(organization)
                self.db.flush()
            departments[name] = organization
        return departments

    def _department_organization(self, department: str) -> Organization:
        requested = department.strip().upper()
        if requested not in self.DEPARTMENTS:
            raise ValueError("Department must be DDIT or QA")
        return self._ensure_departments()[requested]

    def _bootstrap_configured_admin(
        self,
        email_value: str | None,
        password: str | None,
        department: str,
        setting_name: str,
    ) -> None:
        if not email_value:
            return
        if not password:
            raise RuntimeError(f"{setting_name}_PASSWORD is required when {setting_name}_EMAIL is configured")
        email = email_value.strip().lower()
        organization = self._department_organization(department)
        existing = self.db.scalar(select(User).where(User.email == email))
        if existing:
            existing.organization_id = organization.id
            existing.role = "admin"
            return
        self.db.add(
            User(
                organization_id=organization.id,
                email=email,
                display_name=email.split("@", 1)[0].replace(".", " ").title(),
                password_hash=hash_password(password),
                role="admin",
            )
        )

    def register(self, display_name: str, email: str, password: str, department: str) -> AuthenticatedUser:
        if not self.settings.allow_self_registration:
            raise PermissionError("Account registration is disabled")
        normalised_email = email.strip().lower()
        if self.db.scalar(select(User).where(User.email == normalised_email)):
            raise ValueError("An account with this email already exists")
        user = User(
            organization_id=self._department_organization(department).id,
            email=normalised_email,
            display_name=display_name.strip(),
            password_hash=hash_password(password),
            role="engineer",
        )
        self.db.add(user)
        self.db.commit()
        return self._identity(user)

    def login(self, email: str, password: str) -> tuple[str, AuthenticatedUser]:
        if not self.settings.auth_required:
            raise PermissionError("Local authentication is not enabled")
        user = self.db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise PermissionError("Invalid email or password")
        if not self.settings.auth_secret:
            raise RuntimeError("AUTH_SECRET is not configured")
        identity = self._identity(user)
        token = jwt.encode(
            {
                "sub": user.id,
                "organization_id": identity.organization_id,
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
        return self._identity(user)

    @staticmethod
    def _identity(user: User) -> AuthenticatedUser:
        if not user.organization_id:
            raise PermissionError("Account is not assigned to an organization")
        return AuthenticatedUser(
            id=user.id,
            organization_id=user.organization_id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
        )
