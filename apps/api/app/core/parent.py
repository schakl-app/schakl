"""Validate a cross-module parent reference belongs to the caller's tenant (audit F19).

A module that carries a foreign key to another module's row — a project's ``company_id``, a task's
``project_id``, a time entry's ``task_id`` — must confirm the parent is in the same org before
storing it. RLS keeps the *child* in the caller's tenant regardless, but an unvalidated FK to
another org's row creates a dangling reference and, because a Postgres FK check runs as the table
owner and bypasses RLS, lets a caller tell "a real UUID somewhere on the instance" (insert
succeeds) from "no such UUID" (IntegrityError → 500) — a cross-tenant existence oracle. A clean
404 closes both. The contacts/hosting/domains modules already do this per entity; this is the
shared form so projects/tasks/time can too.

``table`` is always a code literal (never user input), so the f-string carries no SQL-injection
risk; the id and org are bound parameters.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError


async def ensure_parent_in_tenant(
    session: AsyncSession, table: str, entity_id: uuid.UUID | None, org_id: uuid.UUID
) -> None:
    """404 unless ``entity_id`` is a row of ``table`` in ``org_id``. ``None`` is a no-op (the FK
    is optional and simply not being set)."""
    if entity_id is None:
        return
    ok = await session.scalar(
        text(f"SELECT 1 FROM {table} WHERE id = :id AND org_id = :oid"),  # noqa: S608 - literal table
        {"id": entity_id, "oid": org_id},
    )
    if not ok:
        raise AppError("not_found", "errors.not_found", status_code=404)
