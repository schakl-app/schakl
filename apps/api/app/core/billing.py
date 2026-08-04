"""Billing-period arithmetic — the calendar rules a recurring charge is built on.

Two modules bill on a cycle (`subscriptions` monthly/quarterly/yearly, `domains` yearly) and
a third (`invoicing`) has to offer both. §6 forbids them importing each other, so before this
existed each re-stated `add_months` and would have had to re-state the harder half too:
*which periods has this agreement reached, and which of them is it too late to bill?* That is
subtle enough that two copies would drift, and a drift here is a client billed twice or not
at all. It is pure arithmetic over dates with no module vocabulary in it, so it belongs in
core, where every module may read it.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum


class AutoInvoiceMode(StrEnum):
    """How far a recurring-billing cron takes an invoice on its own.

    A **level, not a switch**, because "automatic invoicing" means four different amounts of
    automation to four different agencies, and what separates them is how much of a mistake
    reaches the client. Each step strictly contains the one before it:

    - ``OFF`` — nothing is raised. The period stays outstanding and the invoice editor's
      picker offers it, which is the entire manual path: nothing is lost, it waits for a human.
    - ``DRAFT`` — a draft appears, numberless, and a human issues it. **The default**, and what
      every instance did before this level existed, so an upgrade changes nothing.
    - ``ISSUE`` — the draft is issued too: it takes its number, freezes its bill-to snapshot
      and starts counting towards its due date. Nobody outside the agency has seen it yet.
    - ``SEND`` — and it is e-mailed to the client, with the document attached.

    ``ISSUE`` and ``SEND`` deliberately overrule ``docs/INVOICING.md``'s original *"a human
    sends invoices"* rule (#31) — an owner decision, recorded there. They are opt-in per org
    and overridable per agreement precisely because they are the two steps a delete cannot
    undo: an issued invoice is corrected by a credit note, and a sent one has been read.

    It lives in core rather than in `invoicing` because three modules need the word:
    `subscriptions` and `domains` each store an agreement's override and put it on their
    ``due`` event, and `invoicing` resolves it against the org default. A module importing
    another module's enum is exactly what §6 forbids.
    """

    OFF = "off"
    DRAFT = "draft"
    ISSUE = "issue"
    SEND = "send"

    @property
    def issues(self) -> bool:
        return self in (AutoInvoiceMode.ISSUE, AutoInvoiceMode.SEND)

    @property
    def sends(self) -> bool:
        return self is AutoInvoiceMode.SEND


#: How many periods one agreement may offer at once. A bound, not a judgement: an agreement
#: onboarded with a long history, or one whose automation sat off for a year, would otherwise
#: hand a picker an unbounded list. Over it is *reported*, never silently cut.
MAX_OPEN_PERIODS = 24


def add_months(day: date, months: int) -> date:
    """Calendar-safe month addition: 31 Jan + 1 month = 28/29 Feb, never a ValueError."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp to the target month's length.
    next_month_start = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_month_start - date.resolution).day
    return date(year, month, min(day.day, last_day))


def period_boundaries(
    *,
    start_date: date,
    anchor: date,
    months: int,
    floor: date | None = None,
    end_date: date | None = None,
    limit: int = MAX_OPEN_PERIODS,
) -> tuple[list[date], bool]:
    """Every period boundary an agreement has reached, oldest first, and whether the cap bit.

    Boundaries are walked **backwards from ``anchor``** — the cycle's own next date — in
    ``months`` steps, because that is the grid the cron actually bills on. A cron advances
    ``next_invoice_date`` by exactly one period per fire whether or not it raised anything, so
    stepping back from where it now sits lands on precisely the boundaries it already passed,
    including the ones automation was off for. Deriving the grid from ``start_date`` instead
    is the tempting mistake: ``next_invoice_date`` is operator-settable and routinely does not
    sit on a whole number of periods from the start date, so a start-anchored grid would offer
    dates the cron will never bill and miss the ones it will.

    Three things bound the walk, and each answers a different way of being wrong:

    - **``start_date``** — a period whose start falls before the agreement began was never
      served, so it is not billable however far back the grid reaches.
    - **``floor``** — the day the record became this system's problem (its ``created_at``).
      A domain registered in 2005 and onboarded last month has *reached* twenty boundaries
      and owes none of them; #250's rule that onboarding an old record never back-bills
      history is exactly this bound.
    - **``limit``** — everything else. Two years of arrears is already a bookkeeping
      conversation rather than a picker entry, and over the cap is *reported*, never
      silently cut.

    ``anchor`` itself is offered even when it lies in the future: billing a period in advance
    is a choice an agency makes, not a mistake, so it is returned and the caller labels it.
    """
    if months <= 0:
        return [], False
    boundary = min(anchor, end_date) if end_date is not None else anchor
    out: list[date] = []
    while len(out) <= limit:
        if floor is not None and boundary < floor:
            break
        # A period that would begin before the agreement did was never served.
        if add_months(boundary, -months) < start_date:
            break
        out.append(boundary)
        boundary = add_months(boundary, -months)
    out.reverse()  # oldest first: arrears are worked through in the order they fell due
    if len(out) <= limit:
        return out, False
    # Keep the **newest**: recent unbilled periods are what someone opening a picker is
    # looking for, and the older tail is what ``truncated`` exists to admit to.
    return out[-limit:], True
