"""contacts_create_company_links

Moves contacts from a single ``company_id`` FK to a many-to-many join table
(``company_contacts``) so a person can be a contact at several clients at once, each link
carrying an ``is_primary`` flag (at most one primary per client — partial unique index).

Existing ``contacts.company_id`` values are backfilled as links; within each company exactly
one contact (the oldest) becomes primary. The old column is then dropped.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-08 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: str | None = 'c2d3e4f5a6b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'company_contacts',
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('contact_id', sa.UUID(), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_company_contacts_company_id_companies'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_company_contacts_contact_id_contacts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_company_contacts_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_company_contacts')),
        sa.UniqueConstraint('org_id', 'company_id', 'contact_id', name='uq_company_contacts_link'),
    )
    op.create_index(op.f('ix_company_contacts_company_id'), 'company_contacts', ['company_id'], unique=False)
    op.create_index(op.f('ix_company_contacts_contact_id'), 'company_contacts', ['contact_id'], unique=False)
    op.create_index(op.f('ix_company_contacts_org_id'), 'company_contacts', ['org_id'], unique=False)
    # At most one primary contact per company (partial unique index).
    op.create_index(
        'uq_company_contacts_primary',
        'company_contacts',
        ['org_id', 'company_id'],
        unique=True,
        postgresql_where=sa.text('is_primary'),
    )

    # Backfill existing single-company links. Exactly one contact per company becomes primary
    # (the oldest), so the partial unique index above is never violated.
    #
    # Migrations run as the table owner under FORCE ROW LEVEL SECURITY with no tenant GUC set,
    # so an unqualified read of ``contacts`` would return zero rows (RLS fails closed) and the
    # backfill would silently lose every link before ``company_id`` is dropped. Exempt the owner
    # for the duration of the copy, then restore FORCE.
    op.execute("ALTER TABLE contacts NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        INSERT INTO company_contacts (id, org_id, company_id, contact_id, is_primary, created_at, updated_at)
        SELECT gen_random_uuid(),
               c.org_id,
               c.company_id,
               c.id,
               (row_number() OVER (PARTITION BY c.company_id ORDER BY c.created_at, c.id) = 1),
               now(),
               now()
        FROM contacts c
        WHERE c.company_id IS NOT NULL
        """
    )
    op.execute("ALTER TABLE contacts FORCE ROW LEVEL SECURITY")

    op.drop_index(op.f('ix_contacts_company_id'), table_name='contacts')
    op.drop_column('contacts', 'company_id')

    # Tenant isolation (defence-in-depth): links are org-scoped, RLS-forced (CLAUDE.md §5).
    enable_rls("company_contacts")


def downgrade() -> None:
    # Re-add the single-company FK and restore it from the primary (or earliest) link.
    op.add_column('contacts', sa.Column('company_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_contacts_company_id'), 'contacts', ['company_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_contacts_company_id_companies'),
        'contacts',
        'companies',
        ['company_id'],
        ['id'],
        ondelete='SET NULL',
    )
    # Restore each contact's single company from its primary (or earliest) link. As in upgrade,
    # exempt the owner from RLS so the cross-table copy actually sees the rows.
    op.execute("ALTER TABLE contacts NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE company_contacts NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE contacts
        SET company_id = link.company_id
        FROM (
            SELECT DISTINCT ON (contact_id) contact_id, company_id
            FROM company_contacts
            ORDER BY contact_id, is_primary DESC, created_at
        ) AS link
        WHERE contacts.id = link.contact_id
        """
    )
    op.execute("ALTER TABLE contacts FORCE ROW LEVEL SECURITY")

    disable_rls("company_contacts")
    op.drop_index('uq_company_contacts_primary', table_name='company_contacts')
    op.drop_index(op.f('ix_company_contacts_org_id'), table_name='company_contacts')
    op.drop_index(op.f('ix_company_contacts_contact_id'), table_name='company_contacts')
    op.drop_index(op.f('ix_company_contacts_company_id'), table_name='company_contacts')
    op.drop_table('company_contacts')
