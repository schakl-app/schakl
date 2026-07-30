"""DNS lookups for custom-domain onboarding (issues #26, #292).

Isolated in its own module so tests can stub the resolver instead of the network. Unlike the
original TXT-only helper, lookups return a :class:`DnsResult` that keeps the *reason* a name
did not answer — the wizard's whole point is telling the customer whether a record is missing,
wrong, still propagating, or whether their resolver is broken (SERVFAIL). Collapsing every
failure into "no records" is exactly the behaviour #292 replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import dns.asyncresolver
import dns.exception
import dns.resolver

_LOOKUP_TIMEOUT_SECONDS = 5.0

#: Machine-readable reasons a lookup produced no usable answer. ``None`` means the query
#: itself succeeded (the name exists), even when no record of the asked type is present.
NXDOMAIN = "nxdomain"
SERVFAIL = "servfail"
TIMEOUT = "timeout"


@dataclass(frozen=True)
class DnsResult:
    """One lookup's outcome: the values seen, or why nothing could be seen."""

    values: list[str] = field(default_factory=list)
    #: ``None`` on a successful query (possibly with zero records of the type);
    #: :data:`NXDOMAIN` / :data:`SERVFAIL` / :data:`TIMEOUT` otherwise.
    error: str | None = None


async def _resolve(name: str, rtype: str) -> DnsResult:
    try:
        answer = await dns.asyncresolver.resolve(name, rtype, lifetime=_LOOKUP_TIMEOUT_SECONDS)
    except dns.resolver.NXDOMAIN:
        return DnsResult(error=NXDOMAIN)
    except dns.resolver.NoAnswer:
        return DnsResult()
    except (dns.resolver.LifetimeTimeout, dns.exception.Timeout):
        return DnsResult(error=TIMEOUT)
    except dns.exception.DNSException:
        # NoNameservers (SERVFAIL from every resolver) and anything else unexpected: the
        # zone answered brokenly rather than "record absent".
        return DnsResult(error=SERVFAIL)
    if rtype == "TXT":
        values = ["".join(part.decode() for part in record.strings) for record in answer]
    else:
        values = [str(record).rstrip(".").lower() for record in answer]
    return DnsResult(values=values)


async def txt(name: str) -> DnsResult:
    return await _resolve(name, "TXT")


async def cname(name: str) -> DnsResult:
    return await _resolve(name, "CNAME")


async def a_records(name: str) -> DnsResult:
    return await _resolve(name, "A")


async def ns_zone(domain: str) -> tuple[str | None, list[str]]:
    """The nearest ancestor (including ``domain`` itself) that has its own NS records.

    Returns ``(zone, nameservers)`` — the zone apex the customer actually edits records in,
    which is what provider detection and "relative host" guidance key off. ``(None, [])``
    when nothing up the tree answers (broken delegation, or DNS unreachable). Detection is
    advisory only: it tailors instructions and is never treated as authorization to touch
    anyone's DNS (#292).
    """
    labels = domain.split(".")
    # Walk from the full name upward; stop before the bare TLD.
    for start in range(len(labels) - 1):
        candidate = ".".join(labels[start:])
        result = await _resolve(candidate, "NS")
        if result.values:
            return candidate, result.values
        if result.error in (SERVFAIL, TIMEOUT):
            return None, []
    return None, []


async def points_at(host: str, target: str) -> bool | None:
    """Best-effort: does ``host`` still route toward ``target`` (#291)?

    Tri-state on purpose. ``True``/``False`` are real answers; ``None`` means the lookup
    itself failed (resolver down, timeout), which must never be recorded as "the customer
    moved their DNS away".

    Two signals, either suffices: the CNAME chains of host and target converge on the same
    canonical name (the ordinary ``CNAME → edge.<base_domain>`` setup), or their A records
    overlap (covers apex ALIAS/CNAME-flattening, where no CNAME survives to compare). A
    definitive NXDOMAIN/no-answer on the host is a real ``False`` — that domain routes
    nothing, let alone to us.
    """
    import dns.resolver

    try:
        host_answer = await dns.asyncresolver.resolve(
            host, "A", lifetime=_LOOKUP_TIMEOUT_SECONDS
        )
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False
    except dns.exception.DNSException:
        return None
    try:
        target_answer = await dns.asyncresolver.resolve(
            target, "A", lifetime=_LOOKUP_TIMEOUT_SECONDS
        )
    except dns.exception.DNSException:
        # Can't establish what the target resolves to — no verdict about the host.
        return None
    if host_answer.canonical_name == target_answer.canonical_name:
        return True
    host_ips = {record.address for record in host_answer}
    target_ips = {record.address for record in target_answer}
    return bool(host_ips & target_ips)
