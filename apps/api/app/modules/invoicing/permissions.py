"""Permissions the invoicing module introduces (issue #207, CLAUDE.md §15).

Money is commercially sensitive, so reads default to admins (the subscriptions/revenue
stance); a tenant widens per role. ``send`` is split from ``write`` because drafting an
invoice and *mailing a client* are different trusts; ``payment.write`` is split so a
bookkeeper role can register payments without being able to touch documents.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

INVOICING_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("invoicing.invoice.read", position=10),
    PermissionSpec("invoicing.invoice.write", position=20),
    PermissionSpec("invoicing.invoice.send", position=30),
    PermissionSpec("invoicing.invoice.delete", position=40),
    PermissionSpec("invoicing.quote.read", position=50),
    PermissionSpec("invoicing.quote.write", position=60),
    PermissionSpec("invoicing.quote.send", position=70),
    PermissionSpec("invoicing.quote.delete", position=80),
    PermissionSpec("invoicing.payment.write", position=90),
    # Tax rates, templates, numbering, reminders, seller identity, accounting.
    PermissionSpec("invoicing.settings.manage", position=100),
    # Writing a document template's own HTML/CSS. Split from `settings.manage` because it is
    # a strictly larger act: arranging blocks and picking a colour configures a design we
    # ship, while authoring Jinja is running code on the agency's own server. It is sandboxed
    # and fetches nothing (app/modules/invoicing/render/engine.py), but a tenant should still
    # be able to let an office manager rearrange an invoice without handing them that.
    PermissionSpec("invoicing.template.author", position=110),
]
