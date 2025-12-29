from pydantic import BaseModel

# Input Schema: What we expect from the user
class ChatRequest(BaseModel):
    question: str
    
    # Example for documentation
    model_config = {
        "json_schema_extra": {
            "examples": [{"question": "What is the safety protocol?"}]
        }
    }

# Output Schema: What we promise to return
class ChatResponse(BaseModel):
    answer: str
    # sources: list[str] = [] # Optional: list of relevant document pages used