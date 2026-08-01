"""The document renderer: layout resolution, the shipped designs, and the sandbox.

Split from ``test_invoicing_api`` because what it guards is a different kind of thing. That
suite asks whether the money is right; this one asks whether the *paper* is — which pieces
print, in which order, and what a tenant's own template is allowed to reach while printing it.

The sandbox tests are the ones that must not be deleted. Everything else here fails visibly:
a block in the wrong order is a screenshot away. A Jinja escape is invisible until someone
uses it, so the escapes get a named test each, and the assertion is on the *refusal*.
"""

from __future__ import annotations

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
    locale (§17). A label leaking in here is how a Dutch admin gets an English invoice field."""
    for block in catalog_payload():
        assert set(block) == {"key", "region", "default", "locked", "movable", "fields"}
        for field in block["fields"]:
            assert set(field) == {"key", "default", "locked"}


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
