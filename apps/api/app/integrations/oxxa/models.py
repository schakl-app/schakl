"""``oxxa`` models (issue #296) — the registrar credential, and what the registrar says.

Two org-scoped, RLS-forced tables (§5), shaped by the rule the Cloudflare half of #278 already
encodes: **schakl stores what it decided and, separately, what it last observed at the
provider** (CLAUDE.md §10). Here that split has an extra edge, because a domain now has *three*
different nameserver facts and conflating any two of them is a bug:

* ``Domain.nameservers`` — what **public DNS** answers, written only by the domains module's own
  periodic lookup (#92). What the world currently sees.
* ``OxxaDomain.ns_observed`` — what the **registry** has delegated, per OXXA. What the world will
  see once it propagates.
* ``OxxaDomain.ns_desired`` — what **we pushed**. What we asked for.

They disagree for entirely ordinary reasons (a delegation change takes hours to propagate), so a
reconcile *reports* the disagreement and never resolves it. Note especially that the domains
module stores ``[]`` for a *failed* public lookup, indistinguishable from "no nameservers" — so
that column can never be read as evidence of drift here.

Company horizon (#285): neither table carries ``company_id``. ``OxxaDomain``'s client is its
domain's, so it declares ``__company_horizon_clause__`` or the repository's column match would
find nothing and filter *nothing at all*. ``OxxaAccount`` is org-wide configuration with no
client of its own and stays behind its own admin-only manage permission, exactly as §15
describes for config surfaces.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
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

#: ``domains`` belongs to another module; referenced as a bare table rather than by importing
#: its model (CLAUDE.md §6) — the same bridge ``cloudflare`` and ``websites`` use.
_domains = table("domains", column("id"), column("org_id"), column("company_id"))


def _domain_scope_subquery(org_id_col, scope: frozenset[uuid.UUID]):
    """The ids of the domains inside ``scope``, correlated to this model's own ``org_id``."""
    return select(_domains.c.id).where(
        _domains.c.org_id == org_id_col, _domains.c.company_id.in_(scope)
    )


class OxxaAccountStatus(StrEnum):
    """Whether the stored credential still works. ``error`` is set by whatever found out."""

    ACTIVE = "active"
    ERROR = "error"


class NameserverPushStatus(StrEnum):
    """The last thing we learned about the delegation we asked for.

    Five values rather than a boolean, for the reason ``RedirectStatus`` has five: "we never
    pushed" (``pending``), "the registrar agrees with us" (``active``), "the registrar holds
    something else" (``drift``), "the registrar holds nothing" (``missing``) and "the registrar
    refused" (``error``) each need a different button.
    """

    PENDING = "pending"
    ACTIVE = "active"
    DRIFT = "drift"
    MISSING = "missing"
    ERROR = "error"


class OxxaAccount(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One OXXA reseller login the tenant has handed us.

    A **row, not a settings singleton**, for the reason ``cloudflare_accounts`` is one: an agency
    that absorbs another agency holds two reseller logins for a while, and a singleton would
    have made that migration a data-loss event. It is also why nothing in this module ever picks
    an account when there is more than one.

    Auditable (§16): rotating the credential that can repoint a client's nameservers is exactly
    the change an agency needs to attribute later. The password is never part of the trail —
    only the fact that it changed.
    """

    __tablename__ = "oxxa_accounts"
    __entity_type__ = "oxxa_account"
    __activity_read_permission__ = "oxxa.settings.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_oxxa_accounts_org_name"),
        Index("ix_oxxa_accounts_org_active", "org_id", "active"),
    )

    #: Tenant free text ("Breik reseller", "Overgenomen van Bureau X"). Not i18n'd: it names a
    #: thing the tenant owns, like ``providers.name``.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The OXXA login. Not a secret — it is half of an identity, shown so an admin can tell two
    #: accounts apart — but never enough to act on its own.
    api_user: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Fernet at rest (:mod:`app.core.crypto`), write-only through the API. **Never** in a
    #: response, a log line or an error: OXXA authenticates in the query string, so this value
    #: is one careless ``str(exc)`` away from the activity log (see ``client.redact``).
    api_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

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
        default=OxxaAccountStatus.ACTIVE.value,
        server_default=OxxaAccountStatus.ACTIVE.value,
    )

    #: The TLDs this credential may operate on, from ``user_tld_list``, cached because it is the
    #: authority for splitting a name into ``(sld, tld)`` and re-fetching it per domain would
    #: make a sync O(n) in requests. Empty until the first verify — and while it is empty this
    #: module refuses to guess a split rather than addressing the wrong object at the registrar.
    tld_suffixes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    #: Reseller balance at last verify. A register whose balance has run dry stops renewing
    #: domains without telling anyone, so it is worth a number on the settings screen.
    funds_available: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: OXXA's own ``status_description``, which is untranslatable and therefore cannot go in the
    #: error envelope (§9). This is where a user reads it. Redacted before it is written.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class OxxaDomain(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One domain in the OXXA register, as the registrar last described it — plus what we asked
    for.

    ``domain_id`` is **nullable on purpose**, exactly as ``CloudflareZone.domain_id`` is: a
    domain sitting in the register that no schakl record matches is not noise to be hidden, it
    is the single most valuable thing a register sync surfaces — a domain the agency is paying
    to renew and, quite possibly, not billing anyone for.
    """

    __tablename__ = "oxxa_domains"
    __table_args__ = (
        # The registrar addresses a domain as (sld, tld); one row per domain per account.
        UniqueConstraint("org_id", "account_id", "name", name="uq_oxxa_domains_org_name"),
        Index("ix_oxxa_domains_org_domain", "org_id", "domain_id"),
        Index("ix_oxxa_domains_org_expires", "org_id", "expires_on"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("oxxa_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The schakl domain this is. NULL = in the register, unmatched — listed, never hidden.
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )

    #: The full name as the registrar reports it, normalised lowercase — and the two halves the
    #: registrar is actually addressed by, stored rather than re-derived so a later change to
    #: the suffix list cannot silently retarget an existing row.
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    sld: Mapped[str] = mapped_column(String(63), nullable=False)
    tld: Mapped[str] = mapped_column(String(128), nullable=False)

    # --- observed at the registrar -------------------------------------------------------- #
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    transfer_lock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    autorenew: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: NULL means *not reported* — ``domain_list`` never carries DNSSEC, only ``domain_inf``
    #: does. Rendering NULL as "off" would tell an agency their DNSSEC is disabled when nobody
    #: has looked.
    dnssec: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Delegated at the registry. NULL = never read; ``[]`` = the registrar reported none.
    ns_observed: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: OXXA's handle for the nameserver group. The ``external_id`` of the delegation: it is how
    #: "unchanged" is told from "repointed" without comparing lists.
    nsgroup_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Contact handles by role, as reported. Shared objects at the registrar — read, never
    #: written (see ``RegistrarContact``).
    contact_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: A snapshot of the registrant behind ``contact_refs["registrant"]``, resolved on the
    #: explicit per-domain refresh. Snapshotted rather than joined live for the reason §16
    #: snapshots an actor: an answer that evaporates when the handle is deleted is not an answer.
    registrant: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    registry_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- decided here --------------------------------------------------------------------- #
    #: The delegation we asked the registrar for. NULL = we have never pushed one, which is not
    #: the same as "it matches" — most domains will sit here forever, quite correctly.
    ns_desired: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    ns_push_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=NameserverPushStatus.PENDING.value,
        server_default=NameserverPushStatus.PENDING.value,
    )
    ns_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID]):  # noqa: ANN206
        """A registrar row's client is its **domain's** (#285); an unmatched row has none.

        Without this the repository's column match finds no ``company_id`` and filters nothing
        at all, so a membership scoped to one company group would read the whole register.
        """
        return cls.domain_id.is_(None) | cls.domain_id.in_(
            _domain_scope_subquery(cls.org_id, scope)
        )
