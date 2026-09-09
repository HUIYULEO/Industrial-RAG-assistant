"""Evidence-grounded chat endpoints scoped to frozen review packages."""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.auth import require_authenticated_user
from app.api.dependencies import CurrentUser, DesignReviewChatDependency, scoped_review_service
from app.api.schemas import DocumentChatRequest, ReviewChatCitation, ReviewChatRequest, ReviewChatResponse
from app.core.logging_config import get_logger
from app.domain.enums import DocumentStatus
from app.domain.models import DocumentVersion
from app.repositories.database import get_session_factory
from app.services.design_review_chat_service import (
    GroundedAnswer,
    UNVERIFIED_CITATIONS_LIMITATION,
)

router = APIRouter(tags=["evidence-chat"], dependencies=[Depends(require_authenticated_user)])
logger = get_logger(__name__)


def citation_response(item) -> ReviewChatCitation:
    return ReviewChatCitation(
        chunk_id=item.chunk_id,
        document_version_id=item.document_version_id,
        document_title=item.document_title,
        version=item.version,
        page=item.page,
        section=item.section,
        excerpt=item.content,
    )


def chat_response(*, answer, citations, retrieval_query: str) -> ReviewChatResponse:
    return ReviewChatResponse(
        answer=answer.answer,
        retrieval_query=retrieval_query,
        limitations=answer.limitations,
        citations=[citation_response(item) for item in citations],
    )


def sse_event(event: str, payload: dict) -> str:
    """Encode JSON safely for the Fetch ReadableStream SSE client."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _load_review_scope(review_package_id: str, user: CurrentUser) -> tuple[str, list[str]]:
    db = get_session_factory()()
    try:
        service = scoped_review_service(db, user)
        review = service.get_review_package(review_package_id)
        service.ensure_review_ready(review)
        return review.system, [
            link.document_version_id for link in review.document_links
        ]
    finally:
        db.close()


def _load_document_chat_scope(document_version_ids: list[str]) -> tuple[str, list[str], list[str]]:
    """Validate a user-selected document scope without treating it as a review."""
    db = get_session_factory()()
    try:
        if len(document_version_ids) != len(set(document_version_ids)):
            raise ValueError("Document versions must be selected only once")
        versions = list(
            db.scalars(
                select(DocumentVersion)
                .options(selectinload(DocumentVersion.document))
                .where(DocumentVersion.id.in_(document_version_ids))
            )
        )
        if len(versions) != len(set(document_version_ids)):
            raise LookupError("One or more document versions were not found")
        if any(item.status == DocumentStatus.ARCHIVED.value for item in versions):
            raise ValueError("Archived document versions cannot be queried")
        if any(item.ingestion_status != "indexed" for item in versions):
            raise ValueError("All selected document versions must be indexed before querying")
        systems = {item.document.system for item in versions}
        if len(systems) != 1:
            raise ValueError("Selected document versions must belong to the same system")
        if len(versions) == 2 and len({item.document_id for item in versions}) != 1:
            raise ValueError("Version comparison requires two versions of the same document")
        return (
            next(iter(systems)),
            [item.id for item in versions],
            sorted({item.document.document_type for item in versions}),
        )
    finally:
        db.close()


@router.post("/design-review/chat", response_model=ReviewChatResponse)
def design_review_chat(
    payload: ReviewChatRequest,
    user: CurrentUser,
    chat: DesignReviewChatDependency,
):
    """Ask Chinese or English questions against only one frozen review scope."""
    try:
        system, document_version_ids = _load_review_scope(
            payload.review_package_id, user
        )
        answer, citations, retrieval_query = chat.answer(
            question=payload.question,
            document_version_ids=document_version_ids,
            system=system,
            conversation_history=[(message.role, message.content) for message in payload.conversation_history],
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Design review chat failed for review package %s", payload.review_package_id
        )
        raise HTTPException(
            status_code=502,
            detail="The evidence answer could not be generated.",
        ) from exc
    return chat_response(answer=answer, citations=citations, retrieval_query=retrieval_query)


@router.post("/document-chat", response_model=ReviewChatResponse)
def document_chat(
    payload: DocumentChatRequest,
    chat: DesignReviewChatDependency,
):
    """Answer against an explicitly chosen current, historical, or comparison scope."""
    try:
        system, document_version_ids, document_types = _load_document_chat_scope(
            payload.document_version_ids
        )
        answer, citations, retrieval_query = chat.answer(
            question=payload.question,
            document_version_ids=document_version_ids,
            system=system,
            document_types=document_types,
            conversation_history=[(message.role, message.content) for message in payload.conversation_history],
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document chat failed for versions %s", payload.document_version_ids)
        raise HTTPException(status_code=502, detail="The evidence answer could not be generated.") from exc
    return chat_response(answer=answer, citations=citations, retrieval_query=retrieval_query)


@router.post("/design-review/chat/stream")
def stream_design_review_chat(
    payload: ReviewChatRequest,
    user: CurrentUser,
    chat: DesignReviewChatDependency,
):
    """Stream evidence-chat text; send citations only after the answer finishes."""
    try:
        system, document_version_ids = _load_review_scope(
            payload.review_package_id, user
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    history = [(message.role, message.content) for message in payload.conversation_history]

    def events():
        answer_text = ""
        try:
            yield sse_event("status", {"phase": "retrieving"})
            prepared = chat.prepare(
                question=payload.question,
                document_version_ids=document_version_ids,
                system=system,
                conversation_history=history,
            )
            yield sse_event("status", {"phase": "answering"})
            for text in chat.stream_answer(question=payload.question, prepared=prepared):
                answer_text += text
                yield sse_event("token", {"text": text})

            if prepared.no_evidence_answer:
                final_answer = prepared.no_evidence_answer.model_copy(update={"answer": answer_text})
                citations = []
            else:
                if not answer_text:
                    raise ValueError("The language model returned an empty answer.")
                final_answer = GroundedAnswer(
                    answer=answer_text,
                    limitations=UNVERIFIED_CITATIONS_LIMITATION,
                )
                citations = []
            yield sse_event(
                "final",
                chat_response(
                    answer=final_answer,
                    citations=citations,
                    retrieval_query=prepared.retrieval_query,
                ).model_dump(),
            )
        except ValueError as exc:
            yield sse_event("error", {"detail": str(exc)})
        except Exception:
            logger.exception(
                "Streaming design review chat failed for review package %s",
                payload.review_package_id,
            )
            yield sse_event("error", {"detail": "The evidence answer could not be generated."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
