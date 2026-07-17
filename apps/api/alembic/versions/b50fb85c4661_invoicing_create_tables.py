"""invoicing_create_tables

Revision ID: b50fb85c4661
Revises: 5250d2154ad7
Create Date: 2026-07-16 00:00:00.000000

New module tables (issue #207): settings, tax rates, document templates, invoices + lines +
payments + time-entry links, quotes + lines, and the accounting external-ref bookkeeping
(#31's seam). Expand-only: additive DDL, no backfill, nothing existing references them, and
older code never reads them — rollback (downgrade drops everything + RLS policies) is safe
from any released version. Every table is org-scoped and RLS-forced (Golden Rule 1).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'b50fb85c4661'
down_revision: str | None = '5250d2154ad7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    'invoicing_external_refs',
    'invoice_time_entries',
    'invoice_payments',
    'quote_lines',
    'invoice_lines',
    'quotes',
    'invoices',
    'invoicing_settings',
    'invoicing_templates',
    'invoicing_tax_rates',
)


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


def _document_columns() -> list[sa.Column]:
    """What invoices and quotes share (models._DocumentColumns)."""
    return [
        sa.Column('number', sa.String(length=40), nullable=True),
        sa.Column('customer', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('exchange_rate', sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column('locale', sa.String(length=10), nullable=False),
        sa.Column('reference', sa.String(length=120), nullable=True),
        sa.Column('intro', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column(
            'prices_include_tax', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column('subtotal', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('tax_total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('custom', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    ]


def _line_columns() -> list[sa.Column]:
    return [
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(length=512), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=True),
        sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('tax_rate_id', sa.UUID(), nullable=True),
        sa.Column('tax_rate_pct', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('tax_name', sa.String(length=80), nullable=False),
        sa.Column('tax_category', sa.String(length=20), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'invoicing_tax_rates',
        sa.Column('label_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('rate', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('ledger_code', sa.String(length=50), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_invoicing_tax_rates_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoicing_tax_rates')),
    )
    op.create_index(op.f('ix_invoicing_tax_rates_org_id'), 'invoicing_tax_rates', ['org_id'])

    op.create_table(
        'invoicing_templates',
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_invoicing_templates_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoicing_templates')),
    )
    op.create_index(op.f('ix_invoicing_templates_org_id'), 'invoicing_templates', ['org_id'])

    op.create_table(
        'invoicing_settings',
        sa.Column('company_details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('tax_country', sa.String(length=2), server_default='NL', nullable=False),
        sa.Column(
            'prices_include_tax', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column('default_due_days', sa.Integer(), server_default='14', nullable=False),
        sa.Column('quote_valid_days', sa.Integer(), server_default='30', nullable=False),
        sa.Column('default_tax_rate_id', sa.UUID(), nullable=True),
        sa.Column('default_template_id', sa.UUID(), nullable=True),
        sa.Column('default_hourly_rate', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            'invoice_number_format', sa.String(length=60), server_default='{year}-{seq:4}',
            nullable=False,
        ),
        sa.Column(
            'quote_number_format', sa.String(length=60), server_default='Q{year}-{seq:4}',
            nullable=False,
        ),
        sa.Column('invoice_next_seq', sa.Integer(), server_default='1', nullable=False),
        sa.Column('quote_next_seq', sa.Integer(), server_default='1', nullable=False),
        sa.Column('invoice_seq_year', sa.Integer(), nullable=True),
        sa.Column('quote_seq_year', sa.Integer(), nullable=True),
        sa.Column(
            'number_reset_yearly', sa.Boolean(), server_default=sa.text('true'),
            nullable=False,
        ),
        sa.Column(
            'reminders_enabled', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column(
            'reminder_days', postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[7, 14, 30]'"), nullable=False,
        ),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_invoicing_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['default_tax_rate_id'], ['invoicing_tax_rates.id'],
            name=op.f('fk_invoicing_settings_default_tax_rate_id_invoicing_tax_rates'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['default_template_id'], ['invoicing_templates.id'],
            name=op.f('fk_invoicing_settings_default_template_id_invoicing_templates'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoicing_settings')),
        sa.UniqueConstraint('org_id', name='uq_invoicing_settings_org'),
    )

    op.create_table(
        'invoices',
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('contact_id', sa.UUID(), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('credit_for_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('template_id', sa.UUID(), nullable=True),
        sa.Column('quote_id', sa.UUID(), nullable=True),
        sa.Column('subscription_id', sa.UUID(), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=True),
        sa.Column('period_end', sa.Date(), nullable=True),
        sa.Column('paid_total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reminder_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_reminder_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'reminders_paused', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        *_document_columns(),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'], name=op.f('fk_invoices_company_id_companies'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['contact_id'], ['contacts.id'], name=op.f('fk_invoices_contact_id_contacts'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['credit_for_id'], ['invoices.id'], name=op.f('fk_invoices_credit_for_id_invoices'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['template_id'], ['invoicing_templates.id'],
            name=op.f('fk_invoices_template_id_invoicing_templates'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_invoices_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoices')),
    )
    op.create_index(op.f('ix_invoices_org_id'), 'invoices', ['org_id'])
    op.create_index(op.f('ix_invoices_company_id'), 'invoices', ['company_id'])
    op.create_index(op.f('ix_invoices_status'), 'invoices', ['status'])
    op.create_index(op.f('ix_invoices_due_date'), 'invoices', ['due_date'])
    op.create_index(op.f('ix_invoices_subscription_id'), 'invoices', ['subscription_id'])
    op.create_index('ix_invoices_custom', 'invoices', ['custom'], postgresql_using='gin')
    op.create_index(
        'uq_invoices_org_number', 'invoices', ['org_id', 'number'], unique=True,
        postgresql_where=sa.text('number IS NOT NULL'),
    )
    op.create_index(
        'uq_invoices_subscription_period', 'invoices',
        ['org_id', 'subscription_id', 'period_end'], unique=True,
        postgresql_where=sa.text('subscription_id IS NOT NULL'),
    )

    op.create_table(
        'quotes',
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('contact_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('valid_until', sa.Date(), nullable=True),
        sa.Column('template_id', sa.UUID(), nullable=True),
        sa.Column('invoice_id', sa.UUID(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=True),
        *_document_columns(),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'], name=op.f('fk_quotes_company_id_companies'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['contact_id'], ['contacts.id'], name=op.f('fk_quotes_contact_id_contacts'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['invoice_id'], ['invoices.id'], name=op.f('fk_quotes_invoice_id_invoices'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['template_id'], ['invoicing_templates.id'],
            name=op.f('fk_quotes_template_id_invoicing_templates'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_quotes_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_quotes')),
    )
    op.create_index(op.f('ix_quotes_org_id'), 'quotes', ['org_id'])
    op.create_index(op.f('ix_quotes_company_id'), 'quotes', ['company_id'])
    op.create_index(op.f('ix_quotes_status'), 'quotes', ['status'])
    op.create_index('ix_quotes_custom', 'quotes', ['custom'], postgresql_using='gin')
    op.create_index(
        'uq_quotes_org_number', 'quotes', ['org_id', 'number'], unique=True,
        postgresql_where=sa.text('number IS NOT NULL'),
    )

    for table, fk in (('invoice_lines', 'invoices'), ('quote_lines', 'quotes')):
        parent_col = 'invoice_id' if table == 'invoice_lines' else 'quote_id'
        op.create_table(
            table,
            sa.Column(parent_col, sa.UUID(), nullable=False),
            *_line_columns(),
            *_base_columns(),
            sa.ForeignKeyConstraint(
                [parent_col], [f'{fk}.id'],
                name=op.f(f'fk_{table}_{parent_col}_{fk}'), ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['tax_rate_id'], ['invoicing_tax_rates.id'],
                name=op.f(f'fk_{table}_tax_rate_id_invoicing_tax_rates'), ondelete='SET NULL',
            ),
            sa.ForeignKeyConstraint(
                ['org_id'], ['orgs.id'], name=op.f(f'fk_{table}_org_id_orgs'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('id', name=op.f(f'pk_{table}')),
        )
        op.create_index(op.f(f'ix_{table}_org_id'), table, ['org_id'])
        op.create_index(op.f(f'ix_{table}_{parent_col}'), table, [parent_col])

    op.create_table(
        'invoice_payments',
        sa.Column('invoice_id', sa.UUID(), nullable=False),
        sa.Column('paid_on', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('method', sa.String(length=30), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['invoice_id'], ['invoices.id'],
            name=op.f('fk_invoice_payments_invoice_id_invoices'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_invoice_payments_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoice_payments')),
    )
    op.create_index(op.f('ix_invoice_payments_org_id'), 'invoice_payments', ['org_id'])
    op.create_index(op.f('ix_invoice_payments_invoice_id'), 'invoice_payments', ['invoice_id'])

    op.create_table(
        'invoice_time_entries',
        sa.Column('invoice_id', sa.UUID(), nullable=False),
        sa.Column('time_entry_id', sa.UUID(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['invoice_id'], ['invoices.id'],
            name=op.f('fk_invoice_time_entries_invoice_id_invoices'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_invoice_time_entries_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoice_time_entries')),
        sa.UniqueConstraint(
            'org_id', 'time_entry_id', name='uq_invoice_time_entries_entry'
        ),
    )
    op.create_index(
        op.f('ix_invoice_time_entries_org_id'), 'invoice_time_entries', ['org_id']
    )
    op.create_index(
        op.f('ix_invoice_time_entries_invoice_id'), 'invoice_time_entries', ['invoice_id']
    )

    op.create_table(
        'invoicing_external_refs',
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('local_type', sa.String(length=20), nullable=False),
        sa.Column('local_id', sa.UUID(), nullable=False),
        sa.Column('external_id', sa.String(length=160), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_invoicing_external_refs_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_invoicing_external_refs')),
        sa.UniqueConstraint(
            'org_id', 'provider', 'local_type', 'local_id',
            name='uq_invoicing_external_refs_local',
        ),
    )
    op.create_index(
        op.f('ix_invoicing_external_refs_org_id'), 'invoicing_external_refs', ['org_id']
    )

    for table in _TABLES:
        enable_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        disable_rls(table)
        op.drop_table(table)
