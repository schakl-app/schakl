"""``hr_documents`` — the employee dossier (personal page).

HR-adjacent like ``leave``, but its own module: leave is *absence*, this is the employment
paper trail — contract copies, growth plans, bonus agreements, benefits, CAO. "Employees"
are the org's users/memberships (never ``contacts``, §14). Files ride the storage core
(#123); this table is the typed index over them, per user and per category.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: The dossier's fixed category vocabulary; labels are i18n keys (`hr.category.<key>`), so
#: the vocabulary itself never carries user-facing text.
DOCUMENT_CATEGORIES = ("contract", "growth_plan", "bonus", "benefits", "cao", "other")


class HrDocument(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "hr_documents"
    __table_args__ = (Index("ix_hr_documents_user", "org_id", "user_id"),)

    #: The employee this document belongs to. CASCADE: a deleted account takes its dossier
    #: rows along — the underlying files are cleaned up by the service, never orphan-served.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The stored blob (#123). CASCADE both ways keeps index and bytes-row in step.
    file_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Who filed it — snapshotted (§16/#64): the dossier outlives the admin who filled it.
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
