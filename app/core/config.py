"""Runtime configuration for the Design Review RAG workspace.

The original proof of concept read configuration at import time.  The review
workflow is intentionally configured lazily so its metadata APIs can run
locally without an LLM, a vector store, or supplier documents.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with safe local-development defaults."""

    app_name: str = "Industrial Design Review RAG"
    database_url: str = "sqlite:///./data/design_review.db"
    data_dir: Path = Path("data")
    default_output_language: str = "en"
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "knowledge_chunks"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    chat_model: str = "gpt-4o"
    max_upload_size_mb: int = 25
    auth_required: bool = True
    # Safe only for this local Docker workspace. Set a unique secret before sharing a deployment.
    auth_secret: str = "local-development-secret-change-before-shared-deployment"
    access_token_expire_minutes: int = 480
    allow_self_registration: bool = True
    local_admin_email: str | None = None
    local_admin_password: str | None = None
    # Diagram pages remain available as source evidence by default. Vision-model
    # interpretation is an opt-in future capability, not part of the current RAG scope.
    enable_visual_analysis: bool = False
    # Redis/RQ dispatches one job per frozen URS item. Use "memory" only in
    # isolated tests; deployed environments should use the Redis default.
    redis_url: str = "redis://localhost:6379/0"
    analysis_queue_name: str = "design-review"
    analysis_queue_backend: str = "redis"
    analysis_item_max_attempts: int = 3
    analysis_retry_delays_seconds: list[int] = [2, 5]
    analysis_job_timeout_seconds: int = 300
    analysis_progress_poll_seconds: float = 1.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings() -> Settings:
    """Return settings at runtime rather than during module import."""
    return Settings()
