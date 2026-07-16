"""contacts_add_user_link

Client portal (issue #193): ``contacts.user_id`` — the login a contact is linked to. At most
one login per contact and one contact per login within the org (unique over ``(org_id,
user_id)``; NULLs don't collide). ``ON DELETE SET NULL`` so deleting the account unlinks the
person instead of deleting them. Expand-only, nullable, reversible — the previous image never
reads it, so rollback is safe.

Revision ID: e6a3f8b2c7d9
Revises: d4b8c1e6f3a7
Create Date: 2026-07-16 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e6a3f8b2c7d9'
down_revision: str | None = 'd4b8c1e6f3a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('contacts', sa.Column('user_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_contacts_user_id_users', 'contacts', 'users', ['user_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_unique_constraint('uq_contacts_user', 'contacts', ['org_id', 'user_id'])
    op.create_index('ix_contacts_user_id', 'contacts', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_contacts_user_id', table_name='contacts')
    op.drop_constraint('uq_contacts_user', 'contacts', type_='unique')
    op.drop_constraint('fk_contacts_user_id_users', 'contacts', type_='foreignkey')
    op.drop_column('contacts', 'user_id')
