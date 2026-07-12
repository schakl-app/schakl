"""subscriptions_create_tables

Revision ID: 98a7fe4c7d40
Revises: b649b3170eef
Create Date: 2026-07-12 00:00:00.000000

New module tables (issue #30): subscriptions + append-only price history + invoice lines +
project/task links. Expand-only: additive DDL, no backfill, nothing else references them, and
older code never reads them — rollback (downgrade drops all four + their RLS policies) is safe
from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '98a7fe4c7d40'
down_revision: str | None = 'b649b3170eef'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        'subscriptions',
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('interval', sa.String(length=20), nullable=False),
        sa.Column('interval_count', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('next_invoice_date', sa.Date(), nullable=True),
        sa.Column('included_hours', sa.Numeric(precision=7, scale=2), nullable=True),
        sa.Column('rollover', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('notice_period_days', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('custom', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'],
            name=op.f('fk_subscriptions_company_id_companies'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_subscriptions_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_subscriptions')),
    )
    op.create_index(op.f('ix_subscriptions_org_id'), 'subscriptions', ['org_id'])
    op.create_index(op.f('ix_subscriptions_company_id'), 'subscriptions', ['company_id'])
    op.create_index(op.f('ix_subscriptions_status'), 'subscriptions', ['status'])
    op.create_index(
        op.f('ix_subscriptions_next_invoice_date'), 'subscriptions', ['next_invoice_date']
    )
    op.create_index(
        'ix_subscriptions_custom', 'subscriptions', ['custom'], postgresql_using='gin'
    )

    op.create_table(
        'subscription_prices',
        sa.Column('subscription_id', sa.UUID(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['subscription_id'], ['subscriptions.id'],
            name=op.f('fk_subscription_prices_subscription_id_subscriptions'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_subscription_prices_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_subscription_prices')),
        sa.UniqueConstraint(
            'org_id', 'subscription_id', 'valid_from', name='uq_subscription_prices_from'
        ),
    )
    op.create_index(op.f('ix_subscription_prices_org_id'), 'subscription_prices', ['org_id'])
    op.create_index(
        op.f('ix_subscription_prices_subscription_id'), 'subscription_prices',
        ['subscription_id'],
    )

    op.create_table(
        'subscription_lines',
        sa.Column('subscription_id', sa.UUID(), nullable=False),
        sa.Column('description', sa.String(length=512), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('unit_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['subscription_id'], ['subscriptions.id'],
            name=op.f('fk_subscription_lines_subscription_id_subscriptions'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_subscription_lines_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_subscription_lines')),
    )
    op.create_index(op.f('ix_subscription_lines_org_id'), 'subscription_lines', ['org_id'])
    op.create_index(
        op.f('ix_subscription_lines_subscription_id'), 'subscription_lines',
        ['subscription_id'],
    )

    op.create_table(
        'subscription_links',
        sa.Column('subscription_id', sa.UUID(), nullable=False),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['subscription_id'], ['subscriptions.id'],
            name=op.f('fk_subscription_links_subscription_id_subscriptions'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_subscription_links_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_subscription_links')),
        sa.UniqueConstraint(
            'org_id', 'subscription_id', 'entity_type', 'entity_id',
            name='uq_subscription_links_target',
        ),
    )
    op.create_index(op.f('ix_subscription_links_org_id'), 'subscription_links', ['org_id'])
    op.create_index(
        op.f('ix_subscription_links_subscription_id'), 'subscription_links',
        ['subscription_id'],
    )

    for table in ('subscriptions', 'subscription_prices', 'subscription_lines',
                  'subscription_links'):
        enable_rls(table)


def downgrade() -> None:
    for table in ('subscription_links', 'subscription_lines', 'subscription_prices',
                  'subscriptions'):
        disable_rls(table)
        op.drop_table(table)
