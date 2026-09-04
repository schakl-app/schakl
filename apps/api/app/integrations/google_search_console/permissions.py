"""Permissions the google_search_console integration introduces (CLAUDE.md §15).

Business-licensed — see LICENSE.

**Two keys, split the way ``google_analytics`` splits its curated reads from the free-form
report.** Everything here is a read, so one key is the obvious design and it is wrong for the
reason the surface exists: it is reached over MCP by an agent holding an API key, and a key
carries permission *scopes*. The curated shapes — the site list, sitemaps, an overview, a
breakdown by query or page, the movers, a URL inspection — are questions somebody here designed
and can predict the cost of. ``report.run`` is the escape hatch: any dimensions, any filter, any
aggregation, any data state, over any window the property holds, which is the point of it and
also the reason it is a separate decision.

Neither is ever ``client`` (#266). A Search Console grant is the *agency's* Google account and is
narrowed by no company horizon: the same connection reaches every client's property, and a
query-level table of what people searched for is not something a portal login should be able to
pull for a site that is not theirs. The client sees Search Console through the marketing
dashboard, which is horizon-scoped, and nothing else.
"""

from app.core.permissions import PermissionSpec

GOOGLE_SEARCH_CONSOLE_PERMISSIONS: list[PermissionSpec] = [
    # Everything curated: sites, sitemaps, the overview, breakdowns, the daily and hourly
    # series, the movers, the URL inspection and the AI-visibility answer. Granted to `member`
    # for `google_analytics.property.read`'s reason — an account manager who cannot see a
    # client's search performance cannot do the job the client is paying for.
    PermissionSpec(
        "google_search_console.site.read", position=10, default_roles=("admin", "member")
    ),
    # The free-form query. Its own key because it is the one read whose shape nobody
    # reviewed: an agent may ask a question this module never anticipated, which is exactly
    # what it is for, and also why an agency should decide who holds it. Admin-only by default.
    PermissionSpec("google_search_console.report.run", position=20),
]
