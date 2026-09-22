"""meetings: the recorded-meeting record (transcript, minutes, links to what confirm produced)

Purely additive — one new table, RLS-forced like every domain table. An instance that upgrades
unattended gains a module that is off until Instellingen → Modules switches it on.

Revision ID: d4e7f2a9c1b6
Revises: d2f7c4e1b9a3
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision = "d4e7f2a9c1b6"
down_revision = "d2f7c4e1b9a3"
branch_labels = None
depends_on = None

_TABLE = "meetings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_key", sa.String(length=120), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=True),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("participants_informed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("chunks_received", sa.Integer(), server_default="0", nullable=False),
        sa.Column("audio_format", sa.String(length=20), nullable=True),
        sa.Column("audio_file_id", sa.UUID(), nullable=True),
        sa.Column("transcript", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("speakers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("minutes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("interaction_id", sa.UUID(), nullable=True),
        sa.Column("task_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["audio_file_id"], ["files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meetings_org_id"), _TABLE, ["org_id"])
    op.create_index(op.f("ix_meetings_company_id"), _TABLE, ["company_id"])
    op.create_index("ix_meetings_org_occurred", _TABLE, ["org_id", "occurred_at"])
    op.create_index("ix_meetings_org_status", _TABLE, ["org_id", "status"])
    enable_rls(_TABLE)


def downgrade() -> None:
    disable_rls(_TABLE)
    op.drop_index("ix_meetings_org_status", table_name=_TABLE)
    op.drop_index("ix_meetings_org_occurred", table_name=_TABLE)
    op.drop_index(op.f("ix_meetings_company_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_meetings_org_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
