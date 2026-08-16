"""google_gmail_skips

The two ingest skips worth remembering: a message deferred to a colleague's mailbox that then
never logged it, and one the ingest raised on. Everything else the poller declines is policy,
explained on demand by re-running the decision against the one message somebody asks about —
a row per decision would be a record of every email the mailbox receives, which is more than
this module is allowed to know (``app/modules/google/gmail/gates.py``).

Ids, a reason and a timestamp; no subject, no participants, no snippet. A daily cron reaps the
rows past their retention window, because a permanent record of a transient failure is a log by
another name.

Upgrade path: **expand-only**. A new table nothing else references, so a released image that
knows nothing about it keeps polling exactly as before, and the downgrade drops it with its RLS
policy. Nothing to backfill: the rows describe events, and the events we missed are missed.

Revision ID: f4c8a2e37b19
Revises: b3f9a17c62d4
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "f4c8a2e37b19"
down_revision: str | None = "b3f9a17c62d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gmail_skips",
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=64), nullable=False),
        sa.Column("gmail_thread_id", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["google_connections.id"],
            name=op.f("fk_gmail_skips_connection_id_google_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
            name=op.f("fk_gmail_skips_org_id_orgs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gmail_skips")),
    )
    op.create_index(op.f("ix_gmail_skips_org_id"), "gmail_skips", ["org_id"])
    op.create_index(op.f("ix_gmail_skips_connection_id"), "gmail_skips", ["connection_id"])
    # The upsert target: one row per message per mailbox, so a message re-offered on every
    # poll leaves one row and the retention window means what it says.
    op.create_index(
        "uq_gmail_skips_org_conn_message",
        "gmail_skips",
        ["org_id", "connection_id", "gmail_message_id"],
        unique=True,
    )
    # The reaper's whole query.
    op.create_index("ix_gmail_skips_org_created", "gmail_skips", ["org_id", "created_at"])
    enable_rls("gmail_skips")


def downgrade() -> None:
    disable_rls("gmail_skips")
    op.drop_index("ix_gmail_skips_org_created", table_name="gmail_skips")
    op.drop_index("uq_gmail_skips_org_conn_message", table_name="gmail_skips")
    op.drop_index(op.f("ix_gmail_skips_connection_id"), table_name="gmail_skips")
    op.drop_index(op.f("ix_gmail_skips_org_id"), table_name="gmail_skips")
    op.drop_table("gmail_skips")
