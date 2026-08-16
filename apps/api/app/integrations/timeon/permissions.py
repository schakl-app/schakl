"""Permissions the ``timeon`` integration introduces (§15). Business-licensed — see LICENSE.

Three keys, and the split is ``snelstart``'s argument applied to a timesheet: **holding the
credential, looking through it, and letting it write are three different grants.**

- ``timeon.settings.manage`` — connect, rotate, verify, remove, and set the policy. This is the
  one that decides whether tonight's run rewrites somebody's hours, so it is the credential
  screen's key and admin-only.
- ``timeon.sync.run`` — run a sync, read the runs, the pairings and the conflict queue. Nothing
  is decided; a dry run is entirely within it. An ordinary operational job.
- ``timeon.sync.write`` — a run may actually write, and a conflict may be settled. The key that
  changes data, in either direction.

Gating the queue on the write key would mean nobody could *see* what needs deciding without
holding the power to decide it; gating the write on the settings key would mean the only person
who can settle a conflict is the person who rotates API keys. Neither is what an agency wants,
and one permission would have forced both.

**``timeon.sync.write`` is never sufficient on its own.** A pull writes ``time_entries``, so the
routes and the service that pull also require ``time.entry.write`` at ``:any`` — a ride-along
write carries the gates of the module it writes into, not of the route it rode in on (#314). A
key that may sync must not be a second, quieter way to rewrite an employee's timesheet.

All three default to admin only, and none is ever granted to ``client``: a portal login that
could read which projects an agency tracks in somebody else's tool is not a smaller version of
this feature.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

TIMEON_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("timeon.settings.manage", position=10),
    PermissionSpec("timeon.sync.run", position=20),
    PermissionSpec("timeon.sync.write", position=30),
]
