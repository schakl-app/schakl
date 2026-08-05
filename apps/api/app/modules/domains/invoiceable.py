"""Does this domain get invoiced? (#298) — the one place that question is answered.

``Domain.invoiceable`` is three-state and must never be read on its own:

===========  ==========================================================================
``TRUE``     somebody decided: bill it.
``FALSE``    somebody decided: never bill it. The client registered it themselves; we
             only run its DNS, or point it at something, and charge for neither.
``NULL``     **follow the register.** Bill it when a registrar register the agency has
             actually read holds it, and keep billing while no such register exists.
===========  ==========================================================================

The ``NULL`` rule is the whole feature: an agency's domain list mixes names it renews and
names it merely operates, and the register is the only thing that knows which is which. It is
also what makes this safe to ship into an instance that already invoices domains — with no
register connected there is nothing to follow, so every undecided domain keeps billing exactly
as it did. The moment a register *is* connected and synced, the domains it does not hold stop
drafting by themselves; that is a real change in what gets invoiced, so it is reported (the
sync's own result, the list column, the picker) rather than merely happening.

Everything here is **SQL**, not per-row Python: the cron filters hundreds of domains, the
picker one client's, and the list a page of them, all from the same clause (docs/PERFORMANCE.md).
The registers themselves are named by nobody here — :mod:`app.core.registrar.presence` composes
whatever the enabled modules registered (CLAUDE.md §6).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, and_, false, or_

from app.core.registrar import register_presences
from app.modules.domains.models import Domain

#: Why a domain resolved the way it did — the hint a screen shows beside the toggle. A hint
#: naming the wrong reason is worse than none (docs/INVOICING.md), so all three are distinct.
SOURCE_EXPLICIT = "explicit"
"""A person set the flag. The register is not consulted at all."""
SOURCE_REGISTER = "register"
"""Undecided, and a register that has been read answered — either way."""
SOURCE_DEFAULT = "default"
"""Undecided, and no register has been read. Bills, as it always did."""


def register_authority(org_id: uuid.UUID) -> ColumnElement[bool]:
    """Whether *any* register this org holds has actually been read.

    A credential is not an authority: a token stored this morning, or one scoped to DNS and
    nothing else, knows nothing about who pays for a registration. Only a register that has
    answered at least once may narrow what gets invoiced. Uncorrelated to a row, so Postgres
    evaluates it once per statement however many domains the statement touches.
    """
    sources = register_presences()
    if not sources:
        return false()
    return or_(*[source.authority(org_id) for source in sources])


def held_by_register(org_id: uuid.UUID) -> ColumnElement[bool]:
    """Whether some register holds the correlated :class:`Domain` row's registration."""
    sources = register_presences()
    if not sources:
        return false()
    return or_(*[source.holds(org_id, Domain) for source in sources])


def invoiceable_condition(org_id: uuid.UUID) -> ColumnElement[bool]:
    """The clause every billing read filters on: domains that are invoiced.

    Correlated to :class:`Domain`, so it drops into the renewal cron's ``WHERE``, the picker's
    query and the list filter unchanged — one predicate, one definition, no chance of the cron
    and the screen disagreeing about which domains bill.
    """
    if not register_presences():
        # Nothing to follow: undecided means yes, which is what every instance did before the
        # column existed. Stated as `IS NOT FALSE` rather than `IS NULL OR IS TRUE` because
        # that is exactly what it means.
        return Domain.invoiceable.is_not(False)
    return or_(
        Domain.invoiceable.is_(True),
        and_(
            Domain.invoiceable.is_(None),
            or_(~register_authority(org_id), held_by_register(org_id)),
        ),
    )


def source_of(invoiceable: bool | None, *, has_authority: bool) -> str:
    """Which of the three rules decided, for one already-resolved row."""
    if invoiceable is not None:
        return SOURCE_EXPLICIT
    return SOURCE_REGISTER if has_authority else SOURCE_DEFAULT
