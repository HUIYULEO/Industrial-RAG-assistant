"""Concrete runtime assembly kept outside HTTP routes and business services."""

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.repositories.milvus_repository import MilvusChunkRepository
from app.services.embedding_service import ConfiguredEmbeddingService
from app.services.design_review_chat_service import (
    ConfiguredGroundedAnswerGenerator,
    ConfiguredQueryNormalizer,
    DesignReviewChatService,
)
from app.services.coverage_service import ConfiguredDesignFindingJudge, CoverageAnalysisService
from app.services.indexing_service import DocumentIndexingService
from app.services.ingestion_service import DocumentIngestionService
from app.services.model_provider import create_chat_model
from app.services.retrieval_service import HybridRetrievalService
from app.services.visual_evidence_service import ConfiguredVisualInterpreter, VisualEvidenceService, VisualInterpreter


def build_document_ingestion_service(db: Session) -> DocumentIngestionService:
    return DocumentIngestionService(db, get_settings().data_dir)


def build_document_indexing_service(db: Session) -> DocumentIndexingService:
    settings = get_settings()
    return DocumentIndexingService(
        db,
        ConfiguredEmbeddingService(settings),
        MilvusChunkRepository(
            uri=settings.milvus_uri,
            collection_name=settings.milvus_collection,
            dimension=settings.embedding_dimensions,
        ),
        batch_token_budget=settings.embedding_batch_token_budget,
        tokens_per_minute=settings.embedding_tokens_per_minute,
        max_retries=settings.embedding_batch_max_retries,
        retry_base_delay_seconds=settings.embedding_retry_base_delay_seconds,
    )


def build_visual_evidence_service(db: Session) -> VisualEvidenceService:
    return VisualEvidenceService(db, get_settings().data_dir)


def build_visual_interpreter() -> VisualInterpreter:
    return ConfiguredVisualInterpreter(create_chat_model(get_settings()))


def build_retrieval_service() -> HybridRetrievalService:
    """Assemble the evidence retriever used by chat and background analysis."""
    settings = get_settings()
    return HybridRetrievalService(
        repository=MilvusChunkRepository(
            uri=settings.milvus_uri,
            collection_name=settings.milvus_collection,
            dimension=settings.embedding_dimensions,
        ),
        embeddings=ConfiguredEmbeddingService(settings),
    )


def build_design_review_chat_service() -> DesignReviewChatService:
    settings = get_settings()
    chat_model = create_chat_model(settings)
    return DesignReviewChatService(
        retrieval=build_retrieval_service(),
        normalizer=ConfiguredQueryNormalizer(chat_model),
        generator=ConfiguredGroundedAnswerGenerator(chat_model),
    )


def build_coverage_analysis_service(db: Session) -> CoverageAnalysisService:
    """Assemble the worker-only service for one auditable coverage decision."""
    settings = get_settings()
    return CoverageAnalysisService(
        db,
        retrieval=build_retrieval_service(),
        judge=ConfiguredDesignFindingJudge(create_chat_model(settings)),
    )
