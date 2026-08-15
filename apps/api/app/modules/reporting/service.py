"""Reporting service (issue #300): tones, templates, profiles, and the run itself.

Everything tenant-scoped goes through ``ctx.repo(...)`` so the company horizon rides along
(§15, #285), and every read of a *report* goes through :meth:`ReportService._scoped`, which is
the one place the portal narrowing is applied — the model declares it, this resolves it, and no
route re-implements it.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select

from app.config import settings as app_settings
from app.core.activity.service import ActivityService, snapshot
from app.core.events import emit
from app.core.jobs import enqueue
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.core.timezone import org_today
from app.db import set_current_org
from app.errors import AppError
from app.i18n import translate
from app.modules.companies.models import Company
from app.modules.reporting import generate, present, seeds
from app.modules.reporting.models import (
    Report,
    ReportAudience,
    ReportCadence,
    ReportingSettings,
    ReportProfile,
    ReportStatus,
    ReportTemplate,
    ReportTone,
)
from app.modules.reporting.schemas import (
    ReportDetail,
    ReportingSettingsRead,
    ReportingSettingsWrite,
    ReportList,
    ReportNarrativeUpdate,
    ReportProfileRead,
    ReportProfileWrite,
    ReportRow,
    ReportRunBatchRequest,
    ReportRunBatchResult,
    ReportRunRequest,
    ReportSendRequest,
    ReportTemplatePreviewRequest,
    ReportTemplateRead,
    ReportTemplateWrite,
    ReportToneRead,
    ReportToneWrite,
    SectionCatalogEntry,
)
from app.registry import registry

logger = logging.getLogger("schakl.reporting")

_TRACKED_PROFILE_FIELDS = (
    "display_name", "locale", "tone_id", "template_id", "internal_enabled", "active",
    "business_context", "goals", "seo_focus", "sea_focus",
    # Which sections this client's document carries (#373): a change here changes what a client
    # is sent, so it belongs on the trail beside the tone and the template.
    "sections",
)
_TRACKED_REPORT_FIELDS = ("status", "title", "published_at", "sent_at")

_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG.sub("-", (value or "").strip().lower()).strip("-")[:64] or "tone"


def _run_in_flight(report: Report) -> bool:
    """One copy of "is a worker still on this?", declared in ``runner`` beside the timeouts.

    Imported inside the function rather than at module scope: ``runner`` reaches back into this
    module for the tone fallback, and a top-level import would close the circle.
    """
    from app.modules.reporting.runner import run_in_flight

    return run_in_flight(report)


# --------------------------------------------------------------------------------------- #
# Tones
# --------------------------------------------------------------------------------------- #
class ToneService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def ensure_default(self) -> ReportTone:
        """The seeded house voice, created on first use.

        Not a migration: a migration must not import a module's evolving vocabulary
        (docs/WORKFLOW.md), and an org that never reports should not carry a record it never
        asked for. Idempotent — a tenant who edits or deletes it is not given it back.
        """
        existing = await self.ctx.session.scalar(
            select(ReportTone).where(
                ReportTone.org_id == self.ctx.org.id,
                ReportTone.key == seeds.DEFAULT_TONE_KEY,
            )
        )
        if existing is not None:
            return existing
        return await self.ctx.repo(ReportTone).create(
            key=seeds.DEFAULT_TONE_KEY,
            name=seeds.DEFAULT_TONE_NAME,
            description=seeds.DEFAULT_TONE_DESCRIPTION,
            instructions=seeds.DEFAULT_TONE_INSTRUCTIONS,
            banned_phrases=list(seeds.DEFAULT_BANNED_PHRASES),
            preferred_phrases=list(seeds.DEFAULT_PREFERRED_PHRASES),
            is_default=True,
            active=True,
            position=0,
        )

    async def list(self) -> list[ReportToneRead]:
        await self.ensure_default()
        rows = (
            await self.ctx.session.execute(
                self.ctx.repo(ReportTone)
                .scoped_select()
                .order_by(ReportTone.position, ReportTone.name)
            )
        ).scalars().all()
        return [ReportToneRead.model_validate(row) for row in rows]

    async def resolve(self, tone_id: uuid.UUID | None) -> ReportTone | None:
        """The profile's tone, else the org default. NULL means *inherit*, never *none*."""
        if tone_id is not None:
            tone = await self.ctx.repo(ReportTone).get(tone_id)
            if tone is not None and tone.active:
                return tone
        row = await self.ctx.session.scalar(
            self.ctx.repo(ReportTone)
            .scoped_select()
            .where(ReportTone.is_default.is_(True), ReportTone.active.is_(True))
            .limit(1)
        )
        return row or await self.ensure_default()

    async def create(self, data: ReportToneWrite) -> ReportToneRead:
        self.ctx.require("reporting.settings.manage")
        key = await self._unique_key(_slugify(data.name))
        if data.is_default:
            await self._clear_default()
        row = await self.ctx.repo(ReportTone).create(key=key, **_tone_values(data))
        return ReportToneRead.model_validate(row)

    async def update(self, tone_id: uuid.UUID, data: ReportToneWrite) -> ReportToneRead:
        self.ctx.require("reporting.settings.manage")
        row = await self.ctx.repo(ReportTone).get_or_404(tone_id)
        if data.is_default and not row.is_default:
            await self._clear_default()
        for field, value in _tone_values(data).items():
            setattr(row, field, value)
        await self.ctx.session.flush()
        return ReportToneRead.model_validate(row)

    async def delete(self, tone_id: uuid.UUID) -> None:
        self.ctx.require("reporting.settings.manage")
        row = await self.ctx.repo(ReportTone).get_or_404(tone_id)
        remaining = await self.ctx.session.scalar(
            select(func.count())
            .select_from(ReportTone)
            .where(ReportTone.org_id == self.ctx.org.id, ReportTone.active.is_(True))
        )
        # Never leave the org without a voice: the same "never lock the tenant out" reasoning
        # §15 applies to the last role that can manage roles.
        if (remaining or 0) <= 1:
            raise AppError(
                "conflict", "errors.reporting.last_tone", status_code=409
            )
        await self.ctx.repo(ReportTone).delete(row)

    async def _clear_default(self) -> None:
        rows = (
            await self.ctx.session.execute(
                self.ctx.repo(ReportTone).scoped_select().where(ReportTone.is_default.is_(True))
            )
        ).scalars().all()
        for row in rows:
            row.is_default = False

    async def _unique_key(self, base: str) -> str:
        for suffix in ("", *(f"-{n}" for n in range(2, 50))):
            candidate = f"{base}{suffix}"[:64]
            exists = await self.ctx.session.scalar(
                select(ReportTone.id).where(
                    ReportTone.org_id == self.ctx.org.id, ReportTone.key == candidate
                )
            )
            if exists is None:
                return candidate
        return f"{base}-{uuid.uuid4().hex[:6]}"


def _tone_values(data: ReportToneWrite) -> dict[str, Any]:
    return {
        "name": data.name,
        "description": data.description,
        "instructions": data.instructions,
        "banned_phrases": [p.strip() for p in data.banned_phrases if p.strip()],
        "preferred_phrases": [p.strip() for p in data.preferred_phrases if p.strip()],
        "is_default": data.is_default,
        "active": data.active,
        "position": data.position,
    }


# --------------------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------------------- #
class TemplateService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def list(self, audience: str | None = None) -> list[ReportTemplateRead]:
        stmt = self.ctx.repo(ReportTemplate).scoped_select().order_by(ReportTemplate.name)
        if audience:
            stmt = stmt.where(ReportTemplate.audience == audience)
        rows = (await self.ctx.session.execute(stmt)).scalars().all()
        return [ReportTemplateRead.model_validate(row) for row in rows]

    def catalog(self) -> list[SectionCatalogEntry]:
        """Every section a template may order — the registry, made visible to the editor."""
        out: list[SectionCatalogEntry] = []
        for audience in (ReportAudience.CLIENT.value, ReportAudience.INTERNAL.value):
            for spec in registry.report_sections_for(audience, app_settings.enabled_modules):
                if any(entry.key == spec.key for entry in out):
                    continue
                out.append(
                    SectionCatalogEntry(
                        key=spec.key,
                        title_key=spec.title_key,
                        audience=spec.audience,
                        module=spec.key.split(".", 1)[0],
                        source_key=spec.source_key,
                    )
                )
        return out

    async def resolve(
        self, template_id: uuid.UUID | None, audience: str
    ) -> ReportTemplate | None:
        """The template a run of this audience prints with — chosen, marked, or simply the one.

        The fallback past ``is_default`` is not tidiness. A template carries the design, the
        accent, the cover photograph and the intro paragraph, and resolving to ``None`` throws
        **all four away silently**: the run renders the shipped design on the org's brand colour
        and nothing on screen says why the photograph the tenant uploaded is missing. A tenant
        who has made exactly one template for an audience has unambiguously said which one to
        use, whether or not they also found the "standaard" box — so oldest-first is the answer,
        and it is deterministic rather than "whatever the planner returned".
        """
        if template_id is not None:
            template = await self.ctx.repo(ReportTemplate).get(template_id)
            if template is not None:
                return template
        stmt = (
            self.ctx.repo(ReportTemplate)
            .scoped_select()
            .where(ReportTemplate.audience == audience)
            .order_by(
                ReportTemplate.is_default.desc(),
                ReportTemplate.created_at,
                ReportTemplate.id,
            )
            .limit(1)
        )
        return await self.ctx.session.scalar(stmt)

    async def create(self, data: ReportTemplateWrite) -> ReportTemplateRead:
        self.ctx.require("reporting.settings.manage")
        self._validate(data)
        if data.is_default:
            await self._clear_default(data.audience.value)
        values = _template_values(data)
        # The first template of an audience *is* the default. Nobody makes one template and
        # means "use none of it", and leaving the mark off by default is what let a tenant
        # design a cover, save it, and get a report that ignored the lot.
        if not values["is_default"] and not await self._any(data.audience.value):
            values["is_default"] = True
        row = await self.ctx.repo(ReportTemplate).create(**values)
        return ReportTemplateRead.model_validate(row)

    async def _any(self, audience: str) -> bool:
        return (
            await self.ctx.session.scalar(
                self.ctx.repo(ReportTemplate)
                .scoped_select()
                .where(ReportTemplate.audience == audience)
                .limit(1)
            )
        ) is not None

    async def update(self, template_id: uuid.UUID, data: ReportTemplateWrite) -> ReportTemplateRead:
        self.ctx.require("reporting.settings.manage")
        row = await self.ctx.repo(ReportTemplate).get_or_404(template_id)
        self._validate(data, current=row)
        if data.is_default and not row.is_default:
            await self._clear_default(data.audience.value)
        for field, value in _template_values(data).items():
            setattr(row, field, value)
        await self.ctx.session.flush()
        return ReportTemplateRead.model_validate(row)

    async def delete(self, template_id: uuid.UUID) -> None:
        self.ctx.require("reporting.settings.manage")
        await self.ctx.repo(ReportTemplate).delete(
            await self.ctx.repo(ReportTemplate).get_or_404(template_id)
        )

    async def preview(self, data: ReportTemplatePreviewRequest) -> str:
        """Render an **unsaved** template — the editor's live preview.

        Against the tenant's own most recent report of that audience wherever there is one.
        That is the whole argument for the shared renderer restated at the editing end: what
        the author sees is the page their client will get, on their client's real numbers,
        and there is no second implementation that could disagree with it. Only a tenant who
        has never run reporting falls back to :func:`sample_report`.

        The template is a ``ReportTemplate`` instance that is never added to the session. It
        exists to carry six values into ``render_report_html`` in the shape that function
        already takes, which is cheaper and less likely to drift than a parallel protocol —
        and an ORM object nobody adds is an ordinary Python object.
        """
        self.ctx.require("reporting.settings.manage")
        from app.modules.reporting.render import render_report_html
        from app.modules.reporting.render.engine import ENGINE
        from app.modules.reporting.render.sample import sample_report

        # The same refusal the save path gives, so an unparseable body is a message under the
        # editor rather than a 500 from the renderer.
        ENGINE.validate_custom_source(data.custom_html, data.custom_css)
        audience = data.audience.value
        report = await self.ctx.session.scalar(
            self.ctx.repo(Report)
            .scoped_select()
            .where(Report.audience == audience)
            .order_by(Report.period_start.desc(), Report.created_at.desc())
            .limit(1)
        )
        if report is None:
            settings = await ReportingSettingsService(self.ctx).get()
            report = sample_report(
                audience,
                settings.default_locale,
                await org_today(self.ctx.session, self.ctx.org.id),
            )
        template = ReportTemplate(
            name="",
            audience=audience,
            design=data.design,
            layout={},
            custom_html=data.custom_html,
            custom_css=data.custom_css,
            accent_color=data.accent_color,
            cover_image_file_id=data.cover_image_file_id,
            intro_text=data.intro_text,
        )
        return await render_report_html(self.ctx, report, template)

    def _validate(self, data: ReportTemplateWrite, current: ReportTemplate | None = None) -> None:
        """Refuse a template that cannot render, at save time — and gate *authoring* code.

        Writing Jinja that runs on the agency's server is a strictly larger act than arranging
        blocks, so it carries its own permission — the rule ``docs/INVOICING.md`` states for
        the invoice designer. An **unchanged** body passes, so an admin without it can still
        rename a template that happens to carry custom HTML.
        """
        from app.modules.reporting.render.engine import ENGINE

        changed = current is None or (
            (data.custom_html or "") != (current.custom_html or "")
            or (data.custom_css or "") != (current.custom_css or "")
        )
        if changed and (data.custom_html or data.custom_css):
            self.ctx.require("reporting.settings.manage")
        ENGINE.validate_custom_source(data.custom_html, data.custom_css)

    async def _clear_default(self, audience: str) -> None:
        rows = (
            await self.ctx.session.execute(
                self.ctx.repo(ReportTemplate)
                .scoped_select()
                .where(
                    ReportTemplate.audience == audience,
                    ReportTemplate.is_default.is_(True),
                )
            )
        ).scalars().all()
        for row in rows:
            row.is_default = False


def _template_values(data: ReportTemplateWrite) -> dict[str, Any]:
    return {
        "name": data.name,
        "audience": data.audience.value,
        "design": data.design,
        "layout": data.layout.model_dump(mode="json"),
        "custom_html": data.custom_html,
        "custom_css": data.custom_css,
        "accent_color": data.accent_color,
        "cover_image_file_id": data.cover_image_file_id,
        "intro_text": data.intro_text,
        "is_default": data.is_default,
    }


# --------------------------------------------------------------------------------------- #
# Settings + profiles
# --------------------------------------------------------------------------------------- #
class ReportingSettingsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def row(self) -> ReportingSettings | None:
        return await self.ctx.session.scalar(
            select(ReportingSettings).where(ReportingSettings.org_id == self.ctx.org.id)
        )

    async def get(self) -> ReportingSettingsRead:
        row = await self.row()
        return ReportingSettingsRead(
            schedule={**seeds.DEFAULT_SCHEDULE, **((row.schedule if row else None) or {})},
            default_locale=(row.default_locale if row else "nl"),
            footer_text=(row.footer_text if row else None),
        )

    async def save(self, data: ReportingSettingsWrite) -> ReportingSettingsRead:
        self.ctx.require("reporting.settings.manage")
        row = await self.row()
        values = {
            "schedule": {
                key: value
                for key, value in data.schedule.model_dump(mode="json").items()
                if value is not None
            },
            "default_locale": data.default_locale,
            "footer_text": data.footer_text,
        }
        if row is None:
            await self.ctx.repo(ReportingSettings).create(**values)
        else:
            for field, value in values.items():
                setattr(row, field, value)
            await self.ctx.session.flush()
        return await self.get()


class ProfileService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.activity = ActivityService(ctx)

    async def _company_or_404(self, company_id: uuid.UUID) -> Company:
        return await self.ctx.repo(Company).get_or_404(company_id)

    async def get(self, company_id: uuid.UUID) -> ReportProfileRead:
        self.ctx.require("reporting.profile.manage")
        await self._company_or_404(company_id)
        row = await self.ctx.session.scalar(
            self.ctx.repo(ReportProfile)
            .scoped_select()
            .where(ReportProfile.company_id == company_id)
        )
        return await self._read(row, company_id)

    async def save(self, company_id: uuid.UUID, data: ReportProfileWrite) -> ReportProfileRead:
        self.ctx.require("reporting.profile.manage")
        await self._company_or_404(company_id)
        row = await self.ctx.session.scalar(
            self.ctx.repo(ReportProfile)
            .scoped_select()
            .where(ReportProfile.company_id == company_id)
        )
        values = data.model_dump(mode="json", exclude={"schedule", "recipients"})
        values["schedule"] = {
            key: value
            for key, value in data.schedule.model_dump(mode="json").items()
            if value is not None
        }
        values["recipients"] = [r.model_dump(mode="json") for r in data.recipients]
        if row is None:
            row = await self.ctx.repo(ReportProfile).create(company_id=company_id, **values)
            await self.activity.record_created(ReportProfile.__entity_type__, row.id)
        else:
            before = snapshot(row, _TRACKED_PROFILE_FIELDS)
            for field, value in values.items():
                setattr(row, field, value)
            await self.ctx.session.flush()
            await self.activity.record_update(
                ReportProfile.__entity_type__,
                row.id,
                before,
                snapshot(row, _TRACKED_PROFILE_FIELDS),
            )
        return await self._read(row, company_id)

    async def effective_schedule(self, profile: ReportProfile | None) -> dict[str, Any]:
        org = await ReportingSettingsService(self.ctx).get()
        own = (profile.schedule if profile else None) or {}
        return {**seeds.DEFAULT_SCHEDULE, **org.schedule, **own}

    async def effective_sections(self, row: ReportProfile | None) -> list[str]:
        """The client sections that will actually print, after registry → template → profile.

        Resolved here for the same reason :meth:`effective_schedule` is: a screen that draws a
        diff without its result makes the reader compute the answer, and three surfaces
        computing it separately is exactly how a picker comes to promise a section the run then
        drops. It reads the *client* template because that is the document this picker is about;
        the internal analysis follows its own template and is not a per-client decision.
        """
        template = await TemplateService(self.ctx).resolve(
            row.template_id if row else None, ReportAudience.CLIENT.value
        )
        specs = generate.enabled_sections(
            ReportAudience.CLIENT.value,
            template.layout if template else None,
            (row.sections if row else None),
        )
        return [spec.key for spec in specs]

    async def _read(
        self, row: ReportProfile | None, company_id: uuid.UUID
    ) -> ReportProfileRead:
        schedule = await self.effective_schedule(row)
        sections = await self.effective_sections(row)
        if row is None:
            return ReportProfileRead(
                id=uuid.UUID(int=0),
                company_id=company_id,
                effective_schedule=schedule,
                effective_sections=sections,
                next_run_on=await self.next_run(schedule),
            )
        payload = ReportProfileRead.model_validate(row)
        payload.effective_schedule = schedule
        payload.effective_sections = sections
        payload.next_run_on = (
            await self.next_run(schedule) if row.active else None
        )
        return payload

    async def next_run(self, schedule: dict[str, Any]) -> date | None:
        """When this profile next produces a report, in the org's own calendar."""
        if str(schedule.get("cadence") or "") == ReportCadence.OFF.value:
            return None
        today = await org_today(self.ctx.session, self.ctx.org.id)
        day = int(schedule.get("day_of_month") or 5)
        candidate = today.replace(day=min(day, 28))
        if candidate <= today:
            month = candidate.month + 1
            year = candidate.year + (month > 12)
            candidate = candidate.replace(year=year, month=(month - 1) % 12 + 1)
        if str(schedule.get("cadence")) == ReportCadence.QUARTERLY.value:
            while candidate.month not in (1, 4, 7, 10):
                month = candidate.month + 1
                year = candidate.year + (month > 12)
                candidate = candidate.replace(year=year, month=(month - 1) % 12 + 1)
        return candidate


# --------------------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------------------- #
class ReportService:
    """Reports: listed, generated, reviewed, published, sent.

    Every read goes through ``self.repo``, which for an **external (client) login** is the
    portal repository below. That is the invoicing pattern (#266) and it is the reason it is a
    *repository* rather than a filter in each method: overriding ``horizon_condition`` makes
    the predicate the one answer ``get_or_404``, ``scoped_select`` and ``scoped_count_select``
    all take, so the detail, the list, the list's total and the PDF download cannot disagree
    about what a client may see. Per-method filtering is exactly how #285 happened.
    """

    class _PortalReportRepository(TenantScopedRepository):
        def horizon_condition(self):  # noqa: ANN202 — mirrors the base signature
            clause = getattr(self.model, "__portal_horizon_clause__", None)
            if clause is None:
                return super().horizon_condition()
            return clause(self.company_scope)

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = (
            self._PortalReportRepository(
                ctx.session, ctx.org.id, Report, company_scope=ctx.company_scope
            )
            if ctx.is_portal
            else ctx.repo(Report)
        )
        self.activity = ActivityService(ctx)
        self.profiles = ProfileService(ctx)
        self.templates = TemplateService(ctx)
        self.tones = ToneService(ctx)

    # --- reads --------------------------------------------------------------------------- #
    def _visible_audiences(self) -> list[str]:
        """Which documents this caller may see at all.

        The internal analysis is its own permission, never implied by reading the client
        document: they are two documents about the same month and only one of them is written
        to be shown to the customer.
        """
        audiences = [ReportAudience.CLIENT.value]
        if not self.ctx.is_portal and self.ctx.can("reporting.internal.read"):
            audiences.append(ReportAudience.INTERNAL.value)
        return audiences

    async def list(
        self,
        *,
        company_id: uuid.UUID | None = None,
        audience: str | None = None,
        limit: int = 50,
        offset: int = 0,
        count: bool = True,
    ) -> ReportList:
        stmt = self.repo.scoped_select().where(Report.audience.in_(self._visible_audiences()))
        if company_id is not None:
            stmt = stmt.where(Report.company_id == company_id)
        if audience is not None:
            stmt = stmt.where(Report.audience == audience)
        rows = (
            await self.ctx.session.execute(
                stmt.order_by(Report.period_start.desc(), Report.company_name)
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        total: int | None = None
        if count:
            # From the *same* repository, so the total counts exactly the rows the list could
            # return — a hand-built count is how a scoped login gets "2" above one row (#285).
            count_stmt = self.repo.scoped_count_select().where(
                Report.audience.in_(self._visible_audiences())
            )
            if company_id is not None:
                count_stmt = count_stmt.where(Report.company_id == company_id)
            if audience is not None:
                count_stmt = count_stmt.where(Report.audience == audience)
            total = await self.ctx.session.scalar(count_stmt)
        return ReportList(items=[_row(row) for row in rows], total=total)

    async def _get(self, report_id: uuid.UUID) -> Report:
        report = await self.repo.get_or_404(report_id)
        if report.audience not in self._visible_audiences():
            # 404, never 403: revealing that an internal analysis exists for this month is
            # itself the leak (§15's 404-vs-403 rule).
            raise AppError("not_found", "errors.not_found", status_code=404)
        return report

    async def get(self, report_id: uuid.UUID) -> ReportDetail:
        return await self._read(await self._get(report_id))

    async def _read(self, report: Report) -> ReportDetail:
        payload = ReportDetail.model_validate(report)
        payload.warning_count = len(report.warnings or [])
        titles = {
            spec.key: translate(spec.title_key, report.locale)
            for spec in registry.report_sections_for(
                report.audience, app_settings.enabled_modules
            )
        }
        payload.sections = [
            {"key": key, "title": titles.get(key, key)}
            for key in (report.data_snapshot or {}).get("order") or []
        ]
        if self.ctx.is_portal:
            # A client reads the finished document, never the agency's working notes about
            # how it was made.
            payload.warnings = []
        return payload

    # --- generation ----------------------------------------------------------------------- #
    async def generate(self, data: ReportRunRequest) -> tuple[ReportDetail, bool]:
        """Create or find the run and hand it to a worker. Never generates in the request.

        Gathering touches several external APIs and a model call; doing it inline would hold a
        pooled DB connection for a minute and time out the browser besides (CLAUDE.md §11).
        """
        self.ctx.require("reporting.report.write")
        if data.audience == ReportAudience.INTERNAL:
            self.ctx.require("reporting.internal.read")
        company = await self.ctx.repo(Company).get_or_404(data.company_id)
        profile = await self.ctx.session.scalar(
            self.ctx.repo(ReportProfile)
            .scoped_select()
            .where(ReportProfile.company_id == data.company_id)
        )
        schedule = await self.profiles.effective_schedule(profile)
        locale = (profile.locale if profile else None) or (
            await ReportingSettingsService(self.ctx).get()
        ).default_locale
        period = (
            (data.period_start, data.period_end)
            if data.period_start and data.period_end
            else None
        )
        window = await generate.resolve_window(
            self.ctx, data.company_id, schedule=schedule, locale=locale, period=period
        )
        existing = await self.ctx.session.scalar(
            self.repo.scoped_select()
            .where(
                Report.company_id == data.company_id,
                Report.audience == data.audience.value,
                Report.period_start == window.start,
            )
            # The lock is what makes "is a run already in flight?" answerable. Two clicks a
            # moment apart both read a row that is not `generating` yet, both stamp it, and —
            # now that each attempt gets its own job id — both get a worker, so the same report
            # is generated twice at once. In READ COMMITTED the loser of this lock re-reads the
            # committed row, sees `generating`, and returns the run that is already going.
            .with_for_update()
        )
        if existing is not None and _run_in_flight(existing):
            return await self._read(existing), False
        if existing is not None and not data.refresh_data:
            if existing.status in (ReportStatus.READY.value, ReportStatus.SENT.value):
                # A report is a record. Handing back the one that exists is what stops a
                # re-run producing a second document a client could be sent twice.
                return await self._read(existing), False
            report = existing
        elif existing is not None:
            report = existing
        else:
            template = await self.templates.resolve(
                (profile.template_id if data.audience == ReportAudience.CLIENT else
                 profile.internal_template_id) if profile else None,
                data.audience.value,
            )
            report = await self.repo.create(
                company_id=data.company_id,
                company_name=generate.client_name(company.name, profile),
                template_id=template.id if template else None,
                audience=data.audience.value,
                status=ReportStatus.DRAFT.value,
                locale=locale,
                period_start=window.start,
                period_end=window.end,
                compare_start=window.compare_start,
                compare_end=window.compare_end,
                generated_by_user_id=self.ctx.user.id,
                generated_by_name=self.ctx.user.full_name or self.ctx.user.email,
            )
            report.title = generate.report_title(report)
            await self.activity.record_created(Report.__entity_type__, report.id)
        previous_status = report.status
        started_at = datetime.now(UTC)
        report.status = ReportStatus.GENERATING.value
        report.generation_started_at = started_at
        await self.ctx.session.flush()

        from app.modules.reporting.runner import run_job_id

        # `release_db` is exactly the right seam here, for both of the things it does.
        #
        # It **commits on entry**, which is what the worker needs: it opens its own session and
        # its own transaction, so a job handed over from inside this one races it — and for a
        # report created here the worker wins by reading a row that does not exist yet, then
        # returning silently and leaving the `generating` we are about to commit with nobody
        # working on it. And it is a call to Redis, so holding a pooled connection across it is
        # the drain `docs/PERFORMANCE.md` describes. Exit rebinds the RLS GUC, which a bare
        # commit would not: `set_config(..., true)` is transaction-local, so every statement
        # after it — including the failure write below — would match no rows at all.
        job = None
        try:
            async with self.ctx.release_db():
                job = await enqueue(
                    "reporting_run_report",
                    str(self.ctx.org.id),
                    str(report.id),
                    _job_id=run_job_id(report.id, started_at),
                )
        except Exception as exc:  # noqa: BLE001 — Redis being down is not this report's fault
            logger.warning("reporting: could not queue run for %s: %s", report.id, exc)
        if job is None:
            # Nothing is coming. Put the row back where it was — `failed` would hide a document
            # that is still perfectly good, and the only thing that actually happened is that
            # we could not schedule the work — say so, and commit *before* raising, or the
            # rollback the error triggers takes the correction with it.
            report.status = previous_status
            report.generation_started_at = None
            report.warnings = [
                *(report.warnings or []),
                {"code": "reporting.warning.not_queued", "detail": ""},
            ]
            await self.ctx.session.commit()
            # Rebind after the commit that just dropped the GUC, so whatever the error handling
            # does next runs tenant-bound rather than tenant-blind.
            await set_current_org(self.ctx.session, self.ctx.org.id)
            raise AppError("not_queued", "errors.reporting.not_queued", status_code=503)
        return await self._read(report), True

    async def generate_batch(self, data: ReportRunBatchRequest) -> ReportRunBatchResult:
        """Every **enrolled** client, one job each.

        One job *per client*, never a loop inside one transaction: the workflow this replaces
        ran thirty clients in a single execution, so one SE Ranking timeout took the whole
        month's reporting with it.

        Enrolment is a ``report_profiles`` row — the spreadsheet row, in other words. It stays
        explicit rather than "every client with a linked property", because generating for a
        client the agency does not report on spends their AI budget and their Google quota on
        a document nobody asked for.

        **It therefore has to say when nobody is enrolled.** Answering a bare ``0`` to somebody
        who has just linked GA4 to eight clients and pressed the button is technically correct
        and completely useless: it looks like a broken feature rather than a step not taken.
        So the result carries what it looked at (``enrolled``) and how many clients *could* be
        (``unconfigured``), and the screen turns that into a sentence with a way forward.
        """
        self.ctx.require("reporting.report.write")
        stmt = self.ctx.repo(ReportProfile).scoped_select().where(
            ReportProfile.active.is_(True)
        )
        if data.company_ids:
            stmt = stmt.where(ReportProfile.company_id.in_(data.company_ids))
        profiles = (await self.ctx.session.execute(stmt)).scalars().all()
        queued, skipped = 0, []
        for profile in profiles:
            try:
                _, started = await self.generate(
                    ReportRunRequest(
                        company_id=profile.company_id,
                        audience=data.audience,
                        period_start=data.period_start,
                        period_end=data.period_end,
                    )
                )
            except AppError as exc:
                skipped.append({"company_id": profile.company_id, "reason": exc.message_key})
                continue
            queued += 1 if started else 0
            if not started:
                skipped.append({"company_id": profile.company_id, "reason": "already_ready"})
        return ReportRunBatchResult(
            queued=queued,
            skipped=skipped,
            enrolled=len(profiles),
            unconfigured=await self._unconfigured() if not profiles else 0,
        )

    async def _unconfigured(self) -> int:
        """Clients with a live data source but no reporting profile — the ones to enrol next.

        Only asked when the batch found nobody, so the ordinary path pays nothing for it.
        Through the repository, so a member scoped to one company group is counted the same
        clients they can see (§15).
        """
        from app.modules.marketing.models import MarketingLink

        linked = (
            self.ctx.repo(MarketingLink)
            .scoped_select()
            .where(MarketingLink.active.is_(True))
            .with_only_columns(MarketingLink.company_id)
            .distinct()
        )
        company_ids = set((await self.ctx.session.execute(linked)).scalars().all())
        if not company_ids:
            return 0
        enrolled = (
            self.ctx.repo(ReportProfile)
            .scoped_select()
            .with_only_columns(ReportProfile.company_id)
        )
        return len(company_ids - set((await self.ctx.session.execute(enrolled)).scalars().all()))

    # --- review --------------------------------------------------------------------------- #
    async def update_narrative(
        self, report_id: uuid.UUID, data: ReportNarrativeUpdate
    ) -> ReportDetail:
        """A human's edit. Marked as edited, so a later regenerate leaves it alone."""
        self.ctx.require("reporting.report.write")
        report = await self._get(report_id)
        if report.status == ReportStatus.SENT.value:
            raise AppError("conflict", "errors.reporting.already_sent", status_code=409)
        narrative = dict(report.narrative or {})
        edited = set(report.edited_sections or [])
        for key, value in data.narrative.items():
            narrative[key] = value
            edited.add(key)
        report.narrative = narrative
        report.edited_sections = sorted(edited)
        report.pdf_file_id = None  # the PDF no longer matches the prose; re-render on demand
        await self.activity.record(Report.__entity_type__, report.id, "narrative_edited")
        await self.ctx.session.flush()
        return await self._read(report)

    async def rewrite_section(self, report_id: uuid.UUID, section_key: str) -> ReportDetail:
        """Rewrite one paragraph against that section's own data."""
        from app.core.ai.service import AIService
        from app.modules.reporting import narrative as narrative_mod

        self.ctx.require("reporting.report.write")
        report = await self._get(report_id)
        spec = registry.report_section(section_key, app_settings.enabled_modules)
        section = ((report.data_snapshot or {}).get("sections") or {}).get(section_key)
        if spec is None or section is None:
            raise AppError("validation", "errors.validation", status_code=422)
        profile = await self.ctx.session.scalar(
            self.ctx.repo(ReportProfile)
            .scoped_select()
            .where(ReportProfile.company_id == report.company_id)
        )
        tone = await self.tones.resolve(profile.tone_id if profile else None)
        text, warnings = await narrative_mod.rewrite_section(
            AIService(self.ctx),
            presented_section=present.section(
                section,
                locale=report.locale,
                title=translate(spec.title_key, report.locale),
                # `or {}`, never a default: `compare` is stored as an explicit null when the
                # window has nothing to compare against, so `.get("compare", {})` returns None
                # and the next `.get` raises on exactly the reports that need it least.
                compare_label=((report.data_snapshot or {}).get("compare") or {}).get("label"),
            ),
            profile=generate.profile_facts(profile),
            tone=generate.tone_payload(tone),
            section_key=section_key,
            brief=translate(spec.brief_key, report.locale) if spec.brief_key else "",
            locale=report.locale,
            brand=self.ctx.org.name,
            period_label=(report.data_snapshot or {}).get("period", {}).get("label", ""),
            internal=report.audience == ReportAudience.INTERNAL.value,
        )
        if text:
            report.narrative = {**(report.narrative or {}), section_key: text}
            report.edited_sections = [
                key for key in (report.edited_sections or []) if key != section_key
            ]
            report.pdf_file_id = None
        report.warnings = _merge_warnings(report.warnings, warnings)
        await self.ctx.session.flush()
        return await self._read(report)

    # --- delivery -------------------------------------------------------------------------- #
    async def publish(self, report_id: uuid.UUID, published: bool) -> ReportDetail:
        """Make the document visible in the client portal — or take it back down."""
        self.ctx.require("reporting.report.send")
        report = await self._get(report_id)
        if report.audience == ReportAudience.INTERNAL.value:
            # Not a permission question: there is no state in which the internal analysis is
            # a client-facing document, so the portal clause would hide it anyway. Refusing
            # here says why, instead of silently succeeding and showing nobody anything.
            raise AppError(
                "validation", "errors.reporting.internal_not_publishable", status_code=422
            )
        before = snapshot(report, _TRACKED_REPORT_FIELDS)
        report.published_at = datetime.now(UTC) if published else None
        if published and report.status == ReportStatus.DRAFT.value:
            raise AppError("conflict", "errors.reporting.not_ready", status_code=409)
        await self.activity.record_update(
            Report.__entity_type__, report.id, before,
            snapshot(report, _TRACKED_REPORT_FIELDS),
        )
        await self.ctx.session.flush()
        if published:
            await emit("report.published", self.ctx, {"report_id": str(report.id)})
        return await self._read(report)

    async def send(self, report_id: uuid.UUID, data: ReportSendRequest) -> ReportDetail:
        """Mail the report to its recipients, with the PDF attached."""
        from app.modules.reporting.delivery import send_report

        self.ctx.require("reporting.report.send")
        report = await self._get(report_id)
        if report.audience == ReportAudience.INTERNAL.value:
            raise AppError(
                "validation", "errors.reporting.internal_not_sendable", status_code=422
            )
        if report.status not in (ReportStatus.READY.value, ReportStatus.SENT.value):
            raise AppError("conflict", "errors.reporting.not_ready", status_code=409)
        recipients = (
            [r.model_dump(mode="json") for r in data.recipients]
            if data.recipients is not None
            else None
        )
        await send_report(self.ctx, report, recipients=recipients)
        if data.publish and report.published_at is None:
            report.published_at = datetime.now(UTC)
        await self.ctx.session.flush()
        await emit("report.sent", self.ctx, {"report_id": str(report.id)})
        return await self._read(report)

    async def delete(self, report_id: uuid.UUID) -> None:
        self.ctx.require("reporting.report.write")
        report = await self._get(report_id)
        if report.sent_at is not None:
            # A document a client received is a historical fact. Deleting the agency's copy
            # of what it sent is not a tidy-up, it is losing the record.
            raise AppError("conflict", "errors.reporting.already_sent", status_code=409)
        await self.activity.record(
            Report.__entity_type__, report.id, "deleted", {"title": report.title}
        )
        await self.repo.delete(report)


def _row(report: Report) -> ReportRow:
    payload = ReportRow.model_validate(report)
    payload.warning_count = len(report.warnings or [])
    return payload


def _merge_warnings(
    existing: list[dict] | None, new: list[dict[str, str]]
) -> list[dict]:
    out = list(existing or [])
    for warning in new:
        if warning not in out:
            out.append(warning)
    return out


async def latest_company_narrative(ctx: RequestContext, company_id: uuid.UUID):  # noqa: ANN201
    """The narrative seam's provider (``app/core/narratives.py``), registered at import.

    Goes through ``ReportService`` on purpose: its repository is the portal-aware one, so a
    client login borrows prose only from their own *published, client-facing* reports, and
    staff without ``reporting.report.read`` borrow none at all. Reaching into ``reports``
    directly here would be a second copy of that rule.

    One indexed read — the panel that calls it is drawn on every company page.
    """
    from app.core.narratives import CompanyNarrative

    if not ctx.can("reporting.report.read"):
        return None
    service = ReportService(ctx)
    row = await ctx.session.scalar(
        service.repo.scoped_select()
        .where(
            Report.company_id == company_id,
            Report.audience == ReportAudience.CLIENT.value,
            Report.published_at.is_not(None),
        )
        .order_by(Report.period_start.desc())
        .limit(1)
    )
    if row is None or not row.narrative:
        return None
    narrative = dict(row.narrative)
    return CompanyNarrative(
        report_id=row.id,
        period_label=(row.data_snapshot or {}).get("period", {}).get("label", ""),
        summary=str(narrative.pop("summary", "") or ""),
        sections={key: str(value) for key, value in narrative.items() if value},
    )
