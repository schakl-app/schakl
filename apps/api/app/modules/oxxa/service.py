"""Business logic for the oxxa module (issue #296). Business-licensed — see LICENSE.

The shape mirrors ``cloudflare``'s, and the two rules it never bends are the same two:

**Never guess which account.** A tenant may hold more than one OXXA reseller login. Pushing a
delegation through the wrong one either fails or, worse, succeeds against a domain of the same
name in the other register. So :meth:`OxxaService._resolve_account` answers "there are several,
say which" rather than picking.

**Observe before you write, and report drift rather than resolving it.** A sync stores what the
registrar said next to what we asked for and never overwrites one with the other. In particular
it never writes to the ``domains`` table: the registrant it reads is a shared registry object
and ``Domain.registry_contact_*`` is a *decision a user made*. Silently replacing that with
whatever WHOIS says would be the single most surprising thing this module could do — so the
observation is stored here, the disagreement is reported as an issue key, and applying it stays
a human act. (The issue's phrasing — "where a synced registrant lands" — is honoured as a
reported match, not as an automatic write; ``docs/OXXA.md`` §6 records why.)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import column, func, select, table

from app.core.activity import ActivityService
from app.core.crypto import decrypt, encrypt
from app.core.providers.models import Provider
from app.core.registrar.backend import split_suffix
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.oxxa.client import (
    OxxaAuthError,
    OxxaClient,
    OxxaConflictError,
    OxxaError,
    norm_host,
    redact,
)
from app.modules.oxxa.models import (
    NameserverPushStatus,
    OxxaAccount,
    OxxaAccountStatus,
    OxxaDomain,
)
from app.modules.oxxa.schemas import (
    NameserverPush,
    NameserverPushResult,
    OxxaAccountCreate,
    OxxaAccountSyncResult,
    OxxaAccountUpdate,
    OxxaAccountVerifyResult,
)

logger = logging.getLogger("schakl.oxxa")

#: The activity trail hangs on the **domain** for anything that touches one — that is the record
#: a user opens (§16). The credential's own trail uses its own entity type.
DOMAIN_ENTITY = "domain"
ACCOUNT_ENTITY = "oxxa_account"

#: ``domains`` belongs to another module; referenced as a bare table (§6).
_domains = table(
    "domains",
    column("id"),
    column("org_id"),
    column("company_id"),
    column("name"),
    column("nameservers"),
    column("registry_contact_party_type"),
    column("registry_contact_party_id"),
)

#: Issue keys ``domain_status`` can raise, resolved to ``oxxa.issue.*`` by the client.
ISSUE_NO_ACCOUNT = "no_account"
ISSUE_NOT_IN_REGISTER = "not_in_register"
ISSUE_NEVER_SYNCED = "never_synced"
ISSUE_NS_DRIFT = "nameserver_drift"
ISSUE_NS_MISSING = "nameservers_missing"
# Deliberately absent: a "desired but never pushed" key. ``ns_desired`` is only ever written by
# ``push_nameservers``, which sets ``active`` or ``error`` in the same breath, so the state is
# unreachable — and an issue key nothing can raise is copy that documents a lie.
ISSUE_NOT_DELEGATED = "not_delegated_yet"
ISSUE_EXPIRING = "expiring_soon"
ISSUE_EXPIRED = "expired"
ISSUE_TRANSFER_UNLOCKED = "transfer_unlocked"
ISSUE_AUTORENEW_OFF = "autorenew_off"
ISSUE_PUSH_ERROR = "push_error"

#: How near an expiry has to be before the panel says so. A registrar renewal window is 30 days
#: at most registries; 60 gives an agency time to ask the client whether they still want it.
EXPIRY_WARNING_DAYS = 60



@dataclass(frozen=True)
class DomainRow:
    """The columns of another module's ``domains`` row this module reads. A dataclass rather
    than the ORM model, because importing it would be importing that module's internals (§6)."""

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    nameservers: list[str]


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


class OxxaService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.accounts = ctx.repo(OxxaAccount)
        self.register = ctx.repo(OxxaDomain)
        self.activity = ActivityService(ctx)

    # ------------------------------------------------------------------ #
    # Cross-module bridge: domains
    # ------------------------------------------------------------------ #
    async def _domain_or_404(self, domain_id: uuid.UUID) -> DomainRow:
        """The domain, or 404 — **with the company horizon applied**.

        ``domains`` is another module's table, so this read cannot ride its repository. That is
        precisely §15's failure mode 3 (a hand-built cross-client read), so the predicate is
        written here, once, and every domain-addressed path in this module goes through it.
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
                    _domains.c.nameservers,
                ).where(*conditions)
            )
        ).first()
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return DomainRow(
            id=row.id,
            company_id=row.company_id,
            name=norm_host(row.name),
            nameservers=[norm_host(ns) for ns in (row.nameservers or []) if ns],
        )

    async def _domain_ids_by_name(self, names: set[str]) -> dict[str, uuid.UUID]:
        """Match register rows to schakl domains in **one** query, never one per row.

        Horizon-scoped like every other cross-module read here: a restricted membership must not
        be able to learn that a domain exists by watching a sync match it.
        """
        if not names:
            return {}
        conditions = [_domains.c.org_id == self.ctx.org.id, _domains.c.name.in_(sorted(names))]
        if self.ctx.company_scope is not None:
            conditions.append(_domains.c.company_id.in_(self.ctx.company_scope))
        rows = (
            await self.ctx.session.execute(
                select(_domains.c.id, _domains.c.name).where(*conditions)
            )
        ).all()
        return {norm_host(row.name): row.id for row in rows}

    async def _domain_names(self, domain_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Names for a batch of domains — one query (docs/PERFORMANCE.md)."""
        if not domain_ids:
            return {}
        rows = (
            await self.ctx.session.execute(
                select(_domains.c.id, _domains.c.name).where(
                    _domains.c.org_id == self.ctx.org.id,
                    _domains.c.id.in_(sorted(domain_ids)),
                )
            )
        ).all()
        return {row.id: row.name for row in rows}

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #
    async def list_accounts(self) -> list[dict[str, Any]]:
        accounts = list(
            (
                await self.ctx.session.execute(
                    self.accounts.scoped_select().order_by(func.lower(OxxaAccount.name))
                )
            )
            .scalars()
            .all()
        )
        if not accounts:
            return []

        counts = dict(
            (
                await self.ctx.session.execute(
                    self.register.scoped_select()
                    .with_only_columns(OxxaDomain.account_id, func.count(OxxaDomain.id))
                    .group_by(OxxaDomain.account_id)
                )
            ).all()
        )
        provider_ids = {a.provider_id for a in accounts if a.provider_id}
        provider_names: dict[uuid.UUID, str] = {}
        if provider_ids:
            provider_names = dict(
                (
                    await self.ctx.session.execute(
                        self.ctx.repo(Provider)
                        .scoped_select()
                        .with_only_columns(Provider.id, Provider.name)
                        .where(Provider.id.in_(sorted(provider_ids)))
                    )
                ).all()
            )
        return [
            {
                "id": a.id,
                "name": a.name,
                "api_user": a.api_user,
                "provider_id": a.provider_id,
                "provider_name": provider_names.get(a.provider_id) if a.provider_id else None,
                "active": a.active,
                "status": a.status,
                "tld_count": len(a.tld_suffixes or []),
                "funds_available": a.funds_available,
                "last_verified_at": a.last_verified_at,
                "last_synced_at": a.last_synced_at,
                "last_error": a.last_error,
                "password_configured": bool(a.api_password_encrypted),
                "domain_count": int(counts.get(a.id, 0)),
            }
            for a in accounts
        ]

    async def account_options(self) -> list[dict[str, Any]]:
        accounts = await self._active_accounts()
        return [{"id": a.id, "name": a.name, "active": a.active} for a in accounts]

    async def create_account(self, payload: OxxaAccountCreate) -> OxxaAccount:
        await self._assert_account_name_free(payload.name)
        account = await self.accounts.create(
            name=payload.name.strip(),
            api_user=payload.api_user.strip(),
            api_password_encrypted=encrypt(payload.api_password.strip()),
            provider_id=await self._validated_provider(payload.provider_id),
            active=payload.active,
        )
        await self.ctx.session.flush()
        await self.activity.record_created(ACCOUNT_ENTITY, account.id, {"name": account.name})
        return account

    async def update_account(
        self, account_id: uuid.UUID, payload: OxxaAccountUpdate
    ) -> OxxaAccount:
        account = await self.accounts.get_or_404(account_id)
        before = {"name": account.name, "api_user": account.api_user, "active": account.active}

        if payload.name is not None and payload.name.strip() != account.name:
            await self._assert_account_name_free(payload.name, exclude=account.id)
            account.name = payload.name.strip()
        if payload.api_user is not None:
            account.api_user = payload.api_user.strip()
        if payload.provider_id is not None:
            account.provider_id = await self._validated_provider(payload.provider_id)
        if payload.active is not None:
            account.active = payload.active

        rotated = False
        if payload.api_password:
            account.api_password_encrypted = encrypt(payload.api_password.strip())
            # Everything observed through the old credential is now unproven. Clearing it is
            # what stops a stale "verified" badge vouching for a password nobody has tested.
            account.tld_suffixes = []
            account.funds_available = None
            account.last_verified_at = None
            account.status = OxxaAccountStatus.ACTIVE.value
            account.last_error = None
            rotated = True

        await self.ctx.session.flush()
        after = {"name": account.name, "api_user": account.api_user, "active": account.active}
        changes = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
        if changes:
            await self.activity.record(ACCOUNT_ENTITY, account.id, "updated", {"changes": changes})
        if rotated:
            # The password is never in the trail — only that it changed.
            await self.activity.record(ACCOUNT_ENTITY, account.id, "oxxa.credential_rotated")
        return account

    async def delete_account(self, account_id: uuid.UUID) -> None:
        """Forget the credential and the register we synced from it.

        **Nothing at OXXA is touched.** Deleting a client's live domain as a side effect of
        tidying a credential list is unrecoverable, so this is a local forget and nothing else.
        """
        account = await self.accounts.get_or_404(account_id)
        name = account.name
        await self.activity.record(
            ACCOUNT_ENTITY, account.id, "oxxa.account_removed", {"name": name}
        )
        await self.ctx.session.delete(account)
        await self.ctx.session.flush()

    async def verify_account(self, account_id: uuid.UUID) -> OxxaAccountVerifyResult:
        """Prove the credential works and cache what the register may operate on.

        Never raises for a *rejected* credential — "OXXA said no" is a fact to report on the
        settings screen, not a 500 three screens away. It does raise for the states a retry
        cannot fix (an unreadable stored secret).
        """
        account = await self.accounts.get_or_404(account_id)
        client = self._client(account)
        now = datetime.now(UTC)
        try:
            async with self.ctx.release_db():
                facts = await client.verify()
                suffixes = await client.suffixes()
        except OxxaError as exc:
            account.status = OxxaAccountStatus.ERROR.value
            account.last_error = redact(str(exc))[:500]
            account.last_verified_at = now
            await self.ctx.session.flush()
            return OxxaAccountVerifyResult(ok=False, error=account.last_error)

        account.status = OxxaAccountStatus.ACTIVE.value
        account.last_error = None
        account.last_verified_at = now
        account.funds_available = _decimal(facts.get("funds_available"))
        account.tld_suffixes = suffixes
        await self.ctx.session.flush()
        return OxxaAccountVerifyResult(
            ok=True, funds_available=account.funds_available, tld_count=len(suffixes)
        )

    async def _active_accounts(self) -> list[OxxaAccount]:
        return list(
            (
                await self.ctx.session.execute(
                    self.accounts.scoped_select()
                    .where(OxxaAccount.active.is_(True))
                    .order_by(func.lower(OxxaAccount.name))
                )
            )
            .scalars()
            .all()
        )

    async def _resolve_account(self, account_id: uuid.UUID | None) -> OxxaAccount:
        """The account to act through: the one named, or the only active one. Never a guess."""
        if account_id is not None:
            account = await self.accounts.get_or_404(account_id)
            if not account.active:
                raise AppError(
                    "oxxa_account_inactive", "errors.oxxa_account_inactive", status_code=409
                )
            return account
        accounts = await self._active_accounts()
        if not accounts:
            raise AppError("oxxa_no_account", "errors.oxxa_no_account", status_code=409)
        if len(accounts) > 1:
            raise AppError(
                "oxxa_account_ambiguous",
                "errors.oxxa_account_ambiguous",
                status_code=409,
                fields={"account_id": "errors.required"},
            )
        return accounts[0]

    async def _assert_account_name_free(
        self, name: str, *, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = self.accounts.scoped_select().where(
            func.lower(OxxaAccount.name) == name.strip().lower()
        )
        if exclude:
            stmt = stmt.where(OxxaAccount.id != exclude)
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

    def _client(self, account: OxxaAccount) -> OxxaClient:
        try:
            password = decrypt(account.api_password_encrypted)
        except ValueError as exc:
            # A rotated ``SCHAKL_ENCRYPTION_KEY`` leaves an unreadable secret. Say so plainly —
            # the fix is re-entering the password, not retrying.
            raise AppError(
                "oxxa_credential_unreadable",
                "errors.oxxa_credential_unreadable",
                status_code=409,
            ) from exc
        return OxxaClient(account.api_user, password)

    def _translate(self, exc: OxxaError) -> AppError:
        """OXXA's failure → the standard envelope (§9: ``message`` is an i18n key).

        OXXA's own text never enters the envelope — it is not translatable. It is persisted to
        the row's ``last_error`` wherever the operation still commits, which is where a user
        reads it.

        Note that ``push_nameservers`` reads only ``message_key`` off the result: it *returns*
        the failure rather than raising it (see there), so the status codes below apply on the
        raising paths — ``refresh_domain`` — and are advisory on the push. That is deliberate
        rather than an oversight: the key is what the panel renders either way, and a conflict
        the user must resolve at OXXA is worth strictly more as a persisted row than as a 409
        that rolls that row back.
        """
        if isinstance(exc, OxxaAuthError):
            return AppError(
                "oxxa_credential_rejected", "errors.oxxa_credential_rejected", status_code=409
            )
        if isinstance(exc, OxxaConflictError):
            # Something at OXXA was changed outside schakl. A retry cannot fix it and must not
            # be suggested — the admin has to resolve it at the registrar.
            return AppError(
                "oxxa_nsgroup_conflict", "errors.oxxa_nsgroup_conflict", status_code=409
            )
        if exc.http_status is None and exc.code is None:
            return AppError("oxxa_unreachable", "errors.oxxa_unreachable", status_code=502)
        return AppError("oxxa_request_failed", "errors.oxxa_request_failed", status_code=502)

    def _split(self, account: OxxaAccount, name: str) -> tuple[str, str]:
        """``klant.co.uk`` → ``("klant", "co.uk")``, using the register's own TLD list.

        Refuses rather than guesses, for two different reasons that both end in the wrong domain
        being addressed: an unverified account has no suffix list at all, and a name like
        ``shop.klant.nl`` is a hostname inside a zone, which OXXA would happily read as the
        registrable domain ``shop.klant.nl`` — or, worse, as ``shop`` + ``klant.nl``.
        """
        suffixes = list(account.tld_suffixes or [])
        if not suffixes:
            raise AppError("oxxa_not_verified", "errors.oxxa_not_verified", status_code=409)
        split = split_suffix(name, suffixes)
        if split is None:
            raise AppError("oxxa_unknown_tld", "errors.oxxa_unknown_tld", status_code=409)
        return split

    # ------------------------------------------------------------------ #
    # Sync
    # ------------------------------------------------------------------ #
    async def sync_account(self, account_id: uuid.UUID) -> OxxaAccountSyncResult:
        """Pull the whole register in one call and reconcile it with what we stored.

        One request, not one per domain: ``domain_list`` carries expiry, lock, autorenew, the
        contact handles and the nameserver group. DNSSEC and the registrant's *name* are the
        only things it omits, and both are the per-domain refresh's business — which is exactly
        why this is a button an admin presses rather than a cron that hammers OXXA nightly.
        """
        account = await self.accounts.get_or_404(account_id)
        client = self._client(account)
        now = datetime.now(UTC)

        try:
            async with self.ctx.release_db():
                found = await client.list_domains()
                # Resolve each *distinct* nameserver group once, not once per domain: a
                # reseller account with 400 domains usually has a handful of groups.
                refs = {d.nameserver_ref for d in found if d.nameserver_ref}
                members = {ref: await client.nameservers_of(ref) for ref in sorted(refs)}
        except OxxaError as exc:
            account.status = OxxaAccountStatus.ERROR.value
            account.last_error = redact(str(exc))[:500]
            await self.ctx.session.flush()
            return OxxaAccountSyncResult(ok=False, error=account.last_error)

        existing = {
            row.name: row
            for row in (
                await self.ctx.session.execute(
                    self.register.scoped_select().where(OxxaDomain.account_id == account.id)
                )
            )
            .scalars()
            .all()
        }
        matches = await self._domain_ids_by_name({d.name for d in found})

        matched = drifted = 0
        seen: set[str] = set()
        for reported in found:
            name = reported.name
            seen.add(name)
            observed = sorted(members.get(reported.nameserver_ref or "", []))
            row = existing.get(name)
            if row is None:
                row = await self.register.create(
                    account_id=account.id,
                    name=name,
                    sld=reported.sld,
                    tld=reported.tld,
                )
            row.domain_id = matches.get(name) or row.domain_id
            if row.domain_id is not None:
                matched += 1
            row.expires_on = reported.expires_on
            row.transfer_lock = reported.transfer_lock
            row.autorenew = reported.autorenew
            row.nsgroup_ref = reported.nameserver_ref
            row.ns_observed = observed
            row.registry_status = reported.status
            if reported.contacts:
                row.contact_refs = reported.contacts
            row.last_synced_at = now

            # Drift is only meaningful once we have asked for something. A domain we never
            # pushed is not "drifted", it is simply somebody else's delegation.
            if row.ns_desired:
                if not observed:
                    row.ns_push_status = NameserverPushStatus.MISSING.value
                elif set(observed) == {norm_host(ns) for ns in row.ns_desired}:
                    row.ns_push_status = NameserverPushStatus.ACTIVE.value
                else:
                    row.ns_push_status = NameserverPushStatus.DRIFT.value
                    drifted += 1

        # A domain that left the register (transferred away, expired) keeps its row but stops
        # claiming to be current: deleting it would take the trail of what we pushed with it.
        for name, row in existing.items():
            if name not in seen:
                row.registry_status = "gone"

        account.last_synced_at = now
        account.status = OxxaAccountStatus.ACTIVE.value
        account.last_error = None
        await self.ctx.session.flush()
        await self.activity.record(
            ACCOUNT_ENTITY,
            account.id,
            "oxxa.register_synced",
            {"found": len(found), "matched": matched, "drifted": drifted},
        )
        return OxxaAccountSyncResult(
            ok=True,
            found=len(found),
            matched=matched,
            unmatched=len(found) - matched,
            drifted=drifted,
        )

    async def refresh_domain(
        self, domain_id: uuid.UUID, account_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        """Re-read **one** domain from the registrar, including the two things a register-wide
        sync cannot afford: DNSSEC, and the registrant behind the handle."""
        domain = await self._domain_or_404(domain_id)
        account = await self._resolve_account(account_id)
        sld, tld = self._split(account, domain.name)
        client = self._client(account)

        try:
            async with self.ctx.release_db():
                reported = await client.get_domain(sld, tld)
                contact = None
                observed: list[str] = []
                if reported is not None:
                    if reported.nameserver_ref:
                        observed = await client.nameservers_of(reported.nameserver_ref)
                    handle = reported.contacts.get("registrant")
                    if handle:
                        contact = await client.get_contact(handle)
        except OxxaError as exc:
            raise self._translate(exc) from exc

        if reported is None:
            raise AppError(
                "oxxa_domain_not_in_register",
                "errors.oxxa_domain_not_in_register",
                status_code=409,
            )

        row = await self._register_row(account.id, domain.name, sld, tld)
        row.domain_id = domain.id
        row.expires_on = reported.expires_on
        row.transfer_lock = reported.transfer_lock
        row.autorenew = reported.autorenew
        row.dnssec = reported.dnssec
        row.nsgroup_ref = reported.nameserver_ref
        row.ns_observed = sorted(norm_host(ns) for ns in observed)
        if reported.contacts:
            row.contact_refs = reported.contacts
        if contact is not None:
            row.registrant = {
                "ref": contact.ref,
                "name": contact.display_name(),
                "organisation": contact.organisation,
                "email": contact.email,
                "city": contact.city,
                "country": contact.country,
            }
        row.last_synced_at = datetime.now(UTC)
        if row.ns_desired and row.ns_observed:
            row.ns_push_status = (
                NameserverPushStatus.ACTIVE.value
                if set(row.ns_observed) == {norm_host(ns) for ns in row.ns_desired}
                else NameserverPushStatus.DRIFT.value
            )
        await self.ctx.session.flush()
        return await self.domain_status(domain_id)

    async def _register_row(
        self, account_id: uuid.UUID, name: str, sld: str, tld: str
    ) -> OxxaDomain:
        row = (
            (
                await self.ctx.session.execute(
                    self.register.scoped_select().where(
                        OxxaDomain.account_id == account_id, OxxaDomain.name == name
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = await self.register.create(
                account_id=account_id, name=name, sld=sld, tld=tld
            )
            await self.ctx.session.flush()
        return row

    # ------------------------------------------------------------------ #
    # Reading the register
    # ------------------------------------------------------------------ #
    async def list_register(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        account_id: uuid.UUID | None = None,
        linked: bool | None = None,
        q: str | None = None,
        count: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        if account_id is not None:
            conditions.append(OxxaDomain.account_id == account_id)
        if linked is True:
            conditions.append(OxxaDomain.domain_id.is_not(None))
        elif linked is False:
            conditions.append(OxxaDomain.domain_id.is_(None))
        if q:
            conditions.append(OxxaDomain.name.ilike(f"%{q.strip().lower()}%"))

        stmt = self.register.scoped_select().where(*conditions).order_by(OxxaDomain.name)
        rows = list(
            (await self.ctx.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )
        total = 0
        if count:
            total = int(
                (
                    await self.ctx.session.execute(
                        self.register.scoped_count_select().where(*conditions)
                    )
                ).scalar_one()
            )
        names = await self._domain_names({r.domain_id for r in rows if r.domain_id})
        return [self._register_dict(r, names) for r in rows], total

    @staticmethod
    def _register_dict(row: OxxaDomain, names: dict[uuid.UUID, str]) -> dict[str, Any]:
        registrant = row.registrant or None
        return {
            "id": row.id,
            "account_id": row.account_id,
            "domain_id": row.domain_id,
            "domain_name": names.get(row.domain_id) if row.domain_id else None,
            "name": row.name,
            "sld": row.sld,
            "tld": row.tld,
            "expires_on": row.expires_on,
            "transfer_lock": row.transfer_lock,
            "autorenew": row.autorenew,
            "dnssec": row.dnssec,
            "ns_observed": row.ns_observed,
            "ns_desired": row.ns_desired,
            "ns_push_status": row.ns_push_status,
            "ns_pushed_at": row.ns_pushed_at,
            "nsgroup_ref": row.nsgroup_ref,
            "contact_refs": row.contact_refs or {},
            "registrant": registrant,
            "registrant_name": (registrant or {}).get("name"),
            "last_error": row.last_error,
            "last_synced_at": row.last_synced_at,
        }

    async def domain_status(self, domain_id: uuid.UUID) -> dict[str, Any]:
        """What we know about one domain, **from stored rows only** — never calls OXXA."""
        domain = await self._domain_or_404(domain_id)
        accounts = await self._active_accounts()
        row = (
            (
                await self.ctx.session.execute(
                    self.register.scoped_select()
                    .where(OxxaDomain.domain_id == domain.id)
                    .order_by(OxxaDomain.last_synced_at.desc().nullslast())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

        issues: list[str] = []
        account_name = None
        if not accounts:
            issues.append(ISSUE_NO_ACCOUNT)
        elif row is None:
            issues.append(
                ISSUE_NEVER_SYNCED
                if not any(a.last_synced_at for a in accounts)
                else ISSUE_NOT_IN_REGISTER
            )
        if row is not None:
            account_name = next((a.name for a in accounts if a.id == row.account_id), None)
            issues.extend(self._domain_issues(row, domain))

        # The name is already in hand from ``_domain_or_404``; a second query for it would be a
        # query this page-load endpoint does not need (docs/PERFORMANCE.md).
        names = {domain.id: domain.name}
        return {
            "domain_id": domain.id,
            "registrar": self._register_dict(row, names) if row is not None else None,
            "account_id": row.account_id if row is not None else None,
            "account_name": account_name,
            "issues": issues,
            "configured": bool(accounts),
        }

    @staticmethod
    def _domain_issues(row: OxxaDomain, domain: DomainRow) -> list[str]:
        issues: list[str] = []
        today = datetime.now(UTC).date()
        if row.expires_on:
            if row.expires_on < today:
                issues.append(ISSUE_EXPIRED)
            elif (row.expires_on - today).days <= EXPIRY_WARNING_DAYS:
                issues.append(ISSUE_EXPIRING)
        if row.transfer_lock is False:
            issues.append(ISSUE_TRANSFER_UNLOCKED)
        if row.autorenew is False:
            issues.append(ISSUE_AUTORENEW_OFF)

        if row.ns_push_status == NameserverPushStatus.DRIFT.value:
            issues.append(ISSUE_NS_DRIFT)
        elif row.ns_push_status == NameserverPushStatus.MISSING.value:
            issues.append(ISSUE_NS_MISSING)
        elif row.ns_push_status == NameserverPushStatus.ERROR.value:
            issues.append(ISSUE_PUSH_ERROR)

        # Registry vs the public internet. Only meaningful when public DNS actually answered:
        # the domains module stores ``[]`` for a *failed* lookup too, and reading that as
        # "not delegated" would light this up for every domain the resolver could not reach.
        if row.ns_observed and domain.nameservers:
            if set(row.ns_observed) != set(domain.nameservers):
                issues.append(ISSUE_NOT_DELEGATED)
        return issues

    # ------------------------------------------------------------------ #
    # The write path
    # ------------------------------------------------------------------ #
    async def push_nameservers(
        self, domain_id: uuid.UUID, payload: NameserverPush
    ) -> NameserverPushResult:
        """Repoint a domain's delegation at the registrar.

        This is the half of #278 the Cloudflare module deliberately left open: connecting a
        domain to Cloudflare produces a nameserver pair, and until now moving the domain onto it
        meant logging into the OXXA portal by hand.

        It stays a **separate, explicitly-parameterised step** rather than one button that also
        creates the Cloudflare zone, for three reasons that all point the same way. The two
        calls cannot share a transaction (``release_db`` commits on entry, so an in-request
        external call is never rolled back by a later failure); CLAUDE.md §6 forbids this module
        importing ``cloudflare``'s internals to make the first call itself; and #278 specified
        the push as retryable precisely so a half-applied connect is recoverable. The zone is
        the durable half, this is the repeatable half, and the "one action" lives in the web
        layer that can legitimately see both. ``docs/OXXA.md`` §7 records the whole flow.

        Idempotent by construction: a domain already delegated where it should be makes no call
        to OXXA at all and comes back ``changed=False``.
        """
        domain = await self._domain_or_404(domain_id)
        account = await self._resolve_account(payload.account_id)
        sld, tld = self._split(account, domain.name)
        client = self._client(account)
        wanted = [norm_host(ns) for ns in payload.nameservers]

        row = await self._register_row(account.id, domain.name, sld, tld)
        row.domain_id = domain.id
        # What we believed *before* the call, so ``changed`` describes this push rather than the
        # state it produced. A row we have never synced has no belief, which reads as changed —
        # the honest default when we cannot know.
        was_ref = row.nsgroup_ref
        was_observed = set(row.ns_observed or [])

        try:
            async with self.ctx.release_db():
                ref = await client.set_nameservers(sld, tld, wanted)
        except OxxaError as exc:
            # **Returned, not raised.** ``require_context`` rolls the session back on any
            # exception, so raising here would discard the very row this branch just wrote and
            # the panel would come back with an empty form — the user retyping nameservers they
            # already typed, with no record of why the last attempt failed. ``verify`` and
            # ``sync`` answer the same way for the same reason: "the registrar refused" is a
            # fact to report on the screen, not a 502 three screens away. A malformed *request*
            # still raises above this point, where nothing needs persisting.
            translated = self._translate(exc)
            row.ns_desired = wanted
            row.ns_push_status = NameserverPushStatus.ERROR.value
            row.last_error = redact(str(exc))[:500]
            await self.ctx.session.flush()
            logger.warning("oxxa: nameserver push refused for %s (%s)", domain.name, exc.code)
            return NameserverPushResult(
                ok=False,
                changed=False,
                nameservers=wanted,
                error=translated.message_key,
            )

        row.ns_desired = wanted
        row.nsgroup_ref = ref
        # Writing an *observation* from a write is the one place this module lets the two halves
        # touch, and it is defensible only because OXXA acknowledged ``domain_ns_upd``: the
        # registry holds this group now. It is still a claim rather than a read-back, so the
        # next sync overwrites it with what OXXA actually says — and if OXXA says something
        # else, that is drift, correctly reported.
        row.ns_observed = sorted(wanted)
        row.ns_push_status = NameserverPushStatus.ACTIVE.value
        row.ns_pushed_at = datetime.now(UTC)
        row.last_error = None
        await self.ctx.session.flush()
        await self.activity.record(
            DOMAIN_ENTITY,
            domain.id,
            "oxxa.nameservers_pushed",
            {"nameservers": wanted, "nsgroup": ref, "account": account.name},
        )
        return NameserverPushResult(
            ok=True,
            changed=was_ref != ref or was_observed != set(wanted),
            nameservers=wanted,
            nsgroup_ref=ref,
        )
