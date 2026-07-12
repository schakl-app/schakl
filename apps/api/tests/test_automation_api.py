"""automation module API coverage (issue #27): CRUD, validation, dry-run, runs, tenancy."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import auth_cookie, make_tenant


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_enqueue(org_id, run_id, *, requeue=False):  # noqa: ANN001, ANN202
        return True

    monkeypatch.setattr("app.modules.automation.queue.enqueue_run", fake_enqueue)


def _rule_body(**overrides) -> dict:
    body = {
        "name": "Escalate reviews",
        "trigger_event": "task.status_changed",
        "conditions": {"field": "to", "op": "eq", "value": "done"},
        "enabled": True,
        "actions": [
            {"action_type": "notification.send", "config": {"message": "Klaar!", "user_ids": []}},
            {
                "action_type": "webhook.post",
                "config": {"url": "https://x.example/h", "confirm": True},
            },
        ],
    }
    body.update(overrides)
    return body


async def test_requires_authentication(client_for) -> None:
    t = await make_tenant("auto-api-noauth")
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/automation/rules")).status_code == 401


async def test_rule_crud_roundtrip(client_for) -> None:
    t = await make_tenant("auto-api-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post("/api/v1/automation/rules", json=_rule_body(), headers=headers)
        assert created.status_code == 201
        rule = created.json()
        assert rule["trigger_event"] == "task.status_changed"
        assert [a["action_type"] for a in rule["actions"]] == [
            "notification.send",
            "webhook.post",
        ]
        assert [a["position"] for a in rule["actions"]] == [0, 1]

        listed = await c.get("/api/v1/automation/rules", headers=headers)
        assert [r["id"] for r in listed.json()] == [rule["id"]]

        # Patch: rename + disable, replace actions wholesale.
        patched = await c.patch(
            f"/api/v1/automation/rules/{rule['id']}",
            json={
                "name": "Renamed",
                "enabled": False,
                "actions": [{"action_type": "task.set_status", "config": {"status": "done"}}],
            },
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "Renamed"
        assert patched.json()["enabled"] is False
        assert [a["action_type"] for a in patched.json()["actions"]] == ["task.set_status"]

        # Patch without "actions" leaves the action list untouched.
        untouched = await c.patch(
            f"/api/v1/automation/rules/{rule['id']}", json={"enabled": True}, headers=headers
        )
        assert [a["action_type"] for a in untouched.json()["actions"]] == ["task.set_status"]

        deleted = await c.delete(f"/api/v1/automation/rules/{rule['id']}", headers=headers)
        assert deleted.status_code == 204
        assert (
            await c.get(f"/api/v1/automation/rules/{rule['id']}", headers=headers)
        ).status_code == 404


async def test_rule_validation(client_for) -> None:
    t = await make_tenant("auto-api-val")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Unknown trigger.
        r = await c.post(
            "/api/v1/automation/rules",
            json=_rule_body(trigger_event="galaxy.exploded"),
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["fields"]["trigger_event"] == "errors.automation_unknown_trigger"

        # Malformed condition tree (op that doesn't exist ≈ someone hunting for eval).
        r = await c.post(
            "/api/v1/automation/rules",
            json=_rule_body(conditions={"field": "x", "op": "eval", "value": "boom"}),
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["fields"]["conditions"] == "errors.automation_invalid_conditions"

        # Unknown action type.
        r = await c.post(
            "/api/v1/automation/rules",
            json=_rule_body(actions=[{"action_type": "nuke.launch", "config": {}}]),
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["fields"]["actions"] == "errors.automation_unknown_action"

        # A webhook action needs an http(s) URL up front, not at fire time.
        r = await c.post(
            "/api/v1/automation/rules",
            json=_rule_body(actions=[{"action_type": "webhook.post", "config": {"url": "ftp://x"}}]),
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["fields"]["actions"] == "errors.automation_webhook_invalid_url"


async def test_catalog_lists_triggers_and_actions(client_for) -> None:
    t = await make_tenant("auto-api-cat")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        catalog = (await c.get("/api/v1/automation/catalog", headers=headers)).json()
        events = {trig["event"] for trig in catalog["triggers"]}
        assert {"task.created", "task.status_changed", "company.created",
                "website.uptime_toggled", "domain.status_changed"} <= events
        assert set(catalog["actions"]) == {
            "task.create", "task.set_status", "task.assign", "notification.send", "webhook.post",
        }


async def test_dry_run_evaluates_and_executes_nothing(client_for) -> None:
    t = await make_tenant("auto-api-dry")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Dryco"}, headers=headers)
        ).json()

        body = {
            "trigger_event": "company.status_changed",
            "conditions": {"field": "name", "op": "eq", "value": "Dryco"},
            "actions": [{"action_type": "task.create", "config": {"title": "Onboard"}}],
            "entity_id": company["id"],
        }
        result = (await c.post("/api/v1/automation/dry-run", json=body, headers=headers)).json()
        assert result == {"matched": True, "would_fire": ["task.create"], "snapshot_found": True}

        body["conditions"] = {"field": "name", "op": "eq", "value": "Other"}
        result = (await c.post("/api/v1/automation/dry-run", json=body, headers=headers)).json()
        assert result["matched"] is False and result["would_fire"] == []

        # Nothing executed: no run recorded, no task created.
        runs = (await c.get("/api/v1/automation/runs", headers=headers)).json()
        assert runs["total"] == 0
        tasks = (await c.get("/api/v1/tasks", headers=headers)).json()
        assert tasks["total"] == 0

        # A payload sample beats the snapshot, like a real trigger's from/to would.
        body["conditions"] = {"field": "to", "op": "eq", "value": "active"}
        body["payload"] = {"to": "active"}
        result = (await c.post("/api/v1/automation/dry-run", json=body, headers=headers)).json()
        assert result["matched"] is True


async def test_runs_list_and_rule_filter(client_for) -> None:
    t = await make_tenant("auto-api-runs")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        rule_a = (
            await c.post(
                "/api/v1/automation/rules",
                json=_rule_body(
                    name="A", trigger_event="task.created", conditions={},
                    actions=[{"action_type": "task.set_status", "config": {"status": "done"}}],
                ),
                headers=headers,
            )
        ).json()
        rule_b = (
            await c.post(
                "/api/v1/automation/rules",
                json=_rule_body(
                    name="B", trigger_event="task.created", conditions={},
                    actions=[{"action_type": "task.set_status", "config": {"status": "done"}}],
                ),
                headers=headers,
            )
        ).json()

        created = await c.post("/api/v1/tasks", json={"title": "Trigger me"}, headers=headers)
        assert created.status_code == 201

        page = (await c.get("/api/v1/automation/runs", headers=headers)).json()
        assert page["total"] == 2
        assert {run["rule_id"] for run in page["items"]} == {rule_a["id"], rule_b["id"]}
        assert all(run["status"] == "pending" for run in page["items"])

        filtered = (
            await c.get(f"/api/v1/automation/runs?rule_id={rule_a['id']}", headers=headers)
        ).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["rule_name"] == "A"


async def test_api_is_tenant_scoped(client_for) -> None:
    a = await make_tenant("auto-api-iso-a")
    b = await make_tenant("auto-api-iso-b")
    headers_a = await auth_cookie(a.user)
    headers_b = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        rule = (
            await ca.post(
                "/api/v1/automation/rules",
                json=_rule_body(
                    trigger_event="task.created", conditions={},
                    actions=[{"action_type": "task.set_status", "config": {"status": "done"}}],
                ),
                headers=headers_a,
            )
        ).json()
        await ca.post("/api/v1/tasks", json={"title": "A's"}, headers=headers_a)

    async with client_for(b.host) as cb:
        # B sees none of A's rules or runs, and cannot address them by id.
        assert (await cb.get("/api/v1/automation/rules", headers=headers_b)).json() == []
        assert (await cb.get("/api/v1/automation/runs", headers=headers_b)).json()["total"] == 0
        assert (
            await cb.get(f"/api/v1/automation/rules/{rule['id']}", headers=headers_b)
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/automation/rules/{rule['id']}", headers=headers_b)
        ).status_code == 404

    async with client_for(a.host) as ca:
        assert (await ca.get("/api/v1/automation/runs", headers=headers_a)).json()["total"] == 1


async def test_member_without_grants_is_denied(client_for) -> None:
    t = await make_tenant("auto-api-deny", role="client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/automation/rules", headers=headers)).status_code == 403
        assert (
            await c.post("/api/v1/automation/rules", json=_rule_body(), headers=headers)
        ).status_code == 403
        assert (await c.get("/api/v1/automation/runs", headers=headers)).status_code == 403


async def test_dry_run_rejects_unknown_entity_honestly(client_for) -> None:
    t = await make_tenant("auto-api-dry404")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        body = {
            "trigger_event": "task.created",
            "conditions": {"field": "status", "op": "eq", "value": "open"},
            "actions": [],
            "entity_id": str(uuid.uuid4()),
        }
        result = (await c.post("/api/v1/automation/dry-run", json=body, headers=headers)).json()
        # No row → payload-only evaluation, and the response says so instead of pretending.
        assert result["snapshot_found"] is False
        assert result["matched"] is False
