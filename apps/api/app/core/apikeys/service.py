"""Business logic for API keys and service accounts (#20).

All access is tenant-scoped (Golden Rule 1). A key's scopes come from the #19 permission
registry — there is no parallel vocabulary — and are capped by the creator's own grants; a
personal key is further capped by its owner's *live* permissions at request time (see
``auth.py``). Secrets are shown once, hashed at rest, and never logged.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.core.apikeys import keys as keygen
from app.core.apikeys.models import (
    PRINCIPAL_SERVICE_ACCOUNT,
    PRINCIPAL_USER,
    ApiKey,
    ServiceAccount,
)
from app.core.apikeys.schemas import (
    ApiKeyCreate,
    ServiceAccountCreate,
    ServiceAccountKeyCreate,
)
from app.core.permissions.catalog import all_permissions
from app.core.permissions.spec import SCOPES
from app.core.tenancy import RequestContext
from app.errors import AppError

#: No immortal keys (#20). A year is plenty for automation and forces periodic rotation.
MAX_LIFETIME = timedelta(days=366)


def _split_scope(scope: str) -> tuple[str, str | None]:
    base, sep, suffix = scope.partition(":")
    return base, (suffix if sep else None)


class ApiKeyService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.keys = ctx.repo(ApiKey)
        self.accounts = ctx.repo(ServiceAccount)

    # --- validation -------------------------------------------------------------- #
    def _validate_expiry(self, expires_at: datetime | None) -> None:
        if expires_at is None:
            # Never expires — an explicit owner choice; revocation stays the kill switch.
            return
        now = datetime.now(UTC)
        if expires_at <= now:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"expires_at": "errors.apikey_expiry_past"},
            )
        if expires_at > now + MAX_LIFETIME:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"expires_at": "errors.apikey_expiry_too_far"},
            )

    def _validate_scopes(self, scopes: Sequence[str]) -> None:
        """Every scope must be a real catalog permission, correctly suffixed, that the *creator*
        actually holds — a key can never grant more than the person minting it (#20).

        A scoped permission (``time.entry.read``) must carry ``:own``/``:any`` in the key, and an
        unscoped one must not. Without this, a member holding only ``:own`` could store the bare
        key, whose ``has()`` semantics would then satisfy an ``:any`` check — a silent escalation.
        """
        scoped = {spec.key: bool(spec.scopes) for spec in all_permissions()}
        for scope in scopes:
            base, suffix = _split_scope(scope)
            valid = (
                base in scoped
                and (suffix is None or suffix in SCOPES)
                and (suffix is not None) == scoped[base]  # scoped ⇔ suffixed
            )
            if not valid:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"scopes": "errors.apikey_unknown_scope"},
                )
            if not self.ctx.can(base, suffix):
                raise AppError(
                    "forbidden", "errors.apikey_scope_exceeds_grants", status_code=403
                )

    # --- service accounts -------------------------------------------------------- #
    async def list_accounts(self) -> Sequence[ServiceAccount]:
        self.ctx.require("apikeys.service_account.manage")
        return await self.accounts.list(limit=200, order_by=ServiceAccount.name)

    async def create_account(self, data: ServiceAccountCreate) -> ServiceAccount:
        self.ctx.require("apikeys.service_account.manage")
        return await self.accounts.create(
            name=data.name, active=True, created_by_user_id=self.ctx.user.id
        )

    async def delete_account(self, account_id: uuid.UUID) -> None:
        """Deleting the account revokes its keys with it (FK cascade)."""
        self.ctx.require("apikeys.service_account.manage")
        await self.accounts.delete(await self.accounts.get_or_404(account_id))

    # --- keys -------------------------------------------------------------------- #
    async def list_personal(self) -> Sequence[ApiKey]:
        self.ctx.require("apikeys.personal.manage")
        return (
            (
                await self.ctx.session.execute(
                    self.keys.scoped_select()
                    .where(
                        ApiKey.principal_type == PRINCIPAL_USER,
                        ApiKey.user_id == self.ctx.user.id,
                    )
                    .order_by(ApiKey.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def list_for_account(self, account_id: uuid.UUID) -> Sequence[ApiKey]:
        self.ctx.require("apikeys.service_account.manage")
        await self.accounts.get_or_404(account_id)
        return (
            (
                await self.ctx.session.execute(
                    self.keys.scoped_select()
                    .where(ApiKey.service_account_id == account_id)
                    .order_by(ApiKey.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def create_personal(self, data: ApiKeyCreate) -> tuple[ApiKey, str]:
        """A member mints a key for themselves. Its scopes must be a subset of what they hold."""
        self.ctx.require("apikeys.personal.manage")
        self._validate_expiry(data.expires_at)
        self._validate_scopes(data.scopes)
        return await self._mint(
            name=data.name,
            scopes=list(data.scopes),
            expires_at=data.expires_at,
            principal_type=PRINCIPAL_USER,
            user_id=self.ctx.user.id,
            service_account_id=None,
        )

    async def create_for_account(self, data: ServiceAccountKeyCreate) -> tuple[ApiKey, str]:
        self.ctx.require("apikeys.service_account.manage")
        account = await self.accounts.get_or_404(data.service_account_id)
        self._validate_expiry(data.expires_at)
        self._validate_scopes(data.scopes)
        return await self._mint(
            name=data.name,
            scopes=list(data.scopes),
            expires_at=data.expires_at,
            principal_type=PRINCIPAL_SERVICE_ACCOUNT,
            user_id=None,
            service_account_id=account.id,
        )

    async def _mint(
        self,
        *,
        name: str,
        scopes: list[str],
        expires_at: datetime | None,
        principal_type: str,
        user_id: uuid.UUID | None,
        service_account_id: uuid.UUID | None,
    ) -> tuple[ApiKey, str]:
        generated = keygen.generate()
        key = await self.keys.create(
            name=name,
            prefix=generated.prefix,
            hash=generated.secret_hash,
            principal_type=principal_type,
            user_id=user_id,
            service_account_id=service_account_id,
            scopes=scopes,
            expires_at=expires_at,
            created_by_user_id=self.ctx.user.id,
        )
        return key, generated.plaintext

    async def revoke(self, key_id: uuid.UUID) -> ApiKey:
        """Revoke a key. A member may revoke their own; a manager any service-account key."""
        key = await self.keys.get_or_404(key_id)
        if key.principal_type == PRINCIPAL_USER and key.user_id == self.ctx.user.id:
            self.ctx.require("apikeys.personal.manage")
        else:
            self.ctx.require("apikeys.service_account.manage")
        if key.revoked_at is None:
            key = await self.keys.update(key, revoked_at=datetime.now(UTC))
        return key
