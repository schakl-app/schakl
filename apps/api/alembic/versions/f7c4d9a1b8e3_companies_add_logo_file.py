"""companies_add_logo_file

Per-client logo (issue #196): ``companies.logo_file_id`` referencing a stored file (#123) —
reuse of the blob mechanism, no new blob column. ``ON DELETE SET NULL`` so removing the file
row merely unsets the logo. Expand-only, nullable, reversible; the previous image never reads
it, so rollback is safe.

Revision ID: f7c4d9a1b8e3
Revises: e6a3f8b2c7d9
Create Date: 2026-07-16 14:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7c4d9a1b8e3'
down_revision: str | None = 'e6a3f8b2c7d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('logo_file_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_companies_logo_file_id_files', 'companies', 'files', ['logo_file_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_companies_logo_file_id_files', 'companies', type_='foreignkey')
    op.drop_column('companies', 'logo_file_id')
