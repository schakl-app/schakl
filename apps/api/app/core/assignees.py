"""Multi-assignee links: one primary, N others (CLAUDE.md §6, Golden Rule 1).

Several employees work an account — one owns it, the rest are involved. Companies and projects
each own a join table (``company_assignees`` / ``project_assignees``) of exactly the same shape,
so the columns (``AssigneeLinkMixin``) and the read/replace logic (``AssigneeService``) live here
in core rather than being copied per module. It mirrors ``company_contacts``: a partial unique
index on ``is_primary`` lets the database, not the application, guarantee "at most one primary".

The owning entity also keeps a ``responsible_user_id`` column mirroring the primary. That column
is the **expand** half of an expand/contract migration (docs/WORKFLOW.md): it is dual-written
here so a rolled-back image still reads a correct value, and is dropped in a later release once
no reader is left.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import Boolean, ForeignKey, Select, delete, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Membership
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.schemas import AssigneeRead, AssigneeWrite


class AssigneeLinkMixin:
    """The two columns every assignee join table adds on top of the org/UUID/timestamp mixins.

    ``CASCADE`` on ``user_id``: removing a member removes their assignments (unlike the mirrored
    ``responsible_user_id``, which is ``SET NULL`` so the entity itself is never orphaned).
    """

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AssigneeService:
    """Reads and writes one entity type's assignee links, always tenant-scoped.

    ``link_model`` is that module's join model; ``fk`` names its FK column back to the entity
    (``company_id`` / ``project_id``). Every query filters ``org_id`` explicitly — RLS is only
    the second line of defence (CLAUDE.md §5).
    """

    def __init__(self, ctx: RequestContext, link_model: type, fk: str) -> None:
        self.ctx = ctx
        self.model = link_model
        self.fk = fk

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    @property
    def _entity_column(self):
        return getattr(self.model, self.fk)

    # --- reads --------------------------------------------------------------- #
    def _scoped(self) -> Select:
        return select(self.model).where(self.model.org_id == self._org_id)

    async def for_entities(
        self, entity_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[AssigneeRead]]:
        """Assignees for many entities in **one** query — list endpoints must not N+1
        (docs/PERFORMANCE.md). Primary first, then oldest link first."""
        if not entity_ids:
            return {}
        rows = (
            await self.ctx.session.execute(
                self._scoped()
                .where(self._entity_column.in_(entity_ids))
                .order_by(self.model.is_primary.desc(), self.model.created_at.asc())
            )
        ).scalars().all()
        grouped: dict[uuid.UUID, list[AssigneeRead]] = {eid: [] for eid in entity_ids}
        for row in rows:
            grouped[getattr(row, self.fk)].append(AssigneeRead.model_validate(row))
        return grouped

    async def for_entity(self, entity_id: uuid.UUID) -> list[AssigneeRead]:
        return (await self.for_entities([entity_id]))[entity_id]

    async def primary(self, entity_id: uuid.UUID) -> uuid.UUID | None:
        """The primary assignee's user id, or ``None``. The published way for another module to
        ask "who owns this?" without importing its models (CLAUDE.md §6)."""
        return await self.ctx.session.scalar(
            select(self.model.user_id).where(
                self.model.org_id == self._org_id,
                self._entity_column == entity_id,
                self.model.is_primary.is_(True),
            )
        )

    def entity_ids_for_user(self, user_id: uuid.UUID) -> Select:
        """Subquery of the entities this user is assigned to — primary **or** not.

        The "my clients" / "my projects" filters match any assignee: a filter that only matched
        the primary would make the whole feature invisible to everyone else on the account.
        """
        return select(self._entity_column).where(
            self.model.org_id == self._org_id, self.model.user_id == user_id
        )

    # --- writes -------------------------------------------------------------- #
    async def _ensure_members(self, user_ids: Iterable[uuid.UUID]) -> None:
        ids = list(user_ids)
        if not ids:
            return
        known = set(
            (
                await self.ctx.session.execute(
                    select(Membership.user_id).where(
                        Membership.org_id == self._org_id, Membership.user_id.in_(ids)
                    )
                )
            ).scalars()
        )
        if len(known) != len(set(ids)):
            raise AppError("invalid_assignee", "errors.invalid_assignee", status_code=400)

    def normalize(
        self,
        assignees: Sequence[AssigneeWrite] | None,
        *,
        fallback_primary: uuid.UUID | None = None,
    ) -> list[AssigneeWrite]:
        """De-duplicate, enforce at most one primary, and guarantee one exists.

        ``assignees=None`` means "the caller didn't say" — fall back to the single
        ``responsible_user_id`` it did send, which is what pre-assignees clients still post.
        An unstarred list promotes its first entry, exactly like the contacts picker.
        """
        if assignees is None:
            return (
                [AssigneeWrite(user_id=fallback_primary, is_primary=True)]
                if fallback_primary is not None
                else []
            )

        seen: dict[uuid.UUID, AssigneeWrite] = {}
        for entry in assignees:
            # A repeated user_id keeps the strongest claim it made.
            existing = seen.get(entry.user_id)
            if existing is None or (entry.is_primary and not existing.is_primary):
                seen[entry.user_id] = entry
        links = list(seen.values())

        primaries = [link for link in links if link.is_primary]
        if len(primaries) > 1:
            raise AppError(
                "multiple_primary_assignees", "errors.multiple_primary_assignees", status_code=400
            )
        if not primaries and links:
            links[0] = AssigneeWrite(user_id=links[0].user_id, is_primary=True)
        return links

    @staticmethod
    def primary_of(links: Sequence[AssigneeWrite]) -> uuid.UUID | None:
        return next((link.user_id for link in links if link.is_primary), None)

    async def replace(self, entity_id: uuid.UUID, links: Sequence[AssigneeWrite]) -> None:
        """Set the entity's assignees to exactly ``links`` (already normalized).

        Delete-then-insert rather than a diff: the one-primary partial unique index would reject
        an interleaved promote/demote, and the sets are a handful of rows.
        """
        await self._ensure_members(link.user_id for link in links)
        await self.ctx.session.execute(
            delete(self.model).where(
                self.model.org_id == self._org_id, self._entity_column == entity_id
            )
        )
        await self.ctx.session.flush()
        for link in links:
            self.ctx.session.add(
                self.model(
                    org_id=self._org_id,
                    user_id=link.user_id,
                    is_primary=link.is_primary,
                    **{self.fk: entity_id},
                )
            )
        await self.ctx.session.flush()

    async def set_primary(self, entity_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
        """Move the star without touching the rest of the roster.

        This is the compatibility path for a client that PATCHes only ``responsible_user_id``:
        the named user is added if absent and promoted, the old primary stays on as a regular
        assignee. ``None`` demotes the primary and assigns nobody in their place.
        """
        rows = (
            await self.ctx.session.execute(
                self._scoped().where(self._entity_column == entity_id)
            )
        ).scalars().all()
        for row in rows:
            row.is_primary = False
        await self.ctx.session.flush()  # clear the partial unique index before re-claiming it

        if user_id is None:
            return
        await self._ensure_members([user_id])
        existing = next((row for row in rows if row.user_id == user_id), None)
        if existing is not None:
            existing.is_primary = True
        else:
            self.ctx.session.add(
                self.model(
                    org_id=self._org_id,
                    user_id=user_id,
                    is_primary=True,
                    **{self.fk: entity_id},
                )
            )
        await self.ctx.session.flush()
