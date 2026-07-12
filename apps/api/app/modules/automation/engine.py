"""The trigger side of the rule engine (issue #27): bus event → matched rules → queued runs.

This runs **inline in the emitter's transaction** (``app/core/events.py``), so it must stay
fast and must never do network-ish work beyond the Redis enqueue: one indexed rule probe per
event, an optional single-row entity snapshot for condition evaluation, and one ``INSERT``
per matched rule. The heavy lifting — actions, webhooks — happens in the ARQ worker
(``executor.py``), never in the request.

**Idempotency.** Each matched (rule, event occurrence) maps to a deterministic
``dedup_key = sha256(rule_id : entity_id : event : payload-hash)``, unique per org. A
re-emitted event (a cron retry, a double-fired handler) computes the same key and inserts no
second run — the probe-then-insert mirrors the notifications dedup, with the unique index as
the backstop. Caveat, by design: two *genuinely identical* occurrences (the same task making
the same status hop twice) also collapse into one run.

**Loop protection.** An event carries ``_depth`` — how many automation hops caused it (absent
⇒ 0: a human or a cron acted). Every action that causes an event re-emits with ``depth + 1``
(``actions.ActionContext.chain_payload``). Once an event arrives at depth ≥ ``MAX_CHAIN_DEPTH``
(3), matching rules are recorded as **skipped** runs instead of executing — rule A → B → A is
visible in the log, not an outage. Identical-payload cycles die even earlier, on the dedup key.

**Delivery.** The run row commits with the emitter's transaction; the enqueue is best-effort
(Redis down ⇒ the row stays ``pending`` and the requeue cron re-offers it) and slightly
deferred, so the worker doesn't race a transaction that hasn't committed yet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from app.core.events import EmitContext
from app.modules.automation import queue
from app.modules.automation.conditions import evaluate
from app.modules.automation.models import RUN_PENDING, RUN_SKIPPED, AutomationRule, AutomationRun
from app.modules.automation.triggers import TRIGGERS, TriggerSpec

logger = logging.getLogger("schakl.automation")

#: An event caused by ≥ this many chained automation hops no longer fires rules.
MAX_CHAIN_DEPTH = 3


def jsonable(value: Any) -> Any:
    """Coerce a payload to what JSONB accepts (UUIDs, dates, Decimals are common)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(item) for item in value]
    return value


def content_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload minus ``_``-prefixed routing keys, JSON-safe — what a run stores/forwards."""
    return {
        key: jsonable(value) for key, value in payload.items() if not key.startswith("_")
    }


def dedup_key_for(
    rule_id: uuid.UUID, entity_id: uuid.UUID, event: str, payload: dict[str, Any]
) -> str:
    """sha256(rule + entity + event + payload-hash): retries never double-fire.

    ``_dedup_key`` (a cron's occurrence id, e.g. "…:2026-07-12") joins the hashed material
    when present — it *identifies* the occurrence; the other routing keys never do.
    """
    hashable = content_payload(payload)
    if payload.get("_dedup_key"):
        hashable["_dedup_key"] = str(payload["_dedup_key"])
    digest = hashlib.sha256(
        json.dumps(hashable, sort_keys=True, default=str).encode()
    ).hexdigest()
    return hashlib.sha256(
        f"{rule_id}:{entity_id}:{event}:{digest}".encode()
    ).hexdigest()


async def entity_snapshot(
    ctx: EmitContext, spec: TriggerSpec, entity_id: uuid.UUID
) -> dict[str, Any]:
    """The entity's own columns, for condition evaluation.

    Read by table name only (the sanctioned cross-module crossing; the name comes from the
    trigger catalog, never from input) and always ``org_id``-scoped — RLS backs it up.
    ``{}`` when the module has no table yet (#96's forward-declared events) or the row is gone.
    """
    if spec.table is None:
        return {}
    row = await ctx.session.scalar(
        text(f"SELECT row_to_json(t) FROM {spec.table} t WHERE org_id = :org AND id = :id"),
        {"org": str(ctx.org.id), "id": str(entity_id)},
    )
    if row is None:
        return {}
    return row if isinstance(row, dict) else json.loads(row)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def handle_event(ctx: EmitContext, payload: dict[str, Any], event: str) -> None:
    """The bus subscriber: match enabled rules, record runs, hand off to the worker."""
    spec = TRIGGERS.get(event)
    if spec is None:  # subscribed events always have a spec; belt-and-braces
        return
    entity_id = _as_uuid(payload.get(spec.id_key))
    if entity_id is None:
        return

    rules = (
        (
            await ctx.session.execute(
                select(AutomationRule)
                .where(
                    AutomationRule.org_id == ctx.org.id,
                    AutomationRule.trigger_event == event,
                    AutomationRule.enabled.is_(True),
                )
                .order_by(AutomationRule.position.asc(), AutomationRule.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not rules:
        return

    depth = _depth(payload)
    stored_payload = content_payload(payload)
    # One snapshot serves every rule on this event; fetched only if some rule has conditions.
    snapshot: dict[str, Any] | None = None

    for rule in rules:
        if rule.conditions:
            if snapshot is None:
                snapshot = await entity_snapshot(ctx, spec, entity_id)
            # Payload wins on key collisions: it carries the transition (from/to), which is
            # more specific than the entity's current column value.
            if not evaluate(rule.conditions, {**snapshot, **stored_payload}):
                continue

        dedup_key = dedup_key_for(rule.id, entity_id, event, payload)
        exists = await ctx.session.scalar(
            select(AutomationRun.id)
            .where(
                AutomationRun.org_id == ctx.org.id,
                AutomationRun.dedup_key == dedup_key,
            )
            .limit(1)
        )
        if exists is not None:
            continue

        skipped = depth >= MAX_CHAIN_DEPTH
        run = AutomationRun(
            org_id=ctx.org.id,
            rule_id=rule.id,
            rule_name=rule.name,
            trigger_event=event,
            entity_type=spec.entity_type,
            entity_id=entity_id,
            status=RUN_SKIPPED if skipped else RUN_PENDING,
            depth=depth,
            payload=stored_payload,
            error="errors.automation_depth_exceeded" if skipped else None,
            steps=[],
            dedup_key=dedup_key,
        )
        ctx.session.add(run)
        await ctx.session.flush()
        if not skipped:
            await queue.enqueue_run(ctx.org.id, run.id)


def _depth(payload: dict[str, Any]) -> int:
    try:
        return max(0, int(payload.get("_depth", 0) or 0))
    except (TypeError, ValueError):
        return 0


def make_trigger_handler(event: str):  # noqa: ANN201 - returns an EventHandler
    async def handler(ctx: EmitContext, payload: dict[str, Any]) -> None:
        await handle_event(ctx, payload, event)

    handler.__name__ = f"automation_on_{event.replace('.', '_')}"
    return handler
