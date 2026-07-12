"""``files`` — metadata for every stored blob (issue #123).

The filesystem has no RLS; this row does (org-scoped, forced), which is what keeps tenant
isolation true for bytes. Consumers reference the file **id**, never a path, so the backend
can change (local → Drive) without rewriting any column, and orphan cleanup stays possible.
``entity_type``/``entity_id`` say what the file hangs off (avatar, task attachment, logo) —
untyped on purpose, like the activity log: the file may outlive the record.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class StoredFile(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "files"

    #: Which backend holds the bytes ("local", later "gdrive"/"s3") — per row, so a backend
    #: migration can move files gradually.
    backend: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Opaque key inside the backend; layout ``<org_id>/<file_id>`` (org in the path on purpose).
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
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
