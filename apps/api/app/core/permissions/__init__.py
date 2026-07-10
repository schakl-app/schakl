"""Role-based access control (issue #19, CLAUDE.md §6).

Tenant-defined **roles** carry explicitly granted **permissions** drawn from a code-defined
registry. Each module declares its own permissions on its ``ModuleDescriptor``; core declares
core's. A membership may hold several roles and its effective permissions are their union,
resolved once per request and cached on ``RequestContext``.

Two axes that never mix:

* **RLS** enforces tenant isolation (Golden Rule 1) — no permission is ever written into a policy.
* **RBAC** enforces capability *within* a tenant, in the app layer, deny-by-default.
"""

from app.core.permissions.catalog import (
    CORE_PERMISSIONS,
    PRIVILEGE_ORDER,
    ROLE_ADMIN,
    ROLE_CLIENT,
    ROLE_MEMBER,
    ROLE_OWNER,
    SYSTEM_ROLE_KEYS,
    SYSTEM_ROLES,
    all_permissions,
    default_permissions_for,
    permission_keys,
)
from app.core.permissions.models import MembershipRole, Role, RolePermission
from app.core.permissions.permset import PermissionSet
from app.core.permissions.spec import SCOPE_ANY, SCOPE_OWN, SCOPES, WILDCARD, PermissionSpec

__all__ = [
    "CORE_PERMISSIONS",
    "PRIVILEGE_ORDER",
    "ROLE_ADMIN",
    "ROLE_CLIENT",
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "SCOPES",
    "SCOPE_ANY",
    "SCOPE_OWN",
    "SYSTEM_ROLES",
    "SYSTEM_ROLE_KEYS",
    "WILDCARD",
    "MembershipRole",
    "PermissionSet",
    "PermissionSpec",
    "Role",
    "RolePermission",
    "all_permissions",
    "default_permissions_for",
    "permission_keys",
]
