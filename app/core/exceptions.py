"""
Custom exception classes for the Industrial RAG application.
"""

class IndustrialRAGException(Exception):
    """Base exception for all Industrial RAG errors."""
    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

class VectorStoreException(IndustrialRAGException):
    """Raised when vector store operations fail."""
    pass

class EmbeddingException(IndustrialRAGException):
    """Raised when embedding generation fails."""
    pass

class RetrievalException(IndustrialRAGException):
    """Raised when document retrieval fails."""
    pass

class LLMException(IndustrialRAGException):
    """Raised when LLM operations fail."""
    pass

class ConfigurationException(IndustrialRAGException):
    """Raised when configuration is invalid or missing."""
    pass

class ValidationException(IndustrialRAGException):
    """Raised when input validation fails."""
    pass
