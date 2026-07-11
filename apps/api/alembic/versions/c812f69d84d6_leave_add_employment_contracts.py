"""leave_add_employment_contracts

Employment contracts and roostervrije tijd / ADV (#65). Contract hours are a legal fact,
finally distinct from *scheduled* hours: a 38-hour contract worked as a 40-hour week accrues
2 h/week of ADV, taken as a movable free day.

Three parts:
  * ``employment_contracts`` — org-scoped, RLS-forced. One period per row; ``end_date`` NULL is
    open-ended. Non-overlap is enforced in the service layer (a ``daterange`` exclusion
    constraint needs ``btree_gist``, which the ``schakl_app`` role cannot ``CREATE EXTENSION``).
  * ``leave_types.accrues_schedule_gap`` — marks the type whose entitlement is the gap between
    scheduled and contract hours, rather than a ``default_weeks`` multiple.
  * a seeded ``roostervrij`` type per org (paid, balance-tracked, auto-approve, carry-over 0).

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive / expand-only.** A new table, one new column, one new seeded row per org. No
  existing column changes meaning; ``generate_entitlements`` falls back to *scheduled* hours and
  the legacy staff set when an employee has no contract, so **no balance moves on upgrade** and
  no contract backfill is needed. Applies on top of any released ``head``.
* ``accrues_schedule_gap`` lands NOT NULL with a ``false`` server default, so populated
  ``leave_types`` rows get ``false`` without a rewrite.
* **Rollback-safe.** The previous image never selects ``employment_contracts``,
  ``accrues_schedule_gap`` or the ``roostervrij`` type (an unknown type it simply lists).
* Idempotent, per-org, ``org_id``-scoped seed under the bound RLS GUC.

Revision ID: c812f69d84d6
Revises: 6bed32e4e87e
Create Date: 2026-07-11 11:00:00.000000
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'c812f69d84d6'
down_revision: str | None = '6bed32e4e87e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the roostervrij default (app.modules.leave.service.DEFAULT_LEAVE_TYPES). A
# migration never imports app-level constants (docs/WORKFLOW.md).
_ROOSTERVRIJ = {
    "key": "roostervrij",
    "labels": {"nl": "Roostervrije tijd (ADV)", "en": "Rostered days off (ADV)"},
    "color": "cyan",
    "paid": True,
    "tracks_balance": True,
    "requires_approval": False,
    "accrues_schedule_gap": True,
    "carry_over_months": 0,
    "position": 25,
}


def upgrade() -> None:
    op.add_column(
        'leave_types',
        sa.Column(
            'accrues_schedule_gap',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        'employment_contracts',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('contract_hours_per_week', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('schedule', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_employment_contracts_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_employment_contracts_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_employment_contracts')),
    )
    op.create_index(op.f('ix_employment_contracts_org_id'), 'employment_contracts', ['org_id'], unique=False)
    op.create_index(op.f('ix_employment_contracts_user_id'), 'employment_contracts', ['user_id'], unique=False)
    op.create_index(op.f('ix_employment_contracts_start_date'), 'employment_contracts', ['start_date'], unique=False)

    enable_rls('employment_contracts')

    # Seed the roostervrij type for existing orgs (RLS is FORCED, so bind the GUC per org). Skipped
    # for an org that already renamed/created one on this key — ON CONFLICT keeps a second run a
    # no-op. It grants nobody anything until a contract with a scheduling gap exists.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO leave_types
                    (id, org_id, key, label_i18n, color, paid, tracks_balance,
                     requires_approval, accrues_schedule_gap, default_weeks, carry_over_months,
                     position, active)
                VALUES
                    (:id, :org_id, :key, CAST(:labels AS jsonb), :color, :paid, :tracks,
                     :approval, :gap, NULL, :carry, :position, true)
                ON CONFLICT (org_id, key) DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "org_id": str(org_id),
                "key": _ROOSTERVRIJ["key"],
                "labels": json.dumps(_ROOSTERVRIJ["labels"]),
                "color": _ROOSTERVRIJ["color"],
                "paid": _ROOSTERVRIJ["paid"],
                "tracks": _ROOSTERVRIJ["tracks_balance"],
                "approval": _ROOSTERVRIJ["requires_approval"],
                "gap": _ROOSTERVRIJ["accrues_schedule_gap"],
                "carry": _ROOSTERVRIJ["carry_over_months"],
                "position": _ROOSTERVRIJ["position"],
            },
        )

    # The server default was only needed to backfill existing rows; the model has its own default.
    op.alter_column('leave_types', 'accrues_schedule_gap', server_default=None)


def downgrade() -> None:
    disable_rls('employment_contracts')
    op.drop_index(op.f('ix_employment_contracts_start_date'), table_name='employment_contracts')
    op.drop_index(op.f('ix_employment_contracts_user_id'), table_name='employment_contracts')
    op.drop_index(op.f('ix_employment_contracts_org_id'), table_name='employment_contracts')
    op.drop_table('employment_contracts')
    # Leave seeded roostervrij rows in place on downgrade: they are ordinary tenant data and the
    # previous image lists them harmlessly. Only the column the old code cannot read is removed.
    op.drop_column('leave_types', 'accrues_schedule_gap')
