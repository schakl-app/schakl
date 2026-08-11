"""merge_leave_and_uptime_heads

Revision ID: b7e21d5c9034
Revises: a2e4f6b30d19, c4e8b1a92f57
Create Date: 2026-08-11 12:00:00.000000

Two branches, both chained onto ``f3b6c81a9e27``, landed on ``dev`` independently:
``a2e4f6b30d19`` (leave: freelance employment) and ``c4e8b1a92f57`` (uptime: create tables).

That is a **broken tree, not a stylistic problem**: with two heads, ``alembic upgrade head``
refuses outright, which takes down every deployment's unattended upgrade *and*
``tests/conftest.py``'s session fixture, so the whole suite errors at setup for everyone —
including the two branches that caused it. Nothing is wrong with either migration; they simply
cannot both be the tip.

This is the standard empty merge point (``alembic merge``). It creates nothing, drops nothing
and is trivially removable if the branches are re-linearised instead. It is here rather than in
its own change because the next migration needs a single head to chain onto, and leaving the
fork in place while adding a third head would make it worse.

The lesson is the one docs/WORKFLOW.md already states and is worth restating on the file that
had to clean it up: **chain onto the last *pushed* head, re-checked immediately before writing
the file** — two agents each reading a local head they had not published is exactly how this
shape appears.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "b7e21d5c9034"
down_revision: tuple[str, ...] = ("a2e4f6b30d19", "c4e8b1a92f57")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A merge point has no schema of its own."""


def downgrade() -> None:
    """Likewise — going back re-forks the tree, which is the state this repaired."""
