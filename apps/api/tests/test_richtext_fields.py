"""Rich text on real fields (issue #66): checklist/item descriptions, and HTML stripped on write.

The unit behaviour of the sanitizer lives in ``test_richtext.py``; this exercises it through the
API on the fields the convention covers — proving a stored value can never carry ``<script>`` and
that the markdown source survives otherwise intact.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant

_XSS = "**keep** <script>alert('x')</script> [doc](https://x.com)"


async def test_checklist_and_item_descriptions_round_trip(client_for) -> None:
    t = await make_tenant("rt-checklist")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        checklist = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/checklists",
                json={"title": "Launch", "description": "See the **brief**"},
                headers=headers,
            )
        ).json()
        base = f"/api/v1/tasks/{task['id']}/checklists/{checklist['id']}"
        item = (
            await c.post(
                f"{base}/items",
                json={"title": "DNS", "description": "TTL _low_ before cutover"},
                headers=headers,
            )
        ).json()

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        got = detail["checklists"][0]
        assert got["description"] == "See the **brief**"
        assert got["items"][0]["description"] == "TTL _low_ before cutover"

        # Editing the description works and can clear it back to null.
        await c.patch(base, json={"description": "updated"}, headers=headers)
        await c.patch(f"{base}/items/{item['id']}", json={"description": None}, headers=headers)
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        got = detail["checklists"][0]
        assert got["description"] == "updated"
        assert got["items"][0]["description"] is None


async def test_task_description_and_comment_stripped_of_html(client_for) -> None:
    t = await make_tenant("rt-strip")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post("/api/v1/tasks", json={"title": "T", "description": _XSS}, headers=headers)
        ).json()
        assert "<script>" not in task["description"]
        assert "alert" not in task["description"]
        assert "**keep**" in task["description"]  # markdown source preserved

        comment = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments", json={"body": _XSS}, headers=headers
            )
        ).json()
        assert "<script>" not in comment["body"]
        assert "**keep**" in comment["body"]


async def test_comment_notification_excerpt_is_plaintext(client_for) -> None:
    """The activity/notification excerpt must not carry markdown syntax (issue #66)."""
    t = await make_tenant("rt-excerpt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        await c.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={"body": "Please review **the brief** and [the doc](https://x.com)"},
            headers=headers,
        )
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        commented = next(a for a in detail["activities"] if a["action"] == "commented")
        excerpt = commented["payload"]["excerpt"]
        assert "**" not in excerpt and "](" not in excerpt
        assert "Please review the brief and the doc" == excerpt


async def test_long_text_custom_field_stripped_of_html(client_for) -> None:
    t = await make_tenant("rt-cf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/custom-fields/definitions",
            json={
                "entity_type": "company",
                "key": "notes",
                "data_type": "long_text",
                "label_i18n": {"nl": "Notities", "en": "Notes"},
            },
            headers=headers,
        )
        company = (
            await c.post(
                "/api/v1/companies",
                json={"name": "Acme", "custom": {"notes": _XSS}},
                headers=headers,
            )
        ).json()
        stored = company["custom"]["notes"]
        assert "<script>" not in stored
        assert "**keep**" in stored


async def test_notes_fields_stripped_of_html(client_for) -> None:
    """The notes fields the editor rolled out to (#228) strip raw HTML on write too."""
    t = await make_tenant("rt-notes")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme", "notes": _XSS}, headers=headers)
        ).json()
        assert "<script>" not in company["notes"]
        assert "**keep**" in company["notes"]

        contact = (
            await c.post(
                "/api/v1/contacts", json={"first_name": "Jan", "notes": _XSS}, headers=headers
            )
        ).json()
        assert "<script>" not in contact["notes"]
        assert "**keep**" in contact["notes"]
        edited = (
            await c.patch(
                f"/api/v1/contacts/{contact['id']}", json={"notes": _XSS}, headers=headers
            )
        ).json()
        assert "<script>" not in edited["notes"]

        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company["id"],
                    "name": "Onderhoud",
                    "interval": "monthly",
                    "start_date": "2026-01-01",
                    "amount": "100.00",
                    "notes": _XSS,
                },
                headers=headers,
            )
        ).json()
        assert "<script>" not in sub["notes"]
        assert "**keep**" in sub["notes"]

        invoice = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company["id"],
                    "notes": _XSS,
                    "lines": [{"description": "Werk", "quantity": "1", "unit_price": "10"}],
                },
                headers=headers,
            )
        ).json()
        assert "<script>" not in invoice["notes"]
        assert "**keep**" in invoice["notes"]

        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Site", "company_id": company["id"], "description": _XSS},
                headers=headers,
            )
        ).json()
        assert "<script>" not in project["description"]
        assert "**keep**" in project["description"]
        edited_project = (
            await c.patch(
                f"/api/v1/projects/{project['id']}", json={"description": _XSS}, headers=headers
            )
        ).json()
        assert "<script>" not in edited_project["description"]
