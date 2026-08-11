"""``wordpress`` module (docs/WORDPRESS.md) — one Application Password per website.

The credential an agency already needs to administer a client's site turns out to be the
credential that reads Rank Math's **AI Visibility** numbers *and* the one that drives WordPress
MCP, because Rank Math registers its AI Visibility features as WordPress Abilities with
``show_in_rest => true`` and ``mcp => ['public' => true]``. So one row per website reaches four
surfaces on the same host — core REST, the Abilities API, the MCP Adapter, and Rank Math's own
``rankmath/v1/ai-visibility`` proxy — and what looked like two integrations is one.

Its shape is ``cloudflare``'s and ``uptime``'s, one level down the tree: Cloudflare is something
a **domain** has, uptime and WordPress are things a **website** has. The credential is therefore
a **row, not a per-org setting** — an agency holds dozens of client sites and none of them is
"the" WordPress account — and the working surface is one ``EntityPanelSpec`` on the website
detail page rather than a nav item, because WordPress is not a place you go.

This module owns the credential and nothing about marketing. ``marketing`` reads it through
``app/core/wordpress.py`` (§6), which is why the resolver is registered here at import time and
why the seam answers ``None`` — the same answer as "no credential yet" — on an install where
this module is disabled.

Importing this package self-registers the module.
"""

from __future__ import annotations

from app.core.wordpress import register_wordpress_resolver
from app.modules.wordpress.permissions import WORDPRESS_PERMISSIONS
from app.modules.wordpress.router import router
from app.modules.wordpress.service import open_client, resolve_credential
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="wordpress",
    router=router,
    i18n_namespace="wordpress",
    # Licensed module (issue #137): a paid integration, the same bracket as `cloudflare` /
    # `uptime` / `marketing`, and deliberately **not** folded into the `websites` sku. The
    # web-assets trio is the free-ish record-keeping half; reaching into a client's live
    # WordPress and pulling AI-visibility analytics out of it is the paid half. Past expiry the
    # mount-time gate turns every mutation 402 while the panel keeps rendering what was last
    # observed — a lapsed licence must never make an agency unable to see which sites they hold
    # credentials for.
    sku="wordpress",
    permissions=WORDPRESS_PERMISSIONS,
)

registry.register(module)

# The seam `marketing` resolves a website's credential through (§6). Registered here rather
# than imported there, so nothing outside this package ever names `WordPressSite` — or
# `WordPressClient`, which is why the client factory rides along: a borrower asks this module
# for a client instead of constructing one from a class it had to import.
register_wordpress_resolver(resolve_credential, open_client)
