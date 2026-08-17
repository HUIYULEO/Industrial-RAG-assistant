"""Authentication endpoints and dependency used by protected workspace APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.schemas import AuthConfigResponse, AuthUserResponse, LoginRequest, LoginResponse, RegisterRequest
from app.core.config import get_settings
from app.repositories.database import get_db
from app.services.auth_service import AuthService, AuthenticatedUser

router = APIRouter(prefix="/auth", tags=["authentication"])
bearer_scheme = HTTPBearer(auto_error=False)
DbSession = Annotated[Session, Depends(get_db)]


def user_response(user: AuthenticatedUser) -> AuthUserResponse:
    return AuthUserResponse(email=user.email, display_name=user.display_name, role=user.role)


def require_authenticated_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedUser:
    settings = get_settings()
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in is required")
    try:
        return AuthService(db, settings).authenticate_token(credentials.credentials)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.get("/config", response_model=AuthConfigResponse)
def auth_config():
    settings = get_settings()
    return AuthConfigResponse(
        authentication_required=settings.auth_required,
        self_registration_enabled=settings.allow_self_registration,
        visual_analysis_enabled=settings.enable_visual_analysis,
    )


@router.post("/register", response_model=AuthUserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession):
    try:
        user = AuthService(db, get_settings()).register(
            display_name=payload.display_name,
            email=payload.email,
            password=payload.password,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return user_response(user)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: DbSession):
    try:
        token, user = AuthService(db, get_settings()).login(payload.email, payload.password)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return LoginResponse(access_token=token, user=user_response(user))


@router.get("/me", response_model=AuthUserResponse)
def current_user(user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)]):
    return user_response(user)
