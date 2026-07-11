"""Public-DNS lookup for a domain's nameservers + DNSSEC status (issue #92).

A plain resolver query — no registrar account — so it works for *every* domain, not just the ones
held at a provider we integrate with. Isolated here (like ``app.core.dnscheck``) so tests stub the
resolver instead of the network. Every lookup times out and fails soft: a failure reads as "no
nameservers" / "DNSSEC unknown" rather than raising, so one unreachable domain never breaks a
batch refresh (the caller records the attempt anyway).
"""

from __future__ import annotations

from dataclasses import dataclass

import dns.asyncresolver
import dns.exception
import dns.resolver

_LOOKUP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class DnsFacts:
    """What one public-DNS refresh learned. ``dnssec`` is tri-state: True/False, or ``None`` when
    the lookup could not answer (timeout / SERVFAIL) — distinct from a definitive "no DNSSEC"."""

    nameservers: list[str]
    dnssec: bool | None


async def fetch_dns(name: str) -> DnsFacts:
    nameservers: list[str] = []
    try:
        answer = await dns.asyncresolver.resolve(name, "NS", lifetime=_LOOKUP_TIMEOUT_SECONDS)
        nameservers = sorted({r.to_text().rstrip(".").lower() for r in answer})
    except dns.exception.DNSException:
        nameservers = []

    dnssec: bool | None
    try:
        answer = await dns.asyncresolver.resolve(name, "DNSKEY", lifetime=_LOOKUP_TIMEOUT_SECONDS)
        dnssec = len(answer) > 0
    except dns.resolver.NoAnswer:
        dnssec = False  # the zone exists and publishes no DNSKEY → not signed
    except dns.exception.DNSException:
        dnssec = None  # timeout / SERVFAIL / NXDOMAIN → we simply don't know

    return DnsFacts(nameservers=nameservers, dnssec=dnssec)
