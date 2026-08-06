"""Business logic for the cloudflare module (epic #278). Business-licensed — see LICENSE.

Every DB read goes through the tenant-scoped repository (Golden Rule 1); every Cloudflare call
goes through one account's token. Three rules shape the whole file:

**Never guess which account.** A tenant holds several Cloudflare accounts, and the same apex can
exist in more than one of them (Cloudflare only makes *activation* exclusive, not creation). So
resolution either finds exactly one answer or reports the ambiguity — it never takes the first
row. Putting a client's zone in the wrong account is not a mistake you fix by editing a field:
moving a zone between Cloudflare accounts means deleting and recreating it, with a nameserver
change and a propagation window in the middle.

**Observe before you write.** "Connect" adopts an existing zone before it considers creating one,
and a redirect reconcile compares what Cloudflare has against what we asked for and *reports*
drift instead of overwriting it. An agency taking over a client's existing Cloudflare setup is
the normal case, not the edge one.

**Nothing at Cloudflare is deleted that schakl did not create.** Every destructive call takes an
id this module stored when it created the thing.

The one deliberate coupling to another module is ``domains``, reached as a bare table (§6) — the
same bridge ``websites`` uses. That table is also where the horizon lives for everything here, so
:meth:`CloudflareService._domain_or_404` is the single place the predicate is written; every
domain-addressed path goes through it (§15's failure mode 3).
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import column, func, select, table, update

from app.core.activity import ActivityService
from app.core.crypto import decrypt, encrypt
from app.core.providers.models import Provider
from app.core.tenancy import RequestContext
from app.core.urls import reject_dangerous_url
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.modules.cloudflare import redirects as rules
from app.modules.cloudflare.client import (
    CloudflareAuthError,
    CloudflareClient,
    CloudflareError,
)
from app.modules.cloudflare.models import (
    CloudflareAccount,
    CloudflareAccountStatus,
    CloudflarePagesLink,
    CloudflarePagesProject,
    CloudflareRedirect,
    CloudflareRegistrarDomain,
    CloudflareZone,
    RedirectStatus,
)
from app.modules.cloudflare.schemas import (
    AccountCreate,
    AccountSyncResult,
    AccountUpdate,
    AccountVerifyResult,
    ConnectRequest,
    DnsRecordWrite,
    OriginState,
    PagesLinkCreate,
    RedirectConflict,
    RedirectObservation,
    RedirectWrite,
    ZoneCandidate,
)

logger = logging.getLogger("schakl.cloudflare")

#: The activity trail hangs on the **domain**, which is the record a user opens (§16). The
#: account's own trail uses its own entity type.
DOMAIN_ENTITY = "domain"
ACCOUNT_ENTITY = "cloudflare_account"

#: ``domains`` belongs to another module; referenced as a bare table (§6).
_domains = table(
    "domains",
    column("id"),
    column("org_id"),
    column("company_id"),
    column("name"),
    column("status"),
    column("redirect_url"),
    column("nameservers"),
)

#: Cloudflare's documented placeholder for a redirect-only hostname: a **proxied** AAAA at the
#: IPv6 discard prefix. It answers nothing itself — its only job is to make the hostname resolve
#: to Cloudflare's edge so a Redirect Rule has traffic to act on.
ORIGIN_PLACEHOLDER_TYPE = "AAAA"
ORIGIN_PLACEHOLDER_CONTENT = "100::"

#: Record types that can carry the orange cloud. A redirect needs one of these, proxied.
PROXIABLE_TYPES = ("A", "AAAA", "CNAME")

#: How many Pages projects a single sync will interrogate one call at a time. Cloudflare embeds
#: a project's custom domains in the project object, so the normal path costs nothing extra and
#: never reaches this; the cap bounds only the fallback for a payload that omits them, where an
#: account with hundreds of projects would otherwise turn one sync into hundreds of requests.
#: Reaching it is reported as a warning, never swallowed.
PAGES_DOMAIN_SCAN_LIMIT = 100

#: Cloudflare error codes worth their own message. Everything else falls back to the generic
#: key — a wrong-but-specific message is worse than an honest generic one.
_ERROR_CODES: dict[int, tuple[str, str, int]] = {
    # A malformed credential answers **400/6003**, not 401 — Cloudflare rejects the header before
    # it ever looks the token up. Left on the generic key it read as "Cloudflare refused this
    # request", which points at Cloudflare; the thing to fix is the token the admin just pasted.
    6003: ("cloudflare_token_rejected", "errors.cloudflare_token_rejected", 409),
    1061: ("cloudflare_zone_exists", "errors.cloudflare_zone_exists", 409),
    1049: ("cloudflare_zone_not_found", "errors.cloudflare_zone_not_found", 409),
    81053: ("cloudflare_record_exists", "errors.cloudflare_record_exists", 409),
    81057: ("cloudflare_record_exists", "errors.cloudflare_record_exists", 409),
}

#: Issue keys the status report can raise, resolved to ``cloudflare.issue.*`` by the client.
ISSUE_NO_ACCOUNT = "no_account"
ISSUE_NOT_CONNECTED = "not_connected"
ISSUE_DUPLICATE_ZONE = "duplicate_zone"
ISSUE_ZONE_PENDING = "zone_pending"
ISSUE_ZONE_PAUSED = "zone_paused"
ISSUE_NAMESERVERS = "nameservers_not_delegated"
ISSUE_REDIRECT_DRIFT = "redirect_drift"
ISSUE_REDIRECT_MISSING = "redirect_missing"
ISSUE_REDIRECT_UNPUSHED = "redirect_not_pushed"
ISSUE_REDIRECT_CONFLICT = "redirect_conflict"
#: A link schakl holds that the project no longer serves, and a hostname Cloudflare is still
#: waiting on. The second one is the failure worth naming — nothing resolves to the project's
#: ``pages.dev`` name, which reads as a Cloudflare problem and is DNS.
ISSUE_PAGES_MISSING = "pages_missing"
ISSUE_PAGES_PENDING = "pages_pending"
ISSUE_ORIGIN_MISSING = "origin_missing"
#: Its own key, not folded into ``origin_missing``: with the apex proxied and ``www`` not, traffic
#: *does* reach the redirect and "no traffic reaches it" would be a lie. The apex is fine, ``www``
#: serves nothing, and every other signal on the page reads healthy.
ISSUE_ORIGIN_WWW_MISSING = "origin_www_missing"
ISSUE_DOMAIN_SAYS_REDIRECT = "domain_says_redirect"
ISSUE_CLOUDFLARE_SAYS_REDIRECT = "cloudflare_says_redirect"
ISSUE_TOKEN_ERROR = "token_error"


@dataclass(frozen=True)
class DomainRow:
    """The columns of another module's ``domains`` row this module reads. A dataclass rather
    than the ORM model, because importing it would be importing that module's internals (§6)."""

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    status: str
    redirect_url: str | None
    nameservers: list[str]


def _norm_host(value: str | None) -> str:
    return (value or "").strip().lower().rstrip(".")


def _host_candidates(hostname: str) -> list[str]:
    """Every domain record a hostname could belong to, most specific first.

    ``www.shop.klant.nl`` is a hostname of ``shop.klant.nl`` when the tenant holds that as a
    domain of its own, and of ``klant.nl`` otherwise. Longest wins, so an agency holding both
    never gets the link filed under the wrong client — the same reason ``link_pages_project``
    refuses a hostname outside the domain it was called on.
    """
    labels = hostname.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


def _unavailable(report: dict[str, Any], probe: str) -> None:
    """Name a probe that could not run, once. The Pages refresh loops over projects, and three
    projects behind one unreadable token is still one thing the admin has to fix."""
    if probe not in report["unavailable"]:
        report["unavailable"].append(probe)


def _pages_error(row: dict[str, Any]) -> str | None:
    """Whatever Cloudflare put an error message in on a custom-domain row.

    The shape has moved between API versions (``validation_data`` and ``verification_data``
    both occur) and none of it is documented as stable, so every read is defensive: an
    unrecognised payload means *no error*, never an exception on a check that mostly worked.
    """
    for key in ("validation_data", "verification_data"):
        block = row.get(key)
        if isinstance(block, dict) and block.get("error_message"):
            return str(block["error_message"])[:500]
    return str(row["error_message"])[:500] if row.get("error_message") else None


@dataclass
class _RegistrarSyncCounts:
    """What one Registrar sync did, for the result envelope. Mutable and local — a few counters
    threaded through a loop, never a schema.

    ``read`` is not derivable from the counts and is the one that matters (#298): an account
    that holds no registrations and a token that may not read the register both report zero,
    and only the first of those is allowed to narrow what schakl invoices.
    """

    read: bool = False
    synced: int = 0
    at_cloudflare: int = 0
    matched: int = 0


@dataclass
class _PagesSyncCounts:
    """What one Pages sync did. Local counters, threaded through the loop — never a schema.

    ``truncated`` is the one that is not a count: an account whose projects had to be scanned
    one call at a time can hit the cap, and a sync that silently stopped looking would report
    "nothing changed" for the projects it never reached (§17's no-silent-caps rule).
    """

    projects: int = 0
    domains: int = 0
    matched: int = 0
    adopted: int = 0
    missing: int = 0
    truncated: bool = False


def _registrar_name(row: dict) -> str:
    """The registrable name out of a Registrar row, whichever field carries it.

    Cloudflare's own examples show the name under ``name``; the per-domain endpoint addresses a
    domain by name while the list rows also carry an opaque ``id``. Since this has never been
    seen against a live account, all three spellings are tried and an unrecognisable row is
    skipped by the caller rather than guessed at (``docs/CLOUDFLARE.md``).
    """
    for key in ("name", "domain_name", "domain"):
        value = row.get(key)
        if isinstance(value, str) and "." in value:
            return value
    return ""


def _is_cloudflare_registrar(value: object) -> bool:
    """Whether ``current_registrar`` reads as Cloudflare Registrar.

    Substring, case-insensitive, because the field is a display name ("Cloudflare",
    "Cloudflare, Inc.") rather than a slug. **This is the whole billing decision** (#298): a
    domain the client registered at their own registrar is in this list too, and reading mere
    membership as "we hold it" would invoice a client for a name we only serve DNS for.
    """
    return isinstance(value, str) and "cloudflare" in value.lower()


def _parse_cf_datetime(value: object) -> datetime | None:
    """An RFC 3339 instant as Cloudflare writes it, or ``None`` — never an exception. A
    malformed expiry must not fail a sync that otherwise worked."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _opt_bool(value: object) -> bool | None:
    """``None`` stays *not reported*, which is not the same as ``false`` — rendering an absent
    ``locked`` as "unlocked" would tell an agency a domain is transferable when nobody looked."""
    return bool(value) if isinstance(value, bool) else None


class CloudflareService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.accounts = ctx.repo(CloudflareAccount)
        self.zones = ctx.repo(CloudflareZone)
        self.redirects = ctx.repo(CloudflareRedirect)
        self.projects = ctx.repo(CloudflarePagesProject)
        self.registrar = ctx.repo(CloudflareRegistrarDomain)
        self.pages_links = ctx.repo(CloudflarePagesLink)
        self.activity = ActivityService(ctx)

    # ------------------------------------------------------------------ #
    # Cross-module bridge: domains
    # ------------------------------------------------------------------ #
    async def _domain_or_404(self, domain_id: uuid.UUID) -> DomainRow:
        """The domain, or 404 — **with the company horizon applied**.

        ``domains`` is another module's table, so this read cannot ride its repository. That is
        precisely §15's failure mode 3 (a hand-built cross-client read), so the predicate is
        written here, once, and every domain-addressed path in this module goes through this
        method rather than selecting the table itself.
        """
        conditions = [_domains.c.org_id == self.ctx.org.id, _domains.c.id == domain_id]
        if self.ctx.company_scope is not None:
            conditions.append(_domains.c.company_id.in_(self.ctx.company_scope))
        row = (
            await self.ctx.session.execute(
                select(
                    _domains.c.id,
                    _domains.c.company_id,
                    _domains.c.name,
                    _domains.c.status,
                    _domains.c.redirect_url,
                    _domains.c.nameservers,
                ).where(*conditions)
            )
        ).first()
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return DomainRow(
            id=row.id,
            company_id=row.company_id,
            name=_norm_host(row.name),
            status=row.status,
            redirect_url=row.redirect_url,
            nameservers=[_norm_host(ns) for ns in (row.nameservers or []) if ns],
        )

    async def _domain_names(self, domain_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Names for a batch of domains — one query, never one per row (docs/PERFORMANCE.md)."""
        if not domain_ids:
            return {}
        rows = await self.ctx.session.execute(
            select(_domains.c.id, _domains.c.name).where(
                _domains.c.org_id == self.ctx.org.id, _domains.c.id.in_(domain_ids)
            )
        )
        return {row.id: row.name for row in rows}

    async def _set_domain_redirect_state(
        self, domain: DomainRow, *, status: str, redirect_url: str | None
    ) -> None:
        """Keep the domain record honest about the redirect we just configured or removed.

        Setting a domain-wide redirect *is* the domain redirecting; leaving ``Domain.status`` on
        "active" would put two screens in disagreement about the same fact and make the drift
        report cry wolf forever. Both directions are recorded on the domain's own trail, so the
        change is attributable rather than mysterious.
        """
        if domain.status == status and (domain.redirect_url or None) == redirect_url:
            return
        await self.ctx.session.execute(
            update(_domains)
            .where(_domains.c.org_id == self.ctx.org.id, _domains.c.id == domain.id)
            .values(status=status, redirect_url=redirect_url)
        )
        await self.activity.record(
            DOMAIN_ENTITY,
            domain.id,
            "updated",
            {
                "changes": {
                    "status": {"from": domain.status, "to": status},
                    "redirect_url": {"from": domain.redirect_url, "to": redirect_url},
                }
            },
        )

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #
    async def list_accounts(self) -> list[dict[str, Any]]:
        """Every configured account, with its provider label and zone count.

        Two extra queries total, not two per row: an agency has a handful of accounts and the
        settings screen still must not N+1 (docs/PERFORMANCE.md).
        """
        accounts = list(
            (
                await self.ctx.session.execute(
                    self.accounts.scoped_select().order_by(func.lower(CloudflareAccount.name))
                )
            )
            .scalars()
            .all()
        )
        if not accounts:
            return []
        provider_ids = {a.provider_id for a in accounts if a.provider_id}
        provider_names: dict[uuid.UUID, str] = {}
        if provider_ids:
            rows = await self.ctx.session.execute(
                select(Provider.id, Provider.name).where(
                    Provider.org_id == self.ctx.org.id, Provider.id.in_(provider_ids)
                )
            )
            provider_names = {row.id: row.name for row in rows}
        counts = dict(
            (
                await self.ctx.session.execute(
                    select(CloudflareZone.account_id, func.count())
                    .where(CloudflareZone.org_id == self.ctx.org.id)
                    .group_by(CloudflareZone.account_id)
                )
            ).all()
        )
        return [
            {
                "id": a.id,
                "name": a.name,
                "cf_account_id": a.cf_account_id,
                "cf_account_name": a.cf_account_name,
                "provider_id": a.provider_id,
                "provider_name": provider_names.get(a.provider_id) if a.provider_id else None,
                "active": a.active,
                "status": a.status,
                "capabilities": a.capabilities or {},
                "last_verified_at": a.last_verified_at,
                "last_synced_at": a.last_synced_at,
                "last_error": a.last_error,
                "token_configured": bool(a.api_token_encrypted),
                "zone_count": int(counts.get(a.id, 0)),
            }
            for a in accounts
        ]

    async def account_options(self) -> list[dict[str, Any]]:
        """Just enough to choose an account from: id, name, whether it is in use."""
        accounts = list(
            (
                await self.ctx.session.execute(
                    self.accounts.scoped_select().order_by(func.lower(CloudflareAccount.name))
                )
            )
            .scalars()
            .all()
        )
        return [{"id": a.id, "name": a.name, "active": a.active} for a in accounts]

    async def create_account(self, payload: AccountCreate) -> CloudflareAccount:
        await self._assert_account_name_free(payload.name)
        account = await self.accounts.create(
            name=payload.name.strip(),
            api_token_encrypted=encrypt(payload.api_token.strip()),
            cf_account_id=(payload.cf_account_id or None),
            provider_id=await self._validated_provider(payload.provider_id),
            active=payload.active,
        )
        await self.activity.record_created(ACCOUNT_ENTITY, account.id, {"name": account.name})
        return account

    async def update_account(
        self, account_id: uuid.UUID, payload: AccountUpdate
    ) -> CloudflareAccount:
        account = await self.accounts.get_or_404(account_id)
        values: dict[str, Any] = {}
        changes: dict[str, dict[str, Any]] = {}
        if payload.name is not None and payload.name.strip() != account.name:
            await self._assert_account_name_free(payload.name, exclude=account.id)
            changes["name"] = {"from": account.name, "to": payload.name.strip()}
            values["name"] = payload.name.strip()
        if payload.cf_account_id is not None:
            new_id = payload.cf_account_id or None
            if new_id != account.cf_account_id:
                changes["cf_account_id"] = {"from": account.cf_account_id, "to": new_id}
                values["cf_account_id"] = new_id
        if payload.provider_id is not None:
            values["provider_id"] = await self._validated_provider(payload.provider_id)
        if payload.active is not None and payload.active != account.active:
            changes["active"] = {"from": account.active, "to": payload.active}
            values["active"] = payload.active
        rotated = False
        if payload.api_token:
            values["api_token_encrypted"] = encrypt(payload.api_token.strip())
            # A rotated token has not been verified yet; anything the old one learned about its
            # scopes is now a guess, and a stale "this token can create zones" is worse than an
            # empty one.
            values.update(
                capabilities={},
                last_verified_at=None,
                status=CloudflareAccountStatus.ACTIVE.value,
                last_error=None,
            )
            rotated = True
        if values:
            account = await self.accounts.update(account, **values)
        if changes:
            await self.activity.record(ACCOUNT_ENTITY, account.id, "updated", {"changes": changes})
        if rotated:
            # The token itself is never in the trail — only that it changed.
            await self.activity.record(ACCOUNT_ENTITY, account.id, "cloudflare.token_rotated")
        return account

    async def delete_account(self, account_id: uuid.UUID) -> None:
        """Forget an account. Zones, Pages projects and their links cascade **in schakl only** —
        nothing is touched at Cloudflare, because deleting a client's live zone as a side effect
        of tidying up a credential list would be catastrophic and unrecoverable."""
        account = await self.accounts.get_or_404(account_id)
        await self.activity.record(ACCOUNT_ENTITY, account.id, "cloudflare.account_removed",
                                   {"name": account.name})
        await self.accounts.delete(account)

    async def _assert_account_name_free(
        self, name: str, *, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = self.accounts.scoped_select().where(
            func.lower(CloudflareAccount.name) == name.strip().lower()
        )
        if exclude:
            stmt = stmt.where(CloudflareAccount.id != exclude)
        if (await self.ctx.session.execute(stmt.limit(1))).first() is not None:
            raise AppError(
                "conflict",
                "errors.conflict",
                status_code=409,
                fields={"name": "errors.duplicate"},
            )

    async def _validated_provider(self, provider_id: uuid.UUID | None) -> uuid.UUID | None:
        if provider_id is None:
            return None
        provider = await self.ctx.repo(Provider).get(provider_id)
        if provider is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"provider_id": "errors.not_found"},
            )
        return provider.id

    def _client(self, account: CloudflareAccount) -> CloudflareClient:
        try:
            token = decrypt(account.api_token_encrypted)
        except ValueError as exc:
            # A rotated ``SCHAKL_ENCRYPTION_KEY`` leaves an unreadable token. Say so plainly —
            # the fix is re-entering the token, not retrying.
            raise AppError(
                "cloudflare_token_unreadable",
                "errors.cloudflare_token_unreadable",
                status_code=409,
            ) from exc
        return CloudflareClient(token)

    def _translate(self, exc: CloudflareError) -> AppError:
        """Cloudflare's failure → the standard envelope (§9: ``message`` is an i18n key).

        Cloudflare's own text is never put in the envelope — it is not translatable and §9 does
        not allow it there. It is persisted on the row's ``last_error`` wherever the operation
        still commits (verify, sync, check), which is where a user can read it.
        """
        if isinstance(exc, CloudflareAuthError):
            return AppError(
                "cloudflare_token_rejected", "errors.cloudflare_token_rejected", status_code=409
            )
        mapped = _ERROR_CODES.get(exc.code or -1)
        if mapped:
            code, key, status = mapped
            return AppError(code, key, status_code=status)
        if exc.status is None:
            return AppError(
                "cloudflare_unreachable", "errors.cloudflare_unreachable", status_code=502
            )
        return AppError(
            "cloudflare_request_failed", "errors.cloudflare_request_failed", status_code=502
        )

    async def verify_account(self, account_id: uuid.UUID) -> AccountVerifyResult:
        """Probe what this token can do, and remember it.

        Never raises on a *scoped* token — "cannot list accounts" is a fact to report, not a
        failure. Only an invalid or unreachable token comes back with ``ok=False``, and the row
        records why so the settings screen can say it without a second call.
        """
        account = await self.accounts.get_or_404(account_id)
        client = self._client(account)
        try:
            # The pinned id is handed in because an account-owned token verifies at its own
            # account's endpoint and nowhere else (``client.verify_token``).
            capabilities, discovered = await client.probe_capabilities(account.cf_account_id)
        except CloudflareError as exc:
            await self.accounts.update(
                account,
                status=CloudflareAccountStatus.ERROR.value,
                capabilities={},
                last_error=str(exc)[:500],
                last_verified_at=datetime.now(UTC),
            )
            return AccountVerifyResult(ok=False, error=str(exc)[:500])

        choices: list[dict[str, str]] = []
        values: dict[str, Any] = {
            "capabilities": capabilities,
            "status": CloudflareAccountStatus.ACTIVE.value,
            "last_error": None,
            "last_verified_at": datetime.now(UTC),
        }
        if isinstance(discovered, dict) and "_multiple" in discovered:
            choices = [
                {"id": str(a.get("id", "")), "name": str(a.get("name", ""))}
                for a in discovered["_multiple"]
            ]
            # Several accounts behind one token: keep whatever the admin pinned, and let them
            # choose. Taking the first would silently create zones in the wrong account.
        elif isinstance(discovered, dict) and discovered.get("id"):
            values["cf_account_id"] = account.cf_account_id or str(discovered["id"])
            values["cf_account_name"] = str(discovered.get("name") or "") or None

        account = await self.accounts.update(account, **values)
        return AccountVerifyResult(
            ok=True,
            capabilities=capabilities,
            cf_account_id=account.cf_account_id,
            cf_account_name=account.cf_account_name,
            account_choices=choices,
        )

    async def sync_account(self, account_id: uuid.UUID) -> AccountSyncResult:
        """Pull this account's zones (and Pages projects) into schakl and match them to domains.

        Matching is by apex name, and it is **additive only**: a zone that no longer matches is
        never unlinked automatically, because the usual reason a match disappears is that
        somebody renamed the domain record, not that the client left.
        """
        account = await self.accounts.get_or_404(account_id)
        client = self._client(account)
        warnings: list[str] = []
        try:
            zones = await client.list_zones(account_id=account.cf_account_id)
        except CloudflareError as exc:
            # Written outside this transaction: the raise below rolls everything else back.
            await self._record_failure(account, exc)
            raise self._translate(exc) from exc

        now = datetime.now(UTC)
        existing = {
            z.cf_zone_id: z
            for z in (
                await self.ctx.session.execute(
                    self.zones.scoped_select().where(CloudflareZone.account_id == account.id)
                )
            )
            .scalars()
            .all()
        }
        # One lookup for every apex at once, so matching N zones costs one query, not N.
        names = {_norm_host(z.get("name")) for z in zones if z.get("name")}
        domain_by_name = await self._domains_by_name(names)
        # Domains some zone already claims — including zones in *other* accounts. Auto-matching
        # a second zone onto a claimed domain would be the guess this module refuses to make:
        # two zones for one domain makes "where does this domain live" answerable two ways, and
        # nothing downstream could tell which. The duplicate stays unlinked and the status
        # report names it (``duplicate_zone``).
        claimed = {
            row
            for row in (
                await self.ctx.session.execute(
                    select(CloudflareZone.domain_id).where(
                        CloudflareZone.org_id == self.ctx.org.id,
                        CloudflareZone.domain_id.is_not(None),
                    )
                )
            ).scalars()
        }

        matched = 0
        for row in zones:
            cf_zone_id = str(row.get("id") or "")
            name = _norm_host(row.get("name"))
            if not cf_zone_id or not name:
                continue
            domain_id = domain_by_name.get(name)
            if domain_id is not None and domain_id in claimed:
                domain_id = None
            values: dict[str, Any] = {
                "name": name,
                "status": str(row.get("status") or "pending"),
                "plan": str((row.get("plan") or {}).get("name") or "") or None,
                "paused": bool(row.get("paused")),
                "name_servers": [_norm_host(ns) for ns in row.get("name_servers") or []],
                "original_name_servers": [
                    _norm_host(ns) for ns in row.get("original_name_servers") or []
                ],
                "last_synced_at": now,
            }
            zone = existing.get(cf_zone_id)
            if zone is None:
                zone = await self.zones.create(
                    account_id=account.id, cf_zone_id=cf_zone_id, domain_id=domain_id, **values
                )
            else:
                # Only *fill* the link; never clear one an admin set by hand.
                if zone.domain_id is None and domain_id is not None:
                    values["domain_id"] = domain_id
                zone = await self.zones.update(zone, **values)
            if zone.domain_id is not None:
                claimed.add(zone.domain_id)
                matched += 1

        pages = _PagesSyncCounts()
        registrar = _RegistrarSyncCounts()
        if not account.cf_account_id:
            account = await self._resolve_account_id(account, client)
        if account.cf_account_id:
            try:
                pages = await self._sync_pages_projects(account, client, now)
            except CloudflareError as exc:
                # Pages is optional. A token without it still syncs zones, and saying so beats
                # failing an action that mostly worked.
                warnings.append(str(exc)[:200])
            try:
                registrar = await self._sync_registrar_domains(account, client, now)
            except CloudflareError as exc:
                # Registrar is optional in exactly the same way, and the failure matters more:
                # `registrar_synced_at` stays NULL, so this account never becomes an authority
                # over what schakl invoices (#298). An unread register narrows nothing.
                warnings.append(str(exc)[:200])
        else:
            warnings.append("no_account_id")

        if pages.truncated:
            warnings.append("pages_domains_truncated")
        # Cloudflare just answered this token's zone list, so whatever was wrong with it is not
        # wrong any more — clearing the text without clearing the status is what left the screen
        # reading "Token problem" over a sync that had plainly worked.
        await self.accounts.update(
            account,
            last_synced_at=now,
            last_error=None,
            status=CloudflareAccountStatus.ACTIVE.value,
        )
        return AccountSyncResult(
            zones_synced=len(zones),
            zones_matched=matched,
            pages_projects_synced=pages.projects,
            pages_domains_synced=pages.domains,
            pages_links_matched=pages.matched,
            pages_links_adopted=pages.adopted,
            pages_links_missing=pages.missing,
            registrar_read=registrar.read,
            registrar_domains_synced=registrar.synced,
            registrar_domains_at_cloudflare=registrar.at_cloudflare,
            registrar_domains_matched=registrar.matched,
            warnings=warnings,
        )

    async def _resolve_account_id(
        self, account: CloudflareAccount, client: CloudflareClient
    ) -> CloudflareAccount:
        """Fill in a missing ``cf_account_id`` from the token itself, when it is unambiguous.

        **Zones need no account id; Pages and Registrar are addressed by one.** That asymmetry
        is why a half-configured row looks entirely healthy: zones arrive, match domains and
        fill the screen, while the two halves that need an id are skipped — and skipped as a
        *zero*, which reads exactly like "this account has no Pages projects".

        Nothing used to fill it in but ``verify_account``, so any tenant whose verify had ever
        failed — every account-owned token, before ``client.verify_token`` learned the second
        endpoint — kept a NULL id and a permanently blank Pages panel over a Cloudflare account
        that was serving their sites. The sync holds a client and the answer is one call, so it
        asks rather than waiting to be told.

        Exactly one visible account is an answer; **several is not**, and this is the module's
        never-guess rule (§ file docstring) at its sharpest — picking one would silently point
        every later Pages call at the wrong client's account. Ambiguity leaves the id NULL, and
        the caller's ``no_account_id`` warning says so.
        """
        try:
            accounts = await client.list_accounts()
        except CloudflareError:
            # Not scoped to read accounts. Degraded, not broken: the zone half already worked.
            return account
        if len(accounts) != 1:
            return account
        discovered = str(accounts[0].get("id") or "")
        if not discovered:
            return account
        return await self.accounts.update(
            account,
            cf_account_id=discovered,
            cf_account_name=str(accounts[0].get("name") or "") or account.cf_account_name,
        )

    async def _domains_by_name(self, names: set[str]) -> dict[str, uuid.UUID]:
        """apex → domain id for this tenant, horizon-filtered. One query for the whole batch."""
        if not names:
            return {}
        conditions = [
            _domains.c.org_id == self.ctx.org.id,
            func.lower(_domains.c.name).in_(names),
        ]
        if self.ctx.company_scope is not None:
            conditions.append(_domains.c.company_id.in_(self.ctx.company_scope))
        rows = await self.ctx.session.execute(
            select(_domains.c.id, _domains.c.name).where(*conditions)
        )
        return {_norm_host(row.name): row.id for row in rows}

    async def _sync_registrar_domains(
        self, account: CloudflareAccount, client: CloudflareClient, now: datetime
    ) -> _RegistrarSyncCounts:
        """Pull Cloudflare Registrar's domain list and reconcile it (#298).

        Every field is read defensively, because this endpoint has never been exercised against
        a live Registrar account here (``docs/CLOUDFLARE.md`` §Registrar carries the checklist):
        a row whose name cannot be found at all is skipped rather than guessed at, since a
        registration attributed to the wrong name is worse than one nobody counted.

        The one judgement made is ``at_cloudflare``: the list also reports domains registered
        elsewhere, and only "Cloudflare is the registrar" is evidence the agency holds — and
        therefore renews and bills — the registration. Matching to domain records is by apex
        name, **additive only**, like the zone sync: a link an admin corrected by hand is never
        undone by a later run.
        """
        rows = await client.list_registrar_domains(account.cf_account_id or "")
        existing = {
            r.name: r
            for r in (
                await self.ctx.session.execute(
                    self.registrar.scoped_select().where(
                        CloudflareRegistrarDomain.account_id == account.id
                    )
                )
            )
            .scalars()
            .all()
        }
        names = {_norm_host(_registrar_name(row)) for row in rows}
        domain_by_name = await self._domains_by_name({n for n in names if n})

        counts = _RegistrarSyncCounts()
        seen: set[str] = set()
        for row in rows:
            name = _norm_host(_registrar_name(row))
            if not name:
                continue
            seen.add(name)
            counts.synced += 1
            at_cloudflare = _is_cloudflare_registrar(row.get("current_registrar"))
            if at_cloudflare:
                counts.at_cloudflare += 1
            values: dict[str, Any] = {
                "cf_registrar_id": str(row.get("id") or "") or None,
                "current_registrar": str(row.get("current_registrar") or "") or None,
                "at_cloudflare": at_cloudflare,
                "expires_at": _parse_cf_datetime(row.get("expires_at")),
                "auto_renew": _opt_bool(row.get("auto_renew")),
                "locked": _opt_bool(row.get("locked")),
                "registry_statuses": str(row.get("registry_statuses") or "")[:255] or None,
                "last_synced_at": now,
            }
            domain_id = domain_by_name.get(name)
            record = existing.get(name)
            if record is None:
                record = await self.registrar.create(
                    account_id=account.id, name=name, domain_id=domain_id, **values
                )
            else:
                if record.domain_id is None and domain_id is not None:
                    values["domain_id"] = domain_id
                record = await self.registrar.update(record, **values)
            if record.domain_id is not None and record.at_cloudflare:
                counts.matched += 1

        # A registration that left the list (transferred out) keeps its row but stops claiming
        # to be ours — the OXXA rule, and the reason the flag is stored rather than derived.
        for name, record in existing.items():
            if name not in seen and record.at_cloudflare:
                await self.registrar.update(record, at_cloudflare=False, last_synced_at=now)

        # Only now is this account an authority on who holds a registration.
        await self.accounts.update(account, registrar_synced_at=now)
        counts.read = True
        return counts

    async def _sync_pages_projects(
        self, account: CloudflareAccount, client: CloudflareClient, now: datetime
    ) -> _PagesSyncCounts:
        """Pull the account's Pages projects **and the hostnames they serve**.

        The projects half feeds the picker. The hostnames half is what lets an agency's
        existing Cloudflare import itself: a placeholder somebody attached to a project in
        Cloudflare's own dashboard months ago becomes a link on that domain's page here,
        without anyone pressing a button that would re-register what is already registered.

        Reading is the whole of it. Nothing is created, moved or deleted at Cloudflare by a
        sync, and a hostname belonging to no domain record here is counted and left alone.
        """
        projects = await client.list_pages_projects(account.cf_account_id or "")
        counts = _PagesSyncCounts(projects=len(projects))
        existing = {
            p.name: p
            for p in (
                await self.ctx.session.execute(
                    self.projects.scoped_select().where(
                        CloudflarePagesProject.account_id == account.id
                    )
                )
            )
            .scalars()
            .all()
        }
        observed: dict[uuid.UUID, set[str]] = {}
        scans = 0
        for row in projects:
            name = str(row.get("name") or "")
            if not name:
                continue
            values = {
                "subdomain": str(row.get("subdomain") or "") or None,
                "production_branch": str(row.get("production_branch") or "") or None,
                "last_synced_at": now,
            }
            project = existing.get(name)
            project = (
                await self.projects.create(account_id=account.id, name=name, **values)
                if project is None
                else await self.projects.update(project, **values)
            )

            # Cloudflare puts a project's custom domains on the project object, so the normal
            # path costs no extra call. A payload without the key at all (an older shape, a
            # trimmed response) falls back to the per-project endpoint; an *empty list* is a
            # real answer — a project serving nothing — and must not trigger a second look.
            hosts = row.get("domains")
            if isinstance(hosts, list):
                names = {_norm_host(h) for h in hosts if isinstance(h, str)}
            elif scans >= PAGES_DOMAIN_SCAN_LIMIT:
                counts.truncated = True
                continue
            else:
                scans += 1
                names = {
                    _norm_host(d.get("name"))
                    for d in await client.list_pages_domains(
                        account.cf_account_id or "", name
                    )
                }
            observed[project.id] = {h for h in names if h}

        counts.domains = sum(len(hosts) for hosts in observed.values())
        await self._reconcile_pages_links(observed, now, counts)
        return counts

    async def _reconcile_pages_links(
        self, observed: dict[uuid.UUID, set[str]], now: datetime, counts: _PagesSyncCounts
    ) -> None:
        """Make the stored links agree with what the scanned projects actually serve.

        Three outcomes, and only two of them touch a row. A hostname Cloudflare holds that we
        can file under a domain is **adopted**; a link whose project no longer serves it is
        **marked missing**, never deleted — the row is the only record that the hostname was
        ever ours, and one unreadable probe must not be able to erase it. A hostname matching
        no domain record here is counted and left alone: inventing a domain row would file a
        name under a client who never asked for it.

        Only projects present in ``observed`` are judged. A project the scan could not reach
        keeps every link it has, because "we did not look" and "it is gone" are different
        answers and only one of them is this method's to give.
        """
        if not observed:
            return
        links = list(
            (
                await self.ctx.session.execute(
                    self.pages_links.scoped_select().where(
                        CloudflarePagesLink.project_id.in_(list(observed))
                    )
                )
            )
            .scalars()
            .all()
        )
        known = {(link.project_id, link.hostname) for link in links}

        # Every unknown hostname on every scanned project resolves in one batched lookup:
        # each name's candidate apexes go into a single query, never one query per hostname.
        unknown = [
            (project_id, host)
            for project_id, hosts in observed.items()
            for host in hosts
            if (project_id, host) not in known
        ]
        by_name = await self._domains_by_name(
            {candidate for _, host in unknown for candidate in _host_candidates(host)}
        )
        for project_id, host in unknown:
            domain_id = next(
                (by_name[c] for c in _host_candidates(host) if c in by_name), None
            )
            if domain_id is None:
                continue
            await self.pages_links.create(
                project_id=project_id,
                domain_id=domain_id,
                hostname=host,
                last_checked_at=now,
                discovered_at=now,
            )
            counts.adopted += 1

        for link in links:
            present = link.hostname in observed[link.project_id]
            await self.pages_links.update(
                link,
                last_checked_at=now,
                # Keep the *first* time it went missing: "since when" is the question, and
                # restamping it every sync would answer "just now" forever.
                missing_at=None if present else (link.missing_at or now),
            )
            if present:
                counts.matched += 1
            else:
                counts.missing += 1
        counts.matched += counts.adopted

    # ------------------------------------------------------------------ #
    # Account resolution
    # ------------------------------------------------------------------ #
    async def _active_accounts(self) -> list[CloudflareAccount]:
        return list(
            (
                await self.ctx.session.execute(
                    self.accounts.scoped_select()
                    .where(CloudflareAccount.active.is_(True))
                    .order_by(func.lower(CloudflareAccount.name))
                )
            )
            .scalars()
            .all()
        )

    async def _resolve_account(self, account_id: uuid.UUID | None) -> CloudflareAccount:
        """The account to act through: the one named, or the only active one.

        Refuses to pick when there are several. See the module docstring — a zone created in the
        wrong Cloudflare account cannot be moved, only deleted and rebuilt.
        """
        if account_id is not None:
            account = await self.accounts.get_or_404(account_id)
            if not account.active:
                raise AppError(
                    "cloudflare_account_inactive",
                    "errors.cloudflare_account_inactive",
                    status_code=409,
                )
            return account
        accounts = await self._active_accounts()
        if not accounts:
            raise AppError(
                "cloudflare_no_account", "errors.cloudflare_no_account", status_code=409
            )
        if len(accounts) > 1:
            raise AppError(
                "cloudflare_account_ambiguous",
                "errors.cloudflare_account_ambiguous",
                status_code=409,
                fields={"account_id": "errors.required"},
            )
        return accounts[0]

    # ------------------------------------------------------------------ #
    # Zones
    # ------------------------------------------------------------------ #
    async def list_zones(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        account_id: uuid.UUID | None = None,
        domain_id: uuid.UUID | None = None,
        linked: bool | None = None,
        q: str | None = None,
        count: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        if account_id is not None:
            conditions.append(CloudflareZone.account_id == account_id)
        if domain_id is not None:
            conditions.append(CloudflareZone.domain_id == domain_id)
        if linked is True:
            conditions.append(CloudflareZone.domain_id.is_not(None))
        elif linked is False:
            conditions.append(CloudflareZone.domain_id.is_(None))
        if q:
            conditions.append(CloudflareZone.name.ilike(f"%{q.strip()}%"))

        stmt = (
            self.zones.scoped_select()
            .where(*conditions)
            .order_by(func.lower(CloudflareZone.name))
            .limit(limit)
            .offset(offset)
        )
        zones = list((await self.ctx.session.execute(stmt)).scalars().all())
        total = len(zones)
        if count:
            total = int(
                await self.ctx.session.scalar(self.zones.scoped_count_select().where(*conditions))
                or 0
            )
        return await self._decorate_zones(zones), total

    async def _decorate_zones(self, zones: list[CloudflareZone]) -> list[dict[str, Any]]:
        """Attach the account and domain labels — two batched queries, never one per row."""
        if not zones:
            return []
        account_names = dict(
            (
                await self.ctx.session.execute(
                    select(CloudflareAccount.id, CloudflareAccount.name).where(
                        CloudflareAccount.org_id == self.ctx.org.id,
                        CloudflareAccount.id.in_({z.account_id for z in zones}),
                    )
                )
            ).all()
        )
        domain_names = await self._domain_names({z.domain_id for z in zones if z.domain_id})
        return [
            {
                "id": z.id,
                "account_id": z.account_id,
                "account_name": account_names.get(z.account_id),
                "cf_zone_id": z.cf_zone_id,
                "name": z.name,
                "status": z.status,
                "plan": z.plan,
                "paused": z.paused,
                "name_servers": z.name_servers or [],
                "original_name_servers": z.original_name_servers or [],
                "domain_id": z.domain_id,
                "domain_name": domain_names.get(z.domain_id) if z.domain_id else None,
                "last_synced_at": z.last_synced_at,
            }
            for z in zones
        ]

    async def zone_row(self, zone: CloudflareZone) -> dict[str, Any]:
        """One zone with its labels — what every zone-returning endpoint answers with."""
        return (await self._decorate_zones([zone]))[0]

    async def link_zone(self, zone_id: uuid.UUID, domain_id: uuid.UUID) -> CloudflareZone:
        """Point a synced zone at a schakl domain by hand, when the name match did not.

        The apexes are allowed to differ (a client whose domain record carries a typo, or a zone
        deliberately serving another name) — but a domain already holding a different zone is a
        409, because two zones for one domain makes every "where does this domain live" answer
        ambiguous.
        """
        zone = await self.zones.get_or_404(zone_id)
        domain = await self._domain_or_404(domain_id)
        clash = (
            await self.ctx.session.execute(
                self.zones.scoped_select()
                .where(CloudflareZone.domain_id == domain.id, CloudflareZone.id != zone.id)
                .limit(1)
            )
        ).first()
        if clash is not None:
            raise AppError(
                "cloudflare_domain_already_linked",
                "errors.cloudflare_domain_already_linked",
                status_code=409,
            )
        zone = await self.zones.update(zone, domain_id=domain.id)
        await self.activity.record(
            DOMAIN_ENTITY, domain.id, "cloudflare.zone_linked", {"zone": zone.name}
        )
        return zone

    async def unlink_zone(self, zone_id: uuid.UUID) -> CloudflareZone:
        """Forget the link. Nothing at Cloudflare changes — the zone keeps serving."""
        zone = await self.zones.get_or_404(zone_id)
        if zone.domain_id is not None:
            await self.activity.record(
                DOMAIN_ENTITY, zone.domain_id, "cloudflare.zone_unlinked", {"zone": zone.name}
            )
        return await self.zones.update(zone, domain_id=None)

    async def _zone_for_domain(self, domain_id: uuid.UUID) -> CloudflareZone | None:
        return (
            await self.ctx.session.execute(
                self.zones.scoped_select().where(CloudflareZone.domain_id == domain_id).limit(1)
            )
        ).scalar_one_or_none()

    async def _zone_or_409(self, domain: DomainRow) -> CloudflareZone:
        zone = await self._zone_for_domain(domain.id)
        if zone is None:
            raise AppError(
                "cloudflare_not_connected", "errors.cloudflare_not_connected", status_code=409
            )
        return zone

    # ------------------------------------------------------------------ #
    # Connect (adopt or create)
    # ------------------------------------------------------------------ #
    async def connect_domain(
        self, domain_id: uuid.UUID, payload: ConnectRequest
    ) -> dict[str, Any]:
        """Attach this domain to a Cloudflare zone: adopt the existing one, else create it.

        Adoption comes first and always. An agency onboarding a client whose site already runs on
        Cloudflare must not end up with a second, pending zone for the same apex — that is a
        state Cloudflare permits and nothing good comes of it.

        The failure path #278 asks to make explicit: **the zone is the durable half.** Creating
        it at Cloudflare and then failing to record it locally would strand a zone nobody can
        find, so the local row is written in the same request and the *nameserver push* (the
        registrar half, split out with the OXXA module) is deliberately a separate, retryable
        step reading ``zone.name_servers``. Nothing here is half-applied: either the zone row
        exists with its nameservers, or the whole request rolled back and a retry re-adopts the
        zone Cloudflare kept.
        """
        domain = await self._domain_or_404(domain_id)
        existing = await self._zone_for_domain(domain.id)
        if existing is not None:
            return {"zone": existing, "created": False, "adopted": False}

        candidates = await self._zone_candidates(domain.name)
        if len(candidates) > 1 and payload.account_id is None:
            # Same apex in two of the tenant's accounts. Which one is live is not something to
            # infer from a status field that can be stale.
            raise AppError(
                "cloudflare_zone_ambiguous",
                "errors.cloudflare_zone_ambiguous",
                status_code=409,
                fields={"account_id": "errors.required"},
            )
        account = await self._resolve_account(
            payload.account_id or (candidates[0].account_id if candidates else None)
        )

        client = self._client(account)
        try:
            remote = await client.find_zone(domain.name)
            created = False
            if remote is None:
                if not payload.create_if_missing:
                    raise AppError(
                        "cloudflare_zone_not_found",
                        "errors.cloudflare_zone_not_found",
                        status_code=409,
                    )
                if not account.cf_account_id:
                    # A zone-scoped token can read and edit, but creating a zone is an
                    # account-level act. Say which permission is missing, not "it failed".
                    raise AppError(
                        "cloudflare_cannot_create_zone",
                        "errors.cloudflare_cannot_create_zone",
                        status_code=409,
                    )
                remote = await client.create_zone(domain.name, account.cf_account_id)
                created = True
        except CloudflareError as exc:
            raise self._translate(exc) from exc

        zone = await self._upsert_zone(account, remote, domain.id)
        await self.activity.record(
            DOMAIN_ENTITY,
            domain.id,
            "cloudflare.zone_created" if created else "cloudflare.zone_connected",
            {"zone": zone.name, "account": account.name, "nameservers": zone.name_servers or []},
        )
        return {"zone": zone, "created": created, "adopted": not created}

    async def _upsert_zone(
        self, account: CloudflareAccount, remote: dict[str, Any], domain_id: uuid.UUID | None
    ) -> CloudflareZone:
        cf_zone_id = str(remote.get("id") or "")
        values = {
            "name": _norm_host(remote.get("name")),
            "status": str(remote.get("status") or "pending"),
            "plan": str((remote.get("plan") or {}).get("name") or "") or None,
            "paused": bool(remote.get("paused")),
            "name_servers": [_norm_host(ns) for ns in remote.get("name_servers") or []],
            "original_name_servers": [
                _norm_host(ns) for ns in remote.get("original_name_servers") or []
            ],
            "last_synced_at": datetime.now(UTC),
        }
        zone = (
            await self.ctx.session.execute(
                self.zones.scoped_select().where(CloudflareZone.cf_zone_id == cf_zone_id).limit(1)
            )
        ).scalar_one_or_none()
        if zone is None:
            return await self.zones.create(
                account_id=account.id, cf_zone_id=cf_zone_id, domain_id=domain_id, **values
            )
        if domain_id is not None:
            values["domain_id"] = domain_id
        return await self.zones.update(zone, **values)

    async def _zone_candidates(self, apex: str) -> list[CloudflareZone]:
        """Every synced zone in this tenant matching an apex, across all accounts.

        Plural on purpose. Cloudflare lets the same name exist in several accounts as long as
        only one is *active*, and an agency that has both its own and the client's account
        genuinely hits this.
        """
        return list(
            (
                await self.ctx.session.execute(
                    self.zones.scoped_select().where(CloudflareZone.name == apex)
                )
            )
            .scalars()
            .all()
        )

    # ------------------------------------------------------------------ #
    # DNS
    # ------------------------------------------------------------------ #
    async def _account_for_zone(self, zone: CloudflareZone) -> CloudflareAccount:
        return await self.accounts.get_or_404(zone.account_id)

    async def list_dns(self, zone_id: uuid.UUID) -> dict[str, Any]:
        zone = await self.zones.get_or_404(zone_id)
        account = await self._account_for_zone(zone)
        try:
            records = await self._client(account).list_dns_records(zone.cf_zone_id)
        except CloudflareError as exc:
            raise self._translate(exc) from exc
        return {
            "zone_id": zone.id,
            "zone_name": zone.name,
            "records": [self._record(r) for r in records],
        }

    @staticmethod
    def _record(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row.get("id") or ""),
            "type": str(row.get("type") or ""),
            "name": str(row.get("name") or ""),
            "content": str(row.get("content") or ""),
            "ttl": int(row.get("ttl") or 1),
            "proxied": bool(row.get("proxied")),
            "priority": row.get("priority"),
            "comment": row.get("comment"),
        }

    async def export_dns(self, zone_id: uuid.UUID, fmt: str) -> dict[str, str]:
        """A zone as a BIND file or a CSV.

        BIND comes from Cloudflare's own export endpoint, so it round-trips into any other DNS
        host. The CSV is built here because Cloudflare has no CSV export and a spreadsheet is
        what an agency actually hands a client (§17's "everything a tenant can list, they can
        take out").
        """
        zone = await self.zones.get_or_404(zone_id)
        account = await self._account_for_zone(zone)
        client = self._client(account)
        try:
            if fmt == "bind":
                content = await client.export_dns(zone.cf_zone_id)
                return {
                    "filename": f"{zone.name}.zone",
                    "content_type": "text/plain; charset=utf-8",
                    "content": content,
                }
            records = await client.list_dns_records(zone.cf_zone_id)
        except CloudflareError as exc:
            raise self._translate(exc) from exc
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["type", "name", "content", "ttl", "proxied", "priority", "comment"])
        for row in records:
            record = self._record(row)
            writer.writerow(
                [
                    record["type"],
                    record["name"],
                    record["content"],
                    record["ttl"],
                    "true" if record["proxied"] else "false",
                    record["priority"] if record["priority"] is not None else "",
                    record["comment"] or "",
                ]
            )
        return {
            "filename": f"{zone.name}-dns.csv",
            "content_type": "text/csv; charset=utf-8",
            "content": buffer.getvalue(),
        }

    async def create_dns_record(
        self, zone_id: uuid.UUID, payload: DnsRecordWrite
    ) -> dict[str, Any]:
        zone = await self.zones.get_or_404(zone_id)
        account = await self._account_for_zone(zone)
        body = self._record_body(payload)
        try:
            created = await self._client(account).create_dns_record(zone.cf_zone_id, body)
        except CloudflareError as exc:
            raise self._translate(exc) from exc
        await self._record_dns_activity(zone, "cloudflare.dns_record_created", payload)
        return self._record(created)

    async def update_dns_record(
        self, zone_id: uuid.UUID, record_id: str, payload: DnsRecordWrite
    ) -> dict[str, Any]:
        zone = await self.zones.get_or_404(zone_id)
        account = await self._account_for_zone(zone)
        try:
            updated = await self._client(account).update_dns_record(
                zone.cf_zone_id, record_id, self._record_body(payload)
            )
        except CloudflareError as exc:
            raise self._translate(exc) from exc
        await self._record_dns_activity(zone, "cloudflare.dns_record_updated", payload)
        return self._record(updated)

    async def delete_dns_record(self, zone_id: uuid.UUID, record_id: str) -> None:
        zone = await self.zones.get_or_404(zone_id)
        account = await self._account_for_zone(zone)
        try:
            await self._client(account).delete_dns_record(zone.cf_zone_id, record_id)
        except CloudflareError as exc:
            raise self._translate(exc) from exc
        if zone.domain_id is not None:
            await self.activity.record(
                DOMAIN_ENTITY,
                zone.domain_id,
                "cloudflare.dns_record_deleted",
                {"zone": zone.name, "record_id": record_id},
            )

    @staticmethod
    def _record_body(payload: DnsRecordWrite) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": payload.type.upper(),
            "name": payload.name,
            "content": payload.content,
            "ttl": payload.ttl,
        }
        if payload.type.upper() in PROXIABLE_TYPES:
            body["proxied"] = payload.proxied
        if payload.priority is not None:
            body["priority"] = payload.priority
        if payload.comment:
            body["comment"] = payload.comment
        return body

    async def _record_dns_activity(
        self, zone: CloudflareZone, action: str, payload: DnsRecordWrite
    ) -> None:
        if zone.domain_id is None:
            return
        await self.activity.record(
            DOMAIN_ENTITY,
            zone.domain_id,
            action,
            {"zone": zone.name, "type": payload.type.upper(), "name": payload.name},
        )

    # ------------------------------------------------------------------ #
    # Redirects
    # ------------------------------------------------------------------ #
    async def set_redirect(
        self, domain_id: uuid.UUID, payload: RedirectWrite
    ) -> CloudflareRedirect:
        """Create or update the domain-wide redirect, and push it to Cloudflare.

        Everything that can be refused is refused *before* the first Cloudflare call, so a
        rejected request leaves no half-built rule behind.
        """
        domain = await self._domain_or_404(domain_id)
        zone = await self._zone_or_409(domain)
        target = payload.target_url.strip()
        reject_dangerous_url(target, field="target_url")
        if not target.lower().startswith(("http://", "https://")):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"target_url": "errors.cloudflare_target_absolute"},
            )
        if rules.redirect_loop_target(
            apex=zone.name, target_url=target, include_subdomains=payload.include_subdomains
        ):
            # Cloudflare saves this happily; the browser reports ERR_TOO_MANY_REDIRECTS and the
            # client's site is down until someone notices.
            raise AppError(
                "cloudflare_redirect_loop",
                "errors.cloudflare_redirect_loop",
                status_code=422,
                fields={"target_url": "errors.cloudflare_redirect_loop"},
            )

        account = await self._account_for_zone(zone)
        client = self._client(account)
        rule = rules.build_rule(
            apex=zone.name,
            target_url=target,
            status_code=payload.status_code,
            preserve_path=payload.preserve_path,
            preserve_query=payload.preserve_query,
            include_subdomains=payload.include_subdomains,
        )

        row = (
            await self.ctx.session.execute(
                self.redirects.scoped_select()
                .where(CloudflareRedirect.zone_id == zone.id)
                .limit(1)
            )
        ).scalar_one_or_none()

        ruleset_id = ""
        try:
            ruleset = await client.get_redirect_ruleset(zone.cf_zone_id)
            live = rules.find_our_rule(ruleset, row.cf_rule_id if row else None)
            if ruleset is None:
                created = await client.create_redirect_ruleset(zone.cf_zone_id, rule)
                ruleset_id = str(created.get("id") or "")
                pushed = (created.get("rules") or [{}])[0]
            elif live is not None:
                ruleset_id = str(ruleset.get("id") or "")
                result = await client.update_redirect_rule(
                    zone.cf_zone_id, ruleset_id, str(live.get("id")), rule
                )
                pushed = rules.find_our_rule(result, str(live.get("id"))) or live
            else:
                # Either we never pushed, or somebody deleted our rule at Cloudflare. Appending
                # is right in both cases, and it leaves the tenant's own rules untouched — which
                # a PUT of the whole entrypoint would not.
                ruleset_id = str(ruleset.get("id") or "")
                result = await client.add_redirect_rule(zone.cf_zone_id, ruleset_id, rule)
                pushed = (result.get("rules") or [{}])[-1]
            if payload.ensure_origin:
                await self._ensure_origin(client, zone, payload.include_subdomains)
        except CloudflareError as exc:
            raise self._translate(exc) from exc

        now = datetime.now(UTC)
        values = {
            "target_url": target,
            "status_code": payload.status_code,
            "preserve_path": payload.preserve_path,
            "preserve_query": payload.preserve_query,
            "include_subdomains": payload.include_subdomains,
            "cf_ruleset_id": ruleset_id or None,
            "cf_rule_id": str(pushed.get("id") or "") or None,
            "last_status": RedirectStatus.ACTIVE.value,
            "last_error": None,
            "last_checked_at": now,
            "last_pushed_at": now,
        }
        if row is None:
            row = await self.redirects.create(zone_id=zone.id, domain_id=domain.id, **values)
            action = "cloudflare.redirect_set"
        else:
            row = await self.redirects.update(row, **values)
            action = "cloudflare.redirect_updated"

        await self.activity.record(
            DOMAIN_ENTITY,
            domain.id,
            action,
            {"target": target, "status_code": payload.status_code, "zone": zone.name},
        )
        await self._set_domain_redirect_state(domain, status="redirect", redirect_url=target)
        return row

    async def remove_redirect(self, domain_id: uuid.UUID) -> None:
        """Delete our redirect rule at Cloudflare and forget it here.

        Only ever deletes the rule whose id we stored. A rule we never created stays, whatever it
        is called — the tenant's redirect rules are theirs.
        """
        domain = await self._domain_or_404(domain_id)
        zone = await self._zone_or_409(domain)
        row = (
            await self.ctx.session.execute(
                self.redirects.scoped_select()
                .where(CloudflareRedirect.zone_id == zone.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)

        if row.cf_ruleset_id and row.cf_rule_id:
            account = await self._account_for_zone(zone)
            try:
                await self._client(account).delete_redirect_rule(
                    zone.cf_zone_id, row.cf_ruleset_id, row.cf_rule_id
                )
            except CloudflareError as exc:
                # A rule already gone at Cloudflare is the state we wanted; anything else stops
                # us, so the local row never claims a removal that did not happen.
                if exc.status != 404:
                    raise self._translate(exc) from exc

        target = row.target_url
        await self.redirects.delete(row)
        await self.activity.record(
            DOMAIN_ENTITY, domain.id, "cloudflare.redirect_removed", {"target": target}
        )
        # Only walk the domain back when it still says what we put there; a status somebody has
        # since changed by hand is theirs, not ours to revert.
        if domain.status == "redirect" and _norm_host(domain.redirect_url) == _norm_host(target):
            await self._set_domain_redirect_state(domain, status="active", redirect_url=None)

    async def _ensure_origin(
        self, client: CloudflareClient, zone: CloudflareZone, include_subdomains: bool
    ) -> list[str]:
        """Make sure traffic for this zone actually reaches Cloudflare's edge.

        A Redirect Rule acts on requests Cloudflare receives. A zone whose apex has no *proxied*
        record receives none: the rule saves, the dashboard shows it as active, and nothing
        happens — by a distance the most confusing failure mode this feature has. So when there
        is no proxied record for the hostnames the rule matches, add Cloudflare's own documented
        placeholder (a proxied ``AAAA 100::``) rather than leaving an inert rule behind.

        Never *replaces* anything. A hostname that already has a proxied record is left alone,
        including a grey-clouded one the tenant may have greyed on purpose — that case is
        reported by the status check instead.
        """
        records = await client.list_dns_records(zone.cf_zone_id)
        proxied = {
            _norm_host(r.get("name"))
            for r in records
            if r.get("proxied") and str(r.get("type") or "").upper() in PROXIABLE_TYPES
        }
        wanted = [zone.name] + ([f"www.{zone.name}"] if include_subdomains else [])
        created: list[str] = []
        for host in wanted:
            if host in proxied:
                continue
            await client.create_dns_record(
                zone.cf_zone_id,
                {
                    "type": ORIGIN_PLACEHOLDER_TYPE,
                    "name": host,
                    "content": ORIGIN_PLACEHOLDER_CONTENT,
                    "ttl": 1,
                    "proxied": True,
                    "comment": "schakl: redirect placeholder",
                },
            )
            created.append(host)
        if created and zone.domain_id is not None:
            await self.activity.record(
                DOMAIN_ENTITY,
                zone.domain_id,
                "cloudflare.origin_created",
                {"hosts": ", ".join(created)},
            )
        return created

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    async def domain_status(self, domain_id: uuid.UUID, *, live: bool) -> dict[str, Any]:
        """Everything known about one domain's Cloudflare state.

        ``live=False`` is the page-load read: stored rows only, no outside call, so a detail page
        renders at full speed and stays up when Cloudflare does not (docs/PERFORMANCE.md).
        ``live=True`` is the explicit "check at Cloudflare" action, which is the only path that
        fills in conflicts, the origin state and the redirect observation — and the only one that
        can answer *"it already redirects, but not through us"*.
        """
        domain = await self._domain_or_404(domain_id)
        zone = await self._zone_for_domain(domain.id)
        candidates = await self._zone_candidates(domain.name)
        redirect = None
        if zone is not None:
            redirect = (
                await self.ctx.session.execute(
                    self.redirects.scoped_select()
                    .where(CloudflareRedirect.zone_id == zone.id)
                    .limit(1)
                )
            ).scalar_one_or_none()

        report: dict[str, Any] = {
            "domain_id": domain.id,
            "domain_name": domain.name,
            "live": live,
            "checked_at": None,
            "zone": (await self._decorate_zones([zone]))[0] if zone is not None else None,
            "candidates": await self._candidate_refs(candidates),
            "expected_nameservers": (zone.name_servers or []) if zone else [],
            "observed_nameservers": domain.nameservers,
            "nameservers_delegated": False,
            "redirect": redirect,
            "redirect_live": None,
            "conflicts": [],
            "origin": None,
            "pages_links": await self._pages_links_for(domain.id),
            "domain_status": domain.status,
            "domain_redirect_url": domain.redirect_url,
            "issues": [],
            "unavailable": [],
        }
        expected = set(report["expected_nameservers"])
        observed = set(domain.nameservers)
        report["nameservers_delegated"] = bool(expected) and bool(observed & expected)

        if live:
            if zone is not None:
                await self._probe_live(report, zone, redirect)
            # Outside the zone branch on purpose, for the reason the panel draws Pages outside
            # it (docs/CLOUDFLARE.md §6): a custom hostname is registered on a *project*, which
            # names its own account. Inside, the one domain whose DNS lives elsewhere — exactly
            # the domain an agency serves from Pages — could never refresh at all.
            await self._refresh_pages_links(report)

        report["issues"] = self._issues(report, domain, zone, redirect)
        report["checked_at"] = self._last_observed(report)
        return report

    @staticmethod
    def _last_observed(report: dict[str, Any]) -> datetime | None:
        """How old the answer on this report is: the newest of the facts it is built from.

        Taken from the rows themselves rather than stamped when a check runs, because a probe
        that failed leaves its row's timestamp alone (``_probe_live`` fails softly and
        separately) — and a "checked just now" over a report nothing could be read for is the
        one thing this must never say. Every branch of the panel has a check button, including
        the Pages-only one that has no zone at all, so it is one number covering all of them.
        """
        zone = report.get("zone") or {}
        seen = [
            zone.get("last_synced_at"),
            getattr(report.get("redirect"), "last_checked_at", None),
            *(link.get("last_checked_at") for link in report["pages_links"]),
        ]
        return max((at for at in seen if at is not None), default=None)

    async def _candidate_refs(self, zones: list[CloudflareZone]) -> list[ZoneCandidate]:
        if not zones:
            return []
        names = dict(
            (
                await self.ctx.session.execute(
                    select(CloudflareAccount.id, CloudflareAccount.name).where(
                        CloudflareAccount.org_id == self.ctx.org.id,
                        CloudflareAccount.id.in_({z.account_id for z in zones}),
                    )
                )
            ).all()
        )
        return [
            ZoneCandidate(
                account_id=z.account_id,
                account_name=names.get(z.account_id, ""),
                cf_zone_id=z.cf_zone_id,
                status=z.status,
                name_servers=z.name_servers or [],
            )
            for z in zones
        ]

    async def _probe_live(
        self,
        report: dict[str, Any],
        zone: CloudflareZone,
        redirect: CloudflareRedirect | None,
    ) -> None:
        """Ask Cloudflare what it actually has. Every probe fails **softly and separately**.

        A token scoped to DNS but not Page Rules must still produce a usable report; losing the
        whole screen because one optional probe 403'd would push an admin to mint a wider token
        than they need. What could not be read is named in ``unavailable`` — an incomplete report
        that says so beats a complete-looking one that isn't.
        """
        account = await self._account_for_zone(zone)
        client = self._client(account)
        now = datetime.now(UTC)

        try:
            remote = await client.get_zone(zone.cf_zone_id)
        except CloudflareError as exc:
            report["unavailable"].append("zone")
            await self._flag_account(account, exc)
        else:
            await self._clear_account_error(account)
            zone = await self.zones.update(
                zone,
                status=str(remote.get("status") or zone.status),
                paused=bool(remote.get("paused")),
                name_servers=[_norm_host(ns) for ns in remote.get("name_servers") or []]
                or zone.name_servers,
                last_synced_at=now,
            )
            report["zone"] = (await self._decorate_zones([zone]))[0]
            report["expected_nameservers"] = zone.name_servers or []
            expected = set(report["expected_nameservers"])
            report["nameservers_delegated"] = bool(expected) and bool(
                set(report["observed_nameservers"]) & expected
            )

        try:
            ruleset = await client.get_redirect_ruleset(zone.cf_zone_id)
        except CloudflareError as exc:
            report["unavailable"].append("redirect_rules")
            await self._flag_account(account, exc)
        else:
            await self._observe_redirect(report, zone, redirect, ruleset, now)

        try:
            page_rules = await client.list_page_rules(zone.cf_zone_id)
        except CloudflareError:
            # Page Rules need their own token scope, and most tokens will not have it. Not an
            # error — a probe that did not run, named as such.
            report["unavailable"].append("page_rules")
        else:
            for rule in rules.forwarding_page_rules(page_rules):
                report["conflicts"].append(
                    RedirectConflict(
                        kind="page_rule",
                        description=rules.page_rule_pattern(rule),
                        detail=str(rule.get("status") or ""),
                    )
                )

        try:
            records = await client.list_dns_records(zone.cf_zone_id)
        except CloudflareError:
            report["unavailable"].append("dns")
        else:
            proxied = {
                _norm_host(r.get("name"))
                for r in records
                if r.get("proxied") and str(r.get("type") or "").upper() in PROXIABLE_TYPES
            }
            report["origin"] = OriginState(
                apex_proxied=zone.name in proxied,
                www_proxied=f"www.{zone.name}" in proxied,
                has_records=bool(records),
            )

    async def _observe_redirect(
        self,
        report: dict[str, Any],
        zone: CloudflareZone,
        redirect: CloudflareRedirect | None,
        ruleset: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        """Compare our stored intent with the rule Cloudflare holds, and persist the verdict."""
        others = rules.other_redirect_rules(ruleset, redirect.cf_rule_id if redirect else None)
        for rule in others:
            report["conflicts"].append(
                RedirectConflict(
                    kind="redirect_rule",
                    description=str(rule.get("description") or ""),
                    detail=str(rule.get("expression") or ""),
                )
            )
        if redirect is None:
            return

        live = rules.find_our_rule(ruleset, redirect.cf_rule_id)
        if live is None:
            report["redirect_live"] = RedirectObservation(present=False)
            report["redirect"] = await self.redirects.update(
                redirect,
                last_status=RedirectStatus.MISSING.value,
                last_checked_at=now,
            )
            return

        desired = rules.build_rule(
            apex=zone.name,
            target_url=redirect.target_url,
            status_code=redirect.status_code,
            preserve_path=redirect.preserve_path,
            preserve_query=redirect.preserve_query,
            include_subdomains=redirect.include_subdomains,
        )
        differences = rules.compare(desired, live)
        from_value = (live.get("action_parameters") or {}).get("from_value") or {}
        target = from_value.get("target_url")
        report["redirect_live"] = RedirectObservation(
            present=True,
            status_code=from_value.get("status_code"),
            target=(
                target.get("value") or target.get("expression")
                if isinstance(target, dict)
                else target
            ),
            differences=differences,
        )
        report["redirect"] = await self.redirects.update(
            redirect,
            last_status=(
                RedirectStatus.DRIFT.value if differences else RedirectStatus.ACTIVE.value
            ),
            last_checked_at=now,
        )

    async def _flag_account(self, account: CloudflareAccount, exc: CloudflareError) -> None:
        """Remember a token failure on a path that still commits, so the settings screen can say
        what Cloudflare said. (A failing *write* raises and rolls back, so it is not marked —
        the caller sees the error there and then.)

        **Only a 401 marks the row broken.** ``CloudflareAuthError`` covers both of Cloudflare's
        refusals and they are different sentences: a 401 says it does not accept this token at
        all, a 403 says this token is not scoped for *this call* — which §Token scopes calls
        degraded, not broken, and which the probes already report per capability. Flagging a 403
        left an agency's DNS-only token reading "Token problem" for ever over an optional Pages
        probe it was never meant to pass. The text is still recorded either way, because a
        missing scope is worth reading; it is the red status it does not earn.
        """
        rejected = isinstance(exc, CloudflareAuthError) and exc.status == 401
        await self.accounts.update(
            account,
            status=CloudflareAccountStatus.ERROR.value if rejected else account.status,
            last_error=str(exc)[:500],
        )

    async def _record_failure(self, account: CloudflareAccount, exc: CloudflareError) -> None:
        """Remember a token failure on a path that then **raises** — outside this transaction.

        ``require_context`` commits on the way out and rolls back on any exception, so writing
        ``last_error`` and *then* raising records nothing at all: the update is undone by the
        very error it describes. The row kept reading healthy while the admin was looking at a
        red toast, and the settings screen — whose whole job is to say what is wrong with a
        credential — was the one place that never found out.

        So this note is written on its own session and committed on its own, deliberately
        surviving the rollback of everything else. It is the narrow exception the general rule
        allows, and it is safe here because it touches exactly one row and only ever writes the
        *diagnosis*: nothing a caller could mistake for the operation having partly succeeded.
        RLS is bound the same way any out-of-request writer binds it (``app.core.jobs``), and
        the org is pinned in the ``WHERE`` too — belt and braces on the one write in this module
        that does not ride the scoped repository.

        It must never replace the error it is recording, so a failure to write it is logged and
        swallowed. Losing the note is bad; losing the exception is worse.
        """
        rejected = isinstance(exc, CloudflareAuthError) and exc.status == 401
        try:
            async with async_session_maker() as session:
                await set_current_org(session, self.ctx.org.id)
                values: dict[str, Any] = {"last_error": str(exc)[:500]}
                if rejected:
                    values["status"] = CloudflareAccountStatus.ERROR.value
                await session.execute(
                    update(CloudflareAccount)
                    .where(
                        CloudflareAccount.id == account.id,
                        CloudflareAccount.org_id == self.ctx.org.id,
                    )
                    .values(**values)
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — see the docstring: never mask the real failure
            logger.warning("could not record cloudflare failure for account %s", account.id)

    async def _clear_account_error(self, account: CloudflareAccount) -> None:
        """The mirror of :meth:`_flag_account`: Cloudflare answered, so the row is not broken.

        Without it the flag was one-way — nothing but a manual re-verify could take a row out of
        ``error``, so a token that had been fixed (or was never broken, just asked the wrong
        verify endpoint) kept its red line through every successful sync and check.
        """
        if account.status == CloudflareAccountStatus.ERROR.value or account.last_error:
            await self.accounts.update(
                account,
                status=CloudflareAccountStatus.ACTIVE.value,
                last_error=None,
            )

    def _issues(
        self,
        report: dict[str, Any],
        domain: DomainRow,
        zone: CloudflareZone | None,
        redirect: CloudflareRedirect | None,
    ) -> list[str]:
        """The report's findings as stable keys, most actionable first."""
        issues: list[str] = []
        if zone is None:
            if len(report["candidates"]) > 1:
                issues.append(ISSUE_DUPLICATE_ZONE)
            issues.append(ISSUE_NOT_CONNECTED)
        else:
            if len(report["candidates"]) > 1:
                issues.append(ISSUE_DUPLICATE_ZONE)
            if zone.status != "active":
                issues.append(ISSUE_ZONE_PENDING)
            if zone.paused:
                issues.append(ISSUE_ZONE_PAUSED)
            if report["expected_nameservers"] and not report["nameservers_delegated"]:
                issues.append(ISSUE_NAMESERVERS)

        live_redirect = report.get("redirect_live")
        if redirect is not None:
            if redirect.last_status == RedirectStatus.PENDING.value:
                issues.append(ISSUE_REDIRECT_UNPUSHED)
            elif live_redirect is not None and not live_redirect.present:
                issues.append(ISSUE_REDIRECT_MISSING)
            elif live_redirect is not None and live_redirect.differences:
                issues.append(ISSUE_REDIRECT_DRIFT)
            elif redirect.last_status == RedirectStatus.MISSING.value:
                issues.append(ISSUE_REDIRECT_MISSING)
            elif redirect.last_status == RedirectStatus.DRIFT.value:
                issues.append(ISSUE_REDIRECT_DRIFT)
            origin: OriginState | None = report.get("origin")
            if origin is not None:
                if not origin.apex_proxied:
                    # The rule exists and nothing reaches it. Worth its own line: it looks
                    # configured.
                    issues.append(ISSUE_ORIGIN_MISSING)
                # Only when the rule actually claims ``www``. With subdomains excluded the rule
                # never matches it, so an unproxied ``www`` is the configured state, not a finding.
                if redirect.include_subdomains and not origin.www_proxied:
                    issues.append(ISSUE_ORIGIN_WWW_MISSING)
        if report["conflicts"]:
            issues.append(ISSUE_REDIRECT_CONFLICT)

        pages_links = report["pages_links"]
        if any(link["missing_at"] for link in pages_links):
            issues.append(ISSUE_PAGES_MISSING)
        # Only a status Cloudflare actually gave us. A link adopted by a sync carries none yet,
        # and reading "no answer" as "pending" would raise a finding nobody can act on.
        if any(
            link["status"] and link["status"] != "active" and not link["missing_at"]
            for link in pages_links
        ):
            issues.append(ISSUE_PAGES_PENDING)

        # The two halves of "does this domain redirect" disagreeing is its own finding — it is
        # how a redirect wired outside schakl (#96's webhook flow, a hand-made Page Rule) shows
        # up, and how a rule someone deleted at Cloudflare does.
        if domain.status == "redirect" and redirect is None:
            issues.append(ISSUE_DOMAIN_SAYS_REDIRECT)
        if redirect is not None and domain.status != "redirect":
            issues.append(ISSUE_CLOUDFLARE_SAYS_REDIRECT)
        if report["unavailable"]:
            issues.append(ISSUE_TOKEN_ERROR)
        return issues

    # ------------------------------------------------------------------ #
    # Pages
    # ------------------------------------------------------------------ #
    async def list_pages_projects(
        self, *, account_id: uuid.UUID | None = None
    ) -> list[dict[str, Any]]:
        conditions = []
        if account_id is not None:
            conditions.append(CloudflarePagesProject.account_id == account_id)
        projects = list(
            (
                await self.ctx.session.execute(
                    self.projects.scoped_select()
                    .where(*conditions)
                    .order_by(func.lower(CloudflarePagesProject.name))
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
        if not projects:
            return []
        names = dict(
            (
                await self.ctx.session.execute(
                    select(CloudflareAccount.id, CloudflareAccount.name).where(
                        CloudflareAccount.org_id == self.ctx.org.id,
                        CloudflareAccount.id.in_({p.account_id for p in projects}),
                    )
                )
            ).all()
        )
        return [
            {
                "id": p.id,
                "account_id": p.account_id,
                "account_name": names.get(p.account_id),
                "name": p.name,
                "subdomain": p.subdomain,
                "production_branch": p.production_branch,
            }
            for p in projects
        ]

    async def _refresh_pages_links(self, report: dict[str, Any]) -> None:
        """Ask the projects this domain is linked to what they serve now, and record it.

        Without this the panel showed the answer Cloudflare gave in the second the link was
        made and never again: a hostname that finished provisioning still read *pending*
        forever, and one removed in Cloudflare's dashboard still read as linked. It also picks
        up a sibling hostname added there by hand (``www`` beside the apex) — bounded to this
        domain's own names, because a hostname outside it belongs to another client's record
        and is the sync's to file, not this check's.

        One call per distinct project, and a project that refuses is named in ``unavailable``
        and leaves its links untouched — the per-probe degradation :meth:`_probe_live` follows.
        """
        domain_id: uuid.UUID = report["domain_id"]
        domain_name: str = report["domain_name"]
        links = list(
            (
                await self.ctx.session.execute(
                    self.pages_links.scoped_select().where(
                        CloudflarePagesLink.domain_id == domain_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not links:
            return
        projects = list(
            (
                await self.ctx.session.execute(
                    self.projects.scoped_select().where(
                        CloudflarePagesProject.id.in_({link.project_id for link in links})
                    )
                )
            )
            .scalars()
            .all()
        )
        accounts = {
            a.id: a
            for a in (
                await self.ctx.session.execute(
                    self.accounts.scoped_select().where(
                        CloudflareAccount.id.in_({p.account_id for p in projects})
                    )
                )
            )
            .scalars()
            .all()
        }

        now = datetime.now(UTC)
        observed: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}
        for project in projects:
            account = accounts.get(project.account_id)
            if account is None or not account.cf_account_id:
                _unavailable(report, "pages")
                continue
            try:
                rows = await self._client(account).list_pages_domains(
                    account.cf_account_id, project.name
                )
            except CloudflareError as exc:
                _unavailable(report, "pages")
                await self._flag_account(account, exc)
                continue
            observed[project.id] = {
                host: row for row in rows if (host := _norm_host(row.get("name")))
            }

        for link in links:
            seen = observed.get(link.project_id)
            if seen is None:
                continue  # not read, so not judged
            row = seen.get(link.hostname)
            if row is None:
                await self.pages_links.update(
                    link, last_checked_at=now, missing_at=link.missing_at or now
                )
                continue
            await self.pages_links.update(
                link,
                status=str(row.get("status") or "") or None,
                last_error=_pages_error(row),
                last_checked_at=now,
                missing_at=None,
            )

        known = {(link.project_id, link.hostname) for link in links}
        unknown = [
            (project_id, host, row)
            for project_id, seen in observed.items()
            for host, row in seen.items()
            if (project_id, host) not in known
            and (host == domain_name or host.endswith(f".{domain_name}"))
        ]
        # A suffix of this domain's name is not proof it is *this domain's* hostname: a tenant
        # holding both ``klant.nl`` and ``shop.klant.nl`` would get ``www.shop.klant.nl`` filed
        # under the parent — the wrong client's page, and the exact mistake the sync's
        # longest-suffix match exists to prevent. So resolve it the same way and adopt only
        # what resolves back to here; anything else is the sync's to file, correctly.
        by_name = await self._domains_by_name(
            {candidate for _, host, _ in unknown for candidate in _host_candidates(host)}
        )
        for project_id, host, row in unknown:
            owner = next((by_name[c] for c in _host_candidates(host) if c in by_name), None)
            if owner != domain_id:
                continue
            await self.pages_links.create(
                project_id=project_id,
                domain_id=domain_id,
                hostname=host,
                status=str(row.get("status") or "") or None,
                last_error=_pages_error(row),
                last_checked_at=now,
                discovered_at=now,
            )

        report["pages_links"] = await self._pages_links_for(domain_id)

    async def _pages_links_for(self, domain_id: uuid.UUID) -> list[dict[str, Any]]:
        links = list(
            (
                await self.ctx.session.execute(
                    self.pages_links.scoped_select().where(
                        CloudflarePagesLink.domain_id == domain_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not links:
            return []
        names = dict(
            (
                await self.ctx.session.execute(
                    select(CloudflarePagesProject.id, CloudflarePagesProject.name).where(
                        CloudflarePagesProject.org_id == self.ctx.org.id,
                        CloudflarePagesProject.id.in_({link.project_id for link in links}),
                    )
                )
            ).all()
        )
        return [
            {
                "id": link.id,
                "project_id": link.project_id,
                "project_name": names.get(link.project_id),
                "domain_id": link.domain_id,
                "hostname": link.hostname,
                "status": link.status,
                "last_error": link.last_error,
                "last_checked_at": link.last_checked_at,
                "missing_at": link.missing_at,
                "discovered_at": link.discovered_at,
            }
            for link in links
        ]

    async def link_pages_project(
        self, domain_id: uuid.UUID, payload: PagesLinkCreate
    ) -> dict[str, Any]:
        """Serve a hostname of this domain from a Cloudflare Pages project.

        Cloudflare needs the hostname registered on the project *and* a DNS record pointing at
        it; doing only the first leaves a custom domain stuck on "pending" forever, which looks
        like a Cloudflare problem and is not. So both happen here, and the project's own
        ``*.pages.dev`` subdomain is the CNAME target.
        """
        domain = await self._domain_or_404(domain_id)
        project = await self.projects.get_or_404(payload.project_id)
        account = await self.accounts.get_or_404(project.account_id)
        if not account.cf_account_id:
            raise AppError(
                "cloudflare_pages_unavailable",
                "errors.cloudflare_pages_unavailable",
                status_code=409,
            )
        hostname = _norm_host(payload.hostname) or domain.name
        if hostname != domain.name and not hostname.endswith(f".{domain.name}"):
            # Attaching some other client's hostname to this domain's record would put the link
            # on the wrong company — a horizon hole dressed as a typo.
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"hostname": "errors.cloudflare_hostname_not_in_domain"},
            )

        client = self._client(account)
        try:
            created = await client.add_pages_domain(
                account.cf_account_id, project.name, hostname
            )
            zone = await self._zone_for_domain(domain.id)
            if zone is not None and project.subdomain:
                await self._ensure_pages_cname(client, zone, hostname, project.subdomain)
        except CloudflareError as exc:
            raise self._translate(exc) from exc

        existing = (
            await self.ctx.session.execute(
                self.pages_links.scoped_select()
                .where(
                    CloudflarePagesLink.project_id == project.id,
                    CloudflarePagesLink.hostname == hostname,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        values = {
            "status": str(created.get("status") or "pending"),
            "last_error": None,
            "last_checked_at": datetime.now(UTC),
            # Re-linking a hostname a check had found gone is how drift gets resolved, so the
            # flag clears here. Leaving it would leave the panel warning about a link that was
            # just re-registered a second ago.
            "missing_at": None,
        }
        link = (
            await self.pages_links.update(existing, **values)
            if existing is not None
            else await self.pages_links.create(
                project_id=project.id, domain_id=domain.id, hostname=hostname, **values
            )
        )
        await self.activity.record(
            DOMAIN_ENTITY,
            domain.id,
            "cloudflare.pages_linked",
            {"project": project.name, "hostname": hostname},
        )
        return {
            "id": link.id,
            "project_id": link.project_id,
            "project_name": project.name,
            "domain_id": link.domain_id,
            "hostname": link.hostname,
            "status": link.status,
            "last_error": link.last_error,
            "last_checked_at": link.last_checked_at,
            "missing_at": link.missing_at,
            "discovered_at": link.discovered_at,
        }

    async def _ensure_pages_cname(
        self, client: CloudflareClient, zone: CloudflareZone, hostname: str, subdomain: str
    ) -> None:
        """Point the hostname at the Pages project, without stepping on an existing record.

        A record already there is left alone and reported by the status check: silently
        repointing whatever answers on that hostname today is how a live site disappears.
        """
        records = await client.list_dns_records(zone.cf_zone_id)
        if any(_norm_host(r.get("name")) == hostname for r in records):
            return
        await client.create_dns_record(
            zone.cf_zone_id,
            {
                "type": "CNAME",
                "name": hostname,
                "content": subdomain,
                "ttl": 1,
                "proxied": True,
                "comment": "schakl: Cloudflare Pages",
            },
        )

    async def unlink_pages_project(self, link_id: uuid.UUID) -> None:
        """Detach the hostname from the project at Cloudflare, and forget the link.

        The DNS record is deliberately **not** deleted: it may since have been repointed, and
        removing a record we did not verify we own is the one mistake with no undo.
        """
        link = await self.pages_links.get_or_404(link_id)
        project = await self.projects.get_or_404(link.project_id)
        account = await self.accounts.get_or_404(project.account_id)
        if account.cf_account_id:
            try:
                await self._client(account).delete_pages_domain(
                    account.cf_account_id, project.name, link.hostname
                )
            except CloudflareError as exc:
                if exc.status != 404:
                    raise self._translate(exc) from exc
        await self.activity.record(
            DOMAIN_ENTITY,
            link.domain_id,
            "cloudflare.pages_unlinked",
            {"project": project.name, "hostname": link.hostname},
        )
        await self.pages_links.delete(link)
