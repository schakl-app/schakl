"""subscriptions_add_types_templates

Issue #142: tenant-configurable subscription types (the contact-types shape: key +
label_i18n + position + active, plus the task templates spawned on first activation) and
subscription templates (named presets that prefill the create form). ``subscriptions`` gains
a nullable ``subscription_type_id`` (SET NULL — a removed type never strands an agreement)
and ``activated_at``, the once-only guard for the new ``subscription.activated`` event.

``activated_at`` is backfilled for every existing non-draft row: those agreements went live
under the old release, so a later pause→resume must not read as a *first* activation and
spawn onboarding tasks retroactively. ``subscriptions`` is RLS-FORCED, so the backfill binds
the GUC per org (the 39683461b57a pattern); idempotent via ``activated_at IS NULL``.

Upgrade path: purely additive — two new tables, two nullable columns, a backfill of NULLs.
A rolled-back image ignores all of it. Safe to run unattended on a populated database.

Revision ID: 07c40a445f44
Revises: 4acc1cf1b64f
Create Date: 2026-07-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '07c40a445f44'
down_revision: str | None = '4acc1cf1b64f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# FK-child-before-parent, for the downgrade drop order.
_TABLES = (
    "subscription_templates",
    "subscription_types",
)


def upgrade() -> None:
    # --- subscription_types ------------------------------------------------------------ #
    op.create_table(
        'subscription_types',
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('label_i18n', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('task_template_ids', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='[]', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_subscription_types_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_subscription_types')),
        sa.UniqueConstraint('org_id', 'key', name='uq_subscription_types_org_key'),
    )
    op.create_index(op.f('ix_subscription_types_org_id'), 'subscription_types',
                    ['org_id'], unique=False)

    # --- subscription_templates --------------------------------------------------------- #
    op.create_table(
        'subscription_templates',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('subscription_type_id', sa.UUID(), nullable=True),
        sa.Column('currency', sa.String(length=3), server_default='EUR', nullable=False),
        sa.Column('interval', sa.String(length=20), server_default='monthly', nullable=False),
        sa.Column('interval_count', sa.Integer(), server_default='1', nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('included_hours', sa.Numeric(precision=7, scale=2), nullable=True),
        sa.Column('rollover', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('notice_period_days', sa.Integer(), nullable=True),
        sa.Column('lines', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='[]', nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(
            ['subscription_type_id'], ['subscription_types.id'],
            name=op.f('fk_subscription_templates_subscription_type_id_subscription_types'),
            ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_subscription_templates_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_subscription_templates')),
    )
    op.create_index(op.f('ix_subscription_templates_org_id'), 'subscription_templates',
                    ['org_id'], unique=False)
    op.create_index(op.f('ix_subscription_templates_subscription_type_id'),
                    'subscription_templates', ['subscription_type_id'], unique=False)

    # --- subscriptions: type FK + first-activation stamp --------------------------------- #
    op.add_column('subscriptions', sa.Column('subscription_type_id', sa.UUID(), nullable=True))
    op.add_column('subscriptions',
                  sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f('fk_subscriptions_subscription_type_id_subscription_types'),
        'subscriptions', 'subscription_types',
        ['subscription_type_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_subscriptions_subscription_type_id'), 'subscriptions',
                    ['subscription_type_id'], unique=False)

    # Tenant isolation at the database layer too (Golden Rule 1).
    for table in _TABLES:
        enable_rls(table)

    # Existing non-draft agreements went live under the old release: stamp them so a later
    # resume never counts as a first activation. Per-org GUC dance — subscriptions is
    # RLS-FORCED; idempotent (activated_at IS NULL guard).
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
                UPDATE subscriptions
                SET activated_at = updated_at
                WHERE org_id = :org_id AND status != 'draft' AND activated_at IS NULL
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    op.drop_index(op.f('ix_subscriptions_subscription_type_id'), table_name='subscriptions')
    op.drop_constraint(
        op.f('fk_subscriptions_subscription_type_id_subscription_types'),
        'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'activated_at')
    op.drop_column('subscriptions', 'subscription_type_id')

    for table in _TABLES:
        disable_rls(table)

    op.drop_index(op.f('ix_subscription_templates_subscription_type_id'),
                  table_name='subscription_templates')
    op.drop_index(op.f('ix_subscription_templates_org_id'),
                  table_name='subscription_templates')
    op.drop_table('subscription_templates')

    op.drop_index(op.f('ix_subscription_types_org_id'), table_name='subscription_types')
    op.drop_table('subscription_types')
