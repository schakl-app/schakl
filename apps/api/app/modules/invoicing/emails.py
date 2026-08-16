"""Document e-mails (issue #207): composing in the *document's* locale, in the tenant's own
words, sending through the org's configured transport (#17).

Three mails, and they are the ones an agency's **clients** actually read: the invoice, the
quote and the payment reminder. They were also the last outgoing text on the platform nobody
could reword — #161 gave the tenant an editor for the two auth mails and stopped there — so
each one is now a customisable kind (:mod:`app.core.email.kinds`), contributed by this module
onto its descriptor exactly like a panel or a permission (§6). A missing override falls back
to the built-in catalog text, so an instance that upgrades sends precisely what it sent before.

Two delivery paths share the composition:

- **Request path** (`deliver`): the transport row is read *before* the network call and the
  send itself runs inside ``ctx.release_db()`` — an SMTP round-trip must never pin a pooled
  DB connection (docs/PERFORMANCE.md). Failures raise, so the route reports honestly.
- **Cron path** (`jobs.py`): the worker has its own pool, reads the transport once per org
  and calls the sender directly — no request, no release dance.

Composition happens *before* that split on both, because resolving an override is an ordinary
org-scoped read: it must run while the session is still ours, never inside ``release_db``.

Subjects and bodies come from the shared i18n catalogs (``app.i18n.translate``) keyed by the
document's own ``locale`` — a Dutch agency invoicing a German client mails in the document's
language, not the org's — and the tenant's override is looked up in that **same resolved
locale**, so the words and the language can never disagree.
"""

from __future__ import annotations

import html as html_lib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.core.email.branding import EmailBrand, apply_branding, load_brand, paragraphs_html
from app.core.email.kinds import EmailTemplateKind
from app.core.email.senders import EmailAttachment, OutgoingEmail, Sender, send_email
from app.core.email.service import get_row
from app.core.email.templates import build_email_content, resolve_template
from app.errors import AppError
from app.i18n import resolve_locale, translate
from app.modules.invoicing.calc import outstanding_of
from app.modules.invoicing.paylinks import invoice_pay_url

#: The customisable kinds' keys. Namespaced by the module, which is what lets a later module
#: ship its own reminder mail without colliding with this one (asserted at mount time).
INVOICE_KIND = "invoicing.invoice"
QUOTE_KIND = "invoicing.quote"
REMINDER_KIND = "invoicing.reminder"


def _fmt_date(value: Any) -> str:
    """European dd-mm-yyyy — the product's date language everywhere (docs/UX.md)."""
    return value.strftime("%d-%m-%Y") if value else ""


def _fmt_money(amount: Any, currency: str) -> str:
    return f"{currency} {amount}"


#: The inline QR's filename, and therefore its content id — SMTP2GO has no id field and
#: takes the filename as the cid, so the other transports are made to agree with it
#: (``docs/EMAIL.md``). One name for the whole platform: a message carries at most one.
QR_FILENAME = "invoice-qr.png"


def _qr_block(pay_url: str, caption: str) -> str:
    """The QR as an e-mail block: the code, wrapped in its own link, with a line under it.

    **Wrapped in the anchor**, so the code is clickable as well as scannable — a client reading
    on the device they would pay from should not have to fetch a second one. ``width``/``height``
    attributes as well as the style, because Outlook ignores CSS dimensions on images and would
    otherwise draw the raster at its natural pixel size.
    """
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 24px 0;"><tr>'
        f'<td><a href="{html_lib.escape(pay_url, quote=True)}">'
        f'<img src="cid:{QR_FILENAME}" alt="{html_lib.escape(caption)}"'
        ' width="132" height="132"'
        ' style="display:block;width:132px;height:132px;border:0;" /></a>'
        f'<p style="margin:6px 0 0 0;font-size:12px;color:#666666;">{html_lib.escape(caption)}</p>'
        "</td></tr></table>"
    )


def _customer(doc: Any, field: str) -> str:
    return (doc.customer or {}).get(field) or ""


def _customer_label(doc: Any) -> str:
    """What the covering e-mail calls the client.

    The **label**, not the legal name: the document is the legal instrument and it says
    "J. Jansen Holding B.V." on its own bill-to block, while the mail around it is written to a
    person and should open the way the agency actually addresses them. Falls back to the
    document's own name, which is what a document issued before the client label / legal-name
    split carries — and what a client with only one name carries either way.
    """
    return _customer(doc, "trade_name") or _customer(doc, "name")


def _invoice_values(
    invoice: Any, brand: str, pay_url: str = "", qr_caption: str = ""
) -> dict[str, str]:
    return {
        "number": invoice.number or "",
        "company": _customer_label(invoice),
        "contact": _customer(invoice, "attn"),
        "total": _fmt_money(invoice.total, invoice.currency),
        "date": _fmt_date(invoice.issue_date),
        "due_date": _fmt_date(invoice.due_date),
        "reference": invoice.reference or "",
        "brand": brand,
        # The pay button's destination (epic #269) — the invoice's page in the **client
        # portal**, never a provider checkout URL (``paylinks``). Empty when there is nothing
        # to collect or nothing to collect it with, and an empty ``{link}`` renders no button
        # at all rather than a dead one (``core/email/templates.branded_default_html``).
        "link": pay_url,
        # The one value that is **markup** rather than text (``branded_default_html`` skips
        # escaping it): the inline QR with its anchor, or "" when there is no code to draw —
        # nothing to pay, or a transport that cannot carry an inline image.
        "image": _qr_block(pay_url, qr_caption) if (pay_url and qr_caption) else "",
    }


def _quote_values(quote: Any, brand: str) -> dict[str, str]:
    return {
        "number": quote.number or "",
        "company": _customer_label(quote),
        "contact": _customer(quote, "attn"),
        "total": _fmt_money(quote.total, quote.currency),
        "date": _fmt_date(quote.issue_date),
        "valid_until": _fmt_date(quote.valid_until),
        "reference": quote.reference or "",
        "brand": brand,
    }


def _reminder_values(
    invoice: Any, brand: str, days_overdue: int, pay_url: str = "", qr_caption: str = ""
) -> dict[str, str]:
    return _invoice_values(invoice, brand, pay_url, qr_caption) | {
        # What is still owed after payments *and* credit notes — the figure the cron now
        # selects on, so a reminder can never name an amount the invoice no longer carries.
        "outstanding": _fmt_money(outstanding_of(invoice), invoice.currency),
        "days": str(days_overdue),
    }


async def _compose(
    session: AsyncSession,
    org_id: Any,
    kind: str,
    doc: Any,
    brand: EmailBrand,
    values: dict[str, str],
    message: str | None = None,
) -> OutgoingEmail:
    """One document mail: the tenant's template if they wrote one, the catalog text if not.

    ``message`` is the free text the sender typed in the send dialog. It leads — a covering
    note, not a footnote — and it goes into **both** parts: escaped paragraphs before the HTML
    fragment, plain text before the plaintext body. Prepending to the text alone would make the
    branded half of the mail quietly drop a sentence the client was meant to read.
    """
    locale = resolve_locale(doc.locale)
    template = await resolve_template(session, org_id, kind, locale)
    subject, text, html = build_email_content(
        kind,
        locale,
        template.subject if template else None,
        template.body_html if template else None,
        values,
        primary_color=brand.primary_color,
    )
    note = (message or "").strip()
    if note:
        text = f"{note}\n\n{text}"
        if html is not None:
            # ``paragraphs_html`` escapes what it wraps, so the sender's own words can carry
            # no markup into the fragment — the same guarantee the sanitiser gives elsewhere.
            html = paragraphs_html(note) + html
    return OutgoingEmail(to="", subject=subject, text=text, html=html)


def _with_qr(message: OutgoingEmail, pay_qr: bytes | None) -> OutgoingEmail:
    """Attach the QR as an **inline** part, so ``cid:invoice-qr.png`` in the body resolves.

    Inline rather than an ordinary attachment: a paperclipped QR sitting next to the empty box
    where it should have rendered is worse than no QR at all, which is the same reason a
    transport that cannot inline gets no ``<img>`` in the first place (``paylinks.mail_pay_qr``).
    """
    if pay_qr:
        message.attachments.append(
            EmailAttachment(
                filename=QR_FILENAME, content=pay_qr, mimetype="image/png", inline=True
            )
        )
    return message


async def compose_invoice_email(
    session: AsyncSession,
    org_id: Any,
    invoice: Any,
    brand: EmailBrand,
    message: str | None,
    pay_url: str = "",
    pay_qr: bytes | None = None,
) -> OutgoingEmail:
    """``pay_url`` and ``pay_qr`` are resolved by the **caller** (``paylinks``), not here.

    Composing stays a pure function of what it is handed, which is what lets the auto-send cron
    ask "does this org have a provider connected?" **once per org** rather than once per
    invoice — the same reason the transport is read outside this call (docs/PERFORMANCE.md).
    """
    caption = translate("invoicing.email.qr_hint", resolve_locale(invoice.locale))
    composed = await _compose(
        session, org_id, INVOICE_KIND, invoice, brand,
        _invoice_values(invoice, brand.brand_name, pay_url, caption if pay_qr else ""), message,
    )
    return _with_qr(composed, pay_qr)


async def compose_quote_email(
    session: AsyncSession, org_id: Any, quote: Any, brand: EmailBrand, message: str | None
) -> OutgoingEmail:
    return await _compose(
        session, org_id, QUOTE_KIND, quote, brand,
        _quote_values(quote, brand.brand_name), message,
    )


async def compose_reminder_email(
    session: AsyncSession,
    org_id: Any,
    invoice: Any,
    brand: EmailBrand,
    days_overdue: int,
    pay_url: str = "",
    pay_qr: bytes | None = None,
) -> OutgoingEmail:
    """The dunning mail is where the pay button earns most: the client is being chased, and the
    shortest path from "you still owe this" to the money arriving is one press."""
    caption = translate("invoicing.email.qr_hint", resolve_locale(invoice.locale))
    composed = await _compose(
        session, org_id, REMINDER_KIND, invoice, brand,
        _reminder_values(
            invoice, brand.brand_name, days_overdue, pay_url, caption if pay_qr else ""
        ),
    )
    return _with_qr(composed, pay_qr)


# --------------------------------------------------------------------------- #
# What the editor previews (Instellingen -> E-mail)
# --------------------------------------------------------------------------- #
async def _sample_values(ctx: Any, locale: str, kind: str) -> dict[str, str]:
    """Preview values from the **same fabricated document** the PDF template editor draws.

    Reusing ``sample.sample_document`` is not laziness: an admin judging the wording of an
    invoice mail and an admin judging the design of the invoice itself should be looking at one
    document, in the org's own currency, with numbers that add up.
    """
    from app.modules.invoicing.sample import sample_document
    from app.modules.invoicing.service import _org_defaults, org_today

    brand = await load_brand(ctx.session, ctx.org)
    currency, _ = await _org_defaults(ctx)
    doc, _lines, _groups = sample_document(locale, currency, await org_today(ctx))
    if kind == QUOTE_KIND:
        doc.valid_until = doc.issue_date + timedelta(days=30)
        return _quote_values(doc, brand.brand_name)
    # The preview always shows the pay button, whether or not this org has a provider connected
    # (epic #269). An admin judging the wording of a template is asking "what will this mail
    # look like", and a button that vanishes because of an unrelated setting elsewhere in
    # Instellingen makes the editor lie about the template being edited. The *sample* document
    # is fabricated for exactly this reason; its link is fabricated the same way.
    # A real-looking address on the org's own host with a placeholder id — the shape the auth
    # kinds' sample already uses for its reset token. The sample document is fabricated and has
    # no id to point at, which is the honest reason the preview cannot link to a real invoice.
    pay_url = invoice_pay_url(brand.base_url, "preview")
    if kind == REMINDER_KIND:
        # An overdue invoice: a due date a fortnight back, and what the part payment left owing.
        doc.due_date = doc.issue_date - timedelta(days=14)
        return _reminder_values(doc, brand.brand_name, 14, pay_url)
    return _invoice_values(doc, brand.brand_name, pay_url)


def _sample_for(kind: str) -> Any:
    """Bind one kind into the registry's ``async (ctx, locale) -> values`` shape."""

    async def provider(ctx: Any, locale: str) -> dict[str, str]:
        return await _sample_values(ctx, locale, kind)

    return provider


#: Contributed on the module descriptor (§6); core holds no list of these.
INVOICING_EMAIL_KINDS: list[EmailTemplateKind] = [
    EmailTemplateKind(
        key=INVOICE_KIND,
        module="invoicing",
        label_key="invoicing.email.kind.invoice",
        hint_key="invoicing.email.kind.invoice_hint",
        subject_key="invoicing.email.invoice_subject",
        body_key="invoicing.email.invoice_body",
        # ``link`` is the pay button's destination (epic #269): the invoice in the client
        # portal, never a provider checkout URL. It is the one variable here that may resolve
        # to nothing — no provider connected, or nothing left to collect — and then the whole
        # button goes with it.
        variables=(
            "brand", "number", "company", "contact", "total", "date", "due_date", "reference",
            "link",
        ),
        button_key="invoicing.email.pay_button",
        sample=_sample_for(INVOICE_KIND),
        position=110,
    ),
    EmailTemplateKind(
        key=QUOTE_KIND,
        module="invoicing",
        label_key="invoicing.email.kind.quote",
        hint_key="invoicing.email.kind.quote_hint",
        subject_key="invoicing.email.quote_subject",
        body_key="invoicing.email.quote_body",
        variables=(
            "brand", "number", "company", "contact", "total", "date", "valid_until", "reference",
        ),
        sample=_sample_for(QUOTE_KIND),
        position=120,
    ),
    EmailTemplateKind(
        key=REMINDER_KIND,
        module="invoicing",
        label_key="invoicing.email.kind.reminder",
        hint_key="invoicing.email.kind.reminder_hint",
        subject_key="invoicing.email.reminder_subject",
        body_key="invoicing.email.reminder_body",
        variables=(
            "brand", "number", "company", "contact", "total", "outstanding", "date", "due_date",
            "days", "reference", "link",
        ),
        button_key="invoicing.email.pay_button",
        sample=_sample_for(REMINDER_KIND),
        position=130,
    ),
]


async def load_transport(
    session: AsyncSession, org_id: Any
) -> tuple[str, dict, Sender] | None:
    """The org's transport, decrypted — read it *before* any ``release_db`` block: inside
    one, an org-scoped SELECT would run without the RLS GUC and fail closed."""
    row = await get_row(session, org_id)
    if row is None:
        return None
    return (
        row.provider,
        json.loads(decrypt(row.config_enc)),
        Sender(from_email=row.from_email, from_name=row.from_name, reply_to=row.reply_to),
    )


async def deliver(
    ctx: Any,
    message: OutgoingEmail,
    brand: EmailBrand | None = None,
    transport: tuple[str, dict, Sender] | None = None,
) -> None:
    """Request-path send: transport read first, network inside ``release_db``, honest
    failure. Callers write their bookkeeping (sent_at, counts) *after* this returns.

    This path bypasses ``send_org_email`` (the release-db dance), so the branded chrome
    (#236) is applied here — like the transport, the brand is read *before* the network call.

    ``transport`` is accepted pre-resolved because a caller may need to know *which* transport
    before it composes: whether the mail may carry an inline QR is a property of the provider
    (epic #269, ``docs/EMAIL.md``), and asking after composing would mean discovering it too
    late. Passing it also saves the second read the caller would otherwise cause.
    """
    if transport is None:
        transport = await load_transport(ctx.session, ctx.org.id)
    if transport is None:
        raise AppError(
            "email_not_configured", "errors.email_not_configured", status_code=400
        )
    if brand is None:
        brand = await load_brand(ctx.session, ctx.org)
    message = apply_branding(brand, message)
    provider, config, sender = transport
    async with ctx.release_db():
        ok, error = await send_email(provider, config, sender, message)
    if not ok:
        raise AppError("email_failed", "errors.invoicing.email_failed", status_code=502)
