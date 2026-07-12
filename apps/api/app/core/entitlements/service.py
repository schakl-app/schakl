"""License verification and entitlement state (issue #137).

The license key format is ``SCHAKL1.<b64url(payload)>.<b64url(signature)>`` where payload is
canonical JSON (compact separators, sorted keys) and the signature is Ed25519 over exactly
those bytes. The signing side lives in the private ``schakl-licensing`` repo; only the
public key ships here (``settings.license_public_key``), so validation is fully offline.

State semantics per sku:

- **entitled** — a license lists the sku and has not expired: everything works.
- **covered** — entitled *or* within the license's own ``grace_days`` after expiry: the
  module keeps working (and may still be enabled) while a renewal is arranged.
- **writable** — covered, or (when no license lists the sku) within the **bootstrap grace**
  window: installs that enabled a licensed module before licensing shipped get
  ``license_bootstrap_grace_days`` from the upgrade migration before mutations stop.
  Past that: mutations get 402, reads and exports keep working forever — data is never
  hostage (epic #140).

The state is cached in-process for a minute — it changes only when a key is installed
(which invalidates explicitly), and licensed-module requests must not pay a query each.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.models import InstanceLicense
from app.db import async_session_maker
from app.errors import AppError
from app.registry import registry

logger = logging.getLogger(__name__)

LICENSE_PREFIX = "SCHAKL1"
#: The MCP server is core code, not a registry module — its sku is declared here.
MCP_SKU = "mcp"
#: The AI core (epic #131) is likewise a core capability with its own sku. Bundling it with
#: automation (or anything else) is a *license document* decision — a plan simply lists both
#: skus — never a coupling in code.
AI_SKU = "ai"

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CACHE_TTL_SECONDS = 60.0


class LicenseError(ValueError):
    """The key text is not a valid, correctly signed schakl license."""


@dataclass(frozen=True)
class LicenseInfo:
    license_id: str
    customer: str
    plan: str
    modules: tuple[str, ...]
    instance_id: str | None
    issued_at: datetime
    expires_at: datetime
    grace_days: int


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise LicenseError("naive timestamp")
    return ts


def verify_license(key_text: str, public_key_b64: str) -> LicenseInfo:
    """Parse ``key_text`` and verify its signature. Raises :class:`LicenseError` on anything
    that is not a well-formed, correctly signed, schema-1 license."""
    try:
        prefix, payload_b64, sig_b64 = key_text.strip().split(".")
        if prefix != LICENSE_PREFIX:
            raise LicenseError(f"unknown prefix {prefix!r}")
        payload_bytes = _unb64(payload_b64)
        signature = _unb64(sig_b64)
        public_key = Ed25519PublicKey.from_public_bytes(_unb64(public_key_b64))
        public_key.verify(signature, payload_bytes)
        payload = json.loads(payload_bytes)
        if payload.get("schema") != 1:
            raise LicenseError(f"unsupported schema {payload.get('schema')!r}")
        return LicenseInfo(
            license_id=str(payload["license_id"]),
            customer=str(payload["customer"]),
            plan=str(payload["plan"]),
            modules=tuple(sorted(str(m) for m in payload["modules"])),
            instance_id=payload.get("instance_id"),
            issued_at=_parse_ts(payload["issued_at"]),
            expires_at=_parse_ts(payload["expires_at"]),
            grace_days=int(payload.get("grace_days", 0)),
        )
    except LicenseError:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error, InvalidSignature) as exc:
        raise LicenseError(str(exc)) from exc


@dataclass(frozen=True)
class LicenseState:
    """The installation's parsed license (or None) plus the bootstrap grace clock."""

    info: LicenseInfo | None
    bootstrap_grace_until: datetime | None

    def entitled(self, sku: str) -> bool:
        return (
            self.info is not None
            and sku in self.info.modules
            and datetime.now(UTC) <= self.info.expires_at
        )

    def covered(self, sku: str) -> bool:
        """Entitled, or inside the license's own post-expiry grace window."""
        return (
            self.info is not None
            and sku in self.info.modules
            and datetime.now(UTC)
            <= self.info.expires_at + timedelta(days=self.info.grace_days)
        )

    def writable(self, sku: str) -> bool:
        if self.covered(sku):
            return True
        if self.info is not None and sku in self.info.modules:
            # The license knows this sku but is past expiry+grace: read-only. The bootstrap
            # clock never resurrects an expired license.
            return False
        return (
            self.bootstrap_grace_until is not None
            and datetime.now(UTC) <= self.bootstrap_grace_until
        )

    def notice(self, sku: str) -> str | None:
        """UI state for one sku: None (fine) | "grace" | "expired" | "unlicensed"."""
        if self.entitled(sku):
            return None
        if self.covered(sku):
            return "grace"
        if self.info is not None and sku in self.info.modules:
            return "expired"
        return "unlicensed" if self.writable(sku) else "expired"


_cache: tuple[float, LicenseState] | None = None


def invalidate_license_cache() -> None:
    global _cache
    _cache = None


async def _load_state(session: AsyncSession) -> LicenseState:
    row = await session.get(InstanceLicense, 1)
    info: LicenseInfo | None = None
    if row is not None and row.license_text:
        try:
            info = verify_license(row.license_text, settings.license_public_key)
        except LicenseError as exc:
            # A stored license that no longer verifies (rotation, tampering) is no license;
            # loud in the logs, never a crash.
            logger.warning("stored license is invalid: %s", exc)
    bootstrap_until = (
        row.grace_started_at + timedelta(days=settings.license_bootstrap_grace_days)
        if row is not None and row.grace_started_at is not None
        else None
    )
    return LicenseState(info=info, bootstrap_grace_until=bootstrap_until)


async def license_state(session: AsyncSession | None = None) -> LicenseState:
    """The current entitlement state, cached in-process (docs/PERFORMANCE.md)."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    if session is not None:
        state = await _load_state(session)
    else:
        async with async_session_maker() as own:
            state = await _load_state(own)
    _cache = (now, state)
    return state


def licensed_skus() -> dict[str, str]:
    """module name → sku for every registered licensed module, plus the core surfaces."""
    skus = {m.name: m.sku for m in registry.all() if m.sku}
    if settings.mcp_enabled:
        skus[MCP_SKU] = MCP_SKU
    # The AI surface always exists (configured per tenant at runtime), so its sku is always
    # part of the instance's license story.
    skus[AI_SKU] = AI_SKU
    return skus


async def ensure_modules_enableable(requested: list[str], current: list[str]) -> None:
    """Gate for the enable path (org settings, instance admin, setup): newly enabling a
    licensed module requires the sku to be **writable** — covered by a license, or inside a
    grace window. The bootstrap window deliberately counts: a fresh install gets
    ``license_bootstrap_grace_days`` of full function (a built-in trial) before licensed
    modules lock, and the first-run wizard never dead-ends on a box without a key. A module
    that is *already* enabled may always stay enabled; the write gate governs it instead."""
    newly = set(requested) - set(current)
    if not newly:
        return
    state = await license_state()
    blocked = sorted(
        name
        for name in newly
        if (module := registry.get(name)) is not None
        and module.sku is not None
        and not state.writable(module.sku)
    )
    if blocked:
        raise AppError(
            "license_required",
            "errors.license_required",
            status_code=409,
            fields={"enabled_modules": "errors.license_required"},
        )


def license_write_gate(sku: str) -> Depends:  # type: ignore[valid-type]
    """Router-level dependency for licensed modules: mutations require a writable sku.

    Reads never block — past expiry+grace the module is read-only, not gone (epic #140)."""

    async def gate(request: Request) -> None:
        if request.method in _MUTATING_METHODS and not (await license_state()).writable(sku):
            raise AppError("license_expired", "errors.license_expired", status_code=402)

    gate.__name__ = f"license_write_gate_{sku}"
    return Depends(gate)


class LicenseGateASGI:
    """ASGI wrapper for the mounted MCP app: the whole surface requires the ``mcp`` sku.

    MCP is read-first by design (§12), so "read-only" would gate nothing — instead the
    surface answers 402 with the standard error envelope once the sku stops being writable.
    """

    def __init__(self, inner, sku: str) -> None:
        self.inner = inner
        self.sku = sku

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and not (await license_state()).writable(self.sku):
            body = json.dumps(
                {"error": {"code": "license_expired", "message": "errors.license_expired"}}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 402,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.inner(scope, receive, send)
