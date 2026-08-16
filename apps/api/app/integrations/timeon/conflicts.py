"""Settling a conflict. Business-licensed — see LICENSE.

Three resolutions, and the third is not a lesser version of the other two. ``keep_local`` writes
schakl's version into Timeon; ``keep_remote`` writes Timeon's into schakl; ``dismiss`` writes
neither and records that these two rows are **allowed** to differ. That last one is the reason
this is a table rather than a nightly recomputation (#318): "we looked at this and chose not to
act" leaves no trace in either system, so without somewhere to put it the same twelve rows come
back every night until nobody reads the queue.

All three end the same way — both fingerprints re-taken from the values that now stand — because
a resolution that did not update what the two sides last agreed on would be re-detected on the
next run, which is the same failure wearing a button.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.timeon.client import TimeonError
from app.integrations.timeon.mapping import (
    fingerprint,
    neutral_from_entry,
    neutral_from_row,
    observed_of,
    row_date,
    start_seconds_of,
    started_at_for,
)
from app.integrations.timeon.models import (
    TimeonAccount,
    TimeonConflict,
    TimeonConflictStatus,
    TimeonLink,
    TimeonLinkStatus,
)
from app.integrations.timeon.sync import TimeonSyncService
from app.modules.time.models import TimeEntry


class TimeonConflictService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(TimeonConflict)

    async def list_open(
        self, *, account_id: uuid.UUID | None, status: str | None, limit: int, offset: int
    ) -> tuple[list[TimeonConflict], int]:
        stmt = self.repo.scoped_select().order_by(TimeonConflict.detected_at.desc())
        count = self.repo.scoped_count_select()
        if account_id is not None:
            stmt = stmt.where(TimeonConflict.account_id == account_id)
            count = count.where(TimeonConflict.account_id == account_id)
        if status:
            stmt = stmt.where(TimeonConflict.status == status)
            count = count.where(TimeonConflict.status == status)
        rows = list(
            (await self.ctx.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )
        total = int((await self.ctx.session.execute(count)).scalar() or 0)
        return rows, total

    async def resolve(
        self, conflict_id: uuid.UUID, resolution: TimeonConflictStatus, note: str | None
    ) -> TimeonConflict:
        """Apply one decision.

        The write happens through the sync engine's own paths rather than a second copy of them:
        a resolution is the same act the sync would have performed unattended, and two write
        paths into one client's timesheet is how the two start disagreeing about what
        ``protect_invoiced`` means.
        """
        conflict = await self.repo.get_or_404(conflict_id)
        if conflict.status != TimeonConflictStatus.OPEN.value:
            raise AppError(
                "timeon_conflict_settled", "errors.timeon.conflict_settled", status_code=409
            )
        link = await self.ctx.repo(TimeonLink).get_or_404(conflict.link_id)
        account = await self.ctx.repo(TimeonAccount).get_or_404(conflict.account_id)

        if resolution is TimeonConflictStatus.DISMISSED:
            await self._tolerate(account, link)
        elif resolution in (
            TimeonConflictStatus.KEPT_LOCAL,
            TimeonConflictStatus.KEPT_REMOTE,
        ):
            await self._carry(
                account, link, to_timeon=resolution is TimeonConflictStatus.KEPT_LOCAL
            )
        else:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"resolution": "errors.validation"},
            )

        return await self.repo.update(
            conflict,
            status=resolution.value,
            resolved_at=datetime.now(UTC),
            resolved_by_user_id=None if self.ctx.is_system else self.ctx.user.id,
            note=note,
        )

    # --- the three outcomes --------------------------------------------------- #
    async def _entry_and_row(
        self, account: TimeonAccount, link: TimeonLink
    ) -> tuple[TimeEntry, dict]:
        """Both live rows, re-read.

        Re-read rather than taken from the conflict's snapshots, because a snapshot is what the
        divergence *was* and a resolution acts on what it *is*. The snapshots stay frozen for the
        screen — a person settles the diff they were shown — and the write goes to the current
        rows, so a change made between detection and decision is not silently reverted.
        """
        entry = await self.ctx.repo(TimeEntry).get(link.local_id) if link.local_id else None
        if entry is None:
            raise AppError(
                "timeon_conflict_stale", "errors.timeon.conflict_stale", status_code=409
            )
        service = TimeonSyncService(self.ctx, account)
        rows = await service.client.hours_by_id([int(link.external_id)])
        if not rows:
            raise AppError(
                "timeon_conflict_stale", "errors.timeon.conflict_stale", status_code=409
            )
        return entry, rows[0]

    async def _carry(self, account: TimeonAccount, link: TimeonLink, *, to_timeon: bool) -> None:
        entry, row = await self._entry_and_row(account, link)
        service = TimeonSyncService(self.ctx, account)
        resolver = await service.resolver()
        if to_timeon:
            try:
                saved = await service._save_remote(
                    entry, resolver, observed=link.observed, hour_id=int(link.external_id)
                )
            except TimeonError as exc:
                raise AppError(
                    "timeon_push_failed",
                    "errors.timeon.push_failed",
                    status_code=502,
                    details={"detail": str(exc)[:200]},
                ) from exc
            row = saved or row
        else:
            from app.modules.time.system import revise_entry

            day = row_date(row)
            if day is None:
                raise AppError(
                    "timeon_conflict_stale", "errors.timeon.conflict_stale", status_code=409
                )
            seconds = row.get("fromSeconds")
            started = started_at_for(
                day, int(seconds) if seconds is not None else start_seconds_of(entry.started_at)
            )
            company_id = resolver.company_by_ext.get(str(row.get("customerID") or ""))
            project_id = resolver.project_by_ext.get(str(row.get("projectID") or ""))
            touch = {"started_at", "minutes", "description", "billable"}
            if not row.get("customerID") or company_id is not None:
                touch.add("company_id")
            if not row.get("projectID") or project_id is not None:
                touch.add("project_id")
            await revise_entry(
                self.ctx,
                entry,
                started_at=started,
                minutes=int(row.get("seconds") or 0) // 60,
                company_id=company_id,
                project_id=project_id,
                description=(row.get("remark") or "").strip() or None,
                billable=bool(row.get("billable")),
                touch=frozenset(touch),
            )
        await self._agree(link, entry, row, resolver, status=TimeonLinkStatus.LINKED)

    async def _tolerate(self, account: TimeonAccount, link: TimeonLink) -> None:
        """"Leave them different." Both fingerprints are set to what each side says *now*, so
        the next run sees two rows that differ and neither of which has moved — the ``tolerated``
        branch in the engine — rather than re-raising the conflict it was just told to drop."""
        entry, row = await self._entry_and_row(account, link)
        service = TimeonSyncService(self.ctx, account)
        await self._agree(
            link, entry, row, await service.resolver(), status=TimeonLinkStatus.IGNORED
        )

    async def _agree(
        self, link: TimeonLink, entry: TimeEntry, row: dict, resolver, *, status: TimeonLinkStatus
    ) -> None:
        has_start = row.get("fromSeconds") is not None
        remote = neutral_from_row(
            row,
            start_seconds=int(row.get("fromSeconds")) if has_start else None,
            resolver=resolver,
        )
        local = neutral_from_entry(entry, resolver=resolver, has_remote_start=has_start)
        await self.ctx.repo(TimeonLink).update(
            link,
            status=status.value,
            local_hash=fingerprint(local),
            remote_hash=fingerprint(remote),
            observed=observed_of(row),
            observed_at=datetime.now(UTC),
            last_error=None,
        )

    async def dismiss_all_for_link(self, link_id: uuid.UUID) -> int:
        """Close any open conflict on a pairing that has just been settled another way."""
        rows = (
            (
                await self.ctx.session.execute(
                    self.repo.scoped_select()
                    .where(TimeonConflict.link_id == link_id)
                    .where(TimeonConflict.status == TimeonConflictStatus.OPEN.value)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await self.repo.update(
                row, status=TimeonConflictStatus.DISMISSED.value, resolved_at=datetime.now(UTC)
            )
        return len(rows)
