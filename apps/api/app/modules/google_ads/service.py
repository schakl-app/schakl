"""Linking Google Ads accounts, and resolving what it takes to call one.

Business-licensed — see LICENSE.

This is the Phase-1 half of the module: the credential, the account rows, the picker that finds
them and the probe that says whether they still answer. The reporting depth and the writes are
built on top of :meth:`GoogleAdsService.client_for`, which is the only way anything here reaches
Google.

Two rules from the rest of the codebase are load-bearing here and easy to lose:

* **Every in-request Google call happens inside ``ctx.release_db()``** (docs/PERFORMANCE.md).
  A request runs as one transaction pinning one pool connection; held across a call that may
  take thirty seconds, a handful of these drain the pool and every other request queues until
  ``pool_timeout``, which reads as the whole site freezing. Enter ``acting_as`` *first* — it
  reads settings — then release.
* **A probe fails softly and only a credential refused by everything is called broken**
  (CLAUDE.md §10, learned from Cloudflare). ``verify`` records what it found and never raises
  for a partial answer, and success **clears** ``status``/``last_error`` — a flag that only ever
  turns on leaves a red line on a row nothing is wrong with.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.crypto import decrypt, encrypt
from app.core.events import emit
from app.core.googleads import (
    AdsAccountRef,
    AdsCallParams,
    AdsClient,
    AdsCredentials,
    AdsError,
    AdsNotConfigured,
    ads_client,
    describe_failure,
    normalise_customer_id,
)
from app.errors import AppError
from app.modules.google import client as google_client
from app.modules.google.models import ConnectionStatus, GoogleConnection
from app.modules.google.oauth import SCOPE_ADS
from app.modules.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsAccountStatus,
    GoogleAdsSettings,
)

#: How many client accounts one manager (MCC) contributes to the picker. An agency MCC holds
#: tens; a reseller's holds thousands, and an unbounded read behind a combobox is how a picker
#: becomes a timeout (CLAUDE.md §9). Over the cap is **reported**, never silently dropped: a
#: picker listing 500 of an agency's 900 accounts and saying nothing looks exactly like an
#: agency with 500 accounts.
MAX_MANAGER_CHILDREN = 500

_ENTITY = "google_ads_account"
_SETTINGS_ENTITY = "google_ads_settings"

#: What an edit records. The token is deliberately absent — the trail says *that* it changed.
_TRACKED = ("company_id", "login_customer_id", "active", "connection_id")


@dataclass(frozen=True)
class AvailableAccount:
    """One pickable Ads account, as the live picker offers it."""

    customer_id: str
    descriptive_name: str
    login_customer_id: str | None
    currency_code: str | None
    #: ``"1234567890 · Breik hoofdaccount"`` — the combobox hint, so several connected Google
    #: accounts disambiguate without a grouping header.
    hint: str
    already_linked: bool = False


@dataclass(frozen=True)
class PickerResult:
    accounts: list[AvailableAccount]
    #: i18n keys for anything that limited the answer — a cap that was hit, a partial read.
    warnings: tuple[str, ...] = ()


class GoogleAdsService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.activity = ActivityService(ctx)

    # --- settings ------------------------------------------------------------------------- #

    async def settings_row(self, *, create: bool = False) -> GoogleAdsSettings | None:
        row = await self.ctx.session.scalar(
            select(GoogleAdsSettings).where(GoogleAdsSettings.org_id == self.ctx.org.id)
        )
        if row is None and create:
            row = GoogleAdsSettings(org_id=self.ctx.org.id)
            self.ctx.session.add(row)
            await self.ctx.session.flush()
        return row

    async def developer_token(self) -> str:
        """The org's developer token, or :class:`AdsNotConfigured`.

        Resolution is **new column → the module's own env fallback**. The legacy home
        (``marketing_settings.ads_developer_token_encrypted``) is *not* read here: this module
        may not read another module's table (§6), and the migration copied the value across, so
        an upgrading install already has it. Marketing keeps its own resolver for the case where
        this module is not installed at all.
        """
        row = await self.settings_row()
        if row is not None and row.developer_token_encrypted:
            try:
                return decrypt(row.developer_token_encrypted)
            except ValueError as exc:
                # A rotated SCHAKL_ENCRYPTION_KEY. A 409 naming the configuration, never a 500:
                # nothing is broken except that the stored secret can no longer be read.
                raise AppError(
                    "google_ads_token_unreadable",
                    "errors.google_ads_token_unreadable",
                    status_code=409,
                ) from exc
        from app.config import settings as app_settings

        token = (app_settings.google_ads_developer_token or "").strip()
        if not token:
            raise AdsNotConfigured("no google ads developer token configured")
        return token

    async def save_settings(
        self,
        *,
        developer_token: str | None = None,
        default_login_customer_id: str | None = None,
        writes_enabled: bool | None = None,
        developer_token_set: bool = False,
    ) -> GoogleAdsSettings:
        self.ctx.require("google_ads.settings.manage")
        row = await self.settings_row(create=True)
        assert row is not None
        changed: list[str] = []
        # Three states from one field, and all three are real (CLAUDE.md §18):
        #   absent            → leave it alone (a PATCH that is not about the token)
        #   ""                → leave it alone. **This is the load-bearing one**: the form posts
        #                       blank because nobody retyped a secret it never displayed, and
        #                       reading that as "clear it" wipes a working credential on every
        #                       unrelated save.
        #   explicit null     → clear it, deliberately
        #   a value           → set it
        if developer_token_set:
            if developer_token is None:
                if row.developer_token_encrypted is not None:
                    row.developer_token_encrypted = None
                    changed.append("developer_token_cleared")
            elif developer_token.strip():
                row.developer_token_encrypted = encrypt(developer_token.strip())
                changed.append("developer_token_changed")
        if default_login_customer_id is not None:
            row.default_login_customer_id = normalise_customer_id(default_login_customer_id) or None
            changed.append("default_login_customer_id")
        if writes_enabled is not None and writes_enabled != row.writes_enabled:
            row.writes_enabled = writes_enabled
            changed.append("writes_enabled")
        if changed:
            await self.activity.record(_SETTINGS_ENTITY, row.id, "updated", {"changed": changed})
        return row

    async def writes_enabled(self) -> bool:
        row = await self.settings_row()
        return True if row is None else row.writes_enabled

    async def require_writes_enabled(self) -> None:
        """The instance-wide kill switch, checked before any mutating Ads call.

        Separate from the permission and checked separately: the permission decides *who*, this
        decides *whether*. An owner who has just watched an agent do something surprising needs
        one switch that stops all of it without editing eight role grants.
        """
        if not await self.writes_enabled():
            raise AppError(
                "google_ads_writes_disabled",
                "errors.google_ads_writes_disabled",
                status_code=409,
            )

    # --- accounts ------------------------------------------------------------------------- #

    async def list_accounts(
        self, *, company_id: uuid.UUID | None = None, active_only: bool = False
    ) -> list[GoogleAdsAccount]:
        """Every linked account this caller may see.

        Rides ``scoped_select()`` rather than a hand-built ``where(org_id == …)``: this is a
        parameterless list returning ``descriptive_name``, which for a client account *is* the
        client's name — exactly the shape ``test_company_groups``' sweep hunts for. Marketing's
        own cross-client grid shipped with that bug once (#252).
        """
        stmt = self.ctx.repo(GoogleAdsAccount).scoped_select()
        if company_id is not None:
            stmt = stmt.where(GoogleAdsAccount.company_id == company_id)
        if active_only:
            stmt = stmt.where(GoogleAdsAccount.active.is_(True))
        stmt = stmt.order_by(GoogleAdsAccount.descriptive_name, GoogleAdsAccount.customer_id)
        return list((await self.ctx.session.scalars(stmt)).all())

    async def get_account(self, account_id: uuid.UUID) -> GoogleAdsAccount:
        """One account, 404 if it is outside this caller's tenant or company horizon.

        404 rather than 403 (§15): an account the caller may not see must not be revealed to
        exist by the difference between two status codes.
        """
        row = await self.ctx.session.scalar(
            self.ctx.repo(GoogleAdsAccount).scoped_select().where(GoogleAdsAccount.id == account_id)
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def attach(
        self,
        *,
        customer_id: str,
        company_id: uuid.UUID | None = None,
        login_customer_id: str | None = None,
        connection_id: uuid.UUID | None = None,
        descriptive_name: str = "",
        currency_code: str | None = None,
        time_zone: str | None = None,
    ) -> GoogleAdsAccount:
        """Idempotent upsert on ``(org_id, customer_id)``.

        Idempotent because another module's write path calls it: ``POST /marketing/links`` with
        ``source=gads`` attaches here too, and an insert that could raise a unique violation
        would turn a working, shipped endpoint into a 500 the first time two clients share an
        Ads account — which is an ordinary arrangement, not an edge case.

        It also **emits** ``google_ads.account.attached`` (#338), which is the mirror of that
        same sentence in the other direction. Before it, an account linked here recorded half
        the fact: the client's page listed the Ads account in one panel while the marketing
        panel directly above it — and ``/marketing`` — went on saying nothing was connected,
        because ``marketing_links`` had no row. The event is the seam (CLAUDE.md §6): this
        module names ``marketing`` nowhere, and an instance running without it simply has no
        subscriber. Only an account with a **client** emits — a marketing link requires a
        company, and an account attached to none is the agency's own.
        """
        cid = normalise_customer_id(customer_id)
        if not cid:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"customer_id": "errors.google_ads_customer_id_invalid"},
            )
        repo = self.ctx.repo(GoogleAdsAccount)
        existing = await self.ctx.session.scalar(
            repo.scoped_select().where(GoogleAdsAccount.customer_id == cid)
        )
        if existing is not None:
            before = snapshot(existing, _TRACKED)
            # Only fill what the caller actually knows. A picker that has a name and a currency
            # must not blank the manager id a previous link resolved.
            if company_id is not None:
                repo._guard_company_write({"company_id": company_id})
                existing.company_id = company_id
            if login_customer_id:
                existing.login_customer_id = normalise_customer_id(login_customer_id) or None
            if connection_id is not None:
                existing.connection_id = connection_id
            if descriptive_name:
                existing.descriptive_name = descriptive_name
            if currency_code:
                existing.currency_code = currency_code
            if time_zone:
                existing.time_zone = time_zone
            if not existing.active:
                existing.active = True
            await self.ctx.session.flush()
            await self.activity.record_update(
                _ENTITY, existing.id, before, snapshot(existing, _TRACKED)
            )
            await self._emit_attached(existing)
            return existing

        repo._guard_company_write({"company_id": company_id})
        row = GoogleAdsAccount(
            org_id=self.ctx.org.id,
            customer_id=cid,
            company_id=company_id,
            login_customer_id=normalise_customer_id(login_customer_id) or None,
            connection_id=connection_id,
            descriptive_name=descriptive_name or cid,
            currency_code=currency_code,
            time_zone=time_zone,
        )
        self.ctx.session.add(row)
        await self.ctx.session.flush()
        await self.activity.record_created(
            _ENTITY, row.id, {"customer_id": cid, "company_id": str(company_id or "")}
        )
        await self._emit_attached(row)
        return row

    async def _emit_attached(self, row: GoogleAdsAccount) -> None:
        """Announce that this client's Ads account is on file (#338).

        Handlers run inline in this write's transaction, so the account and whatever reacts to
        it commit together or not at all — which is the whole point: two rows describing one
        fact must never be able to half-exist.
        """
        if row.company_id is None:
            return
        await emit(
            "google_ads.account.attached",
            self.ctx,
            {
                "account_id": row.id,
                "company_id": row.company_id,
                "customer_id": row.customer_id,
                "descriptive_name": row.descriptive_name,
                "currency_code": row.currency_code,
                "login_customer_id": row.login_customer_id,
                "connection_id": row.connection_id,
            },
        )

    async def update_account(
        self,
        row: GoogleAdsAccount,
        *,
        company_id: uuid.UUID | None = None,
        login_customer_id: str | None = None,
        active: bool | None = None,
        company_id_set: bool = False,
    ) -> GoogleAdsAccount:
        """Edit the fields schakl *decided*. What Google said is refreshed by verify, never typed.

        ``company_id_set`` carries the absent-vs-null distinction the payload alone cannot
        (CLAUDE.md §18): omitted means leave the client alone, an explicit ``null`` detaches the
        account from its client — which is a real state (the agency's own account), not an
        accident.
        """
        self.ctx.require("google_ads.settings.manage")
        before = snapshot(row, _TRACKED)
        if company_id_set:
            self.ctx.repo(GoogleAdsAccount)._guard_company_write({"company_id": company_id})
            row.company_id = company_id
        if login_customer_id is not None:
            row.login_customer_id = normalise_customer_id(login_customer_id) or None
        if active is not None:
            row.active = active
        await self.ctx.session.flush()
        await self.activity.record_update(_ENTITY, row.id, before, snapshot(row, _TRACKED))
        return row

    async def unlink(self, account_id: uuid.UUID) -> None:
        """Deactivate rather than delete. History attached to this account outlives the link,
        and a re-link must find the same row rather than mint a second one with the same
        customer id — which the unique constraint would refuse anyway."""
        self.ctx.require("google_ads.settings.manage")
        row = await self.get_account(account_id)
        if row.active:
            before = snapshot(row, _TRACKED)
            row.active = False
            await self.activity.record_update(_ENTITY, row.id, before, snapshot(row, _TRACKED))

    # --- credentials + client -------------------------------------------------------------- #

    async def _connection(self, account: GoogleAdsAccount) -> GoogleConnection:
        if account.connection_id is None:
            raise AdsNotConfigured("this ads account has no google connection")
        connection = await self.ctx.session.scalar(
            select(GoogleConnection).where(
                GoogleConnection.org_id == self.ctx.org.id,
                GoogleConnection.id == account.connection_id,
            )
        )
        if connection is None:
            raise AdsNotConfigured("this ads account has no google connection")
        if connection.status != ConnectionStatus.ACTIVE.value:
            raise AdsNotConfigured("the google connection for this ads account needs reconnecting")
        if SCOPE_ADS not in set(connection.scopes or []):
            raise AdsNotConfigured("the google connection does not carry the adwords scope")
        return connection

    async def call_params(
        self,
        *,
        account_id: uuid.UUID | None = None,
        customer_id: str | None = None,
    ) -> AdsCallParams:
        """Resolve one account plus the credentials to reach it with, in one place.

        The login-customer-id falls back to the org default: an agency with one MCC sets it once
        rather than on every row, and a row that names its own always wins.
        """
        account = await self._resolve(account_id=account_id, customer_id=customer_id)
        token = await self.developer_token()
        settings_row = await self.settings_row()
        manager = account.login_customer_id or (
            settings_row.default_login_customer_id if settings_row else None
        )
        return AdsCallParams(
            account=_ref(account),
            credentials=AdsCredentials(developer_token=token, login_customer_id=manager),
        )

    async def _resolve(
        self, *, account_id: uuid.UUID | None, customer_id: str | None
    ) -> GoogleAdsAccount:
        if account_id is not None:
            return await self.get_account(account_id)
        cid = normalise_customer_id(customer_id)
        if not cid:
            raise AdsNotConfigured("no google ads account named")
        row = await self.ctx.session.scalar(
            self.ctx.repo(GoogleAdsAccount)
            .scoped_select()
            .where(GoogleAdsAccount.customer_id == cid)
        )
        if row is None:
            raise AdsNotConfigured(f"no linked google ads account for customer {cid}")
        return row

    @asynccontextmanager
    async def open_client(
        self,
        *,
        account_id: uuid.UUID | None = None,
        customer_id: str | None = None,
        tool: str = "",
    ) -> AsyncIterator[tuple[AdsClient, GoogleAdsAccount]]:
        """The one way anything in this module reaches Google: an :class:`AdsClient` bound to a
        linked account, with the pooled database connection **released** for the duration.

        Everything the session is needed for — the account row, the developer token, the
        connection and its tokens — is read before the release, in that order, because the
        first statement after the block re-binds the RLS GUC and nothing inside it may query.
        """
        account = await self._resolve(account_id=account_id, customer_id=customer_id)
        params = await self.call_params(account_id=account.id)
        connection = await self._connection(account)
        async with (
            ads_client(
                self.ctx.session, self.ctx.org, connection, params.credentials, tool=tool
            ) as client,
            self.ctx.release_db(),
        ):
            yield client, account

    async def accounts_for_company(self, company_id: uuid.UUID) -> list[AdsAccountRef]:
        rows = await self.list_accounts(company_id=company_id, active_only=True)
        return [_ref(row) for row in rows]

    # --- the live picker -------------------------------------------------------------------- #

    async def available_accounts(self) -> PickerResult:
        """Every Ads account the caller's own Google grant can reach, MCC hierarchies expanded.

        ``listAccessibleCustomers`` answers only what the Google user was granted **directly**,
        and an agency's user is granted the *manager* — so the raw list is one MCC and the picker
        is empty of every client under it. Each manager is walked once with a ``customer_client``
        query, which returns the whole tree beneath it in one call, and every child is tagged
        with the manager it must be reached through.

        Manager accounts are not offered: Google refuses metric queries against one, so linking
        it would produce a permanently erroring row rather than a roll-up.
        """
        self.ctx.require("google_ads.settings.manage")
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
            raise AdsNotConfigured("no active google connection for this user")
        if SCOPE_ADS not in set(connection.scopes or []):
            raise AdsNotConfigured("the google connection does not carry the adwords scope")
        token = await self.developer_token()
        linked = {row.customer_id for row in await self.list_accounts()}

        warnings: list[str] = []
        options: list[AvailableAccount] = []
        seen: set[str] = set()
        credentials = AdsCredentials(developer_token=token)
        async with (
            ads_client(
                self.ctx.session, self.ctx.org, connection, credentials, tool="picker"
            ) as client,
            self.ctx.release_db(),
        ):
            for customer_id in await client.accessible_customers():
                meta = await self._customer_meta(client, customer_id)
                if meta is None:
                    continue
                name, currency, is_manager, _tz = meta
                if is_manager:
                    children, capped = await self._manager_children(client, customer_id, name)
                    if capped:
                        warnings.append("google_ads.warning.manager_children_capped")
                    candidates = children
                else:
                    candidates = [
                        AvailableAccount(
                            customer_id=customer_id,
                            descriptive_name=name or customer_id,
                            login_customer_id=None,
                            currency_code=currency,
                            hint=customer_id,
                        )
                    ]
                for option in candidates:
                    # A user granted both an MCC and one of its clients directly would otherwise
                    # see that client twice, under two different configs.
                    if option.customer_id in seen:
                        continue
                    seen.add(option.customer_id)
                    options.append(
                        AvailableAccount(
                            **{
                                **option.__dict__,
                                "already_linked": option.customer_id in linked,
                            }
                        )
                    )
        options.sort(key=lambda o: o.descriptive_name.casefold())
        return PickerResult(accounts=options, warnings=tuple(dict.fromkeys(warnings)))

    async def _customer_meta(
        self, client: AdsClient, customer_id: str
    ) -> tuple[str, str | None, bool, str | None] | None:
        """``(name, currency, is_manager, time_zone)`` for one directly-accessible customer.

        ``customer.manager`` is the only thing separating an MCC from an ordinary advertiser,
        and ``listAccessibleCustomers`` returns bare ids. A customer that refuses is skipped
        rather than failing the whole picker — one revoked grant among ten must not empty it.
        """
        try:
            row = await client.search_one(
                customer_id,
                "SELECT customer.descriptive_name, customer.currency_code, "
                "customer.manager, customer.time_zone FROM customer",
                context="customer_meta",
            )
        except AdsError:
            return None
        if row is None:
            return None
        customer = row.get("customer", {})
        return (
            customer.get("descriptiveName", "") or customer_id,
            customer.get("currencyCode") or None,
            bool(customer.get("manager", False)),
            customer.get("timeZone") or None,
        )

    async def _manager_children(
        self, client: AdsClient, manager_id: str, manager_name: str
    ) -> tuple[list[AvailableAccount], bool]:
        """The enabled, non-manager accounts anywhere under ``manager_id`` — one query.

        ``customer_client`` returns the manager's whole hierarchy, not just its direct children,
        so a nested MCC needs no recursion. Sub-managers are filtered out and their clients kept:
        every one is still reachable with this top-level manager as ``login-customer-id``, which
        is what makes one tag per child correct.

        The LIMIT is one over the cap purely so "there were more" is detectable rather than
        guessed at.
        """
        query = (
            "SELECT customer_client.id, customer_client.descriptive_name, "
            "customer_client.currency_code, customer_client.time_zone "
            "FROM customer_client "
            "WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED' "
            f"LIMIT {MAX_MANAGER_CHILDREN + 1}"
        )
        rows = await client.search(manager_id, query, context="manager_children")
        capped = len(rows) > MAX_MANAGER_CHILDREN
        out: list[AvailableAccount] = []
        for row in rows[:MAX_MANAGER_CHILDREN]:
            child = row.get("customerClient", {})
            child_id = normalise_customer_id(child.get("id"))
            if not child_id:
                continue
            out.append(
                AvailableAccount(
                    customer_id=child_id,
                    descriptive_name=child.get("descriptiveName") or child_id,
                    # The load-bearing half: without it every later call to this account is made
                    # by a user who has no direct grant on it, and 403s.
                    login_customer_id=manager_id,
                    currency_code=child.get("currencyCode") or None,
                    hint=f"{child_id} · {manager_name}",
                )
            )
        return out, capped

    # --- health --------------------------------------------------------------------------- #

    async def verify(self, account_id: uuid.UUID) -> GoogleAdsAccount:
        """Ask Google what it says about this account, and record the answer either way.

        Success **clears** ``status`` and ``last_error``. That is not politeness: a flag that
        only ever turns on leaves a red line on a row nothing is wrong with, through every sync
        that works afterwards, and the only way anyone finds out is by not trusting the flag.
        """
        self.ctx.require("google_ads.settings.manage")
        account = await self.get_account(account_id)
        try:
            params = await self.call_params(account_id=account_id)
            connection = await self._connection(account)
            async with (
                ads_client(
                    self.ctx.session,
                    self.ctx.org,
                    connection,
                    params.credentials,
                    tool="verify",
                ) as client,
                self.ctx.release_db(),
            ):
                meta = await self._customer_meta(client, account.customer_id)
        except AdsNotConfigured as exc:
            account.status = GoogleAdsAccountStatus.ERROR.value
            account.last_error = str(exc)[:500]
            account.last_verified_at = datetime.now(UTC)
            return account
        except AdsError as exc:
            account.status = GoogleAdsAccountStatus.ERROR.value
            account.last_error = describe_failure(exc)
            account.last_verified_at = datetime.now(UTC)
            return account
        if meta is None:
            account.status = GoogleAdsAccountStatus.ERROR.value
            account.last_error = "the account did not answer"
            account.last_verified_at = datetime.now(UTC)
            return account
        name, currency, is_manager, time_zone = meta
        account.descriptive_name = name
        account.currency_code = currency or account.currency_code
        account.time_zone = time_zone or account.time_zone
        account.is_manager = is_manager
        account.status = GoogleAdsAccountStatus.ACTIVE.value
        account.last_error = None
        account.last_verified_at = datetime.now(UTC)
        return account


def _ref(row: GoogleAdsAccount) -> AdsAccountRef:
    return AdsAccountRef(
        id=row.id,
        customer_id=row.customer_id,
        login_customer_id=row.login_customer_id,
        company_id=row.company_id,
        connection_id=row.connection_id,
        descriptive_name=row.descriptive_name,
        currency_code=row.currency_code,
        time_zone=row.time_zone,
        active=row.active,
    )
