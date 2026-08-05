"""Sending a finished report to the client (issue #300).

Three things this does that the workflow it replaces did not:

* **The mail is the tenant's to write** — it resolves the ``reporting.report`` template if the
  agency wrote one, and falls back to the catalog text if not.
* **It is sent by whoever owns the client.** ``companies.responsible_user_id`` is the account
  manager, so their name is on the mail and replies come back to them — the same intent as the
  spreadsheet's ``Verantwoordelijke`` column, resolved from the CRM rather than parsed out of
  a string like ``"Sanne (sanne@bureau.nl)"``.
* **A failed send is a failed send.** It raises, the report keeps its ``ready`` status, and the
  reviewer can try again. Recording ``sent_at`` for a mail that never left is how a client
  ends up never receiving a report that everybody believes they got.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.email.branding import load_brand
from app.core.email.senders import EmailAttachment
from app.core.email.templates import build_email_content, resolve_template
from app.core.storage.models import StoredFile
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.reporting import emails
from app.modules.reporting.models import Report, ReportProfile, ReportStatus

logger = logging.getLogger("schakl.reporting")


async def resolve_recipients(
    ctx: RequestContext, report: Report, override: list[dict] | None
) -> list[dict[str, str]]:
    """Who receives this report: the explicit list, else the profile's.

    Stored flat on the profile rather than joined live from the client's contacts, so a
    contact who leaves does not silently drop out of next month's distribution — the list
    still names them and somebody has to notice.
    """
    if override is not None:
        return [r for r in override if (r.get("email") or "").strip()]
    profile = await ctx.session.scalar(
        ctx.repo(ReportProfile)
        .scoped_select()
        .where(ReportProfile.company_id == report.company_id)
    )
    return [
        r
        for r in ((profile.recipients if profile else None) or [])
        if isinstance(r, dict) and (r.get("email") or "").strip()
    ]


async def _sender_name(ctx: RequestContext, company_id: uuid.UUID) -> str:
    """The account manager's name, else the org's. Never a hardcoded team address."""
    from app.core.auth.models import User
    from app.modules.companies.models import Company

    user_id = await ctx.session.scalar(
        select(Company.responsible_user_id).where(
            Company.org_id == ctx.org.id, Company.id == company_id
        )
    )
    if user_id is None:
        return ctx.org.name
    user = await ctx.session.get(User, user_id)
    return (user.full_name or user.email) if user is not None else ctx.org.name


async def send_report(
    ctx: RequestContext, report: Report, *, recipients: list[dict] | None = None
) -> None:
    """Mail the report with its PDF attached, and record what was sent to whom."""
    from app.modules.invoicing import emails as document_emails  # deliver(): shared send seam

    if report.status not in (ReportStatus.READY.value, ReportStatus.SENT.value):
        raise AppError("conflict", "errors.reporting.not_ready", status_code=409)
    to = await resolve_recipients(ctx, report, recipients)
    if not to:
        raise AppError("validation", "errors.reporting.no_recipient", status_code=400)
    if report.pdf_file_id is None:
        raise AppError("conflict", "errors.reporting.no_document", status_code=409)
    stored = await ctx.session.get(StoredFile, report.pdf_file_id)
    if stored is None:
        raise AppError("conflict", "errors.reporting.no_document", status_code=409)

    brand = await load_brand(ctx.session, ctx.org)
    sender_name = await _sender_name(ctx, report.company_id)
    values = emails.report_values(
        report, brand.brand_name, sender_name, f"{brand.base_url}/reports/{report.id}"
    )
    template = await resolve_template(
        ctx.session, ctx.org.id, emails.REPORT_KIND, report.locale
    )
    content = await _stored_bytes(stored)

    delivered: list[dict[str, str]] = []
    for recipient in to:
        subject, text, html = build_email_content(
            emails.REPORT_KIND,
            report.locale,
            template.subject if template else None,
            template.body_html if template else None,
            {**values, "contact": str(recipient.get("name") or "")},
        )
        message = _message(subject, text, html, str(recipient["email"]))
        message.attachments.append(
            EmailAttachment(
                filename=stored.filename, content=content, mimetype="application/pdf"
            )
        )
        await document_emails.deliver(ctx, message, brand=brand)
        delivered.append(
            {"email": str(recipient["email"]), "name": str(recipient.get("name") or "")}
        )

    report.sent_at = datetime.now(UTC)
    report.sent_to = delivered
    report.status = ReportStatus.SENT.value


def _message(subject: str, text: str, html: str | None, to: str) -> Any:
    from app.core.email.senders import OutgoingEmail

    return OutgoingEmail(to=to, subject=subject, text=text, html=html)


async def _stored_bytes(stored: StoredFile) -> bytes:
    """The PDF's bytes. Blocking IO, so off the event loop — the storage routes' own rule."""
    import asyncio

    from app.core.storage.backend import get_storage

    def _read() -> bytes:
        with get_storage().open(stored.storage_key) as handle:
            return handle.read()

    return await asyncio.to_thread(_read)
