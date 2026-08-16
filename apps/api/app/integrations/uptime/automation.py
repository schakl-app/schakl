"""What ``uptime`` contributes to the automation rule engine (issue #27, docs/UPTIME.md §11).

One action, and it is the one an agency actually asks for: **pause a monitor**, so a planned
migration does not page everyone at 02:00. The *trigger* half needs nothing from this module —
a monitor going down already emits a notification event, and `task.create` already exists — so
contributing a second "create a task" here would be a duplicate with a worse name.

It carries `uptime.monitor.pause` rather than `monitor.write` for the reason the route does:
silencing an alert during a migration is an ordinary act, and repointing a monitor is not.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.registry import AutomationActionSpec

logger = logging.getLogger("schakl.uptime")


async def pause_monitor_action(action_ctx: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Pause (or resume) one monitor.

    Refuses rather than guessing when the rule names no monitor: a pause that silently applied
    to *every* monitor because a field was blank is the automation equivalent of a bulk edit
    with no selection.
    """
    from app.integrations.uptime.service import UptimeWriteService

    raw = config.get("monitor_id")
    if not raw:
        return {"ok": False, "reason": "uptime.automation.no_monitor"}
    try:
        monitor_id = uuid.UUID(str(raw))
    except ValueError:
        return {"ok": False, "reason": "uptime.automation.no_monitor"}

    paused = bool(config.get("paused", True))
    ctx = getattr(action_ctx, "ctx", action_ctx)
    monitor = await UptimeWriteService(ctx).set_paused(monitor_id, paused=paused)
    return {"ok": True, "monitor_id": str(monitor.id), "paused": paused}


UPTIME_AUTOMATION_ACTIONS: list[AutomationActionSpec] = [
    AutomationActionSpec(
        key="uptime.pause",
        handler=pause_monitor_action,
        title_key="automation.action.uptime.pause",
        position=520,
    ),
]
