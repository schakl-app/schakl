"""auth_create_org_auth_settings

Per-org OIDC / SSO configuration (issue #76): one RLS-forced row per org replacing the
``SCHAKL_OIDC_*`` env vars — client id, discovery URL, the enabled/enforced toggles, the JIT
provisioning policy, and the client secret **encrypted at rest** (``app.core.crypto``, the same
Fernet scheme as #17's notification channels and e-mail transport).

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Additive.** One new table; no columns touched elsewhere; applies on top of any released
  ``head``.
* **Seeded from the environment, once.** An install that ran OIDC from env must not silently
  lose SSO on upgrade, so this migration reads the ``SCHAKL_OIDC_*`` vars present at upgrade
  time and writes each org's row from them, secret already encrypted. Reading env in a
  migration is sanctioned here (issue #76); importing the *permission catalog* remains
  forbidden — the new ``settings.auth.manage`` permission reaches existing orgs via the
  startup reconciler. After this release the env vars are ignored (and can be removed).
* **Seeding precedes ``enable_rls``** in the same transaction: the migration runs as the
  app role, which the freshly forced policy would otherwise block (no GUC is bound here).
* **Rollback-safe.** The previous image never selects ``org_auth_settings`` and still reads
  its OIDC config from env — which the operator has not removed yet at that point.
* **Reversible.** ``downgrade()`` drops the table; env config takes over again on the old code.

Revision ID: f8cd40024f05
Revises: 7f3a91c04d2b
Create Date: 2026-07-12 10:00:00.000000
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'f8cd40024f05'
down_revision: str | None = '7f3a91c04d2b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _seed_from_env() -> None:
    """Carry a live env-configured OIDC install into the table, idempotently per org.

    No OIDC env at all (the common case, and every fresh install) seeds nothing: rows are
    created lazily on the first save from Instellingen → SSO.
    """
    enabled = _env_bool("SCHAKL_OIDC_ENABLED")
    enforced = _env_bool("SCHAKL_OIDC_ENFORCED")
    discovery_url = os.environ.get("SCHAKL_OIDC_DISCOVERY_URL") or None
    client_id = os.environ.get("SCHAKL_OIDC_CLIENT_ID") or None
    client_secret = os.environ.get("SCHAKL_OIDC_CLIENT_SECRET") or None
    if not (enabled or enforced or discovery_url or client_id or client_secret):
        return

    from app.core.crypto import encrypt  # key derived from SCHAKL_ENCRYPTION_KEY/SECRET_KEY

    configured = bool(discovery_url and client_id and client_secret)
    now = datetime.now(UTC)
    values = {
        "oidc_enabled": enabled or enforced,
        # The old model refused to boot when enforced-but-unconfigured, so a half-configured
        # enforce flag was never live; do not carry one into the DB where it could lock out.
        "oidc_enforced": enforced and configured,
        "oidc_name": os.environ.get("SCHAKL_OIDC_NAME") or "SSO",
        "oidc_discovery_url": discovery_url,
        "oidc_client_id": client_id,
        "oidc_client_secret_encrypted": encrypt(client_secret) if client_secret else None,
        "oidc_default_role": os.environ.get("SCHAKL_OIDC_DEFAULT_ROLE") or "member",
        "oidc_auto_provision_membership": _env_bool(
            "SCHAKL_OIDC_AUTO_PROVISION_MEMBERSHIP", True
        ),
        # This exact config was the live one on this install, which is what the tested marker
        # asserts — without it, every later edit would have to drop "enforce" first.
        "oidc_tested_at": now if configured else None,
    }

    connection = op.get_bind()
    org_ids = connection.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    insert = sa.text(
        """
        INSERT INTO org_auth_settings (
            id, org_id, oidc_enabled, oidc_enforced, oidc_name, oidc_discovery_url,
            oidc_client_id, oidc_client_secret_encrypted, oidc_default_role,
            oidc_auto_provision_membership, oidc_tested_at, created_at, updated_at
        ) VALUES (
            :id, :org_id, :oidc_enabled, :oidc_enforced, :oidc_name, :oidc_discovery_url,
            :oidc_client_id, :oidc_client_secret_encrypted, :oidc_default_role,
            :oidc_auto_provision_membership, :oidc_tested_at, :now, :now
        )
        """
    )
    for org_id in org_ids:
        connection.execute(
            insert, {"id": uuid.uuid4(), "org_id": org_id, "now": now, **values}
        )


def upgrade() -> None:
    op.create_table(
        'org_auth_settings',
        sa.Column('oidc_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('oidc_enforced', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('oidc_name', sa.String(length=64), server_default='SSO', nullable=False),
        sa.Column('oidc_discovery_url', sa.String(length=1024), nullable=True),
        sa.Column('oidc_client_id', sa.String(length=512), nullable=True),
        sa.Column('oidc_client_secret_encrypted', sa.Text(), nullable=True),
        sa.Column('oidc_default_role', sa.String(length=64), server_default='member', nullable=False),
        sa.Column(
            'oidc_auto_provision_membership',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
        sa.Column('oidc_tested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['org_id'],
            ['orgs.id'],
            name=op.f('fk_org_auth_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_org_auth_settings')),
        sa.UniqueConstraint('org_id', name='uq_org_auth_settings_org'),
    )
    op.create_index(
        op.f('ix_org_auth_settings_org_id'), 'org_auth_settings', ['org_id'], unique=False
    )

    _seed_from_env()

    enable_rls('org_auth_settings')


def downgrade() -> None:
    disable_rls('org_auth_settings')
    op.drop_index(op.f('ix_org_auth_settings_org_id'), table_name='org_auth_settings')
    op.drop_table('org_auth_settings')
