"""Two-factor authentication for local (password) login — TOTP, backup codes, optional SMS.

FastAPI Users has no second-factor concept, so this layer sits on top of it: the login route
(:mod:`app.core.auth.twofactor_router`) withholds the session cookie when the account has a
**confirmed** factor and hands out a short-lived, signed *challenge token* instead; redeeming it
with a valid code is what issues the cookie. Everything reuses the framework's own primitives —
``fastapi_users.jwt`` for the challenge token, the manager's password helper for re-checks, and
the cookie backend for the final login — rather than inventing parallel machinery.

Like ``users``, this is **global identity, not tenant data** (CLAUDE.md §5): a user's second
factor follows them across every org they are a member of, so the table carries no ``org_id``
and no RLS. Org admins can *reset* a member's 2FA (``members.py``) — an availability escape
hatch for a lost phone, audited, never a read of any secret.

Secrets at rest follow the house rules (``app.core.crypto``): the TOTP secret is Fernet-encrypted
(it must round-trip to verify codes); backup codes and SMS codes are stored **hashed** (verify-
only, like API keys). The SMS factor exists only when the instance operator configured a gateway
(``SCHAKL_SMS_GATEWAY_URL`` — docs/TWOFACTOR.md); it is a per-user opt-in on top of TOTP, never a
replacement for it.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pyotp
import segno
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.auth.models import User
from app.core.crypto import decrypt, encrypt
from app.core.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base
from app.errors import AppError

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

BACKUP_CODE_COUNT = 10
#: Verification failures tolerated before the factor locks for the cooldown window.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_WINDOW = timedelta(minutes=15)
SMS_CODE_LIFETIME = timedelta(minutes=10)
#: Minimum seconds between two SMS sends for the same account (cost + abuse control).
SMS_RESEND_INTERVAL = timedelta(seconds=30)
SMS_CODE_MAX_ATTEMPTS = 5


class UserTwoFactor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per enrolled user. The row existing does **not** mean 2FA is on —
    ``confirmed_at`` does: setup writes the row, the first valid TOTP code confirms it, and
    only a confirmed row makes login demand a second factor. Deleting the row (self-disable
    with password, or an org admin's reset) turns the account back into a plain password login.
    """

    __tablename__ = "user_two_factor"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    #: Fernet-encrypted base32 TOTP secret — must round-trip, so encrypted rather than hashed.
    totp_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The TOTP time-step of the last accepted code. A code is one-time: replaying it within
    #: its 30-second window (shoulder-surf, network capture) must fail, so verification refuses
    #: any step at or before this counter.
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: SHA-256 digests of the unused backup codes; a used code is removed, never re-accepted.
    backup_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # --- SMS factor (exists only when the instance gateway is configured) ---
    #: E.164 number the codes go to, set only after it was itself verified by a code.
    sms_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sms_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Number awaiting its verification code (enrollment in progress).
    sms_pending_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The one outstanding SMS code: hashed, expiring, attempt-limited.
    sms_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sms_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sms_code_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sms_code_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- Brute-force damping on the verify paths ---
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------- #
# Row access
# --------------------------------------------------------------------------- #


async def row_for(session: AsyncSession, user_id: uuid.UUID) -> UserTwoFactor | None:
    return await session.scalar(select(UserTwoFactor).where(UserTwoFactor.user_id == user_id))


def is_active(row: UserTwoFactor | None) -> bool:
    """Does login demand a second factor for this account?"""
    return row is not None and row.confirmed_at is not None


def sms_available() -> bool:
    """Instance-level switch: the SMS option exists only when the operator set a gateway."""
    return bool(settings.sms_gateway_url)


def methods_for(row: UserTwoFactor) -> list[str]:
    """The challenge methods this account can answer with, in UI order."""
    methods = ["totp", "backup"]
    if sms_available() and row.sms_phone and row.sms_confirmed_at:
        methods.append("sms")
    return methods


# --------------------------------------------------------------------------- #
# TOTP
# --------------------------------------------------------------------------- #


def new_totp_secret() -> str:
    return pyotp.random_base32()

def _totp(row: UserTwoFactor) -> pyotp.TOTP:
    return pyotp.TOTP(decrypt(row.totp_secret_enc))


def otpauth_url(row: UserTwoFactor, user: User, issuer: str) -> str:
    """The ``otpauth://`` provisioning URI authenticator apps import (QR or manual)."""
    return _totp(row).provisioning_uri(name=user.email, issuer_name=issuer)


def qr_svg(payload: str) -> str:
    """The payload as an inline SVG QR code (segno is pure Python — no PIL, no web dependency)."""
    buffer = io.BytesIO()
    segno.make(payload, error="m").save(buffer, kind="svg", xmldecl=False, scale=4, border=2)
    return buffer.getvalue().decode("utf-8")


def verify_totp(row: UserTwoFactor, code: str) -> bool:
    """Check a TOTP code, tolerating one 30-second step of clock drift either way, and refuse
    replay of a step already accepted (``totp_last_counter``). Mutates the row on success."""
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    totp = _totp(row)
    now = datetime.now(UTC)
    current_step = int(now.timestamp()) // totp.interval
    for step in (current_step, current_step - 1, current_step + 1):
        if row.totp_last_counter is not None and step <= row.totp_last_counter:
            continue
        if hmac.compare_digest(totp.at(step * totp.interval), code):
            row.totp_last_counter = step
            return True
    return False


# --------------------------------------------------------------------------- #
# Backup codes
# --------------------------------------------------------------------------- #


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _normalize_backup(code: str) -> str:
    return code.strip().lower().replace("-", "").replace(" ", "")


def generate_backup_codes(row: UserTwoFactor) -> list[str]:
    """Mint a fresh set, replacing any previous one. Returns the plaintext codes — shown once,
    only hashes are stored (the API-key rule: verify, never read back)."""
    codes = [secrets.token_hex(5) for _ in range(BACKUP_CODE_COUNT)]
    row.backup_codes = [_hash_code(code) for code in codes]
    return [f"{code[:5]}-{code[5:]}" for code in codes]


def consume_backup_code(row: UserTwoFactor, code: str) -> bool:
    """Single-use: a matching code is removed from the set as it is accepted."""
    digest = _hash_code(_normalize_backup(code))
    remaining = [stored for stored in row.backup_codes if not hmac.compare_digest(stored, digest)]
    if len(remaining) == len(row.backup_codes):
        return False
    row.backup_codes = remaining
    return True


# --------------------------------------------------------------------------- #
# Brute-force damping (shared by every verify path)
# --------------------------------------------------------------------------- #


def check_not_locked(row: UserTwoFactor) -> None:
    if row.failed_attempts >= MAX_FAILED_ATTEMPTS and row.last_failed_at is not None:
        if datetime.now(UTC) - row.last_failed_at < LOCKOUT_WINDOW:
            raise AppError("two_factor_locked", "errors.two_factor_locked", status_code=429)
        # Window elapsed: the slate is clean again.
        row.failed_attempts = 0
        row.last_failed_at = None


def record_failure(row: UserTwoFactor) -> None:
    row.failed_attempts += 1
    row.last_failed_at = datetime.now(UTC)


def record_success(row: UserTwoFactor) -> None:
    row.failed_attempts = 0
    row.last_failed_at = None


# --------------------------------------------------------------------------- #
# SMS codes
# --------------------------------------------------------------------------- #


def issue_sms_code(row: UserTwoFactor) -> str:
    """Stamp a fresh outstanding code on the row (hash, expiry, attempt counter) and return the
    plaintext for sending. Enforces the resend interval."""
    now = datetime.now(UTC)
    if row.sms_code_sent_at is not None and now - row.sms_code_sent_at < SMS_RESEND_INTERVAL:
        raise AppError("sms_resend_too_soon", "errors.sms_resend_too_soon", status_code=429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    row.sms_code_hash = _hash_code(code)
    row.sms_code_expires_at = now + SMS_CODE_LIFETIME
    row.sms_code_sent_at = now
    row.sms_code_attempts = 0
    return code


def verify_sms_code(row: UserTwoFactor, code: str) -> bool:
    """Check the outstanding SMS code; expired, attempt-exhausted or absent codes never match.
    The code is cleared on success (single use)."""
    now = datetime.now(UTC)
    if (
        row.sms_code_hash is None
        or row.sms_code_expires_at is None
        or now > row.sms_code_expires_at
        or row.sms_code_attempts >= SMS_CODE_MAX_ATTEMPTS
    ):
        return False
    row.sms_code_attempts += 1
    if not hmac.compare_digest(row.sms_code_hash, _hash_code(code.strip())):
        return False
    row.sms_code_hash = None
    row.sms_code_expires_at = None
    return True


async def send_sms(phone: str, message: str) -> None:
    """POST the message to the operator-configured gateway (docs/TWOFACTOR.md).

    Callers must have **committed** any code-hash write first — never hold a DB transaction
    across an external call (docs/PERFORMANCE.md).
    """
    if not settings.sms_gateway_url:
        raise AppError("sms_not_configured", "errors.sms_not_configured", status_code=400)
    payload: dict[str, str] = {"to": phone, "message": message}
    if settings.sms_gateway_sender:
        payload["sender"] = settings.sms_gateway_sender
    headers = {}
    if settings.sms_gateway_token:
        headers["Authorization"] = f"Bearer {settings.sms_gateway_token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(settings.sms_gateway_url, json=payload, headers=headers)
        response.raise_for_status()


def mask_phone(phone: str) -> str:
    """``+31612345678`` → ``+31•••••678`` — enough to recognise, not enough to harvest."""
    if len(phone) <= 6:
        return phone
    return f"{phone[:3]}{'•' * (len(phone) - 6)}{phone[-3:]}"


def new_row(user_id: uuid.UUID) -> UserTwoFactor:
    return UserTwoFactor(
        user_id=user_id,
        totp_secret_enc=encrypt(new_totp_secret()),
        backup_codes=[],
    )
