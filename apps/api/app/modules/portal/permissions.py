"""Permissions the portal module contributes (issue #19, CLAUDE.md §6, §15).

Only one key lives here, and the absence of a second is deliberate.

**Managing a client login stays ``members.member.write``.** Creating an account, mailing an
invite and disabling it are member management — the same capability that invites a colleague,
pointed at someone outside the building. Minting ``portal.login.manage`` instead would have
silently *removed* the ability from every tenant-defined role that holds member management
today, which no reconciler can put back: a per-key diff cannot tell "never offered" from
"offered and unticked" (see ``core/permissions/reconcile.py``).

**Becoming the client is its own key**, never implied by managing their login (#296): creating
an invite and *being* the person are different acts, and an agency should be able to grant the
first without the second. It was ``contacts.portal.impersonate`` until the portal became a
module; the rename is applied to stored roles and API keys by a one-time revision, so nobody's
grants change — only how they are spelled.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

#: Renamed from ``contacts.portal.impersonate`` when the portal became its own module. The old
#: string is rewritten in place per org by ``@rev:296-portal-module`` in ``reconcile.py``.
PORTAL_IMPERSONATE = "portal.login.impersonate"

PORTAL_PERMISSIONS: list[PermissionSpec] = [
    # Admin-only by default (the spec default), never a client's or an ordinary member's.
    PermissionSpec(PORTAL_IMPERSONATE, position=10),
]
