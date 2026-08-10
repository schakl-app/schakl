"""The data a document design renders — one dict, fully resolved, no ORM behind it.

Everything a template can print is computed here: money and dates already formatted in the
document's locale, labels already translated, fields already filtered and ordered by the
template's layout. Two reasons that boundary is where it is.

**A design must not be able to reach further than its data.** A tenant's own HTML template
(``engine.render_custom``) renders against this exact dict in a Jinja sandbox. If the context
held ORM rows, "print the customer's name" and "walk the session to another org's invoices"
would be the same expression. It holds strings.

**Formatting is a document property, not a viewer property.** A Dutch invoice to a German
client prints ``€ 1.234,56`` and ``30-06-2026`` because *the document* is nl — never because
of who opened it. Same rule the document e-mails follow.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.phone import format_phone_international
from app.core.richtext import markdown_to_html
from app.i18n import translate
from app.modules.invoicing.models import LineKind
from app.modules.invoicing.render.blocks import ResolvedLayout, resolve_layout
from app.modules.invoicing.render.colors import (
    INK,
    MUTED,
    RULE,
    WASH,
    accent_for,
    hex_rgb,
    rgb_hex,
    rgba,
)
from app.modules.invoicing.render.qr import LIGHT, qr_svg, readable_dark

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
#: The order sections print in: what was worked, then what recurs, then what renews, then
#: what was sold. Domains sit beside subscriptions rather than among them (#302) — both
#: recur, but a register of renewals is reconciled against the registrar's own invoice and
#: has to be findable as a block.
SECTION_ORDER = (
    LineKind.HOURS.value,
    LineKind.SUBSCRIPTION.value,
    LineKind.DOMAIN.value,
    LineKind.PRODUCT.value,
)
#: Images are inlined as data URIs (see ``engine``), so a very large upload would balloon
#: every render. Beyond this the image is dropped and the document prints without it —
#: degrading a logo, never an invoice.
MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024


@dataclass(frozen=True)
class DocumentBrand:
    """Everything white-label a document prints (Golden Rule 4). Resolved by the service from
    ``org_settings`` — the renderer never reaches for an identity of its own."""

    name: str
    primary_color: str | None = None
    #: The tenant logo's raw bytes, read from storage. ``None`` prints the brand name instead.
    logo: bytes | None = None
    logo_content_type: str | None = None
    #: The template's background mark, read from storage the same way.
    background: bytes | None = None
    background_content_type: str | None = None
    #: The mark in the middle of the payment QR when the template supplies its own (#305).
    #: A third pair rather than a reuse of ``background``: a watermark is a page-sized crop at
    #: 4% opacity and a QR overlay is a square that has to read at 5mm, and a tenant who wants
    #: one does not thereby want the other.
    qr_logo: bytes | None = None
    qr_logo_content_type: str | None = None


def data_uri(payload: bytes | None, content_type: str | None) -> str | None:
    if not payload or len(payload) > MAX_INLINE_IMAGE_BYTES:
        return None
    kind = (content_type or "image/png").split(";")[0].strip() or "image/png"
    return f"data:{kind};base64,{base64.b64encode(payload).decode('ascii')}"


def fmt_money(value: Any, currency: str, locale: str) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    whole, frac = divmod(abs(amount), 1)
    digits = f"{int(whole):,}"
    cents = f"{int(round(frac * 100)):02d}"
    if locale.startswith("nl") or locale.startswith("de"):
        digits = digits.replace(",", ".")
        formatted = f"{digits},{cents}"
    else:
        formatted = f"{digits}.{cents}"
    sign = "-" if amount < 0 else ""
    symbol = CURRENCY_SYMBOLS.get(currency)
    return f"{sign}{symbol} {formatted}" if symbol else f"{sign}{currency} {formatted}"


def _trim(value: Any) -> str:
    """A decimal with its trailing zeros gone — ``1.00`` reads ``1``, ``1.50`` reads ``1.5``.

    Not ``format(x, "g")``: a ``Decimal`` keeps its own exponent, so a ``NUMERIC(10,2)`` column
    formats as ``1.00`` and a tax rate as ``21.00%``. That is how a quantity of one came to be
    printed "1.00" on every invoice. ``normalize()`` also rewrites ``100`` as ``1E+2``, so a
    whole number is quantized back to plain digits afterwards.
    """
    amount = Decimal(str(value or 0)).normalize()
    if amount == amount.to_integral_value():
        amount = amount.quantize(Decimal(1))
    return f"{amount:f}"


def fmt_qty(value: Any, locale: str = "nl") -> str:
    # A quantity sits next to the money on the same page: 1,5 uur, not 1.5 uur.
    text = _trim(value)
    return text.replace(".", ",") if locale.startswith(("nl", "de")) else text


def fmt_date(value: Any) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


def _pct(value: Any) -> str:
    return f"{_trim(value)}%"


@dataclass
class _Entry:
    """One printable key/value. ``key`` is the layout key, so a design can special-case one."""

    key: str
    label: str
    value: str
    #: Set for the pieces a design may want to emphasise (the addressee's own name).
    strong: bool = False


def pick_locale(texts: Any, locale: str) -> str:
    """A tenant's per-locale text in the document's language, else English, else Dutch.

    The same fallback the template's text blocks use, applied to the smaller pieces too, so
    "the document is nl" means one thing everywhere on the page.
    """
    if not isinstance(texts, dict):
        return ""
    for candidate in (locale, "en", "nl"):
        text = texts.get(candidate)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _entries(
    layout: ResolvedLayout,
    block: str,
    values: dict[str, tuple[str, ...] | None],
    locale: str = "nl",
) -> list[dict]:
    """A block's fields, in layout order, filtered down to what it can actually say.

    Two filters, and both matter. A **disabled block** yields nothing — a design that places
    a block by hand (the letterhead's payment card sits beside the addressee, not in the body
    stack) would otherwise draw it however the layout was set, which is a switch that does
    nothing. And a field switched *on* with nothing behind it prints nothing either: an empty
    "KvK-nr." label on a sole trader who has none is worse than the field being absent.

    A field may carry a third piece, its ``note``: an aside on the value rather than a value
    of its own — "(voor 14-07-2026)" beside the amount owed. It travels separately so a design
    can set it apart; glued onto the value it was one bold string, and the deadline read as
    part of the sum.

    The **label** is the catalog's until the template rewords it: "Telefoon" and "t" are the
    same field, and which one an agency prints is their letterhead, not ours. A field that
    prints no label to begin with keeps none (``FieldSpec.labelled``).
    """
    if not layout.enabled(block):
        return []
    out: list[dict] = []
    for key in layout.fields(block):
        parts = values.get(key)
        if not parts:
            continue
        label, value, *rest = parts
        if value is None or str(value).strip() == "":
            continue
        # `relabelled` travels with it because a design may have wording of its own — the
        # letterhead marks the contact rows `t` / `e` / `i` rather than spelling them out.
        # That is the design's answer to *our* label, not to the tenant's: an agency that
        # typed "Tel." must get "Tel.", or the box they typed it in did nothing.
        override = pick_locale(layout.label_i18n(block, key), locale) if label else ""
        out.append(
            {
                "key": key,
                "label": override or label,
                "relabelled": bool(override),
                "value": str(value),
                "note": rest[0] if rest else "",
            }
        )
    return out


def _address_lines(party: dict[str, Any], *, skip_country: str | None = None) -> dict[str, str]:
    """The address pieces a party block can print, keyed by field."""
    postal_city = " ".join(
        str(party[k]) for k in ("postal_code", "city") if party.get(k)
    )
    country = party.get("country")
    return {
        "address": "\n".join(
            str(party[k]) for k in ("address_line1", "address_line2") if party.get(k)
        ),
        "postal_city": postal_city,
        # Country only when it differs from ours: a domestic invoice needn't state "NL".
        "country": str(country) if country and country != skip_country else "",
    }


def _sections(lines: list[Any], t: Any) -> list[dict]:
    """Lines grouped into the four kinds, each keeping its own ``position`` order.

    A document whose lines are all one kind gets **no** headers: a lone "UREN" band above a
    table of hours, subtotalling to the subtotal directly beneath it, is noise. Headers earn
    their place exactly when the reader has to tell two kinds apart.
    """
    buckets: dict[str, list[Any]] = {}
    for line in lines:
        kind = getattr(line, "line_kind", None) or LineKind.PRODUCT.value
        kind = getattr(kind, "value", kind)
        if kind not in SECTION_ORDER:
            kind = LineKind.PRODUCT.value
        buckets.setdefault(kind, []).append(line)
    ordered = [kind for kind in SECTION_ORDER if kind in buckets]
    if len(ordered) <= 1:
        return [{"kind": "", "label": "", "lines": lines}]
    return [
        {"kind": kind, "label": t(f"invoicing.line.kind.{kind}"), "lines": buckets[kind]}
        for kind in ordered
    ]


def _background(config: dict[str, Any], brand: DocumentBrand) -> dict[str, Any] | None:
    """The watermark mark behind the page: the template's own upload, else the tenant logo.

    Defaulting to the logo is what makes the letterhead design work out of the box — a tenant
    who has uploaded a logo already has the artwork the design wants, at the size and opacity
    the design chose. Uploading a separate mark is for when the letterhead wants a different
    crop from the one the app's sidebar shows.
    """
    raw = config.get("background") or {}
    # Opt-in, not opt-out. A template saved before this feature existed has no `background`
    # key at all, and a release that reads that as "yes please" would put a mark behind every
    # invoice every existing tenant has already approved.
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    source = brand.background or (brand.logo if raw.get("use_logo", True) else None)
    content_type = brand.background_content_type if brand.background else brand.logo_content_type
    uri = data_uri(source, content_type)
    if not uri:
        return None
    return {
        "url": uri,
        # Clamped here rather than trusted: a stored 40 would black out the page behind the
        # text, and the config is tenant-writable.
        "opacity": max(0.0, min(1.0, float(raw.get("opacity", 0.04) or 0))),
        "scale": max(5.0, min(200.0, float(raw.get("scale", 78) or 78))),
        "x": max(-50.0, min(150.0, float(raw.get("x", 50) or 50))),
        "y": max(-50.0, min(150.0, float(raw.get("y", 50) or 50))),
        "rotate": max(-180.0, min(180.0, float(raw.get("rotate", 0) or 0))),
        "repeat": bool(raw.get("repeat", False)),
    }


def qr_appearance(
    config: dict[str, Any], brand: DocumentBrand, accent: str
) -> tuple[str, str, bytes | None, str | None]:
    """``(dark, light, logo_bytes, logo_content_type)`` for this template's payment QR (#305).

    One function, three readers — the document below, the mail's PNG (``service._pay_qr``) and
    the editor's live preview (``TemplateService.qr_preview``). That is the whole reason it is
    a function: three surfaces drawing "the tenant's QR" from three copies of the same three
    ``config.get`` calls is how a preview starts lying about what will print.

    ``custom``'s colours are handed on **raw**: the substitution rule lives in
    ``qr.readable_pair`` and is applied at the one place that draws, so a caller cannot
    accidentally take the colours without the guarantee. Every other style resolves to a pair
    that was already safe.
    """
    style = config.get("qr_style", "brand")
    if style == "plain":
        return "#000000", LIGHT, None, None
    if style != "custom":
        return readable_dark(accent), LIGHT, brand.logo, brand.logo_content_type

    dark = str(config.get("qr_color") or "").strip() or accent
    light = str(config.get("qr_background") or "").strip() or LIGHT
    choice = config.get("qr_logo", "brand")
    if choice == "none":
        return dark, light, None, None
    if choice == "custom":
        # The template's own mark, loaded by the *service* into `brand.qr_logo` for the same
        # reason the background is: the renderer is sandboxed and reaches no storage. Falling
        # back to the org logo when the upload is missing would draw a mark the tenant
        # explicitly replaced, so an absent one draws nothing.
        return dark, light, brand.qr_logo, brand.qr_logo_content_type
    return dark, light, brand.logo, brand.logo_content_type


def build_context(
    *,
    kind: str,
    doc: Any,
    lines: list[Any],
    seller: dict[str, Any],
    config: dict[str, Any],
    brand: DocumentBrand,
    tax_groups: list[Any] | None = None,
    pay_url: str | None = None,
    payable_online: bool = False,
) -> dict[str, Any]:
    """Everything a design needs, resolved. See the module docstring for the contract.

    ``pay_url`` is where this document lives in the client portal (#268), resolved by the
    *service* — this module may not know a host (Golden Rule 4). Absent, the QR block simply
    does not render. ``payable_online`` says whether a payment provider is connected, which
    changes the caption under the code and nothing else: without one the scan still opens the
    invoice, where the client can read it and download the PDF.
    """
    locale = getattr(doc, "locale", None) or "nl"
    config = config or {}

    def t(key: str, **params: object) -> str:
        return translate(key, locale, **params)

    def money(value: Any) -> str:
        return fmt_money(value, doc.currency, locale)

    layout: ResolvedLayout = resolve_layout(config.get("layout"))
    accent = accent_for(config.get("accent_color"), brand.primary_color)
    accent_rgb = hex_rgb(accent, INK)

    invoice_kind = getattr(doc, "kind", None)
    is_credit_note = invoice_kind == "credit_note"
    heading = (
        t("invoicing.doc.quote")
        if kind == "quote"
        else t("invoicing.doc.credit_note")
        if is_credit_note
        else t("invoicing.doc.invoice")
    )
    status = getattr(doc, "status", "")
    watermark = (
        t("invoicing.doc.draft_watermark")
        if status == "draft"
        else t("invoicing.doc.cancelled_watermark")
        if status == "cancelled"
        else ""
    )

    # --- parties ------------------------------------------------------------------- #
    seller_addr = _address_lines(seller)
    seller_values: dict[str, tuple[str, ...] | None] = {
        "name": ("", seller.get("name") or brand.name),
        "address": ("", seller_addr["address"]),
        "postal_city": ("", seller_addr["postal_city"]),
        "country": ("", seller.get("country") or ""),
        "phone": (
            t("invoicing.doc.phone"),
            format_phone_international(str(seller["phone"])) if seller.get("phone") else "",
        ),
        "email": (t("invoicing.doc.email"), seller.get("email") or ""),
        "website": (t("invoicing.doc.website"), seller.get("website") or ""),
        "iban": (t("invoicing.doc.iban"), seller.get("iban") or ""),
        "bic": (t("invoicing.doc.bic"), seller.get("bic") or ""),
        "vat_number": (t("invoicing.doc.vat_number"), seller.get("vat_number") or ""),
        "coc_number": (t("invoicing.doc.coc_number"), seller.get("coc_number") or ""),
    }

    customer = dict(getattr(doc, "customer", None) or {})
    customer_addr = _address_lines(customer, skip_country=seller.get("country"))
    customer_values: dict[str, tuple[str, ...] | None] = {
        "label": ("", t("invoicing.doc.bill_to")),
        "name": ("", customer.get("name") or ""),
        "attn": ("", customer.get("attn") or ""),
        "address": ("", customer_addr["address"]),
        "postal_city": ("", customer_addr["postal_city"]),
        "country": ("", customer_addr["country"]),
        "vat_number": (t("invoicing.doc.vat_number"), customer.get("vat_number") or ""),
        "coc_number": (t("invoicing.doc.coc_number"), customer.get("coc_number") or ""),
        "email": (t("invoicing.doc.email"), customer.get("email") or ""),
    }

    # --- document meta ---------------------------------------------------------------- #
    issue_date = getattr(doc, "issue_date", None)
    due_date = getattr(doc, "due_date", None)
    period_start = getattr(doc, "period_start", None)
    period_end = getattr(doc, "period_end", None)
    period = ""
    if period_end:
        period = (
            f"{fmt_date(period_start)} - {fmt_date(period_end)}"
            if period_start
            else fmt_date(period_end)
        )
    terms = ""
    if isinstance(issue_date, date) and isinstance(due_date, date):
        days = (due_date - issue_date).days
        if days >= 0:
            # No ICU on the server (app/i18n): a one/other key pair, the house rule.
            terms = t(
                "invoicing.doc.terms_days_one" if days == 1 else "invoicing.doc.terms_days_other",
                days=days,
            )
    meta_values: dict[str, tuple[str, ...] | None] = {
        "number": (
            t("invoicing.doc.quote_number") if kind == "quote" else t("invoicing.doc.number"),
            getattr(doc, "number", None) or "",
        ),
        "issue_date": (t("invoicing.doc.date"), fmt_date(issue_date)),
        "reference": (t("invoicing.doc.reference"), getattr(doc, "reference", None) or ""),
        "due_date": (
            t("invoicing.doc.valid_until") if kind == "quote" else t("invoicing.doc.due"),
            fmt_date(getattr(doc, "valid_until", None) if kind == "quote" else due_date),
        ),
        "payment_terms": (t("invoicing.doc.payment_terms"), terms if kind == "invoice" else ""),
        "client_number": (t("invoicing.doc.client_number"), customer.get("client_number") or ""),
        "delivery_date": (
            t("invoicing.doc.delivery_date"),
            fmt_date(getattr(doc, "delivery_date", None)),
        ),
        "period": (t("invoicing.doc.period"), period),
    }

    # --- money -------------------------------------------------------------------------- #
    paid = Decimal(str(getattr(doc, "paid_total", 0) or 0))
    credited = Decimal(str(getattr(doc, "credited_total", 0) or 0))
    # Credit notes come off the balance the same way payments do — the preview is a live view
    # of the document (it already reflects payments registered after sending), so an invoice
    # written off shows nothing outstanding rather than a figure nobody owes.
    outstanding = (
        Decimal(str(getattr(doc, "total", 0) or 0))
        - paid
        - Decimal(str(getattr(doc, "credited_total", 0) or 0))
        + Decimal(str(getattr(doc, "applied_total", 0) or 0))
    )
    groups = list(tax_groups or [])
    tax_rows = [
        {
            "name": getattr(group, "name", None) or _pct(getattr(group, "rate_pct", 0)),
            "rate": _pct(getattr(group, "rate_pct", 0)),
            "base": money(getattr(group, "base", 0)),
            "tax": money(getattr(group, "tax", 0)),
        }
        for group in groups
    ] or [
        {
            "name": t("invoicing.field.tax"),
            "rate": "",
            "base": money(getattr(doc, "subtotal", 0)),
            "tax": money(getattr(doc, "tax_total", 0)),
        }
    ]

    def labelled(block: str, key: str, fallback: str) -> str:
        """A catalog label, unless the template reworded this field. See ``_entries``."""
        return pick_locale(layout.label_i18n(block, key), locale) or fallback

    totals_order = layout.fields("totals")
    totals_rows: list[dict] = []
    for key in totals_order:
        if key == "subtotal":
            totals_rows.append(
                {"key": key, "label": labelled("totals", key, t("invoicing.doc.subtotal")),
                 "value": money(doc.subtotal), "strong": False}
            )
        elif key == "tax_rows":
            # One line, or a line per rate. The reader's question at the foot of an invoice is
            # *how much* VAT; only a document carrying several rates also has to answer
            # *which*, and that split is required **on the invoice**, not required here. With
            # the breakdown block switched on it is already stated, in more detail (base as
            # well as tax) — so repeating it beside the total is the same table twice, which is
            # what "Subtotaal / 21% / 9% / Totaal" beside a Btw-%/Grondslag/Bedrag table was.
            # Without that block these rows *are* the statement, and stay per rate.
            if layout.enabled("tax_summary"):
                totals_rows.append(
                    {"key": "tax",
                     "label": labelled("totals", "tax_rows", t("invoicing.doc.tax_total")),
                     "value": money(getattr(doc, "tax_total", 0)), "strong": False}
                )
            else:
                totals_rows.extend(
                    {"key": "tax", "label": row["name"], "value": row["tax"], "strong": False}
                    for row in tax_rows
                )
        elif key == "total":
            totals_rows.append(
                {"key": key, "label": labelled("totals", key, t("invoicing.doc.total")),
                 "value": money(doc.total), "strong": True}
            )
        elif key == "paid" and kind == "invoice" and paid:
            totals_rows.append(
                {"key": key, "label": labelled("totals", key, t("invoicing.doc.paid")),
                 "value": money(paid), "strong": False}
            )
        elif key == "credited" and kind == "invoice" and credited:
            # Without this row a written-off invoice prints its full total and a "still to
            # pay" of zero, with nothing on the paper explaining the gap.
            totals_rows.append(
                {"key": key, "label": labelled("totals", key, t("invoicing.doc.credited")),
                 "value": money(-credited), "strong": False}
            )
        elif key == "to_pay" and kind == "invoice" and (paid or credited):
            totals_rows.append(
                {"key": key, "label": labelled("totals", key, t("invoicing.doc.to_pay")),
                 "value": money(outstanding), "strong": True}
            )

    # --- lines ---------------------------------------------------------------------------- #
    column_keys = layout.fields("lines")
    columns = [
        {
            "key": key,
            "label": labelled("lines", key, t(f"invoicing.line.{key}")),
            "align": "left" if key in ("description", "unit") else "right",
        }
        for key in column_keys
    ]

    def cell(line: Any, key: str) -> str:
        if key == "description":
            return line.description or ""
        if key == "quantity":
            return fmt_qty(line.quantity, locale)
        if key == "unit":
            return line.unit or ""
        if key == "unit_price":
            return money(line.unit_price)
        if key == "tax":
            return line.tax_name or _pct(line.tax_rate_pct)
        if key == "amount":
            return money(line.amount)
        return ""

    sections = [
        {
            "kind": section["kind"],
            "label": section["label"],
            "subtotal": money(
                sum((Decimal(str(x.amount or 0)) for x in section["lines"]), Decimal(0))
            ),
            "subtotal_label": t("invoicing.doc.section_subtotal", section=section["label"]),
            "rows": [
                {"cells": [{"key": key, "value": cell(line, key)} for key in column_keys]}
                for line in section["lines"]
            ],
        }
        for section in _sections(lines, t)
    ]

    # --- the payment card (the letterhead's "Betaalgegevens") --------------------------- #
    # The amount asked for is `outstanding`, full stop. This used to read
    # `outstanding if paid else doc.total`, which was the same expression twice over while
    # payments were the only thing that moved a balance — and became wrong the moment credit
    # notes did too: a written-off invoice showed no payments, fell to `doc.total`, and asked
    # the client to transfer the whole amount it had just been relieved of.
    deadline = f"({t('invoicing.doc.before_date', date=fmt_date(due_date))})" if due_date else ""
    payment_box_values: dict[str, tuple[str, ...] | None] = {
        "amount": (
            t("invoicing.doc.to_pay"),
            money(outstanding),
            deadline,
        ),
        "iban": (t("invoicing.doc.to_iban"), seller.get("iban") or ""),
        "account_name": (t("invoicing.doc.account_name"), seller.get("name") or brand.name),
        "description": (
            t("invoicing.doc.payment_description"),
            f"{t('invoicing.doc.invoice').capitalize()} {doc.number}" if doc.number else "",
        ),
    }
    # A credit note is money going the other way and a quote is not owed at all: neither
    # gets a "transfer this amount" card, whatever the layout says. Nor does an invoice with
    # nothing left outstanding — paid, or written off by a credit note. Printing "te betalen
    # € 0,00" beside an IBAN is at best noise, and on a credited invoice it is the sentence
    # that used to demand the whole amount back.
    show_payment_box = (
        kind == "invoice"
        and not is_credit_note
        and outstanding > 0
        and layout.enabled("payment_box")
    )

    # --- the portal QR (#268) ------------------------------------------------------------ #
    # Same three conditions as the payment card, for the same reasons — a credit note, a
    # quote and a settled invoice all have nothing to pay — plus two of its own. It needs a
    # **base URL**, which only the service boundary can resolve (the renderer is sandboxed and
    # owns no host, Golden Rule 4); and it needs the document to have been *issued*, because a
    # draft's portal page 404s for the client it would send there. `payable` carries whether
    # an online payment is actually possible: the code still works without a provider (it
    # opens the invoice, where the PDF can be downloaded), so this is not a gate — it only
    # decides which caption goes under it.
    payable_here = (
        kind == "invoice"
        and not is_credit_note
        and outstanding > 0
        and bool(getattr(doc, "status", None) == "open")
        and bool(pay_url)
    )
    show_qr = payable_here and layout.enabled("payment_qr")
    # Branded by default (epic #269), fully configurable since #305 — resolved in one helper so
    # the document, the mail's PNG and the editor's live preview cannot answer differently.
    qr_ink, qr_paper, qr_logo, qr_logo_type = qr_appearance(config, brand, accent)
    payment_qr = (
        qr_svg(
            pay_url or "",
            dark=qr_ink,
            light=qr_paper,
            logo=qr_logo,
            logo_content_type=qr_logo_type,
        )
        if show_qr
        else ""
    )
    # The same link in words, for the reader who is holding a mouse rather than a phone. Its
    # own switch, sharing every condition — so a template may print one, both or neither, and
    # neither can ever appear on a document that has nothing to collect.
    #
    # Two halves since #304, because the address stopped being inert. `/invoices/<uuid>` was
    # long and meant nothing to a reader; `/invoice/<token>` is a **capability in plain text**,
    # and printing it exposes it to a shoulder, a photocopy and any screenshot — which the QR
    # does not, being unreadable at a glance. So the wording and the spelled-out address are
    # separately switchable, and an agency can keep a line that is still clickable in the PDF
    # without putting the credential in human-readable type.
    pay_block = payable_here and layout.enabled("payment_link")
    show_pay_label = pay_block and layout.shows("payment_link", "label")
    show_pay_url = pay_block and layout.shows("payment_link", "url")
    # What the designs branch on, and what the QR's caption stands down beside: whether that
    # block prints *anything*. Kept under its original name so a tenant's own template that
    # reads it goes on working — with both halves off it now correctly says nothing prints.
    show_pay_link = show_pay_label or show_pay_url

    # --- prose -------------------------------------------------------------------------- #
    def template_text(block: str) -> str:
        return pick_locale(config.get(block), locale)

    payment_text = template_text("payment_i18n")
    # The fallback sentence exists so a document that shows no payment card still says where
    # the money goes. With the card switched on it is the same amount, the same IBAN and the
    # same reference a second time, a few centimetres lower — so it stands down. A sentence
    # the tenant wrote themselves always prints: they put it there on purpose.
    if (
        not payment_text
        and kind == "invoice"
        and seller.get("iban")
        and not is_credit_note
        and outstanding > 0
        and not show_payment_box
    ):
        payment_text = t(
            "invoicing.doc.payment_fallback",
            total=money(outstanding),
            due=fmt_date(due_date) or "—",
            iban=seller["iban"],
            number=doc.number or heading,
        )

    return {
        "kind": kind,
        "locale": locale,
        "heading": heading,
        "watermark": watermark,
        "is_credit_note": is_credit_note,
        "number": getattr(doc, "number", None) or "",
        "currency": doc.currency,
        "t": t,
        "layout": layout,
        "body_order": layout.body_order,
        "palette": {
            "accent": accent,
            "accent_wash": rgba(accent_rgb, 0.11),
            "accent_soft": rgba(accent_rgb, 0.06),
            # Two weights of accent-tinted rule, for a design that rules its paper in the
            # tenant's colour instead of grey. Tints rather than the accent itself: a solid
            # brand colour under every column heading competes with the words above it, and
            # `document_accent` already darkened the hue for text, not for lines.
            "accent_line": rgba(accent_rgb, 0.55),
            "accent_hairline": rgba(accent_rgb, 0.22),
            "ink": rgb_hex(INK),
            "muted": rgb_hex(MUTED),
            "rule": rgb_hex(RULE),
            "wash": rgb_hex(WASH),
        },
        "logo": data_uri(brand.logo, brand.logo_content_type)
        if layout.enabled("logo")
        else None,
        "brand_name": brand.name,
        "background": _background(config, brand),
        "seller": _entries(layout, "seller", seller_values, locale),
        "seller_raw": {k: (v or "") for k, v in seller.items() if isinstance(v, str)},
        "customer": _entries(layout, "bill_to", customer_values, locale),
        "meta": _entries(layout, "meta", meta_values, locale),
        "payment_box": _entries(layout, "payment_box", payment_box_values, locale)
        if show_payment_box
        else [],
        "payment_qr": payment_qr,
        # The tenant's own words when they wrote any, else the built-in — which is *two*
        # sentences, picked by whether a payment can actually be started, because "scan to pay"
        # under a code that only opens a document is a promise the page cannot keep. A tenant
        # who overrides it takes that judgement on themselves, which is the right trade: they
        # know whether they have a provider connected and we are guessing per render.
        "payment_qr_caption": (
            pick_locale(config.get("qr_caption_i18n"), locale)
            or (t("invoicing.doc.qr_pay") if payable_online else t("invoicing.doc.qr_view"))
        ),
        #: **The address**, present whenever this document could be paid — not gated on either
        #: block, because both link to it: the pay-online line prints it, and the QR is wrapped
        #: in it so a code is clickable as well as scannable (a PDF opened on a laptop is the
        #: case a QR serves worst).
        "pay_url": pay_url if payable_here else "",
        #: Whether that block prints *anything* — what the QR's caption stands down beside.
        #: The QR has its own switch and its own emptiness check.
        "show_pay_link": show_pay_link,
        #: Its two halves (#304): the clickable wording, and the address spelled out. Separate
        #: because printing a capability token in human-readable type is a choice an agency
        #: should get to make without giving up the line.
        "show_pay_label": show_pay_label,
        "show_pay_url": show_pay_url,
        "pay_label": (
            t("invoicing.doc.pay_online") if payable_online else t("invoicing.doc.view_online")
        ),
        "columns": columns,
        "sections": sections,
        "grouped": len(sections) > 1,
        "totals": totals_rows,
        "tax_summary": tax_rows,
        "tax_summary_labels": {
            "rate": t("invoicing.doc.tax_rate"),
            "base": t("invoicing.doc.tax_base"),
            "tax": t("invoicing.doc.tax_amount"),
        },
        "intro": getattr(doc, "intro", None) or template_text("intro_i18n"),
        "notes_html": markdown_to_html(doc.notes) if getattr(doc, "notes", None) else "",
        "payment_text": payment_text,
        "footer_text": template_text("footer_i18n"),
        "has_reverse_charge": any(
            getattr(line, "tax_category", None) == "reverse_charge" for line in lines
        ),
        "reverse_charge_text": t("settings.invoicing.category.reverse_charge"),
    }
