"""companies_projects_create_assignees

A client or project had exactly one responsible employee. Several people work an account —
one owns it, the rest are involved — so this adds ``company_assignees`` and ``project_assignees``:
``(org_id, <entity>_id, user_id, is_primary)`` with a partial unique index making at most one
primary possible, the same guard ``company_contacts`` uses for the primary contact person.

**Expand only** (docs/WORKFLOW.md). ``responsible_user_id`` stays on both tables and keeps
mirroring the primary, so:

  * every existing release upgrades into this — the column it reads is still there and correct;
  * rolling the image tag back leaves old code on this schema, and it still works;
  * the contract migration (dropping the column) ships once no released reader is left.

Every existing ``responsible_user_id`` is backfilled as that entity's primary assignee. The
backfill is idempotent (``ON CONFLICT DO NOTHING``) and set-based, so re-running it on a
populated database is a no-op rather than a duplicate-key abort.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-09 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: str | None = 'd4e5f6a7b8c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTITIES = ("company", "project")


def _table(entity: str) -> str:
    return f"{entity}_assignees"


def upgrade() -> None:
    for entity in _ENTITIES:
        table = _table(entity)
        fk = f"{entity}_id"
        parent = f"{entity[:-1]}ies" if entity.endswith("y") else f"{entity}s"
        op.create_table(
            table,
            sa.Column(fk, sa.UUID(), nullable=False),
            sa.Column('user_id', sa.UUID(), nullable=False),
            sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('org_id', sa.UUID(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint([fk], [f'{parent}.id'], name=op.f(f'fk_{table}_{fk}_{parent}'), ondelete='CASCADE'),
            # A removed member loses their assignments; the entity itself is never orphaned
            # (its mirrored responsible_user_id is SET NULL).
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f(f'fk_{table}_user_id_users'), ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f(f'fk_{table}_org_id_orgs'), ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id', name=op.f(f'pk_{table}')),
            sa.UniqueConstraint('org_id', fk, 'user_id', name=f'uq_{table}_link'),
        )
        op.create_index(op.f(f'ix_{table}_{fk}'), table, [fk], unique=False)
        op.create_index(op.f(f'ix_{table}_user_id'), table, ['user_id'], unique=False)
        op.create_index(op.f(f'ix_{table}_org_id'), table, ['org_id'], unique=False)
        # At most one primary assignee per entity (partial unique index).
        op.create_index(
            f'uq_{table}_primary',
            table,
            ['org_id', fk],
            unique=True,
            postgresql_where=sa.text('is_primary'),
        )

        # Backfill: today's single responsible becomes the primary assignee.
        #
        # Migrations run as the table owner under FORCE ROW LEVEL SECURITY with no tenant GUC set,
        # so an unqualified read of ``companies``/``projects`` returns zero rows (RLS fails closed)
        # and every existing responsible would be silently lost. Exempt the owner for the copy,
        # then restore FORCE.
        op.execute(f"ALTER TABLE {parent} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            INSERT INTO {table} (id, org_id, {fk}, user_id, is_primary, created_at, updated_at)
            SELECT gen_random_uuid(), e.org_id, e.id, e.responsible_user_id, true, now(), now()
            FROM {parent} e
            WHERE e.responsible_user_id IS NOT NULL
            ON CONFLICT ON CONSTRAINT uq_{table}_link DO NOTHING
            """
        )
        op.execute(f"ALTER TABLE {parent} FORCE ROW LEVEL SECURITY")

        # Tenant isolation (defence-in-depth): links are org-scoped, RLS-forced (CLAUDE.md §5).
        enable_rls(table)


def downgrade() -> None:
    # ``responsible_user_id`` was never dropped and has been kept in step with the primary on
    # every write, so there is nothing to restore — the links simply go away.
    for entity in _ENTITIES:
        table = _table(entity)
        fk = f"{entity}_id"
        disable_rls(table)
        op.drop_index(f'uq_{table}_primary', table_name=table)
        op.drop_index(op.f(f'ix_{table}_org_id'), table_name=table)
        op.drop_index(op.f(f'ix_{table}_user_id'), table_name=table)
        op.drop_index(op.f(f'ix_{table}_{fk}'), table_name=table)
        op.drop_table(table)
