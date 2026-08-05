"""A client reads their own issued invoices in the portal, and nothing else (issue #266).

Three separate refusals hold this together, and each one has its own test here because each
fails silently in a different direction:

* the **company horizon** — company A's portal login must never reach company B's invoice,
  in the same org (that is the cross-company isolation the issue asks for);
* the **draft rule** — ``Invoice.__portal_horizon_clause__`` hides what the agency has not
  issued, on every read path, not only the list;
* the **scope** — ``invoicing.invoice.read`` is scoped since #266, and the six org-wide
  surfaces that ride the same key (the seller's bank details, the price list, the template
  library, the unbilled-hours backlog with every employee's rate on it) declare ``:any``.

Plus the reconciler revision that gets an *existing* org from the old bare grant to the new
pair, which is the only part of this that cannot be observed on a fresh tenant.
"""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.core.apikeys.models import ApiKey
from app.core.auth.models import User
from app.core.models import OrgSettings
from app.core.permissions.models import Role, RolePermission
from app.core.permissions.reconcile import REVISIONS, reconcile_org
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant


async def _seed(client, headers) -> None:
    """Seller details, so an invoice can be issued and rendered at all."""
    resp = await client.put(
        "/api/v1/invoicing/settings",
        json={
            "company_details": {
                "name": "Agency BV",
                "address_line1": "Kerkstraat 1",
                "postal_code": "1234 AB",
                "city": "Amsterdam",
                "country": "NL",
                "iban": "NL02ABNA0123456789",
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def _invoice(client, headers, company_id: str, *, issue: bool) -> dict:
    created = await client.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": "Werk", "quantity": "1", "unit_price": "100"}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    invoice = created.json()
    if not issue:
        return invoice
    issued = await client.post(
        f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _portal_login(client, headers, slug: str, company_id: str) -> dict[str, str]:
    """A contact linked to ``company_id``, with the portal switched on — the #193 flow."""
    contact = (
        await client.post(
            "/api/v1/contacts",
            json={
                "first_name": "Piet",
                "last_name": "Klant",
                "email": f"piet-{slug}@example.com",
                "company_ids": [company_id],
            },
            headers=headers,
        )
    ).json()
    enabled = await client.post(
        f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers
    )
    assert enabled.status_code in (200, 201), enabled.text
    async with async_session_maker() as session:
        user = await session.scalar(
            select(User).where(User.email == f"piet-{slug}@example.com")
        )
    assert user is not None
    return await auth_cookie(user)


# --------------------------------------------------------------------------- #
# The feature
# --------------------------------------------------------------------------- #
async def test_client_reads_only_own_companies_issued_invoices(client_for) -> None:
    """The whole acceptance criterion in one pass: the list, the detail, the PDF — and the
    two invoices that must not appear in any of them."""
    t = await make_tenant("inv-portal")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed(c, headers)
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()["id"]
        theirs = (
            await c.post("/api/v1/companies", json={"name": "Andere BV"}, headers=headers)
        ).json()["id"]

        ours_open = await _invoice(c, headers, mine, issue=True)
        ours_draft = await _invoice(c, headers, mine, issue=False)
        their_open = await _invoice(c, headers, theirs, issue=True)

        portal = await _portal_login(c, headers, "inv-portal", mine)

        listed = await c.get("/api/v1/invoicing/invoices", headers=portal)
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert [i["id"] for i in body["items"]] == [ours_open["id"]]
        # The count must agree with the rows — a total computed without the horizon is the
        # #252 leak, and it is invisible in the item list (CLAUDE.md §15, mode 2).
        assert body["total"] == 1

        # The detail, and the paper. Same document the agency sees.
        assert (
            await c.get(f"/api/v1/invoicing/invoices/{ours_open['id']}", headers=portal)
        ).status_code == 200
        pdf = await c.get(f"/api/v1/invoicing/invoices/{ours_open['id']}/pdf", headers=portal)
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"] == "application/pdf"
        staff_pdf = await c.get(
            f"/api/v1/invoicing/invoices/{ours_open['id']}/pdf", headers=headers
        )
        assert staff_pdf.status_code == 200
        assert pdf.content == staff_pdf.content

        # Every read path refuses the draft and the other client's invoice, and refuses them
        # as 404 — a 403 on get-by-id leaks that the row exists (§15).
        for invoice_id in (ours_draft["id"], their_open["id"]):
            for suffix in ("", "/pdf", "/preview", "/ubl"):
                res = await c.get(
                    f"/api/v1/invoicing/invoices/{invoice_id}{suffix}", headers=portal
                )
                assert res.status_code == 404, f"{invoice_id}{suffix} → {res.status_code}"

        # Asking for the drafts by name is an empty page, not a way past the clause.
        drafts = await c.get(
            "/api/v1/invoicing/invoices", params={"status": "draft"}, headers=portal
        )
        assert drafts.status_code == 200
        assert drafts.json()["items"] == [] and drafts.json()["total"] == 0

        # Naming the other company explicitly does not widen the horizon either.
        cross = await c.get(
            "/api/v1/invoicing/invoices", params={"company_id": theirs}, headers=portal
        )
        assert cross.status_code == 200
        assert cross.json()["items"] == [] and cross.json()["total"] == 0

        # The owner still sees all three — "nothing leaked" must not mean "nothing matched".
        staff = await c.get("/api/v1/invoicing/invoices", headers=headers)
        assert staff.json()["total"] == 3


async def test_client_company_panel_and_summary_hide_drafts(client_for) -> None:
    """The two aggregates that do not ride the list query: the company-hub panel a client can
    already open (#193), and the summary tiles' hand-written SQL."""
    t = await make_tenant("inv-portal-agg")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed(c, headers)
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()["id"]
        issued = await _invoice(c, headers, mine, issue=True)
        await _invoice(c, headers, mine, issue=False)
        await _invoice(c, headers, mine, issue=False)

        portal = await _portal_login(c, headers, "inv-portal-agg", mine)

        panels = await c.get(f"/api/v1/companies/{mine}/panels", headers=portal)
        assert panels.status_code == 200, panels.text
        panel = next(p for p in panels.json() if p["key"] == "invoicing.company")
        assert [r["id"] for r in panel["data"]["invoices"]] == [issued["id"]]

        summary = await c.get("/api/v1/invoicing/summary", headers=portal)
        assert summary.status_code == 200, summary.text
        figures = summary.json()
        # What the client may know: what they owe. Not how many drafts we are preparing,
        # and not the quote pipeline — `invoicing.quote.read` stays staff-only.
        assert figures["open_count"] == 1
        assert figures["draft_count"] == 0
        assert figures["quotes_open_count"] == 0

        # The owner's own tiles are untouched by any of it.
        staff = (await c.get("/api/v1/invoicing/summary", headers=headers)).json()
        assert staff["draft_count"] == 2


async def test_client_cannot_list_a_hidden_invoices_files(
    client_for, tmp_path, monkeypatch
) -> None:
    """§15 failure mode (4), reached through the *other* entity-addressed surface.

    ``GET /files`` takes ``(entity_type, entity_id)`` from the caller and declares
    ``no_permission_required`` — "any signed-in member", which includes a client portal login
    — so ``entity_visible`` is its only gate for an invoice. That gate asked the record's
    **plain** repository, i.e. the horizon a *staff* member passes, and the company match
    alone admits the agency's draft. So a client held off the draft everywhere else could
    still enumerate the documents attached to it.

    ``entity_visible`` now prefers the model's ``__portal_horizon_clause__`` for an external
    login — the rule ``core/directory.py`` already applied at the reference seam, and the one
    §15 says must have exactly one copy. The activity trail was never exposed the same way
    (its router returns ``[]`` for any portal caller before it gets here); it is asserted
    alongside so the two answers stay together.
    """
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    t = await make_tenant("inv-portal-files")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed(c, headers)
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()["id"]
        theirs = (
            await c.post("/api/v1/companies", json={"name": "Andere BV"}, headers=headers)
        ).json()["id"]
        issued = await _invoice(c, headers, mine, issue=True)
        draft = await _invoice(c, headers, mine, issue=False)
        other = await _invoice(c, headers, theirs, issue=True)

        async def attach(invoice_id: str, name: str) -> None:
            res = await c.post(
                "/api/v1/files",
                params={"entity_type": "invoice", "entity_id": invoice_id},
                files={"file": (name, b"intern", "text/plain")},
                headers=headers,
            )
            assert res.status_code == 201, res.text

        for invoice_id, name in (
            (issued["id"], "eigen.txt"),
            (draft["id"], "concept-intern.txt"),
            (other["id"], "andere-klant.txt"),
        ):
            await attach(invoice_id, name)

        portal = await _portal_login(c, headers, "inv-portal-files", mine)

        def files(invoice_id: str, who):  # noqa: ANN202
            return c.get(
                "/api/v1/files",
                params={"entity_type": "invoice", "entity_id": invoice_id},
                headers=who,
            )

        # Their own issued invoice still lists — a green test must not mean "files are off".
        own = await files(issued["id"], portal)
        assert own.status_code == 200, own.text
        assert [f["filename"] for f in own.json()] == ["eigen.txt"]

        for hidden in (draft["id"], other["id"]):
            res = await files(hidden, portal)
            assert res.status_code == 200, res.text
            assert res.json() == [], f"{hidden} leaked {res.json()}"
            # …and the owner lists it, so the empty answer is the gate and not an empty table.
            staff = await files(hidden, headers)
            assert staff.status_code == 200 and staff.json(), hidden

        # The trail is refused one layer earlier, for every portal caller and every record.
        for invoice_id in (issued["id"], draft["id"], other["id"]):
            res = await c.get(
                "/api/v1/activity",
                params={"entity_type": "invoice", "entity_id": invoice_id},
                headers=portal,
            )
            assert res.status_code == 200 and res.json() == [], res.text


async def test_client_cannot_reach_the_invoicing_modules_own_surfaces(client_for) -> None:
    """The reason ``invoice.read`` became scoped: six routes ride the same key, and none of
    them is a document. The unbilled-hours backlog is the sharpest — it carries every
    employee's name and hourly rate, org-wide."""
    t = await make_tenant("inv-portal-staff")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed(c, headers)
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()["id"]
        invoice = await _invoice(c, headers, mine, issue=True)
        portal = await _portal_login(c, headers, "inv-portal-staff", mine)

        for path in (
            "/api/v1/invoicing/settings",  # seller identity, IBAN, numbering, reminders
            "/api/v1/invoicing/tax-rates",
            "/api/v1/invoicing/products",  # the agency's price list
            "/api/v1/invoicing/templates",
            "/api/v1/invoicing/uninvoiced",  # employee names + hourly rates, org-wide
            "/api/v1/invoicing/recurring-backlog",  # every client's unbilled agreements (#302)
            f"/api/v1/invoicing/invoices/{invoice['id']}/refs",  # accounting-sync internals
        ):
            res = await c.get(path, headers=portal)
            assert res.status_code == 403, f"{path} → {res.status_code} {res.text[:200]}"
            # The same call as the owner proves the route exists and the 403 is the gate.
            assert (await c.get(path, headers=headers)).status_code == 200, path

        # Quotes were never in scope and stay out of reach entirely.
        assert (await c.get("/api/v1/invoicing/quotes", headers=portal)).status_code == 403


async def test_impersonating_a_client_hides_the_invoices_it_cannot_show(client_for) -> None:
    """Signing in as a client shows their screens, minus what the impersonator may not see.

    Granting the ``client`` role an invoice read means a staff member who cannot read invoices
    would, through a client session, be able to — which is what the #296 guard exists to stop.
    It used to stop it by **refusing the whole session**; that also meant every grant to the
    tenant-editable ``client`` role silently shrank the set of staff who could impersonate at
    all. Since #266 the session is instead capped to the impersonator's own set
    (``PermissionSet.narrowed_to`` in ``require_context``): a `member` without an invoice read
    signs in fine and simply has no invoices, and ``/meta/me`` says the view is narrowed so the
    banner does not let a partial screen pass for the client's real one.
    """
    t = await make_tenant("inv-portal-imp")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed(c, headers)
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()["id"]
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-inv-portal-imp@example.com",
                    "company_ids": [mine],
                },
                headers=headers,
            )
        ).json()
        assert (
            await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        ).status_code in (200, 201)

        async def grant_member(extra: set[str]) -> None:
            roles = (await c.get("/api/v1/roles", headers=headers)).json()
            member = next(r for r in roles if r["key"] == "member")
            res = await c.patch(
                f"/api/v1/roles/{member['id']}",
                json={"permissions": sorted(set(member["permissions"]) | extra)},
                headers=headers,
            )
            assert res.status_code == 200, res.text

        await grant_member({"portal.login.impersonate", "contacts.contact.read"})
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "accountmanager@bureau.nl", "role": "member"},
            headers=headers,
        )
        assert invited.status_code in (200, 201), invited.text
        async with async_session_maker() as session:
            staff_user = await session.scalar(
                select(User).where(User.email == "accountmanager@bureau.nl")
            )
        staff = await auth_cookie(staff_user)

        def enter(res, base):  # noqa: ANN202 — the grant cookie set beside the real session
            body = res.json()
            return {**base, "Cookie": f"{base['Cookie']}; {body['cookie']}={body['token']}"}

        # No invoice read: the session opens, and the invoices are simply not in it.
        started = await c.post(
            f"/api/v1/portal/logins/contact/{contact['id']}/impersonate",
            json={"minutes": 10},
            headers=staff,
        )
        assert started.status_code == 200, started.text
        blind = enter(started, staff)
        me = (await c.get("/api/v1/meta/me", headers=blind)).json()
        assert me["impersonated_by"] == "accountmanager@bureau.nl"
        assert not any(p.startswith("invoicing.invoice.read") for p in me["permissions"])
        # Said out loud, so the banner can: an unlabelled partial view is a screen that lies.
        assert me["impersonation_narrowed"] is True
        assert (await c.get("/api/v1/invoicing/invoices", headers=blind)).status_code == 403
        # The rest of the client's portal is intact — narrowed, not broken.
        assert (await c.get("/api/v1/companies", headers=blind)).status_code == 200

        # Give them the client's own read and the invoices appear, unnarrowed.
        await grant_member({"invoicing.invoice.read:own"})
        seeing = enter(
            await c.post(
                f"/api/v1/portal/logins/contact/{contact['id']}/impersonate",
                json={"minutes": 10},
                headers=staff,
            ),
            staff,
        )
        me = (await c.get("/api/v1/meta/me", headers=seeing)).json()
        assert "invoicing.invoice.read:own" in me["permissions"]
        assert me["impersonation_narrowed"] is False
        listed = await c.get("/api/v1/invoicing/invoices", headers=seeing)
        assert listed.status_code == 200, listed.text

        # An admin holds the read at `:any`; the impersonated session still gets the *client's*
        # `:own`, never the admin's broader one — a cap, never a promotion.
        admin_seeing = enter(
            await c.post(
                f"/api/v1/portal/logins/contact/{contact['id']}/impersonate",
                json={"minutes": 10},
                headers=headers,
            ),
            headers,
        )
        me = (await c.get("/api/v1/meta/me", headers=admin_seeing)).json()
        assert "invoicing.invoice.read:own" in me["permissions"]
        assert "invoicing.invoice.read:any" not in me["permissions"]
        assert me["impersonation_narrowed"] is False
        # …so the module surfaces stay shut even for an owner inside a client session.
        assert (
            await c.get("/api/v1/invoicing/products", headers=admin_seeing)
        ).status_code == 403


async def test_restricted_staff_still_see_their_clients_drafts(client_for) -> None:
    """The draft rule follows ``is_portal``, not the scope — otherwise the fix would have
    taken drafts away from the staff member whose job is writing them."""
    t = await make_tenant("inv-portal-staff-drafts")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed(c, headers)
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()["id"]
        theirs = (
            await c.post("/api/v1/companies", json={"name": "Andere BV"}, headers=headers)
        ).json()["id"]
        draft = await _invoice(c, headers, mine, issue=False)
        await _invoice(c, headers, theirs, issue=True)

        # A staff member restricted to one company group (#191) — not an external login.
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "medewerker@bureau.nl", "role": "admin"},
            headers=headers,
        )
        assert invited.status_code in (200, 201), invited.text
        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Groep"}, headers=headers
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/companies",
                json={"company_ids": [mine]},
                headers=headers,
            )
        ).status_code in (200, 204)
        members = (await c.get("/api/v1/members", headers=headers)).json()
        rows = members["items"] if isinstance(members, dict) else members
        membership_id = next(
            m["membership_id"] for m in rows if m["email"] == "medewerker@bureau.nl"
        )
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [membership_id]},
                headers=headers,
            )
        ).status_code == 204

        async with async_session_maker() as session:
            staff_user = await session.scalar(
                select(User).where(User.email == "medewerker@bureau.nl")
            )
        staff = await auth_cookie(staff_user)

        listed = (await c.get("/api/v1/invoicing/invoices", headers=staff)).json()
        assert [i["id"] for i in listed["items"]] == [draft["id"]]
        assert listed["total"] == 1
        assert (
            await c.get(f"/api/v1/invoicing/invoices/{draft['id']}", headers=staff)
        ).status_code == 200
        # …and the module's own surfaces stay open to them: they hold the read at `:any`.
        assert (await c.get("/api/v1/invoicing/products", headers=staff)).status_code == 200


async def test_client_invoice_list_is_not_n_plus_one(client_for, count_queries) -> None:
    """docs/PERFORMANCE.md: the shape a functional test cannot see. The portal repository
    adds a predicate, not a query per row."""
    t = await make_tenant("inv-portal-perf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _seed(c, headers)
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()["id"]
        for _ in range(6):
            await _invoice(c, headers, mine, issue=True)
        portal = await _portal_login(c, headers, "inv-portal-perf", mine)

        with count_queries() as counter:
            res = await c.get(
                "/api/v1/invoicing/invoices", params={"lines": False}, headers=portal
            )
        assert res.status_code == 200
        assert res.json()["total"] == 6
        assert len(counter.matching("FROM invoices")) <= 2, counter.matching("FROM invoices")
        assert len(counter) < 15, len(counter)


# --------------------------------------------------------------------------- #
# Getting an existing org there
# --------------------------------------------------------------------------- #
async def test_reconciler_revision_rescopes_and_grants(client_for) -> None:
    """The half a fresh tenant cannot show: an org seeded before #266 holds the read *bare*,
    which the roles API may no longer store, and its ``client`` role holds nothing.

    Rewinding a real tenant to that state is the only honest way to test it — the revision
    must then rewrite the bare grant to ``:any`` (changing no one's access: ``has()`` already
    answered ``True`` for a bare key at every scope) and hand ``client`` the narrow half.
    """
    t = await make_tenant("inv-portal-rev")
    marker = REVISIONS[0].marker
    assert marker == "@rev:266-invoice-read-scoped"

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        roles = {
            r.key: r
            for r in (
                await session.execute(select(Role).where(Role.org_id == t.org.id))
            ).scalars()
        }
        # Rewind to a pre-#266 org: admin holds the bare key, client holds nothing, and the
        # revision has never run.
        await session.execute(
            RolePermission.__table__.delete().where(
                RolePermission.org_id == t.org.id,
                RolePermission.permission.in_(
                    ["invoicing.invoice.read:any", "invoicing.invoice.read:own"]
                ),
            )
        )
        session.add(
            RolePermission(
                org_id=t.org.id,
                role_id=roles["admin"].id,
                permission="invoicing.invoice.read",
            )
        )
        # An API key minted before the change holds the bare string too. Its access is
        # unaffected, but `_validate_scopes` would refuse the key's own next edit.
        session.add(
            ApiKey(
                org_id=t.org.id,
                name="Koppeling",
                prefix="sk_test_rev266",
                hash="x" * 64,
                principal_type="user",
                user_id=t.user.id,
                scopes=["companies.company.read", "invoicing.invoice.read"],
            )
        )
        org_settings = await session.scalar(
            select(OrgSettings).where(OrgSettings.org_id == t.org.id)
        )
        org_settings.applied_permission_defaults = [
            k for k in (org_settings.applied_permission_defaults or ()) if k != marker
        ]
        await session.commit()

    # Boot the reconciler over that org, exactly as the lifespan hook does.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        org = await session.get(type(t.org), t.org.id)
        await reconcile_org(org, session)
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        held = {
            (role_key, permission)
            for role_key, permission in (
                await session.execute(
                    select(Role.key, RolePermission.permission)
                    .join(RolePermission, RolePermission.role_id == Role.id)
                    .where(
                        Role.org_id == t.org.id,
                        RolePermission.permission.like("invoicing.invoice.read%"),
                    )
                )
            ).all()
        }
        assert ("admin", "invoicing.invoice.read:any") in held
        assert ("client", "invoicing.invoice.read:own") in held
        # The bare string is gone — `validate_permissions` rejects it, so leaving it behind
        # would 422 the tenant's next save of a role that was working fine.
        assert not any(p == "invoicing.invoice.read" for _, p in held)
        org_settings = await session.scalar(
            select(OrgSettings).where(OrgSettings.org_id == t.org.id)
        )
        assert marker in org_settings.applied_permission_defaults
        # The key's scopes were rewritten in place, and nothing else about it moved.
        key = await session.scalar(select(ApiKey).where(ApiKey.org_id == t.org.id))
        assert key.scopes == ["companies.company.read", "invoicing.invoice.read:any"]

    # Idempotent: a second boot changes nothing.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        org = await session.get(type(t.org), t.org.id)
        assert await reconcile_org(org, session) == 0
        await session.commit()

    # And the roles API accepts a round-trip of what is now stored — the 422 this prevents.
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        roles = (await c.get("/api/v1/roles", headers=headers)).json()
        admin = next(r for r in roles if r["key"] == "admin")
        saved = await c.patch(
            f"/api/v1/roles/{admin['id']}",
            json={"permissions": admin["permissions"]},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
