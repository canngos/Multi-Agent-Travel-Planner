"""Web search through Tavily (opening hours, ticket and fare prices, operators)."""

from ._http import api_key, post_json
from ._result import ToolError, ok, safe_tool

TAVILY_URL = "https://api.tavily.com/search"
MAX_CONTENT_CHARS = 600


@safe_tool
async def web_search(query: str, max_results: int = 5) -> dict:
    """Searches the web and returns a short answer plus the source pages.

    Use it for facts other tools lack: opening hours, ticket prices, ferry/bus
    fares, operators. Any price taken from these results is a web estimate:
    tag it price_source "web_estimate" and cite the result URL.

    Args:
        query: Search query, e.g. "Jerónimos Monastery ticket price 2026".
        max_results: Number of sources to return (1-8).
    """
    key = api_key("TAVILY_API_KEY")
    data = await post_json(
        TAVILY_URL,
        namespace="tavily",
        max_age_s=7 * 24 * 3600,
        json={
            "query": query,
            "max_results": max(1, min(int(max_results), 8)),
            "search_depth": "basic",  # 1 credit
            "include_answer": True,
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    results = [
        {"title": r.get("title"), "url": r.get("url"), "content": (r.get("content") or "")[:MAX_CONTENT_CHARS]}
        for r in data.get("results", [])
    ]
    if not results:
        raise ToolError(f"No web results for '{query}'")
    return ok(results, answer=data.get("answer"), price_source_hint="web_estimate")
