"""MCP tools this module *will* contribute (CLAUDE.md §6, §12).

Phase-4 seam only: these declarations are captured by the module registry now so the surface
is discoverable, but the MCP server that serves them is built in P4. Read-first by design; each
tool will run through the same tenant-scoped services as the REST API (never crossing tenants).
"""

from __future__ import annotations

from typing import Any

COMPANY_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "companies.find",
        "description": "Find companies for the current tenant by name or attribute.",
        "scope": "mcp:read",
    },
    {
        "name": "companies.recent_projects",
        "description": "List a company's most recent projects.",
        "scope": "mcp:read",
    },
]
