"""Permissions the wordpress module introduces (docs/WORDPRESS.md §6, CLAUDE.md §15).

Two keys, and the split is ``cloudflare.settings.manage`` / ``cloudflare.dns.read``'s — reading
that a client's site is connected and what it can reach is not the same act as pointing schakl
at a different WordPress, or rotating the credential it holds.

It is drawn harder here than anywhere else in the codebase, and the reason is worth stating
once: **every Rank Math AI Visibility route is ``manage_options``**, so the application password
this module stores necessarily belongs to a WordPress **Administrator**. There is no read-only
shape available to ask for. That makes ``wordpress_sites`` a table of full-admin credentials for
every client site an agency touches — a materially bigger blast radius than a Cloudflare token
scoped to DNS reads, and not something to hand out with "may edit a website".

So: ``manage`` is admin-only by default and never folded into ``websites.website.write``, and
neither key is ever granted to ``client``. A client-portal login has no business knowing that a
credential for their site exists, let alone what it reaches.

``read`` goes to ``member`` because "is this client's site connected, and does it have Rank
Math?" is a question an account manager asks while doing ordinary work, and a capability every
employee needs that only admins hold does not read as a policy — it reads as a broken screen
(#310). The company horizon still decides *whose* sites, through
``WordPressSite.__company_horizon_clause__``.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_MEMBER, PermissionSpec

WORDPRESS_PERMISSIONS: list[PermissionSpec] = [
    # The connection as a fact: which websites are connected, what each credential was observed
    # to reach, when it was last verified, and why a probe failed. Never the password.
    PermissionSpec(
        "wordpress.site.read",
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
        position=10,
    ),
    # The credential itself: connect a website, rotate the application password, re-verify,
    # disconnect. Admin-only by default — see the module docstring.
    PermissionSpec("wordpress.site.manage", position=20),
]
