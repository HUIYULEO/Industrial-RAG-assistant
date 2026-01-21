import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from supabase import create_client

from pydantic import Field
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun

from app.core.logging_config import get_logger
from app.core.exceptions import (
    VectorStoreException,
    EmbeddingException,
    RetrievalException,
    LLMException,
    ConfigurationException
)

load_dotenv()
logger = get_logger(__name__)

# Initialize the Supabase Client with error handling
try:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        raise ConfigurationException(
            "Missing Supabase configuration",
            {"missing": [k for k, v in {"SUPABASE_URL": supabase_url, "SUPABASE_SERVICE_KEY": supabase_key}.items() if not v]}
        )

    supabase = create_client(supabase_url, supabase_key)
    logger.info("Supabase client initialized")
except Exception as e:
    logger.error(f"Supabase initialization failed: {str(e)}")
    raise ConfigurationException("Supabase initialization failed", {"error": str(e)})

# Initialize OpenAI Embeddings and Chat Model with error handling
try:
    if not os.environ.get("OPENAI_API_KEY"):
        raise ConfigurationException("Missing OPENAI_API_KEY environment variable")

    embeddings = OpenAIEmbeddings()
    llm = ChatOpenAI(model_name="gpt-4o", temperature=0)
    logger.info("OpenAI models initialized")
except Exception as e:
    logger.error(f"OpenAI initialization failed: {str(e)}")
    raise ConfigurationException("OpenAI initialization failed", {"error": str(e)})

class SupabaseRPCRetriever(BaseRetriever):
    client: Any = Field(description="Supabase client")
    embeddings: Any = Field(description="Embeddings model")
    query_name: str = Field(description="RPC function name")
    k: int = Field(default=3, description="Number of documents to retrieve")
    content_field: str = Field(default="content", description="Field name for content")
    match_threshold: float = Field(default=0.0, description="Similarity threshold")

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
        **kwargs: Any,
    ) -> List[Document]:
        """
        Retrieve relevant documents from Supabase using RPC function.

        Args:
            query: User's question
            run_manager: Callback manager (unused but required by interface)
            **kwargs: Additional arguments (unused but required by interface)

        Returns:
            List of relevant Document objects with metadata

        Raises:
            EmbeddingException: If embedding generation fails
            RetrievalException: If document retrieval fails
        """
        try:
            query_vec = self.embeddings.embed_query(query)
        except Exception as e:
            logger.error(f"Embedding generation failed: {str(e)}")
            raise EmbeddingException("Failed to generate query embedding", {"error": str(e)})

        try:
            params: Dict[str, Any] = {
                "query_embedding": query_vec,
                "match_threshold": self.match_threshold,
                "match_count": self.k,
            }

            res = self.client.rpc(self.query_name, params).execute()
            rows = res.data or []

            documents = []
            for idx, row in enumerate(rows):
                similarity = row.get("similarity", 0.0)
                metadata = {
                    "id": row.get("id"),
                    "similarity": similarity,
                    "rank": idx + 1
                }

                if "metadata" in row and isinstance(row["metadata"], dict):
                    metadata.update(row["metadata"])

                doc = Document(
                    page_content=row.get(self.content_field) or "",
                    metadata=metadata
                )
                documents.append(doc)

            logger.info(f"Retrieved {len(documents)} documents (top similarity: {documents[0].metadata.get('similarity', 0):.2f})" if documents else "No documents retrieved")

            return documents

        except Exception as e:
            logger.error(f"Document retrieval failed: {str(e)}")
            raise RetrievalException("Document retrieval failed", {"error": str(e)})


def calculate_confidence_score(retrieved_docs: List[Document]) -> float:
    """
    Calculate confidence score based on retrieved documents' similarity scores.

    Args:
        retrieved_docs: List of retrieved documents with similarity metadata

    Returns:
        Confidence score between 0 and 1
    """
    if not retrieved_docs:
        return 0.0

    similarities = [doc.metadata.get("similarity", 0.0) for doc in retrieved_docs]

    # Calculate weighted average (first document has more weight)
    weights = [1.0 / (i + 1) for i in range(len(similarities))]
    weighted_sum = sum(s * w for s, w in zip(similarities, weights))
    total_weight = sum(weights)

    confidence = weighted_sum / total_weight if total_weight > 0 else 0.0
    return round(confidence, 4)


def format_sources(retrieved_docs: List[Document]) -> List[str]:
    """
    Format source references from retrieved documents.

    Args:
        retrieved_docs: List of retrieved documents

    Returns:
        List of formatted source strings
    """
    sources = []
    seen = set()

    for doc in retrieved_docs:
        metadata = doc.metadata
        source = metadata.get("source", "Unknown")
        page = metadata.get("page", metadata.get("page_number"))

        if page is not None:
            source_str = f"{source} - Page {page}"
        else:
            source_str = source

        # Avoid duplicate sources
        if source_str not in seen:
            sources.append(source_str)
            seen.add(source_str)

    return sources


def get_chat_response(
    question: str,
    session_id: str = "default",
    chat_history: Optional[List[Dict[str, str]]] = None,
    use_hybrid_retrieval: bool = True
) -> Dict[str, Any]:
    """
    Generate a chat response using RAG pipeline with conversation memory.
    Supports hybrid retrieval (local docs + web search fallback).

    Args:
        question: User's question
        session_id: Session identifier for tracking
        chat_history: Previous conversation history (list of {"question": str, "answer": str})
        use_hybrid_retrieval: Whether to use hybrid retriever with web search fallback

    Returns:
        Dictionary containing:
            - answer: The generated response
            - sources: List of source references
            - confidence_score: Confidence score (0-1)
            - retrieved_chunks: Number of documents retrieved
            - web_search_used: Boolean if web search was triggered
            - web_searches_remaining: Remaining web searches for session

    Raises:
        LLMException: If answer generation fails
    """
    logger.info(f"Chat request - session={session_id}")

    try:
        # Initialize local retriever
        local_retriever = SupabaseRPCRetriever(
            client=supabase,
            embeddings=embeddings,
            query_name="match_whdocuments",
            k=5,
            content_field="content",
        )

        # Use hybrid retrieval if enabled
        web_search_used = False
        web_searches_remaining = 5

        if use_hybrid_retrieval:
            try:
                from app.services.hybrid_retriever import HybridRetriever
                hybrid_retriever = HybridRetriever(
                    local_retriever=local_retriever,
                    max_searches_per_session=5,
                    min_confidence_threshold=0.5
                )

                retrieval_result = hybrid_retriever.retrieve(question, session_id)
                retrieved_docs = retrieval_result["documents"]
                confidence = retrieval_result["confidence_score"]
                sources = retrieval_result["sources"]
                web_search_used = retrieval_result["web_search_used"]
                web_searches_remaining = retrieval_result["web_searches_remaining"]

            except ImportError:
                logger.warning("Hybrid retriever unavailable, using local only")
                retrieved_docs = local_retriever._get_relevant_documents(question)
                confidence = calculate_confidence_score(retrieved_docs)
                sources = format_sources(retrieved_docs)
        else:
            retrieved_docs = local_retriever._get_relevant_documents(question)

        # If not using hybrid retrieval, calculate confidence and sources
        if not use_hybrid_retrieval:
            confidence = calculate_confidence_score(retrieved_docs)
            sources = format_sources(retrieved_docs)

        if not retrieved_docs:
            logger.warning("No relevant documents found")
            return {
                "answer": "I couldn't find relevant information in the warehouse automation documentation to answer your question. Could you please rephrase or ask about warehouse control systems, conveyor systems, or automation protocols?",
                "sources": [],
                "confidence_score": 0.0,
                "retrieved_chunks": 0,
                "web_search_used": False,
                "web_searches_remaining": web_searches_remaining
            }

        # Build prompt with conversation history
        history_context = ""
        if chat_history and len(chat_history) > 0:
            # Include last 3 exchanges for context
            recent_history = chat_history[-3:]
            history_context = "\n\nPrevious conversation:\n"
            for exchange in recent_history:
                history_context += f"Q: {exchange['question']}\nA: {exchange['answer']}\n\n"

        prompt = ChatPromptTemplate.from_template(
            """You are an expert Warehouse Automation Design Decision Assistant specializing in warehouse control systems (WCS), material handling, and industrial automation.

            Your role is to help engineers and decision-makers with:
            - Warehouse control system design and configuration
            - Conveyor system specifications and optimization
            - Automation protocol recommendations
            - Safety and compliance guidelines
            - Integration strategies for warehouse equipment

            Use the following context from technical documentation to answer the question accurately and professionally.

            IMPORTANT GUIDELINES:
            - If the answer is in the context, provide detailed, technical explanations
            - If uncertain, acknowledge limitations and suggest what additional information might help
            - Reference specific technical specifications when available
            - Consider the conversation history for continuity{history_context}

            Context from documentation:
            {context}

            Current Question: {input}

            Provide a clear, professional answer:"""
        )

        # Create and invoke document chain
        document_chain = create_stuff_documents_chain(llm, prompt)
        answer = document_chain.invoke({
            "input": question,
            "context": retrieved_docs,
            "history_context": history_context
        })

        logger.info(f"Response generated - confidence={confidence:.2f}, docs={len(retrieved_docs)}, web_search={web_search_used}")

        return {
            "answer": answer,
            "sources": sources,
            "confidence_score": confidence,
            "retrieved_chunks": len(retrieved_docs),
            "web_search_used": web_search_used,
            "web_searches_remaining": web_searches_remaining
        }

    except (EmbeddingException, RetrievalException):
        raise

    except Exception as e:
        logger.error(f"Chat response failed: {str(e)}")
        raise LLMException("Failed to generate response", {"error": str(e)})
