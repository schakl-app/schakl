"""Where a payer is sent — one answer, for every surface that offers one (epic #269).

The e-mail's button, the QR printed on the invoice, and the document's "pay online" line all
lead to the **same place**, and this module is why they cannot drift apart. It is a leaf: it
imports nothing from this module's service or router, so the renderer, the mail composer and
the payment service can all reach it without a cycle.

**The destination is always the invoice's own page in the client portal. Never a provider
checkout URL.** That is the load-bearing decision (#268 made it for the QR; it holds for every
other surface for the same reasons, and for one more):

* A live checkout URL is a **bearer credential**. Printed on paper or forwarded in a mail, it
  hands whoever picks it up the ability to look at — and settle — somebody else's bill. The
  portal link goes through the login #193 already established: the right person lands on the
  invoice, anyone else lands on a sign-in screen.
* **A checkout expires and the invoice does not.** iDEAL dies in fifteen minutes, a card in
  thirty. A link that is stale before the post arrives is worse than no link, and a *reminder*
  mail sent three weeks later would carry a URL that has been dead for most of a month.
* **It is the only thing that prevents doubles.** The portal's pay control reuses a live
  intent for the same amount (``InvoicePaymentService._reusable``) instead of opening a
  competing checkout. Mail a checkout URL and then let the client also press "pay now", and
  they hold two valid ways to pay one debt — which ends in a refund conversation. Routing
  every entrance through one screen is what makes "one open checkout per invoice" true rather
  than hoped for.
* **It keeps the agency in control of what the client sees.** Status, the amount actually
  outstanding after a part payment or a credit note, the PDF to download, the tenant's own
  branding. A provider's checkout page shows an amount and nothing else, and once it is
  spent — or expired — it shows an error with somebody else's logo on it.

So the provider's checkout URL exists in exactly one place: on the intent row, handed to the
payer by the portal at the moment they press the button. It never travels by mail or on paper.
"""

from __future__ import annotations

from typing import Any

from app.modules.invoicing.calc import outstanding_of


def is_collectable(invoice: Any) -> bool:
    """Is there money to collect on this document, right now?

    Three conditions, and they are the payment card's and the QR's as well — stated once here
    so a mail, a printed invoice and a portal button can never disagree about whether an
    invoice is payable:

    * **``open``.** Not a draft (nobody has been asked for it yet), not cancelled, not already
      paid. A rounding-error overpayment is a conversation, not a second checkout.
    * **Something outstanding**, after payments *and* credit notes (``outstanding_of``). An
      invoice a credit note wrote off owes nothing, and asking for it back is the bug
      ``render/context.py`` already records once.
    * **Not a credit note.** That is money going the other way.
    """
    if getattr(invoice, "kind", "invoice") == "credit_note":
        return False
    if getattr(invoice, "status", None) != "open":
        return False
    return outstanding_of(invoice) > 0


def invoice_pay_url(base_url: str, invoice_id: object) -> str:
    """This invoice's page in the client portal, on the tenant's own canonical host.

    ``base_url`` is resolved by the caller through :func:`app.core.hosts.org_base_url` — the
    one seam that knows which of an org's two valid origins is live (#291). It is passed as a
    **string** rather than an ``Org`` so the document renderer can call this from inside its
    sandbox, where resolving a host of its own would be a hardcoded domain (Golden Rule 4).
    """
    return f"{base_url.rstrip('/')}/invoices/{invoice_id}"


def mail_pay_url(invoice: Any, base_url: str, *, provider_connected: bool) -> str:
    """The link an invoice **mail** puts behind its pay button, or ``""`` for no button at all.

    Deliberately stricter than the *document's* link, and the difference is not an oversight.
    A printed invoice's QR is worth drawing even with no provider connected: it opens the live
    document, where the client reads the status and downloads the PDF, and the caption says so
    ("Scan om deze factuur te bekijken"). A **mail** has already reached them, so that is no
    longer worth a button — and one labelled *"Nu betalen"* leading to a page with nothing to
    press is a control that refuses (#253), with nothing else it could usefully say instead.

    So the mail's button appears exactly when a payment can actually be started, and otherwise
    the mail is precisely the mail an instance without payments sends today.
    """
    if not provider_connected or not is_collectable(invoice):
        return ""
    return invoice_pay_url(base_url, invoice.id)


def mail_pay_qr(
    pay_url: str,
    *,
    transport: str,
    brand_color: str | None = None,
    logo: bytes | None = None,
    logo_content_type: str | None = None,
) -> bytes | None:
    """The QR to embed in a mail body, or ``None`` when it must not be embedded at all.

    The QR answers the one case a *link* cannot: the client is reading the mail on a laptop and
    wants to pay with the banking app on their phone. Same destination as the button above it —
    a link and a code are two ways to walk through one door, not two doors.

    Two reasons this returns ``None``, and both are refusals rather than failures:

    * **There is nothing to pay** (``pay_url`` empty) — the same rule as the button.
    * **This transport cannot carry an inline image.** Brevo's API has no Content-ID mechanism
      at all (``docs/EMAIL.md``), and a mail composed with ``<img src="cid:…">`` for a transport
      that drops the part renders a broken-image box in the client's inbox. Asking *before*
      composing is the whole point of ``supports_inline_images``: only the composer can choose
      the fallback, and here the fallback is excellent — the pay button, which every client
      draws.

    Never raises: an unreadable logo degrades to a plain code inside ``qr_png``, and a mail
    must never fail to leave over its decoration.
    """
    from app.core.email.senders import supports_inline_images
    from app.modules.invoicing.render.qr import qr_png, readable_dark

    if not pay_url or not supports_inline_images(transport):
        return None
    return qr_png(
        pay_url,
        dark=readable_dark(brand_color),
        logo=logo,
        logo_content_type=logo_content_type,
    )
