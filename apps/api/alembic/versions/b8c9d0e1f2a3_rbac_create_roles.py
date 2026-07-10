"""rbac_create_roles

Tenant-defined roles carrying granular permissions (issue #19). Three org-scoped, RLS-forced
tables — ``roles``, ``role_permissions``, ``membership_roles`` — plus
``org_settings.applied_permission_defaults``, which records which catalog keys an org's system
roles have already been offered.

**Upgrade path.** Purely additive: three new tables and one new column with a server default, so
any head from ``a4fb5ebe1dc8`` onward upgrades cleanly, populated or not. ``memberships.role``
stays and keeps being dual-written by the app, so rolling the image back to the previous release
lands old code on a schema it can still read (expand/contract, `docs/WORKFLOW.md`). It is dropped
in the *next* release, not this one.

**Behaviour change, deliberate.** The seeded ``member`` role is read-only apart from creating
tasks, commenting, editing tasks assigned to them, logging their own hours and requesting their
own leave. Existing members therefore lose the ability to create a company, edit a contact, edit a
project, or edit a task they are not assigned to. Every one of those is re-grantable by ticking a
box in Instellingen → Rollen. See `docs/DEPLOY.md` for the operator steps.

The permission strings below are written **literally**, never imported from
``app.core.permissions.catalog``: frozen history must not follow evolving code. Permissions that a
later module introduces are granted by the app's startup reconciler, not by a migration.

The backfill is idempotent (``ON CONFLICT DO NOTHING`` / ``NOT EXISTS`` throughout) and runs
per org with the RLS GUC bound, because the new tables are ``FORCE ROW LEVEL SECURITY`` and
migrations run as ``vlotr_app``, not a superuser.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-10 12:00:00.000000
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: str | None = 'a7b8c9d0e1f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("membership_roles", "role_permissions", "roles")

# --------------------------------------------------------------------------- #
# The catalog as it stands in *this* release. A literal snapshot, on purpose.
# --------------------------------------------------------------------------- #
_SYSTEM_ROLES: tuple[tuple[str, int, dict[str, str], dict[str, str]], ...] = (
    (
        "owner",
        10,
        {"nl": "Eigenaar", "en": "Owner"},
        {"nl": "Volledige toegang tot alles.", "en": "Full access to everything."},
    ),
    (
        "admin",
        20,
        {"nl": "Beheerder", "en": "Administrator"},
        {
            "nl": "Beheert het bureau, de medewerkers en alle gegevens.",
            "en": "Manages the agency, its people and all data.",
        },
    ),
    (
        "member",
        30,
        {"nl": "Medewerker", "en": "Member"},
        {
            "nl": "Leest mee, werkt aan eigen taken, uren en verlof.",
            "en": "Reads along; works on their own tasks, hours and leave.",
        },
    ),
    (
        "client",
        40,
        {"nl": "Klant", "en": "Client"},
        {"nl": "Externe klantgebruiker: alleen lezen.", "en": "External client user: read-only."},
    ),
)

_CATALOG_KEYS: tuple[str, ...] = (
    "companies.company.delete",
    "companies.company.read",
    "companies.company.write",
    "contacts.contact.delete",
    "contacts.contact.read",
    "contacts.contact.write",
    "contacts.link.write",
    "dashboard.prefs.read",
    "dashboard.prefs.write",
    "leave.entitlement.read",
    "leave.entitlement.write",
    "leave.profile.manage",
    "leave.request.approve",
    "leave.request.read",
    "leave.request.write",
    "leave.type.write",
    "members.member.read",
    "members.member.write",
    "notifications.defaults.manage",
    "notifications.notification.read",
    "notifications.notification.write",
    "projects.project.delete",
    "projects.project.read",
    "projects.project.write",
    "settings.branding.write",
    "settings.customfields.read",
    "settings.customfields.write",
    "settings.dashboard.manage",
    "settings.domain.read",
    "settings.domain.write",
    "settings.roles.manage",
    "settings.system.read",
    "tasks.checklist_template.write",
    "tasks.comment.write",
    "tasks.label.write",
    "tasks.task.create",
    "tasks.task.delete",
    "tasks.task.read",
    "tasks.task.write",
    "tasks.template.apply",
    "tasks.template.write",
    "time.entry.approve",
    "time.entry.invoice",
    "time.entry.read",
    "time.entry.write",
    "time.report.read",
)

_ADMIN_PERMISSIONS: tuple[str, ...] = (
    "companies.company.delete",
    "companies.company.read",
    "companies.company.write",
    "contacts.contact.delete",
    "contacts.contact.read",
    "contacts.contact.write",
    "contacts.link.write",
    "dashboard.prefs.read",
    "dashboard.prefs.write",
    "leave.entitlement.read:any",
    "leave.entitlement.write",
    "leave.profile.manage",
    "leave.request.approve",
    "leave.request.read:any",
    "leave.request.write:any",
    "leave.type.write",
    "members.member.read",
    "members.member.write",
    "notifications.defaults.manage",
    "notifications.notification.read",
    "notifications.notification.write",
    "projects.project.delete",
    "projects.project.read",
    "projects.project.write",
    "settings.branding.write",
    "settings.customfields.read",
    "settings.customfields.write",
    "settings.dashboard.manage",
    "settings.domain.read",
    "settings.domain.write",
    "settings.roles.manage",
    "settings.system.read",
    "tasks.checklist_template.write",
    "tasks.comment.write:any",
    "tasks.label.write",
    "tasks.task.create",
    "tasks.task.delete",
    "tasks.task.read",
    "tasks.task.write:any",
    "tasks.template.apply",
    "tasks.template.write",
    "time.entry.approve",
    "time.entry.invoice",
    "time.entry.read:any",
    "time.entry.write:any",
    "time.report.read",
)

_MEMBER_PERMISSIONS: tuple[str, ...] = (
    "companies.company.read",
    "contacts.contact.read",
    "dashboard.prefs.read",
    "dashboard.prefs.write",
    "leave.entitlement.read:own",
    "leave.request.read:own",
    "leave.request.write:own",
    "notifications.notification.read",
    "notifications.notification.write",
    "projects.project.read",
    "settings.customfields.read",
    "tasks.comment.write:own",
    "tasks.task.create",
    "tasks.task.read",
    "tasks.task.write:own",
    "time.entry.read:own",
    "time.entry.write:own",
)

_CLIENT_PERMISSIONS: tuple[str, ...] = (
    "companies.company.read",
    "contacts.contact.read",
    "dashboard.prefs.read",
    "dashboard.prefs.write",
    "notifications.notification.read",
    "notifications.notification.write",
    "projects.project.read",
    "settings.customfields.read",
    "tasks.task.read",
)

_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "owner": ("*",),
    "admin": _ADMIN_PERMISSIONS,
    "member": _MEMBER_PERMISSIONS,
    "client": _CLIENT_PERMISSIONS,
}


def upgrade() -> None:
    op.create_table(
        'roles',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('name_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('description_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_roles_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
        sa.UniqueConstraint('org_id', 'key', name='uq_roles_org_id_key'),
    )
    op.create_index(op.f('ix_roles_org_id'), 'roles', ['org_id'], unique=False)

    op.create_table(
        'role_permissions',
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('permission', sa.String(length=128), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_role_permissions_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_role_permissions_role_id_roles'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_role_permissions')),
        sa.UniqueConstraint('org_id', 'role_id', 'permission', name='uq_role_permissions_org_id_role_id_permission'),
    )
    op.create_index(op.f('ix_role_permissions_org_id'), 'role_permissions', ['org_id'], unique=False)
    op.create_index(
        'ix_role_permissions_role_id_permission', 'role_permissions', ['role_id', 'permission'], unique=False
    )

    op.create_table(
        'membership_roles',
        sa.Column('membership_id', sa.UUID(), nullable=False),
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['membership_id'], ['memberships.id'], name=op.f('fk_membership_roles_membership_id_memberships'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_membership_roles_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_membership_roles_role_id_roles'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_membership_roles')),
        sa.UniqueConstraint('org_id', 'membership_id', 'role_id', name='uq_membership_roles_org_id_membership_id_role_id'),
    )
    op.create_index(op.f('ix_membership_roles_org_id'), 'membership_roles', ['org_id'], unique=False)
    op.create_index('ix_membership_roles_membership_id', 'membership_roles', ['membership_id'], unique=False)

    for table in ("roles", "role_permissions", "membership_roles"):
        enable_rls(table)

    # Populated tables need a default, or the ALTER aborts the upgrade and the API never starts.
    op.add_column(
        'org_settings',
        sa.Column(
            'applied_permission_defaults',
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )

    _backfill()


def _backfill() -> None:
    """Seed the four system roles per org and map every membership onto its legacy role."""
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()

    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        for key, position, name_i18n, description_i18n in _SYSTEM_ROLES:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO roles (id, org_id, key, name_i18n, description_i18n, is_system, position)
                    VALUES (gen_random_uuid(), :org_id, :key, CAST(:name AS jsonb),
                            CAST(:description AS jsonb), true, :position)
                    ON CONFLICT ON CONSTRAINT uq_roles_org_id_key DO NOTHING
                    """
                ),
                {
                    "org_id": str(org_id),
                    "key": key,
                    "name": json.dumps(name_i18n),
                    "description": json.dumps(description_i18n),
                    "position": position,
                },
            )
            bind.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (id, org_id, role_id, permission)
                    SELECT gen_random_uuid(), r.org_id, r.id, p.permission
                    FROM roles r
                    CROSS JOIN unnest(CAST(:permissions AS varchar[])) AS p(permission)
                    WHERE r.org_id = :org_id AND r.key = :key
                    ON CONFLICT ON CONSTRAINT uq_role_permissions_org_id_role_id_permission DO NOTHING
                    """
                ),
                {
                    "org_id": str(org_id),
                    "key": key,
                    "permissions": list(_ROLE_PERMISSIONS[key]),
                },
            )

        # Every existing membership gets the system role its ``memberships.role`` names. A row
        # holding an unknown string (never possible through the API) falls back to ``member``.
        bind.execute(
            sa.text(
                """
                INSERT INTO membership_roles (id, org_id, membership_id, role_id)
                SELECT gen_random_uuid(), m.org_id, m.id, r.id
                FROM memberships m
                JOIN roles r
                  ON r.org_id = m.org_id
                 AND r.key = CASE WHEN m.role IN ('owner', 'admin', 'member', 'client')
                                  THEN m.role ELSE 'member' END
                WHERE m.org_id = :org_id
                ON CONFLICT ON CONSTRAINT uq_membership_roles_org_id_membership_id_role_id DO NOTHING
                """
            ),
            {"org_id": str(org_id)},
        )

        bind.execute(
            sa.text(
                """
                UPDATE org_settings
                SET applied_permission_defaults = CAST(:keys AS varchar[])
                WHERE org_id = :org_id
                """
            ),
            {"org_id": str(org_id), "keys": list(_CATALOG_KEYS)},
        )


def downgrade() -> None:
    op.drop_column('org_settings', 'applied_permission_defaults')
    for table in _TABLES:
        disable_rls(table)
    op.drop_index('ix_membership_roles_membership_id', table_name='membership_roles')
    op.drop_index(op.f('ix_membership_roles_org_id'), table_name='membership_roles')
    op.drop_table('membership_roles')
    op.drop_index('ix_role_permissions_role_id_permission', table_name='role_permissions')
    op.drop_index(op.f('ix_role_permissions_org_id'), table_name='role_permissions')
    op.drop_table('role_permissions')
    op.drop_index(op.f('ix_roles_org_id'), table_name='roles')
    op.drop_table('roles')
