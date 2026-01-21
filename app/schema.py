from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# Input Schema: What we expect from the user
class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, description="User's question about warehouse automation")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "question": "What are the key considerations for conveyor system design?",
                    "session_id": "user_123"
                }
            ]
        }
    }

# Output Schema: What we promise to return
class ChatResponse(BaseModel):
    answer: str = Field(..., description="AI-generated answer based on retrieved documents")
    sources: List[str] = Field(default_factory=list, description="List of source document references")
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Answer confidence score (0-1)")
    retrieved_chunks: int = Field(default=0, description="Number of document chunks retrieved")
    web_search_used: bool = Field(default=False, description="Whether web search was used for this response")
    web_searches_remaining: int = Field(default=5, description="Remaining web searches in this session")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "answer": "Conveyor system design requires considering load capacity, speed requirements, and material handling characteristics...",
                    "sources": ["FS_Warehouse-control-system_EN.pdf - Page 12", "FS_Warehouse-control-system_EN.pdf - Page 15"],
                    "confidence_score": 0.92,
                    "retrieved_chunks": 3,
                    "web_search_used": False,
                    "web_searches_remaining": 5
                }
            ]
        }
    }

# Error Response Schema
class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Human-readable error message")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional error details")