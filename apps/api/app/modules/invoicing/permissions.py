"""Permissions the invoicing module introduces (issue #207, CLAUDE.md §15).

Money is commercially sensitive, so reads default to admins (the subscriptions/revenue
stance); a tenant widens per role. ``send`` is split from ``write`` because drafting an
invoice and *mailing a client* are different trusts; ``payment.write`` is split so a
bookkeeper role can register payments without being able to touch documents.

``invoice.read`` is **scoped** (#266), and the distinction it draws is *breadth*, not
ownership of a row: ``:any`` is the invoicing module — the seller identity and bank details,
the price list, the template library, the org-wide unbilled-hours backlog, what the
accounting package knows — while ``:own`` reaches documents and nothing else, through the
company horizon that already decides which client's they are (#191/#252). That split is what
lets the ``client`` role hold an invoice read at all: without it, one key opened six org-wide
staff surfaces, and *"the client can see their invoices"* would have meant the agency's
margin, rates and price list too.

A pre-#266 org stored the key bare. That reads as the broadest grant everywhere
(``PermissionSet.has`` answers ``True`` for a bare key at any scope), so nothing breaks
mid-flight; the startup reconciler rewrites it to ``:any`` once, because the roles API may
only *store* a scoped permission suffixed.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, SCOPES, PermissionSpec

INVOICING_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "invoicing.invoice.read",
        scopes=SCOPES,
        position=10,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_CLIENT,),
    ),
    PermissionSpec("invoicing.invoice.write", position=20),
    PermissionSpec("invoicing.invoice.send", position=30),
    PermissionSpec("invoicing.invoice.delete", position=40),
    PermissionSpec("invoicing.quote.read", position=50),
    PermissionSpec("invoicing.quote.write", position=60),
    PermissionSpec("invoicing.quote.send", position=70),
    PermissionSpec("invoicing.quote.delete", position=80),
    PermissionSpec("invoicing.payment.write", position=90),
    # Starting an **online** payment (epic #269) — a different act from registering one, and
    # the only write on this module a client may hold. `payment.write` says "this money
    # arrived" and is a bookkeeping claim; this says "open a checkout for what is owed" and
    # settles nothing on its own: the provider's own authenticated answer does that, through
    # a webhook nobody can forge. So a client paying their own invoice needs exactly this and
    # nothing more, and reusing `payment.write` would have handed them the ability to declare
    # an invoice paid.
    #
    # Scoped for the same reason `invoice.read` is (#266): `:own` reaches documents the
    # company horizon already narrowed to them, `:any` additionally reaches the org-wide
    # surface — which credentials are connected at all. Nothing here can name an amount; the
    # server charges `outstanding` whoever asked.
    PermissionSpec(
        "invoicing.payment.link",
        scopes=SCOPES,
        position=95,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_CLIENT,),
    ),
    # Tax rates, templates, numbering, reminders, seller identity, accounting.
    PermissionSpec("invoicing.settings.manage", position=100),
    # Writing a document template's own HTML/CSS. Split from `settings.manage` because it is
    # a strictly larger act: arranging blocks and picking a colour configures a design we
    # ship, while authoring Jinja is running code on the agency's own server. It is sandboxed
    # and fetches nothing (app/modules/invoicing/render/engine.py), but a tenant should still
    # be able to let an office manager rearrange an invoice without handing them that.
    PermissionSpec("invoicing.template.author", position=110),
]
