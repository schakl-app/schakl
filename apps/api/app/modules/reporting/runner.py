"""What a worker actually does to a report (issue #300).

Split from ``service.py`` because the two callers are different in kind: a request has a user,
a permission set and a horizon; a job has an org and a session. Everything below takes a
:class:`~app.core.events.SystemContext` and works for both — the request path calls
:func:`run_report` too, through the same job.

The order is the design (see ``generate.py``): the numbers are frozen before the model runs,
the document is rendered from the same frozen numbers, and a failure at any step leaves a row
whose ``status`` and ``warnings`` say what happened rather than a half-written record.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext, emit
from app.core.models import Org
from app.db import set_current_org
from app.modules.companies.models import Company
from app.modules.reporting import generate, seeds
from app.modules.reporting.models import (
    Report,
    ReportAudience,
    ReportDelivery,
    ReportingSettings,
    ReportProfile,
    ReportStatus,
    ReportTemplate,
    ReportTone,
)
from app.registry import ReportWindow

logger = logging.getLogger("schakl.reporting")


async def _effective_schedule(
    session: AsyncSession, org: Org, profile: ReportProfile | None
) -> dict:
    row = await session.scalar(
        select(ReportingSettings).where(ReportingSettings.org_id == org.id)
    )
    return {
        **seeds.DEFAULT_SCHEDULE,
        **((row.schedule if row else None) or {}),
        **((profile.schedule if profile else None) or {}),
    }


async def schedule_report(
    session: AsyncSession, org: Org, company_id: uuid.UUID, audience: str
) -> uuid.UUID | None:
    """Create (or find) this period's row and return its id. Idempotent by construction.

    The unique index on ``(org, company, audience, period_start)`` is what makes a second tick
    a no-op rather than a second document — and therefore what makes it impossible to mail a
    client the same month twice.
    """
    ctx = SystemContext(org=org, session=session)
    company = await session.scalar(
        select(Company).where(Company.org_id == org.id, Company.id == company_id)
    )
    if company is None:
        return None
    profile = await session.scalar(
        select(ReportProfile).where(
            ReportProfile.org_id == org.id, ReportProfile.company_id == company_id
        )
    )
    schedule = await _effective_schedule(session, org, profile)
    locale = (profile.locale if profile else None) or "nl"
    window = await generate.resolve_window(
        ctx, company_id, schedule=schedule, locale=locale
    )
    existing = await session.scalar(
        select(Report).where(
            Report.org_id == org.id,
            Report.company_id == company_id,
            Report.audience == audience,
            Report.period_start == window.start,
        )
    )
    if existing is not None:
        if existing.status in (ReportStatus.READY.value, ReportStatus.SENT.value):
            return None  # already done this month; a tick must not redo it
        report = existing
    else:
        template_id = None
        if profile is not None:
            template_id = (
                profile.template_id
                if audience == ReportAudience.CLIENT.value
                else profile.internal_template_id
            )
        report = Report(
            org_id=org.id,
            company_id=company_id,
            company_name=company.name,
            template_id=template_id,
            audience=audience,
            status=ReportStatus.DRAFT.value,
            locale=locale,
            period_start=window.start,
            period_end=window.end,
            compare_start=window.compare_start,
            compare_end=window.compare_end,
        )
        report.title = generate.report_title(report)
        session.add(report)
        await session.flush()
    report.status = ReportStatus.GENERATING.value
    await session.flush()
    return report.id


async def run_report(session: AsyncSession, org: Org, report_id: uuid.UUID) -> None:
    """Gather → snapshot → narrate → render → (send). One report, one transaction."""
    await set_current_org(session, org.id)
    report = await session.scalar(
        select(Report).where(Report.org_id == org.id, Report.id == report_id)
    )
    if report is None:
        return
    ctx = SystemContext(org=org, session=session)
    try:
        await _run(ctx, session, org, report)
        await session.commit()
    except Exception:
        logger.exception("reporting: run failed for %s", report_id)
        await session.rollback()
        # Persisting the failure needs its own transaction: the one that raised is gone, and
        # a report stuck in `generating` forever is indistinguishable from one still running
        # (the lesson from `persisting-state-before-raising-is-lost`).
        await set_current_org(session, org.id)
        failed = await session.scalar(
            select(Report).where(Report.org_id == org.id, Report.id == report_id)
        )
        if failed is not None:
            failed.status = ReportStatus.FAILED.value
            failed.warnings = [
                *(failed.warnings or []),
                {"code": "reporting.warning.run_failed", "detail": ""},
            ]
            await session.commit()


async def _run(
    ctx: SystemContext, session: AsyncSession, org: Org, report: Report
) -> None:
    profile = await session.scalar(
        select(ReportProfile).where(
            ReportProfile.org_id == org.id, ReportProfile.company_id == report.company_id
        )
    )
    window = ReportWindow(
        company_id=report.company_id,
        start=report.period_start,
        end=report.period_end,
        compare_start=report.compare_start,
        compare_end=report.compare_end,
        locale=report.locale,
    )
    template = await _template(session, org, report)
    gathered = await generate.gather_sections(
        ctx, window, report.audience, (template.layout if template else None)
    )
    if not gathered.sections:
        # Nothing to report on is a real state — a client with no linked properties — and it
        # is not a crash. It is `failed` with a reason so somebody links an account.
        report.status = ReportStatus.FAILED.value
        report.warnings = [
            *gathered.warnings,
            {"code": "reporting.warning.no_data", "detail": ""},
        ]
        return

    report.data_snapshot = generate.build_snapshot(
        window=window,
        company_name=report.company_name,
        gathered=gathered,
        locale=report.locale,
    )
    report.warnings = list(gathered.warnings)

    tone = await _tone(session, org, profile)
    narrative, warnings = await generate.write_prose(
        ctx,
        snapshot=report.data_snapshot,
        gathered=gathered,
        profile=profile,
        tone=tone,
        locale=report.locale,
        audience=report.audience,
    )
    # A hand-edited paragraph survives a regenerate: the reviewer's sentence is the one that
    # was approved, and silently replacing it is the fastest way to make somebody stop
    # trusting the button.
    kept = {
        key: value
        for key, value in (report.narrative or {}).items()
        if key in set(report.edited_sections or [])
    }
    report.narrative = {**narrative, **kept}
    report.warnings = [*report.warnings, *warnings]

    stored = await generate.render_pdf(ctx, report, template)
    report.pdf_file_id = stored.id
    report.status = ReportStatus.READY.value
    await session.flush()

    schedule = await _effective_schedule(session, org, profile)
    if report.audience == ReportAudience.CLIENT.value:
        if schedule.get("publish_to_portal", True):
            report.published_at = report.published_at or datetime.now(UTC)
        if str(schedule.get("delivery")) == ReportDelivery.AUTO.value:
            await _auto_send(ctx, report)
    await emit("report.ready", ctx, {"report_id": str(report.id)})


async def _auto_send(ctx: SystemContext, report: Report) -> None:
    """Send without review — only where the profile explicitly asked for it.

    A failure here does **not** fail the report: it is ready, it is on the portal, and the
    reviewer can send it by hand. Marking the whole run failed because an SMTP server was
    briefly down would hide a document that is perfectly good.
    """
    from app.modules.reporting.delivery import send_report

    try:
        await send_report(ctx, report)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reporting: auto-send failed for %s: %s", report.id, exc)
        report.warnings = [
            *(report.warnings or []),
            {"code": "reporting.warning.send_failed", "detail": str(exc)[:200]},
        ]


async def _template(
    session: AsyncSession, org: Org, report: Report
) -> ReportTemplate | None:
    if report.template_id is not None:
        template = await session.scalar(
            select(ReportTemplate).where(
                ReportTemplate.org_id == org.id, ReportTemplate.id == report.template_id
            )
        )
        if template is not None:
            return template
    return await session.scalar(
        select(ReportTemplate)
        .where(
            ReportTemplate.org_id == org.id,
            ReportTemplate.audience == report.audience,
            ReportTemplate.is_default.is_(True),
        )
        .limit(1)
    )


async def _tone(
    session: AsyncSession, org: Org, profile: ReportProfile | None
) -> ReportTone | None:
    if profile is not None and profile.tone_id is not None:
        tone = await session.scalar(
            select(ReportTone).where(
                ReportTone.org_id == org.id,
                ReportTone.id == profile.tone_id,
                ReportTone.active.is_(True),
            )
        )
        if tone is not None:
            return tone
    return await session.scalar(
        select(ReportTone)
        .where(
            ReportTone.org_id == org.id,
            ReportTone.is_default.is_(True),
            ReportTone.active.is_(True),
        )
        .limit(1)
    )
