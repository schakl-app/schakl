"""The breik. Bridge plugin as a surface (docs/WORDPRESS.md §9).

What these pin, beyond "the route answers":

* **"Not installed" is decided by the call.** A site without the plugin answers core's
  ``rest_no_route`` on every bridge route and becomes a 409 that names the plugin — never a
  502, never a guess from the stored version.
* **The plugin's refusal is carried, not translated.** ``details.problems`` (path, field,
  code, the allowed choices) lands in schakl's envelope untouched, because the path is what
  an agent corrects by.
* **The audience decides the permission**, read off the plugin's own record: a member drafts,
  editing a live page or publishing is ``content.publish``, and deleting has its own key.
* **Every write leaves a trail line on the site row** (§16).
* **A site is a parameter, never a tool**: the bridge routes are in the ``wordpress`` MCP
  section whether the agency holds one site or forty.
"""

from __future__ import annotations

import pytest

from app.integrations.wordpress import client as wp_client
from tests.conftest import auth_cookie, make_tenant
from tests.test_wordpress_surface import _member, _site
from tests.wordpress_fake import FakeWordPress


@pytest.fixture
def wp(monkeypatch) -> FakeWordPress:
    fake = FakeWordPress()
    wp_client.set_transport(fake.transport())
    yield fake
    wp_client.set_transport(None)


def _url(site: dict, tail: str = "") -> str:
    return f"/api/v1/wordpress/sites/{site['id']}/bridge{tail}"


# ---------------------------------------------------------------- the probe


async def test_the_probe_records_the_plugin_and_its_version(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-probe")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        res = await c.post(f"/api/v1/wordpress/sites/{site['id']}/verify", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["capabilities"]["bridge"] is True
        assert res.json()["bridge_version"] == "1.0.0"
        row = (await c.get(f"/api/v1/wordpress/sites/{site['id']}", headers=headers)).json()
        assert row["bridge_version"] == "1.0.0"

        # A probe that reaches the site and finds the plugin gone clears the version —
        # the observation rule: what ran and found nothing clears its own entry.
        wp.has_bridge = False
        res = await c.post(f"/api/v1/wordpress/sites/{site['id']}/verify", headers=headers)
        assert res.json()["capabilities"]["bridge"] is False
        assert "rest_no_route" in res.json()["capability_errors"]["bridge"]
        assert res.json()["bridge_version"] is None
        # …while a probe that could not reach the site leaves it alone.
        wp.has_bridge = True
        await c.post(f"/api/v1/wordpress/sites/{site['id']}/verify", headers=headers)
        wp.is_wordpress = False
        res = await c.post(f"/api/v1/wordpress/sites/{site['id']}/verify", headers=headers)
        assert (
            "bridge" not in res.json()["capabilities"]
            or res.json()["capabilities"]["bridge"] is False
        )
        assert res.json()["bridge_version"] == "1.0.0"


async def test_a_site_without_the_plugin_answers_409_naming_it(client_for, wp) -> None:
    wp.has_bridge = False
    t = await make_tenant("wp-bridge-missing")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        for path in ("", "/schema?post_type=page", "/records?post_type=page", "/languages"):
            res = await c.get(_url(site, path), headers=headers)
            assert res.status_code == 409, (path, res.text)
            body = res.json()["error"]
            assert body["message"] == "errors.wordpress_bridge_missing"
            assert body["details"]["plugin"] == "breik-bridge"


# ---------------------------------------------------------------- reads


async def test_info_schema_and_records_read_through_the_plugin(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-read")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        member_h = await _member(t, "wp-bridge-read", role="member")

        info = (await c.get(_url(site), headers=member_h)).json()
        assert info["bridge_version"] == "1.0.0"
        assert info["acf"] == {"version": "6.8.10", "pro": True}
        # The whole point: a post type hidden from wp/v2 is listed, and says so.
        dienst = next(p for p in info["post_types"] if p["slug"] == "dienst")
        assert dienst["rest"] is False and dienst["acf_groups"] == 1
        assert info["options_pages"][0]["slug"] == "bedrijfsinformatie"

        schema = (await c.get(_url(site, "/schema?post_type=page"), headers=member_h)).json()
        builder = schema["groups"][0]["fields"][1]
        assert builder["type"] == "repeater"
        assert builder["sub_fields"][1]["conditions"] == [
            [{"field": "type", "operator": "==", "value": "10"}]
        ]
        # Naming no subject is our 422, before the site is asked.
        res = await c.get(_url(site, "/schema"), headers=member_h)
        assert res.status_code == 422

        rows = (await c.get(_url(site, "/records?post_type=dienst"), headers=member_h)).json()
        assert rows["total"] == 1 and rows["items"][0]["title"] == "Hypotheekadvies"
        rows = (
            await c.get(_url(site, "/records?post_type=page&search=arbeids"), headers=member_h)
        ).json()
        assert [r["id"] for r in rows["items"]] == [8262]
        assert rows["items"][0]["path"] == ["Particulier", "Verzekeringen"]

        record = (
            await c.get(_url(site, "/records/8262?include_schema=true"), headers=member_h)
        ).json()
        assert record["fields"]["blokken_blokken"][0]["titel_0_1_2"] == "Eerst het risico begrijpen"
        assert record["references"]["attachments"]["7793"]["alt"] == "Adviesgesprek"
        assert record["seo"]["title"] == "AOV | Klant"
        assert record["schema"][0]["title"] == "Pagina-opbouw"
        assert ("GET", "/content/8262", None) in wp.bridge_calls

        res = await c.get(_url(site, "/records/424242"), headers=member_h)
        assert res.status_code == 404
        assert res.json()["error"]["message"] == "errors.wordpress_not_on_site"

        # An unknown post type is the plugin's 404 with its `known` list carried.
        res = await c.get(_url(site, "/records?post_type=nope"), headers=member_h)
        assert res.status_code == 404
        assert res.json()["error"]["details"]["known"] == ["page", "post", "dienst"]


# ---------------------------------------------------------------- writes and the audience


async def test_a_member_drafts_and_may_not_publish_or_edit_live(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-write")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        member_h = await _member(t, "wp-bridge-write", role="member")

        res = await c.post(
            _url(site, "/records"),
            json={"post_type": "dienst", "title": "Nieuwe dienst", "fields": {"kleur": "green"}},
            headers=member_h,
        )
        assert res.status_code == 201, res.text
        made = res.json()
        assert made["status"] == "draft" and made["fields"]["kleur"] == "green"

        # Publishing on create is `content.publish`, which a member does not hold.
        res = await c.post(
            _url(site, "/records"),
            json={"post_type": "dienst", "title": "Live", "status": "publish"},
            headers=member_h,
        )
        assert res.status_code == 403
        # Editing a draft is fine; editing the live page 8262 is not, whatever field.
        res = await c.patch(
            _url(site, f"/records/{made['id']}"),
            json={"title": "Nieuwe dienst 2"},
            headers=member_h,
        )
        assert res.status_code == 200, res.text
        assert res.json()["title"] == "Nieuwe dienst 2"
        res = await c.patch(_url(site, "/records/8262"), json={"title": "x"}, headers=member_h)
        assert res.status_code == 403
        # Nor setting the draft live.
        res = await c.patch(
            _url(site, f"/records/{made['id']}"), json={"status": "publish"}, headers=member_h
        )
        assert res.status_code == 403
        # The owner may, and the read that decided it went to the plugin first.
        res = await c.patch(
            _url(site, f"/records/{made['id']}"), json={"status": "publish"}, headers=owner_h
        )
        assert res.status_code == 200 and res.json()["status"] == "publish"
        assert ("GET", f"/content/{made['id']}", None) in wp.bridge_calls

        # Deleting has its own key: a member holds none of it, the owner trashes.
        res = await c.delete(_url(site, f"/records/{made['id']}"), headers=member_h)
        assert res.status_code == 403
        res = await c.delete(_url(site, f"/records/{made['id']}"), headers=owner_h)
        assert res.status_code == 200 and res.json()["trashed"] is True

        # Nothing at all to change is our 422, before the site is asked.
        res = await c.patch(_url(site, "/records/8262"), json={}, headers=owner_h)
        assert res.status_code == 422
        assert res.json()["error"]["message"] == "errors.nothing_to_update"

        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "wordpress_site", "entity_id": site["id"]},
                headers=owner_h,
            )
        ).json()
        actions = [row["action"] for row in trail]
        assert "content_created" in actions
        assert "content_updated" in actions
        assert "content_deleted" in actions
        created = next(row for row in trail if row["action"] == "content_created")
        assert created["payload"]["via"] == "breik-bridge"
        assert created["payload"]["title"] == "Nieuwe dienst"


async def test_a_validation_refusal_carries_every_problem_with_its_path(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-422")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        res = await c.patch(
            _url(site, "/records/8262"),
            json={
                "fields": {"kleur": "purple"},
                "ops": [{"op": "append", "path": "blokken_blokken", "value": {"type": "10"}}],
            },
            headers=headers,
        )
        assert res.status_code == 422, res.text
        err = res.json()["error"]
        assert err["message"] == "errors.wordpress_bridge_rejected"
        assert err["details"]["code"] == "validation_failed"
        problems = {p["path"]: p for p in err["details"]["problems"]}
        assert problems["kleur"]["details"]["choices"] == ["auto", "green", "blue"]
        assert problems["blokken_blokken[2].titel_0_1_2"]["code"] == "required"
        # Nothing was written: the record still reads as it did.
        assert wp.bridge_records[8262]["fields"]["kleur"] == "auto"
        assert len(wp.bridge_records[8262]["fields"]["blokken_blokken"]) == 2

        # And a correct op lands: one row merged, the rest untouched, the trail naming it.
        res = await c.patch(
            _url(site, "/records/8262"),
            json={
                "ops": [
                    {"op": "merge", "path": "blokken_blokken[0]", "value": {"titel_0_1_2": "Nieuw"}}
                ]
            },
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["fields"]["blokken_blokken"][0]["titel_0_1_2"] == "Nieuw"
        assert res.json()["fields"]["blokken_blokken"][1]["type"] == "15"
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "wordpress_site", "entity_id": site["id"]},
                headers=headers,
            )
        ).json()
        updated = next(row for row in trail if row["action"] == "content_updated")
        assert updated["payload"]["fields"] == ["ops×1"]


async def test_media_terms_options_and_menus(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-more")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        member_h = await _member(t, "wp-bridge-more", role="member")

        # Media: JSON in, attachment out, and the plugin's mime refusal is our 422.
        res = await c.post(
            _url(site, "/media"),
            json={
                "url": "https://cdn.example/hero.png",
                "alt": "Held",
                "attach_to": 8262,
                "set_featured": True,
            },
            headers=member_h,
        )
        assert res.status_code == 201, res.text
        assert res.json()["alt"] == "Held" and res.json()["id"] == 7801
        assert wp.bridge_records[8262]["featured_media"] == 7801
        res = await c.post(_url(site, "/media"), json={"alt": "no source"}, headers=member_h)
        assert res.status_code == 422
        res = await c.post(
            _url(site, "/media"), json={"base64": "AAAA", "filename": "x.php"}, headers=member_h
        )
        assert res.status_code == 422
        assert res.json()["error"]["details"]["code"] == "mime_not_allowed"

        # Terms.
        rows = (await c.get(_url(site, "/terms?taxonomy=faq_categories"), headers=member_h)).json()
        assert rows["total"] == 1 and rows["items"][0]["name"] == "AOV"
        res = await c.post(
            _url(site, "/terms"),
            json={"taxonomy": "faq_categories", "name": "Hypotheek"},
            headers=member_h,
        )
        assert res.status_code == 201 and res.json()["slug"] == "hypotheek"

        # Options: readable by a member, written only with `publish` (live, site-wide).
        pages = (await c.get(_url(site, "/options"), headers=member_h)).json()
        assert pages["items"][0]["slug"] == "bedrijfsinformatie"
        page = (await c.get(_url(site, "/options/bedrijfsinformatie"), headers=member_h)).json()
        assert page["fields"]["telefoon"] == "0113-123456"
        res = await c.patch(
            _url(site, "/options/bedrijfsinformatie"),
            json={"fields": {"telefoon": "1"}},
            headers=member_h,
        )
        assert res.status_code == 403
        res = await c.patch(
            _url(site, "/options/bedrijfsinformatie"),
            json={"fields": {"telefoon": "0113-654321"}},
            headers=owner_h,
        )
        assert res.status_code == 200 and res.json()["fields"]["telefoon"] == "0113-654321"
        res = await c.patch(_url(site, "/options/bedrijfsinformatie"), json={}, headers=owner_h)
        assert res.status_code == 422

        # Menus: the same split.
        menus = (await c.get(_url(site, "/menus"), headers=member_h)).json()
        assert menus["items"][0]["slug"] == "hoofdmenu"
        menu = (await c.get(_url(site, "/menus/hoofdmenu"), headers=member_h)).json()
        assert menu["items"][0]["title"] == "Home"
        res = await c.post(
            _url(site, "/menus/hoofdmenu/items"), json={"object_id": 8262}, headers=member_h
        )
        assert res.status_code == 403
        res = await c.post(
            _url(site, "/menus/hoofdmenu/items"),
            json={"object_id": 8262, "title": "AOV"},
            headers=owner_h,
        )
        assert res.status_code == 201 and res.json()["added"] == 502
        res = await c.delete(_url(site, "/menus/hoofdmenu/items/502"), headers=owner_h)
        assert res.status_code == 200 and res.json()["removed"] == 502

        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "wordpress_site", "entity_id": site["id"]},
                headers=owner_h,
            )
        ).json()
        actions = {row["action"] for row in trail}
        assert {"media_uploaded", "term_created", "options_updated", "menu_updated"} <= actions


# ---------------------------------------------------------------- WPML


async def test_wpml_refuses_cleanly_where_absent_and_translates_where_present(
    client_for, wp
) -> None:
    t = await make_tenant("wp-bridge-wpml")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        member_h = await _member(t, "wp-bridge-wpml", role="member")

        res = await c.get(_url(site, "/languages"), headers=member_h)
        assert res.status_code == 409
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_unavailable"
        assert res.json()["error"]["details"]["missing"] == "WPML"

        wp.multilingual = True
        langs = (await c.get(_url(site, "/languages"), headers=member_h)).json()
        assert [lang["code"] for lang in langs["languages"]] == ["nl", "en"]
        group = (await c.get(_url(site, "/records/8262/translations"), headers=member_h)).json()
        assert group["lang"] == "nl" and list(group["translations"]) == ["nl"]

        # A member creates the translation as a draft, with the translated fields on top.
        res = await c.post(
            _url(site, "/records/8262/translations"),
            json={"lang": "en", "title": "Disability insurance", "fields": {"kleur": "blue"}},
            headers=member_h,
        )
        assert res.status_code == 201, res.text
        made = res.json()
        assert made["lang"] == "en" and made["status"] == "draft" and made["created"] is True
        assert made["fields"]["kleur"] == "blue"
        # The copy carried the builder rows the caller did not resend.
        assert made["fields"]["blokken_blokken"][1]["type"] == "15"
        # `copy` reaches the plugin under its own name, not the model's.
        sent = next(
            b for m, p, b in wp.bridge_calls if m == "POST" and p == "/wpml/translations/8262"
        )
        assert "copy_source" not in sent

        # A second one is the plugin's 409, with the existing id in details.
        res = await c.post(
            _url(site, "/records/8262/translations"),
            json={"lang": "en", "title": "x"},
            headers=member_h,
        )
        assert res.status_code == 409
        assert res.json()["error"]["details"]["id"] == made["id"]
        # A live translation is `publish`.
        res = await c.post(
            _url(site, "/records/7813/translations"),
            json={"lang": "en", "title": "Insurance", "status": "publish"},
            headers=member_h,
        )
        assert res.status_code == 403
        # Connecting an existing record.
        res = await c.post(
            _url(site, "/records/7813/translations"),
            json={"lang": "en", "translation_id": 9001},
            headers=member_h,
        )
        assert res.status_code == 201, res.text
        assert res.json()["translations"]["en"]["id"] == 9001

        # Strings: read by a member, written with `publish`.
        rows = (await c.get(_url(site, "/strings"), headers=member_h)).json()
        assert rows["items"][0]["name"] == "Lees meer"
        res = await c.put(
            _url(site, "/strings"),
            json={"id": 7, "lang": "en", "value": "Read more"},
            headers=member_h,
        )
        assert res.status_code == 403
        res = await c.put(
            _url(site, "/strings"),
            json={"id": 7, "lang": "en", "value": "Read more"},
            headers=owner_h,
        )
        assert res.status_code == 200 and res.json()["updated"] is True


# ---------------------------------------------------------------- the boundaries


async def test_a_client_login_reaches_none_of_it(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-portal")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        headers = await _member(t, "wp-bridge-portal", role="client")
        for path in (
            "",
            "/schema?post_type=page",
            "/records?post_type=page",
            "/records/8262",
            "/options",
            "/menus",
            "/languages",
        ):
            res = await c.get(_url(site, path), headers=headers)
            assert res.status_code == 403, (path, res.text)


async def test_the_bridge_tools_ride_the_wordpress_section(client_for, wp) -> None:
    from app.core.mcp.sections import build_sections
    from app.core.mcp.server import _tool_index
    from app.main import app

    _, tool_paths = _tool_index(app)
    sections = build_sections(tool_paths)
    wordpress = sections["wordpress"]
    bridge = {
        name for name, path in tool_paths.items() if "/wordpress/sites/{site_id}/bridge" in path
    }
    assert len(bridge) == 22, sorted(bridge)
    assert bridge <= wordpress.tools
    assert {
        "bridge_info",
        "bridge_schema",
        "bridge_update_record",
        "bridge_upload_media",
        "bridge_translate",
    } <= wordpress.tools
