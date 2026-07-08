"""leave_create_tables

Employee PTO (CLAUDE.md §14): tenant-configurable leave types, per-employee contract hours,
yearly entitlements, and requests with an approval flow. Seeds the Dutch default types per
existing org (wettelijk / bovenwettelijk / ziek / bijzonder / onbetaald — tenant-editable
config, not hardcoded law) and enables the module in ``org_settings.enabled_modules``.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-09 09:00:00.000000
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
revision: str = 'c8d9e0f1a2b3'
down_revision: str | None = 'b7c8d9e0f1a2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("leave_requests", "leave_entitlements", "leave_profiles", "leave_types")

# Mirrors app.modules.leave.service.DEFAULT_LEAVE_TYPES (frozen here: migrations must not
# drift when the app-level defaults evolve).
_DEFAULT_TYPES = [
    ("vacation_statutory", {"nl": "Wettelijke vakantie", "en": "Statutory vacation"},
     "emerald", True, True, True, "4", 6, 10),
    ("vacation_extra", {"nl": "Bovenwettelijke vakantie", "en": "Extra vacation"},
     "teal", True, True, True, "1", 60, 20),
    ("sick", {"nl": "Ziek", "en": "Sick"}, "orange", True, False, False, None, None, 30),
    ("special", {"nl": "Bijzonder verlof", "en": "Special leave"},
     "violet", True, False, True, None, None, 40),
    ("unpaid", {"nl": "Onbetaald verlof", "en": "Unpaid leave"},
     "sky", False, False, True, None, None, 50),
]


def upgrade() -> None:
    op.create_table('leave_types',
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('label_i18n', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('color', sa.String(length=20), nullable=False),
    sa.Column('paid', sa.Boolean(), nullable=False),
    sa.Column('tracks_balance', sa.Boolean(), nullable=False),
    sa.Column('requires_approval', sa.Boolean(), nullable=False),
    sa.Column('default_weeks', sa.Numeric(precision=4, scale=2), nullable=True),
    sa.Column('carry_over_months', sa.Integer(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_leave_types_org_id_orgs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_types')),
    sa.UniqueConstraint('org_id', 'key', name=op.f('uq_leave_types_org_id'))
    )
    op.create_index(op.f('ix_leave_types_org_id'), 'leave_types', ['org_id'], unique=False)

    op.create_table('leave_profiles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('hours_per_week', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_leave_profiles_org_id_orgs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_leave_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_profiles')),
    sa.UniqueConstraint('org_id', 'user_id', name=op.f('uq_leave_profiles_org_id'))
    )
    op.create_index(op.f('ix_leave_profiles_org_id'), 'leave_profiles', ['org_id'], unique=False)
    op.create_index(op.f('ix_leave_profiles_user_id'), 'leave_profiles', ['user_id'], unique=False)

    op.create_table('leave_entitlements',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('leave_type_id', sa.UUID(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['leave_type_id'], ['leave_types.id'], name=op.f('fk_leave_entitlements_leave_type_id_leave_types'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_leave_entitlements_org_id_orgs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_leave_entitlements_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_entitlements')),
    sa.UniqueConstraint('org_id', 'user_id', 'leave_type_id', 'year', name=op.f('uq_leave_entitlements_org_id'))
    )
    op.create_index(op.f('ix_leave_entitlements_leave_type_id'), 'leave_entitlements', ['leave_type_id'], unique=False)
    op.create_index(op.f('ix_leave_entitlements_org_id'), 'leave_entitlements', ['org_id'], unique=False)
    op.create_index(op.f('ix_leave_entitlements_user_id'), 'leave_entitlements', ['user_id'], unique=False)
    op.create_index(op.f('ix_leave_entitlements_year'), 'leave_entitlements', ['year'], unique=False)

    op.create_table('leave_requests',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('leave_type_id', sa.UUID(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('decided_by_user_id', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.id'], name=op.f('fk_leave_requests_decided_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['leave_type_id'], ['leave_types.id'], name=op.f('fk_leave_requests_leave_type_id_leave_types'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_leave_requests_org_id_orgs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_leave_requests_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_requests'))
    )
    op.create_index(op.f('ix_leave_requests_end_date'), 'leave_requests', ['end_date'], unique=False)
    op.create_index(op.f('ix_leave_requests_leave_type_id'), 'leave_requests', ['leave_type_id'], unique=False)
    op.create_index(op.f('ix_leave_requests_org_id'), 'leave_requests', ['org_id'], unique=False)
    op.create_index(op.f('ix_leave_requests_start_date'), 'leave_requests', ['start_date'], unique=False)
    op.create_index(op.f('ix_leave_requests_status'), 'leave_requests', ['status'], unique=False)
    op.create_index(op.f('ix_leave_requests_user_id'), 'leave_requests', ['user_id'], unique=False)

    # Tenant isolation (defence-in-depth): all leave tables are org-scoped, RLS-forced.
    for table in _TABLES:
        enable_rls(table)

    # Seed the Dutch default types + enable the module for existing orgs (RLS is FORCED,
    # so bind the GUC per org — same pattern as 9d0e1f2a3b4c).
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        for key, labels, color, paid, tracks, approval, weeks, carry, position in _DEFAULT_TYPES:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO leave_types
                        (id, org_id, key, label_i18n, color, paid, tracks_balance,
                         requires_approval, default_weeks, carry_over_months, position, active)
                    VALUES
                        (:id, :org_id, :key, CAST(:labels AS jsonb), :color, :paid, :tracks,
                         :approval, :weeks, :carry, :position, true)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org_id": str(org_id),
                    "key": key,
                    "labels": json.dumps(labels),
                    "color": color,
                    "paid": paid,
                    "tracks": tracks,
                    "approval": approval,
                    "weeks": weeks,
                    "carry": carry,
                    "position": position,
                },
            )
        bind.execute(
            sa.text(
                """
                UPDATE org_settings
                SET enabled_modules = enabled_modules || '{leave}'
                WHERE org_id = :org_id AND NOT ('leave' = ANY(enabled_modules))
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    for table in _TABLES:
        disable_rls(table)

    op.drop_index(op.f('ix_leave_requests_user_id'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_status'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_start_date'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_org_id'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_leave_type_id'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_end_date'), table_name='leave_requests')
    op.drop_table('leave_requests')
    op.drop_index(op.f('ix_leave_entitlements_year'), table_name='leave_entitlements')
    op.drop_index(op.f('ix_leave_entitlements_user_id'), table_name='leave_entitlements')
    op.drop_index(op.f('ix_leave_entitlements_org_id'), table_name='leave_entitlements')
    op.drop_index(op.f('ix_leave_entitlements_leave_type_id'), table_name='leave_entitlements')
    op.drop_table('leave_entitlements')
    op.drop_index(op.f('ix_leave_profiles_user_id'), table_name='leave_profiles')
    op.drop_index(op.f('ix_leave_profiles_org_id'), table_name='leave_profiles')
    op.drop_table('leave_profiles')
    op.drop_index(op.f('ix_leave_types_org_id'), table_name='leave_types')
    op.drop_table('leave_types')
