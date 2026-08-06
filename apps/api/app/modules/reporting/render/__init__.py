"""Report rendering: one HTML artefact, previewed and printed (issue #300).

``render_report_html`` is what the preview endpoint serves *and* what the PDF is printed from,
so a preview and its download cannot drift — the property ``docs/INVOICING.md`` earned the hard
way and the reason its renderer became core.

Everything white-label is resolved here and passed *in* (Golden Rule 4): the agency's logo and
the client's own come out of storage as bytes, never as an outbound fetch of a URL somebody
typed into a settings field.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.branding import load_org_image
from app.core.tenancy import RequestContext
from app.i18n import translate
from app.modules.reporting.models import Report, ReportAudience, ReportTemplate
from app.modules.reporting.render.context import build_context
from app.modules.reporting.render.engine import ENGINE
from app.registry import registry

logger = logging.getLogger("schakl.reporting")

__all__ = ["ENGINE", "build_context", "render_report_html"]


def _data_uri(payload: bytes | None, content_type: str | None) -> str | None:
    import base64

    if not payload:
        return None
    kind = (content_type or "image/png").split(";")[0].strip() or "image/png"
    return f"data:{kind};base64,{base64.b64encode(payload).decode('ascii')}"


async def _org_logo(ctx: RequestContext) -> str | None:
    from sqlalchemy import select

    from app.core.branding import load_brand_logo
    from app.core.models import OrgSettings

    row = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == ctx.org.id)
    )
    if row is None:
        return None
    payload, content_type = await load_brand_logo(ctx, row)
    return _data_uri(payload, content_type)


async def _client_logo(ctx: RequestContext, company_id: Any) -> str | None:
    """The client's own logo, from the file row ``companies.logo_file_id`` points at.

    The workflow this replaces read a *URL* out of a spreadsheet column and put it in an
    ``<img src>``. That cannot work here and should not: the renderer refuses every scheme but
    ``data:``, precisely so a document can never make the server fetch something on the say-so
    of a field somebody typed into.
    """
    from sqlalchemy import select

    from app.modules.companies.models import Company

    logo_id = await ctx.session.scalar(
        select(Company.logo_file_id).where(
            Company.org_id == ctx.org.id, Company.id == company_id
        )
    )
    if logo_id is None:
        return None
    payload, content_type = await load_org_image(ctx, logo_id, what="client logo")
    return _data_uri(payload, content_type)


async def render_report_html(
    ctx: RequestContext, report: Report, template: ReportTemplate | None
) -> str:
    """The report as a standalone HTML page — previewed as-is, printed as-is."""
    from sqlalchemy import select

    from app.core.models import OrgSettings
    from app.modules.reporting.models import ReportingSettings

    org_settings = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == ctx.org.id)
    )
    reporting_settings = await ctx.session.scalar(
        select(ReportingSettings).where(ReportingSettings.org_id == ctx.org.id)
    )
    cover_uri: str | None = None
    if template is not None and template.cover_image_file_id is not None:
        payload, content_type = await load_org_image(
            ctx, template.cover_image_file_id, what="report cover"
        )
        cover_uri = _data_uri(payload, content_type)

    internal = report.audience == ReportAudience.INTERNAL.value
    from app.config import settings as app_settings

    titles = {
        spec.key: translate(spec.title_key, report.locale)
        for spec in registry.report_sections_for(
            report.audience, app_settings.enabled_modules
        )
    }
    context = build_context(
        report=report,
        snapshot=report.data_snapshot or {},
        narrative=report.narrative or {},
        section_titles=titles,
        brand_name=(org_settings.brand_name if org_settings else None) or ctx.org.name,
        logo_uri=await _org_logo(ctx),
        cover_uri=cover_uri,
        # An internal document carries no client branding: it is our working paper about them,
        # and dressing it in their logo invites it into the wrong folder.
        client_logo_uri=None if internal else await _client_logo(ctx, report.company_id),
        accent=(template.accent_color if template else None)
        or (org_settings.primary_color if org_settings else None),
        intro_text=(template.intro_text if template else None),
        footer_text=(reporting_settings.footer_text if reporting_settings else None),
        locale=report.locale,
        internal=internal,
    )
    config: dict[str, Any] = {
        "design": (template.design if template else ENGINE.default_design),
        "html": (template.custom_html if template else None),
        "css": (template.custom_css if template else None),
    }
    return ENGINE.render_html(context, config)
