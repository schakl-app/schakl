"""microsoft.outlook — matched, metadata-first email logging (docs/MICROSOFT.md §6).

Importing this package wires the review-flow subscribers onto the bus and registers this feed's
mailboxes with the shared *who counts as us* composition. The feed writes through the
interactions module's published ``system`` surface; the dependency only ever points
microsoft → interactions.
"""

from __future__ import annotations

from app.core.events import subscribe
from app.core.mailbox.internals import register_mailbox_provider
from app.integrations.microsoft.outlook.events import (
    handle_interaction_approved,
    handle_interaction_rejected,
)
from app.integrations.microsoft.outlook.service import outlook_mailboxes

subscribe("interaction.approved", handle_interaction_approved)
subscribe("interaction.rejected", handle_interaction_rejected)

# Which colleagues' mailboxes this feed polls (``app/core/mailbox/internals.py``): a copy held
# by a Gmail mailbox defers to a colleague's Outlook mailbox exactly as another Gmail one would.
register_mailbox_provider("microsoft.outlook", outlook_mailboxes)
