"""Linking a found monitor to what it watches, and managing groups (#321, docs/UPTIME.md §7/§9).

The failure these cover is the one an agency meets on day one: adopting an Uptime Kuma that has
been running for years produced a hundred mirrored monitors attached to nothing, so the website
panel this module exists to draw was empty for exactly the monitors it was built for.
"""

from __future__ import annotations

import pytest

from app.integrations.uptime import client as kuma_client
from app.integrations.uptime import matching
from tests.conftest import auth_cookie, make_tenant
from tests.uptime_fake import FakeKuma


@pytest.fixture
def kuma(monkeypatch) -> FakeKuma:
    fake = FakeKuma()
    monkeypatch.setattr(kuma_client, "_connector", fake.connector)
    return fake


async def _connected(c, headers) -> str:
    inst = (
        await c.post(
            "/api/v1/uptime/instances",
            json={"name": "Kuma", "mode": "managed", "base_url": "https://kuma.example.nl"},
            headers=headers,
        )
    ).json()
    await c.post(
        f"/api/v1/uptime/instances/{inst['id']}/enrol",
        json={"username": "admin", "password": "secret"},
        headers=headers,
    )
    return inst["id"]


async def _company(c, headers, name: str) -> str:
    return (await c.post("/api/v1/companies", json={"name": name}, headers=headers)).json()["id"]


async def _domain(c, headers, name: str, company: str) -> str:
    return (
        await c.post(
            "/api/v1/domains", json={"name": name, "company_id": company}, headers=headers
        )
    ).json()["id"]


async def _website(c, headers, domain: str, *, root: bool = True) -> str:
    return (
        await c.post(
            "/api/v1/websites", json={"domain_id": domain, "root": root}, headers=headers
        )
    ).json()["id"]


# ------------------------------------------------------------------ the matcher (unit)


def test_a_host_is_taken_out_of_whatever_the_type_stores() -> None:
    """A pre-check normalises the way the write does (§17), or it matches nothing at all."""
    assert matching.host_of("https://WWW.Klant.nl:8443/health?x=1") == "www.klant.nl"
    assert matching.host_of("klant.nl.") == "klant.nl"
    assert matching.host_of("user:pw@vpn.klant.nl") == "vpn.klant.nl"
    assert matching.host_of("[2001:db8::1]:443") == "2001:db8::1"
    assert matching.host_of(None) is None
    assert matching.host_of("   ") is None


def test_the_specific_anchor_wins_and_a_tie_is_left_alone() -> None:
    """A website ends the search; two possible zones is an answer for a person, not a guess."""
    import uuid

    web = uuid.uuid4()
    apex = uuid.uuid4()
    sub = uuid.uuid4()
    websites = {"www.klant.nl": [matching.LinkCandidate("website", web, "www.klant.nl", None)]}
    domains = {
        "klant.nl": [matching.LinkCandidate("domain", apex, "klant.nl", None)],
        "shop.klant.nl": [matching.LinkCandidate("domain", sub, "shop.klant.nl", None)],
    }

    # The website is a hostname somebody recorded; the domain under it is not a better answer.
    exact = matching.candidates_for("www.klant.nl", websites, domains)
    assert [c.entity_type for c in exact] == ["website"]

    # A host inside a zone we hold, with two zones that contain it: both, and no default.
    both = matching.candidates_for("a.shop.klant.nl", websites, domains)
    assert {c.entity_id for c in both} == {apex, sub}

    assert matching.candidates_for("iemand-anders.nl", websites, domains) == []


# ----------------------------------------------------------------------- matching


async def test_a_sync_proposes_links_and_links_nothing(client_for, kuma) -> None:
    """Gate 1's rule survives: the read never writes — not to Kuma, and not to the record."""
    t = await make_tenant("uptime-match")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant")
        domain = await _domain(c, headers, "klant.nl", company)
        website = await _website(c, headers, domain)
        instance_id = await _connected(c, headers)

        kuma.add(name="Klant website", url="https://klant.nl/")
        kuma.add(name="Klant VPN", type="ping", url=None, hostname="vpn.klant.nl")
        kuma.add(name="Iemand anders", url="https://onbekend.nl")

        report = (
            await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        ).json()
        assert report["ok"] and report["created"] == 3
        # Two proposals and one host we hold nothing for. Nothing is linked yet.
        assert (report["matched"], report["ambiguous"], report["unmatched"]) == (2, 0, 1)

        listed = (
            await c.get("/api/v1/uptime/monitors?link_status=proposed", headers=headers)
        ).json()
        assert listed["total"] == 2
        by_name = {m["name"]: m for m in listed["items"]}
        assert by_name["Klant website"]["link_candidates"][0]["entity_id"] == website
        assert by_name["Klant website"]["link_candidates"][0]["entity_type"] == "website"
        # The VPN endpoint is not a website and never will be; its zone is the anchor.
        assert by_name["Klant VPN"]["link_candidates"][0]["entity_type"] == "domain"
        assert all(m["company_id"] is None for m in listed["items"]), "a sync linked something"
        assert all(m["link_checked_at"] for m in listed["items"])


async def test_the_match_is_one_query_however_many_monitors(client_for, kuma, count_queries):
    """The shape docs/PERFORMANCE.md exists to prevent: correct at three, linear at three hundred.

    Pinned by counting the reads of `domains`, because a per-monitor lookup is invisible in the
    JSON — the same report comes back either way.
    """
    t = await make_tenant("uptime-match-budget")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant")
        for i in range(6):
            await _domain(c, headers, f"klant-{i}.nl", company)
        instance_id = await _connected(c, headers)
        for i in range(6):
            kuma.add(name=f"m{i}", url=f"https://klant-{i}.nl")

        with count_queries() as counter:
            await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        reads = [s for s in counter.statements if "FROM domains" in s]
        assert len(reads) == 1, f"one batched read, got {len(reads)}"


# ------------------------------------------------------------------------ linking


async def test_confirming_a_link_derives_the_client_and_never_calls_kuma(client_for, kuma):
    """Which client a monitor belongs to is ours alone; Uptime Kuma has no field for it.

    Two assertions in one, because they are the same fact: the link is local, so it must both
    set `company_id` itself and cost no outbound call.
    """
    t = await make_tenant("uptime-link")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant")
        domain = await _domain(c, headers, "klant.nl", company)
        website = await _website(c, headers, domain)
        instance_id = await _connected(c, headers)
        kuma.add(name="Klant website", url="https://klant.nl/")
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        monitor = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()["items"][0]

        sockets = len(kuma.connections)
        linked = await c.post(
            f"/api/v1/uptime/monitors/{monitor['id']}/link",
            json={"entity_type": "website", "entity_id": website},
            headers=headers,
        )
        assert linked.status_code == 200, linked.text
        body = linked.json()
        assert body["website_id"] == website
        assert body["company_id"] == company, "the horizon and the record would disagree"
        assert body["link_status"] == "linked"
        assert len(kuma.connections) == sockets, "a local link dialled out"

        # And the website panel — the surface this module exists to draw — now has it.
        panel = (
            await c.get(f"/api/v1/uptime/monitors?website_id={website}", headers=headers)
        ).json()
        assert panel["total"] == 1

        # Detaching is an explicit null on both, and takes the client with it.
        cleared = await c.post(
            f"/api/v1/uptime/monitors/{monitor['id']}/link",
            json={"entity_type": None, "entity_id": None},
            headers=headers,
        )
        assert cleared.json()["company_id"] is None
        assert cleared.json()["website_id"] is None


async def test_an_anchor_from_another_tenant_is_a_404(client_for, kuma) -> None:
    """Golden Rule 1, on the one route that takes another module's id from the caller."""
    other = await make_tenant("uptime-link-other")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as c:
        company = await _company(c, other_headers, "Andermans klant")
        domain = await _domain(c, other_headers, "andermans.nl", company)
        foreign_website = await _website(c, other_headers, domain)

    t = await make_tenant("uptime-link-mine")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        kuma.add(name="Iets", url="https://iets.nl")
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        monitor = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()["items"][0]

        refused = await c.post(
            f"/api/v1/uptime/monitors/{monitor['id']}/link",
            json={"entity_type": "website", "entity_id": foreign_website},
            headers=headers,
        )
        assert refused.status_code == 404, refused.text


async def test_apply_links_takes_the_obvious_ones_and_leaves_the_rest(client_for, kuma) -> None:
    """The button pressed once after adopting an instance. It never resolves an ambiguity."""
    t = await make_tenant("uptime-apply")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant")
        domain = await _domain(c, headers, "klant.nl", company)
        website = await _website(c, headers, domain)
        # A second record whose zone also contains `a.shop.klant.nl` — two defensible anchors.
        await _domain(c, headers, "shop.klant.nl", company)
        instance_id = await _connected(c, headers)
        kuma.add(name="Website", url="https://klant.nl/")
        kuma.add(name="Dubbelzinnig", type="ping", url=None, hostname="a.shop.klant.nl")
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)

        applied = await c.post(
            f"/api/v1/uptime/instances/{instance_id}/links/apply", headers=headers
        )
        assert applied.json() == {"linked": 1, "skipped": 1}

        listed = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()
        rows = {m["name"]: m for m in listed["items"]}
        assert rows["Website"]["website_id"] == website
        assert rows["Website"]["company_id"] == company
        assert rows["Dubbelzinnig"]["link_status"] == "ambiguous"
        assert rows["Dubbelzinnig"]["company_id"] is None, "an ambiguity was guessed"


# ------------------------------------------------------------------------- groups


async def test_a_group_with_children_refuses_to_be_deleted(client_for, kuma) -> None:
    """docs/UPTIME.md §7. The self-FK is SET NULL, so deleting one would silently un-nest every
    monitor beneath it here while Uptime Kuma keeps the tree exactly as it was."""
    t = await make_tenant("uptime-group-delete")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        group_kuma_id = kuma.add_group("Klant X")
        kuma.add(name="Kind", url="https://klant-x.nl", parent=group_kuma_id)
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)

        groups = (
            await c.get("/api/v1/uptime/monitors?monitor_type=group&meta=true", headers=headers)
        ).json()
        assert groups["total"] == 1
        group = groups["items"][0]
        assert group["child_count"] == 1, "the count that makes the refusal predictable"

        refused = await c.delete(f"/api/v1/uptime/monitors/{group['id']}", headers=headers)
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.uptime_group_has_children"

        child = (
            await c.get("/api/v1/uptime/monitors?monitor_type=http", headers=headers)
        ).json()["items"][0]
        await c.delete(f"/api/v1/uptime/monitors/{child['id']}", headers=headers)
        assert (
            await c.delete(f"/api/v1/uptime/monitors/{group['id']}", headers=headers)
        ).status_code == 204


async def test_a_group_move_in_kuma_is_drift_for_a_monitor_we_created(client_for, kuma) -> None:
    """The one field `adopted` was not being applied to.

    A monitor we created carries intent, so a group somebody changed in Uptime Kuma is drift —
    reported, never absorbed — and `adopt` must take the group with the rest, or the reconcile
    clears a flag whose disagreement is still there and the next sync raises it again.
    """
    t = await make_tenant("uptime-group-drift")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        group = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "Klanten", "monitor_type": "group"},
                headers=headers,
            )
        ).json()
        monitor = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={
                    "instance_id": instance_id,
                    "name": "klant.nl",
                    "monitor_type": "http",
                    "target": "https://klant.nl",
                    "parent_id": group["id"],
                },
                headers=headers,
            )
        ).json()
        assert monitor["parent_id"] == group["id"]

        # Somebody drags it out of the group in Uptime Kuma's own UI.
        kuma.monitors[monitor["kuma_monitor_id"]]["parent"] = None
        report = (
            await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        ).json()
        assert report["drifted"] == 1

        after = (await c.get(f"/api/v1/uptime/monitors/{monitor['id']}", headers=headers)).json()
        assert after["drift_fields"] == ["parent_id"]
        assert after["sync_status"] == "drift"
        assert after["parent_id"] == group["id"], "a move in Kuma was absorbed silently"

        adopted = await c.post(
            f"/api/v1/uptime/monitors/{monitor['id']}/reconcile",
            json={"direction": "adopt"},
            headers=headers,
        )
        assert adopted.json()["parent_id"] is None
        assert adopted.json()["drift_fields"] == []


async def test_a_vanished_monitor_keeps_the_group_it_was_put_in(client_for, kuma) -> None:
    """"It is gone from Kuma" must not also mean "and it was never in a group".

    Marked missing, and everything we decided about it left standing — including the group,
    which is what an admin needs in order to recreate it where it belonged.
    """
    t = await make_tenant("uptime-group-pending")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        group = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "Klanten", "monitor_type": "group"},
                headers=headers,
            )
        ).json()
        monitor = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={
                    "instance_id": instance_id,
                    "name": "klant.nl",
                    "monitor_type": "http",
                    "target": "https://klant.nl",
                    "parent_id": group["id"],
                },
                headers=headers,
            )
        ).json()
        # Forget it at the far end, as a failed push would have left it.
        kuma.monitors.pop(monitor["kuma_monitor_id"])
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)

        after = (await c.get(f"/api/v1/uptime/monitors/{monitor['id']}", headers=headers)).json()
        assert after["sync_status"] == "missing"
        assert after["parent_id"] == group["id"]


# ------------------------------------------------- the read half of a link (this fix)


async def test_a_domain_link_is_readable_by_the_domain_it_was_made_on(client_for, kuma) -> None:
    """A link you can write is a link something has to read back.

    `domain_id` had a matcher, a horizon, a confirm button and an activity line, and no way to
    ask "what watches this domain" — so confirming *"koppel aan domein klant.nl"* stored the row
    correctly and showed it on no screen in the product, which reads exactly like a button that
    does nothing. The panel filter is the whole fix, so it is what this pins.
    """
    t = await make_tenant("uptime-domain-read")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant")
        domain = await _domain(c, headers, "klant.nl", company)
        instance_id = await _connected(c, headers)
        # A host inside a zone we hold that will never be a website — `matching`'s own reason
        # for the domain rung, and the case with no website to fall back on.
        kuma.add(name="Mailserver", type="ping", hostname="mail.klant.nl")
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        monitor = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()["items"][0]

        await c.post(
            f"/api/v1/uptime/monitors/{monitor['id']}/link",
            json={"entity_type": "domain", "entity_id": domain},
            headers=headers,
        )
        panel = (
            await c.get(f"/api/v1/uptime/monitors?domain_id={domain}", headers=headers)
        ).json()
        assert [m["name"] for m in panel["items"]] == ["Mailserver"]

        # And the filter is a filter, not a decoration: another domain answers nothing.
        other = await _domain(c, headers, "anders.nl", company)
        assert (
            await c.get(f"/api/v1/uptime/monitors?domain_id={other}", headers=headers)
        ).json()["total"] == 0


async def test_unlinked_offers_what_no_proposal_ever_would(client_for, kuma) -> None:
    """The picker's question is "what may I still attach", not "what did the matcher find".

    Asking for `unmatched` would offer a monitor whose proposal nobody confirmed and hide the ones
    with no proposal at all — and the second set is exactly why the picker exists, because before
    it a monitor the matcher found nothing for appeared on no screen and could never be attached.
    """
    t = await make_tenant("uptime-unlinked")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant")
        domain = await _domain(c, headers, "klant.nl", company)
        website = await _website(c, headers, domain)
        instance_id = await _connected(c, headers)
        kuma.add(name="Website", url="https://klant.nl/")  # matches → a proposal
        kuma.add(name="Iets van niemand", url="https://192.0.2.7/")  # matches nothing at all
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)

        both = (
            await c.get("/api/v1/uptime/monitors?link_status=unlinked", headers=headers)
        ).json()
        assert sorted(m["name"] for m in both["items"]) == ["Iets van niemand", "Website"]
        assert sorted(m["link_status"] for m in both["items"]) == ["matched", "unmatched"]

        # Attach the one nothing proposed — the act that had no route through the UI.
        stray = next(m for m in both["items"] if m["name"] == "Iets van niemand")
        linked = await c.post(
            f"/api/v1/uptime/monitors/{stray['id']}/link",
            json={"entity_type": "website", "entity_id": website},
            headers=headers,
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["company_id"] == company

        # It leaves the attachable set, and the one still unconfirmed stays in it.
        after = (
            await c.get("/api/v1/uptime/monitors?link_status=unlinked", headers=headers)
        ).json()
        assert [m["name"] for m in after["items"]] == ["Website"]


async def test_meta_resolves_the_names_it_promises(client_for, kuma, count_queries) -> None:
    """`company_name` and `instance_name` were declared, documented, and filled by nobody.

    Pinned with the query count because the shape that would break it is invisible in the JSON:
    resolved per row, a list of one and a list of fifty look identical (docs/PERFORMANCE.md).
    """
    t = await make_tenant("uptime-meta")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant BV")
        domain = await _domain(c, headers, "klant.nl", company)
        website = await _website(c, headers, domain)
        instance_id = await _connected(c, headers)
        for n in range(4):
            kuma.add(name=f"Monitor {n}", url="https://klant.nl/")
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        for m in (await c.get("/api/v1/uptime/monitors", headers=headers)).json()["items"]:
            await c.post(
                f"/api/v1/uptime/monitors/{m['id']}/link",
                json={"entity_type": "website", "entity_id": website},
                headers=headers,
            )

        with count_queries() as counter:
            page = (
                await c.get("/api/v1/uptime/monitors?meta=true&count=false", headers=headers)
            ).json()
        assert {m["company_name"] for m in page["items"]} == {"Klant BV"}
        assert {m["instance_name"] for m in page["items"]} == {"Kuma"}
        companies = [s for s in counter.statements if "FROM companies" in s]
        assert len(companies) == 1, f"one read for the page, not one per row: {companies}"

        # Off by default: a picker renders names it never reads.
        bare = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()
        assert bare["items"][0]["company_name"] is None
