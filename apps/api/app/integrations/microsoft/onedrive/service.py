"""OneDrive service: browse-as-the-viewer, links CRUD, upload sessions, folder provisioning.

The Drive rules (docs/GOOGLE.md §5, issue #21) hold here unchanged, because none of them was
ever about Google:

- **Permissions are the provider's, not ours.** Listing and metadata reads always act as the
  *viewing* user's connection — never a privileged identity that would leak files across the
  agency. A viewer who cannot see an item in OneDrive does not see it here.
- **Unlink never deletes.** Deleting an ``onedrive_link`` removes the reference, and no code
  path reached from ``delete_link`` touches Graph. Removing the *item* is a second, separate
  act with its own route and its own dialog (:meth:`trash_item`).
- **A record's folder is a stored decision** (``OneDriveLink.is_root``). Giving a record its
  first folder is ``microsoft.onedrive.write``; re-pointing or detaching one is
  ``microsoft.onedrive.manage``. The route declares the base key and the service refines on
  the row (CLAUDE.md §15's two layers).
- **Graph's own account of a refusal is the diagnosis**, so every round trip runs inside
  :meth:`OneDriveService._call`, which reads ``error.code`` (``describe_api_error``), logs it
  beside the app registration in use, and raises a key that states the fix.

What *is* Graph's: an item is addressed by **two** ids — the drive it lives in and the item —
so every link, every route and every cache key carries the pair. The agency's client folders
live in a SharePoint document library (the Shared Drive analogue) or in the automation
account's OneDrive; ``onedrive_drive_id`` names which, and without it nothing can be
provisioned, because "the drive" is not a question Graph answers by itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.activity import ActivityService
from app.core.cache import get_redis
from app.core.models import Org
from app.core.scope import entity_visible
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.microsoft.client import (
    acting_as,
    active_connection_or_409,
    connection_for,
    describe_api_error,
    mark_connection_error,
    registration_hint,
)
from app.integrations.microsoft.models import (
    ConnectionStatus,
    MicrosoftConnection,
    MicrosoftSettings,
)
from app.integrations.microsoft.oauth import microsoft_settings_row, missing_files_scope
from app.integrations.microsoft.onedrive.models import (
    ONEDRIVE_ENTITY_TYPES,
    FolderJobStatus,
    OneDriveFolderJob,
    OneDriveLink,
)

logger = logging.getLogger("schakl.microsoft.onedrive")

#: Graph's item id for a drive's root folder, and the only item id that is addressed by name.
ROOT_ITEM = "root"

#: Listings are live-as-the-viewer with a short Redis cache — snappy, Graph authoritative.
BROWSE_CACHE_TTL = 45
_ITEM_FIELDS = "id,name,file,folder,webUrl,lastModifiedDateTime,size,parentReference"
#: One page, and the response says so when there is a second (``truncated``).
BROWSE_PAGE_SIZE = 100
MAX_BROWSE_QUERY = 100

_ENTITY_TABLES = {"company": "companies", "project": "projects", "task": "tasks"}
_ENTITY_NAME_COLUMNS = {"company": "name", "project": "name", "task": "title"}


def _item_path(drive_id: str, item_id: str) -> str:
    """The one place a Graph item address is spelled: ``/drives/{d}/items/{id}``, except the
    root, which Graph names rather than numbers."""
    if item_id == ROOT_ITEM:
        return f"/drives/{drive_id}/root"
    return f"/drives/{drive_id}/items/{item_id}"


def _browse_cache_key(
    org_id: uuid.UUID, user_id: uuid.UUID, drive_id: str, folder: str, term: str = ""
) -> str:
    """Per user, per drive, per folder **and per search term** — the Drive rule, one id wider."""
    return f"schakl:onedrive:browse:{org_id}:{user_id}:{drive_id}:{folder}:{term}"


def _search_escape(value: str) -> str:
    """A search term inside ``search(q='…')``: OData doubles a single quote."""
    return value.replace("'", "''")


def _present_item(item: dict[str, Any], drive_id: str) -> dict[str, Any]:
    reference = item.get("parentReference") or {}
    return {
        "id": item.get("id"),
        "drive_id": reference.get("driveId") or drive_id,
        "name": item.get("name"),
        "mime_type": (item.get("file") or {}).get("mimeType"),
        "is_folder": "folder" in item,
        "web_view_link": item.get("webUrl"),
        "modified_at": item.get("lastModifiedDateTime"),
        "size": int(item["size"]) if item.get("size") is not None else None,
    }


def drive_root(settings_row: MicrosoftSettings) -> tuple[str, str] | None:
    """Where new work lands when no explicit folder is given: the configured parent folder in
    the configured drive, else that drive's root. ``None`` means OneDrive is genuinely
    unconfigured — a drive id is not optional, because Graph has no "the shared drive": a
    SharePoint library and a person's OneDrive are both just drives."""
    if not settings_row.onedrive_drive_id:
        return None
    return (settings_row.onedrive_drive_id, settings_row.onedrive_parent_folder_id or ROOT_ITEM)


class OneDriveService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    async def _settings(self) -> MicrosoftSettings:
        row = await microsoft_settings_row(self.ctx.session, self._org_id)
        if row is None or not row.onedrive_enabled:
            raise AppError(
                "microsoft_onedrive_disabled",
                "errors.microsoft_onedrive_disabled",
                status_code=409,
            )
        return row

    async def _connection(self) -> MicrosoftConnection:
        """The viewer's connection, refused up front when it provably lacks the files scope.

        ``active`` only says the *grant* still works: a connection made for the calendar before
        OneDrive was switched on is perfectly healthy and answers 403 to every call in this
        file. That is a reconnect, and saying so here costs no round-trip.
        """
        connection = await active_connection_or_409(
            self.ctx.session, self._org_id, self.ctx.user.id
        )
        if missing_files_scope(connection.scopes):
            raise AppError(
                "microsoft_onedrive_scope_missing",
                "errors.microsoft_onedrive_scope_missing",
                status_code=409,
            )
        return connection

    @asynccontextmanager
    async def _call(
        self, *, forbidden_code: str = "microsoft_onedrive_forbidden"
    ) -> AsyncIterator[None]:
        """Wrap Graph round-trips so a refusal arrives as its reason, not as a 500.

        ``forbidden_code`` names the key Graph's own 401/403 answers with: *may not open* and
        *may not delete* have different cures and different people who grant them.
        """
        try:
            yield
        except httpx.HTTPStatusError as exc:
            raise await self._translate(exc, forbidden_code) from exc

    async def _translate(
        self, exc: httpx.HTTPStatusError, forbidden_code: str = "microsoft_onedrive_forbidden"
    ) -> AppError:
        detail = describe_api_error(exc)
        hint = await registration_hint(self.ctx.session, self._org_id)
        logger.warning("OneDrive call refused (%s): %s", hint, detail or exc)
        if detail is not None:
            if detail.scope_insufficient:
                return AppError(
                    "microsoft_onedrive_scope_missing",
                    "errors.microsoft_onedrive_scope_missing",
                    status_code=409,
                )
            if detail.forbidden:
                return AppError(forbidden_code, f"errors.{forbidden_code}", status_code=409)
            if detail.not_found:
                return AppError("not_found", "errors.not_found", status_code=404)
        return AppError(
            "microsoft_onedrive_unavailable",
            "errors.microsoft_onedrive_unavailable",
            status_code=502,
        )

    # --- browse (as the viewing user) ------------------------------------------- #
    async def browse(
        self,
        drive_id: str | None,
        folder_id: str | None,
        *,
        q: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """One folder's children, optionally searched **at Graph** (#336's rule).

        The listing is one capped page, so a filter over ``items`` in the browser would answer
        "nothing found" for a file that is merely 101st. Graph's ``search`` covers the folder's
        whole *subtree* rather than the folder alone, which is stated on the response so the
        screen names what it is showing rather than presenting a filtered set as the folder.
        """
        settings_row = await self._settings()
        if drive_id and folder_id:
            target = (drive_id, folder_id)
        elif drive_id:
            target = (drive_id, ROOT_ITEM)
        else:
            target = drive_root(settings_row)
        if target is None:
            raise AppError(
                "microsoft_onedrive_no_folder",
                "errors.microsoft_onedrive_no_folder",
                status_code=409,
            )
        connection = await self._connection()
        drive, item = target

        term = (q or "").strip()[:MAX_BROWSE_QUERY]
        cache_key = _browse_cache_key(self._org_id, self.ctx.user.id, drive, item, term)
        if not refresh:
            try:
                cached = await get_redis().get(cache_key)
            except Exception:  # noqa: BLE001 — a cold cache, not an error
                cached = None
            if cached:
                return json.loads(cached)

        base = _item_path(drive, item)
        if term:
            listing_url = f"{base}/search(q='{_search_escape(term)}')"
            params: dict[str, str] = {"$select": _ITEM_FIELDS, "$top": str(BROWSE_PAGE_SIZE)}
        else:
            listing_url = f"{base}/children"
            params = {
                "$select": _ITEM_FIELDS,
                "$top": str(BROWSE_PAGE_SIZE),
                "$orderby": "name",
            }
        # Graph round-trips run with the pool connection released (docs/PERFORMANCE.md).
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.get(listing_url, params=params)
                response.raise_for_status()
                body = response.json()
                folder_meta = await client.get(
                    base, params={"$select": "id,name,webUrl,parentReference"}
                )
                folder_meta.raise_for_status()
                meta = folder_meta.json()

        items = [_present_item(entry, drive) for entry in body.get("value", [])]
        # Graph orders folders and files together; a browser wants the folders first, the way
        # every file manager draws them, and the name order within each half kept.
        items.sort(key=lambda entry: (not entry["is_folder"], (entry["name"] or "").lower()))
        listing = {
            "folder": {
                "id": meta.get("id"),
                "drive_id": (meta.get("parentReference") or {}).get("driveId") or drive,
                "name": meta.get("name"),
                "web_view_link": meta.get("webUrl"),
            },
            "items": items,
            #: What produced this list, so the screen can say so in words.
            "query": term or None,
            #: There is a page two we do not follow. Stated, never swallowed.
            "truncated": bool(body.get("@odata.nextLink")),
        }
        try:
            await get_redis().set(cache_key, json.dumps(listing), ex=BROWSE_CACHE_TTL)
        except Exception:  # noqa: BLE001 — Redis down just means no cache
            pass
        return listing

    # --- links ------------------------------------------------------------------- #
    async def count_links(self, entity_type: str, entity_id: uuid.UUID) -> int:
        return int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(OneDriveLink)
                .where(
                    OneDriveLink.org_id == self._org_id,
                    OneDriveLink.entity_type == entity_type,
                    OneDriveLink.entity_id == entity_id,
                )
            )
            or 0
        )

    async def links_for(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        *,
        rollup: bool = False,
        limit: int | None = None,
    ) -> list[OneDriveLink]:
        await self._require_visible(entity_type, entity_id)
        stmt = (
            select(OneDriveLink)
            .where(
                OneDriveLink.org_id == self._org_id,
                OneDriveLink.entity_type == entity_type,
                OneDriveLink.entity_id == entity_id,
            )
            # The record's own folder first, then oldest: callers read "the folder" off the head.
            .order_by(OneDriveLink.is_root.desc(), OneDriveLink.created_at, OneDriveLink.id)
        )
        if limit is not None and not rollup:
            stmt = stmt.limit(limit)
        rows = list((await self.ctx.session.execute(stmt)).scalars().all())
        if rollup and entity_type == "project":
            # A file linked to a task surfaces on its project too — query-time roll-up, no
            # duplicate rows. Bare-table lookup, never a tasks-module import (§6).
            task_ids = [
                row[0]
                for row in await self.ctx.session.execute(
                    text("SELECT id FROM tasks WHERE project_id = :pid AND org_id = :oid"),
                    {"pid": entity_id, "oid": self._org_id},
                )
            ]
            if task_ids:
                rows.extend(
                    (
                        await self.ctx.session.execute(
                            select(OneDriveLink).where(
                                OneDriveLink.org_id == self._org_id,
                                OneDriveLink.entity_type == "task",
                                OneDriveLink.entity_id.in_(task_ids),
                            )
                        )
                    ).scalars()
                )
        return rows

    async def _item_meta(
        self, connection: MicrosoftConnection, drive_id: str, item_id: str, *, fields: str
    ) -> dict[str, Any]:
        """One item's metadata as the caller — authoritative, and it proves they can see it."""
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.get(
                    _item_path(drive_id, item_id), params={"$select": fields}
                )
                if response.status_code == 404:
                    raise AppError("not_found", "errors.not_found", status_code=404)
                response.raise_for_status()
                return response.json()

    async def create_link(
        self, entity_type: str, entity_id: uuid.UUID, drive_id: str, item_id: str
    ) -> OneDriveLink:
        self.ctx.require("microsoft.onedrive.write")
        # The record comes first: a record this caller cannot see answers 404 whatever the
        # module's own configuration happens to be.
        if entity_type not in ONEDRIVE_ENTITY_TYPES:
            raise AppError("validation", "errors.validation", status_code=422)
        await self._ensure_entity(entity_type, entity_id)
        await self._settings()
        connection = await self._connection()
        meta = await self._item_meta(
            connection, drive_id, item_id, fields="id,name,file,folder,webUrl,parentReference"
        )
        resolved_drive = (meta.get("parentReference") or {}).get("driveId") or drive_id
        existing = await self.ctx.session.scalar(
            select(OneDriveLink).where(
                OneDriveLink.org_id == self._org_id,
                OneDriveLink.entity_type == entity_type,
                OneDriveLink.entity_id == entity_id,
                OneDriveLink.drive_id == resolved_drive,
                OneDriveLink.item_id == meta["id"],
            )
        )
        if existing is not None:
            return existing
        is_folder = "folder" in meta
        # A folder linked to a record that has none becomes that record's folder — filling an
        # empty slot is ordinary write work; replacing one goes through ``set_folder``.
        claim_root = is_folder and (await self.root_link(entity_type, entity_id)) is None
        link = OneDriveLink(
            org_id=self._org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            drive_id=resolved_drive[:256],
            item_id=meta["id"][:256],
            web_url=(meta.get("webUrl") or "")[:1000],
            name=(meta.get("name") or "")[:500],
            mime_type=((meta.get("file") or {}).get("mimeType") or "")[:255] or None,
            is_folder=is_folder,
            is_root=claim_root,
            created_by_user_id=self.ctx.user.id,
            created_by_name=self.ctx.user.full_name or self.ctx.user.email,
        )
        self.ctx.session.add(link)
        await self.ctx.session.flush()
        if claim_root:
            await self._record_folder(entity_type, entity_id, "onedrive.folder_set", link.name)
        return link

    async def delete_link(self, link_id: uuid.UUID) -> None:
        """Unlink. Never — under any code path — a Graph delete."""
        self.ctx.require("microsoft.onedrive.write")
        link = await self.ctx.session.scalar(
            select(OneDriveLink).where(
                OneDriveLink.org_id == self._org_id, OneDriveLink.id == link_id
            )
        )
        if link is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await self._require_visible(link.entity_type, link.entity_id)
        if link.is_root:
            # Detaching the record's folder is the same act as re-pointing it.
            self.ctx.require("microsoft.onedrive.manage")
            await self._record_folder(
                link.entity_type, link.entity_id, "onedrive.folder_cleared", link.name
            )
        await self.ctx.session.delete(link)
        await self.ctx.session.flush()

    # --- recycle an item (the other half of unlink) --------------------------------- #
    async def trash_item(self, drive_id: str, item_id: str) -> int:
        """Move an item to the drive's recycle bin and drop every link that named it. Org-wide.

        Graph's ``DELETE`` on a drive item *is* the recycle bin — recoverable by the drive's
        owner for the library's retention period — so this never purges. It runs as the viewing
        user (Graph's permissions are the whole safety property), every ``onedrive_links`` row
        for the pair goes with it in one transaction, and a folder is refused unless empty,
        checked before anything is written. Returns how many links were removed.
        """
        self.ctx.require("microsoft.onedrive.write")
        await self._settings()
        # Read the links *before* the round-trip: binning a record's own folder is strictly the
        # larger act than detaching it, so it cannot ask for less than ``manage``.
        links = await self._links_naming(drive_id, item_id)
        if any(link.is_root for link in links):
            self.ctx.require("microsoft.onedrive.manage")

        connection = await self._connection()
        base = _item_path(drive_id, item_id)
        async with self._call(forbidden_code="microsoft_onedrive_delete_forbidden"):
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.get(
                    base, params={"$select": "id,name,file,folder,parentReference"}
                )
                if response.status_code == 404:
                    raise AppError("not_found", "errors.not_found", status_code=404)
                response.raise_for_status()
                meta = response.json()
                if "folder" in meta:
                    # One row is enough to refuse: this asks "is it empty", not "how full".
                    children = await client.get(
                        f"{base}/children", params={"$top": "1", "$select": "id"}
                    )
                    children.raise_for_status()
                    if children.json().get("value"):
                        raise AppError(
                            "microsoft_onedrive_folder_not_empty",
                            "errors.microsoft_onedrive_folder_not_empty",
                            status_code=409,
                        )
                deleted = await client.delete(base)
                if deleted.status_code != 404:
                    # Already gone is not a failure: the links still have to go.
                    deleted.raise_for_status()

        name = (meta.get("name") or "")[:500]
        # Re-read: ``release_db`` committed, so the rows loaded above belong to a finished
        # transaction. Org-wide by design.
        links = await self._links_naming(drive_id, item_id)
        for link in links:
            if link.is_root:
                await self._record_folder(
                    link.entity_type, link.entity_id, "onedrive.folder_cleared", link.name
                )
            await ActivityService(self.ctx).record(
                link.entity_type,
                link.entity_id,
                "onedrive.file_trashed",
                {"name": link.name or name},
            )
            await self.ctx.session.delete(link)
        await self.ctx.session.flush()

        # The viewer's cached listing of the folder it sat in still shows it.
        parent = (meta.get("parentReference") or {}).get("id")
        if parent:
            try:
                await get_redis().delete(
                    _browse_cache_key(self._org_id, self.ctx.user.id, drive_id, str(parent))
                )
            except Exception:  # noqa: BLE001 — Redis down just means the ~45 s TTL applies
                pass
        return len(links)

    async def _links_naming(self, drive_id: str, item_id: str) -> list[OneDriveLink]:
        """Every link in this org pointing at one item (CLAUDE.md §5: org-scoped)."""
        return list(
            (
                await self.ctx.session.execute(
                    select(OneDriveLink).where(
                        OneDriveLink.org_id == self._org_id,
                        OneDriveLink.drive_id == drive_id,
                        OneDriveLink.item_id == item_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    # --- the record's own folder ---------------------------------------------------- #
    async def root_link(self, entity_type: str, entity_id: uuid.UUID) -> OneDriveLink | None:
        return await self.ctx.session.scalar(
            select(OneDriveLink).where(
                OneDriveLink.org_id == self._org_id,
                OneDriveLink.entity_type == entity_type,
                OneDriveLink.entity_id == entity_id,
                OneDriveLink.is_root,
            )
        )

    async def set_folder(
        self, entity_type: str, entity_id: uuid.UUID, drive_id: str, item_id: str
    ) -> OneDriveLink:
        """Point a record at an **existing** folder — the picker's target. Replacing one
        additionally requires ``microsoft.onedrive.manage`` (module docstring)."""
        self.ctx.require("microsoft.onedrive.write")
        if entity_type not in ONEDRIVE_ENTITY_TYPES:
            raise AppError("validation", "errors.validation", status_code=422)
        await self._ensure_entity(entity_type, entity_id)
        await self._settings()
        current = await self.root_link(entity_type, entity_id)
        if current is not None and current.drive_id == drive_id and current.item_id == item_id:
            return current
        if current is not None:
            self.ctx.require("microsoft.onedrive.manage")

        connection = await self._connection()
        meta = await self._item_meta(
            connection, drive_id, item_id, fields="id,name,file,folder,webUrl,parentReference"
        )
        if "folder" not in meta:
            raise AppError(
                "microsoft_onedrive_not_a_folder",
                "errors.microsoft_onedrive_not_a_folder",
                status_code=422,
                fields={"item_id": "errors.microsoft_onedrive_not_a_folder"},
            )
        resolved_drive = (meta.get("parentReference") or {}).get("driveId") or drive_id

        previous_name = current.name if current is not None else None
        if current is not None:
            await self.ctx.session.delete(current)
            await self.ctx.session.flush()

        # Already attached as an ordinary link? Promote it rather than duplicate the row.
        link = await self.ctx.session.scalar(
            select(OneDriveLink).where(
                OneDriveLink.org_id == self._org_id,
                OneDriveLink.entity_type == entity_type,
                OneDriveLink.entity_id == entity_id,
                OneDriveLink.drive_id == resolved_drive,
                OneDriveLink.item_id == meta["id"],
            )
        )
        if link is None:
            link = OneDriveLink(
                org_id=self._org_id,
                entity_type=entity_type,
                entity_id=entity_id,
                drive_id=resolved_drive[:256],
                item_id=meta["id"][:256],
                created_by_user_id=self.ctx.user.id,
                created_by_name=self.ctx.user.full_name or self.ctx.user.email,
            )
            self.ctx.session.add(link)
        link.web_url = (meta.get("webUrl") or "")[:1000]
        link.name = (meta.get("name") or "")[:500]
        link.mime_type = None
        link.is_folder = True
        link.is_root = True
        await self.ctx.session.flush()

        if previous_name is None:
            await self._record_folder(entity_type, entity_id, "onedrive.folder_set", link.name)
        else:
            await ActivityService(self.ctx).record(
                entity_type,
                entity_id,
                "onedrive.folder_changed",
                {"from": previous_name, "to": link.name},
            )
        return link

    async def _record_folder(
        self, entity_type: str, entity_id: uuid.UUID, action: str, name: str
    ) -> None:
        """One trail line, in the writing transaction (CLAUDE.md §16)."""
        await ActivityService(self.ctx).record(entity_type, entity_id, action, {"name": name})

    # --- upload session: bytes go browser → Graph, never through this API ---------- #
    async def upload_session(
        self, drive_id: str, folder_id: str, name: str, mime_type: str | None
    ) -> str:
        """Mint a resumable upload session. Graph's ``uploadUrl`` is pre-authenticated, so the
        browser PUTs the bytes straight there (with ``Content-Range``); ``mime_type`` is not
        Graph's to be told — it derives the type from the name and the bytes."""
        self.ctx.require("microsoft.onedrive.write")
        await self._settings()
        cleaned = name.strip()
        if not cleaned:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"name": "errors.required"},
            )
        connection = await self._connection()
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.post(
                    f"{_item_path(drive_id, folder_id)}:/{cleaned}:/createUploadSession",
                    json={
                        "item": {
                            "@microsoft.graph.conflictBehavior": "rename",
                            "name": cleaned,
                        }
                    },
                )
                response.raise_for_status()
                upload_url = (response.json() or {}).get("uploadUrl")
        if not upload_url:
            raise AppError(
                "microsoft_upload_failed", "errors.microsoft_upload_failed", status_code=502
            )
        return upload_url

    # --- create a subfolder while browsing (as the viewing user) ------------------- #
    async def create_folder(self, drive_id: str, parent_id: str, name: str) -> dict[str, Any]:
        """Create ``name`` inside ``parent_id`` — the browser's "New folder". Acts as the
        viewer, name-matches first so re-typing an existing name links to it rather than
        duplicating (issue #21's "link, don't duplicate")."""
        self.ctx.require("microsoft.onedrive.write")
        await self._settings()
        cleaned = name.strip()
        if not cleaned:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"name": "errors.required"},
            )
        connection = await self._connection()
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                folder = await _find_or_create_folder(client, drive_id, parent_id, cleaned)
        # Bust this viewer's cached *listing* of the parent so the new folder appears at once.
        try:
            await get_redis().delete(
                _browse_cache_key(self._org_id, self.ctx.user.id, drive_id, parent_id)
            )
        except Exception:  # noqa: BLE001 — Redis down just means the ~45 s TTL applies
            pass
        return {
            "id": folder["id"],
            "drive_id": (folder.get("parentReference") or {}).get("driveId") or drive_id,
            "name": folder.get("name", cleaned),
            "web_view_link": folder.get("webUrl"),
        }

    # --- provisioning -------------------------------------------------------------- #
    async def provision_state(
        self, entity_type: str | None = None, entity_id: uuid.UUID | None = None
    ) -> dict:
        """What the entity panels need before they draw a provision control (#444): a button
        drawn on less than everything the provision 409s on can only refuse."""
        row = await microsoft_settings_row(self.ctx.session, self._org_id)
        enabled = bool(row is not None and row.onedrive_enabled)
        connection = await connection_for(self.ctx.session, self._org_id, self.ctx.user.id)
        state: dict = {
            "enabled": enabled,
            "viewer_connected": bool(
                connection and connection.status == ConnectionStatus.ACTIVE.value
            ),
            "can_provision": bool(
                enabled
                and row is not None
                and row.automation_connection_user_id
                and drive_root(row)
                and self.ctx.can("microsoft.onedrive.write")
            ),
            "job_status": None,
            "job_error": None,
        }
        if entity_type is not None and entity_id is not None:
            await self._require_visible(entity_type, entity_id)
            job = await self.ctx.session.scalar(
                select(OneDriveFolderJob).where(
                    OneDriveFolderJob.org_id == self._org_id,
                    OneDriveFolderJob.entity_type == entity_type,
                    OneDriveFolderJob.entity_id == entity_id,
                )
            )
            if job is not None and job.status in (
                FolderJobStatus.PENDING.value,
                FolderJobStatus.FAILED.value,
            ):
                state["job_status"] = job.status
                # Graph's own sentence — an admin-facing read, never an i18n key (§9).
                state["job_error"] = job.last_error
        return state

    async def request_provision(self, entity_type: str, entity_id: uuid.UUID) -> None:
        """Queue one entity's folder (the panel's "create folder" button)."""
        self.ctx.require("microsoft.onedrive.write")
        settings_row = await self._settings()
        if not settings_row.automation_connection_user_id:
            raise AppError(
                "microsoft_no_automation_connection",
                "errors.microsoft_no_automation_connection",
                status_code=409,
            )
        if drive_root(settings_row) is None:
            # Fail here, visibly — a queued job the worker can only skip is a phantom 202.
            raise AppError(
                "microsoft_onedrive_no_folder",
                "errors.microsoft_onedrive_no_folder",
                status_code=409,
            )
        await self._require_visible(entity_type, entity_id)
        if await self.root_link(entity_type, entity_id) is not None:
            raise AppError(
                "microsoft_onedrive_folder_exists",
                "errors.microsoft_onedrive_folder_exists",
                status_code=409,
            )
        name = await self._entity_name(entity_type, entity_id)
        if name is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        parent_type, parent_id = await self._provision_parent(entity_type, entity_id)
        await queue_folder_job(
            self.ctx.session,
            self._org_id,
            entity_type,
            entity_id,
            name,
            parent_entity_id=parent_id,
            parent_entity_type=parent_type,
        )

    async def _provision_parent(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> tuple[str | None, uuid.UUID | None]:
        """Which record the new folder nests under — resolved here, found at execution time.
        Bare-table lookups, never an import of another module's internals (§6)."""
        if entity_type == "project":
            company_id = await self.ctx.session.scalar(
                text("SELECT company_id FROM projects WHERE id = :pid AND org_id = :oid"),
                {"pid": entity_id, "oid": self._org_id},
            )
            return ("company", company_id) if company_id else (None, None)
        if entity_type == "task":
            row = (
                await self.ctx.session.execute(
                    text(
                        "SELECT project_id, company_id FROM tasks "
                        "WHERE id = :tid AND org_id = :oid"
                    ),
                    {"tid": entity_id, "oid": self._org_id},
                )
            ).first()
            if row is None:
                return (None, None)
            project_id, company_id = row
            if project_id:
                return ("project", project_id)
            return ("company", company_id) if company_id else (None, None)
        return (None, None)

    async def bulk_provision(self) -> int:
        """Backfill: queue a folder for every company without one. Returns the queue size."""
        self.ctx.require("microsoft.settings.manage")
        settings_row = await self._settings()
        if not settings_row.automation_connection_user_id:
            raise AppError(
                "microsoft_no_automation_connection",
                "errors.microsoft_no_automation_connection",
                status_code=409,
            )
        if drive_root(settings_row) is None:
            raise AppError(
                "microsoft_onedrive_no_folder",
                "errors.microsoft_onedrive_no_folder",
                status_code=409,
            )
        rows = await self.ctx.session.execute(
            text(
                """
                SELECT c.id, c.name FROM companies c
                WHERE c.org_id = :oid
                  AND NOT EXISTS (
                    SELECT 1 FROM onedrive_links l
                    WHERE l.org_id = :oid AND l.entity_type = 'company'
                      AND l.entity_id = c.id AND l.is_root
                  )
                """
            ),
            {"oid": self._org_id},
        )
        queued = 0
        for company_id, name in rows:
            await queue_folder_job(self.ctx.session, self._org_id, "company", company_id, name)
            queued += 1
        return queued

    # --- helpers -------------------------------------------------------------------- #
    async def _require_visible(self, entity_type: str, entity_id: uuid.UUID) -> None:
        """§15's failure mode (4): every surface here is **entity-addressed**, so holding the
        read/write key is not the same as being allowed to see *that* record."""
        if not await entity_visible(self.ctx, entity_type, entity_id):
            raise AppError("not_found", "errors.not_found", status_code=404)

    async def _ensure_entity(self, entity_type: str, entity_id: uuid.UUID) -> None:
        await self._require_visible(entity_type, entity_id)
        if await self._entity_name(entity_type, entity_id) is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"entity_id": "errors.not_found"},
            )

    async def _entity_name(self, entity_type: str, entity_id: uuid.UUID) -> str | None:
        table = _ENTITY_TABLES.get(entity_type)
        if table is None:
            return None
        column = _ENTITY_NAME_COLUMNS[entity_type]
        return await self.ctx.session.scalar(
            text(f"SELECT {column} FROM {table} WHERE id = :eid AND org_id = :oid"),  # noqa: S608 — fixed identifiers
            {"eid": entity_id, "oid": self._org_id},
        )


async def queue_folder_job(
    session: AsyncSession,
    org_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    name: str,
    parent_entity_id: uuid.UUID | None = None,
    parent_entity_type: str | None = None,
) -> OneDriveFolderJob:
    """Idempotent outbox insert + a best-effort worker offer (the sweep cron backstops)."""
    job = await session.scalar(
        select(OneDriveFolderJob).where(
            OneDriveFolderJob.org_id == org_id,
            OneDriveFolderJob.entity_type == entity_type,
            OneDriveFolderJob.entity_id == entity_id,
        )
    )
    if job is None:
        job = OneDriveFolderJob(
            org_id=org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            name=name[:500],
            parent_entity_id=parent_entity_id,
            parent_entity_type=parent_entity_type,
        )
        session.add(job)
    else:
        job.status = FolderJobStatus.PENDING.value
        job.attempts = 0
        job.last_error = None
        # A re-queued job re-resolves its parent: the record may have moved since.
        job.parent_entity_id = parent_entity_id
        job.parent_entity_type = parent_entity_type
    await session.flush()

    from datetime import timedelta

    from app.core.jobs import enqueue

    try:
        await enqueue(
            "onedrive_provision_folder",
            str(org_id),
            str(job.id),
            _defer_by=timedelta(seconds=2),
        )
    except Exception:  # noqa: BLE001 — the sweep cron re-offers pending jobs
        logger.warning("onedrive provision enqueue failed for job %s; sweep will retry", job.id)
    return job


# --------------------------------------------------------------------------- #
# Worker side — folder creation with the org's automation connection
# --------------------------------------------------------------------------- #
MAX_ATTEMPTS = 5
#: A template copy is asynchronous at Graph (``202`` + a monitor URL): how often, and how
#: long, the worker asks whether it finished. Module-level so a test can set both to zero.
COPY_POLL_ATTEMPTS = 20
COPY_POLL_SECONDS = 1.0


async def provision_folder(session: AsyncSession, org: Org, job: OneDriveFolderJob) -> None:
    if job.status != FolderJobStatus.PENDING.value:
        return
    settings_row = await microsoft_settings_row(session, org.id)
    root = drive_root(settings_row) if settings_row is not None else None
    if (
        settings_row is None
        or not settings_row.onedrive_enabled
        or not settings_row.automation_connection_user_id
        or root is None
    ):
        job.status = FolderJobStatus.SKIPPED.value
        job.last_error = "onedrive_not_configured"
        await session.flush()
        return
    connection = await connection_for(
        session, org.id, settings_row.automation_connection_user_id
    )
    if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
        job.status = FolderJobStatus.SKIPPED.value
        job.last_error = "automation_connection_unavailable"
        await session.flush()
        return
    if missing_files_scope(connection.scopes):
        # An automation account picked before OneDrive was switched on is ``active`` and cannot
        # make a folder. Five attempts of 403 would say the same thing five times over.
        job.status = FolderJobStatus.SKIPPED.value
        job.last_error = "automation_connection_missing_files_scope"
        await session.flush()
        return

    drive_id, root_item = root
    # A project folder nests under its client's; a task's under its project's, else its
    # client's. Resolved *now*, not at emit time: the parent may have acquired a folder while
    # this job sat in the outbox.
    parent_item = await _parent_folder_item(session, org, job, drive_id) or root_item

    try:
        async with acting_as(session, org, connection) as client:
            template = (
                settings_row.onedrive_template_folder_id
                if job.entity_type == "company"
                else None
            )
            folder = await _find_or_create_folder(
                client, drive_id, parent_item, job.name, template_id=template
            )
    except Exception as exc:
        from app.integrations.microsoft.client import is_oauth_error

        job.attempts += 1
        detail = describe_api_error(exc)
        job.last_error = str(detail or exc)[:500]
        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        if job.attempts >= MAX_ATTEMPTS:
            job.status = FolderJobStatus.FAILED.value
        logger.warning(
            "onedrive provisioning failed for job %s (attempt %s, %s): %s",
            job.id,
            job.attempts,
            await registration_hint(session, org.id),
            detail or exc,
        )
        await session.flush()
        return

    item_id = folder["id"]
    existing = await session.scalar(
        select(OneDriveLink).where(
            OneDriveLink.org_id == org.id,
            OneDriveLink.entity_type == job.entity_type,
            OneDriveLink.entity_id == job.entity_id,
            OneDriveLink.drive_id == drive_id,
            OneDriveLink.item_id == item_id,
        )
    )
    # The provisioned folder becomes the record's folder unless somebody picked one while the
    # job sat in the outbox — the worker never re-points a decision a human made.
    has_root = await session.scalar(
        select(OneDriveLink.id).where(
            OneDriveLink.org_id == org.id,
            OneDriveLink.entity_type == job.entity_type,
            OneDriveLink.entity_id == job.entity_id,
            OneDriveLink.is_root,
        )
    )
    if existing is not None:
        if has_root is None and existing.is_folder:
            existing.is_root = True
    else:
        session.add(
            OneDriveLink(
                org_id=org.id,
                entity_type=job.entity_type,
                entity_id=job.entity_id,
                drive_id=drive_id[:256],
                item_id=item_id[:256],
                web_url=(folder.get("webUrl") or "")[:1000],
                name=(folder.get("name") or job.name)[:500],
                mime_type=None,
                is_folder=True,
                is_root=has_root is None,
            )
        )
    job.status = FolderJobStatus.DONE.value
    job.last_error = None
    await session.flush()


async def _parent_folder_item(
    session: AsyncSession, org: Org, job: OneDriveFolderJob, drive_id: str
) -> str | None:
    """The item the job's new folder nests inside, or ``None`` for the configured root.

    Walks the record chain rather than reading one link: a task nests under its project's
    folder, and a project whose own folder was never provisioned is not a dead end — the
    client's folder is the next honest answer. Only a folder in the *configured* drive counts:
    a client folder somebody pointed at another library cannot parent a folder created here.
    """
    parent_id = job.parent_entity_id
    if parent_id is None:
        return None
    parent_type = job.parent_entity_type or "company"
    while parent_id is not None:
        link = await session.scalar(
            select(OneDriveLink).where(
                OneDriveLink.org_id == org.id,
                OneDriveLink.entity_type == parent_type,
                OneDriveLink.entity_id == parent_id,
                OneDriveLink.is_root,
            )
        )
        if link is not None:
            return link.item_id if link.drive_id == drive_id else None
        if parent_type != "project":
            return None
        parent_id = await session.scalar(
            text("SELECT company_id FROM projects WHERE id = :pid AND org_id = :oid"),
            {"pid": parent_id, "oid": org.id},
        )
        parent_type = "company"
    return None


async def _find_or_create_folder(
    client: Any, drive_id: str, parent_item: str, name: str, *, template_id: str | None = None
) -> dict[str, Any]:
    """Name-match under the parent first (link, don't duplicate — issue #21); else create,
    copying the template folder whole when one is configured.

    The match is Graph's path addressing (``{parent}:/{name}``): a 404 is "no such child", and
    it costs one round trip where a listing would cost a page.
    """
    lookup = await client.get(
        f"{_item_path(drive_id, parent_item)}:/{name}",
        params={"$select": "id,name,webUrl,folder,parentReference"},
    )
    if lookup.status_code != 404:
        lookup.raise_for_status()
        found = lookup.json()
        if "folder" in found:
            return found
    if template_id:
        return await _copy_template(client, drive_id, template_id, parent_item, name)
    return await _create_folder(client, drive_id, parent_item, name)


async def _create_folder(
    client: Any, drive_id: str, parent_item: str, name: str
) -> dict[str, Any]:
    response = await client.post(
        f"{_item_path(drive_id, parent_item)}/children",
        json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
    )
    response.raise_for_status()
    return response.json()


async def _copy_template(
    client: Any, drive_id: str, template_id: str, parent_item: str, name: str
) -> dict[str, Any]:
    """A template folder copied whole, under the new name — Graph copies a folder recursively,
    which is what a template *is*, and does it **asynchronously**: the ``202`` carries a monitor
    URL, and the copy is only a folder once the monitor says ``completed``. The monitor is
    fetched with a plain client on purpose: Graph refuses an ``Authorization`` header on it.
    """
    started = await client.post(
        f"{_item_path(drive_id, template_id)}/copy",
        json={"parentReference": {"driveId": drive_id, "id": parent_item}, "name": name},
    )
    started.raise_for_status()
    monitor = started.headers.get("location") or started.headers.get("Location")
    if not monitor:
        raise RuntimeError("template copy accepted without a monitor url")
    item_id: str | None = None
    async with httpx.AsyncClient(timeout=15.0) as plain:
        for _ in range(COPY_POLL_ATTEMPTS):
            await asyncio.sleep(COPY_POLL_SECONDS)
            progress = await plain.get(monitor)
            body = progress.json() if progress.status_code < 400 else {}
            status = (body.get("status") or "").lower()
            if status == "completed":
                item_id = body.get("resourceId")
                break
            if status == "failed":
                raise RuntimeError(
                    f"template copy failed: {(body.get('error') or {}).get('message') or body}"
                )
    if not item_id:
        raise RuntimeError("template copy did not finish in time")
    created = await client.get(
        _item_path(drive_id, item_id), params={"$select": "id,name,webUrl,folder,parentReference"}
    )
    created.raise_for_status()
    return created.json()
