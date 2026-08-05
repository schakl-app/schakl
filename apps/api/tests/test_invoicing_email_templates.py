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
