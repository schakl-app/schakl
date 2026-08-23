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
from dataclasses import dataclass, field
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


#: The prerequisites a Rank Math AI Visibility read needs, in the order somebody completes
#: them. Stable keys: the API never turns one into a sentence (§8), the web does.
#:
#: They exist because ``configured=False`` was one boolean over six different jobs for three
#: different people — "no credential yet" and "the credential was refused" were the same state,
#: and the picker drew the first sentence over both (#435). A stage names *which* prerequisite
#: is the first unmet one, which is the only thing that makes a next step drawable.
STAGE_NO_CREDENTIAL = "no_credential"
STAGE_UNREACHABLE = "unreachable"
STAGE_SITE_ERROR = "site_error"
STAGE_CREDENTIAL_REFUSED = "credential_refused"
STAGE_NOT_ADMINISTRATOR = "not_administrator"
STAGE_RANKMATH_MISSING = "rankmath_missing"
STAGE_RANKMATH_TOO_OLD = "rankmath_too_old"
STAGE_AI_VISIBILITY_UNAVAILABLE = "ai_visibility_unavailable"
STAGE_NO_BRANDS = "no_brands"
STAGE_READY = "ready"

#: Stages at which a credential exists **and reaches AI Visibility**. Everything else is a
#: prerequisite somebody still has to go and complete, which is what a borrower reports as
#: ``configured=False``. ``no_brands`` is deliberately on this side of the line: the plumbing is
#: finished and the client simply has no brand yet, which is a different sentence and a
#: different screen from a credential that does not work.
CONFIGURED_STAGES = frozenset({STAGE_NO_BRANDS, STAGE_READY})


@dataclass(frozen=True)
class WordPressSetupState:
    """Why a WordPress-backed read has nothing to offer, and where it is fixed.

    Deliberately not an exception and not a bare i18n key. A borrower needs three things to
    draw a next step — which prerequisite is unmet, what the site itself said about it, and the
    URL of the screen in the client's own ``wp-admin`` that cures it — and only the module that
    owns the credential knows the site's base URL to build the last one from.

    ``detail`` is the site's **own words**, carried as a quote and never translated: it is what
    an admin will match against their own log line, and the panel on the website page already
    renders refusals the same way for the same reason.
    """

    stage: str
    #: WordPress's own error text, already truncated by the owning module. A quote, never i18n.
    detail: str | None = None
    #: Deep links into the client's ``wp-admin`` — ``app_passwords``, ``plugins``,
    #: ``ai_visibility``. Absent where we do not know the site's address (no credential row at
    #: all), because a link built out of a guess is a control that always refuses (#253).
    links: dict[str, str] = field(default_factory=dict)
    #: What the last probe observed, so a checklist can tick a step with what proved it.
    rankmath_version: str | None = None


class WordPressDiagnosisProvider(Protocol):
    """Answers "why did that read come back empty", for the module that owns the credential.

    Registered beside the resolver, for the reason §6 gives and one this seam learned the hard
    way: ``marketing`` cannot import :class:`WordPressError`, so its only alternative was to
    duck-type the exception's attributes across the boundary — which is exactly how
    ``_org_key_error`` came to read ``exc.response.status_code`` off an object that has neither
    (#435). Classification belongs to whoever speaks that vocabulary.
    """

    async def __call__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        website_id: uuid.UUID,
        *,
        exc: Exception | None = None,
        brand_count: int | None = None,
    ) -> WordPressSetupState: ...


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
_diagnosis: WordPressDiagnosisProvider | None = None


def register_wordpress_resolver(
    resolver: WordPressCredentialResolver,
    client_factory: WordPressClientFactory,
    diagnosis: WordPressDiagnosisProvider | None = None,
) -> None:
    """Called once by the ``wordpress`` module at import time.

    ``diagnosis`` is optional only so that this stays one registration rather than two that can
    be half-done; the module always passes one, and :func:`describe_setup` degrades to the one
    state it can be sure of without it.
    """
    global _resolver, _client_factory, _diagnosis
    _resolver = resolver
    _client_factory = client_factory
    _diagnosis = diagnosis


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


async def describe_setup(
    session: AsyncSession,
    org_id: uuid.UUID,
    website_id: uuid.UUID,
    *,
    exc: Exception | None = None,
    brand_count: int | None = None,
) -> WordPressSetupState:
    """Which prerequisite is the first unmet one for ``website_id``, and where it is fixed.

    ``exc`` is the failure a live call just raised, if any; ``brand_count`` is what a call that
    *succeeded* came back with. Both are the better evidence and both outrank the stored probe,
    which is only ever consulted to break a tie a bare 4xx cannot — the Cloudflare rule
    (``docs/CLOUDFLARE.md``): a read that succeeds outranks a probe that refuses, and a stale
    probe must never be the reason a working picker refuses to list.

    With the module disabled there is no credential and no site address to build a link out of,
    which is the same answer as "this website has nothing connected".
    """
    if _diagnosis is None:
        return WordPressSetupState(stage=STAGE_NO_CREDENTIAL)
    return await _diagnosis(
        session, org_id, website_id, exc=exc, brand_count=brand_count
    )

