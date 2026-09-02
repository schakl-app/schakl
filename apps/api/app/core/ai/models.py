"""Per-tenant AI provider configuration, usage metering and report drafts (issues #126, #130).

``AISettings`` follows the email-settings shape (#17): one row per org, the provider secret
encrypted at rest (``api_key_enc``, :mod:`app.core.crypto`), never returned by the API.
``AIUsage`` stores counts only — never prompt or completion content (#126 non-negotiable).
``AIReport`` is the stored draft a monthly client report is edited from (#130): a record,
auditable (§16), whose ``company_id`` carries no FK so a sent report's provenance outlives
the company row it described — the activity-trail precedent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity.mixin import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: Supported providers. ``openai_compatible`` is any server speaking the OpenAI chat API on a
#: tenant-supplied ``base_url`` (Azure OpenAI, Mistral, a local Ollama/vLLM for on-prem data).
AI_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "openai_compatible")

#: The per-feature toggles a tenant can flip independently (#126): a tenant can enable
#: writing assist and leave the assistant off. Keys of ``AISettings.features``.
#:
#: ``email_assist`` (#327) earns its own key rather than riding ``writing_assist``, which
#: docs/AI.md otherwise recommends: it is the only feature that sends *a client's own words* to
#: a model, and an agency happy for AI to polish a colleague's paragraph may well not be happy
#: for it to read the mailbox. Folding the two together would have switched the second on for
#: everyone who had already agreed to the first.
#:
#: ``task_assist`` (#382) earns one for the ordinary reason: both keys it could have ridden say
#: the wrong thing. ``time_assist`` would make dictating a *task* die when a tenant switches off
#: the *time* quick-add — a consequence nothing on the settings screen predicts — and
#: ``writing_assist`` is about polishing prose somebody already wrote, not about drafting a record.
AI_FEATURES: tuple[str, ...] = (
    "assistant",
    "writing_assist",
    "time_assist",
    "task_assist",
    "reporting",
    "email_assist",
)

#: The features that consume a microphone. ``speech`` stays a capability rather than a toggle
#: (see ``service.SPEECH_CAPABILITY``), but it still has to be *for* something: reporting it on
#: an org whose every dictating feature is off would draw a microphone with no host. A tuple
#: rather than the single ``"time_assist"`` this used to be spelled as inline, because #382 added
#: the second host and the single-name version made task dictation die with the time quick-add.
#: The assistant is the third host: a spoken question or instruction lands in its composer.
SPEECH_FEATURES: tuple[str, ...] = ("time_assist", "task_assist", "assistant")


class AISettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One row per org: which provider this tenant's AI features talk to, and how.

    ``features`` is ``{feature: {"enabled": bool, "model": str | None}}`` — a per-feature
    enable plus an optional model override. ``house_style`` is the tenant-authored
    tone-of-voice instruction prepended to every writing prompt.
    """

    __tablename__ = "ai_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_ai_settings_org"),)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    default_model: Mapped[str] = mapped_column(String(255), nullable=False)
    features: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    house_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Soft cap on tokens per calendar month; NULL = unlimited. Interactive use over the cap
    #: is put behind an explicit "budget reached" acknowledgement, never silently allowed.
    monthly_token_budget: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- speech-to-text (#246) ------------------------------------------------------- #
    # Its own credential, because the chat provider usually cannot do it: Anthropic has no
    # transcription endpoint at all, and it is this product's default provider. NULL here
    # means "reuse the chat provider", which only resolves for one that can transcribe — so
    # a tenant on Anthropic points these at whatever speech service they do have, without
    # giving up Claude for everything else.
    speech_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    speech_base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    speech_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    speech_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Soft cap on transcribed seconds per calendar month; NULL = unlimited. Seconds, not
    #: tokens: an audio model reports no token usage, and folding one unit into the other
    #: would corrupt both the token budget and the settings meter.
    monthly_audio_seconds_budget: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AIUsage(UUIDPrimaryKeyMixin, OrgScopedMixin, Base):
    """One row per AI request: counts and labels only, never content (#126)."""

    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_org_created", "org_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    tokens_in: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Transcribed audio, for the speech feature (#246). Its own column because seconds are
    #: not tokens: writing them into ``tokens_out`` would corrupt the token budget and the
    #: settings meter alike. Zero for every text request.
    audio_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIReport(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """A drafted monthly client report (#130): markdown, editable, never auto-sent.

    ``created_by_name`` snapshots the author (#64): a departed account never becomes "the
    system". ``language`` is the report's own language — client-facing language ≠ UI locale.
    """

    __tablename__ = "ai_reports"
    __entity_type__ = "ai_report"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    #: Calendar month the report covers, as ``YYYY-MM``.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="nl")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
