"""interactions_email_markdown_body

An e-mail body was stored as stripped text — every list, heading, link, emphasis and quote
level gone — because the only thing that read it was a pre-wrapped `<p>`. Two columns change
that:

* ``interactions.body_markdown`` — the same message with its formatting kept, written **only**
  when we converted it from an HTML part ourselves. That condition is the design: a received
  body is not our markdown, so rendering an arbitrary plain-text mail as markdown would turn a
  sender's ``*sterretjes*`` into italics. ``body_text`` is untouched and stays the plain-text
  surface search reads and the snippet is cut from.
* ``files.content_id`` — set means the file is part of its entity's *body* rather than an
  attachment of it (an e-mail's ``cid:``-referenced signature logo). The body's image marker is
  rewritten from it, and its presence is what keeps that logo out of the attachment chips on
  every message.

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Expand only, and nothing is backfilled.** Both columns are nullable and start ``NULL``.
  Existing rows keep rendering exactly as they do today — the plain-text branch is still there
  and is still what a message without ``body_markdown`` takes. Only mail ingested after the
  upgrade carries formatting; there is no re-parse of history, because the HTML part of an
  already-ingested message was never stored.
* **Rolling the image tag back is safe.** The previous release selects named columns and
  ignores these two, so a mail ingested by this release reads as the plain text it also wrote,
  and an inline logo reappears as an ordinary attachment — degraded, never broken.
* **Reversible.** ``downgrade`` drops both columns. It loses the converted bodies, which are
  derived data: the plain text they were converted alongside is still there.

Revision ID: e6b3f0a2c74d
Revises: c4a7e18b3d90
Create Date: 2026-08-05 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e6b3f0a2c74d'
down_revision: str | None = 'c4a7e18b3d90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('interactions', sa.Column('body_markdown', sa.Text(), nullable=True))
    op.add_column('files', sa.Column('content_id', sa.String(length=255), nullable=True))
    # The attachment list asks "this entity's files that are *not* inline" on every open of an
    # e-mail. Partial on the inline rows, which are the few: the index exists to make excluding
    # them free, not to make finding them fast.
    op.create_index(
        'ix_files_inline',
        'files',
        ['org_id', 'entity_type', 'entity_id'],
        postgresql_where=sa.text('content_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_files_inline', table_name='files')
    op.drop_column('files', 'content_id')
    op.drop_column('interactions', 'body_markdown')
