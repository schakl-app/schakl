"""``ActivityLog`` — the core, cross-cutting paper trail (issue #67).

Every mutable domain record should carry a visible trail of *what changed, by whom, when* —
the same platform guarantee as tenant isolation (§5) and permissions (§15), not something each
module reimplements or silently omits. This is that trail, promoted out of the tasks module
(``TaskActivity``) into core as a single polymorphic, org-scoped, RLS-forced table.

- Polymorphic ``(entity_type, entity_id)`` with **no FK** on ``entity_id``: the trail must
  outlive the record it describes (an audit that vanishes with the row is not an audit).
- ``actor_name`` snapshots the actor's display name at write time (issue #64). The FK to
  ``users`` is ``ON DELETE SET NULL``, so without the snapshot, deleting an account would
  silently rewrite that person's history into the system's. A name with no ``actor_user_id``
  is a departed human; no name at all is genuinely the system.
- ``action`` maps to an ``activity.action.*`` i18n key; ``payload`` carries the detail
  (e.g. ``{"changes": {"name": {"from": …, "to": …}}}``).

A module opts an entity in with ``AuditableMixin`` (registers the ``entity_type``); a
core-contributed panel then renders the trail on that entity's detail page.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class ActivityLog(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only audit trail for any auditable entity; ``action`` → ``activity.action.*``."""

    __tablename__ = "activity_log"
    __table_args__ = (
        Index(
            "ix_activity_log_entity",
            "org_id",
            "entity_type",
            "entity_id",
            "created_at",
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # No FK: the trail survives the record's deletion (polymorphic, like NotificationEvent).
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    # NULL actor = the system. ``actor_name`` disambiguates a departed human from the system
    # once the FK is SET NULL (issue #64) — written on every record, live join wins while the
    # account exists.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
