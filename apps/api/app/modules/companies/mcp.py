"""Executable AI tools this module contributes (CLAUDE.md §6, §12; issue #127).

The old declarative Phase-4 seam made runnable: each entry is an :class:`AIToolSpec` the
in-app assistant executes today, and the P4 MCP server can serve the same catalog externally
later. Read-only by design; every handler runs under the caller's :class:`RequestContext`, so
a tool can never answer across tenants (Golden Rule 1) or beyond the caller's role (§15) —
the ``permission`` filter keeps a tool the caller may never use out of the model's view, and
the tenant-scoped repository enforces the same rule on every query.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func

from app.core.ai.tools import AIToolSpec, Source, ToolResult
from app.core.customfields import CustomFieldsService
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.companies.models import Company
from app.modules.companies.service import ENTITY_TYPE

_READ_PERMISSION = "companies.company.read"
_FIND_LIMIT = 10


async def _labelled_custom(ctx: RequestContext, custom: dict[str, Any]) -> dict[str, Any]:
    """The stored custom values with the tenant's own field labels applied (§13, #127).

    ``label_i18n`` is tenant data (``{"nl": ..., "en": ...}``); pick the caller's locale with
    the same fallback the rest of the API uses (user → nl → en → key). A value whose
    definition is gone (deactivated field) keeps its raw key rather than being dropped.
    """
    if not custom:
        return {}
    definitions = await CustomFieldsService(ctx).definitions(ENTITY_TYPE)
    locale = ctx.user.locale or "nl"
    labels = {
        d.key: d.label_i18n.get(locale) or d.label_i18n.get("nl") or d.label_i18n.get("en")
        for d in definitions
    }
    return {
        key: {"label": labels.get(key) or key, "value": value} for key, value in custom.items()
    }


async def _find(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    query = args.get("query")
    stmt = ctx.repo(Company).scoped_select()
    if isinstance(query, str) and query.strip():
        # Name-only ilike, ranked by name — same case-insensitive ordering the list API uses.
        stmt = stmt.where(Company.name.ilike(f"%{query.strip()}%"))
        stmt = stmt.order_by(func.lower(Company.name))
    else:
        stmt = stmt.order_by(Company.updated_at.desc())
    companies = (await ctx.session.execute(stmt.limit(_FIND_LIMIT))).scalars().all()
    return ToolResult(
        data={
            "companies": [
                {"id": str(c.id), "name": c.name, "status": c.status} for c in companies
            ]
        },
        sources=tuple(Source(type="company", id=str(c.id), label=c.name) for c in companies),
    )


async def _get(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    try:
        company_id = uuid.UUID(str(args.get("company_id")))
    except (TypeError, ValueError) as exc:
        raise AppError("validation", "errors.validation", status_code=422) from exc
    # Same 404 semantics as the REST API: a row outside the tenant simply does not exist.
    company = await ctx.repo(Company).get_or_404(company_id)
    return ToolResult(
        data={
            "id": str(company.id),
            "name": company.name,
            "status": company.status,
            "website": company.website,
            "notes": company.notes,
            "custom": await _labelled_custom(ctx, company.custom or {}),
        },
        sources=(Source(type="company", id=str(company.id), label=company.name),),
    )


COMPANY_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="companies.find",
        description=(
            "Look up the tenant's client companies by name. Call this to resolve a client "
            "name the user mentioned to its id, or with no query to list the most recently "
            "updated clients. Returns at most 10 matches."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": ["string", "null"],
                    "description": (
                        "Case-insensitive name fragment; omit for the most recent companies."
                    ),
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_find,
        permission=_READ_PERMISSION,
    ),
    AIToolSpec(
        name="companies.get",
        description=(
            "Fetch one client company's details (status, website, notes and the tenant's "
            "custom fields) by id. Call this when the user asks about a specific client, "
            "after companies.find has resolved the name to an id."
        ),
        input_schema={
            "type": "object",
            "properties": {"company_id": {"type": "string"}},
            "required": ["company_id"],
            "additionalProperties": False,
        },
        handler=_get,
        permission=_READ_PERMISSION,
    ),
]
