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

**Joining the two halves is the fiddly part, and it is worth stating why.** ``route.unique_id``
looks like the join key and is not: on this app ``include_router`` is lazy, so a leaf's ``path``
is *relative* (``/login``, not ``/api/v1/auth/login``) and its ``unique_id`` is built from that
relative path, while the document's ``operationId`` is built from the full one. Keying on it
silently matches the wrong routes — the same class of mistake ``docs/MCP.md`` records about
predicting tool names instead of reading them off the built server. So the join is
``(function name, method)`` plus the requirement that the document's path *ends with* the leaf's
own path, and ``_check`` refuses to print a document where any operation came out unclaimed.
"""

from __future__ import annotations

import json
import re
import sys

from app.core.permissions.deps import iter_route_leaves, route_markers
from app.main import app

_METHODS = ("get", "post", "put", "patch", "delete")


def _slug(name: str) -> str:
    """FastAPI sanitises the whole operationId, so a route named ``auth:cookie.login`` appears
    as ``auth_cookie_login…``. Comparing the raw name misses every fastapi-users route."""
    return re.sub(r"\W", "_", name)


def main() -> None:
    spec = app.openapi()

    # operationId -> (method, path), for every operation in the document.
    operations: dict[str, tuple[str, str]] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method in _METHODS:
                operations[op["operationId"]] = (method, path)

    permissions: dict[str, list[list[str | None]]] = {}
    exemptions: dict[str, list[str]] = {}
    claimed: set[str] = set()

    for route in iter_route_leaves(app.routes):
        if not route.include_in_schema:
            continue
        declared, reasons = route_markers(route)
        for method in sorted(m.lower() for m in (route.methods or ())):
            if method not in _METHODS:
                continue
            # Two conditions, and **both** are needed; each alone is silently wrong.
            #
            # FastAPI's own formula (``generate_operation_id_for_path``) is route name + full
            # path, sanitised together, plus the method. Testing it against each candidate's own
            # path is self-fulfilling: all four routers with a `create_account` handler satisfy
            # it for their own path, so it "matches" four operations and the route is dropped.
            #
            # The leaf's relative path is the missing half. `/cloudflare/accounts` is a suffix of
            # exactly one of those four full paths, which settles it.
            candidates = [
                (len(path), op_id)
                for op_id, (m, path) in operations.items()
                if m == method
                and path.endswith(route.path)
                and op_id == f"{_slug(route.name + path)}_{method}"
            ]
            # Still ambiguous where the leaf path is itself a suffix of a sibling's — `/prefs`
            # under `/nav/prefs` and `/dashboard/prefs`, all three named `get_prefs`. The
            # shortest full path is the one whose own router added no further prefix.
            if len(candidates) > 1:
                shortest = min(length for length, _ in candidates)
                candidates = [c for c in candidates if c[0] == shortest]
            if len(candidates) != 1:
                continue
            op_id = candidates[0][1]
            claimed.add(op_id)
            if declared:
                permissions[op_id] = [[key, scope] for key, scope in declared]
            if reasons:
                exemptions[op_id] = list(reasons)

    unclaimed = sorted(set(operations) - claimed)
    if unclaimed:
        # Loud rather than silent: a reference that quietly drops the permission off a tenth of
        # the endpoints is worse than one that refuses to build, because only the second gets
        # noticed. If this ever fires, the join above needs widening, not the check removing.
        print(
            f"openapi_docs_export: {len(unclaimed)} operations matched no route, e.g. "
            f"{unclaimed[:5]}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(
        json.dumps(
            {"spec": spec, "permissions": permissions, "exemptions": exemptions},
            indent=1,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
