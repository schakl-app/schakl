"""Minutes rendering: one HTML artefact, previewed and printed.

``render_meeting_html`` is what the preview serves *and* what the PDF is printed from, so the
two cannot drift — the property invoicing and reporting already hold. Everything white-label is
resolved here and passed *in* (Golden Rule 4): the agency's logo, the client's own, the cover
and every participant's picture come out of storage as bytes, never as a fetch of a URL.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.avatars import load_user_avatar
from app.core.branding import load_brand_logo, load_org_image
from app.core.directory import labels_for
from app.core.timezone import org_zoneinfo
from app.modules.meetings.models import Meeting
from app.modules.meetings.render.context import _person, build_context
from app.modules.meetings.render.engine import ENGINE
from app.modules.meetings.schemas import MinutesDraft
from app.modules.meetings.settings import MeetingSettingsRead, load_settings

logger = logging.getLogger("schakl.meetings")

__all__ = ["ENGINE", "build_context", "render_meeting_html", "render_meeting_pdf"]


def _data_uri(payload: bytes | None, content_type: str | None) -> str | None:
    if not payload:
        return None
    kind = (content_type or "image/png").split(";")[0].strip() or "image/png"
    return f"data:{kind};base64,{base64.b64encode(payload).decode('ascii')}"


async def _org_branding(ctx: Any) -> tuple[str, str | None, str | None]:
    """``(brand name, logo data URI, brand colour)``."""
    from app.core.models import OrgSettings

    row = await ctx.session.scalar(select(OrgSettings).where(OrgSettings.org_id == ctx.org.id))
    if row is None:
        return ctx.org.name, None, None
    payload, content_type = await load_brand_logo(ctx, row)
    return (row.brand_name or ctx.org.name), _data_uri(payload, content_type), row.primary_color


async def _client_logo(ctx: Any, company_id: uuid.UUID | None) -> str | None:
    if company_id is None:
        return None
    from app.modules.companies.models import Company

    logo_id = await ctx.session.scalar(
        select(Company.logo_file_id).where(Company.org_id == ctx.org.id, Company.id == company_id)
    )
    if logo_id is None:
        return None
    payload, content_type = await load_org_image(ctx, logo_id, what="client logo")
    return _data_uri(payload, content_type)


async def _people(
    ctx: Any, row: Meeting, draft: MinutesDraft | None, *, locale: str, avatars: bool
) -> dict[str, dict[str, Any]]:
    """Every person the document names — the roster and every action-item owner — as a
    printable person keyed ``u:<id>`` / ``c:<id>``, with a picture where one is stored.

    A colleague's picture is their own upload or, failing that, their identity provider's
    picture, read as bytes by ``core/avatars.py`` (a closed list of IdP hosts, never an
    arbitrary URL). A contact has no picture here and keeps initials.
    """
    from app.core.auth.models import User
    from app.core.members import staff_select
    from app.modules.meetings.service import participants_of

    participants = participants_of(row)
    user_ids: set[uuid.UUID] = {p.user_id for p in participants if p.user_id is not None}
    contact_ids: set[uuid.UUID] = {p.contact_id for p in participants if p.contact_id is not None}
    for item in draft.action_items if draft else []:
        if item.assignee_user_id is not None:
            user_ids.add(item.assignee_user_id)
        if item.owner_contact_id is not None:
            contact_ids.add(item.owner_contact_id)

    people: dict[str, dict[str, Any]] = {}
    names_on_roster = {f"u:{p.user_id}": p.name for p in participants if p.user_id is not None} | {
        f"c:{p.contact_id}": p.name for p in participants if p.contact_id is not None
    }

    if user_ids:
        rows = (
            (await ctx.session.execute(staff_select(ctx.org.id).where(User.id.in_(list(user_ids)))))
            .scalars()
            .all()
        )
        # Every picture at once: an IdP picture is a fetch, and six of them in a row would be
        # six round trips in front of a preview (``core/avatars.py`` caches them per process).
        pictures = (
            await asyncio.gather(*(load_user_avatar(ctx, user) for user in rows))
            if avatars
            else [(None, None)] * len(rows)
        )
        for user, (payload, content_type) in zip(rows, pictures, strict=True):
            key = f"u:{user.id}"
            people[key] = _person(
                names_on_roster.get(key) or user.full_name or user.email,
                side="agency",
                avatar=_data_uri(payload, content_type),
                locale=locale,
            )
    if contact_ids:
        # Through the directory seam (§15): only a contact this caller may see is named.
        for cid, label in (await labels_for(ctx, "contact", contact_ids)).items():
            key = f"c:{cid}"
            people[key] = _person(
                names_on_roster.get(key) or label, side="client", avatar=None, locale=locale
            )
    # A roster row whose id resolved to nothing (a departed colleague, a contact outside the
    # horizon) still prints under the name the roster stored.
    for key, name in names_on_roster.items():
        people.setdefault(
            key,
            _person(
                name,
                side="agency" if key.startswith("u:") else "client",
                avatar=None,
                locale=locale,
            ),
        )
    return people


async def _body_images(ctx: Any, row: Meeting, draft: MinutesDraft | None) -> dict[str, str]:
    """Every image the minutes embed (``![alt](file:<id>)``), as ``data:`` URIs.

    Only a file stored *against this meeting* is read: the marker carries an id somebody
    typed, and a document must never print another record's picture because its id was
    pasted into the notes. Anything else resolves to nothing and simply is not drawn.
    """
    from app.core.richtext import extract_file_image_ids
    from app.core.storage.models import StoredFile

    if draft is None:
        return {}
    texts = [draft.summary, *(t.text for t in draft.topics), *(d.text for d in draft.decisions)]
    texts += [item.description or "" for item in draft.action_items]
    texts += list(draft.open_questions)
    wanted: list[uuid.UUID] = []
    for text in texts:
        for file_id in extract_file_image_ids(text):
            if file_id not in wanted:
                wanted.append(file_id)
    if not wanted:
        return {}
    owned = (
        await ctx.session.scalars(
            select(StoredFile.id).where(
                StoredFile.org_id == ctx.org.id,
                StoredFile.entity_type == "meeting",
                StoredFile.entity_id == row.id,
                StoredFile.id.in_(wanted[:50]),
            )
        )
    ).all()
    images: dict[str, str] = {}
    for file_id in owned:
        payload, content_type = await load_org_image(ctx, file_id, what="minutes image")
        uri = _data_uri(payload, content_type)
        if uri:
            images[str(file_id).lower()] = uri
    return images


async def render_meeting_html(
    ctx: Any,
    row: Meeting,
    *,
    settings: MeetingSettingsRead | None = None,
    sections: list[str] | None = None,
) -> str:
    """The minutes as a standalone HTML page — previewed as-is, printed as-is."""
    from app.modules.meetings.service import org_locale, participants_of, speaker_names

    current = settings or await load_settings(ctx.session, ctx.org.id)
    locale = await org_locale(ctx)
    zone = await org_zoneinfo(ctx.session, ctx.org.id)
    draft: MinutesDraft | None = None
    if row.minutes:
        try:
            draft = MinutesDraft.model_validate(row.minutes)
        except ValueError:
            draft = None
    participants = participants_of(row)
    brand_name, logo_uri, brand_color = await _org_branding(ctx)
    cover_uri = None
    if current.document_cover_file_id is not None:
        payload, content_type = await load_org_image(
            ctx, current.document_cover_file_id, what="minutes cover"
        )
        cover_uri = _data_uri(payload, content_type)
    companies = await labels_for(ctx, "company", [row.company_id]) if row.company_id else {}
    projects = await labels_for(ctx, "project", [row.project_id]) if row.project_id else {}
    transcript = row.transcript or {}
    context = build_context(
        title=row.title,
        kind=row.kind,
        status=row.status,
        occurred_at=row.occurred_at,
        duration_seconds=row.duration_seconds,
        owner_name=row.owner_name,
        client=companies.get(row.company_id) if row.company_id else None,
        project=projects.get(row.project_id) if row.project_id else None,
        participants=participants,
        people=await _people(ctx, row, draft, locale=locale, avatars=current.document_avatars),
        minutes=draft,
        images=await _body_images(ctx, row, draft),
        segments=[s for s in (transcript.get("segments") or []) if isinstance(s, dict)],
        transcript_text=row.transcript_text,
        transcript_parts=int(transcript.get("parts") or 0),
        speakers=speaker_names(participants),
        sections=list(sections if sections is not None else current.document_sections),
        brand_name=brand_name,
        logo_uri=logo_uri,
        cover_uri=cover_uri,
        client_logo_uri=await _client_logo(ctx, row.company_id),
        accent=current.document_accent_color,
        brand_color=brand_color,
        footer_text=current.document_footer_text,
        show_avatars=current.document_avatars,
        locale=locale,
        zone=zone,
        generated_at=datetime.now(UTC),
    )
    config: dict[str, Any] = {
        "design": current.document_design,
        "html": current.document_custom_html,
        "css": current.document_custom_css,
    }
    return ENGINE.render_html(context, config)


async def render_meeting_pdf(
    ctx: Any, row: Meeting, *, sections: list[str] | None = None
) -> tuple[bytes, str]:
    """The same document, printed. Returns the bytes and a filename."""
    from app.modules.meetings.service import org_locale
    from app.modules.meetings.transcript import transcript_filename

    html = await render_meeting_html(ctx, row, sections=sections)
    locale = await org_locale(ctx)
    content = await asyncio.to_thread(lambda: ENGINE.html_to_pdf(html, locale=locale))
    return content, transcript_filename(row.title, "pdf")
