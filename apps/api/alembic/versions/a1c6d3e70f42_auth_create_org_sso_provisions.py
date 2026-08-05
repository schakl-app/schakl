"""auth_create_org_sso_provisions

Remember that an org has already JIT-provisioned an account, so OIDC auto-provisioning becomes
**first contact per org** instead of "has no membership right now". Without this row the two
are indistinguishable, and removing an SSO user from Instellingen → Gebruikers lasted only
until their next sign-in.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive.** One new table; nothing else touched; applies on top of any released ``head``.
* **Backfilled from the memberships that exist.** Every current membership is evidence that
  the org already admitted that account, so each one seeds a provision row. Without the
  backfill the first release would re-provision every removed user exactly once — the very
  failure this closes — because an empty table reads as "nobody has ever signed in here".
  It is deliberately generous: a membership an admin created by hand is also "this org has
  admitted them", and the consequence of counting it is only that JIT will not silently
  re-create it later.
* **Backfill precedes ``enable_rls``** in the same transaction: the migration runs as the app
  role, which the freshly forced policy would otherwise block (no GUC is bound here).
* **Rollback-safe.** The previous image never selects ``org_sso_provisions``; it simply goes
  back to re-provisioning on next sign-in.
* **Reversible.** ``downgrade()`` drops the table.

Revision ID: a1c6d3e70f42
Revises: a4d61f0b73c9
Create Date: 2026-08-01 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'a1c6d3e70f42'
down_revision: str | None = 'a4d61f0b73c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'org_sso_provisions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['org_id'],
            ['orgs.id'],
            name=op.f('fk_org_sso_provisions_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_org_sso_provisions_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_org_sso_provisions')),
        sa.UniqueConstraint('org_id', 'user_id', name='uq_org_sso_provisions_org_user'),
    )
    op.create_index(
        op.f('ix_org_sso_provisions_org_id'), 'org_sso_provisions', ['org_id'], unique=False
    )
    op.create_index(
        op.f('ix_org_sso_provisions_user_id'), 'org_sso_provisions', ['user_id'], unique=False
    )

    op.execute(
        sa.text(
            """
            INSERT INTO org_sso_provisions (id, org_id, user_id, created_at, updated_at)
            SELECT gen_random_uuid(), m.org_id, m.user_id, now(), now()
            FROM memberships m
            ON CONFLICT (org_id, user_id) DO NOTHING
            """
        )
    )

    enable_rls('org_sso_provisions')


def downgrade() -> None:
    disable_rls('org_sso_provisions')
    op.drop_index(op.f('ix_org_sso_provisions_user_id'), table_name='org_sso_provisions')
    op.drop_index(op.f('ix_org_sso_provisions_org_id'), table_name='org_sso_provisions')
    op.drop_table('org_sso_provisions')
