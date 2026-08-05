"""What a selection of websites can be changed to in one go (CLAUDE.md §17's pattern).

Hosting account and uptime monitoring — the two facts a *migration* changes at once. Moving
thirty sites to a new server and switching monitoring on for a client's whole estate are the
operations this list exists to make cheap.

The **technical owner is deliberately absent**, though the engine resolves party tokens and
would have handled it. A party control has no "leave this one alone" state — it always holds a
type, defaulting to the agency — so over a selection it would arrive pre-answered, and the one
rule the bulk dialog rests on is that an untouched field is not sent. Inventing a second opt-in
gesture for a single field costs more than the field is worth; the owner stays on the record.

The domain is not editable either: a website has no name of its own, so its domain *is* its
identity (``natural_keys=("domain",)``) — and the client follows from it, which is why the
import exports that column read-only and the engine refuses to bulk-write a derived value.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.modules.websites.impex import WEBSITE_IMPEX
from app.modules.websites.models import Website
from app.modules.websites.service import WebsiteService


async def _delete(ctx: RequestContext, website: Any) -> None:
    await WebsiteService(ctx).delete(website.id)


WEBSITE_BULK = BulkDescriptor(
    impex=WEBSITE_IMPEX,
    model=Website,
    editable=(
        BulkField("hosting"),
        BulkField("uptime_enabled"),
    ),
    delete_permission="websites.website.delete",
    delete_row=_delete,
)
