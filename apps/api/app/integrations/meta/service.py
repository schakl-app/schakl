"""The app, the credentials and the assets. Business-licensed — see LICENSE.

Four rules from the rest of the codebase are load-bearing here:

* **Every in-request Meta call happens inside ``ctx.release_db()``** (docs/PERFORMANCE.md).
  Everything the session is needed for is read *before* the block; inside it only loaded ORM
  objects are touched, which is memory and flushes afterwards.
* **A probe fails softly and success clears the flag** (CLAUDE.md §10, Cloudflare). Discovery
  asks six edges and any of them may refuse without the other five being wrong; a verify that
  succeeds wipes ``status`` and ``last_error``.
* **Two credentials, two error paths** (SnelStart). The app secret is the agency's app; the
  token is the agency's system user. A refresh refused on the first must never be reported as
  the second.
* **A token that expires is refreshed weeks early, and says so** — see :meth:`refresh`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.crypto import decrypt, encrypt
from app.core.metagraph import (
    KIND_AD_ACCOUNT,
    KIND_INSTAGRAM,
    KIND_PAGE,
    MetaAppSecretError,
    MetaAuthError,
    MetaClient,
    MetaCredentials,
    MetaError,
    MetaNotConfigured,
    TokenInfo,
    debug_token,
    describe_failure,
    exchange_token,
    meta_client,
)
from app.errors import AppError
from app.integrations.meta.models import (
    AssetStatus,
    CredentialStatus,
    MetaAsset,
    MetaCredential,
    MetaSettings,
    Scheduler,
)

_SETTINGS_ENTITY = "meta_settings"
_CREDENTIAL_ENTITY = "meta_credential"
_ASSET_ENTITY = "meta_asset"

#: What an asset edit records: only what schakl decided.
_ASSET_TRACKED = ("company_id", "active", "credential_id")
_CREDENTIAL_TRACKED = ("label", "business_id", "active")

#: **When an expiring token is refreshed: with this much of its life left.** A system-user token
#: lives sixty days and *cannot* be refreshed once it has expired, so the refresh runs around
#: day forty and leaves nearly three weeks of nightly retries before anything breaks.
REFRESH_WINDOW = timedelta(days=20)
#: The stages at which a token that is *still* not refreshed is reported to the admins.
WARNING_DAYS = (14, 7, 1)
#: How long a derived Page token is trusted before it is asked for again.
PAGE_TOKEN_TTL = timedelta(days=7)

#: What each capability needs on the token. The screen prints the capability, not the scope:
#: "kan niet op Instagram publiceren" is a sentence, ``instagram_content_publish`` is a lookup.
CAPABILITY_SCOPES: dict[str, tuple[str, ...]] = {
    "discover": ("business_management",),
    "facebook_publish": ("pages_show_list", "pages_read_engagement", "pages_manage_posts"),
    "instagram_publish": ("instagram_basic", "instagram_content_publish"),
    "insights": ("read_insights", "instagram_manage_insights"),
    "ads_read": ("ads_read",),
    "ads_write": ("ads_management", "pages_manage_ads"),
}

#: Every scope the setup guide asks the admin to tick, in the order Business Settings lists
#: them — the union of the above, stated once so the guide and the check cannot disagree.
RECOMMENDED_SCOPES: tuple[str, ...] = tuple(
    dict.fromkeys(scope for scopes in CAPABILITY_SCOPES.values() for scope in scopes)
)

_PAGE_FIELDS = (
    "id,name,username,picture{url},instagram_business_account{id,username,name,profile_picture_url}"
)
_AD_ACCOUNT_FIELDS = (
    "account_id,name,currency,timezone_name,account_status,"
    "default_dsa_payor,default_dsa_beneficiary"
)

#: Stable keys written to ``last_error`` where the sentence is *ours* rather than Meta's, so
#: the screen can translate them (the WordPress convention). Meta's own prose never starts
#: with this prefix.
OUR_ERROR_PREFIX = "meta.error."
ERROR_NOT_SEEN = f"{OUR_ERROR_PREFIX}asset_not_seen"
ERROR_NO_CREDENTIAL = f"{OUR_ERROR_PREFIX}no_credential"


def capabilities(scopes: list[str] | tuple[str, ...]) -> dict[str, bool]:
    held = set(scopes)
    return {name: all(s in held for s in needed) for name, needed in CAPABILITY_SCOPES.items()}


def missing_scopes(scopes: list[str] | tuple[str, ...]) -> list[str]:
    held = set(scopes)
    return [s for s in RECOMMENDED_SCOPES if s not in held]


def days_left(expires_at: datetime | None, now: datetime | None = None) -> int | None:
    """Whole days until a token expires; ``None`` for one that never does."""
    if expires_at is None:
        return None
    delta = expires_at - (now or datetime.now(UTC))
    return max(-1, delta.days if delta.total_seconds() >= 0 else -1)


def refresh_due(row: MetaCredential, now: datetime | None = None) -> bool:
    """Whether the nightly job should try to refresh this token tonight."""
    if not row.active or row.expires_at is None:
        return False
    if row.status == CredentialStatus.EXPIRED.value:
        return False
    now = now or datetime.now(UTC)
    return now < row.expires_at <= now + REFRESH_WINDOW


def ad_account_path(external_id: str) -> str:
    """``1234`` → ``act_1234``. The one place the prefix is spelled."""
    clean = str(external_id).strip().removeprefix("act_")
    if not clean.isdigit():
        raise MetaNotConfigured(f"not an ad account id: {external_id!r}")
    return f"act_{clean}"


@dataclass
class DiscoveryResult:
    found: int = 0
    created: int = 0
    #: i18n keys, deduplicated — one per probe that could not run.
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    app_id: str
    app_secret: str


class MetaService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.activity = ActivityService(ctx)

    # --- settings ---------------------------------------------------------------------------- #

    async def settings_row(self, *, create: bool = False) -> MetaSettings | None:
        row = await self.ctx.session.scalar(
            select(MetaSettings).where(MetaSettings.org_id == self.ctx.org.id)
        )
        if row is None and create:
            row = MetaSettings(org_id=self.ctx.org.id)
            self.ctx.session.add(row)
            await self.ctx.session.flush()
        return row

    async def save_settings(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        writes_enabled: bool | None = None,
        facebook_scheduler: str | None = None,
        app_id_set: bool = False,
        app_secret_set: bool = False,
    ) -> MetaSettings:
        self.ctx.require("meta.settings.manage")
        row = await self.settings_row(create=True)
        assert row is not None
        changed: list[str] = []
        if app_id_set:
            clean = (app_id or "").strip() or None
            if clean is not None and not clean.isdigit():
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"app_id": "errors.meta_app_id_invalid"},
                )
            if clean != row.app_id:
                row.app_id = clean
                changed.append("app_id")
        if app_secret_set:
            # The secret is write-only: what is recorded is *that* it changed, never what to.
            clean_secret = (app_secret or "").strip()
            row.app_secret_encrypted = encrypt(clean_secret) if clean_secret else None
            changed.append("app_secret")
        if writes_enabled is not None and writes_enabled != row.writes_enabled:
            row.writes_enabled = writes_enabled
            changed.append("writes_enabled")
        if facebook_scheduler is not None and facebook_scheduler != row.facebook_scheduler:
            if facebook_scheduler not in {s.value for s in Scheduler}:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"facebook_scheduler": "errors.validation"},
                )
            row.facebook_scheduler = facebook_scheduler
            changed.append("facebook_scheduler")
        if changed:
            await self.ctx.session.flush()
            await self.activity.record(
                _SETTINGS_ENTITY, row.id, "meta.settings_changed", {"changed": changed}
            )
        return row

    async def require_writes_enabled(self) -> None:
        """The kill switch, checked before any call that changes something at Meta."""
        row = await self.settings_row()
        if row is not None and not row.writes_enabled:
            raise AppError("meta_writes_disabled", "errors.meta_writes_disabled", status_code=409)

    async def app_config(self) -> AppConfig | None:
        """The tenant's app id and secret, or ``None`` while either is missing."""
        row = await self.settings_row()
        if row is None or not row.app_id or not row.app_secret_encrypted:
            return None
        try:
            return AppConfig(app_id=row.app_id, app_secret=decrypt(row.app_secret_encrypted))
        except ValueError:
            # The encryption key was rotated under it. Indistinguishable from "not set" for
            # every caller: the secret has to be entered again either way.
            return None

    async def status(self) -> dict[str, Any]:
        """The connection in six facts, for a screen that works *with* it (three queries,
        whatever the number of assets)."""
        row = await self.settings_row()
        credentials = (
            await self.ctx.session.execute(
                select(
                    MetaCredential.status,
                    MetaCredential.refresh_error,
                    MetaCredential.active,
                ).where(MetaCredential.org_id == self.ctx.org.id)
            )
        ).all()
        usable = [c for c in credentials if c.active and c.status != CredentialStatus.EXPIRED.value]
        # Counted through the repository, so a member scoped to one client group is told
        # about that group's channels and not the agency's whole portfolio (§15, mode 2).
        counts = (
            await self.ctx.session.execute(
                self.ctx.repo(MetaAsset)
                .scoped_select()
                .with_only_columns(MetaAsset.kind, MetaAsset.active, func.count())
                .group_by(MetaAsset.kind, MetaAsset.active)
            )
        ).all()
        linked = unlinked = accounts = accounts_unlinked = 0
        for kind, active, number in counts:
            if kind == KIND_AD_ACCOUNT:
                if active:
                    accounts += int(number)
                else:
                    accounts_unlinked += int(number)
            elif active:
                linked += int(number)
            else:
                unlinked += int(number)
        return {
            "connected": bool(row and row.app_id and row.app_secret_encrypted and usable),
            "writes_enabled": row.writes_enabled if row else True,
            "facebook_scheduler": row.facebook_scheduler if row else Scheduler.SCHAKL.value,
            "channels_linked": linked,
            "channels_unlinked": unlinked,
            "ad_accounts_linked": accounts,
            "ad_accounts_unlinked": accounts_unlinked,
            "needs_attention": any(
                c.active and (c.status != CredentialStatus.ACTIVE.value or bool(c.refresh_error))
                for c in credentials
            ),
        }

    async def scheduler(self) -> str:
        row = await self.settings_row()
        return row.facebook_scheduler if row is not None else Scheduler.SCHAKL.value

    # --- credentials ------------------------------------------------------------------------- #

    async def list_credentials(self) -> list[MetaCredential]:
        self.ctx.require("meta.settings.manage")
        stmt = (
            select(MetaCredential)
            .where(MetaCredential.org_id == self.ctx.org.id)
            .order_by(MetaCredential.label)
        )
        return list((await self.ctx.session.scalars(stmt)).all())

    async def get_credential(self, credential_id: uuid.UUID) -> MetaCredential:
        row = await self.ctx.session.scalar(
            select(MetaCredential).where(
                MetaCredential.org_id == self.ctx.org.id, MetaCredential.id == credential_id
            )
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def create_credential(
        self, *, label: str, token: str, business_id: str = ""
    ) -> MetaCredential:
        """Store a system-user token, and find out at once what it is.

        Verified in the same call because a token that was pasted wrong should say so while the
        admin still has Business Settings open in the other tab — not tonight, from a cron.
        """
        self.ctx.require("meta.settings.manage")
        clean_label = label.strip()
        clean_token = token.strip()
        if not clean_label or not clean_token:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={("label" if not clean_label else "token"): "errors.required"},
            )
        await self._ensure_label_free(clean_label)
        row = MetaCredential(
            org_id=self.ctx.org.id,
            label=clean_label,
            business_id=_clean_id(business_id, "business_id"),
            token_encrypted=encrypt(clean_token),
        )
        self.ctx.session.add(row)
        await self.ctx.session.flush()
        await self.activity.record_created(_CREDENTIAL_ENTITY, row.id, {"label": clean_label})
        await self.verify(row.id)
        return row

    async def update_credential(
        self,
        row: MetaCredential,
        *,
        label: str | None = None,
        token: str | None = None,
        business_id: str | None = None,
        active: bool | None = None,
    ) -> MetaCredential:
        self.ctx.require("meta.settings.manage")
        before = snapshot(row, _CREDENTIAL_TRACKED)
        token_changed = False
        if label is not None and label.strip() and label.strip() != row.label:
            await self._ensure_label_free(label.strip(), except_id=row.id)
            row.label = label.strip()
        if business_id is not None:
            row.business_id = _clean_id(business_id, "business_id")
        if active is not None:
            row.active = active
        if token is not None and token.strip():
            row.token_encrypted = encrypt(token.strip())
            row.refresh_error = None
            row.warned_days = 0
            token_changed = True
        await self.ctx.session.flush()
        await self.activity.record_update(
            _CREDENTIAL_ENTITY, row.id, before, snapshot(row, _CREDENTIAL_TRACKED)
        )
        if token_changed:
            await self.activity.record(_CREDENTIAL_ENTITY, row.id, "meta.token_changed", {})
            await self._forget_page_tokens(row.id)
            await self.verify(row.id)
        return row

    async def delete_credential(self, credential_id: uuid.UUID) -> None:
        """Forget the token. **The assets stay**, dormant, with their client links intact.

        Nothing is revoked at Meta: the token was generated in Business Settings and is
        revoked there, which the screen says beside the button.
        """
        self.ctx.require("meta.settings.manage")
        row = await self.get_credential(credential_id)
        await self.activity.record(
            _CREDENTIAL_ENTITY, row.id, "meta.credential_removed", {"label": row.label}
        )
        await self._forget_page_tokens(row.id)
        await self.ctx.session.delete(row)
        await self.ctx.session.flush()

    async def verify(self, credential_id: uuid.UUID) -> MetaCredential:
        """Ask Meta what this token is, and record the answer either way. Never raises for a
        token that answered badly: the outcome *is* the row."""
        self.ctx.require("meta.settings.manage")
        return await self.observe_credential(credential_id)

    async def observe_credential(self, credential_id: uuid.UUID) -> MetaCredential:
        row = await self.get_credential(credential_id)
        app = await self.app_config()
        token = self._token(row)
        now = datetime.now(UTC)
        if token is None:
            return self._credential_failed(row, "the stored token cannot be decrypted", now)

        info: TokenInfo | None = None
        me: dict[str, Any] = {}
        business: dict[str, Any] = {}
        failure: Exception | None = None
        business_failure: Exception | None = None
        async with self.ctx.release_db():
            try:
                if app is not None:
                    info = await debug_token(app.app_id, app.app_secret, token)
                async with meta_client(self._credentials(token, app), tool="verify") as client:
                    # The read that matters outranks the probe (Cloudflare): ``/me`` is what
                    # every later call rides on, so it decides "does this token work".
                    me = await client.get("me", {"fields": "id,name"})
                    if row.business_id:
                        try:
                            business = await client.get(row.business_id, {"fields": "id,name"})
                        except MetaError as exc:
                            business_failure = exc
            except MetaError as exc:
                failure = exc

        row.last_verified_at = now
        if info is not None:
            row.scopes = list(info.scopes)
            row.token_kind = info.kind
            row.token_app_id = info.app_id
            row.issued_at = _from_stamp(info.issued_at)
            row.expires_at = _from_stamp(info.expires_at)
            row.data_access_expires_at = _from_stamp(info.data_access_expires_at)
        if me:
            row.subject_id = str(me.get("id") or "") or None
            row.subject_name = str(me.get("name") or "")
        if business:
            row.business_name = str(business.get("name") or "")

        if failure is not None:
            if isinstance(failure, MetaAuthError):
                row.status = CredentialStatus.EXPIRED.value
                row.last_error = describe_failure(failure)
                return row
            return self._credential_failed(row, describe_failure(failure), now)
        if info is not None and not info.is_valid:
            row.status = CredentialStatus.EXPIRED.value
            row.last_error = (info.error or "the token is no longer valid")[:500]
            return row
        if business_failure is not None:
            # The token works and the business id does not answer to it: a wrong id, or a
            # token without ``business_management``. The credential is usable; discovery
            # through the business is not, and the sentence says which.
            row.status = CredentialStatus.ERROR.value
            row.last_error = describe_failure(business_failure)
            return row
        row.status = CredentialStatus.ACTIVE.value
        row.last_error = None
        return row

    async def refresh(self, credential_id: uuid.UUID, *, force: bool = False) -> MetaCredential:
        """Exchange an expiring token for another sixty days.

        **Refreshing needs the app secret, and only works while the token is alive.** Both
        facts shape what is reported: a missing or refused app secret is *not* a token problem
        (``MetaAppSecretError`` keeps its own sentence), and a token already past its expiry
        cannot be exchanged at all — a new one has to be generated in Business Settings.

        The old token keeps working until its own expiry, so a refresh that is stored and then
        lost to a crash costs nothing, and a failed one leaves the credential ``active``: the
        screen shows the failure beside a token that still works.
        """
        row = await self.get_credential(credential_id)
        now = datetime.now(UTC)
        if row.expires_at is None and not force:
            return row
        row.refresh_attempted_at = now
        app = await self.app_config()
        token = self._token(row)
        if app is None:
            row.refresh_error = "meta.error.app_not_configured"
            return row
        if token is None:
            row.refresh_error = "the stored token cannot be decrypted"
            return row

        fresh = None
        info: TokenInfo | None = None
        failure: MetaError | None = None
        async with self.ctx.release_db():
            try:
                fresh = await exchange_token(app.app_id, app.app_secret, token)
                # The exchange names a lifetime in seconds; ``debug_token`` names the instant.
                # Asking is one more call and removes the arithmetic, and it is also how a
                # never-expiring answer (``expires_at: 0``) is told apart from a missing one.
                info = await debug_token(app.app_id, app.app_secret, fresh.token)
            except MetaError as exc:
                failure = exc

        if fresh is None:
            assert failure is not None
            row.refresh_error = describe_failure(failure)
            if isinstance(failure, MetaAppSecretError):
                row.refresh_error = "meta.error.app_secret_refused"
            elif isinstance(failure, MetaAuthError):
                row.status = CredentialStatus.EXPIRED.value
                row.last_error = describe_failure(failure)
            return row

        row.token_encrypted = encrypt(fresh.token)
        row.refreshed_at = now
        row.refresh_error = None
        row.warned_days = 0
        if info is not None:
            row.expires_at = _from_stamp(info.expires_at)
            row.issued_at = _from_stamp(info.issued_at) or row.issued_at
            row.data_access_expires_at = _from_stamp(info.data_access_expires_at)
            row.scopes = list(info.scopes) or row.scopes
        elif fresh.expires_in:
            row.expires_at = now + timedelta(seconds=fresh.expires_in)
        row.status = CredentialStatus.ACTIVE.value
        row.last_error = None
        await self.ctx.session.flush()
        await self._forget_page_tokens(row.id)
        await self.activity.record(
            _CREDENTIAL_ENTITY,
            row.id,
            "meta.token_refreshed",
            {"expires_at": row.expires_at.isoformat() if row.expires_at else None},
        )
        return row

    def warning_stage(self, row: MetaCredential, now: datetime | None = None) -> int | None:
        """The warning that is due for this token and has not been sent, or ``None``.

        A stage is reached when the days left drop to it *and* no smaller stage was already
        announced — so a job that did not run for a week says "7 days" once rather than
        replaying "14".
        """
        left = days_left(row.expires_at, now)
        if left is None or not row.active or row.status == CredentialStatus.EXPIRED.value:
            return None
        due = [stage for stage in WARNING_DAYS if left <= stage]
        if not due:
            return None
        stage = min(due)
        if row.warned_days and row.warned_days <= stage:
            return None
        return stage

    # --- assets ------------------------------------------------------------------------------ #

    async def list_assets(
        self,
        *,
        kind: str | None = None,
        company_id: uuid.UUID | None = None,
        active_only: bool = False,
        unlinked_only: bool = False,
        credential_id: uuid.UUID | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[MetaAsset]:
        stmt = (
            self.ctx.repo(MetaAsset)
            .scoped_select()
            .where(
                *self._asset_conditions(
                    kind=kind,
                    company_id=company_id,
                    active_only=active_only,
                    unlinked_only=unlinked_only,
                    credential_id=credential_id,
                    q=q,
                )
            )
        )
        stmt = stmt.order_by(MetaAsset.name, MetaAsset.kind, MetaAsset.external_id)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        return list((await self.ctx.session.scalars(stmt)).all())

    async def count_assets(self, **filters: Any) -> int:
        """How many assets the same filters match — what a page is a page *of*."""
        stmt = (
            self.ctx.repo(MetaAsset).scoped_count_select().where(*self._asset_conditions(**filters))
        )
        return int(await self.ctx.session.scalar(stmt) or 0)

    @staticmethod
    def _asset_conditions(
        *,
        kind: str | None = None,
        company_id: uuid.UUID | None = None,
        active_only: bool = False,
        unlinked_only: bool = False,
        credential_id: uuid.UUID | None = None,
        q: str | None = None,
    ) -> list[Any]:
        conditions: list[Any] = []
        if kind is not None:
            conditions.append(MetaAsset.kind == kind)
        if company_id is not None:
            conditions.append(MetaAsset.company_id == company_id)
        if active_only:
            conditions.append(MetaAsset.active.is_(True))
        if unlinked_only:
            conditions.append(MetaAsset.active.is_(False))
        if credential_id is not None:
            conditions.append(MetaAsset.credential_id == credential_id)
        if q and q.strip():
            needle = f"%{q.strip()}%"
            conditions.append(
                MetaAsset.name.ilike(needle)
                | MetaAsset.username.ilike(needle)
                | (MetaAsset.external_id == q.strip())
            )
        return conditions

    async def get_asset(self, asset_id: uuid.UUID) -> MetaAsset:
        """One asset, 404 outside this caller's tenant or company horizon (§15)."""
        row = await self.ctx.session.scalar(
            self.ctx.repo(MetaAsset).scoped_select().where(MetaAsset.id == asset_id)
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def update_asset(
        self,
        row: MetaAsset,
        *,
        company_id: uuid.UUID | None = None,
        active: bool | None = None,
        company_id_set: bool = False,
    ) -> MetaAsset:
        """Say whose asset this is, and whether it is worked on. Never what Meta said."""
        self.ctx.require("meta.settings.manage")
        before = snapshot(row, _ASSET_TRACKED)
        if company_id_set:
            self.ctx.repo(MetaAsset)._guard_company_write({"company_id": company_id})
            row.company_id = company_id
            if company_id is not None and active is None:
                # Linking *is* switching on: nobody names a Page's client in order to leave
                # the Page unused, and a second click to say so is a click for nothing.
                row.active = True
        if active is not None:
            row.active = active
        await self.ctx.session.flush()
        await self.activity.record_update(
            _ASSET_ENTITY, row.id, before, snapshot(row, _ASSET_TRACKED)
        )
        return row

    async def discover(self, credential_id: uuid.UUID) -> DiscoveryResult:
        """Read every asset this token reaches, and remember it. **Links nothing.**

        Six probes, each failing softly: the business's own Pages and ad accounts, the ones
        clients shared with it, and the token's own two lists as a fallback for a token whose
        business id is unknown or whose ``business_management`` scope is missing. One refusal
        costs its own probe and is named in ``warnings``; it never empties the result.

        A new asset arrives **inactive and unlinked**. Discovery finds everything a token can
        see, which for an agency is every client it ever worked for; which of those it manages
        here is a decision a person makes.
        """
        self.ctx.require("meta.settings.manage")
        row = await self.get_credential(credential_id)
        token = self._token(row)
        if token is None:
            raise MetaNotConfigured("the stored token cannot be decrypted")
        app = await self.app_config()
        existing = {
            (asset.kind, asset.external_id): asset
            for asset in (
                await self.ctx.session.scalars(
                    select(MetaAsset).where(MetaAsset.org_id == self.ctx.org.id)
                )
            ).all()
        }
        business_id = row.business_id
        org_id = self.ctx.org.id

        found: dict[tuple[str, str], dict[str, Any]] = {}
        warnings: list[str] = []
        tasks_by_page: dict[str, list[str]] = {}
        any_answered = False
        auth_failure: MetaError | None = None

        async with (
            meta_client(self._credentials(token, app), tool="discover") as client,
            self.ctx.release_db(),
        ):

            async def probe(path: str, params: dict[str, Any], warning: str) -> list[dict]:
                nonlocal any_answered, auth_failure
                try:
                    rows = await client.get_all(path, params)
                except MetaAuthError as exc:
                    auth_failure = exc
                    return []
                except MetaError:
                    warnings.append(warning)
                    return []
                any_answered = True
                return rows

            for page in await probe(
                "me/accounts", {"fields": f"{_PAGE_FIELDS},tasks"}, "meta.warning.own_pages"
            ):
                page_id = str(page.get("id") or "")
                if page_id:
                    tasks_by_page[page_id] = [str(t) for t in (page.get("tasks") or [])]
                _remember_page(found, page, relation="owned")
            if business_id:
                for relation, edge, warning in (
                    ("owned", "owned_pages", "meta.warning.business_pages"),
                    ("client", "client_pages", "meta.warning.client_pages"),
                ):
                    for page in await probe(
                        f"{business_id}/{edge}", {"fields": _PAGE_FIELDS}, warning
                    ):
                        _remember_page(found, page, relation=relation)
                for relation, edge, warning in (
                    ("owned", "owned_ad_accounts", "meta.warning.business_ad_accounts"),
                    ("client", "client_ad_accounts", "meta.warning.client_ad_accounts"),
                ):
                    for account in await probe(
                        f"{business_id}/{edge}", {"fields": _AD_ACCOUNT_FIELDS}, warning
                    ):
                        _remember_ad_account(found, account, relation=relation)
            for account in await probe(
                "me/adaccounts", {"fields": _AD_ACCOUNT_FIELDS}, "meta.warning.own_ad_accounts"
            ):
                _remember_ad_account(found, account, relation="owned", keep_relation=True)

        now = datetime.now(UTC)
        if auth_failure is not None and not any_answered:
            row.status = CredentialStatus.EXPIRED.value
            row.last_error = describe_failure(auth_failure)
            raise auth_failure

        created = 0
        for key, payload in found.items():
            asset = existing.get(key)
            if asset is None:
                asset = MetaAsset(
                    org_id=org_id, kind=key[0], external_id=key[1], credential_id=row.id
                )
                self.ctx.session.add(asset)
                created += 1
            elif asset.credential_id is None:
                # A dormant asset found again: it wakes up on the credential that sees it.
                asset.credential_id = row.id
            _apply_asset_payload(asset, payload)
            tasks = tasks_by_page.get(key[1]) if key[0] == KIND_PAGE else None
            if tasks is not None:
                asset.tasks = tasks
            asset.observed_at = now
            if asset.credential_id == row.id:
                asset.status = AssetStatus.ACTIVE.value
                asset.last_error = None
                asset.last_verified_at = now
        if any_answered and not warnings:
            # Only a *complete* read may say something is gone: an asset missing from a read
            # that was cut short is merely unread (Timeon's absence rule, CLAUDE.md §10).
            for key, asset in existing.items():
                if asset.credential_id == row.id and key not in found:
                    asset.status = AssetStatus.ERROR.value
                    asset.last_error = ERROR_NOT_SEEN
                    asset.last_verified_at = now
        row.last_discovered_at = now
        await self.ctx.session.flush()
        return DiscoveryResult(
            found=len(found), created=created, warnings=list(dict.fromkeys(warnings))
        )

    # --- calling Meta ------------------------------------------------------------------------ #

    async def credentials_for(self, asset: MetaAsset) -> MetaCredentials:
        """The credentials one asset is called with, or :class:`MetaNotConfigured`."""
        if asset.credential_id is None:
            raise MetaNotConfigured("this asset has no credential")
        row = await self.ctx.session.scalar(
            select(MetaCredential).where(
                MetaCredential.org_id == self.ctx.org.id,
                MetaCredential.id == asset.credential_id,
            )
        )
        if row is None or not row.active:
            raise MetaNotConfigured("the credential for this asset is switched off")
        if row.status == CredentialStatus.EXPIRED.value:
            raise MetaAuthError("the token for this asset has expired", meta_code=190)
        token = self._token(row)
        if token is None:
            raise MetaNotConfigured("the stored token cannot be decrypted")
        return self._credentials(token, await self.app_config())

    @asynccontextmanager
    async def open_client(
        self, asset_id: uuid.UUID, *, tool: str = ""
    ) -> AsyncIterator[tuple[MetaClient, MetaAsset]]:
        """The one way a request reaches Meta: a client on a linked asset's credential, with
        the pooled database connection **released** for the duration."""
        asset = await self.get_asset(asset_id)
        credentials = await self.credentials_for(asset)
        async with meta_client(credentials, tool=tool) as client, self.ctx.release_db():
            yield client, asset

    def cached_page_token(self, asset: MetaAsset, now: datetime | None = None) -> str | None:
        """The Page's own token, if one is held and young enough to be trusted."""
        if not asset.page_token_encrypted or asset.page_token_at is None:
            return None
        if (now or datetime.now(UTC)) - asset.page_token_at > PAGE_TOKEN_TTL:
            return None
        try:
            return decrypt(asset.page_token_encrypted)
        except ValueError:
            return None

    async def page_client(
        self, client: MetaClient, page: MetaAsset, *, fresh: bool = False
    ) -> MetaClient:
        """``client`` acting as the Page. Derives the Page token where none is cached.

        Safe inside ``release_db()``: it touches the loaded row only. ``fresh`` is how a caller
        that was just refused says "that token is dead, ask again".
        """
        token = None if fresh else self.cached_page_token(page)
        if token is None:
            answer = await client.get(page.external_id, {"fields": "access_token"})
            token = str(answer.get("access_token") or "")
            if not token:
                raise MetaNotConfigured("meta returned no page token for this page")
            page.page_token_encrypted = encrypt(token)
            page.page_token_at = datetime.now(UTC)
        return client.acting_as(token)

    # --- internals --------------------------------------------------------------------------- #

    def _credentials(self, token: str, app: AppConfig | None) -> MetaCredentials:
        return MetaCredentials(
            token=token,
            app_id=app.app_id if app else None,
            app_secret=app.app_secret if app else None,
        )

    @staticmethod
    def _token(row: MetaCredential) -> str | None:
        try:
            return decrypt(row.token_encrypted)
        except ValueError:
            return None

    @staticmethod
    def _credential_failed(row: MetaCredential, message: str, now: datetime) -> MetaCredential:
        row.status = CredentialStatus.ERROR.value
        row.last_error = message[:500]
        row.last_verified_at = now
        return row

    async def _ensure_label_free(self, label: str, *, except_id: uuid.UUID | None = None) -> None:
        """A uniqueness the database guarantees is a refusal the service owes (§10)."""
        stmt = select(MetaCredential.id).where(
            MetaCredential.org_id == self.ctx.org.id, MetaCredential.label == label
        )
        if except_id is not None:
            stmt = stmt.where(MetaCredential.id != except_id)
        if await self.ctx.session.scalar(stmt) is not None:
            raise AppError(
                "conflict",
                "errors.conflict",
                status_code=409,
                fields={"label": "errors.meta_label_taken"},
            )

    async def _forget_page_tokens(self, credential_id: uuid.UUID) -> None:
        """A Page token is derived from its credential's token; a new parent means new children."""
        await self.ctx.session.execute(
            update(MetaAsset)
            .where(MetaAsset.org_id == self.ctx.org.id, MetaAsset.credential_id == credential_id)
            .values(page_token_encrypted=None, page_token_at=None)
        )


def _clean_id(value: str | None, field_name: str) -> str:
    clean = (value or "").strip()
    if clean and not clean.isdigit():
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={field_name: "errors.meta_id_invalid"},
        )
    return clean


def _from_stamp(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


def _remember_page(
    found: dict[tuple[str, str], dict[str, Any]], page: dict[str, Any], *, relation: str
) -> None:
    page_id = str(page.get("id") or "")
    if not page_id:
        return
    picture = page.get("picture")
    picture_url = None
    if isinstance(picture, dict):
        data = picture.get("data")
        picture_url = (data or {}).get("url") if isinstance(data, dict) else picture.get("url")
    entry = found.setdefault((KIND_PAGE, page_id), {"relation": relation})
    # "Shared with us by a client" outranks "we can see it": the first is the more specific
    # statement, and ``me/accounts`` lists both kinds without saying which is which.
    if relation == "client":
        entry["relation"] = "client"
    entry.update(
        name=str(page.get("name") or page_id),
        username=str(page.get("username") or "") or None,
        picture_url=str(picture_url) if picture_url else None,
    )
    instagram = page.get("instagram_business_account")
    if isinstance(instagram, dict) and instagram.get("id"):
        ig_id = str(instagram["id"])
        ig = found.setdefault((KIND_INSTAGRAM, ig_id), {})
        ig.update(
            relation=entry["relation"],
            name=str(instagram.get("name") or instagram.get("username") or ig_id),
            username=str(instagram.get("username") or "") or None,
            picture_url=str(instagram.get("profile_picture_url") or "") or None,
            linked_page_id=page_id,
        )


def _remember_ad_account(
    found: dict[tuple[str, str], dict[str, Any]],
    account: dict[str, Any],
    *,
    relation: str,
    keep_relation: bool = False,
) -> None:
    account_id = str(account.get("account_id") or "").strip()
    if not account_id:
        account_id = str(account.get("id") or "").removeprefix("act_")
    if not account_id.isdigit():
        return
    entry = found.setdefault((KIND_AD_ACCOUNT, account_id), {"relation": relation})
    if not keep_relation and relation == "client":
        entry["relation"] = "client"
    status = account.get("account_status")
    entry.update(
        name=str(account.get("name") or account_id),
        currency=str(account.get("currency") or "") or None,
        timezone=str(account.get("timezone_name") or "") or None,
        account_status=int(status)
        if isinstance(status, int | str) and str(status).isdigit()
        else None,
        dsa_payor=str(account.get("default_dsa_payor") or "") or None,
        dsa_beneficiary=str(account.get("default_dsa_beneficiary") or "") or None,
    )


def _apply_asset_payload(asset: MetaAsset, payload: dict[str, Any]) -> None:
    """What Meta said about the asset itself. Never anything schakl decided."""
    asset.name = str(payload.get("name") or asset.name or asset.external_id)[:255]
    asset.relation = str(payload.get("relation") or asset.relation or "owned")
    for key in ("username", "picture_url", "linked_page_id", "currency", "timezone"):
        if key in payload:
            setattr(asset, key, payload[key])
    if "account_status" in payload:
        asset.account_status = payload["account_status"]
    for key in ("dsa_payor", "dsa_beneficiary"):
        if key in payload:
            value = payload[key]
            setattr(asset, key, value[:512] if value else None)
