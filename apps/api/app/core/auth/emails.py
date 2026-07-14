"""Tenant-branded password emails (#161): reset and invite, through the org transport (#17).

One function serves both flows — an invite *is* a set-password link riding the reset-token
mechanism, only worded as a welcome. The send never raises and always leaves the token in the
log on failure: the write that triggered it (a forgot-password request, an invite) must stand,
and an operator without a configured transport can still recover the flow the P0 way.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.auth.sso import org_base_url
from app.core.email.senders import OutgoingEmail
from app.core.email.service import send_org_email
from app.core.email.templates import build_email_content, resolve_template
from app.core.models import OrgSettings
from app.core.tenancy import resolve_org
from app.db import set_current_org
from app.i18n import resolve_locale

logger = logging.getLogger("schakl.auth")

PasswordEmailKind = Literal["reset", "invite"]


async def send_password_email(
    session: AsyncSession,
    user: User,
    token: str,
    request: Request | None,
    kind: PasswordEmailKind = "reset",
) -> tuple[bool, str | None]:
    """``(sent, error)`` — the error is an i18n key for a missing transport, provider text
    otherwise. The org resolves from the request host (§5); the link lands on the org's own
    address (``org_base_url``), in the recipient's locale (§8)."""
    host = (request.headers.get("host") or "").split(":")[0] if request is not None else ""
    org = await resolve_org(session, host) if host else None
    if org is None:
        logger.warning(
            "Password %s email for %s: no resolvable org (host=%r); token=%s",
            kind,
            user.email,
            host,
            token,
        )
        return False, None
    # Bind the resolved tenant so the RLS-FORCED reads below (org_settings, the tier-2 email
    # template) actually return this org's rows. The forgot/invite flows may run on a session
    # that never bound the GUC (a pre-auth reset, or FastAPI Users' own user-db session), and
    # without this an org's branding *and* its custom template would silently fall back.
    await set_current_org(session, org.id)
    org_settings = await session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org.id)
    )
    brand = (org_settings.brand_name if org_settings else "") or org.name
    locale = resolve_locale(
        user.locale, org_settings.default_locale if org_settings else None
    )
    link = f"{org_base_url(org)}/reset-password?token={token}"
    values = {"name": user.full_name or user.email, "brand": brand, "link": link}
    # A tenant override for (kind, locale) wins (#161 tier 2); a missing one falls back to the
    # catalog default. The plaintext part always carries the link, HTML rides only when set.
    template = await resolve_template(session, org.id, kind, locale)
    subject, text, html = build_email_content(
        kind,
        locale,
        template.subject if template else None,
        template.body_html if template else None,
        values,
    )
    message = OutgoingEmail(to=user.email, subject=subject, text=text, html=html)
    sent, error = await send_org_email(session, org.id, message)
    if not sent:
        logger.warning(
            "Password %s email to %s not sent (%s); token=%s", kind, user.email, error, token
        )
    return sent, error
