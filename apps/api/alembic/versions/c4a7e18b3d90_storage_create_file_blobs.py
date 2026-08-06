"""storage_create_file_blobs

De-duplicate stored bytes. A ``files`` row used to own one object of its own, so the signature
logo attached to 500 e-mails was 500 objects on the volume or in the bucket. ``file_blobs``
holds the bytes once per ``(org, backend, sha256)`` and ``files.blob_id`` points at it.

**Per org, never across orgs** (Golden Rule 1): keys stay under ``<org_id>/``, so terminating a
tenant still reclaims exactly its own key space and no tenant's bytes are reachable from
another's.

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Expand only.** Nothing is dropped or renamed and nothing is backfilled here — hashing every
  existing object is unbounded work and must never run inside an unattended upgrade. Existing
  rows keep ``blob_id IS NULL``, keep their own object, and keep the exact read/delete
  behaviour they had. The core ``storage_maintenance`` cron folds them in bounded batches.
* ``files.backend`` / ``files.storage_key`` are still written on every new row, mirrored from
  the blob, so the **previous release still reads every file** after a rollback.
* **Rollback caveat, and it is the one thing this cannot make safe:** the previous release's
  delete path removes the object at ``files.storage_key`` unconditionally. Deleting a *shared*
  file there also deletes it for its siblings. Roll back if you must, but do not delete files
  until you have rolled forward again. Stated in docs/STORAGE.md and the release notes.
* **Reversible.** ``downgrade`` repoints nothing and destroys no bytes: it only drops the
  column and the table. A blob adopted from a pre-dedup file kept that file's original key, and
  a blob created new lives at ``<org_id>/sha256/<hex>`` which the old release never reads — so
  after a downgrade some objects are orphaned, never missing. Orphaned space is recoverable;
  missing bytes are not.

Revision ID: c4a7e18b3d90
Revises: d5e1a93c7f28
Create Date: 2026-08-05 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c4a7e18b3d90'
down_revision: str | None = 'd5e1a93c7f28'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'file_blobs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('backend', sa.String(length=20), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('unreferenced_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'org_id', 'backend', 'sha256', name='uq_file_blobs_org_backend_sha'
        ),
    )
    op.create_index(op.f('ix_file_blobs_org_id'), 'file_blobs', ['org_id'])
    # The sweeper's candidate scan: unreferenced blobs, oldest stamp first. Partial, because
    # the overwhelming majority of blobs are referenced and never belong in this index.
    op.create_index(
        'ix_file_blobs_unreferenced',
        'file_blobs',
        ['org_id', 'unreferenced_since'],
        postgresql_where=sa.text('unreferenced_since IS NOT NULL'),
    )
    enable_rls('file_blobs')

    op.add_column('files', sa.Column('blob_id', sa.UUID(), nullable=True))
    # Deferred, not RESTRICT: purging an org cascades from ``orgs`` into ``files`` and
    # ``file_blobs`` at once, and an immediately-checked constraint would refuse whichever
    # side arrived first. Deferring says both things that matter — any order within a
    # transaction, and no ``files`` row pointing at a missing blob once it commits.
    op.create_foreign_key(
        'fk_files_blob_id',
        'files',
        'file_blobs',
        ['blob_id'],
        ['id'],
        ondelete='NO ACTION',
        deferrable=True,
        initially='DEFERRED',
    )
    # Two readers, two indexes. The sweeper asks "does any file reference this blob?" and the
    # fold job asks "which files in this org are not folded yet?" — the partial index keeps the
    # second one cheap on an instance that has long since finished folding.
    op.create_index(op.f('ix_files_blob_id'), 'files', ['blob_id'])
    op.create_index(
        'ix_files_unfolded',
        'files',
        ['org_id', 'id'],
        postgresql_where=sa.text('blob_id IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_files_unfolded', table_name='files')
    op.drop_index(op.f('ix_files_blob_id'), table_name='files')
    op.drop_constraint('fk_files_blob_id', 'files', type_='foreignkey')
    op.drop_column('files', 'blob_id')
    disable_rls('file_blobs')
    op.drop_index('ix_file_blobs_unreferenced', table_name='file_blobs')
    op.drop_index(op.f('ix_file_blobs_org_id'), table_name='file_blobs')
    op.drop_table('file_blobs')
