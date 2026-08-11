"""Permissions the uptime module introduces (docs/UPTIME.md §13, CLAUDE.md §15).

``instance.manage`` is admin-only by default and separate from everything else, for the reason
``cloudflare.settings.manage`` is separate from ``zone.manage``: Uptime Kuma has **no user
management** — three socket events, none of which creates an account — so whatever an agency
enrols is the full administrator of that instance. Handing out "may point schakl at a Kuma" is
handing out a client's entire monitoring, and it is not the same act as using the connection
somebody else configured.

``monitor.pause`` is deliberately not folded into ``monitor.write``. Silencing an alert during a
planned migration is an ordinary thing to ask of an ordinary employee; repointing a monitor at a
different host is not, and an agency that wants the first without the second must be able to say
so. The same split ``leave`` draws between requesting and approving.

``monitor.read`` is the one scoped key (§15): ``:own`` is what a client-portal login holds and
``:any`` what staff hold, with the company horizon still deciding *whose*. Scope is the only
thing that can fence it, because a client seeing their own uptime is a genuinely good portal
feature and the same endpoint serves the agency's cross-client list.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

UPTIME_PERMISSIONS: list[PermissionSpec] = [
    # The credential and the connection: add, enrol, re-enrol, verify, delete an instance, and
    # run a sync. Admin-only by default.
    PermissionSpec("uptime.instance.manage", position=10),
    # Read monitors and their status. Scoped, and granted to `member` because looking up whether
    # a client's site is down is not an administrator's act.
    #
    # `client` is **not** in `default_own_roles` yet, deliberately: the portal half is gate 3, and
    # until `UptimeMonitor` declares `__portal_horizon_clause__` a client login would be fenced
    # by the staff rule. Granting the scope before the clause exists is #266's mistake in
    # miniature. Gate 3 widens it through a `DefaultsRevision`, which is the only thing that
    # reaches orgs that already applied these defaults.
    PermissionSpec(
        "uptime.monitor.read",
        scopes=("own", "any"),
        default_roles=("admin", "member"),
        position=20,
    ),
    # Create, edit, delete a monitor or a group; adopt an import.
    PermissionSpec("uptime.monitor.write", position=30),
    # Pause and resume, without the ability to change what is monitored.
    PermissionSpec("uptime.monitor.pause", position=40),
    # The tenant's default-settings profiles (gate 2).
    PermissionSpec("uptime.profile.manage", position=50),
]
