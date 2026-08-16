"""``wordpress`` models (docs/WORDPRESS.md) — one credential per website.

One org-scoped, RLS-forced table (§5). The shape follows ``cloudflare``'s rule one level down:
Cloudflare is something a **domain** has, WordPress is something a **website** has — so the
credential is a **row keyed to a website**, not a per-org setting and not a column bag on
``websites``. An agency holds dozens of client sites and no one of them is "the" WordPress
account, which is the same argument ``cloudflare_accounts`` and ``uptime_instances`` already
make.

The second rule is theirs too: **schakl stores what it decided, and separately what it last
observed.** ``base_url`` / ``username`` / the password are the tenant's intent;
``capabilities`` / ``capability_errors`` / ``wp_version`` / ``rankmath_version`` /
``mcp_server_path`` beside them are the last thing the site said about itself. Keeping them
apart is what lets the panel say *"Rank Math is not installed here"* rather than silently
rendering an empty integration — and what lets ``capabilities_checked_at`` distinguish "we
looked and it is absent" from "nobody has ever looked", which an empty object cannot.

Company horizon (#285): this table carries no ``company_id``, and its client is its website's
**domain's** client. So it declares ``__company_horizon_clause__`` and walks the chain, exactly
as :class:`~app.modules.websites.models.Website` does — the failure it prevents is failure mode
(1), where the repository's column match finds nothing and therefore filters *nothing at all*.
Getting this wrong here is worse than getting it wrong on a website: the rows are WordPress
administrator credentials.
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

# ``websites`` and ``domains`` belong to other modules; reference them as bare tables rather
# than importing their models (CLAUDE.md §6) — the bridge ``websites`` itself uses for its
# parent domain, and ``cloudflare`` for its client.
_websites = table("websites", column("id"), column("org_id"), column("domain_id"))
_domains = table("domains", column("id"), column("org_id"), column("company_id"))


class WordPressStatus(StrEnum):
    """What we last learned about this credential.

    Four values rather than a boolean, because each sends an admin somewhere different.
    ``PENDING`` is a row nobody has verified yet — not a failure, and the state every freshly
    typed credential is in. ``UNREACHABLE`` is deliberately not ``ERROR``: a site behind a
    firewall, a DNS change mid-flight or an expired certificate is a *transport* problem the
    credential has nothing to do with, and collapsing it into ``ERROR`` sends somebody to
    re-mint an application password that was never wrong.
    """

    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    UNREACHABLE = "unreachable"


class WordPressSite(
    UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base
):
    """One WordPress Application Password, and what it was observed to reach.

    Auditable (§16) because rotating or repointing a credential that can administer a client's
    live site is exactly the change an agency needs to attribute later. The password is never
    part of the trail — only the fact that it changed (``password_changed``).
    """

    __tablename__ = "wordpress_sites"
    __entity_type__ = "wordpress_site"
    #: Reading the trail needs the key that manages the credential, not the one that reads it:
    #: the trail's subject *is* the credential (§16 — core holds no module list).
    __activity_read_permission__ = "wordpress.site.manage"

    __table_args__ = (
        # The whole point of the feature, stated in the schema: **one credential per website**.
        UniqueConstraint("org_id", "website_id", name="uq_wordpress_sites_website"),
        Index("ix_wordpress_sites_org_status", "org_id", "status"),
    )

    #: CASCADE: the credential is a property of the website and outlives nothing. Deleting the
    #: website is the one act that unambiguously means "we no longer manage this site".
    website_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("websites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Absolute, subpath preserved, no trailing slash. Normally the website's own host, but
    #: overridable and stored rather than derived: WordPress at ``https://klant.nl/blog`` is
    #: ordinary, and so is a site whose admin answers on a different host than the apex the
    #: ``websites`` row names. Deriving it would make both unreachable with no way to say so.
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)

    #: The WordPress account the application password belongs to. Not a credential on its own
    #: and safe to show — the settings screen needs it to say *whose* password this is.
    username: Mapped[str] = mapped_column(String(255), nullable=False)

    #: A WordPress **Application Password**, Fernet at rest (:mod:`app.core.crypto`),
    #: write-only through the API, never in a response, a log line or the activity trail.
    #: Never the account's real password: an application password is individually named and
    #: individually revocable from the client's own profile screen, which is the mitigation
    #: that costs nothing and is worth more than the rest combined (docs/WORDPRESS.md §6).
    app_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=WordPressStatus.PENDING.value,
        server_default=WordPressStatus.PENDING.value,
    )
    #: Why the last verify failed, as a stable key the web resolves (``wordpress.issue.*``) —
    #: never a sentence, because the API does not pick a locale for someone else's screen (§8).
    #: **Cleared by any verify that succeeds.** A status flag that only ever turns on is a bug
    #: with a long tail (docs/CLOUDFLARE.md's ``_flag_account``), so whatever sets this says
    #: what clears it, and the answer is: the next probe that reaches the site.
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- observed, never decided ------------------------------------------------------------ #
    #: What the credential was seen to reach — the keys of
    #: :data:`app.integrations.wordpress.client.CAPABILITIES`. A **missing** key means "not probed",
    #: which is why this is written per probe rather than replaced wholesale.
    capabilities: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Why a probe answered no, keyed the same way, and only ever for a capability that is
    #: ``False``. A ✗ with no explanation is the one state an admin cannot act on: the fix for
    #: "Rank Math is absent" and for "this host strips the Authorization header" are nothing
    #: alike, and only the site's own words distinguish them.
    capability_errors: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Separate from :attr:`capabilities` on purpose. An empty capability map means *both*
    #: "we looked and this credential reaches nothing" and "nobody has ever looked", and those
    #: need different screens. NULL here is the second one.
    capabilities_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: The MCP Adapter's server route, **discovered from the site's REST index, never
    #: assumed** (CLAUDE.md §12 — do not hardcode well-known paths). The namespace is
    #: per-server and configurable; ``mcp/mcp-adapter-default-server`` is merely the default
    #: one install happens to use.
    mcp_server_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Rank Math's plugin version where it is installed; NULL where it is not. AI Visibility
    #: arrived in 1.0.273, so this is what the panel reads to say "installed, but too old" —
    #: a sentence neither "installed" nor "working" can express.
    #:
    #: There is deliberately **no ``wp_version``** beside it. WordPress core does not publish
    #: its version over REST, and the question this column would have been asked ("is this site
    #: new enough for the Abilities API?") is answered honestly by whether ``wp-abilities/v1``
    #: is in the site's own REST index — which is what ``capabilities["abilities"]`` records.
    #: A version string we could not observe would be a stored fact nobody checked.
    rankmath_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID]):  # noqa: ANN206
        """This credential's client is its website's **domain's** client (#285).

        Two joins rather than one, and neither may be skipped: ``websites`` carries no
        ``company_id`` either, which is precisely why it declares a clause of its own. With no
        clause here the repository's column match would find nothing to filter on and would
        therefore filter nothing at all — handing every restricted membership, and every client
        login, the whole org's WordPress administrator credentials.

        ``websites.domain_id`` and ``domains.company_id`` are both ``NOT NULL``, so there is no
        client-less site to exempt: a row either resolves into the scope or it is out of it.
        """
        return cls.website_id.in_(
            select(_websites.c.id).where(
                _websites.c.org_id == cls.org_id,
                _websites.c.domain_id.in_(
                    select(_domains.c.id).where(
                        _domains.c.org_id == cls.org_id,
                        _domains.c.company_id.in_(scope),
                    )
                ),
            )
        )
