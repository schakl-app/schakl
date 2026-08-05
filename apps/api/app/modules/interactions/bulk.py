"""What a selection of contact moments can have done to it in one go.

Delete, and nothing else. A contact moment is the record of something that was said — its
subject, its body, who it was with — and there is no field on it that several rows could
sensibly be given the same value for. Reviewing a batch is the other thing worth doing to one,
and that already exists as its own surface (``/bulk/approve|assign|reject``, #299), because
approving is a decision about a queue rather than an edit to a record.

Deleting a run of them is ordinary though: a mis-logged import, a test thread, forty notes from
a client that turned out to be someone else's. The service refuses per row what it always
refuses — a gmail row still in review is not deletable, and someone else's is not yours — and
those come back in ``failed`` rather than stopping the batch.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor
from app.core.tenancy import RequestContext
from app.modules.interactions.models import Interaction
from app.modules.interactions.service import InteractionService


async def _delete(ctx: RequestContext, interaction: Any) -> None:
    """Through the service, so a batch refuses exactly what one row's ⋯ → Verwijderen does."""
    await InteractionService(ctx).delete(interaction.id)


INTERACTION_BULK = BulkDescriptor(
    model=Interaction,
    # No import shape to borrow: interactions have no CSV surface, and a delete needs no column
    # vocabulary — so the entity names itself.
    entity="interaction",
    delete_permission="interactions.interaction.delete",
    delete_row=_delete,
)
