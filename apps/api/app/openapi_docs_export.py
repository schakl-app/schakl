"""Dump the OpenAPI spec *plus* the permission every route declares, for the public manual.

    uv run python -m app.openapi_docs_export > api-doc.json

Why this is not ``app.openapi_export``: that one feeds ``scripts/gen-client.sh``, whose output
(``apps/web/src/lib/core/api/schema.d.ts``) is drift-checked in CI. Adding a field to the
document it prints would be a schema change to defend for a reason that has nothing to do with
the typed client. So the reference generator gets its own exporter, and the spec half it prints
is byte-identical to the other one's.

The interesting half is ``permissions``. The permission a route declares is the single most
useful thing to know about an endpoint before calling it — it is what the caller must hold and
what an API key must be minted with — and it appears nowhere in the OpenAPI document, because it
is a FastAPI dependency rather than a schema. It is recoverable only by introspecting the route
tree (``app.core.permissions.deps``), which is what the deny-by-default tests already do.

**Joining the two halves is the fiddly part**, and it now lives in
``app.core.permissions.deps.operation_index`` rather than here: the authentication sweep
(``tests/test_anonymous_denied.py``) needs the same route-to-URL join, and a second copy of it
is a copy that drops routes quietly. That function's docstring holds the reasoning — the short
version is that ``route.unique_id`` looks like the join key and is not, which is the same class
of mistake ``docs/MCP.md`` records about predicting tool names instead of reading them off the
built server. What stays here is the refusal to print a document where any operation came out
unclaimed: a reference that quietly drops the permission off a tenth of the endpoints is worse
than one that will not build, because only the second gets noticed.
"""

from __future__ import annotations

import json
import sys

from app.core.permissions.deps import operation_index, route_markers
from app.main import app


def main() -> None:
    spec = app.openapi()
    operations, unclaimed = operation_index(app)

    if unclaimed:
        # Loud rather than silent: a reference that quietly drops the permission off a tenth of
        # the endpoints is worse than one that refuses to build, because only the second gets
        # noticed. If this ever fires, the join needs widening, not the check removing.
        print(
            f"openapi_docs_export: {len(unclaimed)} operations matched no route, e.g. "
            f"{unclaimed[:5]}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    permissions: dict[str, list[list[str | None]]] = {}
    exemptions: dict[str, list[str]] = {}
    for operation in operations:
        declared, reasons = route_markers(operation.route)
        if declared:
            permissions[operation.operation_id] = [[key, scope] for key, scope in declared]
        if reasons:
            exemptions[operation.operation_id] = list(reasons)

    print(
        json.dumps(
            {"spec": spec, "permissions": permissions, "exemptions": exemptions},
            indent=1,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
