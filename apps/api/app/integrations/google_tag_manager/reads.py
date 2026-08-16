"""Live reads of a container's contents. Business-licensed — see LICENSE.

Everything here calls Google on every request, on purpose: a tag list is exactly the thing that
must not be a mirror, because the whole question an agency asks it — *what is measuring this site
right now* — is a question about somebody else's live state, and half of the edits to it are made
in the Tag Manager interface by people who do not work here. The stored row (:mod:`.models`)
carries only what a *panel* needs, so the company hub never waits for Google (#364).

**Which workspace** is the one decision a caller keeps having to make and should not have to.
:func:`resolve_workspace_path` answers it once: the workspace named, else the one schakl writes
in, else whatever the container has. And the reason schakl writes in one of its own by default is
the thing about GTM that surprises everybody: a workspace is a *shared* draft, so writing into
"Default Workspace" puts our half-finished change in front of whoever else is mid-edit — and
their next Publish ships it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.integrations.google_tag_manager.client import GtmClient
from app.integrations.google_tag_manager.errors import (
    GtmInvalidError,
    GtmNotFoundError,
)


@dataclass(frozen=True)
class WorkspaceChoice:
    """The org's workspace policy, read from the database **before** the client opens.

    It has to be: every Google call runs inside ``ctx.release_db()``, so a settings read from
    inside the block would check a pooled connection back out with no RLS GUC bound and match
    nothing (docs/PERFORMANCE.md).
    """

    own_workspace: bool
    workspace_name: str

    @classmethod
    def from_row(cls, row: Any) -> WorkspaceChoice:
        if row is None:
            return cls(own_workspace=True, workspace_name="schakl")
        return cls(own_workspace=row.own_workspace, workspace_name=row.workspace_name)


async def resolve_workspace_path(
    client: GtmClient,
    container_path: str,
    *,
    workspace_id: str | None = None,
    choice: WorkspaceChoice | None = None,
    create: bool = False,
) -> str:
    """The workspace to read from or write into, as a Google-relative path.

    ``create`` is what separates a read from a write: a read must never bring a workspace into
    existence as a side effect of somebody opening a screen, while a write has to have one. A
    read of a container with no workspace at all is an empty list, not an error.
    """
    if workspace_id:
        return f"{container_path}/workspaces/{workspace_id}"

    workspaces = await client.list(f"{container_path}/workspaces", "workspace")
    wanted = (choice.workspace_name if choice and choice.own_workspace else "").strip()
    if wanted:
        for workspace in workspaces:
            if str(workspace.get("name") or "").strip().casefold() == wanted.casefold():
                return str(workspace.get("path") or "")
        if create:
            made = await client.post(f"{container_path}/workspaces", {"name": wanted})
            return str(made.get("path") or "")
    if workspaces:
        return str(workspaces[0].get("path") or "")
    if create:
        made = await client.post(f"{container_path}/workspaces", {"name": wanted or "schakl"})
        return str(made.get("path") or "")
    return ""


def _url(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("tagManagerUrl") or "")
    return value or None


async def list_workspaces(client: GtmClient, container_path: str) -> list[dict[str, Any]]:
    rows = await client.list(f"{container_path}/workspaces", "workspace")
    return [
        {
            "workspace_id": str(row.get("workspaceId") or ""),
            "name": str(row.get("name") or ""),
            "description": str(row.get("description") or "") or None,
            "path": str(row.get("path") or ""),
            "fingerprint": str(row.get("fingerprint") or "") or None,
        }
        for row in rows
    ]


async def workspace_status(client: GtmClient, workspace_path: str) -> dict[str, Any]:
    """What is staged and not live, plus anything that will not merge cleanly.

    The entries are flattened into ``{kind, change, name}`` rather than handed over as GTM's
    ``Entity`` union: the union has eleven mutually exclusive members, of which a screen wants
    the one that is set and the name inside it.
    """
    payload = await client.get(f"{workspace_path}/status")
    entries: list[dict[str, Any]] = []
    for entity in payload.get("workspaceChange") or []:
        if not isinstance(entity, dict):
            continue
        change = str(entity.get("changeStatus") or "")
        for kind in (
            "tag",
            "trigger",
            "variable",
            "folder",
            "builtInVariable",
            "client",
            "zone",
            "customTemplate",
            "transformation",
            "gtagConfig",
        ):
            body = entity.get(kind)
            if isinstance(body, dict):
                entries.append(
                    {
                        "kind": kind,
                        "change": change,
                        "name": str(body.get("name") or body.get("type") or ""),
                    }
                )
                break
    return {
        "workspace_id": workspace_path.rsplit("/", 1)[-1],
        "changes": len(entries),
        "entries": entries,
        "merge_conflicts": len(payload.get("mergeConflict") or []),
    }


async def list_tags(client: GtmClient, workspace_path: str) -> list[dict[str, Any]]:
    rows = await client.list(f"{workspace_path}/tags", "tag")
    return [
        {
            "tag_id": str(row.get("tagId") or ""),
            "name": str(row.get("name") or ""),
            "type": str(row.get("type") or ""),
            "paused": bool(row.get("paused") or False),
            "notes": str(row.get("notes") or "") or None,
            "firing_trigger_id": [str(v) for v in (row.get("firingTriggerId") or [])],
            "blocking_trigger_id": [str(v) for v in (row.get("blockingTriggerId") or [])],
            "parameter": [p for p in (row.get("parameter") or []) if isinstance(p, dict)],
            "fingerprint": str(row.get("fingerprint") or "") or None,
            "path": str(row.get("path") or ""),
            "tag_manager_url": _url(row),
        }
        for row in rows
    ]


async def list_triggers(client: GtmClient, workspace_path: str) -> list[dict[str, Any]]:
    rows = await client.list(f"{workspace_path}/triggers", "trigger")
    return [
        {
            "trigger_id": str(row.get("triggerId") or ""),
            "name": str(row.get("name") or ""),
            "type": str(row.get("type") or ""),
            "notes": str(row.get("notes") or "") or None,
            "fingerprint": str(row.get("fingerprint") or "") or None,
            "path": str(row.get("path") or ""),
        }
        for row in rows
    ]


async def list_variables(client: GtmClient, workspace_path: str) -> list[dict[str, Any]]:
    rows = await client.list(f"{workspace_path}/variables", "variable")
    return [
        {
            "variable_id": str(row.get("variableId") or ""),
            "name": str(row.get("name") or ""),
            "type": str(row.get("type") or ""),
            "notes": str(row.get("notes") or "") or None,
            "parameter": [p for p in (row.get("parameter") or []) if isinstance(p, dict)],
            "fingerprint": str(row.get("fingerprint") or "") or None,
            "path": str(row.get("path") or ""),
        }
        for row in rows
    ]


async def list_versions(
    client: GtmClient, container_path: str, *, live_version_id: str | None
) -> list[dict[str, Any]]:
    """The version history, newest first, with the live one marked.

    ``version_headers`` rather than ``versions``: the header carries the counts and the name
    without pulling every tag of every version, which for a container with a hundred versions is
    the difference between one small response and a great many enormous ones.
    """
    rows = await client.list(f"{container_path}/version_headers", "containerVersionHeader")
    out = [
        {
            "version_id": str(row.get("containerVersionId") or ""),
            "name": str(row.get("name") or ""),
            "deleted": bool(row.get("deleted") or False),
            "live": bool(live_version_id)
            and str(row.get("containerVersionId") or "") == live_version_id,
            "num_tags": int(row.get("numTags") or 0),
            "num_triggers": int(row.get("numTriggers") or 0),
            "num_variables": int(row.get("numVariables") or 0),
            "path": str(row.get("path") or ""),
        }
        for row in rows
    ]
    # GTM answers oldest-first; a version list is read newest-first by everybody who reads one.
    out.sort(key=lambda v: int(v["version_id"] or 0), reverse=True)
    return out


async def container_snippet(client: GtmClient, container_path: str) -> str:
    """The install snippet, for the developer who has to put it on the site.

    A missing snippet is not an error: a **server** container has none, and answering 502 for
    one would make an ordinary container type look broken. Only *that* is swallowed — a refused
    credential still raises, because a blank install snippet with no explanation is the worst
    thing this endpoint could hand a developer.
    """
    try:
        payload = await client.get(f"{container_path}:snippet")
    except (GtmNotFoundError, GtmInvalidError):
        return ""
    return str(payload.get("snippet") or "")
