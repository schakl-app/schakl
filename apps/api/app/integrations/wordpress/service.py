"""Business logic for the wordpress module — all DB access tenant-scoped (Golden Rule 1).

Three rules shape every method here.

* **A probe is evidence, never the gate.** A verify that reaches nothing marks *this row* and
  changes nothing else; a verify that reaches something records what, and clears the error it
  set last time. Nothing else in the platform is allowed to become unavailable because one
  client's WordPress was down when we last looked.
* **The reachable call runs inside ``ctx.release_db()``.** A request is one transaction pinning
  one pooled connection, and a cold PHP host can take seconds to answer. Holding the connection
  across it drains the pool and reads as *the whole site* freezing, not as one slow button
  (CLAUDE.md §3, docs/PERFORMANCE.md).
* **The request path reads stored state.** Only an explicit verify dials out. The panel, the
  list and the marketing picker read what was last observed, so a website page renders at the
  same speed whether the client's WordPress is up, down or gone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.activity import ActivityService
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.core.wordpress import (
    STAGE_AI_VISIBILITY_UNAVAILABLE,
    STAGE_CREDENTIAL_REFUSED,
    STAGE_NO_BRANDS,
    STAGE_NO_CREDENTIAL,
    STAGE_NOT_ADMINISTRATOR,
    STAGE_RANKMATH_MISSING,
    STAGE_RANKMATH_TOO_OLD,
    STAGE_READY,
    STAGE_SITE_ERROR,
    STAGE_UNREACHABLE,
    WordPressCredential,
    WordPressSetupState,
)
from app.errors import AppError
from app.integrations.wordpress.client import (
    CAPABILITIES,
    WordPressAuthError,
    WordPressClient,
    WordPressError,
    WordPressUnreachable,
    describe_failure,
    supports_ai_visibility,
)
from app.integrations.wordpress.models import WordPressSite, WordPressStatus
from app.integrations.wordpress.schemas import (
    WordPressBrand,
    WordPressSiteCreate,
    WordPressSiteRead,
    WordPressSiteUpdate,
    WordPressVerifyResult,
    brand_from_payload,
)

#: ``websites`` belongs to another module; referenced as a bare table rather than imported
#: (§6), the same bridge :mod:`app.integrations.wordpress.models` uses for the horizon clause.
_websites = table("websites", column("id"), column("org_id"))

ENTITY_TYPE = "wordpress_site"

#: Definition fields the activity trail tracks (§16). The credential is not among them: the
#: trail records *that* it changed (``password_changed``), never a value — and the observed
#: columns are not edits, so a verify that wrote a trail line per probe would bury the one
#: line somebody is looking for.
_AUDITED_FIELDS = ("base_url", "username", "active")

#: Stable ``wordpress.issue.*`` keys. The web turns these into sentences; the API never does
#: (§8). ``AUTH_HEADER_STRIPPED`` is not a guess we could make from a status code alone — see
#: :func:`_classify` for why it is worth naming at all.
ISSUE_UNREACHABLE = "unreachable"
ISSUE_CREDENTIAL_REFUSED = "credential_refused"
ISSUE_NOT_ADMINISTRATOR = "not_administrator"
ISSUE_NOT_WORDPRESS = "not_wordpress"


def _now() -> datetime:
    return datetime.now(UTC)


def _classify(caps: dict[str, bool], errors: dict[str, str]) -> tuple[str, str | None]:
    """``(status, last_error)`` from what the probe found.

    The ordering is the whole point, and it is the ordering ``docs/CLOUDFLARE.md`` argues for:
    **a read that succeeds outranks a probe that refuses.** A credential that reached the REST
    API is a working credential even if Rank Math answered 404 and the MCP namespace is absent,
    because those describe the *site*, not the password. Only a credential that reached nothing
    at all is called refused — and even then, "we never got an answer" and "the answer was no"
    are separate states, because one of them is not about the credential.
    """
    if caps.get("rest"):
        # Reachable and authenticated. Missing `admin` is a real problem for Rank Math and is
        # worth surfacing, but it is a *scope* problem: the credential is valid, the account is
        # simply not an administrator, and the fix is a different application password.
        if not caps.get("admin"):
            return WordPressStatus.ACTIVE.value, ISSUE_NOT_ADMINISTRATOR
        return WordPressStatus.ACTIVE.value, None

    text = " ".join(errors.values()).lower()
    if "http 401" in text or "http 403" in text:
        return WordPressStatus.ERROR.value, ISSUE_CREDENTIAL_REFUSED
    if not errors:
        return WordPressStatus.ERROR.value, ISSUE_CREDENTIAL_REFUSED
    if "rest_no_route" in text or "http 404" in text:
        # Every probe 404'd, including the REST index. That is not a WordPress site (or REST is
        # disabled), and telling somebody their password is wrong would send them to re-mint a
        # credential that would fail identically.
        return WordPressStatus.ERROR.value, ISSUE_NOT_WORDPRESS
    return WordPressStatus.UNREACHABLE.value, ISSUE_UNREACHABLE


def _read(site: WordPressSite) -> WordPressSiteRead:
    """One row for the wire. The password becomes a *fact about* the password."""
    return WordPressSiteRead(
        id=site.id,
        website_id=site.website_id,
        base_url=site.base_url,
        username=site.username,
        active=site.active,
        status=site.status,
        last_error=site.last_error,
        capabilities=site.capabilities or {},
        capability_errors=site.capability_errors or {},
        capabilities_checked_at=site.capabilities_checked_at,
        mcp_server_path=site.mcp_server_path,
        rankmath_version=site.rankmath_version,
        rankmath_ai_visibility=supports_ai_visibility(site.rankmath_version),
        last_verified_at=site.last_verified_at,
        password_configured=bool(site.app_password_encrypted),
        created_at=site.created_at,
        updated_at=site.updated_at,
    )


async def resolve_credential(
    session: AsyncSession, org_id: uuid.UUID, website_id: uuid.UUID
) -> WordPressCredential | None:
    """The ``app.core.wordpress`` seam's implementation — registered in this package's ``__init__``.

    Scoped by ``org_id`` and to an **active** row. It deliberately does *not* re-check the
    company horizon: the callers are a nightly cron bound per org and a service that has
    already loaded the link through its own tenant-scoped repository, so the horizon was
    applied where the caller's identity exists. A background job has no membership to narrow by,
    and inventing one here would be a second opinion about a question already answered.
    """
    site = await session.scalar(
        select(WordPressSite).where(
            WordPressSite.org_id == org_id,
            WordPressSite.website_id == website_id,
            WordPressSite.active.is_(True),
        )
    )
    if site is None or not site.app_password_encrypted:
        return None
    try:
        password = decrypt(site.app_password_encrypted)
    except ValueError:
        # The encryption key was rotated and this secret is unreadable, so it is not there.
        # Same conclusion `marketing.resolve_seranking_key` reaches, and for the same reason.
        return None
    return WordPressCredential(
        site_id=site.id,
        website_id=site.website_id,
        base_url=site.base_url,
        username=site.username,
        app_password=password,
        capabilities=dict(site.capabilities or {}),
        rankmath_ai_visibility=supports_ai_visibility(site.rankmath_version),
    )


def open_client(credential: WordPressCredential) -> WordPressClient:
    """The ``app.core.wordpress`` seam's client factory — registered beside the resolver.

    One line, and it is the line that keeps ``marketing`` from importing
    :class:`WordPressClient` (§6). Anything that later belongs on every outbound call to a
    client's site — a retry policy, a per-site TLS quirk, a header this module wants in their
    access log — lands here rather than at whichever call sites had imported the class.
    """
    return WordPressClient(
        credential.base_url, credential.username, credential.app_password
    )


#: Where each unmet prerequisite is actually fixed, inside the client's own ``wp-admin``.
#: Built from the stored ``base_url`` and never guessed: a link to a site we have no address
#: for is a control that can only refuse (#253), so a state with no credential row carries no
#: links at all.
_ADMIN_PATHS = {
    # WordPress's own "an application wants access" screen. Deliberately this rather than
    # ``profile.php#application-passwords``: it opens the form with the name prefilled, which
    # is two fewer things to explain to somebody who has never minted one.
    "app_passwords": "/wp-admin/authorize-application.php?app_name=schakl",
    "plugins": "/wp-admin/plugin-install.php?s=rank%20math&tab=search&type=term",
    "ai_visibility": "/wp-admin/admin.php?page=rank-math-ai-visibility",
}


def _links(base_url: str) -> dict[str, str]:
    base = base_url.rstrip("/")
    return {name: f"{base}{path}" for name, path in _ADMIN_PATHS.items()} if base else {}


def _stage_from_failure(exc: Exception, site: WordPressSite) -> str:
    """Which prerequisite a live AI Visibility failure names.

    The **slug** is the diagnosis, not the status code: ``rest_no_route``, ``rest_forbidden``
    and ``aiv_unauthorized`` are three different jobs for three different people and two of
    them share a 403 (``client.describe_failure`` says the same thing one layer down). The
    stored probe only ever breaks a tie the slug cannot — it is a memory, and the call that
    just happened is evidence.
    """
    if isinstance(exc, WordPressUnreachable):
        return STAGE_UNREACHABLE

    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None) or ""
    caps = site.capabilities or {}

    if code == "aiv_unauthorized":
        # Rank Math answered, and said this site has no Content AI subscription reaching AI
        # Visibility. Nothing about the credential is wrong.
        return STAGE_AI_VISIBILITY_UNAVAILABLE
    if status in (401, 403):
        # An administrator check we have *observed* to fail outranks the generic refusal: every
        # AI Visibility route is `manage_options`, so a valid editor credential refuses here and
        # re-minting it would produce exactly the same 403.
        if caps.get("admin") is False:
            return STAGE_NOT_ADMINISTRATOR
        if code == "rest_forbidden":
            return STAGE_NOT_ADMINISTRATOR
        return STAGE_CREDENTIAL_REFUSED
    if status == 404 or code == "rest_no_route":
        # The route is not registered. Whether that is "no plugin", "too old" or "the feature is
        # switched off" is a question the plugin list already answered, and the honest order is
        # absent → too old → present and still not serving.
        if not site.rankmath_version:
            return STAGE_RANKMATH_MISSING
        if not supports_ai_visibility(site.rankmath_version):
            return STAGE_RANKMATH_TOO_OLD
        return STAGE_AI_VISIBILITY_UNAVAILABLE
    # A 500 from the site, a WAF page, a PHP fatal. Not unreachable — we got an answer — and
    # emphatically not the credential, so it keeps its own state rather than borrowing one that
    # would send somebody to re-mint a password that is fine.
    return STAGE_SITE_ERROR


async def describe_setup(
    session: AsyncSession,
    org_id: uuid.UUID,
    website_id: uuid.UUID,
    *,
    exc: Exception | None = None,
    brand_count: int | None = None,
) -> WordPressSetupState:
    """The ``app.core.wordpress`` seam's diagnosis provider.

    Registered in this package's ``__init__`` beside the resolver.

    Scoped by ``org_id`` and deliberately **not** re-checking the company horizon, for the
    reason :func:`resolve_credential` gives: the caller has already loaded this website through
    its own tenant-scoped repository, and a second opinion here would be a different answer to
    a question already asked.

    It reads the stored row and never dials out. Everything it needs from the site itself
    arrived with ``exc`` or ``brand_count`` — the call the borrower just made — which is what
    keeps this from being a second round trip on a screen that already spent one.
    """
    site = await session.scalar(
        select(WordPressSite).where(
            WordPressSite.org_id == org_id,
            WordPressSite.website_id == website_id,
            WordPressSite.active.is_(True),
        )
    )
    if site is None or not site.app_password_encrypted:
        # No row, a deactivated one, or one whose secret was never stored. All three mean the
        # same thing to somebody trying to connect, and none of them tells us a site address.
        return WordPressSetupState(stage=STAGE_NO_CREDENTIAL)

    links = _links(site.base_url)
    if exc is not None:
        return WordPressSetupState(
            stage=_stage_from_failure(exc, site),
            detail=describe_failure(exc),
            links=links,
            rankmath_version=site.rankmath_version,
        )
    if brand_count == 0:
        # The route answered, so the plugin is there, the credential is an administrator's and
        # the subscription reaches. What is missing is a brand, which is a job in Rank Math.
        return WordPressSetupState(
            stage=STAGE_NO_BRANDS, links=links, rankmath_version=site.rankmath_version
        )
    return WordPressSetupState(
        stage=STAGE_READY, links=links, rankmath_version=site.rankmath_version
    )


class WordPressService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = TenantScopedRepository(
            ctx.session, ctx.org.id, WordPressSite, company_scope=ctx.company_scope
        )
        self.activity = ActivityService(ctx)

    # --- reads ---------------------------------------------------------------------------- #
    async def list(self, *, website_id: uuid.UUID | None = None) -> list[WordPressSiteRead]:
        stmt = self.repo.scoped_select().order_by(WordPressSite.created_at)
        if website_id is not None:
            stmt = stmt.where(WordPressSite.website_id == website_id)
        rows = (await self.ctx.session.execute(stmt)).scalars().all()
        return [_read(row) for row in rows]

    async def get(self, site_id: uuid.UUID) -> WordPressSiteRead:
        return _read(await self.repo.get_or_404(site_id))

    async def for_website(self, website_id: uuid.UUID) -> WordPressSiteRead | None:
        """The one credential a website has, or ``None``.

        The panel's whole load. ``None`` rather than a 404 because "this website has no
        WordPress connected" is the *normal* state of most websites, not an error — a panel
        that had to catch a 404 to draw its empty state would log one per page view.
        """
        rows = await self.list(website_id=website_id)
        return rows[0] if rows else None

    # --- writes --------------------------------------------------------------------------- #
    async def create(self, data: WordPressSiteCreate) -> WordPressSiteRead:
        await self._assert_website_visible(data.website_id)
        existing = await self.ctx.session.scalar(
            self.repo.scoped_select().where(WordPressSite.website_id == data.website_id)
        )
        if existing is not None:
            raise AppError(
                "conflict", "errors.wordpress_already_connected", status_code=409
            )
        site = await self.repo.create(
            website_id=data.website_id,
            base_url=data.base_url,
            username=data.username,
            app_password_encrypted=encrypt(data.app_password),
            active=data.active,
            status=WordPressStatus.PENDING.value,
        )
        await self.ctx.session.flush()
        await self.activity.record(ENTITY_TYPE, site.id, "created")
        return _read(site)

    async def update(self, site_id: uuid.UUID, data: WordPressSiteUpdate) -> WordPressSiteRead:
        site = await self.repo.get_or_404(site_id)
        before = {field: getattr(site, field) for field in _AUDITED_FIELDS}

        for field in _AUDITED_FIELDS:
            value = getattr(data, field)
            if value is not None:
                setattr(site, field, value)

        if data.app_password is not None:
            site.app_password_encrypted = encrypt(data.app_password)
            # The trail records *that* it changed, never the value (§16, and the model's own
            # docstring). A rotated credential is exactly the change somebody needs to
            # attribute six months later; its bytes are exactly what they must not find here.
            await self.activity.record(ENTITY_TYPE, site.id, "password_changed")
            # A rotated password invalidates everything the *old* one was observed to reach.
            # Leaving the ✓s up would have the panel vouching for a credential nobody has
            # tried yet, which is the "we looked" / "nobody has looked" distinction thrown
            # away — so the row goes back to pending and asks to be verified.
            site.capabilities = {}
            site.capability_errors = {}
            site.capabilities_checked_at = None
            site.status = WordPressStatus.PENDING.value
            site.last_error = None

        await self.activity.record_update(
            ENTITY_TYPE, site.id, before, {f: getattr(site, f) for f in _AUDITED_FIELDS}
        )
        await self.ctx.session.flush()
        # ``updated_at`` is a server-side ``onupdate``, so the flush expires it and reading it
        # from a sync serializer would lazy-load outside the greenlet. The repository's own
        # ``update()`` refreshes for exactly this reason; setting attributes directly (which is
        # what the password rotation needs) skips it, so refresh here.
        await self.ctx.session.refresh(site)
        return _read(site)

    async def delete(self, site_id: uuid.UUID) -> None:
        """Forget the credential here and touch **nothing** on the client's WordPress.

        Deliberately not "and revoke the application password", which we could do: revoking is
        the client's own act on their own profile screen, and doing it as a side effect of
        tidying a list would break whatever else that password was minted for. The same
        reasoning ``uptime.delete_instance`` gives for leaving a client's monitors alone.
        """
        site = await self.repo.get_or_404(site_id)
        await self.activity.record(ENTITY_TYPE, site.id, "deleted")
        await self.repo.delete(site)

    # --- the one call that dials out ------------------------------------------------------ #
    async def verify(self, site_id: uuid.UUID) -> WordPressVerifyResult:
        """Probe the site and record what was observed.

        Never raises for a refused credential: the point of the screen is to *say* what is
        wrong, and an exception is the one shape that cannot carry a per-capability answer. It
        raises only for the states that are not about this site at all (the row is gone, the
        stored secret is unreadable).
        """
        site = await self.repo.get_or_404(site_id)
        try:
            password = decrypt(site.app_password_encrypted)
        except ValueError as exc:
            raise AppError(
                "invalid_state", "errors.wordpress_secret_unreadable", status_code=409
            ) from exc

        client = WordPressClient(site.base_url, site.username, password)
        # The pool connection is handed back for the round trip — §3's rule, and this call can
        # take seconds against a cold shared host.
        async with self.ctx.release_db():
            caps, errors, observed = await client.probe_capabilities()

        site = await self.repo.get_or_404(site_id)  # re-attach after the release
        return await self._record_probe(site, caps, errors, observed)

    async def _record_probe(
        self,
        site: WordPressSite,
        caps: dict[str, bool],
        errors: dict[str, str],
        observed: dict[str, Any],
    ) -> WordPressVerifyResult:
        status, issue = _classify(caps, errors)
        site.capabilities = caps
        # Only ever the refusals of capabilities that actually answered no; keeping the rest
        # would leave stale explanations beside green ticks.
        site.capability_errors = {k: v for k, v in errors.items() if not caps.get(k)}
        site.capabilities_checked_at = _now()
        site.last_verified_at = _now()
        site.status = status
        # **This is the line that clears the flag.** A status that only ever turns on is a bug
        # with a long tail (docs/CLOUDFLARE.md's `_flag_account`), so the assignment is
        # unconditional: a probe that got through sets `issue` to None and the red line goes.
        site.last_error = issue

        if isinstance(observed.get("mcp_server_path"), str):
            site.mcp_server_path = observed["mcp_server_path"]
        elif caps.get("rest") and not caps.get("mcp"):
            # Reached the site and it has no MCP namespace: forget a path we recorded when it
            # did. An observation that ran and found nothing clears its own entry; one that
            # could not run leaves the previous value alone, which is why this is not in the
            # `else` of a bare `if`.
            site.mcp_server_path = None
        if isinstance(observed.get("rankmath_version"), str):
            site.rankmath_version = observed["rankmath_version"]
        elif observed.get("rankmath_absent"):
            site.rankmath_version = None

        await self.ctx.session.flush()
        await self.ctx.session.refresh(site)  # server-side ``updated_at`` — see ``update``
        brand_count = observed.get("brand_count")
        return WordPressVerifyResult(
            ok=any(caps.values()),
            status=status,
            capabilities=caps,
            capability_errors=site.capability_errors,
            rankmath_version=site.rankmath_version,
            rankmath_ai_visibility=supports_ai_visibility(site.rankmath_version),
            mcp_server_path=site.mcp_server_path,
            brand_count=brand_count if isinstance(brand_count, int) else None,
            error=issue,
        )

    async def brands(self, site_id: uuid.UUID) -> list[WordPressBrand]:
        """The Rank Math brands this site tracks — the marketing picker's options.

        Cache-first on Rank Math's side (no ``refresh``): choosing which brand to link is not
        the moment to spend a client's Content AI quota on a fresh upstream analysis. The
        *sync* is what forces one.
        """
        site = await self.repo.get_or_404(site_id)
        try:
            password = decrypt(site.app_password_encrypted)
        except ValueError as exc:
            raise AppError(
                "invalid_state", "errors.wordpress_secret_unreadable", status_code=409
            ) from exc

        client = WordPressClient(site.base_url, site.username, password)
        async with self.ctx.release_db():
            try:
                payload = await client.ai_visibility_overview()
            except (WordPressAuthError, WordPressError, WordPressUnreachable) as exc:
                raise AppError(
                    "upstream", "errors.wordpress_ai_visibility_unavailable", status_code=502,
                    fields={"detail": describe_failure(exc)},
                ) from exc

        rows = payload.get("brands")
        brands = [brand_from_payload(row) for row in rows] if isinstance(rows, list) else []
        return [brand for brand in brands if brand is not None]

    # --- helpers -------------------------------------------------------------------------- #
    async def _assert_website_visible(self, website_id: uuid.UUID) -> None:
        """The website must exist **in this org** and **inside the caller's horizon**.

        Two checks, because they answer different questions and each is silent about the
        other's failure.

        ``entity_visible`` is the horizon seam (§15) and is what stops a restricted membership
        attaching a credential to a client it cannot see — #285's "writes that would land
        outside a client's horizon are refused *before* the row is written". But it answers
        ``True`` **without a query** for every unrestricted membership, which is the right
        shape for a horizon check and no tenancy check at all: an owner handing us another
        org's website UUID would have written a row whose ``website_id`` points outside the
        tenant, and the FK cannot object because ``websites.id`` is global. So the org-scoped
        existence check comes first, through a bare-table bridge rather than an import (§6) —
        the same bridge this module's model uses for the horizon clause.

        Both answer 404, never 403: on a get-by-id a 403 leaks that the row exists (§15).
        """
        from app.core.scope import entity_visible

        exists = await self.ctx.session.scalar(
            select(_websites.c.id).where(
                _websites.c.id == website_id, _websites.c.org_id == self.ctx.org.id
            )
        )
        if exists is None or not await entity_visible(self.ctx, "website", website_id):
            raise AppError("not_found", "errors.not_found", status_code=404)


__all__ = [
    "CAPABILITIES",
    "WordPressService",
    "describe_setup",
    "open_client",
    "resolve_credential",
]
