"""A website is a site at an address, and a domain may carry several (app/core/webaddress.py).

The case that drove it: an agency's dev installs live under one domain of its own —
``breik.dev/briellaerd``, ``breik.dev/nova`` — one WordPress per client. Before, the domain
normaliser stripped the path, ``uq_websites_domain`` allowed one site per domain, and the site's
client could only ever be the domain's (the agency itself).
"""

from __future__ import annotations

import pytest

from app.core.webaddress import (
    normalize_path,
    split_address,
    website_label,
    website_url,
)
from app.integrations.uptime import matching
from tests.conftest import auth_cookie, make_tenant
from tests.test_company_groups import _setup
from tests.test_impex_assets import _csv_bytes, _import, _rows

# --------------------------------------------------------------------------- #
# The pure rule
# --------------------------------------------------------------------------- #


def test_normalize_path_reads_what_people_type() -> None:
    assert normalize_path("") == ""
    assert normalize_path("/") == ""
    assert normalize_path("briellaerd") == "/briellaerd"
    assert normalize_path("/briellaerd/") == "/briellaerd"
    assert normalize_path("//klant//shop/") == "/klant/shop"
    # Case is kept: a Linux host treats /Nova and /nova as two directories.
    assert normalize_path("/Nova") == "/Nova"
    for bad in ("?preview=1", "/a b", "/x#top", "https://breik.dev/x"):
        with pytest.raises(ValueError, match="errors.invalid_website_path"):
            normalize_path(bad)


def test_split_address_is_the_inverse_of_the_label() -> None:
    assert split_address("https://www.Klant.nl/shop/") == ("klant.nl", False, "/shop")
    assert split_address("breik.dev/briellaerd") == ("breik.dev", True, "/briellaerd")
    assert split_address("klant.nl") == ("klant.nl", True, "")
    assert website_label("breik.dev", True, "/briellaerd") == "breik.dev/briellaerd"
    assert website_label("klant.nl", False, "") == "www.klant.nl"
    assert website_url("breik.dev", True, "/briellaerd") == "https://breik.dev/briellaerd"
    for raw in ("breik.dev/briellaerd", "www.klant.nl/shop", "klant.nl"):
        apex, root, path = split_address(raw)
        assert website_label(apex, root, path) == raw


def test_uptime_matching_tells_a_domains_sites_apart_by_path() -> None:
    assert matching.path_of("https://breik.dev/briellaerd/wp-admin/") == "/briellaerd/wp-admin"
    assert matching.path_of("breik.dev") == ""
    assert matching.path_of("https://breik.dev/?x=1") == ""
    import uuid

    root = matching.LinkCandidate("website", uuid.uuid4(), "breik.dev", None, "")
    nova = matching.LinkCandidate("website", uuid.uuid4(), "breik.dev/nova", None, "/nova")
    deep = matching.LinkCandidate(
        "website", uuid.uuid4(), "breik.dev/nova/shop", None, "/nova/shop"
    )
    websites = {"breik.dev": [root, nova, deep]}
    # The longest site path the monitor sits under wins; a target under none means the root.
    assert matching.candidates_for("breik.dev", websites, {}, path="/nova/wp-admin") == [nova]
    assert matching.candidates_for("breik.dev", websites, {}, path="/nova/shop/x") == [deep]
    assert matching.candidates_for("breik.dev", websites, {}, path="") == [root]
    assert matching.candidates_for("breik.dev", websites, {}, path="/other") == [root]
    # No root site: every site on the host is offered, for a person to choose.
    assert matching.candidates_for("breik.dev", {"breik.dev": [nova, deep]}, {}, path="/x") == [
        nova,
        deep,
    ]


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #


async def _company(c, headers, name: str) -> str:
    return (await c.post("/api/v1/companies", json={"name": name}, headers=headers)).json()["id"]


async def _domain(c, headers, name: str, company_id: str) -> str:
    r = await c.post(
        "/api/v1/domains", json={"name": name, "company_id": company_id}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_a_domain_carries_one_site_per_address(client_for) -> None:
    t = await make_tenant("web-addr")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        agency = await _company(c, headers, "breik.")
        domain = await _domain(c, headers, "breik.dev", agency)

        root = await c.post("/api/v1/websites", json={"domain_id": domain}, headers=headers)
        assert root.status_code == 201, root.text
        assert root.json()["path"] == ""
        assert root.json()["label"] == "breik.dev"
        assert root.json()["host"] == "breik.dev"
        assert root.json()["url"] == "https://breik.dev"

        # The path is normalised the way a person types it, and the address is the name.
        dev = await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "path": "briellaerd/"},
            headers=headers,
        )
        assert dev.status_code == 201, dev.text
        assert dev.json()["path"] == "/briellaerd"
        assert dev.json()["label"] == "breik.dev/briellaerd"
        assert dev.json()["url"] == "https://breik.dev/briellaerd"
        # Still the domain's client: no override was named.
        assert dev.json()["company_id"] == agency
        assert dev.json()["company_override_id"] is None

        # One site per address — refused with the address named (#305).
        dup = await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "path": "/briellaerd"},
            headers=headers,
        )
        assert dup.status_code == 409, dup.text
        assert dup.json()["error"]["message"] == "errors.website_exists"
        assert dup.json()["error"]["details"] == {"label": "breik.dev/briellaerd"}
        # …and the same address under www is a different site.
        www = await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "root": False, "path": "/briellaerd"},
            headers=headers,
        )
        assert www.status_code == 201, www.text
        assert www.json()["label"] == "www.breik.dev/briellaerd"

        # A path that is not a path is refused as a field error, in the envelope's language.
        bad = await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "path": "/x?preview=1"},
            headers=headers,
        )
        assert bad.status_code == 422, bad.text
        assert bad.json()["error"]["fields"]["path"] == "errors.invalid_website_path"

        # The search reads the address, not only the domain.
        found = await c.get("/api/v1/websites?q=briellaerd", headers=headers)
        assert {w["label"] for w in found.json()["items"]} == {
            "breik.dev/briellaerd",
            "www.breik.dev/briellaerd",
        }
        listed = await c.get(f"/api/v1/websites?domain_id={domain}&sort=name", headers=headers)
        assert [w["label"] for w in listed.json()["items"]] == [
            "breik.dev",
            "breik.dev/briellaerd",
            "www.breik.dev/briellaerd",
        ]

        # Moving a site onto an address that is taken is refused the same way.
        moved = await c.patch(
            f"/api/v1/websites/{www.json()['id']}", json={"root": True}, headers=headers
        )
        assert moved.status_code == 409
        renamed = await c.patch(
            f"/api/v1/websites/{www.json()['id']}", json={"path": "/nova"}, headers=headers
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["label"] == "www.breik.dev/nova"

        # The picker offers every domain, saying what is already on it.
        offered = await c.get("/api/v1/websites/available-domains", headers=headers)
        assert offered.status_code == 200
        (row,) = [d for d in offered.json() if d["id"] == domain]
        assert row["taken"] == ["breik.dev", "breik.dev/briellaerd", "www.breik.dev/nova"]


async def test_a_dev_site_belongs_to_the_client_it_names(client_for) -> None:
    t = await make_tenant("web-owner")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        agency = await _company(c, headers, "breik.")
        nova = await _company(c, headers, "Nova Fietsen")
        domain = await _domain(c, headers, "breik.dev", agency)

        site = await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "path": "/nova", "company_override_id": nova},
            headers=headers,
        )
        assert site.status_code == 201, site.text
        wid = site.json()["id"]
        assert site.json()["company_id"] == nova
        assert site.json()["company_name"] == "Nova Fietsen"
        assert site.json()["company_override_id"] == nova

        # The client filter — what the hub's panel and every picker read — follows the override.
        mine = await c.get(f"/api/v1/websites?company_id={nova}", headers=headers)
        assert [w["id"] for w in mine.json()["items"]] == [wid]
        theirs = await c.get(f"/api/v1/websites?company_id={agency}", headers=headers)
        assert theirs.json()["total"] == 0

        # An override that restates the domain's own client stores nothing: NULL = inherit.
        restated = await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "company_override_id": agency},
            headers=headers,
        )
        assert restated.status_code == 201, restated.text
        assert restated.json()["company_override_id"] is None
        assert restated.json()["company_id"] == agency

        # Absent leaves the client alone; an explicit null follows the domain again (§18).
        touched = await c.patch(
            f"/api/v1/websites/{wid}", json={"uptime_enabled": True}, headers=headers
        )
        assert touched.json()["company_id"] == nova
        cleared = await c.patch(
            f"/api/v1/websites/{wid}", json={"company_override_id": None}, headers=headers
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["company_override_id"] is None
        assert cleared.json()["company_id"] == agency

        # A client this tenant does not hold answers as a read of it would (§15's 404 rule).
        other = await make_tenant("web-owner-b")
        other_headers = await auth_cookie(other.user)
        async with client_for(other.host) as cb:
            foreign = await _company(cb, other_headers, "Elders")
        refused = await c.patch(
            f"/api/v1/websites/{wid}", json={"company_override_id": foreign}, headers=headers
        )
        assert refused.status_code == 404


async def test_the_horizon_follows_the_site_s_own_client(client_for) -> None:
    """A restricted manager whose portfolio holds client A sees A's dev site on B's domain and
    not the root site on that domain; and cannot file a site onto B."""
    t, _member, membership, owner, member, company_a, company_b, group = await _setup(
        client_for, "web-horizon", role="admin"
    )
    async with client_for(t.host) as c:
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership.id)]},
                headers=owner,
            )
        ).status_code == 204
        domain = await _domain(c, owner, "breik.dev", company_b["id"])
        root = (
            await c.post("/api/v1/websites", json={"domain_id": domain}, headers=owner)
        ).json()["id"]
        dev = (
            await c.post(
                "/api/v1/websites",
                json={
                    "domain_id": domain,
                    "path": "/alpha",
                    "company_override_id": company_a["id"],
                },
                headers=owner,
            )
        ).json()["id"]

        seen = await c.get("/api/v1/websites", headers=member)
        assert seen.status_code == 200, seen.text
        assert [w["id"] for w in seen.json()["items"]] == [dev]
        assert seen.json()["total"] == 1
        assert (await c.get(f"/api/v1/websites/{dev}", headers=member)).status_code == 200
        assert (await c.get(f"/api/v1/websites/{root}", headers=member)).status_code == 404

        # The domain itself is outside the horizon, so nothing new goes on it; and an
        # override onto B is refused before anything is written.
        blocked = await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "path": "/two", "company_override_id": company_a["id"]},
            headers=member,
        )
        assert blocked.status_code == 404
        escalated = await c.patch(
            f"/api/v1/websites/{dev}", json={"company_override_id": company_b["id"]}, headers=member
        )
        assert escalated.status_code == 404


async def test_impex_matches_a_site_by_its_address(client_for) -> None:
    """The export's ``address`` column is the row's identity, so an export of a domain with
    several sites re-imports as updates; a file carrying only ``domain`` is ambiguous there."""
    t = await make_tenant("web-impex-addr")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        agency = await _company(c, headers, "breik.")
        nova = await _company(c, headers, "Nova Fietsen")
        domain = await _domain(c, headers, "breik.dev", agency)
        await c.post("/api/v1/websites", json={"domain_id": domain}, headers=headers)
        await c.post(
            "/api/v1/websites",
            json={"domain_id": domain, "path": "/nova", "company_override_id": nova},
            headers=headers,
        )

        r = await c.get("/api/v1/impex/website/export", headers=headers)
        _, rows = _rows(r.content)
        by_address = {row["address"]: row for row in rows}
        assert set(by_address) == {"breik.dev", "breik.dev/nova"}
        assert by_address["breik.dev/nova"]["path"] == "/nova"
        assert by_address["breik.dev/nova"]["company"] == "Nova Fietsen"
        assert by_address["breik.dev"]["company"] == "breik."

        again = await _import(c, headers, "website", r.content)
        assert (again["creates"], again["updates"], again["error_count"]) == (0, 2, 0)
        # Re-importing the resolved client did not freeze the domain's own client onto the root.
        listed = await c.get(f"/api/v1/websites?domain_id={domain}&sort=name", headers=headers)
        by_label = {w["label"]: w for w in listed.json()["items"]}
        assert by_label["breik.dev"]["company_override_id"] is None
        assert by_label["breik.dev/nova"]["company_override_id"] == nova

        # A hand-made file naming the client for a new dev install creates it under that client.
        created = await _import(
            c,
            headers,
            "website",
            _csv_bytes(
                ["domain", "path", "company"], [["breik.dev", "briellaerd", "Nova Fietsen"]]
            ),
        )
        assert (created["creates"], created["error_count"]) == (1, 0)
        listed = await c.get("/api/v1/websites?q=briellaerd", headers=headers)
        assert listed.json()["items"][0]["company_id"] == nova

        # Only the domain named, on a domain with three sites: the domain is no identity any
        # more, so the row is a create — of the root, which exists — and the preview says so on
        # the row rather than the commit failing the whole file.
        vague = await _import(
            c,
            headers,
            "website",
            _csv_bytes(["domain", "uptime_enabled"], [["breik.dev", "true"]]),
            commit=False,
        )
        assert vague["error_count"] == 1
        assert vague["errors"][0]["message_key"] == "errors.website_exists"
        assert vague["errors"][0]["field"] == "domain"

        # A domain carrying one site still matches on its name alone (an older export).
        single = await _domain(c, headers, "klant.nl", nova)
        await c.post("/api/v1/websites", json={"domain_id": single}, headers=headers)
        older = await _import(
            c,
            headers,
            "website",
            _csv_bytes(["domain", "uptime_enabled"], [["klant.nl", "true"]]),
        )
        assert (older["creates"], older["updates"], older["error_count"]) == (0, 1, 0)
