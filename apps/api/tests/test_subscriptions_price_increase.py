"""Price increase over subscriptions (and template defaults): preview + apply.

Scope is all / one type / one subscription / one template (#231); the single-row scopes are
the row shortcut's contract.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant


def _iso(d: date) -> str:
    return d.isoformat()


async def _company(client, headers, name: str = "Klant BV") -> str:
    r = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    return r.json()["id"]


async def _subscription(client, headers, company_id: str, name: str, amount: str, **extra):
    today = datetime.now(UTC).date()
    body = {
        "company_id": company_id,
        "name": name,
        "status": "active",
        "interval": "monthly",
        "start_date": _iso(today - timedelta(days=60)),
        "amount": amount,
        **extra,
    }
    r = await client.post("/api/v1/subscriptions", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_price_increase_percent_preview_and_apply(client_for) -> None:
    t = await make_tenant("subs-bump")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    effective = today + timedelta(days=30)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        a = await _subscription(c, headers, company, "Hosting klein", "100.00")
        b = await _subscription(c, headers, company, "Hosting groot", "80.55")
        # Cancelled agreements are never repriced.
        cancelled = await _subscription(c, headers, company, "Opgezegd", "10.00")
        await c.patch(
            f"/api/v1/subscriptions/{cancelled['id']}",
            json={"status": "cancelled"},
            headers=headers,
        )
        # A subscription starting after the effective date has no base yet — skipped.
        future = await c.post(
            "/api/v1/subscriptions",
            json={
                "company_id": company,
                "name": "Toekomst",
                "start_date": _iso(effective + timedelta(days=10)),
                "amount": "50.00",
            },
            headers=headers,
        )
        assert future.status_code == 201

        payload = {"mode": "percent", "value": "5", "valid_from": _iso(effective)}
        preview = await c.post(
            "/api/v1/subscriptions/price-increase/preview", json=payload, headers=headers
        )
        assert preview.status_code == 200, preview.text
        items = {i["subscription_id"]: i for i in preview.json()["items"]}
        assert set(items) == {a["id"], b["id"]}
        assert items[a["id"]]["current_amount"] == "100.00"
        assert items[a["id"]]["new_amount"] == "105.00"
        # 80.55 * 1.05 = 84.5775 → rounds half-up to 84.58.
        assert items[b["id"]]["new_amount"] == "84.58"

        # Preview writes nothing.
        history = await c.get(f"/api/v1/subscriptions/{a['id']}/prices", headers=headers)
        assert len(history.json()) == 1

        applied = await c.post(
            "/api/v1/subscriptions/price-increase", json=payload, headers=headers
        )
        assert applied.status_code == 200, applied.text
        history = (
            await c.get(f"/api/v1/subscriptions/{a['id']}/prices", headers=headers)
        ).json()
        assert [(h["amount"], h["valid_from"]) for h in history] == [
            ("105.00", _iso(effective)),
            ("100.00", _iso(today - timedelta(days=60))),
        ]

        # Applying again on the same date corrects the row in place — no duplicate history.
        again = await c.post(
            "/api/v1/subscriptions/price-increase",
            json={"mode": "percent", "value": "10", "valid_from": _iso(effective)},
            headers=headers,
        )
        assert again.status_code == 200
        history = (
            await c.get(f"/api/v1/subscriptions/{a['id']}/prices", headers=headers)
        ).json()
        # Base is still today's 100.00 (the corrected row itself is not yet in effect).
        assert [(h["amount"], h["valid_from"]) for h in history] == [
            ("110.00", _iso(effective)),
            ("100.00", _iso(today - timedelta(days=60))),
        ]

        # Until the effective date, the current amount is untouched.
        current = await c.get(f"/api/v1/subscriptions/{a['id']}", headers=headers)
        assert current.json()["amount"] == "100.00"

        # The change lands on the activity trail.
        trail = await c.get(
            "/api/v1/activity",
            params={"entity_type": "subscription", "entity_id": a["id"]},
            headers=headers,
        )
        assert any(row["action"] == "price_increased" for row in trail.json())


async def test_price_increase_scoped_by_type_and_templates(client_for) -> None:
    t = await make_tenant("subs-bump-type")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        types = (await c.get("/api/v1/subscriptions/types", headers=headers)).json()
        hosting = next(x for x in types if x["key"] == "hosting")
        support = next(x for x in types if x["key"] == "support")

        in_scope = await _subscription(
            c, headers, company, "Hosting", "200.00", subscription_type_id=hosting["id"]
        )
        await _subscription(
            c, headers, company, "Support", "300.00", subscription_type_id=support["id"]
        )

        tpl = await c.post(
            "/api/v1/subscriptions/templates",
            json={"name": "Hosting standaard", "amount": "20.00",
                  "subscription_type_id": hosting["id"]},
            headers=headers,
        )
        assert tpl.status_code == 201, tpl.text
        other_tpl = await c.post(
            "/api/v1/subscriptions/templates",
            json={"name": "Support standaard", "amount": "99.00",
                  "subscription_type_id": support["id"]},
            headers=headers,
        )
        assert other_tpl.status_code == 201

        payload = {
            "mode": "amount",
            "value": "10",
            "valid_from": _iso(today),
            "subscription_type_id": hosting["id"],
            "include_templates": True,
        }
        applied = await c.post(
            "/api/v1/subscriptions/price-increase", json=payload, headers=headers
        )
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert [i["subscription_id"] for i in body["items"]] == [in_scope["id"]]
        assert body["items"][0]["new_amount"] == "210.00"
        assert [tp["template_id"] for tp in body["templates"]] == [tpl.json()["id"]]
        assert body["templates"][0]["new_amount"] == "30.00"

        templates = (await c.get("/api/v1/subscriptions/templates", headers=headers)).json()
        by_id = {x["id"]: x for x in templates}
        assert by_id[tpl.json()["id"]]["amount"] == "30.00"
        # The other type's template is untouched.
        assert by_id[other_tpl.json()["id"]]["amount"] == "99.00"


async def test_price_increase_tenant_isolation(client_for) -> None:
    a = await make_tenant("subs-bump-a")
    b = await make_tenant("subs-bump-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    today = datetime.now(UTC).date()

    async with client_for(a.host) as ca:
        company = await _company(ca, a_headers)
        sub = await _subscription(ca, a_headers, company, "Van A", "100.00")

    async with client_for(b.host) as cb:
        applied = await cb.post(
            "/api/v1/subscriptions/price-increase",
            json={"mode": "percent", "value": "50", "valid_from": _iso(today)},
            headers=b_headers,
        )
        assert applied.status_code == 200
        assert applied.json()["items"] == []

    async with client_for(a.host) as ca:
        history = (
            await ca.get(f"/api/v1/subscriptions/{sub['id']}/prices", headers=a_headers)
        ).json()
        assert [h["amount"] for h in history] == ["100.00"]


# --- single-row scopes (#231) -------------------------------------------------- #


async def test_price_increase_single_subscription_scope(client_for) -> None:
    t = await make_tenant("subs-bump-one")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        target = await _subscription(c, headers, company, "Doelwit", "100.00")
        other = await _subscription(c, headers, company, "Ander", "50.00")
        tpl = await c.post(
            "/api/v1/subscriptions/templates",
            json={"name": "Standaard", "amount": "25.00"},
            headers=headers,
        )
        assert tpl.status_code == 201

        payload = {
            "mode": "percent",
            "value": "10",
            "valid_from": _iso(today),
            "subscription_id": target["id"],
        }
        preview = await c.post(
            "/api/v1/subscriptions/price-increase/preview", json=payload, headers=headers
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        # The single row, with its before/after — and never a template alongside.
        assert [i["subscription_id"] for i in body["items"]] == [target["id"]]
        assert body["items"][0]["current_amount"] == "100.00"
        assert body["items"][0]["new_amount"] == "110.00"
        assert body["templates"] == []

        applied = await c.post(
            "/api/v1/subscriptions/price-increase", json=payload, headers=headers
        )
        assert applied.status_code == 200, applied.text

        history = (
            await c.get(f"/api/v1/subscriptions/{target['id']}/prices", headers=headers)
        ).json()
        assert history[0]["amount"] == "110.00"
        # Only the targeted subscription's history moved.
        other_history = (
            await c.get(f"/api/v1/subscriptions/{other['id']}/prices", headers=headers)
        ).json()
        assert [h["amount"] for h in other_history] == ["50.00"]
        templates = (await c.get("/api/v1/subscriptions/templates", headers=headers)).json()
        assert templates[0]["amount"] == "25.00"

        # The change lands on the target's activity trail, same as the bulk path.
        trail = await c.get(
            "/api/v1/activity",
            params={"entity_type": "subscription", "entity_id": target["id"]},
            headers=headers,
        )
        assert any(row["action"] == "price_increased" for row in trail.json())


async def test_price_increase_single_template_scope(client_for) -> None:
    t = await make_tenant("subs-bump-tpl")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        sub = await _subscription(c, headers, company, "Lopend", "100.00")
        target = (
            await c.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Doel", "amount": "20.00"},
                headers=headers,
            )
        ).json()
        other = (
            await c.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Ander", "amount": "99.00"},
                headers=headers,
            )
        ).json()

        payload = {
            "mode": "amount",
            "value": "5",
            "valid_from": _iso(today),
            "subscription_template_id": target["id"],
        }
        preview = await c.post(
            "/api/v1/subscriptions/price-increase/preview", json=payload, headers=headers
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["items"] == []
        assert [tp["template_id"] for tp in body["templates"]] == [target["id"]]
        assert body["templates"][0]["new_amount"] == "25.00"

        applied = await c.post(
            "/api/v1/subscriptions/price-increase", json=payload, headers=headers
        )
        assert applied.status_code == 200, applied.text

        templates = {
            x["id"]: x
            for x in (await c.get("/api/v1/subscriptions/templates", headers=headers)).json()
        }
        assert templates[target["id"]]["amount"] == "25.00"
        # Only the targeted template moved; the running agreement's history is untouched.
        assert templates[other["id"]]["amount"] == "99.00"
        history = (
            await c.get(f"/api/v1/subscriptions/{sub['id']}/prices", headers=headers)
        ).json()
        assert [h["amount"] for h in history] == ["100.00"]


async def test_price_increase_scope_combinations_are_rejected(client_for) -> None:
    t = await make_tenant("subs-bump-combo")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        sub = await _subscription(c, headers, company, "Solo", "100.00")
        tpl = (
            await c.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Solo", "amount": "20.00"},
                headers=headers,
            )
        ).json()
        types = (await c.get("/api/v1/subscriptions/types", headers=headers)).json()

        base = {"mode": "percent", "value": "5", "valid_from": _iso(today)}
        bad = [
            # Two scopes at once.
            {"subscription_id": sub["id"], "subscription_template_id": tpl["id"]},
            {"subscription_id": sub["id"], "subscription_type_id": types[0]["id"]},
            {"subscription_template_id": tpl["id"], "subscription_type_id": types[0]["id"]},
            # A single-row scope never drags templates (or anything else) along.
            {"subscription_id": sub["id"], "include_templates": True},
            {"subscription_template_id": tpl["id"], "include_templates": True},
        ]
        for extra in bad:
            r = await c.post(
                "/api/v1/subscriptions/price-increase/preview",
                json={**base, **extra},
                headers=headers,
            )
            assert r.status_code == 400, (extra, r.text)
            assert r.json()["error"]["message"] == "errors.validation"


async def test_price_increase_single_row_tenant_isolation(client_for) -> None:
    a = await make_tenant("subs-bump-one-a")
    b = await make_tenant("subs-bump-one-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    today = datetime.now(UTC).date()

    async with client_for(a.host) as ca:
        company = await _company(ca, a_headers)
        sub = await _subscription(ca, a_headers, company, "Van A", "100.00")
        tpl = (
            await ca.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Van A", "amount": "20.00"},
                headers=a_headers,
            )
        ).json()

    async with client_for(b.host) as cb:
        for scope in (
            {"subscription_id": sub["id"]},
            {"subscription_template_id": tpl["id"]},
        ):
            r = await cb.post(
                "/api/v1/subscriptions/price-increase",
                json={"mode": "percent", "value": "50", "valid_from": _iso(today), **scope},
                headers=b_headers,
            )
            assert r.status_code == 404, (scope, r.text)

    async with client_for(a.host) as ca:
        history = (
            await ca.get(f"/api/v1/subscriptions/{sub['id']}/prices", headers=a_headers)
        ).json()
        assert [h["amount"] for h in history] == ["100.00"]
        templates = (await ca.get("/api/v1/subscriptions/templates", headers=a_headers)).json()
        assert templates[0]["amount"] == "20.00"


async def test_template_scope_requires_template_manage(client_for) -> None:
    """A subscription-writer without the catalog grant cannot reach template defaults
    through the price-increase side door (#231); the subscription scope still works."""
    t = await make_tenant("subs-bump-perm")
    writer = await make_tenant("subs-bump-perm-w", email="writer-bump@example.com")
    owner_headers = await auth_cookie(t.user)
    writer_headers = await auth_cookie(writer.user)
    today = datetime.now(UTC).date()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, writer.user.id, role="member")
        await session.commit()

    async with client_for(t.host) as c:
        company = await _company(c, owner_headers)
        sub = await _subscription(c, owner_headers, company, "Van schrijver", "100.00")
        tpl = (
            await c.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Alleen beheer", "amount": "20.00"},
                headers=owner_headers,
            )
        ).json()

        # Widen the member system role to subscription read+write — not template.manage.
        roles = {r["key"]: r for r in (await c.get("/api/v1/roles", headers=owner_headers)).json()}
        member = roles["member"]
        widened = await c.patch(
            f"/api/v1/roles/{member['id']}",
            json={
                "permissions": [
                    *member["permissions"],
                    "subscriptions.subscription.read",
                    "subscriptions.subscription.write",
                ]
            },
            headers=owner_headers,
        )
        assert widened.status_code == 200, widened.text

        payload = {"mode": "percent", "value": "10", "valid_from": _iso(today)}
        denied = await c.post(
            "/api/v1/subscriptions/price-increase",
            json={**payload, "subscription_template_id": tpl["id"]},
            headers=writer_headers,
        )
        assert denied.status_code == 403, denied.text

        allowed = await c.post(
            "/api/v1/subscriptions/price-increase",
            json={**payload, "subscription_id": sub["id"]},
            headers=writer_headers,
        )
        assert allowed.status_code == 200, allowed.text
