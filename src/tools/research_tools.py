"""Research tools: web search, web fetch, deep research, strategy library access.

Primary provider: Parallel.ai (Search API, Extract API, Task API)
Fallback: Brave Search / DuckDuckGo for search, httpx/BeautifulSoup for fetch
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING


import httpx
from bs4 import BeautifulSoup

from src.tools import AgentRole, ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from src.memory.strategy import StrategyLibrary

logger = logging.getLogger("kaggle-company.tools.research")

PARALLEL_API_BASE = "https://api.parallel.ai/v1beta"
MAX_CONTENT_LENGTH = 100000  # 100K chars — generous for Kaggle pages and research results


# ---------------------------------------------------------------------------
# Parallel.ai Search API
# ---------------------------------------------------------------------------

async def _parallel_search(query: str, max_results: int, api_key: str) -> str:
    """Search using Parallel.ai Search API — optimized for AI agent consumption."""
    logger.info("Parallel Search: query=%s, max_results=%d", query[:100], max_results)
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{PARALLEL_API_BASE}/search",
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "objective": query,
                    "search_queries": [query],
                    "mode": "agentic",
                    "max_results": max_results,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "excerpts": item.get("excerpts", []),
            })

        logger.info("Parallel Search returned %d results for: %s", len(results), query[:80])
        return json.dumps({"query": query, "results": results, "source": "parallel"}, indent=2)
    except Exception as e:
        logger.warning("Parallel search failed: %s", e)
        raise


# ---------------------------------------------------------------------------
# Parallel.ai Extract API
# ---------------------------------------------------------------------------

async def _parallel_extract(url: str, api_key: str, objective: str = "", max_age_seconds: int | None = None) -> str:
    """Fetch and extract content using Parallel.ai Extract API — handles JS-rendered pages.

    Args:
        url: The URL to extract.
        api_key: Parallel.ai API key.
        objective: What information to focus on (enables focused excerpts).
        max_age_seconds: Force a live fetch if cached content is older than this.
                        Minimum 600 seconds (10 min). Use for frequently-updated pages.
    """
    logger.info("Parallel Extract: url=%s, objective=%s, max_age_seconds=%s", url[:100], (objective or "none")[:80], max_age_seconds)
    try:
        payload: dict[str, Any] = {
            "urls": [url],
            "full_content": True,
        }
        if objective:
            payload["objective"] = objective
            payload["excerpts"] = True

        if max_age_seconds is not None:
            payload["fetch_policy"] = {"max_age_seconds": max(max_age_seconds, 600)}

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{PARALLEL_API_BASE}/extract",
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract API returns a list of results
        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list) and results:
            result = results[0]
        elif isinstance(results, dict):
            result = results
        else:
            return f"No content extracted from {url}"

        parts = []
        title = result.get("title", "")
        if title:
            parts.append(f"# {title}\n")

        # Include excerpts if available (objective-matched content)
        excerpts = result.get("excerpts", [])
        if excerpts:
            parts.append("## Key Excerpts\n")
            for excerpt in excerpts:
                if isinstance(excerpt, str):
                    parts.append(excerpt)
                elif isinstance(excerpt, dict):
                    parts.append(excerpt.get("text", str(excerpt)))
            parts.append("")

        # Include full content
        full_content = result.get("full_content", "") or result.get("content", "")
        if full_content:
            parts.append(full_content)

        content = "\n".join(parts)
        logger.info("Parallel Extract returned %d chars for: %s", len(content), url[:80])
        return content
    except httpx.ReadTimeout:
        logger.warning("Parallel extract timed out for %s (300s limit)", url)
        raise
    except httpx.HTTPStatusError as e:
        logger.warning("Parallel extract HTTP %d for %s: %s", e.response.status_code, url, e.response.text[:200])
        raise
    except Exception as e:
        logger.warning("Parallel extract failed for %s: %s (%s)", url, e, type(e).__name__)
        raise


# ---------------------------------------------------------------------------
# Parallel.ai Task API (Deep Research)
# ---------------------------------------------------------------------------

async def _parallel_deep_research(
    prompt: str,
    processor: str,
    api_key: str,
    source_domains: list[str] | None = None,
) -> str:
    """Run deep research using Parallel.ai Task API."""
    import time as _time
    from parallel import AsyncParallel
    from parallel.types import TextSchemaParam, SourcePolicy

    logger.info("Deep Research START: processor=%s, prompt=%s, domains=%s",
                processor, prompt[:150], source_domains)
    start_time = _time.time()

    client = AsyncParallel(api_key=api_key)

    try:
        source_policy = None
        if source_domains:
            source_policy = SourcePolicy(include_domains=source_domains)

        run = await client.task_run.create(
            input=prompt,
            processor=processor,
            source_policy=source_policy,
            task_spec={"output_schema": TextSchemaParam(type="text")},
        )
        logger.info("Deep Research submitted: run_id=%s, processor=%s", run.run_id, processor)

        # result() blocks until completion (with server-side timeout)
        result = await client.task_run.result(
            run.run_id,
            api_timeout=3600,  # 1 hour max wait (ultra-tier runs up to 45 min)
        )

        duration = _time.time() - start_time
        output = result.output
        if hasattr(output, "content"):
            logger.info("Deep Research DONE: run_id=%s, duration=%.1fs, result=%d chars",
                        run.run_id, duration, len(output.content))
            return output.content
        content = json.dumps(output, default=str)
        logger.info("Deep Research DONE: run_id=%s, duration=%.1fs, result=%d chars",
                    run.run_id, duration, len(content))
        return content
    except Exception as e:
        duration = _time.time() - start_time
        logger.error("Deep Research FAILED after %.1fs: %s", duration, e)
        return f"Deep research error: {e}"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Brave Search API (fallback)
# ---------------------------------------------------------------------------

async def _brave_search(query: str, max_results: int, api_key: str) -> str:
    """Search using Brave Search API (fallback)."""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                params={"q": query, "count": max_results},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            })

        return json.dumps({"query": query, "results": results, "source": "brave"}, indent=2)
    except Exception as e:
        logger.warning("Brave search failed, falling back to DuckDuckGo: %s", e)
        return await _duckduckgo_search(query, max_results)


# ---------------------------------------------------------------------------
# DuckDuckGo (last-resort fallback)
# ---------------------------------------------------------------------------

async def _duckduckgo_search(query: str, max_results: int) -> str:
    """Search using DuckDuckGo HTML (no API key needed)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=300.0) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; KaggleCompanyBot/1.0)"},
            )
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for result_div in soup.select(".result")[:max_results]:
            title_elem = result_div.select_one(".result__title a, .result__a")
            snippet_elem = result_div.select_one(".result__snippet")

            title = title_elem.get_text(strip=True) if title_elem else ""
            url = ""
            if title_elem and title_elem.get("href"):
                href = title_elem["href"]
                if "uddg=" in href:
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    url = parsed.get("uddg", [href])[0]
                else:
                    url = href
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

            if title:
                results.append({"title": title, "url": url, "snippet": snippet})

        if not results:
            return json.dumps({
                "query": query,
                "results": [],
                "note": "No results found. Try different search terms.",
            })

        return json.dumps({"query": query, "results": results, "source": "duckduckgo"}, indent=2)
    except Exception as e:
        return f"Error searching: {e}. Try web_fetch on a known URL instead."


# ---------------------------------------------------------------------------
# httpx/BeautifulSoup fetch (fallback)
# ---------------------------------------------------------------------------

async def _httpx_fetch(url: str) -> str:
    """Fetch a web page using httpx + BeautifulSoup (fallback)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=300.0) as client:
            resp = await client.get(url, headers={"User-Agent": "KaggleCompanyBot/1.0"})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")

            if "json" in content_type:
                text = json.dumps(resp.json(), indent=2)
                if len(text) > MAX_CONTENT_LENGTH:
                    return text[:MAX_CONTENT_LENGTH] + "\n\n[Content truncated — original was %d chars]" % len(text)
                return text

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)
            if len(text) > MAX_CONTENT_LENGTH:
                return text[:MAX_CONTENT_LENGTH] + "\n\n[Content truncated — original was %d chars]" % len(text)
            return text
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {e}"
    except Exception as e:
        return f"Error fetching {url}: {e}"


# ---------------------------------------------------------------------------
# Strategy & Skill tools (unchanged)
# ---------------------------------------------------------------------------

def make_strategy_tools(strategy_library: StrategyLibrary) -> list[ToolDefinition]:
    """Create strategy library access tools."""

    async def get_strategy(params: dict[str, Any]) -> str:
        """Get a strategy document."""
        name = params.get("name", "")
        if not name:
            return "Error: strategy name required. Use list_strategies to see available."
        content = strategy_library.get(name)
        if content is None:
            return f"Strategy '{name}' not found. Use list_strategies to see available."
        return content

    async def list_strategies(params: dict[str, Any]) -> str:
        """List available strategy documents."""
        strategies = strategy_library.list_available()
        if not strategies:
            return "No strategies available yet."
        return json.dumps({"strategies": strategies})

    async def update_strategy(params: dict[str, Any]) -> str:
        """Update a strategy document (consolidation agent only)."""
        name = params.get("name", "")
        content = params.get("content", "")
        if not name or not content:
            return "Error: name and content required"
        strategy_library.write(name, content)
        return f"Updated strategy: {name}"

    return [
        ToolDefinition(
            name="get_strategy",
            description="Read a strategy document from the knowledge library.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Strategy name (e.g., 'tabular-methods')"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=get_strategy,
            allowed_roles={AgentRole.VP, AgentRole.WORKER, AgentRole.SUBAGENT, AgentRole.CONSOLIDATION},
        ),
        ToolDefinition(
            name="list_strategies",
            description="List all available strategy documents in the knowledge library.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=list_strategies,
            allowed_roles={AgentRole.VP, AgentRole.WORKER, AgentRole.SUBAGENT, AgentRole.CONSOLIDATION},
        ),
        ToolDefinition(
            name="update_strategy",
            description="Update or create a strategy document in the knowledge library.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Strategy name"},
                    "content": {"type": "string", "description": "Full markdown content"},
                },
                "required": ["name", "content"],
                "additionalProperties": False,
            },
            handler=update_strategy,
            allowed_roles={AgentRole.CONSOLIDATION},
        ),
    ]



# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_research_tools(
    registry: ToolRegistry,
    strategy_library: StrategyLibrary,
    brave_search_api_key: str = "",
    parallel_api_key: str = "",
) -> None:
    """Register all research tools."""
    _parallel_key = parallel_api_key
    _brave_key = brave_search_api_key

    # --- web_search: Parallel → Brave → DuckDuckGo ---
    async def _web_search_handler(params: dict[str, Any]) -> str:
        query = params.get("query", "")
        max_results = min(params.get("max_results", 20), 50)

        if not query:
            return "Error: query is required"

        if _parallel_key:
            try:
                return await _parallel_search(query, max_results, _parallel_key)
            except Exception:
                logger.info("Parallel search failed, trying fallback")

        if _brave_key:
            # Brave API max count is 20
            return await _brave_search(query, min(max_results, 20), _brave_key)

        return await _duckduckgo_search(query, max_results)

    registry.register(ToolDefinition(
        name="web_search",
        description=(
            "Search the web and return a list of results with titles, URLs, and content excerpts. "
            "Use this when you need to discover pages, find URLs, or get a broad overview of what's "
            "available on a topic. Returns JSON with a list of results — each has a title, URL, and "
            "excerpts from the page. Use web_fetch to read the full content of any URL you find. "
            "Supports natural language queries and keyword searches."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query — natural language (e.g., 'active kaggle competitions with cash prizes') or keywords"},
                "max_results": {"type": "integer", "description": "Maximum number of results to return (1-50, default 20)", "default": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=_web_search_handler,
        allowed_roles={AgentRole.VP, AgentRole.WORKER, AgentRole.SUBAGENT},
        input_examples=[
            {"query": "kaggle protein function prediction winning solution 2025", "max_results": 20},
            {"query": "ESM2 protein language model fine-tuning benchmark results"},
        ],
    ))

    # --- web_fetch: Parallel Extract → httpx/BS4 ---
    async def _web_fetch_handler(params: dict[str, Any]) -> str:
        url = params.get("url", "")
        objective = params.get("objective", "")
        fresh = params.get("fresh", False)

        if not url:
            return "Error: url is required"

        # If fresh=True, force a live fetch (min 600 seconds = 10 min cache bypass)
        max_age_seconds = 600 if fresh else None

        if _parallel_key:
            try:
                return await _parallel_extract(url, _parallel_key, objective, max_age_seconds)
            except Exception:
                logger.info("Parallel extract failed for %s, using httpx fallback", url)

        return await _httpx_fetch(url)

    registry.register(ToolDefinition(
        name="web_fetch",
        description=(
            "Fetch a web page and extract its content as clean text/markdown. "
            "Uses Parallel.ai Extract API, which handles JavaScript-rendered pages (Kaggle SPAs, dynamic content), "
            "PDFs, and other complex formats. Returns the page title and full page content as markdown. "
            "IMPORTANT: Fetch the URL once and read the full content. Do not make multiple fetches of the same URL with different objectives — "
            "this wastes time and API calls. The objective parameter is only for very large pages where you want the API to highlight specific sections; "
            "most of the time, omit it and read the full result. Use this when you have a specific URL to read — competition pages, "
            "papers, GitHub repos, discussion threads. For discovering URLs when you don't know them yet, use web_search. "
            "Use fresh=True for frequently-updated pages (leaderboards, live discussions) to bypass cache and get latest content."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full URL to fetch, e.g. 'https://www.kaggle.com/competitions/cafa-6-protein-function-prediction'"},
                "objective": {"type": "string", "description": "RARELY USED: For very large pages, optionally specify what you're looking for so the API can highlight relevant sections. Most pages: omit this and read the full result. Example: 'evaluation metric' for a competition rules page (not for every fetch)."},
                "fresh": {"type": "boolean", "description": "Force a live fetch instead of cached content (max age ~10 min). Use for competition leaderboards, live discussion threads, and other frequently-updated pages. Default false.", "default": False},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        handler=_web_fetch_handler,
        allowed_roles={AgentRole.VP, AgentRole.WORKER, AgentRole.SUBAGENT},
        input_examples=[
            {"url": "https://www.kaggle.com/competitions"},
            {"url": "https://www.kaggle.com/competitions/competition-slug/leaderboard", "fresh": True},
            {"url": "https://arxiv.org/abs/2401.12345"},
        ],
    ))

    # --- deep_research: Parallel Task API ---
    if _parallel_key:
        async def _deep_research_handler(params: dict[str, Any]) -> str:
            prompt = params.get("prompt", "")
            processor = params.get("processor", "core")
            source_domains = params.get("source_domains", None)

            if not prompt:
                return "Error: prompt is required"

            valid_processors = {"lite", "base", "core", "core2x", "pro", "pro-fast", "ultra", "ultra-fast", "ultra2x", "ultra4x", "ultra8x"}
            if processor not in valid_processors:
                return f"Error: processor must be one of {valid_processors}"

            return await _parallel_deep_research(prompt, processor, _parallel_key, source_domains)

        registry.register(ToolDefinition(
            name="deep_research",
            description=(
                "Run deep, autonomous web research on a topic using Parallel.ai's Task API. "
                "This is a powerful research engine that autonomously plans what to search, reads "
                "dozens of sources, follows leads, re-searches based on findings, and synthesizes "
                "everything into a comprehensive report with citations. Returns a detailed markdown "
                "report typically 5-15K chars long. Use for competition deep-dives, technique research, "
                "winning strategy analysis, and domain investigation. Higher processor tiers run longer "
                "but search more sources and produce deeper analysis. Default to 'pro' for most research; "
                "use 'ultra' for deep scientific or niche domains where shallow search won't find what you need."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Research question or task (max 15K chars). Be specific and include context about what you already know — this helps the research engine focus on the right areas.",
                    },
                    "processor": {
                        "type": "string",
                        "description": "Research depth tier: 'core' (1-5min), 'pro' (5-20min, recommended default), 'ultra' (5-45min, for deep scientific domains).",
                        "default": "core",
                        "enum": ["lite", "base", "core", "core2x", "pro", "pro-fast", "ultra", "ultra-fast", "ultra2x", "ultra4x", "ultra8x"],
                    },
                    "source_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: restrict research to specific domains (e.g., ['kaggle.com', 'arxiv.org']).",
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            handler=_deep_research_handler,
            allowed_roles={AgentRole.VP, AgentRole.WORKER, AgentRole.SUBAGENT},
            input_examples=[
                {"prompt": "Comprehensive analysis of winning approaches in CAFA 5 protein function prediction competition on Kaggle", "processor": "pro"},
                {"prompt": "State of the art protein language models for GO term annotation 2024-2025", "processor": "ultra", "source_domains": ["arxiv.org", "biorxiv.org"]},
            ],
        ))

    for tool in make_strategy_tools(strategy_library):
        registry.register(tool)

