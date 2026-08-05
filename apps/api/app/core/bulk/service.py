"""The bulk engine: load a selection, write it row by row, report what it could not do.

Three properties are the whole of it, and each exists because the obvious alternative is wrong:

1. **One query loads the selection.** A ``get_or_404`` per id would make the cheap half of a
   batch the expensive half (docs/PERFORMANCE.md), and the read must not scale with the
   selection even though the writes necessarily do. The read rides ``scoped_select()``, so
   tenant isolation *and* the company horizon come along — a bulk call can no more name a row
   across the horizon than a list can show one (CLAUDE.md §15).
2. **Every row writes through its own module's service.** Never ``repo.update``: that would
   skip the validation, the activity trail, the events, and — in ``tasks`` — the per-row
   ``:own``/``:any`` refinement that decides whether *this* caller may touch *this* row.
3. **Every row runs inside its own SAVEPOINT.** A partial batch is the honest answer, but
   `require_context` rolls the request back on any exception, so simply catching one would
   leave the session poisoned for every row after it. ``begin_nested()`` bounds the damage to
   the row that caused it: its half-written state and its activity line roll back together,
   and the rows before and after it stand.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.bulk.schemas import BulkDeleteRequest, BulkUpdateRequest
from app.core.bulk.spec import BulkDescriptor
from app.core.bulk.values import resolve_values
from app.core.tenancy import RequestContext
from app.errors import AppError


def _reason(exc: AppError) -> str:
    """The most specific i18n key this refusal carries.

    A service that refuses *a value* raises the envelope's generic key and puts the real reason
    in ``fields`` — ``AppError("validation", "errors.validation", fields={"due_change_reason":
    "errors.due_reason_required"})``. Reporting ``message_key`` there tells the user "3 rows
    failed: invalid" and nothing else, which is not a reason, and it is most of the refusals a
    batch will actually meet. Refusals that *are* their own key (``errors.forbidden`` from the
    tasks scope refinement, ``errors.projects_budget_hours_locked``) have no ``fields`` and pass
    straight through.

    One key per row, because a row's outcome is one line in the banner. Where a service names
    several fields at once they are facets of the same refusal; the first is the one it led with.
    """
    if exc.fields:
        return next(iter(exc.fields.values()))
    return exc.message_key


class BulkService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def update(
        self, descriptor: BulkDescriptor, payload: BulkUpdateRequest
    ) -> dict[str, Any]:
        """Set the payload's fields on every row of the selection the caller may write."""
        writer = descriptor.writer
        if writer is None or descriptor.write_permission is None:
            # A delete-only entity mounts no update route; this is the service-level answer to
            # the same question, so a non-HTTP caller cannot reach further than HTTP can.
            raise AppError("not_found", "errors.not_found", status_code=404)
        # The route declares it too; this is the defence-in-depth half (CLAUDE.md §15), and
        # what keeps a service-level caller (MCP, a future job) gated identically.
        self.ctx.require(descriptor.write_permission)
        values = await resolve_values(self.ctx, descriptor, payload.values)
        rows, failed = await self._selection(descriptor, payload.ids)
        succeeded = 0
        for row in rows:
            # A fresh dict per row: a writer is free to consume its values, and one that did
            # would otherwise leave the rest of the batch with an empty payload.
            if await self._attempt(lambda r=row: writer(self.ctx, r, dict(values)), row, failed):
                succeeded += 1
        return {"succeeded": succeeded, "failed": failed}

    async def delete(
        self, descriptor: BulkDescriptor, payload: BulkDeleteRequest
    ) -> dict[str, Any]:
        """Delete every row of the selection the caller may delete."""
        if descriptor.delete_row is None or descriptor.delete_permission is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        self.ctx.require(descriptor.delete_permission)
        rows, failed = await self._selection(descriptor, payload.ids)
        remover = descriptor.delete_row
        succeeded = 0
        for row in rows:
            if await self._attempt(lambda r=row: remover(self.ctx, r), row, failed):
                succeeded += 1
        return {"succeeded": succeeded, "failed": failed}

    async def _attempt(
        self,
        work: Callable[[], Awaitable[None]],
        row: Any,
        failed: list[dict[str, Any]],
    ) -> bool:
        """Run one row's write in its own SAVEPOINT; report an ``AppError`` instead of raising.

        Only ``AppError`` is caught, and that is the deliberate line: it is the vocabulary the
        services speak when they *decide* to refuse — a locked budget, a task this caller may
        not write, a status the tenant does not have. Anything else is a bug or a database
        telling us something we did not model, and a bug that surfaces as "3 rows skipped"
        rather than a 500 is a bug nobody will ever find.
        """
        try:
            async with self.ctx.session.begin_nested():
                await work()
        except AppError as exc:
            failed.append({"id": row.id, "error": _reason(exc)})
            return False
        return True

    async def _selection(
        self, descriptor: BulkDescriptor, ids: list[uuid.UUID]
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        """Load the selection in one query and split it into "can" and "cannot, because"."""
        unique = list(dict.fromkeys(ids))  # a row selected twice is written once, not twice
        model = descriptor.model
        repo = self.ctx.repo(model)
        found = {
            row.id: row
            for row in (
                await self.ctx.session.execute(repo.scoped_select().where(model.id.in_(unique)))
            )
            .scalars()
            .all()
        }
        rows: list[Any] = []
        failed: list[dict[str, Any]] = []
        for entity_id in unique:
            row = found.get(entity_id)
            if row is None:
                # Outside the tenant, outside the horizon, or already gone — one answer for
                # all three (CLAUDE.md §15): an id you cannot act on must not read as an id
                # that exists.
                failed.append({"id": entity_id, "error": "errors.not_found"})
            else:
                rows.append(row)
        return rows, failed
