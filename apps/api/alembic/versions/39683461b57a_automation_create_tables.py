"""automation_create_tables

The rule engine, stage 1 of issue #27 (+ the #96 webhook recipes): per-tenant rules
(trigger event + declarative JSONB conditions), their ordered actions, and the run log —
the engine's audit trail, idempotent per org on ``dedup_key``.

All three tables are org-scoped and RLS-forced. The migration also enables the ``automation``
module in every existing org's ``org_settings.enabled_modules`` (RLS is FORCED, so the GUC is
bound per org — same pattern as a7b8c9d0e1f2).

Upgrade path: purely additive — new tables + an append to an existing array column. A rolled-
back image simply ignores the new tables and the extra ``enabled_modules`` entry. No backfill
of existing rows, no destructive change, safe to run unattended on a populated database.

Revision ID: 39683461b57a
Revises: f8cd40024f05
Create Date: 2026-07-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '39683461b57a'
down_revision: str | None = 'f8cd40024f05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# FK-child-before-parent, for the downgrade drop order.
_TABLES = (
    "automation_runs",
    "automation_actions",
    "automation_rules",
)


def upgrade() -> None:
    # --- automation_rules ------------------------------------------------------------- #
    op.create_table(
        'automation_rules',
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('trigger_event', sa.String(length=50), nullable=False),
        sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_automation_rules_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_automation_rules')),
    )
    op.create_index(op.f('ix_automation_rules_org_id'), 'automation_rules',
                    ['org_id'], unique=False)
    op.create_index('ix_automation_rules_trigger', 'automation_rules',
                    ['org_id', 'trigger_event', 'enabled'], unique=False)

    # --- automation_actions ----------------------------------------------------------- #
    op.create_table(
        'automation_actions',
        sa.Column('rule_id', sa.UUID(), nullable=False),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['rule_id'], ['automation_rules.id'],
                                name=op.f('fk_automation_actions_rule_id_automation_rules'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_automation_actions_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_automation_actions')),
    )
    op.create_index(op.f('ix_automation_actions_org_id'), 'automation_actions',
                    ['org_id'], unique=False)
    op.create_index(op.f('ix_automation_actions_rule_id'), 'automation_actions',
                    ['rule_id'], unique=False)

    # --- automation_runs ---------------------------------------------------------------- #
    op.create_table(
        'automation_runs',
        sa.Column('rule_id', sa.UUID(), nullable=True),
        sa.Column('rule_name', sa.String(length=200), nullable=False),
        sa.Column('trigger_event', sa.String(length=50), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='pending', nullable=False),
        sa.Column('depth', sa.Integer(), server_default='0', nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='[]', nullable=False),
        sa.Column('dedup_key', sa.String(length=80), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        # SET NULL: the run log outlives the rule it describes (rule_name is the snapshot).
        sa.ForeignKeyConstraint(['rule_id'], ['automation_rules.id'],
                                name=op.f('fk_automation_runs_rule_id_automation_rules'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_automation_runs_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_automation_runs')),
    )
    op.create_index(op.f('ix_automation_runs_org_id'), 'automation_runs',
                    ['org_id'], unique=False)
    op.create_index('ix_automation_runs_rule', 'automation_runs',
                    ['org_id', 'rule_id', 'created_at'], unique=False)
    op.create_index('uq_automation_runs_dedup', 'automation_runs',
                    ['org_id', 'dedup_key'], unique=True)
    op.create_index('ix_automation_runs_pending', 'automation_runs',
                    ['org_id', 'created_at'], unique=False,
                    postgresql_where=sa.text("status = 'pending'"))

    # Tenant isolation at the database layer too (Golden Rule 1).
    for table in _TABLES:
        enable_rls(table)

    # Enable the module for existing orgs. org_settings is RLS-FORCED, so bind the GUC per
    # org; idempotent (the ANY() guard), like every backfill (docs/WORKFLOW.md).
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
                UPDATE org_settings
                SET enabled_modules = enabled_modules || '{automation}'
                WHERE org_id = :org_id AND NOT ('automation' = ANY(enabled_modules))
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    # Undo the module enablement too, so a downgraded install does not advertise a module
    # whose tables no longer exist. Same GUC dance as the upgrade — org_settings is RLS-FORCED.
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
                UPDATE org_settings
                SET enabled_modules = array_remove(enabled_modules, 'automation')
                WHERE org_id = :org_id
                """
            ),
            {"org_id": str(org_id)},
        )

    for table in _TABLES:
        disable_rls(table)

    op.drop_index('ix_automation_runs_pending', table_name='automation_runs')
    op.drop_index('uq_automation_runs_dedup', table_name='automation_runs')
    op.drop_index('ix_automation_runs_rule', table_name='automation_runs')
    op.drop_index(op.f('ix_automation_runs_org_id'), table_name='automation_runs')
    op.drop_table('automation_runs')

    op.drop_index(op.f('ix_automation_actions_rule_id'), table_name='automation_actions')
    op.drop_index(op.f('ix_automation_actions_org_id'), table_name='automation_actions')
    op.drop_table('automation_actions')

    op.drop_index('ix_automation_rules_trigger', table_name='automation_rules')
    op.drop_index(op.f('ix_automation_rules_org_id'), table_name='automation_rules')
    op.drop_table('automation_rules')
