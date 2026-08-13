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

**On cloud, a tenant's modules follow the tenant's own plan, not the instance key.** The
instance license is a *one-per-installation* artefact, so an org-blind read of it is the right
answer on a self-hosted box — there is one tenant and the agency that runs it holds the key.
On the cloud posture (epic #199) the same read is a category error: the operator runs the
installation and the tenant buys a **plan** (``orgs.plan``), which is exactly what
``UpgradeModal`` already promises when it tells a cloud user an upgrade means "a plan change".
Before this, a plan change altered nothing — ``plan`` reached only the trial-suspension cron —
so an org explicitly marked ``unlimited`` still went read-only across every licensed module the
moment the operator's instance key lapsed or was never installed, reporting a licence expiry to
someone who holds no licence and cannot install one.

So :func:`sku_writable` takes the resolved org's plan and, on cloud, answers from it: ``trial``
until ``trial_ends_at``, ``standard`` and ``unlimited`` always (both are billing-managed — the
provisioning API suspends the *org* when payment stops, and suspension is enforced far earlier,
in ``require_context``). The instance key keeps governing exactly one sku on cloud,
:data:`CLOUD_SKU`, because that one really is the operator's own right to run the posture — and
it is resolved on the console apex, where no org resolves at all. Plans are **lifecycle, not
bundles**: every live plan unlocks every module the org has enabled, which is what ``PLANS``
already says (three bare strings, no module lists). A plan that carried a module set would be a
second, competing answer to "which modules does this org run?" beside ``enabled_modules``.
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
#: The cloud posture (epic #199) is a business-licensed capability: the provisioning surface
#: mounts behind this sku's write gate. Only relevant when SCHAKL_DEPLOYMENT=cloud.
CLOUD_SKU = "cloud"

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CACHE_TTL_SECONDS = 60.0

#: Attribute :func:`license_exempt` stamps on an endpoint. Never read it by string elsewhere.
LICENSE_EXEMPT_MARKER = "__schakl_license_exempt__"


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
        """UI state for one sku: None (fine) | "grace" | "expired" | "unlicensed" | "none".

        ``expired`` is reserved for a licence that really did lapse — one that lists the sku and
        is past expiry+grace. A box that never had a key answers ``unlicensed`` whether or not
        its bootstrap window is still open; the window changes what *works*, never whether a
        licence once existed. Saying "expired" there is a false statement about a document
        nobody ever installed, and it sends the reader off to renew something that does not
        exist. ``none`` is the same fact one step further along: no licence, and the bootstrap
        window has closed too.
        """
        if self.entitled(sku):
            return None
        if self.covered(sku):
            return "grace"
        if self.info is not None and sku in self.info.modules:
            return "expired"
        return "unlicensed" if self.writable(sku) else "none"


#: Cloud plans that entitle a tenant for as long as they hold one. Both are billing-managed:
#: nothing local expires them, and payment trouble arrives as an org *suspension* over the
#: provisioning API — which ``require_context`` refuses far earlier than any licence gate.
#: ``trial`` is the third plan and is the only one with a clock, so it answers from its own.
_EVERGREEN_PLANS = frozenset({"standard", "unlimited"})


@dataclass(frozen=True)
class OrgPlan:
    """A resolved tenant's cloud plan — the entitlement authority on the cloud posture.

    ``plan is None`` means *no org resolves on this hostname*, which is a real answer and not a
    failure: the cloud console lives on the apex (docs/CLOUD.md). Callers fall back to the
    instance licence there, which is the correct reading — the console's own surface is licensed
    by :data:`CLOUD_SKU`, the operator's right to run the posture, not by any tenant's plan.
    """

    plan: str | None
    trial_ends_at: datetime | None

    @classmethod
    def of(cls, org: object) -> OrgPlan:
        """The plan of an already-loaded ``Org``, so a caller holding one pays no query."""
        return cls(
            plan=getattr(org, "plan", None), trial_ends_at=getattr(org, "trial_ends_at", None)
        )

    def live(self) -> bool:
        if self.plan in _EVERGREEN_PLANS:
            return True
        if self.plan == "trial":
            # An unarmed trial (no end date) is running, not lapsed. Locking a freshly
            # provisioned org out of the product it is trialling is the one failure direction
            # this must not have; the cron arms and then enforces the clock.
            return self.trial_ends_at is None or datetime.now(UTC) <= self.trial_ends_at
        return False


_cache: tuple[float, LicenseState] | None = None
#: hostname → (resolved_at, plan). Keyed by **hostname** rather than org id so the whole answer
#: costs one query per host per TTL: resolving the id first and the plan second would put a
#: second round trip on every mutation, and the write gate runs before ``require_context`` has
#: an org to lend it (CLAUDE.md §11 — a gate must not cost a query each).
_plan_cache: dict[str, tuple[float, OrgPlan]] = {}


def invalidate_license_cache() -> None:
    global _cache
    _cache = None


def invalidate_plan_cache() -> None:
    """Drop the memoised plans. Called when a plan is written, so an operator who lifts an org
    to ``unlimited`` sees it take effect on the next request rather than within the TTL."""
    _plan_cache.clear()


async def org_plan_for_host(hostname: str) -> OrgPlan:
    """The plan of the org this hostname serves, memoised for :data:`_CACHE_TTL_SECONDS`."""
    key = (hostname or "").lower()
    now = time.monotonic()
    hit = _plan_cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    from app.core.tenancy import resolve_org

    async with async_session_maker() as session:
        org = await resolve_org(session, key)
    plan = OrgPlan(
        plan=org.plan if org is not None else None,
        trial_ends_at=org.trial_ends_at if org is not None else None,
    )
    _plan_cache[key] = (now, plan)
    return plan


async def sku_writable(sku: str, *, host: str | None = None, plan: OrgPlan | None = None) -> bool:
    """May ``sku`` be written to right now — for this tenant, on this posture?

    Self-hosted: the instance licence, exactly as before. Cloud: the resolved org's own plan,
    because the tenant bought a plan and never a licence key (see the module docstring). Pass
    whichever of ``host`` / ``plan`` you already have; with neither — a cron tick, a call from a
    context that names no tenant — the answer falls back to the instance licence, which is the
    only authority that exists without an org.
    """
    if settings.is_cloud and sku != CLOUD_SKU:
        if plan is None and host is not None:
            plan = await org_plan_for_host(host)
        # A host that names no tenant leaves the instance licence as the only thing to ask.
        if plan is not None and plan.plan is not None:
            return plan.live()
    return (await license_state()).writable(sku)


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
    if settings.is_cloud:
        # The cloud posture itself (epic #199): provisioning mutations ride its write gate.
        skus[CLOUD_SKU] = CLOUD_SKU
    return skus


async def ensure_modules_enableable(
    requested: list[str], current: list[str], *, plan: OrgPlan | None = None
) -> None:
    """Gate for the enable path (org settings, instance admin, setup): newly enabling a
    licensed module requires the sku to be **writable** — covered by a license, or inside a
    grace window. The bootstrap window deliberately counts: a fresh install gets
    ``license_bootstrap_grace_days`` of full function (a built-in trial) before licensed
    modules lock, and the first-run wizard never dead-ends on a box without a key. A module
    that is *already* enabled may always stay enabled; the write gate governs it instead.

    ``plan`` is the org being edited, and callers on cloud must pass it: enabling has to answer
    from the same authority the write gate will, or an org is handed a module it may then not
    write to — a screen that grants something and a 402 that takes it straight back.
    """
    newly = set(requested) - set(current)
    if not newly:
        return
    blocked = []
    for name in sorted(newly):
        module = registry.get(name)
        if module is None or module.sku is None:
            continue
        if not await sku_writable(module.sku, plan=plan):
            blocked.append(name)
    if blocked:
        # Same split as the write gate's 402: on cloud the thing that is missing is a plan, and
        # a tenant told to install a licence key is told to do something they cannot do.
        cloud = settings.is_cloud and plan is not None and plan.plan is not None
        code = "plan_inactive" if cloud else "license_required"
        message = "errors.plan_inactive" if cloud else "errors.license_required"
        raise AppError(
            code, message, status_code=409, fields={"enabled_modules": message}
        )


def license_exempt(reason: str):  # noqa: ANN201 — a decorator, typed by what it wraps
    """Mark one route of a licensed module as exempt from its write gate, and say why.

    A whole-router gate is the right default — it is what makes "licensed" mean one thing per
    module rather than a judgement call per endpoint — but a mutation that *releases* something
    is not a mutation of licensed data, and gating it turns an expired licence into a trap. The
    one case today: ending your own portal impersonation (#296). If that 402'd, a lapsed licence
    would strand whoever was inside a client's session, and the way out is not a thing anyone
    should have to buy.

    Read at request time off ``scope["endpoint"]`` — one ``getattr``, no dependant walk on the
    hot path. Anything the router cannot identify stays gated: this fails closed.
    """

    def mark(endpoint):  # noqa: ANN001, ANN202
        setattr(endpoint, LICENSE_EXEMPT_MARKER, reason)
        return endpoint

    return mark


async def sku_cron_enabled(sku: str) -> bool:
    """Whether a **scheduled** job for ``sku`` should run at all.

    Self-hosted: the instance licence, exactly as the request gate — an expired module stops
    writing in the background too (epic #140: read-only, not gone). Cloud: always, because the
    licence is not the tenant's authority there and this check runs *before* the per-org
    fan-out, with no org to ask about. Which tenants are actually entitled is then decided one
    at a time by ``app.core.jobs.run_per_org``. Answering "no" here on cloud would stop every
    tenant's background work — including orgs on ``unlimited`` — because the *operator* had not
    installed a key, which is the request-path bug this whole change is about, in cron form.
    """
    if settings.is_cloud and sku != CLOUD_SKU:
        return True
    return (await license_state()).writable(sku)


def refusal_for(sku: str) -> tuple[str, str]:
    """The 402's ``(code, message key)``, which is not the same sentence on both postures.

    A cloud tenant holds no licence and could not install one if they wanted to, so "de licentie
    is verlopen" names the wrong artefact *and* the wrong person: what actually ran out is their
    plan, and the fix is a conversation with the operator, not a key. The one exception is
    :data:`CLOUD_SKU` itself, where the reader **is** the operator and a licence really is what
    expired.
    """
    if settings.is_cloud and sku != CLOUD_SKU:
        return "plan_inactive", "errors.plan_inactive"
    return "license_expired", "errors.license_expired"


def license_write_gate(sku: str) -> Depends:  # type: ignore[valid-type]
    """Router-level dependency for licensed modules: mutations require a writable sku.

    Reads never block — past expiry+grace the module is read-only, not gone (epic #140)."""

    async def gate(request: Request) -> None:
        if request.method not in _MUTATING_METHODS:
            return
        if getattr(request.scope.get("endpoint"), LICENSE_EXEMPT_MARKER, None):
            return
        from app.core.tenancy import request_hostname

        if not await sku_writable(sku, host=request_hostname(request)):
            raise AppError(*refusal_for(sku), status_code=402)

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
        from app.core.tenancy import scope_hostname

        if scope["type"] == "http" and not await sku_writable(
            self.sku, host=scope_hostname(scope)
        ):
            code, message = refusal_for(self.sku)
            body = json.dumps({"error": {"code": code, "message": message}}).encode()
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
