"""Request/response models for ``/api/v1/reporting`` (issue #300).

Every name is **prefixed**. This module shipped with bare ``ReportRead``, ``TemplateRead`` and
friends, and FastAPI resolves a component-name collision by qualifying *both* sides: the AI
core's ``ReportRead`` became ``app__core__ai__schemas__ReportRead`` and the tasks and invoicing
``TemplateRead`` were renamed alongside it, in three modules that changed nothing. A prefix here
is what keeps the next ``gen:client`` from rewriting someone else's types.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.reporting.models import (
    ReportAudience,
    ReportCadence,
    ReportCompare,
    ReportDelivery,
)


# --- tones ------------------------------------------------------------------------------ #
class ReportToneWrite(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    instructions: str = Field(default="", max_length=20_000)
    banned_phrases: list[str] = Field(default_factory=list, max_length=200)
    preferred_phrases: list[str] = Field(default_factory=list, max_length=200)
    is_default: bool = False
    active: bool = True
    position: int = 0


class ReportToneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    description: str | None = None
    instructions: str = ""
    banned_phrases: list[str] = Field(default_factory=list)
    preferred_phrases: list[str] = Field(default_factory=list)
    is_default: bool = False
    active: bool = True
    position: int = 0


# --- templates -------------------------------------------------------------------------- #
class ReportTemplateLayoutSection(BaseModel):
    key: str = Field(max_length=120)
    enabled: bool = True
    label_i18n: dict[str, str] = Field(default_factory=dict)


class ReportTemplateLayout(BaseModel):
    sections: list[ReportTemplateLayoutSection] = Field(default_factory=list, max_length=100)


class ReportTemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    audience: ReportAudience = ReportAudience.CLIENT
    design: str = Field(default="standard", max_length=32)
    layout: ReportTemplateLayout = Field(default_factory=ReportTemplateLayout)
    custom_html: str | None = None
    custom_css: str | None = None
    accent_color: str | None = Field(default=None, max_length=16)
    cover_image_file_id: uuid.UUID | None = None
    intro_text: str | None = Field(default=None, max_length=4000)
    is_default: bool = False


class ReportTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    audience: str
    design: str
    layout: dict = Field(default_factory=dict)
    custom_html: str | None = None
    custom_css: str | None = None
    accent_color: str | None = None
    cover_image_file_id: uuid.UUID | None = None
    intro_text: str | None = None
    is_default: bool = False


class ReportTemplateSource(BaseModel):
    """A shipped design's own source, for branching a custom report template off it.

    Named for its module rather than ``TemplateSource``: invoicing already publishes a schema
    by that name, and two same-named models make FastAPI qualify *both* components in the
    OpenAPI document — renaming a type in a module that changed nothing.
    """

    html: str
    css: str


class SectionCatalogEntry(BaseModel):
    """One section a template may order or switch off — the registry, made visible."""

    key: str
    title_key: str
    audience: str
    module: str


# --- profiles --------------------------------------------------------------------------- #
class ReportRecipient(BaseModel):
    contact_id: uuid.UUID | None = None
    email: str = Field(max_length=320)
    name: str = Field(default="", max_length=200)


class ReportSchedule(BaseModel):
    """A profile's own schedule. Every field may be absent, and absent means *inherit*."""

    cadence: ReportCadence | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    hour: int | None = Field(default=None, ge=0, le=23)
    compare: ReportCompare | None = None
    #: Review first, or send as soon as it is rendered. The choice the owner asked to be a
    #: setting rather than a policy — see ``ReportDelivery``.
    delivery: ReportDelivery | None = None
    publish_to_portal: bool | None = None


class ReportProfileWrite(BaseModel):
    tone_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    internal_template_id: uuid.UUID | None = None
    locale: str = Field(default="nl", max_length=8)
    business_context: str | None = Field(default=None, max_length=4000)
    goals: str | None = Field(default=None, max_length=4000)
    seo_focus: str | None = Field(default=None, max_length=4000)
    sea_focus: str | None = Field(default=None, max_length=4000)
    key_services: str | None = Field(default=None, max_length=4000)
    priority_pages: str | None = Field(default=None, max_length=4000)
    conversion_goals: str | None = Field(default=None, max_length=4000)
    scope_notes: str | None = Field(default=None, max_length=4000)
    avoid_topics: str | None = Field(default=None, max_length=4000)
    recipients: list[ReportRecipient] = Field(default_factory=list, max_length=50)
    schedule: ReportSchedule = Field(default_factory=ReportSchedule)
    internal_enabled: bool = True
    active: bool = True


class ReportProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    tone_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    internal_template_id: uuid.UUID | None = None
    locale: str = "nl"
    business_context: str | None = None
    goals: str | None = None
    seo_focus: str | None = None
    sea_focus: str | None = None
    key_services: str | None = None
    priority_pages: str | None = None
    conversion_goals: str | None = None
    scope_notes: str | None = None
    avoid_topics: str | None = None
    recipients: list[dict] = Field(default_factory=list)
    schedule: dict = Field(default_factory=dict)
    internal_enabled: bool = True
    active: bool = True
    #: The schedule after inheritance, so a screen can show what will actually happen rather
    #: than a form full of blanks that mean "something else decides".
    effective_schedule: dict = Field(default_factory=dict)
    next_run_on: date | None = None


# --- settings --------------------------------------------------------------------------- #
class ReportingSettingsWrite(BaseModel):
    schedule: ReportSchedule = Field(default_factory=ReportSchedule)
    default_locale: str = Field(default="nl", max_length=8)
    footer_text: str | None = Field(default=None, max_length=2000)


class ReportingSettingsRead(BaseModel):
    schedule: dict = Field(default_factory=dict)
    default_locale: str = "nl"
    footer_text: str | None = None


# --- reports ---------------------------------------------------------------------------- #
class ReportRow(BaseModel):
    """A list row. Carries what the list draws and nothing else (docs/PERFORMANCE.md)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    audience: str
    status: str
    locale: str
    title: str
    period_start: date
    period_end: date
    published_at: datetime | None = None
    sent_at: datetime | None = None
    pdf_file_id: uuid.UUID | None = None
    warning_count: int = 0
    generated_by_name: str | None = None
    created_at: datetime | None = None


class ReportDetail(ReportRow):
    compare_start: date | None = None
    compare_end: date | None = None
    data_snapshot: dict = Field(default_factory=dict)
    narrative: dict = Field(default_factory=dict)
    edited_sections: list[str] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    sent_to: list[dict] = Field(default_factory=list)
    template_id: uuid.UUID | None = None
    #: ``[{key, title}]`` in print order, so the review screen and the document agree.
    sections: list[dict] = Field(default_factory=list)


class ReportList(BaseModel):
    items: list[ReportRow]
    total: int | None = None


class ReportRunRequest(BaseModel):
    company_id: uuid.UUID
    audience: ReportAudience = ReportAudience.CLIENT
    #: An explicit period, for a backfill or a correction. Omitted means the schedule's own —
    #: the previous whole calendar month.
    period_start: date | None = None
    period_end: date | None = None
    #: Re-gather the numbers as well as the prose. Off by default: a report is a record, and
    #: silently re-pricing last month because somebody pressed a button is how a client ends
    #: up holding two documents that disagree.
    refresh_data: bool = False


class ReportNarrativeUpdate(BaseModel):
    """Hand-edited prose. Every key present is stored and marked as edited."""

    narrative: dict[str, str] = Field(default_factory=dict)


class ReportRewriteRequest(BaseModel):
    section_key: str = Field(max_length=120)


class ReportSendRequest(BaseModel):
    #: Overrides the profile's recipients for this one send.
    recipients: list[ReportRecipient] | None = None
    publish: bool = True


class ReportActionResult(BaseModel):
    report: ReportDetail
    queued: bool = False


class ReportRunBatchRequest(BaseModel):
    """Run the whole book of clients for one period — the "it is the 5th" button."""

    company_ids: list[uuid.UUID] | None = None
    audience: ReportAudience = ReportAudience.CLIENT
    period_start: date | None = None
    period_end: date | None = None


class ReportRunBatchResult(BaseModel):
    queued: int
    skipped: list[dict[Literal["company_id", "reason"], Any]] = Field(default_factory=list)
    #: How many clients the batch actually looked at — an enrolled client is one with a
    #: reporting profile. Zero is the answer that needs explaining, not hiding.
    enrolled: int = 0
    #: Clients with a linked data source and no profile yet. Only counted when nothing was
    #: enrolled, so the screen can point at the next step instead of shrugging.
    unconfigured: int = 0
