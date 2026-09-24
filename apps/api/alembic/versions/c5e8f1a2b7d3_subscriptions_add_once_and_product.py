"""subscriptions: an agreement may name the product it sells, and a one-time sale is an agreement

Two additive columns and one reversal. ``subscription_templates.product_id`` and
``subscriptions.product_id`` (bare UUIDs, §6: the price list is the invoicing module's) say which
product a standard subscription or an agreement sells, so the price list can answer where it is
used. ``interval = 'once'`` and ``status = 'completed'`` need no schema — both columns are free
text — and are what makes a one-time product an ordinary agreement rather than a record of its
own: the separate ``invoicing_product_sales`` table (``b4d7e2f9a1c6``, never released) and the
``invoice_lines.sale_id`` it was claimed through are dropped here.

Revision ID: c5e8f1a2b7d3
Revises: b4d7e2f9a1c6
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision = "c5e8f1a2b7d3"
down_revision = "b4d7e2f9a1c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscription_templates", sa.Column("product_id", sa.UUID(), nullable=True))
    op.add_column("subscriptions", sa.Column("product_id", sa.UUID(), nullable=True))
    op.create_index(
        "ix_subscriptions_product", "subscriptions", ["org_id", "product_id"]
    )

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


def downgrade() -> None:
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
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
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
        "ix_invoicing_product_sales_company", "invoicing_product_sales", ["org_id", "company_id"]
    )
    op.create_index(
        "ix_invoicing_product_sales_project", "invoicing_product_sales", ["org_id", "project_id"]
    )
    op.create_index(
        "ix_invoicing_product_sales_invoice", "invoicing_product_sales", ["org_id", "invoice_id"]
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

    op.drop_index("ix_subscriptions_product", table_name="subscriptions")
    op.drop_column("subscriptions", "product_id")
    op.drop_column("subscription_templates", "product_id")
