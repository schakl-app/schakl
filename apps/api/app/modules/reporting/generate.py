"""The pipeline: period → sections → snapshot → narrative → PDF (issue #300).

Five steps, and the order matters. The numbers are frozen *before* the model runs, so the prose
describes exactly what the document prints; the PDF is rendered *after* the prose, from the same
snapshot; and the whole thing is idempotent on ``(company, audience, period)``, so re-running a
schedule updates one row rather than mailing a client a second copy.

    resolve_period ──▶ gather_sections ──▶ snapshot ──▶ narrative ──▶ render ──▶ file
     (calendar        (registry-declared    (frozen     (core/ai)     (core/    (dedup
      month, org       providers, each       JSONB)                   documents) blob)
      timezone)        permission-filtered)

**The period is a calendar month.** The workflow this replaces took "today minus one month" to
"yesterday", so a run on 5 August covered 5 July to 4 August and filed it as *Maandrapportage
juli*. A client reading "juli" means July.

**A section that fails is a warning, not a failure.** A report whose SE Ranking project is
unreachable should still go out with its traffic in it, saying on the *agency's* copy of the
warnings that rankings are missing. The only fatal errors are having no sections at all and
failing to render.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from io import BytesIO
from typing import Any

from app.config import settings as app_settings
from app.core.ai.service import AIService
from app.core.periods import ComparePeriod, compare_window
from app.core.storage.models import StoredFile
from app.core.storage.service import write_file
from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.i18n import translate
from app.modules.reporting import narrative as narrative_mod
from app.modules.reporting import present, prompts
from app.modules.reporting.models import (
    Report,
    ReportAudience,
    ReportCompare,
    ReportProfile,
    ReportStatus,
    ReportTemplate,
    ReportTone,
)
from app.registry import ReportSectionSpec, ReportWindow, registry

logger = logging.getLogger("schakl.reporting")

_ONE_DAY = timedelta(days=1)


# --------------------------------------------------------------------------------------- #
# 1. The period
# --------------------------------------------------------------------------------------- #
def previous_month(today: date) -> tuple[date, date]:
    """The calendar month before ``today``'s. Whole, always — never a trailing 30 days."""
    first_of_this = today.replace(day=1)
    end = first_of_this - _ONE_DAY
    return end.replace(day=1), end


def previous_quarter(today: date) -> tuple[date, date]:
    quarter = (today.month - 1) // 3
    year = today.year if quarter else today.year - 1
    quarter = quarter or 4
    start = date(year, (quarter - 1) * 3 + 1, 1)
    last_month = start.month + 2
    return start, date(year, last_month, monthrange(year, last_month)[1])


def comparison(start: date, end: date, mode: str) -> tuple[date | None, date | None]:
    """The span this period is measured against — ``app/core/periods.py`` (#312).

    Kept as a named function here because the reporting pipeline reads better for it, but the
    date math moved to core the moment a *second* surface needed the same two rules: the
    marketing dashboard compares the same client's same numbers, and the two disagreeing about
    what "vorige periode" means is exactly the confusion this file's own docstring warns about.
    ``ReportCompare``'s members mirror :class:`ComparePeriod`'s values one for one.
    """
    return compare_window(start, end, ComparePeriod(mode))


# --------------------------------------------------------------------------------------- #
# 2. Sections
# --------------------------------------------------------------------------------------- #
@dataclass
class Gathered:
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    specs: dict[str, ReportSectionSpec] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)


def enabled_sections(
    audience: str, layout: dict | None, overrides: dict | None = None
) -> list[ReportSectionSpec]:
    """The registry's sections for this audience, ordered by the template and toggled by both.

    Resolution is three layers, each a **diff over the one before, never a snapshot**
    (docs/INVOICING.md's rule):

        registry  →  template layout  →  this client's own overrides

    A stored layout reorders and disables what it *mentions*, so a section a later release adds
    appears in every existing tenant's next report rather than being invisible to all of them
    until somebody re-saves a template. ``overrides`` is ``ReportProfile.sections`` — one
    client saying "not this one" (or "yes this one" over a template that hid it) without needing
    a template of their own, which was the only escape before #373 and left an agency
    maintaining a near-copy of the house template per client.

    Order stays the template's: what a client may change is *whether* a section prints, not where
    it goes. A per-client running order would make two reports from the same agency read like two
    different products, and nobody has asked for one.
    """
    available = registry.report_sections_for(audience, app_settings.enabled_modules)
    entries = (layout or {}).get("sections") or []
    own = {
        str(key): bool(value)
        for key, value in (overrides or {}).items()
        if isinstance(value, bool)
    }

    def on(key: str, template_says: bool) -> bool:
        return own.get(key, template_says)

    ordered: list[ReportSectionSpec] = []
    mentioned: set[str] = set()
    for entry in entries:
        key = str(entry.get("key")) if isinstance(entry, dict) else None
        spec = next((s for s in available if s.key == key), None)
        if spec is None or spec.key in mentioned:
            continue
        mentioned.add(spec.key)
        if on(spec.key, bool(entry.get("enabled", True))):
            ordered.append(spec)
    # A section the layout has never heard of is on unless this client says otherwise — the
    # whole reason resolution is a diff rather than a stored list.
    ordered.extend(
        spec for spec in available if spec.key not in mentioned and on(spec.key, True)
    )
    return ordered


async def gather_sections(
    ctx: RequestContext,
    window: ReportWindow,
    audience: str,
    layout: dict | None,
    overrides: dict | None = None,
) -> Gathered:
    out = Gathered()
    for spec in enabled_sections(audience, layout, overrides):
        # A section is *skipped*, never 403'd: a report is assembled from what the generating
        # caller may read, so a member without ad-spend access produces a report without it
        # rather than a failure they cannot fix.
        if spec.requires_permission and not ctx.can(spec.requires_permission):
            continue
        try:
            data = await spec.provider(ctx, window)
        except Exception as exc:  # noqa: BLE001 — one source must not cost the whole report
            logger.warning("reporting: section %s failed: %s", spec.key, exc)
            out.warnings.append(
                {"code": "reporting.warning.section_failed", "detail": spec.key}
            )
            continue
        if not data:
            continue
        out.sections[spec.key] = data
        out.order.append(spec.key)
        out.specs[spec.key] = spec
        for note in data.pop("notes", None) or []:
            if note not in out.warnings:
                out.warnings.append(note)
    return out


# --------------------------------------------------------------------------------------- #
# 3. The snapshot
# --------------------------------------------------------------------------------------- #
def build_snapshot(
    *,
    window: ReportWindow,
    company_name: str,
    gathered: Gathered,
    locale: str,
) -> dict[str, Any]:
    """Every number the document will print, frozen.

    This is what makes a report a record rather than a job output: opened next December it
    shows what it showed today. It is also what the model is handed — the prose and the tables
    therefore describe the same figures by construction, not by both re-querying and hoping.
    """
    return {
        "company": {"name": company_name},
        "period": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "label": prompts.period_label(window.start, window.end, locale),
        },
        "compare": (
            {
                "start": window.compare_start.isoformat(),
                "end": window.compare_end.isoformat(),
                "label": prompts.period_label(window.compare_start, window.compare_end, locale),
            }
            if window.compare_start and window.compare_end
            else None
        ),
        "order": gathered.order,
        "sections": gathered.sections,
    }


def client_name(company_name: str, profile: ReportProfile | None) -> str:
    """What this client is called **on their report** — the profile's name, or the CRM's.

    One function, because the answer has to be the same in five places at once: the title, the
    cover, the PDF filename, the covering e-mail and the snapshot the model reads. It resolves
    once, at creation, and is then snapshotted onto the row (``Report.company_name``);
    everything downstream keeps reading the row, so a later rename cannot re-title a document a
    client already has.
    """
    override = (profile.display_name or "").strip() if profile is not None else ""
    return override or company_name


def profile_facts(profile: ReportProfile | None) -> dict[str, Any]:
    """The client's own facts, as **data** for the model (see ``prompts``' injection stance)."""
    if profile is None:
        return {}
    return {
        key: value
        for key, value in {
            "business_context": profile.business_context,
            "goals": profile.goals,
            "seo_focus": profile.seo_focus,
            "sea_focus": profile.sea_focus,
            "key_services": profile.key_services,
            "priority_pages": profile.priority_pages,
            "conversion_goals": profile.conversion_goals,
            "scope_notes": profile.scope_notes,
            "avoid_topics": profile.avoid_topics,
        }.items()
        if value
    }


def tone_payload(tone: ReportTone | None) -> dict[str, Any] | None:
    if tone is None:
        return None
    return {
        "instructions": tone.instructions,
        "banned_phrases": list(tone.banned_phrases or []),
        "preferred_phrases": list(tone.preferred_phrases or []),
    }


# --------------------------------------------------------------------------------------- #
# 4. The narrative
# --------------------------------------------------------------------------------------- #
def section_briefs(
    gathered: Gathered, locale: str, audience: str
) -> list[tuple[str, str]]:
    """``[(key, brief)]`` for the sections that actually have data.

    Only those: asking the model to write about a section this client has no data for invites
    it to write something, and an invented paragraph about search rankings for a client who
    tracks none is exactly the failure a grounded report exists to prevent.
    """
    extra = (
        [
            ("actions", translate("reporting.brief.actions", locale)),
            ("questions", translate("reporting.brief.questions", locale)),
        ]
        if audience == ReportAudience.INTERNAL.value
        else []
    )
    return [
        (key, translate(gathered.specs[key].brief_key, locale))
        for key in gathered.order
        if gathered.specs[key].brief_key
    ] + extra


async def write_prose(
    ctx: RequestContext,
    *,
    snapshot: dict[str, Any],
    gathered: Gathered,
    profile: ReportProfile | None,
    tone: ReportTone | None,
    locale: str,
    audience: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    service = AIService(ctx)
    compare = snapshot.get("compare") or {}
    return await narrative_mod.write_narrative(
        service,
        presented=present.document(
            snapshot,
            locale=locale,
            section_titles={
                key: translate(spec.title_key, locale)
                for key, spec in gathered.specs.items()
            },
            internal=audience == ReportAudience.INTERNAL.value,
        ),
        profile=profile_facts(profile),
        tone=tone_payload(tone),
        sections=section_briefs(gathered, locale, audience),
        locale=locale,
        brand=ctx.org.name,
        period_label=snapshot["period"]["label"],
        compare_label=compare.get("label"),
        internal=audience == ReportAudience.INTERNAL.value,
    )


# --------------------------------------------------------------------------------------- #
# 5. The document
# --------------------------------------------------------------------------------------- #
async def render_pdf(
    ctx: RequestContext, report: Report, template: ReportTemplate | None
) -> StoredFile:
    """Render and store the PDF, de-duplicated like every other stored file.

    Rendering is CPU-bound and blocking, so it runs in a thread — the rule the storage routes
    and the invoice renderer already follow. The bytes go through ``write_file``, so two
    identical reports (a re-render that changed nothing) share one blob and neither row may
    ever delete it (``docs/STORAGE.md``).
    """
    from app.modules.reporting.render import render_report_html
    from app.modules.reporting.render.engine import ENGINE

    html = await render_report_html(ctx, report, template)
    pdf = await asyncio.to_thread(ENGINE.html_to_pdf, html, locale=report.locale)
    filename = f"{_document_name(report)}.pdf"
    return await write_file(
        ctx,
        filename=filename,
        content_type="application/pdf",
        stream=BytesIO(pdf),
        entity_type=Report.__entity_type__,
        entity_id=report.id,
    )


def _document_name(report: Report) -> str:
    label = prompts.period_label(report.period_start, report.period_end, report.locale)
    kind = translate(
        "reporting.document.internal"
        if report.audience == ReportAudience.INTERNAL.value
        else "reporting.document.client",
        report.locale,
    )
    safe = "".join(ch for ch in report.company_name if ch.isalnum() or ch in " -_&.").strip()
    return f"{kind} {safe} - {label}"[:120]


async def resolve_window(
    ctx: RequestContext,
    company_id: uuid.UUID,
    *,
    schedule: dict[str, Any],
    locale: str,
    period: tuple[date, date] | None = None,
) -> ReportWindow:
    """The window a run covers, in the **org's** timezone (CLAUDE.md §8: no private clocks)."""
    if period is None:
        today = await org_today(ctx.session, ctx.org.id)
        cadence = str(schedule.get("cadence") or "monthly")
        period = (
            previous_quarter(today) if cadence == "quarterly" else previous_month(today)
        )
    compare_start, compare_end = comparison(
        period[0], period[1], str(schedule.get("compare") or ReportCompare.YEAR.value)
    )
    return ReportWindow(
        company_id=company_id,
        start=period[0],
        end=period[1],
        compare_start=compare_start,
        compare_end=compare_end,
        locale=locale,
    )


def report_title(report: Report) -> str:
    return translate(
        "reporting.title.internal"
        if report.audience == ReportAudience.INTERNAL.value
        else "reporting.title.client",
        report.locale,
        client=report.company_name,
        period=prompts.period_label(report.period_start, report.period_end, report.locale),
    )


def is_terminal(status: str) -> bool:
    return status in (ReportStatus.READY.value, ReportStatus.SENT.value)
