"""projects_create_table

Creates the ``projects`` table (P2 agency core, CLAUDE.md §6, §10). Org-scoped + RLS-forced,
attachable to a company, customizable (JSONB ``custom`` + GIN index).

Revision ID: d2b3c4e5f6a1
Revises: c1d2e3f4a5b6
Create Date: 2026-07-07 13:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'd2b3c4e5f6a1'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'projects',
        sa.Column('company_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('billable_default', sa.Boolean(), nullable=False),
        sa.Column('budget_hours', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('budget_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('hourly_rate', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('custom', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_projects_company_id_companies'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_projects_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_projects')),
    )
    op.create_index('ix_projects_custom', 'projects', ['custom'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_projects_company_id'), 'projects', ['company_id'], unique=False)
    op.create_index(op.f('ix_projects_name'), 'projects', ['name'], unique=False)
    op.create_index(op.f('ix_projects_org_id'), 'projects', ['org_id'], unique=False)
    op.create_index(op.f('ix_projects_status'), 'projects', ['status'], unique=False)

    # Tenant isolation (defence-in-depth): projects are org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls("projects")


def downgrade() -> None:
    disable_rls("projects")

    op.drop_index(op.f('ix_projects_status'), table_name='projects')
    op.drop_index(op.f('ix_projects_org_id'), table_name='projects')
    op.drop_index(op.f('ix_projects_name'), table_name='projects')
    op.drop_index(op.f('ix_projects_company_id'), table_name='projects')
    op.drop_index('ix_projects_custom', table_name='projects', postgresql_using='gin')
    op.drop_table('projects')
