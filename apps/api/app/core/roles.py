"""Membership roles within an org (CLAUDE.md §5)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    CLIENT = "client"

    @property
    def can_manage(self) -> bool:
        """Owners and admins may create/update/delete and approve."""
        return self in {Role.OWNER, Role.ADMIN}


ROLE_VALUES: tuple[str, ...] = tuple(r.value for r in Role)
