"""Permissions the contacts module contributes (issue #19, CLAUDE.md §6)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

CONTACT_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "contacts.contact.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    # Keeping the people at a client current is what an agency employee does all day — a new
    # contact person at Acme is not an administrative act, and an org where only admins may type
    # one in gets an address book that is quietly wrong (#310). So `member` holds both writes by
    # default; `delete` stays admin-only, because losing a person loses their contact moments'
    # counterpart and their portal login with them.
    PermissionSpec("contacts.contact.write", position=20, default_roles=(ROLE_ADMIN, ROLE_MEMBER)),
    PermissionSpec("contacts.contact.delete", position=30),
    # Attaching a contact to a company is a distinct capability from editing the contact — but it
    # is not a *rarer* one: creating a contact from a client page, from the "Verbonden klanten"
    # picker or from a contact moment all attach, so a role holding one write and not the other
    # meets a 403 on the flow it was granted for. They default together and the web mirrors both.
    PermissionSpec("contacts.link.write", position=40, default_roles=(ROLE_ADMIN, ROLE_MEMBER)),
    # Tenant-configurable contact types (issue #91): everyone reads them (to type a link and to
    # filter); managing the catalog under Instellingen is admin-only.
    PermissionSpec(
        "contacts.type.read",
        position=50,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("contacts.type.manage", position=60),
    # ``contacts.portal.impersonate`` moved to the portal module as ``portal.login.impersonate``
    # when the portal became one (#296). Stored grants are rewritten in place per org by
    # ``@rev:296-portal-module`` in ``core/permissions/reconcile.py`` — nobody's access changes.
]
