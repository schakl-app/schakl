"""Acting on a selection of monitors (CLAUDE.md §18, docs/UPTIME.md).

Two capabilities, and what is **absent** is the point.

``editable`` carries only the *link* fields — which client a monitor belongs to, which website
it watches on our side. Those touch nothing at Uptime Kuma, which is what makes them safe to
apply to forty rows in one request; and attaching a freshly-adopted instance's monitors to their
clients is exactly the chore a selection exists for.

Everything that would **push** is deliberately not here. ``interval_seconds``, ``target``,
``name`` and ``profile_id`` each mean an outbound Socket.IO round-trip per row, so a forty-row
edit would hold one request open for a minute and spend the instance's login budget doing it
(§3). ``target`` would be wrong for a second reason as well, the one ``Domain.name`` is excluded
for: a shared value that retargets forty monitors at one host is a way to lose monitoring
silently, with every row valid.

Delete is local by default, exactly as the single-row delete is: "stop tracking these here" and
"stop watching these clients' sites" are different decisions, and a *bulk* version of the
destructive one is the last place to blur them.
"""

from __future__ import annotations

from app.core.bulk.spec import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.integrations.uptime.impex import UPTIME_MONITOR_IMPEX
from app.integrations.uptime.models import UptimeMonitor


async def _delete_row(ctx: RequestContext, row: UptimeMonitor) -> None:
    """Local delete only — never ``at_kuma``.

    Core's bulk contract has no way to carry "and also delete it at the far end", and that is
    the right answer rather than a limitation to work around: a selection is not the place to
    take an irreversible action on a client's live monitoring.
    """
    from app.integrations.uptime.service import UptimeWriteService

    await UptimeWriteService(ctx).delete_monitor(row.id, at_kuma=False)


UPTIME_MONITOR_BULK = BulkDescriptor(
    model=UptimeMonitor,
    # Borrows the export shape for its column vocabulary and its resolver, so a selection edit
    # and a spreadsheet mean the same thing by the same `company` cell (§18).
    impex=UPTIME_MONITOR_IMPEX,
    editable=(BulkField("company"),),
    write_permission_override="uptime.monitor.write",
    delete_permission="uptime.monitor.write",
    delete_row=_delete_row,
)
