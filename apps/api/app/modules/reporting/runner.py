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

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

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

#: How long the model gets to write the whole document before the run gives up on prose and
#: keeps its numbers. It needs a bound of its own: ``complete`` streams, and httpx's read
#: timeout is *per chunk*, so a model that emits a token every few seconds for an hour never
#: trips it. Without this the only thing ending such a run is the job runner killing it, which
#: is the difference between a report that says "de tekst kwam niet op tijd" and one that sits
#: on ``generating`` for ever.
AI_TIMEOUT_SECONDS = 240

#: The arq job timeout for ``reporting_run_report``, declared rather than inherited. arq's
#: default is 300 s — less than gathering several external sources plus the model call above
#: plus a WeasyPrint render, so the default was killing healthy runs.
RUN_TIMEOUT_SECONDS = 900

#: After this, a run is not slow, it is gone: the worker was restarted, the box was rebooted,
#: or the job died somewhere no ``except`` could reach. Comfortably above
#: :data:`RUN_TIMEOUT_SECONDS` so the reaper never races a run that is merely taking its time.
STALE_RUN_SECONDS = 1200


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


def run_job_id(report_id: uuid.UUID, started_at: datetime) -> str:
    """The arq job id for one **attempt** at one report.

    Not the report id alone, which is what it used to be: arq declines to enqueue an id whose
    job is queued *or whose result is still in Redis* — ``keep_result``, an hour by default —
    and returns ``None`` for it. So every retry inside that hour flipped the row to
    ``generating`` and queued precisely nothing. Keyed by attempt, a genuine double delivery of
    the *same* attempt still deduplicates, which is the only thing the shared id ever bought.
    """
    # Milliseconds, not seconds: a retry after a fast failure lands in the same second as the
    # attempt it is retrying, and a second-resolution stamp would hand it the same id — the
    # very collision this exists to break.
    return f"reporting-run-{report_id}-{int(started_at.timestamp() * 1000)}"


async def schedule_report(
    session: AsyncSession, org: Org, company_id: uuid.UUID, audience: str
) -> tuple[uuid.UUID, datetime] | None:
    """Create (or find) this period's row and return ``(id, started_at)``. Idempotent.

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
        if run_in_flight(existing):
            # Somebody pressed the button minutes before the schedule came round. Two workers
            # on one report is two renders and two AI bills for one document.
            return None
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
            company_name=generate.client_name(company.name, profile),
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
    started_at = datetime.now(UTC)
    report.status = ReportStatus.GENERATING.value
    report.generation_started_at = started_at
    await session.flush()
    return report.id, started_at


def run_in_flight(report: Report) -> bool:
    """Is a worker plausibly still on this run?

    ``generating`` alone cannot answer it: the status says a worker *took* the job, not that
    the worker still exists. So it is read together with when the run started, and anything
    older than a run can possibly take is treated as gone — which is what lets somebody press
    the button again instead of waiting on a process that died an hour ago. A ``NULL`` stamp
    means a report from before that column existed, i.e. one of the runs already stuck when
    this shipped: also retryable, deliberately.

    The reaper asks the same question in SQL over a whole org; this is the per-row form the
    two schedulers use before they start anything.
    """
    if report.status != ReportStatus.GENERATING.value or report.generation_started_at is None:
        return False
    return report.generation_started_at > datetime.now(UTC) - timedelta(seconds=STALE_RUN_SECONDS)


async def run_report(session: AsyncSession, org: Org, report_id: uuid.UUID) -> None:
    """Gather → snapshot → narrate → render → (send). One report, one transaction."""
    # Read the id **now**, into a plain value. `org` was loaded in this session, and the
    # rollback in the failure path below expires every instance it holds — so the next
    # `org.id` is a lazy refresh, which under SQLAlchemy's async engine is not a slow query
    # but a `MissingGreenlet` raised from inside the handler that exists to record failures.
    # That is how the *ordinary* failure path came to fail too, silently, leaving exactly the
    # `generating` it was written to prevent.
    org_id = org.id
    await set_current_org(session, org_id)
    report = await session.scalar(
        select(Report).where(Report.org_id == org_id, Report.id == report_id)
    )
    if report is None:
        # Not nothing: the run was queued for a row this session cannot see. Either it was
        # deleted between enqueue and pickup, or — the bug this log exists to name — it was
        # enqueued before the transaction that created it committed, so the worker won the
        # race and the row is now sitting on `generating` with nobody working on it.
        logger.warning(
            "reporting: run %s has no report row (deleted, or enqueued too early)", report_id
        )
        return
    ctx = SystemContext(org=org, session=session)
    try:
        await _run(ctx, session, org, report)
        await session.commit()
    except BaseException as exc:
        # **BaseException, not Exception.** A job that outlives its timeout is *cancelled*, and
        # `asyncio.CancelledError` has not been an `Exception` since 3.8 — so the whole block
        # below, whose entire purpose is that a run never dies silently, was the one thing a
        # timeout skipped. The report kept the `generating` the request had already committed,
        # for ever, with no warning and no reaper (the lesson from
        # `persisting-state-before-raising-is-lost`, one class up the hierarchy).
        cancelled = isinstance(exc, asyncio.CancelledError)
        if cancelled:
            logger.warning(
                "reporting: run cancelled for %s (timed out or worker shutting down)", report_id
            )
        else:
            logger.exception("reporting: run failed for %s", report_id)
        await session.rollback()
        # Persisting the failure needs its own transaction: the one that raised is gone. The
        # RLS GUC went with it too (`set_config(..., true)` is transaction-local), so rebind
        # before reading — an unbound read matches no rows and would report success.
        await set_current_org(session, org_id)
        failed = await session.scalar(
            select(Report).where(Report.org_id == org_id, Report.id == report_id)
        )
        if failed is not None:
            failed.status = ReportStatus.FAILED.value
            failed.warnings = [
                *(failed.warnings or []),
                {
                    "code": (
                        "reporting.warning.run_timeout"
                        if cancelled
                        else "reporting.warning.run_failed"
                    ),
                    "detail": "",
                },
            ]
            await session.commit()
        if not isinstance(exc, Exception):
            # Never swallow what is not an ordinary error: a cancellation means the loop asked
            # this task to stop and arq's `wait_for` is entitled to hear that it did, and
            # `SystemExit`/`KeyboardInterrupt` are the process leaving. An ordinary failure
            # stays swallowed on purpose — it is recorded on the row above, and letting it out
            # would have arq retry this whole expensive pipeline five times over.
            raise


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
    # A run bounds its own slowest step rather than letting the job runner bound the whole run.
    # The difference is what the tenant gets: a document with its numbers, its tables and a
    # warning saying the prose did not arrive — against nothing at all, for ever.
    try:
        async with asyncio.timeout(AI_TIMEOUT_SECONDS):
            narrative, warnings = await generate.write_prose(
                ctx,
                snapshot=report.data_snapshot,
                gathered=gathered,
                profile=profile,
                tone=tone,
                locale=report.locale,
                audience=report.audience,
            )
    except TimeoutError:
        logger.warning(
            "reporting: narrative timed out after %ss for report %s",
            AI_TIMEOUT_SECONDS,
            report.id,
        )
        narrative, warnings = {}, [{"code": "reporting.warning.ai_timeout", "detail": ""}]
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
    # The marked default first, then simply the oldest of that audience — the same order
    # ``TemplateService.resolve`` takes, and for the same reason: a run that resolves to no
    # template at all prints the shipped design and drops the tenant's accent, cover and intro
    # without saying so.
    return await session.scalar(
        select(ReportTemplate)
        .where(
            ReportTemplate.org_id == org.id,
            ReportTemplate.audience == report.audience,
        )
        .order_by(
            ReportTemplate.is_default.desc(),
            ReportTemplate.created_at,
            ReportTemplate.id,
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
    default = await session.scalar(
        select(ReportTone)
        .where(
            ReportTone.org_id == org.id,
            ReportTone.is_default.is_(True),
            ReportTone.active.is_(True),
        )
        .limit(1)
    )
    if default is not None:
        return default
    # Nobody has opened Instellingen → Rapportage yet, so the seeded house voice does not exist
    # and this run would write in no particular voice at all. Seed it here rather than let the
    # *first* report — the one somebody judges the feature by — come out toneless.
    from app.modules.reporting.service import ToneService

    return await ToneService(SystemContext(org=org, session=session)).ensure_default()
