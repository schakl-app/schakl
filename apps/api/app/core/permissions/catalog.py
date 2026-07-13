"""The permission catalog: core's own capabilities, plus every enabled module's (issue #19).

Core declares the permissions of the surfaces core owns (members, settings, dashboard) and
nothing else. A module declares its own on its :class:`~app.registry.ModuleDescriptor`, so
shipping a new module ships its permissions with it — core is never edited.

The four **system roles** are seeded per org from ``PermissionSpec.default_roles`` /
``default_own_roles``. ``owner`` is special: it stores exactly ``["*"]``, immutable. ``admin``
gets an *explicit* full list rather than a wildcard, so a tenant can still restrict it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from app.config import settings
from app.core.permissions.spec import WILDCARD, PermissionSpec
from app.registry import registry

# --------------------------------------------------------------------------- #
# System roles
# --------------------------------------------------------------------------- #
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_CLIENT = "client"

#: Highest privilege first. Used to collapse a multi-role membership back to a single legacy
#: ``memberships.role`` value for the release-N dual-write (issue #19, rollback decision).
PRIVILEGE_ORDER: tuple[str, ...] = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT)

#: Every role a person can actually hold a *read* with. ``owner`` is absent on purpose: it
#: stores ``"*"`` and nothing else.
_ALL = (ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT)


@dataclass(frozen=True)
class SystemRoleSpec:
    key: str
    position: int
    name_i18n: dict[str, str]
    description_i18n: dict[str, str]


SYSTEM_ROLES: tuple[SystemRoleSpec, ...] = (
    SystemRoleSpec(
        ROLE_OWNER,
        10,
        {"nl": "Eigenaar", "en": "Owner"},
        {"nl": "Volledige toegang tot alles.", "en": "Full access to everything."},
    ),
    SystemRoleSpec(
        ROLE_ADMIN,
        20,
        {"nl": "Beheerder", "en": "Administrator"},
        {
            "nl": "Beheert het bureau, de medewerkers en alle gegevens.",
            "en": "Manages the agency, its people and all data.",
        },
    ),
    SystemRoleSpec(
        ROLE_MEMBER,
        30,
        {"nl": "Medewerker", "en": "Member"},
        {
            "nl": "Leest mee, werkt aan eigen taken, uren en verlof.",
            "en": "Reads along; works on their own tasks, hours and leave.",
        },
    ),
    SystemRoleSpec(
        ROLE_CLIENT,
        40,
        {"nl": "Klant", "en": "Client"},
        {
            "nl": "Externe klantgebruiker: alleen lezen.",
            "en": "External client user: read-only.",
        },
    ),
)
SYSTEM_ROLE_KEYS: tuple[str, ...] = tuple(role.key for role in SYSTEM_ROLES)


# --------------------------------------------------------------------------- #
# Core catalog
# --------------------------------------------------------------------------- #
CORE_PERMISSIONS: tuple[PermissionSpec, ...] = (
    # --- members ---------------------------------------------------------- #
    PermissionSpec("members.member.read", group="members", position=10),
    PermissionSpec("members.member.write", group="members", position=20),
    # --- settings --------------------------------------------------------- #
    PermissionSpec("settings.roles.manage", group="settings", position=10),
    PermissionSpec("settings.branding.write", group="settings", position=20),
    PermissionSpec("settings.domain.read", group="settings", position=30),
    PermissionSpec("settings.domain.write", group="settings", position=40),
    PermissionSpec(
        "settings.customfields.read", group="settings", position=50, default_roles=_ALL
    ),
    PermissionSpec("settings.customfields.write", group="settings", position=60),
    PermissionSpec("settings.dashboard.manage", group="settings", position=70),
    # The org-wide sidebar default everyone without a personal layout inherits (#169).
    PermissionSpec("settings.nav.manage", group="settings", position=75),
    PermissionSpec("settings.system.read", group="settings", position=80),
    # Provider catalog (issue #89): all staff read it to fill pickers; managing it is admin-only.
    PermissionSpec(
        "settings.providers.read", group="settings", position=90, default_roles=_ALL
    ),
    PermissionSpec("settings.providers.manage", group="settings", position=100),
    # Org e-mail transport (issue #17): embeds API keys / SMTP credentials, admin-only.
    PermissionSpec("settings.email.manage", group="settings", position=110),
    # Single sign-on (issue #76): embeds the IdP client secret and its "enforce" toggle can
    # turn password login off for the whole org — admin-only (owner via the wildcard).
    PermissionSpec("settings.auth.manage", group="settings", position=120),
    # --- dashboard (personal My Day layout) ------------------------------- #
    PermissionSpec("dashboard.prefs.read", group="dashboard", position=10, default_roles=_ALL),
    PermissionSpec("dashboard.prefs.write", group="dashboard", position=20, default_roles=_ALL),
    # --- nav (personal sidebar layout, #169) ------------------------------ #
    PermissionSpec("nav.prefs.read", group="dashboard", position=30, default_roles=_ALL),
    PermissionSpec("nav.prefs.write", group="dashboard", position=40, default_roles=_ALL),
    # --- activity trail (issue #67) --------------------------------------- #
    # Every role that can reach a record's detail page may read its paper trail; the rows are
    # org-scoped (RLS), so this never crosses a tenant. Recording is not a permission — it is a
    # side effect of a write the caller was already allowed to make.
    PermissionSpec("activity.read", group="activity", position=10, default_roles=_ALL),
    # --- API keys & service accounts (issue #20) -------------------------- #
    # Any member may mint personal keys for themselves (capped by their own live permissions);
    # only an admin manages service accounts, which are shared, employee-outliving principals.
    PermissionSpec(
        "apikeys.personal.manage",
        group="apikeys",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec("apikeys.service_account.manage", group="apikeys", position=20),
    # --- file storage (issue #123) ----------------------------------------- #
    # Uploading is a staff act (avatars, attachments, logos); reading is any member — the
    # serve route is RLS-scoped and declares no permission on purpose.
    PermissionSpec(
        "files.file.write",
        group="files",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    # --- AI assistance (epic #131) ----------------------------------------- #
    # ``ai.use`` covers every AI feature for staff (a split into per-feature keys waits until
    # roles genuinely need to differ, #126); clients get none by default — AI reads across
    # the very records a client role is scoped away from. Managing the provider key and
    # budget is admin-only, like the email transport.
    PermissionSpec(
        "ai.use",
        group="ai",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec("ai.settings.manage", group="ai", position=20),
)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _ensure_modules_registered() -> None:
    """Import the enabled modules so their permissions are in the registry.

    ``main.py`` already does this for a serving process, but ``seed_system_roles`` also runs from
    the worker, from the first-run wizard's import path and from scripts. A catalog that silently
    degraded to core-only would seed an ``admin`` role unable to touch a single module — and
    nothing would say so. Module import is idempotent (``importlib`` caches).
    """
    if all(registry.get(name) is not None for name in settings.enabled_modules):
        return
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")


def all_permissions() -> list[PermissionSpec]:
    """Core's catalog plus the catalogs of every module mounted in this deployment.

    Ordered by ``(group, position, key)`` — the order the permission matrix renders in.
    """
    _ensure_modules_registered()
    specs: list[PermissionSpec] = list(CORE_PERMISSIONS)
    for module in registry.enabled(settings.enabled_modules):
        specs.extend(module.permissions)
    _assert_unique(specs)
    return sorted(specs, key=lambda s: (s.module, s.position, s.key))


def _assert_unique(specs: list[PermissionSpec]) -> None:
    seen: set[str] = set()
    for spec in specs:
        if spec.key in seen:
            raise ValueError(f"duplicate permission key '{spec.key}'")
        seen.add(spec.key)


def permission_keys() -> list[str]:
    """The catalog's spec keys (unsuffixed), used to track ``applied_permission_defaults``."""
    return [spec.key for spec in all_permissions()]


def default_permissions_for(role_key: str) -> list[str]:
    """The stored permission strings a freshly seeded system role holds."""
    if role_key == ROLE_OWNER:
        return [WILDCARD]
    granted = [
        permission
        for spec in all_permissions()
        for role, permission in spec.default_grants().items()
        if role == role_key
    ]
    return sorted(granted)


__all__ = [
    "CORE_PERMISSIONS",
    "PRIVILEGE_ORDER",
    "ROLE_ADMIN",
    "ROLE_CLIENT",
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "SYSTEM_ROLES",
    "SYSTEM_ROLE_KEYS",
    "SystemRoleSpec",
    "all_permissions",
    "default_permissions_for",
    "permission_keys",
]
