"""google_tag_manager — a client's Tag Manager container as a first-class surface, and as MCP tools.

Business-licensed — see LICENSE. Architecture: ``docs/GOOGLE_TAG_MANAGER.md``.

**What this is for.** Half of an agency's marketing work is *making the measuring happen* — a
conversion on a new form, an event on a quote request, a tag for the campaign that starts on
Monday — and until now the whole of it happened in a browser tab schakl knew nothing about. Which
container is this client's, what is live in it, who put that tag there and when, and whether the
change somebody staged three weeks ago was ever published: none of that had an answer here.

Now all of it is an API operation, which means all of it is an **MCP tool** (CLAUDE.md §12). An
assistant asked to "set up the offerte-conversie for AAZET" travels the same tenant resolution,
the same RLS binding and the same permission checks the browser does, and can never exceed the
API key's scopes.

**Why an integration rather than depth in ``marketing``.** §6a's test: cancel the vendor and is
the thing *gone*, or merely poorer? Cancel Tag Manager and there is nothing left here at all —
every row is a pointer into somebody else's state. ``marketing`` stays a module by the same test
even though every number on it arrives through Google, because the dashboard, the periods and the
narratives are ours.

``MarketingSource`` still, correctly, does not list GTM. That decision was made about *metrics*
and it was right: a container has no marketeer-facing numbers of its own, and the conversions it
fires arrive through GA4 already. This module is not a source; it is the surface that *creates*
the thing GA4 later reports on.

**Where it sits.** Under Marketing in the menu, beside Google Ads: the dashboard says how the
client is doing, Ads says what the advertising is doing, and this says what is measuring it.
"""

from __future__ import annotations

from arq import cron

# ``contributions`` is imported for its side effect: it registers the company-container
# provider on the core seam (#411). The card this module used to draw on the hub is gone, and
# the one fact it carried that nothing else did rides the marketing panel now.
from app.integrations.google_tag_manager import contributions  # noqa: F401
from app.integrations.google_tag_manager.jobs import gtm_sync_all
from app.integrations.google_tag_manager.permissions import GOOGLE_TAG_MANAGER_PERMISSIONS
from app.integrations.google_tag_manager.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="google_tag_manager",
    # A conversation with somebody else's service, not a capability of our own.
    kind=KIND_INTEGRATION,
    # Requires `google`: the credential is a `google_connections` row and there is no second way
    # to obtain one. Deliberately **not** `marketing` as well — the nav item sits in that group,
    # but an agency who wants their clients' tagging under control and no traffic dashboard must
    # not be made to switch on a module they did not ask for (``google_ads``' reasoning exactly).
    requires=("google",),
    router=router,
    i18n_namespace="gtm",
    # Licensed (#137), the same bracket as ``google`` / ``marketing`` / ``google_ads``. Past
    # expiry+grace the mount-time gate turns every mutation 402 while the reads keep working: an
    # expired licence must never leave an agency unable to look up what is measuring a client's
    # site, and it certainly must not leave a container half-edited.
    sku="google_tag_manager",
    permissions=GOOGLE_TAG_MANAGER_PERMISSIONS,
    # 05:35 — after marketing (04:45) and google_ads (05:15). All three walk every org making
    # outbound Google calls, and stacking them on one minute is how a box with thirty clients
    # meets its own rate limits at four in the morning.
    cron_jobs=[cron(gtm_sync_all, hour=5, minute=35)],
)

registry.register(module)
