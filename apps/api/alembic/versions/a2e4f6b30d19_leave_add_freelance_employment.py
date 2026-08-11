"""leave_add_freelance_employment

Freelance engagements in Dienstverband, and the availability a freelancer keeps themselves.

Three parts:
  * ``employment_contracts.employment_type`` — ``employee`` (the server default, so every
    existing row is payroll) or ``freelance``. A freelance period accrues no statutory vacation
    and no free time; nothing else about a period changes.
  * ``employment_contracts.contract_hours_per_week`` becomes nullable — "no fixed weekly
    commitment", which only a freelance period may say. The service refuses an employee period
    without hours, so every accrual path still only ever reads a populated value.
  * ``employment_availability`` — org-scoped, RLS-forced. Dated bends in the base week (an extra
    day, an unavailable day), with the repeat expressed as a rule on the row rather than as
    generated occurrences: availability is computed, so there is nothing to place.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive / expand-only.** One new table and one new column; the one column whose *type*
  changes is widened (NOT NULL → NULL), never narrowed. No existing row changes meaning, so no
  balance moves on upgrade and no backfill is needed.
* ``employment_type`` lands NOT NULL with an ``'employee'`` server default, so populated rows are
  filled without a rewrite. The default is kept on the column (not dropped as
  ``accrues_schedule_gap``'s was): the previous image inserts contracts without this column, so a
  rollback that then rolls forward again must still be able to write.
* **Rollback-safe with one caveat, and it is why the default stays.** The previous image never
  selects ``employment_type``, ``employment_availability`` or a NULL ``contract_hours_per_week``
  — but it *would* fail on a freelance row saved without hours, since its ORM maps that column
  NOT NULL. Nothing can produce such a row until the new image is running, so the ordinary
  upgrade path is safe; a roll-forward-then-back after freelance periods exist is the case to
  state in the release notes.

Revision ID: a2e4f6b30d19
Revises: f3b6c81a9e27
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = "a2e4f6b30d19"
down_revision: str | None = "f3b6c81a9e27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "employment_contracts",
        sa.Column(
            "employment_type",
            sa.String(length=20),
            nullable=False,
            server_default="employee",
        ),
    )
    op.alter_column(
        "employment_contracts",
        "contract_hours_per_week",
        existing_type=sa.Numeric(precision=5, scale=2),
        nullable=True,
    )

    op.create_table(
        "employment_availability",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("repeat_weeks", sa.Integer(), nullable=True),
        sa.Column("repeat_until", sa.Date(), nullable=True),
        sa.Column("pair_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
            ["org_id"],
            ["orgs.id"],
            name=op.f("fk_employment_availability_org_id_orgs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_employment_availability_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employment_availability")),
    )
    op.create_index(
        op.f("ix_employment_availability_org_id"),
        "employment_availability",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_employment_availability_user_id"),
        "employment_availability",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_employment_availability_date"),
        "employment_availability",
        ["date"],
        unique=False,
    )
    # The read is always "this person, this window", so the composite is what serves it; the
    # single-column indexes above come with the FK/model declarations (docs/PERFORMANCE.md).
    op.create_index(
        "ix_employment_availability_org_user_date",
        "employment_availability",
        ["org_id", "user_id", "date"],
        unique=False,
    )

    enable_rls("employment_availability")


def downgrade() -> None:
    disable_rls("employment_availability")
    op.drop_index(
        "ix_employment_availability_org_user_date", table_name="employment_availability"
    )
    op.drop_index(
        op.f("ix_employment_availability_date"), table_name="employment_availability"
    )
    op.drop_index(
        op.f("ix_employment_availability_user_id"), table_name="employment_availability"
    )
    op.drop_index(
        op.f("ix_employment_availability_org_id"), table_name="employment_availability"
    )
    op.drop_table("employment_availability")
    # A freelance period with no agreed hours cannot survive the column going back to NOT NULL,
    # and inventing one would be this migration deciding somebody's contract. Filling them with
    # the org's full-time norm would be a number nobody agreed to, so they are set to 0 — the
    # honest "no commitment recorded" the old schema is able to hold. Stated in the release notes.
    #
    # **Per org under a bound GUC**, because ``employment_contracts`` is RLS-FORCED and the
    # migration runs as ``schakl_app``: an unbound UPDATE matches nothing, reports success, and
    # the ``SET NOT NULL`` below then fails on the rows it was supposed to have fixed.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                "UPDATE employment_contracts SET contract_hours_per_week = 0 "
                "WHERE contract_hours_per_week IS NULL"
            )
        )
    op.alter_column(
        "employment_contracts",
        "contract_hours_per_week",
        existing_type=sa.Numeric(precision=5, scale=2),
        nullable=False,
    )
    op.drop_column("employment_contracts", "employment_type")
