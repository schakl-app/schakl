"""Background work the leave module contributes (#47).

Each December, import next year's holidays for every org that asked for it. Runs through
``run_per_org`` so the RLS GUC is bound per tenant and one org's failure can't poison another's
(CLAUDE.md §6). It is a top-up, not a reset: the importer never touches a manual row and never
resurrects a holiday the tenant deactivated.

ARQ cron fires in UTC; "next year" is computed from the tenant's configured timezone (CLAUDE.md
§8), because on 31 December a UTC-based job would already be running for the wrong year locally.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org, system_context
from app.core.models import Org
from app.core.timezone import org_zoneinfo
from app.modules.leave import holidays
from app.modules.leave.models import LeaveHoliday, LeaveSettings

logger = logging.getLogger("schakl.leave")


async def _import_next_year(org: Org, session: AsyncSession) -> None:
    settings = await session.scalar(select(LeaveSettings).where(LeaveSettings.org_id == org.id))
    if settings is not None and not settings.holiday_auto_import:
        return
    country = (settings.holiday_country if settings else None) or holidays.COUNTRY_NL

    # "Next year" is computed in the org's own zone (CLAUDE.md §8): on 31 December a UTC-based
    # job would already be running for the wrong year for a tenant east of UTC.
    year = datetime.now(await org_zoneinfo(session, org.id)).year + 1
    generated = holidays.generate(country, year)
    if not generated:
        return

    # One query for the year, then plain-Python matching — the same rules as
    # ``LeaveService._import_year``, minus the request context a job doesn't have.
    existing = (
        (
            await session.execute(
                select(LeaveHoliday).where(
                    LeaveHoliday.org_id == org.id,
                    LeaveHoliday.date >= date(year, 1, 1),
                    LeaveHoliday.date < date(year + 1, 1, 1),
                )
            )
        )
        .scalars()
        .all()
    )
    by_key = {row.key: row for row in existing if row.key is not None}
    by_date = {row.date: row for row in existing}

    created = 0
    for holiday in generated:
        current = by_key.get(holiday.key)
        if current is not None:
            if current.date != holiday.day and holiday.day not in by_date:
                by_date.pop(current.date, None)
                current.date = holiday.day
                by_date[holiday.day] = current
            continue
        if holiday.day in by_date:
            continue
        row = LeaveHoliday(
            org_id=org.id,
            date=holiday.day,
            name_i18n=holiday.name_i18n,
            active=True,
            source=country,
            key=holiday.key,
        )
        session.add(row)
        by_date[holiday.day] = row
        by_key[holiday.key] = row
        created += 1

    if created:
        logger.info("seeded %s holidays for %s in org %s", created, year, org.slug)


async def import_next_year_holidays(ctx: dict) -> None:
    """ARQ entrypoint: December, once, for every active org (issue #47)."""
    await run_per_org(_import_next_year)


async def _generate_next_year(org: Org, session: AsyncSession) -> None:
    """Seed next year's missing entitlements for the whole staff (#108).

    Through the same service core the "Genereer" button and the contract hook use (#105) — via
    a system context, so the tenant-scoped repos and the RLS GUC hold exactly as on the request
    path. Missing rows only; a tenant's manual grants are never touched.
    """
    from app.modules.leave.service import LeaveService

    year = datetime.now(await org_zoneinfo(session, org.id)).year + 1
    created = await LeaveService(system_context(org, session)).seed_entitlements(year)
    if created:
        logger.info("seeded %s entitlements for %s in org %s", created, year, org.slug)


async def generate_next_year_entitlements(ctx: dict) -> None:
    """ARQ entrypoint: December, once, for every active org (issue #108)."""
    await run_per_org(_generate_next_year)
