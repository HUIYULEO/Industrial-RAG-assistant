"""
Tests for chat engine and RAG pipeline.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from langchain_core.documents import Document


def test_calculate_confidence_score(sample_documents):
    """Test confidence score calculation."""
    from app.services.chat_engine import calculate_confidence_score

    score = calculate_confidence_score(sample_documents)
    assert 0.0 <= score <= 1.0
    assert score > 0.5  # Should be relatively high with good similarity scores


def test_calculate_confidence_score_empty():
    """Test confidence score with no documents."""
    from app.services.chat_engine import calculate_confidence_score

    score = calculate_confidence_score([])
    assert score == 0.0


def test_calculate_confidence_score_weighted():
    """Test that first document has more weight."""
    from app.services.chat_engine import calculate_confidence_score

    docs_high_first = [
        Document(page_content="test", metadata={"similarity": 0.95}),
        Document(page_content="test", metadata={"similarity": 0.50})
    ]

    docs_low_first = [
        Document(page_content="test", metadata={"similarity": 0.50}),
        Document(page_content="test", metadata={"similarity": 0.95})
    ]

    score_high_first = calculate_confidence_score(docs_high_first)
    score_low_first = calculate_confidence_score(docs_low_first)

    # First document should have more influence
    assert score_high_first > score_low_first


def test_format_sources(sample_documents):
    """Test source formatting."""
    from app.services.chat_engine import format_sources

    sources = format_sources(sample_documents)
    assert len(sources) > 0
    assert "test.pdf" in sources[0]
    assert "Page" in sources[0]


def test_format_sources_no_duplicates():
    """Test that duplicate sources are removed."""
    from app.services.chat_engine import format_sources

    docs_with_duplicates = [
        Document(page_content="test1", metadata={"source": "doc.pdf", "page": 1}),
        Document(page_content="test2", metadata={"source": "doc.pdf", "page": 1}),
        Document(page_content="test3", metadata={"source": "doc.pdf", "page": 2})
    ]

    sources = format_sources(docs_with_duplicates)
    assert len(sources) == 2  # Page 1 and Page 2, no duplicates


def test_format_sources_no_page():
    """Test source formatting when page number is missing."""
    from app.services.chat_engine import format_sources

    docs_no_page = [
        Document(page_content="test", metadata={"source": "doc.pdf"})
    ]

    sources = format_sources(docs_no_page)
    assert len(sources) == 1
    assert sources[0] == "doc.pdf"


@patch("app.services.chat_engine.supabase")
@patch("app.services.chat_engine.embeddings")
@patch("app.services.chat_engine.llm")
def test_supabase_rpc_retriever_success(mock_llm, mock_embeddings, mock_supabase):
    """Test SupabaseRPCRetriever successful retrieval."""
    from app.services.chat_engine import SupabaseRPCRetriever

    # Setup mocks
    mock_embeddings.embed_query.return_value = [0.1] * 1536

    mock_response = MagicMock()
    mock_response.data = [
        {
            "id": "doc1",
            "content": "Test content",
            "similarity": 0.92,
            "metadata": {"source": "test.pdf", "page": 1}
        }
    ]
    mock_supabase.rpc.return_value.execute.return_value = mock_response

    # Create retriever
    retriever = SupabaseRPCRetriever(
        client=mock_supabase,
        embeddings=mock_embeddings,
        query_name="test_function",
        k=3
    )

    # Test retrieval
    docs = retriever._get_relevant_documents("test query")

    assert len(docs) == 1
    assert docs[0].page_content == "Test content"
    assert docs[0].metadata["similarity"] == 0.92
    assert docs[0].metadata["rank"] == 1


@patch("app.services.chat_engine.supabase")
@patch("app.services.chat_engine.embeddings")
def test_supabase_rpc_retriever_no_results(mock_embeddings, mock_supabase):
    """Test retriever when no documents found."""
    from app.services.chat_engine import SupabaseRPCRetriever

    # Setup mocks
    mock_embeddings.embed_query.return_value = [0.1] * 1536

    mock_response = MagicMock()
    mock_response.data = []
    mock_supabase.rpc.return_value.execute.return_value = mock_response

    # Create retriever
    retriever = SupabaseRPCRetriever(
        client=mock_supabase,
        embeddings=mock_embeddings,
        query_name="test_function",
        k=3
    )

    # Test retrieval
    docs = retriever._get_relevant_documents("test query")
    assert len(docs) == 0


@patch("app.services.chat_engine.supabase")
@patch("app.services.chat_engine.embeddings")
def test_supabase_rpc_retriever_embedding_error(mock_embeddings, mock_supabase):
    """Test retriever handles embedding errors."""
    from app.services.chat_engine import SupabaseRPCRetriever
    from app.core.exceptions import EmbeddingException

    # Simulate embedding error
    mock_embeddings.embed_query.side_effect = Exception("OpenAI API error")

    retriever = SupabaseRPCRetriever(
        client=mock_supabase,
        embeddings=mock_embeddings,
        query_name="test_function",
        k=3
    )

    with pytest.raises(EmbeddingException):
        retriever._get_relevant_documents("test query")


@patch("app.services.chat_engine.supabase")
@patch("app.services.chat_engine.embeddings")
def test_supabase_rpc_retriever_rpc_error(mock_embeddings, mock_supabase):
    """Test retriever handles RPC errors."""
    from app.services.chat_engine import SupabaseRPCRetriever
    from app.core.exceptions import RetrievalException

    # Setup mocks
    mock_embeddings.embed_query.return_value = [0.1] * 1536

    # Simulate RPC error
    mock_supabase.rpc.return_value.execute.side_effect = Exception("Database connection error")

    retriever = SupabaseRPCRetriever(
        client=mock_supabase,
        embeddings=mock_embeddings,
        query_name="test_function",
        k=3
    )

    with pytest.raises(RetrievalException):
        retriever._get_relevant_documents("test query")


@patch("app.services.chat_engine.SupabaseRPCRetriever")
@patch("app.services.chat_engine.llm")
def test_get_chat_response_success(mock_llm, mock_retriever_class):
    """Test successful chat response generation."""
    from app.services.chat_engine import get_chat_response

    # Mock retriever
    mock_retriever = MagicMock()
    mock_docs = [
        Document(
            page_content="WCS manages warehouse operations",
            metadata={"similarity": 0.92, "source": "test.pdf", "page": 1}
        )
    ]
    mock_retriever._get_relevant_documents.return_value = mock_docs
    mock_retriever_class.return_value = mock_retriever

    # Mock LLM chain response
    with patch("app.services.chat_engine.create_retrieval_chain") as mock_chain:
        mock_chain_instance = MagicMock()
        mock_chain_instance.invoke.return_value = {
            "answer": "WCS manages warehouse operations and material flow."
        }
        mock_chain.return_value = mock_chain_instance

        result = get_chat_response("What is WCS?")

        assert "answer" in result
        assert "sources" in result
        assert "confidence_score" in result
        assert "retrieved_chunks" in result
        assert result["retrieved_chunks"] == 1
        assert result["confidence_score"] > 0


@patch("app.services.chat_engine.SupabaseRPCRetriever")
def test_get_chat_response_no_documents(mock_retriever_class):
    """Test chat response when no documents found."""
    from app.services.chat_engine import get_chat_response

    # Mock retriever to return no documents
    mock_retriever = MagicMock()
    mock_retriever._get_relevant_documents.return_value = []
    mock_retriever_class.return_value = mock_retriever

    result = get_chat_response("Unknown question")

    assert "answer" in result
    assert "couldn't find relevant information" in result["answer"].lower()
    assert result["confidence_score"] == 0.0
    assert result["retrieved_chunks"] == 0


@patch("app.services.chat_engine.SupabaseRPCRetriever")
@patch("app.services.chat_engine.llm")
def test_get_chat_response_with_history(mock_llm, mock_retriever_class, sample_chat_history):
    """Test chat response includes conversation history."""
    from app.services.chat_engine import get_chat_response

    # Mock retriever
    mock_retriever = MagicMock()
    mock_docs = [
        Document(
            page_content="Test content",
            metadata={"similarity": 0.85, "source": "test.pdf", "page": 1}
        )
    ]
    mock_retriever._get_relevant_documents.return_value = mock_docs
    mock_retriever_class.return_value = mock_retriever

    # Mock LLM chain
    with patch("app.services.chat_engine.create_retrieval_chain") as mock_chain:
        mock_chain_instance = MagicMock()
        mock_chain_instance.invoke.return_value = {"answer": "Response based on history"}
        mock_chain.return_value = mock_chain_instance

        result = get_chat_response(
            "Follow-up question",
            session_id="test_session",
            chat_history=sample_chat_history
        )

        # Check that history context was included
        call_args = mock_chain_instance.invoke.call_args
        assert "history_context" in call_args[0][0]
        assert len(result["answer"]) > 0


@patch("app.services.chat_engine.SupabaseRPCRetriever")
@patch("app.services.chat_engine.llm")
def test_get_chat_response_llm_error(mock_llm, mock_retriever_class):
    """Test chat response handles LLM errors."""
    from app.services.chat_engine import get_chat_response
    from app.core.exceptions import LLMException

    # Mock retriever
    mock_retriever = MagicMock()
    mock_docs = [
        Document(page_content="test", metadata={"similarity": 0.9, "source": "test.pdf"})
    ]
    mock_retriever._get_relevant_documents.return_value = mock_docs
    mock_retriever_class.return_value = mock_retriever

    # Mock LLM to raise error
    with patch("app.services.chat_engine.create_retrieval_chain") as mock_chain:
        mock_chain.side_effect = Exception("LLM API timeout")

        with pytest.raises(LLMException):
            get_chat_response("Test question")
