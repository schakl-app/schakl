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
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

API_DIR = Path(__file__).resolve().parents[1]

# Point the app at the test database *before* importing anything that reads settings.
os.environ.setdefault(
    "SCHAKL_DATABASE_URL",
    "postgresql+asyncpg://schakl_app:schakl_app@localhost:5432/schakl",
)
os.environ.setdefault("SCHAKL_BASE_DOMAIN", "localhost")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from pwdlib import PasswordHash  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.auth.backend import get_jwt_strategy  # noqa: E402
from app.core.auth.models import User  # noqa: E402
from app.core.models import Membership, Org, OrgSettings  # noqa: E402
from app.core.permissions.service import create_membership, seed_system_roles  # noqa: E402
from app.db import async_session_maker, engine, set_current_org  # noqa: E402
from app.main import app  # noqa: E402

_password_hash = PasswordHash.recommended()
# Truncated in FK-child-before-parent order: `membership_roles` and `role_permissions` hang off
# `memberships`/`roles`, so they precede both (CASCADE would take them anyway, but the explicit
# order is what documents the dependency).
_DOMAIN_TABLES = (
    "automation_runs, automation_actions, automation_rules, "
    "notification_deliveries, notification_channels, notifications, notification_watchers, "
    "notification_preferences, notification_events, "
    "task_checklist_items, task_checklists, task_checklist_templates, task_links, "
    "task_label_links, task_labels, "
    "task_comments, task_activities, task_template_items, task_templates, "
    "leave_requests, leave_recurring_days, leave_entitlements, leave_profiles, leave_types, "
    "leave_holidays, leave_settings, employment_contracts, "
    "subscription_links, subscription_lines, subscription_prices, subscriptions, "
    "subscription_templates, subscription_types, "
    "invoicing_external_refs, invoice_time_entries, invoice_payments, quote_lines, "
    "invoice_lines, quotes, invoices, invoicing_settings, invoicing_templates, "
    "invoicing_tax_rates, "
    "marketing_metrics_daily, marketing_links, marketing_company_settings, marketing_settings, "
    "interactions, interaction_kinds, "
    "calendar_event_links, google_calendar_events, google_calendar_channels, "
    "drive_links, drive_folder_jobs, gmail_suppressions, google_connections, google_settings, "
    "websites, hosting, domains, providers, "
    "time_entry_drafts, time_entries, time_entry_types, tasks, projects, contacts, contact_types, "
    "custom_field_definitions, "
    "files, activity_log, dashboard_prefs, nav_prefs, user_prefs, companies, "
    "api_keys, service_accounts, "
    "email_settings, org_email_templates, org_auth_settings, "
    "role_audit_log, membership_roles, role_permissions, roles, memberships, org_settings, "
    "instance_audit_log, users, orgs"
)
_ENABLED_MODULES = [
    "companies", "contacts", "tasks", "projects", "time", "leave", "notifications",
    "domains", "hosting", "websites", "subscriptions", "invoicing", "automation",
    "interactions", "google", "marketing",
]


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
        session.add(
            OrgSettings(
                org_id=org.id, brand_name=slug.title(), enabled_modules=list(_ENABLED_MODULES)
            )
        )
        await session.flush()
        await seed_system_roles(session, org.id)
        await create_membership(session, org.id, user.id, role)
        await session.commit()
        # Detach copies for use outside the session.
        org_out = Org(id=org.id, slug=org.slug, name=org.name)
        user_out = User(id=user.id, email=user.email, hashed_password="", is_active=True)
    return Tenant(org=org_out, user=user_out, password=password, host=f"{slug}.localhost")


async def add_membership(
    session: Any, org_id: uuid.UUID, user_id: uuid.UUID, role: str = "member"
) -> Membership:
    """A membership **plus** the system role that carries its permissions (issue #19).

    Constructing a bare ``Membership`` gives a user who authenticates and then holds nothing —
    every gated endpoint 403s. Always go through this (or ``make_tenant``).
    """
    return await create_membership(session, org_id, user_id, role)


def leave_workday(index: int = 0) -> date:
    """The ``index``-th weekday from the first Monday of November, this year.

    A leave request's hours are computed from the employee's schedule (#48), so a request on a
    Saturday — or on Tweede Kerstdag — is worth zero hours and is refused outright. November is
    the one month with no Dutch public holiday in it, so a weekday there is always worth a full
    eight hours. Anchor leave test dates here rather than counting days from today and hoping.
    """
    first = date(date.today().year, 11, 1)
    monday = first + timedelta(days=(7 - first.weekday()) % 7)
    return monday + timedelta(weeks=index // 5, days=index % 5)


async def auth_cookie(user: User) -> dict[str, str]:
    """A Cookie header carrying a valid session for ``user`` (skips the login form)."""
    token = await get_jwt_strategy().write_token(user)
    return {"Cookie": f"schakl_auth={token}"}


@pytest.fixture
def client_for() -> Callable[[str], AsyncClient]:
    """Factory: an HTTP client whose Host header resolves to the given tenant hostname."""

    def _make(host: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url=f"http://{host}"
        )

    return _make


class QueryCounter:
    """Counts the SQL statements a block of code issues, so "no N+1" can be *asserted*.

    An aggregate that is one grouped query at ten rows and one-per-row at a thousand looks
    identical from the outside — same JSON, same test, a list that dies in production. The only
    honest check is to count the statements, so this exists.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []

    def matching(self, needle: str) -> list[str]:
        """Statements containing ``needle`` (case-insensitive) — e.g. a table name."""
        return [s for s in self.statements if needle.lower() in s.lower()]

    def __len__(self) -> int:
        return len(self.statements)


@pytest.fixture
def count_queries() -> Callable[[], Any]:
    """Context manager yielding a :class:`QueryCounter` for the statements executed inside it.

    Listens on the sync engine behind the async one — that is where SQLAlchemy emits the event.
    """
    from contextlib import contextmanager

    from sqlalchemy import event

    @contextmanager
    def _counting():
        counter = QueryCounter()

        def _on_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            counter.statements.append(statement)

        target = engine.sync_engine
        event.listen(target, "before_cursor_execute", _on_execute)
        try:
            yield counter
        finally:
            event.remove(target, "before_cursor_execute", _on_execute)

    return _counting
