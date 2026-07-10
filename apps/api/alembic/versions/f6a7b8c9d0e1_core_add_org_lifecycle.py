"""core_add_org_lifecycle

Org lifecycle + instance administration (issue #26):

* ``orgs`` gains lifecycle state (``status``/``suspended_at``/``deleted_at``/``exported_at``)
  and the custom-domain fields. The domain moves from ``org_settings`` to ``orgs``
  (expand step — hostname resolution runs *before* RLS is bound, so it can only read
  tables without RLS; ``org_settings`` is RLS-forced and the old lookup never matched).
  The ``org_settings.custom_domain`` column stays this release (contract in a later one).
* ``instance_audit_log`` — instance-level (no ``org_id`` scoping, no RLS, like ``orgs`` and
  ``users``): it records the actions that manage or cross tenants and must survive them.

Upgrade path for existing installs (docs/DEPLOY.md): hostname resolution no longer falls
back to the seeded org, so a single-org install whose settings carry no custom domain gets
``app.<SCHAKL_BASE_DOMAIN>`` — the documented ingress hostname of both compose files —
claimed as its verified custom domain. Domains set manually before verification existed
are grandfathered as verified, otherwise an unattended upgrade would stop resolving them.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-09 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.config import settings

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: str | None = 'e5f6a7b8c9d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- orgs: lifecycle + hostname routing -------------------------------------------
    op.add_column('orgs', sa.Column('status', sa.String(length=20), nullable=False,
                                    server_default='active'))
    op.add_column('orgs', sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orgs', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orgs', sa.Column('exported_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orgs', sa.Column('custom_domain', sa.String(length=255), nullable=True))
    op.add_column('orgs', sa.Column('custom_domain_verified_at', sa.DateTime(timezone=True),
                                    nullable=True))
    op.add_column('orgs', sa.Column('pending_domain', sa.String(length=255), nullable=True))
    op.add_column('orgs', sa.Column('domain_verification_token', sa.String(length=64),
                                    nullable=True))
    op.create_index(op.f('ix_orgs_custom_domain'), 'orgs', ['custom_domain'], unique=True)

    # --- data: move domains up from org_settings; grandfather them as verified --------
    # org_settings is RLS-FORCED, so reading it needs the GUC bound per org.
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE orgs
                SET custom_domain = s.custom_domain, custom_domain_verified_at = now()
                FROM org_settings s
                WHERE s.org_id = orgs.id AND orgs.id = :org_id
                  AND s.custom_domain IS NOT NULL
                """
            ),
            {"org_id": str(org_id)},
        )

    # Single-org installs resolved via the (removed) seed fallback until now. Both compose
    # files serve the app at app.<SCHAKL_BASE_DOMAIN>, so claim that as the org's verified
    # domain — idempotent to re-run, and freely changeable later in the branding settings.
    bind.execute(
        sa.text(
            """
            UPDATE orgs
            SET custom_domain = :host, custom_domain_verified_at = now()
            WHERE custom_domain IS NULL
              AND (SELECT count(*) FROM orgs) = 1
              AND NOT EXISTS (SELECT 1 FROM orgs WHERE custom_domain = :host)
            """
        ),
        {"host": f"app.{settings.base_domain.lower()}"},
    )

    # --- instance_audit_log (instance-level: no org_id scoping, no RLS on purpose) -----
    op.create_table('instance_audit_log',
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('actor_email', sa.String(length=320), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=True),
    sa.Column('org_slug', sa.String(length=63), nullable=True),
    sa.Column('target_user_id', sa.UUID(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}',
              nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
              nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'],
                            name=op.f('fk_instance_audit_log_actor_user_id_users'),
                            ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                            name=op.f('fk_instance_audit_log_org_id_orgs'),
                            ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['target_user_id'], ['users.id'],
                            name=op.f('fk_instance_audit_log_target_user_id_users'),
                            ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_instance_audit_log'))
    )
    op.create_index(op.f('ix_instance_audit_log_created_at'), 'instance_audit_log',
                    ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_instance_audit_log_created_at'), table_name='instance_audit_log')
    op.drop_table('instance_audit_log')
    op.drop_index(op.f('ix_orgs_custom_domain'), table_name='orgs')
    op.drop_column('orgs', 'domain_verification_token')
    op.drop_column('orgs', 'pending_domain')
    op.drop_column('orgs', 'custom_domain_verified_at')
    op.drop_column('orgs', 'custom_domain')
    op.drop_column('orgs', 'exported_at')
    op.drop_column('orgs', 'deleted_at')
    op.drop_column('orgs', 'suspended_at')
    op.drop_column('orgs', 'status')
