"""projects_add_responsible

Adds ``responsible_user_id`` (verantwoordelijke) to projects. Defaults from the parent
company on create (app layer) and seeds new tasks' assignee; overridable per project.
Nullable FK → ``users.id`` with ``ON DELETE SET NULL``. Additive DDL only.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-07-08 10:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a6b7c8d9e0f1'
down_revision: str | None = 'f5a6b7c8d9e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'projects',
        sa.Column(
            'responsible_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index(
        op.f('ix_projects_responsible_user_id'),
        'projects',
        ['responsible_user_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_projects_responsible_user_id'), table_name='projects')
    op.drop_column('projects', 'responsible_user_id')
