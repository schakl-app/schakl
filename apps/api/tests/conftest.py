"""Test harness (CLAUDE.md §9).

Runs against a real Postgres because tenant isolation depends on Row-Level Security, which
SQLite can't model. Applies migrations once, truncates between tests, and provides helpers to
provision tenants and authenticated HTTP clients bound to a tenant hostname.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]

# Point the app at the test database *before* importing anything that reads settings.
os.environ.setdefault(
    "VLOTR_DATABASE_URL",
    "postgresql+asyncpg://vlotr_app:vlotr_app@localhost:5432/vlotr",
)
os.environ.setdefault("VLOTR_BASE_DOMAIN", "localhost")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from pwdlib import PasswordHash  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.auth.backend import get_jwt_strategy  # noqa: E402
from app.core.auth.models import User  # noqa: E402
from app.core.models import Membership, Org, OrgSettings  # noqa: E402
from app.db import async_session_maker, engine, set_current_org  # noqa: E402
from app.main import app  # noqa: E402

_password_hash = PasswordHash.recommended()
_DOMAIN_TABLES = (
    "task_checklist_items, task_checklists, task_checklist_templates, task_links, "
    "task_label_links, task_labels, "
    "task_comments, task_activities, task_template_items, task_templates, "
    "time_entries, tasks, projects, contacts, custom_field_definitions, "
    "dashboard_prefs, user_prefs, companies, memberships, org_settings, users, orgs"
)
_ENABLED_MODULES = ["companies", "contacts", "tasks", "projects", "time"]


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    """Bring the schema to head once for the whole test session."""
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=API_DIR, check=True)


@pytest.fixture(autouse=True)
async def _clean_db() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {_DOMAIN_TABLES} RESTART IDENTITY CASCADE")
        )
    yield
    # Dispose the pool so each test's event loop gets fresh asyncpg connections.
    await engine.dispose()


@dataclass
class Tenant:
    org: Org
    user: User
    password: str
    host: str


async def make_tenant(
    slug: str,
    *,
    email: str | None = None,
    password: str = "secret1234",
    role: str = "owner",
) -> Tenant:
    """Create an org + user + membership + org_settings; return handles for the test."""
    email = email or f"{slug}@example.com"
    async with async_session_maker() as session:
        org = Org(slug=slug, name=slug.title())
        session.add(org)
        await session.flush()

        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=_password_hash.hash(password),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

        # Org-scoped rows require the RLS GUC bound to this org.
        await set_current_org(session, org.id)
        session.add(Membership(org_id=org.id, user_id=user.id, role=role))
        session.add(
            OrgSettings(
                org_id=org.id, brand_name=slug.title(), enabled_modules=list(_ENABLED_MODULES)
            )
        )
        await session.commit()
        # Detach copies for use outside the session.
        org_out = Org(id=org.id, slug=org.slug, name=org.name)
        user_out = User(id=user.id, email=user.email, hashed_password="", is_active=True)
    return Tenant(org=org_out, user=user_out, password=password, host=f"{slug}.localhost")


async def auth_cookie(user: User) -> dict[str, str]:
    """A Cookie header carrying a valid session for ``user`` (skips the login form)."""
    token = await get_jwt_strategy().write_token(user)
    return {"Cookie": f"vlotr_auth={token}"}


@pytest.fixture
def client_for() -> Callable[[str], AsyncClient]:
    """Factory: an HTTP client whose Host header resolves to the given tenant hostname."""

    def _make(host: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url=f"http://{host}"
        )

    return _make
