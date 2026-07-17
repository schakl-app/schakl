"""The 2FA HTTP surface: the login override, the challenge flow, and self-service enrollment.

Two routers, both mounted by :func:`app.core.auth.router.build_auth_router` under the same
``require_local_login`` guard as the rest of the password machinery (2FA *is* local-login
machinery — an org that enforces OIDC has no use for any of it):

* ``login_router`` — replaces FastAPI Users' ``/auth/login``. Same contract for accounts
  without a confirmed factor (cookie on 204, ``LOGIN_BAD_CREDENTIALS`` on 400); an account
  *with* one gets ``200 {"two_factor_required": true, "challenge_token", "methods"}`` and no
  cookie. The challenge token is a short-lived JWT (``fastapi_users.jwt``, its own audience) —
  proof of a fresh password check, redeemable only at ``/auth/2fa/verify``.
* ``twofactor_router`` — ``/auth/2fa/*``: the pre-auth challenge endpoints (verify, SMS send)
  and the authenticated self-service ones (status, setup, confirm, backup codes, SMS
  enrollment, disable).

Every code check runs through the shared brute-force damping in :mod:`twofactor`, and every
mutation is committed *before* any external call (the SMS gateway) per docs/PERFORMANCE.md.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.router.common import ErrorCode
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import twofactor as tf
from app.core.auth.backend import auth_backend
from app.core.auth.models import User
from app.core.auth.users import current_active_user, get_user_manager
from app.core.models import OrgSettings
from app.core.tenancy import request_hostname, resolve_org
from app.db import get_session, set_current_org
from app.errors import AppError
from app.i18n import resolve_locale, translate

logger = logging.getLogger("schakl.auth")

CHALLENGE_AUDIENCE = "schakl:twofactor"
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")

login_router = APIRouter()
router = APIRouter(prefix="/2fa")


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class LoginChallenge(BaseModel):
    """What ``/auth/login`` returns instead of a cookie when a second factor is required."""

    two_factor_required: bool = True
    challenge_token: str
    methods: list[str]


class ChallengeVerify(BaseModel):
    challenge_token: str
    code: str = Field(min_length=1, max_length=32)
    #: Which factor ``code`` is: authenticator app, single-use backup code, or SMS code.
    method: str = Field(default="totp", pattern="^(totp|backup|sms)$")


class ChallengeSms(BaseModel):
    challenge_token: str


class TwoFactorSmsInfo(BaseModel):
    phone_masked: str
    confirmed: bool


class TwoFactorStatus(BaseModel):
    #: Login demands a second factor (setup was confirmed).
    enabled: bool
    #: A setup exists but was never confirmed with a code (QR shown, app never verified).
    pending: bool
    backup_codes_remaining: int = 0
    #: Instance-level: whether the operator configured an SMS gateway at all.
    sms_available: bool = False
    sms: TwoFactorSmsInfo | None = None


class TwoFactorSetupOut(BaseModel):
    secret: str
    otpauth_url: str
    qr_svg: str


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class BackupCodesOut(BaseModel):
    backup_codes: list[str]


class DisableIn(BaseModel):
    password: str = Field(default="", max_length=128)


class SmsSetupIn(BaseModel):
    phone: str = Field(max_length=32)


class SmsSendOut(BaseModel):
    phone_masked: str


# --------------------------------------------------------------------------- #
# Challenge token — proof of a fresh password check, nothing more
# --------------------------------------------------------------------------- #


def make_challenge_token(user: User) -> str:
    return generate_jwt(
        {"sub": str(user.id), "aud": CHALLENGE_AUDIENCE},
        settings.secret_key,
        settings.twofactor_challenge_lifetime_seconds,
    )


async def _challenge_user(session: AsyncSession, token: str) -> tuple[User, tf.UserTwoFactor]:
    """Resolve a challenge token to its (active) user and confirmed 2FA row, or 401."""
    invalid = AppError(
        "two_factor_challenge_invalid", "errors.two_factor_challenge_invalid", status_code=401
    )
    try:
        payload = decode_jwt(token, settings.secret_key, audience=[CHALLENGE_AUDIENCE])
        user_id = uuid.UUID(str(payload["sub"]))
    except Exception:  # noqa: BLE001 — expired/forged/malformed all read the same to the caller
        raise invalid from None
    user = await session.get(User, user_id)
    row = await tf.row_for(session, user_id) if user is not None else None
    if user is None or not user.is_active or not tf.is_active(row):
        raise invalid
    return user, row  # type: ignore[return-value]  # is_active(row) narrowed it


# --------------------------------------------------------------------------- #
# Login — replaces FastAPI Users' route (same name, same contract without 2FA)
# --------------------------------------------------------------------------- #


@login_router.post(
    "/login",
    name=f"auth:{auth_backend.name}.login",
    responses={
        status.HTTP_200_OK: {
            "model": LoginChallenge,
            "description": "Password accepted, second factor required — no cookie yet.",
        },
        status.HTTP_204_NO_CONTENT: {"description": "Logged in; session cookie set."},
        status.HTTP_400_BAD_REQUEST: {"description": "Bad credentials or inactive user."},
    },
)
async def login(
    request: Request,
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager=Depends(get_user_manager),  # noqa: ANN001 — FastAPI Users' provider
    strategy=Depends(auth_backend.get_strategy),  # noqa: ANN001
    session: AsyncSession = Depends(get_session),
):
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.LOGIN_BAD_CREDENTIALS,
        )
    row = await tf.row_for(session, user.id)
    if tf.is_active(row):
        # The password is right, but that is only the first factor: no cookie leaves the
        # server until /auth/2fa/verify redeems this token with a valid code.
        challenge = LoginChallenge(
            challenge_token=make_challenge_token(user), methods=tf.methods_for(row)
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=challenge.model_dump())
    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response


# --------------------------------------------------------------------------- #
# Challenge flow (pre-auth — the caller holds a challenge token, not a session)
# --------------------------------------------------------------------------- #


@router.post("/verify", name="auth:twofactor.verify")
async def verify_challenge(
    payload: ChallengeVerify,
    request: Request,
    user_manager=Depends(get_user_manager),  # noqa: ANN001
    strategy=Depends(auth_backend.get_strategy),  # noqa: ANN001
    session: AsyncSession = Depends(get_session),
):
    """Redeem a login challenge with a code from any enrolled factor → session cookie."""
    user, row = await _challenge_user(session, payload.challenge_token)
    tf.check_not_locked(row)

    if payload.method == "totp":
        ok = tf.verify_totp(row, payload.code)
    elif payload.method == "backup":
        ok = tf.consume_backup_code(row, payload.code)
    else:
        ok = "sms" in tf.methods_for(row) and tf.verify_sms_code(row, payload.code)

    if not ok:
        tf.record_failure(row)
        await session.commit()
        raise AppError("two_factor_code_invalid", "errors.two_factor_code_invalid")
    tf.record_success(row)
    await session.commit()
    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response


@router.post("/challenge/sms", response_model=SmsSendOut, name="auth:twofactor.challenge_sms")
async def send_challenge_sms(
    payload: ChallengeSms,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SmsSendOut:
    """Text a login code to the enrolled number — only for accounts that confirmed one."""
    user, row = await _challenge_user(session, payload.challenge_token)
    if "sms" not in tf.methods_for(row):
        raise AppError("sms_not_configured", "errors.sms_not_configured")
    tf.check_not_locked(row)
    code = tf.issue_sms_code(row)
    phone = row.sms_phone or ""
    locale = await _locale_for(session, request, user)
    await session.commit()  # the hash must survive even if the gateway call fails
    await tf.send_sms(phone, translate("twofactor.sms_message", locale, code=code))
    return SmsSendOut(phone_masked=tf.mask_phone(phone))


# --------------------------------------------------------------------------- #
# Self-service enrollment (authenticated; own account only)
# --------------------------------------------------------------------------- #


@router.get("", response_model=TwoFactorStatus, name="auth:twofactor.status")
async def twofactor_status(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> TwoFactorStatus:
    row = await tf.row_for(session, user.id)
    sms = None
    if row is not None and row.sms_phone:
        sms = TwoFactorSmsInfo(
            phone_masked=tf.mask_phone(row.sms_phone), confirmed=row.sms_confirmed_at is not None
        )
    return TwoFactorStatus(
        enabled=tf.is_active(row),
        pending=row is not None and row.confirmed_at is None,
        backup_codes_remaining=len(row.backup_codes) if row is not None else 0,
        sms_available=tf.sms_available(),
        sms=sms,
    )


@router.post("/setup", response_model=TwoFactorSetupOut, name="auth:twofactor.setup")
async def setup_twofactor(
    request: Request,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> TwoFactorSetupOut:
    """Start (or restart) enrollment: mint a secret, return it as QR + manual key.

    Idempotent while unconfirmed — every call rotates the pending secret, so an abandoned
    tab's QR can never silently stay valid. A *confirmed* setup must be disabled first.
    """
    row = await tf.row_for(session, user.id)
    if tf.is_active(row):
        raise AppError(
            "two_factor_already_enabled", "errors.two_factor_already_enabled", status_code=409
        )
    if row is None:
        row = tf.new_row(user.id)
        session.add(row)
    else:
        row.totp_secret_enc = tf.encrypt(tf.new_totp_secret())
    issuer = await _issuer_for(session, request)
    url = tf.otpauth_url(row, user, issuer)
    secret = tf.decrypt(row.totp_secret_enc)
    await session.commit()
    return TwoFactorSetupOut(secret=secret, otpauth_url=url, qr_svg=tf.qr_svg(url))


@router.post("/confirm", response_model=BackupCodesOut, name="auth:twofactor.confirm")
async def confirm_twofactor(
    payload: CodeIn,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> BackupCodesOut:
    """A valid code from the freshly-scanned app is what turns 2FA on — and mints the backup
    codes, returned exactly once."""
    row = await tf.row_for(session, user.id)
    if row is None:
        raise AppError("two_factor_not_enabled", "errors.two_factor_not_enabled", status_code=409)
    if row.confirmed_at is not None:
        raise AppError(
            "two_factor_already_enabled", "errors.two_factor_already_enabled", status_code=409
        )
    tf.check_not_locked(row)
    if not tf.verify_totp(row, payload.code):
        tf.record_failure(row)
        await session.commit()
        raise AppError("two_factor_code_invalid", "errors.two_factor_code_invalid")
    tf.record_success(row)
    row.confirmed_at = datetime.now(UTC)
    codes = tf.generate_backup_codes(row)
    await session.commit()
    logger.info("2FA enabled for %s", user.email)
    return BackupCodesOut(backup_codes=codes)


@router.post(
    "/backup-codes", response_model=BackupCodesOut, name="auth:twofactor.backup_codes"
)
async def regenerate_backup_codes(
    payload: CodeIn,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> BackupCodesOut:
    """A fresh set (shown once), invalidating every previous code. Costs a current TOTP code —
    a stolen session alone must not be able to mint recovery codes."""
    row = await tf.row_for(session, user.id)
    if not tf.is_active(row):
        raise AppError("two_factor_not_enabled", "errors.two_factor_not_enabled", status_code=409)
    tf.check_not_locked(row)
    if not tf.verify_totp(row, payload.code):
        tf.record_failure(row)
        await session.commit()
        raise AppError("two_factor_code_invalid", "errors.two_factor_code_invalid")
    tf.record_success(row)
    codes = tf.generate_backup_codes(row)
    await session.commit()
    return BackupCodesOut(backup_codes=codes)


@router.post("/disable", status_code=204, name="auth:twofactor.disable")
async def disable_twofactor(
    payload: DisableIn,
    user: User = Depends(current_active_user),
    user_manager=Depends(get_user_manager),  # noqa: ANN001
    session: AsyncSession = Depends(get_session),
) -> None:
    """Turn 2FA off for the caller's own account. A **confirmed** setup costs the password
    (a stolen session must not be able to strip the second factor); abandoning an unconfirmed
    setup is free. Lost everything? That is what the org admin's reset is for (members.py)."""
    row = await tf.row_for(session, user.id)
    if row is None:
        return
    if row.confirmed_at is not None:
        verified, _ = user_manager.password_helper.verify_and_update(
            payload.password, user.hashed_password
        )
        if not verified:
            raise AppError(
                "two_factor_password_invalid", "errors.two_factor_password_invalid"
            )
    await session.delete(row)
    await session.commit()
    logger.info("2FA disabled for %s", user.email)


# --- SMS factor enrollment (on top of a confirmed TOTP setup) ---


@router.post("/sms/setup", response_model=SmsSendOut, name="auth:twofactor.sms_setup")
async def setup_sms(
    payload: SmsSetupIn,
    request: Request,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> SmsSendOut:
    """Register a number for SMS codes: it becomes usable only after echoing a code sent to it.
    SMS is an *additional* factor on a confirmed TOTP setup, never the only one — a number can
    be re-registered, but 2FA cannot start out SMS-only."""
    if not tf.sms_available():
        raise AppError("sms_not_configured", "errors.sms_not_configured")
    row = await tf.row_for(session, user.id)
    if not tf.is_active(row):
        raise AppError("two_factor_not_enabled", "errors.two_factor_not_enabled", status_code=409)
    phone = payload.phone.strip().replace(" ", "")
    if not _E164.fullmatch(phone):
        raise AppError(
            "validation", "errors.validation", status_code=422,
            fields={"phone": "errors.invalid_phone"},
        )
    row.sms_pending_phone = phone
    code = tf.issue_sms_code(row)
    locale = await _locale_for(session, request, user)
    await session.commit()
    await tf.send_sms(phone, translate("twofactor.sms_message", locale, code=code))
    return SmsSendOut(phone_masked=tf.mask_phone(phone))


@router.post("/sms/confirm", status_code=204, name="auth:twofactor.sms_confirm")
async def confirm_sms(
    payload: CodeIn,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await tf.row_for(session, user.id)
    if not tf.is_active(row) or not row.sms_pending_phone:
        raise AppError("two_factor_not_enabled", "errors.two_factor_not_enabled", status_code=409)
    tf.check_not_locked(row)
    if not tf.verify_sms_code(row, payload.code):
        tf.record_failure(row)
        await session.commit()
        raise AppError("two_factor_code_invalid", "errors.two_factor_code_invalid")
    tf.record_success(row)
    row.sms_phone = row.sms_pending_phone
    row.sms_pending_phone = None
    row.sms_confirmed_at = datetime.now(UTC)
    await session.commit()


@router.delete("/sms", status_code=204, name="auth:twofactor.sms_disable")
async def disable_sms(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Drop the SMS factor (TOTP + backup codes remain — never leaves the account factor-less)."""
    row = await tf.row_for(session, user.id)
    if row is None:
        return
    row.sms_phone = None
    row.sms_pending_phone = None
    row.sms_confirmed_at = None
    row.sms_code_hash = None
    row.sms_code_expires_at = None
    await session.commit()


# --------------------------------------------------------------------------- #
# Tenant context for pre/post-auth reads (branding + locale, the emails.py pattern)
# --------------------------------------------------------------------------- #


async def _org_settings_for(session: AsyncSession, request: Request) -> OrgSettings | None:
    org = await resolve_org(session, request_hostname(request))
    if org is None:
        return None
    await set_current_org(session, org.id)
    return await session.scalar(select(OrgSettings).where(OrgSettings.org_id == org.id))


async def _issuer_for(session: AsyncSession, request: Request) -> str:
    """The issuer shown in authenticator apps: the tenant's brand, never a hardcoded name."""
    org_settings = await _org_settings_for(session, request)
    if org_settings is not None and org_settings.brand_name:
        return org_settings.brand_name
    return request_hostname(request) or "login"


async def _locale_for(session: AsyncSession, request: Request, user: User) -> str:
    org_settings = await _org_settings_for(session, request)
    return resolve_locale(
        user.locale, org_settings.default_locale if org_settings is not None else None
    )
