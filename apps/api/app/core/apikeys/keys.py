"""Secret generation, hashing and parsing for API keys (#20).

Format: ``schakl_<prefix>_<secret>``. The ``prefix`` (looked up plaintext) and the ``secret``
(never stored — only its SHA-256) are both high-entropy URL-safe tokens. SHA-256 over a 256-bit
random secret, then a constant-time compare, is the right cost: a slow KDF like Argon2 protects
low-entropy passwords, and would only tax every API request here for nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

# Hex tokens, not base64url: base64url's alphabet includes ``_``, which would collide with the
# separator and make the prefix ambiguous. Hex is [0-9a-f] only, so the split is unambiguous.
TOKEN_PREFIX = "schakl_"
_PREFIX_BYTES = 6  # → 12 hex chars
_SECRET_BYTES = 32  # → 64 hex chars, 256 bits


@dataclass(frozen=True)
class GeneratedKey:
    prefix: str
    secret_hash: str
    #: The full key, shown to the caller exactly once and never persisted.
    plaintext: str


def generate() -> GeneratedKey:
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_hex(_SECRET_BYTES)
    plaintext = f"{TOKEN_PREFIX}{prefix}_{secret}"
    return GeneratedKey(prefix=prefix, secret_hash=hash_secret(secret), plaintext=plaintext)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_secret(secret: str, stored_hash: str) -> bool:
    """Constant-time compare, so a timing side-channel can't recover the secret byte by byte."""
    return hmac.compare_digest(hash_secret(secret), stored_hash)


def parse(raw: str) -> tuple[str, str] | None:
    """``(prefix, secret)`` from a presented key, or ``None`` if it isn't one of ours."""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    body = raw[len(TOKEN_PREFIX) :]
    prefix, sep, secret = body.partition("_")
    if not sep or not prefix or not secret:
        return None
    return prefix, secret


def redacted(prefix: str) -> str:
    """What the API returns instead of the secret: ``schakl_<prefix>_****``."""
    return f"{TOKEN_PREFIX}{prefix}_{'*' * 8}"
