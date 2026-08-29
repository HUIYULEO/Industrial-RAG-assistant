"""Runtime configuration for the Design Review RAG workspace.

The original proof of concept read configuration at import time.  The review
workflow is intentionally configured lazily so its metadata APIs can run
locally without an LLM, a vector store, or supplier documents.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with safe local-development defaults."""

    database_url: str = "sqlite:///./data/design_review.db"
    data_dir: Path = Path("data")
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "knowledge_chunks"
    # Chat and embeddings may use different providers. This permits, for
    # example, comparing DeepSeek and Qwen answers on one frozen vector index.
    llm_provider: str = "openai"
    embedding_provider: str = "openai"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    qwen_api_key: str | None = None
    # DASHSCOPE_API_KEY is accepted as an equivalent, provider-native name.
    dashscope_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # Keep local indexing below the free-tier 40k TPM limit by default.  These
    # settings are intentionally provider-neutral; they can be tuned for a
    # paid OpenAI tier or another OpenAI-compatible embedding provider.
    embedding_batch_token_budget: int = 10_000
    embedding_tokens_per_minute: int = 30_000
    embedding_batch_max_retries: int = 4
    embedding_retry_base_delay_seconds: float = 2.0
    chat_model: str = "gpt-4o"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    # ABB's public AMR T702 Product Manual is about 44 MB, so permit a single
    # conventional supplier manual in the local review workspace by default.
    max_upload_size_mb: int = 50
    auth_required: bool = True
    # Safe only for this local Docker workspace. Set a unique secret before sharing a deployment.
    auth_secret: str = "local-development-secret-change-before-shared-deployment"
    access_token_expire_minutes: int = 480
    allow_self_registration: bool = True
    local_admin_email: str | None = None
    local_admin_password: str | None = None
    local_admin_department: str = "DDIT"
    ddit_admin_email: str | None = None
    ddit_admin_password: str | None = None
    qa_admin_email: str | None = None
    qa_admin_password: str | None = None
    # Diagram pages remain available as source evidence by default. Vision-model
    # interpretation is an opt-in future capability, not part of the current RAG scope.
    enable_visual_analysis: bool = False
    # Redis/RQ dispatches one job per frozen URS item. Use "memory" only in
    # isolated tests; deployed environments should use the Redis default.
    redis_url: str = "redis://localhost:6379/0"
    analysis_queue_name: str = "design-review"
    document_index_queue_name: str = "document-indexing"
    document_index_job_timeout_seconds: int = 1800
    analysis_queue_backend: str = "redis"
    analysis_item_max_attempts: int = 3
    analysis_retry_delays_seconds: list[int] = [2, 5]
    analysis_job_timeout_seconds: int = 300
    analysis_progress_poll_seconds: float = 1.0
    analysis_lease_seconds: int = 360
    analysis_heartbeat_interval_seconds: int = 30
    analysis_maintenance_poll_seconds: float = 2.0
    worker_build_version: str = "local"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings() -> Settings:
    """Return settings at runtime rather than during module import."""
    return Settings()
