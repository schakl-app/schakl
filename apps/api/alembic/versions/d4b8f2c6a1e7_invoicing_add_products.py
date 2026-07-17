"""invoicing_add_products

Default products (owner request): a tenant's named line presets — description, unit, unit
price, tax rate — the invoice/quote line editor drops onto a document with one pick. Lines
keep snapshotting what they copy, so this table stays a picker catalog, never a live join.

Upgrade plan (docs/WORKFLOW.md): expand-only — one new org-scoped, RLS-forced table;
nothing existing references it and older code never reads it, so rollback is safe.

Revision ID: d4b8f2c6a1e7
Revises: c8f3a1e5b9d2
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.rls import disable_rls, enable_rls

revision = "d4b8f2c6a1e7"
down_revision = "c8f3a1e5b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoicing_products",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate_id", sa.UUID(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoicing_products")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["orgs.id"], name=op.f("fk_invoicing_products_org_id_orgs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"],
            ["invoicing_tax_rates.id"],
            name=op.f("fk_invoicing_products_tax_rate_id_invoicing_tax_rates"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        op.f("ix_invoicing_products_org_id"), "invoicing_products", ["org_id"], unique=False
    )
    enable_rls("invoicing_products")


def downgrade() -> None:
    disable_rls("invoicing_products")
    op.drop_index(op.f("ix_invoicing_products_org_id"), table_name="invoicing_products")
    op.drop_table("invoicing_products")
