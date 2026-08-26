"""Drive service: browse-as-the-viewer, links CRUD, resumable uploads, folder provisioning.

Two rules from docs/GOOGLE.md §5 and issue #21 govern everything here:

- **Permissions are Drive's, not ours.** Listing and metadata reads always act as the
  *viewing* user's connection — never a privileged identity that would leak files across the
  agency. A viewer who cannot see a file in Drive does not see it here.
- **Unlink never deletes.** Deleting a ``drive_link`` removes the reference, and no code path
  reached from ``delete_link`` touches Drive. Removing the *file* is a second, separate act
  with its own route and its own dialog (:meth:`trash_file`, #394) — never a rename of this
  one: collapsing them would silently bin a client's document for whoever was only tidying a
  record's attachments.

A third rule arrived with the folder picker: **a record's folder is a stored decision**
(``DriveLink.is_root``), not whichever folder link a query returned first. Giving a record its
first folder is ordinary ``google.drive.write`` work — provisioning one, or pointing a project
at its client's. **Re-pointing or detaching one is ``google.drive.manage``**, because it
silently moves where every colleague's uploads land while the history stays behind in a folder
nobody is looking at any more. The route declares the base key and the service refines on the
row, the two layers of CLAUDE.md §15.

And a fourth arrived with that picker's first 403: **Google's own account of a refusal is the
diagnosis, so it may not be discarded**. A bare ``raise_for_status()`` at a call site turned
every Drive refusal into one unhandled 500 whose traceback carries the status line and the URL
and nothing else, and *"Drive is op dit moment niet beschikbaar"* on screen — while the three
ordinary causes are each fixed somewhere different: the token was minted before Drive was
enabled (reconnect), the Drive API is off in the org's Cloud project (a Google Cloud console
visit), or the viewer simply is not a member of that shared drive. Every Drive round-trip here
now runs inside :meth:`DriveService._call`, which reads the reason out of the error body
(``describe_api_error``), logs it verbatim beside the OAuth client the call was made with
(``oauth_client_hint`` — the project it names is half the answer), and raises a key that states
the fix. The scope case is additionally refused *before* the round-trip, because the connection
row already knows (``missing_drive_scope``).
"""

from __future__ import annotations

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
from app.integrations.google.client import (
    acting_as,
    active_connection_or_409,
    connection_for,
    describe_api_error,
    mark_connection_error,
    oauth_client_hint,
)
from app.integrations.google.drive.models import (
    DRIVE_ENTITY_TYPES,
    DriveFolderJob,
    DriveLink,
    FolderJobStatus,
)
from app.integrations.google.models import ConnectionStatus, GoogleConnection, GoogleSettings
from app.integrations.google.oauth import google_settings_row, missing_drive_scope

logger = logging.getLogger("schakl.google.drive")

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

#: Listings are live-as-the-viewer with a short Redis cache — snappy, Drive authoritative.
BROWSE_CACHE_TTL = 45
_BROWSE_FIELDS = "nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime,size)"
#: One page, and the response says so when there is a second (``truncated``). A list that is
#: silently a prefix of a folder is the failure §17 names for imports — it looks like it worked.
BROWSE_PAGE_SIZE = 100
#: A search term long enough for any filename, short enough that the Drive query stays a query.
MAX_BROWSE_QUERY = 100

_ENTITY_TABLES = {"company": "companies", "project": "projects", "task": "tasks"}
_ENTITY_NAME_COLUMNS = {"company": "name", "project": "name", "task": "title"}


def _drive_query_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _browse_cache_key(org_id: uuid.UUID, user_id: uuid.UUID, folder: str, term: str = "") -> str:
    """The listing cache is per user, per folder **and per search term**.

    Without the term the two are the same entry: one person's search would be served to the
    next person opening that folder as its contents, for the length of the TTL — a filtered
    list presenting as the whole folder, which is the very lie the search exists to avoid.
    """
    return f"schakl:gdrive:browse:{org_id}:{user_id}:{folder}:{term}"


class DriveService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    async def _settings(self) -> GoogleSettings:
        row = await google_settings_row(self.ctx.session, self._org_id)
        if row is None or not row.drive_enabled:
            raise AppError(
                "google_drive_disabled", "errors.google_drive_disabled", status_code=409
            )
        return row

    async def _connection(self) -> GoogleConnection:
        """The viewer's connection, refused up front when it provably lacks Drive.

        ``active`` only says the *grant* still works: a connection made for Calendar or the
        marketing sources before Drive was switched on is perfectly healthy and answers 403 to
        every call in this file. That is a reconnect, and saying so here costs no round-trip.
        """
        connection = await active_connection_or_409(
            self.ctx.session, self._org_id, self.ctx.user.id
        )
        if missing_drive_scope(connection.scopes):
            raise AppError(
                "google_drive_scope_missing",
                "errors.google_drive_scope_missing",
                status_code=409,
            )
        return connection

    @asynccontextmanager
    async def _call(
        self, *, forbidden_code: str = "google_drive_forbidden"
    ) -> AsyncIterator[None]:
        """Wrap Drive round-trips so a refusal arrives as its reason, not as a 500.

        Enters *outside* ``acting_as`` / ``release_db()``, so by the time this handles the
        error the session holds a pool connection again and may be read.

        ``forbidden_code`` names the key Drive's own 401/403 answers with. The default reads
        as "you cannot see this folder", which is the right sentence for a read and the wrong
        one for a trash: *may not open* and *may not delete* have different cures and, in
        Drive, different people who grant them (#394).
        """
        try:
            yield
        except httpx.HTTPStatusError as exc:
            raise await self._translate(exc, forbidden_code) from exc

    async def _translate(
        self, exc: httpx.HTTPStatusError, forbidden_code: str = "google_drive_forbidden"
    ) -> AppError:
        detail = describe_api_error(exc)
        hint = await oauth_client_hint(self.ctx.session, self._org_id)
        # Verbatim, because Google's message names the Cloud project — which is the whole
        # answer when an org rides the instance env client by accident.
        logger.warning("Drive call refused (%s): %s", hint, detail or exc)
        if detail is not None:
            if detail.api_disabled:
                return AppError(
                    "google_drive_api_disabled",
                    "errors.google_drive_api_disabled",
                    status_code=409,
                )
            if detail.scope_insufficient:
                return AppError(
                    "google_drive_scope_missing",
                    "errors.google_drive_scope_missing",
                    status_code=409,
                )
            if detail.status_code in (401, 403):
                # Drive's own permission answer: this account cannot see that folder or drive.
                # Not our 403 — nothing an org admin grants in schakl changes it.
                return AppError(
                    forbidden_code,
                    f"errors.{forbidden_code}",
                    status_code=409,
                )
            if detail.status_code == 404:
                return AppError("not_found", "errors.not_found", status_code=404)
        return AppError(
            "google_drive_unavailable", "errors.google_drive_unavailable", status_code=502
        )

    # --- browse (as the viewing user) ------------------------------------------- #
    async def browse(
        self, folder_id: str | None, *, q: str | None = None, refresh: bool = False
    ) -> dict[str, Any]:
        """One folder's children, optionally filtered by name **at Drive** (#336).

        Server-side because the listing is one capped page: a filter applied in the browser
        over the first 100 rows would answer "geen resultaten" for a file that is merely 101st
        alphabetically. And the search covers this folder only — the breadcrumb is the promise
        the screen makes about where you are, and a hit three folders down carries no path.
        """
        settings_row = await self._settings()
        target = folder_id or drive_root(settings_row)
        if not target:
            raise AppError(
                "google_drive_no_folder", "errors.google_drive_no_folder", status_code=409
            )
        connection = await self._connection()

        term = (q or "").strip()[:MAX_BROWSE_QUERY]
        cache_key = _browse_cache_key(self._org_id, self.ctx.user.id, target, term)
        if not refresh:
            try:
                cached = await get_redis().get(cache_key)
            except Exception:  # noqa: BLE001 — a cold cache, not an error
                cached = None
            if cached:
                return json.loads(cached)

        # Every interpolated value is escaped, the search term included: an apostrophe typed
        # into a search box must not be able to rewrite the query it lands in.
        clauses = [f"'{_drive_query_escape(target)}' in parents", "trashed=false"]
        if term:
            clauses.insert(0, f"name contains '{_drive_query_escape(term)}'")
        params = {
            "q": " and ".join(clauses),
            "fields": _BROWSE_FIELDS,
            "orderBy": "folder,name",
            "pageSize": str(BROWSE_PAGE_SIZE),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        # Drive round-trips run with the pool connection released (docs/PERFORMANCE.md).
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.get(f"{DRIVE_API}/files", params=params)
                response.raise_for_status()
                body = response.json()
                folder_meta = await client.get(
                    f"{DRIVE_API}/files/{target}",
                    params={"fields": "id,name,webViewLink", "supportsAllDrives": "true"},
                )
                folder_meta.raise_for_status()
                meta = folder_meta.json()

        listing = {
            "folder": {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "web_view_link": meta.get("webViewLink"),
            },
            "items": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "mime_type": item.get("mimeType"),
                    "is_folder": item.get("mimeType") == FOLDER_MIME,
                    "web_view_link": item.get("webViewLink"),
                    "modified_at": item.get("modifiedTime"),
                    "size": int(item["size"]) if item.get("size") else None,
                }
                for item in body.get("files", [])
            ],
            #: What produced this list, so the screen can say so in words rather than
            #: presenting a filtered set as the folder.
            "query": term or None,
            #: There is a page two we do not follow. Stated, never swallowed.
            "truncated": bool(body.get("nextPageToken")),
        }
        try:
            await get_redis().set(cache_key, json.dumps(listing), ex=BROWSE_CACHE_TTL)
        except Exception:  # noqa: BLE001 — Redis down just means no cache
            pass
        return listing

    # --- links ------------------------------------------------------------------- #
    async def count_links(self, entity_type: str, entity_id: uuid.UUID) -> int:
        """How many files are attached here in total — what a capped panel is hiding (#407)."""
        return int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(DriveLink)
                .where(
                    DriveLink.org_id == self._org_id,
                    DriveLink.entity_type == entity_type,
                    DriveLink.entity_id == entity_id,
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
    ) -> list[DriveLink]:
        await self._require_visible(entity_type, entity_id)
        conditions = [
            DriveLink.org_id == self._org_id,
            DriveLink.entity_type == entity_type,
            DriveLink.entity_id == entity_id,
        ]
        # Ordered, not incidental: the record's own folder first, then oldest. Callers read
        # "the folder" off this list, and an unordered query made that a coin flip.
        stmt = select(DriveLink).where(*conditions).order_by(
            DriveLink.is_root.desc(), DriveLink.created_at, DriveLink.id
        )
        # A panel asks for a page and gets one (#407). The ordering already leads with the
        # record's own folder, so a cap never costs the caller the row it reads off the head.
        if limit is not None and not rollup:
            stmt = stmt.limit(limit)
        rows = list((await self.ctx.session.execute(stmt)).scalars().all())
        if rollup and entity_type == "project":
            # Issue #21: a file linked to a task surfaces on its project too — query-time
            # roll-up, no duplicate rows. Bare-table lookup, never a tasks-module import (§6).
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
                            select(DriveLink).where(
                                DriveLink.org_id == self._org_id,
                                DriveLink.entity_type == "task",
                                DriveLink.entity_id.in_(task_ids),
                            )
                        )
                    ).scalars()
                )
        return rows

    async def create_link(
        self, entity_type: str, entity_id: uuid.UUID, drive_file_id: str
    ) -> DriveLink:
        self.ctx.require("google.drive.write")
        # The record comes first: a record this caller cannot see answers 404 whatever the
        # module's own configuration happens to be.
        if entity_type not in DRIVE_ENTITY_TYPES:
            raise AppError("validation", "errors.validation", status_code=422)
        await self._ensure_entity(entity_type, entity_id)
        await self._settings()
        connection = await self._connection()
        # Metadata comes from Drive as the caller — authoritative, and it proves they can
        # actually see the file they are linking. Fetched with the pool connection
        # released (docs/PERFORMANCE.md).
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.get(
                    f"{DRIVE_API}/files/{drive_file_id}",
                    params={
                        "fields": "id,name,mimeType,webViewLink,driveId",
                        "supportsAllDrives": "true",
                    },
                )
                if response.status_code == 404:
                    raise AppError("not_found", "errors.not_found", status_code=404)
                response.raise_for_status()
                meta = response.json()

        existing = await self.ctx.session.scalar(
            select(DriveLink).where(
                DriveLink.org_id == self._org_id,
                DriveLink.entity_type == entity_type,
                DriveLink.entity_id == entity_id,
                DriveLink.drive_file_id == drive_file_id,
            )
        )
        if existing is not None:
            return existing
        is_folder = meta.get("mimeType") == FOLDER_MIME
        # A folder linked to a record that has none becomes that record's folder — this is the
        # project panel's "in klantmap werken", and filling an empty slot is ordinary write
        # work. It can only ever *fill* one: replacing a folder goes through ``set_folder``,
        # which asks for ``google.drive.manage``.
        claim_root = is_folder and (await self.root_link(entity_type, entity_id)) is None
        link = DriveLink(
            org_id=self._org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            drive_file_id=meta["id"],
            drive_url=(meta.get("webViewLink") or "")[:500],
            name=(meta.get("name") or "")[:500],
            mime_type=(meta.get("mimeType") or "")[:255] or None,
            is_folder=is_folder,
            is_root=claim_root,
            shared_drive_id=(meta.get("driveId") or "")[:128] or None,
            created_by_user_id=self.ctx.user.id,
            created_by_name=self.ctx.user.full_name or self.ctx.user.email,
        )
        self.ctx.session.add(link)
        await self.ctx.session.flush()
        if claim_root:
            await self._record_folder(entity_type, entity_id, "drive.folder_set", link.name)
        return link

    async def delete_link(self, link_id: uuid.UUID) -> None:
        """Unlink. Never — under any code path — a Drive delete (issue #21)."""
        self.ctx.require("google.drive.write")
        link = await self.ctx.session.scalar(
            select(DriveLink).where(
                DriveLink.org_id == self._org_id, DriveLink.id == link_id
            )
        )
        if link is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await self._require_visible(link.entity_type, link.entity_id)
        if link.is_root:
            # Detaching the record's folder is the same act as re-pointing it, reached from
            # the other side: everyone's browser falls back to the org root tomorrow.
            self.ctx.require("google.drive.manage")
            await self._record_folder(
                link.entity_type, link.entity_id, "drive.folder_cleared", link.name
            )
        await self.ctx.session.delete(link)
        await self.ctx.session.flush()

    # --- trash a file in Drive (the other half of unlink, #394) --------------------- #
    async def trash_file(self, drive_file_id: str) -> int:
        """Move a Drive file to Drive's bin and drop every link that named it. Org-wide.

        Four decisions, and each is the reason this is not simply ``files.delete``:

        - **Trash, never purge.** ``files.update {trashed: true}`` is recoverable by the
          document's owner for thirty days, which is the right default for a destructive act
          performed on somebody else's document from a CRM. Permanent delete is not offered.
        - **It runs as the viewing user**, exactly like :meth:`browse`. Drive's permissions are
          authoritative: a colleague who cannot delete that file in Drive is refused *by
          Google*, and schakl neither grants around it nor explains it away.
        - **Every ``drive_links`` row for that file goes with it**, for the record it was
          deleted from and for every other record that linked it, in this transaction. A link
          to a trashed file is a row that renders a name and 404s when clicked.
        - **A folder is refused unless empty.** The panel lists a client's project folder
          beside an accidental upload; a delete that silently took the folder and everything
          in it would be the worst control on the screen.

        Returns how many links were removed.
        """
        self.ctx.require("google.drive.write")
        await self._settings()

        # Read the links *before* the round-trip: whether this is somebody's record folder
        # decides who may press the button, and checking after the file is already in the bin
        # is not a check. Detaching a record's folder is ``google.drive.manage`` (docs/GOOGLE.md
        # §5) and binning it is strictly the larger act, so it cannot ask for less.
        links = await self._links_naming(drive_file_id)
        if any(link.is_root for link in links):
            self.ctx.require("google.drive.manage")

        connection = await self._connection()
        # Drive round-trips run with the pool connection released (docs/PERFORMANCE.md).
        async with self._call(forbidden_code="google_drive_delete_forbidden"):
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.get(
                    f"{DRIVE_API}/files/{drive_file_id}",
                    params={
                        "fields": "id,name,mimeType,parents,trashed",
                        "supportsAllDrives": "true",
                    },
                )
                if response.status_code == 404:
                    raise AppError("not_found", "errors.not_found", status_code=404)
                response.raise_for_status()
                meta = response.json()

                if meta.get("mimeType") == FOLDER_MIME:
                    # One row is enough to refuse: this asks "is it empty", not "how full".
                    children = await client.get(
                        f"{DRIVE_API}/files",
                        params={
                            "q": (
                                f"'{_drive_query_escape(drive_file_id)}' in parents "
                                "and trashed=false"
                            ),
                            "fields": "files(id)",
                            "pageSize": "1",
                            "supportsAllDrives": "true",
                            "includeItemsFromAllDrives": "true",
                        },
                    )
                    children.raise_for_status()
                    if children.json().get("files"):
                        raise AppError(
                            "google_drive_folder_not_empty",
                            "errors.google_drive_folder_not_empty",
                            status_code=409,
                        )

                if not meta.get("trashed"):
                    # Already-in-the-bin is not a failure: the links still have to go.
                    trashed = await client.patch(
                        f"{DRIVE_API}/files/{drive_file_id}",
                        params={"supportsAllDrives": "true", "fields": "id"},
                        json={"trashed": True},
                    )
                    trashed.raise_for_status()

        name = (meta.get("name") or "")[:500]
        # Re-read: ``release_db`` committed, so the rows loaded above belong to a finished
        # transaction. Org-wide by design — a link on a record this caller cannot open still
        # points at a file that no longer exists.
        links = await self._links_naming(drive_file_id)
        for link in links:
            if link.is_root:
                await self._record_folder(
                    link.entity_type, link.entity_id, "drive.folder_cleared", link.name
                )
            await ActivityService(self.ctx).record(
                link.entity_type,
                link.entity_id,
                "drive.file_trashed",
                {"name": link.name or name},
            )
            await self.ctx.session.delete(link)
        await self.ctx.session.flush()

        # The viewer's cached listing of the folder it sat in still shows it. Same busting the
        # browser's "new folder" does, and for the same reason.
        for parent in meta.get("parents") or []:
            try:
                await get_redis().delete(
                    _browse_cache_key(self._org_id, self.ctx.user.id, str(parent))
                )
            except Exception:  # noqa: BLE001 — Redis down just means the ~45 s TTL applies
                pass
        return len(links)

    async def _links_naming(self, drive_file_id: str) -> list[DriveLink]:
        """Every link in this org pointing at one Drive file (CLAUDE.md §5: org-scoped)."""
        return list(
            (
                await self.ctx.session.execute(
                    select(DriveLink).where(
                        DriveLink.org_id == self._org_id,
                        DriveLink.drive_file_id == drive_file_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    # --- the record's own folder ---------------------------------------------------- #
    async def root_link(self, entity_type: str, entity_id: uuid.UUID) -> DriveLink | None:
        """The record's Drive folder, or ``None``. One row by construction (partial index)."""
        return await self.ctx.session.scalar(
            select(DriveLink).where(
                DriveLink.org_id == self._org_id,
                DriveLink.entity_type == entity_type,
                DriveLink.entity_id == entity_id,
                DriveLink.is_root,
            )
        )

    async def set_folder(
        self, entity_type: str, entity_id: uuid.UUID, drive_file_id: str
    ) -> DriveLink:
        """Point a record at an **existing** Drive folder — the UI's folder picker (#21).

        Metadata is read as the caller, which is also what proves they can see the folder they
        are choosing, and refuses a file: a record's folder is a folder. Replacing an existing
        one additionally requires ``google.drive.manage`` (see the module docstring).
        """
        self.ctx.require("google.drive.write")
        # The record comes first, as in ``create_link``: a record this caller cannot see
        # answers 404 whatever the module's own configuration happens to be.
        if entity_type not in DRIVE_ENTITY_TYPES:
            raise AppError("validation", "errors.validation", status_code=422)
        await self._ensure_entity(entity_type, entity_id)
        await self._settings()
        current = await self.root_link(entity_type, entity_id)
        if current is not None and current.drive_file_id == drive_file_id:
            return current
        if current is not None:
            self.ctx.require("google.drive.manage")

        connection = await self._connection()
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.get(
                    f"{DRIVE_API}/files/{drive_file_id}",
                    params={
                        "fields": "id,name,mimeType,webViewLink,driveId",
                        "supportsAllDrives": "true",
                    },
                )
                if response.status_code == 404:
                    raise AppError("not_found", "errors.not_found", status_code=404)
                response.raise_for_status()
                meta = response.json()
        if meta.get("mimeType") != FOLDER_MIME:
            raise AppError(
                "google_drive_not_a_folder",
                "errors.google_drive_not_a_folder",
                status_code=422,
                fields={"drive_file_id": "errors.google_drive_not_a_folder"},
            )

        previous_name = current.name if current is not None else None
        if current is not None:
            # The old folder does not linger as a loose attachment nobody attached: the trail
            # keeps its name, and the Drive folder itself is untouched as always.
            await self.ctx.session.delete(current)
            await self.ctx.session.flush()

        # Already attached as an ordinary link? Promote it rather than duplicate the row.
        link = await self.ctx.session.scalar(
            select(DriveLink).where(
                DriveLink.org_id == self._org_id,
                DriveLink.entity_type == entity_type,
                DriveLink.entity_id == entity_id,
                DriveLink.drive_file_id == meta["id"],
            )
        )
        if link is None:
            link = DriveLink(
                org_id=self._org_id,
                entity_type=entity_type,
                entity_id=entity_id,
                drive_file_id=meta["id"],
                created_by_user_id=self.ctx.user.id,
                created_by_name=self.ctx.user.full_name or self.ctx.user.email,
            )
            self.ctx.session.add(link)
        link.drive_url = (meta.get("webViewLink") or "")[:500]
        link.name = (meta.get("name") or "")[:500]
        link.mime_type = FOLDER_MIME
        link.is_folder = True
        link.is_root = True
        link.shared_drive_id = (meta.get("driveId") or "")[:128] or None
        await self.ctx.session.flush()

        if previous_name is None:
            await self._record_folder(entity_type, entity_id, "drive.folder_set", link.name)
        else:
            await ActivityService(self.ctx).record(
                entity_type,
                entity_id,
                "drive.folder_changed",
                {"from": previous_name, "to": link.name},
            )
        return link

    async def _record_folder(
        self, entity_type: str, entity_id: uuid.UUID, action: str, name: str
    ) -> None:
        """One trail line, in the writing transaction (CLAUDE.md §16)."""
        await ActivityService(self.ctx).record(entity_type, entity_id, action, {"name": name})

    # --- resumable upload: bytes go browser → Google, never through this API ------ #
    async def upload_session(
        self, folder_id: str, name: str, mime_type: str | None, origin: str | None
    ) -> str:
        self.ctx.require("google.drive.write")
        await self._settings()
        connection = await self._connection()
        headers = {"X-Upload-Content-Type": mime_type or "application/octet-stream"}
        if origin:
            # Google echoes this origin on the session's CORS headers, which is what lets
            # the browser PUT the bytes straight to googleusercontent (issue #21: no proxying).
            headers["Origin"] = origin
        # Session creation runs with the pool connection released (docs/PERFORMANCE.md).
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                response = await client.post(
                    f"{UPLOAD_API}/files",
                    params={"uploadType": "resumable", "supportsAllDrives": "true"},
                    json={"name": name, "parents": [folder_id]},
                    headers=headers,
                )
                response.raise_for_status()
                session_uri = response.headers.get("location")
        if not session_uri:
            raise AppError("google_upload_failed", "errors.google_upload_failed", status_code=502)
        return session_uri

    # --- create a subfolder while browsing (as the viewing user) ------------------- #
    async def create_folder(self, parent_id: str, name: str) -> dict[str, Any]:
        """Create a folder named ``name`` inside ``parent_id`` — the browser's "New folder".

        Acts as the *viewing* user (§5, like ``browse``/``upload_session``), never the
        automation identity: a viewer only makes folders where Drive already lets them write.
        Name-match first so re-typing an existing name links to it rather than duplicating
        (issue #21's "link, don't duplicate"); no template structure is copied — that is the
        entity-provisioning path's job, not an ad-hoc subfolder's.
        """
        self.ctx.require("google.drive.write")
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
        # Find-or-create runs with the pool connection released (docs/PERFORMANCE.md).
        async with self._call():
            async with (
                acting_as(self.ctx.session, self.ctx.org, connection) as client,
                self.ctx.release_db(),
            ):
                folder = await _find_or_create_folder(
                    client, parent_id, cleaned, template_id=None
                )
        # Bust this viewer's cached *listing* of the parent so the new folder appears at once.
        # Only the unfiltered entry: the browser hides "nieuwe map" while a search is active,
        # so no search result set can be showing this folder to this viewer right now.
        try:
            await get_redis().delete(
                _browse_cache_key(self._org_id, self.ctx.user.id, parent_id)
            )
        except Exception:  # noqa: BLE001 — Redis down just means the ~45 s TTL applies
            pass
        return {
            "id": folder["id"],
            "name": folder.get("name", cleaned),
            "web_view_link": folder.get("webViewLink"),
        }

    # --- provisioning -------------------------------------------------------------- #
    async def provision_state(
        self, entity_type: str | None = None, entity_id: uuid.UUID | None = None
    ) -> dict:
        """What the entity panels need before they draw a provision control (#444).

        The company panel gets these flags from its own provider; the project/task panels load
        over ``/links``, which knows nothing about provisioning readiness — so the button was
        drawn off the caller's permission alone and could only 409. One read answers: is Drive
        on, is the viewer's own connection live (the browser needs it), and is everything the
        provision 409s on actually configured. With an entity named, the panel also learns
        whether a queued folder job is still pending or has failed — a worker slower than one
        optimistic reload otherwise leaves "de map wordt aangemaakt…" standing forever.
        """
        row = await google_settings_row(self.ctx.session, self._org_id)
        enabled = bool(row is not None and row.drive_enabled)
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
                and self.ctx.can("google.drive.write")
            ),
            "job_status": None,
            "job_error": None,
        }
        if entity_type is not None and entity_id is not None:
            await self._require_visible(entity_type, entity_id)
            job = await self.ctx.session.scalar(
                select(DriveFolderJob).where(
                    DriveFolderJob.org_id == self._org_id,
                    DriveFolderJob.entity_type == entity_type,
                    DriveFolderJob.entity_id == entity_id,
                )
            )
            if job is not None and job.status in (
                FolderJobStatus.PENDING.value,
                FolderJobStatus.FAILED.value,
            ):
                state["job_status"] = job.status
                # Google's own sentence, scrubbed on write — an admin-facing read like the
                # container's ``last_error``, never an i18n key (§9).
                state["job_error"] = job.last_error
        return state

    async def request_provision(self, entity_type: str, entity_id: uuid.UUID) -> None:
        """Queue one entity's folder (the panel's "create folder" button)."""
        self.ctx.require("google.drive.write")
        settings_row = await self._settings()
        if not settings_row.automation_connection_user_id:
            raise AppError(
                "google_no_automation_connection",
                "errors.google_no_automation_connection",
                status_code=409,
            )
        if drive_root(settings_row) is None:
            # Fail here, visibly — a queued job the worker can only skip is a phantom
            # 202 the user reads as "the button did nothing" (#149).
            raise AppError(
                "google_drive_no_folder", "errors.google_drive_no_folder", status_code=409
            )
        await self._require_visible(entity_type, entity_id)
        if await self.root_link(entity_type, entity_id) is not None:
            # A record has one folder. Wanting a different one is the picker's job (and its
            # permission) — not a second provisioning run that would land a phantom folder.
            raise AppError(
                "google_drive_folder_exists",
                "errors.google_drive_folder_exists",
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

        A project folder nests under its client's (#150); a task's under its project's (#328).
        Both are bare-table lookups, never an import of another module's internals (§6). The
        *folder* is deliberately not resolved here: the outbox row may sit while the parent
        gets a folder of its own, and the worker walks the chain then.
        """
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
            # A task on no project still belongs to a client often enough to say so; the
            # agency's own to-do list has neither, and lands in the org root like a client does.
            if project_id:
                return ("project", project_id)
            return ("company", company_id) if company_id else (None, None)
        return (None, None)

    async def bulk_provision(self) -> int:
        """Backfill: queue a folder for every company without one. Returns the queue size."""
        self.ctx.require("google.settings.manage")
        settings_row = await self._settings()
        if not settings_row.automation_connection_user_id:
            raise AppError(
                "google_no_automation_connection",
                "errors.google_no_automation_connection",
                status_code=409,
            )
        if drive_root(settings_row) is None:
            raise AppError(
                "google_drive_no_folder", "errors.google_drive_no_folder", status_code=409
            )
        rows = await self.ctx.session.execute(
            text(
                """
                SELECT c.id, c.name FROM companies c
                WHERE c.org_id = :oid
                  AND NOT EXISTS (
                    SELECT 1 FROM drive_links l
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
        """§15's failure mode (4): every surface here is **entity-addressed**, so holding
        ``google.drive.read``/``.write`` is not the same as being allowed to see *that* record.

        Free for an unrestricted membership (no query); only a company-group-scoped one pays,
        and the record's own repository answers, so an indirect company link is honoured.
        """
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
) -> DriveFolderJob:
    """Idempotent outbox insert + a best-effort worker offer (the sweep cron backstops)."""
    job = await session.scalar(
        select(DriveFolderJob).where(
            DriveFolderJob.org_id == org_id,
            DriveFolderJob.entity_type == entity_type,
            DriveFolderJob.entity_id == entity_id,
        )
    )
    if job is None:
        job = DriveFolderJob(
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
        # A re-queued job re-resolves its parent: the record may have been moved to another
        # project since the row was written, and a retry must not nest under the old one.
        job.parent_entity_id = parent_entity_id
        job.parent_entity_type = parent_entity_type
    await session.flush()

    from datetime import timedelta

    from app.core.jobs import enqueue

    try:
        await enqueue(
            "google_drive_provision_folder",
            str(org_id),
            str(job.id),
            _defer_by=timedelta(seconds=2),
        )
    except Exception:  # noqa: BLE001 — the sweep cron re-offers pending jobs
        logger.warning("drive provision enqueue failed for job %s; sweep will retry", job.id)
    return job


# --------------------------------------------------------------------------- #
# Worker side — folder creation with the org's automation connection
# --------------------------------------------------------------------------- #
MAX_ATTEMPTS = 5
#: Template copies are bounded — a template is a skeleton, not an archive.
_TEMPLATE_MAX_DEPTH = 3
_TEMPLATE_MAX_ITEMS = 100


def drive_root(settings_row: GoogleSettings) -> str | None:
    """Where new work lands when no explicit folder is given: the configured parent
    folder, else the shared drive's **root** (#149) — the Drive API accepts a shared
    drive's id as its root folder id, and every call here already sends
    ``supportsAllDrives``. ``None`` means Drive is genuinely unconfigured."""
    return settings_row.drive_parent_folder_id or settings_row.drive_shared_drive_id or None


async def provision_folder(session: AsyncSession, org: Org, job: DriveFolderJob) -> None:
    if job.status != FolderJobStatus.PENDING.value:
        return
    settings_row = await google_settings_row(session, org.id)
    if (
        settings_row is None
        or not settings_row.drive_enabled
        or not settings_row.automation_connection_user_id
        or drive_root(settings_row) is None
    ):
        job.status = FolderJobStatus.SKIPPED.value
        job.last_error = "drive_not_configured"
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
    if missing_drive_scope(connection.scopes):
        # An automation account picked before Drive was switched on is ``active`` and cannot
        # make a folder. Five attempts of 403 would say the same thing five times over.
        job.status = FolderJobStatus.SKIPPED.value
        job.last_error = "automation_connection_missing_drive_scope"
        await session.flush()
        return

    # A project folder nests under its client's; a task's under its project's, else its
    # client's (#328). Resolved *now*, not at emit time: the parent may have acquired a folder
    # while this job sat in the outbox.
    parent = await _parent_folder_id(session, org, job) or drive_root(settings_row)

    try:
        async with acting_as(session, org, connection) as client:
            folder = await _find_or_create_folder(
                client,
                parent,
                job.name,
                # Templates shape *client* folders; a project subfolder starts empty.
                template_id=(
                    settings_row.drive_template_folder_id
                    if job.entity_type == "company"
                    else None
                ),
            )
    except Exception as exc:
        from app.integrations.google.client import is_oauth_error

        job.attempts += 1
        # Google's reason, not httpx's status line: ``str(exc)`` on an HTTP error is the URL and
        # nothing else, and this string is the only account of the failure a human ever reads.
        detail = describe_api_error(exc)
        job.last_error = str(detail or exc)[:500]
        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        if job.attempts >= MAX_ATTEMPTS:
            job.status = FolderJobStatus.FAILED.value
        logger.warning(
            "drive provisioning failed for job %s (attempt %s, %s): %s",
            job.id,
            job.attempts,
            await oauth_client_hint(session, org.id),
            detail or exc,
        )
        await session.flush()
        return

    existing = await session.scalar(
        select(DriveLink).where(
            DriveLink.org_id == org.id,
            DriveLink.entity_type == job.entity_type,
            DriveLink.entity_id == job.entity_id,
            DriveLink.drive_file_id == folder["id"],
        )
    )
    # The provisioned folder becomes the record's folder unless somebody picked one while the
    # job sat in the outbox — the worker never re-points a decision a human made.
    has_root = await session.scalar(
        select(DriveLink.id).where(
            DriveLink.org_id == org.id,
            DriveLink.entity_type == job.entity_type,
            DriveLink.entity_id == job.entity_id,
            DriveLink.is_root,
        )
    )
    if existing is not None:
        # Already attached as an ordinary link (someone linked this very folder by hand before
        # the job ran): promote it, or the record would read as folderless for ever.
        if has_root is None and existing.is_folder:
            existing.is_root = True
    else:
        session.add(
            DriveLink(
                org_id=org.id,
                entity_type=job.entity_type,
                entity_id=job.entity_id,
                drive_file_id=folder["id"],
                drive_url=(folder.get("webViewLink") or "")[:500],
                name=(folder.get("name") or job.name)[:500],
                mime_type=FOLDER_MIME,
                is_folder=True,
                is_root=has_root is None,
                shared_drive_id=settings_row.drive_shared_drive_id,
            )
        )
    job.status = FolderJobStatus.DONE.value
    job.last_error = None
    await session.flush()


async def _parent_folder_id(
    session: AsyncSession, org: Org, job: DriveFolderJob
) -> str | None:
    """The Drive folder the job's new folder nests inside, or ``None`` for the org root.

    Walks the record chain rather than reading one link: a task nests under its project's
    folder, and a project whose own folder was never provisioned is not a dead end — the
    client's folder is the next honest answer, and it is where the panel already sends the
    browser. ``parent_entity_type`` is ``NULL`` on every row written before tasks could nest
    (and on anything an older replica writes mid-rollout), which means ``company``.
    """
    parent_id = job.parent_entity_id
    if parent_id is None:
        return None
    parent_type = job.parent_entity_type or "company"
    # At most two steps — project, then its client — and the second can only end the loop.
    while parent_id is not None:
        link = await session.scalar(
            select(DriveLink).where(
                DriveLink.org_id == org.id,
                DriveLink.entity_type == parent_type,
                DriveLink.entity_id == parent_id,
                DriveLink.is_root,
            )
        )
        if link is not None:
            return link.drive_file_id
        if parent_type != "project":
            return None
        # Bare-table lookup, never a projects-module import (§6) — the same crossing
        # ``links_for``'s roll-up makes.
        parent_id = await session.scalar(
            text("SELECT company_id FROM projects WHERE id = :pid AND org_id = :oid"),
            {"pid": parent_id, "oid": org.id},
        )
        parent_type = "company"
    return None


async def _find_or_create_folder(
    client: Any, parent: str, name: str, *, template_id: str | None
) -> dict[str, Any]:
    """Name-match under the parent first (link, don't duplicate — issue #21); else create,
    copying the template's structure when one is configured."""
    query = (
        f"name = '{_drive_query_escape(name)}' and '{_drive_query_escape(parent)}' in parents "
        f"and mimeType = '{FOLDER_MIME}' and trashed = false"
    )
    response = await client.get(
        f"{DRIVE_API}/files",
        params={
            "q": query,
            "fields": "files(id,name,webViewLink)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "pageSize": "1",
        },
    )
    response.raise_for_status()
    matches = response.json().get("files", [])
    if matches:
        return matches[0]

    folder = await _create_folder(client, parent, name)
    if template_id:
        await _copy_template_children(client, template_id, folder["id"], depth=0, budget=[0])
    return folder


async def _create_folder(client: Any, parent: str, name: str) -> dict[str, Any]:
    response = await client.post(
        f"{DRIVE_API}/files",
        params={"supportsAllDrives": "true", "fields": "id,name,webViewLink"},
        json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
    )
    response.raise_for_status()
    return response.json()


async def _copy_template_children(
    client: Any, source_folder: str, target_folder: str, *, depth: int, budget: list[int]
) -> None:
    if depth >= _TEMPLATE_MAX_DEPTH:
        return
    response = await client.get(
        f"{DRIVE_API}/files",
        params={
            "q": f"'{_drive_query_escape(source_folder)}' in parents and trashed=false",
            "fields": "files(id,name,mimeType)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "pageSize": "100",
        },
    )
    response.raise_for_status()
    for item in response.json().get("files", []):
        if budget[0] >= _TEMPLATE_MAX_ITEMS:
            logger.warning("template copy hit the %s-item cap; stopping", _TEMPLATE_MAX_ITEMS)
            return
        budget[0] += 1
        if item.get("mimeType") == FOLDER_MIME:
            subfolder = await _create_folder(client, target_folder, item["name"])
            await _copy_template_children(
                client, item["id"], subfolder["id"], depth=depth + 1, budget=budget
            )
        else:
            copy = await client.post(
                f"{DRIVE_API}/files/{item['id']}/copy",
                params={"supportsAllDrives": "true"},
                json={"name": item["name"], "parents": [target_folder]},
            )
            copy.raise_for_status()
