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
    # --- the site's content, through the credential ------------------------------------- #
    # Reading a client's pages, posts and media as the editor sees them — drafts included,
    # raw content and ACF fields beside the rendered HTML. Ordinary account-manager work.
    PermissionSpec(
        "wordpress.content.read",
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
        position=30,
    ),
    # Writing what **no visitor sees yet**: creating a draft, editing a draft or a pending
    # review. Member by default, because "prepare the new service page" is the job.
    PermissionSpec(
        "wordpress.content.write",
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
        position=40,
    ),
    # Writing what visitors see: editing a published page, or setting a draft live. The
    # `google_tag_manager` split (`tag.write` / `version.publish`), applied where WordPress
    # puts the boundary — there is no staging in core, so an edit to a published page *is*
    # the broadcast. Admin-only by default, and the key an agency withholds from an MCP key
    # it lets an assistant hold.
    PermissionSpec("wordpress.content.publish", position=50),
    # --- Contact Form 7 -------------------------------------------------------------------- #
    PermissionSpec(
        "wordpress.forms.read",
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
        position=60,
    ),
    # A form edit is live the moment it saves and decides where a client's leads land, so it
    # sits with `publish` rather than with `write`.
    PermissionSpec("wordpress.forms.write", position=70),
    # --- Abilities ------------------------------------------------------------------------ #
    # Listing what the site registers, and running the abilities that declare themselves
    # `readonly`. The annotation is the plugin author's claim; an ability that makes no claim
    # is treated as a write, so the failure direction is "refused something harmless".
    PermissionSpec(
        "wordpress.ability.read",
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
        position=80,
    ),
    # Running any ability at all, writes included. What ACF 6.8's "Allow AI access" toggle
    # opens on the site side, this key opens on ours; both have to say yes.
    PermissionSpec("wordpress.ability.run", position=90),
    # --- the passthrough --------------------------------------------------------------- #
    # Any GET the site's REST index lists, under the credential. The escape hatch for the
    # plugin namespace no curated route knows (WPML, LiteSpeed, FileBird, Link Genius).
    PermissionSpec(
        "wordpress.rest.read",
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
        position=100,
    ),
    # Any other verb — minus the site-takeover routes `surface.REST_WRITE_DENIED` refuses
    # outright. Admin only, and the key an agency should mint deliberately for a person, never
    # by default for an assistant: one call under it can deface a client's site.
    PermissionSpec("wordpress.rest.write", position=110),
]
