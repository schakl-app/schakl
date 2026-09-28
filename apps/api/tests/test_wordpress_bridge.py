"""The schakl WordPress MCP Bridge plugin as a surface (docs/WORDPRESS.md §9).

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
* **The theme's files have keys of their own**, admin only, and neither the passthrough nor an
  ability is a way around them.
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


async def test_the_probe_records_whether_the_plugin_can_update_itself(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-updates")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        verify = f"/api/v1/wordpress/sites/{site['id']}/verify"
        row_url = f"/api/v1/wordpress/sites/{site['id']}"

        # A plugin older than 1.3.1 does not say: unknown, not "cannot".
        res = await c.post(verify, headers=headers)
        assert res.json()["bridge_updates"] is None

        wp.bridge_version = "1.3.1"
        wp.bridge_updates = {
            "token": "none",
            "installed": "1.3.1",
            "latest": None,
            "available": False,
            "error": "No GitHub token configured.",
            "auto_update": False,
        }
        res = await c.post(verify, headers=headers)
        updates = res.json()["bridge_updates"]
        assert updates["token"] == "none" and updates["can_update"] is False
        assert updates["error"] == "No GitHub token configured."
        assert (await c.get(row_url, headers=headers)).json()["bridge_updates"] == updates

        # A token GitHub accepts: it can, and says what it found.
        wp.bridge_updates = {
            "token": "setting",
            "installed": "1.3.1",
            "latest": "1.4.0",
            "available": True,
            "error": None,
            "auto_update": True,
            "secret": "never stored",
        }
        info = (await c.get(_url(site), headers=headers)).json()
        assert info["updates"]["token"] == "setting"
        row = (await c.get(row_url, headers=headers)).json()["bridge_updates"]
        assert row["can_update"] is True and row["latest"] == "1.4.0"
        assert row["available"] is True and row["auto_update"] is True
        assert "secret" not in row

        # The plugin gone clears it with the version.
        wp.has_bridge = False
        res = await c.post(verify, headers=headers)
        assert res.json()["bridge_updates"] is None


async def test_the_probe_records_where_theme_editing_stands(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-theme-row")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        verify = f"/api/v1/wordpress/sites/{site['id']}/verify"
        row_url = f"/api/v1/wordpress/sites/{site['id']}"

        # A plugin older than 1.5.0 does not say: unknown, not "closed".
        res = await c.post(verify, headers=headers)
        assert res.json()["bridge_theme"] is None
        assert (await c.get(row_url, headers=headers)).json()["bridge_theme"] is None

        # Switched off: the site's own switch is named, and no loopback was asked.
        wp.bridge_version = "1.5.0"
        wp.bridge_theme = {
            "active": {"stylesheet": "klant", "name": "Klant", "writable": True},
            "read": True,
            "editing": {
                "allowed": False, "php": False, "reason": "Switched off in the bridge settings.",
                "setting": False, "php_setting": True, "config": None,
                "loopback": None, "loopback_checked_at": None, "loopback_error": None,
            },
        }
        res = await c.post(verify, headers=headers)
        theme = res.json()["bridge_theme"]
        assert theme["stylesheet"] == "klant" and theme["writable"] is True
        assert theme["editing"] is False and theme["setting"] is False
        assert theme["php"] is False and theme["loopback"] is None
        assert theme["reason"] == "Switched off in the bridge settings."
        assert (await c.get(row_url, headers=headers)).json()["bridge_theme"] == theme
        listed = (await c.get("/api/v1/wordpress/sites", headers=headers)).json()
        assert [r["bridge_theme"] for r in listed if r["id"] == site["id"]] == [theme]

        # Switched on since, on a site that cannot reach itself: reading the plugin's info is
        # an observation too, and the row follows it without a probe.
        wp.bridge_theme["editing"] = {
            "allowed": True, "php": False,
            "reason": "The site cannot check itself for fatal errors: timed out",
            "setting": True, "php_setting": True, "config": None,
            "loopback": False, "loopback_checked_at": "2026-09-28T12:00:00+02:00",
            "loopback_error": "timed out", "secret": "never stored",
        }
        info = (await c.get(_url(site), headers=headers)).json()
        assert info["theme"]["editing"]["allowed"] is True
        row = (await c.get(row_url, headers=headers)).json()["bridge_theme"]
        assert row["editing"] is True and row["php"] is False
        assert row["loopback"] is False and row["loopback_error"] == "timed out"
        assert "secret" not in row and "loopback_checked_at" not in row

        wp.bridge_theme["editing"] |= {"php": True, "reason": None, "loopback": True}
        res = await c.post(verify, headers=headers)
        assert res.json()["bridge_theme"]["php"] is True
        assert res.json()["bridge_theme"]["reason"] is None

        # The plugin gone clears it with the version.
        wp.has_bridge = False
        res = await c.post(verify, headers=headers)
        assert res.json()["bridge_theme"] is None


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
            assert body["details"]["plugin"] == "schakl-wordpress-mcp-bridge"


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

        # The text mode: what a chatbot reads — no tree, the words in order.
        text = (await c.get(_url(site, "/records/8262?mode=text"), headers=member_h)).json()
        assert text["fields_mode"] == "text" and text["fields"] is None
        assert text["text"].startswith("Arbeidsongeschiktheidsverzekering")
        assert "Eerst het risico begrijpen" in text["text"]
        assert ("GET", "/content/8262", None) in wp.bridge_calls
        rows = (
            await c.get(_url(site, "/records?post_type=page&fields=text"), headers=member_h)
        ).json()
        assert rows["fields_mode"] == "text"
        assert all("Eerst het risico" in r["text"] or r["text"] for r in rows["items"])
        assert rows["items"][0]["fields"] is None

        # One term's records: the FAQ items of one category, not the whole type.
        in_term = (
            await c.get(
                _url(site, "/records?post_type=page&taxonomy=faq_categories&term=aov"),
                headers=member_h,
            )
        ).json()
        assert [r["id"] for r in in_term["items"]] == [8262]
        assert wp.bridge_queries[-1][2]["taxonomy"] == "faq_categories"
        assert wp.bridge_queries[-1][2]["term"] == "aov"
        # A term without its taxonomy names nothing, and is refused here rather than ignored there.
        res = await c.get(_url(site, "/records?post_type=page&term=aov"), headers=member_h)
        assert res.status_code == 422
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_term_pair"
        assert res.json()["error"]["fields"] == {"taxonomy": "errors.wordpress_bridge_term_pair"}

        res = await c.get(_url(site, "/records/8262?mode=prose"), headers=member_h)
        assert res.status_code == 422

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
        assert created["payload"]["via"] == "schakl-wordpress-mcp-bridge"
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

        # Editing menus (plugin 1.3.0): an item in place, the order, the menu itself.
        res = await c.patch(
            _url(site, "/menus/hoofdmenu/items/501"), json={"title": "Start"}, headers=member_h
        )
        assert res.status_code == 403
        res = await c.patch(_url(site, "/menus/hoofdmenu/items/501"), json={}, headers=owner_h)
        assert res.status_code == 422
        res = await c.patch(
            _url(site, "/menus/hoofdmenu/items/501"), json={"url": "https://x"}, headers=owner_h
        )
        assert res.status_code == 422
        assert res.json()["error"]["details"]["type"] == "post_type"
        res = await c.patch(
            _url(site, "/menus/hoofdmenu/items/501"),
            json={"title": "Start", "target": True},
            headers=owner_h,
        )
        assert res.status_code == 200 and res.json()["updated"] == 501, res.text
        assert res.json()["items"][0]["title"] == "Start"
        assert res.json()["items"][0]["url"] == "https://klant.nl/", "what was not sent is kept"
        assert wp.writes[-1][1] == {"title": "Start", "target": True}, "no unset field is sent"

        res = await c.post(
            _url(site, "/menus/hoofdmenu/items"),
            json={"url": "https://klant.nl/contact", "title": "Contact"},
            headers=owner_h,
        )
        contact = res.json()["added"]
        res = await c.put(
            _url(site, "/menus/hoofdmenu/order"), json={"order": [9]}, headers=owner_h
        )
        assert res.status_code == 422
        assert res.json()["error"]["details"]["siblings"] == [501, contact]
        res = await c.put(
            _url(site, "/menus/hoofdmenu/order"), json={"order": [contact]}, headers=owner_h
        )
        assert res.status_code == 200
        assert [i["id"] for i in res.json()["items"]] == [contact, 501]

        res = await c.post(_url(site, "/menus"), json={"name": "Footer"}, headers=member_h)
        assert res.status_code == 403
        res = await c.post(
            _url(site, "/menus"), json={"name": "Footer", "locations": ["nope"]}, headers=owner_h
        )
        assert res.status_code == 422 and res.json()["error"]["details"]["known"] == ["primary"]
        res = await c.post(
            _url(site, "/menus"), json={"name": "Footer", "locations": ["primary"]}, headers=owner_h
        )
        assert res.status_code == 201, res.text
        footer = res.json()
        assert footer["created"] is True and footer["items"] == []
        assert footer["locations"] == ["primary"]
        menus = (await c.get(_url(site, "/menus"), headers=member_h)).json()
        assert menus["locations"] == [{"slug": "primary", "label": "Primary", "menu": footer["id"]}]
        res = await c.patch(
            _url(site, "/menus/footer"),
            json={"name": "Voettekst", "locations": []},
            headers=owner_h,
        )
        assert res.status_code == 200, res.text
        assert res.json()["slug"] == "voettekst" and res.json()["locations"] == []
        res = await c.delete(_url(site, "/menus/voettekst"), headers=member_h)
        assert res.status_code == 403
        res = await c.delete(_url(site, "/menus/voettekst"), headers=owner_h)
        assert res.status_code == 200, res.text
        assert res.json()["deleted"] is True and res.json()["items_removed"] == 0
        res = await c.get(_url(site, "/menus/voettekst"), headers=member_h)
        assert res.status_code == 404

        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "wordpress_site", "entity_id": site["id"]},
                headers=owner_h,
            )
        ).json()
        actions = {row["action"] for row in trail}
        assert {"media_uploaded", "term_created", "options_updated", "menu_updated"} <= actions
        assert {"menu_created", "menu_deleted"} <= actions


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
    assert len(bridge) == 43, sorted(bridge)
    assert bridge <= wordpress.tools
    assert {
        "bridge_info",
        "bridge_schema",
        "bridge_update_record",
        "bridge_upload_media",
        "bridge_translate",
        "bridge_forms",
        "bridge_update_form",
        "bridge_translate_form",
    } <= wordpress.tools


# ---------------------------------------------------------------- Contact Form 7


async def test_forms_read_write_and_delete_through_the_plugin(client_for, wp) -> None:
    """The plugin's forms.* (bridge 1.2.0): a member reads, `forms.write` writes — live, so it
    sits with publish — and deleting has its own key. Mail and messages merge; the plugin's
    refusals (an unknown message key, no Contact Form 7) carry through with their details."""
    t = await make_tenant("wp-bridge-forms")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        member_h = await _member(t, "wp-bridge-forms", role="member")

        rows = (await c.get(_url(site, "/forms"), headers=member_h)).json()
        assert rows["total"] == 1
        assert rows["items"][0]["shortcode"].startswith('[contact-form-7 id="a1b2c3d"')
        form = (await c.get(_url(site, "/forms/6584"), headers=member_h)).json()
        assert [f["name"] for f in form["fields"]] == ["your-name", "your-email"]
        assert form["mail"]["recipient"] == "info@klant.nl"
        assert form["messages_help"]["mail_sent_ok"] == "Sent"
        assert form["config_errors"] == {}
        assert form["strings"] is None  # no WPML on this site

        # A member holds `forms.read` only.
        res = await c.post(_url(site, "/forms"), json={"title": "Offerte"}, headers=member_h)
        assert res.status_code == 403
        res = await c.patch(_url(site, "/forms/6584"), json={"title": "x"}, headers=member_h)
        assert res.status_code == 403

        # A title alone makes a form, live, with its shortcode.
        res = await c.post(_url(site, "/forms"), json={"title": "Offerte"}, headers=owner_h)
        assert res.status_code == 201, res.text
        made = res.json()
        assert made["created"] is True and made["shortcode"].startswith("[contact-form-7 ")
        assert made["mail"]["active"] is True

        # One mail key changes and the rest survives; the trail names what was touched.
        res = await c.patch(
            _url(site, f"/forms/{made['id']}"),
            json={"mail": {"recipient": "sales@klant.nl"}, "messages": {"mail_sent_ok": "Dank."}},
            headers=owner_h,
        )
        assert res.status_code == 200, res.text
        assert res.json()["mail"]["recipient"] == "sales@klant.nl"
        assert res.json()["mail"]["subject"] == "[_site_title]"
        assert res.json()["messages"] == {"mail_sent_ok": "Dank."}
        sent = next(
            b for m, p, b in wp.bridge_calls if m == "PATCH" and p == f"/forms/{made['id']}"
        )
        assert sent == {
            "mail": {"recipient": "sales@klant.nl"},
            "messages": {"mail_sent_ok": "Dank."},
        }

        # The plugin's refusal of an unknown message key is our 422 with its details.
        res = await c.patch(
            _url(site, "/forms/6584"), json={"messages": {"nope": "x"}}, headers=owner_h
        )
        assert res.status_code == 422
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_rejected"
        assert res.json()["error"]["details"]["unknown"] == ["nope"]
        # Nothing to change is our 422, before the site is asked.
        res = await c.patch(_url(site, "/forms/6584"), json={}, headers=owner_h)
        assert res.status_code == 422
        assert res.json()["error"]["message"] == "errors.nothing_to_update"

        # Deleting is its own key: the owner holds it, a member does not.
        res = await c.delete(_url(site, f"/forms/{made['id']}"), headers=member_h)
        assert res.status_code == 403
        res = await c.delete(_url(site, f"/forms/{made['id']}"), headers=owner_h)
        assert res.status_code == 200 and res.json()["deleted"] is True
        res = await c.get(_url(site, f"/forms/{made['id']}"), headers=owner_h)
        assert res.status_code == 404

        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "wordpress_site", "entity_id": site["id"]},
                headers=owner_h,
            )
        ).json()
        by_action = {row["action"]: row for row in trail}
        assert by_action["form_created"]["payload"]["via"] == "schakl-wordpress-mcp-bridge"
        assert by_action["form_updated"]["payload"]["fields"] == ["mail", "messages"]
        assert by_action["content_deleted"]["payload"]["type"] == "wpcf7_contact_form"

        # No Contact Form 7: the plugin's 409 `unavailable`, named.
        wp.has_forms = False
        res = await c.get(_url(site, "/forms"), headers=member_h)
        assert res.status_code == 409
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_unavailable"
        assert res.json()["error"]["details"]["missing"] == "Contact Form 7"


async def test_forms_translate_both_ways_wpml_knows_them(client_for, wp) -> None:
    """WPML two ways: strings on one form (the Contact Form 7 Multilingual add-on) travel on
    the update; a linked form per language is `translate`, refused with a pointer where the
    site does not translate forms as records."""
    t = await make_tenant("wp-bridge-forms-wpml")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)

        # Without WPML, strings are the plugin's 409.
        res = await c.patch(
            _url(site, "/forms/6584"), json={"strings": {"en": {"Form": "x"}}}, headers=owner_h
        )
        assert res.status_code == 409
        assert res.json()["error"]["details"]["missing"] == "WPML"

        wp.multilingual = True
        form = (await c.get(_url(site, "/forms/6584"), headers=owner_h)).json()
        assert form["strings"]["items"][0]["name"] == "Form"
        res = await c.patch(
            _url(site, "/forms/6584"),
            json={"strings": {"en": {"Form": "[text* your-name]"}}},
            headers=owner_h,
        )
        assert res.status_code == 200, res.text
        assert res.json()["translated"][0]["lang"] == "en"
        strings = res.json()["strings"]["items"]
        assert strings[0]["translations"]["en"]["value"] == "[text* your-name]"
        # One form serves every language here: translate refuses and says so.
        res = await c.post(
            _url(site, "/forms/6584/translations"), json={"lang": "en"}, headers=owner_h
        )
        assert res.status_code == 409
        assert res.json()["error"]["details"]["missing"] == "Form translation"

        # Where forms are a translatable post type, a linked copy is made in the language.
        wp.forms_translatable = True
        res = await c.post(
            _url(site, "/forms/6584/translations"),
            json={"lang": "en", "title": "Contact form", "messages": {"mail_sent_ok": "Thanks."}},
            headers=owner_h,
        )
        assert res.status_code == 201, res.text
        made = res.json()
        assert made["lang"] == "en" and made["created"] is True
        assert made["messages"]["mail_sent_ok"] == "Thanks."
        assert made["messages"]["validation_error"] == "Controleer."  # copied from the source
        assert made["source"] == {"id": 6584, "lang": "nl"}
        assert made["shortcode"] != form["shortcode"]
        # `copy` reaches the plugin under its own name, not the model's.
        sent = next(
            b for m, p, b in wp.bridge_calls if m == "POST" and p == "/forms/6584/translate"
        )
        assert "copy_source" not in sent
        group = (await c.get(_url(site, "/forms/6584"), headers=owner_h)).json()["translations"]
        assert group == {"nl": 6584, "en": made["id"]}
        # A second one is the plugin's 409 with the existing id.
        res = await c.post(
            _url(site, "/forms/6584/translations"), json={"lang": "en"}, headers=owner_h
        )
        assert res.status_code == 409
        assert res.json()["error"]["details"]["id"] == made["id"]

        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "wordpress_site", "entity_id": site["id"]},
                headers=owner_h,
            )
        ).json()
        translated = next(row for row in trail if row["action"] == "translation_created")
        assert translated["payload"]["translation"] == made["id"]
        assert translated["payload"]["lang"] == "en"


# ---------------------------------------------------------------- the theme's files, the caches


async def test_theme_files_are_read_and_written_on_keys_of_their_own(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-theme")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        member_h = await _member(t, "wp-bridge-theme", role="member")

        # A theme is code: reading it is not a member's by default, writing it even less.
        for path in ("/theme", "/theme/files", "/theme/file?path=style.css", "/theme/file/history"):
            res = await c.get(_url(site, path), headers=member_h)
            assert res.status_code == 403, (path, res.text)
        res = await c.put(
            _url(site, "/theme/file"),
            json={"path": "style.css", "edits": [{"old_string": "#111", "new_string": "#222"}]},
            headers=member_h,
        )
        assert res.status_code == 403
        assert wp.writes == []

        info = (await c.get(_url(site, "/theme?refresh=true"), headers=owner_h)).json()
        assert info["active"]["stylesheet"] == "klant"
        assert info["editing"]["allowed"] is True
        assert info["editing"]["php"]["loopback"] == {"ok": True, "refreshed": True}

        found = (
            await c.get(_url(site, "/theme/files?search=COLOR&extensions=css"), headers=owner_h)
        ).json()
        assert [r["path"] for r in found["items"]] == ["style.css"]
        assert found["items"][0]["matches"] == [{"line": 4, "text": "body { color: #111; }"}]
        assert wp.bridge_queries[-1][2] == {
            "limit": "500", "recursive": "true", "extensions": "css", "search": "COLOR",
        }

        css = (await c.get(_url(site, "/theme/file?path=style.css"), headers=owner_h)).json()
        assert "#111" in css["content"] and len(css["hash"]) == 64
        res = await c.get(_url(site, "/theme/file?path=../wp-config.php"), headers=owner_h)
        assert res.status_code == 422
        assert res.json()["error"]["details"]["code"] == "invalid_input"
        res = await c.get(_url(site, "/theme/file?path=nope.css"), headers=owner_h)
        assert res.status_code == 404

        # Edits, carried as sent; the answer names the kept version.
        edit = {"path": "style.css", "edits": [{"old_string": "#111", "new_string": "#222"}]}
        res = await c.put(_url(site, "/theme/file"), json=edit, headers=owner_h)
        assert res.status_code == 200, res.text
        assert res.json()["revision"] == 900 and res.json()["hash"] != css["hash"]
        assert wp.bridge_calls[-1] == ("PUT", "/theme/file", edit)
        assert "#222" in wp.theme_files["style.css"]

        # The plugin's refusals are carried: an edit that does not apply, a stale hash.
        res = await c.put(_url(site, "/theme/file"), json=edit, headers=owner_h)
        assert res.status_code == 422
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_rejected"
        assert res.json()["error"]["details"]["problems"] == [
            {"path": "edits[0]", "code": "not_found"}
        ]
        res = await c.put(
            _url(site, "/theme/file"),
            json={"path": "style.css", "content": "a{}", "expected_hash": css["hash"]},
            headers=owner_h,
        )
        assert res.status_code == 409
        assert res.json()["error"]["details"]["code"] == "conflict"
        assert res.json()["error"]["details"]["written"] is False

        # Ours to refuse, before the site is asked: both, or neither.
        calls = len(wp.bridge_calls)
        for body in (
            {"path": "style.css"},
            {"path": "style.css", "content": "a{}", "edits": edit["edits"]},
        ):
            res = await c.put(_url(site, "/theme/file"), json=body, headers=owner_h)
            assert res.status_code == 422, res.text
            assert res.json()["error"]["message"] == "errors.wordpress_theme_edits_or_content"
        assert len(wp.bridge_calls) == calls

        # PHP: what does not parse is never written, what kills the site is put back.
        for marker, code in (("SYNTAX", "php_syntax_error"), ("FATAL", "php_error")):
            res = await c.put(
                _url(site, "/theme/file"),
                json={
                    "path": "functions.php",
                    "edits": [{"old_string": "'one'", "new_string": f"'{marker}'"}],
                    "check_urls": ["/contact/"],
                },
                headers=owner_h,
            )
            assert res.status_code == 422, res.text
            assert res.json()["error"]["details"]["code"] == code
        assert res.json()["error"]["details"]["rolled_back"] is True
        assert "'one'" in wp.theme_files["functions.php"]
        assert wp.bridge_calls[-1][2]["check_urls"] == ["/contact/"]

        # Create, delete, history, restore.
        res = await c.post(
            _url(site, "/theme/file"),
            json={"path": "template-parts/card.php", "content": "<?php // card", "purge": False},
            headers=owner_h,
        )
        assert res.status_code == 201, res.text
        assert res.json()["created"] is True and res.json()["caches"] is None
        assert wp.bridge_calls[-1][2] == {
            "path": "template-parts/card.php", "content": "<?php // card", "purge": False,
        }
        res = await c.post(
            _url(site, "/theme/file"),
            json={"path": "template-parts/card.php", "content": ""},
            headers=owner_h,
        )
        assert res.status_code == 409

        res = await c.delete(
            _url(site, "/theme/file?path=template-parts/card.php"), headers=owner_h
        )
        assert res.status_code == 200, res.text
        assert res.json()["deleted"] is True
        deleted = res.json()["revision"]
        assert "template-parts/card.php" not in wp.theme_files

        history = (
            await c.get(
                _url(site, "/theme/file/history?path=template-parts/card.php"), headers=owner_h
            )
        ).json()
        assert [r["before"] for r in history["items"]] == ["delete", "create"]
        one = (
            await c.get(_url(site, f"/theme/file/history?revision={deleted}"), headers=owner_h)
        ).json()
        assert one["content"] == "<?php // card"

        res = await c.post(
            _url(site, "/theme/file/restore"), json={"revision": deleted}, headers=owner_h
        )
        assert res.status_code == 200, res.text
        assert wp.theme_files["template-parts/card.php"] == "<?php // card"

        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=wordpress_site&entity_id={site['id']}",
                headers=owner_h,
            )
        ).json()
        theme_lines = [
            (r["action"], r["payload"]["path"])
            for r in trail
            if r["action"].startswith("theme_file_")
        ]
        assert sorted(theme_lines) == [
            ("theme_file_created", "template-parts/card.php"),
            ("theme_file_deleted", "template-parts/card.php"),
            ("theme_file_restored", "template-parts/card.php"),
            ("theme_file_updated", "style.css"),
        ]
        updated = next(r["payload"] for r in trail if r["action"] == "theme_file_updated")
        assert updated["revision"] == 900 and updated["edits"] == 1
        assert updated["via"] == "schakl-wordpress-mcp-bridge"


async def test_a_site_that_keeps_its_theme_closed_says_so_as_a_state(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-theme-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, headers)
        edit = {"path": "style.css", "edits": [{"old_string": "#111", "new_string": "#222"}]}

        # The switch is off: a 409 that names it — not "the credential was refused", which
        # would send an admin to re-mint a password that was never wrong.
        wp.theme_editing = False
        res = await c.put(_url(site, "/theme/file"), json=edit, headers=headers)
        assert res.status_code == 409, res.text
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_switched_off"
        assert res.json()["error"]["details"]["setting"] == "theme_editing"
        assert "#111" in wp.theme_files["style.css"]
        # Reading stays open.
        res = await c.get(_url(site, "/theme/file?path=style.css"), headers=headers)
        assert res.status_code == 200

        # The site cannot request its own pages: PHP is closed, a stylesheet is not.
        wp.theme_editing, wp.theme_loopback = True, False
        res = await c.put(
            _url(site, "/theme/file"),
            json={
                "path": "functions.php",
                "edits": [{"old_string": "'one'", "new_string": "'two'"}],
            },
            headers=headers,
        )
        assert res.status_code == 409, res.text
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_unavailable"
        assert res.json()["error"]["details"]["code"] == "loopback_unavailable"
        res = await c.put(_url(site, "/theme/file"), json=edit, headers=headers)
        assert res.status_code == 200, res.text

        # An older plugin has no theme routes at all.
        wp.has_bridge = False
        res = await c.get(_url(site, "/theme"), headers=headers)
        assert res.status_code == 409
        assert res.json()["error"]["message"] == "errors.wordpress_bridge_missing"


async def test_the_caches_are_listed_and_emptied(client_for, wp) -> None:
    t = await make_tenant("wp-bridge-cache")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        member_h = await _member(t, "wp-bridge-cache", role="member")

        info = (await c.get(_url(site, "/cache"), headers=member_h)).json()
        assert [p["id"] for p in info["providers"]] == ["litespeed"]

        # Emptying is felt by visitors: `publish`, which a member does not hold.
        res = await c.post(_url(site, "/cache/purge"), json={}, headers=member_h)
        assert res.status_code == 403
        assert wp.writes == []

        res = await c.post(_url(site, "/cache/purge"), json={}, headers=owner_h)
        assert res.status_code == 200, res.text
        assert res.json()["requested"] == ["page", "assets", "opcache"]
        assert wp.bridge_calls[-1] == ("POST", "/cache/purge", {})

        res = await c.post(
            _url(site, "/cache/purge"),
            json={"what": ["page"], "urls": ["/contact/"]},
            headers=owner_h,
        )
        assert res.json()["purged"] == [{"kind": "page", "provider": "litespeed", "scope": "urls"}]
        res = await c.post(
            _url(site, "/cache/purge"), json={"what": ["everything"]}, headers=owner_h
        )
        assert res.status_code == 422

        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=wordpress_site&entity_id={site['id']}",
                headers=owner_h,
            )
        ).json()
        kinds = sorted(
            tuple(r["payload"]["kinds"]) for r in trail if r["action"] == "cache_purged"
        )
        assert kinds == [("assets", "opcache", "page"), ("page",)]


async def test_the_passthrough_and_abilities_are_no_way_around_the_theme_keys(
    client_for, wp
) -> None:
    from app.integrations.wordpress.surface import _names_theme_write as names

    assert names("schakl/v1/theme/file", None)
    assert names("Schakl/V1/Theme/file/restore", {})
    assert names(
        "schakl/v1/mcp", {"method": "tools/call", "params": {"name": "theme_files_update"}}
    )
    assert names("mcp/agency-server", {"params": {"name": "schakl-bridge-theme-files-create"}})
    assert names("wp-abilities/v1/abilities/schakl-bridge/theme-files-delete/run", {"input": {}})
    assert not names("schakl/v1/mcp", {"method": "tools/call", "params": {"name": "content_get"}})
    assert not names("schakl/v1/cache/purge", {"what": ["page"]})
    assert not names("litespeed/v1/purge", {"all": True})

    t = await make_tenant("wp-bridge-theme-doors")
    owner_h = await auth_cookie(t.user)
    wp.extra_abilities.append({
        "name": "schakl-bridge/theme-files-get", "label": "Read a theme file",
        "description": "", "category": "schakl-bridge", "input_schema": {}, "output_schema": {},
        "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
    })
    async with client_for(t.host) as c:
        _, site = await _site(c, owner_h)
        member_h = await _member(t, "wp-bridge-theme-doors", role="member")
        base = f"/api/v1/wordpress/sites/{site['id']}"

        # `rest.read` reads any plugin route — but not a client's theme source.
        body = {"path": "schakl/v1/theme/file", "params": {"path": "functions.php"}}
        res = await c.post(f"{base}/rest", json=body, headers=member_h)
        assert res.status_code == 403, res.text
        res = await c.post(f"{base}/rest", json=body, headers=owner_h)
        assert res.status_code == 200, res.text
        assert "klant_value" in res.json()["data"]["content"]
        # The rest of the plugin's namespace is what it was.
        res = await c.post(f"{base}/rest", json={"path": "schakl/v1/cache"}, headers=member_h)
        assert res.status_code == 200, res.text

        # A read-only ability is `ability.read`, which a member holds — the theme's is not.
        runs = len(wp.ability_runs)
        res = await c.post(
            f"{base}/abilities/run",
            json={"name": "schakl-bridge/theme-files-get", "input": {"path": "functions.php"}},
            headers=member_h,
        )
        assert res.status_code == 403, res.text
        assert len(wp.ability_runs) == runs
        res = await c.post(
            f"{base}/abilities/run",
            json={"name": "schakl-bridge/theme-files-get", "input": {"path": "functions.php"}},
            headers=owner_h,
        )
        assert res.status_code == 200, res.text
