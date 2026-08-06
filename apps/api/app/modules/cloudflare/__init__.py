"""cloudflare module (CLAUDE.md §6, epic #278) — DNS, redirects and Pages per client domain.

Gives ``Domain.status = redirect`` a real mechanism for the first time. Until now that field and
``redirect_url`` were a status/URL pair with nothing behind them: the actual redirect was wired by
an external flow (#96). This module makes it a Cloudflare Redirect Rule schakl owns, can read
back, and can tell you has been changed underneath it.

The credential is a **row, not a setting**. An agency holds its own Cloudflare account and its
clients bring theirs, so a per-org singleton would have been wrong on the first day of use —
which is also why nothing in here ever picks an account for you when there is more than one.

The registrar half of #278 (OXXA: nameserver sync + the write path that pushes
``CloudflareZone.name_servers`` back to the registrar) is split into its own issue: it needs
credentials and real API documentation that do not exist yet, and CLAUDE.md forbids writing an
integration from memory. The seam it plugs into is already here — a connected zone stores the
nameservers Cloudflare assigned, and pushing them is a separate, retryable step.

Importing this package self-registers the module.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Date, cast, or_, select

from app.core.registrar import (
    RegisterExpiry,
    RegisterPresence,
    register_expiry,
    register_presence,
)
from app.modules.cloudflare.models import CloudflareAccount, CloudflareRegistrarDomain
from app.modules.cloudflare.permissions import CLOUDFLARE_PERMISSIONS
from app.modules.cloudflare.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="cloudflare",
    router=router,
    i18n_namespace="cloudflare",
    # Licensed module (issue #137): a paid integration, the same bracket as ``google`` /
    # ``invoicing`` / ``marketing``, never part of the free CRM core (epic #140). Past
    # expiry+grace the mount-time gate turns every mutation 402; the read surface (zone list,
    # DNS view, export, stored status) keeps working, so an expired licence never leaves an
    # agency unable to look up a client's DNS.
    sku="cloudflare",
    permissions=CLOUDFLARE_PERMISSIONS,
)

registry.register(module)


def _authority(org_id: uuid.UUID) -> ColumnElement[bool]:
    """This org holds a Cloudflare account whose **Registrar** list has been read (#298).

    ``registrar_synced_at``, deliberately not ``last_synced_at``: syncing zones every night says
    nothing about who pays for a registration, and a token scoped to DNS cannot read the
    registrar at all. Only the register that answered may narrow what schakl invoices.
    """
    return (
        select(CloudflareAccount.id)
        .where(
            CloudflareAccount.org_id == org_id,
            CloudflareAccount.registrar_synced_at.is_not(None),
        )
        .exists()
    )


def _holds(org_id: uuid.UUID, domain: Any) -> ColumnElement[bool]:
    """Cloudflare Registrar holds this domain's registration — **not** merely its zone.

    ``at_cloudflare`` is the whole point: a client's own registration shows up in the same list,
    and a zone shows up in neither. Matched by the link a sync writes or by name, so a domain
    record typed after the last sync is not silently dropped from billing.
    """
    return (
        select(CloudflareRegistrarDomain.id)
        .where(
            CloudflareRegistrarDomain.org_id == org_id,
            CloudflareRegistrarDomain.at_cloudflare.is_(True),
            or_(
                CloudflareRegistrarDomain.domain_id == domain.id,
                CloudflareRegistrarDomain.name == domain.name,
            ),
        )
        .exists()
    )


def _expires_on(org_id: uuid.UUID, domain: Any) -> ColumnElement[Any]:
    """When Cloudflare Registrar says this registration lapses; NULL where it holds none.

    ``at_cloudflare`` again, and for :func:`_holds`' reason: a client's own registration is in
    the same list, and its expiry is a date the client's registrar will invoice *them* for.

    Cloudflare reports an instant and a renewal date is a calendar day, so this casts — in the
    session's zone, which is UTC. That is a deliberate exception to §8's "take the zone as an
    argument" rather than an oversight of it: the correlated subquery has no org to resolve one
    from, the value is a **default somebody is shown and can overwrite**, and a registry expiry
    that lands within hours of midnight is inside the registrar's own grace window either way.
    Nothing bills off this date without a person or the backfill having accepted it first.
    """
    return (
        select(cast(CloudflareRegistrarDomain.expires_at, Date))
        .where(
            CloudflareRegistrarDomain.org_id == org_id,
            CloudflareRegistrarDomain.at_cloudflare.is_(True),
            CloudflareRegistrarDomain.expires_at.is_not(None),
            or_(
                CloudflareRegistrarDomain.domain_id == domain.id,
                CloudflareRegistrarDomain.name == domain.name,
            ),
        )
        .order_by(CloudflareRegistrarDomain.expires_at.desc())
        .limit(1)
        .scalar_subquery()
    )


# Who holds the registration (#298) and until when — both asked by ``domains``, which may not
# name this module.
register_presence(RegisterPresence(key="cloudflare", authority=_authority, holds=_holds))
register_expiry(RegisterExpiry(key="cloudflare", expires_on=_expires_on))
