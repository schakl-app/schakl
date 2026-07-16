"""auth_create_user_two_factor

Two-factor authentication for local login (TOTP + backup codes + optional SMS): one row per
enrolled user in ``user_two_factor``. Like ``users``, this is **global identity, not tenant
data** (CLAUDE.md §5) — a user's second factor follows them across every org they belong to —
so the table carries no ``org_id`` and no RLS; org admins interact with it only through the
tenant-scoped members surface (reset).

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive.** One new table, nothing touched elsewhere; applies on top of any released
  ``head``. Existing users simply have no row — password login is unchanged until they enroll.
* **Rollback-safe.** The previous image never reads ``user_two_factor``; rolling back merely
  stops enforcing second factors (accounts fall back to password login — availability over
  enforcement, and their enrollment is intact when the image rolls forward again).
* **Reversible.** ``downgrade()`` drops the table (enrollments are lost, logins keep working).

Revision ID: 661bb9ebf62b
Revises: c4e1b8a6f025
Create Date: 2026-07-16 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '661bb9ebf62b'
down_revision: str | None = 'c4e1b8a6f025'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_two_factor',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('totp_secret_enc', sa.Text(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('totp_last_counter', sa.BigInteger(), nullable=True),
        sa.Column(
            'backup_codes',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column('sms_phone', sa.String(length=32), nullable=True),
        sa.Column('sms_confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sms_pending_phone', sa.String(length=32), nullable=True),
        sa.Column('sms_code_hash', sa.String(length=64), nullable=True),
        sa.Column('sms_code_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sms_code_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sms_code_attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_failed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_user_two_factor_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_two_factor')),
        sa.UniqueConstraint('user_id', name='uq_user_two_factor_user'),
    )
    op.create_index(op.f('ix_user_two_factor_user_id'), 'user_two_factor', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_two_factor_user_id'), table_name='user_two_factor')
    op.drop_table('user_two_factor')
