"""Request/response models for the roles API (issue #19)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PermissionRead(BaseModel):
    """One entry of the code-defined catalog, as the permission matrix renders it."""

    key: str
    #: ``()`` for an unscoped permission, ``("own", "any")`` for a scoped one.
    scopes: list[str]
    #: i18n message key (``permissions.<key>``), resolved by the web catalog.
    label_key: str
    #: The module this groups under in the matrix.
    group: str
    position: int


class PermissionCatalog(BaseModel):
    permissions: list[PermissionRead]
    #: Group order, so the UI doesn't have to re-derive it.
    groups: list[str]


class RoleRead(BaseModel):
    id: str
    key: str
    name_i18n: dict[str, str]
    description_i18n: dict[str, str]
    is_system: bool
    position: int
    #: Stored permission strings — scoped ones carry their ``:own`` / ``:any`` suffix. The
    #: ``owner`` role holds exactly ``["*"]``.
    permissions: list[str]
    member_count: int


class RoleCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name_i18n: dict[str, str] = Field(default_factory=dict)
    description_i18n: dict[str, str] = Field(default_factory=dict)
    position: int = 100
    #: Omit together with ``?from=`` to copy the source role's permissions.
    permissions: list[str] | None = None


class RoleUpdate(BaseModel):
    """``key`` is immutable, deliberately: it is what ``role_permissions`` rows point at."""

    name_i18n: dict[str, str] | None = None
    description_i18n: dict[str, str] | None = None
    position: int | None = None
    #: The **whole** permission set, replaced in one save (docs/UX.md: one save per surface).
    permissions: list[str] | None = None


class MembershipRolesUpdate(BaseModel):
    #: The whole role set for this membership, replaced in one save.
    role_ids: list[str]


class EffectivePermissions(BaseModel):
    membership_id: str
    user_id: str
    role_ids: list[str]
    #: ``["*"]`` for an owner.
    permissions: list[str]
