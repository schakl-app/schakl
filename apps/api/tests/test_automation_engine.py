"""The rule engine end to end, without a live worker (issue #27, #96).

The trigger side is driven through the real event bus (the same ``emit`` the modules call);
the execution side calls ``executor.execute_run`` directly in-process — the exact function
the ARQ job wraps — so nothing here needs Redis. ``queue.enqueue_run`` is stubbed to a
recorder: what the request side *would* enqueue is asserted, not fired.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from app.core.auth.models import User
from app.core.events import SystemContext, emit
from app.core.permissions.permset import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.modules.automation import webhook
from app.modules.automation.engine import MAX_CHAIN_DEPTH
from app.modules.automation.executor import execute_run
from app.modules.automation.models import (
    RUN_FAILED,
    RUN_PENDING,
    RUN_SKIPPED,
    RUN_SUCCEEDED,
    AutomationAction,
    AutomationRule,
    AutomationRun,
)
from app.modules.notifications.models import Notification, NotificationEvent
from app.modules.tasks.models import Task, TaskActivity
from tests.conftest import Tenant, make_tenant

#: A public address (example.com) so the SSRF guard passes for stubbed hosts.
_PUBLIC_ADDR = "93.184.216.34"


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Record enqueues instead of talking to Redis; tests execute runs in-process."""
    enqueued: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def fake_enqueue(org_id, run_id, *, requeue=False):  # noqa: ANN001, ANN202
        enqueued.append((org_id, run_id))
        return True

    monkeypatch.setattr("app.modules.automation.queue.enqueue_run", fake_enqueue)
    return enqueued


@asynccontextmanager
async def _ctx(tenant: Tenant, user: User | None = None):
    """A tenant-bound context; with a user it mimics a request, without it a cron/system."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        if user is None:
            yield SystemContext(org=tenant.org, session=session)
        else:
            yield RequestContext(
                user=user,
                org=tenant.org,
                session=session,
                permissions=PermissionSet.of(["*"]),
            )
        await session.commit()


async def _make_rule(
    tenant: Tenant,
    *,
    trigger_event: str,
    actions: list[tuple[str, dict]],
    conditions: dict | None = None,
    enabled: bool = True,
    name: str = "Test rule",
) -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        rule = AutomationRule(
            org_id=tenant.org.id,
            name=name,
            trigger_event=trigger_event,
            conditions=conditions or {},
            enabled=enabled,
            position=0,
        )
        session.add(rule)
        await session.flush()
        for index, (action_type, config) in enumerate(actions):
            session.add(
                AutomationAction(
                    org_id=tenant.org.id,
                    rule_id=rule.id,
                    action_type=action_type,
                    config=config,
                    position=index,
                )
            )
        await session.commit()
        return rule.id


async def _runs(tenant: Tenant, status: str | None = None) -> list[AutomationRun]:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        stmt = select(AutomationRun).where(AutomationRun.org_id == tenant.org.id)
        if status is not None:
            stmt = stmt.where(AutomationRun.status == status)
        return list((await session.execute(stmt.order_by(AutomationRun.created_at))).scalars())


async def _execute_all_pending(tenant: Tenant, max_rounds: int = 10) -> None:
    """Drain the queue the way the worker would — runs may spawn chained runs."""
    for _ in range(max_rounds):
        pending = await _runs(tenant, RUN_PENDING)
        if not pending:
            return
        for run in pending:
            await execute_run(tenant.org.id, run.id)
    raise AssertionError("pending runs never drained — a chain is not terminating")


async def _make_task(tenant: Tenant, title: str = "A task", **kwargs) -> uuid.UUID:
    from app.modules.tasks.schemas import TaskCreate
    from app.modules.tasks.service import TaskService

    async with _ctx(tenant, tenant.user) as ctx:
        task = await TaskService(ctx).create(TaskCreate(title=title, **kwargs))
        return task.id


# --------------------------------------------------------------------------- #
# Matching, conditions, dedup, depth
# --------------------------------------------------------------------------- #
async def test_rule_matches_and_records_pending_run(_no_redis) -> None:
    tenant = await make_tenant("auto-match")
    rule_id = await _make_rule(
        tenant, trigger_event="task.created", actions=[("task.set_status", {"status": "done"})]
    )
    task_id = await _make_task(tenant)

    runs = await _runs(tenant)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == RUN_PENDING
    assert run.rule_id == rule_id
    assert run.entity_type == "task"
    assert run.entity_id == task_id
    assert run.depth == 0
    assert run.payload["title"] == "A task"
    assert "_recipients" not in run.payload  # routing keys never persist
    assert _no_redis == [(tenant.org.id, run.id)]


async def test_conditions_gate_on_entity_snapshot() -> None:
    tenant = await make_tenant("auto-cond")
    await _make_rule(
        tenant,
        trigger_event="task.created",
        conditions={"field": "priority", "op": "eq", "value": "high"},
        actions=[("task.set_status", {"status": "in_progress"})],
    )
    await _make_task(tenant, title="calm")  # normal priority — snapshot says so
    assert await _runs(tenant) == []
    await _make_task(tenant, title="loud", priority="high")
    assert len(await _runs(tenant)) == 1


async def test_disabled_rule_never_fires() -> None:
    tenant = await make_tenant("auto-off")
    await _make_rule(
        tenant,
        trigger_event="task.created",
        actions=[("task.set_status", {"status": "done"})],
        enabled=False,
    )
    await _make_task(tenant)
    assert await _runs(tenant) == []


async def test_same_event_twice_is_one_run() -> None:
    tenant = await make_tenant("auto-dedup")
    await _make_rule(
        tenant, trigger_event="website.uptime_toggled", actions=[("webhook.post", {"url": "https://x.example/hook"})]
    )
    payload = {"website_id": uuid.uuid4(), "uptime": True}
    async with _ctx(tenant) as ctx:
        await emit("website.uptime_toggled", ctx, dict(payload))
    async with _ctx(tenant) as ctx:  # the retry
        await emit("website.uptime_toggled", ctx, dict(payload))
    assert len(await _runs(tenant)) == 1
    # A genuinely different occurrence still fires.
    async with _ctx(tenant) as ctx:
        await emit(
            "website.uptime_toggled", ctx, {"website_id": payload["website_id"], "uptime": False}
        )
    assert len(await _runs(tenant)) == 2


async def test_chain_depth_caps_and_is_visible() -> None:
    """task.created → task.create → task.created → … stops at MAX_CHAIN_DEPTH, as a
    *skipped* run in the log rather than an infinite loop or a silent drop."""
    tenant = await make_tenant("auto-depth")
    await _make_rule(
        tenant,
        trigger_event="task.created",
        actions=[("task.create", {"title": "chained"})],
        name="Chain reactor",
    )
    await _make_task(tenant, title="seed")
    await _execute_all_pending(tenant)

    runs = await _runs(tenant)
    by_depth = sorted(run.depth for run in runs)
    assert by_depth == list(range(MAX_CHAIN_DEPTH + 1))  # 0, 1, 2, 3
    executed = [run for run in runs if run.status == RUN_SUCCEEDED]
    skipped = [run for run in runs if run.status == RUN_SKIPPED]
    assert len(executed) == MAX_CHAIN_DEPTH
    assert len(skipped) == 1
    assert skipped[0].depth == MAX_CHAIN_DEPTH
    assert skipped[0].error == "errors.automation_depth_exceeded"


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
async def test_action_task_set_status_and_activity_actor(_no_redis) -> None:
    tenant = await make_tenant("auto-status")
    await _make_rule(
        tenant,
        trigger_event="task.created",
        actions=[("task.set_status", {"status": "in_progress"})],
        name="Kickstart",
    )
    task_id = await _make_task(tenant)
    await _execute_all_pending(tenant)

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        task = await session.get(Task, task_id)
        assert task.status == "in_progress"
        activity = (
            await session.execute(
                select(TaskActivity).where(
                    TaskActivity.task_id == task_id, TaskActivity.action == "status_changed"
                )
            )
        ).scalar_one()
        # The system acted; the rule's name is the accountable actor (§16).
        assert activity.actor_user_id is None
        assert activity.actor_name == "Kickstart"

    run = (await _runs(tenant))[0]
    assert run.status == RUN_SUCCEEDED
    assert run.steps[0]["action_type"] == "task.set_status"
    assert run.steps[0]["status"] == "succeeded"
    assert run.started_at is not None and run.finished_at is not None


async def test_action_task_assign() -> None:
    tenant = await make_tenant("auto-assign")
    await _make_rule(
        tenant,
        trigger_event="task.status_changed",
        conditions={"field": "to", "op": "eq", "value": "done"},
        actions=[("task.assign", {"user_id": str(tenant.user.id)})],
    )
    task_id = await _make_task(tenant)
    from app.modules.tasks.schemas import TaskUpdate
    from app.modules.tasks.service import TaskService

    async with _ctx(tenant, tenant.user) as ctx:
        await TaskService(ctx).update(task_id, TaskUpdate(status="done"))
    await _execute_all_pending(tenant)

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        task = await session.get(Task, task_id)
        assert task.assignee_user_id == tenant.user.id


async def test_action_task_create_from_template() -> None:
    tenant = await make_tenant("auto-tmpl")
    # A template with two items, applied to the triggering company.
    from app.modules.tasks.models import TaskTemplate, TaskTemplateItem

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        template = TaskTemplate(org_id=tenant.org.id, name="Onboarding", trigger="manual")
        session.add(template)
        await session.flush()
        for i, title in enumerate(["Kick-off call", "Collect assets"]):
            session.add(
                TaskTemplateItem(
                    org_id=tenant.org.id, template_id=template.id, title=title, position=i
                )
            )
        await session.commit()
        template_id = template.id

    await _make_rule(
        tenant,
        trigger_event="company.created",
        actions=[("task.create", {"template_id": str(template_id)})],
    )
    from app.modules.companies.schemas import CompanyCreate
    from app.modules.companies.service import CompanyService

    async with _ctx(tenant, tenant.user) as ctx:
        company = await CompanyService(ctx).create(CompanyCreate(name="Acme"))
        company_id = company.id
    await _execute_all_pending(tenant)

    run = next(r for r in await _runs(tenant) if r.trigger_event == "company.created")
    assert run.status == RUN_SUCCEEDED
    assert len(run.steps[0]["result"]["task_ids"]) == 2
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        titles = {
            t.title
            for t in (
                await session.execute(select(Task).where(Task.company_id == company_id))
            ).scalars()
        }
    assert titles == {"Kick-off call", "Collect assets"}


async def test_action_notification_send() -> None:
    tenant = await make_tenant("auto-notify")
    await _make_rule(
        tenant,
        trigger_event="company.created",
        actions=[
            ("notification.send", {"message": "Nieuwe klant!", "user_ids": [str(tenant.user.id)]})
        ],
        name="Welcome bell",
    )
    from app.modules.companies.schemas import CompanyCreate
    from app.modules.companies.service import CompanyService

    async with _ctx(tenant, tenant.user) as ctx:
        await CompanyService(ctx).create(CompanyCreate(name="Bellco"))
    await _execute_all_pending(tenant)

    runs = await _runs(tenant)
    assert [r.status for r in runs] == [RUN_SUCCEEDED]
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        event = (
            await session.execute(
                select(NotificationEvent).where(
                    NotificationEvent.event_type == "automation.notify"
                )
            )
        ).scalar_one()
        assert event.payload["message"] == "Nieuwe klant!"
        assert event.payload["rule"] == "Welcome bell"
        assert event.actor_user_id is None  # the system speaks
        inbox = (
            await session.execute(
                select(Notification).where(Notification.event_id == event.id)
            )
        ).scalars().all()
        assert [n.user_id for n in inbox] == [tenant.user.id]


# --------------------------------------------------------------------------- #
# webhook.post + the #96 confirmation recipes
# --------------------------------------------------------------------------- #
def _stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str) -> list[str]:
        return [_PUBLIC_ADDR]

    monkeypatch.setattr(webhook, "_resolve_addrs", resolve)


def _stub_send(monkeypatch: pytest.MonkeyPatch, status_code: int, body):  # noqa: ANN001
    calls: list[dict] = []

    async def send(url: str, payload: dict):  # noqa: ANN202
        calls.append({"url": url, "body": payload})
        return status_code, body

    monkeypatch.setattr(webhook, "_send", send)
    return calls


async def _uptime_rule(tenant: Tenant, *, confirm: bool) -> uuid.UUID:
    return await _make_rule(
        tenant,
        trigger_event="website.uptime_toggled",
        actions=[("webhook.post", {"url": "https://flows.example.com/uptime", "confirm": confirm})],
        name="Uptime → Kuma",
    )


async def _toggle_uptime(tenant: Tenant, uptime: bool) -> uuid.UUID:
    """The event the websites module (#94) will emit; the engine is ready for it today."""
    website_id = uuid.uuid4()
    async with _ctx(tenant) as ctx:
        await emit(
            "website.uptime_toggled", ctx, {"website_id": website_id, "uptime": uptime}
        )
    return website_id


async def test_webhook_confirm_ok_true_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await make_tenant("auto-hook-ok")
    await _uptime_rule(tenant, confirm=True)
    website_id = await _toggle_uptime(tenant, True)
    _stub_dns(monkeypatch)
    calls = _stub_send(monkeypatch, 200, {"ok": True})
    await _execute_all_pending(tenant)

    run = (await _runs(tenant))[0]
    assert run.status == RUN_SUCCEEDED
    assert run.steps[0]["result"] == {"status_code": 200, "confirmed": True}
    # The #96 contract: {event, entity_type, entity_id, payload}.
    assert calls[0]["body"] == {
        "event": "website.uptime_toggled",
        "entity_type": "website",
        "entity_id": str(website_id),
        "payload": {"website_id": str(website_id), "uptime": True},
    }


async def test_webhook_confirm_ok_false_fails_run(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await make_tenant("auto-hook-nok")
    await _uptime_rule(tenant, confirm=True)
    await _toggle_uptime(tenant, True)
    _stub_dns(monkeypatch)
    _stub_send(monkeypatch, 200, {"ok": False})
    await _execute_all_pending(tenant)

    run = (await _runs(tenant))[0]
    assert run.status == RUN_FAILED
    assert run.error == "errors.automation_webhook_not_confirmed"
    assert run.steps[0]["status"] == "failed"
    assert run.steps[0]["result"]["status_code"] == 200


async def test_webhook_confirm_non_2xx_fails_run(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await make_tenant("auto-hook-500")
    await _uptime_rule(tenant, confirm=True)
    await _toggle_uptime(tenant, True)
    _stub_dns(monkeypatch)
    _stub_send(monkeypatch, 500, None)
    await _execute_all_pending(tenant)
    run = (await _runs(tenant))[0]
    assert run.status == RUN_FAILED
    assert run.steps[0]["result"]["status_code"] == 500


async def test_webhook_without_confirm_records_status_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await make_tenant("auto-hook-fire")
    await _uptime_rule(tenant, confirm=False)
    await _toggle_uptime(tenant, True)
    _stub_dns(monkeypatch)
    _stub_send(monkeypatch, 500, None)
    await _execute_all_pending(tenant)
    run = (await _runs(tenant))[0]
    assert run.status == RUN_SUCCEEDED  # fire-and-forget: dispatched is delivered
    assert run.steps[0]["result"] == {"status_code": 500}


async def test_webhook_refuses_private_target(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await make_tenant("auto-hook-ssrf")
    await _uptime_rule(tenant, confirm=False)
    await _toggle_uptime(tenant, True)

    async def resolve(host: str) -> list[str]:
        return ["192.168.1.10"]

    monkeypatch.setattr(webhook, "_resolve_addrs", resolve)
    sent = _stub_send(monkeypatch, 200, {"ok": True})
    await _execute_all_pending(tenant)

    run = (await _runs(tenant))[0]
    assert run.status == RUN_FAILED
    assert run.error == "errors.automation_webhook_private_target"
    assert sent == []  # refused before any request left the box

    # The documented LAN escape hatch: the operator explicitly allows private targets.
    monkeypatch.setattr(
        "app.config.settings.allow_private_notification_targets", True
    )
    await _toggle_uptime(tenant, False)
    await _execute_all_pending(tenant)
    second = (await _runs(tenant))[1]
    assert second.status == RUN_SUCCEEDED
    assert len(sent) == 1


async def test_domain_status_changed_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """#96's redirect recipe: domain status=redirect + a condition, → webhook."""
    tenant = await make_tenant("auto-domain")
    await _make_rule(
        tenant,
        trigger_event="domain.status_changed",
        conditions={"field": "to", "op": "eq", "value": "redirect"},
        actions=[("webhook.post", {"url": "https://flows.example.com/redirect", "confirm": True})],
    )
    domain_id = uuid.uuid4()
    async with _ctx(tenant) as ctx:
        await emit(
            "domain.status_changed", ctx, {"domain_id": domain_id, "from": "active", "to": "parked"}
        )
    assert await _runs(tenant) == []  # condition filters the non-redirect transition

    async with _ctx(tenant) as ctx:
        await emit(
            "domain.status_changed",
            ctx,
            {"domain_id": domain_id, "from": "active", "to": "redirect"},
        )
    _stub_dns(monkeypatch)
    _stub_send(monkeypatch, 200, {"ok": True})
    await _execute_all_pending(tenant)
    run = (await _runs(tenant))[0]
    assert run.status == RUN_SUCCEEDED


# --------------------------------------------------------------------------- #
# Failure isolation + idempotent execution
# --------------------------------------------------------------------------- #
async def test_failing_action_stops_chain_and_keeps_earlier_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await make_tenant("auto-fail")
    await _make_rule(
        tenant,
        trigger_event="task.created",
        actions=[
            ("task.set_status", {"status": "in_progress"}),
            ("task.assign", {}),  # missing user_id → config error
            ("task.set_status", {"status": "done"}),  # never reached
        ],
    )
    task_id = await _make_task(tenant)
    await _execute_all_pending(tenant)

    run = (await _runs(tenant))[0]
    assert run.status == RUN_FAILED
    assert [s["status"] for s in run.steps] == ["succeeded", "failed"]
    assert run.error == "errors.automation_action_config_invalid"
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        task = await session.get(Task, task_id)
        assert task.status == "in_progress"  # step 1 kept, step 3 never ran


async def test_execute_run_is_idempotent() -> None:
    tenant = await make_tenant("auto-idem")
    await _make_rule(
        tenant, trigger_event="task.created", actions=[("task.create", {"title": "one copy"})]
    )
    # Condition-less rule also fires on the chained task.created; cap keeps it finite, and
    # the double-execute below must not add copies beyond the chain's own.
    await _make_task(tenant, title="seed")
    first = (await _runs(tenant))[0]
    assert await execute_run(tenant.org.id, first.id) == RUN_SUCCEEDED
    assert await execute_run(tenant.org.id, first.id) == "not-claimed"

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        copies = (
            await session.execute(select(Task).where(Task.title == "one copy"))
        ).scalars().all()
        assert len(copies) == 1  # the status guard, not the queue, prevents the double fire


async def test_unknown_action_type_fails_cleanly() -> None:
    tenant = await make_tenant("auto-unknown")
    await _make_rule(
        tenant, trigger_event="task.created", actions=[("teleport.everything", {})]
    )
    await _make_task(tenant)
    await _execute_all_pending(tenant)
    run = (await _runs(tenant))[0]
    assert run.status == RUN_FAILED
    assert run.error == "errors.automation_unknown_action"


# --------------------------------------------------------------------------- #
# Tenant isolation (Golden Rule 1)
# --------------------------------------------------------------------------- #
async def test_rules_never_fire_for_another_tenants_events() -> None:
    a = await make_tenant("auto-iso-a")
    b = await make_tenant("auto-iso-b")
    await _make_rule(
        a, trigger_event="task.created", actions=[("task.set_status", {"status": "done"})]
    )

    await _make_task(b, title="B's task")
    assert await _runs(a) == []
    assert await _runs(b) == []  # B has no rules; A's rule is not B's

    await _make_task(a, title="A's task")
    assert len(await _runs(a)) == 1


async def test_runs_and_rules_invisible_cross_tenant() -> None:
    a = await make_tenant("auto-iso-c")
    b = await make_tenant("auto-iso-d")
    await _make_rule(
        a, trigger_event="task.created", actions=[("task.set_status", {"status": "done"})]
    )
    await _make_task(a)

    # RLS: a session bound to B sees neither A's rules nor A's runs.
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        rules = (await session.execute(select(AutomationRule))).scalars().all()
        runs = (await session.execute(select(AutomationRun))).scalars().all()
        assert rules == [] and runs == []
    # Fail closed: no GUC bound → nothing.
    async with async_session_maker() as session:
        rules = (await session.execute(select(AutomationRule))).scalars().all()
        assert rules == []
