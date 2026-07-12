"""The v1 action set + the action registry (issue #27).

An action is an :class:`~app.registry.AutomationActionSpec` — a key, an async handler
``(ActionContext, config) -> dict`` and an i18n title. Modules contribute their own on their
``ModuleDescriptor`` (the ``subscriptions`` module can ship "generate invoice line" without
touching this file); the v1 set below rides on the automation module's own descriptor, per
the issue. Handlers reach other modules only through **published surfaces**
(``tasks/system.py``, ``TemplateService.instantiate_system``, ``NotificationService.ingest``)
— never their models.

Every handler runs in the ARQ worker inside a per-org, RLS-bound session
(:class:`~app.core.events.SystemContext`; ``user=None`` ⇒ the system acts). Authorization
happened when the permission-gated rule author saved the rule. A handler raising
:class:`ActionError` (or anything else) fails its step; the executor rolls its savepoint back
and marks the run failed.

**Loop protection:** ``ctx.depth`` is the run's chain depth. Any event a handler causes must
carry ``{"_depth": depth + 1}`` (the ``chain_payload`` helper); the engine skips rules once an
event arrives at depth ≥ 3. Handlers pass it via the owning modules' ``extra_payload`` seams.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.core.events import SystemContext
from app.modules.automation.models import AutomationRun
from app.modules.automation.webhook import WebhookError, post_webhook
from app.registry import AutomationActionSpec, registry


class ActionError(Exception):
    """A step failure whose message is an ``errors.*`` i18n key (or raw upstream data).

    ``result`` optionally carries response detail (a webhook's HTTP status), recorded on the
    failed step so the runs log can still say what came back.
    """

    def __init__(self, message: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class ActionContext:
    """What a handler gets: the tenant-bound system context and the run being executed."""

    ctx: SystemContext
    run: AutomationRun

    @property
    def depth(self) -> int:
        return self.run.depth

    @property
    def actor_name(self) -> str:
        # What the activity trail shows instead of a person (§16: NULL actor = the system).
        return self.run.rule_name

    def chain_payload(self) -> dict[str, Any]:
        """Ride-along payload for any event this action causes — the loop counter."""
        return {"_depth": self.run.depth + 1}


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _required_uuid(config: dict, key: str) -> uuid.UUID:
    parsed = _uuid_or_none(config.get(key))
    if parsed is None:
        raise ActionError("errors.automation_action_config_invalid")
    return parsed


def _entity_uuid(action_ctx: ActionContext) -> uuid.UUID:
    return action_ctx.run.entity_id


# --------------------------------------------------------------------------- #
# task.* — via the tasks module's published system surface
# --------------------------------------------------------------------------- #
async def _task_create(action_ctx: ActionContext, config: dict) -> dict:
    """From a template (``template_id`` + a company) or bare (``title`` [+ ids]).

    The company defaults to the triggering entity when the rule fires on a company event —
    "when a client becomes active, create its onboarding tasks" needs no config at all.
    """
    from app.modules.tasks.templates import TemplateService

    company_id = _uuid_or_none(config.get("company_id"))
    if company_id is None and action_ctx.run.entity_type == "company":
        company_id = _entity_uuid(action_ctx)

    template_id = _uuid_or_none(config.get("template_id"))
    if template_id is not None:
        if company_id is None:
            raise ActionError("errors.automation_action_config_invalid")
        tasks = await TemplateService(action_ctx.ctx).instantiate_system(
            template_id, company_id, actor_name=action_ctx.actor_name
        )
        return {"task_ids": [str(t.id) for t in tasks]}

    from app.modules.tasks.system import create_task_system

    title = str(config.get("title") or "").strip()
    if not title:
        raise ActionError("errors.automation_action_config_invalid")
    task = await create_task_system(
        action_ctx.ctx,
        title=title,
        company_id=company_id,
        project_id=_uuid_or_none(config.get("project_id")),
        assignee_user_id=_uuid_or_none(config.get("assignee_user_id")),
        description=config.get("description") or None,
        priority=str(config.get("priority") or "normal"),
        actor_name=action_ctx.actor_name,
        extra_payload=action_ctx.chain_payload(),
    )
    return {"task_ids": [str(task.id)]}


def _target_task_id(action_ctx: ActionContext, config: dict) -> uuid.UUID:
    """The task to act on: config wins, else the triggering entity when it is a task."""
    configured = _uuid_or_none(config.get("task_id"))
    if configured is not None:
        return configured
    if action_ctx.run.entity_type == "task":
        return _entity_uuid(action_ctx)
    raise ActionError("errors.automation_action_config_invalid")


async def _task_set_status(action_ctx: ActionContext, config: dict) -> dict:
    from app.modules.tasks.system import set_task_status_system

    status = str(config.get("status") or "")
    task = await set_task_status_system(
        action_ctx.ctx,
        _target_task_id(action_ctx, config),
        status,
        actor_name=action_ctx.actor_name,
        extra_payload=action_ctx.chain_payload(),
    )
    return {"task_id": str(task.id), "status": task.status}


async def _task_assign(action_ctx: ActionContext, config: dict) -> dict:
    from app.modules.tasks.system import assign_task_system

    task = await assign_task_system(
        action_ctx.ctx,
        _target_task_id(action_ctx, config),
        _required_uuid(config, "user_id"),
        actor_name=action_ctx.actor_name,
        extra_payload=action_ctx.chain_payload(),
    )
    return {"task_id": str(task.id), "assignee_user_id": str(task.assignee_user_id)}


# --------------------------------------------------------------------------- #
# notification.send — through the notifications module's published service
# --------------------------------------------------------------------------- #
async def _notification_send(action_ctx: ActionContext, config: dict) -> dict:
    from app.modules.notifications.events import AUTOMATION_NOTIFY
    from app.modules.notifications.service import NotificationService

    message = str(config.get("message") or "").strip()
    recipients = [
        parsed
        for parsed in (_uuid_or_none(raw) for raw in config.get("user_ids") or [])
        if parsed is not None
    ]
    if not message or not recipients:
        raise ActionError("errors.automation_action_config_invalid")
    event = await NotificationService(action_ctx.ctx).ingest(
        AUTOMATION_NOTIFY,
        action_ctx.run.entity_type,
        action_ctx.run.entity_id,
        {
            # Tenant-authored content, rendered verbatim in the recipient's inbox.
            "message": message,
            "rule": action_ctx.run.rule_name,
            "_recipients": recipients,
        },
    )
    return {"notified": event is not None, "recipients": [str(r) for r in recipients]}


# --------------------------------------------------------------------------- #
# webhook.post — outbound, SSRF-guarded, optional confirmation (#96)
# --------------------------------------------------------------------------- #
async def _webhook_post(action_ctx: ActionContext, config: dict) -> dict:
    url = str(config.get("url") or "")
    if not url:
        raise ActionError("errors.automation_action_config_invalid")
    body = {
        "event": action_ctx.run.trigger_event,
        "entity_type": action_ctx.run.entity_type,
        "entity_id": str(action_ctx.run.entity_id),
        "payload": action_ctx.run.payload,
    }
    try:
        return await post_webhook(url, body, confirm=bool(config.get("confirm")))
    except WebhookError as exc:
        raise ActionError(str(exc), result=exc.result) from exc


#: The v1 set, declared on the automation module's own ModuleDescriptor.
BUILTIN_ACTIONS: list[AutomationActionSpec] = [
    AutomationActionSpec("task.create", _task_create, position=10),
    AutomationActionSpec("task.set_status", _task_set_status, position=20),
    AutomationActionSpec("task.assign", _task_assign, position=30),
    AutomationActionSpec("notification.send", _notification_send, position=40),
    AutomationActionSpec("webhook.post", _webhook_post, position=50),
]


def available_actions() -> dict[str, AutomationActionSpec]:
    """Every action key currently on offer: each **enabled** module's contribution.

    Resolved at call time, not import time, so a module registered after this one still
    counts, and a disabled module's actions are neither offered nor executable.
    """
    specs: dict[str, AutomationActionSpec] = {}
    for module in registry.enabled(settings.enabled_modules):
        for spec in module.automation_actions:
            specs[spec.key] = spec
    return specs


def resolve(action_type: str) -> AutomationActionSpec | None:
    return available_actions().get(action_type)
