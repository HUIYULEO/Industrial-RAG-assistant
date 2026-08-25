"""Database session lifecycle used by the review-workflow repositories."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domain.models import Base


@lru_cache
def get_engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, connect_args=connect_args)


@lru_cache
def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)


def initialise_database() -> None:
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # The project is still in its local-development phase and does not yet use
    # Alembic. Keep existing local databases compatible when an imported URS
    # table gains new traceability fields.
    required_columns = {
        "requirements": {
            "source_row": "VARCHAR(50)",
            "requirement_system": "VARCHAR(150)",
            "rationale_impact": "TEXT",
            "is_critical": "BOOLEAN NOT NULL DEFAULT FALSE",
        },
        "review_package_requirements": {
            "source_row": "VARCHAR(50)",
            "requirement_system": "VARCHAR(150)",
            "rationale_impact": "TEXT",
            "is_critical": "BOOLEAN NOT NULL DEFAULT FALSE",
        },
        "document_figures": {
            "analysis_error": "TEXT",
            "diagram_type": "VARCHAR(120)",
            "visible_labels": "JSON",
            "candidate_description": "TEXT",
            "candidate_relationships": "JSON",
            "citation_chunk_id": "VARCHAR(36)",
        },
        "document_versions": {
            "archived_at": "TIMESTAMP WITH TIME ZONE",
            "archived_by_user_id": "VARCHAR(36)",
            "archived_reason": "TEXT",
        },
        "review_findings": {
            "audit_points": "JSON",
        },
        "users": {
            "organization_id": "VARCHAR(36)",
        },
        "review_packages": {
            "owner_user_id": "VARCHAR(36)",
            "organization_id": "VARCHAR(36)",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, columns in required_columns.items():
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
