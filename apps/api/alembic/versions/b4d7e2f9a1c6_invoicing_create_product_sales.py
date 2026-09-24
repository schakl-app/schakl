"""invoicing: one-time product sales, and the line that bills one

Purely additive. ``invoicing_product_sales`` is a product sold once to a client — a subscription
with no cycle — priced from the price list at the moment of sale and claimed by exactly one
invoice through its own ``invoice_id``; ``invoice_lines.sale_id`` is the provenance the line
carries back so a re-saved draft cannot forget what it billed (the ``subscription_id`` rule).

Revision ID: b4d7e2f9a1c6
Revises: a3c9e1f7b5d2
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision = "b4d7e2f9a1c6"
down_revision = "a3c9e1f7b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoicing_product_sales",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate_id", sa.UUID(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EUR"),
        sa.Column("sold_on", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("invoice_id", sa.UUID(), nullable=True),
        sa.Column("invoiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["invoicing_products.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"], ["invoicing_tax_rates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_invoicing_product_sales_org_id"), "invoicing_product_sales", ["org_id"]
    )
    op.create_index(
        "ix_invoicing_product_sales_company",
        "invoicing_product_sales",
        ["org_id", "company_id"],
    )
    op.create_index(
        "ix_invoicing_product_sales_project",
        "invoicing_product_sales",
        ["org_id", "project_id"],
    )
    op.create_index(
        "ix_invoicing_product_sales_invoice",
        "invoicing_product_sales",
        ["org_id", "invoice_id"],
    )
    enable_rls("invoicing_product_sales")

    op.add_column("invoice_lines", sa.Column("sale_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_invoice_lines_sale_id",
        "invoice_lines",
        "invoicing_product_sales",
        ["sale_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_invoice_lines_sale_id", "invoice_lines", type_="foreignkey")
    op.drop_column("invoice_lines", "sale_id")
    disable_rls("invoicing_product_sales")
    op.drop_index("ix_invoicing_product_sales_invoice", table_name="invoicing_product_sales")
    op.drop_index("ix_invoicing_product_sales_project", table_name="invoicing_product_sales")
    op.drop_index("ix_invoicing_product_sales_company", table_name="invoicing_product_sales")
    op.drop_index(
        op.f("ix_invoicing_product_sales_org_id"), table_name="invoicing_product_sales"
    )
    op.drop_table("invoicing_product_sales")
