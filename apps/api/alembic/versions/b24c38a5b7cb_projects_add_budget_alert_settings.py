"""projects_add_budget_alert_settings

Revision ID: b24c38a5b7cb
Revises: b3d17c5e8a02
Create Date: 2026-08-26 17:09:15.107307

Org-wide projects settings (one row per org: the budget alert mail toggle and the warn
threshold) plus the mail's dedup fingerprint on ``projects``. Expand-only and rollback-safe:
a new table and a new nullable column nothing older reads, and the server defaults (on, 75)
reproduce the pre-settings behaviour, so an instance that upgrades and types nothing warns
exactly as it did.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'b24c38a5b7cb'
down_revision: str | None = 'b3d17c5e8a02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'project_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column(
            'budget_alert_emails', sa.Boolean(), server_default='true', nullable=False
        ),
        sa.Column(
            'budget_alert_threshold', sa.Integer(), server_default='75', nullable=False
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_project_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_project_settings')),
        sa.UniqueConstraint('org_id', name='uq_project_settings_org'),
    )
    op.create_index(op.f('ix_project_settings_org_id'), 'project_settings', ['org_id'])

    enable_rls('project_settings')

    op.add_column(
        'projects', sa.Column('budget_alerted_for', sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('projects', 'budget_alerted_for')
    disable_rls('project_settings')
    op.drop_index(op.f('ix_project_settings_org_id'), table_name='project_settings')
    op.drop_table('project_settings')
