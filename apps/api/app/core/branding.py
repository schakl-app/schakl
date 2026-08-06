"""The tenant's logo as **bytes**, for renderers that cannot follow a URL.

``org_settings.logo_url`` is what a browser needs: a path the web app resolves against the
org's own host (``/api/v1/files/<id>/public``). A PDF has no browser and must never make an
outbound request to render a document — an org-controlled URL fetched by the server is an
SSRF the moment someone edits it by hand. So the id is read straight out of the path and the
bytes come from the storage backend the file was written to.

A logo that cannot be resolved returns ``(None, None)``: every caller degrades to the brand
name. Branding must never be able to fail an invoice.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from sqlalchemy import select

from app.core.storage.backend import StorageUnavailableError, storage_for
from app.core.storage.models import StoredFile

logger = logging.getLogger("schakl.branding")

#: The shape `settings/branding` writes: `/api/v1/files/<uuid>/public`. Anything else — an
#: absolute URL to a CDN, a data: URI — is not ours to read from disk.
_LOCAL_FILE_URL = re.compile(
    r"^/api/v\d+/files/(?P<id>[0-9a-fA-F-]{36})/public/?$",
)


def _file_id(logo_url: str | None) -> uuid.UUID | None:
    match = _LOCAL_FILE_URL.match((logo_url or "").strip())
    if match is None:
        return None
    try:
        return uuid.UUID(match.group("id"))
    except ValueError:
        return None


async def load_org_image(ctx, file_id, *, what: str = "image") -> tuple[bytes | None, str | None]:  # noqa: ANN001
    """``(bytes, content_type)`` of one of *this org's* stored files, or ``(None, None)``.

    The tenant scope is on the statement, so a file id belonging to another org resolves to
    nothing rather than to their artwork — an id is caller-supplied wherever this is used
    (a template's background is a config value), and §5's rule holds: never a raw id lookup
    that is not tenant-scoped.

    Every failure degrades rather than raises. A missing or unreadable image must cost a
    logo, never the invoice a client is waiting for.

    **The key comes from the row, never from its ids.** This built ``{org_id}/{id}``, which was
    the layout before de-duplication (``docs/STORAGE.md``): a file row is not its bytes, and
    since ``file_blobs`` the object lives at ``{org_id}/sha256/{digest}`` with exactly one copy
    per distinct content per org. So the path this composed had not existed for any file
    written since, and every document quietly printed without its logo, its background mark and
    its cover — for months, because the ``OSError`` lands in the degrade-don't-raise branch
    three lines down. A silent fallback needs a test that the *happy* path still happens; the
    one below round-trips a real upload rather than a mocked backend.
    """
    if file_id is None:
        return None, None
    stored = await ctx.session.scalar(
        select(StoredFile).where(StoredFile.org_id == ctx.org.id, StoredFile.id == file_id)
    )
    if stored is None:
        return None, None
    try:
        backend = storage_for(stored.backend)
        # Blocking storage IO off the event loop, the rule the file routes follow (#190).
        data = await asyncio.to_thread(lambda: backend.open(stored.storage_key).read())
    except (StorageUnavailableError, OSError):
        logger.warning("%s %s could not be read; rendering without it", what, file_id)
        return None, None
    return data, stored.content_type


async def load_brand_logo(ctx, org_settings) -> tuple[bytes | None, str | None]:  # noqa: ANN001
    """``(bytes, content_type)`` of the org's logo, or ``(None, None)``."""
    return await load_org_image(
        ctx, _file_id(getattr(org_settings, "logo_url", None)), what="brand logo"
    )
