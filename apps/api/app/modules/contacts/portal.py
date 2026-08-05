"""What contacts contributes to the client portal (issue #193) — and only that.

The portal *itself* is its own module now (``app/modules/portal/``): inviting a client,
disabling the login, signing in as them. What stays here is the half that is genuinely
contacts' own knowledge, published through the three core seams:

* **The horizon** (#191's third axis): a contact-linked membership sees exactly the companies
  the contact is linked to via ``company_contacts`` — live, so linking/unlinking widens or
  narrows the portal the same moment, and **never** ``None``: a portal login is never
  unrestricted.
* **Who is a portal login** (``app/core/portal.py``), so notification fan-out can keep staff
  events out of client inboxes without importing this module.
* **The subject provider**, the read/write handle the portal module works through:
  ``contacts.user_id`` is the link that makes a membership a portal membership, and this file
  stays the only place that knows it.

The first two deliberately live *here* rather than in the portal module, and it is not a
leftover: they must answer even when the portal module is disabled or unlicensed. A client
whose login already exists must stay scoped to their own companies whatever the instance's
licence says — an entitlement governs whether you may invite someone new, never whether an
existing session is contained.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Membership
from app.core.portal import PortalSubject
from app.core.tenancy import RequestContext
from app.modules.contacts.models import CompanyContact, Contact


# --------------------------------------------------------------------------- #
# Horizon resolver (#191 seam): a portal membership sees its contact's companies
# --------------------------------------------------------------------------- #
async def resolve_portal_company_scope(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> frozenset[uuid.UUID] | None:
    rows = (
        await session.execute(
            select(CompanyContact.company_id)
            .select_from(Membership)
            .join(
                Contact,
                (Contact.user_id == Membership.user_id) & (Contact.org_id == org_id),
            )
            .outerjoin(
                CompanyContact,
                (CompanyContact.contact_id == Contact.id)
                & (CompanyContact.org_id == org_id),
            )
            .where(Membership.id == membership_id, Membership.org_id == org_id)
        )
    ).all()
    if not rows:
        # Not a contact-linked membership — this source doesn't restrict them.
        return None
    # Linked but attached to no company = an empty portal, not an unrestricted one.
    return frozenset(company_id for (company_id,) in rows if company_id is not None)


async def resolve_portal_users(
    session: AsyncSession, org_id: uuid.UUID, candidates: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``candidates`` are contact-linked (portal) logins — the core seam's answerer
    (``app/core/portal.py``), used to keep staff notifications out of client inboxes."""
    rows = await session.execute(
        select(Contact.user_id).where(
            Contact.org_id == org_id, Contact.user_id.in_(candidates)
        )
    )
    return set(rows.scalars())


# --------------------------------------------------------------------------- #
# Subject provider (``app/core/portal.py`` seam): a contact can carry a login
# --------------------------------------------------------------------------- #
class ContactPortalSubjectProvider:
    """Contacts as portal subjects. Registered once by the module's package ``__init__``."""

    entity_type = "contact"

    @staticmethod
    def _subject(contact: Contact) -> PortalSubject:
        display_name = f"{contact.first_name} {contact.last_name or ''}".strip()
        return PortalSubject(
            entity_type="contact",
            id=contact.id,
            email=(contact.email or "").strip().lower() or None,
            display_name=display_name or None,
            user_id=contact.user_id,
        )

    async def load(
        self, ctx: RequestContext, subject_id: uuid.UUID
    ) -> PortalSubject | None:
        """Through the tenant repository, so the company horizon applies: a membership scoped
        to one company group can only ever invite the contacts of its own clients."""
        contact = await ctx.repo(Contact).get(subject_id)
        return self._subject(contact) if contact is not None else None

    async def for_user(
        self, ctx: RequestContext, user_id: uuid.UUID
    ) -> PortalSubject | None:
        # Deliberately not through the repository — see the protocol's docstring: the one
        # caller is a portal session ending its own impersonation, and that row *is* the
        # caller, who may have an empty horizon and so would not find itself.
        contact = await ctx.session.scalar(
            select(Contact).where(
                Contact.org_id == ctx.org.id, Contact.user_id == user_id
            )
        )
        return self._subject(contact) if contact is not None else None

    async def attach(
        self, ctx: RequestContext, subject_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        contact = await ctx.repo(Contact).get_or_404(subject_id)
        contact.user_id = user_id
        await ctx.session.flush()
