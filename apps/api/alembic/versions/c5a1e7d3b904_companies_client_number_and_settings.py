"""companies_client_number_and_settings

Revision ID: c5a1e7d3b904
Revises: a2f95c630d14
Create Date: 2026-07-27 00:00:00.000000

Klantnummer on companies (+ the per-org numbering settings that allocate it), and the org's
default country.

**Upgrade path (docs/WORKFLOW.md — self-hosted releases migrate unattended):** purely additive,
so there is no expand/contract pair to sequence. `companies.client_number` arrives NULL for
every existing row and nothing reads it as required; the partial unique index only constrains
non-NULL values, so an instance with 5000 unnumbered companies upgrades without contention and
without a backfill. Numbers are handed out from the app afterwards — on create, or by the
explicit "number existing companies" action in Instellingen → Bedrijven. Deliberately *not*
backfilled here: which companies deserve which number is a bookkeeping decision, and a
migration that silently numbered them would fight the tenant's existing numbering.

`org_settings.default_country` is NOT NULL with a server default, which is what makes adding it
to a populated table safe in one statement.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c5a1e7d3b904'
down_revision: str | None = 'a2f95c630d14'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('client_number', sa.String(length=40), nullable=True))
    # Partial: NULLs never contend, so an org that does not number its clients is unaffected.
    # Scoped to org_id — a global unique index would let one tenant's allocation collide with
    # another's (Golden Rule 1).
    op.create_index(
        'uq_companies_client_number',
        'companies',
        ['org_id', 'client_number'],
        unique=True,
        postgresql_where=sa.text('client_number IS NOT NULL'),
    )

    op.add_column(
        'org_settings',
        sa.Column(
            'default_country', sa.String(length=2), server_default='NL', nullable=False
        ),
    )

    op.create_table(
        'company_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column(
            'client_number_format', sa.String(length=60), server_default='{seq:4}',
            nullable=False,
        ),
        sa.Column(
            'client_number_next_seq', sa.Integer(), server_default='1', nullable=False
        ),
        sa.Column('client_number_seq_year', sa.Integer(), nullable=True),
        sa.Column(
            'client_number_reset_yearly', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column(
            'client_number_auto', sa.Boolean(), server_default=sa.text('true'), nullable=False
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
            ['org_id'], ['orgs.id'], name=op.f('fk_company_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_company_settings')),
        sa.UniqueConstraint('org_id', name='uq_company_settings_org'),
    )
    op.create_index(op.f('ix_company_settings_org_id'), 'company_settings', ['org_id'])

    enable_rls('company_settings')


def downgrade() -> None:
    disable_rls('company_settings')
    op.drop_index(op.f('ix_company_settings_org_id'), table_name='company_settings')
    op.drop_table('company_settings')
    op.drop_column('org_settings', 'default_country')
    op.drop_index('uq_companies_client_number', table_name='companies')
    op.drop_column('companies', 'client_number')
