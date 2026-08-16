"""Tag Manager containers, and what schakl put in them. Business-licensed — see LICENSE.

Three tables, and the split between them is the same one ``google_ads`` draws.

:class:`GtmSettings` is **org configuration**: one row, one switch. There is no credential here —
GTM needs no developer token and the grant is a ``google_connections`` row — so what the row
carries is the instance-wide kill switch for every mutating call. That is worth a table on its
own precisely because this integration writes to what runs on a client's *website*.

:class:`GtmContainer` is **the authority for "which container is this client's"**. A row rather
than a column on ``companies`` for the reason ``cloudflare`` made its credential a row and
``google_ads`` its account: an agency holds its own container *and* its clients', a client with
two websites has two containers, and the same container can legitimately serve two companies in
one group. So nothing here ever picks a container for you.

The observed-vs-decided rule (CLAUDE.md §10, ``cloudflare``) applies throughout, and it applies
harder here than anywhere else in the tree: a container is edited by *us*, by the client's own
marketeer and by whichever freelancer set it up in 2019, all in the same week. ``name``,
``live_version_id``, the counts and ``workspace_changes`` are **what Google last said**, refreshed
by verify and the nightly sync and never typed. What schakl decides is ``company_id``,
``website_id`` and ``active``.

:class:`GtmConversion` is the third kind of fact, and the one that has nowhere else to live:
**what we created in somebody else's container**. Google records that a tag exists; it records
nowhere that the tag is the "offerte aangevraagd" conversion an agency set up for this client and
is expected to keep working. Without the row, the second person to look at the account has to
read the container and guess.
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
    UniqueConstraint,
    column,
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


class GtmContainerStatus(StrEnum):
    """Two values, and the second one must be clearable.

    A flag that only ever turns *on* is a bug with a long tail (CLAUDE.md §10): a row nothing is
    wrong with keeps its red line through every sync that works. Whatever sets ``ERROR`` names
    what clears it — here, the next verify or sync that succeeds.
    """

    ACTIVE = "active"
    ERROR = "error"


class GtmSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """The org's Tag Manager posture: one switch, and an audit trail for flipping it."""

    __tablename__ = "gtm_settings"
    __entity_type__ = "gtm_settings"
    __activity_read_permission__ = "google_tag_manager.settings.manage"

    __table_args__ = (UniqueConstraint("org_id", name="uq_gtm_settings_org"),)

    #: The kill switch for every mutating route in this module — tags, triggers, variables,
    #: versions and publishing alike. Distinct from the permissions, which decide *who*: this
    #: decides *whether*, for the whole instance, in one place an owner can reach in a hurry
    #: after watching an agent do something surprising on a client's live site.
    #:
    #: Defaults to ``true`` for ``google_ads``' reason: the write permissions are already
    #: admin-only and deny-by-default, so a switch defaulting off is a second lock on a door
    #: nobody can open anyway — and a new install whose publish button never worked reads as a
    #: broken feature rather than as a safety measure.
    writes_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: Whether a schakl write lands in a workspace of our own rather than in whatever workspace
    #: the container happens to have. On by default, and the reason is the one thing about GTM
    #: that surprises people: workspaces are a shared draft, so writing into "Default Workspace"
    #: puts our change in front of whoever else was mid-edit — and their next *Publish* ships it.
    own_workspace: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: What that workspace is called. Tenant-visible because the client's own marketeer sees it
    #: in Tag Manager, so an agency wants their own name on it, not ours.
    workspace_name: Mapped[str] = mapped_column(
        String(120), nullable=False, default="schakl", server_default="schakl"
    )


class GtmContainer(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One Tag Manager container this org may work in, and whose it is."""

    __tablename__ = "gtm_containers"
    __entity_type__ = "gtm_container"
    __activity_read_permission__ = "google_tag_manager.container.read"

    __table_args__ = (
        # One row per container per org. Not ``(org, account, container)``: a container id is
        # unique across GTM, so a compound key would let the same container be linked twice
        # under two spellings of its account and give "whose client is this" two answers.
        UniqueConstraint("org_id", "container_id", name="uq_gtm_containers_org_container"),
        Index("ix_gtm_containers_org_company", "org_id", "company_id"),
        Index("ix_gtm_containers_org_active", "org_id", "active"),
    )

    #: The GTM **account** id (the agency's or the client's own Tag Manager account). Bare
    #: digits, as Google gives it. Kept beside the container id because every API path needs it:
    #: there is no way to address a container without naming its account.
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The numeric container id. The identity — see the unique constraint.
    container_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: ``GTM-NPGFR9W9``. What a human types, what is on the client's website, and what
    #: ``containers:lookup`` resolves — so it is stored as well as the numeric id, and neither is
    #: derived from the other.
    public_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    #: ``accounts/6371679663/containers/261371074``, Google's own relative path. Stored rather
    #: than rebuilt from the two ids above, so a shape change at Google's end is one row to fix
    #: rather than a string built in eleven places.
    path: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    #: The client whose container this is. Nullable: an agency's own container belongs to no
    #: client, and one discovered before anyone said whose it is should still be linkable. A NULL
    #: row is not company data, so the company horizon leaves it visible (#285).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    #: The site this container is installed on. A client with two websites has two containers,
    #: and the panel on a website's page is the surface that makes that legible. SET NULL, not
    #: CASCADE: a removed website must not delete the record of the client's container.
    website_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("websites.id", ondelete="SET NULL"), nullable=True
    )
    #: Whose Google grant reaches this container. SET NULL rather than CASCADE, for the reason
    #: ``marketing_links`` gives: a colleague disconnecting their Google account must not delete
    #: the client's container record. The link goes dormant and asks to be reconnected.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -- what Google last said (observed, never edited here) ---------------------------------- #
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: ``["web"]``, ``["androidSdk5"]``, ``["server"]`` — a server-side container answers a very
    #: different set of questions from a web one, and a screen that cannot tell them apart offers
    #: a page-view trigger for a container that has no pages.
    usage_context: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    domain_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    tagging_server_urls: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: The live version — what is actually running on the client's site right now. The single
    #: most useful fact this table holds, and the one nobody can get from the container id.
    live_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    live_version_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Counts off the **live** version, not off a workspace: "what is running" is the question.
    tag_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    trigger_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    variable_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: How many unpublished changes are sitting in the workspaces. Non-zero means somebody — us,
    #: the client, a freelancer — has staged a change that is not live, which is exactly the
    #: state an agency wants to notice before the client asks why nothing happened.
    workspace_changes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: When the four fields above were last true. Separate from ``last_synced_at`` for the reason
    #: ``cloudflare`` separates them: "we looked and there is nothing" and "nobody has ever
    #: looked" are different sentences that one nullable count cannot tell apart.
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- what schakl decided ------------------------------------------------------------------ #
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # -- health -------------------------------------------------------------------------------- #
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GtmContainerStatus.ACTIVE.value,
        server_default=GtmContainerStatus.ACTIVE.value,
    )
    #: Google's own sentence, scrubbed and truncated. Never i18n'd and never in the error
    #: envelope (§9) — it lives here so an admin can read what Google actually said.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _container_horizon_clause(container_column, *, unattached_visible: bool = False):
    """The company horizon for a table whose client link is its **container's**.

    These rows carry no ``company_id``, so the repository's column match would find nothing and
    therefore filter nothing at all — #285's first failure mode, and the one that leaks rather
    than over-restricts. The model states the join instead and every repository path picks it up.
    A container attached to no client (the agency's own) stays visible either way: it is not
    company data.
    """

    def clause(scope):
        containers = table("gtm_containers", column("id"), column("org_id"), column("company_id"))
        joined = container_column.in_(
            select(containers.c.id).where(
                (containers.c.company_id.is_(None)) | (containers.c.company_id.in_(scope))
            )
        )
        return (container_column.is_(None)) | joined if unattached_visible else joined

    return clause


class GtmConversionKind(StrEnum):
    """What a set-up conversion actually deploys.

    Two, and deliberately not more. These are the two tags an agency sets up over and over, and
    they are the two whose parameter vocabulary is small enough to state safely. Everything else
    goes through the raw tag endpoint, where the caller writes the tag body and GTM's own
    validator is the judge — which is a better answer than a half-modelled recipe that produces a
    tag firing into nothing.
    """

    #: A GA4 event tag (``gaawe``): the event name plus the measurement id it reports to.
    GA4_EVENT = "ga4_event"
    #: A Google Ads conversion tag (``awct``): the conversion id and label.
    ADS_CONVERSION = "ads_conversion"


class GtmConversionStatus(StrEnum):
    #: Created in a workspace. Real, invisible to the world until a version of it is published.
    DRAFT = "draft"
    #: A version containing it has been published — it is firing on the client's site.
    LIVE = "live"
    #: The last attempt to create or publish it failed; ``last_error`` says what Google said.
    ERROR = "error"
    #: Its tag or trigger is no longer in the container — somebody removed it in Tag Manager.
    MISSING = "missing"


class GtmConversion(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """A conversion schakl set up in a client's container: what was asked for, and what it made.

    **The row exists because Google keeps only half of this.** GTM records that a trigger and a
    tag exist; it records nowhere that together they are *"offerte aangevraagd"*, that an agency
    promised the client it would keep working, or that it was set up from here rather than by
    hand. Without the row, the next person to look has to read the container and guess — which is
    the same argument ``google_ads_decisions`` makes about a decision not to act.

    It carries both halves for the same reason ``cloudflare_zones`` does: ``config`` is **what was
    asked for**, ``tag_id``/``trigger_id``/``status`` are **what was last observed**. A conversion
    whose tag somebody deleted in Tag Manager is then an expressible state (``MISSING``) rather
    than a row that silently claims to be working.
    """

    __tablename__ = "gtm_conversions"
    __entity_type__ = "gtm_conversion"
    __activity_read_permission__ = "google_tag_manager.container.read"

    __table_args__ = (
        # One conversion per key per container: setting up "offerte" twice is a mistake, not an
        # arrangement, and the second attempt should say so rather than leave two tags firing.
        UniqueConstraint("org_id", "container_id", "key", name="uq_gtm_conversions_key"),
        Index("ix_gtm_conversions_container", "org_id", "container_id", "status"),
    )

    container_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("gtm_containers.id", ondelete="CASCADE"), nullable=False
    )
    #: What the agency calls it, as typed.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Casefolded, whitespace-collapsed — the identity the unique constraint matches on, so
    #: "Offerte aangevraagd" and "offerte  aangevraagd" are one conversion rather than two.
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)

    # -- what was asked for -------------------------------------------------------------------- #
    #: ``{trigger: {kind, url_contains, event_name, selector, …}, tag: {event_name,
    #: measurement_id, conversion_id, conversion_label, …}}`` — the recipe's own vocabulary, not
    #: GTM's. Stored so a conversion can be re-explained, and re-created, without reading Google.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # -- what was made (observed) --------------------------------------------------------------- #
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trigger_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tag_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The version that first carried it live. Nullable while it is still a draft.
    published_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GtmConversionStatus.DRAFT.value,
        server_default=GtmConversionStatus.DRAFT.value,
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Snapshotted at record time (§16). An audit trail whose actor evaporates is not one — and
    #: this one answers "who put this tag on the client's site", which is asked months later.
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _container_horizon_clause(cls.container_id)
