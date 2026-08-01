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
ISSUE_ORIGIN_MISSING = "origin_missing"
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


class CloudflareService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.accounts = ctx.repo(CloudflareAccount)
        self.zones = ctx.repo(CloudflareZone)
        self.redirects = ctx.repo(CloudflareRedirect)
        self.projects = ctx.repo(CloudflarePagesProject)
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
            capabilities, discovered = await client.probe_capabilities()
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
            await self.accounts.update(
                account,
                status=(
                    CloudflareAccountStatus.ERROR.value
                    if isinstance(exc, CloudflareAuthError)
                    else account.status
                ),
                last_error=str(exc)[:500],
            )
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

        projects_synced = 0
        if account.cf_account_id:
            try:
                projects_synced = await self._sync_pages_projects(account, client, now)
            except CloudflareError as exc:
                # Pages is optional. A token without it still syncs zones, and saying so beats
                # failing an action that mostly worked.
                warnings.append(str(exc)[:200])
        else:
            warnings.append("no_account_id")

        await self.accounts.update(account, last_synced_at=now, last_error=None)
        return AccountSyncResult(
            zones_synced=len(zones),
            zones_matched=matched,
            pages_projects_synced=projects_synced,
            warnings=warnings,
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

    async def _sync_pages_projects(
        self, account: CloudflareAccount, client: CloudflareClient, now: datetime
    ) -> int:
        projects = await client.list_pages_projects(account.cf_account_id or "")
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
            if project is None:
                await self.projects.create(account_id=account.id, name=name, **values)
            else:
                await self.projects.update(project, **values)
        return len(projects)

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

        if live and zone is not None:
            await self._probe_live(report, zone, redirect)

        report["issues"] = self._issues(report, domain, zone, redirect)
        return report

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
        the caller sees the error there and then.)"""
        await self.accounts.update(
            account,
            status=(
                CloudflareAccountStatus.ERROR.value
                if isinstance(exc, CloudflareAuthError)
                else account.status
            ),
            last_error=str(exc)[:500],
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
            if origin is not None and not origin.apex_proxied:
                # The rule exists and nothing reaches it. Worth its own line: it looks configured.
                issues.append(ISSUE_ORIGIN_MISSING)
        if report["conflicts"]:
            issues.append(ISSUE_REDIRECT_CONFLICT)

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
