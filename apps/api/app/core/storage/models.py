"""``files`` — metadata for every stored blob (issue #123) — and ``file_blobs``, the bytes.

The filesystem has no RLS; this row does (org-scoped, forced), which is what keeps tenant
isolation true for bytes. Consumers reference the file **id**, never a path, so the backend
can change (local → Drive) without rewriting any column, and orphan cleanup stays possible.
``entity_type``/``entity_id`` say what the file hangs off (avatar, task attachment, logo) —
untyped on purpose, like the activity log: the file may outlive the record.

A ``files`` row used to *be* the bytes — one object per row — which meant the signature logo
on 500 e-mails was 500 objects. ``file_blobs`` splits *what the bytes are* (a sha256, stored
once) from *what a file is to its entity* (its name, type, owner and attachment point), so
many rows share one object. **Per org, never across orgs**: keys stay under ``<org_id>/`` so
``delete_prefix`` on org termination is still correct and no tenant's bytes are ever reachable
from another's key space (Golden Rule 1).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class FileBlob(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One stored object, identified by the sha256 of its content.

    There is no ``ref_count`` column on purpose: the ``files`` rows **are** the reference
    count, so it cannot drift under an org import, an archive restore or a cascade delete.
    ``unreferenced_since`` is stamped by the sweeper the first time it finds a blob with no
    referencing row, and the blob is reclaimed only once that stamp is older than the grace
    window — two passes, so a blob is never collected in the same breath as the delete that
    (perhaps temporarily) unreferenced it.
    """

    __tablename__ = "file_blobs"
    __table_args__ = (
        # Per org (Golden Rule 1) *and* per backend: an instance mid-migration from the local
        # volume to S3 holds the same bytes in both, and those are two different objects.
        UniqueConstraint("org_id", "backend", "sha256", name="uq_file_blobs_org_backend_sha"),
    )

    backend: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Lowercase hex sha256 of the content — the identity of the *object*, not of the file.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The key inside the backend. A new blob is content-addressed (``<org_id>/sha256/<hex>``)
    #: so two racing writers put identical bytes to the same key; a blob adopted from a
    #: pre-dedup file keeps that file's original key, because adopting is a row write and must
    #: never be a byte copy.
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unreferenced_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StoredFile(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "files"

    #: Which backend holds the bytes ("local", later "gdrive"/"s3") — per row, so a backend
    #: migration can move files gradually. Mirrored from the blob when there is one, so a
    #: rolled-back release still reads every file from the row alone (docs/WORKFLOW.md).
    backend: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Opaque key inside the backend; ``<org_id>/<file_id>`` before dedup, the blob's key
    #: since. Resolve it through ``blobs.location_of``, never by reading this column directly.
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The shared object holding this file's bytes. ``NULL`` on a pre-dedup row: it owns its
    #: own object at ``storage_key`` and keeps the original delete-the-bytes behaviour until
    #: the maintenance job folds it.
    blob_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        # Not CASCADE — that would make removing a blob delete the files that describe it,
        # which is a data-loss path. Not RESTRICT either: purging an org cascades from
        # ``orgs`` into *both* tables at once and an immediate check would refuse whichever
        # arrived first. Deferred is the shape that says both things: within one transaction
        # any order is fine, and at commit a ``files`` row may never point at a blob that is
        # gone.
        ForeignKey(
            "file_blobs.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"
        ),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
