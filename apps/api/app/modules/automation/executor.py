"""The execution side of the rule engine (issue #27) — runs in the ARQ worker.

Claim, execute, record:

1. **Claim** — a guarded ``UPDATE … WHERE status = 'pending'`` flips the run to ``running``
   in its own committed transaction. No row claimed ⇒ another delivery got there first (or
   the emitter rolled back and the row never existed): exit. This status guard — not the
   queue — is what makes double delivery safe.
2. **Execute** — a fresh session, RLS GUC bound to the run's org, a ``SystemContext`` as the
   actor (``user=None`` ⇒ the system, §16). Actions run in order, **each inside a SAVEPOINT**:
   a failing action rolls back its own partial writes only, earlier successful actions keep
   their effects (real-world automation semantics), and the chain stops at the first failure.
3. **Record** — per-step results into ``runs.steps``, the final status + ``error`` on the row,
   one commit. A crash anywhere still marks the run failed from a last-resort session;
   the worker itself never dies on a rule.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.core.events import SystemContext
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.modules.automation import actions as actions_mod
from app.modules.automation.engine import jsonable
from app.modules.automation.models import (
    RUN_FAILED,
    RUN_PENDING,
    RUN_RUNNING,
    RUN_SUCCEEDED,
    AutomationAction,
    AutomationRun,
)

logger = logging.getLogger("schakl.automation")


async def _claim(org_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        result = await session.execute(
            update(AutomationRun)
            .where(
                AutomationRun.org_id == org_id,
                AutomationRun.id == run_id,
                AutomationRun.status == RUN_PENDING,
            )
            .values(status=RUN_RUNNING, started_at=datetime.now(UTC))
            .returning(AutomationRun.id)
        )
        claimed = result.scalar() is not None
        await session.commit()
        return claimed


async def _mark_failed(org_id: uuid.UUID, run_id: uuid.UUID, error: str) -> None:
    """Last-resort bookkeeping when the execute transaction itself blew up."""
    try:
        async with async_session_maker() as session:
            await set_current_org(session, org_id)
            await session.execute(
                update(AutomationRun)
                .where(AutomationRun.org_id == org_id, AutomationRun.id == run_id)
                .values(status=RUN_FAILED, finished_at=datetime.now(UTC), error=error[:2000])
            )
            await session.commit()
    except Exception:
        logger.exception("automation: could not record failure of run %s", run_id)


async def execute_run(org_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """Execute one claimed run; returns the final status (for the ARQ job result)."""
    if not await _claim(org_id, run_id):
        return "not-claimed"
    try:
        return await _execute_claimed(org_id, run_id)
    except Exception as exc:
        logger.exception("automation: run %s crashed", run_id)
        await _mark_failed(org_id, run_id, f"{type(exc).__name__}: {exc}")
        return RUN_FAILED


async def _execute_claimed(org_id: uuid.UUID, run_id: uuid.UUID) -> str:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        org = await session.get(Org, org_id)
        run = await session.scalar(
            select(AutomationRun).where(
                AutomationRun.org_id == org_id, AutomationRun.id == run_id
            )
        )
        if org is None or run is None:
            return "gone"

        steps: list[dict] = []
        status, error = RUN_SUCCEEDED, None

        if run.rule_id is None:
            # The rule was deleted between trigger and execution; there is nothing to do.
            status, error = RUN_FAILED, "errors.automation_rule_deleted"
        else:
            rule_actions = (
                (
                    await session.execute(
                        select(AutomationAction)
                        .where(
                            AutomationAction.org_id == org_id,
                            AutomationAction.rule_id == run.rule_id,
                        )
                        .order_by(
                            AutomationAction.position.asc(), AutomationAction.created_at.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
            ctx = SystemContext(org=org, session=session)
            action_ctx = actions_mod.ActionContext(ctx=ctx, run=run)

            for action in rule_actions:
                spec = actions_mod.resolve(action.action_type)
                if spec is None:
                    steps.append(
                        {
                            "action_type": action.action_type,
                            "status": "failed",
                            "error": "errors.automation_unknown_action",
                        }
                    )
                    status, error = RUN_FAILED, "errors.automation_unknown_action"
                    break
                try:
                    # SAVEPOINT per action: a failure discards only its own partial writes.
                    async with session.begin_nested():
                        result = await spec.handler(action_ctx, action.config or {})
                    steps.append(
                        {
                            "action_type": action.action_type,
                            "status": "succeeded",
                            "result": jsonable(result or {}),
                        }
                    )
                except Exception as exc:
                    message = str(exc) or type(exc).__name__
                    step: dict = {
                        "action_type": action.action_type,
                        "status": "failed",
                        "error": message[:500],
                    }
                    detail = getattr(exc, "result", None)
                    if isinstance(detail, dict):
                        step["result"] = jsonable(detail)
                    steps.append(step)
                    status, error = RUN_FAILED, message[:2000]
                    break

        run.status = status
        run.error = error
        run.steps = steps
        run.finished_at = datetime.now(UTC)
        await session.commit()
        return status
