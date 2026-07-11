"""contacts_create_types

Revision ID: e7f8a9b0c1d2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-11 15:21:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: str | None = 'e6f7a8b9c0d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('contact_types',
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('label_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_contact_types_org_id_orgs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_types')),
    sa.UniqueConstraint('org_id', 'key', name='uq_contact_types_org_key')
    )
    op.create_index(op.f('ix_contact_types_org_id'), 'contact_types', ['org_id'], unique=False)

    # The type lives on the company↔contact link (§91): a person can be a client contact for one
    # company and the technical contact for another. SET NULL so deleting a type keeps the link.
    op.add_column('company_contacts', sa.Column('contact_type_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_company_contacts_contact_type_id'), 'company_contacts', ['contact_type_id'], unique=False)
    op.create_foreign_key(op.f('fk_company_contacts_contact_type_id_contact_types'), 'company_contacts', 'contact_types', ['contact_type_id'], ['id'], ondelete='SET NULL')

    # Tenant isolation (defence-in-depth): contact types are org-scoped, RLS-forced (§5).
    enable_rls("contact_types")


def downgrade() -> None:
    op.drop_constraint(op.f('fk_company_contacts_contact_type_id_contact_types'), 'company_contacts', type_='foreignkey')
    op.drop_index(op.f('ix_company_contacts_contact_type_id'), table_name='company_contacts')
    op.drop_column('company_contacts', 'contact_type_id')

    disable_rls("contact_types")
    op.drop_index(op.f('ix_contact_types_org_id'), table_name='contact_types')
    op.drop_table('contact_types')
