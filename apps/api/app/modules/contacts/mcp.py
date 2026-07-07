"""MCP tools this module *will* contribute (CLAUDE.md §6, §12).

Phase-4 seam only: captured by the registry now for discoverability; the MCP server that serves
them is built in P4. Read-first, and each tool will run through the same tenant-scoped services
as the REST API.
"""

from __future__ import annotations

from typing import Any

CONTACT_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "contacts.find",
        "description": "Find contacts for the current tenant by name, email, or company.",
        "scope": "mcp:read",
    },
    {
        "name": "contacts.for_company",
        "description": "List the contacts attached to a company.",
        "scope": "mcp:read",
    },
]
