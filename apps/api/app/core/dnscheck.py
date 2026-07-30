"""DNS TXT lookup for custom-domain verification (issue #26).

Isolated in its own module so tests can stub the resolver instead of the network. Lookup
failures of any kind read as "no records": verification then simply fails closed and the
operator retries once DNS has propagated.
"""

from __future__ import annotations

import dns.asyncresolver
import dns.exception

_LOOKUP_TIMEOUT_SECONDS = 5.0


async def txt_records(name: str) -> list[str]:
    try:
        answer = await dns.asyncresolver.resolve(
            name, "TXT", lifetime=_LOOKUP_TIMEOUT_SECONDS
        )
    except dns.exception.DNSException:
        return []
    return ["".join(part.decode() for part in record.strings) for record in answer]


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
