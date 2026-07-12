"""License management routes (issue #137): ``/api/v1/instance/license``.

Instance-owner gated (``users.is_superuser``) but deliberately **not** behind
``SCHAKL_INSTANCE_ADMIN_ENABLED`` — installing a license key must work on every self-hosted
box, while the cross-tenant admin surface stays opt-in. Permission-exempt on the org axis
(§15's sanctioned "gated on a different axis" reason), like the rest of ``/instance``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import settings
from app.core.auth.models import User
from app.core.auth.users import current_active_user
from app.core.entitlements.service import (
    LicenseError,
    invalidate_license_cache,
    license_state,
    licensed_skus,
    verify_license,
)
from app.core.models import InstanceLicense
from app.core.permissions.deps import no_permission_required
from app.db import async_session_maker
from app.errors import AppError

router = APIRouter(prefix="/instance/license", tags=["instance"])


async def require_instance_owner(user: User = Depends(current_active_user)) -> User:
    if not user.is_superuser:
        raise AppError("forbidden", "errors.forbidden", status_code=403)
    return user


class LicensedModuleState(BaseModel):
    """One licensed surface (a module's sku, or ``mcp``) and where it stands."""

    sku: str
    entitled: bool
    writable: bool
    # None (fine) | "grace" | "expired" | "unlicensed" (bootstrap window still open).
    notice: str | None = None


class LicenseStatus(BaseModel):
    installed: bool
    customer: str | None = None
    plan: str | None = None
    modules: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    grace_until: datetime | None = None
    # Bootstrap clock (upgrade path): when unlicensed-but-enabled modules turn read-only.
    bootstrap_grace_until: datetime | None = None
    licensed: list[LicensedModuleState] = Field(default_factory=list)


class LicenseInstall(BaseModel):
    key: str = Field(min_length=1, max_length=10_000)


async def _status() -> LicenseStatus:
    state = await license_state()
    info = state.info
    return LicenseStatus(
        installed=info is not None,
        customer=info.customer if info else None,
        plan=info.plan if info else None,
        modules=list(info.modules) if info else [],
        expires_at=info.expires_at if info else None,
        grace_until=(
            info.expires_at + timedelta(days=info.grace_days) if info else None
        ),
        bootstrap_grace_until=state.bootstrap_grace_until,
        licensed=[
            LicensedModuleState(
                sku=sku,
                entitled=state.entitled(sku),
                writable=state.writable(sku),
                notice=state.notice(sku),
            )
            for sku in sorted(set(licensed_skus().values()))
        ],
    )


@router.get(
    "",
    response_model=LicenseStatus,
    dependencies=[no_permission_required("instance-owner gated (users.is_superuser)")],
)
async def get_license(_: User = Depends(require_instance_owner)) -> LicenseStatus:
    return await _status()


@router.put(
    "",
    response_model=LicenseStatus,
    dependencies=[no_permission_required("instance-owner gated (users.is_superuser)")],
)
async def install_license(
    payload: LicenseInstall, user: User = Depends(require_instance_owner)
) -> LicenseStatus:
    """Install (or replace) the instance license. The key is verified before it is stored,
    so a stored license is always one that was valid at install time."""
    try:
        verify_license(payload.key, settings.license_public_key)
    except LicenseError as exc:
        raise AppError(
            "license_invalid",
            "errors.license_invalid",
            status_code=422,
            fields={"key": "errors.license_invalid"},
        ) from exc
    async with async_session_maker() as session:
        row = await session.get(InstanceLicense, 1)
        if row is None:
            row = InstanceLicense(id=1)
            session.add(row)
        row.license_text = payload.key.strip()
        row.installed_at = datetime.now(UTC)
        row.installed_by_email = user.email
        await session.commit()
    invalidate_license_cache()
    return await _status()


@router.delete(
    "",
    response_model=LicenseStatus,
    dependencies=[no_permission_required("instance-owner gated (users.is_superuser)")],
)
async def remove_license(_: User = Depends(require_instance_owner)) -> LicenseStatus:
    """Remove the installed license (API-level escape hatch; the UI replaces rather than
    removes). Data in licensed modules is untouched — they go read-only, never away."""
    async with async_session_maker() as session:
        row = await session.get(InstanceLicense, 1)
        if row is not None:
            row.license_text = None
            row.installed_at = None
            row.installed_by_email = None
            await session.commit()
    invalidate_license_cache()
    return await _status()
