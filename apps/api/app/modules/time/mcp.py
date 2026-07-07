"""MCP tools this module *will* contribute (CLAUDE.md §6, §12).

Phase-4 seam only. Read-first; served in P4 through the same tenant-scoped services.
"""

from __future__ import annotations

from typing import Any

TIME_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "time.summary",
        "description": "Summarise time logged by the current user for a day or week.",
        "scope": "mcp:read",
    },
    {
        "name": "time.for_company",
        "description": "Total time logged against a company.",
        "scope": "mcp:read",
    },
]
