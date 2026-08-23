"""The seam other modules read a client's Tag Manager containers through (§6).

``marketing`` draws one row per *connection* on a client's panel (#411) — Tag Manager being the
one thing an agency attaches to a client that is not a metrics source — and §6 forbids it
importing ``app.integrations.google_tag_manager`` internals. The house answer is a registered
provider, not a bare table read: ``app/core/wordpress.py`` does it for "what credential reaches
this website", ``app/core/registrar/presence.py`` for "who holds this registration", and this is
the same shape for "what is measuring this client's site".

**The permission travels with the provider, not with the borrower.** §15/#365's rule — "each
provider remembers" is not a rule, it is a hope — applies twice over here, because the borrower
is a panel whose own spec is ``explicit_public``. So the registered provider checks
``google_tag_manager.container.read`` itself and answers ``[]`` for a caller who does not hold
it. A second borrower tomorrow inherits that by construction rather than by reading this
docstring.

With the module disabled nothing is registered and :func:`company_containers` answers ``[]``,
which every caller must already handle: it is the same answer as "this client has no container".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a core → tenancy import cycle
    from app.core.tenancy import RequestContext


@dataclass(frozen=True)
class CompanyContainer:
    """One container attached to a client, as a borrower needs to *draw* it.

    Deliberately not the ORM row: a borrower gets what a card prints and nothing that would let
    it write a container, judge its health, or grow a second opinion about what a status means.
    """

    id: uuid.UUID
    #: ``GTM-XXXXXXX`` — the id on the client's own website, and the only one anybody quotes.
    public_id: str
    name: str
    status: str
    #: Google's own sentence, already scrubbed. Untranslated on purpose: it is the one thing
    #: that says *what* to fix, and categorising it would mean inventing categories Google
    #: does not have.
    last_error: str | None
    live_version_id: str | None
    tag_count: int
    #: Staged and never published. The number this whole row exists to carry onto the hub: a
    #: change staged weeks ago and never published is how a client's tracking quietly stops
    #: being what they were told it is, and nobody opens a container they have no reason to.
    workspace_changes: int
    observed_at: datetime | None
    #: Into Tag Manager itself.
    deep_link: str


class CompanyContainerProvider(Protocol):
    async def __call__(
        self, ctx: RequestContext, company_id: uuid.UUID
    ) -> list[CompanyContainer]: ...


_provider: CompanyContainerProvider | None = None


def register_container_provider(provider: CompanyContainerProvider) -> None:
    """Called once by the ``google_tag_manager`` integration at import time."""
    global _provider
    _provider = provider


async def company_containers(
    ctx: RequestContext, company_id: uuid.UUID
) -> list[CompanyContainer]:
    """This client's linked containers, or ``[]``.

    ``[]`` covers three situations a caller treats identically — the integration is disabled,
    the caller may not read containers, or this client has none — because all three mean the
    same thing at the call site: there is no connection row to draw.
    """
    if _provider is None:
        return []
    return await _provider(ctx, company_id)
