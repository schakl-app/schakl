"""Daily budget watch: warn when a project's burn crosses the org's threshold (issue #16).

A budget that is only discovered when it is blown is a budget nobody managed. This walks the
active, budgeted projects once a day and tells their assignees when the burn crosses a
threshold — the in-app ``project.budget_threshold`` notification, and (per org setting,
Instellingen → Projecten) a dedicated alert mail carrying the burn bar and the numbers.

**What "almost" means is the org's, not this file's**: `project_settings` holds the warn
threshold (default 75, the pre-settings behaviour) and whether the mail is on. Both halves
read the same number, so the bell and the mail can never disagree.

The notification dedup key carries the period start, so a **monthly** budget can warn again
next month without the cron having to remember anything. The mail dedups on a fingerprint
column instead (``projects.budget_alerted_for``, the ``domain_alerted_for`` pattern): written
only when a mail actually left, cleared while the burn is back under the threshold, and
naming level + threshold + period so a raised threshold re-arms and a new period re-alerts.

Spend comes from the time module's published service (``minutes_by_project``) and the budget
from the same ``effective_budget`` rule the on-screen bar uses — a project covered by a
subscription's included hours (#225) alerts on those, never on its dormant stored column.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext, emit
from app.core.models import Membership, Org, OrgSettings
from app.core.timezone import org_zoneinfo
from app.modules.projects.budget import effective_budget, period_bound, period_start_date
from app.modules.projects.emails import compose_budget_alert
from app.modules.projects.models import (
    Project,
    ProjectAssignee,
    ProjectSettings,
    ProjectStatus,
)

logger = logging.getLogger(__name__)

#: The pre-settings warn threshold — what an org that never saved a row gets.
DEFAULT_WARN_THRESHOLD = 75


async def _assignees(session: AsyncSession, org_id, project_id) -> list:
    return list(
        (
            await session.execute(
                select(ProjectAssignee.user_id).where(
                    ProjectAssignee.org_id == org_id,
                    ProjectAssignee.project_id == project_id,
                )
            )
        ).scalars()
    )


async def _mail_recipients(
    session: AsyncSession, org: Org, project_id: uuid.UUID
) -> list[tuple[str, str | None]]:
    """The project's assignees as ``(email, locale)`` — active staff only.

    The same two narrowings the domain alert applies (`domain_health._admin_emails`): a
    deactivated membership or disabled account is not written to, and an external login —
    a client-role membership or a contact-linked portal account (#274) — must not receive
    the agency's own operational mail, even if someone assigned them to the project.
    """
    from app.core.auth.models import User
    from app.core.permissions.catalog import ROLE_CLIENT
    from app.core.permissions.models import MembershipRole, Role
    from app.core.portal import portal_user_ids

    external_roles = (
        select(MembershipRole.membership_id)
        .join(Role, Role.id == MembershipRole.role_id)
        .where(MembershipRole.org_id == org.id, Role.key == ROLE_CLIENT)
    )
    rows = await session.execute(
        select(User.id, User.email, User.locale)
        .join(Membership, Membership.user_id == User.id)
        .join(ProjectAssignee, ProjectAssignee.user_id == User.id)
        .where(
            ProjectAssignee.org_id == org.id,
            ProjectAssignee.project_id == project_id,
            Membership.org_id == org.id,
            Membership.deactivated_at.is_(None),
            User.is_active.is_(True),
            Membership.id.not_in(external_roles),
        )
    )
    candidates = {user_id: (email, locale) for user_id, email, locale in rows if email}
    portal = await portal_user_ids(session, org.id, set(candidates))
    return [
        candidates[user_id]
        for user_id in sorted(candidates, key=lambda uid: candidates[uid][0])
        if user_id not in portal
    ]


async def _company_names(
    session: AsyncSession, org_id, projects: list[Project]
) -> dict[uuid.UUID, str]:
    """Label lookup for the mail's "Klant" line — one query, bare columns (CLAUDE.md §6)."""
    ids = {p.company_id for p in projects if p.company_id is not None}
    if not ids:
        return {}
    stmt = text("SELECT id, name FROM companies WHERE org_id = :oid AND id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    rows = (await session.execute(stmt, {"oid": org_id, "ids": list(ids)})).all()
    return {row[0]: row[1] for row in rows}


def _level(percent: int, warn: int) -> str | None:
    """The mail-worthy state of a burn: ``over`` at 100, ``warn`` at the org threshold."""
    if percent >= 100:
        return "over"
    if percent >= warn:
        return "warn"
    return None


async def watch_for_org(org: Org, session: AsyncSession) -> int:
    """Announce every active project that has crossed a threshold; mail the new crossings.

    Returns the number of *candidates announced*, not notifications delivered: a project over
    100% re-announces both thresholds on every tick, and the notifications module drops the
    repeats on their dedup keys. The mail half keeps its own state on the project row.
    """
    from app.modules.subscriptions.service import SubscriptionService
    from app.modules.time.service import LoggedMinutes, TimeService

    projects = (
        await session.execute(
            select(Project).where(
                Project.org_id == org.id,
                Project.status == ProjectStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    if not projects:
        return 0

    ctx = SystemContext(org=org, session=session)
    settings_row = await session.scalar(
        select(ProjectSettings).where(ProjectSettings.org_id == org.id)
    )
    warn = settings_row.budget_alert_threshold if settings_row else DEFAULT_WARN_THRESHOLD
    emails_on = settings_row.budget_alert_emails if settings_row else True
    thresholds = tuple(sorted({warn, 100}))

    # The same budget the screen shows (#225): a covering subscription outranks the column.
    sources = await SubscriptionService(ctx).hours_for_projects([p.id for p in projects])
    # This org's own calendar decides where its month starts (CLAUDE.md §8) — resolved once for
    # the whole sweep, never per project.
    tz = await org_zoneinfo(session, org.id)
    effective = {
        p.id: effective_budget(p.budget_hours, p.budget_period, sources.get(p.id, []))
        for p in projects
    }
    periods = {p.id: period_bound(effective[p.id][1], tz=tz) for p in projects}
    logged = await TimeService(ctx).minutes_by_project(periods)

    org_settings = None
    names: dict[uuid.UUID, str] | None = None

    emitted = 0
    for project in projects:
        budget, period_kind = effective[project.id]
        if not budget or budget <= 0:  # a zero budget is "unbudgeted", not "instantly over"
            continue
        spent = logged.get(project.id, LoggedMinutes()).total / 60
        percent = round(spent / budget * 100)
        period = period_start_date(period_kind, tz=tz)

        recipients = await _assignees(session, org.id, project.id)
        if recipients:
            for threshold in thresholds:
                if percent < threshold:
                    continue
                await emit(
                    "project.budget_threshold",
                    ctx,
                    {
                        "project_id": project.id,
                        "title": project.name,
                        "threshold": threshold,
                        "percent": percent,
                        "_recipients": recipients,
                        # Period-scoped: a monthly budget may warn again next month.
                        "_dedup_key": (
                            f"project.budget_threshold:{project.id}:{threshold}:"
                            f"{period.isoformat() if period else 'total'}"
                        ),
                    },
                )
                emitted += 1

        # --- the mail half ---------------------------------------------------- #
        level = _level(percent, warn)
        if level is None:
            # Back under the threshold (entries corrected, budget raised): re-arm, so
            # crossing again is news again.
            if project.budget_alerted_for is not None:
                project.budget_alerted_for = None
            continue
        if not emails_on:
            continue
        fingerprint = f"{level}:{warn}:{period.isoformat() if period else 'total'}"
        if project.budget_alerted_for == fingerprint:
            continue
        addresses = await _mail_recipients(session, org, project.id)
        if not addresses:
            # Not marked handled: a project that gains an active assignee is alerted tomorrow.
            continue
        if org_settings is None:
            org_settings = await session.scalar(
                select(OrgSettings).where(OrgSettings.org_id == org.id)
            )
        if names is None:
            names = await _company_names(session, org.id, list(projects))
        sent = await _send_alert(
            session,
            org,
            org_settings,
            project=project,
            company_name=names.get(project.company_id) if project.company_id else None,
            level=level,
            percent=percent,
            spent=spent,
            budget=budget,
            period_kind=period_kind,
            warn=warn,
            addresses=addresses,
        )
        if sent:
            project.budget_alerted_for = fingerprint
    return emitted


async def _send_alert(
    session: AsyncSession,
    org: Org,
    org_settings,  # noqa: ANN001 — OrgSettings | None, loaded once by the caller
    *,
    project: Project,
    company_name: str | None,
    level: str,
    percent: int,
    spent: float,
    budget: float,
    period_kind: str,
    warn: int,
    addresses: list[tuple[str, str | None]],
) -> bool:
    """Mail everyone on the project; report whether anyone was actually reached.

    Best effort — a mail outage must not stall the sweep — but not silently: the caller only
    remembers a state it managed to tell someone about, so a broken transport is retried on
    the next tick instead of being marked handled.
    """
    from app.config import settings as app_settings
    from app.core.email.branding import brand_from
    from app.core.email.service import send_org_email

    try:
        brand = brand_from(org, org_settings)
        default_locale = (
            org_settings.default_locale if org_settings else None
        ) or app_settings.default_locale
        project_url = f"{brand.base_url}/projects/{project.id}"
        sent = False
        for address, locale in addresses:
            message = compose_budget_alert(
                project_name=project.name,
                project_url=project_url,
                company_name=company_name,
                level=level,
                percent=percent,
                spent_hours=spent,
                budget_hours=budget,
                budget_period=period_kind,
                threshold=warn,
                locale=locale or default_locale,
                primary_color=brand.primary_color,
            )
            ok, _error = await send_org_email(
                session, org.id, replace(message, to=address), brand=brand
            )
            sent = sent or ok
        return sent
    except Exception:  # noqa: BLE001 — the alert mail is best effort by contract
        logger.exception(
            "project budget alert failed for org %s project %s", org.slug, project.id
        )
        return False


async def watch_project_budgets(ctx: dict) -> int:
    """ARQ cron entry point: budget threshold warnings for every org."""
    from app.core.entitlements.service import sku_cron_enabled
    from app.core.jobs import run_per_org

    # Licensed module (issue #137): the mount-time 402 gate covers requests, but crons write
    # on a schedule — an expired license must stop the background half too.
    if not await sku_cron_enabled("projects"):
        return 0

    total = 0

    async def _per_org(org: Org, session: AsyncSession) -> None:
        nonlocal total
        total += await watch_for_org(org, session)

    await run_per_org(_per_org)
    return total
