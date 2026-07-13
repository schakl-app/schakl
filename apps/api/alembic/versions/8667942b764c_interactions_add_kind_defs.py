"""interactions_add_kind_defs

Issue #174: tenant-configurable interaction kinds (the contact-types shape: key +
label_i18n + position + active). ``interactions.kind`` becomes a free key into the org's
kind list — the column widens from 10 to 50 chars — and the hardcoded ``meeting`` kind is
split into ``online_meeting`` / ``physical_meeting``. Every existing ``meeting`` row is
remapped to ``physical_meeting`` (the closer semantic match to plain "Meeting"; the org can
re-tag afterwards). Default kinds seed lazily per org on first use, the leave-types pattern,
so this migration seeds nothing.

Upgrade path: a new table, a widened varchar (non-destructive), and an idempotent per-org
remap (``interactions`` is RLS-FORCED, so the update binds the GUC per org — the
39683461b57a pattern). The previous image never wrote ``online_meeting``/``physical_meeting``
but reads any string fine (the column was already a bare varchar), so rollback works; the
real ``downgrade()`` remaps both new kinds back to ``meeting`` and narrows the column again.

Revision ID: 8667942b764c
Revises: c4f8b26d9a17
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '8667942b764c'
down_revision: str | None = 'c4f8b26d9a17'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _per_org_remap(kind_from: str, kind_to: str) -> None:
    """Remap one kind key across every org — GUC per org, RLS is FORCED on interactions."""
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                "UPDATE interactions SET kind = :to WHERE org_id = :org_id AND kind = :from"
            ),
            {"to": kind_to, "from": kind_from, "org_id": str(org_id)},
        )


def upgrade() -> None:
    op.create_table(
        'interaction_kinds',
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('label_i18n', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_interaction_kinds_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_interaction_kinds')),
        sa.UniqueConstraint('org_id', 'key', name='uq_interaction_kinds_org_key'),
    )
    op.create_index(op.f('ix_interaction_kinds_org_id'), 'interaction_kinds',
                    ['org_id'], unique=False)
    # Tenant isolation at the database layer too (Golden Rule 1).
    enable_rls('interaction_kinds')

    # Kind keys are tenant-defined now — 10 chars was the old enum's ceiling.
    op.alter_column('interactions', 'kind',
                    existing_type=sa.String(length=10),
                    type_=sa.String(length=50),
                    existing_nullable=False)

    # Split "meeting": existing rows land on physical_meeting (idempotent by construction —
    # a second run finds no 'meeting' rows left).
    _per_org_remap('meeting', 'physical_meeting')


def downgrade() -> None:
    # Fold the split back together before narrowing, or the ALTER would truncate/fail.
    _per_org_remap('physical_meeting', 'meeting')
    _per_org_remap('online_meeting', 'meeting')
    # A tenant-defined key longer than 10 chars cannot survive the narrowing; clamp it to the
    # old enum's fallback rather than aborting a rollback on someone's production data.
    _per_org_remap_long_to_note()
    op.alter_column('interactions', 'kind',
                    existing_type=sa.String(length=50),
                    type_=sa.String(length=10),
                    existing_nullable=False)

    disable_rls('interaction_kinds')
    op.drop_index(op.f('ix_interaction_kinds_org_id'), table_name='interaction_kinds')
    op.drop_table('interaction_kinds')


def _per_org_remap_long_to_note() -> None:
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                "UPDATE interactions SET kind = 'note' "
                "WHERE org_id = :org_id AND length(kind) > 10"
            ),
            {"org_id": str(org_id)},
        )
