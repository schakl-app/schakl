"""google.drive — the reference/link model, no sync, no mirror (docs/GOOGLE.md §5).

The Shared Drive stays the source of truth: ``drive_links`` stores *references* (id + display
metadata), the embedded browser lists live contents as the viewing user, and nothing here ever
copies bytes into the platform. ``drive_folder_jobs`` is the provisioning outbox: the
``company.created``/``project.created`` handlers write a row in the emitter's transaction and
the worker creates the folder with the org's automation connection.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: The entities Drive files attach to. Matching the panels: the hub plus its work records.
DRIVE_ENTITY_TYPES = ("company", "project", "task")


class FolderJobStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    #: Skipped on purpose: provisioning was disabled, or no automation connection exists.
    SKIPPED = "skipped"


class DriveLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "drive_links"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "entity_type", "entity_id", "drive_file_id",
            name="uq_drive_links_org_entity_file",
        ),
        Index("ix_drive_links_org_entity", "org_id", "entity_type", "entity_id"),
    )

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: No FK — polymorphic, like ``files`` and ``activity_log``.
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    drive_file_id: Mapped[str] = mapped_column(String(128), nullable=False)
    drive_url: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_folder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    shared_drive_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Who linked it — snapshot rule (#64): the name survives the account.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DriveFolderJob(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One entity's pending folder — written in-transaction, executed by the worker."""

    __tablename__ = "drive_folder_jobs"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "entity_type", "entity_id", name="uq_drive_folder_jobs_org_entity"
        ),
        Index("ix_drive_folder_jobs_org_status", "org_id", "status"),
    )

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: The folder name, snapshotted at emit time (the worker never re-reads the entity).
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    #: For a project folder: the company whose folder it nests under (resolved at emit time).
    parent_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=FolderJobStatus.PENDING.value,
        server_default="pending",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
