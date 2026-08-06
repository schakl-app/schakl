"""The invoice a client can open without a login — one document, named by a token (#304).

Everything an agency mails or prints already assumes this: an invoice carries an IBAN and a
number, and whoever holds the paper can pay it. What they could *not* do until now was look at
it — the QR (#268) and the pay-online line (epic #269) both led to the client portal, and the
majority of an agency's clients hold no portal login at all. So the code on the paper opened a
sign-in screen for a person with no account, which is #253's broken control printed on paper.

This module is the other end of that link. Read it as four decisions.

**1. The token is the authentication, and it authenticates exactly one row.**
``invoices.public_token`` is ``secrets.token_urlsafe(32)`` — 256 bits, minted at issue and
rotatable — and the lookup is ``org_id = :oid AND public_token = :token`` with RLS already
bound. There is no id path: a caller cannot name an invoice, only present a token, so the
usual "can I see *that* one?" question never arises because there is no way to ask it.

**2. The session it builds is a client-portal session for one company, not a system one.**
The obvious shortcut is ``jobs.system_context`` — the webhook already uses it — and it is
wrong here: that context holds ``*`` and would make every narrowing in this file a matter of
this file remembering. Instead :func:`_reader_context` builds a ``RequestContext`` whose
``company_scope`` is the single company the token's invoice belongs to and whose ``is_portal``
is ``True``. Both are the machinery §15 and #266 already built and already test: the company
horizon fences the rows, ``Invoice.__portal_horizon_clause__`` hides drafts, and
``_PortalDocumentRepository`` applies both on every path — ``get_or_404``, ``scoped_select``,
``scoped_count_select`` alike. The permission set holds two keys at ``:own`` and nothing else,
so the seller's bank details, the price list, the template library and the unbilled backlog —
all ``:any`` since #266 — are refused by the same dependency that refuses them to a client.

The result is a reader that is, by construction, **no more powerful than a portal login
scoped to one company**, minus the ability to log in. That is the containment: not a list of
things this file remembers not to do.

**3. What it may do is read the document and start a payment for what the document owes.**
No amount is accepted, no id is accepted, nothing else is writable. The one extra verb is
:meth:`PublicInvoiceService.refresh` — re-asking the provider about an attempt already in
flight — which exists because a payer coming back from a checkout must see their own money
arrive without pressing reload (#304), and which is throttled so a public POST cannot be
turned into an outbound-call amplifier.

**4. The switch is retroactive.** ``invoicing_settings.public_invoice_links`` is checked
*before* the token is compared, so unticking the box in Instellingen stops every link that has
already been printed — which is the only version of an off switch worth having for a
credential that lives on paper.

What the token is **not**: a login. It grants no navigation, no other document, no company
record, no contact, no trail. ``docs/INVOICING.md`` §"De publieke factuurlink" carries the
threat model in full, including why a 256-bit path segment is safe where a 128-bit UUID would
have been the wrong shape.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import select

from app.core.auth.models import User
from app.core.models import Org, OrgStatus
from app.core.permissions.permset import PermissionSet
from app.core.tenancy import RequestContext, request_hostname, resolve_org
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.modules.invoicing.calc import outstanding_of
from app.modules.invoicing.models import (
    Invoice,
    InvoicePaymentIntent,
    InvoiceStatus,
    InvoicingSettings,
    PaymentIntentStatus,
)

#: 32 bytes → a 43-character URL-safe string. The size is chosen against *guessing*, not
#: against a lucky collision: an attacker who could try ten thousand tokens a second for a
#: century would cover about 2⁻¹⁹⁷ of the space. A ``uuid4`` (122 effective bits) would also
#: have been unguessable, and is still the wrong choice — a UUID in a URL reads as an
#: identifier, gets copied into tickets and spreadsheets as though it were one, and carries no
#: hint that it is a credential. A 43-character random string reads as what it is.
TOKEN_BYTES = 32

#: How stale an in-flight attempt must be before :meth:`PublicInvoiceService.refresh` will
#: spend another provider call on it. Short enough that a payer returning from a checkout gets
#: an answer on their first poll, long enough that a public endpoint cannot be used to make us
#: hammer a provider: a page that polls every two seconds costs at most one outbound call every
#: five, whatever the caller does.
REFRESH_MIN_INTERVAL = timedelta(seconds=5)

#: The states worth re-asking about. A final one is final — the provider will not change its
#: mind, so a refresh against it is a call that can only ever return what we already stored.
IN_FLIGHT_STATUSES = frozenset(
    {
        PaymentIntentStatus.OPEN.value,
        PaymentIntentStatus.PENDING.value,
        PaymentIntentStatus.AUTHORIZED.value,
    }
)

#: Exactly what the reader may do, and it is a *client's* grant rather than a special one.
#: ``:own`` is load-bearing: since #266 the module's org-wide surfaces (the seller's bank
#: details, the price list, the template library, the unbilled backlog) declare ``:any``, so
#: this set cannot reach them even if a future route forgets to think about it.
_READER_PERMISSIONS = ("invoicing.invoice.read:own", "invoicing.payment.link:own")


def new_token() -> str:
    """A fresh public token. URL-safe, so it survives being a path segment untouched."""
    return secrets.token_urlsafe(TOKEN_BYTES)


async def ensure_public_token(ctx: RequestContext, invoice: Invoice) -> str | None:
    """This invoice's public token, minting one if it should have one and does not.

    Lazy rather than backfilled, which is a deliberate refusal: a migration that stamped a
    token onto every historical invoice would mint thousands of live bearer credentials for
    documents nobody is ever going to send again. A link comes into existence the first time
    something actually needs one — an issue, a send, a render that prints the QR.

    Returns ``None`` — and writes nothing — for a document that must not have a public address:
    a **draft** (it has no number, the client has never seen it, and #266 already hides it from
    them), and an org that has switched the feature off.
    """
    if invoice.status == InvoiceStatus.DRAFT.value:
        return None
    if invoice.public_token:
        return invoice.public_token
    if not await public_links_enabled(ctx):
        return None
    # Through the repository, not a bare ``setattr`` + ``flush``. ``updated_at`` carries an
    # ``onupdate=func.now()``, so any flush that touches the row expires it — and the next read
    # of it is a lazy load, which Pydantic then attempts while serialising the response and
    # raises ``MissingGreenlet``. ``TenantScopedRepository.update`` refreshes for exactly this
    # reason; hand-rolling the write around it broke every issue in the suite.
    await ctx.repo(Invoice).update(invoice, public_token=new_token())
    return invoice.public_token


async def public_links_enabled(ctx: RequestContext) -> bool:
    """Does this org hand out public invoice links at all?

    Read straight off the settings row rather than through ``InvoicingSettingsService``: that
    service's read declares ``invoicing.invoice.read:any`` (#266), which the reader context
    below deliberately does not hold — and asking a client's own session for permission to read
    a switch that decides whether *they* may be here would be circular.
    """
    row = await ctx.session.scalar(
        select(InvoicingSettings).where(InvoicingSettings.org_id == ctx.org.id)
    )
    return bool(row.public_invoice_links) if row is not None else False


def _reader_context(org: Org, session: Any, company_id: uuid.UUID) -> RequestContext:
    """A session-less reader, expressed entirely in the machinery that already exists.

    ``is_portal`` and ``company_scope`` are the two facts; everything else follows from them,
    because every invoicing read already routes through ``_PortalDocumentRepository`` when the
    first is true and through the company horizon when the second is set. ``is_system`` is here
    for the reason ``jobs.system_context`` documents — the user exists in no ``users`` row, so
    the activity trail must write the NULL actor its own contract defines as the system rather
    than a foreign key to somebody who is not there.
    """
    return RequestContext(
        user=User(
            id=uuid.uuid4(), email="public@localhost", hashed_password="", is_active=True
        ),
        org=org,
        session=session,
        permissions=PermissionSet.of(_READER_PERMISSIONS),
        company_scope=frozenset({company_id}),
        is_portal=True,
        is_system=True,
    )


@dataclass(frozen=True)
class PublicInvoice:
    """What a route gets: the reader's context and the one invoice it may touch."""

    ctx: RequestContext
    invoice: Invoice


async def require_public_invoice(
    request: Request, token: str
) -> AsyncGenerator[PublicInvoice, None]:
    """Resolve ``token`` to one invoice, or 404. The dependency every public route takes.

    The order is the whole security argument and matches the payment callback's
    (``payments.handle_webhook``): **the host names the tenant, RLS is bound before anything is
    read, and only then is the caller's string used at all.** A token is never looked up across
    tenants — that would be a second unscoped crossing (§5 sanctions exactly one) and it would
    answer before authenticating.

    Every refusal is the same bare ``404 errors.not_found``: an unknown token, a token belonging
    to a draft, a suspended org, a tenant with the feature switched off. Distinguishing them
    would tell an enumerator which of their guesses was close, and none of the distinctions is
    useful to a person holding a real link.
    """
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None or org.status != OrgStatus.ACTIVE.value:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await set_current_org(session, org.id)

        settings_row = await session.scalar(
            select(InvoicingSettings).where(InvoicingSettings.org_id == org.id)
        )
        if settings_row is None or not settings_row.public_invoice_links:
            raise AppError("not_found", "errors.not_found", status_code=404)

        # A short or empty token can never match, but it must not reach the index either: an
        # empty string against a partial index is a query, and a query with a caller-controlled
        # length is a shape worth refusing before it runs.
        if not token or len(token) < 20 or len(token) > 64:
            raise AppError("not_found", "errors.not_found", status_code=404)

        invoice = await session.scalar(
            select(Invoice).where(
                Invoice.org_id == org.id,
                Invoice.public_token == token,
                Invoice.status != InvoiceStatus.DRAFT.value,
            )
        )
        if invoice is None:
            raise AppError("not_found", "errors.not_found", status_code=404)

        ctx = _reader_context(org, session, invoice.company_id)
        try:
            yield PublicInvoice(ctx=ctx, invoice=invoice)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class PublicInvoiceService:
    """The three things a session-less reader may do with the document its token names."""

    def __init__(self, public: PublicInvoice) -> None:
        self.ctx = public.ctx
        self.invoice = public.invoice

    async def read(self) -> dict[str, Any]:
        """The invoice as the public page draws it — a hand-built dict, never ``InvoiceRead``.

        Spreading the staff model here would have been one line and a standing liability:
        ``InvoiceRead`` gains a field every few releases (``reminder_count``,
        ``auto_send_pending``, the custom JSONB, the activity-bearing ids), and each one would
        appear on an unauthenticated endpoint the day it was added, with nobody reviewing that
        decision because nobody made it. So the public shape is written out, and adding to it is
        an edit somebody has to justify.
        """
        from app.modules.invoicing.service import InvoiceService

        service = InvoiceService(self.ctx)
        # Through the service, not off the row we already hold: it is the path that attaches
        # lines, tax groups and payments, and it re-applies the portal repository's own
        # narrowing — so the one read that matters is done by the code that is tested to do it.
        invoice = await service.get(self.invoice.id)
        intents = await self._intents()
        latest = intents[0] if intents else None
        outstanding = outstanding_of(invoice)
        payable = (
            invoice.status == InvoiceStatus.OPEN.value
            and outstanding > 0
            and await service._payable()  # noqa: SLF001 — the memoised "is a provider connected"
        )
        return {
            "number": invoice.number or "",
            "kind": invoice.kind,
            "status": invoice.status,
            "issue_date": invoice.issue_date,
            "due_date": invoice.due_date,
            "currency": invoice.currency,
            "locale": invoice.locale,
            "total": Decimal(invoice.total),
            "paid_total": Decimal(invoice.paid_total),
            "outstanding": outstanding,
            "customer_name": (invoice.customer or {}).get("name") or "",
            # Whether a checkout can be opened at all: an active credential *and* something
            # left to collect. Derived here for the same reason `InvoiceRead.online_payment`
            # is — so the page can decide whether to draw a pay button without being allowed
            # to read which accounts the agency connected (#253: a control that always
            # refuses is a broken control).
            "payable": payable,
            # The attempt in flight, if any — the page says "we are confirming your payment"
            # off this rather than leaving a payer who has just paid looking at "open".
            "payment_status": latest.status if latest is not None else None,
            "payment_settled": latest is not None and latest.settled_at is not None,
            "payment_pending": latest is not None and latest.status in IN_FLIGHT_STATUSES,
        }

    async def _intents(self) -> list[InvoicePaymentIntent]:
        rows = await self.ctx.session.execute(
            self.ctx.repo(InvoicePaymentIntent)
            .scoped_select()
            .where(InvoicePaymentIntent.invoice_id == self.invoice.id)
            .order_by(InvoicePaymentIntent.created_at.desc())
        )
        return list(rows.scalars())

    async def start_payment(self) -> str:
        """Open a checkout and hand back the provider's URL. No body, no amount, no account.

        Every one of those absences is the point: the service charges the invoice's outstanding
        balance recomputed at creation, picks the credential itself, and reuses a live intent
        for the same amount rather than opening a competing one. A public endpoint that took an
        amount would be a public endpoint that decides what someone owes.
        """
        from app.modules.invoicing.payments import InvoicePaymentService
        from app.modules.invoicing.schemas import InvoicePaymentIntentCreate

        service = InvoicePaymentService(self.ctx)
        intent = await service.start(
            self.invoice.id, InvoicePaymentIntentCreate(), surface="public"
        )
        if not intent.checkout_url:
            # A reused intent whose checkout has since gone final has no URL left. Refusing is
            # right — sending the payer to an empty string is worse — and the page re-reads,
            # which is where they find out it has already been paid.
            raise AppError(
                "payment_not_payable",
                "errors.invoicing.payment_not_payable",
                status_code=409,
            )
        return intent.checkout_url

    async def refresh(self) -> dict[str, Any]:
        """Re-ask the provider about this invoice's in-flight attempts, and say where we stand.

        This is what makes a return from a checkout tell the truth (#304). It is deliberately
        **not** a second implementation: ``InvoicePaymentService.refresh_pending`` holds the
        bounds — non-final attempts only, throttled on ``synced_at`` — and both the signed-in
        route and this one call it. A public endpoint with its own private copy of a rate limit
        is a rate limit that drifts.
        """
        from app.modules.invoicing.payments import InvoicePaymentService
        from app.modules.invoicing.service import InvoiceService

        asked, latest = await InvoicePaymentService(self.ctx).refresh_pending(self.invoice.id)
        # Re-read rather than trusting the row we are holding: the settle path writes an
        # ``InvoicePayment`` and flips the invoice through ``InvoiceService._settle``, and the
        # one thing the payer is waiting to see is that status.
        invoice = await InvoiceService(self.ctx).get(self.invoice.id)
        return {
            "changed": asked,
            "status": latest.status if latest is not None else None,
            "settled": latest is not None and latest.settled_at is not None,
            "invoice_status": invoice.status,
        }
