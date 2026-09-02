"""ARQ jobs for the interactions module (#327): reading an approved email into its task.

Two functions and one cron:

* ``interactions_enrich_task`` — one email, one task. Enqueued by the approve that asked for it.
* ``interactions_reap_stale_enrichment`` — every quarter of an hour, per org. Ends the runs that
  are claimed by nobody.

**The body is not there when the job is enqueued, and that is the whole design problem.** A
pending row holds metadata only; the body is fetched by the google module *after* approval,
outside the approving transaction, deliberately. So this job is enqueued deferred and, if the
body still has not landed, **re-defers itself** on a widening delay rather than reading a
``NULL`` and writing an empty description. The attempts are bounded: an email whose body never
arrives (a disconnected mailbox, a message deleted in Gmail) ends as ``skipped``, which is a
true sentence, and not as a run that waits for ever.

Polling rather than chaining off the fetch is what keeps this in one module. The alternative —
having the google module tell us its fetch landed — would put knowledge of task enrichment in
an integration whose whole contract with us is ``interactions.system``, and would still need
this fallback for the ``.eml`` upload and the manual note, which never fetch anything because
their body is already there.

**A status a process owns needs a process-independent way back** (the reporting lesson, #300).
``running`` says a worker has this row; the row cannot tell a busy worker from one that was
restarted between the flush and the first ``await``. The reaper is the answer that does not run
in the process it is answering for.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import enqueue, run_per_org, system_context
from app.core.models import Org, OrgStatus
from app.db import async_session_maker, set_current_org
from app.modules.interactions.enrich import is_stale
from app.modules.tasks.models import Task, TaskAIStatus
from app.modules.tasks.system import set_ai_status_system

logger = logging.getLogger("schakl.interactions.enrich")

#: How long the first attempt waits. The gmail body fetch is itself deferred two seconds and
#: then makes one API call, so the common case is ready well inside this.
FIRST_DELAY_SECONDS = 10
#: Widening waits for the attempts after it — a slow mailbox, a worker with a queue in front of
#: it. Their length is the whole budget for "the body is on its way".
RETRY_DELAYS_SECONDS = (30, 90, 300)
#: After the last delay the run gives up and says so. Four attempts spread over ~7 minutes.
MAX_ATTEMPTS = 1 + len(RETRY_DELAYS_SECONDS)
#: How long a first attempt that found no ``queued`` row waits before asking once more — the
#: request that queued it is still committing (``_unclaimable``). One attempt of the ladder
#: above is spent on it, which is the point: a claim that never becomes possible still ends.
CLAIM_GRACE_SECONDS = 15

#: A run still claimed after this is claimed by nobody. Comfortably longer than a slow provider
#: call plus the retry ladder above, so the reaper never races a job that is simply working.
STALE_AFTER_MINUTES = 20


async def _licensed() -> bool:
    """The cron half of the module's licence gate.

    The router's write gate covers requests; a worker writes on a schedule and would sail
    straight past it (the shape ``reporting/jobs.py`` states for the same reason).
    """
    return await sku_cron_enabled("interactions")


async def _active_org(session: AsyncSession, org_id: str) -> Org | None:
    org = await session.get(Org, uuid.UUID(org_id))
    return org if org is not None and org.status == OrgStatus.ACTIVE.value else None


async def schedule_enrichment(org_id: uuid.UUID, interaction_id: uuid.UUID, task_id: uuid.UUID):
    """Offer one enrichment to the worker. Returns the arq job, or ``None`` if nothing queued.

    The caller cares about ``None``: it has just flipped the task to ``queued``, and a row that
    claims a worker has it when none does is exactly the twenty-minutes-of-"bezig" the reaper
    exists to clean up after — better not to create it.
    """
    return await enqueue(
        "interactions_enrich_task",
        str(org_id),
        str(interaction_id),
        str(task_id),
        1,
        _defer_by=timedelta(seconds=FIRST_DELAY_SECONDS),
        # Deterministic per **task and email**, so a double approve (two tabs, a retried
        # request) queues one run rather than two racing writers of the same description.
        #
        # The interaction id is what stops it repeating reporting's #300 bug, which
        # ``core.jobs.enqueue`` already names: arq declines a ``_job_id`` whose *result* is
        # still in Redis, one hour by default. Keyed on the task alone, filing a second email
        # onto a task enriched within the hour queued nothing, ``offer_task_enrichment`` read
        # the ``None`` as "no worker took it" and set ``failed`` — so the obvious response to a
        # disappointing run ("try it with another mail") was the one thing guaranteed not to
        # work. Two different emails are two different runs; the same email twice is not.
        _job_id=f"interactions-enrich-{task_id}-{interaction_id}",
    )


async def interactions_enrich_task(
    ctx: dict,  # noqa: ARG001
    org_id: str,
    interaction_id: str,
    task_id: str,
    attempt: int = 1,
) -> None:
    """Read one approved email into its task; re-defer while the body has not landed."""
    if not await _licensed():
        return
    from app.modules.interactions.enrich import enrich_task

    async with async_session_maker() as session:
        org = await _active_org(session, org_id)
        if org is None:
            return
        await set_current_org(session, org.id)
        context = system_context(org, session)
        task_uuid, interaction_uuid = uuid.UUID(task_id), uuid.UUID(interaction_id)

        # Claim the row. A second delivery of the same job, or the reaper having already given
        # up on it, loses here and stands down rather than writing the description twice.
        claimed = await set_ai_status_system(
            context,
            task_uuid,
            TaskAIStatus.RUNNING,
            only_if=(TaskAIStatus.QUEUED, TaskAIStatus.RUNNING),
        )
        if not claimed:
            outcome = await _unclaimable(org.id, interaction_uuid, task_uuid, attempt)
            await session.commit()
            logger.info("interactions: enrichment for task %s %s", task_id, outcome)
            return

        if not await _body_ready(session, org.id, interaction_uuid):
            outcome = await _defer_or_give_up(context, org.id, interaction_uuid, task_uuid, attempt)
            await session.commit()
            logger.debug("interactions: enrichment for task %s %s", task_id, outcome)
            return

        try:
            status = await enrich_task(context, interaction_uuid, task_uuid)
        except Exception:
            # A worker failure must leave a row that says so, never one stuck on ``running``.
            logger.exception("interactions: enrichment crashed for task %s", task_id)
            await session.rollback()
            await set_current_org(session, org.id)
            await set_ai_status_system(system_context(org, session), task_uuid, TaskAIStatus.FAILED)
            await session.commit()
            return

        await set_ai_status_system(context, task_uuid, TaskAIStatus(status))
        await session.commit()


async def _body_ready(session: AsyncSession, org_id: uuid.UUID, interaction_id: uuid.UUID) -> bool:
    """Has the email's body landed yet? (the one question the retry ladder is about)"""
    from app.modules.interactions.models import Interaction

    row = (
        await session.execute(
            select(Interaction.body_text, Interaction.body_markdown).where(
                Interaction.org_id == org_id, Interaction.id == interaction_id
            )
        )
    ).first()
    if row is None:
        return True  # gone: let the run itself resolve it to `skipped`, not the ladder
    return bool((row[1] or row[0] or "").strip())


async def _unclaimable(
    org_id: uuid.UUID, interaction_id: uuid.UUID, task_id: uuid.UUID, attempt: int
) -> str:
    """The row was not ours to claim. Once, on the first attempt, ask again; otherwise stop.

    A first attempt that finds no ``queued`` row is, far more often than a duplicate
    delivery, **the request that queued it not having committed yet**: the offer enqueues the
    job with :data:`FIRST_DELAY_SECONDS` of head start, and the approve (a whole thread of
    siblings, #372) or the manual import (a Google round trip per attachment, before #342's
    offer moved behind them) can still be inside its transaction when that runs out. Standing
    down there left the task on "in de wachtrij" until the reaper called it failed, twenty
    minutes later, with nothing in the log about either.

    So a *first* attempt re-defers itself once — on a fresh job id, and without touching the
    status, which is the request's to write — and only a later one stands down. That later
    one is a real duplicate or a reaped run, and both are right to stop: the claim is the
    thing that keeps two deliveries from writing one description twice.
    """
    if attempt > 1:
        return "not claimable; standing down"
    queued = await enqueue(
        "interactions_enrich_task",
        str(org_id),
        str(interaction_id),
        str(task_id),
        attempt + 1,
        _defer_by=timedelta(seconds=CLAIM_GRACE_SECONDS),
        _job_id=_retry_job_id(task_id, interaction_id, attempt + 1),
    )
    if queued is None:
        return "not claimable and could not re-queue"
    return f"not claimable yet; asking again in {CLAIM_GRACE_SECONDS}s"


def _retry_job_id(task_id: uuid.UUID, interaction_id: uuid.UUID, attempt: int) -> str:
    """A fresh id per attempt **and per email**.

    Fresh per attempt because the previous one's result is still in Redis and a repeated id
    is declined silently — a run that stops without ever saying so. Per email for the reason
    the first attempt's id already carries it (:func:`schedule_enrichment`): keyed on the task
    alone, the second email filed onto a task within the hour shared every retry id with the
    first, so its first re-defer was declined and it settled as ``failed``.
    """
    return f"interactions-enrich-{task_id}-{interaction_id}-{attempt}"


async def _defer_or_give_up(
    context, org_id: uuid.UUID, interaction_id: uuid.UUID, task_id: uuid.UUID, attempt: int
) -> str:  # noqa: ANN001
    """Wait for the body once more, or stop and say the run found nothing to read."""
    if attempt >= MAX_ATTEMPTS:
        await set_ai_status_system(context, task_id, TaskAIStatus.SKIPPED)
        return "gave up: no body"
    delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
    queued = await enqueue(
        "interactions_enrich_task",
        str(org_id),
        str(interaction_id),
        str(task_id),
        attempt + 1,
        _defer_by=timedelta(seconds=delay),
        _job_id=_retry_job_id(task_id, interaction_id, attempt + 1),
    )
    if queued is None:
        await set_ai_status_system(context, task_id, TaskAIStatus.FAILED)
        return "not queued"
    # Back to `queued`: nothing is running until that job starts, and leaving it on `running`
    # would let the reaper mistake a waiting run for an abandoned one.
    await set_ai_status_system(context, task_id, TaskAIStatus.QUEUED)
    return f"deferred {delay}s (attempt {attempt + 1})"


async def _reap_org(org: Org, session: AsyncSession) -> None:
    now = datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(Task).where(
                    Task.org_id == org.id,
                    Task.ai_status.in_((TaskAIStatus.QUEUED.value, TaskAIStatus.RUNNING.value)),
                )
            )
        )
        .scalars()
        .all()
    )
    for task in rows:
        if not is_stale(task.ai_status_at, now=now, minutes=STALE_AFTER_MINUTES):
            continue
        task.ai_status = TaskAIStatus.FAILED.value
        task.ai_status_at = now
        logger.warning(
            "interactions: enrichment for task %s was claimed by nobody; failing it", task.id
        )


async def interactions_reap_stale_enrichment(ctx: dict) -> None:  # noqa: ARG001
    """Quarter-hourly: end the enrichment runs whose worker is gone."""
    if not await _licensed():
        return
    await run_per_org(_reap_org)
