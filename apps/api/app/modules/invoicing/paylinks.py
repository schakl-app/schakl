"""Where a payer is sent — one answer, for every surface that offers one (epic #269).

The e-mail's button, the QR printed on the invoice, and the document's "pay online" line all
lead to the **same place**, and this module is why they cannot drift apart. It is a leaf: it
imports nothing from this module's service or router, so the renderer, the mail composer and
the payment service can all reach it without a cycle.

**The destination is always the invoice's own page here. Never a provider checkout URL.**
That is the load-bearing decision (#268 made it for the QR; it holds for every other surface
for the same reasons, and for one more):

* A live checkout URL is a **bearer credential that spends money**. Printed on paper or
  forwarded in a mail, it hands whoever picks it up a live, pre-filled payment.
* **A checkout expires and the invoice does not.** iDEAL dies in fifteen minutes, a card in
  thirty. A link that is stale before the post arrives is worse than no link, and a *reminder*
  mail sent three weeks later would carry a URL that has been dead for most of a month.
* **It is the only thing that prevents doubles.** The pay control reuses a live intent for the
  same amount (``InvoicePaymentService._reusable``) instead of opening a competing checkout.
  Mail a checkout URL and then let the client also press "pay now", and they hold two valid
  ways to pay one debt — which ends in a refund conversation. Routing every entrance through
  one screen is what makes "one open checkout per invoice" true rather than hoped for.
* **It keeps the agency in control of what the client sees.** Status, the amount actually
  outstanding after a part payment or a credit note, the PDF to download, the tenant's own
  branding. A provider's checkout page shows an amount and nothing else, and once it is
  spent — or expired — it shows an error with somebody else's logo on it.

So the provider's checkout URL exists in exactly one place: on the intent row, handed to the
payer at the moment they press the button. It never travels by mail or on paper.

**Which of our own pages, though, changed in #304, and the reasoning it replaces is worth
keeping visible.** #268 said the portal link "goes through the login #193 already
established: the right person lands on the invoice, anyone else lands on a sign-in screen".
That was true and it was answering the wrong question. Most of an agency's clients hold no
portal login at all — the portal is a licensed product the agency buys per client — so for
them the sentence read *everyone lands on a sign-in screen*, and a QR whose only outcome is a
login form for an account you do not have is #253's control that always refuses, printed on
paper and mailed out.

So :func:`invoice_pay_url` now prefers the document's **public** address
(``/invoice/<token>``, ``app/modules/invoicing/public.py``) and falls back to the portal page
for a document that has no token — a draft, or an org that switched public links off. The
security argument survives the change intact, because it was never about the login:

* The token is still a bearer credential, and it is now the *right* one. It grants exactly
  what handing somebody the paper invoice already grants — look at this one document, and pay
  what it says is owed. It does not grant a second invoice, a company record, a contact, a
  trail, or any write at all.
* It is **still not a checkout**. It does not expire, it survives a part payment, it reflects
  a credit note, it carries the tenant's branding, and it is what makes "one open checkout per
  invoice" true. Every bullet above holds word for word.
* Anyone with a portal login still gets the portal: they arrive signed in, on the same
  document, with their other invoices one click away.
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


def portal_invoice_url(base_url: str, invoice_id: object) -> str:
    """This invoice's page for somebody who is **signed in** — staff, or a portal client."""
    return f"{base_url.rstrip('/')}/invoices/{invoice_id}"


def public_invoice_url(base_url: str, token: str) -> str:
    """This invoice's public address (#304). Short on purpose.

    ``/invoice/<token>`` and not ``/invoices/public/<token>``: the whole string is encoded into
    a QR that has to survive being printed at 24mm and photographed at an angle, and every
    character is another module the camera has to resolve. Singular, so it can never collide
    with the signed-in ``/invoices/`` section — one path segment apart is a routing accident
    waiting to happen, and the two audiences must not share a prefix.
    """
    return f"{base_url.rstrip('/')}/invoice/{token}"


def invoice_pay_url(base_url: str, invoice_id: object, public_token: str | None = None) -> str:
    """Where every surface sends a payer: the public page when there is one, else the portal.

    ``base_url`` is resolved by the caller through :func:`app.core.hosts.org_base_url` — the
    one seam that knows which of an org's two valid origins is live (#291). It is passed as a
    **string** rather than an ``Org`` so the document renderer can call this from inside its
    sandbox, where resolving a host of its own would be a hardcoded domain (Golden Rule 4).

    The fallback is not a degraded mode, it is the honest answer for a document that has no
    public address: a draft being previewed, or an org that switched the feature off. Staff
    reading their own preview land on their own screen either way.
    """
    if public_token:
        return public_invoice_url(base_url, public_token)
    return portal_invoice_url(base_url, invoice_id)


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
    return invoice_pay_url(base_url, invoice.id, getattr(invoice, "public_token", None))


def mail_pay_qr(
    pay_url: str,
    *,
    transport: str,
    dark: str | None = None,
    light: str | None = None,
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

    ``dark``/``light`` come from ``render.qr_appearance`` — the *document's* template, not the
    org's branding (#305). Before that this took a ``brand_color`` and drew the org's own, so a
    template set to ``plain`` printed monochrome on paper and mailed a coloured code: two
    answers to "what does our QR look like" from one document. The colours are passed on raw,
    because ``qr_png`` applies ``readable_pair`` itself and a caller must not be able to take
    the colours without the guarantee.
    """
    from app.core.email.senders import supports_inline_images
    from app.modules.invoicing.render.qr import qr_png

    if not pay_url or not supports_inline_images(transport):
        return None
    return qr_png(
        pay_url,
        dark=dark or "#000000",
        light=light,
        logo=logo,
        logo_content_type=logo_content_type,
    )
