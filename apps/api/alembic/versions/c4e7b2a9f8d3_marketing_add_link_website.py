"""marketing_add_link_website

A marketing link can point at the client *website* it measures, so a client with several
sites gets its GA4/GSC/Ads properties grouped per site instead of one flat client-level
list. Expand-only: the column is nullable (NULL = client-level, exactly today's behaviour)
and ON DELETE SET NULL keeps the link and its synced history when a website is removed.

Revision ID: c4e7b2a9f8d3
Revises: b2d8f5a3c6e1
Create Date: 2026-07-16 19:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e7b2a9f8d3'
down_revision: str | None = 'b2d8f5a3c6e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'marketing_links',
        sa.Column('website_id', PGUUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        'ix_marketing_links_website_id', 'marketing_links', ['website_id']
    )
    op.create_foreign_key(
        'fk_marketing_links_website_id',
        'marketing_links',
        'websites',
        ['website_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_marketing_links_website_id', 'marketing_links', type_='foreignkey')
    op.drop_index('ix_marketing_links_website_id', table_name='marketing_links')
    op.drop_column('marketing_links', 'website_id')
