"""The three mails a client reads are the tenant's to write (#161 tier 2 x #207).

The invoice, quote and reminder mails are customisable kinds contributed by the invoicing
module, so this covers both delivery paths — the request one (``POST /send``, ``/remind``) and
the cron one (``invoicing_daily``) — because they compose through the same seam but reach it
differently, and a template that only the manual send honoured would be the worst outcome
available: every dunning mail an agency actually sends comes off the schedule.

Also the invariants that make an override safe: the plaintext part still carries the catalog
summary whatever the HTML says, and the covering note the sender typed leads *both* parts.
"""

from __future__ import annotations

import re
from datetime import timedelta

from app.core.email.senders import Sender
from tests.conftest import Tenant, auth_cookie, make_tenant, org_today

_BREVO = {
    "provider": "brevo",
    "from_email": "noreply@agency-example.nl",
    "from_name": "Agency",
    "api_key": "xkeysib-secret-123",
}

_today = org_today


async def _setup_org(client, headers) -> None:
    resp = await client.put(
        "/api/v1/invoicing/settings",
        json={"company_details": {"name": "Agency BV", "city": "Amsterdam", "country": "NL"}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert (await client.get("/api/v1/invoicing/tax-rates", headers=headers)).status_code == 200
    assert (
        await client.put("/api/v1/settings/email", json=_BREVO, headers=headers)
    ).status_code == 200


async def _company(client, headers) -> str:
    resp = await client.post(
        "/api/v1/companies",
        json={"name": "Klant BV", "invoice_email": "boekhouding@klant.nl"},
        headers=headers,
    )
    return resp.json()["id"]


async def _open_invoice(client, headers, company_id: str, *, due_days: int = 14) -> dict:
    invoice = (
        await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "locale": "nl",
                "lines": [{"description": "Werk", "quantity": "1", "unit_price": "100"}],
            },
            headers=headers,
        )
    ).json()
    issued = await client.post(
        f"/api/v1/invoicing/invoices/{invoice['id']}/issue",
        json={"due_date": (_today() + timedelta(days=due_days)).isoformat()},
        headers=headers,
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _save_template(client, headers, kind: str, **body) -> None:
    saved = await client.put(
        "/api/v1/settings/email/templates",
        json={"kind": kind, "locale": "nl", **body},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text


async def test_invoice_send_uses_the_tenant_template(client_for, monkeypatch) -> None:
    tenant: Tenant = await make_tenant("invtpl-send")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        await _save_template(
            client,
            headers,
            "invoicing.invoice",
            subject="Factuur {number} — {company}",
            body_html=(
                "<p>Beste {contact}, hierbij {number} van {total}.</p>"
                "<script>evil()</script>"
            ),
        )
        invoice = await _open_invoice(client, headers, company_id)
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl", "message": "Fijne feestdagen!"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    assert len(sent) == 1
    message = sent[0]
    assert message.to == "info@klant.nl"
    # Subject: the override, with the document's own values substituted.
    assert message.subject == f"Factuur {invoice['number']} — Klant BV"
    # HTML: the override, sanitised, wrapped in the tenant's chrome, and the covering note
    # leading it — a sentence the plaintext carries must never be missing from the HTML.
    assert "evil()" not in message.html and "<script" not in message.html
    assert f"hierbij {invoice['number']} van EUR" in message.html
    assert "Fijne feestdagen!" in message.html
    assert message.html.lstrip().lower().startswith("<!doctype")
    # Plaintext: still the catalog summary, so the client always gets the amount and due date.
    assert message.text.startswith("Fijne feestdagen!")
    assert invoice["number"] in message.text and "vervaldatum" in message.text.lower()
    # The document itself rides along, as it did before templates existed.
    assert [a.mimetype for a in message.attachments] == ["application/pdf"]


async def test_quote_send_uses_its_own_template(client_for, monkeypatch) -> None:
    tenant: Tenant = await make_tenant("invtpl-quote")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        await _save_template(
            client, headers, "invoicing.quote", subject="Offerte {number}, geldig tot {valid_until}"
        )
        quote = (
            await client.post(
                "/api/v1/invoicing/quotes",
                json={
                    "company_id": company_id,
                    "locale": "nl",
                    "lines": [{"description": "Werk", "quantity": "1", "unit_price": "10"}],
                },
                headers=headers,
            )
        ).json()
        issued = (
            await client.post(
                f"/api/v1/invoicing/quotes/{quote['id']}/issue",
                json={"due_date": (_today() + timedelta(days=30)).isoformat()},
                headers=headers,
            )
        ).json()
        resp = await client.post(
            f"/api/v1/invoicing/quotes/{quote['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    valid_until = _today() + timedelta(days=30)
    assert sent[0].subject == (
        f"Offerte {issued['number']}, geldig tot {valid_until.strftime('%d-%m-%Y')}"
    )
    # The invoice template is a different kind: writing one never rewrites the other.
    assert "Offerte" in sent[0].subject


async def test_reminder_template_reaches_the_cron_too(client_for, monkeypatch) -> None:
    """The schedule sends the dunning mail an agency actually sends, so the cron path must
    honour the override — composing it only on the manual ``/remind`` would customise the
    exception and leave the rule alone."""
    from app.modules.invoicing.jobs import invoicing_daily

    tenant: Tenant = await make_tenant("invtpl-cron")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await client.put(
            "/api/v1/invoicing/settings",
            json={"reminders_enabled": True, "reminder_days": [7]},
            headers=headers,
        )
        company_id = await _company(client, headers)
        await _save_template(
            client,
            headers,
            "invoicing.reminder",
            subject="Herinnering {number}: {outstanding} open",
        )
        await _open_invoice(client, headers, company_id, due_days=-15)

    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    async def fake_transport(session, org_id):  # noqa: ANN001, ARG001
        return ("smtp", {}, Sender(from_email="mail@agency.nl", from_name="Agency"))

    monkeypatch.setattr("app.modules.invoicing.jobs.send_email", fake_send)
    monkeypatch.setattr("app.modules.invoicing.jobs.load_transport", fake_transport)

    await invoicing_daily({})

    assert len(sent) == 1
    assert sent[0].subject.startswith("Herinnering ")
    assert "EUR 121.00 open" in sent[0].subject
    assert sent[0].html.lstrip().lower().startswith("<!doctype")


async def test_without_an_override_the_built_in_mail_is_unchanged(client_for, monkeypatch) -> None:
    """The upgrade is silent: an instance that writes no template sends exactly what it sent
    before, catalog subject and all."""
    tenant: Tenant = await make_tenant("invtpl-default")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _open_invoice(client, headers, company_id)
        await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )

    assert sent[0].subject == f"Factuur {invoice['number']} van {tenant.org.name}"
    assert "Beste Klant BV" in sent[0].text


async def test_template_preview_renders_a_sample_document(client_for, monkeypatch) -> None:
    """The editor's test send previews a *plausible* invoice — the same fabricated document
    the PDF template editor draws, in the org's currency."""
    tenant: Tenant = await make_tenant("invtpl-preview")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.core.email.service.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        resp = await client.post(
            "/api/v1/settings/email/templates/test",
            json={
                "kind": "invoicing.reminder",
                "locale": "nl",
                "subject": "Test {number}",
                "body_html": "<p>{company} — {outstanding} open, {days} dagen</p>",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

    message = sent[0]
    assert message.to == tenant.user.email
    # Every declared variable resolved to something: a marker left unfilled would reach the
    # inbox as a literal "{days}".
    assert "{number}" not in message.subject and message.subject != "Test "
    assert "{company}" not in message.html and "{outstanding}" not in message.html
    assert "14 dagen" in message.html
    assert "EUR" in message.html


# --------------------------------------------------------------------------------------- #
# The pay button (epic #269)
# --------------------------------------------------------------------------------------- #
_LIVE_KEY = "live_JhRk9NcQdTzWbV4pM2sXgY7eF3uL5aKq"


async def _connect_mollie(client, headers) -> None:
    resp = await client.post(
        "/api/v1/mollie/accounts",
        json={"name": "Mollie", "api_key": _LIVE_KEY},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


async def test_the_invoice_mail_offers_the_portal_and_never_a_checkout_url(
    client_for, monkeypatch
) -> None:
    """The pay button leads to **our** page, and that is the whole design (``paylinks``).

    A provider's checkout URL is a bearer credential, it expires in minutes, and mailing one
    lets a client hold two valid ways to pay one debt. So the mail carries the invoice's
    portal address and the checkout is minted on the far side, once, when they press.
    """
    tenant: Tenant = await make_tenant("invtpl-pay")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await _connect_mollie(client, headers)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    message = sent[0]
    expected = f"/invoices/{invoice['id']}"
    # The branded CTA, pointing at the portal, in both parts of the mail.
    assert "Nu betalen" in message.html
    assert expected in message.html
    assert expected in message.text
    # …and nothing anywhere in the mail points at the provider.
    assert "mollie.com" not in message.html and "mollie.com" not in message.text


async def test_no_provider_means_no_button_and_an_unchanged_mail(
    client_for, monkeypatch
) -> None:
    """An instance that has never heard of online payments sends the mail it sent yesterday.

    Not "a button that 404s", not "an empty paragraph where a button would be": the catalog
    body's ``{link}`` paragraph disappears whole, in the HTML *and* the plaintext. A blank line
    in the middle of a client-facing letter is the kind of thing nobody reports and everybody
    notices.
    """
    tenant: Tenant = await make_tenant("invtpl-nopay")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)  # deliberately no Mollie account
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    message = sent[0]
    assert "Nu betalen" not in message.html
    assert f"/invoices/{invoice['id']}" not in message.html
    # No dead anchor, no hole in either part.
    assert 'href=""' not in message.html
    assert "\n\n\n" not in message.text
    assert "{link}" not in message.text and "{link}" not in message.html
    # The mail is otherwise exactly itself.
    assert invoice["number"] in message.text and "vervaldatum" in message.text.lower()


async def test_a_settled_invoice_is_never_mailed_a_pay_button(client_for, monkeypatch) -> None:
    """``is_collectable`` is the same predicate the document's QR and the portal button use.

    A reminder is the mail where this matters most: the cron only chases what is owed, but a
    payment can land between the query and the send, and a client who has just paid must not
    be handed a button asking them to do it again.
    """
    tenant: Tenant = await make_tenant("invtpl-paid")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await _connect_mollie(client, headers)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        paid = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": invoice["total"], "method": "bank"},
            headers=headers,
        )
        assert paid.status_code == 200, paid.text
        assert paid.json()["status"] == "paid"
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    assert "Nu betalen" not in sent[0].html


async def test_a_tenants_own_template_may_place_the_link_itself(client_for, monkeypatch) -> None:
    """``{link}`` is a declared variable, so an agency that wants the button inside a sentence
    — or styled as their own — writes their own anchor and gets the same destination."""
    tenant: Tenant = await make_tenant("invtpl-paylink-own")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await _connect_mollie(client, headers)
        await _save_template(
            client,
            headers,
            "invoicing.invoice",
            subject="Factuur {number}",
            body_html='<p>Betaal factuur {number} <a href="{link}">hier</a>.</p>',
        )
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    html = sent[0].html
    assert 'href="https://' in html
    assert f"/invoices/{invoice['id']}" in html
    assert "mollie.com" not in html


# --------------------------------------------------------------------------------------- #
# The inline QR (epic #269)
# --------------------------------------------------------------------------------------- #
_SMTP2GO = {
    "provider": "smtp2go",
    "from_email": "noreply@agency-example.nl",
    "from_name": "Agency",
    "api_key": "api-secret-123",
}


async def test_the_mail_carries_a_clickable_qr_as_an_inline_part(client_for, monkeypatch) -> None:
    """The QR answers the one case the button cannot: reading on a laptop, paying by phone.

    Three things have to hold at once, and each has failed on its own in some product: the
    image is a **real inline MIME part** (not a paperclip, not a remote URL that would report
    the open back to us), the body references it by the cid that *is* its filename, and the
    code is **wrapped in the same link** — a reader on the device they would pay from should
    not have to go and find their phone.
    """
    tenant: Tenant = await make_tenant("invtpl-qr")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        # SMTP2GO carries inline images; the fixture default (Brevo) does not.
        assert (
            await client.put("/api/v1/settings/email", json=_SMTP2GO, headers=headers)
        ).status_code == 200
        await _connect_mollie(client, headers)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    message = sent[0]
    inline = [a for a in message.attachments if a.inline]
    assert len(inline) == 1
    qr = inline[0]
    assert qr.filename == "invoice-qr.png"
    assert qr.mimetype == "image/png"
    assert qr.content[:4] == b"\x89PNG"
    # The body points at it by the filename — the cid every transport agrees on.
    assert 'src="cid:invoice-qr.png"' in message.html
    # …and the code is a link to the same portal page the button uses.
    anchor = re.search(r'<a href="([^"]+)"[^>]*>\s*<img src="cid:invoice-qr\.png"', message.html)
    assert anchor is not None, "the QR is not clickable"
    assert anchor.group(1).endswith(f"/invoices/{invoice['id']}")
    # The PDF is still an ordinary attachment beside it.
    assert [a.filename for a in message.attachments if not a.inline][0].endswith(".pdf")
    # Plaintext never carries markup — an image has no plaintext form.
    assert "<img" not in message.text and "{image}" not in message.text


async def test_a_transport_that_cannot_inline_gets_the_button_and_no_broken_image(
    client_for, monkeypatch
) -> None:
    """Brevo's API has no Content-ID mechanism at all (``docs/EMAIL.md``).

    The failure this prevents is specific and ugly: an ``<img src="cid:…">`` composed for a
    transport that drops the part renders as a broken-image box in the client's inbox, exactly
    where the code should be. Asking ``supports_inline_images`` *before* composing is what lets
    the fallback be chosen rather than discovered — and the fallback is the pay button, which
    every client draws.
    """
    tenant: Tenant = await make_tenant("invtpl-qr-brevo")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)  # _BREVO
        await _connect_mollie(client, headers)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    message = sent[0]
    assert [a for a in message.attachments if a.inline] == []
    assert "cid:" not in message.html
    assert "<img" not in message.html
    # The way in survives, in the form this transport can carry.
    assert "Nu betalen" in message.html
    assert f"/invoices/{invoice['id']}" in message.html


async def test_no_provider_means_no_qr_either(client_for, monkeypatch) -> None:
    """The QR is a payment affordance, so it follows the button exactly: an org with nothing to
    collect through sends the mail it always sent, with no image part attached to it."""
    tenant: Tenant = await make_tenant("invtpl-qr-none")
    headers = await auth_cookie(tenant.user)
    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.modules.invoicing.emails.send_email", fake_send)

    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        assert (
            await client.put("/api/v1/settings/email", json=_SMTP2GO, headers=headers)
        ).status_code == 200
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/send",
            json={"to": "info@klant.nl"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    assert [a for a in sent[0].attachments if a.inline] == []
    assert "cid:" not in sent[0].html
