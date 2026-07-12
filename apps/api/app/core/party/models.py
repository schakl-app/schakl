"""The party value type: its enum and the column pair a consuming entity stores (issue #88).

A party is a *polymorphic* reference, so ``party_id`` cannot carry a foreign key — it points at
``companies``, ``users`` or ``contacts`` depending on ``party_type`` (and at nothing for
``agency``). The columns are therefore plain, nullable, and unconstrained; existence and
tenant-scoping are enforced by :class:`~app.core.party.service.PartyService` on every write, and a
row that later disappears simply resolves to an empty label (no cascade to chase).

An entity that references several parties (a domain has a *registry* contact **and** an *email*
contact) declares one column pair per role with a distinct prefix, which is why these are column
**factories** rather than a mixin — a mixin can only contribute a column once.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class PartyType(StrEnum):
    """What a party reference resolves to. ``AGENCY`` is the default and needs no id."""

    AGENCY = "agency"
    COMPANY = "company"
    EMPLOYEE = "employee"
    CONTACT = "contact"


def party_type_column(**kwargs: object) -> Mapped[str | None]:
    """The ``<name>_party_type`` column: one of :class:`PartyType`, or NULL for "unset"."""
    return mapped_column(String(20), nullable=True, **kwargs)  # type: ignore[arg-type]


def party_id_column(**kwargs: object) -> Mapped[uuid.UUID | None]:
    """The ``<name>_party_id`` column: the referenced row's id, or NULL.

    NULL is meaningful for ``company`` (*the record's own company*) and mandatory for ``agency``;
    it is required to be present for ``employee`` and ``contact`` (enforced in the service).
    """
    return mapped_column(PGUUID(as_uuid=True), nullable=True, **kwargs)  # type: ignore[arg-type]
