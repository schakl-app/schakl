"""What a selection of contact people can be changed to in one go (CLAUDE.md §17's pattern).

Only the client link, and only in the attaching direction. A contact's own fields — name,
address, phone — are the definition of that person and never shared by a selection; the link
is the one thing a batch of them genuinely has in common ("these six all work at Acme now").

The link is deliberately **not clearable here**, unlike in the import. The import's empty cell
is a column the file did not carry; a dialog's empty picker over rows that disagree with each
other would be read as "leave them alone", and offering "detach the client of these six" from
the same control as "set it" is how the wrong one gets clicked. Unlinking stays where it is
visible: on the contact.
"""

from __future__ import annotations

from typing import Any

from app.core.bulk import BulkDescriptor, BulkField
from app.core.tenancy import RequestContext
from app.modules.contacts.impex import CONTACT_IMPEX
from app.modules.contacts.models import Contact
from app.modules.contacts.service import ContactService


async def _delete(ctx: RequestContext, contact: Any) -> None:
    await ContactService(ctx).delete(contact.id)


CONTACT_BULK = BulkDescriptor(
    impex=CONTACT_IMPEX,
    model=Contact,
    editable=(BulkField("company", clearable=False),),
    delete_permission="contacts.contact.delete",
    delete_row=_delete,
)
