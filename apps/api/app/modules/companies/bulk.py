"""What a selection of clients can be changed to in one go (CLAUDE.md §17's pattern).

Only the status. Everything else on a client — its name, its klantnummer, its address, its
invoice address — is a fact about *that* client, and a control that wrote one of them across a
selection would exist purely to be misfired. Archiving the twelve clients an agency stopped
working for last quarter is the operation people actually have.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.modules.companies.impex import COMPANY_IMPEX
from app.modules.companies.models import Company
from app.modules.companies.service import CompanyService


async def _delete(ctx: RequestContext, company: Any) -> None:
    """Through the service, so a bulk delete is exactly the row's own ⋯ → Verwijderen."""
    await CompanyService(ctx).delete(company.id)


COMPANY_BULK = BulkDescriptor(
    impex=COMPANY_IMPEX,
    model=Company,
    editable=(BulkField("status"),),
    delete_permission="companies.company.delete",
    delete_row=_delete,
)
