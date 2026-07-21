"""Server-side PDF rendering for invoices and quotes (owner feedback).

Until now the only "PDF" was the browser's print dialog, and a *sent* invoice e-mail
carried no document at all — a text summary is not an invoice. This renders the same
layout ``DocumentView.svelte`` draws (seller block, bill-to, template columns, totals,
per-locale template texts, the payment fallback) as real PDF bytes, so the send path can
attach the document and the API can serve a download.

Labels come from the shared i18n catalogs in the **document's** locale (``app.i18n``), the
same rule the document e-mails follow. Fonts: the system DejaVu face when present (full
Unicode, the € sign); the built-in Helvetica with latin-1 replacement otherwise — a missing
font must degrade a glyph, never fail an invoice.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from fpdf import FPDF

from app.core.phone import format_phone_international
from app.core.richtext import markdown_to_plaintext
from app.i18n import translate

_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    Path("/usr/share/fonts/TTF"),
)

_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}

_GRAY = (107, 114, 128)
_DARK = (17, 24, 39)
_LIGHT_RULE = (229, 231, 235)


def _fmt_money(value: Any, currency: str, locale: str) -> str:
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
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{sign}{symbol} {formatted}" if symbol else f"{sign}{currency} {formatted}"


def _fmt_date(value: Any) -> str:
    return value.strftime("%d-%m-%Y") if value else "—"


def _hex_rgb(value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    raw = (value or "").lstrip("#")
    if len(raw) != 6:
        return fallback
    try:
        return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


class _DocPdf(FPDF):
    """FPDF with a Unicode face when the system has one, latin-1 degradation otherwise."""

    def __init__(self) -> None:
        super().__init__(format="A4")
        self.unicode_ok = False
        for fonts in _FONT_DIRS:
            regular, bold = fonts / "DejaVuSans.ttf", fonts / "DejaVuSans-Bold.ttf"
            if regular.exists() and bold.exists():
                self.add_font("doc", "", str(regular))
                self.add_font("doc", "B", str(bold))
                self.unicode_ok = True
                break
        self.face = "doc" if self.unicode_ok else "helvetica"
        self.set_auto_page_break(auto=True, margin=18)

    def txt(self, value: str) -> str:
        if self.unicode_ok:
            return value
        return value.encode("latin-1", "replace").decode("latin-1")

    def font(self, size: float, *, bold: bool = False) -> None:
        self.set_font(self.face, "B" if bold else "", size)


def render_document_pdf(
    *,
    kind: str,
    doc: Any,
    lines: list[Any],
    seller: dict[str, Any],
    config: dict[str, Any],
    brand_name: str,
) -> bytes:
    locale = doc.locale or "nl"

    def t(key: str, **params: object) -> str:
        return translate(key, locale, **params)

    def money(value: Any) -> str:
        return _fmt_money(value, doc.currency, locale)

    invoice_kind = getattr(doc, "kind", None)
    heading = (
        t("invoicing.doc.quote")
        if kind == "quote"
        else t("invoicing.doc.credit_note")
        if invoice_kind == "credit_note"
        else t("invoicing.doc.invoice")
    )
    accent = _hex_rgb(config.get("accent_color"), _hex_rgb("#4f46e5", _DARK))
    columns = {
        "quantity": True,
        "unit": False,
        "unit_price": True,
        "tax": True,
        **(config.get("columns") or {}),
    }

    pdf = _DocPdf()
    pdf.add_page()
    content_w = pdf.w - pdf.l_margin - pdf.r_margin

    # --- header: heading left, seller identity right ------------------------------- #
    top = pdf.get_y()
    pdf.font(22, bold=True)
    pdf.set_text_color(*accent)
    pdf.cell(content_w * 0.55, 10, pdf.txt(heading), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_GRAY)
    if doc.number:
        pdf.font(10)
        pdf.cell(content_w * 0.55, 5, pdf.txt(doc.number), new_x="LMARGIN", new_y="NEXT")

    seller_lines: list[tuple[str, bool]] = [(seller.get("name") or brand_name, True)]
    for key in ("address_line1", "address_line2"):
        if seller.get(key):
            seller_lines.append((str(seller[key]), False))
    postal_city = " ".join(str(seller[k]) for k in ("postal_code", "city") if seller.get(k))
    if postal_city:
        seller_lines.append((postal_city, False))
    if seller.get("vat_number"):
        seller_lines.append((f"{t('invoicing.doc.vat_number')} {seller['vat_number']}", False))
    if seller.get("coc_number"):
        seller_lines.append((f"{t('invoicing.doc.coc_number')} {seller['coc_number']}", False))
    if seller.get("iban"):
        seller_lines.append((f"{t('invoicing.doc.iban')} {seller['iban']}", False))
    if seller.get("email"):
        seller_lines.append((str(seller["email"]), False))
    if seller.get("phone"):
        # E.164 prints international; a legacy freeform value prints as stored (#256).
        seller_lines.append((str(format_phone_international(str(seller["phone"]))), False))

    y = top
    for text_value, bold in seller_lines:
        pdf.set_xy(pdf.l_margin + content_w * 0.55, y)
        pdf.font(9, bold=bold)
        pdf.set_text_color(*(_DARK if bold else _GRAY))
        pdf.cell(content_w * 0.45, 4.6, pdf.txt(text_value), align="R")
        y += 4.6
    pdf.set_y(max(pdf.get_y(), y) + 8)

    # --- bill-to + meta ------------------------------------------------------------- #
    customer = doc.customer or {}
    bill_top = pdf.get_y()
    pdf.font(7.5, bold=True)
    pdf.set_text_color(*_GRAY)
    pdf.cell(content_w * 0.55, 4, pdf.txt(t("invoicing.doc.bill_to").upper()),
             new_x="LMARGIN", new_y="NEXT")
    bill_lines: list[tuple[str, bool]] = [(customer.get("name") or "—", True)]
    for key in ("address_line1", "address_line2"):
        if customer.get(key):
            bill_lines.append((str(customer[key]), False))
    postal_city = " ".join(
        str(customer[k]) for k in ("postal_code", "city") if customer.get(k)
    )
    if postal_city:
        bill_lines.append((postal_city, False))
    if customer.get("vat_number"):
        bill_lines.append((f"{t('invoicing.doc.vat_number')} {customer['vat_number']}", False))
    if customer.get("coc_number"):
        bill_lines.append((f"{t('invoicing.doc.coc_number')} {customer['coc_number']}", False))
    if customer.get("email"):
        bill_lines.append((str(customer["email"]), False))
    for text_value, bold in bill_lines:
        pdf.font(9.5, bold=bold)
        pdf.set_text_color(*(_DARK if bold else _GRAY))
        pdf.cell(content_w * 0.55, 4.8, pdf.txt(text_value), new_x="LMARGIN", new_y="NEXT")
    bill_bottom = pdf.get_y()

    meta: list[tuple[str, str]] = [
        (
            t("invoicing.doc.quote_number") if kind == "quote" else t("invoicing.doc.number"),
            doc.number or "—",
        ),
        (t("invoicing.doc.date"), _fmt_date(doc.issue_date)),
    ]
    if kind == "invoice":
        meta.append((t("invoicing.doc.due"), _fmt_date(getattr(doc, "due_date", None))))
    else:
        meta.append(
            (t("invoicing.doc.valid_until"), _fmt_date(getattr(doc, "valid_until", None)))
        )
    if doc.reference:
        meta.append((t("invoicing.doc.reference"), doc.reference))
    y = bill_top
    for label, value in meta:
        pdf.set_xy(pdf.l_margin + content_w * 0.58, y)
        pdf.font(9)
        pdf.set_text_color(*_GRAY)
        pdf.cell(content_w * 0.20, 4.8, pdf.txt(label))
        pdf.font(9, bold=True)
        pdf.set_text_color(*_DARK)
        pdf.cell(content_w * 0.22, 4.8, pdf.txt(value), align="R")
        y += 4.8
    pdf.set_y(max(bill_bottom, y) + 6)

    if doc.intro or (config.get("intro_i18n") or {}).get(locale):
        pdf.font(9)
        pdf.set_text_color(*_DARK)
        intro = doc.intro or (config.get("intro_i18n") or {}).get(locale, "")
        pdf.multi_cell(content_w, 4.8, pdf.txt(intro))
        pdf.ln(3)

    # --- lines table ---------------------------------------------------------------- #
    widths = {"quantity": 18.0, "unit": 16.0, "unit_price": 26.0, "tax": 20.0, "amount": 26.0}
    used = sum(widths[key] for key in ("quantity", "unit", "unit_price", "tax") if columns[key])
    desc_w = content_w - used - widths["amount"]

    def header_cell(label: str, width: float, align: str) -> None:
        pdf.cell(width, 6, pdf.txt(label.upper()), align=align, border="B")

    pdf.font(7.5, bold=True)
    pdf.set_text_color(*_GRAY)
    pdf.set_draw_color(*_LIGHT_RULE)
    header_cell(t("invoicing.line.description"), desc_w, "L")
    if columns["quantity"]:
        header_cell(t("invoicing.line.quantity"), widths["quantity"], "R")
    if columns["unit"]:
        header_cell(t("invoicing.line.unit"), widths["unit"], "L")
    if columns["unit_price"]:
        header_cell(t("invoicing.line.unit_price"), widths["unit_price"], "R")
    if columns["tax"]:
        header_cell(t("invoicing.line.tax"), widths["tax"], "R")
    header_cell(t("invoicing.line.amount"), widths["amount"], "R")
    pdf.ln(6)

    for line in lines:
        pdf.font(9)
        pdf.set_text_color(*_DARK)
        description = line.description or ""
        # Height of the row follows the wrapped description.
        start_y = pdf.get_y()
        pdf.multi_cell(desc_w, 5.2, pdf.txt(description), border="B", new_x="RIGHT", new_y="TOP")
        row_h = max(pdf.get_y() - start_y, 5.2) if pdf.get_y() > start_y else 5.2
        pdf.set_xy(pdf.l_margin + desc_w, start_y)
        if columns["quantity"]:
            pdf.cell(widths["quantity"], row_h, pdf.txt(f"{Decimal(str(line.quantity)):g}"),
                     align="R", border="B")
        if columns["unit"]:
            pdf.cell(widths["unit"], row_h, pdf.txt(line.unit or ""), border="B")
        if columns["unit_price"]:
            pdf.cell(widths["unit_price"], row_h, pdf.txt(money(line.unit_price)),
                     align="R", border="B")
        if columns["tax"]:
            pct = f"{Decimal(str(line.tax_rate_pct or 0)):g}%"
            pdf.cell(widths["tax"], row_h, pdf.txt(pct), align="R", border="B")
        pdf.cell(widths["amount"], row_h, pdf.txt(money(line.amount)), align="R", border="B")
        pdf.ln(row_h)

    # --- totals ---------------------------------------------------------------------- #
    pdf.ln(3)
    totals_rows: list[tuple[str, str, bool]] = [
        (t("invoicing.field.subtotal"), money(doc.subtotal), False),
        (t("invoicing.field.tax"), money(doc.tax_total), False),
        (t("invoicing.field.total"), money(doc.total), True),
    ]
    if kind == "invoice":
        paid = Decimal(str(getattr(doc, "paid_total", 0) or 0))
        if paid:
            outstanding = Decimal(str(doc.total or 0)) - paid
            totals_rows.append((t("invoicing.doc.paid"), money(paid), False))
            totals_rows.append((t("invoicing.doc.to_pay"), money(outstanding), True))
    label_w, value_w = 46.0, 34.0
    for label, value, bold in totals_rows:
        pdf.set_x(pdf.l_margin + content_w - label_w - value_w)
        pdf.font(9.5, bold=bold)
        pdf.set_text_color(*(_DARK if bold else _GRAY))
        pdf.cell(label_w, 5.6, pdf.txt(label))
        pdf.set_text_color(*_DARK)
        pdf.cell(value_w, 5.6, pdf.txt(value), align="R", new_x="LMARGIN", new_y="NEXT")

    # --- notes, reverse charge, payment text, footer --------------------------------- #
    if any(line.tax_category == "reverse_charge" for line in lines):
        pdf.ln(2)
        pdf.font(8)
        pdf.set_text_color(*_GRAY)
        pdf.multi_cell(content_w, 4.4, pdf.txt(t("settings.invoicing.category.reverse_charge")))
    if doc.notes:
        pdf.ln(3)
        pdf.font(9)
        pdf.set_text_color(*_DARK)
        # Notes are markdown source (#228); fpdf renders text, so flatten — words, not syntax.
        pdf.multi_cell(content_w, 4.8, pdf.txt(markdown_to_plaintext(doc.notes)))

    payment_text = (config.get("payment_i18n") or {}).get(locale, "")
    fallback_ok = (
        not payment_text
        and kind == "invoice"
        and seller.get("iban")
        and invoice_kind != "credit_note"
    )
    if fallback_ok:
        paid = Decimal(str(getattr(doc, "paid_total", 0) or 0))
        outstanding = Decimal(str(doc.total or 0)) - paid
        payment_text = t(
            "invoicing.doc.payment_fallback",
            total=money(outstanding if paid else doc.total),
            due=_fmt_date(getattr(doc, "due_date", None)),
            iban=seller["iban"],
            number=doc.number or heading,
        )
    if payment_text:
        pdf.ln(4)
        pdf.font(9)
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(content_w, 4.8, pdf.txt(payment_text))

    footer_text = (config.get("footer_i18n") or {}).get(locale, "")
    if footer_text:
        pdf.ln(5)
        pdf.font(8)
        pdf.set_text_color(*_GRAY)
        pdf.multi_cell(content_w, 4.4, pdf.txt(footer_text), align="C")

    return bytes(pdf.output())
