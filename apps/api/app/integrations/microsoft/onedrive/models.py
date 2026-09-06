"""microsoft.onedrive — the reference/link model, no sync, no mirror (docs/MICROSOFT.md §5).

OneDrive (or the SharePoint document library the agency works in) stays the source of truth:
``onedrive_links`` stores *references* (drive + item id plus display metadata), the embedded
browser lists live contents as the viewing user, and nothing here ever copies bytes into the
platform. ``onedrive_folder_jobs`` is the provisioning outbox: the ``company.created`` /
``project.created`` handlers write a row in the emitter's transaction and the worker creates the
folder with the org's automation connection.

A Graph item is addressed by **two** ids — the drive it lives in and the item — where a Drive
file is one. Both are stored, because a link into a SharePoint library and a link into somebody's
personal OneDrive are otherwise indistinguishable strings.
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

#: The entities OneDrive files attach to. Matching the panels: the hub plus its work records.
ONEDRIVE_ENTITY_TYPES = ("company", "project", "task")


class FolderJobStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    #: Skipped on purpose: provisioning was disabled, or no automation connection exists.
    SKIPPED = "skipped"


class OneDriveLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "onedrive_links"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "entity_type", "entity_id", "drive_id", "item_id",
            name="uq_onedrive_links_org_entity_item",
        ),
        Index("ix_onedrive_links_org_entity", "org_id", "entity_type", "entity_id"),
        Index("ix_onedrive_links_org_item", "org_id", "drive_id", "item_id"),
        # A record has **at most one** folder, and which one is a decision, not row order
        # (docs/GOOGLE.md §5's rule, kept here for the same reason).
        Index(
            "uq_onedrive_links_org_entity_root",
            "org_id", "entity_type", "entity_id",
            unique=True,
            postgresql_where=text("is_root"),
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: No FK — polymorphic, like ``files`` and ``activity_log``.
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    drive_id: Mapped[str] = mapped_column(String(256), nullable=False)
    item_id: Mapped[str] = mapped_column(String(256), nullable=False)
    web_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_folder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: This link *is* the record's folder — where its browser opens, where uploads land, and
    #: what a child's folder nests under. Only ever true for a folder.
    is_root: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Who linked it — snapshot rule (#64): the name survives the account.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OneDriveFolderJob(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One entity's pending folder — written in-transaction, executed by the worker."""

    __tablename__ = "onedrive_folder_jobs"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "entity_type", "entity_id", name="uq_onedrive_folder_jobs_org_entity"
        ),
        Index("ix_onedrive_folder_jobs_org_status", "org_id", "status"),
    )

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: The folder name, snapshotted at emit time (the worker never re-reads the entity).
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    #: The record whose folder this one nests under, resolved at emit time; the *folder* is
    #: resolved at execution time — the parent may still have been folderless when this row
    #: was written (docs/GOOGLE.md §5, #328).
    parent_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    parent_entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=FolderJobStatus.PENDING.value,
        server_default="pending",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
