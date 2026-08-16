"""oxxa module (CLAUDE.md §6, issue #296) — the registrar half of epic #278.

Gives a domain record the four facts only a registrar knows — **when it expires**, **whether it
is locked**, **what the registry has delegated** (as opposed to what public DNS answers today)
and **who the registrant is** — and one write: moving that delegation.

That write is what finishes the Cloudflare story. Connecting a domain to Cloudflare produces a
nameserver pair and, until now, moving the domain onto it meant logging into the OXXA portal by
hand. `CloudflareZone.name_servers` has been storing exactly that payload since #278 shipped,
waiting for this.

**Written from OXXA's official API documentation (v1.2); not yet exercised against a live
account.** No sandbox credential exists — see ``docs/OXXA.md`` §1 for exactly what that means
for the parsing here and what to check first when a credential does arrive. CLAUDE.md §11's ban
is on writing an integration *from memory*; this one has the document, and says plainly where
the document is the only evidence.

Importing this package self-registers the module and its registrar provider.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, or_, select

from app.core.registrar import (
    RegisterExpiry,
    RegisterPresence,
    register_expiry,
    register_presence,
    register_registrar,
)
from app.integrations.oxxa.client import OxxaClient
from app.integrations.oxxa.models import OxxaAccount, OxxaDomain
from app.integrations.oxxa.permissions import OXXA_PERMISSIONS
from app.integrations.oxxa.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="oxxa",
    # A conversation with somebody else's service, not a capability of our own.
    kind=KIND_INTEGRATION,
    # Requires `domains`, for `cloudflare`'s reason and more bluntly: the register *is* the
    # domain list. Its one panel is an `entityType: "domain"` panel, and the whole point of the
    # sync is deciding who pays for which name (#298).
    requires=("domains",),
    router=router,
    i18n_namespace="oxxa",
    # Licensed module (issue #137): a paid integration, the same bracket as ``cloudflare`` /
    # ``google`` / ``invoicing``, never part of the free CRM core (epic #140). Past
    # expiry+grace the mount-time gate turns every mutation 402; the read surface (the stored
    # register, expiry dates, the status report) keeps working, so an expired licence never
    # leaves an agency unable to see when a client's domain runs out.
    sku="oxxa",
    permissions=OXXA_PERMISSIONS,
)

registry.register(module)

# The registrar seam (#296 scope item 4). Registering the class rather than an instance: a
# provider is constructed per credential, and there is one credential per account row.
register_registrar(OxxaClient.key, OxxaClient)


def _authority(org_id: uuid.UUID) -> ColumnElement[bool]:
    """This org holds an OXXA credential whose register has actually been read.

    ``last_synced_at``, not merely a row: a credential saved this morning knows nothing about
    who pays for which registration, and until the register has answered once it may not be
    allowed to narrow what gets invoiced (#298).
    """
    return (
        select(OxxaAccount.id)
        .where(OxxaAccount.org_id == org_id, OxxaAccount.last_synced_at.is_not(None))
        .exists()
    )


def _holds(org_id: uuid.UUID, domain: Any) -> ColumnElement[bool]:
    """The OXXA register holds this domain — so the agency is the party renewing it.

    Matched by the link a sync writes **or** by name, because the two orders an agency actually
    works in disagree: sync the register first and a domain record typed afterwards is unlinked
    until the next sync, and "it stopped invoicing because I added it on a Tuesday" is not an
    answer anyone would accept. ``gone`` rows are excluded — a domain transferred away is in the
    table only so the trail of what we pushed survives it, not as evidence we still hold it.
    """
    return (
        select(OxxaDomain.id)
        .where(
            OxxaDomain.org_id == org_id,
            OxxaDomain.registry_status.is_distinct_from("gone"),
            or_(OxxaDomain.domain_id == domain.id, OxxaDomain.name == domain.name),
        )
        .exists()
    )


def _expires_on(org_id: uuid.UUID, domain: Any) -> ColumnElement[Any]:
    """When OXXA says this domain's registration lapses; NULL if it does not hold it.

    Matched exactly as :func:`_holds` matches, and excluding ``gone`` for the same reason: the
    expiry of a domain transferred away is the date it would *have* lapsed here, which is not a
    renewal anyone is going to invoice.

    Ordered furthest-out first because one name can sit in two accounts (a transfer between two
    of the agency's own OXXA logins, mid-move): the registration that is actually keeping the
    domain alive is the one running longest, and taking the nearer date would invoice a renewal
    that has already been paid at the other account.
    """
    return (
        select(OxxaDomain.expires_on)
        .where(
            OxxaDomain.org_id == org_id,
            OxxaDomain.expires_on.is_not(None),
            OxxaDomain.registry_status.is_distinct_from("gone"),
            or_(OxxaDomain.domain_id == domain.id, OxxaDomain.name == domain.name),
        )
        .order_by(OxxaDomain.expires_on.desc())
        .limit(1)
        .scalar_subquery()
    )


# Who holds the registration (#298) and until when — both asked by ``domains``, which may not
# name this module.
register_presence(RegisterPresence(key="oxxa", authority=_authority, holds=_holds))
register_expiry(RegisterExpiry(key="oxxa", expires_on=_expires_on))
