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
