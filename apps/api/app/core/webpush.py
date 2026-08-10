"""Web Push: VAPID authentication, payload encryption, and the send (#309).

Three RFCs, none of which needs a new dependency — the whole of it is ``cryptography`` (already
here for Fernet and the licence keys) plus ``httpx`` (already here, already async):

* **RFC 8030** — the protocol: POST the encrypted body to the subscription's own ``endpoint``.
* **RFC 8291** — the encryption: an ephemeral P-256 key, ECDH against the browser's ``p256dh``,
  two HKDF derivations, AES-128-GCM, wrapped in an ``aes128gcm`` content-coding record.
* **RFC 8292** — the authentication: an ES256 JWT naming the push service as its audience, sent
  beside the application server's own public key.

The payload is encrypted **to the browser's keys**, so the push service (Google, Mozilla, Apple)
forwards a blob it cannot read. That is what makes it acceptable to put a notification's actual
sentence in it rather than a "you have a message" stub.

Deliberately not ``pywebpush``: it is synchronous and ``requests``-based, so every send would
need a thread out of an otherwise async worker. Deliberately not ``apprise``'s ``vapid://``
plugin either — it reads its subscriptions from a JSON file on disk and POSTs to a fixed FCM URL
instead of the subscription's own endpoint. Its ``apprise/utils/pem.py`` *is* a correct reference
implementation of the RFC 8291 half, and :func:`encrypt` was checked against it, but it is a
private module of a dependency pinned with no upper bound.

**The endpoint is attacker-supplied.** It arrives from a browser, but nothing about the request
proves that — the API cannot tell a browser from a ``curl``, so an unguarded endpoint aims the
worker at the instance's own network. It is guarded on the way in *and* again here on the way
out, because DNS can rebind in between (``app.core.net_guard``, the rule every tenant-supplied
outbound target follows).
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.net_guard import SsrfBlocked, assert_host_public_sync

#: A push service must accept at least 4096 bytes of encrypted record. The record adds a 86-byte
#: header and 16 bytes of GCM tag over the plaintext, so this is what a payload may weigh.
MAX_PAYLOAD_BYTES = 4096 - 86 - 16

#: How long the browser's push service should hold the message for a device that is offline.
#: Four hours: long enough for a closed laptop over lunch, short enough that a notification never
#: arrives so late it is a lie about the present.
DEFAULT_TTL_SECONDS = 4 * 60 * 60

_JWT_LIFETIME_SECONDS = 12 * 60 * 60
_TIMEOUT = httpx.Timeout(10.0)


class WebPushError(Exception):
    """A send failed. ``gone`` marks the one failure that is not an error (see below)."""

    def __init__(self, message: str, *, status_code: int | None = None, gone: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        #: The subscription is retired (``404``/``410``). The caller must delete the row rather
        #: than retry it: a device someone threw away is not a delivery failure, and spending
        #: attempts on it eventually fails the bundle for the devices that *are* alive.
        self.gone = gone


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64url_decode(value: str) -> bytes:
    """Decode unpadded base64url. Browsers omit the padding; ``binascii`` insists on it."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


# --------------------------------------------------------------------------- #
# VAPID (RFC 8292)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VapidKeys:
    """An application server's identity to the push services.

    ``public_key`` is the uncompressed P-256 point the browser passed to ``pushManager.subscribe``
    as its ``applicationServerKey``; ``private_key`` is the PKCS#8 PEM behind it. A subscription
    is bound to the public key it was created with, so **rotating these invalidates every
    existing subscription** — which is why nothing in this codebase rotates them.
    """

    public_key: str
    private_key: str


def generate_keys() -> VapidKeys:
    """Mint a fresh application-server keypair. Called once per org, lazily."""
    private = ec.generate_private_key(ec.SECP256R1())
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return VapidKeys(public_key=b64url_encode(public_bytes), private_key=pem)


def _origin(endpoint: str) -> str:
    """The JWT audience: the push service's origin, scheme and host only (RFC 8292 §2)."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def vapid_headers(endpoint: str, keys: VapidKeys, *, subject: str) -> dict[str, str]:
    """The ``Authorization`` header proving who is asking the push service to deliver.

    ``subject`` is a contact the push service can reach if this application server misbehaves —
    a ``mailto:`` or an ``https:`` URL, per the RFC. It is not a secret and reaches no browser.
    """
    private = serialization.load_pem_private_key(keys.private_key.encode(), password=None)
    head = json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()
    header = b64url_encode(head)
    claims = {
        "aud": _origin(endpoint),
        "exp": int(time.time()) + _JWT_LIFETIME_SECONDS,
        "sub": subject,
    }
    body = b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()

    # ES256 wants the raw r‖s pair; `cryptography` signs to DER, so unpack and re-pad. Getting
    # this wrong produces a signature every local test accepts and every push service rejects.
    der = private.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = asym_utils.decode_dss_signature(der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    token = f"{header}.{body}.{b64url_encode(signature)}"
    return {"Authorization": f"vapid t={token}, k={keys.public_key}"}


# --------------------------------------------------------------------------- #
# Payload encryption (RFC 8291)
# --------------------------------------------------------------------------- #
def encrypt(
    payload: bytes,
    *,
    p256dh: str,
    auth: str,
    salt: bytes | None = None,
    ephemeral: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """Encrypt ``payload`` to one subscription, as a single ``aes128gcm`` record.

    ``p256dh`` is the browser's public key and ``auth`` its shared authentication secret, both
    base64url exactly as ``PushSubscription.toJSON()`` hands them over. Neither is a secret of
    *ours*: they are the recipient's, and their only use is this direction of the conversation.

    ``salt`` and ``ephemeral`` exist so the RFC 8291 §5 test vector can be reproduced byte for
    byte — the only way to prove this function is *correct* rather than merely self-consistent,
    since a round-trip against our own decryption would pass on a wrong-but-symmetric mistake.
    Nothing outside the tests passes them; both are random per message in production, and reusing
    either across two messages to the same subscription would leak the plaintext.
    """
    client_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b64url_decode(p256dh)
    )
    auth_secret = b64url_decode(auth)

    ephemeral = ephemeral or ec.generate_private_key(ec.SECP256R1())
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    client_public_bytes = client_public.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    shared = ephemeral.exchange(ec.ECDH(), client_public)

    # RFC 8291 §3.4: the auth secret salts the first derivation and the two public keys are the
    # info, which is what binds the ciphertext to *this* pair of parties.
    prk = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=auth_secret,
        info=b"WebPush: info\x00" + client_public_bytes + ephemeral_public,
    ).derive(shared)

    salt = salt or os.urandom(16)
    content_key = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(prk)
    nonce = HKDF(
        algorithm=hashes.SHA256(),
        length=12,
        salt=salt,
        info=b"Content-Encoding: nonce\x00",
    ).derive(prk)

    # The 0x02 delimiter marks the last record; we always send exactly one.
    ciphertext = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!L", 4096) + struct.pack("!B", len(ephemeral_public))
    return header + ephemeral_public + ciphertext


# --------------------------------------------------------------------------- #
# The send (RFC 8030)
# --------------------------------------------------------------------------- #
def assert_endpoint_safe(endpoint: str) -> None:
    """Refuse an endpoint that is not ``https`` on a publicly-routable host.

    Called at subscribe time *and* again at send time. Once is not enough: the two are minutes
    or days apart, and a host that resolved publicly then can resolve to ``169.254.169.254``
    now. ``SCHAKL_ALLOW_PRIVATE_NOTIFICATION_TARGETS`` covers the trusted-LAN deployment, the
    same escape hatch every other guarded outbound path uses.
    """
    parts = urlsplit(endpoint)
    if parts.scheme != "https":
        raise SsrfBlocked("a push endpoint must be https")
    if not parts.hostname:
        raise SsrfBlocked("missing host")
    assert_host_public_sync(parts.hostname)


async def send(
    endpoint: str,
    *,
    p256dh: str,
    auth: str,
    payload: dict,
    keys: VapidKeys,
    subject: str,
    ttl: int = DEFAULT_TTL_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Deliver one notification to one device. Returns on success, raises :class:`WebPushError`.

    A ``404``/``410`` raises with ``gone=True`` — the caller deletes the subscription instead of
    retrying it. Everything else (``429``, ``5xx``, a connection error) is an ordinary failure
    that rides the delivery row's existing backoff.
    """
    assert_endpoint_safe(endpoint)
    body = json.dumps(payload, separators=(",", ":")).encode()
    if len(body) > MAX_PAYLOAD_BYTES:
        # The truncation upstream is meant to prevent this; reaching here is our bug, not the
        # push service's, and retrying an oversized body would only fail identically.
        raise WebPushError(f"payload too large ({len(body)} bytes)")

    headers = {
        **vapid_headers(endpoint, keys, subject=subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": "normal",
    }
    encrypted = encrypt(body, p256dh=p256dh, auth=auth)

    owned = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await http.post(endpoint, content=encrypted, headers=headers)
    except httpx.HTTPError as exc:
        raise WebPushError(str(exc)) from exc
    finally:
        if owned:
            await http.aclose()

    if response.status_code in (404, 410):
        raise WebPushError("subscription is gone", status_code=response.status_code, gone=True)
    if response.status_code >= 400:
        detail = (response.text or "").strip()[:200]
        raise WebPushError(
            f"push service returned {response.status_code}{f': {detail}' if detail else ''}",
            status_code=response.status_code,
        )
