"""email_create_settings

Org-level e-mail transport (#17): one row per org selecting a provider (Brevo / SendGrid /
SMTP2GO official APIs, or plain SMTP) whose secrets live encrypted in ``config_enc``.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive.** One new RLS-forced table, no columns touched elsewhere; applies on top of any
  released ``head``. Nothing to backfill — e-mail is off until an org configures it.
* **Rollback-safe.** The previous image never selects ``email_settings``.
* The new ``settings.email.manage`` permission reaches existing orgs via the startup reconciler
  (a migration must never import the catalog).

Revision ID: 9acc35eb863a
Revises: d7e2f4a91c36
Create Date: 2026-07-12 16:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '9acc35eb863a'
down_revision: str | None = 'd7e2f4a91c36'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'email_settings',
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('config_enc', sa.Text(), nullable=False),
        sa.Column('from_email', sa.String(length=320), nullable=False),
        sa.Column('from_name', sa.String(length=255), nullable=False),
        sa.Column('reply_to', sa.String(length=320), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_email_settings_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_email_settings')),
        sa.UniqueConstraint('org_id', name='uq_email_settings_org'),
    )
    op.create_index(op.f('ix_email_settings_org_id'), 'email_settings', ['org_id'], unique=False)

    enable_rls('email_settings')


def downgrade() -> None:
    disable_rls('email_settings')
    op.drop_index(op.f('ix_email_settings_org_id'), table_name='email_settings')
    op.drop_table('email_settings')
