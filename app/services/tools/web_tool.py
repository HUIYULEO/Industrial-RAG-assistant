"""
Web Search Tool for querying the internet via DuckDuckGo.

This tool is used as a fallback when local documentation
doesn't contain sufficient information, or for current/trending topics.
"""

from typing import List, Dict, Any

from langchain_core.tools import tool

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _perform_web_search(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Perform web search using DuckDuckGo.

    Args:
        query: Search query
        max_results: Maximum number of results to return

    Returns:
        List of search results with title, body, and href
    """
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            logger.info(f"Web search returned {len(results)} results")
            return results

    except ImportError:
        logger.error("duckduckgo-search package not installed")
        return []
    except Exception as e:
        logger.error(f"Web search failed: {str(e)}")
        return []


@tool
def web_search(query: str) -> str:
    """
    Search the internet for current information about warehouse automation.

    Use this tool when:
    - The local documentation doesn't have the answer
    - The user asks about recent developments or news
    - The user asks about products, vendors, or market trends
    - You need to verify or supplement local documentation

    Args:
        query: The search query for web search

    Returns:
        Search results with titles, snippets, and source URLs
    """
    results = _perform_web_search(query, max_results=3)

    if not results:
        return "Web search returned no results. Please try a different query."

    # Format results
    formatted = []
    for i, result in enumerate(results, 1):
        title = result.get("title", "No title")
        body = result.get("body", "No description")
        href = result.get("href", "No URL")

        formatted.append(
            f"[Web Result {i}]\n"
            f"Title: {title}\n"
            f"URL: {href}\n"
            f"Summary: {body}"
        )

    return "\n\n---\n\n".join(formatted)
