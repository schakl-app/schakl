"""Online payment of an invoice — the provider-independent half (epic #269, issue #267).

``app.core.payments`` says what a provider can do; this says what it *means*. Everything
here is written in terms of the seam, so the words "Mollie", "Stripe" and "Adyen" appear in
this file exactly once each: in this sentence.

The one rule the whole design hangs from: **a confirmed online payment writes an ordinary
``InvoicePayment`` row.** Not a parallel ledger, not a status flag — the same row a bookkeeper
creates when they see a bank transfer land, through the same ``_settle`` that recomputes
``paid_total``, flips the status and emits ``invoice.paid``. Invoicing therefore stays the
single answer to "what has been paid", and every consumer of that answer — the dashboard, the
reminders cron, the accounting export, the company panel — needed no change at all.

Four decisions worth stating, because each has a plausible wrong version:

* **An intent is per attempt, not per invoice.** iDEAL expires in fifteen minutes and clients
  abandon checkouts; an invoice legitimately collects several. Keying on the invoice (the
  ``ExternalRef`` shape) would let a late webhook for attempt #1 settle against attempt #2.
* **We charge ``outstanding``, recomputed at creation.** Never ``total``, never a number the
  caller sent. A credited or part-paid invoice that asked for its full total is the exact bug
  ``render/context.py`` already records once for the payment block.
* **A test-mode payment settles nothing.** It flips the intent to ``paid`` and stops there.
  The whole loop — create, redirect, webhook, re-fetch, status — is observable, and the one
  step withheld is the one that would book a real invoice as paid against money that does not
  exist. An agency that leaves a test key in place gets an obviously-stuck screen instead of
  silently wrong revenue.
* **Settling takes a row lock, and a unique index backs it up.** A provider retries a webhook
  until it gets a 200 (Mollie: ten times over 26 hours), and two deliveries can be in flight
  at once. ``SELECT … FOR UPDATE`` on the intent serialises them; the partial unique index on
  ``(org_id, intent_id)`` catches anything that still races. An application-level "have we
  settled?" check alone loses this race, and losing it means charging a client twice.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.activity import ActivityService
from app.core.hosts import org_base_url
from app.core.payments import (
    PaymentAccount,
    PaymentProviderAuthError,
    PaymentProviderError,
    PaymentRequest,
    PaymentSnapshot,
    PaymentStatus,
    available_accounts,
    resolve_account,
)
from app.core.payments.tokens import mint as mint_callback_token
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.invoicing.calc import outstanding_of
from app.modules.invoicing.models import (
    Invoice,
    InvoicePayment,
    InvoicePaymentIntent,
    InvoiceStatus,
    PaymentIntentStatus,
)
from app.modules.invoicing.paylinks import portal_invoice_url, public_invoice_url
from app.modules.invoicing.public import IN_FLIGHT_STATUSES, REFRESH_MIN_INTERVAL
from app.modules.invoicing.schemas import InvoicePaymentIntentCreate
from app.modules.invoicing.service import InvoiceService, org_today

logger = logging.getLogger("schakl.invoicing.payments")

#: What ``InvoicePayment.method`` says for a row a provider wrote. One value for every
#: provider on purpose: the *provider* is on the intent, and a ledger row's method answers the
#: bookkeeper's question ("how did this arrive?"), which is "online", not "which vendor".
#: ``PaymentWrite.method`` stays a closed ``Literal`` that does not include it — nobody should
#: be able to hand-register a payment as though a provider had confirmed it.
ONLINE_METHOD = "online"

#: An intent in one of these states may still be paid, so a second "pay now" reuses it rather
#: than opening a competing checkout the client can also complete.
_REUSABLE = frozenset({PaymentIntentStatus.OPEN.value, PaymentIntentStatus.PENDING.value})


def callback_url(org: Any, provider: str, token: str) -> str:
    """Where a provider posts status changes for one of this org's payments.

    Through :func:`app.core.hosts.org_base_url` like every other generated absolute link, so a
    callback never points at a host whose edge cannot serve it. The token carries the tenant
    (``app.core.payments.tokens``) because a provider sends no hostname and no session.

    Deployment note, and the one thing an operator must get right: this path has to be
    reachable from the public internet. Behind Cloudflare Zero Trust (docs/DEPLOY.md) it needs
    a bypass rule, or every payment is collected and none is ever booked.
    """
    return f"{org_base_url(org)}/api/v1/invoicing/payments/webhook/{provider}/{token}"


def _snapshot_status(status: PaymentStatus) -> str:
    """The seam's status as this module stores it. One-to-one by construction — the enums are
    the same vocabulary — but converted explicitly so a provider adding a state cannot slip an
    unknown string into a column the UI switches on."""
    return PaymentIntentStatus(status.value).value


class InvoicePaymentService:
    """Starting, reading and reconciling online payments for invoices."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.invoices = InvoiceService(ctx)
        self.intents = ctx.repo(InvoicePaymentIntent)
        self.activity = ActivityService(ctx)

    # --- accounts -------------------------------------------------------------- #
    async def accounts(self) -> list[PaymentAccount]:
        """Every payment credential this org has connected, across the enabled modules."""
        return await available_accounts(self.ctx.session, self.ctx.org.id)

    async def _pick_account(self, data: InvoicePaymentIntentCreate) -> PaymentAccount:
        """Resolve which credential to charge with — and refuse to guess.

        The OXXA rule (#296): nothing ever picks a credential for you when there is more than
        one. An agency mid-migration holds two, and an agency testing holds a live and a test
        key at once; charging the wrong one is not recoverable by editing a row afterwards.
        """
        candidates = await self.accounts()
        if data.provider:
            candidates = [a for a in candidates if a.provider == data.provider]
        if data.account_id is not None:
            chosen = next((a for a in candidates if a.id == data.account_id), None)
            if chosen is None:
                raise AppError("not_found", "errors.not_found", status_code=404)
            if not chosen.active:
                raise AppError(
                    "payment_account_inactive",
                    "errors.invoicing.payment_account_inactive",
                    status_code=409,
                )
            return chosen
        usable = [a for a in candidates if a.active]
        if not usable:
            raise AppError(
                "payment_no_account", "errors.invoicing.payment_no_account", status_code=409
            )
        # One tiebreak, and only one: **a live credential beats a test one.** An agency
        # integrating holds both at once — that is the whole reason the credential is a row
        # rather than a settings singleton — and "there are two, say which" is a useless answer
        # to a *client* who cannot see the list at all (#266 keeps it at `:any`). Charging a
        # real client through a test key collects nothing and settles nothing, so there is no
        # judgement being made here: one of the two is not a candidate for real money.
        #
        # Two *live* accounts stay ambiguous, deliberately. That one has no principled answer,
        # and the agency resolves it in Instellingen by switching one off.
        if len(usable) > 1:
            live = [a for a in usable if a.mode != "test"]
            if len(live) == 1:
                return live[0]
        if len(usable) > 1:
            raise AppError(
                "payment_account_ambiguous",
                "errors.invoicing.payment_account_ambiguous",
                status_code=409,
                fields={"account_id": "errors.required"},
            )
        return usable[0]

    # --- reads ----------------------------------------------------------------- #
    async def list_for(self, invoice_id: uuid.UUID) -> list[InvoicePaymentIntent]:
        """This invoice's attempts, newest first.

        Loaded *through* the invoice, so a client reading their own invoice gets their own
        attempts and a horizon-restricted member gets nothing they could not already open —
        the repository decided that before this method ran (#285's rule: never hand-build the
        scoped select).
        """
        self.ctx.require("invoicing.invoice.read")
        invoice = await self.invoices.repo.get_or_404(invoice_id)
        rows = await self.ctx.session.execute(
            self.intents.scoped_select()
            .where(InvoicePaymentIntent.invoice_id == invoice.id)
            .order_by(InvoicePaymentIntent.created_at.desc())
        )
        return list(rows.scalars())

    # --- starting a payment ---------------------------------------------------- #
    async def start(
        self,
        invoice_id: uuid.UUID,
        data: InvoicePaymentIntentCreate,
        *,
        surface: str = "portal",
    ) -> InvoicePaymentIntent:
        """Open a checkout for an invoice's outstanding balance.

        Reachable by a client through the portal (``invoicing.payment.link:own``) as well as
        by staff, which is why every narrowing rides the repository: ``get_or_404`` is the
        portal one for an external login, so a draft and another company's invoice are both
        already 404 by the time this body runs.

        ``surface`` decides which of *our* pages the provider returns the payer to, and it is
        an enum-ish string rather than a URL for one reason: a caller-supplied ``return_url``
        on a route a client can reach is an open redirect, and this one would be an open
        redirect handed to a payment provider to send someone to after they have typed their
        bank details. The two values name pages this module already owns
        (``paylinks.public_invoice_url`` / ``portal_invoice_url``), so the worst a wrong value
        can do is land somebody on the other one of ours.
        """
        self.ctx.require("invoicing.payment.link")
        invoice = await self.invoices.repo.get_or_404(invoice_id)
        if invoice.status != InvoiceStatus.OPEN.value:
            # Never a draft (nobody has been asked to pay it yet), never a cancelled or
            # already-paid one. `paid` is included in that list on purpose: a rounding-error
            # overpayment is a conversation, not a second checkout.
            raise AppError(
                "payment_not_payable",
                "errors.invoicing.payment_not_payable",
                status_code=409,
            )
        amount = outstanding_of(invoice)
        if amount <= 0:
            raise AppError(
                "payment_not_payable",
                "errors.invoicing.payment_not_payable",
                status_code=409,
            )

        account = await self._pick_account(data)
        existing = await self._reusable(invoice.id, account, amount)
        if existing is not None:
            return existing

        # Everything the provider needs is built *before* the connection goes back to the
        # pool: nothing below the `release_db` boundary may touch the session.
        intent_id = uuid.uuid4()
        request = PaymentRequest(
            amount=amount,
            currency=invoice.currency,
            description=self._description(invoice),
            return_url=self._return_url(invoice, surface),
            webhook_url=callback_url(
                self.ctx.org,
                account.provider,
                mint_callback_token(self.ctx.org.id, account.id, account.webhook_secret),
            ),
            reference=str(intent_id),
            locale=invoice.locale,
            metadata={
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.number or "",
                "intent_id": str(intent_id),
            },
        )
        client = self._connect(account)
        try:
            async with self.ctx.release_db():
                snapshot = await client.create_payment(request)
        except PaymentProviderError as exc:
            # Nothing was written, so this may raise: there is no partial state to lose. The
            # provider's own words go nowhere near the envelope (§9); they are logged for an
            # operator and the caller gets a key.
            logger.warning("payment create failed (%s): %s", account.provider, exc)
            raise self._translate(exc) from exc

        intent = await self.intents.create(
            id=intent_id,
            invoice_id=invoice.id,
            provider=account.provider,
            account_id=account.id,
            external_id=snapshot.reference,
            status=_snapshot_status(snapshot.status),
            amount=amount,
            currency=invoice.currency,
            mode=snapshot.mode or account.mode,
            checkout_url=snapshot.checkout_url,
            method=snapshot.method,
            synced_at=datetime.now(UTC),
            payload=snapshot.raw,
        )
        await self.activity.record(
            "invoice",
            invoice.id,
            "payment_intent_started",
            {
                "provider": account.provider,
                "amount": float(amount),
                "currency": invoice.currency,
                "mode": intent.mode,
            },
        )
        return intent

    async def _reusable(
        self, invoice_id: uuid.UUID, account: PaymentAccount, amount: Decimal
    ) -> InvoicePaymentIntent | None:
        """A live checkout for exactly this amount, if one is already open.

        Two clicks on "pay now" must not open two payments: the client would then hold two
        valid links for one debt, and paying both is a refund conversation. Matched on the
        amount as well as the account because a partial payment registered in between changes
        what is owed, and a stale link promising the old figure is worse than a new one.
        """
        rows = await self.ctx.session.execute(
            self.intents.scoped_select()
            .where(
                InvoicePaymentIntent.invoice_id == invoice_id,
                InvoicePaymentIntent.provider == account.provider,
                InvoicePaymentIntent.account_id == account.id,
                InvoicePaymentIntent.status.in_(_REUSABLE),
                InvoicePaymentIntent.amount == amount,
                InvoicePaymentIntent.checkout_url.is_not(None),
            )
            .order_by(InvoicePaymentIntent.created_at.desc())
            .limit(1)
        )
        return rows.scalars().first()

    def _return_url(self, invoice: Invoice, surface: str) -> str:
        """Where the provider drops the payer afterwards — the page they started from.

        Sending a public payer to the portal would end a successful payment on a sign-in screen
        for an account they do not have, which is the failure #304 exists to remove; sending a
        signed-in one to the public page would silently downgrade them out of their own session.

        The URL carries ``?return=1``. It is not decoration and it is not state: it is how the
        landing page knows it is a *return* and may spend one provider call asking whether the
        money arrived (``public.PublicInvoiceService.refresh`` / the invoice route's own
        refresh). Without it every ordinary view of an invoice with a stale open intent would
        do the same, which is a poll nobody asked for on a page nobody is waiting on.
        """
        base = org_base_url(self.ctx.org)
        if surface == "public" and invoice.public_token:
            return f"{public_invoice_url(base, invoice.public_token)}?return=1"
        return f"{portal_invoice_url(base, invoice.id)}?return=1"

    def _description(self, invoice: Invoice) -> str:
        """What the payer sees on their statement. Providers truncate hard (Mollie at 255,
        card networks far shorter), so it leads with the number a client can match."""
        brand = self.ctx.org.name
        number = invoice.number or str(invoice.id)[:8]
        # A plain hyphen: a bank statement is the one surface whose typography is not ours,
        # and an em dash lands there as "?" or as a mojibake pair on plenty of them.
        return f"{number} - {brand}"[:255]

    async def refresh_pending(
        self, invoice_id: uuid.UUID
    ) -> tuple[bool, InvoicePaymentIntent | None]:
        """Ask the provider about this invoice's in-flight attempts. "Did we ask", plus the latest.

        The answer to "does a payer coming back from a checkout see their money?" (#304). They
        did not, and the reason is not a bug anywhere in the settle path: a provider's webhook
        is **asynchronous and routinely later than the browser redirect** — Mollie documents
        exactly this and makes no ordering promise — so the return landed on a page whose SSR
        read had happened before anything told us. The invoice said *open* to the person who
        had just paid it, and the only cure on the screen was ``sync``, which is ``:any`` and
        therefore staff-only: the one human who could prove the payment was the one human
        without a button.

        So the *landing* asks, once, and the ordinary reconcile cron stays the safety net
        underneath. Two bounds keep it honest, and both matter because the public sibling of
        this method (``public.PublicInvoiceService.refresh``) is reachable with no session:

        * **Only non-final attempts.** A final status is final; asking again can only return
          what is already stored.
        * **Throttled on ``refreshed_at``** (:data:`REFRESH_MIN_INTERVAL`) — a column this
          method alone writes. At most one outbound call per attempt per interval, whatever
          the caller does, so a page polling every two seconds costs one provider call and a
          public endpoint cannot be turned into an amplifier pointed at somebody else's API.

          The clock is its own, and that is not tidiness. Throttling on ``synced_at`` — the
          obvious reuse — meant the *creation* of the intent counted as a poll, so a payer
          who came back inside the window was told there was nothing to ask about the payment
          they had just made. The one case the feature exists for was the one case it skipped.
          For the same reason a webhook and the reconcile cron leave it alone: neither is a
          caller whose rate needs bounding, and a well-timed callback would otherwise suppress
          the payer's own first press.

        ``:own`` on purpose, unlike ``sync``: this is not the operator's repair action, it is
        the payer finding out what happened to their own money. It spends nothing when there is
        nothing in flight.
        """
        self.ctx.require("invoicing.payment.link")
        intents = await self.list_for(invoice_id)
        cutoff = datetime.now(UTC) - REFRESH_MIN_INTERVAL
        asked = False
        for intent in intents:
            if intent.status not in IN_FLIGHT_STATUSES:
                continue
            if intent.refreshed_at is not None and intent.refreshed_at > cutoff:
                continue
            # Stamped *before* the call, so a provider that hangs cannot be hammered by a page
            # whose polls all start before any of them finish.
            await self.intents.update(intent, refreshed_at=datetime.now(UTC))
            await self.reconcile(intent)
            asked = True
        latest = intents[0] if intents else None
        return asked, latest

    # --- reconciling ----------------------------------------------------------- #
    async def reconcile(
        self, intent: InvoicePaymentIntent, account: PaymentAccount | None = None
    ) -> InvoicePaymentIntent:
        """Ask the provider what actually happened, and act on the answer.

        This is the **only** place a payment's status is believed, and it believes it because
        the answer came back over an authenticated call with the tenant's own credential. A
        webhook body reaches this function as nothing more than the id it should look up.
        """
        if account is None and intent.account_id is not None:
            account = await resolve_account(
                self.ctx.session, self.ctx.org.id, intent.provider, intent.account_id
            )
        if account is None:
            await self._note_error(intent, "credential unavailable")
            return intent

        client = self._connect(account)
        reference = intent.external_id
        try:
            async with self.ctx.release_db():
                snapshot = await client.fetch_payment(reference)
        except PaymentProviderError as exc:
            # **Recorded, not raised.** The caller is a webhook or a cron; raising would roll
            # back the very row that says we tried, and a provider outage would then leave no
            # trace at all. The reconcile cron picks it up again.
            await self._note_error(intent, str(exc))
            return intent

        if snapshot is None:
            await self._note_error(intent, "provider does not know this payment")
            return intent
        return await self.apply(intent, snapshot)

    async def apply(
        self, intent: InvoicePaymentIntent, snapshot: PaymentSnapshot
    ) -> InvoicePaymentIntent:
        """Fold an authenticated snapshot into the intent, settling it when it says paid.

        Split from :meth:`reconcile` so the settle logic is testable without a transport, and
        so a future provider that *can* be trusted to push a signed, complete entity has one
        obvious place to hand it over.
        """
        # The lock is the idempotency. Two webhook deliveries land as two transactions; the
        # second waits here and then reads a ``settled_at`` the first has already written.
        #
        # ``populate_existing`` is what makes that true, and leaving it off is a silent,
        # load-bearing bug: this row is already in the session's identity map (the caller
        # loaded it to find us), and by default SQLAlchemy returns that **stale in-memory
        # object** rather than the freshly locked row. The lock would then do its job at the
        # database and the code would read the pre-lock value anyway — a second
        # ``InvoicePayment`` for one payment, caught only by the unique index, as a 500 on a
        # webhook the provider retries for a day.
        locked = await self.ctx.session.scalar(
            self.intents.scoped_select()
            .where(InvoicePaymentIntent.id == intent.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        intent = locked or intent

        values: dict[str, Any] = {
            "status": _snapshot_status(snapshot.status),
            "synced_at": datetime.now(UTC),
            "payload": snapshot.raw or intent.payload,
        }
        if snapshot.method:
            values["method"] = snapshot.method
        if snapshot.mode:
            values["mode"] = snapshot.mode
        # A final payment is not payable, so the link is cleared — that is what stops the UI
        # offering a checkout that answers "this payment has expired". While it is *not* final,
        # a snapshot without a link is a provider that did not repeat itself, never a
        # retraction: overwriting with ``None`` there would blank a live checkout the client is
        # standing in front of, and take the "reuse the open intent" guard down with it.
        if snapshot.status.final:
            values["checkout_url"] = None
        elif snapshot.checkout_url:
            values["checkout_url"] = snapshot.checkout_url
        if snapshot.detail:
            values["last_error"] = snapshot.detail[:500]
        elif snapshot.status is PaymentStatus.PAID:
            values["last_error"] = None
        intent = await self.intents.update(intent, **values)

        if not snapshot.status.settled or intent.settled_at is not None:
            return intent
        if intent.mode == "test":
            # Deliberate dead end (see the module docstring): the loop is fully observable and
            # the ledger stays clean. `settled_at` is left NULL, which is exactly what the
            # screen reads to say "testbetaling — niet geboekt".
            return intent
        return await self._settle(intent, snapshot)

    async def _settle(
        self, intent: InvoicePaymentIntent, snapshot: PaymentSnapshot
    ) -> InvoicePaymentIntent:
        """Write the ledger row for a confirmed payment, through the ordinary payment path."""
        invoice = await self.ctx.session.scalar(
            select(Invoice).where(
                Invoice.org_id == self.ctx.org.id, Invoice.id == intent.invoice_id
            )
        )
        if invoice is None:  # pragma: no cover — the FK cascades, so only a race gets here
            await self._note_error(intent, "invoice is gone")
            return intent
        if invoice.status not in (InvoiceStatus.OPEN.value, InvoiceStatus.PAID.value):
            # The invoice was cancelled between checkout and settlement. The client's money
            # still moved, so this is reported and retryable, never a 409 thrown at a provider
            # that would simply keep retrying — and never silently dropped.
            await self._note_error(intent, "invoice is no longer open")
            return intent

        # Mollie's `paidAt` is an instant; a ledger row's `paid_on` is a calendar day, and
        # which day depends on the tenant's clock, not the server's (§8).
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        paid_on = (
            snapshot.paid_at.astimezone(zone).date()
            if snapshot.paid_at is not None
            else await org_today(self.ctx)
        )
        amount = snapshot.amount if snapshot.amount is not None else intent.amount
        await self.ctx.repo(InvoicePayment).create(
            invoice_id=invoice.id,
            paid_on=paid_on,
            amount=amount,
            method=ONLINE_METHOD,
            note=f"{intent.provider}:{intent.external_id}"[:255],
            intent_id=intent.id,
        )
        intent = await self.intents.update(
            intent, settled_at=datetime.now(UTC), last_error=None
        )
        await self.activity.record(
            "invoice",
            invoice.id,
            "payment_intent_settled",
            {
                "provider": intent.provider,
                "amount": float(amount),
                "currency": intent.currency,
                "method": snapshot.method or "",
            },
        )
        # The one call that makes this an ordinary payment: recompute `paid_total`, flip the
        # status, emit `invoice.paid`. Nothing downstream knows a provider was involved.
        await self.invoices._settle(invoice)  # noqa: SLF001 — same module, one settle path
        return intent

    async def _note_error(self, intent: InvoicePaymentIntent, message: str) -> None:
        """Record why this attempt did not complete, without failing the caller.

        The text is the provider's or ours and is untranslatable either way, so it lives on
        the row where a human reads it (§9). ``settled_at`` stays NULL, which is what the
        reconcile cron and the screen both key off.
        """
        await self.intents.update(
            intent, last_error=message[:500], synced_at=datetime.now(UTC)
        )
        logger.info(
            "payment intent %s (%s) unsettled: %s", intent.id, intent.provider, message
        )

    # --- provider plumbing ------------------------------------------------------ #
    def _connect(self, account: PaymentAccount):  # noqa: ANN202 — the seam's Protocol
        try:
            return account.connect()
        except ValueError as exc:
            # A rotated SCHAKL_ENCRYPTION_KEY leaves an unreadable credential. Say so plainly:
            # the fix is re-entering it, not retrying.
            raise AppError(
                "payment_credential_unreadable",
                "errors.invoicing.payment_credential_unreadable",
                status_code=409,
            ) from exc

    def _translate(self, exc: PaymentProviderError) -> AppError:
        """A provider failure → the standard envelope (§9: ``message`` is an i18n key).

        Three outcomes, because they need three different buttons: the credential is wrong
        (only the tenant can fix it), we could not reach them (try again), or they refused
        this particular request (read the row's ``last_error``).
        """
        if isinstance(exc, PaymentProviderAuthError):
            return AppError(
                "payment_credential_rejected",
                "errors.invoicing.payment_credential_rejected",
                status_code=409,
            )
        if exc.http_status is None:
            return AppError(
                "payment_provider_unreachable",
                "errors.invoicing.payment_provider_unreachable",
                status_code=502,
            )
        return AppError(
            "payment_provider_failed",
            "errors.invoicing.payment_provider_failed",
            status_code=502,
        )


# --------------------------------------------------------------------------- #
# The callback
# --------------------------------------------------------------------------- #
async def handle_webhook(
    provider_key: str, token: str, body: bytes, headers: Mapping[str, str]
) -> int:
    """Process one provider callback and return the HTTP status to answer with.

    Split out of the router so the security order is written once, in a function that can be
    tested without a transport. Read it as five gates, in this order and no other:

    1. **The token names the tenant.** No hostname, no session, no unscoped lookup — the org
       comes out of a URL we minted (``app.core.payments.tokens``).
    2. **The RLS GUC is bound before anything is read.** Every read below is org-scoped and
       fails closed, which is what makes step 3 safe to run against attacker-chosen ids.
    3. **The secret is compared in constant time**, and a mismatch is a bare 404 — never 401
       or 403, which would confirm that the account exists.
    4. **The provider gets its optional signature check**, now that the credential is in hand.
    5. **The body's ids are looked up, and nothing else in it is read.** Status, amount and
       method all come from an authenticated re-fetch. This is Mollie's stated model and it is
       the right one for every provider: a signature proves who sent a message, not that the
       message is still true.

    An id this tenant does not know answers **200**, deliberately: a provider must not be able
    to learn which references exist here by watching status codes, and Mollie documents exactly
    this. A failure we might recover from answers 503, so the provider's own retry schedule —
    ten attempts over 26 hours — becomes the recovery mechanism rather than something merely
    tolerated.
    """
    from app.core.models import Org, OrgStatus
    from app.core.payments import get_payment_provider
    from app.core.payments.tokens import matches, parse
    from app.db import async_session_maker, set_current_org

    parsed = parse(token)
    if parsed is None:
        return 404
    try:
        provider_cls = get_payment_provider(provider_key)
    except LookupError:
        return 404

    async with async_session_maker() as session:
        org = await session.get(Org, parsed.org_id)
        if org is None or org.status != OrgStatus.ACTIVE.value:
            return 404
        await set_current_org(session, org.id)
        account = await resolve_account(session, org.id, provider_key, parsed.account_id)
        if account is None or not matches(account.webhook_secret, parsed.secret):
            return 404
        try:
            client = account.connect()
        except ValueError:
            # An unreadable credential is our configuration problem, not the provider's. 503
            # keeps the callback in their retry queue while an operator re-enters the key.
            logger.error("payment callback: credential unreadable for org %s", org.slug)
            return 503
        if not client.verify_webhook(body, headers):
            return 404

        references = provider_cls.references_in_webhook(body, headers)
        if not references:
            return 200

        from app.core.jobs import system_context

        ctx = system_context(org, session)
        service = InvoicePaymentService(ctx)
        try:
            for reference in references:
                intent = await session.scalar(
                    service.intents.scoped_select().where(
                        InvoicePaymentIntent.provider == provider_key,
                        InvoicePaymentIntent.external_id == reference,
                    )
                )
                if intent is None:
                    continue
                await service.reconcile(intent, account)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("payment callback failed for org %s", org.slug)
            return 503
        return 200
