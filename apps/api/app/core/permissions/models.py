"""RBAC tables: ``roles``, ``role_permissions``, ``membership_roles`` (issue #19).

All three are org-scoped and RLS-forced in their migration — a tenant defines its own roles and
can never see another tenant's (Golden Rule 1). RLS enforces *tenant isolation*; these tables
enforce *capability within* a tenant. The two are separate concerns and never mix: no permission
is ever expressed in an RLS policy.

A membership may hold several roles; its effective permissions are the union of theirs. The
permission strings themselves — including the owner's ``"*"`` — live on ``role_permissions``, so
resolving a request never has to join ``roles``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class Role(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant-defined role. The four ``is_system`` ones are seeded per org.

    System roles are undeletable and their ``key`` is immutable, but — ``owner`` excepted —
    their permissions are editable and they can be duplicated into a custom role. That is how
    an agency loosens the deliberately restrictive ``member`` default from Instellingen → Rollen.
    """

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_roles_org_id_key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Per-locale names, e.g. {"nl": "Beheerder", "en": "Admin"} — tenant data, not i18n keys.
    name_i18n: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    description_i18n: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class RolePermission(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One granted permission string, from the code-defined registry — never free text."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "role_id", "permission", name="uq_role_permissions_org_id_role_id_permission"
        ),
        Index("ix_role_permissions_role_id_permission", "role_id", "permission"),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    permission: Mapped[str] = mapped_column(String(128), nullable=False)


class MembershipRole(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Many-to-many: a membership holds one or more roles; permissions are their union."""

    __tablename__ = "membership_roles"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "membership_id",
            "role_id",
            name="uq_membership_roles_org_id_membership_id_role_id",
        ),
        Index("ix_membership_roles_membership_id", "membership_id"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
