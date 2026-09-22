"""The org's meetings settings: the consent statement, the minutes document, the house rules.

One row per org, absent = the defaults (the tasks settings' shape). Three readers that are not
the settings screen: the recorder's policy (``consent_required``, the retention), the worker
(``ai_instructions`` into the minutes prompt) and the renderer (everything ``document_*``). All
three read through :func:`load_settings` so "no row yet" has exactly one meaning.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import RequestContext
from app.modules.meetings.models import (
    DEFAULT_DOCUMENT_SECTIONS,
    DOCUMENT_SECTIONS,
    MeetingSettings,
)
from app.modules.meetings.schemas import (
    MeetingDesignSource,
    MeetingPolicy,
    MeetingSectionCatalogEntry,
    MeetingSettingsPreviewRequest,
    MeetingSettingsRead,
    MeetingSettingsUpdate,
)

#: How long a confirmed meeting keeps its audio. Stated on the recording screen and in the
#: settings; the nightly sweep (``jobs.AUDIO_RETENTION_DAYS``) reads the same number.
AUDIO_RETENTION_DAYS = 30


async def load_settings(session: AsyncSession, org_id: uuid.UUID) -> MeetingSettingsRead:
    """The org's settings as the read shape, defaults where no row exists."""
    row = await session.scalar(select(MeetingSettings).where(MeetingSettings.org_id == org_id))
    return settings_read(row)


def settings_read(row: MeetingSettings | None) -> MeetingSettingsRead:
    if row is None:
        return MeetingSettingsRead(audio_retention_days=AUDIO_RETENTION_DAYS)
    sections = [s for s in (row.document_sections or []) if s in DOCUMENT_SECTIONS]
    return MeetingSettingsRead(
        consent_required=row.consent_required,
        document_design=row.document_design or "standard",
        document_accent_color=row.document_accent_color,
        document_cover_file_id=row.document_cover_file_id,
        document_footer_text=row.document_footer_text,
        # An empty list is "nothing ticked", which nobody means: a row written before the
        # sections existed, or one saved from a screen that posted nothing, reads as the
        # default set rather than as a document with no chapters.
        document_sections=sections or list(DEFAULT_DOCUMENT_SECTIONS),
        document_avatars=row.document_avatars,
        document_custom_html=row.document_custom_html,
        document_custom_css=row.document_custom_css,
        ai_instructions=row.ai_instructions,
        audio_retention_days=AUDIO_RETENTION_DAYS,
    )


class MeetingSettingsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _row(self) -> MeetingSettings | None:
        return await self.ctx.session.scalar(
            select(MeetingSettings).where(MeetingSettings.org_id == self.ctx.org.id)
        )

    async def settings(self) -> MeetingSettingsRead:
        self.ctx.require("meetings.settings.manage")
        return settings_read(await self._row())

    async def policy(self) -> MeetingPolicy:
        """What the recorder needs: readable by whoever may record, which is not the admin."""
        self.ctx.require("meetings.meeting.write")
        current = settings_read(await self._row())
        return MeetingPolicy(
            consent_required=current.consent_required,
            audio_retention_days=AUDIO_RETENTION_DAYS,
        )

    async def update(self, data: MeetingSettingsUpdate) -> MeetingSettingsRead:
        """Absent means leave alone; an explicit ``null`` clears a nullable field."""
        self.ctx.require("meetings.settings.manage")
        from app.modules.meetings.render.engine import ENGINE

        sent = data.model_dump(exclude_unset=True)
        # A custom body that cannot render is refused at save time, under the editor, rather
        # than at the first download by whoever pressed it (the reporting editor's rule).
        if "document_custom_html" in sent or "document_custom_css" in sent:
            ENGINE.validate_custom_source(
                sent.get("document_custom_html"), sent.get("document_custom_css")
            )
        if "document_design" in sent:
            design = sent["document_design"] or ENGINE.default_design
            if design != "custom" and design not in ENGINE.builtin_designs:
                design = ENGINE.default_design
            sent["document_design"] = design
        if "document_cover_file_id" in sent and sent["document_cover_file_id"] is not None:
            from app.core.storage.models import StoredFile

            # A file id is caller-supplied: it must be one of this org's, or the renderer
            # would inline another tenant's artwork on the say-so of a settings field.
            await self.ctx.repo(StoredFile).get_or_404(sent["document_cover_file_id"])
        values: dict[str, Any] = {}
        for key, value in sent.items():
            if (
                key in ("consent_required", "document_avatars", "document_sections")
                and value is None
            ):
                continue
            values[key] = value
        row = await self._row()
        repo = self.ctx.repo(MeetingSettings)
        if row is None:
            await repo.create(**values)
        elif values:
            await repo.update(row, **values)
        return settings_read(await self._row())

    async def preview(self, data: MeetingSettingsPreviewRequest) -> str:
        """Render an unsaved design over the org's most recent minuted meeting, or a sample.

        What the admin sees is the renderer every download comes out of, on their own data —
        the reporting editor's argument, one document family over.
        """
        self.ctx.require("meetings.settings.manage")
        from app.modules.meetings.render import render_meeting_html
        from app.modules.meetings.render.engine import ENGINE
        from app.modules.meetings.render.sample import sample_meeting

        ENGINE.validate_custom_source(data.document_custom_html, data.document_custom_css)
        current = settings_read(await self._row())
        draft = current.model_copy(
            update={
                "document_design": data.document_design,
                "document_accent_color": data.document_accent_color,
                "document_cover_file_id": data.document_cover_file_id,
                "document_footer_text": data.document_footer_text,
                "document_sections": data.document_sections or current.document_sections,
                "document_avatars": data.document_avatars,
                "document_custom_html": data.document_custom_html,
                "document_custom_css": data.document_custom_css,
            }
        )
        from app.modules.meetings.models import Meeting, MeetingStatus

        row = await self.ctx.session.scalar(
            self.ctx.repo(Meeting)
            .scoped_select()
            .where(Meeting.minutes.isnot(None))
            .where(Meeting.status.in_([MeetingStatus.DONE.value, MeetingStatus.REVIEW.value]))
            .order_by(Meeting.occurred_at.desc())
            .limit(1)
        )
        meeting = row if row is not None else sample_meeting(self.ctx.org.id)
        # The preview draws every section the settings tick, plus the transcript when ticked —
        # the same resolution a download makes with nothing overridden.
        return await render_meeting_html(
            self.ctx, meeting, settings=draft, sections=list(draft.document_sections)
        )

    def source(self, design: str) -> MeetingDesignSource:
        self.ctx.require("meetings.settings.manage")
        from app.modules.meetings.render.engine import ENGINE

        html, css = ENGINE.builtin_source(design)
        return MeetingDesignSource(html=html, css=css)

    async def catalog(self) -> list[MeetingSectionCatalogEntry]:
        self.ctx.require("meetings.settings.manage")
        current = settings_read(await self._row())
        return section_catalog(current.document_sections)


def section_catalog(defaults: list[str]) -> list[MeetingSectionCatalogEntry]:
    return [
        MeetingSectionCatalogEntry(
            key=key, title_key=f"meetings.doc.section.{key}", default=key in defaults
        )
        for key in DOCUMENT_SECTIONS
    ]


__all__ = [
    "AUDIO_RETENTION_DAYS",
    "MeetingSettingsService",
    "load_settings",
    "section_catalog",
    "settings_read",
]
