"""Worker entry point. One RQ job evaluates one frozen URS item."""

from __future__ import annotations

from app.core.config import get_settings
from app.repositories.database import get_session_factory, initialise_database
from app.repositories.milvus_repository import MilvusChunkRepository
from app.services.coverage_service import ConfiguredDesignFindingJudge, CoverageAnalysisService
from app.services.embedding_service import ConfiguredEmbeddingService
from app.services.model_provider import create_chat_model
from app.services.retrieval_service import MilvusRetrievalService


def execute_analysis_item(analysis_run_item_id: str) -> None:
    """Execute and persist one independently retryable analysis item."""
    initialise_database()
    settings = get_settings()
    db = get_session_factory()()
    try:
        retrieval = MilvusRetrievalService(
            repository=MilvusChunkRepository(
                uri=settings.milvus_uri,
                collection_name=settings.milvus_collection,
                dimension=settings.embedding_dimensions,
            ),
            embeddings=ConfiguredEmbeddingService(settings),
        )
        CoverageAnalysisService(
            db,
            retrieval=retrieval,
            judge=ConfiguredDesignFindingJudge(create_chat_model(settings)),
        ).execute_item(
            analysis_run_item_id,
            max_attempts=settings.analysis_item_max_attempts,
            retry_delays_seconds=settings.analysis_retry_delays_seconds,
        )
    finally:
        db.close()
