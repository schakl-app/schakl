"""What an instance admin may do (issue #26 — delegated instance access).

Instance access used to be one boolean: ``users.is_superuser``, all-or-nothing across every
org on the box. That makes a second operator impossible to hire safely — a support person who
should read an org to help them also gets impersonation, purge and every customer's files.

So there are two principals, and only one of them can delegate:

======  =============================  =========================  ==================
        who                            capabilities               may manage people
======  =============================  =========================  ==================
owner   ``users.is_superuser``         implicitly **all**         yes
admin   a row in ``instance_admins``   exactly what was granted   **no**
======  =============================  =========================  ==================

Granting is **owner-only and deliberately not itself a capability**. An admin who could grant
``instance.impersonate`` to themselves is an owner with extra steps, so the escalation edge
simply does not exist. Owners may promote another owner, so this is not a bus factor.

**This is not the org RBAC catalog** (CLAUDE.md §15) and must not become it. Those permissions
are org-scoped, stored in an org-scoped `role_permissions` table, and resolved through RLS;
instance access is a third axis that crosses every tenant. Putting a cross-tenant grant in an
org-scoped table would be the exact category error §15's "RLS ≠ RBAC" warns about.

The catalog is small, fixed and code-defined on purpose: it is a security boundary, not tenant
configuration, so it ships in the repo and is reviewable in a diff.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilitySpec:
    """One thing an instance admin can be granted.

    ``group`` only orders the console's checkboxes; ``label_key`` is an i18n key, because the
    console renders in the operator's own language like everything else (CLAUDE.md §2).
    """

    key: str
    label_key: str
    group: str
    #: Marks the ones that read or destroy a tenant's own data, so the console can warn before
    #: ticking them. Advisory for the UI — the API refuses on the key, not on this flag.
    sensitive: bool = False


#: Read the org list and one org's detail. The baseline: an admin with nothing else can see
#: which orgs exist and their status, and do nothing at all.
ORGS_READ = "instance.orgs.read"
AUDIT_READ = "instance.audit.read"
ORGS_WRITE = "instance.orgs.write"
LIFECYCLE_WRITE = "instance.lifecycle.write"
DATA_EXPORT = "instance.data.export"
IMPERSONATE = "instance.impersonate"
ORGS_PURGE = "instance.orgs.purge"
KEYS_MANAGE = "instance.keys.manage"

CATALOG: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(ORGS_READ, "instance.cap.orgs_read", "orgs"),
    CapabilitySpec(ORGS_WRITE, "instance.cap.orgs_write", "orgs"),
    CapabilitySpec(LIFECYCLE_WRITE, "instance.cap.lifecycle_write", "orgs"),
    CapabilitySpec(AUDIT_READ, "instance.cap.audit_read", "oversight"),
    # The three that reach a tenant's own contents or end it. On cloud each additionally needs
    # an org-issued service PIN (docs/CLOUD.md) — this grant is necessary, never sufficient.
    CapabilitySpec(DATA_EXPORT, "instance.cap.data_export", "data", sensitive=True),
    CapabilitySpec(IMPERSONATE, "instance.cap.impersonate", "data", sensitive=True),
    CapabilitySpec(ORGS_PURGE, "instance.cap.orgs_purge", "data", sensitive=True),
    CapabilitySpec(KEYS_MANAGE, "instance.cap.keys_manage", "platform"),
)

CAPABILITY_KEYS: frozenset[str] = frozenset(spec.key for spec in CATALOG)


def validate(capabilities: list[str]) -> list[str]:
    """Normalise a granted set: known keys only, de-duplicated, catalog order.

    Unknown keys are rejected rather than dropped — silently ignoring one would hand back a
    200 for a grant that did not happen, and the caller would believe the person holds it.
    """
    unknown = sorted(set(capabilities) - CAPABILITY_KEYS)
    if unknown:
        from app.errors import AppError

        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"capabilities": "errors.unknown_capability"},
        )
    granted = set(capabilities)
    return [spec.key for spec in CATALOG if spec.key in granted]
