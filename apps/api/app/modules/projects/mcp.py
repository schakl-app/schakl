"""Executable AI tools this module contributes (CLAUDE.md §6, §12; issue #127).

Each entry is an :class:`AIToolSpec` on the descriptor's ``mcp_tools``: the in-app assistant
executes them today, and the P4 MCP server can serve the *same* catalog externally later.
Read-only, and every handler runs through :class:`ProjectService` under the caller's
:class:`RequestContext` — a tool can never answer beyond the caller's tenant (Golden Rule 1)
or role (§15). Budget burn reuses the service's own computation (``hours=True``), so period
boundaries stay the module's rule (budget.py) and are never recomputed here.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.ai import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.projects.models import Project
from app.modules.projects.panels import _STATUS_ORDER
from app.modules.projects.service import ProjectService


def _uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise AppError("validation", "errors.validation", status_code=422) from None


def _source(project: Project) -> Source:
    return Source(type="project", id=str(project.id), label=project.name)


def _burn(project: Project) -> dict[str, Any]:
    """The hour-budget summary the service already computed (``BudgetHours``). Hours are not
    money: they stay visible to anyone who may read the project."""
    hours = project.hours
    return {
        "period": hours.period,
        "period_start": hours.period_start.isoformat() if hours.period_start else None,
        "budget_hours": hours.budget_hours,
        "spent_hours": hours.spent_hours,
        "remaining_hours": hours.remaining_hours,
    }


async def _find(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    query = args.get("query")
    q = query.strip() if isinstance(query, str) else None
    raw_company = args.get("company_id")
    company_id = _uuid(raw_company) if raw_company is not None else None
    projects, _ = await ProjectService(ctx).list(
        limit=10, offset=0, q=q or None, company_id=company_id, count=False
    )
    return ToolResult(
        data={
            "projects": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "company_id": str(p.company_id) if p.company_id else None,
                    "status": p.status,
                }
                for p in projects
            ]
        },
        sources=tuple(_source(p) for p in projects),
    )


async def _for_company(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    company_id = _uuid(args.get("company_id"))
    projects, _ = await ProjectService(ctx).list(
        limit=50, offset=0, company_id=company_id, count=False, hours=True
    )
    # Active first, like the company panel; the service's name order holds within a status.
    projects = sorted(projects, key=lambda p: _STATUS_ORDER.get(p.status, 9))
    return ToolResult(
        data={
            "projects": [
                {"id": str(p.id), "name": p.name, "status": p.status, "budget": _burn(p)}
                for p in projects
            ]
        },
        sources=tuple(_source(p) for p in projects),
    )


async def _budget_status(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    project_id = _uuid(args.get("project_id"))
    project = await ProjectService(ctx).get(project_id, hours=True)
    hours = project.hours
    data: dict[str, Any] = {
        "id": str(project.id),
        "name": project.name,
        "status": project.status,
        **_burn(project),
        "billable_hours": hours.billable_hours,
        "unapproved_hours": hours.unapproved_hours,
        "percent_used": (
            round(hours.spent_hours / hours.budget_hours * 100, 1)
            if hours.budget_hours
            else None
        ),
    }
    # Money is priced at the rate of the employee who logged each hour (#226), so it is gated
    # like the rest of the employee-rate money: the manager report grant plus the any-scope
    # rate read — the same pair ``/time/cost`` requires. Hours above stay free.
    if ctx.can("time.report.read") and ctx.can("leave.rate.read", scope="any"):
        from app.modules.time.service import TimeService

        cost = await TimeService(ctx).project_cost(project.id)
        data["currency"] = project.currency
        data["budget_amount"] = (
            float(project.budget_amount) if project.budget_amount is not None else None
        )
        # Billable hours priced per logger — the same figure the project page shows.
        data["billable_amount"] = cost["billable_amount"]
        data["cost"] = cost["cost"]
        data["unrated_minutes"] = cost["unrated_minutes"]
    return ToolResult(data=data, sources=(_source(project),))


PROJECT_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="projects.find",
        description=(
            "Search this workspace's projects by a name fragment, optionally within one "
            "client company. Call this whenever the user refers to a project by name and "
            "you need its id or status, or to check whether a project exists. Omit query "
            "to list the first 10 by name. Returns at most 10 matches."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": ["string", "null"],
                    "description": "Project name fragment; omit to list by name.",
                },
                "company_id": {
                    "type": ["string", "null"],
                    "description": "Restrict to one client company's projects.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_find,
        permission="projects.project.read",
    ),
    AIToolSpec(
        name="projects.for_company",
        description=(
            "List one client company's projects, active first, each with its status and an "
            "hour-budget summary (budget, spent and remaining hours for the current budget "
            "period). Call this when the user asks which projects a company has or how that "
            "company's work stands. Requires the company's id; if you only have a name, "
            "resolve it with companies.find first."
        ),
        input_schema={
            "type": "object",
            "properties": {"company_id": {"type": "string"}},
            "required": ["company_id"],
            "additionalProperties": False,
        },
        handler=_for_company,
        permission="projects.project.read",
    ),
    AIToolSpec(
        name="projects.budget_status",
        description=(
            "Report one project's logged hours against its budget for the current budget "
            "period: budget, spent, billable, unapproved and remaining hours, plus the "
            "percentage used (over 100 means over budget). Call this when the user asks "
            "where a project's budget stands or whether it is over budget. Monetary figures "
            "appear only when the caller may see them. Requires the project's id; if you "
            "only have a name, resolve it with projects.find first."
        ),
        input_schema={
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
        handler=_budget_status,
        permission="projects.project.read",
    ),
]
