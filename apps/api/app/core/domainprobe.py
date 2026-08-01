"""End-to-end proof that a custom domain routes to this instance (#291 follow-up).

A DNS comparison cannot see through a proxy. When a customer's zone fronts their domain with
Cloudflare (the supported orange-to-orange setup for Cloudflare for SaaS), the public answer
for their domain is **A records on Cloudflare's anycast range**, not the CNAME they entered —
and those addresses belong to *their* zone, so they never match the edge hostname's. The old
check read that as "the customer moved their DNS away", demoted a perfectly healthy domain and
mailed its managers about an outage that was not happening.

So routing is proven the way it is actually experienced: **fetch the domain and see whether
this instance answers for this org.** ``GET https://<domain>/api/v1/meta/domain-probe?nonce=…``
echoes the nonce and the resolved org slug (:mod:`app.core.meta`), which no cache, parking page
or other provider can forge — and which is true through any number of proxies in front.

Three verdicts, and the asymmetry between them is the point:

- :data:`OURS` — positive proof. Overrides whatever the addresses said.
- :data:`OTHER` — positive proof of the opposite: something that is not us served this
  hostname. Only a 200 with a foreign body, or a 404 from a server that is not this API.
- :data:`UNKNOWN` — no verdict, and therefore no consequence. A WAF challenge, a redirect, a
  TLS error, a 5xx, our own ``unknown_host`` envelope (which is also what a domain still
  mid-wizard answers) all land here. A domain is never demoted on the absence of an answer.

Not a security boundary: ownership is proven by the TXT challenge in :mod:`app.core.domainflow`
long before anything is probed. This only answers "is it serving?".
"""

from __future__ import annotations

import ipaddress
import json
import logging
import secrets
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: The path the probe fetches; served unauthenticated by ``app.core.meta``.
PROBE_PATH = "/api/v1/meta/domain-probe"

#: The constant marker in the probe response body. Identifies the software, never the tenant.
INSTANCE_MARKER = "schakl"

OURS = "ours"
OTHER = "other"
UNKNOWN = "unknown"

#: Short by design: a customer clicking "check" waits on this, and the sweep runs it per org.
_TIMEOUT = httpx.Timeout(connect=4.0, read=6.0, write=4.0, pool=4.0)
#: Our answer is a few hundred bytes; anything larger is somebody else's page, and we stop
#: reading rather than buffer a stranger's response.
_MAX_BODY = 16 * 1024
_USER_AGENT = "schakl-domain-probe/1"

#: Statuses that mean "something answered, but not with an answer": an edge challenge, an
#: authenticated proxy, a rate limit, an origin error. Never evidence of a move.
_INCONCLUSIVE_STATUSES = frozenset({401, 402, 403, 407, 408, 429, 451})

#: Test seam — an ``httpx`` transport used instead of the network, as in the Cloudflare
#: client. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


def _routable(addresses: list[str] | None) -> bool:
    """Whether it is safe (and useful) to fetch this name.

    A custom domain is customer-controlled, so its A records are too: refuse to have the API
    or the worker fetch a loopback, link-local or private address on their say-so. ``None``
    means the caller has not resolved the name — fetch anyway, the DNS layer has already
    established that the name answers publicly. An empty list means it resolves to nothing,
    so there is nothing to fetch either.
    """
    if addresses is None:
        return True
    public = False
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.is_global and not address.is_multicast:
            public = True
        else:
            return False
    return public


def _is_our_envelope(payload: Any) -> bool:
    """Whether a body is this API's own error envelope (CLAUDE.md §9)."""
    error = payload.get("error") if isinstance(payload, dict) else None
    return isinstance(error, dict) and "code" in error


def _inconclusive(response: httpx.Response) -> bool:
    """Whether the status alone says "something answered, but not with an answer".

    Asked **before** the body is read, and that ordering is load-bearing: an edge challenge
    page can easily exceed the body cap, and the cap must never be what turns a WAF into a
    verdict that the customer moved their domain.
    """
    if response.status_code in _INCONCLUSIVE_STATUSES or response.status_code >= 500:
        return True
    # The hostname does not serve the app itself; where it points is not ours to follow.
    return response.is_redirect


def _classify(response: httpx.Response, body: bytes, nonce: str, expected_slug: str) -> str:
    if _inconclusive(response):
        return UNKNOWN
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        payload = None
    if response.status_code == 200:
        if isinstance(payload, dict) and payload.get("instance") == INSTANCE_MARKER:
            if payload.get("nonce") != nonce:
                # A cached or replayed answer proves nothing about right now.
                return UNKNOWN
            return OURS if payload.get("org") == expected_slug else OTHER
        return OTHER
    if response.status_code == 404 and _is_our_envelope(payload):
        # This API answered that it does not know the hostname — which is also what a domain
        # still in the wizard answers, since it does not resolve until it is verified.
        return UNKNOWN
    return OTHER if response.status_code == 404 else UNKNOWN


async def probe(domain: str, expected_slug: str, *, addresses: list[str] | None = None) -> str:
    """Fetch ``domain`` and report whether this instance serves ``expected_slug`` there.

    Never raises: every transport failure is :data:`UNKNOWN`. Redirects are not followed —
    a hostname that redirects elsewhere is not serving the app, and following would let a
    redirect to our own slug host read as proof about the wrong hostname.
    """
    if not _routable(addresses):
        return UNKNOWN
    nonce = secrets.token_hex(16)
    url = f"https://{domain}{PROBE_PATH}?nonce={nonce}"
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=False, transport=_transport
        ) as client:
            async with client.stream(
                "GET", url, headers={"user-agent": _USER_AGENT, "accept": "application/json"}
            ) as response:
                if _inconclusive(response):
                    return UNKNOWN
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_BODY:
                        return OTHER  # a page this size is somebody else's, not our answer
                return _classify(response, body, nonce, expected_slug)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.debug("domain probe for %s could not complete: %s", domain, exc)
        return UNKNOWN
