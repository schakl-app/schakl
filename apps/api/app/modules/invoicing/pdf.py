"""Server-side PDF rendering for invoices and quotes (owner feedback).

The document a client receives is the product's face, so this renders **the same document
``DocumentView.svelte`` draws** — same blocks, same order, same palette, same grouping into
uren / abonnementen / diensten. The two are a matched pair: change one, change the other.

Three rules shape it:

* **Branding is runtime, per-tenant** (Golden Rule 4). The tenant's logo is drawn from its
  own stored bytes and the accent defaults to the tenant's brand colour — never a hardcoded
  hex. A PDF that ignores both is a white-label product printing someone else's identity.
* **It reads like the app.** Ink, muted text and rules are the CSS tokens from ``app.css``
  (``--text``/``--text-muted``/``--border``); micro-labels are uppercase and tracked; figures
  are right-aligned. The face is Inter — the one the CRM itself renders in.
* **A missing font degrades a glyph, never an invoice.** Inter (shipped in the image) →
  a system DejaVu → the built-in Helvetica with latin-1 replacement.

Labels come from the shared i18n catalogs in the **document's** locale (``app.i18n``), the
same rule the document e-mails follow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.core.phone import format_phone_international
from app.core.richtext import markdown_to_plaintext
from app.i18n import translate
from app.modules.invoicing.models import LineKind

logger = logging.getLogger("schakl.invoicing")

#: Faces we accept, best first. Inter is installed by the API image (see ``apps/api/Dockerfile``);
#: DejaVu covers a distro that has one but not the other.
_FONT_CANDIDATES: tuple[tuple[Path, str, str], ...] = (
    (Path("/usr/share/fonts/opentype/inter"), "Inter-Regular.otf", "Inter-SemiBold.otf"),
    (Path("/usr/share/fonts/truetype/inter"), "Inter-Regular.ttf", "Inter-SemiBold.ttf"),
    (Path("/usr/share/fonts/truetype/dejavu"), "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    (Path("/usr/share/fonts/dejavu"), "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    (Path("/usr/share/fonts/TTF"), "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
)

_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}

# The app's own tokens (apps/web/src/app.css) — the document is paper, so always the light set.
_INK = (23, 23, 23)  # --text
_MUTED = (115, 115, 115)  # --text-muted
_RULE = (229, 229, 229)  # --border
_WASH = (250, 250, 250)  # --surface
_WATERMARK = (238, 238, 238)

_MARGIN = 16.0
_BOTTOM_MARGIN = 18.0
#: The order sections print in: what was worked, then what recurs, then what was sold.
_SECTION_ORDER = (LineKind.HOURS.value, LineKind.SUBSCRIPTION.value, LineKind.PRODUCT.value)


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


def _fmt_qty(value: Any) -> str:
    return f"{Decimal(str(value or 0)):g}"


def _fmt_date(value: Any) -> str:
    return value.strftime("%d-%m-%Y") if value else "—"


def _hex_rgb(value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    raw = (value or "").lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return fallback
    try:
        return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def _tint(color: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    """``color`` mixed into white — the accent wash behind a section header or the totals."""
    return tuple(round(c * ratio + 255 * (1 - ratio)) for c in color)  # type: ignore[return-value]


def _luminance(color: tuple[int, int, int]) -> float:
    """WCAG relative luminance. Mirrors ``luminance()`` in ``lib/core/theme.ts``."""

    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_on_white(color: tuple[int, int, int]) -> float:
    return 1.05 / (_luminance(color) + 0.05)


def document_accent(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """The tenant's colour, darkened in HSL until it reads on paper — hue preserved.

    Paper is white, so this is ``deriveOnDark`` from ``lib/core/theme.ts`` pointed the other
    way: a pale-yellow or mint brand would otherwise print an invisible heading and an
    unreadable "UREN" band. The threshold is 4.5:1 because the accent carries **small** text
    (section labels, the total), not only rules. Keep this in sync with ``documentAccent`` in
    ``lib/modules/invoicing/types.ts`` — preview and PDF must reach the same colour.
    """
    if _contrast_on_white(color) >= 4.5:
        return color
    import colorsys

    r, g, b = (c / 255 for c in color)
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    current = color
    for _ in range(24):
        if _contrast_on_white(current) >= 4.5:
            break
        lightness = max(lightness - 0.04, 0.08)
        current = tuple(  # type: ignore[assignment]
            round(c * 255) for c in colorsys.hls_to_rgb(hue, lightness, saturation)
        )
    return current


@dataclass(frozen=True)
class DocumentBrand:
    """Everything white-label the document prints (Golden Rule 4). Resolved by the service
    from ``org_settings`` — this module never reaches for a default hex of its own."""

    name: str
    primary_color: str | None = None
    #: The tenant logo's raw bytes, read from storage. ``None`` prints the brand name instead.
    logo: bytes | None = None
    logo_content_type: str | None = None


@dataclass
class _Section:
    kind: str
    label: str
    lines: list[Any] = field(default_factory=list)

    @property
    def subtotal(self) -> Decimal:
        return sum((Decimal(str(x.amount or 0)) for x in self.lines), Decimal(0))


def _sections(lines: list[Any], t: Any) -> list[_Section]:
    """Lines grouped into the three kinds, each keeping its own ``position`` order.

    A document whose lines are all one kind gets **no** headers: a lone "UREN" band above a
    table of hours, subtotalling to the subtotal directly beneath it, is noise. Headers earn
    their place exactly when the reader has to tell two kinds apart.
    """
    buckets: dict[str, _Section] = {}
    for line in lines:
        kind = getattr(line, "line_kind", None) or LineKind.PRODUCT.value
        if kind not in _SECTION_ORDER:
            kind = LineKind.PRODUCT.value
        bucket = buckets.get(kind)
        if bucket is None:
            bucket = buckets[kind] = _Section(kind, t(f"invoicing.line.kind.{kind}"))
        bucket.lines.append(line)
    ordered = [buckets[kind] for kind in _SECTION_ORDER if kind in buckets]
    if len(ordered) <= 1:
        return [_Section("", "", list(lines))]
    return ordered


class _DocPdf(FPDF):
    """FPDF with the document's face, its watermark and its page numbering."""

    def __init__(self, *, watermark: str = "") -> None:
        super().__init__(format="A4")
        self.watermark = watermark
        self.unicode_ok = False
        for directory, regular, bold in _FONT_CANDIDATES:
            regular_path, bold_path = directory / regular, directory / bold
            if regular_path.exists() and bold_path.exists():
                self.add_font("doc", "", str(regular_path))
                self.add_font("doc", "B", str(bold_path))
                self.unicode_ok = True
                break
        self.face = "doc" if self.unicode_ok else "helvetica"
        self.set_margins(_MARGIN, _MARGIN, _MARGIN)
        self.set_auto_page_break(auto=True, margin=_BOTTOM_MARGIN)

    # -- helpers ------------------------------------------------------------------------ #
    def txt(self, value: str) -> str:
        if self.unicode_ok:
            return value
        return value.encode("latin-1", "replace").decode("latin-1")

    def font(self, size: float, *, bold: bool = False) -> None:
        self.set_font(self.face, "B" if bold else "", size)

    def ink(self, color: tuple[int, int, int]) -> None:
        self.set_text_color(*color)

    def line_out(self, text: str, width: float, height: float = 4.8, **kwargs: Any) -> None:
        """One line of text that advances to the next — the block-building primitive."""
        self.cell(width, height, self.txt(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT, **kwargs)

    def rule(self, y: float, *, color: tuple[int, int, int] = _RULE, weight: float = 0.2) -> None:
        self.set_draw_color(*color)
        self.set_line_width(weight)
        self.line(_MARGIN, y, self.w - _MARGIN, y)

    # -- fpdf hooks --------------------------------------------------------------------- #
    def header(self) -> None:
        """The watermark rides ``header`` so an automatic page break carries it too."""
        if not self.watermark:
            return
        with self.rotation(24, self.w / 2, self.h / 2):
            self.font(58, bold=True)
            self.ink(_WATERMARK)
            self.set_xy(0, self.h / 2 - 14)
            self.cell(self.w, 28, self.txt(self.watermark), align="C")
        self.set_xy(_MARGIN, _MARGIN)


def _draw_logo(pdf: _DocPdf, brand: DocumentBrand, x: float, y: float) -> float:
    """The tenant logo, capped to a sane block. Returns the height it consumed (0 = none).

    Any failure here is swallowed: a corrupt upload must degrade to the brand name, never
    take down the invoice a client is waiting for.
    """
    if not brand.logo:
        return 0.0
    max_w, max_h = 52.0, 15.0
    try:
        info = pdf.image(
            BytesIO(brand.logo), x=x, y=y, w=max_w, h=max_h, keep_aspect_ratio=True
        )
    except Exception:  # noqa: BLE001 — see the docstring
        logger.warning("document logo could not be drawn; falling back to the brand name")
        return 0.0
    rendered = getattr(info, "rendered_height", None)
    return float(rendered) if rendered else max_h


def render_document_pdf(
    *,
    kind: str,
    doc: Any,
    lines: list[Any],
    seller: dict[str, Any],
    config: dict[str, Any],
    brand: DocumentBrand,
    tax_groups: list[Any] | None = None,
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
    status = getattr(doc, "status", "")
    watermark = (
        t("invoicing.doc.draft_watermark")
        if status == "draft"
        else t("invoicing.doc.cancelled_watermark")
        if status == "cancelled"
        else ""
    )
    # The template's accent wins; absent one the **tenant's** brand colour does (Golden
    # Rule 4) — the same fallback chain DocumentView applies.
    accent = document_accent(
        _hex_rgb(config.get("accent_color"), _hex_rgb(brand.primary_color, (79, 70, 229)))
    )
    accent_wash = _tint(accent, 0.11)
    show_logo = config.get("show_logo") is not False
    columns = {
        "quantity": True,
        "unit": False,
        "unit_price": True,
        "tax": True,
        **(config.get("columns") or {}),
    }

    pdf = _DocPdf(watermark=watermark)
    pdf.add_page()
    content_w = pdf.w - 2 * _MARGIN
    right = _MARGIN + content_w

    # --- header: identity left, seller block right ---------------------------------- #
    top = pdf.get_y()
    left_y = top
    if show_logo and brand.logo:
        left_y += _draw_logo(pdf, brand, _MARGIN, top) + 5.0
    pdf.set_xy(_MARGIN, left_y)
    pdf.font(21, bold=True)
    pdf.ink(accent)
    pdf.line_out(heading, content_w * 0.55, 9)
    if doc.number:
        pdf.font(10)
        pdf.ink(_MUTED)
        pdf.set_x(_MARGIN)
        pdf.line_out(doc.number, content_w * 0.55, 5)
    left_bottom = pdf.get_y()

    seller_lines: list[tuple[str, bool]] = [(seller.get("name") or brand.name, True)]
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
        pdf.set_xy(_MARGIN + content_w * 0.5, y)
        pdf.font(9.5 if bold else 9, bold=bold)
        pdf.ink(_INK if bold else _MUTED)
        pdf.cell(content_w * 0.5, 4.6, pdf.txt(text_value), align="R")
        y += 4.6

    band_y = max(left_bottom, y) + 5
    pdf.rule(band_y, color=accent, weight=0.7)
    pdf.set_y(band_y + 7)

    # --- bill-to + document meta ------------------------------------------------------ #
    def micro_label(text_value: str, width: float) -> None:
        pdf.font(7)
        pdf.ink(_MUTED)
        pdf.cell(width, 4, pdf.txt(text_value.upper()), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    customer = doc.customer or {}
    bill_top = pdf.get_y()
    micro_label(t("invoicing.doc.bill_to"), content_w * 0.55)
    pdf.ln(1)
    bill_lines: list[tuple[str, bool]] = [(customer.get("name") or "—", True)]
    for key in ("address_line1", "address_line2"):
        if customer.get(key):
            bill_lines.append((str(customer[key]), False))
    postal_city = " ".join(str(customer[k]) for k in ("postal_code", "city") if customer.get(k))
    if postal_city:
        bill_lines.append((postal_city, False))
    # Country only when it differs from ours: a domestic invoice needn't state "NL".
    if customer.get("country") and customer.get("country") != seller.get("country"):
        bill_lines.append((str(customer["country"]), False))
    if customer.get("vat_number"):
        bill_lines.append((f"{t('invoicing.doc.vat_number')} {customer['vat_number']}", False))
    if customer.get("coc_number"):
        bill_lines.append((f"{t('invoicing.doc.coc_number')} {customer['coc_number']}", False))
    if customer.get("email"):
        bill_lines.append((str(customer["email"]), False))
    for text_value, bold in bill_lines:
        pdf.set_x(_MARGIN)
        pdf.font(10 if bold else 9.5, bold=bold)
        pdf.ink(_INK if bold else _MUTED)
        pdf.line_out(text_value, content_w * 0.55, 5)
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
    period_start = getattr(doc, "period_start", None)
    period_end = getattr(doc, "period_end", None)
    if period_end:
        span = (
            f"{_fmt_date(period_start)} – {_fmt_date(period_end)}"
            if period_start
            else _fmt_date(period_end)
        )
        meta.append((t("invoicing.doc.period"), span))
    meta_w, value_w = content_w * 0.22, content_w * 0.23
    y = bill_top
    for label, value in meta:
        pdf.set_xy(right - meta_w - value_w, y)
        pdf.font(9)
        pdf.ink(_MUTED)
        pdf.cell(meta_w, 5, pdf.txt(label))
        pdf.font(9, bold=True)
        pdf.ink(_INK)
        pdf.cell(value_w, 5, pdf.txt(value), align="R")
        y += 5
    pdf.set_y(max(bill_bottom, y) + 7)

    intro = doc.intro or (config.get("intro_i18n") or {}).get(locale, "")
    if intro:
        pdf.font(9.5)
        pdf.ink(_INK)
        pdf.set_x(_MARGIN)
        pdf.multi_cell(content_w, 5, pdf.txt(intro), align="L")
        pdf.ln(4)

    # --- lines ------------------------------------------------------------------------ #
    widths = {"quantity": 16.0, "unit": 16.0, "unit_price": 25.0, "tax": 22.0, "amount": 27.0}
    used = sum(widths[key] for key in ("quantity", "unit", "unit_price", "tax") if columns[key])
    desc_w = content_w - used - widths["amount"]

    def table_header() -> None:
        pdf.set_x(_MARGIN)
        pdf.font(7)
        pdf.ink(_MUTED)
        pdf.cell(desc_w, 5, pdf.txt(t("invoicing.line.description").upper()))
        if columns["quantity"]:
            pdf.cell(widths["quantity"], 5, pdf.txt(t("invoicing.line.quantity").upper()),
                     align="R")
        if columns["unit"]:
            pdf.cell(widths["unit"], 5, pdf.txt(t("invoicing.line.unit").upper()))
        if columns["unit_price"]:
            pdf.cell(widths["unit_price"], 5, pdf.txt(t("invoicing.line.unit_price").upper()),
                     align="R")
        if columns["tax"]:
            pdf.cell(widths["tax"], 5, pdf.txt(t("invoicing.line.tax").upper()), align="R")
        pdf.cell(widths["amount"], 5, pdf.txt(t("invoicing.line.amount").upper()), align="R")
        pdf.ln(5)
        pdf.rule(pdf.get_y(), color=accent, weight=0.5)
        pdf.ln(1.5)

    def section_header(section: _Section) -> None:
        height = 6.4
        if pdf.will_page_break(height + 8):
            pdf.add_page()
            table_header()
        pdf.set_fill_color(*accent_wash)
        pdf.rect(_MARGIN, pdf.get_y(), content_w, height, style="F",
                 round_corners=True, corner_radius=1.2)
        pdf.set_xy(_MARGIN + 2.4, pdf.get_y())
        pdf.font(7)
        pdf.ink(accent)
        pdf.cell(content_w - 4.8, height, pdf.txt(section.label.upper()),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1.2)

    def draw_line(line: Any) -> None:
        pdf.font(9.5)
        description = line.description or ""
        wrapped = pdf.multi_cell(
            desc_w - 2, 4.9, pdf.txt(description), dry_run=True, output="LINES"
        )
        row_h = max(len(wrapped) * 4.9, 4.9) + 3.4
        if pdf.will_page_break(row_h):
            pdf.add_page()
            table_header()
        start_y = pdf.get_y()

        pdf.set_xy(_MARGIN, start_y + 1.7)
        pdf.ink(_INK)
        pdf.multi_cell(
            desc_w - 2, 4.9, pdf.txt(description), align="L",
            new_x=XPos.RIGHT, new_y=YPos.TOP,
        )

        # Every other cell is single-line and vertically centred on the row the description set.
        cell_y = start_y + (row_h - 4.9) / 2
        x = _MARGIN + desc_w
        pdf.font(9.5)
        if columns["quantity"]:
            pdf.set_xy(x, cell_y)
            pdf.cell(widths["quantity"], 4.9, pdf.txt(_fmt_qty(line.quantity)), align="R")
            x += widths["quantity"]
        if columns["unit"]:
            pdf.set_xy(x, cell_y)
            pdf.ink(_MUTED)
            pdf.cell(widths["unit"], 4.9, pdf.txt(f"  {line.unit or ''}"))
            pdf.ink(_INK)
            x += widths["unit"]
        if columns["unit_price"]:
            pdf.set_xy(x, cell_y)
            pdf.cell(widths["unit_price"], 4.9, pdf.txt(money(line.unit_price)), align="R")
            x += widths["unit_price"]
        if columns["tax"]:
            pdf.set_xy(x, cell_y)
            pdf.font(9)
            pdf.ink(_MUTED)
            label = line.tax_name or f"{Decimal(str(line.tax_rate_pct or 0)):g}%"
            pdf.cell(widths["tax"], 4.9, pdf.txt(label), align="R")
            pdf.font(9.5)
            pdf.ink(_INK)
            x += widths["tax"]
        pdf.set_xy(x, cell_y)
        pdf.cell(widths["amount"], 4.9, pdf.txt(money(line.amount)), align="R")

        pdf.set_y(start_y + row_h)
        pdf.rule(pdf.get_y())

    table_header()
    sections = _sections(lines, t)
    grouped = len(sections) > 1
    for index, section in enumerate(sections):
        if grouped:
            if index:
                pdf.ln(2.5)
            section_header(section)
        for line in section.lines:
            draw_line(line)
        if grouped:
            pdf.ln(1.4)
            pdf.set_x(right - 70)
            pdf.font(8.5)
            pdf.ink(_MUTED)
            pdf.cell(44, 4.6, pdf.txt(t("invoicing.doc.section_subtotal", section=section.label)),
                     align="R")
            pdf.font(8.5, bold=True)
            pdf.ink(_INK)
            pdf.cell(26, 4.6, pdf.txt(money(section.subtotal)), align="R",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # --- totals ------------------------------------------------------------------------ #
    rows: list[tuple[str, str, bool]] = [(t("invoicing.doc.subtotal"), money(doc.subtotal), False)]
    groups = list(tax_groups or [])
    if groups:
        for group in groups:
            name = getattr(group, "name", None) or f"{Decimal(str(group.rate_pct or 0)):g}%"
            rows.append((name, money(group.tax), False))
    else:
        rows.append((t("invoicing.field.tax"), money(doc.tax_total), False))
    rows.append((t("invoicing.doc.total"), money(doc.total), True))
    paid = Decimal(str(getattr(doc, "paid_total", 0) or 0))
    if kind == "invoice" and paid:
        rows.append((t("invoicing.doc.paid"), money(paid), False))
        rows.append((t("invoicing.doc.to_pay"), money(Decimal(str(doc.total or 0)) - paid), True))

    card_w, pad = 78.0, 4.0
    card_h = pad * 2 + len(rows) * 6.0
    if pdf.will_page_break(card_h + 6):
        pdf.add_page()
    pdf.ln(4)
    card_x, card_y = right - card_w, pdf.get_y()
    pdf.set_fill_color(*_WASH)
    pdf.rect(card_x, card_y, card_w, card_h, style="F", round_corners=True, corner_radius=2)
    y = card_y + pad
    for label, value, strong in rows:
        if strong:
            pdf.set_draw_color(*_RULE)
            pdf.set_line_width(0.2)
            pdf.line(card_x + pad, y - 1.2, card_x + card_w - pad, y - 1.2)
        pdf.set_xy(card_x + pad, y)
        pdf.font(10 if strong else 9, bold=strong)
        pdf.ink(_INK if strong else _MUTED)
        pdf.cell((card_w - 2 * pad) * 0.55, 6, pdf.txt(label))
        pdf.ink(accent if strong else _INK)
        pdf.cell((card_w - 2 * pad) * 0.45, 6, pdf.txt(value), align="R")
        y += 6
    pdf.set_y(card_y + card_h)

    # --- notes, reverse charge, payment text, footer ------------------------------------ #
    def block(text_value: str, *, size: float, color: tuple[int, int, int],
              leading: float, gap: float) -> None:
        """A paragraph that breaks *before* itself rather than in its middle, when it can.

        Splitting "Gelieve € 4.939,28 vóór 30-04-2026 over te maken" across a page boundary
        is how a payment instruction stops being read.
        """
        pdf.ln(gap)
        pdf.font(size)
        wrapped = pdf.multi_cell(content_w, leading, pdf.txt(text_value), dry_run=True,
                                 output="LINES")
        height = max(len(wrapped), 1) * leading
        if pdf.will_page_break(height) and height <= pdf.h - _MARGIN - _BOTTOM_MARGIN:
            pdf.add_page()
        pdf.set_x(_MARGIN)
        pdf.ink(color)
        pdf.multi_cell(content_w, leading, pdf.txt(text_value), align="L")

    if any(line.tax_category == "reverse_charge" for line in lines):
        block(t("settings.invoicing.category.reverse_charge"), size=8.5, color=_MUTED,
              leading=4.4, gap=3)
    if doc.notes:
        # Notes are markdown source (#228); fpdf renders text, so flatten — words, not syntax.
        block(markdown_to_plaintext(doc.notes), size=9.5, color=_INK, leading=5, gap=4)

    payment_text = (config.get("payment_i18n") or {}).get(locale, "")
    fallback_ok = (
        not payment_text
        and kind == "invoice"
        and seller.get("iban")
        and invoice_kind != "credit_note"
    )
    if fallback_ok:
        outstanding = Decimal(str(doc.total or 0)) - paid
        payment_text = t(
            "invoicing.doc.payment_fallback",
            total=money(outstanding if paid else doc.total),
            due=_fmt_date(getattr(doc, "due_date", None)),
            iban=seller["iban"],
            number=doc.number or heading,
        )
    if payment_text:
        pdf.ln(5)
        pdf.font(9.5)
        wrapped = pdf.multi_cell(content_w - 5, 5, pdf.txt(payment_text), dry_run=True,
                                 output="LINES")
        if pdf.will_page_break(max(len(wrapped), 1) * 5):
            pdf.add_page()
        box_y = pdf.get_y()
        pdf.set_x(_MARGIN + 3.5)
        pdf.ink(_INK)
        pdf.multi_cell(content_w - 5, 5, pdf.txt(payment_text), align="L")
        # The accent keel — how the app marks the one line on a card that must be read.
        pdf.set_draw_color(*accent)
        pdf.set_line_width(0.9)
        pdf.line(_MARGIN, box_y, _MARGIN, pdf.get_y() - 0.6)

    footer_text = (config.get("footer_i18n") or {}).get(locale, "")
    if footer_text:
        pdf.ln(6)
        if pdf.will_page_break(9):
            pdf.add_page()
        pdf.rule(pdf.get_y() - 2)
        pdf.set_x(_MARGIN)
        pdf.font(8)
        pdf.ink(_MUTED)
        pdf.multi_cell(content_w, 4.4, pdf.txt(footer_text), align="C")

    _stamp_page_numbers(pdf, locale)
    return bytes(pdf.output())


def _stamp_page_numbers(pdf: _DocPdf, locale: str) -> None:
    """"2 / 3" bottom-right, added afterwards so a one-page document stays unnumbered."""
    total = pdf.page_no()
    if total < 2:
        return
    # Writing below the break margin would otherwise *add* a page per stamp — and then the
    # totals printed here would be wrong about the document they number.
    pdf.set_auto_page_break(auto=False)
    for number in range(1, total + 1):
        pdf.page = number
        pdf.set_xy(_MARGIN, pdf.h - 12)
        pdf.font(7.5)
        pdf.ink(_MUTED)
        pdf.cell(
            pdf.w - 2 * _MARGIN,
            5,
            pdf.txt(translate("invoicing.doc.page", locale, page=number, total=total)),
            align="R",
        )
