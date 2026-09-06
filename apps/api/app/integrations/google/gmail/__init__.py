"""google.gmail — matched, metadata-first email logging (docs/GOOGLE.md §6).

Importing this package wires the review-flow subscribers onto the bus. The feed writes
through the interactions module's published ``system`` surface; the dependency only ever
points google → interactions.
"""

from __future__ import annotations

from app.core.events import subscribe
from app.core.mailbox.internals import register_mailbox_provider
from app.integrations.google.gmail.events import (
    handle_interaction_approved,
    handle_interaction_rejected,
)
from app.integrations.google.gmail.service import gmail_mailboxes

subscribe("interaction.approved", handle_interaction_approved)
subscribe("interaction.rejected", handle_interaction_rejected)

# Which colleagues' mailboxes this feed polls, for the shared *who counts as us* composition
# (``app/core/mailbox/internals.py``): a copy held by an Outlook mailbox defers to a colleague's
# Gmail mailbox exactly as another Gmail one would, and the reverse.
register_mailbox_provider("google.gmail", gmail_mailboxes)
