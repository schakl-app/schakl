"""Permissions the meta integration introduces (§15). Business-licensed — see LICENSE.

**Four keys, and the line that matters is between the third and the fourth.**

Writing a post changes a **draft**: real, recorded, and shown to nobody. Scheduling or
publishing one changes what a client's followers read, under the client's name, with no review
step behind it. That is the split an agency wants to hand out separately, and the one a single
``meta.post.write`` would destroy: with it, "let the assistant prepare next month's posts and I
will look them over" is an API key holding ``meta.post.write`` and nothing else.

**Scheduling is publishing.** A scheduled post is a stored decision a worker executes with
nobody in front of it, so the permission is asked when the decision is *written* (#335) — and
the key for that is ``meta.post.publish``, not the drafting key.

Never ``client`` (#266). What the read alone covers is every planned post for every client the
caller's horizon reaches, which is an agency's content calendar.
"""

from app.core.permissions import PermissionSpec

META_PERMISSIONS: list[PermissionSpec] = [
    # The app, the tokens, which asset is which client's, the scheduler and the kill switch.
    PermissionSpec("meta.settings.manage", position=10),
    # Everything read-only: the linked assets, the planned and published posts, the insights.
    PermissionSpec("meta.asset.read", position=20, default_roles=("admin", "member")),
    # Drafts: write, edit, attach media, offer for approval. Nothing here reaches Meta.
    PermissionSpec("meta.post.write", position=30, default_roles=("admin", "member")),
    # Approve, schedule, publish now, reschedule, withdraw, take down. The one set of acts on
    # this surface with an audience outside the building.
    PermissionSpec("meta.post.publish", position=40),
]
