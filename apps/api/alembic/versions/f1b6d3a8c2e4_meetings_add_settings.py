"""meetings_add_settings

Revision ID: f1b6d3a8c2e4
Revises: e5a8c3d7f1b2
Create Date: 2026-09-22

Org-wide meetings settings (one row per org, absent = the defaults): whether the recorder asks
for the consent statement, what the minutes document looks like, and the agency's own writing
instructions for the minutes. Purely additive and rollback-safe — a new table nothing older
reads, so an instance that upgrades and types nothing records, minutes and prints exactly as it
did.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

revision: str = "f1b6d3a8c2e4"
down_revision: str | None = "e5a8c3d7f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "meeting_settings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column(
            "consent_required", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "document_design", sa.String(length=32), server_default="standard", nullable=False
        ),
        sa.Column("document_accent_color", sa.String(length=16), nullable=True),
        sa.Column("document_cover_file_id", sa.UUID(), nullable=True),
        sa.Column("document_footer_text", sa.Text(), nullable=True),
        sa.Column(
            "document_sections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "document_avatars", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("document_custom_html", sa.Text(), nullable=True),
        sa.Column("document_custom_css", sa.Text(), nullable=True),
        sa.Column("ai_instructions", sa.Text(), nullable=True),
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
            ["org_id"], ["orgs.id"], name=op.f("fk_meeting_settings_org_id_orgs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_cover_file_id"],
            ["files.id"],
            name=op.f("fk_meeting_settings_document_cover_file_id_files"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meeting_settings")),
        sa.UniqueConstraint("org_id", name="uq_meeting_settings_org"),
    )
    op.create_index(op.f("ix_meeting_settings_org_id"), _TABLE, ["org_id"])
    enable_rls(_TABLE)


def downgrade() -> None:
    disable_rls(_TABLE)
    op.drop_index(op.f("ix_meeting_settings_org_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
