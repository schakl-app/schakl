"""A standard subscription's notes may print on the invoices its agreements raise (#259, cont.).

The notes were a transparency field the client never saw: "what we do for you and what you may
expect", authored once on the preset with ``{{company_name}}``-style variables, and read by the
agency alone. Whether they belong on the invoice is the preset's decision (``notes_on_invoice``,
off unless a tenant says so), one agreement may say otherwise for itself
(``notes_on_invoice_override``, ``NULL`` follows), and one resolution is read by the cycle cron,
the editor's picker and the agreement's own read alike — a test per reader, plus the variable
contract pinned against the web's copy of it, and the markup pinned against the paper.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.billing import add_months
from app.modules.subscriptions.jobs import advance_subscriptions
from app.modules.subscriptions.variables import (
    NOTE_VARIABLES,
    note_values,
    resolve_note_variables,
)
from tests.conftest import auth_cookie, make_tenant, org_today

NOTE = (
    "### Wat {{brand_name}} voor {{company_name}} doet\n\n"
    "- **{{subscription_name}}** ({{type}}), {{interval}} voor {{amount}}\n"
    "- {{included_hours}} uur inbegrepen, sinds {{start_date}}\n\n"
    "> Vragen? Mail ons gerust. {{unknown_token}}"
)


def _iso(day) -> str:
    return day.isoformat()


async def _company(client, headers, name: str = "Klant BV") -> str:
    resp = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _hosting_type(client, headers) -> dict:
    types = (await client.get("/api/v1/subscriptions/types", headers=headers)).json()
    return next(row for row in types if row["key"] == "hosting")


async def _template(client, headers, *, notes_on_invoice: bool, type_id: str) -> dict:
    resp = await client.post(
        "/api/v1/subscriptions/templates",
        json={
            "name": "Hosting Pro",
            "subscription_type_id": type_id,
            "interval": "monthly",
            "amount": "25.00",
            "included_hours": "2",
            "notes": NOTE,
            "notes_on_invoice": notes_on_invoice,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _subscription(client, headers, company: str, **extra) -> dict:
    anchor = add_months(org_today(), -1)
    payload = {
        "company_id": company,
        "name": "Hosting Pro",
        "status": "active",
        "interval": "monthly",
        "amount": "25.00",
        "included_hours": "2",
        "start_date": _iso(add_months(anchor, -6)),
        "next_invoice_date": _iso(anchor),
        "notes": NOTE,
        **extra,
    }
    resp = await client.post("/api/v1/subscriptions", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _picker_note(client, headers, company: str, sub_id: str) -> str | None:
    outstanding = (
        await client.get(
            "/api/v1/invoicing/outstanding", params={"company_id": company}, headers=headers
        )
    ).json()
    return next(s for s in outstanding["subscriptions"] if s["id"] == sub_id)["notes"]


# --- the vocabulary and the contract, pinned against the web -------------------------------


def test_the_variables_are_the_web_s_variables() -> None:
    """Two copies of one vocabulary: the web resolves for display, the API for the document.
    A slug added on one side and not the other prints as a raw ``{{token}}`` on the other."""
    source = (
        Path(__file__).resolve().parents[3]
        / "apps/web/src/lib/modules/subscriptions/variables.ts"
    ).read_text(encoding="utf-8")
    block = re.search(
        r"SUBSCRIPTION_NOTE_VARIABLES = \[(.*?)\] as const", source, re.DOTALL
    )
    assert block is not None
    web = tuple(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert web == NOTE_VARIABLES


def test_resolution_contract_matches_the_web_s() -> None:
    """Known with a value → the value; known without → nothing (a document never prints a
    raw variable); unknown → left verbatim so its author can see and fix it."""
    out = resolve_note_variables(
        "{{company_name}} / {{ amount }} / {{included_hours}} / {{typo_here}}",
        {"company_name": "Klant BV", "amount": "€ 25,00", "included_hours": None},
    )
    assert out == "Klant BV / € 25,00 /  / {{typo_here}}"
    assert resolve_note_variables(None, {}) == ""


def test_values_are_printed_the_way_the_document_prints_them() -> None:
    from datetime import date
    from decimal import Decimal

    values = note_values(
        company_name="Klant BV",
        subscription_name="Hosting Pro",
        type_label="Hosting",
        amount=Decimal("1250.5"),
        currency="EUR",
        interval="monthly",
        included_hours=Decimal("2.50"),
        start_date=date(2026, 3, 1),
        brand_name="Bureau",
        locale="nl",
    )
    assert values["amount"] == "€ 1.250,50"
    assert values["interval"] == "Maandelijks"
    assert values["included_hours"] == "2,5"
    assert values["start_date"] == "01-03-2026"
    assert note_values(
        company_name=None,
        subscription_name=None,
        type_label=None,
        amount=None,
        currency="EUR",
        interval=None,
        included_hours=Decimal("2"),
        start_date=None,
        brand_name=None,
        locale="en",
    )["included_hours"] == "2"


# --- the flag, resolved through the preset and the agreement ----------------------------


async def test_the_preset_decides_and_the_agreement_may_say_otherwise(client_for) -> None:
    t = await make_tenant("notes-preset")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        hosting = await _hosting_type(c, headers)
        template = await _template(c, headers, notes_on_invoice=True, type_id=hosting["id"])
        assert template["notes_on_invoice"] is True
        company = await _company(c, headers)
        sub = await _subscription(
            c,
            headers,
            company,
            subscription_type_id=hosting["id"],
            subscription_template_id=template["id"],
        )
        assert sub["notes_on_invoice"] is True  # follows the preset
        assert sub["notes_on_invoice_override"] is None

        # The picker hands over the note *resolved*: every variable filled in with this
        # agreement's own details, the markup untouched, the unknown token left as authored.
        note = await _picker_note(c, headers, company, sub["id"])
        assert note is not None
        assert "{{company_name}}" not in note
        assert "### Wat Notes-Preset voor Klant BV doet" in note
        assert "**Hosting Pro** (Hosting), Maandelijks voor € 25,00" in note
        since = add_months(org_today(), -7).strftime("%d-%m-%Y")
        assert f"2 uur inbegrepen, sinds {since}" in note
        assert "{{unknown_token}}" in note

        # The agreement's own "no" wins over the preset's "yes" …
        off = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"notes_on_invoice_override": False},
            headers=headers,
        )
        assert off.status_code == 200, off.text
        assert off.json()["notes_on_invoice"] is False
        assert off.json()["notes_on_invoice_override"] is False
        assert await _picker_note(c, headers, company, sub["id"]) is None

        # … an explicit null follows the preset again …
        back = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"notes_on_invoice_override": None},
            headers=headers,
        )
        assert back.json()["notes_on_invoice"] is True

        # … and the preset switched off takes every follower with it.
        flipped = await c.patch(
            f"/api/v1/subscriptions/templates/{template['id']}",
            json={"notes_on_invoice": False},
            headers=headers,
        )
        assert flipped.status_code == 200, flipped.text
        assert flipped.json()["notes_on_invoice"] is False
        after = (await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)).json()
        assert after["notes_on_invoice"] is False
        assert await _picker_note(c, headers, company, sub["id"]) is None


async def test_an_agreement_following_no_preset_keeps_its_notes_unless_told_otherwise(
    client_for,
) -> None:
    """The field began life as the agency's working notes. Nothing already running may start
    printing them because a flag defaulted — only an explicit yes on the agreement does."""
    t = await make_tenant("notes-loose")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        sub = await _subscription(c, headers, company)
        assert sub["notes_on_invoice"] is False
        assert await _picker_note(c, headers, company, sub["id"]) is None
        own = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"notes_on_invoice_override": True},
            headers=headers,
        )
        assert own.json()["notes_on_invoice"] is True
        assert "Klant BV" in (await _picker_note(c, headers, company, sub["id"]) or "")


# --- the cron drafts the same note, and the paper prints its markup --------------------------


async def test_the_cron_writes_the_resolved_note_onto_the_draft(client_for) -> None:
    t = await make_tenant("notes-cron")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        hosting = await _hosting_type(c, headers)
        template = await _template(c, headers, notes_on_invoice=True, type_id=hosting["id"])
        company = await _company(c, headers)
        printed = await _subscription(
            c,
            headers,
            company,
            subscription_type_id=hosting["id"],
            subscription_template_id=template["id"],
        )
        silent = await _subscription(c, headers, company, name="Onderhoud")

    # The cycle cron fires ``subscription.due`` for both; invoicing drafts each (the org's
    # default automation level is ``draft``).
    await advance_subscriptions({})

    async with client_for(t.host) as c:
        rows = (await c.get("/api/v1/invoicing/invoices", headers=headers)).json()["items"]
        by_sub = {row["subscription_id"]: row for row in rows if row.get("subscription_id")}
        assert set(by_sub) == {printed["id"], silent["id"]}

        drafted = (
            await c.get(
                f"/api/v1/invoicing/invoices/{by_sub[printed['id']]['id']}", headers=headers
            )
        ).json()
        assert drafted["notes"] is not None
        assert "### Wat Notes-Cron voor Klant BV doet" in drafted["notes"]
        assert "{{" not in drafted["notes"].replace("{{unknown_token}}", "")
        # What was picked by hand and what the cron drafted are the same sentence.
        assert drafted["notes"] == await _picker_note(c, headers, company, printed["id"])

        quiet = (
            await c.get(
                f"/api/v1/invoicing/invoices/{by_sub[silent['id']]['id']}", headers=headers
            )
        ).json()
        assert quiet["notes"] is None

        # The paper renders the markup rather than printing it: a heading, the bold run, the
        # list and the quote each land as their element, and no ``**`` or ``###`` survives.
        html = (
            await c.get(
                f"/api/v1/invoicing/invoices/{drafted['id']}/preview", headers=headers
            )
        ).text
        notes_block = html[html.index('class="notes"') :]
        assert "<h3>Wat Notes-Cron voor Klant BV doet</h3>" in notes_block
        assert "<strong>Hosting Pro</strong>" in notes_block
        assert "<ul>" in notes_block and "<blockquote>" in notes_block
        assert "###" not in notes_block and "**" not in notes_block
