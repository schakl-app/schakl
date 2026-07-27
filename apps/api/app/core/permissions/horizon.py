"""Company-horizon floor for the external ``client`` role (issue #252).

The horizon seam (#191, ``app/core/scope.py``) treats "no source restricts this membership"
as *unrestricted* — right for staff, inverted for the one role that exists to let an outside
person log in: a directly-invited ``client``-role member with no contact link and no group
assignment saw the agency's entire company roster, count and all.

This resolver closes that default, deny-by-default like the rest of the RBAC posture (§15):
holding the seeded ``client`` system role restricts the membership to the **empty set**, and
the seam's union semantics then widen it to exactly what the other sources grant — a portal
contact's linked companies (#193), a company-group assignment (#191). Staff roles are never
touched (the resolver answers ``None``), and a wildcard owner never reaches resolution at
all. Deliberate consequence: a membership holding ``client`` *alongside* a staff role is
still floored — the client role marks a login as external; mixing it with staff roles is a
configuration error, not a wider grant.

That last sentence is now load-bearing beyond the horizon: since #274 ``RequestContext``
resolves ``is_portal`` from the same fact (client role **or** contact link), so every
"what a client may see" narrowing built on top of a contact link — the address book, the
client-visible task filter — covers a directly-invited client too. The floor alone did not:
``contacts`` carries no ``company_id``, so the repository's generic horizon filter never
touched it and the roster leak #252 closed for companies stayed open for their people.

The two answers are resolved separately (this resolver here, a ``bool_or`` on the membership
statement in ``require_context``) because the seam takes no arguments beyond the membership;
they must agree, and the ``client``-role key is the single fact both read.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions.catalog import ROLE_CLIENT
from app.core.permissions.models import MembershipRole, Role

_EMPTY: frozenset[uuid.UUID] = frozenset()


async def resolve_client_role_floor(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> frozenset[uuid.UUID] | None:
    holds_client = await session.scalar(
        select(
            exists().where(
                MembershipRole.membership_id == membership_id,
                MembershipRole.org_id == org_id,
                Role.id == MembershipRole.role_id,
                Role.key == ROLE_CLIENT,
            )
        )
    )
    return _EMPTY if holds_client else None
