"""``cloudflare`` models (epic #278) — accounts, zones, redirects and Pages links.

Five org-scoped, RLS-forced tables (§5). The shape follows one rule: **schakl stores what it
decided, and separately what it last observed at Cloudflare** — never one column pretending to
be both. A redirect row is the tenant's intent (target, status code, what to preserve); the
``rule_id`` / ``last_status`` / ``last_checked_at`` beside it are the last thing Cloudflare
said about it. That split is what makes "somebody edited this in the Cloudflare dashboard"
expressible at all; a single `is_active` boolean would have silently overwritten one with the
other on the next sync.

**A tenant holds more than one Cloudflare account.** An agency has its own, and clients bring
theirs; a zone lives in exactly one account, and the *same apex may exist in two of them* (
Cloudflare only refuses the second **activation**, not the second pending zone). So the
credential is a row, not a settings singleton, uniqueness is on ``(org_id, cf_zone_id)`` and
never on the zone name, and every read that resolves a domain to a zone must be able to answer
"two candidates, pick one" instead of guessing.

Company horizon (#285): none of these carry ``company_id``, and the ones that belong to a
client belong to it *through a domain* — so they declare ``__company_horizon_clause__`` or the
repository would filter nothing at all. ``cloudflare_accounts`` and ``cloudflare_pages_projects``
are org-wide configuration with no client of their own; they stay readable behind their own
admin-only manage permission, exactly as §15 describes for config surfaces.
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
    select,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

# ``domains`` belongs to another module; reference it as a bare table rather than importing its
# model (CLAUDE.md §6) — the same bridge ``websites`` uses for its parent domain.
_domains = table("domains", column("id"), column("org_id"), column("company_id"))


def _domain_scope_subquery(org_id_col, scope: frozenset[uuid.UUID]):
    """The ids of the domains inside ``scope``, correlated to this model's own ``org_id``."""
    return select(_domains.c.id).where(
        _domains.c.org_id == org_id_col, _domains.c.company_id.in_(scope)
    )


class CloudflareAccountStatus(StrEnum):
    """Whether the stored token still works. ``error`` is set by whatever call found out."""

    ACTIVE = "active"
    ERROR = "error"


class RedirectStatus(StrEnum):
    """The last thing a reconcile learned about our rule at Cloudflare.

    Deliberately five values, not a boolean: "we never pushed it" (``pending``), "it is there
    and matches" (``active``), "it is there and somebody changed it" (``drift``), "it is gone"
    (``missing``) and "Cloudflare refused" (``error``) each need a different button.
    """

    PENDING = "pending"
    ACTIVE = "active"
    DRIFT = "drift"
    MISSING = "missing"
    ERROR = "error"


#: HTTP status codes a domain-wide redirect may use. 301/308 are permanent (and cached by
#: browsers, which is why the form warns), 302/307 temporary; 307/308 preserve the method.
REDIRECT_STATUS_CODES: tuple[int, ...] = (301, 302, 307, 308)


class CloudflareAccount(
    UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base
):
    """One Cloudflare API token the tenant has handed us, and what it can do.

    Auditable (§16) because rotating or repointing a credential that can edit live DNS is
    exactly the change an agency needs to be able to attribute later. The token itself is never
    part of the trail — only the fact that it changed (``token_changed``).
    """

    __tablename__ = "cloudflare_accounts"
    __entity_type__ = "cloudflare_account"
    __activity_read_permission__ = "cloudflare.settings.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_cloudflare_accounts_org_name"),
        Index("ix_cloudflare_accounts_org_active", "org_id", "active"),
    )

    #: Tenant free text ("Breik hoofdaccount", "Klant X — eigen account"). Not i18n'd: it names
    #: a thing the tenant owns, like ``providers.name``.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: A **scoped API token**, never the legacy Global API Key. Fernet at rest
    #: (:mod:`app.core.crypto`), write-only through the API, never in a response or a log line.
    api_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    #: Cloudflare's own account id, discovered at verify time. NULL when the token is
    #: zone-scoped: such a token can still read zones, DNS and redirects, it simply cannot
    #: create a zone or list Pages projects — which is a *degraded* account, not a broken one.
    cf_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cf_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: The user-facing "which provider is this" label (#89). SET NULL: deleting a catalog row
    #: must never take a working credential with it.
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CloudflareAccountStatus.ACTIVE.value,
        server_default=CloudflareAccountStatus.ACTIVE.value,
    )
    #: What the token was observed to be allowed to do, probed at verify time — the keys of
    #: :data:`app.modules.cloudflare.client.CAPABILITIES`. Stored so the UI can say "this token
    #: cannot create zones, add Account → Zone → Edit" instead of failing at the button.
    capabilities: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Why a probe answered *no*, keyed by the same capability name: Cloudflare's status, code and
    #: own text (``client.describe_failure``). A refused probe used to be recorded as a bare
    #: ``False``, which is the one shape an admin cannot act on — "niet toegekend" against a
    #: permission their token screen plainly grants leaves nothing to check but the token, and the
    #: token is what they were already looking at. Only ever holds keys that answered ``False``.
    capability_errors: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the **Registrar** list was last read — separate from ``last_synced_at`` because the
    #: two are separate authorities (#298). A token that syncs zones every day and has never
    #: been allowed to read the registrar knows nothing about who pays for a registration, and
    #: only a register that has answered may narrow what schakl invoices.
    registrar_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CloudflareZone(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A zone as Cloudflare last described it, optionally matched to a schakl domain.

    Uniqueness is ``(org_id, cf_zone_id)`` and **not** the name: the same apex can legitimately
    exist as an active zone in one of the tenant's accounts and a pending one in another, and a
    unique name would make the second sync fail instead of surfacing the ambiguity.
    """

    __tablename__ = "cloudflare_zones"
    __table_args__ = (
        UniqueConstraint("org_id", "cf_zone_id", name="uq_cloudflare_zones_org_zone"),
        Index("ix_cloudflare_zones_org_name", "org_id", "name"),
        Index("ix_cloudflare_zones_org_domain", "org_id", "domain_id"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cloudflare_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cf_zone_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    #: Cloudflare's own vocabulary, stored raw: active | pending | initializing | moved |
    #: deleted | deactivated. Not an enum here — a value we don't know must still round-trip.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    plan: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The nameservers Cloudflare assigned. This is the payload the registrar write path (the
    #: OXXA half of #278, split out) pushes; surfacing it is what makes the manual route work
    #: in the meantime.
    name_servers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    original_name_servers: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    #: The schakl domain this zone is for. NULL = a zone in the account that no domain record
    #: matches (yet) — listed, never hidden: an unknown zone in a client's account is exactly
    #: the thing an agency wants to see.
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID]):  # noqa: ANN206
        """A zone's client is its **domain's** (#285); an unmatched zone has none.

        Without this the repository's column match finds no ``company_id`` and filters nothing
        at all, so a membership scoped to one company group would read every client's zones.
        """
        return cls.domain_id.is_(None) | cls.domain_id.in_(
            _domain_scope_subquery(cls.org_id, scope)
        )


class CloudflareRegistrarDomain(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A domain as **Cloudflare Registrar** last described it (#298).

    A zone is not a registration. Cloudflare will happily run DNS for a domain the client
    registered at their own registrar and pays for themselves, which is exactly the domain an
    agency must not invoice — so this table is deliberately *not* a couple of columns on
    :class:`CloudflareZone`. The registrar list answers a different question, from a different
    endpoint, under a different token permission, and is the only Cloudflare evidence that the
    agency holds a registration at all.

    ``at_cloudflare`` is that evidence, decided once at sync time from ``current_registrar``:
    the endpoint also reports domains registered *elsewhere*, and treating the list's mere
    membership as "we pay for this" would bill a client for a domain we only serve DNS for.
    The raw registrar string is kept beside it, because "it moved to GoDaddy last month" is
    something an agency needs to read rather than infer from a flag that silently flipped.

    ``domain_id`` is nullable for :class:`CloudflareZone`'s reason: a registration nothing
    matches is the most valuable row a sync produces — something the agency is paying to renew
    and quite possibly not billing anyone for.
    """

    __tablename__ = "cloudflare_registrar_domains"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "account_id", "name", name="uq_cloudflare_registrar_domains_org_name"
        ),
        Index("ix_cloudflare_registrar_domains_org_name", "org_id", "name"),
        Index("ix_cloudflare_registrar_domains_org_domain", "org_id", "domain_id"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cloudflare_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The registrable name, normalised lowercase.
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    #: Cloudflare's own id for the registration, where the payload carried one.
    cf_registrar_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The schakl domain this is. NULL = a registration no domain record matches.
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )

    # --- observed at Cloudflare ------------------------------------------------------------ #
    #: Whoever holds the registration today, as Cloudflare names them ("Cloudflare", "GoDaddy").
    #: Stored raw and never parsed into an enum: an unknown registrar must still round-trip.
    current_registrar: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Whether that reads as Cloudflare Registrar. Derived at sync time and **stored**, so the
    #: billing clause is one indexed column test rather than a string match per row.
    at_cloudflare: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_renew: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    locked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: The registry's own status words, comma-joined as Cloudflare reports them.
    registry_statuses: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID]):  # noqa: ANN206
        """A registration's client is its **domain's** (#285); an unmatched row has none."""
        return cls.domain_id.is_(None) | cls.domain_id.in_(
            _domain_scope_subquery(cls.org_id, scope)
        )


class CloudflareRedirect(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """The domain-wide redirect schakl owns for one zone — intent, plus last observation.

    One per zone by constraint. Cloudflare will happily hold ten redirect rules on a zone and
    the tenant may well have their own; this row is only ever *ours*, identified at Cloudflare
    by ``cf_rule_id``, and a reconcile never touches a rule it did not create.
    """

    __tablename__ = "cloudflare_redirects"
    __table_args__ = (
        UniqueConstraint("org_id", "zone_id", name="uq_cloudflare_redirects_org_zone"),
        Index("ix_cloudflare_redirects_org_domain", "org_id", "domain_id"),
    )

    zone_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cloudflare_zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: NOT NULL: a domain-wide redirect is configured *from* a domain, and the domain is what
    #: gives the row a client — which is what the horizon clause below reads.
    domain_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- intent ------------------------------------------------------------------------- #
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=301)
    #: Append the incoming path to the target ("/prijzen" → "https://new.nl/prijzen"). Off
    #: sends every URL to the target itself, which is what an agency usually wants when the
    #: new site has a different structure.
    preserve_path: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    preserve_query: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: Also redirect ``www`` and every other subdomain of the apex. On by default: a domain-wide
    #: redirect that leaves ``www`` serving the old site is the bug this feature exists to avoid.
    include_subdomains: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # --- last observation at Cloudflare -------------------------------------------------- #
    cf_ruleset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cf_rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=RedirectStatus.PENDING.value,
        server_default=RedirectStatus.PENDING.value,
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID]):  # noqa: ANN206
        """A redirect's client is its domain's (#285). ``domain_id`` is NOT NULL, so there is
        no company-less redirect to exempt."""
        return cls.domain_id.in_(_domain_scope_subquery(cls.org_id, scope))


class CloudflarePagesProject(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A Cloudflare Pages project, synced from the account so the picker never calls Cloudflare.

    Same rule as ``marketing_links``' snapshotted ``display_name``: rendering a list must not
    depend on an outside API being up (docs/PERFORMANCE.md).
    """

    __tablename__ = "cloudflare_pages_projects"
    __table_args__ = (
        UniqueConstraint("org_id", "account_id", "name", name="uq_cloudflare_pages_org_name"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cloudflare_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The project's Cloudflare name — its identifier in every Pages API path.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: "my-project.pages.dev" — the CNAME target a custom domain points at.
    subdomain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    production_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CloudflarePagesLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One hostname attached to a Pages project, and what Cloudflare says about it.

    A project serves many hostnames (apex, ``www``, a staging host), so this is a row per
    hostname rather than a column on the project.
    """

    __tablename__ = "cloudflare_pages_links"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "project_id", "hostname", name="uq_cloudflare_pages_links_host"
        ),
        Index("ix_cloudflare_pages_links_org_domain", "org_id", "domain_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cloudflare_pages_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    #: Cloudflare's own word for where the custom domain stands: pending | active |
    #: initializing | deployment_failed. Stored raw for the same reason as the zone's.
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When a look last found that Cloudflare does **not** hold this hostname on this project;
    #: ``NULL`` means present at the last look. Drift is *reported*, never acted on — the row
    #: stays and the panel says so, exactly as a drifted redirect rule does. Deleting a link
    #: because one probe came back empty would silently forget a hostname a token could not
    #: read, and there is no undo for a hostname nobody remembers registering.
    missing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When a sync adopted this link from Cloudflare, rather than the link button creating it.
    #: The two are the same row and behave identically; this is the honest answer to *who
    #: decided this*, and it is what makes adoption safe to do automatically — recording a
    #: hostname already registered at Cloudflare writes nothing there.
    discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID]):  # noqa: ANN206
        """A Pages link's client is its domain's (#285)."""
        return cls.domain_id.in_(_domain_scope_subquery(cls.org_id, scope))
