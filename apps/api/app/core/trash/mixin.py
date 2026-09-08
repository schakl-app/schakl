"""``TrashableMixin`` — opt an entity into the core trash can (docs/TRASH.md).

Three columns and nothing else: *when* the row was put in the trash, and *who* did it — as an
id while the account exists and as a snapshot for after it does not, the activity trail's own
rule (§16). A row with ``deleted_at`` set is in the trash: every read through the tenant-scoped
repository leaves it out (``TenantScopedRepository.trash_condition``), and so does every read of
a row that *belongs* to it through a ``company_id`` column, so a client in the trash takes its
tasks and its contact moments out of sight with it and a restore brings them all back unchanged.

What the mixin does **not** do is decide what a delete means for the entity — which dependents
stop it, what goes with it, how long it is kept. That is the module's :class:`TrashableSpec`
on its ``ModuleDescriptor``, exactly as a bulk delete is described rather than inherited.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

#: ``__tablename__`` of every trashable model, filled at class-definition time. Read by
#: ``app.core.parent.ensure_parent_in_tenant`` so a raw "does this parent exist" check — the
#: shape most modules use to validate a ``company_id`` on write — refuses a parent in the trash
#: without each of them learning the column.
_TRASHABLE_TABLES: set[str] = set()


class TrashableMixin:
    #: When the row went into the trash; ``NULL`` is "live", the only state every read admits.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who put it there — the live account while it exists (``ON DELETE SET NULL``)…
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: …and their name as it was, so the trash can still say who after they have left (§16).
    deleted_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        table = getattr(cls, "__tablename__", None)
        if table:
            _TRASHABLE_TABLES.add(table)


def is_trashable(model: type) -> bool:
    """Does ``model`` carry the trash columns? Answered by inheritance, never by attribute
    name — a module that happens to own a ``deleted_at`` of its own is not thereby in the trash."""
    return isinstance(model, type) and issubclass(model, TrashableMixin)


def trashable_table(table: str) -> bool:
    return table in _TRASHABLE_TABLES
