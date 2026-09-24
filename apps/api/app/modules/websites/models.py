"""``Website`` — a site at an address under one of the tenant's domains (issue #94, part of #87).

A domain may carry **several** websites, told apart by where they answer: the apex (``@``) or
``www``, and the **path** under that host (``""`` for the root). That is what lets an agency
record the dev installs it runs under one domain of its own — ``breik.dev/briellaerd`` and
``breik.dev/nova`` are two records, two WordPress credentials, two monitors — where the old
``UniqueConstraint(org_id, domain_id)`` allowed exactly one site per domain and the domain
normaliser stripped the path off anything typed into the picker.

Whose the site is follows the same two-level shape: the parent domain's client by default, and
``company_override_id`` where the site names a client of its own (``NULL`` = *follow the
domain*, the platform's usual reading — and the service stores ``NULL`` for an override that
merely restates the domain's client, so a form re-posting the default never freezes it). A dev
site on the agency's own domain is the **client's** site, on the client's hub and inside the
client's horizon, which is the whole reason the column exists.

It also records its technical owner (a :mod:`~app.core.party`, the agency by default) and the
``hosting`` it points at; ``uptime_enabled`` is a toggle the uptime webhook acts on. Customizable
(§13), org-scoped and RLS-forced (§5). ``uq_websites_address`` is what makes an address name
exactly one site.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    and_,
    column,
    or_,
    select,
    table,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.party import party_id_column, party_type_column
from app.db import Base

# `domains` belongs to another module; reference it as a bare table by name rather than importing
# its model (CLAUDE.md §6) — the same bridge the service already uses for the domain's name.
_domains = table("domains", column("id"), column("org_id"), column("company_id"))


class Website(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "websites"
    __entity_type__ = "website"  # customizable (§13) + auditable (§16)
    #: Reading the trail needs this module's own read key — core holds no module list (§16).
    __activity_read_permission__ = "websites.website.read"

    __table_args__ = (
        # One site per address: the same host and path may not be recorded twice. ``path`` is
        # ``NOT NULL`` with ``""`` for the root precisely so this constraint holds — two NULLs
        # are distinct inside a unique constraint (``dim_key``'s lesson), and two root sites on
        # one host would have slipped through.
        UniqueConstraint(
            "org_id", "domain_id", "root", "path", name="uq_websites_address"
        ),
        Index("ix_websites_custom", "custom", postgresql_using="gin"),
    )

    domain_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # True ⇒ the root apex (``@``); False ⇒ the ``www`` host.
    root: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The path under the host, ``""`` for the root and ``/segment[/…]`` otherwise — always in
    #: the form :func:`app.core.webaddress.normalize_path` produces, never as typed.
    path: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")

    #: The client this site belongs to where it is **not** the domain's — a client's dev site
    #: on the agency's own domain. ``NULL`` follows the domain, which is every ordinary site.
    company_override_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    technical_owner_party_type: Mapped[str | None] = party_type_column()
    technical_owner_party_id: Mapped[uuid.UUID | None] = party_id_column()

    hosting_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("hosting.id", ondelete="SET NULL"), nullable=True
    )

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID]):  # noqa: ANN206
        """A website's client is the one it names, else its **domain's** (#285).

        Stated as a clause because the column-matched horizon cannot say "this column, else
        that table's": with nothing declared here the repository found no ``company_id`` to
        filter on and did nothing at all, so every restricted membership — and every client
        login — read the whole org's websites. ``domain_id`` and ``domains.company_id`` are
        both ``NOT NULL``, so there is no company-less website to exempt; an override outside
        the scope hides the site even where the domain would have shown it, because the
        override *is* the answer to whose it is.
        """
        return or_(
            cls.company_override_id.in_(scope),
            and_(
                cls.company_override_id.is_(None),
                cls.domain_id.in_(
                    select(_domains.c.id).where(
                        _domains.c.org_id == cls.org_id, _domains.c.company_id.in_(scope)
                    )
                ),
            ),
        )

    # The uptime webhook (a later automation slice) acts on this flag.
    uptime_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
