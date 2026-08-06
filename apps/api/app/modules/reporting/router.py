"""REST endpoints for reporting under ``/api/v1/reporting`` (issue #300).

Every route declares a permission (deny-by-default, §15). The two that look like they could
share one deliberately do not: ``reporting.report.write`` drafts, ``reporting.report.send``
puts the document in a client's inbox.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.reporting.models import ReportAudience
from app.modules.reporting.render.engine import ENGINE
from app.modules.reporting.schemas import (
    ReportActionResult,
    ReportDetail,
    ReportingSettingsRead,
    ReportingSettingsWrite,
    ReportList,
    ReportNarrativeUpdate,
    ReportProfileRead,
    ReportProfileWrite,
    ReportRewriteRequest,
    ReportRunBatchRequest,
    ReportRunBatchResult,
    ReportRunRequest,
    ReportSendRequest,
    ReportTemplateRead,
    ReportTemplateSource,
    ReportTemplateWrite,
    ReportToneRead,
    ReportToneWrite,
    SectionCatalogEntry,
)
from app.modules.reporting.service import (
    ProfileService,
    ReportingSettingsService,
    ReportService,
    TemplateService,
    ToneService,
)

router = APIRouter(prefix="/reporting", tags=["reporting"])


# --- tones ------------------------------------------------------------------------------- #
@router.get(
    "/tones",
    response_model=list[ReportToneRead],
    dependencies=[require_permission("reporting.profile.manage")],
)
async def list_tones(ctx: RequestContext = Depends(require_context)) -> list[ReportToneRead]:
    """Readable by whoever assigns one to a client, editable only by an admin (the service
    re-checks) — a manager must be able to see what voice they are picking."""
    return await ToneService(ctx).list()


@router.post(
    "/tones",
    response_model=ReportToneRead,
    status_code=201,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def create_tone(
    payload: ReportToneWrite, ctx: RequestContext = Depends(require_context)
) -> ReportToneRead:
    return await ToneService(ctx).create(payload)


@router.put(
    "/tones/{tone_id}",
    response_model=ReportToneRead,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def update_tone(
    tone_id: uuid.UUID, payload: ReportToneWrite, ctx: RequestContext = Depends(require_context)
) -> ReportToneRead:
    return await ToneService(ctx).update(tone_id, payload)


@router.delete(
    "/tones/{tone_id}",
    status_code=204,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def delete_tone(
    tone_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await ToneService(ctx).delete(tone_id)


# --- templates --------------------------------------------------------------------------- #
@router.get(
    "/templates",
    response_model=list[ReportTemplateRead],
    dependencies=[require_permission("reporting.profile.manage")],
)
async def list_templates(
    audience: ReportAudience | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[ReportTemplateRead]:
    return await TemplateService(ctx).list(audience.value if audience else None)


@router.get(
    "/templates/sections",
    response_model=list[SectionCatalogEntry],
    dependencies=[require_permission("reporting.settings.manage")],
)
async def section_catalog(
    ctx: RequestContext = Depends(require_context),
) -> list[SectionCatalogEntry]:
    """What a template may order or switch off — the registry, made visible (§15's
    "registry, not free text", applied to design)."""
    return TemplateService(ctx).catalog()


@router.get(
    "/templates/designs/{design}/source",
    response_model=ReportTemplateSource,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def template_source(design: str) -> ReportTemplateSource:
    """A shipped design's own HTML and CSS, to start a custom report template from.

    The counterpart invoicing has had since its designer shipped, and the piece whose absence
    made ``design: "custom"`` a field nobody could reach: writing a report template from a
    blank page means knowing the whole render context by heart, while branching from the
    design you already like means changing the two things you want changed. These are the
    *same* files ``standard`` renders from, so what an author gets is what they saw.

    Declared on ``reporting.settings.manage`` because handing back the body a tenant is about
    to author against is part of the same act as saving it.
    """
    html, css = ENGINE.builtin_source(design)
    return ReportTemplateSource(html=html, css=css)


@router.post(
    "/templates",
    response_model=ReportTemplateRead,
    status_code=201,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def create_template(
    payload: ReportTemplateWrite, ctx: RequestContext = Depends(require_context)
) -> ReportTemplateRead:
    return await TemplateService(ctx).create(payload)


@router.put(
    "/templates/{template_id}",
    response_model=ReportTemplateRead,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def update_template(
    template_id: uuid.UUID,
    payload: ReportTemplateWrite,
    ctx: RequestContext = Depends(require_context),
) -> ReportTemplateRead:
    return await TemplateService(ctx).update(template_id, payload)


@router.delete(
    "/templates/{template_id}",
    status_code=204,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def delete_template(
    template_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TemplateService(ctx).delete(template_id)


# --- org settings ------------------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=ReportingSettingsRead,
    dependencies=[require_permission("reporting.profile.manage")],
)
async def get_settings(
    ctx: RequestContext = Depends(require_context),
) -> ReportingSettingsRead:
    return await ReportingSettingsService(ctx).get()


@router.put(
    "/settings",
    response_model=ReportingSettingsRead,
    dependencies=[require_permission("reporting.settings.manage")],
)
async def save_settings(
    payload: ReportingSettingsWrite, ctx: RequestContext = Depends(require_context)
) -> ReportingSettingsRead:
    return await ReportingSettingsService(ctx).save(payload)


# --- per-client profile -------------------------------------------------------------------- #
@router.get(
    "/companies/{company_id}/profile",
    response_model=ReportProfileRead,
    dependencies=[require_permission("reporting.profile.manage")],
)
async def get_profile(
    company_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> ReportProfileRead:
    """The client's reporting setup. A company that has never had one answers with the
    inherited defaults rather than a 404 — the form is the same either way."""
    return await ProfileService(ctx).get(company_id)


@router.put(
    "/companies/{company_id}/profile",
    response_model=ReportProfileRead,
    dependencies=[require_permission("reporting.profile.manage")],
)
async def save_profile(
    company_id: uuid.UUID,
    payload: ReportProfileWrite,
    ctx: RequestContext = Depends(require_context),
) -> ReportProfileRead:
    return await ProfileService(ctx).save(company_id, payload)


# --- reports --------------------------------------------------------------------------- #
@router.get(
    "/reports",
    response_model=ReportList,
    dependencies=[require_permission("reporting.report.read")],
)
async def list_reports(
    company_id: uuid.UUID | None = Query(None),
    audience: ReportAudience | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    count: bool = Query(True),
    ctx: RequestContext = Depends(require_context),
) -> ReportList:
    return await ReportService(ctx).list(
        company_id=company_id,
        audience=audience.value if audience else None,
        limit=limit,
        offset=offset,
        count=count,
    )


@router.get(
    "/reports/{report_id}",
    response_model=ReportDetail,
    dependencies=[require_permission("reporting.report.read")],
)
async def get_report(
    report_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> ReportDetail:
    return await ReportService(ctx).get(report_id)


@router.get(
    "/reports/{report_id}/preview",
    dependencies=[require_permission("reporting.report.read")],
)
async def preview_report(
    report_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> Response:
    """The document as HTML — the same artefact the PDF prints, so the two cannot drift."""
    from app.modules.reporting.render import render_report_html
    from app.modules.reporting.service import ReportService as _Service

    service = _Service(ctx)
    report = await service._get(report_id)  # noqa: SLF001 — the portal-aware load
    template = await service.templates.resolve(report.template_id, report.audience)
    html = await render_report_html(ctx, report, template)
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.post(
    "/reports/generate",
    response_model=ReportActionResult,
    dependencies=[require_permission("reporting.report.write")],
)
async def generate_report(
    payload: ReportRunRequest, ctx: RequestContext = Depends(require_context)
) -> ReportActionResult:
    """Queue a run. Never generates inline — it calls several APIs and a model."""
    report, queued = await ReportService(ctx).generate(payload)
    return ReportActionResult(report=report, queued=queued)


@router.post(
    "/reports/generate-batch",
    response_model=ReportRunBatchResult,
    dependencies=[require_permission("reporting.report.write")],
)
async def generate_batch(
    payload: ReportRunBatchRequest, ctx: RequestContext = Depends(require_context)
) -> ReportRunBatchResult:
    return await ReportService(ctx).generate_batch(payload)


@router.put(
    "/reports/{report_id}/narrative",
    response_model=ReportDetail,
    dependencies=[require_permission("reporting.report.write")],
)
async def edit_narrative(
    report_id: uuid.UUID,
    payload: ReportNarrativeUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ReportDetail:
    """Hand-edit the prose before it goes out — the point of review-before-send."""
    return await ReportService(ctx).update_narrative(report_id, payload)


@router.post(
    "/reports/{report_id}/rewrite",
    response_model=ReportDetail,
    dependencies=[require_permission("reporting.report.write")],
)
async def rewrite_section(
    report_id: uuid.UUID,
    payload: ReportRewriteRequest,
    ctx: RequestContext = Depends(require_context),
) -> ReportDetail:
    return await ReportService(ctx).rewrite_section(report_id, payload.section_key)


@router.post(
    "/reports/{report_id}/publish",
    response_model=ReportDetail,
    dependencies=[require_permission("reporting.report.send")],
)
async def publish_report(
    report_id: uuid.UUID,
    published: bool = Query(True),
    ctx: RequestContext = Depends(require_context),
) -> ReportDetail:
    return await ReportService(ctx).publish(report_id, published)


@router.post(
    "/reports/{report_id}/send",
    response_model=ReportDetail,
    dependencies=[require_permission("reporting.report.send")],
)
async def send_report(
    report_id: uuid.UUID,
    payload: ReportSendRequest,
    ctx: RequestContext = Depends(require_context),
) -> ReportDetail:
    return await ReportService(ctx).send(report_id, payload)


@router.delete(
    "/reports/{report_id}",
    status_code=204,
    dependencies=[require_permission("reporting.report.write")],
)
async def delete_report(
    report_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await ReportService(ctx).delete(report_id)


@router.get(
    "/reports/{report_id}/pdf",
    dependencies=[require_permission("reporting.report.read")],
)
async def report_pdf(
    report_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> Response:
    """The stored document. Loaded through the portal-aware repository, so a client can
    download exactly the reports they can open — and never the internal analysis."""
    from app.core.storage.models import StoredFile
    from app.modules.reporting.delivery import _stored_bytes
    from app.modules.reporting.service import ReportService as _Service

    report = await _Service(ctx)._get(report_id)  # noqa: SLF001
    if report.pdf_file_id is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    stored = await ctx.session.get(StoredFile, report.pdf_file_id)
    if stored is None or stored.org_id != ctx.org.id:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return Response(
        content=await _stored_bytes(stored),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{stored.filename}"'},
    )
