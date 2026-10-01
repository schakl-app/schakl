"""meta: the app, the credentials, the token's clock and the assets — through the fake transport.

Every request travels the real path builder, the real parameter encoding, the real paging loop
and the real error classifier; the fake is installed at the transport, the lowest seam there is.

What is asserted beyond "it works": that a secret is never read back, that a token's expiry is
*managed* rather than discovered, that the two credentials (the app's secret, the system
user's token) fail as two different sentences, that discovery links nothing, and that tenant
isolation and the company horizon hold on the asset table.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import decrypt
from app.core.metagraph import set_transport
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.integrations.meta.jobs import refresh_org_tokens
from app.integrations.meta.models import MetaAsset, MetaCredential
from tests.conftest import auth_cookie, make_tenant
from tests.meta_fake import (
    AD_ACCOUNT_CLIENT,
    APP_ID,
    APP_SECRET,
    IG_CLIENT,
    PAGE_CLIENT,
    PAGE_OWN,
    SYSTEM_TOKEN,
    FakeMeta,
    error,
)
from tests.meta_helpers import BASE, add_credential, company, configure, member, ready

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake() -> FakeMeta:
    stub = FakeMeta()
    set_transport(stub.transport())
    try:
        yield stub
    finally:
        set_transport(None)


async def _refresh_tokens(tenant) -> None:  # noqa: ANN001
    """The nightly token job, for one org."""
    async with async_session_maker() as session:
        org = await session.scalar(select(Org).where(Org.id == tenant.org.id))
        await set_current_org(session, org.id)
        await refresh_org_tokens(org, session)
        await session.commit()


async def _credential_row(tenant, credential_id: str) -> MetaCredential:  # noqa: ANN001
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        return await session.scalar(
            select(MetaCredential).where(MetaCredential.id == credential_id)
        )


# --- settings ------------------------------------------------------------------------------- #


async def test_settings_default_to_writing_and_our_own_scheduler(client_for) -> None:
    """Absence is a stated default: an org that never opened the screen still gets an answer,
    and the answer names what the setup guide needs — the scopes and the media address."""
    t = await make_tenant("meta-settings")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.get(f"{BASE}/settings", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["app_id"] is None
    assert body["app_secret_configured"] is False
    assert body["writes_enabled"] is True
    assert body["facebook_scheduler"] == "schakl"
    assert "pages_manage_posts" in body["recommended_scopes"]
    assert body["media_url_prefix"].endswith("/api/v1/meta-business/media/")


async def test_the_app_secret_is_written_and_never_read(client_for) -> None:
    t = await make_tenant("meta-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.put(
            f"{BASE}/settings",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            headers=headers,
        )
        assert res.status_code == 200
        assert APP_SECRET not in res.text
        assert res.json()["app_secret_configured"] is True
        # Leaving it out keeps it; sending null clears it (§18).
        res = await c.put(f"{BASE}/settings", json={"writes_enabled": False}, headers=headers)
        assert res.json()["app_secret_configured"] is True
        res = await c.put(f"{BASE}/settings", json={"app_secret": None}, headers=headers)
        assert res.json()["app_secret_configured"] is False


async def test_an_app_id_that_is_not_a_number_is_refused_naming_the_field(client_for) -> None:
    t = await make_tenant("meta-appid")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.put(f"{BASE}/settings", json={"app_id": "my-app"}, headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["fields"] == {"app_id": "errors.meta_app_id_invalid"}


async def test_a_member_may_not_open_the_settings(client_for) -> None:
    t = await make_tenant("meta-settings-member")
    headers = await member(t, "lid@meta-settings-member.example.com")
    async with client_for(t.host) as c:
        assert (await c.get(f"{BASE}/settings", headers=headers)).status_code == 403
        assert (await c.get(f"{BASE}/credentials", headers=headers)).status_code == 403


# --- credentials ---------------------------------------------------------------------------- #


async def test_a_token_is_verified_the_moment_it_is_stored(client_for, fake) -> None:
    """A token pasted wrong should say so while Business Settings is still open in the other
    tab — so adding one asks Meta at once who it is, what it may do and when it ends."""
    fake.expire_in(SYSTEM_TOKEN, 58)
    t = await make_tenant("meta-cred")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        body = await add_credential(c, headers)
    assert body["status"] == "active"
    assert body["token_kind"] == "SYSTEM_USER"
    assert body["app_matches"] is True
    assert body["business_name"] == "breik. B.V."
    assert body["subject_name"] == "schakl system user"
    assert body["missing_scopes"] == []
    assert body["capabilities"]["facebook_publish"] is True
    assert body["capabilities"]["instagram_publish"] is True
    assert 56 <= body["days_left"] <= 58
    assert body["refresh_due_at"] is not None
    assert SYSTEM_TOKEN not in str(body)


async def test_a_token_that_never_expires_has_no_clock(client_for, fake) -> None:
    """Meta's ``expires_at: 0`` is stored as absence, not as 1970 — and is never refreshed."""
    t = await make_tenant("meta-never")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        body = await add_credential(c, headers)
    assert body["expires_at"] is None
    assert body["days_left"] is None
    assert body["refresh_due_at"] is None
    await _refresh_tokens(t)
    assert fake.refreshes == 0


async def test_missing_scopes_are_named_as_capabilities(client_for, fake) -> None:
    """``instagram_content_publish`` is a lookup; "cannot publish to Instagram" is a sentence."""
    fake.tokens[SYSTEM_TOKEN].scopes = (
        "business_management",
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
    )
    t = await make_tenant("meta-scopes")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        body = await add_credential(c, headers)
    assert body["status"] == "active"
    assert body["capabilities"]["facebook_publish"] is True
    assert body["capabilities"]["instagram_publish"] is False
    assert body["capabilities"]["ads_write"] is False
    assert "instagram_content_publish" in body["missing_scopes"]


async def test_a_dead_token_is_stored_and_says_so(client_for, fake) -> None:
    """Never a refusal to store: the outcome is the row, where the screen draws it."""
    t = await make_tenant("meta-dead")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        body = await add_credential(c, headers, token="EAAnot-a-token-that-meta-knows-0000000")
    assert body["status"] == "expired"
    assert body["last_error"]
    assert "EAAnot-a-token" not in str(body["last_error"])


async def test_a_token_for_another_app_is_flagged(client_for, fake) -> None:
    """The refresh authenticates as the app in the settings, so a token generated for a
    different app would be refused there — said now, not in forty days."""
    fake.tokens[SYSTEM_TOKEN].app_id = "999"
    t = await make_tenant("meta-otherapp")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        body = await add_credential(c, headers)
    assert body["app_matches"] is False


async def test_two_credentials_may_not_share_a_label(client_for, fake) -> None:
    """A uniqueness the database guarantees is a refusal the service owes (§10) — a 409
    naming the field, never the index's 500."""
    t = await make_tenant("meta-label")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        await add_credential(c, headers)
        res = await c.post(
            f"{BASE}/credentials",
            json={"label": "breik. portfolio", "token": SYSTEM_TOKEN},
            headers=headers,
        )
    assert res.status_code == 409
    assert res.json()["error"]["fields"] == {"label": "errors.meta_label_taken"}


async def test_every_call_carries_the_proof_of_server(client_for, fake) -> None:
    """``appsecret_proof`` rides every call once the app secret is known, so an app with
    "Require App Secret" switched on works without anybody having to know the switch exists."""
    fake.require_proof = True
    t = await make_tenant("meta-proof")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        body = await add_credential(c, headers)
        res = await c.post(f"{BASE}/credentials/{body['id']}/discover", headers=headers)
    assert body["status"] == "active"
    assert res.status_code == 200
    assert res.json()["found"] == 5


async def test_the_token_never_travels_in_a_url(client_for, fake) -> None:
    t = await make_tenant("meta-url")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        body = await add_credential(c, headers)
        await c.post(f"{BASE}/credentials/{body['id']}/discover", headers=headers)
    graph_calls = [call for call in fake.calls if call.path not in ("debug_token",)]
    assert graph_calls
    assert all("access_token" not in call.params for call in graph_calls)
    assert all(call.token == SYSTEM_TOKEN for call in graph_calls)


# --- the token's clock ---------------------------------------------------------------------- #


async def test_a_token_is_refreshed_weeks_before_it_expires(client_for, fake) -> None:
    """A system-user token cannot be refreshed once it is dead, so the nightly job does it
    with twenty days to spare — and the new token is what every later call uses."""
    fake.expire_in(SYSTEM_TOKEN, 15)
    r = await ready(client_for, "meta-refresh")
    await _refresh_tokens(r.tenant)

    row = await _credential_row(r.tenant, r.credential_id)
    assert fake.refreshes == 1
    assert decrypt(row.token_encrypted) != SYSTEM_TOKEN
    assert row.refreshed_at is not None
    assert row.refresh_error is None
    assert row.expires_at - datetime.now(UTC) > timedelta(days=58)

    async with client_for(r.tenant.host) as c:
        res = await c.post(f"{BASE}/credentials/{r.credential_id}/discover", headers=r.headers)
    assert res.status_code == 200
    assert fake.calls[-1].token == decrypt(row.token_encrypted)


async def test_a_token_with_months_left_is_left_alone(client_for, fake) -> None:
    fake.expire_in(SYSTEM_TOKEN, 45)
    r = await ready(client_for, "meta-norefresh")
    await _refresh_tokens(r.tenant)
    assert fake.refreshes == 0


async def test_a_refresh_forgets_the_page_tokens_derived_from_the_old_one(client_for, fake) -> None:
    """A Page token is derived from its credential's. Whether it survives a refresh is not
    documented, so nothing assumes it does: a new parent means new children."""
    fake.expire_in(SYSTEM_TOKEN, 10)
    r = await ready(client_for, "meta-pagetoken")
    async with client_for(r.tenant.host) as c:
        res = await c.get(f"{BASE}/assets/{r.page_id}/published", headers=r.headers)
        assert res.status_code == 200
    async with async_session_maker() as session:
        await set_current_org(session, r.tenant.org.id)
        page = await session.scalar(select(MetaAsset).where(MetaAsset.id == r.page_id))
        assert page.page_token_encrypted is not None
    await _refresh_tokens(r.tenant)
    async with async_session_maker() as session:
        await set_current_org(session, r.tenant.org.id)
        page = await session.scalar(select(MetaAsset).where(MetaAsset.id == r.page_id))
        assert page.page_token_encrypted is None


async def test_a_refused_app_secret_is_not_reported_as_a_token_problem(client_for, fake) -> None:
    """Two credentials, two people who can fix them. Reporting the app's secret as the token
    sends an admin to generate a new token when the one they have is fine."""
    fake.expire_in(SYSTEM_TOKEN, 12)
    r = await ready(client_for, "meta-appsecret")
    fake.app_secret = "somebody-reset-it-in-the-dashboard"  # noqa: S105
    await _refresh_tokens(r.tenant)

    row = await _credential_row(r.tenant, r.credential_id)
    assert row.refresh_error == "meta.error.app_secret_refused"
    # The token itself still works, and the row says so.
    assert row.status == "active"
    assert decrypt(row.token_encrypted) == SYSTEM_TOKEN


async def test_a_refresh_that_keeps_failing_is_reported_once_per_stage(client_for, fake) -> None:
    """Silence is how a token expires. Fourteen, seven, one: each said once, to the people
    who can generate a new one — never once a night."""
    fake.expire_in(SYSTEM_TOKEN, 13)
    r = await ready(client_for, "meta-warn")
    fake.app_secret = "reset"  # noqa: S105
    await _refresh_tokens(r.tenant)
    await _refresh_tokens(r.tenant)

    async with client_for(r.tenant.host) as c:
        inbox = (await c.get("/api/v1/notifications", headers=r.headers)).json()
    items = inbox["items"] if isinstance(inbox, dict) else inbox
    mine = [n for n in items if n["event_type"] == "meta.token_expiring"]
    assert len(mine) == 1
    assert mine[0]["payload"]["label"] == "breik. portfolio"
    assert mine[0]["payload"]["days"] in (12, 13)

    # A week later, still refused. (Set on the row: with the app secret wrong, nothing can
    # ask Meta what the expiry is, which is exactly the situation being reported.)
    async with async_session_maker() as session:
        await set_current_org(session, r.tenant.org.id)
        row = await session.scalar(
            select(MetaCredential).where(MetaCredential.id == r.credential_id)
        )
        row.expires_at = datetime.now(UTC) + timedelta(days=6, hours=1)
        await session.commit()
    await _refresh_tokens(r.tenant)
    async with client_for(r.tenant.host) as c:
        inbox = (await c.get("/api/v1/notifications", headers=r.headers)).json()
    items = inbox["items"] if isinstance(inbox, dict) else inbox
    assert len([n for n in items if n["event_type"] == "meta.token_expiring"]) == 2


async def test_an_expired_token_cannot_be_refreshed_and_says_what_to_do(client_for, fake) -> None:
    r = await ready(client_for, "meta-expired")
    fake.tokens[SYSTEM_TOKEN].expires_at = int(time.time()) - 60
    async with client_for(r.tenant.host) as c:
        res = await c.post(f"{BASE}/credentials/{r.credential_id}/refresh", headers=r.headers)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "expired"
    assert fake.refreshes == 0


async def test_replacing_a_token_verifies_the_new_one(client_for, fake) -> None:
    r = await ready(client_for, "meta-replace")
    fresh = "EAAfake-second-system-user-token-000000000"
    fake.add_token(fresh, scopes=("pages_show_list",))
    async with client_for(r.tenant.host) as c:
        res = await c.patch(
            f"{BASE}/credentials/{r.credential_id}", json={"token": fresh}, headers=r.headers
        )
    assert res.status_code == 200
    assert res.json()["scopes"] == ["pages_show_list"]
    assert res.json()["capabilities"]["facebook_publish"] is False


# --- discovery ------------------------------------------------------------------------------ #


async def test_discovery_finds_everything_and_links_nothing(client_for, fake) -> None:
    """A token reaches every client an agency ever worked for. Which of those it manages here
    is a decision a person makes, so a found asset arrives switched off and unlinked."""
    t = await make_tenant("meta-discover")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        credential = await add_credential(c, headers)
        res = await c.post(f"{BASE}/credentials/{credential['id']}/discover", headers=headers)
        assert res.json() == {"found": 5, "created": 5, "warnings": []}
        active = (await c.get(f"{BASE}/assets", headers=headers)).json()["items"]
        everything = (
            await c.get(f"{BASE}/assets", params={"active_only": False}, headers=headers)
        ).json()["items"]
    assert active == []
    by_meta = {asset["meta_id"]: asset for asset in everything}
    assert set(by_meta) == {PAGE_OWN, PAGE_CLIENT, IG_CLIENT, AD_ACCOUNT_CLIENT, "555000222"}
    assert all(asset["company_id"] is None for asset in everything)
    assert by_meta[PAGE_CLIENT]["relation"] == "client"
    assert by_meta[PAGE_OWN]["relation"] == "owned"
    assert "CREATE_CONTENT" in by_meta[PAGE_CLIENT]["tasks"]
    # The Instagram account knows the Page it publishes through, as *our* id for it.
    assert by_meta[IG_CLIENT]["kind"] == "instagram"
    assert by_meta[IG_CLIENT]["linked_page_id"] == by_meta[PAGE_CLIENT]["id"]
    assert by_meta[IG_CLIENT]["username"] == "novafietsen"
    assert by_meta[AD_ACCOUNT_CLIENT]["currency"] == "EUR"
    assert by_meta[AD_ACCOUNT_CLIENT]["dsa_payor"] == "Nova Fietsen B.V."


async def test_discovering_twice_makes_nothing_twice(client_for, fake) -> None:
    r = await ready(client_for, "meta-twice")
    async with client_for(r.tenant.host) as c:
        res = await c.post(f"{BASE}/credentials/{r.credential_id}/discover", headers=r.headers)
        assets = (
            await c.get(f"{BASE}/assets", params={"active_only": False}, headers=r.headers)
        ).json()["items"]
    assert res.json()["created"] == 0
    assert len(assets) == 5
    # And what a person decided survived what Meta said.
    linked = next(a for a in assets if a["meta_id"] == PAGE_CLIENT)
    assert linked["company_id"] == r.company_id
    assert linked["active"] is True


async def test_a_list_is_read_to_its_end_and_never_follows_a_leaked_token(client_for, fake) -> None:
    """Meta's ``next`` link echoes the token in its query string. The link is data from a
    response, so its host and its credentials are ours, not its."""
    for index in range(7):
        page_id = f"20000000000000{index}"
        fake.pages[page_id] = {
            "id": page_id,
            "name": f"Pagina {index}",
            "relation": "client",
            "tasks": ["CREATE_CONTENT"],
        }
    fake.page_size = 3
    t = await make_tenant("meta-paging")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        credential = await add_credential(c, headers)
        res = await c.post(f"{BASE}/credentials/{credential['id']}/discover", headers=headers)
    assert res.json()["found"] == 5 + 7
    assert all(call.params.get("access_token") != "LEAKED" for call in fake.calls)
    assert all(call.token == SYSTEM_TOKEN for call in fake.calls if call.path != "debug_token")


async def test_one_list_refusing_costs_its_own_probe(client_for, fake) -> None:
    """A probe is evidence, never the gate (Cloudflare's rule): without
    ``business_management`` the business's lists are unreadable and the token's own are not."""
    fake.tokens[SYSTEM_TOKEN].scopes = tuple(
        s for s in fake.tokens[SYSTEM_TOKEN].scopes if s != "business_management"
    )
    t = await make_tenant("meta-softfail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await configure(c, headers)
        credential = await add_credential(c, headers)
        res = await c.post(f"{BASE}/credentials/{credential['id']}/discover", headers=headers)
    body = res.json()
    assert res.status_code == 200
    assert body["found"] == 5
    assert "meta.warning.client_pages" in body["warnings"]
    assert "meta.warning.business_ad_accounts" in body["warnings"]


async def test_an_asset_missing_from_a_cut_short_read_is_not_called_gone(client_for, fake) -> None:
    """Absence is a finding only inside a read known to be complete (Timeon's rule)."""
    r = await ready(client_for, "meta-absence")
    fake.fail_next("GET", "client_pages", error(2, "Service temporarily unavailable", status=500))
    fake.fail_next("GET", "client_pages", error(2, "Service temporarily unavailable", status=500))
    fake.fail_next("GET", "client_pages", error(2, "Service temporarily unavailable", status=500))
    del fake.pages[PAGE_OWN]
    async with client_for(r.tenant.host) as c:
        res = await c.post(f"{BASE}/credentials/{r.credential_id}/discover", headers=r.headers)
        asset = (await c.get(f"{BASE}/assets/{r.own_page_id}", headers=r.headers)).json()
    assert res.json()["warnings"] == ["meta.warning.client_pages"]
    assert asset["status"] == "active"

    async with client_for(r.tenant.host) as c:
        await c.post(f"{BASE}/credentials/{r.credential_id}/discover", headers=r.headers)
        asset = (await c.get(f"{BASE}/assets/{r.own_page_id}", headers=r.headers)).json()
    assert asset["status"] == "error"
    assert asset["last_error"] == "meta.error.asset_not_seen"


async def test_removing_a_credential_keeps_the_assets_and_their_clients(client_for, fake) -> None:
    r = await ready(client_for, "meta-remove")
    async with client_for(r.tenant.host) as c:
        res = await c.delete(f"{BASE}/credentials/{r.credential_id}", headers=r.headers)
        assert res.status_code == 204
        asset = (await c.get(f"{BASE}/assets/{r.page_id}", headers=r.headers)).json()
    assert asset["company_id"] == r.company_id
    assert asset["credential_id"] is None
    assert asset["can_publish"] is False
    assert asset["blocked_by"] == "meta.issue.asset_dormant"


# --- linking, isolation, horizon ------------------------------------------------------------- #


async def test_naming_a_client_switches_the_asset_on(client_for, fake) -> None:
    t = await make_tenant("meta-link")
    headers = await auth_cookie(t.user)
    company_id = await company(t.org.id)
    async with client_for(t.host) as c:
        await configure(c, headers)
        credential = await add_credential(c, headers)
        await c.post(f"{BASE}/credentials/{credential['id']}/discover", headers=headers)
        assets = (
            await c.get(f"{BASE}/assets", params={"active_only": False}, headers=headers)
        ).json()["items"]
        page = next(a for a in assets if a["meta_id"] == PAGE_CLIENT)
        res = await c.patch(
            f"{BASE}/assets/{page['id']}", json={"company_id": company_id}, headers=headers
        )
        body = res.json()
        assert body["active"] is True
        assert body["company_name"] == "Nova Fietsen"
        assert body["can_publish"] is True
        # An explicit null detaches; the asset becomes the agency's own and stays on.
        res = await c.patch(
            f"{BASE}/assets/{page['id']}", json={"company_id": None}, headers=headers
        )
        assert res.json()["company_id"] is None
        assert res.json()["active"] is True


async def test_one_tenant_never_sees_anothers_meta(client_for, fake) -> None:
    a = await ready(client_for, "meta-iso-a")
    b = await make_tenant("meta-iso-b")
    headers_b = await auth_cookie(b.user)
    async with client_for(b.host) as c:
        assert (await c.get(f"{BASE}/assets", headers=headers_b)).json()["items"] == []
        assert (await c.get(f"{BASE}/credentials", headers=headers_b)).json() == []
        assert (await c.get(f"{BASE}/settings", headers=headers_b)).json()["app_id"] is None
        for path in (
            f"{BASE}/assets/{a.page_id}",
            f"{BASE}/assets/{a.page_id}/published",
        ):
            assert (await c.get(path, headers=headers_b)).status_code == 404
        res = await c.post(f"{BASE}/credentials/{a.credential_id}/verify", headers=headers_b)
        assert res.status_code == 404
        res = await c.patch(f"{BASE}/assets/{a.page_id}", json={"active": False}, headers=headers_b)
        assert res.status_code == 404


async def test_a_client_login_reads_no_assets(client_for, fake) -> None:
    """The permissions are never the ``client`` role's (#266)."""
    r = await ready(client_for, "meta-portal")
    headers = await member(r.tenant, "klant@meta-portal.example.com", role="client")
    async with client_for(r.tenant.host) as c:
        assert (await c.get(f"{BASE}/assets", headers=headers)).status_code == 403
        assert (await c.get(f"{BASE}/posts", headers=headers)).status_code == 403


# --- the MCP sections ----------------------------------------------------------------------- #


async def test_both_sections_are_derived_from_their_routers() -> None:
    """``/mcp/meta-business`` and ``/mcp/meta-ads`` are this integration's tools and nobody
    else's: the first segment they share with core's own ``/api/v1/meta`` must not fold the
    tenant and module routes in, and neither may bleed into the other."""
    from app.core.mcp.sections import build_sections
    from app.core.mcp.server import _tool_index
    from app.main import app

    _, paths = _tool_index(app)
    sections = build_sections(paths)
    social = sections["meta-business"]
    ads = sections["meta-ads"]
    assert social.kind == ads.kind == "module"
    assert social.label_key == "module.meta.label"
    assert ads.label_key == "module.meta_ads.label"
    assert social.tools and ads.tools
    assert not (social.tools & ads.tools)
    for tool in social.tools:
        assert paths[tool].startswith("/api/v1/meta-business/"), (tool, paths[tool])
    for tool in ads.tools:
        assert paths[tool].startswith("/api/v1/meta-ads/"), (tool, paths[tool])
    # The bundle unions them rather than naming any of their tools.
    assert social.tools <= sections["growth"].tools
    assert ads.tools <= sections["growth"].tools


async def test_an_upload_is_not_offered_as_a_tool_and_the_public_media_is_no_tool() -> None:
    """A generated tool sends JSON, so a multipart route can only answer 422, and the
    address Instagram fetches an image from is a capability URL, not something to call."""
    from app.core.mcp.server import _tool_index
    from app.main import app

    _, paths = _tool_index(app)
    ours = {path for path in paths.values() if path.startswith("/api/v1/meta-")}
    assert ours
    assert not any("/media/" in path for path in ours)


# --- the status a working screen reads ------------------------------------------------------ #


async def test_the_status_tells_nothing_connected_from_nothing_linked(client_for, fake) -> None:
    """Three different empty screens, and the planner has to be able to say which it is:
    no connection at all, channels found that nobody linked, and everything in place."""
    t = await make_tenant("meta-status")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        bare = (await c.get(f"{BASE}/status", headers=headers)).json()
    assert bare["connected"] is False
    assert bare["channels_linked"] == bare["channels_unlinked"] == 0
    assert bare["needs_attention"] is False

    r = await ready(client_for, "meta-status-ready")
    async with client_for(r.tenant.host) as c:
        full = (await c.get(f"{BASE}/status", headers=r.headers)).json()
        # Asked without a network call: a status that costs a round trip to Meta is one the
        # layout pays on every page of the section.
        before = len(fake.calls)
        await c.get(f"{BASE}/status", headers=r.headers)
        assert len(fake.calls) == before
    assert full["connected"] is True
    assert full["writes_enabled"] is True
    assert full["facebook_scheduler"] == "schakl"
    assert full["channels_linked"] >= 2
    assert full["ad_accounts_linked"] >= 1


async def test_the_status_is_readable_by_a_member_and_carries_no_secret(client_for, fake) -> None:
    r = await ready(client_for, "meta-status-member")
    headers = await member(r.tenant, "lid@meta-status.example.com")
    async with client_for(r.tenant.host) as c:
        res = await c.get(f"{BASE}/status", headers=headers)
        settings = await c.get(f"{BASE}/settings", headers=headers)
    assert res.status_code == 200
    assert set(res.json()) == {
        "connected",
        "writes_enabled",
        "facebook_scheduler",
        "channels_linked",
        "channels_unlinked",
        "ad_accounts_linked",
        "ad_accounts_unlinked",
        "needs_attention",
    }
    # The settings themselves stay an admin's.
    assert settings.status_code == 403


async def test_a_dead_token_is_what_needs_attention(client_for, fake) -> None:
    r = await ready(client_for, "meta-status-dead")
    async with client_for(r.tenant.host) as c:
        await c.patch(
            f"{BASE}/credentials/{r.credential_id}",
            json={"token": "EAAnot-a-token-that-meta-knows-0000000"},
            headers=r.headers,
        )
        status = (await c.get(f"{BASE}/status", headers=r.headers)).json()
    assert status["needs_attention"] is True


# --- live reads ----------------------------------------------------------------------------- #


async def test_published_posts_are_read_as_the_page(client_for, fake) -> None:
    r = await ready(client_for, "meta-published")
    fake.posts["p1"] = {
        "id": "p1",
        "page_id": PAGE_CLIENT,
        "message": "Nieuwe collectie",
        "is_published": True,
        "created_time": "2026-09-20T09:00:00+0000",
    }
    async with client_for(r.tenant.host) as c:
        res = await c.get(f"{BASE}/assets/{r.page_id}/published", headers=r.headers)
    assert res.status_code == 200
    body = res.json()
    assert body[0]["text"] == "Nieuwe collectie"
    assert body[0]["likes"] == 12
    assert body[0]["published_at"].startswith("2026-09-20T09:00:00")
    assert fake.calls[-1].token == fake.page_token(PAGE_CLIENT)


async def test_a_metric_meta_stopped_serving_costs_its_own_tile(client_for, fake) -> None:
    """Meta removed whole metric families in 2025–26 and its reference went on listing them.
    One unknown metric fails a whole request, so each is asked for alone."""
    del fake.metrics["page_follows"]
    r = await ready(client_for, "meta-insights")
    async with client_for(r.tenant.host) as c:
        res = await c.get(f"{BASE}/assets/{r.page_id}/insights", headers=r.headers)
        ig = await c.get(f"{BASE}/assets/{r.instagram_id}/insights", headers=r.headers)
        ads = await c.get(f"{BASE}/assets/{r.ad_account_id}/insights", headers=r.headers)
    body = res.json()
    assert res.status_code == 200
    assert body["metrics"]["page_media_view"] == 12_400
    assert body["unavailable"] == ["page_follows"]
    assert ig.json()["metrics"]["reach"] == 9_400
    assert ads.status_code == 422


async def test_metas_own_text_never_reaches_the_envelope(client_for, fake) -> None:
    """The envelope's message is an i18n key (§9). What rides ``details`` are identifiers."""
    r = await ready(client_for, "meta-envelope")
    fake.fail_next(
        "GET",
        "published_posts",
        error(200, "(#200) Dit is de eigen zin van Meta", status=403),
    )
    async with client_for(r.tenant.host) as c:
        res = await c.get(f"{BASE}/assets/{r.page_id}/published", headers=r.headers)
    assert res.status_code == 409
    body = res.json()["error"]
    assert body["code"] == "meta_permission"
    assert body["message"] == "errors.meta_permission"
    assert "eigen zin" not in res.text
    assert body["details"]["meta_code"] == 200
    assert body["details"]["meta_trace_id"] == "AbCdEfGhIjK"


async def test_a_rate_limit_says_how_long_to_wait(client_for, fake) -> None:
    r = await ready(client_for, "meta-rate")
    usage = '{"1":[{"type":"pages","call_count":100,"estimated_time_to_regain_access":17}]}'
    for _ in range(3):
        fake.fail_next(
            "GET",
            "published_posts",
            error(
                32,
                "Page request limit reached",
                status=400,
                headers={"x-business-use-case-usage": usage},
            ),
        )
    async with client_for(r.tenant.host) as c:
        res = await c.get(f"{BASE}/assets/{r.page_id}/published", headers=r.headers)
    assert res.status_code == 429
    assert res.json()["error"]["details"]["retry_after_minutes"] == 17
