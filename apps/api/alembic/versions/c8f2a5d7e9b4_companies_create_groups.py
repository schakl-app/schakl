"""companies_create_groups

Company groups — the per-membership company data horizon (issue #191). Three new org-scoped,
RLS-forced tables: ``company_groups`` (the tenant-defined sets), ``company_group_members``
(group ↔ company M2M) and ``membership_company_groups`` (the visibility assignment). Purely
additive — no existing table changes, older code never reads them — so a single expand
release with a real downgrade; rollback safe from any released version.

Indexes match the two hot paths: the per-request horizon resolution
(``(org_id, membership_id)`` on assignments) and the admin listing (``(org_id, group_id)`` /
``(org_id, company_id)`` on the M2M).

Revision ID: c8f2a5d7e9b4
Revises: b7e1f4a9c3d2
Create Date: 2026-07-16 11:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c8f2a5d7e9b4'
down_revision: str | None = 'b7e1f4a9c3d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'company_groups',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'name', name='uq_company_groups_name'),
    )
    op.create_index(op.f('ix_company_groups_org_id'), 'company_groups', ['org_id'])
    enable_rls('company_groups')

    op.create_table(
        'company_group_members',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['company_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'org_id', 'group_id', 'company_id', name='uq_company_group_members_link'
        ),
    )
    op.create_index(
        op.f('ix_company_group_members_org_id'), 'company_group_members', ['org_id']
    )
    op.create_index(
        'ix_company_group_members_group', 'company_group_members', ['org_id', 'group_id']
    )
    op.create_index(
        'ix_company_group_members_company', 'company_group_members', ['org_id', 'company_id']
    )
    enable_rls('company_group_members')

    op.create_table(
        'membership_company_groups',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('membership_id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['membership_id'], ['memberships.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['company_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'org_id', 'membership_id', 'group_id', name='uq_membership_company_groups_link'
        ),
    )
    op.create_index(
        op.f('ix_membership_company_groups_org_id'), 'membership_company_groups', ['org_id']
    )
    op.create_index(
        'ix_membership_company_groups_membership',
        'membership_company_groups',
        ['org_id', 'membership_id'],
    )
    enable_rls('membership_company_groups')


def downgrade() -> None:
    disable_rls('membership_company_groups')
    op.drop_index(
        'ix_membership_company_groups_membership', table_name='membership_company_groups'
    )
    op.drop_index(
        op.f('ix_membership_company_groups_org_id'), table_name='membership_company_groups'
    )
    op.drop_table('membership_company_groups')

    disable_rls('company_group_members')
    op.drop_index('ix_company_group_members_company', table_name='company_group_members')
    op.drop_index('ix_company_group_members_group', table_name='company_group_members')
    op.drop_index(op.f('ix_company_group_members_org_id'), table_name='company_group_members')
    op.drop_table('company_group_members')

    disable_rls('company_groups')
    op.drop_index(op.f('ix_company_groups_org_id'), table_name='company_groups')
    op.drop_table('company_groups')
