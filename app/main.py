from fastapi import FastAPI, HTTPException
from app.schema import ChatRequest, ChatResponse
from app.services.chat_engine import get_chat_response

#initialize FastAPI app
app = FastAPI(
    title="Industrial AI Assistant",
    description="A RAG-based API for querying industrial documents.",
    version="1.0.0"
)

@app.get("/")
async def health_check():
    """Simple endpoint to check if the server is running."""
    return {"status": "ok", "message": "System is online"}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Receives a question, searches the vector DB, and returns an answer.

    :param request: Description
    :type request: ChatRequest
    """
    try:
        #call your AI logic here
        #note: in a real app, consider making get_chat_response async
        answer = get_chat_response(request.question)
        return ChatResponse(answer=answer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    