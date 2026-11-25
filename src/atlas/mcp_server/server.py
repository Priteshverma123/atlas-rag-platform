"""MCP server exposing the knowledge base as tools (for Relay agents, Claude Code, IDEs, ...).

Security: the server holds ONE scoped Atlas API key (read-only recommended) from the environment;
it never accepts or forwards client tokens (no token passthrough, per the MCP security guidance).
Results are returned as data with explicit source metadata, never as instructions.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API = os.environ.get("ATLAS_MCP_API_URL", "http://api:8000")
KEY = os.environ.get("ATLAS_MCP_API_KEY", "")
DEFAULT_COLLECTIONS = [c for c in os.environ.get("ATLAS_MCP_COLLECTIONS", "").split(",") if c]

mcp = FastMCP("atlas-knowledge-base", host=os.environ.get("ATLAS_MCP_HOST", "0.0.0.0"),  # noqa: S104
              port=int(os.environ.get("ATLAS_MCP_PORT", "8765")))


async def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.request(method, f"{API}{path}", json=body, headers={"Authorization": f"Bearer {KEY}"})
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def list_collections() -> list[dict[str, Any]]:
    """List knowledge-base collections available to this server (id, name, description, document count)."""
    rows = await _call("GET", "/v1/collections")
    return [{k: r[k] for k in ("id", "name", "description", "documents")} for r in rows]


@mcp.tool()
async def search_knowledge_base(query: str, collection_ids: list[str] | None = None, top_k: int = 8
                                ) -> list[dict[str, Any]]:
    """Hybrid (keyword + semantic) search over internal documents. Returns ranked passages with
    document title, section, pages and chunk ids. Use for finding evidence; cite chunk ids."""
    res = await _call("POST", "/v1/search", {"query": query, "collection_ids": collection_ids or DEFAULT_COLLECTIONS,
                                             "top_k": max(1, min(top_k, 20))})
    return [{k: r.get(k) for k in ("chunk_id", "document_id", "title", "section_path", "page_start", "page_end",
                                   "text")} for r in res["results"]]


@mcp.tool()
async def ask_knowledge_base(question: str, collection_ids: list[str] | None = None) -> dict[str, Any]:
    """Answer a question from internal documents with verified citations. Returns the answer,
    citations (document, pages) and a support ratio; an explicit 'not found' when evidence is missing."""
    res = await _call("POST", "/v1/query", {"question": question, "stream": False, "use_memory": False,
                                            "collection_ids": collection_ids or DEFAULT_COLLECTIONS})
    return {"answer": res.get("answer"), "citations": res.get("citations", []),
            "supported_ratio": (res.get("verification") or {}).get("supported_ratio"),
            "abstained": res.get("abstained", False)}


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
