"""google.gmail — matched, metadata-first email logging (docs/GOOGLE.md §6).

Importing this package wires the review-flow subscribers onto the bus. The feed writes
through the interactions module's published ``system`` surface; the dependency only ever
points google → interactions.
"""

from __future__ import annotations

from app.core.events import subscribe
from app.modules.google.gmail.events import (
    handle_interaction_approved,
    handle_interaction_rejected,
)

subscribe("interaction.approved", handle_interaction_approved)
subscribe("interaction.rejected", handle_interaction_rejected)
