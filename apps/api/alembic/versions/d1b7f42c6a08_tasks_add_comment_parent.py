"""tasks_add_comment_parent

Give a comment somewhere to put an answer (#312).

``task_comments`` was a flat list, so answering someone meant writing a new comment at the bottom
and hoping the reader still had the question in view. On a task people work for a month that is a
transcript, not a conversation: the reply to the third comment sits under the twentieth.

``parent_id`` is a self-reference — NULL opens a thread, set answers one. **One level deep**, which
the service enforces by re-rooting a reply-to-a-reply onto the same parent rather than refusing it;
the column could carry a tree and deliberately never does, because a tree indents itself off a
phone screen and gives two readers two different reading orders.

``ON DELETE CASCADE`` because a thread is one conversation: answers to a question that is gone
record nothing. The confirmation dialog counts the replies out loud before it happens, and the
activity trail keeps the count (docs/UX.md — every inline sub-item delete is written to the feed).

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older head. It adds one nullable column and
  its index; nothing is rewritten.
* **What happens to existing rows?** They get ``NULL`` — every comment written before today opens
  its own thread, which is exactly what it did.
* **Is it reversible?** Yes. ``downgrade`` drops the column, and every reply becomes an ordinary
  comment in the same chronological place it already occupied. Nothing is deleted.
* **Does anything read it before it is written?** The detail read orders threads by it and the web
  nests on it; ``NULL`` everywhere is the flat list that shipped yesterday.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "d1b7f42c6a08"
down_revision: str | None = "c4a1e77b2d19"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "task_comments",
        sa.Column("parent_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_task_comments_parent_id_task_comments",
        "task_comments",
        "task_comments",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_task_comments_parent_id", "task_comments", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_task_comments_parent_id", table_name="task_comments")
    op.drop_constraint(
        "fk_task_comments_parent_id_task_comments", "task_comments", type_="foreignkey"
    )
    op.drop_column("task_comments", "parent_id")
