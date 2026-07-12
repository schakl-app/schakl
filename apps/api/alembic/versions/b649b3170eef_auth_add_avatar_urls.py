"""auth_add_avatar_urls

Revision ID: b649b3170eef
Revises: 7dc80932d0e0
Create Date: 2026-07-12 00:00:00.000000

Additive expand (#122): two nullable URL columns on users — the IdP's picture claim
(refreshed each SSO login) and the personal override. NULL everywhere is exactly today's
behaviour (initials). Reversible; the previous release never reads them.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b649b3170eef'
down_revision: str | None = '7dc80932d0e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('oidc_avatar_url', sa.String(length=1024), nullable=True))
    op.add_column('users', sa.Column('custom_avatar_url', sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'custom_avatar_url')
    op.drop_column('users', 'oidc_avatar_url')
