"""Permissions the google_tag_manager integration introduces (§15). Business-licensed — see LICENSE.

**Four keys, and the line that matters is between the third and the fourth.**

Everything this integration can do falls into two kinds of act. Editing a workspace — adding a
tag, a trigger, a variable, cutting a version out of them — changes a **draft**: real, recorded,
and served to nobody. Publishing a version changes what runs in every visitor's browser on the
client's website, immediately, with no review step behind it.

That is the split an agency wants to be able to hand out separately, and it is precisely the one a
single ``google_tag_manager.write`` would destroy. With it, "let the assistant prepare the
tracking for the new campaign and I will look it over" is an API key holding
``google_tag_manager.tag.write`` and nothing else: it may build and version, and the publish route
answers 403 however useful it thinks the change is.

Version *creation* deliberately rides ``tag.write`` rather than earning a fifth key. A version is
a snapshot of a workspace — the act of writing it down — and gating it behind the publish
permission would leave the staging half unable to finish its own work.

All admin-only by default and **never** ``client`` (#266). Before granting any of these to the
seeded client role, list what the read alone covers: every tag on the container, which includes
the conversion values, the remarketing ids and whatever the previous agency left behind.
"""

from app.core.permissions import PermissionSpec

GOOGLE_TAG_MANAGER_PERMISSIONS: list[PermissionSpec] = [
    # The links and the kill switch: connect a container to a client, verify one, unlink, and
    # switch every mutating call off at once. A materially different act from *using* a linked
    # container — the same split ``google_ads.settings.manage`` draws.
    PermissionSpec("google_tag_manager.settings.manage", position=10),
    # Everything read-only: containers, workspaces, tags, triggers, variables, versions, the
    # install snippet and the conversions schakl set up. Granted to ``member`` because an account
    # manager who cannot see what is measuring the client's site cannot answer for it.
    PermissionSpec(
        "google_tag_manager.container.read", position=20, default_roles=("admin", "member")
    ),
    # Change a workspace: tags, triggers, variables, the conversion recipe, and cutting a version
    # out of what was staged. Nothing here is live until somebody publishes.
    PermissionSpec("google_tag_manager.tag.write", position=30),
    # Make a version live on the client's website. The one act on this surface with an audience
    # outside the building, and therefore the one key an agency hands out separately.
    PermissionSpec("google_tag_manager.version.publish", position=40),
]
