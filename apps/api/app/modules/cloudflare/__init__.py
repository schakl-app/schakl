"""cloudflare module (CLAUDE.md §6, epic #278) — DNS, redirects and Pages per client domain.

Gives ``Domain.status = redirect`` a real mechanism for the first time. Until now that field and
``redirect_url`` were a status/URL pair with nothing behind them: the actual redirect was wired by
an external flow (#96). This module makes it a Cloudflare Redirect Rule schakl owns, can read
back, and can tell you has been changed underneath it.

The credential is a **row, not a setting**. An agency holds its own Cloudflare account and its
clients bring theirs, so a per-org singleton would have been wrong on the first day of use —
which is also why nothing in here ever picks an account for you when there is more than one.

The registrar half of #278 (OXXA: nameserver sync + the write path that pushes
``CloudflareZone.name_servers`` back to the registrar) is split into its own issue: it needs
credentials and real API documentation that do not exist yet, and CLAUDE.md forbids writing an
integration from memory. The seam it plugs into is already here — a connected zone stores the
nameservers Cloudflare assigned, and pushing them is a separate, retryable step.

Importing this package self-registers the module.
"""

from __future__ import annotations

from app.modules.cloudflare.permissions import CLOUDFLARE_PERMISSIONS
from app.modules.cloudflare.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="cloudflare",
    router=router,
    i18n_namespace="cloudflare",
    # Licensed module (issue #137): a paid integration, the same bracket as ``google`` /
    # ``invoicing`` / ``marketing``, never part of the free CRM core (epic #140). Past
    # expiry+grace the mount-time gate turns every mutation 402; the read surface (zone list,
    # DNS view, export, stored status) keeps working, so an expired licence never leaves an
    # agency unable to look up a client's DNS.
    sku="cloudflare",
    permissions=CLOUDFLARE_PERMISSIONS,
)

registry.register(module)
