"""The document renderer: layout resolution, the shipped designs, and the sandbox.

Split from ``test_invoicing_api`` because what it guards is a different kind of thing. That
suite asks whether the money is right; this one asks whether the *paper* is — which pieces
print, in which order, and what a tenant's own template is allowed to reach while printing it.

The sandbox tests are the ones that must not be deleted. Everything else here fails visibly:
a block in the wrong order is a screenshot away. A Jinja escape is invisible until someone
uses it, so the escapes get a named test each, and the assertion is on the *refusal*.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.errors import AppError
from app.modules.invoicing.render import (
    BLOCK_CATALOG,
    BLOCKS_BY_KEY,
    DocumentBrand,
    catalog_payload,
    render_document_html,
    render_document_pdf,
    resolve_layout,
)
from app.modules.invoicing.sample import sample_document
from tests.conftest import Tenant, auth_cookie, make_tenant

TODAY = __import__("datetime").date(2026, 6, 30)

SELLER = {
    "name": "Agency BV",
    "address_line1": "Kerkstraat 1",
    "postal_code": "1234 AB",
    "city": "Amsterdam",
    "country": "NL",
    "vat_number": "NL999888777B01",
    # Deliberately unlike the sample customer's (which also prints a KvK number): these tests
    # ask what the *seller* block printed, and a shared value cannot answer that.
    "coc_number": "90909090",
    "iban": "NL02ABNA0123456789",
    "email": "administratie@agency.nl",
    "phone": "+31201234567",
    "website": "agency.nl",
    "bic": "ABNANL2A",
}


def _render(config: dict, *, brand: DocumentBrand | None = None) -> str:
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    return render_document_html(
        kind="invoice",
        doc=doc,
        lines=lines,
        seller=SELLER,
        config=config,
        brand=brand or DocumentBrand(name="Agency", primary_color="#4f46e5"),
        tax_groups=groups,
    )


def _boxes(html: str):
    """Every laid-out box WeasyPrint produced, page by page.

    The HTML is not the document. Everything else in this file reads the markup, which is why
    a code that printed the full width of the sheet passed all of it — the markup was right and
    the *box* was not. Anything about size or arrangement has to ask the layout.
    """
    from weasyprint import HTML

    def walk(box):
        yield box
        yield from (b for child in getattr(box, "children", ()) for b in walk(child))

    for page in HTML(string=html).render().pages:
        yield from walk(page._page_box)


#: Elements that never open a level, so the ancestry stack below stays balanced.
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}


def _descends(html: str, *, inner: str, outer: str) -> bool:
    """Does an element carrying class ``inner`` sit *inside* one carrying class ``outer``?

    Ancestry rather than "appears after", because the arrangement this file's placement tests
    guard against — the block drawn below the card instead of within it — satisfies a source
    order comparison perfectly well.
    """
    from html.parser import HTMLParser

    class Ancestry(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.open: list[bool] = []
            self.found = False

        def handle_starttag(self, tag: str, attrs: list) -> None:
            classes = (dict(attrs).get("class") or "").split()
            if inner in classes and any(self.open):
                self.found = True
            if tag not in _VOID:
                self.open.append(outer in classes)

        def handle_endtag(self, tag: str) -> None:
            if tag not in _VOID and self.open:
                self.open.pop()

    parser = Ancestry()
    parser.feed(html)
    return parser.found


# --------------------------------------------------------------------------- #
# Layout resolution — a stored layout is a diff against the catalog, not a snapshot
# --------------------------------------------------------------------------- #
def test_empty_layout_is_the_catalog_defaults() -> None:
    resolved = resolve_layout([])
    for spec in BLOCK_CATALOG:
        assert resolved.enabled(spec.key) is (spec.default or spec.locked), spec.key
        for field in spec.fields:
            assert resolved.shows(spec.key, field.key) is (field.default or field.locked)


def test_a_partial_layout_reorders_only_what_it_names() -> None:
    """Naming three blocks moves those three; everything else keeps its catalog place.

    This is what lets the editor send a partial statement, and what stops a release that adds
    a block from having it swept to the end of every existing tenant's document.
    """
    resolved = resolve_layout([{"key": "footer"}, {"key": "notes"}, {"key": "payment"}])
    body = resolved.body_order
    assert body.index("footer") < body.index("notes") < body.index("payment")
    # The unnamed blocks are still in catalog order relative to each other.
    assert body.index("lines") < body.index("totals")


def test_a_field_the_layout_never_heard_of_lands_at_its_catalog_position() -> None:
    """A layout written before a field existed still prints it, beside its neighbours.

    Simulated by naming only the two ends of the seller block: everything between them is
    "new" as far as this layout is concerned.
    """
    resolved = resolve_layout(
        [{"key": "seller", "fields": [{"key": "name"}, {"key": "coc_number"}]}]
    )
    order = resolved.fields("seller")
    catalog = [f.key for f in BLOCKS_BY_KEY["seller"].fields if f.default or f.locked]
    assert order == catalog, "unnamed fields must keep their catalog order"


def test_locked_blocks_and_fields_cannot_be_switched_off() -> None:
    """Not a preference: an invoice without its number or VAT breakdown is not sendable."""
    resolved = resolve_layout(
        [
            {"key": "lines", "enabled": False, "fields": [{"key": "amount", "enabled": False}]},
            {"key": "meta", "enabled": False, "fields": [{"key": "number", "enabled": False}]},
            {"key": "totals", "enabled": False, "fields": [{"key": "total", "enabled": False}]},
            {"key": "reverse_charge", "enabled": False},
        ]
    )
    assert resolved.enabled("lines") and resolved.shows("lines", "amount")
    assert resolved.enabled("meta") and resolved.shows("meta", "number")
    assert resolved.enabled("totals") and resolved.shows("totals", "total")
    assert resolved.enabled("reverse_charge")


def test_unknown_keys_in_a_stored_layout_are_ignored() -> None:
    """A config hand-edited (or written by a later release we rolled back from) still renders."""
    resolved = resolve_layout(
        [{"key": "not_a_block"}, {"key": "lines", "fields": [{"key": "not_a_field"}]}, "junk"]
    )
    assert "not_a_block" not in resolved.blocks
    assert "not_a_field" not in resolved.fields("lines")
    assert resolved.enabled("lines")


def test_catalog_payload_carries_no_labels() -> None:
    """Keys only — the client resolves labels, because the API does not pick someone else's
    locale (§17). A label leaking in here is how a Dutch admin gets an English invoice field.

    ``labelled`` is not a label: it says whether the field prints one at all, which is what
    lets the editor offer a rewording box for *Telefoon* and not for the street.
    """
    for block in catalog_payload():
        assert set(block) == {"key", "region", "default", "locked", "movable", "fields"}
        for field in block["fields"]:
            assert set(field) == {"key", "default", "locked", "labelled"}


def test_a_template_may_reword_a_label_but_not_invent_one() -> None:
    """"Telefoon" and "t" are the same field; which one prints is the agency's letterhead.

    An override on a field that prints no label is dropped rather than honoured: an address
    line prints as a line, and a label there would not rename anything — in the letterhead it
    would move the street out of the address stack and into the labelled grid.
    """
    reworded = _render({
        "layout": [{"key": "seller", "fields": [
            {"key": "phone", "label_i18n": {"nl": "Tel.", "en": "Phone"}},
            {"key": "address", "label_i18n": {"nl": "Adres"}},
        ]}],
    })
    assert "Tel." in reworded and "Telefoon" not in reworded
    assert "Adres" not in reworded
    # English wording, on a Dutch document, is not the document's — it prints `nl`.
    assert "Phone" not in reworded


def test_a_reworded_label_beats_the_design_s_own_shorthand() -> None:
    """The letterhead marks the contact rows `t` / `e` / `i` rather than spelling them out.
    That answers *our* label, never the tenant's — an agency that typed "Tel." must get it,
    or the box they typed it in did nothing."""
    import re

    def sender_labels(config: dict) -> list[str]:
        html = _render({"design": "letterhead", **config})
        return [cell.strip() for cell in re.findall(r'<td class="k">(.*?)</td>', html, re.S)]

    # The website field is off by default, so the marked rows here are phone and e-mail.
    assert sender_labels({})[:2] == ["t", "e"]
    reworded = sender_labels({
        "layout": [{"key": "seller", "fields": [{"key": "phone", "label_i18n": {"nl": "Tel."}}]}],
    })
    # Only the one they reworded; the rest are still the design's.
    assert reworded[:2] == ["Tel.", "e"]


# --------------------------------------------------------------------------- #
# The shipped designs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("design", ["classic", "letterhead"])
def test_a_design_renders_html_and_pdf(design: str) -> None:
    html = _render({"design": design})
    assert "<!doctype html>" in html.lower()
    assert "Voorbeeldklant B.V." in html
    pdf = render_document_pdf(
        kind="invoice",
        doc=sample_document("nl", "EUR", TODAY)[0],
        lines=sample_document("nl", "EUR", TODAY)[1],
        seller=SELLER,
        config={"design": design},
        brand=DocumentBrand(name="Agency"),
        tax_groups=sample_document("nl", "EUR", TODAY)[2],
    )
    assert pdf.startswith(b"%PDF")


def test_a_disabled_field_leaves_the_document() -> None:
    with_coc = _render({})
    without = _render({"layout": [{"key": "seller", "fields": [{"key": "coc_number",
                                                               "enabled": False}]}]})
    assert SELLER["coc_number"] in with_coc
    assert SELLER["coc_number"] not in without
    # The addressee's own KvK number is a different field in a different block, and switching
    # the seller's off must not take it with them.
    assert "KvK" in without


def test_a_field_on_with_nothing_behind_it_prints_nothing() -> None:
    """An empty "KvK-nr." label on a sole trader who has none is worse than no field.

    Counted rather than searched: the sample addressee prints a KvK number of its own, so
    "KvK is absent" would be false even when the seller's line is correctly dropped.
    """
    doc, lines, groups = sample_document("nl", "EUR", TODAY)

    def render(seller: dict) -> str:
        return render_document_html(
            kind="invoice", doc=doc, lines=lines, seller=seller, config={},
            brand=DocumentBrand(name="Agency"), tax_groups=groups,
        )

    both = render(SELLER)
    sole_trader = render({"name": "Eenmanszaak"})  # no coc, no iban, no vat
    assert both.count("KvK") == sole_trader.count("KvK") + 1
    assert "IBAN" not in sole_trader


def test_a_block_a_design_places_by_hand_still_honours_its_switch() -> None:
    """The letterhead draws the payment card beside the addressee, not from the body stack —
    so it has to consult ``enabled`` itself. It did not, and the switch did nothing."""
    on = _render({"design": "letterhead", "layout": [{"key": "payment_box", "enabled": True}]})
    off = _render({"design": "letterhead", "layout": [{"key": "payment_box", "enabled": False}]})
    assert "Betaalgegevens" in on
    assert "Betaalgegevens" not in off


def test_a_kind_heads_its_own_table_only_when_there_is_more_than_one() -> None:
    """Three kinds, three headed tables: *Aantal* means hours in one and licences in the next,
    and a heading eighteen rows up is not there when the reader needs it. One kind gets the
    plain table — a lone "UREN" over a table that subtotals to the subtotal beneath it is
    noise, which is the same rule ``_sections`` already applies to the grouping itself.
    """
    doc, lines, groups = sample_document("nl", "EUR", TODAY)

    def render(rows: list) -> str:
        return render_document_html(
            kind="invoice", doc=doc, lines=rows, seller=SELLER,
            config={"design": "letterhead"}, brand=DocumentBrand(name="Agency"),
            tax_groups=groups,
        )

    many = render(lines)
    assert many.count('class="line-group"') == 3
    # One column-heading row per kind, each carrying the kind as its description heading.
    assert many.count('class="group-name col-description"') == 3
    assert many.count("Abonnementen") >= 1

    one = render([lines[0]])
    assert 'class="line-group"' not in one
    assert one.count("<thead>") == 1
    """The band is drawn by hand *and* skipped in the body loop; getting one of the two wrong
    prints the VAT breakdown twice, or drops it from a template that asked for it."""
    on = _render({"design": "letterhead",
                  "layout": [{"key": "tax_summary", "enabled": True}]})
    off = _render({"design": "letterhead",
                   "layout": [{"key": "tax_summary", "enabled": False}]})
    assert on.count('class="tax-summary') == 1
    assert on.count('class="totals') == 1
    assert off.count('class="tax-summary') == 0
    # The band itself stays, so the totals keep their column rather than stretching across it.
    assert off.count('class="closing avoid-break"') == 1
    assert off.count('class="totals') == 1


def test_a_block_ordered_around_the_total_still_lands_there() -> None:
    """The letterhead splits the body loop around the closing band, so the band lands where
    ``totals`` was ordered. A split that ignored the order would print every remaining block
    below the band whatever the template said."""
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.notes = "Bedankt voor de opdracht."

    def render(order: list[str]) -> str:
        return render_document_html(
            kind="invoice", doc=doc, lines=lines, seller=SELLER,
            config={"design": "letterhead", "layout": [{"key": key} for key in order]},
            brand=DocumentBrand(name="Agency"), tax_groups=groups,
        )

    above = render(["lines", "notes", "totals", "footer"])
    below = render(["lines", "totals", "notes", "footer"])
    assert above.index("Bedankt") < above.index('class="closing')
    assert below.index("Bedankt") > below.index('class="closing')


def test_the_vat_split_is_stated_once() -> None:
    """The per-rate rows in the totals are what makes a multi-rate invoice lawful — so they
    collapse to one *Totaal btw* exactly when the breakdown block already carries the split,
    in more detail, a few centimetres to the left. Not a preference: without that block these
    rows *are* the statement and stay per rate.
    """
    from app.modules.invoicing.render.context import build_context

    def totals(tax_summary: bool) -> list[dict]:
        doc, lines, groups = sample_document("nl", "EUR", TODAY)
        return build_context(
            kind="invoice", doc=doc, lines=lines, seller=SELLER,
            config={"layout": [{"key": "tax_summary", "enabled": tax_summary}]},
            brand=DocumentBrand(name="Agency"), tax_groups=groups,
        )["totals"]

    # The sample carries two rates on purpose, so "one row" cannot pass by accident.
    per_rate = [row["label"] for row in totals(False) if row["key"] == "tax"]
    assert per_rate == ["21%", "9%"]

    combined = [row for row in totals(True) if row["key"] == "tax"]
    assert [row["label"] for row in combined] == ["Totaal btw"]
    # And it is the whole tax, not the first group's: 236,25 + 8,10.
    assert combined[0]["value"] == "€ 244,35"


def test_the_letterhead_prints_the_sample_on_one_sheet() -> None:
    """Density is part of the design, not a nicety.

    The sample is deliberately the busiest document a tenant will meet — three line kinds with
    their subtotals, two VAT rates, a partial payment, a footer — and it is exactly what the
    template editor previews. It used to run to two sheets with the totals stranded alone on
    the second, which is the first thing anyone choosing this design would have seen.

    Bound to the *default* layout on purpose: switching every optional block on at once (the
    payment card and the VAT breakdown and every meta field) can still cost a second sheet,
    and pinning that would buy a few millimetres of margin by making every ordinary invoice
    tighter than the paper it is modelled on.
    """
    from weasyprint import HTML

    html = _render({"design": "letterhead", "footer_i18n": {"nl": "Bedankt voor uw vertrouwen."}})
    assert len(HTML(string=html).render().pages) == 1


def test_the_payment_sentence_stands_down_behind_the_payment_card() -> None:
    """Both say the same amount, the same IBAN and the same reference. The fallback sentence
    is there for a document that shows no card — with one, it is the instruction twice."""
    card = _render({"design": "letterhead",
                    "layout": [{"key": "payment_box", "enabled": True}]})
    no_card = _render({"design": "letterhead",
                       "layout": [{"key": "payment_box", "enabled": False}]})
    assert "Gelieve" not in card
    assert "Gelieve" in no_card
    # A sentence the tenant wrote themselves is not a fallback and always prints.
    theirs = _render({"design": "letterhead", "payment_i18n": {"nl": "Betaal binnen 14 dagen."},
                      "layout": [{"key": "payment_box", "enabled": True}]})
    assert "Betaal binnen 14 dagen." in theirs


@pytest.mark.parametrize("design", ["classic", "letterhead"])
def test_a_shipped_design_still_renders_as_a_tenants_own_template(design: str) -> None:
    """"Start from this design" hands over the very files the shipped design renders from —
    so those files must run in the **sandbox** too, which allows less than our own environment
    does. The letterhead reaches for ``body_order.index``, a slice and ``dict.get`` to split
    its body around the closing band; any of them refused would turn "branch from letterhead"
    into a 422 on the first save.
    """
    from app.modules.invoicing.render.engine import builtin_source

    body, css = builtin_source(design)
    html = _render({"design": "custom", "html": body, "css": css})
    assert "Voorbeeldklant B.V." in html
    assert 'class="lines"' in html


def test_the_background_is_opt_in() -> None:
    """A template saved before backgrounds existed has no key; that must not mean "yes"."""
    logo = DocumentBrand(name="Agency", logo=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
                         logo_content_type="image/png")
    # The class is in the stylesheet either way; what is opt-in is the element.
    assert '<div class="doc-background"' not in _render({}, brand=logo)
    assert '<div class="doc-background"' in _render({"background": {"enabled": True}}, brand=logo)


def test_background_numbers_are_clamped_at_render_time() -> None:
    """The config is tenant-writable, and an opacity of 40 would black out the text."""
    logo = DocumentBrand(name="Agency", logo=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
                         logo_content_type="image/png")
    html = _render({"background": {"enabled": True, "opacity": 40, "scale": 9000}}, brand=logo)
    assert "opacity: 1;" in html or "opacity: 1.0" in html
    assert "9000%" not in html


def test_a_credit_note_never_asks_to_be_paid() -> None:
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.kind = "credit_note"
    html = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER,
        config={"design": "letterhead", "layout": [{"key": "payment_box", "enabled": True}]},
        brand=DocumentBrand(name="Agency"), tax_groups=groups,
    )
    assert "Betaalgegevens" not in html
    assert "Gelieve" not in html


def test_numbers_print_without_their_column_scale() -> None:
    """A NUMERIC(10,2) quantity of one must read "1", not "1.00" (and a rate "21%")."""
    html = _render({})
    assert ">1.00<" not in html and ">1,00<" not in html
    assert "21.00%" not in html


def test_the_document_renders_in_its_own_locale_not_the_viewers() -> None:
    doc, lines, groups = sample_document("en", "EUR", TODAY)
    html = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER, config={},
        brand=DocumentBrand(name="Agency"), tax_groups=groups,
    )
    assert "INVOICE" in html and "FACTUUR" not in html


# --------------------------------------------------------------------------- #
# The sandbox — a tenant's own template runs on the agency's server
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("{{ ''.__class__.__mro__ }}", id="mro"),
        pytest.param("{{ ''.__class__.__base__.__subclasses__() }}", id="subclasses"),
        pytest.param("{{ self.__init__.__globals__ }}", id="globals"),
        pytest.param("{{ cycler.__init__.__globals__.os }}", id="cycler-os"),
        pytest.param("{{ ''.__class__.__mro__[1].__subclasses__() }}", id="mro-index"),
    ],
)
def test_the_sandbox_refuses_the_standard_jinja_escapes(source: str) -> None:
    """Each of these is a published route from a naive Jinja setup to ``os.system``."""
    with pytest.raises(AppError) as excinfo:
        _render({"design": "custom", "html": source})
    assert excinfo.value.status_code == 422


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("{% include 'letterhead.body.html' %}", id="include"),
        pytest.param("{% extends '_shell.html' %}", id="extends"),
        pytest.param("{% import '_blocks.html' as b %}", id="import"),
    ],
)
def test_a_custom_template_has_no_loader_to_reach_the_filesystem_with(source: str) -> None:
    with pytest.raises(AppError):
        _render({"design": "custom", "html": source})


def test_a_custom_template_renders_against_the_same_context(client_for) -> None:  # noqa: ARG001
    html = _render(
        {
            "design": "custom",
            "html": "<h1>{{ heading }}</h1>{{ blocks.totals_card(totals) }}",
            "css": "h1 { color: red }",
        }
    )
    assert "<h1>FACTUUR</h1>" in html
    assert "Totaal" in html
    assert "h1 { color: red }" in html
    # The shell's furniture is ours, not theirs: a draft still gets its stamp, and the page
    # is still A4 — "bring your own template" is the content, not the paper.
    assert "@page" in html


def test_tenant_css_cannot_close_its_own_style_element() -> None:
    """The payload stays *inside* the style element, where it is text and not markup.

    Escaping the CSS as text would break every ``>`` in a real stylesheet, so it goes in raw
    with the one sequence that ends the element neutralised. The assertion is therefore about
    where the payload lands, not whether the characters appear at all.
    """
    html = _render({"css": "</style><script>alert(1)</script>"})
    head, _, after_style = html.partition("</style>")
    assert "<script>alert(1)</script>" in head, "payload must stay inside the style element"
    assert "<script" not in after_style, "nothing executable may escape it"


def test_a_value_interpolated_by_a_custom_template_is_escaped() -> None:
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.customer = {**doc.customer, "name": "<script>alert(1)</script>"}
    html = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER,
        config={"design": "custom", "html": "{% for e in customer %}{{ e.value }}{% endfor %}"},
        brand=DocumentBrand(name="Agency"), tax_groups=groups,
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_url_fetcher_answers_data_and_nothing_else() -> None:
    from app.modules.invoicing.render.engine import _no_network_fetcher

    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "https://example.com/pixel.png",
        "//example.com/protocol-relative.png",
    ):
        with pytest.raises(ValueError):
            _no_network_fetcher(url)


def test_a_refused_image_costs_the_image_not_the_invoice() -> None:
    """WeasyPrint logs and skips a resource its fetcher rejects. That is the behaviour we
    want: an invoice with a stray external ``<img>`` still goes out, minus the image."""
    from app.modules.invoicing.render.engine import html_to_pdf

    pdf = html_to_pdf(
        '<!doctype html><html><body><p>Factuur</p>'
        '<img src="file:///etc/passwd"><img src="http://169.254.169.254/"></body></html>'
    )
    assert pdf.startswith(b"%PDF")
    assert b"root:x:" not in pdf


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #
async def test_preview_and_pdf_are_the_same_document(client_for) -> None:
    """Not merely similar: the PDF is printed *from* the bytes the preview serves."""
    tenant: Tenant = await make_tenant("render-pair")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company = await client.post(
            "/api/v1/companies", json={"name": "Klant BV"}, headers=headers
        )
        invoice = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company.json()["id"],
                "lines": [{"description": "Werk", "quantity": "2", "unit_price": "100"}],
            },
            headers=headers,
        )
        invoice_id = invoice.json()["id"]

        preview = await client.get(
            f"/api/v1/invoicing/invoices/{invoice_id}/preview", headers=headers
        )
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("text/html")
        assert "frame-ancestors 'self'" in preview.headers["content-security-policy"]
        assert "Klant BV" in preview.text

        pdf = await client.get(f"/api/v1/invoicing/invoices/{invoice_id}/pdf", headers=headers)
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")


async def test_a_template_from_another_org_is_invisible(client_for) -> None:
    """Golden Rule 1 on the new surface: a preview may not render someone else's design."""
    one: Tenant = await make_tenant("render-iso-a")
    two: Tenant = await make_tenant("render-iso-b")
    headers_one = await auth_cookie(one.user)
    headers_two = await auth_cookie(two.user)

    async with client_for(one.host) as client:
        created = await client.post(
            "/api/v1/invoicing/templates",
            json={"name": "Alleen van ons", "config": {"design": "letterhead"}},
            headers=headers_one,
        )
        assert created.status_code == 201
        template_id = created.json()["id"]

    async with client_for(two.host) as client:
        listed = await client.get("/api/v1/invoicing/templates", headers=headers_two)
        assert all(t["id"] != template_id for t in listed.json())
        # And it cannot be attached to their own document by id.
        company = await client.post(
            "/api/v1/companies", json={"name": "Andere klant"}, headers=headers_two
        )
        attached = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company.json()["id"],
                "template_id": template_id,
                "lines": [{"description": "x", "quantity": "1", "unit_price": "1"}],
            },
            headers=headers_two,
        )
        assert attached.status_code == 400
        patched = await client.patch(
            f"/api/v1/invoicing/templates/{template_id}",
            json={"name": "gekaapt"},
            headers=headers_two,
        )
        assert patched.status_code == 404


async def test_authoring_html_needs_its_own_permission(client_for) -> None:
    """Arranging blocks is ``settings.manage``; writing Jinja that runs on the agency's own
    server is a strictly larger act, so it is a permission of its own."""
    tenant: Tenant = await make_tenant("render-author")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        from app.db import async_session_maker, set_current_org

        created = await client.post(
            "/api/v1/invoicing/templates",
            json={"name": "Eigen", "config": {"design": "custom", "html": "<p>{{ heading }}</p>"}},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        template_id = created.json()["id"]

        # Strip the author permission from the owner's role and try again. The owner holds
        # "*", so the permission has to be taken away at the role rather than by inventing a
        # second user — which is also the shape a tenant would use to let an office manager
        # rearrange an invoice without handing them the code editor.
        from app.core.permissions.service import replace_permissions, role_by_key

        async with async_session_maker() as session:
            await set_current_org(session, tenant.org.id)
            role = await role_by_key(session, tenant.org.id, "owner")
            await replace_permissions(
                session,
                tenant.org.id,
                role.id,
                [
                    "invoicing.settings.manage",
                    "invoicing.invoice.read",
                    "invoicing.invoice.write",
                ],
            )
            await session.commit()

        refused = await client.patch(
            f"/api/v1/invoicing/templates/{template_id}",
            json={"config": {"design": "custom", "html": "<p>anders</p>"}},
            headers=headers,
        )
        assert refused.status_code == 403

        # But an *unchanged* body passes: an admin without the permission must still be able
        # to rename a template that happens to carry custom HTML, not have to delete it.
        renamed = await client.patch(
            f"/api/v1/invoicing/templates/{template_id}",
            json={"name": "Andere naam",
                  "config": {"design": "custom", "html": "<p>{{ heading }}</p>"}},
            headers=headers,
        )
        assert renamed.status_code == 200, renamed.text


async def test_rendering_costs_a_fixed_number_of_queries_however_many_lines(
    client_for, count_queries
) -> None:
    """Rendering is per-document work, so its query count must not follow the line count.

    The shape this pins is invisible in the output: a design that reached for a tax rate or a
    product row per line would render an identical document and cost one query per line, and
    only a three-hundred-line invoice would ever show it (docs/PERFORMANCE.md).
    """
    tenant: Tenant = await make_tenant("render-budget")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company = await client.post(
            "/api/v1/companies", json={"name": "Klant BV"}, headers=headers
        )

        async def render(line_count: int) -> int:
            created = await client.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company.json()["id"],
                    "lines": [
                        {"description": f"Regel {i}", "quantity": "1", "unit_price": "10"}
                        for i in range(line_count)
                    ],
                },
                headers=headers,
            )
            invoice_id = created.json()["id"]
            with count_queries() as counter:
                resp = await client.get(
                    f"/api/v1/invoicing/invoices/{invoice_id}/preview", headers=headers
                )
            assert resp.status_code == 200
            assert f"Regel {line_count - 1}" in resp.text
            return len(counter.statements)

        two_lines = await render(2)
        forty_lines = await render(40)
        assert forty_lines == two_lines, "the render must not pay per line"


def test_a_written_off_invoice_does_not_ask_for_the_money_back() -> None:
    """The payment ask follows `outstanding`, and crediting is one of the two things that
    moves it.

    The amount used to read `outstanding if paid else total` — the same expression twice
    while payments were the only way a balance came down. A credited invoice has no payments,
    so it fell to `total` and printed "Gelieve € 500,00 over te maken" for money the client
    had just been relieved of. Nothing outstanding now means no card and no sentence, and the
    totals block says *why* rather than showing an unexplained zero.
    """
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.paid_total = Decimal("0")
    doc.credited_total = doc.total
    html = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER,
        config={"design": "letterhead", "layout": [{"key": "payment_box", "enabled": True}]},
        brand=DocumentBrand(name="Agency"), tax_groups=groups,
    )
    assert "Betaalgegevens" not in html
    assert "Gelieve" not in html
    assert "Gecrediteerd" in html, "the paper has to explain where the total went"

    # Partly credited: still owed, so the ask comes back — for the netted amount only.
    doc.credited_total = Decimal("100.00")
    partial = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER,
        config={"design": "letterhead", "layout": [{"key": "payment_box", "enabled": True}]},
        brand=DocumentBrand(name="Agency"), tax_groups=groups,
    )
    assert "Betaalgegevens" in partial
    assert "Gecrediteerd" in partial


# --------------------------------------------------------------------------- #
# The portal QR (issue #268)
# --------------------------------------------------------------------------- #
_QR_ON = {"design": "letterhead", "layout": [{"key": "payment_qr", "enabled": True}]}
_PAY_URL = "https://bureau.schakl.app/invoices/6f1a0d5c-2f0e-4e9f-9c3f-0a1b2c3d4e5f"


def _with_qr(**overrides) -> str:  # noqa: ANN003 — mirrors ``_render``'s shape
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.status = overrides.pop("status", "open")
    for key, value in overrides.pop("doc", {}).items():
        setattr(doc, key, value)
    return render_document_html(
        kind=overrides.pop("kind", "invoice"),
        doc=doc,
        lines=lines,
        seller=SELLER,
        config=overrides.pop("config", _QR_ON),
        brand=DocumentBrand(name="Agency"),
        tax_groups=groups,
        pay_url=overrides.pop("pay_url", _PAY_URL),
        payable_online=overrides.pop("payable_online", True),
    )


def test_the_qr_is_off_until_a_template_asks_for_it() -> None:
    """A block a tenant never enabled must not appear on documents they already send."""
    assert 'class="payment-qr' not in _with_qr(config={"design": "letterhead"})
    assert 'class="payment-qr' in _with_qr()


def test_the_qr_is_an_inline_svg_and_survives_the_print() -> None:
    """The document CSP allows ``img-src data:`` and nothing else, and the renderer resolves no
    URL at all — an ``<img src="https://…">`` would be blocked in the preview and blank in the
    PDF. So the code has to be markup the page already carries, and #268 asks for more than
    "no exception": the bytes have to come out the other end."""
    html = _with_qr()
    block = html[html.index('class="payment-qr') :]
    assert "<svg" in block[:400]
    assert "<img" not in block[: block.index("</div>")]

    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.status = "open"
    with_code = render_document_pdf(
        kind="invoice", doc=doc, lines=lines, seller=SELLER, config=_QR_ON,
        brand=DocumentBrand(name="Agency"), tax_groups=groups,
        pay_url=_PAY_URL, payable_online=True,
    )
    without = render_document_pdf(
        kind="invoice", doc=doc, lines=lines, seller=SELLER, config={"design": "letterhead"},
        brand=DocumentBrand(name="Agency"), tax_groups=groups,
    )
    assert with_code.startswith(b"%PDF")
    # WeasyPrint draws the matrix as vector paths, so the page with a code in it is materially
    # bigger. A "renders without raising" assertion passes just as well on a blank 24 mm box.
    assert len(with_code) > len(without) + 1000


def test_the_caption_says_pay_only_when_something_can_collect() -> None:
    """The code works either way — it opens the invoice — so a connected provider changes the
    words and nothing else. Promising "scan om te betalen" with nothing to pay through would be
    a control that refuses (#253), printed on paper where nobody can fix it."""
    assert "Scan om te betalen" in _with_qr(payable_online=True)
    assert "Scan om deze factuur te bekijken" in _with_qr(payable_online=False)


def test_the_qr_never_appears_where_there_is_nothing_to_pay() -> None:
    """The payment card's three conditions, for the same reasons: a credit note is money going
    the other way, a settled invoice owes nothing, and a draft's portal page 404s for the very
    client it would send there (``Invoice.__portal_horizon_clause__``)."""
    assert 'class="payment-qr' not in _with_qr(status="draft")
    assert 'class="payment-qr' not in _with_qr(doc={"paid_total": Decimal("99999.00")})
    assert 'class="payment-qr' not in _with_qr(doc={"kind": "credit_note"})
    # No host resolved means nothing to encode; a quote is never paid from a portal page.
    assert 'class="payment-qr' not in _with_qr(pay_url=None)
    assert 'class="payment-qr' not in _with_qr(kind="quote")


def test_the_qr_encodes_the_portal_url_and_never_a_checkout_url() -> None:
    """The security decision worth pinning: a provider's checkout URL is a bearer credential,
    and printing one on paper hands whoever picks that paper up somebody else's bill. What the
    document gets is the invoice's page in the portal, behind the login #193 established."""
    from app.modules.invoicing.paylinks import invoice_pay_url
    from app.modules.invoicing.render.qr import qr_svg

    assert invoice_pay_url("https://bureau.schakl.app/", "abc") == (
        "https://bureau.schakl.app/invoices/abc"
    )
    # Deterministic, and genuinely a function of the payload — a code that came out identical
    # for two different URLs would pass every other assertion in this file.
    assert qr_svg(_PAY_URL) == qr_svg(_PAY_URL)
    assert qr_svg(_PAY_URL) != qr_svg("https://www.mollie.com/checkout/select-method/abc")
    assert qr_svg("") == ""


# --------------------------------------------------------------------------- #
# The pay-online line (epic #269) — the QR's twin, in words
# --------------------------------------------------------------------------- #
_LINK_ON = {"design": "letterhead", "layout": [{"key": "payment_link", "enabled": True}]}
#: The marker every switch test below reads. It is on the anchor rather than on a wrapper,
#: because *whether* a document offers the line is a property of the layout and the wrapper is
#: a property of the design that arranged it: the letterhead now draws both halves as one strip
#: inside its payment card, and seven tests bound to `class="payment-link"` failed a change
#: that broke nothing. `payment-qr-code` is the same marker for the code, and both are stated
#: as shared vocabulary in ``_blocks.html``.
_LINK = 'class="pay-online-link"'
_CODE = 'class="payment-qr-code"'


def test_the_pay_line_is_off_until_a_template_asks_for_it() -> None:
    assert _LINK not in _with_qr(config={"design": "letterhead"})
    assert _LINK in _with_qr(config=_LINK_ON)


def test_the_pay_line_prints_the_url_as_well_as_linking_it() -> None:
    """A document is read on two surfaces. A PDF viewer follows the anchor; paper has to be
    typed, so the address itself is on the page rather than hidden behind a word."""
    html = _with_qr(config=_LINK_ON)
    assert f'href="{_PAY_URL}"' in html
    block = html[html.index(_LINK) :]
    assert _PAY_URL in block[: block.index("</div>")], "the URL is linked but never shown"
    assert "Betaal deze factuur online" in html


def test_the_pay_line_and_the_qr_switch_independently() -> None:
    """Two affordances for one destination, and an agency printing monochrome on a copier may
    reasonably want the line without the code."""
    both = _with_qr(
        config={
            "design": "letterhead",
            "layout": [
                {"key": "payment_link", "enabled": True},
                {"key": "payment_qr", "enabled": True},
            ],
        }
    )
    assert _LINK in both and _CODE in both
    assert _CODE not in _with_qr(config=_LINK_ON)
    assert _LINK not in _with_qr()


def test_the_pay_line_never_appears_where_there_is_nothing_to_pay() -> None:
    """Exactly the QR's conditions — they share one predicate, so they cannot drift."""
    assert _LINK not in _with_qr(config=_LINK_ON, status="draft")
    assert _LINK not in _with_qr(config=_LINK_ON, doc={"paid_total": Decimal("99999.00")})
    assert _LINK not in _with_qr(config=_LINK_ON, doc={"kind": "credit_note"})
    assert _LINK not in _with_qr(config=_LINK_ON, pay_url=None)


def test_the_document_qr_is_branded_by_default_and_clickable() -> None:
    """A QR on a client's invoice should look like *the agency's* (epic #269), and a code on a
    PDF opened on a laptop should be pressable — the one case a QR serves worst."""
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.status = "open"
    html = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER, config=_QR_ON,
        brand=DocumentBrand(name="Agency", primary_color="#4f46e5"),
        tax_groups=groups, pay_url=_PAY_URL, payable_online=True,
    )
    # The accent reaches the modules, and the code is wrapped in the same destination.
    assert "#4f46e5" in html[html.index('class="payment-qr') :]
    assert f'href="{_PAY_URL}"' in html[html.index('class="payment-qr') :]
    # segno emits width/height only; without a viewBox the CSS box resizes the viewport rather
    # than the symbol and the code overflows its 24mm square.
    assert "viewBox" in html


def test_a_plain_qr_style_drops_the_colour_and_the_logo() -> None:
    """The escape hatch for an agency printing monochrome, or one whose logo does not survive
    being seven modules across."""
    plain = {"design": "letterhead", "qr_style": "plain",
             "layout": [{"key": "payment_qr", "enabled": True}]}
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.status = "open"
    html = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER, config=plain,
        brand=DocumentBrand(name="Agency", primary_color="#4f46e5"),
        tax_groups=groups, pay_url=_PAY_URL, payable_online=True,
    )
    block = html[html.index('class="payment-qr') :]
    assert "#4f46e5" not in block
    assert "data:image" not in block, "a plain code carries no logo"


def test_a_pale_brand_colour_never_reaches_the_code() -> None:
    """The one failure a QR cannot afford is being beautiful and unreadable, and the person
    holding the paper cannot squint harder (``render/qr.readable_dark``)."""
    doc, lines, groups = sample_document("nl", "EUR", TODAY)
    doc.status = "open"
    html = render_document_html(
        kind="invoice", doc=doc, lines=lines, seller=SELLER, config=_QR_ON,
        brand=DocumentBrand(name="Agency", primary_color="#ffe066"),
        tax_groups=groups, pay_url=_PAY_URL, payable_online=True,
    )
    block = html[html.index('class="payment-qr') :]
    assert "#ffe066" not in block


# --------------------------------------------------------------------------- #
# How the two of them are printed and arranged
# --------------------------------------------------------------------------- #
#: Every way to pay at once — the card, the code and the line. All three ship off, so this is
#: the arrangement no default exercises, and it is the one the QR was broken in.
_PAY_ON = [
    {"key": "payment_box", "enabled": True},
    {"key": "payment_qr", "enabled": True},
    {"key": "payment_link", "enabled": True},
]


@pytest.mark.parametrize("design", ["classic", "letterhead"])
def test_the_qr_prints_at_the_size_the_design_asks_for(design: str) -> None:
    """24 mm is the smallest a phone camera reads reliably off paper — and it is a *box* the
    anchor has to have before the number means anything.

    It had none. An ``<a>`` is inline, ``width``/``height`` do not apply to it, and the svg's
    ``100%`` then resolved against the paragraph: the code came out the full width of the sheet
    and the sample ran to three pages, identically in the preview and in the PDF. Every other
    test here reads the markup, and the markup was right in both states — so this one measures
    the laid-out box, and it is all that stands between that rule and a stylesheet edit that
    quietly deletes it again.
    """
    boxes = [
        box
        for box in _boxes(_with_qr(config={"design": design, "layout": _PAY_ON}))
        if str(box.element_tag).endswith("}svg")
    ]
    assert len(boxes) == 1, "the QR is the only inline SVG the sample draws"
    # 24 mm in CSS pixels. Loose by a pixel: this asks for a stamp, not a poster.
    assert boxes[0].width == pytest.approx(24 * 96 / 25.4, abs=1.0)
    assert boxes[0].height == pytest.approx(24 * 96 / 25.4, abs=1.0)


@pytest.mark.parametrize("design", ["classic", "letterhead"])
def test_the_printed_pay_url_keeps_its_own_case(design: str) -> None:
    """The URL is printed as well as linked for one reader: the one who cannot click it.

    It was set in ``micro``, the house label style, which uppercases — so what reached the paper
    was ``HTTPS://…/INVOICES/6F1A…``, and a URL path is case-sensitive: the route is
    ``/invoices/[id]``, and anyone who typed what they were given landed on a 404. The markup
    carried the right characters either way, so the assertion is on the *rendered* text.
    """
    printed = "".join(
        box.text
        for box in _boxes(_with_qr(config={"design": design, "layout": _PAY_ON}))
        if getattr(box, "text", None)
    )
    assert "/invoices/" in printed
    assert "/INVOICES/" not in printed


def test_the_letterhead_asks_for_the_money_in_one_box() -> None:
    """The code and the line are body blocks in ``classic``'s stack. In this design they belong
    *inside* the payment card: they answer the same question as the IBAN above them, and left
    in the loop they landed centimetres lower, in the open middle of the sheet.

    Ancestry and not source order, because "below the card" satisfies an index comparison
    perfectly well and is exactly the arrangement this replaced.
    """
    html = _with_qr(config={"design": "letterhead", "layout": _PAY_ON})
    assert _descends(html, inner="pay-online", outer="payment-card")
    # ...and exactly once. Each is drawn by hand *and* skipped in the body loop; getting one of
    # the two wrong prints the code twice, which is the mistake the closing band's own test
    # guards against for the VAT breakdown.
    assert html.count(_CODE) == 1
    assert html.count(_LINK) == 1


def test_the_pay_strip_stands_on_its_own_without_the_card() -> None:
    """The three switches are independent, so the code and the line have to print with the box
    switched off — the left column is where this design puts how to settle the invoice, drawn
    around or not. Hanging them off the card alone would have made ``payment_box`` a silent
    third switch on both of them.
    """
    html = _with_qr(
        config={
            "design": "letterhead",
            "layout": [*_PAY_ON, {"key": "payment_box", "enabled": False}],
        }
    )
    assert "Betaalgegevens" not in html
    assert _CODE in html and _LINK in html
    assert not _descends(html, inner="pay-online", outer="payment-card")


def test_the_code_is_captioned_only_when_nothing_else_says_what_it_is() -> None:
    """"Scan om te betalen" draws the distinction the pay-online label already draws (#253's
    "betalen" against "bekijken"), about a picture nobody needs told is scannable. Beside that
    label it is a third line under the address that reads as belonging to the address. Without
    it, it is the only thing saying the code is worth pointing a phone at, and it prints.
    """
    both = _with_qr(config={"design": "letterhead", "layout": _PAY_ON})
    assert "Betaal deze factuur online" in both
    assert "Scan om te betalen" not in both

    code_only = _with_qr(
        config={
            "design": "letterhead",
            "layout": [*_PAY_ON, {"key": "payment_link", "enabled": False}],
        }
    )
    assert "Scan om te betalen" in code_only


def test_the_pay_line_reads_as_viewing_when_nothing_can_collect() -> None:
    """Same rule as the QR's caption: the page still works without a provider — it opens the
    invoice — so the words change and the link stays."""
    assert "Betaal deze factuur online" in _with_qr(config=_LINK_ON, payable_online=True)
    assert "Bekijk deze factuur online" in _with_qr(config=_LINK_ON, payable_online=False)
