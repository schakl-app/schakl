"""The connected site as a surface (docs/WORDPRESS.md §7): content, forms, abilities, facts.

What these pin, beyond "the route answers":

* **A site is a parameter, never a tool** — the MCP section for the module is the same set of
  tools however many sites are connected, and a site is picked by client.
* **The audience decides the permission** — a draft is ``content.write``, anything a visitor
  can see is ``content.publish``, and an ability runs on ``ability.read`` only if it says it is
  read-only.
* **The wire shapes are the plugin's, not ours** — CF7's flat write against its nested read,
  core's verb-per-annotation for abilities, ``X-WP-Total`` for a count.
* **Every write leaves a trail line on the site row** (§16).
"""

from __future__ import annotations

import pytest

from app.db import async_session_maker, set_current_org
from app.integrations.wordpress import client as wp_client
from tests.conftest import add_membership, auth_cookie, make_tenant
from tests.test_wordpress_api import _connect, _restricted_member, _website
from tests.wordpress_fake import FakeWordPress


@pytest.fixture
def wp(monkeypatch) -> FakeWordPress:
    fake = FakeWordPress()
    wp_client.set_transport(fake.transport())
    yield fake
    wp_client.set_transport(None)


async def _member(t, slug: str, *, role: str) -> dict[str, str]:
    """A second login in ``t`` holding the system role's defaults — the way
    ``test_wordpress_api._restricted_member`` conjures one, minus the group."""
    other = await make_tenant(f"{slug}-{role}", email=f"{role}-{slug}@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, other.user.id, role=role)
        await session.commit()
    return await auth_cookie(other.user, org_id=t.org.id)


async def _site(c, headers) -> tuple[dict, dict]:
    made = await _website(c, headers)
    site = await _connect(c, headers, made["website"]["id"])
    return made, site


# ---------------------------------------------------------------- the probe, corrected


async def test_the_adapter_is_found_under_the_bare_mcp_namespace(client_for, wp) -> None:
    """The real index lists the namespace as ``mcp`` and each server as a route under it.
    Three live sites had the adapter and were all recorded as having none."""
    t = await make_tenant("wp-mcp-ns")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        result = (
            await c.post(f"/api/v1/wordpress/sites/{site['id']}/verify", headers=headers)
        ).json()
        assert result["capabilities"]["mcp"] is True
        # The transport route, not the OAuth metadata one that sits beside it.
        assert result["mcp_server_path"] == "/wp-json/mcp/agency-server"


def test_the_server_route_prefers_the_one_that_takes_post() -> None:
    routes = {
        "/mcp": {"endpoints": [{"methods": ["GET"]}]},
        "/mcp/mcp-oauth-server": {"endpoints": [{"methods": ["GET"]}]},
        "/mcp/mcp-adapter-default-server": {"endpoints": [{"methods": ["POST", "GET", "DELETE"]}]},
    }
    assert wp_client.mcp_server_route(["wp/v2", "mcp"], routes) == "/mcp/mcp-adapter-default-server"
    assert wp_client.mcp_server_route(["wp/v2"], routes) is None
    assert wp_client.mcp_server_route(["mcp"], {"/mcp": {}}) is None


# ---------------------------------------------------------------- a site, by client


async def test_sites_are_listed_by_client_and_carry_their_labels(client_for, wp) -> None:
    t = await make_tenant("wp-by-client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made_a, site_a = await _site(c, headers)
        made_b = await _website(c, headers, key="b")
        await _connect(c, headers, made_b["website"]["id"], base_url="https://klant-b.nl")

        rows = (await c.get("/api/v1/wordpress/sites", headers=headers)).json()
        assert {r["company_name"] for r in rows} == {"Klant a", "Klant b"}
        assert {r["domain_name"] for r in rows} == {"klant-a.nl", "klant-b.nl"}

        mine = (
            await c.get(
                f"/api/v1/wordpress/sites?company_id={made_a['company']['id']}", headers=headers
            )
        ).json()
        assert [r["id"] for r in mine] == [site_a["id"]]
        assert mine[0]["company_id"] == made_a["company"]["id"]


async def test_the_summary_says_what_the_site_is(client_for, wp) -> None:
    t = await make_tenant("wp-summary")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        res = await c.get(f"/api/v1/wordpress/sites/{site['id']}/summary", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["name"] == "Klant BV"
        # From the two core abilities — the versions core's REST never publishes on its own.
        assert body["wp_version"] == "6.9.1"
        assert body["php_version"] == "8.3.4"
        assert body["locale"] == "nl_NL"
        assert body["has_forms"] is True
        assert body["has_abilities"] is True
        assert body["multilingual"] is False
        types = {row["slug"]: row["rest_base"] for row in body["content_types"]}
        assert types["page"] == "pages"
        assert types["dienst"] == "diensten"
        # Core types first, then the site's own.
        assert [row["slug"] for row in body["content_types"]][:2] == ["page", "post"]


async def test_the_summary_survives_a_site_without_abilities(client_for, wp) -> None:
    wp.has_abilities = False
    t = await make_tenant("wp-summary-old")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        body = (
            await c.get(f"/api/v1/wordpress/sites/{site['id']}/summary", headers=headers)
        ).json()
        assert body["wp_version"] is None, "never guessed"
        assert body["has_abilities"] is False
        assert body["content_types"], "post types do not depend on abilities"


# ---------------------------------------------------------------- content


async def test_content_lists_drafts_too_and_reads_one_whole(client_for, wp) -> None:
    t = await make_tenant("wp-content")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        base = f"/api/v1/wordpress/sites/{site['id']}"

        listing = (await c.get(f"{base}/content?type=page", headers=headers)).json()
        assert listing["total"] == 2
        assert [r["slug"] for r in listing["items"]] == ["nieuwe-dienst", "home"]
        assert listing["items"][0]["status"] == "draft"
        assert listing["items"][1]["lang"] == "nl"

        found = (await c.get(f"{base}/content?type=page&search=home", headers=headers)).json()
        assert [r["id"] for r in found["items"]] == [1]

        page = (await c.get(f"{base}/content/page/1", headers=headers)).json()
        assert page["title"] == "Home"
        assert page["content"] == "<h2>Welkom</h2>"
        assert page["rendered"].startswith("<p>")
        assert page["acf"]["slider"] == [{"titel": "Een"}]
        assert page["featured_media"] == 6632

        # A custom post type resolves its REST base through the site's own `/types`.
        cpt = (await c.get(f"{base}/content?type=dienst", headers=headers)).json()
        assert cpt["items"] == [] and cpt["total"] == 0
        unknown = await c.get(f"{base}/content?type=nonsense", headers=headers)
        assert unknown.status_code == 422
        assert unknown.json()["error"]["message"] == "errors.wordpress_unknown_type"
        assert "dienst" in unknown.json()["error"]["details"]["known"]

        missing = await c.get(f"{base}/content/page/999", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["error"]["message"] == "errors.wordpress_not_on_site"


async def test_media_resolves_an_acf_image_id(client_for, wp) -> None:
    t = await make_tenant("wp-media")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        base = f"/api/v1/wordpress/sites/{site['id']}"
        row = (await c.get(f"{base}/media/6632", headers=headers)).json()
        assert row["source_url"].endswith("/hero.jpg")
        assert row["width"] == 1600 and row["alt"] == "Hero"
        listing = (await c.get(f"{base}/media", headers=headers)).json()
        assert listing["total"] == 1


async def test_a_draft_is_written_on_write_and_a_live_page_needs_publish(client_for, wp) -> None:
    """The audience is the boundary: editing a draft is `content.write`; touching anything a
    visitor can see, or making a draft visible, is `content.publish`."""
    t = await make_tenant("wp-publish")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        base = f"/api/v1/wordpress/sites/{site['id']}"
        # A member holds `content.write` by default and never `content.publish`.
        member_h = await _member(t, "wp-publish", role="member")

        # Editing the draft: fine.
        res = await c.patch(
            f"{base}/content/page/2",
            json={"acf": {"subtitel": "Nieuw"}, "title": "Nieuwe dienst 2"},
            headers=member_h,
        )
        assert res.status_code == 200, res.text
        assert res.json()["acf"] == {"subtitel": "Nieuw"}
        assert res.json()["title"] == "Nieuwe dienst 2"
        path, body = wp.writes[-1]
        assert path.endswith("/pages/2") and body == {
            "acf": {"subtitel": "Nieuw"},
            "title": "Nieuwe dienst 2",
        }

        # Editing the live home page: not with this key, whatever the field.
        res = await c.patch(f"{base}/content/page/1", json={"title": "x"}, headers=member_h)
        assert res.status_code == 403
        # Publishing the draft: also not.
        res = await c.patch(f"{base}/content/page/2", json={"status": "publish"}, headers=member_h)
        assert res.status_code == 403
        assert wp.content["pages"][2]["status"] == "draft", "refused before the write"

        # Creating: a draft by default, and a live create is a publish.
        res = await c.post(f"{base}/content", json={"title": "Concept"}, headers=member_h)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "draft"
        res = await c.post(
            f"{base}/content", json={"title": "Live", "status": "publish"}, headers=member_h
        )
        assert res.status_code == 403

        # The owner publishes.
        res = await c.patch(f"{base}/content/page/2", json={"status": "publish"}, headers=owner_h)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "publish"

        # An empty patch is refused rather than sent.
        res = await c.patch(f"{base}/content/page/2", json={}, headers=owner_h)
        assert res.status_code == 422

        # And the site row carries the trail (§16).
        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=wordpress_site&entity_id={site['id']}",
                headers=owner_h,
            )
        ).json()
        actions = [row["action"] for row in trail if row["action"].startswith("content_")]
        assert "content_updated" in actions and "content_created" in actions
        updated = next(
            row
            for row in trail
            if row["action"] == "content_updated" and row["payload"]["wp_id"] == 2
        )
        assert updated["payload"]["fields"] in (["acf", "title"], ["status"])


async def test_a_status_the_site_would_refuse_is_refused_here_first(client_for, wp) -> None:
    t = await make_tenant("wp-status")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        res = await c.patch(
            f"/api/v1/wordpress/sites/{site['id']}/content/page/2",
            json={"status": "trash"},
            headers=headers,
        )
        assert res.status_code == 422
        assert wp.writes == []


# ---------------------------------------------------------------- forms


async def test_forms_read_nested_and_write_flat(client_for, wp) -> None:
    """CF7 answers ``properties.form = {content, fields}`` and takes ``form`` as a string —
    read from the plugin source, because a body posted in the read's shape is ignored."""
    t = await make_tenant("wp-forms")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        base = f"/api/v1/wordpress/sites/{site['id']}"

        rows = (await c.get(f"{base}/forms", headers=headers)).json()
        assert [r["slug"] for r in rows] == ["contactformulier"]

        form = (await c.get(f"{base}/forms/6584", headers=headers)).json()
        assert form["form"].startswith("[text* your-name]")
        assert form["fields"] == ["your-name", "your-email"], "the submit button is not a field"
        assert form["mail"]["recipient"] == "info@klant.nl"
        assert form["messages"] == {"mail_sent_ok": "Bedankt."}

        res = await c.patch(
            f"{base}/forms/6584",
            json={"mail": {**form["mail"], "recipient": "sales@klant.nl"}},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["mail"]["recipient"] == "sales@klant.nl"
        path, body = wp.writes[-1]
        assert path.endswith("/contact-forms/6584")
        assert set(body) == {"mail"} and body["mail"]["recipient"] == "sales@klant.nl"

        created = await c.post(
            f"{base}/forms",
            json={"title": "Offerte", "form": "[email* your-email] [submit]"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["title"] == "Offerte"


async def test_a_site_without_cf7_says_so(client_for, wp) -> None:
    wp.has_forms = False
    t = await make_tenant("wp-no-cf7")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        res = await c.get(f"/api/v1/wordpress/sites/{site['id']}/forms", headers=headers)
        assert res.status_code == 404
        assert res.json()["error"]["message"] == "errors.wordpress_not_on_site"
        assert "rest_no_route" in res.json()["error"]["fields"]["detail"]
        summary = (
            await c.get(f"/api/v1/wordpress/sites/{site['id']}/summary", headers=headers)
        ).json()
        assert summary["has_forms"] is False


# ---------------------------------------------------------------- abilities


async def test_abilities_are_listed_whole_and_run_by_their_own_verb(client_for, wp) -> None:
    t = await make_tenant("wp-abilities")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        base = f"/api/v1/wordpress/sites/{site['id']}"
        member_h = await _member(t, "wp-abilities", role="member")

        rows = (await c.get(f"{base}/abilities", headers=member_h)).json()
        names = {r["name"]: r for r in rows}
        assert "acf/field-groups" in names and "rank-math/create-ai-visibility-brand" in names
        assert names["acf/field-groups"]["readonly"] is True
        assert names["acf/create-field-group"]["readonly"] is False
        assert names["acf/create-field-group"]["input_schema"]["properties"]["title"]

        # Read-only: a member may, and core is asked with GET + query input.
        res = await c.post(
            f"{base}/abilities/run",
            json={"name": "acf/field-groups", "input": {"post_type": "page"}},
            headers=member_h,
        )
        assert res.status_code == 200, res.text
        assert res.json()["readonly"] is True
        assert wp.ability_runs[-1] == ("GET", "acf/field-groups", {"post_type": "page"})

        # A write: not on `ability.read`, and refused before the site is asked.
        runs = len(wp.ability_runs)
        res = await c.post(
            f"{base}/abilities/run",
            json={"name": "acf/create-field-group", "input": {"title": "Hero"}},
            headers=member_h,
        )
        assert res.status_code == 403
        assert len(wp.ability_runs) == runs

        # The owner may, and core is asked with POST + JSON input.
        res = await c.post(
            f"{base}/abilities/run",
            json={"name": "acf/create-field-group", "input": {"title": "Hero"}},
            headers=owner_h,
        )
        assert res.status_code == 200, res.text
        assert wp.ability_runs[-1] == ("POST", "acf/create-field-group", {"title": "Hero"})

        res = await c.post(f"{base}/abilities/run", json={"name": "nope/nothing"}, headers=owner_h)
        assert res.status_code == 404

        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=wordpress_site&entity_id={site['id']}",
                headers=owner_h,
            )
        ).json()
        assert [r["payload"]["name"] for r in trail if r["action"] == "ability_run"] == [
            "acf/create-field-group"
        ], "a read-only run is not a change worth a trail line"


async def test_the_abilities_list_is_paged_to_the_end(client_for, wp) -> None:
    wp.extra_abilities += [
        {
            "name": f"plugin/ability-{n}",
            "label": "",
            "description": "",
            "category": "x",
            "input_schema": {},
            "output_schema": {},
            "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
        }
        for n in range(120)
    ]
    t = await make_tenant("wp-abilities-paged")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        rows = (
            await c.get(f"/api/v1/wordpress/sites/{site['id']}/abilities", headers=headers)
        ).json()
        assert len(rows) == 120 + 4 + 4


# ---------------------------------------------------------------- who may


async def test_the_surface_is_inside_the_caller_s_horizon(client_for, wp) -> None:
    """A restricted member reaches their own client's site and nobody else's — the horizon
    clause on the row is what the surface routes inherit, so the 404 is the same one."""
    t = await make_tenant("wp-surface-horizon")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        made_a, site_a = await _site(c, owner_h)
        made_b = await _website(c, owner_h, key="b")
        site_b = await _connect(c, owner_h, made_b["website"]["id"], base_url="https://klant-b.nl")
        headers = await _restricted_member(
            client_for, c, t, owner_h, slug="wp-surface-horizon", company_id=made_a["company"]["id"]
        )
        ok = await c.get(f"/api/v1/wordpress/sites/{site_a['id']}/summary", headers=headers)
        assert ok.status_code == 200, ok.text
        no = await c.get(f"/api/v1/wordpress/sites/{site_b['id']}/summary", headers=headers)
        assert no.status_code == 404
        no = await c.get(f"/api/v1/wordpress/sites/{site_b['id']}/content", headers=headers)
        assert no.status_code == 404


async def test_a_client_login_reaches_none_of_it(client_for, wp) -> None:
    t = await make_tenant("wp-surface-portal")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        headers = await _member(t, "wp-surface-portal", role="client")
        for path in ("summary", "content", "forms", "abilities", "media"):
            res = await c.get(f"/api/v1/wordpress/sites/{site['id']}/{path}", headers=headers)
            assert res.status_code == 403, (path, res.text)


async def test_the_mcp_section_does_not_grow_with_the_sites(client_for, wp) -> None:
    """Forty sites are forty rows and zero tools: the section is derived from the router."""
    from app.core.mcp.sections import build_sections
    from app.core.mcp.server import _tool_index
    from app.main import app

    _, tool_paths = _tool_index(app)
    sections = build_sections(tool_paths)
    wordpress = sections["wordpress"]
    surface = {name for name, path in tool_paths.items() if "/wordpress/sites/{site_id}/" in path}
    assert surface <= wordpress.tools
    assert {"list_site_content", "run_site_ability", "get_site_form"} <= wordpress.tools


# ---------------------------------------------------------------- the passthrough


def test_a_passthrough_path_is_relative_to_wp_json_and_cannot_climb() -> None:
    from app.integrations.wordpress.surface import normalise_rest_path as n

    assert n("wp/v2/settings") == "wp/v2/settings"
    assert n("/wp-json/wpml/v1/languages?x=1") == "wpml/v1/languages"
    assert n("/wp/v2/pages/") == "wp/v2/pages"
    assert n("wp/v2/../../wp-admin") == ""
    assert n("https://evil.example/wp-json/wp/v2") == ""
    assert n("//evil.example/x") == ""
    assert n("") == ""


async def test_the_passthrough_reads_on_read_and_writes_on_write_minus_takeover(
    client_for, wp
) -> None:
    t = await make_tenant("wp-rest")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        url = f"/api/v1/wordpress/sites/{site['id']}/rest"
        member_h = await _member(t, "wp-rest", role="member")

        # A plugin route no curated route knows, read by a member.
        res = await c.post(url, json={"path": "wpml/v1/languages"}, headers=member_h)
        assert res.status_code == 200, res.text
        assert res.json()["data"] == [{"code": "nl"}, {"code": "en"}]
        assert res.json()["path"] == "wpml/v1/languages"
        assert res.json()["truncated"] is False

        # Reads of the takeover routes stay open.
        res = await c.post(url, json={"path": "/wp-json/wp/v2/settings"}, headers=member_h)
        assert res.status_code == 200
        assert res.json()["data"]["email"] == "info@klant.nl"

        # A write is not a member's.
        res = await c.post(
            url, json={"method": "POST", "path": "litespeed/v1/purge"}, headers=member_h
        )
        assert res.status_code == 403
        assert wp.writes == []

        # The owner writes a plugin route, and it leaves a trail line.
        res = await c.post(
            url,
            json={"method": "post", "path": "litespeed/v1/purge", "body": {"all": True}},
            headers=owner_h,
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"] == {"purged": True}
        assert wp.writes[-1] == ("/wp-json/litespeed/v1/purge", {"all": True})
        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=wordpress_site&entity_id={site['id']}",
                headers=owner_h,
            )
        ).json()
        assert [r["payload"] for r in trail if r["action"] == "rest_written"] == [
            {"method": "POST", "path": "litespeed/v1/purge"}
        ]

        # The takeover routes are refused for everybody, before the site is asked.
        for path in ("wp/v2/users", "wp/v2/settings", "wp/v2/plugins/akismet", "wp/v2/themes"):
            res = await c.post(url, json={"method": "POST", "path": path}, headers=owner_h)
            assert res.status_code == 403, path
            assert res.json()["error"]["message"] == "errors.wordpress_rest_denied"
            assert "wp/v2/users" in res.json()["error"]["details"]["denied"]
        assert len(wp.writes) == 1

        # Bad shapes are ours to refuse.
        res = await c.post(url, json={"path": "wp/v2/../../x"}, headers=owner_h)
        assert res.status_code == 422
        res = await c.post(url, json={"method": "HEAD", "path": "wp/v2"}, headers=owner_h)
        assert res.status_code == 422


async def test_the_passthrough_caps_what_it_hands_back_and_says_so(client_for, wp) -> None:
    t = await make_tenant("wp-rest-cap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        res = await c.post(
            f"/api/v1/wordpress/sites/{site['id']}/rest",
            json={"path": "big/v1/rows"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["truncated"] is True
        assert 0 < body["shown"] < 400
        assert len(body["data"]) == body["shown"]


async def test_a_client_login_has_no_passthrough(client_for, wp) -> None:
    t = await make_tenant("wp-rest-portal")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        headers = await _member(t, "wp-rest-portal", role="client")
        res = await c.post(
            f"/api/v1/wordpress/sites/{site['id']}/rest",
            json={"path": "wp/v2/pages"},
            headers=headers,
        )
        assert res.status_code == 403
