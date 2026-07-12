"""REST surface for the per-org SSO settings under ``/api/v1/settings/sso`` (issue #76).

Everything is gated on ``settings.auth.manage``: the config embeds an IdP client secret and the
``enforced`` toggle can turn password login off for the whole org. Deny-by-default (CLAUDE.md §15).

The secret is **write-only**: the API accepts a new value and answers only
``secret_configured`` — it never plays the secret back, encrypted or not. ``enforced`` cannot be
stored without a successful "Test connection" of the current connection fields
(``oidc_tested_at``), and the marker is cleared whenever those fields change: enforcing an
untested config would be instant lockout (the #75 failure, moved from boot time to write time
— where it can actually be refused).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.auth.sso import (
    OrgAuthSettings,
    callback_url,
    fetch_discovery,
    invalidate_client,
    sso_row,
    valid_http_url,
)
from app.core.crypto import decrypt, encrypt
from app.core.permissions.deps import require_permission
from app.core.permissions.models import Role
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

router = APIRouter(prefix="/settings/sso", tags=["sso-settings"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class RoleOption(BaseModel):
    """A role the JIT-provisioning default can point at — included here so the picker does not
    require ``settings.roles.manage`` on top of ``settings.auth.manage``."""

    key: str
    name_i18n: dict[str, str]


class SsoSettingsRead(BaseModel):
    enabled: bool
    enforced: bool
    name: str
    discovery_url: str | None
    client_id: str | None
    #: A client secret is stored. The value itself is never returned (write-only).
    secret_configured: bool
    default_role: str
    auto_provision: bool
    #: The current connection fields passed a "Test connection". Cleared on every change to
    #: them; ``enforced`` cannot be switched on without it.
    tested: bool
    #: Derived from the org's verified custom domain or ``<slug>.<base_domain>`` — the exact
    #: redirect URI to register at the IdP (docs/SSO.md).
    callback_url: str
    #: The encryption key protecting the stored secret is still the shipped default
    #: (``SCHAKL_SECRET_KEY`` unchanged, no ``SCHAKL_ENCRYPTION_KEY``) — surfaced as a warning.
    weak_encryption_key: bool
    role_options: list[RoleOption]


class SsoSettingsWrite(BaseModel):
    enabled: bool
    enforced: bool
    name: str = Field(min_length=1, max_length=64)
    discovery_url: str | None = Field(default=None, max_length=1024)
    client_id: str | None = Field(default=None, max_length=512)
    #: Write-only. Empty / omitted on an update means "keep the stored secret".
    client_secret: str | None = Field(default=None, max_length=1024)
    default_role: str = Field(min_length=1, max_length=64)
    auto_provision: bool = True


class SsoTestResult(BaseModel):
    ok: bool
    issuer: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
def _weak_encryption_key() -> bool:
    default_secret = type(settings).model_fields["secret_key"].default
    return not settings.encryption_key and settings.secret_key == default_secret


class SsoSettingsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _role_options(self) -> list[RoleOption]:
        rows = (
            await self.ctx.session.execute(
                select(Role)
                .where(Role.org_id == self.ctx.org.id)
                .order_by(Role.position, Role.key)
            )
        ).scalars()
        return [RoleOption(key=r.key, name_i18n=dict(r.name_i18n)) for r in rows]

    async def _read(self, row: OrgAuthSettings | None) -> SsoSettingsRead:
        return SsoSettingsRead(
            enabled=row.oidc_enabled if row else False,
            enforced=row.oidc_enforced if row else False,
            name=row.oidc_name if row else "SSO",
            discovery_url=row.oidc_discovery_url if row else None,
            client_id=row.oidc_client_id if row else None,
            secret_configured=bool(row and row.oidc_client_secret_encrypted),
            default_role=row.oidc_default_role if row else "member",
            auto_provision=row.oidc_auto_provision_membership if row else True,
            tested=bool(row and row.oidc_tested_at),
            callback_url=callback_url(self.ctx.org),
            weak_encryption_key=_weak_encryption_key(),
            role_options=await self._role_options(),
        )

    async def get(self) -> SsoSettingsRead:
        return await self._read(await sso_row(self.ctx.session, self.ctx.org.id))

    async def save(self, data: SsoSettingsWrite) -> SsoSettingsRead:
        row = await sso_row(self.ctx.session, self.ctx.org.id)

        # An empty secret keeps the stored one (the form never sees it back); a resent
        # *identical* secret is not a change — otherwise every save through an API client
        # that round-trips its own copy would needlessly void the tested marker.
        secret_encrypted = row.oidc_client_secret_encrypted if row else None
        secret_changed = False
        if data.client_secret:
            stored_plain: str | None = None
            if secret_encrypted:
                try:
                    stored_plain = decrypt(secret_encrypted)
                except ValueError:  # rotated key: the stored token is dead anyway
                    stored_plain = None
            if stored_plain != data.client_secret:
                secret_encrypted = encrypt(data.client_secret)
                secret_changed = True

        fields: dict[str, str] = {}
        if data.discovery_url and not valid_http_url(data.discovery_url):
            fields["discovery_url"] = "errors.invalid_url"
        if data.enabled and not (data.discovery_url and data.client_id and secret_encrypted):
            for name, value in (
                ("discovery_url", data.discovery_url),
                ("client_id", data.client_id),
                ("client_secret", secret_encrypted),
            ):
                if not value:
                    fields.setdefault(name, "errors.required")
        if data.enforced and not data.enabled:
            fields["enforced"] = "errors.validation"
        if fields:
            raise AppError("validation", "errors.validation", status_code=422, fields=fields)

        role = await self.ctx.session.scalar(
            select(Role).where(Role.org_id == self.ctx.org.id, Role.key == data.default_role)
        )
        if role is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"default_role": "errors.validation"},
            )

        # The tested marker belongs to the exact connection fields it was earned with.
        connection_changed = row is None or (
            (row.oidc_discovery_url or None) != (data.discovery_url or None)
            or (row.oidc_client_id or None) != (data.client_id or None)
            or secret_changed
        )
        tested_at = None if connection_changed else (row.oidc_tested_at if row else None)

        if data.enforced and tested_at is None:
            # Never lock the tenant out (issue #76 / CLAUDE.md §15's spirit): enforcing kills
            # local login, so it demands a proven-working SSO config first.
            raise AppError(
                "sso_test_required",
                "errors.sso_test_required",
                status_code=422,
                fields={"enforced": "errors.sso_test_required"},
            )

        if row is None:
            row = OrgAuthSettings(org_id=self.ctx.org.id)
            self.ctx.session.add(row)
        row.oidc_enabled = data.enabled
        row.oidc_enforced = data.enforced
        row.oidc_name = data.name
        row.oidc_discovery_url = data.discovery_url or None
        row.oidc_client_id = data.client_id or None
        row.oidc_client_secret_encrypted = secret_encrypted
        row.oidc_default_role = data.default_role
        row.oidc_auto_provision_membership = data.auto_provision
        row.oidc_tested_at = tested_at
        await self.ctx.session.flush()
        invalidate_client(self.ctx.org.id)
        return await self._read(row)

    async def test(self) -> SsoTestResult:
        """Fetch + validate the *stored* discovery document — the explicit admin action that
        earns the tested marker ``enforced`` requires. Provider failures are surfaced verbatim
        (the admin is debugging their own IdP), never a 500."""
        row = await sso_row(self.ctx.session, self.ctx.org.id)
        if row is None or not row.oidc_discovery_url:
            return SsoTestResult(ok=False, error="errors.sso_not_configured")
        try:
            document = await fetch_discovery(row.oidc_discovery_url)
        except Exception as exc:  # noqa: BLE001 - surface the real reason, don't 500
            return SsoTestResult(ok=False, error=str(exc))
        row.oidc_tested_at = datetime.now(UTC)
        await self.ctx.session.flush()
        return SsoTestResult(ok=True, issuer=str(document.get("issuer")))


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get(
    "",
    response_model=SsoSettingsRead,
    dependencies=[require_permission("settings.auth.manage")],
)
async def get_sso_settings(
    ctx: RequestContext = Depends(require_context),
) -> SsoSettingsRead:
    return await SsoSettingsService(ctx).get()


@router.put(
    "",
    response_model=SsoSettingsRead,
    dependencies=[require_permission("settings.auth.manage")],
)
async def save_sso_settings(
    payload: SsoSettingsWrite,
    ctx: RequestContext = Depends(require_context),
) -> SsoSettingsRead:
    return await SsoSettingsService(ctx).save(payload)


@router.post(
    "/test",
    response_model=SsoTestResult,
    dependencies=[require_permission("settings.auth.manage")],
)
async def test_sso_settings(ctx: RequestContext = Depends(require_context)) -> SsoTestResult:
    return await SsoSettingsService(ctx).test()
