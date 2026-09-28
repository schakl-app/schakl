"""Bulk actions on reports (CLAUDE.md §18).

Delete only. A report has no import shape and no field a selection could share: its prose is
per section, and publishing or sending one is a decision about *that* document and *that*
client (``docs/REPORTING.md`` — review is the default), never something to do to forty at once.
What a register does collect is drafts nobody will send: a run generated for the wrong month,
a batch made before the template was right.

The permission is ``reporting.report.write`` because that is the key the single delete
declares — the module has no separate delete key, and a bulk route asking for one the record's
own route does not would be a second answer to "may this person delete a report".

Every row goes through :meth:`ReportService.delete`, so a report already sent is refused
(``errors.reporting.already_sent``) and an internal analysis the caller may not read is simply
not found.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor
from app.core.tenancy import RequestContext
from app.modules.reporting.models import Report
from app.modules.reporting.service import ReportService


async def _delete(ctx: RequestContext, report: Any) -> None:
    await ReportService(ctx).delete(report.id)


REPORT_BULK = BulkDescriptor(
    model=Report,
    entity="report",
    delete_permission="reporting.report.write",
    delete_row=_delete,
)
