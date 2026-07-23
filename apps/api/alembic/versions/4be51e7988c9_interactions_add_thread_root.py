"""interactions_add_thread_root

Fold uploaded ``.eml`` emails into conversations by their RFC 5322 threading headers (#272,
option B). An uploaded message has no ``gmail_thread_id`` — it came from a file, not a mailbox —
so the Gmail-thread grouping never reached it. Instead we thread on the message's own
``In-Reply-To`` / ``References`` headers: ``thread_root_id`` is the oldest Message-ID in that
chain (the thread root), and two uploads fold when they share a root or one references the
other's ``rfc822_message_id``. Both are scalar equality matches over indexed columns — no JSONB
operators — so this stays cheap.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older ``head``. The column add is
  unconditional and nullable.
* **What happens to existing rows?** ``thread_root_id`` is nullable with no default: every
  existing row keeps working, a ``NULL`` simply doesn't participate in upload-thread matching.
* **No backfill.** The raw ``.eml`` headers of already-uploaded emails were never retained (only
  the parsed body + attachments were stored), so their thread root can't be reconstructed — and
  Gmail rows are grouped by ``gmail_thread_id``, not this column. Only uploads logged *after*
  this migration thread by header; existing uploads stay their own singletons. That is the
  honest limit of not having kept the bytes, not a bug.
* **Is it reversible?** Yes — ``downgrade()`` drops the index and column.
* **Can the previous image still run against the new schema?** Yes — the previous release never
  selects ``thread_root_id``.

Revision ID: 4be51e7988c9
Revises: f4b2c7e19a05
Create Date: 2026-07-23 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4be51e7988c9'
down_revision: str | None = 'f4b2c7e19a05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'interactions',
        sa.Column('thread_root_id', sa.String(length=512), nullable=True),
    )
    op.create_index(
        op.f('ix_interactions_org_thread_root'),
        'interactions',
        ['org_id', 'thread_root_id'],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_interactions_org_thread_root'), table_name='interactions')
    op.drop_column('interactions', 'thread_root_id')
