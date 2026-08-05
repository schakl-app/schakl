"""Grant a *later* module's permissions to an existing org's system roles (issue #19).

An org seeded before `subscriptions` shipped has an `admin` role that has never heard of
``subscriptions.read``, and — worse — a `member` role without the module's read permission, which
would make the whole module invisible to members. Giving `admin` a ``"*"`` does not fix the second
half, so both need reconciling.

``org_settings.applied_permission_defaults`` records which catalog keys this org has already been
seeded with. The reconciler diffs the code catalog against it and grants **only the new keys**,
so a tenant who unticked something keeps it unticked.

It runs in the app's lifespan hook, not in a migration: a migration must apply on top of any older
head (`docs/WORKFLOW.md`) and therefore must never import the evolving catalog. Steady state is one
``SELECT`` per org at boot, and a self-hosted instance has exactly one org (CLAUDE.md §5).

**Widening an existing key's defaults is a different event, and the key diff cannot see it**
(#266). ``fresh`` is keyed on ``spec.key``, so adding ``ROLE_CLIENT`` to a permission every org
was seeded with years ago changes nothing for any of them: the key is already applied. That is
not a bug in the diff — per-key is exactly what makes "a tenant who unticked something keeps it
unticked" true, and no per-role diff can distinguish *never offered* from *offered and removed*.
So a widening is recorded as what it actually is: a **one-time revision**, listed in
:data:`REVISIONS` with its own sentinel marker in the same ``applied_permission_defaults`` array.
Each runs once per org, in the boot transaction the reconciler already owns — which is also why
this is not an Alembic migration: it needs the catalog's own vocabulary and the per-org RLS
binding, both of which a migration is forbidden to have.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apikeys.models import ApiKey
from app.core.jobs import run_per_org
from app.core.models import Org, OrgSettings
from app.core.permissions.catalog import ROLE_OWNER, all_permissions
from app.core.permissions.models import Role, RolePermission
from app.core.permissions.service import grant, mark_defaults_applied

logger = logging.getLogger("schakl.permissions")


@dataclass(frozen=True)
class DefaultsRevision:
    """A one-time correction to defaults an org was **already** offered (see the module docstring).

    ``marker`` is stored alongside the catalog keys in ``applied_permission_defaults``; it is
    prefixed so it can never collide with a permission key. ``rescope`` rewrites a stored
    permission string in place — the one thing a plain grant cannot do — and ``grants`` adds
    permissions to a system role the way a fresh spec would.
    """

    marker: str
    #: ``{stored string: replacement}``. Rewriting, not granting: a permission that *becomes*
    #: scoped may no longer be stored bare (``validate_permissions``), so leaving the old string
    #: behind would 422 the tenant's next save of a role that was working fine.
    rescope: dict[str, str] = field(default_factory=dict)
    #: ``{role key: permissions}`` — the widened defaults themselves.
    grants: dict[str, tuple[str, ...]] = field(default_factory=dict)


#: Append-only. A revision that has run is recorded per org and never runs again, so removing an
#: entry here does **not** undo it — write a new revision instead.
REVISIONS: tuple[DefaultsRevision, ...] = (
    DefaultsRevision(
        # #266: clients read their own companies' issued invoices in the portal. The read
        # permission became scoped in the same change, so the admin/custom roles that hold it
        # bare are rewritten to the `:any` they already effectively had (`PermissionSet.has`
        # answers True for a bare key at every scope — this changes no one's access, only how
        # it is spelled), and the seeded `client` role gains the narrow half.
        marker="@rev:266-invoice-read-scoped",
        rescope={"invoicing.invoice.read": "invoicing.invoice.read:any"},
        grants={"client": ("invoicing.invoice.read:own",)},
    ),
    DefaultsRevision(
        # #296: the client portal became its own module, so the permission that was named for
        # where it happened to live is renamed to where it belongs. Pure spelling — the key is
        # gone from the catalog, so a role or API key still holding the old string would fail
        # `validate_permissions` on its own next save while granting exactly what it did before.
        # Nothing is granted here: whoever held it holds it, whoever did not still does not.
        marker="@rev:296-portal-module",
        rescope={"contacts.portal.impersonate": "portal.login.impersonate"},
    ),
)


async def _rescope(
    session: AsyncSession, org_id: uuid.UUID, old: str, new: str
) -> int:
    """Replace every stored ``old`` with ``new``, for roles and for API keys.

    Not an ``UPDATE``: a role may already hold ``new`` (a fresh org seeded after the change,
    reconciled again), and the unique constraint would raise rather than no-op. Delete then
    ``grant`` — which is ``ON CONFLICT DO NOTHING`` — is idempotent either way.
    """
    role_ids = list(
        (
            await session.execute(
                select(RolePermission.role_id).where(
                    RolePermission.org_id == org_id, RolePermission.permission == old
                )
            )
        ).scalars()
    )
    if role_ids:
        await session.execute(
            delete(RolePermission).where(
                RolePermission.org_id == org_id, RolePermission.permission == old
            )
        )
        for role_id in role_ids:
            await grant(session, org_id, role_id, [new])

    # An API key's scopes are validated on write against the same catalog, so a key minted
    # with the bare string would fail its own next edit. Its *effective* access is unchanged
    # (`PermissionSet.has`), so this is spelling, not a re-grant.
    keys = list(
        (
            await session.execute(
                select(ApiKey).where(ApiKey.org_id == org_id, ApiKey.scopes.contains([old]))
            )
        ).scalars()
    )
    for key in keys:
        key.scopes = sorted({new if s == old else s for s in (key.scopes or [])})
    return len(role_ids) + len(keys)


async def _apply_revisions(
    session: AsyncSession,
    org: Org,
    applied: set[str],
    roles: dict[str, Role],
) -> tuple[int, list[str]]:
    """Run every revision this org has not seen; return ``(changes, markers to record)``."""
    changed, markers = 0, []
    for revision in REVISIONS:
        if revision.marker in applied:
            continue
        for old, new in revision.rescope.items():
            changed += await _rescope(session, org.id, old, new)
        for role_key, permissions in revision.grants.items():
            role = roles.get(role_key)
            if role is None or role_key == ROLE_OWNER:
                continue  # owner holds "*" and nothing else, forever
            await grant(session, org.id, role.id, list(permissions))
            changed += 1
        markers.append(revision.marker)
    return changed, markers


async def reconcile_org(org: Org, session: AsyncSession) -> int:
    """Grant this org's system roles any catalog permission they have never been offered,
    and run any :data:`REVISIONS` it has not seen."""
    org_settings = await session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org.id)
    )
    if org_settings is None:
        return 0

    catalog = all_permissions()
    applied = set(org_settings.applied_permission_defaults or ())
    fresh = [spec for spec in catalog if spec.key not in applied]
    pending = [r for r in REVISIONS if r.marker not in applied]
    if not fresh and not pending:
        return 0

    roles = {
        role.key: role
        for role in (
            await session.execute(
                select(Role).where(Role.org_id == org.id, Role.is_system.is_(True))
            )
        ).scalars()
    }
    granted = 0
    for spec in fresh:
        for role_key, permission in spec.default_grants().items():
            if role_key == ROLE_OWNER:
                continue  # owner holds "*" and nothing else, forever
            role = roles.get(role_key)
            if role is None:
                continue
            await grant(session, org.id, role.id, [permission])
            granted += 1

    revised, markers = await _apply_revisions(session, org, applied, roles)

    await mark_defaults_applied(
        session, org.id, [spec.key for spec in catalog] + markers
    )
    logger.info(
        "granted %d new default permission(s) to org %s (%d new capabilities, "
        "%d revision change(s) over %d revision(s))",
        granted,
        org.slug,
        len(fresh),
        revised,
        len(markers),
    )
    return granted + revised


async def reconcile_permission_defaults() -> None:
    """Run :func:`reconcile_org` for every active org, one transaction each.

    Never fatal: a stale catalog is a missing capability, not a broken API, and refusing to boot
    would take a healthy instance down. The failure is logged and retried on the next start.
    """
    try:
        await run_per_org(reconcile_org)
    except Exception:  # noqa: BLE001 - boot must not depend on this succeeding
        logger.exception("permission default reconciliation failed")
