"""REST endpoints for the org e-mail transport under ``/api/v1/settings/email`` (#17).

Everything is gated on ``settings.email.manage`` (Instellingen admin surface): the config embeds
API keys and SMTP credentials, and even the redacted read reveals infrastructure. Deny-by-default
(CLAUDE.md §15).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.email.schemas import EmailSettingsRead, EmailSettingsWrite, EmailTestResult
from app.core.email.service import EmailSettingsService
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/settings/email", tags=["email-settings"])


@router.get(
    "",
    response_model=EmailSettingsRead | None,
    dependencies=[require_permission("settings.email.manage")],
)
async def get_email_settings(
    ctx: RequestContext = Depends(require_context),
) -> EmailSettingsRead | None:
    return await EmailSettingsService(ctx).get()


@router.put(
    "",
    response_model=EmailSettingsRead,
    dependencies=[require_permission("settings.email.manage")],
)
async def save_email_settings(
    payload: EmailSettingsWrite,
    ctx: RequestContext = Depends(require_context),
) -> EmailSettingsRead:
    return await EmailSettingsService(ctx).save(payload)


@router.delete(
    "",
    status_code=204,
    dependencies=[require_permission("settings.email.manage")],
)
async def delete_email_settings(ctx: RequestContext = Depends(require_context)) -> None:
    await EmailSettingsService(ctx).delete()


@router.post(
    "/test",
    response_model=EmailTestResult,
    dependencies=[require_permission("settings.email.manage")],
)
async def test_email_settings(ctx: RequestContext = Depends(require_context)) -> EmailTestResult:
    return await EmailSettingsService(ctx).test()
