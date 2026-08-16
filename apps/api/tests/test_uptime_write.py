"""Gate 2: profiles, the write-back to Uptime Kuma, groups, drift and reconcile."""

from __future__ import annotations

import pytest

from app.integrations.uptime import client as kuma_client
from app.integrations.uptime import profiles as prof
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


# --------------------------------------------------------------- resolution (unit)


def test_the_three_layers_do_not_fuse() -> None:
    """Built-ins, then the tenant's profile, then this monitor. Each overrides the one before."""

    class P:
        defaults = {"interval_seconds": 300, "retries": 5}

    assert prof.resolve({}, None)["interval_seconds"] == 60
    assert prof.resolve({}, P())["interval_seconds"] == 300
    assert prof.resolve({"interval_seconds": 120}, P())["interval_seconds"] == 120
    # None means inherit, never clear — an unfilled form field falls back to the profile.
    assert prof.resolve({"interval_seconds": None}, P())["interval_seconds"] == 300


def test_invariants_are_applied_last() -> None:
    """A tenant cannot configure their way under a floor the far end will refuse anyway."""

    class P:
        defaults = {"interval_seconds": 1, "retries": 99}

    resolved = prof.resolve({}, P())
    assert resolved["interval_seconds"] == prof.MIN_INTERVAL_SECONDS
    assert resolved["retries"] == prof.MAX_RETRIES


def test_a_profile_can_never_carry_a_target() -> None:
    """A profile that can set a URL is a profile that can point forty monitors at the wrong
    host in one save."""
    cleaned = prof.profile_defaults_input({"interval_seconds": 90, "url": "https://evil.nl"})
    assert cleaned == {"interval_seconds": 90}
    assert "url" not in prof.PROFILE_KEYS and "target" not in prof.PROFILE_KEYS


def test_resolving_to_no_profile_falls_back_rather_than_dropping_settings() -> None:
    """Reporting already paid for this once: a template that resolved to none silently threw
    four settings away, so nobody's design, accent or cover photo was ever used."""

    class P:
        def __init__(self, **kw):
            self.__dict__.update(
                {"active": True, "is_default": False, "monitor_type": "http",
                 "position": 0, "created_at": 0} | kw
            )

    only = P(monitor_type="http")
    assert prof.pick_profile([only], "http", None) is only
    # The first profile of a type is its default, even unticked.
    assert prof.pick_profile([only], "http", None).monitor_type == "http"
    assert prof.pick_profile([], "http", None) is None


# ------------------------------------------------------------------- profiles


async def test_profile_crud_and_one_default_per_type(client_for, kuma) -> None:
    t = await make_tenant("uptime-profiles")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        a = (
            await c.post(
                "/api/v1/uptime/profiles",
                json={"name": "Standaard", "defaults": {"interval_seconds": 120},
                      "is_default": True},
                headers=headers,
            )
        ).json()
        b = (
            await c.post(
                "/api/v1/uptime/profiles",
                json={"name": "SLA", "defaults": {"interval_seconds": 30}, "is_default": True},
                headers=headers,
            )
        ).json()
        listed = (await c.get("/api/v1/uptime/profiles", headers=headers)).json()
        defaults = [p["id"] for p in listed if p["is_default"]]
        assert defaults == [b["id"]], "two defaults for one monitor type"
        assert a["defaults"] == {"interval_seconds": 120}


# -------------------------------------------------------------------- writing


async def test_create_pushes_to_kuma_with_the_resolved_settings(client_for, kuma) -> None:
    t = await make_tenant("uptime-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        await c.post(
            "/api/v1/uptime/profiles",
            json={"name": "Standaard", "defaults": {"interval_seconds": 300}, "is_default": True},
            headers=headers,
        )
        created = await c.post(
            "/api/v1/uptime/monitors",
            json={
                "instance_id": instance_id,
                "name": "klant.nl",
                "monitor_type": "http",
                "target": "https://klant.nl",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["sync_status"] == "active"
        assert body["kuma_monitor_id"] is not None
        assert body["adopted"] is False

        remote = kuma.monitors[body["kuma_monitor_id"]]
        assert remote["url"] == "https://klant.nl"
        assert remote["interval"] == 300, "the profile's default never reached Kuma"


async def test_an_edit_preserves_what_kuma_holds_and_we_do_not_model(client_for, kuma) -> None:
    """The 119-versus-16 problem, end to end: a payload rebuilt from known fields would reset
    a hundred keys, including every field of a monitor type this module has never heard of."""
    t = await make_tenant("uptime-edit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        created = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "site", "monitor_type": "http",
                      "target": "https://a.nl"},
                headers=headers,
            )
        ).json()
        kuma_id = created["kuma_monitor_id"]
        # Something only Kuma knows about, set outside schakl.
        kuma.monitors[kuma_id]["some_future_field"] = "keep me"
        kuma.monitors[kuma_id]["basic_auth_pass"] = "pw"

        await c.patch(
            f"/api/v1/uptime/monitors/{created['id']}",
            json={"name": "renamed", "interval_seconds": 180},
            headers=headers,
        )
        remote = kuma.monitors[kuma_id]
        assert remote["name"] == "renamed" and remote["interval"] == 180
        assert remote["some_future_field"] == "keep me", "a blind write reset an unknown field"
        assert remote["basic_auth_pass"] == "pw", "the edit destroyed a credential we never held"


async def test_a_group_and_its_child_are_pushed_with_the_parent_link(client_for, kuma) -> None:
    t = await make_tenant("uptime-groups")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        group = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "Klant X", "monitor_type": "group"},
                headers=headers,
            )
        ).json()
        child = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "site", "monitor_type": "http",
                      "target": "https://x.nl", "parent_id": group["id"]},
                headers=headers,
            )
        ).json()
        assert kuma.monitors[child["kuma_monitor_id"]]["parent"] == group["kuma_monitor_id"]
        # A group watches nothing: sending it a target is how a group acquires a URL nobody meant.
        assert not kuma.monitors[group["kuma_monitor_id"]].get("url")


async def test_a_failed_push_leaves_a_retryable_row_not_an_orphan(client_for, kuma) -> None:
    """The local row is written first on purpose: a failed push leaves something an admin can
    retry, rather than a monitor at Kuma that schakl has no record of."""
    t = await make_tenant("uptime-pushfail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        kuma.unreachable = True
        failed = await c.post(
            "/api/v1/uptime/monitors",
            json={"instance_id": instance_id, "name": "site", "monitor_type": "http",
                  "target": "https://a.nl"},
            headers=headers,
        )
        assert failed.status_code == 502
        assert failed.json()["error"]["message"] == "errors.uptime_unreachable"


async def test_pause_and_resume_are_not_drift(client_for, kuma) -> None:
    t = await make_tenant("uptime-pause")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        m = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "site", "monitor_type": "http",
                      "target": "https://a.nl"},
                headers=headers,
            )
        ).json()
        paused = (
            await c.post(f"/api/v1/uptime/monitors/{m['id']}/pause", headers=headers)
        ).json()
        assert paused["active"] is False
        assert kuma.monitors[m["kuma_monitor_id"]]["active"] is False

        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        after = (await c.get(f"/api/v1/uptime/monitors/{m['id']}", headers=headers)).json()
        assert after["sync_status"] != "drift", "a paused monitor read as a configuration conflict"
        assert after["drift_fields"] == []


# ----------------------------------------------------------------------- drift


async def test_an_edit_in_kuma_becomes_drift_with_the_fields_named(client_for, kuma) -> None:
    t = await make_tenant("uptime-drift")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        m = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "site", "monitor_type": "http",
                      "target": "https://a.nl", "interval_seconds": 60},
                headers=headers,
            )
        ).json()
        # Somebody edits it in Uptime Kuma's own UI, which is the normal case.
        kuma.monitors[m["kuma_monitor_id"]]["interval"] = 900
        kuma.monitors[m["kuma_monitor_id"]]["url"] = "https://elders.nl"

        report = (
            await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        ).json()
        assert report["drifted"] == 1
        after = (await c.get(f"/api/v1/uptime/monitors/{m['id']}", headers=headers)).json()
        assert after["sync_status"] == "drift"
        assert sorted(after["drift_fields"]) == ["interval_seconds", "target"]
        # Reported, never silently absorbed: our record still says what we decided.
        assert after["interval_seconds"] == 60 and after["target"] == "https://a.nl"


async def test_an_adopted_monitor_never_drifts_on_its_first_sync(client_for, kuma) -> None:
    """It has no intent of its own yet, so the observed state simply is the truth. Reading it
    as drift would put every monitor of a freshly adopted instance into a conflict queue."""
    kuma.add(name="found", url="https://found.nl", interval=1234)
    t = await make_tenant("uptime-adopt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        report = (
            await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)
        ).json()
        assert report["drifted"] == 0
        item = (await c.get("/api/v1/uptime/monitors", headers=headers)).json()["items"][0]
        assert item["adopted"] is True and item["sync_status"] == "active"
        assert item["interval_seconds"] == 1234


async def test_reconcile_adopt_takes_kumas_side(client_for, kuma) -> None:
    t = await make_tenant("uptime-adopt-dir")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        m = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "site", "monitor_type": "http",
                      "target": "https://a.nl", "interval_seconds": 60},
                headers=headers,
            )
        ).json()
        kuma.monitors[m["kuma_monitor_id"]]["interval"] = 900
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)

        resolved = (
            await c.post(
                f"/api/v1/uptime/monitors/{m['id']}/reconcile",
                json={"direction": "adopt"},
                headers=headers,
            )
        ).json()
        assert resolved["interval_seconds"] == 900
        assert resolved["sync_status"] == "active" and resolved["drift_fields"] == []
        assert kuma.monitors[m["kuma_monitor_id"]]["interval"] == 900, "adopt wrote to Kuma"


async def test_reconcile_push_takes_ours(client_for, kuma) -> None:
    t = await make_tenant("uptime-push-dir")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        m = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "site", "monitor_type": "http",
                      "target": "https://a.nl", "interval_seconds": 60},
                headers=headers,
            )
        ).json()
        kuma.monitors[m["kuma_monitor_id"]]["interval"] = 900
        await c.post(f"/api/v1/uptime/instances/{instance_id}/sync", headers=headers)

        resolved = (
            await c.post(
                f"/api/v1/uptime/monitors/{m['id']}/reconcile",
                json={"direction": "push"},
                headers=headers,
            )
        ).json()
        assert resolved["interval_seconds"] == 60 and resolved["drift_fields"] == []
        assert kuma.monitors[m["kuma_monitor_id"]]["interval"] == 60


async def test_reconcile_has_no_default_direction(client_for, kuma) -> None:
    """One overwrites a colleague's edit in Uptime Kuma, the other overwrites schakl's record.
    Picking either silently would be making the tenant's decision for them."""
    t = await make_tenant("uptime-nodir")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        m = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "s", "monitor_type": "http",
                      "target": "https://a.nl"},
                headers=headers,
            )
        ).json()
        r = await c.post(
            f"/api/v1/uptime/monitors/{m['id']}/reconcile", json={}, headers=headers
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------- delete


async def test_delete_leaves_kuma_alone_unless_asked(client_for, kuma) -> None:
    """"Stop tracking this here" and "stop watching this client's site" are different
    decisions, and the destructive one is never a side effect of the other."""
    t = await make_tenant("uptime-del")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        keep = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "keep", "monitor_type": "http",
                      "target": "https://a.nl"},
                headers=headers,
            )
        ).json()
        drop = (
            await c.post(
                "/api/v1/uptime/monitors",
                json={"instance_id": instance_id, "name": "drop", "monitor_type": "http",
                      "target": "https://b.nl"},
                headers=headers,
            )
        ).json()

        await c.delete(f"/api/v1/uptime/monitors/{keep['id']}", headers=headers)
        assert keep["kuma_monitor_id"] in kuma.monitors, "a local delete removed it at Kuma"

        await c.delete(
            f"/api/v1/uptime/monitors/{drop['id']}?at_kuma=true", headers=headers
        )
        assert drop["kuma_monitor_id"] not in kuma.monitors


# ------------------------------------------------------ creating from a website (#366)


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


async def test_a_monitor_created_from_a_website_gets_that_websites_client(
    client_for, kuma
) -> None:
    """The create path derives `company_id` exactly as the update and link paths do (#366).

    Posting only `website_id` used to land the row at `company_id IS NULL` — visible to staff
    outside that client's group and invisible to the client whose site it watches (§285, #266).
    Nothing on the screen would have said so, which is what makes this worth pinning rather than
    leaving to the panel that happens to post it.
    """
    t = await make_tenant("uptime-create-anchor")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        company = await _company(c, headers, "Klant")
        domain = await _domain(c, headers, "klant.nl", company)
        website = await _website(c, headers, domain)

        created = await c.post(
            "/api/v1/uptime/monitors",
            json={
                "instance_id": instance_id,
                "name": "klant.nl",
                "monitor_type": "http",
                "target": "https://klant.nl",
                # Deliberately no `company_id`: the panel does not post one, because two copies
                # of "whose monitor is this" is how the horizon starts disagreeing with the row.
                "website_id": website,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["website_id"] == website
        assert body["company_id"] == company
        # One anchor, never three: the others stay empty rather than being carried along.
        assert body["domain_id"] is None and body["hosting_id"] is None


async def test_a_monitor_created_from_a_domain_gets_the_domains_client(client_for, kuma) -> None:
    """The domain page posts the other anchor, and it resolves through the same ladder."""
    t = await make_tenant("uptime-create-domain")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        company = await _company(c, headers, "Klant")
        domain = await _domain(c, headers, "klant.nl", company)

        created = await c.post(
            "/api/v1/uptime/monitors",
            json={
                "instance_id": instance_id,
                "name": "mail.klant.nl",
                "monitor_type": "ping",
                "target": "mail.klant.nl",
                "domain_id": domain,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["company_id"] == company
        assert created.json()["website_id"] is None


async def test_creating_against_another_tenants_anchor_is_a_404(client_for, kuma) -> None:
    """The create route stands behind the same fence as `link_monitor` (§15, Golden Rule 1).

    A 404 and not a 403: a refusal must not confirm that the row exists. Without this the create
    route would be the one way past a gate the other two write paths already hold.
    """
    other = await make_tenant("uptime-create-other")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as c:
        company = await _company(c, other_headers, "Andermans klant")
        domain = await _domain(c, other_headers, "andermans.nl", company)
        foreign_website = await _website(c, other_headers, domain)

    t = await make_tenant("uptime-create-mine")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        instance_id = await _connected(c, headers)
        refused = await c.post(
            "/api/v1/uptime/monitors",
            json={
                "instance_id": instance_id,
                "name": "Van iemand anders",
                "monitor_type": "http",
                "target": "https://andermans.nl",
                "website_id": foreign_website,
            },
            headers=headers,
        )
        assert refused.status_code == 404, refused.text


async def test_the_instance_picker_reads_on_monitor_read_and_hides_the_credential(
    client_for, kuma
) -> None:
    """The create form's picker (#366).

    Readable on `monitor.read` for `list_profiles`' reason — a form gated on a permission the
    create route does not require is a picker its holder cannot populate (#310) — and carrying no
    fact about the credential, which stays behind `instance.manage`.
    """
    t = await make_tenant("uptime-selectable")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        managed = await _connected(c, headers)
        # A `linked` instance holds no credential by definition, so nothing can ever be pushed
        # to it: offering it would be a control that can only refuse (#253).
        linked = (
            await c.post(
                "/api/v1/uptime/instances",
                json={"name": "Klant-Kuma", "mode": "linked"},
                headers=headers,
            )
        ).json()["id"]

        options = await c.get("/api/v1/uptime/instances/selectable", headers=headers)
        assert options.status_code == 200, options.text
        by_id = {o["id"]: o for o in options.json()}
        assert by_id[managed]["writable"] is True
        assert by_id[linked]["writable"] is False
        # The literal segment is matched as itself, not read as an id.
        assert set(by_id[managed]) == {"id", "name", "mode", "writable"}
