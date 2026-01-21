"""
Hybrid Retriever - Combines local vector search with FREE web search (DuckDuckGo).
Falls back to web search when local document confidence is low.
"""
import os
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document

from app.core.logging_config import get_logger
from app.core.exceptions import RetrievalException

logger = get_logger(__name__)

# Feature flag for web search
ENABLE_WEB_SEARCH = os.getenv("ENABLE_WEB_SEARCH", "true").lower() == "true"
MAX_WEB_SEARCHES_PER_SESSION = int(os.getenv("MAX_WEB_SEARCHES_PER_SESSION", "5"))


class HybridRetriever:
    """
    Hybrid retriever that combines local vector search with FREE DuckDuckGo web search.

    Features:
    - Always searches local documents first
    - Falls back to web search when confidence is low
    - FREE tier: Max 5 web searches per session
    - No API keys required for web search
    """

    def __init__(
        self,
        local_retriever: Any,
        max_searches_per_session: int = MAX_WEB_SEARCHES_PER_SESSION,
        min_confidence_threshold: float = 0.5
    ):
        """
        Initialize the hybrid retriever.

        Args:
            local_retriever: The local vector store retriever (Supabase)
            max_searches_per_session: Maximum web searches allowed per session (FREE tier limit)
            min_confidence_threshold: Minimum confidence score before triggering web search
        """
        self.local_retriever = local_retriever
        self.max_searches = max_searches_per_session
        self.min_confidence = min_confidence_threshold
        self.search_count: Dict[str, int] = {}  # Track per session

        # Initialize DuckDuckGo search (lazy load to avoid import errors)
        self._ddg = None

    @property
    def ddg(self):
        """Lazy load DuckDuckGo search client."""
        if self._ddg is None:
            try:
                from duckduckgo_search import DDGS
                self._ddg = DDGS()
            except ImportError:
                logger.warning("duckduckgo-search not installed, web search disabled")
                self._ddg = False
        return self._ddg

    def retrieve(
        self,
        query: str,
        session_id: str = "default",
        k: int = 5
    ) -> Dict[str, Any]:
        """
        Retrieve documents using hybrid strategy.

        Args:
            query: User's search query
            session_id: Session identifier for tracking web search usage
            k: Number of documents to retrieve from local store

        Returns:
            Dict containing:
                - documents: List of Document objects
                - confidence_score: Weighted confidence score
                - sources: List of source citations
                - web_search_used: Boolean indicating if web search was used
                - web_searches_remaining: Number of web searches left in session
        """
        # Step 1: Always search local documents first
        local_docs = self._search_local(query)
        local_confidence = self._calculate_confidence(local_docs)

        # Step 2: Determine if web search is needed
        web_search_used = False
        web_docs = []

        should_search_web = (
            ENABLE_WEB_SEARCH and
            self.ddg and
            self._can_search_web(session_id) and
            (local_confidence < self.min_confidence or len(local_docs) == 0)
        )

        if should_search_web:
            logger.info(f"Web search triggered (local confidence {local_confidence:.2f} < {self.min_confidence})")
            web_docs = self._search_web(query)
            self._increment_search_count(session_id)
            web_search_used = True

        # Step 3: Merge results
        all_docs = self._merge_documents(local_docs, web_docs)

        # Step 4: Calculate final confidence
        final_confidence = self._calculate_confidence(all_docs)

        # Step 5: Format sources
        sources = self._format_sources(all_docs)

        return {
            "documents": all_docs,
            "confidence_score": final_confidence,
            "sources": sources,
            "web_search_used": web_search_used,
            "web_searches_remaining": self._get_remaining_searches(session_id)
        }

    def _search_local(self, query: str) -> List[Document]:
        """Search local vector store."""
        try:
            docs = self.local_retriever._get_relevant_documents(query)
            return docs
        except Exception as e:
            logger.error(f"Local search failed: {e}")
            return []

    def _search_web(self, query: str, max_results: int = 3) -> List[Document]:
        """
        Search web using DuckDuckGo (100% FREE).

        Args:
            query: Search query
            max_results: Maximum number of results (limited to control processing)

        Returns:
            List of Document objects from web search
        """
        if not self.ddg:
            return []

        try:
            # Enhance query for warehouse automation context
            search_query = f"{query} warehouse automation"
            results = self.ddg.text(search_query, max_results=max_results)

            documents = []
            for idx, result in enumerate(results):
                doc = Document(
                    page_content=f"{result.get('title', '')}\n\n{result.get('body', '')}",
                    metadata={
                        "source": "web",
                        "url": result.get("href", ""),
                        "title": result.get("title", ""),
                        "similarity": 0.6 - (idx * 0.05),  # Decreasing pseudo-similarity
                        "rank": idx + 1
                    }
                )
                documents.append(doc)

            return documents

        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return []

    def _merge_documents(
        self,
        local_docs: List[Document],
        web_docs: List[Document]
    ) -> List[Document]:
        """
        Merge local and web documents, prioritizing local results.

        Args:
            local_docs: Documents from local vector store
            web_docs: Documents from web search

        Returns:
            Merged list with local docs first
        """
        # Prioritize local documents
        merged = list(local_docs)

        # Add web docs (up to 3)
        for doc in web_docs[:3]:
            merged.append(doc)

        return merged

    def _calculate_confidence(self, docs: List[Document]) -> float:
        """
        Calculate weighted confidence score from documents.

        Uses weighted average where first documents have more weight.
        """
        if not docs:
            return 0.0

        similarities = [doc.metadata.get("similarity", 0.5) for doc in docs]

        # Weighted average (first doc has weight 1.0, second 0.5, etc.)
        weights = [1.0 / (i + 1) for i in range(len(similarities))]
        weighted_sum = sum(s * w for s, w in zip(similarities, weights))
        total_weight = sum(weights)

        confidence = weighted_sum / total_weight if total_weight > 0 else 0.0
        return round(confidence, 4)

    def _format_sources(self, docs: List[Document]) -> List[str]:
        """Format source citations from documents."""
        sources = []
        seen = set()

        for doc in docs:
            metadata = doc.metadata

            if metadata.get("source") == "web":
                # Web source
                title = metadata.get("title", "Web Source")
                url = metadata.get("url", "")
                source_str = f"🌐 {title}"
                if url:
                    source_str += f" ({url[:50]}...)" if len(url) > 50 else f" ({url})"
            else:
                # Local document source
                source = metadata.get("source", "Unknown")
                page = metadata.get("page", metadata.get("page_number"))
                source_str = f"📄 {source}"
                if page is not None:
                    source_str += f" - Page {page}"

            if source_str not in seen:
                sources.append(source_str)
                seen.add(source_str)

        return sources

    def _can_search_web(self, session_id: str) -> bool:
        """Check if session has web searches remaining (FREE tier limit)."""
        current_count = self.search_count.get(session_id, 0)
        can_search = current_count < self.max_searches

        if not can_search:
            logger.warning(f"Session {session_id} reached web search limit ({self.max_searches})")

        return can_search

    def _increment_search_count(self, session_id: str) -> None:
        """Increment web search count for session."""
        self.search_count[session_id] = self.search_count.get(session_id, 0) + 1

    def _get_remaining_searches(self, session_id: str) -> int:
        """Get remaining web searches for session."""
        return max(0, self.max_searches - self.search_count.get(session_id, 0))

    def reset_session(self, session_id: str) -> None:
        """Reset web search count for a session."""
        if session_id in self.search_count:
            del self.search_count[session_id]
