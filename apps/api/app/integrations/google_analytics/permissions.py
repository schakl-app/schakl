"""Permissions the google_analytics integration introduces (CLAUDE.md §15).

Business-licensed — see LICENSE.

**Two keys, and the split is the same one ``google_ads`` draws between its curated reads and its
GAQL passthrough.** Everything this integration does is a read, so a single key is the obvious
design; it is wrong for the reason the surface exists. It is reached over MCP by an agent
holding an API key, and a key carries permission *scopes* — so the question an agency actually
has to answer is not "may this agent read Analytics" but "may this agent ask Analytics
**anything**". The curated shapes (properties, streams, key events, an overview, a breakdown,
real time) are questions somebody here designed and can predict the cost of. ``report.run`` is
the escape hatch: any dimension crossed with any metric, over any window the property holds,
which is the point of it and also the reason it is a separate decision.

Neither is ever ``client`` (#266). Before granting either to the seeded client role, list what
they reach: a property's raw event stream, its custom dimensions — which are whatever the agency
configured, frequently including internal identifiers — and every other client's property that
the same Google account happens to reach, because a GA4 grant is not narrowed by a company
horizon and there is no row here for one to narrow. This surface is the *agency's* Google
account, not the client's data in our database, and the two are not interchangeable.
"""

from app.core.permissions import PermissionSpec

GOOGLE_ANALYTICS_PERMISSIONS: list[PermissionSpec] = [
    # Everything curated: the property list, its configuration, its metadata document, the
    # overview, breakdowns, the daily series and real time. Granted to `member` for
    # `google_ads.account.read`'s reason — an account manager who cannot see a client's traffic
    # cannot do the job the client is paying for.
    PermissionSpec(
        "google_analytics.property.read", position=10, default_roles=("admin", "member")
    ),
    # The free-form report, the pivot and the compatibility check. Its own key because it is the
    # one read whose shape nobody reviewed: an agent may ask a question this module never
    # anticipated, which is exactly what it is for, and also why an agency should decide who
    # holds it. Admin-only by default.
    PermissionSpec("google_analytics.report.run", position=20),
]
