"""invoicing_add_import_origin

Revision ID: a9c4e17f2b5d
Revises: f3b8d2a9c4e1
Create Date: 2026-09-02 10:00:00.000000

Invoices an agency brings in from the system it used before schakl (docs/INVOICING.md,
"Bringing the back catalogue in"). Five columns on ``invoices``, all additive and reversible:

* ``origin`` — ``native`` (raised here) or ``imported`` (issued elsewhere, recorded here).
  ``NOT NULL DEFAULT 'native'`` with a server default, so every existing row is what it always
  was, and the previous image — which never reads the column — keeps working against it.
* ``import_source`` / ``imported_at`` — where it came from and when it arrived.
* ``original_file_id`` — the document the client actually received, as a stored file
  (``ON DELETE SET NULL``: losing the file row unsets the pointer, it never takes the invoice).
* ``original_sha256`` — the invoice's **own** record of the fingerprint of that document, so
  "is this still the file that was attached" is answerable without trusting the blob table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e17f2b5d"
down_revision: str | None = "f3b8d2a9c4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "origin", sa.String(length=20), nullable=False, server_default="native"
        ),
    )
    op.add_column(
        "invoices", sa.Column("import_source", sa.String(length=80), nullable=True)
    )
    op.add_column(
        "invoices", sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("invoices", sa.Column("original_file_id", sa.UUID(), nullable=True))
    op.add_column(
        "invoices", sa.Column("original_sha256", sa.String(length=64), nullable=True)
    )
    op.create_foreign_key(
        "fk_invoices_original_file_id_files",
        "invoices",
        "files",
        ["original_file_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_invoices_original_file_id_files", "invoices", type_="foreignkey")
    op.drop_column("invoices", "original_sha256")
    op.drop_column("invoices", "original_file_id")
    op.drop_column("invoices", "imported_at")
    op.drop_column("invoices", "import_source")
    op.drop_column("invoices", "origin")
