import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from app.schema import ChatRequest, ChatResponse, ErrorResponse
from app.services.chat_engine import get_chat_response
from app.core.logging_config import setup_logging, get_logger
from app.core.exceptions import (
    IndustrialRAGException,
    VectorStoreException,
    EmbeddingException,
    RetrievalException,
    LLMException,
    ValidationException
)

# Initialize logging
setup_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

# Session storage (in production, use Redis or database)
chat_sessions = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the FastAPI application."""
    logger.info("Starting Industrial RAG Assistant API...")
    logger.info("Verifying environment variables...")

    required_env_vars = ["OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]

    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        raise RuntimeError(f"Missing environment variables: {missing_vars}")

    logger.info("All environment variables verified")
    logger.info("Application startup complete")

    yield

    logger.info("Shutting down Industrial RAG Assistant API...")
    chat_sessions.clear()

# Initialize FastAPI app
app = FastAPI(
    title="Warehouse Automation Design Decision Assistant",
    description="An intelligent RAG-based API for warehouse automation design decisions and industrial documentation queries.",
    version="2.0.0",
    lifespan=lifespan
)

# Global exception handlers
@app.exception_handler(IndustrialRAGException)
async def industrial_rag_exception_handler(request, exc: IndustrialRAGException):
    logger.error(f"Industrial RAG error: {exc.message}", extra={"details": exc.details})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error=exc.__class__.__name__,
            message=exc.message,
            details=exc.details
        ).model_dump()
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    logger.error(f"Unexpected error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="InternalServerError",
            message="An unexpected error occurred. Please try again later.",
            details={"error_type": exc.__class__.__name__}
        ).model_dump()
    )

@app.get("/")
async def health_check():
    """Health check endpoint to verify the server is running."""
    logger.info("Health check requested")
    return {
        "status": "ok",
        "message": "Warehouse Automation Design Decision Assistant is online",
        "version": "2.0.0"
    }

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Main chat endpoint for warehouse automation design queries.

    Receives a question, retrieves relevant context from the vector database,
    and returns an AI-generated answer with metadata.

    Args:
        request: ChatRequest containing the user's question and optional session_id

    Returns:
        ChatResponse with the answer, sources, and evaluation metrics

    Raises:
        HTTPException: For validation errors or processing failures
    """
    logger.info(f"Chat request received - Session: {request.session_id}, Question length: {len(request.question)}")

    try:
        # Validate input
        if not request.question or len(request.question.strip()) == 0:
            logger.warning("Empty question received")
            raise ValidationException("Question cannot be empty")

        if len(request.question) > 1000:
            logger.warning(f"Question too long: {len(request.question)} characters")
            raise ValidationException("Question exceeds maximum length of 1000 characters")

        # Get or create session
        session_id = request.session_id or "default"
        if session_id not in chat_sessions:
            logger.info(f"Creating new chat session: {session_id}")
            chat_sessions[session_id] = {"history": []}

        # Get chat response with session context
        logger.debug(f"Processing question: {request.question[:100]}...")
        response = get_chat_response(
            question=request.question,
            session_id=session_id,
            chat_history=chat_sessions[session_id]["history"]
        )

        # Store in session history
        chat_sessions[session_id]["history"].append({
            "question": request.question,
            "answer": response["answer"]
        })

        # Keep only last 10 exchanges to prevent memory bloat
        if len(chat_sessions[session_id]["history"]) > 10:
            chat_sessions[session_id]["history"] = chat_sessions[session_id]["history"][-10:]

        logger.info(f"Chat response generated successfully - Session: {session_id}, Sources: {len(response.get('sources', []))}")

        return ChatResponse(
            answer=response["answer"],
            sources=response.get("sources", []),
            confidence_score=response.get("confidence_score"),
            retrieved_chunks=response.get("retrieved_chunks", 0)
        )

    except ValidationException as e:
        logger.warning(f"Validation error: {e.message}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)

    except IndustrialRAGException as e:
        logger.error(f"RAG processing error: {e.message}", extra={"details": e.details})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{e.__class__.__name__}: {e.message}"
        )

    except Exception as e:
        logger.error(f"Unexpected error in chat endpoint: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing your request"
        )

@app.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    """
    Retrieve chat history for a specific session.

    Args:
        session_id: The session identifier

    Returns:
        List of previous questions and answers
    """
    logger.info(f"History requested for session: {session_id}")

    if session_id not in chat_sessions:
        logger.warning(f"Session not found: {session_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return {
        "session_id": session_id,
        "history": chat_sessions[session_id]["history"]
    }

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    Delete a chat session and its history.

    Args:
        session_id: The session identifier to delete

    Returns:
        Confirmation message
    """
    logger.info(f"Delete request for session: {session_id}")

    if session_id in chat_sessions:
        del chat_sessions[session_id]
        logger.info(f"Session deleted: {session_id}")
        return {"message": f"Session {session_id} deleted successfully"}
    else:
        logger.warning(f"Session not found for deletion: {session_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    
