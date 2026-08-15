"""Permissions the ``snelstart`` module introduces (§15). Business-licensed — see LICENSE.

Three keys, and the split is ``oxxa``'s argument applied to a ledger: **holding the credential,
acting through it, and writing with it are three different grants.**

- ``snelstart.settings.manage`` — connect, rotate, verify, remove, and set the mappings. The
  credential screen.
- ``snelstart.sync.run`` — read the administration: pull the vocabulary, look up relations,
  reconcile which invoices are paid. Nothing outside schakl changes.
- ``snelstart.ledger.write`` — push. This is the one that changes somebody else's books, and
  it is the one an agency hands to the person who is allowed to.

Gating the sync screen on the write key would mean anyone who may push may also read which
credentials exist; gating the push on the settings key would mean the only person who can send
an invoice to the accountant is the person who administers the integration. Neither is what an
agency wants, and both are what one permission would have forced.

Deliberately **absent**: a permission for pushing *an invoice*. That act is invoicing's, it is
already gated by ``invoicing.invoice.write``, and minting a second key for it here would let a
role hold one without the other — which is how an invoice reaches an accountant's ledger from
somebody who may not touch invoices. The push route declares both.

All three default to admin only, and none is ever granted to ``client``: a client-portal login
that could read an agency's chart of accounts is not a smaller version of the feature, it is a
different feature nobody asked for.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

SNELSTART_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("snelstart.settings.manage", position=10),
    PermissionSpec("snelstart.sync.run", position=20),
    PermissionSpec("snelstart.ledger.write", position=30),
]
