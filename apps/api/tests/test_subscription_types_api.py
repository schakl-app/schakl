"""Subscription types + templates (#142): CRUD, seed, activation spawning, isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tests.conftest import auth_cookie, make_tenant


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


async def _company(c, headers, name: str = "Klant BV") -> dict:
    return (await c.post("/api/v1/companies", json={"name": name}, headers=headers)).json()


def _subscription_body(company_id: str, **extra) -> dict:
    return {
        "company_id": company_id,
        "name": "Hostingpakket",
        "interval": "monthly",
        "start_date": _today_iso(),
        "amount": "25.00",
        **extra,
    }


async def test_types_seed_crud_and_key_rules(client_for) -> None:
    t = await make_tenant("subtypes-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # First list lazily seeds the starter set (the DEFAULT_LEAVE_TYPES pattern).
        listed = await c.get("/api/v1/subscriptions/types", headers=headers)
        assert listed.status_code == 200, listed.text
        keys = [row["key"] for row in listed.json()]
        assert keys == ["hosting", "onderhoud", "marketing", "support"]

        created = await c.post(
            "/api/v1/subscriptions/types",
            json={"key": "seo", "label_i18n": {"nl": "SEO", "en": "SEO"}, "position": 50},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        type_id = created.json()["id"]

        # Duplicate keys conflict; the starter set counts.
        dup = await c.post(
            "/api/v1/subscriptions/types",
            json={"key": "hosting", "label_i18n": {"nl": "Dubbel", "en": "Dup"}},
            headers=headers,
        )
        assert dup.status_code == 409

        # Key is immutable by omission from the update schema — a sent key changes nothing.
        patched = await c.patch(
            f"/api/v1/subscriptions/types/{type_id}",
            json={"key": "renamed", "label_i18n": {"nl": "Zoekmachines", "en": "SEO"}},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["key"] == "seo"
        assert patched.json()["label_i18n"]["nl"] == "Zoekmachines"

        # Deactivated types disappear from the default list, remain with include_inactive.
        await c.patch(
            f"/api/v1/subscriptions/types/{type_id}", json={"active": False}, headers=headers
        )
        default = (await c.get("/api/v1/subscriptions/types", headers=headers)).json()
        assert all(row["id"] != type_id for row in default)
        full = (
            await c.get(
                "/api/v1/subscriptions/types", params={"include_inactive": True}, headers=headers
            )
        ).json()
        assert any(row["id"] == type_id for row in full)

        deleted = await c.delete(f"/api/v1/subscriptions/types/{type_id}", headers=headers)
        assert deleted.status_code == 204


async def test_templates_crud_and_type_link(client_for) -> None:
    t = await make_tenant("subtpl-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/subscriptions/types", headers=headers)).json()
        hosting = next(row for row in types if row["key"] == "hosting")

        created = await c.post(
            "/api/v1/subscriptions/templates",
            json={
                "name": "Hosting Basis",
                "subscription_type_id": hosting["id"],
                "interval": "monthly",
                "amount": "25.00",
                "included_hours": "1",
                "rollover": {"mode": "carry", "expires_after_periods": 2},
                "notice_period_days": 30,
                "lines": [{"description": "Hosting", "quantity": "1", "unit_amount": "25.00"}],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        template = created.json()
        assert template["amount"] == "25.00"
        assert template["rollover"] == {"mode": "carry", "expires_after_periods": 2}
        assert template["lines"][0]["description"] == "Hosting"

        listed = (await c.get("/api/v1/subscriptions/templates", headers=headers)).json()
        assert [row["name"] for row in listed] == ["Hosting Basis"]

        updated = await c.patch(
            f"/api/v1/subscriptions/templates/{template['id']}",
            json={"name": "Hosting Plus", "amount": "50.00"},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Hosting Plus"
        assert updated.json()["amount"] == "50.00"

        # Deleting the type SET NULLs the template's reference, never the template.
        await c.delete(f"/api/v1/subscriptions/types/{hosting['id']}", headers=headers)
        survivor = (await c.get("/api/v1/subscriptions/templates", headers=headers)).json()[0]
        assert survivor["subscription_type_id"] is None

        deleted = await c.delete(
            f"/api/v1/subscriptions/templates/{template['id']}", headers=headers
        )
        assert deleted.status_code == 204


async def test_subscription_carries_type_and_filters(client_for) -> None:
    t = await make_tenant("subtypes-field")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        types = (await c.get("/api/v1/subscriptions/types", headers=headers)).json()
        hosting = next(row for row in types if row["key"] == "hosting")
        support = next(row for row in types if row["key"] == "support")

        created = await c.post(
            "/api/v1/subscriptions",
            json=_subscription_body(company["id"], subscription_type_id=hosting["id"]),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        sub = created.json()
        assert sub["subscription_type_id"] == hosting["id"]
        await c.post(
            "/api/v1/subscriptions",
            json=_subscription_body(company["id"], name="Los contract"),
            headers=headers,
        )

        filtered = (
            await c.get(
                "/api/v1/subscriptions",
                params={"subscription_type_id": hosting["id"]},
                headers=headers,
            )
        ).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["id"] == sub["id"]

        # Re-typing and clearing the type both land in the activity trail (§16).
        retyped = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"subscription_type_id": support["id"]},
            headers=headers,
        )
        assert retyped.status_code == 200, retyped.text
        assert retyped.json()["subscription_type_id"] == support["id"]
        cleared = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"subscription_type_id": None},
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["subscription_type_id"] is None
        activity = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "subscription", "entity_id": sub["id"]},
                headers=headers,
            )
        ).json()
        changes = [
            row["payload"]["changes"]["subscription_type_id"]
            for row in activity
            if row["action"] == "updated"
            and "subscription_type_id" in row["payload"].get("changes", {})
        ]
        assert len(changes) == 2

        # Deleting the type never strands the agreement: the FK SET NULLs.
        await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"subscription_type_id": hosting["id"]},
            headers=headers,
        )
        await c.delete(f"/api/v1/subscriptions/types/{hosting['id']}", headers=headers)
        survivor = (
            await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)
        ).json()
        assert survivor["subscription_type_id"] is None


async def test_first_activation_spawns_type_templates_once(client_for) -> None:
    t = await make_tenant("subtypes-activate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        template = (
            await c.post(
                "/api/v1/tasks/templates",
                json={
                    "name": "Hosting onboarding",
                    "items": [
                        {"title": "DNS omzetten", "relative_due_days": 3},
                        {"title": "Monitoring aanzetten"},
                    ],
                },
                headers=headers,
            )
        ).json()
        types = (await c.get("/api/v1/subscriptions/types", headers=headers)).json()
        hosting = next(row for row in types if row["key"] == "hosting")
        await c.patch(
            f"/api/v1/subscriptions/types/{hosting['id']}",
            json={"task_template_ids": [template["id"]]},
            headers=headers,
        )

        async def company_task_count() -> int:
            page = (
                await c.get(
                    "/api/v1/tasks",
                    params={"company_id": company["id"], "meta": False, "count": False},
                    headers=headers,
                )
            ).json()
            return len(page["items"])

        # A draft spawns nothing.
        created = await c.post(
            "/api/v1/subscriptions",
            json=_subscription_body(
                company["id"], subscription_type_id=hosting["id"], status="draft"
            ),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        sub = created.json()
        assert sub["activated_at"] is None
        assert await company_task_count() == 0

        # First activation spawns the type's templates and stamps the instant.
        activated = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}", json={"status": "active"}, headers=headers
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["activated_at"] is not None
        assert await company_task_count() == 2

        # Pause → resume is not a first activation: nothing respawns.
        await c.patch(
            f"/api/v1/subscriptions/{sub['id']}", json={"status": "paused"}, headers=headers
        )
        resumed = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}", json={"status": "active"}, headers=headers
        )
        assert resumed.status_code == 200, resumed.text
        assert await company_task_count() == 2

        # Created straight as active: spawns immediately, exactly once.
        second = await c.post(
            "/api/v1/subscriptions",
            json=_subscription_body(
                company["id"],
                name="Direct actief",
                subscription_type_id=hosting["id"],
                status="active",
            ),
            headers=headers,
        )
        assert second.status_code == 201, second.text
        assert second.json()["activated_at"] is not None
        assert await company_task_count() == 4

        # A template deleted after linking is skipped, never an activation error.
        await c.delete(f"/api/v1/tasks/templates/{template['id']}", headers=headers)
        third = await c.post(
            "/api/v1/subscriptions",
            json=_subscription_body(
                company["id"],
                name="Zonder sjabloon",
                subscription_type_id=hosting["id"],
                status="active",
            ),
            headers=headers,
        )
        assert third.status_code == 201, third.text
        assert await company_task_count() == 4


async def test_types_and_templates_tenant_isolation(client_for) -> None:
    a = await make_tenant("subtypes-iso-a")
    b = await make_tenant("subtypes-iso-b")
    headers_a = await auth_cookie(a.user)
    headers_b = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        types_a = (await ca.get("/api/v1/subscriptions/types", headers=headers_a)).json()
        type_a = types_a[0]
        template_a = (
            await ca.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Alleen van A", "amount": "10.00"},
                headers=headers_a,
            )
        ).json()

    async with client_for(b.host) as cb:
        # B's lazily seeded set is its own; A's rows are unreachable.
        patched = await cb.patch(
            f"/api/v1/subscriptions/types/{type_a['id']}",
            json={"label_i18n": {"nl": "Gekaapt", "en": "Hijacked"}},
            headers=headers_b,
        )
        assert patched.status_code == 404
        templates_b = (
            await cb.get("/api/v1/subscriptions/templates", headers=headers_b)
        ).json()
        assert all(row["id"] != template_a["id"] for row in templates_b)
        deleted = await cb.delete(
            f"/api/v1/subscriptions/templates/{template_a['id']}", headers=headers_b
        )
        assert deleted.status_code == 404

        # A's type cannot label B's subscription (a cross-tenant reference is a validation
        # error, not a stranded FK).
        company_b = await _company(cb, headers_b, name="B Klant")
        crossed = await cb.post(
            "/api/v1/subscriptions",
            json=_subscription_body(company_b["id"], subscription_type_id=type_a["id"]),
            headers=headers_b,
        )
        assert crossed.status_code == 400


async def test_member_without_grants_cannot_manage_types(client_for) -> None:
    t = await make_tenant("subtypes-rbac", role="member")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        denied = await c.post(
            "/api/v1/subscriptions/types",
            json={"key": "verboden", "label_i18n": {"nl": "Nee", "en": "No"}},
            headers=headers,
        )
        assert denied.status_code == 403
        denied_tpl = await c.post(
            "/api/v1/subscriptions/templates",
            json={"name": "Verboden"},
            headers=headers,
        )
        assert denied_tpl.status_code == 403


async def test_template_rename_follows_the_subscriptions_it_created(client_for) -> None:
    """Renaming a standard subscription renames the agreements made from it.

    The create form takes the name *from* the preset (read-only there), so the preset's name
    is literally their label. What must not move: an agreement someone deliberately renamed,
    and one that merely happens to share the name without coming from the preset.
    """
    t = await make_tenant("subtpl-rename")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        template = (
            await c.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Hosting Basis", "amount": "25.00"},
                headers=headers,
            )
        ).json()

        follower, renamed_by_hand = [
            (
                await c.post(
                    "/api/v1/subscriptions",
                    json=_subscription_body(
                        company["id"],
                        name="Hosting Basis",
                        subscription_template_id=template["id"],
                    ),
                    headers=headers,
                )
            ).json()
            for _ in range(2)
        ]
        assert follower["subscription_template_id"] == template["id"]

        # A deliberate per-client wording opts that agreement out for good.
        await c.patch(
            f"/api/v1/subscriptions/{renamed_by_hand['id']}",
            json={"name": "Hosting Basis extra IP"},
            headers=headers,
        )
        # Same name, but not made from the preset: coincidence is not provenance.
        namesake = (
            await c.post(
                "/api/v1/subscriptions",
                json=_subscription_body(company["id"], name="Hosting Basis"),
                headers=headers,
            )
        ).json()

        # A save that does not touch the name moves nothing.
        priced = await c.patch(
            f"/api/v1/subscriptions/templates/{template['id']}",
            json={"amount": "30.00"},
            headers=headers,
        )
        assert priced.status_code == 200, priced.text
        assert priced.json()["renamed_subscriptions"] == 0

        renamed = await c.patch(
            f"/api/v1/subscriptions/templates/{template['id']}",
            json={"name": "Hosting Start"},
            headers=headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["renamed_subscriptions"] == 1

        async def name_of(subscription: dict) -> str:
            row = await c.get(f"/api/v1/subscriptions/{subscription['id']}", headers=headers)
            return row.json()["name"]

        assert await name_of(follower) == "Hosting Start"
        assert await name_of(renamed_by_hand) == "Hosting Basis extra IP"
        assert await name_of(namesake) == "Hosting Basis"

        # The bulk change is never invisible: it lands on the agreement's own trail (§16).
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "subscription", "entity_id": follower["id"]},
                headers=headers,
            )
        ).json()
        entry = next(row for row in trail if row["action"] == "renamed_with_template")
        assert entry["payload"] == {"from": "Hosting Basis", "to": "Hosting Start"}

        # Deleting the preset unlinks; the agreement keeps the name it was left with.
        await c.delete(f"/api/v1/subscriptions/templates/{template['id']}", headers=headers)
        survivor = (
            await c.get(f"/api/v1/subscriptions/{follower['id']}", headers=headers)
        ).json()
        assert survivor["subscription_template_id"] is None
        assert survivor["name"] == "Hosting Start"


async def test_subscription_cannot_claim_another_tenants_template(client_for) -> None:
    """A foreign preset id is a validation error, not a stranded FK — otherwise another org's
    rename would reach in here later (Golden Rule 1)."""
    a = await make_tenant("subtpl-iso-a")
    b = await make_tenant("subtpl-iso-b")
    headers_a = await auth_cookie(a.user)
    headers_b = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        template_a = (
            await ca.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Alleen van A", "amount": "10.00"},
                headers=headers_a,
            )
        ).json()

    async with client_for(b.host) as cb:
        company_b = await _company(cb, headers_b, name="B Klant")
        crossed = await cb.post(
            "/api/v1/subscriptions",
            json=_subscription_body(
                company_b["id"], subscription_template_id=template_a["id"]
            ),
            headers=headers_b,
        )
        assert crossed.status_code == 400


async def test_an_agreement_can_be_pointed_at_a_preset_after_the_fact(client_for) -> None:
    """"Opslaan als standaardabonnement" makes an agreement the preset, then points that same
    agreement at it — so a later rename of either keeps the two in step. Explicit ``null``
    stops it following; a foreign preset is refused like on create."""
    t = await make_tenant("subtpl-relink")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json=_subscription_body(company["id"], name="Onderhoud Basis"),
                headers=headers,
            )
        ).json()
        assert sub["subscription_template_id"] is None
        template = (
            await c.post(
                "/api/v1/subscriptions/templates",
                json={"name": "Onderhoud Basis", "amount": "25.00"},
                headers=headers,
            )
        ).json()

        linked = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"subscription_template_id": template["id"]},
            headers=headers,
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["subscription_template_id"] == template["id"]

        # It now follows the preset like any agreement created from it.
        renamed = await c.patch(
            f"/api/v1/subscriptions/templates/{template['id']}",
            json={"name": "Onderhoud Compleet"},
            headers=headers,
        )
        assert renamed.json()["renamed_subscriptions"] == 1
        after = (await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)).json()
        assert after["name"] == "Onderhoud Compleet"

        cleared = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"subscription_template_id": None},
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["subscription_template_id"] is None

        stranger = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"subscription_template_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert stranger.status_code == 400
