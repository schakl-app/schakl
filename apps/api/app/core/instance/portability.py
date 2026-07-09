"""Per-org data portability: export and import (issue #26).

An agency that self-hosts must be able to leave, and an export is what makes hard delete
safe. The format is deliberately generic: every table carrying ``org_id`` is dumped from
``Base.metadata``, so a new module's data is exported without anyone remembering to add it.

Import creates a **new** org (fresh id, no domain — a domain must be re-verified on the
importing instance) and gives every imported row a fresh primary key, remapping all FK
references between exported tables along the way (primary keys are unique per table, so
importing next to the source org on the same box would otherwise collide). Ids embedded in
free-form JSONB (e.g. activity payloads) are not rewritten. Exported users are matched to
local accounts by email, created otherwise (never as superuser: an export must not be able
to smuggle an instance owner onto another box).

Both directions require the same schema revision — a dump from release *N* does not
silently load into *N+1*; upgrade first, then export/import.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.models import Org
from app.db import INSTANCE_LEVEL_TABLES, Base, set_current_org
from app.errors import AppError

EXPORT_FORMAT = 1

_USER_FIELDS = ("email", "hashed_password", "is_active", "is_verified", "full_name", "locale")


def _tenant_tables() -> list[sa.Table]:
    """Every org-scoped table, in FK-dependency order (parents before children)."""
    return [
        t
        for t in Base.metadata.sorted_tables
        if "org_id" in t.c and t.name not in INSTANCE_LEVEL_TABLES
    ]


def _user_fk_columns(table: sa.Table) -> list[str]:
    return [
        c.name for c in table.c for fk in c.foreign_keys if fk.column.table.name == "users"
    ]


def _encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _decode(column: sa.Column, value: Any) -> Any:
    if value is None:
        return None
    kind = column.type
    if isinstance(kind, sa.Uuid):
        return uuid.UUID(value)
    if isinstance(kind, sa.DateTime):
        return datetime.fromisoformat(value)
    if isinstance(kind, sa.Date):
        return date.fromisoformat(value)
    if isinstance(kind, sa.Time):
        return time.fromisoformat(value)
    if isinstance(kind, sa.Numeric) and not isinstance(kind, sa.Float):
        return Decimal(value)
    return value


async def _schema_revision(session: AsyncSession) -> str:
    rows = await session.execute(sa.text("SELECT version_num FROM alembic_version"))
    return ",".join(sorted(rows.scalars().all()))


async def export_org(session: AsyncSession, org: Org) -> dict[str, Any]:
    """Dump one org: every org-scoped row plus the users those rows reference."""
    await set_current_org(session, org.id)

    tables: dict[str, list[dict[str, Any]]] = {}
    user_ids: set[uuid.UUID] = set()
    for table in _tenant_tables():
        result = await session.execute(select(table).where(table.c.org_id == org.id))
        rows = [dict(m) for m in result.mappings()]
        for col in _user_fk_columns(table):
            user_ids.update(row[col] for row in rows if row.get(col) is not None)
        tables[table.name] = [{k: _encode(v) for k, v in row.items()} for row in rows]

    users: list[dict[str, Any]] = []
    if user_ids:
        for user in (
            (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        ):
            users.append({"id": str(user.id), **{f: getattr(user, f) for f in _USER_FIELDS}})

    return {
        "format": EXPORT_FORMAT,
        "exported_at": datetime.now(UTC).isoformat(),
        "app_version": settings.version,
        "schema_revision": await _schema_revision(session),
        "org": {"id": str(org.id), "slug": org.slug, "name": org.name},
        "users": users,
        "tables": tables,
    }


async def import_org(
    session: AsyncSession, payload: dict[str, Any], *, slug: str, name: str | None = None
) -> tuple[Org, dict[str, int]]:
    """Recreate an exported org under ``slug``. Atomic: any failure rolls the whole org back.

    The caller has already validated the slug and its global uniqueness.
    """
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
        raise AppError("import_invalid", "errors.import_invalid", status_code=422)
    if payload.get("schema_revision") != await _schema_revision(session):
        raise AppError("import_schema_mismatch", "errors.import_schema_mismatch", status_code=409)

    org = Org(slug=slug, name=name or payload["org"]["name"])
    session.add(org)
    await session.flush()

    # Users: reuse a local account with the same email, otherwise recreate the exported one
    # (with its original id, so unmapped references still resolve). Never as superuser.
    user_map: dict[str, uuid.UUID] = {}
    for u in payload.get("users", []):
        existing = await session.scalar(
            select(User).where(func.lower(User.email) == u["email"].lower())
        )
        if existing is not None:
            user_map[u["id"]] = existing.id
        else:
            created = User(
                id=uuid.UUID(u["id"]),
                is_superuser=False,
                **{f: u.get(f) for f in _USER_FIELDS},
            )
            session.add(created)
            user_map[u["id"]] = created.id
    await session.flush()

    # Every imported row gets a fresh primary key (same-box imports would otherwise collide
    # with the source org). First pass: mint the ids, so cross-table FKs can be rewritten.
    tables_payload: dict[str, list[dict[str, Any]]] = payload.get("tables", {})
    id_map: dict[tuple[str, str], uuid.UUID] = {
        (table.name, row["id"]): uuid.uuid4()
        for table in _tenant_tables()
        for row in tables_payload.get(table.name, [])
    }

    def _mapped(table_name: str, raw: Any) -> uuid.UUID:
        mapped = id_map.get((table_name, str(raw)))
        if mapped is None:
            raise AppError("import_invalid", "errors.import_invalid", status_code=422)
        return mapped

    # Org-scoped rows are RLS-forced: bind the GUC to the new org for the inserts.
    await set_current_org(session, org.id)
    counts: dict[str, int] = {}
    for table in _tenant_tables():
        rows = tables_payload.get(table.name, [])
        if not rows:
            continue
        fk_targets = {
            c.name: next(iter(c.foreign_keys)).column.table.name
            for c in table.c
            if c.foreign_keys
        }
        values: list[dict[str, Any]] = []
        for row in rows:
            decoded: dict[str, Any] = {}
            for key, raw in row.items():
                if key not in table.c:
                    continue
                target = fk_targets.get(key)
                if key == "id":
                    decoded[key] = _mapped(table.name, raw)
                elif key == "org_id":
                    decoded[key] = org.id
                elif raw is None:
                    decoded[key] = None
                elif target == "users":
                    mapped_user = user_map.get(str(raw))
                    if mapped_user is None:
                        raise AppError(
                            "import_invalid", "errors.import_invalid", status_code=422
                        )
                    decoded[key] = mapped_user
                elif target is not None:
                    decoded[key] = _mapped(target, raw)
                else:
                    decoded[key] = _decode(table.c[key], raw)
            # A domain routes hostnames and must be re-verified on this instance.
            if table.name == "org_settings":
                decoded["custom_domain"] = None
            values.append(decoded)
        await session.execute(table.insert(), values)
        counts[table.name] = len(values)

    return org, counts
