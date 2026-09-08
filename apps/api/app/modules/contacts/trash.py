"""A contact person is a person, not the client's property (docs/TRASH.md): the *link* goes
with the client (``company_contacts`` cascades), the contact stays and is simply attached to
one client fewer. Named in the dialog so nobody is surprised by an unattached contact."""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

CONTACT_TRASH_DEPENDENTS = (
    TrashDependent(
        key="contacts.links",
        label_key="trash.dependent.contacts.links",
        blocks=False,
        count=count_by_column("company_contacts", "company_id"),
    ),
)
