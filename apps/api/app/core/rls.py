"""Row-Level Security helpers for Alembic migrations (CLAUDE.md §5, Golden Rule 1).

Every org-scoped table gets the same policy: a row is visible/writable only when its ``org_id``
equals the request's bound tenant (``app.current_org`` GUC, set per transaction in ``db.py``).

Notes:
  * ``FORCE ROW LEVEL SECURITY`` makes the policy apply to the *table owner* too (the app role),
    so a forgotten ``org_id`` filter still can't leak across tenants — true defence-in-depth.
  * ``NULLIF(current_setting(..., true), '')`` makes an unset/empty GUC evaluate to NULL, so the
    comparison is false and the query returns no rows (fail closed).
"""

from __future__ import annotations

from alembic import op

POLICY_NAME = "tenant_isolation"
GUC = "app.current_org"


def enable_rls(table: str, org_column: str = "org_id") -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {POLICY_NAME} ON {table}
        USING ({org_column} = NULLIF(current_setting('{GUC}', true), '')::uuid)
        WITH CHECK ({org_column} = NULLIF(current_setting('{GUC}', true), '')::uuid)
        """
    )


def disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
