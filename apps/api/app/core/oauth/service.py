"""The OAuth 2.1 flow (docs/MCP.md) — registration, consent, redemption, refresh.

**The authorization server authenticates nobody.** It has no login of its own: the consent step
runs on the browser session the web app already holds, which may have been a local password with
2FA or an OIDC federation this org configured (§3). Building a second login here would have
meant a second password path, a second 2FA decision and a second answer to "which org is this
session for" — three copies of things that are already right once.

**And it issues no new kind of credential.** What redemption hands back is an ``api_keys`` row
belonging to the consenting *user*, so every rule that already governs a personal key governs an
OAuth session unchanged: scopes capped by the owner's live permissions on **every** request (a
demoted member's connector is demoted with them), the company horizon of the person who
consented, tenant scoping by hostname, revocation, rate limiting. The protocol contributes the
handshake; it contributes no authority.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlparse, urlunparse

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apikeys import keys as keygen
from app.core.apikeys.models import PRINCIPAL_USER, ApiKey
from app.core.oauth.models import OAuthClient, OAuthGrant
from app.core.permissions.catalog import all_permissions
from app.core.permissions.permset import PermissionSet
from app.core.permissions.spec import SCOPES
from app.core.tenancy import RequestContext
from app.errors import AppError

logger = logging.getLogger("schakl.oauth")

#: How long a client has to exchange its code. RFC 6749 says "short"; ten minutes covers a
#: browser redirect and a slow token request without leaving a usable code lying in a log.
CODE_TTL = timedelta(minutes=10)
#: The access token's life. Short, because it is the credential that travels on every call —
#: the refresh below is what keeps a connector working without a person in the loop.
ACCESS_TTL = timedelta(hours=1)
#: The refresh token's life, extended on every use, so an in-use connector never expires and an
#: abandoned one lapses within a quarter.
REFRESH_TTL = timedelta(days=90)

#: Coarse scopes a client may ask for by name. A connector that has never met this instance
#: cannot know that ``time.entry.write:own`` exists, and a request listing 300 permission
#: strings is not one a person can read on a consent screen — so the request is allowed to be
#: coarse and the *consent screen* is where it becomes exact.
SCOPE_MCP_READ = "mcp:read"
SCOPE_MCP_FULL = "mcp:full"
COARSE_SCOPES = (SCOPE_MCP_READ, SCOPE_MCP_FULL)

#: Registration is unauthenticated, so it is capped per org. Far above any real client count and
#: far below "a table somebody filled up overnight".
MAX_CLIENTS_PER_ORG = 200


def _now() -> datetime:
    return datetime.now(UTC)


# --- redirect URIs ------------------------------------------------------------------------ #


def validate_redirect_uris(uris: Sequence[str]) -> list[str]:
    """The registered targets, or a 422 naming why one was refused.

    ``https`` or loopback only, no fragment, and an **exact** string — the match at authorize
    time is equality, never a prefix. A prefix match is how an open redirector gets built by
    accident: ``https://client.example/cb`` would accept ``https://client.example/cb.evil.com``.

    A custom scheme (``claude://callback``) is allowed because a desktop client has no web
    origin to come back to, and it is exactly the case OAuth 2.1 keeps native redirects for.
    """
    if not uris:
        raise _invalid_request("errors.oauth_redirect_required")
    cleaned: list[str] = []
    for raw in uris:
        uri = (raw or "").strip()
        parsed = urlparse(uri)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        scheme_ok = (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and loopback)
            # A native client has no web origin to come back to, which is exactly the case
            # OAuth 2.1 keeps custom redirect schemes for.
            or (bool(parsed.scheme) and parsed.scheme not in {"http", "https"})
        )
        ok = uri and len(uri) <= 1024 and not parsed.fragment and scheme_ok
        if not ok:
            raise _invalid_request("errors.oauth_redirect_invalid")
        cleaned.append(uri)
    return cleaned


def _invalid_request(message_key: str) -> AppError:
    return AppError("validation", message_key, status_code=422)


# --- scopes -------------------------------------------------------------------------------- #


def _catalog() -> dict[str, bool]:
    """Permission key → whether it is scoped (``:own``/``:any``)."""
    return {spec.key: bool(spec.scopes) for spec in all_permissions()}


def _is_read(key: str) -> bool:
    return key.rsplit(".", 1)[-1] == "read"


def expand_scopes(requested: Sequence[str], holder: PermissionSet) -> list[str]:
    """What the client asked for, resolved against the catalog and capped by the *holder*.

    The cap is the whole safety property and it is applied twice on purpose: here, so a consent
    screen never offers a person the ability to hand out something they do not have, and again
    on every request the key later makes (``apikeys/auth.py``), so a permission removed
    tomorrow is removed from the connector tomorrow. This one is the cosmetic half — it decides
    what the screen shows. The one on the request path is the one that holds.

    An unknown scope is *dropped* rather than fatal: a client sends the scopes it was built to
    send, an instance runs the modules it runs, and refusing the whole authorization because a
    connector asked for something this instance does not have would be a dead "Add connector"
    button with no way for anyone to see why.
    """
    scoped = _catalog()
    wanted = set(requested)
    want_all = SCOPE_MCP_FULL in wanted or not wanted
    want_read = SCOPE_MCP_READ in wanted

    resolved: list[str] = []
    for key, is_scoped in scoped.items():
        coarse = want_all or (want_read and _is_read(key))
        explicit = key in wanted or any(w.split(":")[0] == key for w in wanted if ":" in w)
        if not (coarse or explicit):
            continue
        # A scoped permission is only ever stored suffixed (§15), and the broadest suffix the
        # holder actually has is the honest answer: handing out `:any` to someone holding `:own`
        # would be a silent escalation, and handing out `:own` to a holder of `:any` would
        # quietly break a screen they can open.
        if is_scoped:
            suffix = next((s for s in ("any", "own") if holder.has(key, s)), None)
            if suffix is None:
                continue
            resolved.append(f"{key}:{suffix}")
        elif holder.has(key):
            resolved.append(key)
    return sorted(resolved)


def validate_consented_scopes(scopes: Sequence[str], holder: PermissionSet) -> list[str]:
    """The scopes the user ticked, refused unless the catalog and the holder both allow them.

    The consent form is a browser form, so what comes back is whatever the browser sent — the
    narrowing a person did on screen is a *request*, not a fact. Re-derived against the same
    two authorities rather than trusted, exactly as ``ApiKeyService._validate_scopes`` does for
    the key screen.
    """
    scoped = _catalog()
    allowed: list[str] = []
    for scope in scopes:
        base, sep, suffix = scope.partition(":")
        if base not in scoped or (suffix != "" and suffix not in SCOPES):
            raise _invalid_request("errors.oauth_scope_invalid")
        if bool(sep) != scoped[base]:  # scoped ⇔ suffixed
            raise _invalid_request("errors.oauth_scope_invalid")
        if not holder.has(base, suffix or None):
            raise AppError("forbidden", "errors.apikey_scope_exceeds_grants", status_code=403)
        allowed.append(scope)
    if not allowed:
        raise _invalid_request("errors.oauth_scope_empty")
    return allowed


# --- clients ------------------------------------------------------------------------------- #


class OAuthService:
    """Everything the flow does, against one tenant-bound session.

    Deliberately not built on ``RequestContext``: three of the four steps happen before anyone
    has authenticated (registration, the metadata reads, the token exchange), so the org comes
    from the resolved hostname and RLS is already bound by the caller. The one step that *does*
    have a user — consent — takes it as an argument.
    """

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self.session = session
        self.org_id = org_id

    async def register_client(
        self,
        *,
        client_name: str,
        redirect_uris: Sequence[str],
        client_uri: str | None = None,
        logo_uri: str | None = None,
        confidential: bool = False,
        created_by_user_id: uuid.UUID | None = None,
    ) -> tuple[OAuthClient, str | None]:
        """RFC 7591. Returns the row and the client secret, which is shown exactly once."""
        # Counted with a LIMIT rather than a COUNT(*): the question is "are there already too
        # many", and the cheap version of that question stops looking as soon as it knows.
        live = (
            await self.session.execute(
                select(OAuthClient.id)
                .where(OAuthClient.org_id == self.org_id, OAuthClient.revoked_at.is_(None))
                .limit(MAX_CLIENTS_PER_ORG + 1)
            )
        ).scalars()
        if len(live.all()) > MAX_CLIENTS_PER_ORG:
            raise AppError("rate_limited", "errors.oauth_too_many_clients", status_code=429)

        secret: str | None = None
        secret_hash: str | None = None
        if confidential:
            secret = secrets.token_urlsafe(32)
            secret_hash = keygen.hash_secret(secret)

        client = OAuthClient(
            org_id=self.org_id,
            client_id=f"schakc_{secrets.token_hex(16)}",
            secret_hash=secret_hash,
            client_name=(client_name or "MCP client")[:200],
            client_uri=client_uri,
            logo_uri=logo_uri,
            redirect_uris=validate_redirect_uris(redirect_uris),
            created_by_user_id=created_by_user_id,
        )
        self.session.add(client)
        await self.session.flush()
        return client, secret

    async def client_by_id(self, client_id: str) -> OAuthClient | None:
        return await self.session.scalar(
            select(OAuthClient).where(
                OAuthClient.org_id == self.org_id,
                OAuthClient.client_id == client_id,
                OAuthClient.revoked_at.is_(None),
            )
        )

    async def require_client(self, client_id: str, redirect_uri: str) -> OAuthClient:
        """The client, or a refusal — and the refusal is deliberately *not* a redirect.

        An unregistered client or an unregistered redirect target is the one class of error that
        must never be reported by redirecting to the URI in question: that is the open-redirect
        the exact-match list exists to prevent, and bouncing an error to an attacker-chosen URL
        would hand them the ``state`` as well.
        """
        client = await self.client_by_id(client_id)
        if client is None:
            raise AppError("not_found", "errors.oauth_client_unknown", status_code=400)
        if redirect_uri not in (client.redirect_uris or []):
            raise AppError("validation", "errors.oauth_redirect_mismatch", status_code=400)
        return client

    # --- consent ---------------------------------------------------------------------- #

    async def approve(
        self,
        *,
        client: OAuthClient,
        ctx: RequestContext,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scopes: Sequence[str],
        resource: str | None,
        state: str | None,
        issuer: str | None = None,
    ) -> str:
        """Write the grant and return the URL to send the browser back to.

        PKCE is required, not optional, and only ``S256`` is accepted: OAuth 2.1 drops ``plain``,
        and accepting it would make the verifier a value anyone who saw the authorization request
        already holds — which is the entire attack PKCE exists to stop.
        """
        if code_challenge_method != "S256" or not (43 <= len(code_challenge) <= 128):
            raise _invalid_request("errors.oauth_pkce_required")
        granted = validate_consented_scopes(scopes, ctx.permissions)

        code = secrets.token_urlsafe(32)
        self.session.add(
            OAuthGrant(
                org_id=self.org_id,
                client_pk=client.id,
                user_id=ctx.user.id,
                code_hash=keygen.hash_secret(code),
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                scopes=granted,
                resource=resource,
                expires_at=_now() + CODE_TTL,
            )
        )
        client.last_used_at = _now()
        await self.session.flush()
        return _redirect_with(
            redirect_uri,
            {
                "code": code,
                **({"state": state} if state else {}),
                # RFC 9207 — strict clients compare it against the issuer they discovered.
                **({"iss": issuer} if issuer else {}),
            },
        )

    # --- redemption ------------------------------------------------------------------- #

    async def redeem_code(
        self,
        *,
        client: OAuthClient,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> tuple[str, str, int, list[str]]:
        """Exchange a code for ``(access_token, refresh_token, expires_in, scopes)``.

        **Single use is the database's job.** Two deliveries of a retried token request run
        against two API replicas that share no memory, so "have we redeemed this?" followed by a
        write leaves a window every retry enters. The conditional ``UPDATE … WHERE redeemed_at
        IS NULL`` closes it: the loser updates zero rows and is refused, which is also the right
        answer to a *replayed* code (docs/PAYMENTS.md's rule, one protocol over).
        """
        code_hash = keygen.hash_secret(code)
        stamped = (
            await self.session.execute(
                update(OAuthGrant)
                .where(
                    OAuthGrant.org_id == self.org_id,
                    OAuthGrant.code_hash == code_hash,
                    OAuthGrant.redeemed_at.is_(None),
                    OAuthGrant.expires_at > _now(),
                )
                .values(redeemed_at=_now())
                .returning(OAuthGrant.id)
            )
        ).scalar_one_or_none()
        if stamped is None:
            raise _invalid_grant()
        grant = await self.session.get(OAuthGrant, stamped)
        assert grant is not None  # noqa: S101 — just stamped it, in this transaction

        if grant.client_pk != client.id or grant.redirect_uri != redirect_uri:
            raise _invalid_grant()
        # RFC 7636 §4.6: BASE64URL(SHA256(verifier)), unpadded, equals the stored challenge.
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        if not hmac.compare_digest(computed, grant.code_challenge):
            raise _invalid_grant()

        key, access, refresh = await self._mint_session(
            client=client, user_id=grant.user_id, scopes=list(grant.scopes)
        )
        grant.api_key_id = key.id
        await self.session.flush()
        return access, refresh, int(ACCESS_TTL.total_seconds()), list(grant.scopes)

    async def refresh(
        self, *, client: OAuthClient, refresh_token: str
    ) -> tuple[str, str, int, list[str]]:
        """Rotate the access token on an existing session.

        The **refresh token itself is not rotated**, and that is a decision rather than an
        omission. OAuth 2.1 asks a public client to rotate or sender-constrain; rotation without
        a replay window turns one dropped HTTP response into a connector that is silently dead
        until somebody re-consents, and the failure happens on a machine nobody here can see.
        What limits exposure instead is the hour-long access token, plus the three kill switches
        this design already has and a token table would not: revoke the key, revoke the client,
        or remove the person's membership. Written down here so a later reader can weigh it
        rather than discover it.
        """
        parsed = keygen.parse(refresh_token, keygen.REFRESH_TOKEN_PREFIX)
        if parsed is None:
            raise _invalid_grant()
        prefix, secret = parsed
        key = await self.session.scalar(
            select(ApiKey).where(
                ApiKey.org_id == self.org_id,
                ApiKey.refresh_prefix == prefix,
                ApiKey.oauth_client_id == client.id,
            )
        )
        if (
            key is None
            or key.refresh_hash is None
            or not keygen.verify_secret(secret, key.refresh_hash)
            or key.revoked_at is not None
            or (key.refresh_expires_at is not None and key.refresh_expires_at <= _now())
        ):
            raise _invalid_grant()

        access = keygen.generate()
        key.prefix = access.prefix
        key.hash = access.secret_hash
        key.expires_at = _now() + ACCESS_TTL
        key.refresh_expires_at = _now() + REFRESH_TTL
        await self.session.flush()
        return (
            access.plaintext,
            refresh_token,
            int(ACCESS_TTL.total_seconds()),
            list(key.scopes),
        )

    async def _mint_session(
        self, *, client: OAuthClient, user_id: uuid.UUID, scopes: list[str]
    ) -> tuple[ApiKey, str, str]:
        """One ``api_keys`` row carrying both tokens — the access secret and the refresh secret.

        One row rather than two tables because they are one grant: revoking it revokes both, and
        the pair can never drift into disagreeing about who consented to what.
        """
        access = keygen.generate()
        refresh = keygen.generate(keygen.REFRESH_TOKEN_PREFIX)
        key = ApiKey(
            org_id=self.org_id,
            name=client.client_name[:120],
            prefix=access.prefix,
            hash=access.secret_hash,
            principal_type=PRINCIPAL_USER,
            user_id=user_id,
            scopes=scopes,
            expires_at=_now() + ACCESS_TTL,
            created_by_user_id=user_id,
            oauth_client_id=client.id,
            refresh_prefix=refresh.prefix,
            refresh_hash=refresh.secret_hash,
            refresh_expires_at=_now() + REFRESH_TTL,
        )
        self.session.add(key)
        await self.session.flush()
        return key, access.plaintext, refresh.plaintext

    async def revoke_token(self, token: str) -> None:
        """RFC 7009. Always succeeds — a revocation endpoint that reports "no such token" is a
        token oracle, and the caller can do nothing useful with the distinction anyway."""
        for token_prefix, column, secret_column in (
            (keygen.TOKEN_PREFIX, ApiKey.prefix, "hash"),
            (keygen.REFRESH_TOKEN_PREFIX, ApiKey.refresh_prefix, "refresh_hash"),
        ):
            parsed = keygen.parse(token, token_prefix)
            if parsed is None:
                continue
            prefix, secret = parsed
            key = await self.session.scalar(
                select(ApiKey).where(ApiKey.org_id == self.org_id, column == prefix)
            )
            stored = getattr(key, secret_column, None) if key is not None else None
            if key is not None and stored and keygen.verify_secret(secret, stored):
                key.revoked_at = _now()
                await self.session.flush()
            return


def _invalid_grant() -> AppError:
    """One answer for every way a code or refresh token can be wrong.

    Expired, replayed, issued to another client, PKCE mismatch — all the same refusal, because
    telling them apart tells a holder of a stolen code which part they still need.
    """
    return AppError("invalid_grant", "errors.oauth_invalid_grant", status_code=400)


def _redirect_with(redirect_uri: str, params: dict[str, str]) -> str:
    parsed = urlparse(redirect_uri)
    query = f"{parsed.query}&{urlencode(params)}" if parsed.query else urlencode(params)
    return urlunparse(parsed._replace(query=query))
