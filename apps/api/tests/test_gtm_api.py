"""google_tag_manager, driven end to end through the fake transport.

Every request here travels the real OAuth client, the real path builder, the real paging loop, the
real fingerprint handling and the real error classifier — the fake is installed at the transport,
which is the lowest seam there is. So a wrong list key, a dropped ``pageToken`` or a misread
``reason`` fails *here* rather than against a client's live container.

What is asserted, beyond "it works": the four gates in front of every write (permission, kill
switch, OAuth scope, GTM's own validator), that publishing is never implied by anything else, that
tenant isolation and the company horizon hold on both tables, and that the recipe puts
``measurementIdOverride`` on the wire.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.core.crypto import encrypt
from app.db import async_session_maker, set_current_org
from app.integrations.google.models import ConnectionStatus, GoogleConnection, GoogleSettings
from app.integrations.google.oauth import (
    SCOPE_TAG_MANAGER_EDIT,
    SCOPE_TAG_MANAGER_READ,
    SCOPE_TAG_MANAGER_VERSIONS,
    TAG_MANAGER_SCOPES,
)
from app.integrations.google_tag_manager.client import set_transport
from app.integrations.google_tag_manager.models import GtmContainer, GtmConversion
from tests.conftest import add_membership, auth_cookie, make_tenant
from tests.gtm_fake import ACCOUNT, CONTAINER, PUBLIC_ID, FakeTagManager, error

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake() -> FakeTagManager:
    """A Tag Manager that exists only in memory, torn down after each test."""
    stub = FakeTagManager()
    set_transport(stub.transport())
    try:
        yield stub
    finally:
        set_transport(None)


async def _connected(slug: str, *, scopes: tuple[str, ...] = TAG_MANAGER_SCOPES):
    """An org whose owner holds a Google grant carrying ``scopes``."""
    t = await make_tenant(slug)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            GoogleSettings(
                org_id=t.org.id,
                client_id="fake-client-id",
                client_secret_encrypted=encrypt("fake-client-secret"),
            )
        )
        session.add(
            GoogleConnection(
                org_id=t.org.id,
                user_id=t.user.id,
                google_sub="sub-1",
                email="gtm@example.com",
                scopes=list(scopes),
                refresh_token_encrypted=encrypt("1//fake-refresh-token"),
                status=ConnectionStatus.ACTIVE.value,
            )
        )
        await session.commit()
    return t


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


async def _link(client, headers, **body) -> dict:
    res = await client.post("/api/v1/gtm/containers", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# --- settings ------------------------------------------------------------------------------- #


async def test_settings_default_to_writing_in_a_workspace_of_our_own(client_for) -> None:
    """Absence is a stated default, not an empty row: an org that never opens this screen still
    gets the safe answer — schakl's changes in schakl's workspace, not in the client's draft."""
    t = await make_tenant("gtm-settings-default")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.get("/api/v1/gtm/settings", headers=headers)
    assert res.status_code == 200
    assert res.json() == {
        "writes_enabled": True,
        "own_workspace": True,
        "workspace_name": "schakl",
    }


async def test_the_kill_switch_stops_every_write(client_for, fake) -> None:
    t = await _connected("gtm-killswitch")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        off = await c.put("/api/v1/gtm/settings", json={"writes_enabled": False}, headers=headers)
        assert off.status_code == 200
        res = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/tags",
            json={"name": "x", "type": "html", "parameter": []},
            headers=headers,
        )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "gtm_writes_disabled"
    # And nothing reached Google: the switch is checked before the client opens.
    assert not [p for p in fake.paths("POST") if p.endswith("/tags")]


# --- linking -------------------------------------------------------------------------------- #


async def test_a_container_can_be_linked_by_the_gtm_id_on_the_website(client_for, fake) -> None:
    """``GTM-NPGFR9W9`` is what is on the site and in the developer's e-mail; the numeric pair is
    what the API addresses. Accepting only the second makes every link start with somebody
    digging an id out of a URL."""
    t = await _connected("gtm-lookup")
    headers = await auth_cookie(t.user)
    company_id = await _company(t.org.id)
    async with client_for(t.host) as c:
        body = await _link(c, headers, public_id=PUBLIC_ID, company_id=str(company_id))
    assert body["gtm_account_id"] == ACCOUNT
    assert body["gtm_container_id"] == CONTAINER
    assert body["public_id"] == PUBLIC_ID
    assert body["name"] == "breik. test"
    assert body["company_id"] == str(company_id)
    assert body["tag_manager_url"].endswith(f"accounts/{ACCOUNT}/containers/{CONTAINER}/workspaces")
    assert "accounts/containers:lookup" in fake.paths("GET")


async def test_an_unknown_gtm_id_is_a_field_error_not_a_502(client_for, fake) -> None:
    t = await _connected("gtm-lookup-miss")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/gtm/containers", json={"public_id": "GTM-NOPE1234"}, headers=headers
        )
    assert res.status_code == 422
    assert res.json()["error"]["fields"] == {"public_id": "errors.gtm_container_not_found"}


async def test_linking_the_same_container_twice_reattaches_it(client_for, fake) -> None:
    """A second link is somebody putting it on the right client, not an error to shout about —
    and the unique constraint would otherwise surface as a 500."""
    t = await _connected("gtm-relink")
    headers = await auth_cookie(t.user)
    first = await _company(t.org.id, "Eerste")
    second = await _company(t.org.id, "Tweede")
    async with client_for(t.host) as c:
        a = await _link(c, headers, public_id=PUBLIC_ID, company_id=str(first))
        b = await _link(c, headers, public_id=PUBLIC_ID, company_id=str(second))
        listed = await c.get("/api/v1/gtm/containers", headers=headers)
    assert a["id"] == b["id"]
    assert b["company_id"] == str(second)
    assert len(listed.json()) == 1


async def test_unlinking_deactivates_and_touches_nothing_at_google(client_for, fake) -> None:
    """An agency that stops working for a client does not delete the tracking off their site."""
    t = await _connected("gtm-unlink")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.delete(f"/api/v1/gtm/containers/{container['id']}", headers=headers)
        assert res.status_code == 204
        again = await c.get(f"/api/v1/gtm/containers/{container['id']}", headers=headers)
    assert again.json()["active"] is False
    assert not fake.paths("DELETE")


# --- the picker is a search, because the quota says so --------------------------------------- #


def _reseller(fake: FakeTagManager, count: int = 20) -> None:
    """An agency holding many Tag Manager accounts — the shape that broke the first picker.

    The live grant this was written against holds **44**, and Tag Manager's quota is per user per
    minute: "list the accounts, then list each account's containers" is 45 requests and refused on
    the last one. Twenty is enough to prove the arithmetic without making the fake a load test.
    """
    for index in range(count):
        account_id = f"90000000{index:02d}"
        fake.accounts.append(
            {
                "accountId": account_id,
                "name": f"Klant {index:02d}",
                "path": f"accounts/{account_id}",
            }
        )
        fake.containers.append(
            {
                "accountId": account_id,
                "containerId": f"7000{index:02d}",
                "publicId": f"GTM-KLANT{index:02d}",
                "name": f"www.klant{index:02d}.nl",
                "path": f"accounts/{account_id}/containers/7000{index:02d}",
                "usageContext": ["web"],
            }
        )


async def test_the_picker_opens_only_the_accounts_the_search_names(client_for, fake) -> None:
    """The whole point: a search costs one request per *matched* account, not per account.

    Before this, the picker was ``1 + n`` requests where n is however many Tag Manager accounts
    the agency holds — which against a real 44-account grant answered ``RESOURCE_EXHAUSTED``
    rather than a list, so the control that exists to find a container found none.
    """
    t = await _connected("gtm-pick-search")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _reseller(fake)
        res = await c.get(
            "/api/v1/gtm/containers/available", headers=headers, params={"q": "Klant 07"}
        )
    body = res.json()
    assert [row["public_id"] for row in body["containers"]] == ["GTM-KLANT07"]
    assert body["accounts_total"] == 21
    assert body["accounts_read"] == 1
    # One `accounts` call plus one `containers` call. Twenty-one accounts, two requests.
    assert len(fake.paths("GET")) == 2


async def test_a_blank_search_says_how_many_accounts_it_left_unopened(client_for, fake) -> None:
    """A short list that looks complete is the failure §17 exists to prevent.

    ``accounts_read`` of ``accounts_total`` plus the warning is what turns "my client is not
    here" into "type their name" — the same rule #373 applied to a report's long tail.
    """
    t = await _connected("gtm-pick-blank")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _reseller(fake)
        res = await c.get("/api/v1/gtm/containers/available", headers=headers)
    body = res.json()
    assert body["accounts_total"] == 21
    assert body["accounts_read"] == 8
    assert body["warnings"] == ["gtm.warning.narrow_search"]
    assert len(body["containers"]) == 8


async def test_a_gtm_id_resolves_in_one_request_instead_of_a_sweep(client_for, fake) -> None:
    """Somebody pasting the id off a client's website should never cost an account sweep."""
    t = await _connected("gtm-pick-id")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _reseller(fake)
        fake.calls.clear()
        res = await c.get(
            "/api/v1/gtm/containers/available", headers=headers, params={"q": "gtm-klant13"}
        )
    body = res.json()
    assert [row["public_id"] for row in body["containers"]] == ["GTM-KLANT13"]
    # The account name comes off the account list, not off the container: `containers:lookup`
    # answers a container and no account, and a blank heading is worse than a numeric one.
    assert body["containers"][0]["account_name"] == "Klant 13"
    assert fake.paths("GET") == ["accounts", "accounts/containers:lookup"]


async def test_an_unknown_id_in_the_picker_is_an_empty_result_not_a_refusal(
    client_for, fake
) -> None:
    """On a search box "no match" is an ordinary outcome; an error envelope is a wrong sentence
    about it. The *link* route still 422s on the same id, because there it is an instruction."""
    t = await _connected("gtm-pick-miss")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.get(
            "/api/v1/gtm/containers/available", headers=headers, params={"q": "GTM-NOTHERE"}
        )
    assert res.status_code == 200
    assert res.json()["containers"] == []


async def test_a_quota_refusal_keeps_what_was_read_and_says_the_reading_stopped(
    client_for, fake
) -> None:
    """A rate is not a verdict (CLAUDE.md §10, learned from Cloudflare's probes).

    Emptying the picker because the sixth account refused would hide the five that answered, and
    "narrow your search" is something the user can act on where a 429 envelope is not.
    """
    t = await _connected("gtm-pick-quota")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _reseller(fake, count=3)
        fake.fail(
            "accounts/9000000001/containers",
            error(429, reason="RESOURCE_EXHAUSTED", message="Quota exceeded"),
        )
        res = await c.get(
            "/api/v1/gtm/containers/available", headers=headers, params={"q": "Klant"}
        )
    body = res.json()
    assert res.status_code == 200
    assert body["warnings"] == ["gtm.warning.quota"]
    assert [row["public_id"] for row in body["containers"]] == ["GTM-KLANT00"]


# --- isolation ------------------------------------------------------------------------------ #


async def test_a_container_is_invisible_to_another_tenant(client_for, fake) -> None:
    one = await _connected("gtm-iso-a")
    two = await _connected("gtm-iso-b")
    headers_one = await auth_cookie(one.user)
    headers_two = await auth_cookie(two.user)
    async with client_for(one.host) as c:
        container = await _link(c, headers_one, public_id=PUBLIC_ID)
    async with client_for(two.host) as c:
        listed = await c.get("/api/v1/gtm/containers", headers=headers_two)
        fetched = await c.get(f"/api/v1/gtm/containers/{container['id']}", headers=headers_two)
    assert listed.json() == []
    # 404, not 403: the difference between two status codes would reveal that it exists (§15).
    assert fetched.status_code == 404


async def test_a_company_scoped_member_sees_only_their_clients_containers(client_for, fake) -> None:
    """The parameterless list is exactly the shape #285's sweep hunts for: it returns ``name``,
    which for a client container *is* the client's name."""
    t = await _connected("gtm-horizon")
    owner_headers = await auth_cookie(t.user)
    other = await make_tenant("gtm-horizon-member", email="scoped@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, other.user.id, role="member")
        membership_id = membership.id
        await session.commit()
    member_headers = await auth_cookie(other.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        mine = (
            await c.post("/api/v1/companies", json={"name": "Van mij"}, headers=owner_headers)
        ).json()
        theirs = (
            await c.post("/api/v1/companies", json={"name": "Niet van mij"}, headers=owner_headers)
        ).json()
        await _link(c, owner_headers, public_id=PUBLIC_ID, company_id=theirs["id"])

        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Mijn klanten"}, headers=owner_headers
            )
        ).json()
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/companies",
            json={"company_ids": [mine["id"]]},
            headers=owner_headers,
        )
        await c.put(
            f"/api/v1/companies/groups/{group['id']}/memberships",
            json={"membership_ids": [str(membership_id)]},
            headers=owner_headers,
        )

        listed = await c.get("/api/v1/gtm/containers", headers=member_headers)
        owner_listed = await c.get("/api/v1/gtm/containers", headers=owner_headers)

    assert listed.status_code == 200
    # The container belongs to a client outside the horizon, so it is not there at all …
    assert listed.json() == []
    # … and the control run proves "nothing leaked" does not quietly mean "nothing matched".
    assert len(owner_listed.json()) == 1


# --- observation ----------------------------------------------------------------------------- #


async def test_verify_records_the_live_version_and_clears_a_previous_error(
    client_for, fake
) -> None:
    """Success **clears** the flag. A flag that only ever turns on leaves a red line on a row
    nothing is wrong with, through every sync that works afterwards."""
    t = await _connected("gtm-verify")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)

        fake.fail("versions:live", error(403, reason="rateLimitExceeded"))
        bad = await c.post(f"/api/v1/gtm/containers/{container['id']}/verify", headers=headers)
        assert bad.status_code == 200
        assert bad.json()["status"] == "error"

        fake.failures.clear()
        fake.versions.append(
            {
                "accountId": ACCOUNT,
                "containerId": CONTAINER,
                "containerVersionId": "7",
                "name": "Live",
                "path": f"accounts/{ACCOUNT}/containers/{CONTAINER}/versions/7",
                "tag": [{"tagId": "1"}, {"tagId": "2"}],
                "trigger": [{"triggerId": "1"}],
                "variable": [],
            }
        )
        fake.live_version_id = "7"
        good = await c.post(f"/api/v1/gtm/containers/{container['id']}/verify", headers=headers)

    body = good.json()
    assert body["status"] == "active"
    assert body["last_error"] is None
    assert body["live_version_id"] == "7"
    assert (body["tag_count"], body["trigger_count"]) == (2, 1)


async def test_a_container_that_was_never_published_is_not_an_error(client_for, fake) -> None:
    """GTM answers 404 for ``versions:live`` on a container nobody has published. Ordinary —
    somebody made it last week — and emphatically not a broken row."""
    t = await _connected("gtm-neverpub")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.post(f"/api/v1/gtm/containers/{container['id']}/verify", headers=headers)
    body = res.json()
    assert body["status"] == "active"
    assert body["live_version_id"] is None


# --- reading a workspace ---------------------------------------------------------------------- #


async def test_reading_tags_resolves_a_workspace_without_creating_one(client_for, fake) -> None:
    """A read must never bring a workspace into existence as a side effect of opening a screen."""
    t = await _connected("gtm-readtags")
    headers = await auth_cookie(t.user)
    fake.tags["1"] = [
        {"tagId": "5", "name": "GA4 config", "type": "googtag", "firingTriggerId": ["2147479553"]}
    ]
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.get(f"/api/v1/gtm/containers/{container['id']}/tags", headers=headers)
    assert res.status_code == 200
    assert [row["name"] for row in res.json()] == ["GA4 config"]
    assert not [p for p in fake.paths("POST") if p.endswith("/workspaces")]


async def test_the_whole_workspace_costs_one_resolution_not_four(client_for, fake) -> None:
    """The number this endpoint exists for, written down.

    Asking for tags, triggers, variables and the staged count separately is **eight** Google
    requests, because each of the four lists the container's workspaces to find out which one it
    means. This is five, and the difference is not only latency: Tag Manager's quota is counted
    per user per minute, so the count is how many times somebody may open the page.

    Invisible in the JSON — the four separate calls answer exactly the same rows — which is why
    it is pinned by a number rather than left to review (docs/PERFORMANCE.md).
    """
    t = await _connected("gtm-one-resolve")
    headers = await auth_cookie(t.user)
    fake.tags["1"] = [{"tagId": "5", "name": "GA4 config", "type": "googtag"}]
    fake.triggers["1"] = [{"triggerId": "6", "name": "Alle pagina's", "type": "pageview"}]
    fake.variables["1"] = [{"variableId": "7", "name": "Klant-id", "type": "c"}]
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        fake.calls.clear()
        res = await c.get(f"/api/v1/gtm/containers/{container['id']}/workspace", headers=headers)
    body = res.json()
    assert res.status_code == 200, res.text
    assert [row["name"] for row in body["tags"]] == ["GA4 config"]
    assert [row["name"] for row in body["triggers"]] == ["Alle pagina's"]
    assert [row["name"] for row in body["variables"]] == ["Klant-id"]
    # The staged count rides along rather than costing its own workspace resolution — which is
    # the whole point, and the reason the overview tile can stop being a fifth round trip.
    assert body["status"]["workspace_id"] == "1"
    assert body["workspace_id"] == "1"

    paths = fake.paths("GET")
    assert sum(1 for p in paths if p.endswith("/workspaces")) == 1
    assert len(paths) == 5, paths
    # And still never creates one: a read that mints a workspace puts our name in front of the
    # client because somebody opened a screen.
    assert not [p for p in fake.paths("POST") if p.endswith("/workspaces")]


async def test_a_container_with_no_workspace_reads_empty_rather_than_erroring(
    client_for, fake
) -> None:
    """An empty page, never a 502 — and never a workspace brought into existence to fill it."""
    t = await _connected("gtm-no-workspace")
    headers = await auth_cookie(t.user)
    fake.workspaces.clear()
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.get(f"/api/v1/gtm/containers/{container['id']}/workspace", headers=headers)
    assert res.status_code == 200
    assert res.json() == {
        "workspace_id": "",
        "status": None,
        "tags": [],
        "triggers": [],
        "variables": [],
    }
    assert not [p for p in fake.paths("POST") if p.endswith("/workspaces")]


async def test_a_list_follows_every_page(client_for, fake) -> None:
    """A client that ignores ``nextPageToken`` returns a prefix, and no assertion about the first
    page would ever notice."""
    t = await _connected("gtm-paging")
    headers = await auth_cookie(t.user)
    fake.tags["1"] = [{"tagId": str(i), "name": f"Tag {i}", "type": "html"} for i in range(1, 8)]
    fake.pages = 3
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.get(f"/api/v1/gtm/containers/{container['id']}/tags", headers=headers)
    assert len(res.json()) == 7


# --- writing ------------------------------------------------------------------------------------ #


async def test_a_tag_is_written_into_schakls_own_workspace(client_for, fake) -> None:
    """A workspace is a *shared* draft: writing into "Default Workspace" puts our half-finished
    change in front of whoever else is mid-edit, and their next Publish ships it."""
    t = await _connected("gtm-ownws")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/tags",
            json={
                "name": "Custom HTML",
                "type": "html",
                "parameter": [{"type": "template", "key": "html", "value": "<script></script>"}],
            },
            headers=headers,
        )
    assert res.status_code == 201, res.text
    made = [w for w in fake.workspaces if w["name"] == "schakl"]
    assert made, "a workspace of our own should have been created"
    assert fake.tags[made[0]["workspaceId"]][-1]["name"] == "Custom HTML"


async def test_our_first_write_does_not_hide_the_clients_existing_tags(client_for, fake) -> None:
    """A GTM workspace is a **copy of the live container**, not an empty slate.

    Worth pinning because the screen it protects is the one somebody opens next: the read
    resolves to the workspace schakl writes in, so if a fresh workspace started empty, the
    client's existing tags would appear to have vanished the moment we added one.
    """
    t = await _connected("gtm-wscopy")
    headers = await auth_cookie(t.user)
    fake.versions.append(
        {
            "accountId": ACCOUNT,
            "containerId": CONTAINER,
            "containerVersionId": "9",
            "name": "Live",
            "path": f"accounts/{ACCOUNT}/containers/{CONTAINER}/versions/9",
            "tag": [{"tagId": "1", "name": "Google tag", "type": "googtag"}],
            "trigger": [],
            "variable": [],
        }
    )
    fake.live_version_id = "9"
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        await c.post(
            f"/api/v1/gtm/containers/{container['id']}/tags",
            json={"name": "Van ons", "type": "html", "parameter": []},
            headers=headers,
        )
        listed = await c.get(f"/api/v1/gtm/containers/{container['id']}/tags", headers=headers)
    assert [row["name"] for row in listed.json()] == ["Google tag", "Van ons"]


async def test_an_update_carries_the_fingerprint_and_a_stale_one_is_a_conflict(
    client_for, fake
) -> None:
    """That is the whole of GTM's optimistic concurrency, and the difference between "your change
    landed" and "your change silently replaced somebody's"."""
    t = await _connected("gtm-fingerprint")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        created = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/tags",
            json={"name": "Eerst", "type": "html", "parameter": []},
            headers=headers,
        )
        tag_id = created.json()["tag_id"]
        ok = await c.patch(
            f"/api/v1/gtm/containers/{container['id']}/tags/{tag_id}",
            json={"name": "Daarna"},
            headers=headers,
        )
        assert ok.status_code == 200
        assert ok.json()["name"] == "Daarna"

        # Somebody edits it in Tag Manager: the stored fingerprint moves on under us.
        workspace = next(w["workspaceId"] for w in fake.workspaces if w["name"] == "schakl")
        fake.tags[workspace][0]["fingerprint"] = "changed-elsewhere"
        fake.fail("/tags/", error(409, message="fingerprint mismatch"))
        clash = await c.patch(
            f"/api/v1/gtm/containers/{container['id']}/tags/{tag_id}",
            json={"name": "Derde"},
            headers=headers,
        )
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "gtm_conflict"


async def test_an_update_merges_rather_than_replacing(client_for, fake) -> None:
    """GTM's update is a whole-object PUT: sending only the changed field blanks everything else,
    which here would silently unhook a tag from its trigger."""
    t = await _connected("gtm-merge")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        created = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/tags",
            json={"name": "Met trigger", "type": "html", "firing_trigger_id": ["42"]},
            headers=headers,
        )
        tag_id = created.json()["tag_id"]
        updated = await c.patch(
            f"/api/v1/gtm/containers/{container['id']}/tags/{tag_id}",
            json={"notes": "aangepast"},
            headers=headers,
        )
    assert updated.json()["firing_trigger_id"] == ["42"]


async def test_a_trigger_is_written_from_the_recipes_vocabulary(client_for, fake) -> None:
    t = await _connected("gtm-trigger")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/triggers",
            json={"name": "Contactformulier", "kind": "form_submit", "url_contains": "/contact"},
            headers=headers,
        )
    assert res.status_code == 201, res.text
    workspace = next(w["workspaceId"] for w in fake.workspaces if w["name"] == "schakl")
    stored = fake.triggers[workspace][0]
    assert stored["type"] == "formSubmission"
    assert stored["checkValidation"]["value"] == "true"
    # The built-in the filter reads was switched on with it.
    assert "pageUrl" in fake.built_ins.get(workspace, [])


# --- the conversion recipe----------------------------------------------------------------------- #


async def test_a_ga4_conversion_creates_a_trigger_a_tag_and_the_record_of_both(
    client_for, fake
) -> None:
    t = await _connected("gtm-conv")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/conversions",
            json={
                "name": "Offerte aangevraagd",
                "kind": "ga4_event",
                "event_name": "generate_lead",
                "measurement_id": "G-ABC123",
                "trigger": {
                    "name": "unused",
                    "kind": "form_submit",
                    "url_contains": "/offerte",
                },
            },
            headers=headers,
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "draft"
    assert body["trigger_id"] and body["tag_id"]

    workspace = next(w["workspaceId"] for w in fake.workspaces if w["name"] == "schakl")
    tag = fake.tags[workspace][0]
    assert tag["type"] == "gaawe"
    keys = {p["key"]: p["value"] for p in tag["parameter"]}
    # The one that is silently wrong if it is spelled ``measurementId``.
    assert keys["measurementIdOverride"] == "G-ABC123"
    assert keys["eventName"] == "generate_lead"
    # And it fires on the trigger that was just made, not on nothing.
    assert tag["firingTriggerId"] == [fake.triggers[workspace][0]["triggerId"]]


async def test_setting_up_the_same_conversion_twice_is_a_409_not_a_500(client_for, fake) -> None:
    """A uniqueness the database guarantees is a refusal the service owes — and refusing before
    any Google call is what stops the second attempt leaving an orphan tag behind."""
    t = await _connected("gtm-conv-dup")
    headers = await auth_cookie(t.user)
    payload = {
        "name": "Offerte aangevraagd",
        "kind": "ga4_event",
        "event_name": "generate_lead",
        "measurement_id": "G-ABC123",
        "trigger": {"name": "x", "kind": "page_view"},
    }
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        first = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/conversions", json=payload, headers=headers
        )
        assert first.status_code == 201
        before = len(fake.paths("POST"))
        # Same name, different capitalisation and spacing: one conversion, not two.
        payload["name"] = "offerte  Aangevraagd"
        second = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/conversions", json=payload, headers=headers
        )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "gtm_conversion_exists"
    assert len(fake.paths("POST")) == before, "the refusal must cost no Google calls"


async def test_a_conversion_refuses_to_invent_a_measurement_id(client_for, fake) -> None:
    t = await _connected("gtm-conv-noid")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/conversions",
            json={
                "name": "Zonder id",
                "kind": "ga4_event",
                "event_name": "generate_lead",
                "trigger": {"name": "x", "kind": "page_view"},
            },
            headers=headers,
        )
    assert res.status_code == 422
    assert res.json()["error"]["fields"] == {"measurement_id": "errors.gtm_measurement_id_required"}


# --- versioning and publishing------------------------------------------------------------------- #


async def test_an_empty_workspace_makes_no_version_and_says_so(client_for, fake) -> None:
    """GTM answers 200 with no version at all. A caller that read that as success would then try
    to publish a version that does not exist."""
    t = await _connected("gtm-emptyversion")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/versions", json={}, headers=headers
        )
    assert res.status_code == 201
    assert res.json() == {
        "version_id": None,
        "name": "",
        "compiler_error": False,
        "empty": True,
        "sync_conflicts": 0,
    }


async def test_publishing_makes_the_version_live_and_the_draft_conversions_with_it(
    client_for, fake
) -> None:
    t = await _connected("gtm-publish")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        await c.post(
            f"/api/v1/gtm/containers/{container['id']}/conversions",
            json={
                "name": "Offerte",
                "kind": "ga4_event",
                "event_name": "generate_lead",
                "measurement_id": "G-ABC123",
                "trigger": {"name": "x", "kind": "form_submit"},
            },
            headers=headers,
        )
        version = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/versions",
            json={"name": "Offerte-conversie"},
            headers=headers,
        )
        version_id = version.json()["version_id"]
        assert version_id

        published = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/versions/{version_id}/publish",
            headers=headers,
        )
        assert published.status_code == 200
        conversions = await c.get(
            f"/api/v1/gtm/containers/{container['id']}/conversions", headers=headers
        )
        row = await c.get(f"/api/v1/gtm/containers/{container['id']}", headers=headers)

    assert published.json()["live_version_id"] == version_id
    assert conversions.json()[0]["status"] == "live"
    assert conversions.json()[0]["published_version_id"] == version_id
    # The row is updated in the same request, so the screen never says the old version is live.
    assert row.json()["live_version_id"] == version_id


async def test_a_grant_without_the_publish_scope_is_refused_before_google_is_asked(
    client_for, fake
) -> None:
    """Google's own refusal says "permission denied"; what actually happened is that this
    connection predates the org asking for the publish scope. One reconnect fixes it."""
    t = await _connected(
        "gtm-noscope",
        scopes=(SCOPE_TAG_MANAGER_READ, SCOPE_TAG_MANAGER_EDIT, SCOPE_TAG_MANAGER_VERSIONS),
    )
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        res = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/versions/9/publish", headers=headers
        )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "gtm_not_configured"
    assert not [p for p in fake.paths("POST") if p.endswith(":publish")]


async def test_a_grant_with_no_tag_manager_scope_at_all_cannot_even_link(client_for, fake) -> None:
    t = await _connected("gtm-noscope-read", scopes=())
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post("/api/v1/gtm/containers", json={"public_id": PUBLIC_ID}, headers=headers)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "gtm_not_configured"


async def test_publishing_needs_its_own_permission(client_for, fake) -> None:
    """The whole reason the write half is split in two: an agent may stage and version, and only
    a key holding ``version.publish`` may make it live."""
    t = await _connected("gtm-pubperm")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, owner_headers, public_id=PUBLIC_ID)

    other = await make_tenant("gtm-pubperm-member", email="stager@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, other.user.id, role="member")
        role_id = (
            await session.execute(
                text("SELECT id FROM roles WHERE org_id = :o AND key = 'member'"),
                {"o": str(t.org.id)},
            )
        ).scalar_one()
        for permission in ("google_tag_manager.tag.write", "google_tag_manager.container.read"):
            await session.execute(
                text(
                    "INSERT INTO role_permissions (id, org_id, role_id, permission, created_at,"
                    " updated_at) VALUES (gen_random_uuid(), :o, :r, :p, now(), now())"
                    " ON CONFLICT DO NOTHING"
                ),
                {"o": str(t.org.id), "r": str(role_id), "p": permission},
            )
        await session.commit()

    member_headers = await auth_cookie(other.user, org_id=t.org.id)
    async with client_for(t.host) as c:
        staged = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/tags",
            json={"name": "Van de agent", "type": "html", "parameter": []},
            headers=member_headers,
        )
        refused = await c.post(
            f"/api/v1/gtm/containers/{container['id']}/versions/1/publish", headers=member_headers
        )
    assert staged.status_code == 201
    assert refused.status_code == 403


# --- the trail----------------------------------------------------------------------------------- #


async def test_every_write_leaves_a_line_in_the_activity_trail(client_for, fake) -> None:
    """ "Who put this tag on the client's site" is asked months later, and Google's own history
    says only that a tag exists."""
    t = await _connected("gtm-trail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        container = await _link(c, headers, public_id=PUBLIC_ID)
        await c.post(
            f"/api/v1/gtm/containers/{container['id']}/tags",
            json={"name": "Gelogd", "type": "html", "parameter": []},
            headers=headers,
        )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        actions = (
            (
                await session.execute(
                    text(
                        "SELECT action FROM activity_log WHERE entity_type = 'gtm_container'"
                        " ORDER BY created_at"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "created" in actions
    assert "gtm.tag_created" in actions


# --- the seam the panel reads-------------------------------------------------------------------- #


async def test_the_company_panel_counts_conversions_in_one_query(client_for, fake) -> None:
    """One grouped read, not one per container: a panel that is two queries for this client and
    thirty for the next is the shape a functional test cannot see."""
    from app.core.jobs import system_context
    from app.integrations.google_tag_manager.service import GtmService

    t = await _connected("gtm-panelcount")
    headers = await auth_cookie(t.user)
    company_id = await _company(t.org.id)
    async with client_for(t.host) as c:
        await _link(c, headers, public_id=PUBLIC_ID, company_id=str(company_id))

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        containers = (await session.scalars(select(GtmContainer))).all()
        session.add(
            GtmConversion(
                org_id=t.org.id,
                container_id=containers[0].id,
                name="Offerte",
                key="offerte",
                kind="ga4_event",
                status="live",
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = system_context(t.org, session)
        counts = await GtmService(ctx).conversion_counts([c.id for c in containers])
    assert counts[containers[0].id] == (1, 1)
