"""MCP server (CLAUDE.md §12) — the API surface as tools, guarded by API keys."""

from app.core.mcp.server import build_mcp_asgi_app

__all__ = ["build_mcp_asgi_app"]
