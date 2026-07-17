"""Shared SSRF address classification for tenant-supplied outbound targets (audit F5/F6/F8/F9/F24).

One place decides whether a resolved address is safe to connect to, so the webhook, notification,
and SMTP guards cannot drift. The notification guard used to hand-roll a deny-list
(``is_private or is_loopback or is_link_local or is_reserved``) that missed ranges ``is_global``
catches — ``0.0.0.0`` (routes to localhost on Linux), CGNAT ``100.64.0.0/10``, and IPv4-mapped
IPv6 literals like ``::ffff:127.0.0.1`` whose ``.is_loopback`` is ``False`` on Python < 3.13.
``is_public_address`` unwraps the mapped form first, so those can no longer slip past.

Not every outbound path wants this: an OIDC IdP or an OpenAI-compatible LLM endpoint may
*legitimately* live on the LAN for a self-hosted install (see ``auth/sso.py`` / ``ai/providers``),
so those paths deliberately do not call the private-address block — they close the redirect-pivot
instead. The paths that expect a public target (webhooks, generic notification URLs, SMTP relays)
use the check here, still overridable via ``SCHAKL_ALLOW_PRIVATE_NOTIFICATION_TARGETS``.
"""

from __future__ import annotations

import ipaddress
import socket

from app.config import settings


class SsrfBlocked(Exception):
    """A target resolved to a non-public address (or would not resolve / is malformed)."""


def is_public_address(addr: str) -> bool:
    """True only for a globally-routable address. IPv4-mapped IPv6 is unwrapped first so
    ``::ffff:169.254.169.254`` is classified as the link-local IPv4 it actually reaches."""
    ip = ipaddress.ip_address(addr)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global


def _resolve(host: str) -> list[str]:
    """Every address a host resolves to; a literal IP skips DNS."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def assert_host_public_sync(host: str, *, allow_private: bool | None = None) -> None:
    """Raise :class:`SsrfBlocked` if ``host`` resolves to any non-public address.

    ``allow_private`` defaults to the instance's ``allow_private_notification_targets`` so a
    trusted-LAN deployment can opt every guarded path into private targets at once.
    """
    allow = settings.allow_private_notification_targets if allow_private is None else allow_private
    if allow:
        return
    if not host:
        raise SsrfBlocked("missing host")
    try:
        addrs = _resolve(host)
    except OSError as exc:
        raise SsrfBlocked(f"{host} does not resolve") from exc
    if not addrs or any(not is_public_address(a) for a in addrs):
        raise SsrfBlocked(f"{host} resolves to a non-public address")
