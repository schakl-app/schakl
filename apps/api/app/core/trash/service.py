"""The trash can — trash, restore, purge, and the counts that decide which (docs/TRASH.md).

Three verbs, one rule each, and the rule is always about *records*:

* **trash** is what ``DELETE`` on a trashable entity means now. It is refused
  (``errors.trash_blocked``, 409, the counts in ``details``) while any *blocking* dependent —
  an issued invoice, a domain, an agreement, a project, logged hours — hangs off the record,
  because those must outlive it and a client holding them is archived, never deleted. Otherwise
  it stamps ``deleted_at`` and nothing else changes: every dependent row is still in the
  database exactly as it was, merely hidden with its parent.
* **restore** clears the stamp. Because nothing was moved, nothing has to be moved back.
* **purge** is the delete the database always did — the cascade — reached only through here,
  only for a row already in the trash, and re-checked against the same blockers, so a record
  that has grown one *since* being trashed is kept and the screen says why. Each contributing
  module gets to take its own rows out first (``TrashDependent.purge``): the cascade is right
  for a link row and wrong for a task whose planned block is mirrored into somebody's calendar.

The trail records all three (``trashed`` / ``restored`` / ``purged``), the last one written
*before* the row goes with the label it had — the invoice-number rule (§16): a trail line that
names what disappeared is the only thing left that can.

Retention is a stated constant, not a setting: the dialog prints it, the trash screen prints it,
and one number in one place is what lets the two agree. Make it per-org the day a tenant asks.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.core.activity.service import ActivityService, _display_name
from app.core.tenancy import RequestContext
from app.core.trash.files import files_dependent
from app.core.trash.schemas import TrashDependentCount, TrashItem, TrashPage, TrashPreview
from app.core.trash.spec import TrashableSpec, TrashDependent, dependents_of
from app.errors import AppError

#: How long a trashed record is kept before the nightly sweep purges it.
TRASH_RETENTION_DAYS = 30

ACTION_TRASHED = "trashed"
ACTION_RESTORED = "restored"
ACTION_PURGED = "purged"


def trash_specs() -> dict[str, TrashableSpec]:
    """``entity_type`` → spec, over the **enabled** modules only — disabling a module takes its
    trash routes and its sweep with it, exactly as it takes its router."""
    from app.registry import registry

    return {
        spec.entity_type: spec
        for module in registry.enabled(settings.enabled_modules)
        for spec in module.trash
    }


def spec_for(entity_type: str) -> TrashableSpec:
    spec = trash_specs().get(entity_type)
    if spec is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return spec


def _split(
    counts: dict[uuid.UUID, list[tuple[TrashDependent, int]]], entity_id: uuid.UUID
) -> tuple[list[TrashDependentCount], list[TrashDependentCount]]:
    blocking: list[TrashDependentCount] = []
    taken: list[TrashDependentCount] = []
    for dependent, n in counts.get(entity_id, ()):
        row = TrashDependentCount(key=dependent.key, label_key=dependent.label_key, count=n)
        (blocking if dependent.blocks else taken).append(row)
    return blocking, taken


class TrashService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ #
    # gates
    # ------------------------------------------------------------------ #
    def _require(self, spec: TrashableSpec) -> None:
        # A client login never holds an entity's delete permission, and this says so in one
        # place rather than trusting every role edit to keep it that way: the trash is where the
        # agency's deleted records are, and none of them is a client's to see or to bring back.
        if self.ctx.is_portal:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        self.ctx.require(spec.delete_permission)

    def _dependents(self, spec: TrashableSpec) -> list[TrashDependent]:
        return [*dependents_of(spec.entity_type), files_dependent(spec.entity_type)]

    async def _counts(
        self, spec: TrashableSpec, ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[TrashDependent, int]]]:
        """Every dependent's count for every id — one query per dependent, never per row."""
        out: dict[uuid.UUID, list[tuple[TrashDependent, int]]] = {i: [] for i in ids}
        if not ids:
            return out
        for dependent in self._dependents(spec):
            counted = await dependent.count(self.ctx.session, self.ctx.org.id, ids)
            for entity_id, n in counted.items():
                if n and entity_id in out:
                    out[entity_id].append((dependent, int(n)))
        return out

    @staticmethod
    def _blocked(blocking: list[TrashDependentCount]) -> AppError:
        return AppError(
            "conflict",
            "errors.trash_blocked",
            status_code=409,
            # The machine-readable half (§9): the counts by key, so a caller — the dialog, an
            # agent — can say *what* stands in the way, not merely that something does.
            details={"blocking": {row.key: row.count for row in blocking}},
        )

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    async def preview(self, entity_type: str, entity_id: uuid.UUID) -> TrashPreview:
        """What deleting this **live** record would do."""
        spec = spec_for(entity_type)
        self._require(spec)
        obj = await self.ctx.repo(spec.model).get_or_404(entity_id)
        blocking, taken = _split(await self._counts(spec, [obj.id]), obj.id)
        return TrashPreview(
            entity_type=entity_type,
            entity_id=obj.id,
            can_trash=not blocking,
            blocking=blocking,
            taken_along=taken,
            retention_days=TRASH_RETENTION_DAYS,
        )

    def _item(
        self,
        spec: TrashableSpec,
        obj: Any,
        counts: dict[uuid.UUID, list[tuple[TrashDependent, int]]],
    ) -> TrashItem:
        blocking, taken = _split(counts, obj.id)
        return TrashItem(
            entity_type=spec.entity_type,
            entity_id=obj.id,
            label=spec.label(obj),
            deleted_at=obj.deleted_at,
            deleted_by_user_id=obj.deleted_by_user_id,
            deleted_by_name=obj.deleted_by_name,
            purge_at=obj.deleted_at + timedelta(days=TRASH_RETENTION_DAYS),
            blocking=blocking,
            taken_along=taken,
        )

    async def list(self, entity_type: str, *, limit: int = 50, offset: int = 0) -> TrashPage:
        spec = spec_for(entity_type)
        self._require(spec)
        repo = self.ctx.repo(spec.model, include_trashed=True)
        model = spec.model
        base = repo.scoped_select().where(model.deleted_at.isnot(None))
        rows = list(
            (
                await self.ctx.session.execute(
                    base.order_by(model.deleted_at.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = int(
            await self.ctx.session.scalar(
                repo.scoped_count_select().where(model.deleted_at.isnot(None))
            )
            or 0
        )
        counts = await self._counts(spec, [row.id for row in rows])
        return TrashPage(
            items=[self._item(spec, row, counts) for row in rows],
            total=total,
            retention_days=TRASH_RETENTION_DAYS,
        )

    async def _trashed_or_404(self, spec: TrashableSpec, entity_id: uuid.UUID) -> Any:
        obj = await self.ctx.repo(spec.model, include_trashed=True).get_or_404(entity_id)
        if obj.deleted_at is None:
            # A live record is not in the trash. 404 rather than 409: to a caller of the
            # trash, the row does not exist here — and saying "it exists, elsewhere" is a
            # statement a 404 on the live route was already careful not to make.
            raise AppError("not_found", "errors.not_found", status_code=404)
        return obj

    async def get(self, entity_type: str, entity_id: uuid.UUID) -> TrashItem:
        spec = spec_for(entity_type)
        self._require(spec)
        obj = await self._trashed_or_404(spec, entity_id)
        return self._item(spec, obj, await self._counts(spec, [obj.id]))

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    async def trash(self, entity_type: str, obj: Any) -> None:
        """Put a live record the caller has already loaded in the trash.

        Takes the row rather than an id — the owning service loaded it through its own
        repository and its own 404 rule, and re-fetching would be a second answer to "may
        this caller see it".
        """
        spec = spec_for(entity_type)
        self._require(spec)
        if obj.deleted_at is not None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        blocking, _taken = _split(await self._counts(spec, [obj.id]), obj.id)
        if blocking:
            raise self._blocked(blocking)
        actor = None if self.ctx.is_system else self.ctx.user
        obj.deleted_at = datetime.now(UTC)
        obj.deleted_by_user_id = actor.id if actor else None
        obj.deleted_by_name = _display_name(actor.full_name, actor.email) if actor else None
        await self.ctx.session.flush()
        await ActivityService(self.ctx).record(
            spec.entity_type, obj.id, ACTION_TRASHED, {"label": spec.label(obj)}
        )

    async def restore(self, entity_type: str, entity_id: uuid.UUID) -> Any:
        spec = spec_for(entity_type)
        self._require(spec)
        obj = await self._trashed_or_404(spec, entity_id)
        obj.deleted_at = None
        obj.deleted_by_user_id = None
        obj.deleted_by_name = None
        await self.ctx.session.flush()
        await ActivityService(self.ctx).record(
            spec.entity_type, obj.id, ACTION_RESTORED, {"label": spec.label(obj)}
        )
        return obj

    async def purge(self, entity_type: str, entity_id: uuid.UUID) -> None:
        """Delete a trashed record for good — the cascade, behind the same blockers."""
        spec = spec_for(entity_type)
        self._require(spec)
        obj = await self._trashed_or_404(spec, entity_id)
        blocking, _taken = _split(await self._counts(spec, [obj.id]), obj.id)
        if blocking:
            raise self._blocked(blocking)
        label = spec.label(obj)
        for dependent in self._dependents(spec):
            if dependent.purge is not None:
                await dependent.purge(self.ctx, obj.id)
        # Written before the row goes, with what the row was called: the trail has no FK on
        # purpose (§16), so this line is what "definitief verwijderd" points at afterwards.
        await ActivityService(self.ctx).record(
            spec.entity_type,
            obj.id,
            ACTION_PURGED,
            {"label": label, "deleted_at": obj.deleted_at.isoformat()},
        )
        await self.ctx.repo(spec.model, include_trashed=True).delete(obj)

    async def due_for_purge(
        self, spec: TrashableSpec, *, now: datetime | None = None
    ) -> list[uuid.UUID]:
        """Ids the retention window has run out on — what the nightly sweep works through."""
        cutoff = (now or datetime.now(UTC)) - timedelta(days=TRASH_RETENTION_DAYS)
        model = spec.model
        stmt = (
            select(model.id)
            .where(model.org_id == self.ctx.org.id, model.deleted_at <= cutoff)
            .order_by(model.deleted_at.asc())
        )
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def trashed_count(self, entity_type: str) -> int:
        """How many rows of one type the caller's trash holds — the chip on the list screen."""
        spec = spec_for(entity_type)
        self._require(spec)
        repo = self.ctx.repo(spec.model, include_trashed=True)
        stmt = repo.scoped_count_select().where(spec.model.deleted_at.isnot(None))
        return int(await self.ctx.session.scalar(stmt) or 0)


__all__ = [
    "ACTION_PURGED",
    "ACTION_RESTORED",
    "ACTION_TRASHED",
    "TRASH_RETENTION_DAYS",
    "TrashService",
    "spec_for",
    "trash_specs",
]
