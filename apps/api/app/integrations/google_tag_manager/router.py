"""REST endpoints for google_tag_manager under ``/api/v1/gtm``. Business-licensed — see LICENSE.

Deny-by-default: every route declares one of the four ``google_tag_manager.*`` permissions (§15).
No route here carries ``no_permission_required`` and none should — needing one would mean
something about a client's tracking is readable without a grant.

**These routes are the MCP surface.** Every ``/api/v1`` operation becomes an MCP tool, generated
from this app's own OpenAPI document and proxied in-process back through ``require_context``
(CLAUDE.md §12) — so the tool name is the handler's name and the permission a key must carry is
the one declared right here. Which is why the handlers are named for what an agent would ask for
(``create_gtm_tag``, not ``create_tag``, which would collide with two other modules' and fall
back to an unreadable operation id), and why the router prefix is ``/gtm`` rather than
``/google-tag-manager``: the prefix's last segment *is* the section URL an agent is pointed at
(``/mcp/gtm``), and that URL gets pasted into somebody else's settings screen.

The one shape worth noticing while reading: nearly every route takes ``workspace_id`` as an
optional query parameter and almost nobody passes it. Absent means "the workspace schakl writes
in", resolved from Instellingen → Tag Manager — because the alternative is making every caller,
human and machine, learn what a GTM workspace is before they can list a tag.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.google_tag_manager import reads
from app.integrations.google_tag_manager.models import GtmContainer, GtmConversion
from app.integrations.google_tag_manager.reads import WorkspaceChoice, resolve_workspace_path
from app.integrations.google_tag_manager.schemas import (
    GtmAvailableContainer,
    GtmContainerCreate,
    GtmContainerRead,
    GtmContainerUpdate,
    GtmConversionCreate,
    GtmConversionRead,
    GtmPickerRead,
    GtmPublishResult,
    GtmSettingsRead,
    GtmSettingsWrite,
    GtmSnippetRead,
    GtmTagRead,
    GtmTagUpdate,
    GtmTagWrite,
    GtmTriggerRead,
    GtmTriggerWrite,
    GtmVariableRead,
    GtmVariableWrite,
    GtmVersionCreate,
    GtmVersionCreated,
    GtmVersionRead,
    GtmWorkspaceContentsRead,
    GtmWorkspaceRead,
    GtmWorkspaceStatusRead,
)
from app.integrations.google_tag_manager.service import GtmService, container_url
from app.integrations.google_tag_manager.writes import GtmWriteService

router = APIRouter(prefix="/gtm", tags=["google_tag_manager"])


def _read(row: GtmContainer, company_name: str | None = None) -> GtmContainerRead:
    """The response shape, written out.

    Deliberately not a sweep over ``row.__table__.columns``: that reads every column the table
    has, ``updated_at`` included, which carries ``onupdate=func.now()`` and is therefore expired
    after a flush — touching it from a synchronous helper fires a refresh SELECT with no greenlet
    to run it in. Enumerating is also what stops the payload quietly growing a field the next
    migration adds.
    """
    return GtmContainerRead(
        id=row.id,
        gtm_account_id=row.account_id,
        gtm_container_id=row.container_id,
        public_id=row.public_id,
        path=row.path,
        company_id=row.company_id,
        company_name=company_name,
        website_id=row.website_id,
        connection_id=row.connection_id,
        name=row.name,
        usage_context=list(row.usage_context or []),
        domain_names=list(row.domain_names or []),
        tagging_server_urls=list(row.tagging_server_urls or []),
        live_version_id=row.live_version_id,
        live_version_name=row.live_version_name,
        tag_count=row.tag_count,
        trigger_count=row.trigger_count,
        variable_count=row.variable_count,
        workspace_changes=row.workspace_changes,
        observed_at=row.observed_at,
        active=row.active,
        status=row.status,
        last_error=row.last_error,
        last_verified_at=row.last_verified_at,
        last_synced_at=row.last_synced_at,
        tag_manager_url=container_url(row.account_id, row.container_id),
    )


def _conversion(row: GtmConversion) -> GtmConversionRead:
    return GtmConversionRead(
        id=row.id,
        container_id=row.container_id,
        name=row.name,
        key=row.key,
        kind=row.kind,
        status=row.status,
        config=dict(row.config or {}),
        workspace_id=row.workspace_id,
        trigger_id=row.trigger_id,
        tag_id=row.tag_id,
        published_version_id=row.published_version_id,
        last_error=row.last_error,
        observed_at=row.observed_at,
        created_by_name=row.created_by_name,
        created_at=row.created_at,
    )


# --- settings ------------------------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=GtmSettingsRead,
    dependencies=[require_permission("google_tag_manager.settings.manage")],
)
async def get_gtm_settings(ctx: RequestContext = Depends(require_context)) -> GtmSettingsRead:
    """The org's Tag Manager posture: the write kill switch and the workspace schakl writes in."""
    row = await GtmService(ctx).settings_row()
    choice = WorkspaceChoice.from_row(row)
    return GtmSettingsRead(
        writes_enabled=row.writes_enabled if row else True,
        own_workspace=choice.own_workspace,
        workspace_name=choice.workspace_name,
    )


@router.put(
    "/settings",
    response_model=GtmSettingsRead,
    dependencies=[require_permission("google_tag_manager.settings.manage")],
)
async def save_gtm_settings(
    payload: GtmSettingsWrite, ctx: RequestContext = Depends(require_context)
) -> GtmSettingsRead:
    await GtmService(ctx).save_settings(
        writes_enabled=payload.writes_enabled,
        own_workspace=payload.own_workspace,
        workspace_name=payload.workspace_name,
    )
    return await get_gtm_settings(ctx)


# --- containers ------------------------------------------------------------------------------ #
@router.get(
    "/containers",
    response_model=list[GtmContainerRead],
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def list_gtm_containers(
    company_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(default=False),
    ctx: RequestContext = Depends(require_context),
) -> list[GtmContainerRead]:
    """Every linked Tag Manager container this caller may see — **start here**.

    The list an agent needs before anything else: it names the containers, and every other tool
    takes one of these ``id`` values. Company-scoped logins see only the containers of clients in
    their horizon; a container attached to no client (the agency's own) stays visible to all.
    """
    rows = await GtmService(ctx).list_containers(company_id=company_id, active_only=active_only)
    return [_read(row) for row in rows]


@router.get(
    "/containers/available",
    response_model=GtmPickerRead,
    dependencies=[require_permission("google_tag_manager.settings.manage")],
)
async def list_available_gtm_containers(
    q: str = Query(
        "",
        max_length=120,
        description=(
            "A Tag Manager account name, or a container id such as GTM-XXXXXXX. An id is "
            "resolved directly; anything else selects accounts by name and opens those."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> GtmPickerRead:
    """Search the containers the caller's own Google grant can reach — **a search, not a list**.

    Live: a picker showing a stale list is how somebody links a container that was deleted last
    month. It is a *search* because Tag Manager's quota is per user per minute and listing every
    account's containers is one request per account — an agency holding forty-four of them cannot
    afford the sweep, and got a quota refusal instead of a picker.

    ``accounts_total`` and ``accounts_read`` say how much of the grant this answer covers, so an
    empty result reads as "narrow the search" rather than as "you are not in that account".
    """
    result = await GtmService(ctx).available_containers(q)
    return GtmPickerRead(
        query=q,
        accounts_total=result.accounts_total,
        accounts_read=result.accounts_read,
        containers=[
            GtmAvailableContainer(
                gtm_account_id=option.account_id,
                account_name=option.account_name,
                gtm_container_id=option.container_id,
                public_id=option.public_id,
                name=option.name,
                path=option.path,
                usage_context=list(option.usage_context),
                already_linked=option.already_linked,
            )
            for option in result.containers
        ],
        warnings=list(result.warnings),
    )


@router.get(
    "/containers/{container_id}",
    response_model=GtmContainerRead,
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def get_gtm_container(
    container_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GtmContainerRead:
    return _read(await GtmService(ctx).get_container(container_id))


@router.post(
    "/containers",
    response_model=GtmContainerRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_tag_manager.settings.manage")],
)
async def link_gtm_container(
    payload: GtmContainerCreate, ctx: RequestContext = Depends(require_context)
) -> GtmContainerRead:
    """Attach a container to this workspace, and say whose it is.

    Named either by its numeric pair or by the ``GTM-XXXXXXX`` on the client's website — the
    second is resolved through Google's own lookup, so nobody has to dig a container id out of a
    URL before they can link the container they are looking at.
    """
    row = await GtmService(ctx).link(
        account_id=payload.gtm_account_id,
        container_id=payload.gtm_container_id,
        public_id=payload.public_id,
        company_id=payload.company_id,
        website_id=payload.website_id,
    )
    return _read(row)


@router.patch(
    "/containers/{container_id}",
    response_model=GtmContainerRead,
    dependencies=[require_permission("google_tag_manager.settings.manage")],
)
async def update_gtm_container(
    container_id: uuid.UUID,
    payload: GtmContainerUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GtmContainerRead:
    service = GtmService(ctx)
    row = await service.get_container(container_id)
    await service.update_container(
        row,
        company_id=payload.company_id,
        website_id=payload.website_id,
        active=payload.active,
        # Absent and explicit-null are different answers and the payload alone cannot tell them
        # apart — only ``model_fields_set`` can (CLAUDE.md §18).
        company_id_set="company_id" in payload.model_fields_set,
        website_id_set="website_id" in payload.model_fields_set,
    )
    return _read(row)


@router.delete(
    "/containers/{container_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("google_tag_manager.settings.manage")],
)
async def unlink_gtm_container(
    container_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Forget the container here. **Nothing is removed from Tag Manager** — an agency that stops
    working for a client does not thereby delete the tracking off their website."""
    await GtmService(ctx).unlink(container_id)


@router.post(
    "/containers/{container_id}/verify",
    response_model=GtmContainerRead,
    dependencies=[require_permission("google_tag_manager.settings.manage")],
)
async def verify_gtm_container(
    container_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GtmContainerRead:
    """Ask Google what it says about this container, and record the answer either way.

    Never raises for a container that answered badly: the outcome *is* the row, so a failure
    comes back as ``status="error"`` with Google's own sentence on it rather than as an envelope
    the screen has to guess the meaning of.
    """
    return _read(await GtmService(ctx).verify(container_id))


@router.get(
    "/containers/{container_id}/snippet",
    response_model=GtmSnippetRead,
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def gtm_container_snippet(
    container_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GtmSnippetRead:
    """The install snippet, for the developer who has to put it on the site."""
    service = GtmService(ctx)
    async with service.open_client(container_id, tool="snippet") as (client, row):
        snippet = await reads.container_snippet(client, row.path)
    return GtmSnippetRead(public_id=row.public_id, snippet=snippet)


# --- workspaces -------------------------------------------------------------------------------- #
@router.get(
    "/containers/{container_id}/workspaces",
    response_model=list[GtmWorkspaceRead],
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def list_gtm_workspaces(
    container_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> list[GtmWorkspaceRead]:
    """The container's workspaces — its shared drafts. Usually one; sometimes one per person."""
    service = GtmService(ctx)
    async with service.open_client(container_id, tool="workspaces") as (client, row):
        rows = await reads.list_workspaces(client, row.path)
    return [GtmWorkspaceRead(**entry) for entry in rows]


@router.get(
    "/containers/{container_id}/workspace",
    response_model=GtmWorkspaceContentsRead,
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def read_gtm_workspace(
    container_id: uuid.UUID,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> GtmWorkspaceContentsRead:
    """One workspace, whole: its tags, triggers, variables and what of them is staged.

    The four routes below answer the same questions separately, and each resolves the workspace
    for itself — which means listing the container's workspaces first. A screen wanting all four
    therefore spent **eight** Google requests where this spends **five**, on an API whose quota is
    counted per user per minute. Ask for this when you want the workspace; ask for one of the
    others when you want one of them.
    """
    service = GtmService(ctx)
    choice = WorkspaceChoice.from_row(await service.settings_row())
    async with service.open_client(container_id, tool="workspace") as (client, row):
        payload = await reads.workspace_contents(
            client, row.path, workspace_id=workspace_id, choice=choice
        )
    return GtmWorkspaceContentsRead(**payload)


@router.get(
    "/containers/{container_id}/status",
    response_model=GtmWorkspaceStatusRead,
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def gtm_workspace_status(
    container_id: uuid.UUID,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> GtmWorkspaceStatusRead:
    """What is staged in a workspace and not live — the question before every publish."""
    service = GtmService(ctx)
    choice = WorkspaceChoice.from_row(await service.settings_row())
    async with service.open_client(container_id, tool="status") as (client, row):
        workspace = await resolve_workspace_path(
            client, row.path, workspace_id=workspace_id, choice=choice
        )
        payload = (
            await reads.workspace_status(client, workspace)
            if workspace
            else {"workspace_id": "", "changes": 0, "entries": [], "merge_conflicts": 0}
        )
    return GtmWorkspaceStatusRead(**payload)


# --- tags---------------------------------------------------------------------------------------- #
@router.get(
    "/containers/{container_id}/tags",
    response_model=list[GtmTagRead],
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def list_gtm_tags(
    container_id: uuid.UUID,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> list[GtmTagRead]:
    """Every tag in a workspace — what is (or is about to be) measuring this client's site."""
    rows = await _in_workspace(ctx, container_id, workspace_id, reads.list_tags, tool="tags")
    return [GtmTagRead(**entry) for entry in rows]


@router.post(
    "/containers/{container_id}/tags",
    response_model=GtmTagRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def create_gtm_tag(
    container_id: uuid.UUID,
    payload: GtmTagWrite,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> GtmTagRead:
    """Create a tag from its own ``type`` and parameter array.

    The general case, and the escape hatch from the conversion recipe: GTM decides which
    parameter keys a tag template accepts and its refusal names the field, so this is validated
    by Google rather than here. It lands in a workspace and is live for nobody until a version is
    published — which is a different permission.
    """
    created = await GtmWriteService(ctx).create_tag(
        container_id, payload, workspace_id=workspace_id
    )
    return GtmTagRead(**_tag_shape(created))


@router.patch(
    "/containers/{container_id}/tags/{tag_id}",
    response_model=GtmTagRead,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def update_gtm_tag(
    container_id: uuid.UUID,
    tag_id: str,
    payload: GtmTagUpdate,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> GtmTagRead:
    """Change one tag. Reads it first and writes back under its fingerprint, so an edit somebody
    made in Tag Manager meanwhile is a 409 rather than a silent overwrite of their work."""
    updated = await GtmWriteService(ctx).update_tag(
        container_id, tag_id, payload, workspace_id=workspace_id
    )
    return GtmTagRead(**_tag_shape(updated))


@router.delete(
    "/containers/{container_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def delete_gtm_tag(
    container_id: uuid.UUID,
    tag_id: str,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> None:
    await GtmWriteService(ctx).delete_tag(container_id, tag_id, workspace_id=workspace_id)


# --- triggers------------------------------------------------------------------------------------ #
@router.get(
    "/containers/{container_id}/triggers",
    response_model=list[GtmTriggerRead],
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def list_gtm_triggers(
    container_id: uuid.UUID,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> list[GtmTriggerRead]:
    rows = await _in_workspace(
        ctx, container_id, workspace_id, reads.list_triggers, tool="triggers"
    )
    return [GtmTriggerRead(**entry) for entry in rows]


@router.post(
    "/containers/{container_id}/triggers",
    response_model=GtmTriggerRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def create_gtm_trigger(
    container_id: uuid.UUID,
    payload: GtmTriggerWrite,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> GtmTriggerRead:
    """Create a trigger from six named kinds rather than from GTM's own vocabulary.

    ``page_view``, ``form_submit``, ``link_click``, ``element_click``, ``element_visibility``,
    ``custom_event`` — plus ``url_contains`` to narrow any of them to one part of the site. The
    built-in variables the resulting trigger reads are switched on with it, because a trigger
    referring to a variable that does not exist is stored happily by GTM and fires never.
    """
    created = await GtmWriteService(ctx).create_trigger(
        container_id, payload, workspace_id=workspace_id
    )
    return GtmTriggerRead(
        trigger_id=str(created.get("triggerId") or ""),
        name=str(created.get("name") or ""),
        type=str(created.get("type") or ""),
        notes=str(created.get("notes") or "") or None,
        fingerprint=str(created.get("fingerprint") or "") or None,
        path=str(created.get("path") or ""),
    )


@router.delete(
    "/containers/{container_id}/triggers/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def delete_gtm_trigger(
    container_id: uuid.UUID,
    trigger_id: str,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> None:
    await GtmWriteService(ctx).delete_trigger(container_id, trigger_id, workspace_id=workspace_id)


# --- variables----------------------------------------------------------------------------------- #
@router.get(
    "/containers/{container_id}/variables",
    response_model=list[GtmVariableRead],
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def list_gtm_variables(
    container_id: uuid.UUID,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> list[GtmVariableRead]:
    rows = await _in_workspace(
        ctx, container_id, workspace_id, reads.list_variables, tool="variables"
    )
    return [GtmVariableRead(**entry) for entry in rows]


@router.post(
    "/containers/{container_id}/variables",
    response_model=GtmVariableRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def create_gtm_variable(
    container_id: uuid.UUID,
    payload: GtmVariableWrite,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> GtmVariableRead:
    """Create a user-defined variable — a dataLayer read (``v``), a constant (``c``), a lookup.

    Same contract as a tag: the ``type`` and parameter keys are GTM's, and GTM validates them.
    """
    created = await GtmWriteService(ctx).create_variable(
        container_id, payload, workspace_id=workspace_id
    )
    return GtmVariableRead(
        variable_id=str(created.get("variableId") or ""),
        name=str(created.get("name") or ""),
        type=str(created.get("type") or ""),
        notes=str(created.get("notes") or "") or None,
        parameter=[p for p in (created.get("parameter") or []) if isinstance(p, dict)],
        fingerprint=str(created.get("fingerprint") or "") or None,
        path=str(created.get("path") or ""),
    )


@router.delete(
    "/containers/{container_id}/variables/{variable_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def delete_gtm_variable(
    container_id: uuid.UUID,
    variable_id: str,
    workspace_id: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> None:
    await GtmWriteService(ctx).delete_variable(container_id, variable_id, workspace_id=workspace_id)


# --- versions------------------------------------------------------------------------------------ #
@router.get(
    "/containers/{container_id}/versions",
    response_model=list[GtmVersionRead],
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def list_gtm_versions(
    container_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> list[GtmVersionRead]:
    """The container's version history, newest first, with the live one marked."""
    service = GtmService(ctx)
    async with service.open_client(container_id, tool="versions") as (client, row):
        rows = await reads.list_versions(client, row.path, live_version_id=row.live_version_id)
    return [GtmVersionRead(**entry) for entry in rows]


@router.post(
    "/containers/{container_id}/versions",
    response_model=GtmVersionCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def create_gtm_version(
    container_id: uuid.UUID,
    payload: GtmVersionCreate,
    ctx: RequestContext = Depends(require_context),
) -> GtmVersionCreated:
    """Freeze what is staged into a version. **Still live for nobody** — publishing is separate.

    ``empty=true`` means the workspace had nothing to freeze: GTM answers 200 with no version at
    all, which is not a failure and is emphatically not something anybody can publish.
    """
    return GtmVersionCreated(**await GtmWriteService(ctx).create_version(container_id, payload))


@router.post(
    "/containers/{container_id}/versions/{version_id}/publish",
    response_model=GtmPublishResult,
    dependencies=[require_permission("google_tag_manager.version.publish")],
)
async def publish_gtm_version(
    container_id: uuid.UUID,
    version_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GtmPublishResult:
    """Make this version live on the client's website, now, for every visitor.

    The only call on this surface with an audience outside the building, which is why it carries
    its own permission, its own OAuth scope and its own line in the activity trail.
    """
    return GtmPublishResult(**await GtmWriteService(ctx).publish(container_id, version_id))


# --- conversions--------------------------------------------------------------------------------- #
@router.get(
    "/containers/{container_id}/conversions",
    response_model=list[GtmConversionRead],
    dependencies=[require_permission("google_tag_manager.container.read")],
)
async def list_gtm_conversions(
    container_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> list[GtmConversionRead]:
    """The conversions schakl set up in this container, and whether each is live yet.

    Stored rows, not a Google call: GTM records that a tag exists and records nowhere that it is
    the client's "offerte aangevraagd" conversion, set up by us, on a date, by a person.
    """
    rows = await GtmService(ctx).list_conversions(container_id)
    return [_conversion(row) for row in rows]


@router.post(
    "/containers/{container_id}/conversions",
    response_model=GtmConversionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_tag_manager.tag.write")],
)
async def create_gtm_conversion(
    container_id: uuid.UUID,
    payload: GtmConversionCreate,
    ctx: RequestContext = Depends(require_context),
) -> GtmConversionRead:
    """Set up one conversion: the trigger, the tag, and the record that they belong together.

    ``kind="ga4_event"`` needs ``event_name`` and ``measurement_id``; ``kind="ads_conversion"``
    needs ``conversion_id`` and ``conversion_label``. Neither is ever guessed — sending a client's
    conversions to a measurement id we picked would be wrong in a way no screen could show.

    It lands in a workspace and is live for nobody until a version is published.
    """
    return _conversion(await GtmWriteService(ctx).create_conversion(container_id, payload))


# --- helpers------------------------------------------------------------------------------------- #
def _tag_shape(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag_id": str(payload.get("tagId") or ""),
        "name": str(payload.get("name") or ""),
        "type": str(payload.get("type") or ""),
        "paused": bool(payload.get("paused") or False),
        "notes": str(payload.get("notes") or "") or None,
        "firing_trigger_id": [str(v) for v in (payload.get("firingTriggerId") or [])],
        "blocking_trigger_id": [str(v) for v in (payload.get("blockingTriggerId") or [])],
        "parameter": [p for p in (payload.get("parameter") or []) if isinstance(p, dict)],
        "fingerprint": str(payload.get("fingerprint") or "") or None,
        "path": str(payload.get("path") or ""),
        "tag_manager_url": str(payload.get("tagManagerUrl") or "") or None,
    }


async def _in_workspace(
    ctx: RequestContext,
    container_id: uuid.UUID,
    workspace_id: str | None,
    reader: Any,
    *,
    tool: str,
) -> list[dict[str, Any]]:
    """Resolve the workspace, then read it — the shape every list route above shares.

    The settings row is read **before** the client opens, because everything inside that block
    runs with the pooled database connection released (docs/PERFORMANCE.md).
    """
    service = GtmService(ctx)
    choice = WorkspaceChoice.from_row(await service.settings_row())
    async with service.open_client(container_id, tool=tool) as (client, row):
        workspace = await resolve_workspace_path(
            client, row.path, workspace_id=workspace_id, choice=choice
        )
        if not workspace:
            return []
        return await reader(client, workspace)
