"""ai_add_speech_transcription

Revision ID: e2c5a90d47bf
Revises: d1a7f3b60c92
Create Date: 2026-08-04 12:00:00.000000

Speech-to-text for the time quick-add (#246): a tenant can speak an entry instead of typing it.

Purely additive — five nullable columns on ``ai_settings`` and one NOT NULL DEFAULT 0 on
``ai_usage`` — so an existing install upgrades unattended and the previous image runs unchanged
against this schema (docs/WORKFLOW.md: nothing to contract, because nothing is touched, retyped
or renamed). A rollback drops only what this created.

Two column choices carry the design rather than merely storing it:

* **The speech credential is its own, not the chat one.** Anthropic has no transcription
  endpoint and is this product's default provider, so "reuse whatever the org configured for
  chat" resolves to nothing for the typical tenant. ``speech_provider`` / ``speech_base_url`` /
  ``speech_api_key_enc`` / ``speech_model`` let an org keep Claude for writing and point audio
  at a service that can actually transcribe. All nullable: NULL means "reuse the chat
  provider", which is the right answer for an OpenAI-configured org and resolves to "speech is
  off" for an Anthropic one — the surface is hidden rather than offered and then 409'd.
* **``audio_seconds`` is a column, not a reuse of ``tokens_out``.** A transcription response
  reports no token usage at all, so seconds are the only unit available. Folding them into the
  token counters would inflate ``_month_tokens()`` — the number the monthly token budget is
  enforced against — and quietly corrupt the settings meter, which is the one place a tenant
  looks to understand what AI is costing them. ``monthly_audio_seconds_budget`` is the separate
  cap that follows from the separate unit.

``speech_api_key_enc`` holds the same Fernet ciphertext shape as ``api_key_enc`` (#126): the key
is write-only and never returned by the API.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e2c5a90d47bf"
down_revision: str | None = "d1a7f3b60c92"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("ai_settings", sa.Column("speech_provider", sa.String(20), nullable=True))
    op.add_column("ai_settings", sa.Column("speech_base_url", sa.String(1024), nullable=True))
    op.add_column("ai_settings", sa.Column("speech_api_key_enc", sa.Text(), nullable=True))
    op.add_column("ai_settings", sa.Column("speech_model", sa.String(255), nullable=True))
    op.add_column(
        "ai_settings",
        sa.Column("monthly_audio_seconds_budget", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "ai_usage",
        sa.Column(
            "audio_seconds", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_usage", "audio_seconds")
    op.drop_column("ai_settings", "monthly_audio_seconds_budget")
    op.drop_column("ai_settings", "speech_model")
    op.drop_column("ai_settings", "speech_api_key_enc")
    op.drop_column("ai_settings", "speech_base_url")
    op.drop_column("ai_settings", "speech_provider")
