"""MCP tools this module *will* contribute (CLAUDE.md §6, §12).

Phase-4 seam only. Read-first; served in P4 through the same tenant-scoped services.
"""

from __future__ import annotations

from typing import Any

TASK_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "tasks.mine",
        "description": "List open tasks assigned to the current user.",
        "scope": "mcp:read",
    },
    {
        "name": "tasks.for_company",
        "description": "List the open tasks attached to a company.",
        "scope": "mcp:read",
    },
]
