"""Connecting, verifying and configuring a SnelStart administration (epic #377).

Business-licensed — see LICENSE.

Three rules this file never bends, all of them learned elsewhere in this codebase and all of
them worse to get wrong here because the subject is money.

**Verify never raises.** ``require_context`` rolls the session back on any exception, so raising
from a verify would discard the very row that records what SnelStart said. The result is
returned, the row keeps the failure on ``last_error``, and the settings screen shows it. This is
``mollie``'s rule; what it buys here is bigger, because *which* credential was refused is the
whole diagnosis and an exception loses it.

**The credential is read exactly once, in :func:`client_for`.** Fernet at rest, never echoed,
never logged, never in an error envelope.

**Two credentials, two error paths.** A koppelsleutel is the tenant's and an agency re-issues it
in ten seconds; a subscription key is the *install's* and only its operator can. Reporting one
as the other sends an admin to re-do the thing that was already right, so
:class:`SnelstartSubscriptionError` survives all the way to its own i18n key.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.core.activity import ActivityService
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.snelstart.client import (
    ACTIVATION_URL,
    SnelstartAuthError,
    SnelstartClient,
    SnelstartError,
    SnelstartSubscriptionError,
    redact,
)
from app.integrations.snelstart.models import (
    SnelstartAccount,
    SnelstartAccountStatus,
    SnelstartConnectMethod,
    SnelstartLink,
    SnelstartRef,
    SnelstartRefKind,
    SnelstartSyncKind,
    SnelstartSyncRun,
)
from app.integrations.snelstart.schemas import (
    SnelstartAccountCreate,
    SnelstartAccountUpdate,
    SnelstartVerifyResult,
)

logger = logging.getLogger("schakl.snelstart")

ACCOUNT_ENTITY = "snelstart_account"

#: Which reference lists are pulled, and where from. A dict rather than five methods because
#: nothing about them differs except the resource and how a row names itself.
REFERENCE_SOURCES: dict[str, tuple[str, str, str]] = {
    # kind: (resource, code field, name field)
    SnelstartRefKind.LEDGER.value: ("grootboeken", "nummer", "omschrijving"),
    SnelstartRefKind.JOURNAL.value: ("dagboeken", "nummer", "omschrijving"),
    SnelstartRefKind.COST_CENTRE.value: ("kostenplaatsen", "nummer", "omschrijving"),
    SnelstartRefKind.COUNTRY.value: ("landen", "landcode", "naam"),
    SnelstartRefKind.REVENUE_GROUP.value: ("artikelomzetgroepen", "nummer", "omschrijving"),
}

#: A run stores at most this many per-row failures. A sync against a credential that lost its
#: write scope would otherwise write one entry per row in the administration into a JSONB column
#: nobody will read past the tenth. The count in ``counts["failed"]`` stays exact.
MAX_RUN_ERRORS = 50


def new_secret() -> str:
    """The secret half of a coupling ``referenceKey``. 32 bytes of urlsafe randomness."""
    return secrets.token_urlsafe(32)[:64]


def subscription_key_for(account: SnelstartAccount) -> str:
    """The partner key this account calls with — its own, or the instance's.

    Refused rather than defaulted when neither exists: a call with an empty subscription key
    fails at Azure's gateway with a message about a *subscription*, which an admin reads as a
    problem with their SnelStart licence. Naming the missing deployment setting instead is the
    difference between a five-minute fix and a support ticket.
    """
    if account.subscription_key_encrypted:
        return decrypt(account.subscription_key_encrypted)
    key = (settings.snelstart_subscription_key or "").strip()
    if not key:
        raise AppError(
            "snelstart_subscription_key_missing",
            "errors.snelstart.subscription_key_missing",
            status_code=409,
        )
    return key


def client_for(account: SnelstartAccount) -> SnelstartClient:
    """A live client for one stored credential.

    Module-level rather than a method for the reason ``mollie``'s is: decrypting in exactly one
    place is what makes "the key is read once" a checkable claim rather than a hope.
    """
    if not account.client_key_encrypted:
        raise AppError(
            "snelstart_not_connected", "errors.snelstart.not_connected", status_code=409
        )
    return SnelstartClient(
        client_key=decrypt(account.client_key_encrypted),
        subscription_key=subscription_key_for(account),
    )


def translate(exc: Exception) -> AppError:
    """A client exception as the standard envelope (§9) — ``message`` is always an i18n key.

    The three-way split is the point. A rejected **koppelsleutel** is the tenant's to re-issue;
    a rejected **subscription key** is the operator's to renew (and expires after 90 days on the
    free product, which is exactly the failure that looks like nothing changed); anything else is
    SnelStart being unreachable, which is nobody's fault and resolves itself.
    """
    if isinstance(exc, SnelstartSubscriptionError):
        return AppError(
            "snelstart_subscription_rejected",
            "errors.snelstart.subscription_rejected",
            status_code=409,
        )
    if isinstance(exc, SnelstartAuthError):
        return AppError(
            "snelstart_credential_rejected",
            "errors.snelstart.credential_rejected",
            status_code=409,
        )
    if isinstance(exc, SnelstartError) and exc.http_status is None:
        return AppError("snelstart_unreachable", "errors.snelstart.unreachable", status_code=502)
    return AppError("snelstart_request_failed", "errors.snelstart.request_failed", status_code=502)


class SnelstartAccountService:
    """The credential screen: connect, rotate, verify, configure, disconnect."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.accounts = ctx.repo(SnelstartAccount)
        self.refs = ctx.repo(SnelstartRef)
        self.runs = ctx.repo(SnelstartSyncRun)
        self.activity = ActivityService(ctx)

    # --- reads ------------------------------------------------------------------- #
    async def list_accounts(self) -> list[dict[str, Any]]:
        rows = await self.ctx.session.execute(
            self.accounts.scoped_select().order_by(SnelstartAccount.name)
        )
        accounts = list(rows.scalars())
        counts = await self._link_counts([a.id for a in accounts])
        return [self.serialize(a, counts.get(a.id, {})) for a in accounts]

    async def _link_counts(
        self, account_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, int]]:
        """How many links of each kind and status, per account — in **one** query.

        A loop of counts per account is the shape that passes every functional test at three
        accounts and falls over at three hundred links (``docs/PERFORMANCE.md``); it is also
        pointless here, since the grouping the screen wants is a single ``GROUP BY``.
        """
        if not account_ids:
            return {}
        stmt = (
            select(
                SnelstartLink.account_id,
                SnelstartLink.kind,
                SnelstartLink.status,
                func.count(),
            )
            .where(
                SnelstartLink.org_id == self.ctx.org.id,
                SnelstartLink.account_id.in_(account_ids),
            )
            .group_by(SnelstartLink.account_id, SnelstartLink.kind, SnelstartLink.status)
        )
        out: dict[uuid.UUID, dict[str, int]] = {}
        for account_id, kind, status, total in await self.ctx.session.execute(stmt):
            out.setdefault(account_id, {})[f"{kind}.{status}"] = int(total)
        return out

    def serialize(
        self, account: SnelstartAccount, counts: dict[str, int] | None = None
    ) -> dict[str, Any]:
        """The account as the settings screen reads it. **Never a credential.**"""
        return {
            "id": account.id,
            "name": account.name,
            "connected": bool(account.client_key_encrypted),
            "connect_method": account.connect_method,
            "own_subscription_key": bool(account.subscription_key_encrypted),
            "administration_id": account.administration_id,
            "administration_name": account.administration_name,
            "financial_year": (account.company_info or {}).get("huidigBoekjaar"),
            "article_code_kind": account.article_code_kind,
            "article_code_max_length": account.article_code_max_length,
            "scopes": list(account.scopes or []),
            "default_ledger_code": account.default_ledger_code,
            "auto_push_invoices": account.auto_push_invoices,
            "attach_invoice_pdf": account.attach_invoice_pdf,
            "pull_payments": account.pull_payments,
            "provider_id": account.provider_id,
            "active": account.active,
            "status": account.status,
            "last_verified_at": account.last_verified_at,
            "last_reference_sync_at": account.last_reference_sync_at,
            "last_synced_at": account.last_synced_at,
            "last_error": account.last_error,
            "activation_url": self.activation_url(account),
            "coupling_webhook_url": coupling_webhook_url(),
            "counts": counts or {},
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }

    def activation_url(self, account: SnelstartAccount) -> str:
        """Where to send the tenant so SnelStart hands us the key, or ``""``.

        Empty when the install has no ``appShortName`` — which is the normal self-hosted case —
        and the screen then renders no activation button at all rather than one that leads to a
        404 at SnelStart (#253: a control that always refuses is a broken control).
        """
        shortname = (settings.snelstart_app_shortname or "").strip()
        if not shortname:
            return ""
        from urllib.parse import quote

        reference = coupling_reference(self.ctx.org.id, account.id, account.connect_secret)
        base = ACTIVATION_URL.format(shortname=quote(shortname, safe=""))
        return f"{base}?referenceKey={quote(reference, safe='')}"

    # --- writes ------------------------------------------------------------------ #
    async def create_account(self, payload: SnelstartAccountCreate) -> SnelstartAccount:
        await self._assert_name_free(payload.name)
        client_key = (payload.client_key or "").strip()
        account = await self.accounts.create(
            name=payload.name.strip(),
            client_key_encrypted=encrypt(client_key) if client_key else None,
            subscription_key_encrypted=(
                encrypt(payload.subscription_key.strip()) if payload.subscription_key else None
            ),
            connect_method=(
                SnelstartConnectMethod.MANUAL.value
                if client_key
                else SnelstartConnectMethod.COUPLING.value
            ),
            connect_secret=new_secret(),
            provider_id=await self._validated_provider(payload.provider_id),
            active=payload.active,
            status=(
                SnelstartAccountStatus.ACTIVE.value
                if client_key
                else SnelstartAccountStatus.PENDING.value
            ),
        )
        await self.ctx.session.flush()
        await self.activity.record_created(
            ACCOUNT_ENTITY, account.id, {"name": account.name, "method": account.connect_method}
        )
        return account

    async def update_account(
        self, account_id: uuid.UUID, payload: SnelstartAccountUpdate
    ) -> SnelstartAccount:
        account = await self.accounts.get_or_404(account_id)
        if payload.name and payload.name.strip() != account.name:
            await self._assert_name_free(payload.name, exclude=account.id)
            account.name = payload.name.strip()

        rotated = False
        if payload.client_key:
            self._adopt_key(account, payload.client_key.strip())
            rotated = True
        if payload.subscription_key is not None:
            account.subscription_key_encrypted = (
                encrypt(payload.subscription_key.strip())
                if payload.subscription_key.strip()
                else None
            )

        if payload.provider_id is not None:
            account.provider_id = await self._validated_provider(payload.provider_id)
        for attr in (
            "active",
            "auto_push_invoices",
            "attach_invoice_pdf",
            "pull_payments",
        ):
            value = getattr(payload, attr)
            if value is not None:
                setattr(account, attr, value)
        if payload.default_ledger_code is not None:
            account.default_ledger_code = payload.default_ledger_code.strip() or None

        await self.ctx.session.flush()
        # Refresh before serialising: ``updated_at`` is a server default and reading it off a
        # flushed-but-unrefreshed instance raises MissingGreenlet under asyncio.
        await self.ctx.session.refresh(account)
        if rotated:
            await self.activity.record(ACCOUNT_ENTITY, account.id, "snelstart.credential_rotated")
        return account

    def _adopt_key(self, account: SnelstartAccount, client_key: str) -> None:
        """Store a new koppelsleutel and forget everything the old one told us.

        Every observation on this row was made through the credential being replaced — which
        administration it opened, what the token was allowed to do, whether it worked. Keeping
        any of it would let a screen say "connected to Marcusse Online Marketing" about a key
        that now opens somebody else's books. The *links* are deliberately kept: a rotation is
        usually a rotation, not a move, and re-verifying is what proves which.
        """
        account.client_key_encrypted = encrypt(client_key)
        account.administration_id = None
        account.administration_name = None
        account.company_info = {}
        account.article_code_kind = None
        account.article_code_max_length = None
        account.scopes = []
        account.last_verified_at = None
        account.status = SnelstartAccountStatus.ACTIVE.value
        account.last_error = None
        account.connect_secret = new_secret()

    async def delete_account(self, account_id: uuid.UUID) -> None:
        account = await self.accounts.get_or_404(account_id)
        await self.activity.record(ACCOUNT_ENTITY, account.id, "deleted", {"name": account.name})
        await self.ctx.session.delete(account)
        await self.ctx.session.flush()

    # --- verify ------------------------------------------------------------------ #
    async def verify(self, account_id: uuid.UUID) -> SnelstartVerifyResult:
        """Prove both credentials work, and learn *which administration* they open.

        ``GET /companyInfo`` rather than a ping, deliberately: a credential that merely works
        still lets somebody connect the wrong company's books, and finding that out at the first
        invoice is finding it out in an accountant's ledger. It is also where the two
        article-code rules come from, which decide whether a product can be pushed at all.

        **Returns rather than raises** on a rejection — the probe succeeded, its answer was no,
        and the row keeps SnelStart's own words on it.
        """
        account = await self.accounts.get_or_404(account_id)
        if not account.client_key_encrypted:
            return SnelstartVerifyResult(ok=False, error_key="errors.snelstart.not_connected")
        now = datetime.now(UTC)
        try:
            client = client_for(account)
        except AppError as exc:
            account.status = SnelstartAccountStatus.ERROR.value
            account.last_error = exc.message_key
            account.last_verified_at = now
            await self.ctx.session.flush()
            return SnelstartVerifyResult(ok=False, error_key=exc.message_key)

        try:
            async with self.ctx.release_db():
                info = await client.company_info()
                own = await client.own_relation()
                scopes = list(client.scopes)
        except SnelstartError as exc:
            account.status = SnelstartAccountStatus.ERROR.value
            account.last_error = redact(str(exc))[:500]
            account.last_verified_at = now
            await self.ctx.session.flush()
            return SnelstartVerifyResult(
                ok=False,
                error=account.last_error,
                error_key=translate(exc).message_key,
            )

        account.company_info = info
        account.administration_name = str(info.get("administratieNaam") or "")[:255] or None
        account.administration_id = _as_uuid(info.get("administratieIdentifier"))
        account.article_code_kind = str(info.get("artikelcodeSoort") or "") or None
        length = info.get("artikelcodeMaxLengte")
        account.article_code_max_length = int(length) if isinstance(length, int) else None
        account.scopes = scopes
        account.status = SnelstartAccountStatus.ACTIVE.value
        account.last_error = None
        account.last_verified_at = now
        await self.ctx.session.flush()
        await self.activity.record(
            ACCOUNT_ENTITY,
            account.id,
            "snelstart.verified",
            {"administration": account.administration_name},
        )
        return SnelstartVerifyResult(
            ok=True,
            administration_name=account.administration_name,
            administration_id=account.administration_id,
            financial_year=info.get("huidigBoekjaar"),
            scopes=scopes,
            #: The seller block SnelStart itself prints. Handed back so the screen can show the
            #: two side by side — an agency whose invoice says one address and whose bookkeeper
            #: sends reminders from another has a problem worth seeing before a client does.
            seller=_seller_of(info, own),
            missing_scopes=_missing_scopes(scopes),
        )

    # --- reference data ---------------------------------------------------------- #
    async def sync_reference(self, account_id: uuid.UUID) -> SnelstartSyncRun:
        """Cache the administration's own vocabulary: ledgers, journals, countries, groups.

        Read wholesale rather than incrementally. A chart of accounts is 233 rows and changes
        when a bookkeeper adds an account, which no ``modifiedOn`` filter would tell us about
        reliably — and a *stale* ledger list is the one that books revenue to an account that no
        longer exists.
        """
        account = await self.accounts.get_or_404(account_id)
        run = await self._start_run(account, SnelstartSyncKind.REFERENCE)
        try:
            client = client_for(account)
        except AppError as exc:
            return await self._fail_run(run, account, exc.message_key)

        counts: dict[str, int] = {}
        try:
            async with self.ctx.release_db():
                fetched = {
                    kind: await client.fetch_all(resource)
                    for kind, (resource, _, _) in REFERENCE_SOURCES.items()
                }
                # The rate table is not a list of rows with ids — it is a schedule, and it is
                # stored whole on the account rather than as refs, because "which percentage was
                # Laag in 2018" is a question about the schedule and not about any one row.
                vat_rates = await client.fetch_all("btwtarieven")
        except SnelstartError as exc:
            return await self._fail_run(run, account, redact(str(exc))[:500], exc=exc)

        for kind, rows in fetched.items():
            _, code_field, name_field = REFERENCE_SOURCES[kind]
            counts[kind] = await self._store_refs(account, kind, rows, code_field, name_field)
        await self._store_vat_rates(account, vat_rates)
        counts["vat_rate"] = len(vat_rates)

        account.last_reference_sync_at = datetime.now(UTC)
        account.status = SnelstartAccountStatus.ACTIVE.value
        account.last_error = None
        return await self._finish_run(run, ok=True, counts=counts)

    async def _store_refs(
        self,
        account: SnelstartAccount,
        kind: str,
        rows: list[dict[str, Any]],
        code_field: str,
        name_field: str,
    ) -> int:
        """Replace this kind's cache with what SnelStart just said.

        An entry SnelStart no longer reports is marked inactive rather than deleted: a stored
        mapping may still name it, and a picker that silently loses the account an invoice books
        to teaches nobody anything. Inactive rows stop being offered and keep resolving.
        """
        existing = {
            row.external_id: row
            for row in (
                await self.ctx.session.execute(
                    self.refs.scoped_select().where(
                        SnelstartRef.account_id == account.id, SnelstartRef.kind == kind
                    )
                )
            ).scalars()
        }
        seen: set[str] = set()
        for row in rows:
            external_id = str(row.get("id") or "")
            if not external_id:
                continue
            seen.add(external_id)
            code = row.get(code_field)
            values = {
                "code": str(code)[:80] if code not in (None, "") else None,
                "name": str(row.get(name_field) or "")[:255],
                "data": row,
                "active": not bool(row.get("nonactief") or row.get("isNonActief")),
            }
            current = existing.get(external_id)
            if current is None:
                await self.refs.create(
                    account_id=account.id, kind=kind, external_id=external_id, **values
                )
            else:
                for key, value in values.items():
                    setattr(current, key, value)
        for external_id, row in existing.items():
            if external_id not in seen:
                row.active = False
        await self.ctx.session.flush()
        return len(seen)

    async def _store_vat_rates(
        self, account: SnelstartAccount, rows: list[dict[str, Any]]
    ) -> None:
        """The btw schedule, on the account.

        Kept in :attr:`SnelstartAccount.company_info` under a private key rather than as
        ``snelstart_refs`` rows, because the rows have no ids — they are ``(soort, percentage,
        from, until)`` tuples — and a cache keyed on an id that does not exist would need a
        synthetic one that changes whenever SnelStart reorders the list.
        """
        info = dict(account.company_info or {})
        info["_btwtarieven"] = rows
        account.company_info = info

    async def vat_rates(self, account: SnelstartAccount) -> list[dict[str, Any]]:
        rates = (account.company_info or {}).get("_btwtarieven")
        return rates if isinstance(rates, list) else []

    async def ledger_options(self, account_id: uuid.UUID) -> list[dict[str, Any]]:
        """The revenue accounts an invoice line may book to.

        Narrowed to ``Verkopen*``/``DienstverleningBinnenEU`` ``grootboekfunctie`` values plus
        anything whose number is in the 8000–8999 revenue band, because offering a bookkeeper
        all 233 accounts — including *Btw af te dragen hoog* — is offering them a way to make an
        invoice that does not balance. ``Diversen`` accounts in the band are kept: an agency
        that books its hosting resale to 8299 is not doing anything wrong.
        """
        account = await self.accounts.get_or_404(account_id)
        rows = (
            await self.ctx.session.execute(
                self.refs.scoped_select()
                .where(
                    SnelstartRef.account_id == account.id,
                    SnelstartRef.kind == SnelstartRefKind.LEDGER.value,
                    SnelstartRef.active.is_(True),
                )
                .order_by(SnelstartRef.code)
            )
        ).scalars()
        options: list[dict[str, Any]] = []
        for row in rows:
            data = row.data or {}
            function = str(data.get("grootboekfunctie") or "")
            number = data.get("nummer")
            revenue_band = isinstance(number, int) and 8000 <= number <= 8999
            if not (function.startswith(("Verkopen", "Dienstverlening")) or revenue_band):
                continue
            options.append(
                {
                    "id": row.external_id,
                    "code": row.code or "",
                    "name": row.name,
                    "function": function,
                    "vat_kinds": list(data.get("btwSoort") or []),
                }
            )
        return options

    async def resolve_ledger(
        self, account: SnelstartAccount, code: str | None
    ) -> tuple[str, str] | None:
        """A grootboek *number* → ``(uuid, number)``, or nothing.

        Mappings are stored as numbers because that is what a bookkeeper says out loud and what
        survives a restore into a fresh administration; the API is addressed by uuid. This is
        the join, and it is why a reference sync is a prerequisite for a push rather than a
        nicety.
        """
        if not code:
            return None
        row = await self.ctx.session.scalar(
            self.refs.scoped_select().where(
                SnelstartRef.account_id == account.id,
                SnelstartRef.kind == SnelstartRefKind.LEDGER.value,
                SnelstartRef.code == str(code).strip(),
            )
        )
        return (row.external_id, row.code or str(code)) if row else None

    async def country_id(self, account: SnelstartAccount, iso2: str | None) -> str | None:
        """A two-letter country code → SnelStart's country uuid.

        Matched on ``landcode`` (``NL``), never ``landcodeISO`` (``NLD``) — they are different
        fields with different lengths and picking the wrong one silently matches nothing. And
        never by filtering server-side: ``/landen`` **ignores ``$filter``** and would happily
        answer with Nederland for a request about Estonia.
        """
        if not iso2:
            return None
        row = await self.ctx.session.scalar(
            self.refs.scoped_select().where(
                SnelstartRef.account_id == account.id,
                SnelstartRef.kind == SnelstartRefKind.COUNTRY.value,
                SnelstartRef.code == iso2.strip().upper(),
            )
        )
        return row.external_id if row else None

    # --- sync runs --------------------------------------------------------------- #
    async def _start_run(
        self, account: SnelstartAccount, kind: SnelstartSyncKind
    ) -> SnelstartSyncRun:
        return await self.runs.create(
            account_id=account.id,
            kind=kind.value,
            ok=False,
            actor_user_id=None if getattr(self.ctx, "is_system", False) else self.ctx.user.id,
        )

    async def _finish_run(
        self,
        run: SnelstartSyncRun,
        *,
        ok: bool,
        counts: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        message: str | None = None,
    ) -> SnelstartSyncRun:
        run.ok = ok
        run.counts = counts or {}
        run.errors = (errors or [])[:MAX_RUN_ERRORS]
        run.message = message
        run.finished_at = datetime.now(UTC)
        await self.ctx.session.flush()
        return run

    async def _fail_run(
        self,
        run: SnelstartSyncRun,
        account: SnelstartAccount,
        message: str,
        *,
        exc: Exception | None = None,
    ) -> SnelstartSyncRun:
        """Record a run that failed as a whole, and flag the account only where it is earned.

        ``cloudflare``'s rule: a **rejected credential** earns the red status, an ordinary
        request failure does not. SnelStart being unreachable for ninety seconds is not a reason
        to tell an agency their connection is broken — the text is recorded either way, and the
        status is what a screen shouts about.
        """
        account.last_error = message
        if exc is None or isinstance(exc, SnelstartAuthError | SnelstartSubscriptionError):
            account.status = SnelstartAccountStatus.ERROR.value
        return await self._finish_run(run, ok=False, message=message)

    async def recent_runs(self, account_id: uuid.UUID, limit: int = 20) -> list[SnelstartSyncRun]:
        rows = await self.ctx.session.execute(
            self.runs.scoped_select()
            .where(SnelstartSyncRun.account_id == account_id)
            .order_by(SnelstartSyncRun.created_at.desc())
            .limit(min(limit, 100))
        )
        return list(rows.scalars())

    # --- helpers ----------------------------------------------------------------- #
    async def _assert_name_free(self, name: str, *, exclude: uuid.UUID | None = None) -> None:
        stmt = self.accounts.scoped_select().where(
            func.lower(SnelstartAccount.name) == name.strip().lower()
        )
        if exclude is not None:
            stmt = stmt.where(SnelstartAccount.id != exclude)
        if await self.ctx.session.scalar(stmt) is not None:
            raise AppError(
                "conflict", "errors.conflict", status_code=409, fields={"name": "errors.duplicate"}
            )

    async def _validated_provider(self, provider_id: uuid.UUID | None) -> uuid.UUID | None:
        if provider_id is None:
            return None
        from app.core.providers.models import Provider

        row = await self.ctx.session.scalar(
            self.ctx.repo(Provider).scoped_select().where(Provider.id == provider_id)
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return provider_id


# --------------------------------------------------------------------------- #
# The coupling reference — how an unauthenticated webhook names a tenant
# --------------------------------------------------------------------------- #
def coupling_reference(org_id: uuid.UUID, account_id: uuid.UUID, secret: str) -> str:
    """``{org}.{account}.{secret}`` — the ``referenceKey`` SnelStart quotes back to us.

    The Google channel-token pattern ``app.core.payments.tokens`` already uses for Mollie, and
    for the same reason: SnelStart's coupling webhook is **one URL for the whole partner app**,
    arriving on a host where no org resolves, so the request must carry its own tenancy. The
    first two parts route it; the third proves it. Well inside SnelStart's 500-character limit.
    """
    return f"{org_id}.{account_id}.{secret}"


def parse_coupling_reference(reference: str) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    """The reference back into its three parts, or ``None`` for anything malformed.

    Returning ``None`` rather than raising because this parses **attacker-controlled input**:
    the route answers a bare 404 for every failure, and a parse error must look exactly like a
    wrong secret.
    """
    parts = (reference or "").split(".")
    if len(parts) != 3:
        return None
    try:
        return uuid.UUID(parts[0]), uuid.UUID(parts[1]), parts[2]
    except (ValueError, AttributeError):
        return None


def coupling_webhook_url() -> str:
    """The single URL SnelStart must be told to post koppelsleutels to.

    Shown on the settings screen for the reason Mollie's is: it has to be reachable from the
    public internet, and behind an access proxy (Cloudflare Zero Trust, ``docs/DEPLOY.md``) that
    is a rule somebody has to add. An admin who cannot see the URL cannot allow it, and
    "activation never completes" is otherwise a mystery with no clue on screen. It is the
    *instance* apex, not the tenant's host — SnelStart holds one URL for the whole app.
    """
    base = (settings.snelstart_webhook_base or "").strip().rstrip("/")
    if not base:
        base = f"https://{settings.base_domain}"
    return f"{base}/api/v1/snelstart/coupling/callback"


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _seller_of(info: dict[str, Any], own: dict[str, Any] | None) -> dict[str, Any]:
    """The administration's own identity, in schakl's ``SellerDetails`` vocabulary.

    Two sources, because SnelStart splits them: ``companyInfo`` holds the legal numbers and the
    bank details, while the ``Eigen`` relation holds the address that is actually printed. A
    fresh administration has neither filled in — which is worth showing as empty rather than
    hiding, since it is the first thing an agency has to fix.
    """
    address = (own or {}).get("vestigingsAdres") or {}
    return {
        "name": info.get("bedrijfsnaam") or (own or {}).get("naam") or "",
        "address_line1": address.get("straat") or info.get("adres") or "",
        "postal_code": address.get("postcode") or info.get("postcode") or "",
        "city": address.get("plaats") or info.get("plaats") or "",
        "vat_number": info.get("btwNummer") or "",
        "coc_number": info.get("kvKNummer") or "",
        "iban": info.get("iban") or "",
        "bic": info.get("bic") or "",
        "email": info.get("email") or "",
        "phone": info.get("telefoon") or "",
    }


#: What each half of this integration needs the token to be allowed to do. Checked against the
#: scopes the JWT declares so a screen can say "this key cannot write invoices" *before* a sync
#: fails halfway, rather than as a 403 forty rows in.
REQUIRED_SCOPES: dict[str, tuple[str, ...]] = {
    "relations": ("relaties:read", "relaties:write"),
    "invoices": ("boekhouden:read", "boekhouden:write"),
    "articles": ("artikelen:read", "artikelen:write"),
    "attachments": ("documenten:write",),
    "settings": ("settings:read",),
}


def _missing_scopes(scopes: list[str]) -> list[str]:
    """Which capabilities this token cannot deliver. Empty is the happy answer."""
    held = set(scopes)
    return [name for name, needed in REQUIRED_SCOPES.items() if not held.issuperset(needed)]
