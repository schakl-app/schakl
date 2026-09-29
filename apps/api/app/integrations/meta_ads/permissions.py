"""Permissions the meta_ads integration introduces (§15). Business-licensed — see LICENSE.

**Five keys, and three of them are writes.** An MCP key carries scopes, so one
``meta_ads.write`` would make "rename a campaign", "raise its budget" and "start it spending"
the same grant. They are three different acts with three different costs of being wrong:

* ``campaign.write`` builds and edits. Everything it makes is **created paused**, so a key
  holding only this can prepare a whole campaign and spend nothing.
* ``budget.write`` changes what may be spent. Split off because a daily budget with an extra
  zero is money that has already gone.
* ``ads.activate`` is ``PAUSED`` → ``ACTIVE``: the single act after which a client is billed.

Pausing rides ``campaign.write``: stopping spend is the safe direction, and a key that may
build a campaign and not stop one is a key that cannot clean up after itself.

Never ``client`` (#266). The read alone covers what every campaign costs and returns.
"""

from app.core.permissions import PermissionSpec

META_ADS_PERMISSIONS: list[PermissionSpec] = [
    # The guardrails and the kill switch.
    PermissionSpec("meta_ads.policy.manage", position=10),
    # Campaigns, ad sets, ads, creatives, insights and the decisions log.
    PermissionSpec("meta_ads.account.read", position=20, default_roles=("admin", "member")),
    # Create and edit campaigns, ad sets, creatives and ads — always paused — and pause them.
    PermissionSpec("meta_ads.campaign.write", position=30),
    # Daily and lifetime budgets.
    PermissionSpec("meta_ads.budget.write", position=40),
    # Switch a campaign, ad set or ad on. The act that spends.
    PermissionSpec("meta_ads.ads.activate", position=50),
]
