"""Permissions the oxxa module introduces (issue #296, CLAUDE.md §15).

Three keys, all **admin-only by default and never ``client``**. `domains.domain.write` already
excludes the client role, but reusing it here would be wrong for the reason the `cloudflare.*`
keys give: it edits *our record of* a domain, while these read a registrar's register and
repoint a client's live delegation. A tenant who widens "may edit a domain" to every member must
not silently also hand out "may move this client's nameservers".

**The issue names two keys; this ships three.** `oxxa.registrar.sync` and
`oxxa.registrar.manage` collapse two different powers into one grant, because acting *through*
the configured reseller login and *replacing* that login are not the same act — the second
repoints schakl at a different register entirely. `cloudflare.settings.manage` draws exactly
this line around its API token, and `google.settings.manage` around the OAuth client. A settings
screen also needs a permission to gate on, and gating it on `registrar.manage` would mean anyone
who may push nameservers may also read which credentials exist.

Tenant-editable like every permission (§15) — an agency that wants a member to run registrar
syncs grants it explicitly, which is the whole difference.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

OXXA_PERMISSIONS: list[PermissionSpec] = [
    # The credential itself: add, rotate, verify, delete an OXXA reseller login.
    PermissionSpec("oxxa.settings.manage", position=10),
    # Read-only: run a register sync, read the stored register, refresh one domain from the
    # registrar. Separate from ``registrar.manage`` so an agency can let someone *look up* when
    # a domain expires and who the registrant is without handing them the delegation.
    PermissionSpec("oxxa.registrar.sync", position=20),
    # The one thing here that changes the outside world: push a domain's nameservers.
    PermissionSpec("oxxa.registrar.manage", position=30),
]
