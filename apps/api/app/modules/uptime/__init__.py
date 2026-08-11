"""``uptime`` module (docs/UPTIME.md) — Uptime Kuma monitors, groups and defaults.

Gives ``Website.uptime_enabled`` a real mechanism for the first time. It has existed since #94
as a flag whose own comment reads *"The uptime webhook (a later automation slice) acts on this
flag"* — the same shape ``Domain.status = redirect`` was in before ``cloudflare`` (#278), and
resolved the same way: by talking to the provider directly and owning what we created, rather
than firing a webhook into a flow that cannot tell us it drifted.

The credential is a **row, not a setting**, and the row's ``mode`` decides what the rest of it
means: a ``managed`` instance we hold a token for and configure, a ``linked`` instance that only
posts at us. Uptime Kuma has no user management at all, so there is no least-privilege service
account to ask for — whatever an agency enrols is the full admin of that instance — which is why
we store a token and never a password, and why ``linked`` is a first-class mode rather than a
degraded one.

Importing this package self-registers the module.
"""

from __future__ import annotations

from app.modules.uptime.panels import UPTIME_PANELS
from app.modules.uptime.permissions import UPTIME_PERMISSIONS
from app.modules.uptime.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="uptime",
    router=router,
    i18n_namespace="uptime",
    # Licensed module (issue #137): a paid integration, the same bracket as `cloudflare` /
    # `google` / `marketing`. Past expiry the mount-time gate turns every mutation 402 while the
    # read surface — the stored mirror, the last observed status, the timeline — keeps working,
    # so a lapsed licence never leaves an agency unable to see that a client's site is down.
    sku="uptime",
    permissions=UPTIME_PERMISSIONS,
    panels=UPTIME_PANELS,
)

registry.register(module)
