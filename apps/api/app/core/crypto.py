"""Symmetric encryption for secrets at rest (#17, and the Google token vault per docs/GOOGLE.md).

A single primitive — Fernet (AES-128-CBC + HMAC, authenticated) keyed from a derived 32-byte key
— so credentials that must round-trip (a Slack webhook, an SMTP password, an OAuth refresh token)
are never stored plaintext. The key is derived from ``settings.encryption_key`` (falling back to
``secret_key``) so a fresh install needs no extra configuration, and a deployment can rotate the
encryption secret independently by setting ``SCHAKL_ENCRYPTION_KEY``.

This is the ``*_enc`` column convention GOOGLE.md specifies; reuse it wherever a secret has to be
read back rather than merely verified (for verification, hash instead — see ``apikeys/keys.py``).
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    material = (settings.encryption_key or settings.secret_key).encode()
    # A Fernet key is 32 url-safe-base64 bytes; SHA-256 of the configured secret gives exactly 32.
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 secret to a token safe to store in a text column."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token from :func:`encrypt`. Raises :class:`ValueError` on a bad/rotated token."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - surfaced as a decrypt failure upstream
        raise ValueError("could not decrypt secret (wrong key or corrupt data)") from exc
