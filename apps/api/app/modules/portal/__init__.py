"""portal module (CLAUDE.md §6, issues #193 and #296) — client logins into the agency's app.

Importing this package self-registers the module (router, permissions, i18n namespace) into
the shared registry.

**Why this is a module and not a wing of ``contacts``.** The portal is a product the agency
sells access to, not a property of the address book: it has its own screens, its own audience,
its own licence, and — the deciding argument — its own lifecycle. Turning it off must stop new
invites without touching a single contact, and turning it on for a second kind of subject (a
supplier, a company-level login) must not edit the contacts module. What contacts keeps is the
one thing that is genuinely its own: the row that says who the client *is*, published through
``app/core/portal.py``'s subject seam.

**What deliberately did not move** (``modules/contacts/portal.py``): the company horizon of an
existing portal membership, and the "is this user a client login?" resolver. Those must answer
whether or not this module is enabled or licensed. An entitlement decides whether you may
invite someone new; it may never decide whether an existing client session stays contained.
"""

from __future__ import annotations

from app.modules.portal.permissions import PORTAL_PERMISSIONS
from app.modules.portal.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="portal",
    router=router,
    i18n_namespace="portal",
    # Licensed module (issue #137): giving clients their own logins requires a licence covering
    # this sku. Past expiry+grace it goes read-only — existing client logins keep working and
    # keep their horizon (that lives in contacts, on purpose), but no new invite goes out and
    # the web renders the invite control locked with the upgrade path behind it.
    sku="portal",
    permissions=PORTAL_PERMISSIONS,
)

registry.register(module)
