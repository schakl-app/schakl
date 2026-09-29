"""The app, the credentials, the assets and the posts. Business-licensed — see LICENSE.

Six tables, and the split is the one every integration here draws (CLAUDE.md §10).

:class:`MetaSettings` is **org configuration**: the tenant's *own* Meta app (id and secret),
the write kill switch, and which scheduler publishes a Facebook post. The app is the tenant's
because the route that needs no App Review is "the agency's own app in the agency's own
Business portfolio" — an instance-wide app serving several agencies is the Tech Provider case.

:class:`MetaCredential` is **a row, not a setting** — Cloudflare's rule. It is a system-user
token from the agency's Business portfolio, and it carries its own clock: when it expires, when
it was last refreshed, and what the last refresh said. A token that expires silently is an
outage that arrives on a date nobody wrote down.

:class:`MetaAsset` is **the authority for "which Page is this client's"**: a Facebook Page, an
Instagram account or an ad account the credential can reach, and the client it was linked to by
a person. ``name``, ``username``, ``tasks`` and the rest are what Meta last said; ``company_id``
and ``active`` are what schakl decided. Nothing ever picks an asset for a client.

:class:`MetaPost` and :class:`MetaPostTarget` are the one kind of fact that is *ours*: what was
written, for whom, for when, who approved it — and, per channel, what came of it. One post is
composed once and delivered to each channel it names; the delivery is its own row because a
post that reached Facebook and failed on Instagram is an ordinary Tuesday and must be
expressible.

:class:`MetaMediaToken` exists because Instagram takes media only from a URL Meta's servers can
fetch. It is a capability for one file, minted when a delivery needs it and withdrawn when the
delivery is over — the public invoice link's shape (#304), with a lifetime.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    column,
    false,
    select,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class Scheduler(StrEnum):
    """Who publishes a scheduled **Facebook** post at its time. Instagram has no choice.

    Instagram's API has no scheduling at all, so an Instagram delivery is always published by
    schakl's own worker at the due time. For Facebook the tenant chooses, because each answer
    costs something the other does not (docs/META.md §5).
    """

    #: schakl's worker publishes at the due time. One mechanism for both channels, an edit
    #: before the due time is a local edit, and the post is not visible in Meta Business Suite
    #: until it is live. An instance that is down at the due time publishes late.
    SCHAKL = "schakl"
    #: The post is handed to Meta's own scheduler as soon as the window allows. It shows in
    #: Meta Business Suite's planner and goes out even if this instance is down — and every
    #: later edit is a call to Meta rather than a local save.
    META = "meta"


class CredentialStatus(StrEnum):
    ACTIVE = "active"
    #: The last verify or refresh failed. Cleared by the next one that succeeds.
    ERROR = "error"
    #: The token is past its expiry, or Meta called it invalid. Only a new token clears it.
    EXPIRED = "expired"


class AssetStatus(StrEnum):
    ACTIVE = "active"
    ERROR = "error"


class PostStatus(StrEnum):
    #: Being written. Nothing will happen to it on its own.
    DRAFT = "draft"
    #: Offered for approval by somebody who may not schedule. Still nothing happens on its own.
    REVIEW = "review"
    #: Approved with a time. The worker (or Meta's scheduler) will publish it.
    SCHEDULED = "scheduled"
    #: At least one delivery is in flight right now.
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    #: Some channels carry it and at least one does not.
    PARTIAL = "partial"
    FAILED = "failed"
    #: Withdrawn before it went out.
    CANCELLED = "cancelled"


#: What a list opens on (CLAUDE.md §9, #329): everything still going on. Published and
#: cancelled posts are behind their own filter, wearing their status.
WORKING_POST_STATUSES: tuple[str, ...] = (
    PostStatus.DRAFT.value,
    PostStatus.REVIEW.value,
    PostStatus.SCHEDULED.value,
    PostStatus.PUBLISHING.value,
    PostStatus.PARTIAL.value,
    PostStatus.FAILED.value,
)

#: The statuses in which a post's words may still change.
EDITABLE_POST_STATUSES: frozenset[str] = frozenset(
    {PostStatus.DRAFT.value, PostStatus.REVIEW.value, PostStatus.SCHEDULED.value}
)


class TargetStatus(StrEnum):
    #: Waiting for its post to be scheduled, or for its time.
    PENDING = "pending"
    #: Handed to Meta's own scheduler. Meta holds it; we hold its id.
    HANDED_OVER = "handed_over"
    #: Claimed by a worker. A row that stays here is one whose call got no answer, and the next
    #: sweep *looks* before it does anything (Meta has no idempotency key).
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Channel(StrEnum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"


class PostFormat(StrEnum):
    #: Text, a link, one image, several images, or one video.
    POST = "post"
    #: A vertical video, published as a reel on both channels.
    REEL = "reel"


class MetaSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """The org's Meta posture: its app, the kill switch, and who schedules."""

    __tablename__ = "meta_settings"
    __entity_type__ = "meta_settings"
    __activity_read_permission__ = "meta.settings.manage"

    __table_args__ = (UniqueConstraint("org_id", name="uq_meta_settings_org"),)

    #: The tenant's own Meta app. Public by nature (it is in every OAuth URL); stored so the
    #: refresh and ``debug_token`` can name it.
    app_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Fernet at rest, write-only through the API.
    app_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The kill switch for every call that changes something at Meta — publishing, scheduling,
    #: taking down. Distinct from the permissions, which decide *who*: this decides *whether*.
    writes_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    facebook_scheduler: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=Scheduler.SCHAKL.value,
        server_default=Scheduler.SCHAKL.value,
    )


class MetaCredential(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One system-user token from the agency's Business portfolio, and its clock."""

    __tablename__ = "meta_credentials"
    __entity_type__ = "meta_credential"
    __activity_read_permission__ = "meta.settings.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "label", name="uq_meta_credentials_label"),
        Index("ix_meta_credentials_org_active", "org_id", "active"),
    )

    # -- what schakl decided ------------------------------------------------------------------ #
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The Business portfolio this token belongs to. Typed by the admin because no endpoint
    #: reliably answers "which business is this system user in" for every token; verified by
    #: reading the business node, which is what fills ``business_name``.
    business_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # -- what Meta last said (observed) ------------------------------------------------------- #
    business_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: The system user's own id and name, from ``/me``.
    subject_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: ``debug_token``'s ``type`` — ``SYSTEM_USER`` is what this integration expects. Anything
    #: else is a person's token and is said so on the screen: it dies with their account.
    token_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The app the token was generated for. Must be the app in :class:`MetaSettings`, or the
    #: refresh authenticates as the wrong app and is refused.
    token_app_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: ``NULL`` means the token never expires — Meta's ``expires_at: 0`` — and is therefore
    #: never refreshed. Not "unknown": an unverified token has ``last_verified_at`` NULL too.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # -- the refresh --------------------------------------------------------------------------- #
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When the refresh was last *tried*, whatever came of it — "we tried last night and it was
    #: refused" and "nobody has tried" are different sentences (the Timeon schedule rule).
    refresh_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The stage of the expiry warning already sent (``0`` none, then 14, 7, 1 days), so a
    #: nightly job warns once per stage rather than once per night.
    warned_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # -- health -------------------------------------------------------------------------------- #
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CredentialStatus.ACTIVE.value,
        server_default=CredentialStatus.ACTIVE.value,
    )
    #: Meta's own sentence, scrubbed. Never in the envelope (§9).
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MetaAsset(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One Page, Instagram account or ad account this org may work on, and whose it is."""

    __tablename__ = "meta_assets"
    __entity_type__ = "meta_asset"
    __activity_read_permission__ = "meta.asset.read"

    __table_args__ = (
        # One row per asset per org, whichever credential found it: the same Page reached
        # through two tokens is one Page, and "whose client is this" must have one answer.
        UniqueConstraint("org_id", "kind", "external_id", name="uq_meta_assets_identity"),
        Index("ix_meta_assets_org_company", "org_id", "company_id"),
        Index("ix_meta_assets_org_kind", "org_id", "kind", "active"),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Meta's id. For an ad account the bare number — ``act_`` is added where a call is built,
    #: so a row never holds two spellings of one account.
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Which credential reaches it. SET NULL: removing a token must not delete the record of
    #: which Page is which client's; the asset goes dormant and says so.
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("meta_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -- what schakl decided ------------------------------------------------------------------ #
    #: The client whose asset this is. NULL is the agency's own, which is not company data and
    #: stays visible whatever the caller's horizon (#285).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    #: Off means "known, and not worked on": discovery finds every asset the token reaches, and
    #: an agency links the ones it manages.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # -- what Meta last said (observed) ------------------------------------------------------- #
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: ``@handle`` for Instagram, the vanity name for a Page where there is one.
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: For an Instagram account: the Facebook Page it is connected to, by Meta's id. Instagram
    #: publishing rides that Page's access, so an Instagram account with no Page cannot publish.
    linked_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``owned`` by the agency's portfolio, or shared with it by a ``client`` as a partner.
    relation: Mapped[str] = mapped_column(String(16), nullable=False, default="owned")
    #: The Page tasks this credential holds (``CREATE_CONTENT``, ``ANALYZE``, ``ADVERTISE``…).
    #: What decides whether a publish can work at all, so it is what the screen prints.
    tasks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    # Ad accounts only.
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Meta's ``account_status`` (1 active, 2 disabled, 3 unsettled, 7 pending review, 9 in
    #: grace period, 101 closed…). Stored as Meta sent it; the screen names the ones it knows.
    account_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The ad account's own DSA defaults, as Meta holds them.
    dsa_beneficiary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dsa_payor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- the Page's own token ------------------------------------------------------------------ #
    #: Derived from the credential's token, cached, and **never treated as durable**: it is
    #: re-derived when the credential's token changes, when it is older than a week, and on
    #: any auth refusal. Whether it inherits the parent's expiry is not documented, so nothing
    #: here assumes either answer.
    page_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_token_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- health -------------------------------------------------------------------------------- #
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=AssetStatus.ACTIVE.value,
        server_default=AssetStatus.ACTIVE.value,
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """What an **external (client) login** reads here (§15, #266): nothing.

        The column-matched horizon would hand a client the rows on their own companies *and*
        every row attached to none. Neither is theirs yet: a draft is the agency's working
        material, and approval in the portal is a later phase with rules of its own
        (docs/META.md). On the model so the list, the detail, ``entity_visible`` and the file
        routes all answer the same.
        """
        return false()


class MetaPost(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One post: what it says, for whom, for when, and who stood behind it."""

    __tablename__ = "meta_posts"
    __entity_type__ = "meta_post"
    __activity_read_permission__ = "meta.asset.read"

    __table_args__ = (
        Index("ix_meta_posts_org_status", "org_id", "status", "scheduled_at"),
        Index("ix_meta_posts_org_company", "org_id", "company_id"),
    )

    #: The client the post is for — the client of the assets it goes to, which must agree.
    #: NULL is a post on the agency's own channels.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    format: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PostFormat.POST.value, server_default="post"
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: A link to share. Facebook draws its preview; Instagram has no link in a caption, so the
    #: Instagram delivery simply does not carry it and the composer says so.
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``[{"file_id": uuid, "kind": "image", "alt": "…"}, {"url": "https://…", "kind": "video"}]``
    #: in order. A stored file or an address — never both.
    media: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    #: For the team, never published.
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    #: The instant it should go out. A draft may carry one: it is the time being proposed.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PostStatus.DRAFT.value, server_default="draft"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- who (snapshotted, §16) ---------------------------------------------------------------- #
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: Who said it may go out. The permission was asked when this was written (#335): the
    #: worker that publishes it later is nobody.
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """What an **external (client) login** reads here (§15, #266): nothing.

        The column-matched horizon would hand a client the rows on their own companies *and*
        every row attached to none. Neither is theirs yet: a draft is the agency's working
        material, and approval in the portal is a later phase with rules of its own
        (docs/META.md). On the model so the list, the detail, ``entity_visible`` and the file
        routes all answer the same.
        """
        return false()


def _post_horizon_clause(post_column):
    """The company horizon for a table whose client link is its **post's** (#285, mode 1)."""

    def clause(scope):
        posts = table("meta_posts", column("id"), column("company_id"))
        return post_column.in_(
            select(posts.c.id).where(
                (posts.c.company_id.is_(None)) | (posts.c.company_id.in_(scope))
            )
        )

    return clause


class MetaPostTarget(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One post on one channel: what was asked for, and what came of it."""

    __tablename__ = "meta_post_targets"

    __table_args__ = (
        UniqueConstraint("post_id", "asset_id", name="uq_meta_post_targets_asset"),
        Index("ix_meta_post_targets_org_status", "org_id", "status"),
        Index("ix_meta_post_targets_post", "post_id"),
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meta_posts.id", ondelete="CASCADE"), nullable=False
    )
    #: RESTRICT: an asset that carries posts is deactivated, never deleted, or the record of
    #: what was published where goes with it.
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meta_assets.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    #: This channel's own words, where they differ from the post's. NULL = the post's body.
    body_override: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TargetStatus.PENDING.value, server_default="pending"
    )
    #: Who holds the clock for this delivery, decided when it was scheduled and kept: a
    #: setting changed afterwards must not leave a post both here and in Meta's planner.
    scheduler: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Scheduler.SCHAKL.value, server_default="schakl"
    )

    # -- what was made (observed) --------------------------------------------------------------- #
    #: The post's id at Meta. Stored the moment Meta answers, in the same transaction as the
    #: status — the only idempotency there is.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Instagram's media container, created before it is published. Kept so a delivery whose
    #: publish call got no answer can ask the container what happened instead of repeating it.
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: A marker this delivery wrote nowhere public and can be found by: the claim's own id.
    claim_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The envelope code of the last failure (``meta_auth``, ``meta_invalid``…), so the screen
    #: can say what to *do* in the viewer's language beside Meta's own sentence.
    last_error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _post_horizon_clause(cls.post_id)


class MetaMediaToken(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A capability to fetch one file without a session, for as long as a delivery needs it."""

    __tablename__ = "meta_media_tokens"

    __table_args__ = (
        UniqueConstraint("token", name="uq_meta_media_tokens_token"),
        Index("ix_meta_media_tokens_post", "post_id"),
    )

    token: Mapped[str] = mapped_column(String(64), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meta_posts.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set when the delivery is over. Read **before** the token is compared (#304): an off
    #: switch for a credential that cannot be collected back has to be retroactive.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
