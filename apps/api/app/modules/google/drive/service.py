"""Drive service: browse-as-the-viewer, links CRUD, resumable uploads, folder provisioning.

Two rules from docs/GOOGLE.md §5 and issue #21 govern everything here:

- **Permissions are Drive's, not ours.** Listing and metadata reads always act as the
  *viewing* user's connection — never a privileged identity that would leak files across the
  agency. A viewer who cannot see a file in Drive does not see it here.
- **Unlink never deletes.** Deleting a ``drive_link`` removes the reference; no code path in
  this module issues a Drive delete.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.models import Org
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.google.client import (
    acting_as,
    active_connection_or_409,
    connection_for,
    mark_connection_error,
)
from app.modules.google.drive.models import (
    DRIVE_ENTITY_TYPES,
    DriveFolderJob,
    DriveLink,
    FolderJobStatus,
)
from app.modules.google.models import ConnectionStatus, GoogleSettings
from app.modules.google.oauth import google_settings_row

logger = logging.getLogger("schakl.google.drive")

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

#: Listings are live-as-the-viewer with a short Redis cache — snappy, Drive authoritative.
BROWSE_CACHE_TTL = 45
_BROWSE_FIELDS = "nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime,size)"

_ENTITY_TABLES = {"company": "companies", "project": "projects", "task": "tasks"}
_ENTITY_NAME_COLUMNS = {"company": "name", "project": "name", "task": "title"}


def _drive_query_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


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

    # --- browse (as the viewing user) ------------------------------------------- #
    async def browse(self, folder_id: str | None, *, refresh: bool = False) -> dict[str, Any]:
        settings_row = await self._settings()
        target = folder_id or settings_row.drive_parent_folder_id
        if not target:
            raise AppError(
                "google_drive_no_folder", "errors.google_drive_no_folder", status_code=409
            )
        connection = await active_connection_or_409(
            self.ctx.session, self._org_id, self.ctx.user.id
        )

        cache_key = f"schakl:gdrive:browse:{self._org_id}:{self.ctx.user.id}:{target}"
        if not refresh:
            try:
                cached = await get_redis().get(cache_key)
            except Exception:  # noqa: BLE001 — a cold cache, not an error
                cached = None
            if cached:
                return json.loads(cached)

        params = {
            "q": f"'{_drive_query_escape(target)}' in parents and trashed=false",
            "fields": _BROWSE_FIELDS,
            "orderBy": "folder,name",
            "pageSize": "100",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        async with acting_as(self.ctx.session, self.ctx.org, connection) as client:
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
        }
        try:
            await get_redis().set(cache_key, json.dumps(listing), ex=BROWSE_CACHE_TTL)
        except Exception:  # noqa: BLE001 — Redis down just means no cache
            pass
        return listing

    # --- links ------------------------------------------------------------------- #
    async def links_for(
        self, entity_type: str, entity_id: uuid.UUID, *, rollup: bool = False
    ) -> list[DriveLink]:
        conditions = [
            DriveLink.org_id == self._org_id,
            DriveLink.entity_type == entity_type,
            DriveLink.entity_id == entity_id,
        ]
        stmt = select(DriveLink).where(*conditions)
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
        await self._settings()
        if entity_type not in DRIVE_ENTITY_TYPES:
            raise AppError("validation", "errors.validation", status_code=422)
        await self._ensure_entity(entity_type, entity_id)
        connection = await active_connection_or_409(
            self.ctx.session, self._org_id, self.ctx.user.id
        )
        # Metadata comes from Drive as the caller — authoritative, and it proves they can
        # actually see the file they are linking.
        async with acting_as(self.ctx.session, self.ctx.org, connection) as client:
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
        link = DriveLink(
            org_id=self._org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            drive_file_id=meta["id"],
            drive_url=(meta.get("webViewLink") or "")[:500],
            name=(meta.get("name") or "")[:500],
            mime_type=(meta.get("mimeType") or "")[:255] or None,
            is_folder=meta.get("mimeType") == FOLDER_MIME,
            shared_drive_id=(meta.get("driveId") or "")[:128] or None,
            created_by_user_id=self.ctx.user.id,
            created_by_name=self.ctx.user.full_name or self.ctx.user.email,
        )
        self.ctx.session.add(link)
        await self.ctx.session.flush()
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
        await self.ctx.session.delete(link)
        await self.ctx.session.flush()

    # --- resumable upload: bytes go browser → Google, never through this API ------ #
    async def upload_session(
        self, folder_id: str, name: str, mime_type: str | None, origin: str | None
    ) -> str:
        self.ctx.require("google.drive.write")
        await self._settings()
        connection = await active_connection_or_409(
            self.ctx.session, self._org_id, self.ctx.user.id
        )
        headers = {"X-Upload-Content-Type": mime_type or "application/octet-stream"}
        if origin:
            # Google echoes this origin on the session's CORS headers, which is what lets
            # the browser PUT the bytes straight to googleusercontent (issue #21: no proxying).
            headers["Origin"] = origin
        async with acting_as(self.ctx.session, self.ctx.org, connection) as client:
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

    # --- provisioning -------------------------------------------------------------- #
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
        name = await self._entity_name(entity_type, entity_id)
        if name is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await queue_folder_job(
            self.ctx.session, self._org_id, entity_type, entity_id, name
        )

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
        rows = await self.ctx.session.execute(
            text(
                """
                SELECT c.id, c.name FROM companies c
                WHERE c.org_id = :oid
                  AND NOT EXISTS (
                    SELECT 1 FROM drive_links l
                    WHERE l.org_id = :oid AND l.entity_type = 'company'
                      AND l.entity_id = c.id AND l.is_folder
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
    async def _ensure_entity(self, entity_type: str, entity_id: uuid.UUID) -> None:
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
        )
        session.add(job)
    else:
        job.status = FolderJobStatus.PENDING.value
        job.attempts = 0
        job.last_error = None
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


async def provision_folder(session: AsyncSession, org: Org, job: DriveFolderJob) -> None:
    if job.status != FolderJobStatus.PENDING.value:
        return
    settings_row = await google_settings_row(session, org.id)
    if (
        settings_row is None
        or not settings_row.drive_enabled
        or not settings_row.automation_connection_user_id
        or not settings_row.drive_parent_folder_id
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

    # A project folder nests under its company's folder when that exists.
    parent = settings_row.drive_parent_folder_id
    if job.parent_entity_id is not None:
        company_folder = await session.scalar(
            select(DriveLink).where(
                DriveLink.org_id == org.id,
                DriveLink.entity_type == "company",
                DriveLink.entity_id == job.parent_entity_id,
                DriveLink.is_folder,
            )
        )
        if company_folder is not None:
            parent = company_folder.drive_file_id

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
        from app.modules.google.client import is_oauth_error

        job.attempts += 1
        job.last_error = str(exc)[:500]
        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        if job.attempts >= MAX_ATTEMPTS:
            job.status = FolderJobStatus.FAILED.value
        logger.warning("drive provisioning failed for job %s (attempt %s)", job.id, job.attempts)
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
    if existing is None:
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
                shared_drive_id=settings_row.drive_shared_drive_id,
            )
        )
    job.status = FolderJobStatus.DONE.value
    job.last_error = None
    await session.flush()


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
