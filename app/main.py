"""FastAPI application for the controlled design-review workspace."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.documents import router as documents_router
from app.api.routes.requirements import router as requirements_router
from app.api.routes.review_packages import router as review_packages_router
from app.api.routes.analysis_runs import router as analysis_runs_router
from app.core.config import get_settings
from app.core.logging_config import get_logger, setup_logging
from app.repositories.database import get_session_factory, initialise_database
from app.services.auth_service import AuthService

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting Industrial RAG Assistant API")
    initialise_database()
    db = get_session_factory()()
    try:
        AuthService(db, get_settings()).bootstrap_admin()
    finally:
        db.close()
    logger.info("Application startup complete")
    yield
    logger.info("Shutting down Industrial RAG Assistant API")


app = FastAPI(
    title="Warehouse Automation Design Decision Assistant",
    description="A controlled, evidence-grounded workspace for warehouse design review.",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(requirements_router)
app.include_router(review_packages_router)
app.include_router(analysis_runs_router)
app.include_router(auth_router)


@app.exception_handler(Exception)
async def general_exception_handler(_, exc: Exception):
    logger.error("Unexpected error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred. Please try again later.",
            "details": {"error_type": exc.__class__.__name__},
        },
    )


@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "message": "Warehouse Automation Design Decision Assistant is online",
        "version": "2.0.0",
    }
