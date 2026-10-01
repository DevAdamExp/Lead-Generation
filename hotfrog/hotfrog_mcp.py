"""
Hotfrog MCP server.

Exposes Hotfrog directory search to Claude as MCP tools, reusing the
scraping logic in hotfrog_core.py (keep both files together).

Transport (set MCP_TRANSPORT):
  - "stdio" (default)        -> local use in Claude Desktop via claude_desktop_config.json
  - "streamable-http"        -> remote connector for claude.ai / mobile / Cowork
                                (host this on a public URL; default port 8000)

Run:
    pip install mcp httpx beautifulsoup4
    python hotfrog_mcp.py                 # stdio
    MCP_TRANSPORT=streamable-http python hotfrog_mcp.py   # http on :8000
"""

import os
import json
import hotfrog_core as core
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hotfrog")


@mcp.tool()
def search_businesses(query: str, country: str = "us", limit: int = 10) -> str:
    """
    Search Hotfrog for businesses by keyword/category and country.

    Args:
        query: business name, category, or keyword (e.g. "plumbers").
        country: two-letter Hotfrog country code (e.g. "us", "gb", "au", "de").
        limit: max results to return (1-50).
    """
    limit = max(1, min(int(limit), 50))
    try:
        data = core.search_businesses(query, country, limit)
    except Exception as e:
        return json.dumps({"error": str(e), "query": query})
    return json.dumps({"count": len(data), "results": data}, ensure_ascii=False)


@mcp.tool()
def get_business_details(url: str) -> str:
    """Fetch one Hotfrog listing page and return its parsed details."""
    try:
        return json.dumps(core.get_business_details(url), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)