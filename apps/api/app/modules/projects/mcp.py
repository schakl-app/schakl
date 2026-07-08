"""MCP tools this module *will* contribute (CLAUDE.md §6, §12).

Phase-4 seam only. Read-first; served in P4 through the same tenant-scoped services.
"""

from __future__ import annotations

from typing import Any

PROJECT_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "projects.for_company",
        "description": "List a company's projects with status and budget.",
        "scope": "mcp:read",
    },
    {
        "name": "projects.budget_status",
        "description": "Report a project's logged hours and billable value against its budget.",
        "scope": "mcp:read",
    },
]
