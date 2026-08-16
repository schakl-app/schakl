"""The seam other modules resolve a website's WordPress credential through (§6).

``marketing`` needs one WordPress credential to sync a client's Rank Math AI Visibility, and
§6 forbids it importing ``app.integrations.wordpress`` internals. The house answer to that is a
registered resolver, not a bare table read: ``app/core/registrar/presence.py`` does it for "who
holds this registration", ``app/core/directory.py`` for "which contacts may this caller name",
and this is the same shape for "what credential reaches this website".

The reason it is a *seam* rather than a `SELECT` the borrower could write itself is the reason
`directory.py` gives, one table over: the rules that make the read correct — the org scope, the
active flag, and the company horizon reached through website → domain — live on the owning
model, and a borrower that reimplements the join reimplements it *slightly* differently. Here
the value being read is a WordPress administrator password, so "slightly" is not a word this
codebase should be using.

The resolver is registered by the ``wordpress`` module at import time. With that module
disabled nothing is registered and :func:`resolve_credential` answers ``None``, which every
caller must already handle — it is the same answer as "this website has no credential yet".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class WordPressCredential:
    """One website's WordPress access, decrypted, as a borrower needs it.

    Deliberately not the ORM row. A borrower gets what it needs to *make a call* and nothing
    that would let it write the credential, judge its health, or grow a second opinion about
    what the capability map means.
    """

    site_id: uuid.UUID
    website_id: uuid.UUID
    base_url: str
    username: str
    app_password: str
    #: Observed at the last probe — see ``app.integrations.wordpress.client.CAPABILITIES``. A
    #: borrower reads this to *teach* ("Rank Math is not installed on this site") rather than
    #: to gate: the call it is about to make is the better evidence, and a stale probe must
    #: never be the reason a working sync does not run.
    capabilities: dict[str, bool]
    rankmath_ai_visibility: bool = False


class WordPressCredentialResolver(Protocol):
    async def __call__(
        self, session: AsyncSession, org_id: uuid.UUID, website_id: uuid.UUID
    ) -> WordPressCredential | None: ...


class WordPressClientFactory(Protocol):
    """Builds the HTTP client that speaks to a site. Returned duck-typed on purpose.

    The factory exists so a borrower never imports ``app.integrations.wordpress.client`` (§6). It
    is the *transport*, not a data path, but the rule does not carve that out and should not:
    the day the client grows a retry policy or a per-site TLS quirk, the module that owns the
    credential is the one that should decide it — not five call sites that happened to import
    the class.
    """

    def __call__(self, credential: WordPressCredential) -> Any: ...


_resolver: WordPressCredentialResolver | None = None
_client_factory: WordPressClientFactory | None = None


def register_wordpress_resolver(
    resolver: WordPressCredentialResolver, client_factory: WordPressClientFactory
) -> None:
    """Called once by the ``wordpress`` module at import time."""
    global _resolver, _client_factory
    _resolver = resolver
    _client_factory = client_factory


def open_client(credential: WordPressCredential) -> Any:
    """A client for ``credential``, built by whoever owns the credential.

    Raises if the module is disabled, which cannot happen in practice: the only way to hold a
    :class:`WordPressCredential` is to have been handed one by the resolver that the same
    registration installs.
    """
    if _client_factory is None:  # pragma: no cover - unreachable via resolve_credential
        raise RuntimeError("wordpress module is not enabled")
    return _client_factory(credential)


async def resolve_credential(
    session: AsyncSession, org_id: uuid.UUID, website_id: uuid.UUID
) -> WordPressCredential | None:
    """The active WordPress credential for ``website_id``, or ``None``.

    ``None`` covers three situations a caller treats identically — the module is disabled, the
    website has no credential, or the one it has was deactivated — because all three mean the
    same thing at the call site: there is nothing to authenticate with. What differs is what
    the *screen* says, and that is the panel's job, not a syncing job's.
    """
    if _resolver is None:
        return None
    return await _resolver(session, org_id, website_id)
