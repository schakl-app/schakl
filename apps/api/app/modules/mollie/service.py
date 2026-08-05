"""Business logic for the mollie module (issue #267). Business-licensed — see LICENSE.

The shape mirrors ``oxxa``'s, and the rules it never bends are the same:

**Never guess which account.** A tenant may legitimately hold two Mollie keys — a live one and
a test one, or two profiles mid-merger. Picking one is how an agency takes real money in a test
or fails to take any in production, so the resolver answers *"there are several, say which"*.
That resolution lives in :mod:`app.modules.invoicing.payments` (it is the caller's question);
what lives here is refusing to make it ambiguous in the first place.

**Verify never raises.** ``require_context`` rolls the session back on any exception, so
raising from a verify would discard the very row that records what Mollie said. The result is
returned, the row keeps the failure on ``last_error``, and the screen shows it.

**The credential is never echoed, never logged, never in an envelope.** It is Fernet at rest
and read exactly once, in :meth:`_client`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func

from app.core.activity import ActivityService
from app.core.crypto import decrypt, encrypt
from app.core.payments import PaymentAccount
from app.core.payments.tokens import mint, new_secret
from app.core.providers.models import Provider
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.mollie.client import (
    MollieAuthError,
    MollieError,
    MolliePaymentProvider,
    mode_of,
    redact,
)
from app.modules.mollie.models import MollieAccount, MollieAccountStatus
from app.modules.mollie.schemas import (
    MollieAccountCreate,
    MollieAccountUpdate,
    MollieAccountVerifyResult,
)

logger = logging.getLogger("schakl.mollie")

ACCOUNT_ENTITY = "mollie_account"


def client_for(account: MollieAccount) -> MolliePaymentProvider:
    """A live client for one stored credential.

    Module-level rather than a method because the payment seam's account resolver hands core a
    zero-argument ``connect`` callable and holds no service instance — and because decrypting
    in exactly one place is what makes "the key is read once" checkable.
    """
    return MolliePaymentProvider(decrypt(account.api_key_encrypted))


class MollieAccountService:
    """Connecting, rotating, verifying and removing a tenant's Mollie credentials."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.accounts = ctx.repo(MollieAccount)
        self.activity = ActivityService(ctx)

    # --- reads ------------------------------------------------------------------- #
    async def list_accounts(self) -> list[dict[str, Any]]:
        rows = await self.ctx.session.execute(
            self.accounts.scoped_select().order_by(MollieAccount.name)
        )
        return [self.serialize(account) for account in rows.scalars()]

    def serialize(self, account: MollieAccount) -> dict[str, Any]:
        """The account as the settings screen reads it — credential replaced by a boolean."""
        return {
            "id": account.id,
            "name": account.name,
            "api_key_configured": bool(account.api_key_encrypted),
            "mode": account.mode,
            "methods": list(account.methods or []),
            "provider_id": account.provider_id,
            "active": account.active,
            "status": account.status,
            "last_verified_at": account.last_verified_at,
            "last_error": account.last_error,
            "webhook_url": self.webhook_url(account),
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }

    def webhook_url(self, account: MollieAccount) -> str:
        """The URL Mollie posts to for this account's payments.

        Shown on the settings screen for one reason: it has to be reachable from the public
        internet, and behind an access proxy (Cloudflare Zero Trust, docs/DEPLOY.md) that is a
        rule somebody has to add. An admin who cannot see the URL cannot allow it, and
        "payments are collected but never booked" is a silent failure.
        """
        from app.modules.invoicing.payments import callback_url

        return callback_url(
            self.ctx.org,
            MolliePaymentProvider.key,
            mint(self.ctx.org.id, account.id, account.webhook_secret),
        )

    # --- writes ------------------------------------------------------------------ #
    async def create_account(self, payload: MollieAccountCreate) -> MollieAccount:
        await self._assert_name_free(payload.name)
        api_key = payload.api_key.strip()
        account = await self.accounts.create(
            name=payload.name.strip(),
            api_key_encrypted=encrypt(api_key),
            mode=mode_of(api_key),
            webhook_secret=new_secret(),
            provider_id=await self._validated_provider(payload.provider_id),
            active=payload.active,
        )
        await self.ctx.session.flush()
        await self.activity.record_created(
            ACCOUNT_ENTITY, account.id, {"name": account.name, "mode": account.mode}
        )
        return account

    async def update_account(
        self, account_id: uuid.UUID, payload: MollieAccountUpdate
    ) -> MollieAccount:
        account = await self.accounts.get_or_404(account_id)
        before = {"name": account.name, "active": account.active, "mode": account.mode}

        if payload.name is not None and payload.name.strip() != account.name:
            await self._assert_name_free(payload.name, exclude=account.id)
            account.name = payload.name.strip()
        if payload.provider_id is not None:
            account.provider_id = await self._validated_provider(payload.provider_id)
        if payload.active is not None:
            account.active = payload.active

        rotated = False
        if payload.api_key:
            api_key = payload.api_key.strip()
            account.api_key_encrypted = encrypt(api_key)
            # The mode follows the key, always: rotating a live key onto a test one has to move
            # this with it, or an agency believes it is taking money it is not.
            account.mode = mode_of(api_key)
            # Everything observed through the old credential is now unproven. Clearing it is
            # what stops a stale "verified" badge vouching for a key nobody has tested.
            account.methods = []
            account.last_verified_at = None
            account.status = MollieAccountStatus.ACTIVE.value
            account.last_error = None
            # A key is usually rotated *because* it leaked. The callback URL is derived from a
            # secret stored beside it, so it moves too — leaving the old URL answering would
            # keep one half of a compromised pair alive.
            account.webhook_secret = new_secret()
            rotated = True

        await self.ctx.session.flush()
        # Refresh so the server-side ``updated_at`` (onupdate) is populated, exactly as
        # ``TenantScopedRepository.update`` does it. The fields above are set one at a time so
        # the before/after diff can be recorded, which means this write does not travel through
        # the repository — and without this the *synchronous* ``serialize`` below reads an
        # expired attribute, which SQLAlchemy answers by attempting IO outside a greenlet:
        # ``MissingGreenlet``, i.e. a 500 on every PATCH that actually changed something.
        await self.ctx.session.refresh(account)
        after = {"name": account.name, "active": account.active, "mode": account.mode}
        changes = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
        if changes:
            await self.activity.record(ACCOUNT_ENTITY, account.id, "updated", {"changes": changes})
        if rotated:
            # The fact, never the value.
            await self.activity.record(ACCOUNT_ENTITY, account.id, "mollie.credential_rotated")
        return account

    async def delete_account(self, account_id: uuid.UUID) -> None:
        """Disconnect a credential.

        The payment intents it opened stay: they are invoicing's rows, they carry the ledger
        link that already settled, and deleting the history of how an invoice was paid because
        somebody rotated a key would be the wrong kind of tidy. Their ``account_id`` simply
        points at a row that is gone, which is exactly what a bare UUID (§6) is for.
        """
        account = await self.accounts.get_or_404(account_id)
        await self.activity.record(ACCOUNT_ENTITY, account.id, "deleted", {"name": account.name})
        await self.accounts.delete(account)

    async def verify(self, account_id: uuid.UUID) -> MollieAccountVerifyResult:
        """Ask Mollie whether the stored key works, and record the answer either way."""
        account = await self.accounts.get_or_404(account_id)
        client = self._client(account)
        now = datetime.now(UTC)
        try:
            async with self.ctx.release_db():
                facts = await client.verify()
        except MollieError as exc:
            # **Returned, not raised** — see the module docstring.
            account.status = MollieAccountStatus.ERROR.value
            account.last_error = redact(str(exc))[:500]
            account.last_verified_at = now
            if isinstance(exc, MollieAuthError):
                account.methods = []
            await self.ctx.session.flush()
            return MollieAccountVerifyResult(ok=False, error=account.last_error)

        account.status = MollieAccountStatus.ACTIVE.value
        account.last_error = None
        account.last_verified_at = now
        account.methods = list(facts.get("methods") or [])
        # Mollie's own word for which world the key acts in, over our prefix reading of it.
        # They agree in every documented case; if they ever did not, Mollie is right.
        account.mode = str(facts.get("mode") or account.mode)
        await self.ctx.session.flush()
        await self.activity.record(
            ACCOUNT_ENTITY,
            account.id,
            "mollie.verified",
            {"mode": account.mode, "methods": len(account.methods)},
        )
        return MollieAccountVerifyResult(
            ok=True, mode=account.mode, methods=list(account.methods)
        )

    # --- plumbing ---------------------------------------------------------------- #
    async def _assert_name_free(self, name: str, *, exclude: uuid.UUID | None = None) -> None:
        stmt = self.accounts.scoped_select().where(
            func.lower(MollieAccount.name) == name.strip().lower()
        )
        if exclude:
            stmt = stmt.where(MollieAccount.id != exclude)
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

    def _client(self, account: MollieAccount) -> MolliePaymentProvider:
        try:
            return client_for(account)
        except ValueError as exc:
            # A rotated ``SCHAKL_ENCRYPTION_KEY`` leaves an unreadable secret. Say so plainly —
            # the fix is re-entering the key, not retrying.
            raise AppError(
                "mollie_credential_unreadable",
                "errors.mollie_credential_unreadable",
                status_code=409,
            ) from exc


async def resolve_accounts(session, org_id: uuid.UUID) -> list[PaymentAccount]:
    """This org's Mollie credentials, described in the payment seam's own vocabulary.

    Registered onto ``app.core.payments`` at import (see ``__init__.py``) so ``invoicing`` can
    ask *"what can this org charge with?"* without ever naming this module or reading its table
    (§6). Reads through the RLS-bound session the caller already has — an unbound one returns
    nothing, which is the right answer for a caller who has not proven which tenant they are.
    """
    from sqlalchemy import select

    rows = await session.execute(
        select(MollieAccount)
        .where(MollieAccount.org_id == org_id)
        .order_by(MollieAccount.name)
    )
    return [
        PaymentAccount(
            provider=MolliePaymentProvider.key,
            id=account.id,
            org_id=account.org_id,
            label=account.name,
            mode=account.mode,
            active=account.active,
            webhook_secret=account.webhook_secret,
            connect=lambda account=account: client_for(account),
        )
        for account in rows.scalars()
    ]
