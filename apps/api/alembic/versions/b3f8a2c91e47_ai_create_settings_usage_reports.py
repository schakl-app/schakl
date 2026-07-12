"""ai_create_settings_usage_reports

The AI core (epic #131, issues #126 + #130): per-org provider settings with the API key
encrypted at rest, content-free usage metering, and stored report drafts. New tables only —
safe on any populated database, and the previous release never reads them, so rollback
stays possible (docs/WORKFLOW.md).

Revision ID: b3f8a2c91e47
Revises: 07c40a445f44
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

revision: str = 'b3f8a2c91e47'
down_revision: str | None = '07c40a445f44'
branch_labels = None
depends_on = None

_TABLES = ("ai_settings", "ai_usage", "ai_reports")


def upgrade() -> None:
    op.create_table(
        'ai_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('api_key_enc', sa.Text(), nullable=False),
        sa.Column('base_url', sa.String(length=1024), nullable=True),
        sa.Column('default_model', sa.String(length=255), nullable=False),
        sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default='{}'),
        sa.Column('house_style', sa.Text(), nullable=True),
        sa.Column('monthly_token_budget', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_ai_settings_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_settings')),
        sa.UniqueConstraint('org_id', name='uq_ai_settings_org'),
    )
    op.create_index(op.f('ix_ai_settings_org_id'), 'ai_settings', ['org_id'], unique=False)

    op.create_table(
        'ai_usage',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('feature', sa.String(length=40), nullable=False),
        sa.Column('model', sa.String(length=255), nullable=False),
        sa.Column('tokens_in', sa.BigInteger(), nullable=False),
        sa.Column('tokens_out', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_ai_usage_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name=op.f('fk_ai_usage_user_id_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_usage')),
    )
    op.create_index(op.f('ix_ai_usage_org_id'), 'ai_usage', ['org_id'], unique=False)
    op.create_index('ix_ai_usage_org_created', 'ai_usage', ['org_id', 'created_at'],
                    unique=False)

    op.create_table(
        'ai_reports',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_ai_reports_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'],
                                name=op.f('fk_ai_reports_created_by_user_id_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_reports')),
    )
    op.create_index(op.f('ix_ai_reports_org_id'), 'ai_reports', ['org_id'], unique=False)
    op.create_index(op.f('ix_ai_reports_company_id'), 'ai_reports', ['company_id'],
                    unique=False)

    # Tenant isolation at the database layer too (Golden Rule 1).
    for table in _TABLES:
        enable_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        disable_rls(table)
    op.drop_index(op.f('ix_ai_reports_company_id'), table_name='ai_reports')
    op.drop_index(op.f('ix_ai_reports_org_id'), table_name='ai_reports')
    op.drop_table('ai_reports')
    op.drop_index('ix_ai_usage_org_created', table_name='ai_usage')
    op.drop_index(op.f('ix_ai_usage_org_id'), table_name='ai_usage')
    op.drop_table('ai_usage')
    op.drop_index(op.f('ix_ai_settings_org_id'), table_name='ai_settings')
    op.drop_table('ai_settings')
