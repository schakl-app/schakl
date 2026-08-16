"""Changing a container, and publishing one. Business-licensed — see LICENSE.

This is the half with an audience outside the building, so four gates stand in front of every
call here and each of them answers a different question:

1. **the route's permission** — may this caller do this kind of thing at all (§15);
2. **the org's kill switch** (``gtm_settings.writes_enabled``) — is this instance writing to
   Tag Manager *today*, a switch an owner can reach in a hurry;
3. **the OAuth scope on the grant being used** — Google's own answer, checked here so a missing
   consent reads as "reconnect your Google account" rather than as an unexplained 403 three
   calls later;
4. **GTM's own validator** — the tag template decides which parameter keys are legal, and its
   refusal names the field.

Two properties are worth stating on their own.

**Nothing here publishes as a side effect.** A tag, a trigger, a variable and a version are all
staged: they change a draft nobody is served. ``publish`` is the only call that changes what runs
in a visitor's browser, it has its own permission, and it is never implied by any of the others.

**A write reads first.** Every update passes the fingerprint the read handed back, which turns
"the client's own marketeer edited this tag while your form was open" into a 409 instead of a
silent overwrite of their work.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.activity import ActivityService
from app.errors import AppError
from app.integrations.google.models import GoogleConnection
from app.integrations.google.oauth import (
    has_tag_manager_edit_scope,
    has_tag_manager_publish_scope,
    has_tag_manager_version_scope,
)
from app.integrations.google_tag_manager import recipes
from app.integrations.google_tag_manager.client import GtmClient, gtm_client
from app.integrations.google_tag_manager.errors import GtmError, GtmNotConfigured
from app.integrations.google_tag_manager.models import (
    GtmContainer,
    GtmConversion,
    GtmConversionKind,
    GtmConversionStatus,
)
from app.integrations.google_tag_manager.reads import WorkspaceChoice, resolve_workspace_path
from app.integrations.google_tag_manager.schemas import (
    GtmConversionCreate,
    GtmTagUpdate,
    GtmTagWrite,
    GtmTriggerWrite,
    GtmVariableWrite,
    GtmVersionCreate,
)
from app.integrations.google_tag_manager.service import GtmService

_ENTITY = "gtm_container"
_CONVERSION_ENTITY = "gtm_conversion"

#: Which grant a call needs, beyond simply being able to read.
NEED_EDIT = "edit"
NEED_VERSION = "version"
NEED_PUBLISH = "publish"

_WHITESPACE = re.compile(r"\s+")


def conversion_key(name: str) -> str:
    """Casefolded and whitespace-collapsed — see :class:`~.models.GtmConversion`."""
    return _WHITESPACE.sub(" ", str(name or "").strip()).casefold()


def _params(payload: Any) -> list[dict[str, Any]]:
    """A ``GtmParameter`` list as Google spells it — ``by_alias`` restores ``list`` and ``map``."""
    return [p.model_dump(by_alias=True, exclude_none=True) for p in (payload or [])]


@dataclass(frozen=True)
class _Prepared:
    row: GtmContainer
    connection: GoogleConnection
    choice: WorkspaceChoice


class GtmWriteService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.service = GtmService(ctx)
        self.activity = ActivityService(ctx)

    # --- gates -------------------------------------------------------------------------------- #

    async def _prepare(self, container_pk: uuid.UUID, *, need: str) -> _Prepared:
        """Everything the session is needed for, read **before** the pool connection is released.

        The order is the point: the row, the kill switch, the workspace policy and the connection
        all come out of the database, and the first statement after ``release_db()`` would rebind
        the RLS GUC — so a settings read from inside the client block matches nothing.
        """
        row = await self.service.get_container(container_pk)
        await self.service.require_writes_enabled()
        settings_row = await self.service.settings_row()
        connection = await self.service.connection_for_container(row)
        self._require_scope(connection, need)
        return _Prepared(
            row=row, connection=connection, choice=WorkspaceChoice.from_row(settings_row)
        )

    @staticmethod
    def _require_scope(connection: GoogleConnection, need: str) -> None:
        """Google's own gate, asked here so the refusal names the fix.

        Checked in advance rather than left to a 403 from Google, because the two are not the
        same sentence: Google's says "permission denied", and what actually happened is that this
        connection was granted before the org asked for the Tag Manager scopes. One of those
        sends somebody to the client's Tag Manager permissions; the other is one reconnect.
        """
        scopes = connection.scopes
        if need == NEED_PUBLISH and not has_tag_manager_publish_scope(scopes):
            raise GtmNotConfigured("the google connection may not publish tag manager versions")
        if need == NEED_VERSION and not has_tag_manager_version_scope(scopes):
            raise GtmNotConfigured("the google connection may not create tag manager versions")
        if need == NEED_EDIT and not has_tag_manager_edit_scope(scopes):
            raise GtmNotConfigured("the google connection may not edit tag manager containers")

    @asynccontextmanager
    async def _open(
        self, container_pk: uuid.UUID, *, need: str, tool: str
    ) -> AsyncIterator[tuple[GtmClient, _Prepared]]:
        prepared = await self._prepare(container_pk, need=need)
        async with (
            gtm_client(self.ctx.session, self.ctx.org, prepared.connection, tool=tool) as client,
            self.ctx.release_db(),
        ):
            yield client, prepared

    # --- tags ---------------------------------------------------------------------------------- #

    async def create_tag(
        self, container_pk: uuid.UUID, payload: GtmTagWrite, *, workspace_id: str | None = None
    ) -> dict[str, Any]:
        """Create one tag, exactly as written. GTM validates the parameters; we validate nobody.

        Not retried anywhere below this call: a repeated ``tags.create`` is a second tag firing a
        second time on somebody's website, and no amount of hoping makes that idempotent.
        """
        self.ctx.require("google_tag_manager.tag.write")
        body: dict[str, Any] = {
            "name": payload.name,
            "type": payload.type,
            "parameter": _params(payload.parameter),
            "firingTriggerId": list(payload.firing_trigger_id),
        }
        if payload.blocking_trigger_id:
            body["blockingTriggerId"] = list(payload.blocking_trigger_id)
        if payload.notes:
            body["notes"] = payload.notes
        if payload.paused is not None:
            body["paused"] = payload.paused

        async with self._open(container_pk, need=NEED_EDIT, tool="create_tag") as (client, prep):
            workspace = await resolve_workspace_path(
                client,
                prep.row.path,
                workspace_id=workspace_id,
                choice=prep.choice,
                create=True,
            )
            created = await client.post(f"{workspace}/tags", body)
        await self.activity.record(
            _ENTITY,
            prep.row.id,
            "gtm.tag_created",
            {"tag": created.get("name"), "type": created.get("type"), "id": created.get("tagId")},
        )
        return created

    async def update_tag(
        self,
        container_pk: uuid.UUID,
        tag_id: str,
        payload: GtmTagUpdate,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Read, merge, write back under the fingerprint that read handed us.

        A merge rather than a replace, because GTM's ``update`` is a whole-object PUT: sending
        only the changed fields blanks everything else on the tag — the wholesale-PUT trap, which
        here would silently unhook a tag from its trigger.
        """
        self.ctx.require("google_tag_manager.tag.write")
        async with self._open(container_pk, need=NEED_EDIT, tool="update_tag") as (client, prep):
            workspace = await resolve_workspace_path(
                client, prep.row.path, workspace_id=workspace_id, choice=prep.choice
            )
            if not workspace:
                raise GtmError("this container has no workspace to write in", status=None)
            path = f"{workspace}/tags/{_id(tag_id)}"
            current = await client.get(path)
            body = dict(current)
            if payload.name is not None:
                body["name"] = payload.name
            if payload.parameter is not None:
                body["parameter"] = _params(payload.parameter)
            if payload.firing_trigger_id is not None:
                body["firingTriggerId"] = list(payload.firing_trigger_id)
            if payload.blocking_trigger_id is not None:
                body["blockingTriggerId"] = list(payload.blocking_trigger_id)
            if payload.notes is not None:
                body["notes"] = payload.notes
            if payload.paused is not None:
                body["paused"] = payload.paused
            updated = await client.put(
                path, body, fingerprint=str(current.get("fingerprint") or "") or None
            )
        await self.activity.record(
            _ENTITY,
            prep.row.id,
            "gtm.tag_updated",
            {"tag": updated.get("name"), "id": updated.get("tagId")},
        )
        return updated

    async def delete_tag(
        self, container_pk: uuid.UUID, tag_id: str, *, workspace_id: str | None = None
    ) -> None:
        self.ctx.require("google_tag_manager.tag.write")
        async with self._open(container_pk, need=NEED_EDIT, tool="delete_tag") as (client, prep):
            workspace = await resolve_workspace_path(
                client, prep.row.path, workspace_id=workspace_id, choice=prep.choice
            )
            if not workspace:
                raise GtmError("this container has no workspace to write in", status=None)
            await client.delete(f"{workspace}/tags/{_id(tag_id)}")
        await self.activity.record(_ENTITY, prep.row.id, "gtm.tag_deleted", {"id": tag_id})

    # --- triggers ------------------------------------------------------------------------------ #

    async def create_trigger(
        self,
        container_pk: uuid.UUID,
        payload: GtmTriggerWrite,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a trigger from the recipe's vocabulary — see :mod:`.recipes` for why not GTM's."""
        self.ctx.require("google_tag_manager.tag.write")
        body = recipes.build_trigger(
            payload.name,
            payload.kind,
            url_contains=payload.url_contains,
            event_name=payload.event_name,
            selector=payload.selector,
            visible_percent=payload.visible_percent,
        )
        needed = recipes.required_built_ins(payload.kind, url_contains=payload.url_contains)
        async with self._open(container_pk, need=NEED_EDIT, tool="create_trigger") as (
            client,
            prep,
        ):
            workspace = await resolve_workspace_path(
                client,
                prep.row.path,
                workspace_id=workspace_id,
                choice=prep.choice,
                create=True,
            )
            await _enable_built_ins(client, workspace, needed)
            created = await client.post(f"{workspace}/triggers", body)
        await self.activity.record(
            _ENTITY,
            prep.row.id,
            "gtm.trigger_created",
            {"trigger": created.get("name"), "id": created.get("triggerId")},
        )
        return created

    async def delete_trigger(
        self, container_pk: uuid.UUID, trigger_id: str, *, workspace_id: str | None = None
    ) -> None:
        self.ctx.require("google_tag_manager.tag.write")
        async with self._open(container_pk, need=NEED_EDIT, tool="delete_trigger") as (
            client,
            prep,
        ):
            workspace = await resolve_workspace_path(
                client, prep.row.path, workspace_id=workspace_id, choice=prep.choice
            )
            if not workspace:
                raise GtmError("this container has no workspace to write in", status=None)
            await client.delete(f"{workspace}/triggers/{_id(trigger_id)}")
        await self.activity.record(_ENTITY, prep.row.id, "gtm.trigger_deleted", {"id": trigger_id})

    # --- variables ------------------------------------------------------------------------------ #

    async def create_variable(
        self,
        container_pk: uuid.UUID,
        payload: GtmVariableWrite,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        self.ctx.require("google_tag_manager.tag.write")
        body: dict[str, Any] = {
            "name": payload.name,
            "type": payload.type,
            "parameter": _params(payload.parameter),
        }
        if payload.notes:
            body["notes"] = payload.notes
        async with self._open(container_pk, need=NEED_EDIT, tool="create_variable") as (
            client,
            prep,
        ):
            workspace = await resolve_workspace_path(
                client,
                prep.row.path,
                workspace_id=workspace_id,
                choice=prep.choice,
                create=True,
            )
            created = await client.post(f"{workspace}/variables", body)
        await self.activity.record(
            _ENTITY,
            prep.row.id,
            "gtm.variable_created",
            {"variable": created.get("name"), "id": created.get("variableId")},
        )
        return created

    async def delete_variable(
        self, container_pk: uuid.UUID, variable_id: str, *, workspace_id: str | None = None
    ) -> None:
        self.ctx.require("google_tag_manager.tag.write")
        async with self._open(container_pk, need=NEED_EDIT, tool="delete_variable") as (
            client,
            prep,
        ):
            workspace = await resolve_workspace_path(
                client, prep.row.path, workspace_id=workspace_id, choice=prep.choice
            )
            if not workspace:
                raise GtmError("this container has no workspace to write in", status=None)
            await client.delete(f"{workspace}/variables/{_id(variable_id)}")
        await self.activity.record(
            _ENTITY, prep.row.id, "gtm.variable_deleted", {"id": variable_id}
        )

    # --- versions ------------------------------------------------------------------------------- #

    async def create_version(
        self, container_pk: uuid.UUID, payload: GtmVersionCreate
    ) -> dict[str, Any]:
        """Freeze a workspace into a version. Still live for nobody.

        Rides ``tag.write`` rather than earning a permission of its own: a version is the act of
        writing down what was staged, and gating it behind the publish key would leave the
        staging half unable to finish its own work.

        **An empty workspace is not a failure.** GTM answers 200 with no ``containerVersion``
        when there was nothing to freeze, and a caller that read that as success would then try
        to publish a version that does not exist.
        """
        self.ctx.require("google_tag_manager.tag.write")
        body: dict[str, Any] = {}
        if payload.name:
            body["name"] = payload.name
        if payload.notes:
            body["notes"] = payload.notes
        async with self._open(container_pk, need=NEED_VERSION, tool="create_version") as (
            client,
            prep,
        ):
            workspace = await resolve_workspace_path(
                client,
                prep.row.path,
                workspace_id=payload.workspace_id,
                choice=prep.choice,
                create=True,
            )
            answer = await client.post(f"{workspace}:create_version", body)
        version = answer.get("containerVersion") or {}
        result = {
            "version_id": str(version.get("containerVersionId") or "") or None,
            "name": str(version.get("name") or payload.name or ""),
            "compiler_error": bool(answer.get("compilerError") or False),
            "empty": not version,
            "sync_conflicts": len((answer.get("syncStatus") or {}).get("mergeConflict") or []),
        }
        if result["version_id"]:
            await self.activity.record(
                _ENTITY,
                prep.row.id,
                "gtm.version_created",
                {"version": result["version_id"], "name": result["name"]},
            )
        return result

    async def publish(self, container_pk: uuid.UUID, version_id: str) -> dict[str, Any]:
        """Make a version live on the client's website. The one act here with an audience.

        Its own permission, its own OAuth scope, and its own line in the trail — and the
        container row's ``live_version_id`` is updated in the same transaction, because a screen
        that still says the old version is live after a successful publish is a screen nobody
        trusts twice.
        """
        self.ctx.require("google_tag_manager.version.publish")
        version = _id(version_id)
        async with self._open(container_pk, need=NEED_PUBLISH, tool="publish") as (client, prep):
            answer = await client.post(f"{prep.row.path}/versions/{version}:publish")
        published = answer.get("containerVersion") or {}
        live_id = str(published.get("containerVersionId") or version)
        prep.row.live_version_id = live_id
        prep.row.live_version_name = str(published.get("name") or "") or prep.row.live_version_name
        if published.get("tag") is not None:
            prep.row.tag_count = len(published.get("tag") or [])
            prep.row.trigger_count = len(published.get("trigger") or [])
            prep.row.variable_count = len(published.get("variable") or [])
        prep.row.observed_at = datetime.now(UTC)
        await self.ctx.session.flush()
        await self.activity.record(
            _ENTITY,
            prep.row.id,
            "gtm.version_published",
            {"version": live_id, "name": prep.row.live_version_name},
        )
        await self._mark_conversions_live(prep.row, live_id)
        return {
            "version_id": live_id,
            "name": prep.row.live_version_name or "",
            "compiler_error": bool(answer.get("compilerError") or False),
            "live_version_id": live_id,
        }

    async def _mark_conversions_live(self, row: GtmContainer, version_id: str) -> None:
        """A publish is what turns every draft conversion in this container into a live one.

        Per container rather than per conversion, because a version is a snapshot of the whole
        workspace: publishing does not pick which staged conversions to carry, so neither may we.
        """
        stmt = (
            self.ctx.repo(GtmConversion)
            .scoped_select()
            .where(
                GtmConversion.container_id == row.id,
                GtmConversion.status == GtmConversionStatus.DRAFT.value,
            )
        )
        for conversion in (await self.ctx.session.scalars(stmt)).all():
            conversion.status = GtmConversionStatus.LIVE.value
            conversion.published_version_id = version_id
            conversion.observed_at = datetime.now(UTC)
        await self.ctx.session.flush()

    # --- the conversion recipe -------------------------------------------------------------- #

    async def create_conversion(
        self, container_pk: uuid.UUID, payload: GtmConversionCreate
    ) -> GtmConversion:
        """Trigger + tag + the record that they belong together, in one call.

        The record is the part Google has nowhere to keep (see :class:`~.models.GtmConversion`).
        The order is: refuse a duplicate *first* (so a second attempt costs no Google calls and
        leaves no orphan tag), then build, then write the row.
        """
        self.ctx.require("google_tag_manager.tag.write")
        kind = payload.kind
        if kind not in {k.value for k in GtmConversionKind}:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"kind": "errors.gtm_conversion_kind_unknown"},
            )
        if payload.trigger is None and not payload.trigger_id:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"trigger": "errors.gtm_trigger_required"},
            )

        key = conversion_key(payload.name)
        row = await self.service.get_container(container_pk)
        existing = await self.ctx.session.scalar(
            self.ctx.repo(GtmConversion)
            .scoped_select()
            .where(GtmConversion.container_id == row.id, GtmConversion.key == key)
        )
        if existing is not None:
            # A uniqueness the database guarantees is a refusal the service owes (#377): letting
            # this reach the index answers 500 for what is really "you already set that one up".
            raise AppError("gtm_conversion_exists", "errors.gtm_conversion_exists", status_code=409)

        trigger_body = None
        needed: tuple[str, ...] = ()
        if payload.trigger is not None:
            trigger_body = recipes.build_trigger(
                f"{payload.name} (trigger)",
                payload.trigger.kind,
                url_contains=payload.trigger.url_contains,
                event_name=payload.trigger.event_name,
                selector=payload.trigger.selector,
                visible_percent=payload.trigger.visible_percent,
            )
            needed = recipes.required_built_ins(
                payload.trigger.kind, url_contains=payload.trigger.url_contains
            )

        async with self._open(container_pk, need=NEED_EDIT, tool="conversion") as (client, prep):
            workspace = await resolve_workspace_path(
                client, prep.row.path, choice=prep.choice, create=True
            )
            await _enable_built_ins(client, workspace, needed)
            if trigger_body is not None:
                created_trigger = await client.post(f"{workspace}/triggers", trigger_body)
                trigger_id = str(created_trigger.get("triggerId") or "")
            else:
                trigger_id = _id(payload.trigger_id or "")

            if kind == GtmConversionKind.GA4_EVENT:
                tag_body = recipes.build_ga4_event_tag(
                    payload.name,
                    event_name=payload.event_name or "",
                    measurement_id=payload.measurement_id or "",
                    firing_trigger_ids=[trigger_id],
                )
            else:
                tag_body = recipes.build_ads_conversion_tag(
                    payload.name,
                    conversion_id=payload.conversion_id or "",
                    conversion_label=payload.conversion_label or "",
                    firing_trigger_ids=[trigger_id],
                    conversion_value=payload.conversion_value,
                    currency_code=payload.currency_code,
                )
            created_tag = await client.post(f"{workspace}/tags", tag_body)

        conversion = GtmConversion(
            org_id=self.ctx.org.id,
            container_id=row.id,
            name=payload.name.strip(),
            key=key,
            kind=kind,
            config=payload.model_dump(exclude_none=True, mode="json"),
            workspace_id=workspace.rsplit("/", 1)[-1],
            trigger_id=trigger_id or None,
            tag_id=str(created_tag.get("tagId") or "") or None,
            status=GtmConversionStatus.DRAFT.value,
            observed_at=datetime.now(UTC),
            created_by_user_id=getattr(self.ctx.user, "id", None),
            # Snapshotted, not joined (§16): "who put this tag on the client's site" is asked
            # months later, and an actor that evaporates with the account is not an answer.
            created_by_name=str(
                getattr(self.ctx.user, "full_name", None)
                or getattr(self.ctx.user, "email", "")
                or ""
            ),
        )
        self.ctx.session.add(conversion)
        await self.ctx.session.flush()
        await self.activity.record_created(
            _CONVERSION_ENTITY,
            conversion.id,
            {"name": conversion.name, "kind": kind, "container": str(row.id)},
        )
        return conversion


async def _enable_built_ins(client: GtmClient, workspace: str, types: tuple[str, ...]) -> None:
    """Switch on the built-in variables a trigger reads, if they are not on already.

    Best-effort on purpose: GTM refuses a built-in that already exists, and every container in
    the world already has ``Page URL``. Failing the whole write for "that one was already on"
    would make the commonest case the broken one — while *not* asking at all is the silent
    failure this exists to prevent, since a trigger reading a variable that does not exist is
    stored happily and fires never.
    """
    if not types or not workspace:
        return
    try:
        await client.post(f"{workspace}/built_in_variables", None, params={"type": list(types)})
    except GtmError:
        return


def _id(raw: str) -> str:
    """A GTM numeric id, or a refusal. Ids arrive from outside and end up in a URL path."""
    value = str(raw or "").strip()
    if not value.isdigit():
        raise AppError(
            "validation", "errors.validation", status_code=422, fields={"id": "errors.validation"}
        )
    return value
