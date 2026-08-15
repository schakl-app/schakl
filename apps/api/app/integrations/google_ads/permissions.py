"""Permissions the google_ads module introduces (CLAUDE.md §15). Business-licensed — see LICENSE.

**Eight keys, and the write half is split four ways on purpose.** The obvious design is one
``google_ads.write``, and it is wrong for the reason this module exists: the surface is reached
over MCP by an agent holding an API key, and a key carries permission *scopes*. One write key
means the only key you can mint is one that may do everything — so "let the assistant clean up
search terms overnight" and "let the assistant change budgets" become the same grant. Split, an
agency can hand an agent ``google_ads.negative.write`` and nothing else, and the deny-by-default
route permissions answer every other call.

``budget.write`` is separate from ``campaign.write`` for the same reason one step further in:
pausing a campaign is reversible in a click, and a daily budget with an extra zero is money that
has already gone. They are different risks and therefore different decisions.

All admin-only by default, and **never ``client``** (#266). Before granting any of these to the
seeded client role, list every route the key gates: ``account.read`` covers cost-per-click,
competitor-adjacent search terms and the agency's own spend — none of which is a row a company
horizon narrows into something safe to show a client.
"""

from app.core.permissions import PermissionSpec

GOOGLE_ADS_PERMISSIONS: list[PermissionSpec] = [
    # The credential and the links: set the developer token, link an account to a client,
    # verify one, unlink. A materially different act from *using* a linked account — the same
    # split ``cloudflare.settings.manage`` and ``google.settings.manage`` draw.
    PermissionSpec("google_ads.settings.manage", position=10),
    # Everything read-only: accounts, campaigns, ad groups, keywords, negatives, search terms,
    # conversions, changes, recommendations. Granted to `member` because an account manager who
    # cannot see the client's spend cannot do their job.
    PermissionSpec("google_ads.account.read", position=20, default_roles=("admin", "member")),
    # The tenant's own Ads policy per client — protected terms, exclude categories, thresholds,
    # the decisions log. Editing it changes what an agent will propose, so it is not a read.
    PermissionSpec("google_ads.policy.manage", position=30),
    # The validated GAQL passthrough. Its own key because it is the one read whose shape nobody
    # reviewed: an agent may ask a question this module never anticipated, which is the point,
    # and also the reason an agency should decide who gets it.
    PermissionSpec("google_ads.query.run", position=40),
    # -- writes ------------------------------------------------------------------------------ #
    PermissionSpec("google_ads.campaign.write", position=50),
    PermissionSpec("google_ads.budget.write", position=60),
    PermissionSpec("google_ads.keyword.write", position=70),
    PermissionSpec("google_ads.negative.write", position=80),
]
