"""Executable AI tools this module contributes (CLAUDE.md §6, §12; issue #127).

Each entry is an :class:`AIToolSpec` on the descriptor's ``mcp_tools``: the in-app assistant
executes them today, and the P4 MCP server can serve the *same* catalog externally later.
Read-only, and every handler runs through :class:`ContactService` under the caller's
:class:`RequestContext` — a tool can never answer beyond the caller's tenant (Golden Rule 1)
or role (§15).
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.ai import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.contacts.models import Contact
from app.modules.contacts.service import ContactService


def _name(contact: Contact) -> str:
    return " ".join(part for part in (contact.first_name, contact.last_name) if part)


def _contact_data(contact: Contact, **extra: Any) -> dict[str, Any]:
    return {
        "id": str(contact.id),
        "name": _name(contact),
        "email": contact.email,
        "phone": contact.phone,
        **extra,
    }


def _sources(contacts: list[Contact]) -> tuple[Source, ...]:
    return tuple(Source(type="contact", id=str(c.id), label=_name(c)) for c in contacts)


async def _find(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    query = args.get("query")
    q = query.strip() if isinstance(query, str) else None
    contacts, _ = await ContactService(ctx).list(limit=10, offset=0, q=q or None, count=False)
    contacts = list(contacts)
    return ToolResult(
        data={"contacts": [_contact_data(c) for c in contacts]},
        sources=_sources(contacts),
    )


async def _for_company(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    try:
        company_id = uuid.UUID(str(args.get("company_id")))
    except (TypeError, ValueError):
        raise AppError("validation", "errors.validation", status_code=422) from None
    rows = await ContactService(ctx).contacts_for_company(company_id)
    return ToolResult(
        data={
            "contacts": [_contact_data(c, is_primary=is_primary) for c, is_primary in rows]
        },
        sources=_sources([c for c, _ in rows]),
    )


CONTACT_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="contacts.find",
        description=(
            "Search this workspace's client contacts (people at client companies) by a name "
            "or email fragment. Call this whenever the user refers to a person by name or "
            "email and you need their contact record or id. Omit query to get the 10 most "
            "recently added contacts. Returns at most 10 matches."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": ["string", "null"],
                    "description": "Name or email fragment; omit for the most recent contacts.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_find,
        permission="contacts.contact.read",
    ),
    AIToolSpec(
        name="contacts.for_company",
        description=(
            "List the contacts (people) linked to one client company, the primary contact "
            "first and flagged is_primary. Call this when the user asks who their contact "
            "at a specific company is, or for that company's people. Requires the company's "
            "id; if you only have a name, resolve it with companies.find first."
        ),
        input_schema={
            "type": "object",
            "properties": {"company_id": {"type": "string"}},
            "required": ["company_id"],
            "additionalProperties": False,
        },
        handler=_for_company,
        permission="contacts.contact.read",
    ),
]
