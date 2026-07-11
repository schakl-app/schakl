"""providers_create_table

Revision ID: a4556bc04430
Revises: b4c5d6e7f8a9
Create Date: 2026-07-11 15:06:07.227085
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'a4556bc04430'
down_revision: str | None = '38f758f7afbe'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('providers',
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_providers_org_id_orgs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_providers'))
    )
    op.create_index(op.f('ix_providers_kind'), 'providers', ['kind'], unique=False)
    op.create_index(op.f('ix_providers_org_id'), 'providers', ['org_id'], unique=False)

    # Tenant isolation (defence-in-depth): providers are org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls("providers")


def downgrade() -> None:
    disable_rls("providers")
    op.drop_index(op.f('ix_providers_org_id'), table_name='providers')
    op.drop_index(op.f('ix_providers_kind'), table_name='providers')
    op.drop_table('providers')
