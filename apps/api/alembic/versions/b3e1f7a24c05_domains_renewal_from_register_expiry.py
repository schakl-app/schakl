"""domains_renewal_from_register_expiry

Point existing domains' renewal date at the expiry a connected register actually observed.

``domains.next_invoice_date`` has always been *derived*: the first yearly anniversary of
``start_date`` still ahead (``3c14443ed1fc``). That is the real expiry exactly when
``start_date`` is the real registration date — and it is not, for every domain onboarded in
bulk, where ``start_date`` was backfilled from ``created_at`` and the whole portfolio ended up
anchored to the afternoon somebody imported it. Those renewals then invoice on the wrong day,
every year, and no amount of re-saving the record fixes it because nothing ever asked the
registrar. Since this release the registers answer (``app/core/registrar/expiry.py``), and a
new or edited domain takes the date from them. This is the same correction applied once to the
rows that already exist.

Four guards, and each of them is why this is safe to run unattended on an upgrade:

* **Only where a register actually spoke.** OXXA's and Cloudflare Registrar's stored rows are
  written by a sync and by nothing else, so an instance with no registrar connected — which is
  most of them — matches no rows at all and this migration is a no-op with a log line.
* **Only forward.** An expiry in the past is a lapsed registration, which is a thing to look at
  and not a date to bill on: taking it would hand the renewal cron a date it fires on
  immediately and draft an invoice for a registration that has run out.
* **Only what was never billed.** A domain with a row in ``invoice_domain_periods`` has had a
  renewal invoiced against a period boundary, and moving the boundary underneath a claim is how
  a period gets billed twice or skipped entirely. Those keep the date they have; the register's
  answer is still shown beside it in the app, and a person decides.
* **Only a real change.** ``IS DISTINCT FROM`` — a domain already on the register's date is not
  rewritten, so re-running this changes nothing and the count it logs is honest.

Irreversible by design: the downgrade cannot know which dates it wrote, and inventing an
anniversary to "restore" would be a second unasked-for reschedule rather than an undo. The
column and its meaning are unchanged, so an older release reads these rows perfectly well.

RLS is FORCED on every table read here, so the work binds the GUC per org
(``9d0e1f2a3b4c``'s mechanism — an unbound backfill reads zero rows and reports success).

Revision ID: b3e1f7a24c05
Revises: d1a7c4be9f30
Create Date: 2026-08-06 10:00:00.000000
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3e1f7a24c05'
down_revision: str | None = 'd1a7c4be9f30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: Statuses that renew and therefore bill; mirrors ``models.BILLABLE_STATUSES`` as literals —
#: a migration must apply on top of any older head and never import the evolving app code.
_BILLABLE = ("active", "redirect", "parked")

#: Per billable domain of one org, the expiry its registers observed — a plain subquery over a
#: **second** alias of ``domains`` rather than a ``LATERAL`` beside the update target, because
#: Postgres does not let the ``FROM`` of an ``UPDATE`` reference the row being updated.
#:
#: The two registers are ``COALESCE``d in the key order
#: :func:`app.core.registrar.expiry.register_expiries` fixes (sorted: cloudflare, then oxxa), so
#: a domain sitting in both resolves here exactly as it does in the app.
#:
#: The match — linked id **or** name — is each module's own ``holds`` clause, restated: a domain
#: record typed since the last sync has no link yet and is still the same registration. Ordered
#: furthest-out first because one name can sit in two accounts mid-transfer, and the
#: registration keeping the domain alive is the one running longest.
_CANDIDATES_SQL = """
    SELECT d2.id AS domain_id,
           COALESCE(
               (SELECT (crd.expires_at)::date
                  FROM cloudflare_registrar_domains crd
                 WHERE crd.org_id = d2.org_id
                   AND crd.at_cloudflare IS TRUE
                   AND crd.expires_at IS NOT NULL
                   AND (crd.domain_id = d2.id OR crd.name = d2.name)
                 ORDER BY crd.expires_at DESC
                 LIMIT 1),
               (SELECT od.expires_on
                  FROM oxxa_domains od
                 WHERE od.org_id = d2.org_id
                   AND od.expires_on IS NOT NULL
                   AND od.registry_status IS DISTINCT FROM 'gone'
                   AND (od.domain_id = d2.id OR od.name = d2.name)
                 ORDER BY od.expires_on DESC
                 LIMIT 1)
           ) AS observed
      FROM domains d2
     WHERE d2.org_id = :org_id
       AND d2.status IN :statuses
"""


def upgrade() -> None:
    bind = op.get_bind()
    # Every org, deliberately unfiltered on status: a suspended tenant's register is as correct
    # as anyone's, and skipping it would leave one instance permanently half-corrected.
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    moved = 0
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        result = bind.execute(
            sa.text(
                f"""
                UPDATE domains d
                   SET next_invoice_date = candidate.observed
                  FROM ({_CANDIDATES_SQL}) AS candidate
                 WHERE d.id = candidate.domain_id
                   AND d.org_id = :org_id
                   AND candidate.observed IS NOT NULL
                   -- CURRENT_DATE is the connection's (UTC); the org's own day could differ by
                   -- one. Immaterial here: the only rows it can move are an expiry landing on
                   -- today itself, and the direction it errs in is "leave it alone".
                   AND candidate.observed > CURRENT_DATE
                   AND d.next_invoice_date IS DISTINCT FROM candidate.observed
                   AND NOT EXISTS (
                       SELECT 1
                         FROM invoice_domain_periods p
                        WHERE p.org_id = d.org_id
                          AND p.domain_id = d.id
                   )
                """
            ).bindparams(sa.bindparam("statuses", expanding=True)),
            {"org_id": str(org_id), "statuses": list(_BILLABLE)},
        )
        moved += result.rowcount or 0
    # Said out loud, because this changes when invoices go out. An instance with no register
    # connected sees "0", which is the whole reassurance it needs.
    logger.info(
        "domains: %s renewal date(s) moved to the registrar's observed expiry across %s org(s)",
        moved,
        len(org_ids),
    )


def downgrade() -> None:
    """Nothing to undo — see the module docstring. Re-deriving the anniversary would be a
    second reschedule wearing an undo's clothes, and would also overwrite dates a person set
    by hand, which this migration never touched."""
