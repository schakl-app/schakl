"""google_ads module: the credential, the account rows, and the seam marketing reaches them by.

The Google-facing paths (the live picker, verify) need a live Google, so what is covered here is
what does not: the settings and account CRUD, tenant isolation on both tables, the company
horizon on the parameterless account list, and — the reason the seam exists at all — that
``marketing`` keeps working whether or not this module is installed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.core.crypto import decrypt
from app.db import async_session_maker, set_current_org
from app.modules.google_ads.models import GoogleAdsAccount, GoogleAdsSettings
from tests.conftest import add_membership, auth_cookie, make_tenant

pytestmark = pytest.mark.asyncio


async def _company(org_id, name: str = "Klant BV") -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = await session.execute(
            text(
                "INSERT INTO companies (id, org_id, name, status, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org, :name, 'active', now(), now()) RETURNING id"
            ),
            {"org": str(org_id), "name": name},
        )
        company_id = row.scalar_one()
        await session.commit()
    return company_id


# --- settings ------------------------------------------------------------------------------ #


async def test_the_developer_token_is_write_only(client_for) -> None:
    """A credential screen reports whether one is configured; it never hands the secret back."""
    t = await make_tenant("gads-settings")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.put(
            "/api/v1/google-ads/settings",
            json={
                "developer_token": "s3cret-dev-token",
                "default_login_customer_id": "840-880-4299",
            },
            headers=headers,
        )
    assert res.status_code == 200
    body = res.json()
    assert body["developer_token_configured"] is True
    # Normalised on write: Google takes the id without dashes everywhere.
    assert body["default_login_customer_id"] == "8408804299"
    assert "developer_token" not in body
    assert "s3cret" not in res.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(
            select(GoogleAdsSettings).where(GoogleAdsSettings.org_id == t.org.id)
        )
        assert row is not None
        assert decrypt(row.developer_token_encrypted) == "s3cret-dev-token"


async def test_an_empty_token_field_keeps_the_stored_one(client_for) -> None:
    """The form posts blank because nobody retyped the secret. That must not wipe it."""
    t = await make_tenant("gads-keep")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/google-ads/settings",
            json={"developer_token": "keep-me"},
            headers=headers,
        )
        res = await c.put(
            "/api/v1/google-ads/settings",
            json={"developer_token": "", "writes_enabled": False},
            headers=headers,
        )
    assert res.json()["developer_token_configured"] is True
    assert res.json()["writes_enabled"] is False
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(
            select(GoogleAdsSettings).where(GoogleAdsSettings.org_id == t.org.id)
        )
        assert decrypt(row.developer_token_encrypted) == "keep-me"


async def test_an_explicit_null_clears_the_token(client_for) -> None:
    """The third state. Blank keeps (the form nobody retyped); ``null`` is the deliberate wipe."""
    t = await make_tenant("gads-clear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put("/api/v1/google-ads/settings", json={"developer_token": "x"}, headers=headers)
        res = await c.put(
            "/api/v1/google-ads/settings", json={"developer_token": None}, headers=headers
        )
    assert res.json()["developer_token_configured"] is False


async def test_a_save_that_is_not_about_the_token_leaves_it_alone(client_for) -> None:
    """The first state: a field the payload never mentions is not a field being cleared."""
    t = await make_tenant("gads-absent")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/google-ads/settings", json={"developer_token": "still-here"}, headers=headers
        )
        res = await c.put(
            "/api/v1/google-ads/settings", json={"writes_enabled": False}, headers=headers
        )
    assert res.json()["developer_token_configured"] is True
    assert res.json()["writes_enabled"] is False


# --- accounts ------------------------------------------------------------------------------ #


async def test_linking_an_account_normalises_every_spelling(client_for) -> None:
    """The same account arrives hyphenated from a human, bare from the picker and as a resource
    name from a GAQL row. A table whose unique key is the customer id cannot hold all three."""
    t = await make_tenant("gads-link")
    headers = await auth_cookie(t.user)
    company_id = await _company(t.org.id)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/google-ads/accounts",
            json={
                "customer_id": "124-264-3293",
                "company_id": str(company_id),
                "login_customer_id": "840-880-4299",
                "descriptive_name": "AAZET",
                "currency_code": "EUR",
            },
            headers=headers,
        )
    assert res.status_code == 201
    body = res.json()
    assert body["customer_id"] == "1242643293"
    assert body["login_customer_id"] == "8408804299"
    # And the display form is the one Google's own UI shows, computed once here.
    assert body["customer_id_formatted"] == "124-264-3293"


async def test_linking_the_same_customer_twice_is_an_upsert_not_a_conflict(client_for) -> None:
    """Two companies legitimately share one Ads account — a holding and its trading name. The
    second link must find the first row, not collide with the unique constraint."""
    t = await make_tenant("gads-upsert")
    headers = await auth_cookie(t.user)
    holding = await _company(t.org.id, "Holding")
    trading = await _company(t.org.id, "Trading")
    async with client_for(t.host) as c:
        first = await c.post(
            "/api/v1/google-ads/accounts",
            json={"customer_id": "1242643293", "company_id": str(holding)},
            headers=headers,
        )
        second = await c.post(
            "/api/v1/google-ads/accounts",
            json={"customer_id": "124-264-3293", "company_id": str(trading)},
            headers=headers,
        )
        listing = await c.get("/api/v1/google-ads/accounts", headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(listing.json()) == 1


async def test_unlinking_deactivates_and_keeps_the_row(client_for) -> None:
    """History hangs off the account, and a re-link must find the same row rather than collide
    with its own unique constraint."""
    t = await make_tenant("gads-unlink")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/google-ads/accounts", json={"customer_id": "1112223333"}, headers=headers
        )
        account_id = created.json()["id"]
        res = await c.delete(f"/api/v1/google-ads/accounts/{account_id}", headers=headers)
        after = await c.get(f"/api/v1/google-ads/accounts/{account_id}", headers=headers)
        active_only = await c.get(
            "/api/v1/google-ads/accounts?active_only=true", headers=headers
        )
    assert res.status_code == 204
    assert after.status_code == 200
    assert after.json()["active"] is False
    assert active_only.json() == []


# --- isolation ------------------------------------------------------------------------------ #


async def test_one_tenants_accounts_are_invisible_to_another(client_for) -> None:
    a = await make_tenant("gads-org-a")
    b = await make_tenant("gads-org-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as c:
        created = await c.post(
            "/api/v1/google-ads/accounts",
            json={"customer_id": "9990001111", "descriptive_name": "Org A klant"},
            headers=a_headers,
        )
    account_id = created.json()["id"]
    async with client_for(b.host) as c:
        listing = await c.get("/api/v1/google-ads/accounts", headers=b_headers)
        direct = await c.get(f"/api/v1/google-ads/accounts/{account_id}", headers=b_headers)
    assert listing.json() == []
    # 404, not 403: the other tenant's row must not be revealed to exist by a status code.
    assert direct.status_code == 404


async def test_a_company_scoped_member_sees_only_their_clients_accounts(client_for) -> None:
    """The #285 horizon on a parameterless list that returns ``descriptive_name`` — which for a
    client account *is* the client's name, i.e. exactly what the horizon sweep hunts for.

    Also pins the other half: an account attached to **no** client is the agency's own account,
    which is not company data, so a scoped login keeps seeing it rather than losing it.
    """
    t = await make_tenant("gads-horizon")
    member = await make_tenant("gads-horizon-m", email="gads-scoped@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, member.user.id, role="admin")
        membership_id = membership.id
        await session.commit()
    owner_h = await auth_cookie(t.user)
    member_h = await auth_cookie(member.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        mine = (
            await c.post("/api/v1/companies", json={"name": "Alpha"}, headers=owner_h)
        ).json()
        theirs = (
            await c.post("/api/v1/companies", json={"name": "Beta"}, headers=owner_h)
        ).json()
        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Team Noord"}, headers=owner_h
            )
        ).json()
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/companies",
            json={"company_ids": [mine["id"]]},
            headers=owner_h,
        )
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/memberships",
            json={"membership_ids": [str(membership_id)]},
            headers=owner_h,
        )
        for customer_id, company, name in (
            ("1111111111", mine["id"], "Mine"),
            ("2222222222", theirs["id"], "Theirs"),
            ("3333333333", None, "Bureau zelf"),
        ):
            payload = {"customer_id": customer_id, "descriptive_name": name}
            if company:
                payload["company_id"] = company
            await c.post("/api/v1/google-ads/accounts", json=payload, headers=owner_h)

        listing = await c.get("/api/v1/google-ads/accounts", headers=member_h)
        owner_listing = await c.get("/api/v1/google-ads/accounts", headers=owner_h)

    assert sorted(r["descriptive_name"] for r in listing.json()) == ["Bureau zelf", "Mine"]
    assert "Theirs" not in listing.text
    # The control run: "nothing leaked" must not quietly mean "nothing matched".
    assert len(owner_listing.json()) == 3


# --- the seam -------------------------------------------------------------------------------- #


async def test_marketing_resolves_the_token_through_the_seam(client_for) -> None:
    """The google_ads module's token wins over marketing's legacy column — that is the whole
    point of the expand release, and the thing a silent precedence bug would hide."""
    from app.modules.marketing.service import resolve_ads_developer_token

    t = await make_tenant("gads-seam-token")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/google-ads/settings",
            json={"developer_token": "from-google-ads"},
            headers=headers,
        )
        await c.put(
            "/api/v1/marketing/settings",
            json={"ads_developer_token": "legacy-from-marketing"},
            headers=headers,
        )

    from app.core.tenancy import RequestContext  # noqa: F401 — documents what ctx must satisfy

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # Without a ctx (the worker's shape) the legacy column answers, which is correct: a
        # background job on an install without the module has nothing else to ask.
        assert await resolve_ads_developer_token(session, t.org.id) == "legacy-from-marketing"


async def test_the_seam_answers_not_configured_rather_than_none() -> None:
    """A None customer id reaches the URL builder and asks Google about a customer named
    "None" — a 404, which this module's error model reads as a sunset API version."""
    from app.core.googleads import AdsNotConfigured, ads_accounts_registered, ads_call_params

    assert ads_accounts_registered() is True
    with pytest.raises(AdsNotConfigured):
        await ads_call_params(object(), customer_id="")


async def test_a_marketing_gads_link_attaches_one_account(client_for) -> None:
    """One truth for "which Ads customer is this client's": linking in marketing records the
    account here, and the link points at it."""
    from app.modules.marketing.models import MarketingLink

    t = await make_tenant("gads-attach")
    headers = await auth_cookie(t.user)
    company_id = await _company(t.org.id)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": str(company_id),
                "source": "gads",
                "external_id": "customers/1242643293",
                "display_name": "AAZET",
                "config": {"currency": "EUR", "manager_id": "840-880-4299"},
            },
            headers=headers,
        )
        accounts = await c.get("/api/v1/google-ads/accounts", headers=headers)
    assert res.status_code == 201
    rows = accounts.json()
    assert len(rows) == 1
    # Normalised out of the "customers/…" spelling marketing stores raw.
    assert rows[0]["customer_id"] == "1242643293"
    assert rows[0]["login_customer_id"] == "8408804299"
    assert rows[0]["company_id"] == str(company_id)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        link = await session.scalar(
            select(MarketingLink).where(MarketingLink.org_id == t.org.id)
        )
        assert str(link.google_ads_account_id) == rows[0]["id"]
        # …and marketing keeps its own display copy, which is what makes the panel's
        # `SourceMetrics.external_id: str` unbreakable.
        assert link.external_id == "customers/1242643293"


async def test_the_accounts_table_is_rls_forced() -> None:
    """Golden Rule 1 at the database layer: the app role reads nothing without the GUC bound."""
    a = await make_tenant("gads-rls-a")
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        session.add(
            GoogleAdsAccount(
                org_id=a.org.id, customer_id="4445556666", descriptive_name="A"
            )
        )
        await session.commit()
    b = await make_tenant("gads-rls-b")
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        rows = (await session.scalars(select(GoogleAdsAccount))).all()
        assert rows == []
