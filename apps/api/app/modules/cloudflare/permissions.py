"""Permissions the cloudflare module introduces (issue #278, CLAUDE.md §15).

Three keys, all **admin-only by default and never ``client``**. `domains.domain.write` already
excludes the client role, but reusing it here would have been wrong for a different reason: it
edits *our record of* a domain, while these edit the domain's live DNS, its redirects and the
account it lives in. A tenant who widens "may edit a domain" to every member must not silently
also hand out "may repoint this client's nameservers".

Tenant-editable like every permission (§15) — an agency that wants a member to run DNS changes
grants it explicitly, which is the whole difference.

Why ``settings.manage`` exists on top of the two keys the issue names: a Cloudflare API token is
a credential, and creating or rotating one is a materially different act from using it. Holding
``zone.manage`` lets you change a client's DNS through the account the agency configured;
holding ``settings.manage`` lets you point schakl at a *different* Cloudflare account entirely.
The same split ``google.settings.manage`` draws around the OAuth client.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

CLOUDFLARE_PERMISSIONS: list[PermissionSpec] = [
    # The credential itself: add, rotate, verify, delete a Cloudflare account, and sync its
    # zone/Pages inventory.
    PermissionSpec("cloudflare.settings.manage", position=10),
    # Read-only: the zone list, its DNS records and the export, the connection status report.
    # Separate from ``zone.manage`` so an agency can let a marketeer *look up* a client's DNS
    # without also handing them the ability to change it.
    PermissionSpec("cloudflare.dns.read", position=20),
    # Everything that mutates live infrastructure: create/adopt a zone, edit DNS records, set or
    # remove the domain-wide redirect, attach a hostname to a Pages project.
    PermissionSpec("cloudflare.zone.manage", position=30),
]
