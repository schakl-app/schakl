"""The wordpress module's API surface: the credential, the probe, isolation, the horizon.

Every test drives :class:`~tests.wordpress_fake.FakeWordPress` through the client's single
transport seam — nothing here touches the network.

The tests that matter most are the ones about *degradation*. This module has four surfaces
behind one credential, and the failure it is most exposed to is a verify that reads one
endpoint's refusal as a verdict on all of them (docs/CLOUDFLARE.md's health-probe lesson). So
there is a test per way a site can be partly reachable, and each asserts that the *other*
surfaces stayed green.
"""

from __future__ import annotations

import pytest

from app.db import async_session_maker, set_current_org
from app.integrations.wordpress import client as wp_client
from tests.conftest import add_membership, auth_cookie, make_tenant
from tests.wordpress_fake import FakeWordPress

PASSWORD = "abcd EFGH ijkl MNOP qrst UVWX"


@pytest.fixture
def wp(monkeypatch) -> FakeWordPress:
    fake = FakeWordPress()
    wp_client.set_transport(fake.transport())
    yield fake
    wp_client.set_transport(None)


async def _website(c, headers, *, key: str = "a") -> dict:
    """A company → domain → website chain, which is what a credential hangs off."""
    company = await c.post("/api/v1/companies", json={"name": f"Klant {key}"}, headers=headers)
    assert company.status_code == 201, company.text
    domain = await c.post(
        "/api/v1/domains",
        json={"name": f"klant-{key}.nl", "company_id": company.json()["id"]},
        headers=headers,
    )
    assert domain.status_code == 201, domain.text
    website = await c.post(
        "/api/v1/websites",
        json={"domain_id": domain.json()["id"], "root": True},
        headers=headers,
    )
    assert website.status_code == 201, website.text
    return {"company": company.json(), "domain": domain.json(), "website": website.json()}


async def _connect(c, headers, website_id: str, **overrides) -> dict:
    body = {
        "website_id": website_id,
        "base_url": "https://klant.nl",
        "username": "agency",
        "app_password": PASSWORD,
    } | overrides
    response = await c.post("/api/v1/wordpress/sites", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- the credential


async def test_the_application_password_never_comes_back(client_for, wp) -> None:
    """Write-only, on every read path — the create response included."""
    t = await make_tenant("wp-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])

        assert created["password_configured"] is True
        assert created["status"] == "pending", "a fresh credential is unverified, not active"

        got = (
            await c.get(f"/api/v1/wordpress/sites/{created['id']}", headers=headers)
        ).json()
        listed = (await c.get("/api/v1/wordpress/sites", headers=headers)).json()
        verified = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        for payload in (created, got, listed, verified):
            assert PASSWORD not in str(payload)
            assert "app_password" not in str(payload)


async def test_one_credential_per_website(client_for, wp) -> None:
    """The unique index is the feature. A second credential for one site is a 409, not a
    silent second row that whichever query ran first would win."""
    t = await make_tenant("wp-unique")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        await _connect(c, headers, made["website"]["id"])

        again = await c.post(
            "/api/v1/wordpress/sites",
            json={
                "website_id": made["website"]["id"],
                "base_url": "https://elders.nl",
                "username": "other",
                "app_password": PASSWORD,
            },
            headers=headers,
        )
        assert again.status_code == 409
        assert again.json()["error"]["message"] == "errors.wordpress_already_connected"


async def test_the_url_is_normalised_so_one_site_cannot_become_two(client_for, wp) -> None:
    t = await make_tenant("wp-url")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(
            c, headers, made["website"]["id"], base_url="KLANT.NL/blog/"
        )
        assert created["base_url"] == "https://klant.nl/blog"


async def test_rotating_the_password_forgets_what_the_old_one_reached(client_for, wp) -> None:
    """A rotated credential has been observed by nobody.

    Leaving the ✓s up would have the panel vouching for a password nothing has tried — the
    "we looked" / "nobody has looked" distinction thrown away, which is the whole reason
    ``capabilities_checked_at`` exists as its own column.
    """
    t = await make_tenant("wp-rotate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        await c.post(f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers)

        before = (
            await c.get(f"/api/v1/wordpress/sites/{created['id']}", headers=headers)
        ).json()
        assert before["capabilities"]["rest"] is True
        assert before["capabilities_checked_at"] is not None

        rotated = (
            await c.patch(
                f"/api/v1/wordpress/sites/{created['id']}",
                json={"app_password": "zzzz YYYY xxxx WWWW vvvv UUUU"},
                headers=headers,
            )
        ).json()
        assert rotated["capabilities"] == {}
        assert rotated["capabilities_checked_at"] is None
        assert rotated["status"] == "pending"


# ---------------------------------------------------------------- the probe


async def test_a_healthy_site_reports_every_surface(client_for, wp) -> None:
    t = await make_tenant("wp-probe-ok")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["ok"] is True
        assert result["status"] == "active"
        assert result["error"] is None
        assert result["capabilities"] == {
            "rest": True,
            "admin": True,
            "abilities": True,
            "rankmath_aiv": True,
            "mcp": True,
        }
        assert result["capability_errors"] == {}
        assert result["rankmath_version"] == "1.0.275"
        assert result["rankmath_ai_visibility"] is True
        assert result["brand_count"] == 1
        # Discovered from the site's own REST index, never assumed. The fake deliberately
        # serves a *non-default* namespace, so a hardcoded path fails this.
        assert result["mcp_server_path"] == "/wp-json/mcp/agency-server"


async def test_no_rank_math_is_a_working_connection(client_for, wp) -> None:
    """The health-probe rule: a site without Rank Math is a perfectly good WordPress.

    If this ever fails, somebody has turned one endpoint's opinion into a verdict on the whole
    integration — which is the exact mistake this module was written to avoid.
    """
    wp.rankmath_version = None
    t = await make_tenant("wp-no-rm")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["ok"] is True
        assert result["status"] == "active"
        assert result["capabilities"]["rest"] is True
        assert result["capabilities"]["mcp"] is True
        assert result["capabilities"]["rankmath_aiv"] is False
        assert result["rankmath_version"] is None
        assert result["brand_count"] is None, "no Rank Math and zero brands are different"
        # And the refusal is *kept*: a ✗ with no explanation is the one state an admin cannot
        # act on.
        assert "rankmath_aiv" in result["capability_errors"]
        assert "rest_no_route" in result["capability_errors"]["rankmath_aiv"]


async def test_no_mcp_adapter_is_a_working_connection(client_for, wp) -> None:
    wp.mcp_namespace = None
    t = await make_tenant("wp-no-mcp")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["status"] == "active"
        assert result["capabilities"]["mcp"] is False
        assert result["capabilities"]["rankmath_aiv"] is True
        assert result["mcp_server_path"] is None


async def test_an_editor_reaches_rest_but_not_rank_math(client_for, wp) -> None:
    """Every AI Visibility route is ``manage_options``, so a non-admin password is a real and
    ordinary half-working state — and the one the panel most needs to name, because the fix is
    a different application password, not a different plugin."""
    wp.is_admin = False
    t = await make_tenant("wp-editor")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["ok"] is True
        assert result["status"] == "active", "the credential works; it is simply not an admin"
        assert result["error"] == "not_administrator"
        assert result["capabilities"]["rest"] is True
        assert result["capabilities"]["admin"] is False
        assert result["capabilities"]["rankmath_aiv"] is False
        # The plugin list is admin-only too, so the version could not be read. It must NOT be
        # reported absent: a probe that could not run leaves the previous value alone.
        assert result["rankmath_version"] is None
        assert "admin" in result["capability_errors"]


async def test_rank_math_without_a_content_ai_subscription(client_for, wp) -> None:
    """Installed, admin credential, and AI Visibility still refuses — because the *site* is not
    connected to a Rank Math account with a subscription. Nothing to do with our password, and
    the stored refusal is the only thing that says so."""
    wp.aiv_subscribed = False
    t = await make_tenant("wp-no-sub")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["status"] == "active"
        assert result["capabilities"]["rankmath_aiv"] is False
        assert result["rankmath_version"] == "1.0.275", "the plugin is installed and readable"
        assert "aiv_unauthorized" in result["capability_errors"]["rankmath_aiv"]


async def test_an_old_rank_math_is_installed_but_has_no_ai_visibility(client_for, wp) -> None:
    wp.rankmath_version = "1.0.272"
    t = await make_tenant("wp-old-rm")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["rankmath_version"] == "1.0.272"
        assert result["rankmath_ai_visibility"] is False, "AI Visibility begins at 1.0.273"


async def test_a_wrong_password_is_refused_everywhere(client_for, wp) -> None:
    """The fake authenticates every route, so this is the test that would catch a probe
    concluding "the credential is fine" from an endpoint it never authenticated against."""
    t = await make_tenant("wp-bad-pw")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(
            c, headers, made["website"]["id"], app_password="wrong wrong wrong"
        )
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["ok"] is False
        assert result["status"] == "error"
        assert result["error"] == "credential_refused"
        assert not any(result["capabilities"].values())


async def test_a_host_that_strips_the_authorization_header(client_for, wp) -> None:
    """Indistinguishable from a revoked password by status alone, common on shared hosting, and
    the reason every refusal is stored with its text rather than reduced to a boolean."""
    wp.strips_auth_header = True
    t = await make_tenant("wp-stripped")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["ok"] is False
        assert result["error"] == "credential_refused"
        assert "rest_not_logged_in" in result["capability_errors"]["rest"]


async def test_not_a_wordpress_site_is_not_a_wrong_password(client_for, wp) -> None:
    wp.is_wordpress = False
    t = await make_tenant("wp-not-wp")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        assert result["error"] == "not_wordpress"

        # **A key that was never asked is absent, not False.** `admin` lives inside the
        # `users/me` body and `mcp` inside the REST index's namespace list, so when those
        # never answered there is nothing to report — and reporting `False` would draw a red
        # cross the panel cannot explain, against a question nobody put. Found in the browser:
        # a `dict.fromkeys(CAPABILITIES, False)` had quietly made every capability answer
        # "probed and refused", which is the one distinction this module is built on.
        assert "admin" not in result["capabilities"]
        assert "mcp" not in result["capabilities"]
        assert result["capabilities"]["rest"] is False


async def test_a_refusal_with_no_message_reads_as_the_status_alone(client_for, wp) -> None:
    """A body WordPress did not write (a proxy's HTML 502, a WAF block) has no message.

    It used to render as *"HTTP 404: HTTP 404"* — the status prefixed onto a text that was
    only the status again. Noise dressed as diagnosis is worse than the bare cross it replaced.
    """
    wp.is_wordpress = False
    t = await make_tenant("wp-bare-status")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        result = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()

        rest_error = result["capability_errors"]["rest"]
        assert "rest_no_route" in rest_error
        assert rest_error.count("HTTP 404") == 1, rest_error


async def test_a_verify_that_succeeds_clears_the_error_it_set(client_for, wp) -> None:
    """A status flag that only ever turns on is a bug with a long tail: the row keeps its red
    line through every check that works afterwards (docs/CLOUDFLARE.md's ``_flag_account``)."""
    wp.strips_auth_header = True
    t = await make_tenant("wp-clears")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        broken = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()
        assert broken["status"] == "error" and broken["error"] is not None

        wp.strips_auth_header = False
        fixed = (
            await c.post(
                f"/api/v1/wordpress/sites/{created['id']}/verify", headers=headers
            )
        ).json()
        assert fixed["status"] == "active"
        assert fixed["error"] is None
        assert fixed["capability_errors"] == {}, "stale refusals must not outlive their ✗"


# ---------------------------------------------------------------- brands


async def test_the_brand_picker_does_not_spend_the_client_s_quota(client_for, wp) -> None:
    """Listing brands to choose one is cache-first; only a *sync* forces a fresh upstream
    analysis. ``refresh_calls`` is what says the difference was honoured."""
    t = await make_tenant("wp-brands")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        brands = (
            await c.get(f"/api/v1/wordpress/sites/{created['id']}/brands", headers=headers)
        ).json()

        assert [b["id"] for b in brands] == ["brand-1"]
        assert brands[0]["name"] == "Klant BV"
        assert brands[0]["score"] == 42.5
        assert wp.refresh_calls == 0


async def test_a_brand_mid_analysis_survives_its_nulls(client_for, wp) -> None:
    """Rank Math returns nulls where numbers will be while an analysis runs. A row that
    disappeared here would read as "this client has no brands", which is indistinguishable
    from the truth on a screen."""
    wp.brands = [
        {"id": "brand-2", "name": "Nieuw", "url": "", "status": "active",
         "score": None, "rank": None, "avg_sentiment": None, "mentions": None,
         "citations": None, "analysis_status": "processing", "last_analyzed": None},
        {"name": "geen id"},  # unusable — dropped, never guessed at
    ]
    t = await make_tenant("wp-brands-null")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made = await _website(c, headers)
        created = await _connect(c, headers, made["website"]["id"])
        brands = (
            await c.get(f"/api/v1/wordpress/sites/{created['id']}/brands", headers=headers)
        ).json()

        assert [b["id"] for b in brands] == ["brand-2"]
        assert brands[0]["score"] is None
        assert brands[0]["analysis_status"] == "processing"


# ---------------------------------------------------------------- tenancy & horizon


async def test_another_tenant_cannot_see_the_credential(client_for, wp) -> None:
    t1 = await make_tenant("wp-iso-a")
    t2 = await make_tenant("wp-iso-b")
    h1 = await auth_cookie(t1.user)
    h2 = await auth_cookie(t2.user)
    async with client_for(t1.host) as c1:
        made = await _website(c1, h1)
        created = await _connect(c1, h1, made["website"]["id"])
    async with client_for(t2.host) as c2:
        assert (await c2.get("/api/v1/wordpress/sites", headers=h2)).json() == []
        # 404, never 403: a 403 on get-by-id leaks that the row exists (§15).
        assert (
            await c2.get(f"/api/v1/wordpress/sites/{created['id']}", headers=h2)
        ).status_code == 404


async def _restricted_member(client_for, c, t, owner_h, *, slug: str, company_id: str):
    """A second membership in ``t``, scoped by a group to exactly one company.

    The shape ``test_company_groups.py`` establishes: the member is conjured with its own
    tenant and then given a membership in this one, so its session must name ``t``'s org
    explicitly (a session belongs to one org — §5).
    """
    member = await make_tenant(f"{slug}-m", email=f"member-{slug}@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, member.user.id, role="admin")
        membership_id = membership.id
        await session.commit()

    group = (
        await c.post(
            "/api/v1/companies/groups", json={"name": "Team Noord"}, headers=owner_h
        )
    ).json()
    assert (
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/companies",
            json={"company_ids": [company_id]},
            headers=owner_h,
        )
    ).status_code == 204
    assert (
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/memberships",
            json={"membership_ids": [str(membership_id)]},
            headers=owner_h,
        )
    ).status_code == 204
    return await auth_cookie(member.user, org_id=t.org.id)


async def test_another_tenant_s_website_cannot_be_attached(client_for, wp) -> None:
    """An owner is unrestricted, so the horizon seam answers ``True`` without a query — which
    is the right shape for a horizon check and no tenancy check at all.

    ``websites.id`` is global, so the foreign key cannot object either: without an org-scoped
    existence check this wrote a row in *our* org whose ``website_id`` points into somebody
    else's. 404, never 403 — a 403 would confirm the id exists.
    """
    t1 = await make_tenant("wp-xorg-a")
    t2 = await make_tenant("wp-xorg-b")
    h1 = await auth_cookie(t1.user)
    h2 = await auth_cookie(t2.user)
    async with client_for(t1.host) as c1:
        theirs = await _website(c1, h1)
    async with client_for(t2.host) as c2:
        response = await c2.post(
            "/api/v1/wordpress/sites",
            json={
                "website_id": theirs["website"]["id"],
                "base_url": "https://klant.nl",
                "username": "agency",
                "app_password": PASSWORD,
            },
            headers=h2,
        )
        assert response.status_code == 404, response.text
        assert (await c2.get("/api/v1/wordpress/sites", headers=h2)).json() == []


async def test_a_website_that_does_not_exist_is_a_404(client_for, wp) -> None:
    t = await make_tenant("wp-no-site-id")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        response = await c.post(
            "/api/v1/wordpress/sites",
            json={
                "website_id": "00000000-0000-0000-0000-000000000000",
                "base_url": "https://klant.nl",
                "username": "agency",
                "app_password": PASSWORD,
            },
            headers=headers,
        )
        assert response.status_code == 404, response.text


async def test_the_website_must_be_inside_the_caller_s_horizon(client_for, wp) -> None:
    """A write that names another module's row checks the horizon *before* writing (#285).

    Without it a restricted membership could attach a credential to a client it cannot see —
    and then read the site it just connected.
    """
    t = await make_tenant("wp-horizon")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        mine = await _website(c, owner_h, key="mine")
        theirs = await _website(c, owner_h, key="theirs")
        member_h = await _restricted_member(
            client_for, c, t, owner_h, slug="wp-horizon", company_id=mine["company"]["id"]
        )

        blocked = await c.post(
            "/api/v1/wordpress/sites",
            json={
                "website_id": theirs["website"]["id"],
                "base_url": "https://theirs.nl",
                "username": "agency",
                "app_password": PASSWORD,
            },
            headers=member_h,
        )
        assert blocked.status_code == 404, blocked.text

        allowed = await c.post(
            "/api/v1/wordpress/sites",
            json={
                "website_id": mine["website"]["id"],
                "base_url": "https://mine.nl",
                "username": "agency",
                "app_password": PASSWORD,
            },
            headers=member_h,
        )
        assert allowed.status_code == 201, allowed.text


async def test_a_restricted_member_lists_only_their_own_clients_sites(client_for, wp) -> None:
    """``wordpress_sites`` carries no ``company_id``, so without
    ``__company_horizon_clause__`` the repository filters *nothing at all* — and these rows are
    WordPress administrator credentials (#285 failure mode 1)."""
    t = await make_tenant("wp-horizon-list")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        mine = await _website(c, owner_h, key="mine")
        theirs = await _website(c, owner_h, key="theirs")
        await _connect(c, owner_h, mine["website"]["id"])
        await _connect(c, owner_h, theirs["website"]["id"], base_url="https://theirs.nl")

        member_h = await _restricted_member(
            client_for, c, t, owner_h, slug="wp-hz-list", company_id=mine["company"]["id"]
        )
        listed = (await c.get("/api/v1/wordpress/sites", headers=member_h)).json()
        assert [row["website_id"] for row in listed] == [mine["website"]["id"]]

        # And the owner still sees both — "nothing leaked" must not quietly mean "nothing
        # matched" (the control run test_company_groups.py closes with).
        assert len((await c.get("/api/v1/wordpress/sites", headers=owner_h)).json()) == 2
