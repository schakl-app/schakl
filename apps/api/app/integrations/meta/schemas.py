"""Request/response shapes for the meta integration. Business-licensed — see LICENSE.

Every name is prefixed ``Meta`` (or ``SocialPost``): a generic Pydantic schema name makes
FastAPI qualify *both* modules' components in the OpenAPI document, which silently rewrites
somebody else's generated client.

**A secret is written and never read.** The app secret and a token go in through a write
schema and come back as ``…_configured: bool`` — what a screen needs to draw "ingesteld".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MetaScheduler = Literal["schakl", "meta"]
MetaAssetKind = Literal["page", "instagram", "ad_account"]
SocialPostFormat = Literal["post", "reel"]
SocialPostStatus = Literal[
    "draft", "review", "scheduled", "publishing", "published", "partial", "failed", "cancelled"
]


# --------------------------------------------------------------------------- settings
class MetaSettingsRead(BaseModel):
    app_id: str | None
    app_secret_configured: bool
    writes_enabled: bool
    facebook_scheduler: MetaScheduler
    #: The Graph API version every call is pinned to. Instance configuration, shown so an
    #: admin comparing against Meta's changelog can see which one is in use.
    api_version: str
    #: The scopes the setup guide asks for, in the order Business Settings lists them.
    recommended_scopes: list[str]
    #: Where Meta will fetch Instagram media from. Shown because an instance behind an access
    #: gateway has to let exactly this prefix through (docs/DEPLOY.md).
    media_url_prefix: str


class MetaStatusRead(BaseModel):
    """What a working screen needs to know about the connection, and nothing it may not.

    Readable by anyone who may read the channels — the settings themselves are an admin's —
    because a planner that cannot tell "nothing is connected" from "nothing is linked" can
    only draw an empty list, and the composer has to be able to say who holds the clock.
    """

    #: The app and at least one usable token are in place.
    connected: bool
    writes_enabled: bool
    facebook_scheduler: MetaScheduler
    #: Channels (Pages and Instagram accounts) linked and switched on.
    channels_linked: int
    #: Channels a token has found that nobody has linked yet.
    channels_unlinked: int
    ad_accounts_linked: int
    #: Ad accounts found and not linked. Kept apart from the channels: a planner asks
    #: "can I post yet", and an unlinked ad account is not an answer to that.
    ad_accounts_unlinked: int = 0
    #: A token is expired, was refused, or is running out and could not be refreshed.
    needs_attention: bool


class MetaSettingsWrite(BaseModel):
    """§18 throughout: a field left out is left alone. ``app_secret: null`` clears it."""

    app_id: str | None = Field(default=None, max_length=32)
    app_secret: str | None = Field(default=None, max_length=255)
    writes_enabled: bool | None = None
    facebook_scheduler: MetaScheduler | None = None


# --------------------------------------------------------------------------- credentials
class MetaCredentialRead(BaseModel):
    id: uuid.UUID
    label: str
    business_id: str
    business_name: str
    subject_id: str | None
    subject_name: str
    #: ``SYSTEM_USER`` is what this integration is built for. A ``USER`` token works and dies
    #: with that person's account, which the screen says.
    token_kind: str | None
    #: Whether the token was generated for the app configured here. ``None`` = not yet known.
    app_matches: bool | None
    scopes: list[str]
    missing_scopes: list[str]
    #: ``{facebook_publish: true, instagram_publish: false, …}`` — the scopes, as sentences.
    capabilities: dict[str, bool]
    issued_at: datetime | None
    #: ``null`` = the token never expires.
    expires_at: datetime | None
    days_left: int | None
    data_access_expires_at: datetime | None
    refreshed_at: datetime | None
    refresh_attempted_at: datetime | None
    #: Meta's own sentence, or one of our ``meta.error.*`` keys.
    refresh_error: str | None
    #: When the nightly job will next try to refresh it. ``null`` for a token that never
    #: expires, and for one that already has.
    refresh_due_at: datetime | None
    active: bool
    status: str
    last_error: str | None
    last_verified_at: datetime | None
    last_discovered_at: datetime | None
    asset_count: int = 0
    linked_asset_count: int = 0


class MetaCredentialCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    token: str = Field(min_length=20, max_length=2_000)
    #: The Business portfolio's id, from Business Settings → Business info. Optional: without
    #: it, discovery reads what the token itself lists and cannot tell "shared by a client"
    #: from "ours".
    business_id: str = Field(default="", max_length=32)


class MetaCredentialUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    #: A replacement token. Blank or absent keeps the one that is stored.
    token: str | None = Field(default=None, max_length=2_000)
    business_id: str | None = Field(default=None, max_length=32)
    active: bool | None = None


class MetaDiscoveryRead(BaseModel):
    found: int
    created: int
    #: i18n keys under ``meta.warning.*`` — one per list that could not be read.
    warnings: list[str]


# --------------------------------------------------------------------------- assets
class MetaAssetRead(BaseModel):
    #: schakl's own id — the one every route takes. Meta's id rides beside it under a name
    #: that cannot be mistaken for it.
    id: uuid.UUID
    kind: MetaAssetKind
    meta_id: str
    name: str
    username: str | None
    picture_url: str | None
    #: For an Instagram account: the Page it publishes through, as schakl's asset id where
    #: that Page is known here.
    linked_page_id: uuid.UUID | None = None
    linked_page_name: str | None = None
    relation: str
    company_id: uuid.UUID | None
    company_name: str | None = None
    credential_id: uuid.UUID | None
    credential_label: str | None = None
    active: bool
    tasks: list[str]
    #: Whether a post can be published here as things stand. ``false`` carries ``blocked_by``.
    can_publish: bool
    #: An i18n key under ``meta.issue.*`` saying why not.
    blocked_by: str | None = None
    currency: str | None
    timezone: str | None
    account_status: int | None
    dsa_beneficiary: str | None
    dsa_payor: str | None
    status: str
    last_error: str | None
    observed_at: datetime | None
    last_verified_at: datetime | None
    #: Into Meta's own interface.
    meta_url: str | None


class MetaAssetPage(BaseModel):
    items: list[MetaAssetRead]
    total: int
    limit: int
    offset: int


class MetaAssetUpdate(BaseModel):
    """Only what schakl decides. ``company_id: null`` detaches — the agency's own asset."""

    company_id: uuid.UUID | None = None
    active: bool | None = None


class MetaPublishedPost(BaseModel):
    external_id: str
    channel: str
    text: str
    kind: str
    permalink: str | None
    published_at: datetime | None
    image_url: str | None
    likes: int | None
    comments: int | None
    shares: int | None


class MetaInsightsRead(BaseModel):
    asset_id: uuid.UUID
    date_from: date
    date_to: date
    metrics: dict[str, float]
    #: Metrics Meta did not answer for. Named rather than dropped.
    unavailable: list[str]


# --------------------------------------------------------------------------- posts
class SocialPostMedia(BaseModel):
    """One image or video. A stored file **or** an address, never both."""

    kind: Literal["image", "video"] = "image"
    #: A file uploaded to this post (``POST /files?entity_type=meta_post&entity_id=<post>``).
    file_id: uuid.UUID | None = None
    #: An ``https`` address Meta can fetch — how a video is attached, since the file store
    #: does not take one.
    url: str | None = Field(default=None, max_length=2_000)
    alt: str = Field(default="", max_length=1_000)


class SocialPostImage(BaseModel):
    """An image for a post, carried inside the JSON body.

    The browser uploads a picture as multipart and then names it in ``media``. A tool call is
    a JSON document and cannot do the first half, so an agent that may write a post could
    write every part of it except the picture — which on Instagram is the part without which
    nothing can be published at all. This is that half, as JSON (docs/STORAGE.md's twin).
    """

    filename: str = Field(min_length=1, max_length=255)
    #: ``image/jpeg``, ``image/png`` or ``image/webp``.
    content_type: str = Field(min_length=1, max_length=120)
    #: Standard base64, padding optional; a ``data:`` URL prefix is also accepted.
    data: str = Field(min_length=1)
    #: What the picture shows, for somebody who cannot see it. Published with it.
    alt: str = Field(default="", max_length=1_000)


class SocialPostMediaRead(SocialPostMedia):
    width: int | None = None
    height: int | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    filename: str | None = None


class SocialPostIssue(BaseModel):
    level: Literal["error", "warning"]
    #: An i18n key under ``meta.issue.*``.
    code: str
    field: str
    channel: str | None
    asset_id: uuid.UUID | None
    details: dict[str, Any]


class SocialPostTargetRead(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    asset_name: str
    asset_username: str | None
    asset_picture_url: str | None
    channel: str
    body_override: str | None
    status: str
    scheduler: MetaScheduler
    #: The post's id at Meta once it exists there.
    meta_post_id: str | None
    permalink: str | None
    attempts: int
    published_at: datetime | None
    last_error: str | None
    last_error_code: str | None


class SocialPostRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    company_name: str | None = None
    format: SocialPostFormat
    #: The first line of the body, cut — what a list prints.
    title: str
    body: str
    link: str | None
    media: list[SocialPostMediaRead]
    notes: str
    scheduled_at: datetime | None
    status: SocialPostStatus
    published_at: datetime | None
    created_by_user_id: uuid.UUID | None
    created_by_name: str
    approved_by_user_id: uuid.UUID | None
    approved_by_name: str
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    targets: list[SocialPostTargetRead]
    #: What stands between this post and a channel, errors first. Empty once it has gone out.
    issues: list[SocialPostIssue]
    #: Whether it could be scheduled as it stands.
    ready: bool


class SocialPostPage(BaseModel):
    items: list[SocialPostRead]
    total: int
    limit: int
    offset: int


class SocialPostCounts(BaseModel):
    #: ``{status: count}`` for every status that has at least one post.
    by_status: dict[str, int]
    #: How many are still going on — the working set a list opens on.
    working: int
    total: int


def plain_newlines(value: Any) -> Any:
    """``\r\n`` → ``\n``, in a post's words and in each channel's own.

    An HTML form submits a line break as CRLF, whatever was typed. Stored as sent, a caption
    of 2,199 characters and two paragraphs counts as 2,201 here and is refused — and the
    carriage returns travel on to Meta, which prints a post exactly as it was given.
    """
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, dict):
        return {key: plain_newlines(text) for key, text in value.items()}
    return value


class _Words(BaseModel):
    @field_validator("body", "overrides", mode="before", check_fields=False)
    @classmethod
    def _newlines(cls, value: Any) -> Any:
        return plain_newlines(value)


class SocialPostCreate(_Words):
    """A new draft. ``asset_ids`` are the channels, and they decide whose post it is."""

    asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    format: SocialPostFormat = "post"
    body: str = Field(default="", max_length=70_000)
    link: str | None = Field(default=None, max_length=2_000)
    notes: str = Field(default="", max_length=8_000)
    #: A naive time is the org's wall clock; an aware one is the instant it names.
    scheduled_at: datetime | None = None
    #: ``{asset id: words}`` for a channel that should say something different.
    overrides: dict[uuid.UUID, str] = Field(default_factory=dict)


class SocialPostUpdate(_Words):
    """§18: absent means leave alone. ``link: null`` removes the link, ``media: []`` the media."""

    asset_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=20)
    format: SocialPostFormat | None = None
    body: str | None = Field(default=None, max_length=70_000)
    link: str | None = Field(default=None, max_length=2_000)
    notes: str | None = Field(default=None, max_length=8_000)
    scheduled_at: datetime | None = None
    media: list[SocialPostMedia] | None = Field(default=None, max_length=10)
    overrides: dict[uuid.UUID, str] | None = None


class SocialPostSchedule(BaseModel):
    #: When it should go out. Absent uses the time already on the post.
    scheduled_at: datetime | None = None
