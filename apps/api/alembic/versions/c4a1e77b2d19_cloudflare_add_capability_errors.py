"""cloudflare_add_capability_errors

Keep *why* a token probe answered no.

``cloudflare_accounts.capabilities`` records what a token was observed to be allowed to do, as a
bare boolean per capability. A ``False`` is the one shape an admin cannot act on: the settings
screen prints *"DNS van een zone lezen: niet toegekend"* beside a Cloudflare token screen that
plainly grants DNS Read, and there is nothing anywhere — not on the row, not in a log — that says
what Cloudflare actually answered. The only remaining move is to widen a token that was already
wide enough, which is the failure this module has corrected twice before in other clothes.

``capability_errors`` holds Cloudflare's status, numeric code and own text per refused capability
(``client.describe_failure``), written by the same verify that writes ``capabilities`` and keyed
identically. Cloudflare's text is not translatable and never enters an error envelope (§9); it
lives here and is rendered as evidence, exactly as ``last_error`` already is on this table.

Upgrade plan (docs/WORKFLOW.md → *Breaking database changes*):

* **Which released versions upgrade into this?** Any older head. It only adds a column.
* **What happens to existing rows?** They get ``{}`` from the server default — indistinguishable
  from "this verify refused nothing", which is the honest reading of a row probed before the
  column existed. The next verify fills it.
* **Is it reversible?** Yes: ``downgrade`` drops the column and loses only diagnostics that the
  next verify regenerates.
* **Does anything read it before it is written?** The API serialises it (``AccountRead``) and the
  settings screen renders it only where a capability is ``False``; empty renders nothing.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c4a1e77b2d19"
down_revision: str | None = "b3f6c1d80a45"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "cloudflare_accounts",
        sa.Column(
            "capability_errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("cloudflare_accounts", "capability_errors")
