"""What a selection of domains can be changed to in one go (CLAUDE.md §17's pattern).

The register is where bulk editing earns its keep: an agency moves forty names to a new
registrar, points a client's whole portfolio at Cloudflare, or finally decides which of them
they actually invoice. All four of those are one shared value over a long list.

``invoiceable`` keeps its three states here (#298), and that is why the field is clearable:
``true``/``false`` are somebody's decision, and **clearing it means "follow the register"** —
which is a real answer, not an empty one.

``next_invoice_date`` is the second, and it is the one place this descriptor deliberately
disagrees with the import it borrows from. Both surfaces write the same field through the same
service call; they differ on what an **empty** one means. In a file, a blank column is what an
export that somebody edited two cells of comes back as, and letting it reschedule a thousand
renewal invoices is not a thing a blank should be able to say — so the import leaves it alone.
In this dialog the field is filled in over a selection the user is looking at, one they had to
tick row by row, and "put these back on the date they should have" is exactly the repair a bulk
edit is for: hence :class:`BulkField`'s ``clearable`` override, which is what that flag exists
for (its docstring makes the same argument in the other direction, for a contact's client).

The name is not editable, in bulk or otherwise: it *is* the record.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.modules.domains.impex import DOMAIN_IMPEX
from app.modules.domains.models import Domain
from app.modules.domains.service import DomainService


async def _delete(ctx: RequestContext, domain: Any) -> None:
    """Through the service — and worth knowing: this cascades the domain's website row."""
    await DomainService(ctx).delete(domain.id)


DOMAIN_BULK = BulkDescriptor(
    impex=DOMAIN_IMPEX,
    model=Domain,
    editable=(
        BulkField("status"),
        BulkField("company"),
        BulkField("registrar_provider"),
        BulkField("dns_provider"),
        BulkField("email_provider"),
        BulkField("invoiceable"),
        BulkField("next_invoice_date", clearable=True),
        # A portfolio migrated from another system was invoiced there up to one date — one
        # statement over the whole selection, and clearable, since withdrawing it is a decision
        # somebody makes on purpose over rows they picked.
        BulkField("billed_until", clearable=True),
        # The two remaining invoicing facts. A portfolio onboarded in one afternoon carries
        # that afternoon as every start date (CLAUDE.md §10), and the fix is one date over the
        # selection; it anchors the cycle, so it is settable and never emptiable — the import
        # says the same. Set beside a *cleared* renewal date, the service works the new cycle
        # out from the new anchor in the same call.
        BulkField("start_date"),
        # One negotiated price over a client's names, or — cleared — back to the TLD list
        # price, which is a real answer rather than an empty one.
        BulkField("price_override", clearable=True),
    ),
    delete_permission="domains.domain.delete",
    delete_row=_delete,
)
