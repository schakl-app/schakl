"""``Website`` — an optional 0/1 child of a domain (issue #94, part of #87).

A domain may have one website, enabled per domain. It records whether it lives at the root ``@``
or ``www``, its technical owner (a :mod:`~app.core.party`, the agency by default), and the
``hosting`` it points at. ``uptime_enabled`` is a toggle the uptime webhook acts on later.
Customizable (§13), org-scoped and RLS-forced (§5). ``UniqueConstraint(org_id, domain_id)`` is
what makes it *at most one* website per domain.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.party import party_id_column, party_type_column
from app.db import Base


class Website(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    Base,
):
    __tablename__ = "websites"
    __entity_type__ = "website"  # registers as customizable

    __table_args__ = (
        UniqueConstraint("org_id", "domain_id", name="uq_websites_domain"),
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

    technical_owner_party_type: Mapped[str | None] = party_type_column()
    technical_owner_party_id: Mapped[uuid.UUID | None] = party_id_column()

    hosting_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("hosting.id", ondelete="SET NULL"), nullable=True
    )
    # The uptime webhook (a later automation slice) acts on this flag.
    uptime_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
