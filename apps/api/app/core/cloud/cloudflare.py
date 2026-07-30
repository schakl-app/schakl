"""Cloudflare for SaaS API client (epic #199). Business-licensed — see this directory's LICENSE.

When the operator fronts the cloud instance with **Cloudflare for SaaS**, a verified customer
domain stops being a Traefik router with a Let's Encrypt certificate
(:mod:`app.core.cloud.ingress`) and becomes a **custom hostname** on the operator's zone:
Cloudflare issues and renews the edge certificate, and forwards to the fallback origin. This
module is the seam that keeps Cloudflare in step with ``orgs.custom_domain``.

Design rules:

* **Instance-level and server-side only.** The token comes from the environment
  (``SCHAKL_CLOUD_CF_API_TOKEN``, or ``*_FILE`` for a Docker secret). It is never stored in
  the database, never returned by an endpoint, never in the OpenAPI spec, and never reaches
  the web app — so no browser ever holds a credential that can edit the operator's zone.
* **Least privilege.** Everything here needs exactly two zone-scoped scopes, on one zone:
  *SSL and Certificates → Edit* (custom hostnames) and *DNS → Edit* (the per-org subdomain,
  used from the provisioning path). Nothing account-level; never a Global API Key.
* **Off unless configured.** :func:`cloudflare_configured` gates every call, so a self-host
  box and the test suite never touch the network.
* **The custom origin is what saves Full (strict); the SNI rewrite is not.** Cloudflare opens
  a second TLS connection to the origin and presents the **custom origin server's** name as
  SNI by default — which is the operator's own edge hostname, exactly what the wildcard origin
  certificate covers. So ``custom_origin_server`` is all this flow needs, and the HTTP ``Host``
  header stays the customer's domain either way, so tenant resolution is unaffected
  (CLAUDE.md §7). ``custom_origin_sni`` is the *rewrite* on top of that default, it is an
  **Enterprise-only entitlement**, and sending it on a Free/Pro/Business zone fails the whole
  create with *"Access to setting a custom origin SNI has not been granted"* (#293). It is
  therefore sent only when an operator explicitly configures
  ``SCHAKL_CLOUD_CF_ORIGIN_SNI`` — never derived.
* **The token never reaches a log line or an exception message.** It lives only in the
  request headers, and nothing here formats headers into an error.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.core.cloud.ingress import cname_target

logger = logging.getLogger(__name__)

_API_BASE = "https://api.cloudflare.com/client/v4"
#: Fail fast rather than hold a request open: a domain verification waits on this call.
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
#: One retry, for transient conditions only (429 / 5xx). Cloudflare is not idempotent on
#: create, so the retry sits below a find-then-create caller that tolerates a duplicate.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Test seam — an ``httpx`` transport used instead of the network. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


class CloudflareError(RuntimeError):
    """A Cloudflare call failed. The message carries Cloudflare's own error text (which
    never contains the token) plus the status; callers translate it to an error envelope."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class CloudflareNotEntitledError(CloudflareError):
    """Cloudflare refused the call over a **permission or plan entitlement**, not a hiccup.

    Worth its own class because the response to it is the opposite of every other failure:
    retrying can never succeed, and nobody in the tenant can fix it. Only the operator can —
    by widening the API token's scopes or by removing a setting their zone's plan does not
    include (#293) — so the message carries a diagnostic aimed at them and the caller maps it
    to its own error code instead of the generic "try again in a moment".
    """


#: Fragments of Cloudflare's own error text that mean "your token or your plan, not our edge".
#: Deliberately narrow — a wrong guess here would report a transient failure as a permanent
#: one and tell the tenant not to retry something a retry would have fixed.
_NOT_ENTITLED_MARKERS = (
    "has not been granted",
    "not entitled",
    "not authorized",
    "unauthorized",
    "insufficient permission",
    "requires enterprise",
    "enterprise plan",
    "not available on your plan",
)


def cloudflare_configured() -> bool:
    """Whether this instance can talk to Cloudflare. Mirrors ``storage.s3.s3_configured``."""
    return bool(
        settings.is_cloud and settings.cloud_cf_api_token and settings.cloud_cf_zone_id
    )


def origin_sni() -> str | None:
    """The explicit SNI **rewrite** for a custom hostname, or None when there is none.

    Never derived. Cloudflare already presents the custom origin server's name as SNI, which is
    the value this instance would have derived anyway; asking for it explicitly is an
    Enterprise-only entitlement that fails the create outright on every other plan (#293). So
    an unset ``SCHAKL_CLOUD_CF_ORIGIN_SNI`` means *omit the field*, not *compute one*.
    """
    return settings.cloud_cf_origin_sni or None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_API_BASE,
        timeout=_TIMEOUT,
        headers={
            "Authorization": f"Bearer {settings.cloud_cf_api_token}",
            "Content-Type": "application/json",
        },
        transport=_transport,
    )


def _fail(response: httpx.Response) -> CloudflareError:
    """Cloudflare's error envelope, flattened. Reads the body defensively — an edge error page
    is HTML, not the documented JSON."""
    detail = ""
    try:
        body = response.json()
        detail = "; ".join(
            str(err.get("message", err)) for err in (body.get("errors") or []) if err
        )
    except (ValueError, AttributeError):
        detail = ""
    message = detail or f"Cloudflare returned HTTP {response.status_code}"
    lowered = detail.lower()
    if response.status_code in (401, 403) or any(
        marker in lowered for marker in _NOT_ENTITLED_MARKERS
    ):
        return CloudflareNotEntitledError(message, status=response.status_code)
    return CloudflareError(message, status=response.status_code)


async def _request(
    method: str, path: str, *, json: dict[str, Any] | None = None, params: dict | None = None
) -> Any:
    """One authenticated call, returning Cloudflare's ``result``. Raises CloudflareError."""
    if not cloudflare_configured():  # pragma: no cover — callers gate first
        raise CloudflareError("Cloudflare is not configured on this instance")
    async with _client() as client:
        for attempt in (1, 2):
            try:
                response = await client.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                # str(exc) is a transport message ("connect timeout"), never the headers.
                if attempt == 1:
                    continue
                raise CloudflareError(f"Cloudflare unreachable: {exc}") from exc
            if response.status_code in _RETRY_STATUSES and attempt == 1:
                continue
            if response.status_code >= 400:
                raise _fail(response)
            body = response.json()
            if not body.get("success", False):
                raise _fail(response)
            return body.get("result")
    raise CloudflareError("Cloudflare unreachable")  # pragma: no cover — loop always returns


def _zone_path(suffix: str) -> str:
    return f"/zones/{settings.cloud_cf_zone_id}{suffix}"


# --------------------------------------------------------------------------- #
# Custom hostnames (Zone → SSL and Certificates → Edit)
# --------------------------------------------------------------------------- #
async def find_custom_hostname(hostname: str) -> dict[str, Any] | None:
    """The existing custom hostname for this name, or None.

    Cloudflare's list filter is a *substring* match, so the exact name is re-checked here —
    otherwise ``klant.nl`` would match ``mijn.klant.nl`` and we would adopt the wrong record.
    """
    result = await _request(
        "GET", _zone_path("/custom_hostnames"), params={"hostname": hostname, "per_page": 50}
    )
    for row in result or []:
        if row.get("hostname") == hostname:
            return row
    return None


async def create_custom_hostname(hostname: str) -> dict[str, Any]:
    """Register a custom hostname routed at the operator's own origin.

    Validation is ``http``: by the time schakl verifies a domain the customer has already
    pointed their CNAME at us (that is what makes the domain reach this instance at all), so
    Cloudflare can answer its own challenge at the edge. ``txt`` would make the customer add
    a second DNS record for no benefit.

    ``custom_origin_server`` is the CNAME target and is never taken from the SNI setting — an
    entitled operator rewriting SNI to some other name must not silently re-route the origin
    with it (#293).
    """
    payload: dict[str, Any] = {
        "hostname": hostname,
        "ssl": {
            "method": "http",
            "type": "dv",
            "settings": {"min_tls_version": "1.2"},
        },
        "custom_origin_server": cname_target(),
    }
    sni = origin_sni()
    if sni:
        payload["custom_origin_sni"] = sni
    try:
        return await _request("POST", _zone_path("/custom_hostnames"), json=payload)
    except CloudflareNotEntitledError as exc:
        if sni and "sni" in str(exc).lower():
            origin = payload["custom_origin_server"]
            raise CloudflareNotEntitledError(
                f"{exc} — SCHAKL_CLOUD_CF_ORIGIN_SNI is set to {sni!r}, but 'SNI Rewrite for "
                "Custom Origin' is an Enterprise-only entitlement. Leave the setting empty: "
                f"Cloudflare then presents the custom origin server ({origin}) as SNI on its "
                "own, which is the value this instance wants anyway.",
                status=exc.status,
            ) from exc
        raise


async def ensure_custom_hostname_record(hostname: str) -> dict[str, Any]:
    """Idempotent create → the full Cloudflare custom-hostname record.

    Adopts an existing record rather than duplicating it, so a retried verification (or a
    hostname registered by hand during the manual era) converges instead of erroring. The
    full record (not just the id) comes back so the caller can seed the domain-health state
    (#291) without a second round-trip.
    """
    existing = await find_custom_hostname(hostname)
    if existing and existing.get("id"):
        return existing
    created = await create_custom_hostname(hostname)
    if not (created or {}).get("id"):
        raise CloudflareError("Cloudflare accepted the custom hostname but returned no id")
    return created


async def ensure_custom_hostname(hostname: str) -> str:
    """Idempotent create → the Cloudflare custom-hostname id."""
    return str((await ensure_custom_hostname_record(hostname))["id"])


async def get_custom_hostname(hostname_id: str) -> dict[str, Any] | None:
    """The current custom-hostname record — status, SSL state, verification errors — or
    None when it no longer exists (deleted by hand, or moved to another zone)."""
    try:
        return await _request("GET", _zone_path(f"/custom_hostnames/{hostname_id}"))
    except CloudflareError as exc:
        if exc.status == 404:
            return None
        raise


async def delete_custom_hostname(hostname_id: str) -> None:
    """Remove a custom hostname and its issued certificates. A 404 is success — the record is
    already gone, which is the state the caller wanted."""
    try:
        await _request("DELETE", _zone_path(f"/custom_hostnames/{hostname_id}"))
    except CloudflareError as exc:
        if exc.status == 404:
            return
        raise


# --------------------------------------------------------------------------- #
# DNS records (Zone → DNS → Edit) — the per-org <slug>.<base_domain> subdomain
# --------------------------------------------------------------------------- #
def subdomain_for(slug: str) -> str:
    return f"{slug}.{settings.base_domain}"


async def find_dns_record(name: str) -> dict[str, Any] | None:
    """The existing DNS record for this exact name, or None.

    A zone that routes ``*.<base_domain>`` by wildcard does **not** answer this: the wildcard
    is stored under the literal name ``*.<base_domain>``, so an exact-name query never matches
    it. That is what makes the collision check mean "somebody explicitly took this name"
    rather than "the zone has a catch-all".
    """
    result = await _request(
        "GET", _zone_path("/dns_records"), params={"name": name, "per_page": 50}
    )
    for row in result or []:
        if row.get("name") == name:
            return row
    return None


async def create_subdomain_record(slug: str) -> str:
    """Point ``<slug>.<base_domain>`` at the edge, proxied, and return the record id.

    A CNAME to the CNAME target rather than an A record to the server: the target already
    exists (it is the Cloudflare for SaaS fallback origin), so a server migration re-points one
    record instead of every org's. Proxied is required — an unproxied record would expose the
    origin IP and bypass the edge entirely.
    """
    result = await _request(
        "POST",
        _zone_path("/dns_records"),
        json={
            "type": "CNAME",
            "name": subdomain_for(slug),
            "content": cname_target(),
            "proxied": True,
            "comment": f"schakl org: {slug}",
        },
    )
    record_id = (result or {}).get("id")
    if not record_id:
        raise CloudflareError("Cloudflare accepted the DNS record but returned no id")
    return str(record_id)


async def delete_dns_record(record_id: str) -> None:
    """Remove a subdomain record. A 404 is success — already gone is the wanted state."""
    try:
        await _request("DELETE", _zone_path(f"/dns_records/{record_id}"))
    except CloudflareError as exc:
        if exc.status == 404:
            return
        raise
