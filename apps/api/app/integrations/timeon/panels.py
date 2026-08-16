"""What ``timeon`` shows on a company (§6, #364/#365). Business-licensed — see LICENSE.

One panel, answering one question: **is this client's time registration in step?** Which customer
they are in Timeon, how many of their hours are paired, and whether anything about them is
waiting for a decision.

It deliberately does **not** call Timeon. It reads stored pairings, so a company page loads at
full speed and still renders when Timeon is unreachable — ``cloudflare``'s rule, and it matters
here because a company page is opened all day and a timesheet API is not on that path.

``requires_permission`` is not optional (#365): a company hub that called thirteen providers
behind one ``companies.company.read`` handed a member holding that single key the client's whole
change history. A pairing list is the same kind of thing, so it declares its key and the hub
filters *before* calling.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.integrations.timeon.models import (
    TimeonAccount,
    TimeonConflict,
    TimeonConflictStatus,
    TimeonLink,
    TimeonLinkKind,
)
from app.registry import PanelSpec


async def company_panel(ctx: RequestContext, company_id: uuid.UUID) -> dict[str, Any]:
    """This client's Timeon identity and the state of their hours there.

    **Three** statements whatever the client's size: a client with two thousand paired entries
    costs the same as one with two (docs/PERFORMANCE.md). The organisation's name rides on the
    pairing's own query rather than a fourth round trip — a company page is opened all day, and
    a panel that costs one statement per fact it prints is how a hub gets slow one module at a
    time.
    """
    row = (
        await ctx.session.execute(
            ctx.repo(TimeonLink)
            .scoped_select()
            .add_columns(TimeonAccount.organisation_name)
            .join(TimeonAccount, TimeonAccount.id == TimeonLink.account_id)
            .where(
                TimeonLink.kind == TimeonLinkKind.CUSTOMER.value,
                TimeonLink.local_id == company_id,
            )
            .limit(1)
        )
    ).first()
    customer, organisation = (row[0], row[1]) if row is not None else (None, None)
    hours = {
        str(status): int(total)
        for status, total in await ctx.session.execute(
            select(TimeonLink.status, func.count())
            .where(
                TimeonLink.org_id == ctx.org.id,
                TimeonLink.kind == TimeonLinkKind.HOUR.value,
                TimeonLink.company_id == company_id,
            )
            .group_by(TimeonLink.status)
        )
    }
    open_conflicts = int(
        await ctx.session.scalar(
            select(func.count())
            .select_from(TimeonConflict)
            .where(
                TimeonConflict.org_id == ctx.org.id,
                TimeonConflict.company_id == company_id,
                TimeonConflict.status == TimeonConflictStatus.OPEN.value,
            )
        )
        or 0
    )
    return {
        "linked": customer is not None,
        "customer_id": customer.external_id if customer else None,
        "customer_name": customer.external_name if customer else None,
        "organisation": organisation,
        "hours": hours,
        "hours_paired": sum(hours.values()),
        "open_conflicts": open_conflicts,
    }


TIMEON_PANELS: list[PanelSpec] = [
    PanelSpec(
        key="timeon.company",
        entity_type="company",
        title_key="timeon.panel.title",
        provider=company_panel,
        # Beside the hours panel rather than among the assets: what it answers is a question
        # about this client's time registration.
        position=62,
        # Whose timesheets are mirrored is the sync operator's question, and never
        # `time.entry.read`, which a client-portal login may hold at `:own` (#266).
        requires_permission="timeon.sync.run",
        # A client with nothing in Timeon folds into the "nog niets vastgelegd" strip (#364)
        # rather than drawing a card that is a heading over a negative sentence.
        empty_when=lambda data: not data.get("linked") and not data.get("hours_paired"),
    )
]
