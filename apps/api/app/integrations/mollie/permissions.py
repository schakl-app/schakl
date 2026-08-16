"""Permissions the mollie module introduces (issue #267, CLAUDE.md §15).

**One key, admin-only by default and never ``client``.** It governs the credential: adding,
rotating, verifying and removing the API key that collects an agency's money.

There is deliberately no ``mollie.payment.*``. The act of *starting* a payment is an invoice
act, so it declares ``invoicing.payment.link`` — the key that already knows about documents,
the company horizon and the client role. Minting a parallel key here would mean an agency
granting two permissions to let a bookkeeper do one thing, and a second provider would make it
three. §6's rule about not importing another module's internals is about code; this is the same
rule about grants.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

MOLLIE_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("mollie.settings.manage", position=10),
]
