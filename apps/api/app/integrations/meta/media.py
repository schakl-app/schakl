"""A post's media: what it is, what Meta will accept, and how Meta gets at it.

Two channels, two ways in, and the difference decides the shape of this file.

**Facebook takes bytes.** A photo is uploaded in the request that makes it, so a Facebook
delivery needs nothing from the outside world and works on an instance no stranger can reach.

**Instagram takes an address.** Its API fetches the media itself, from a URL that has to be
"publicly accessible at the time of the attempt". So an Instagram delivery mints a
:class:`~app.integrations.meta.models.MetaMediaToken` — a capability for one file, the public
invoice link's shape (#304) — hands Meta the address, and **withdraws it when the delivery is
over**. The off switch is read before the token is compared, so a withdrawn address is dead
even to somebody who kept it.

**Instagram takes JPEG and nothing else**, so the public route serves JPEG whatever was
uploaded: a PNG or a WebP is re-encoded on the way out, flattened onto white where it had
transparency. The original is never touched — a stored file is evidence of what was uploaded.

What a file *is* (its size in pixels, its type) is measured **once, when it is attached**, and
kept on the post's media entry. The composer's warnings and the publisher's refusal then read
the same numbers, and nobody decodes an image to draw a list.
"""

from __future__ import annotations

import asyncio
import io
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.hosts import org_base_url
from app.core.storage.backend import StorageUnavailableError, storage_for
from app.core.storage.models import StoredFile
from app.errors import AppError
from app.integrations.meta.models import MetaMediaToken

ENTITY_TYPE = "meta_post"

KIND_IMAGE = "image"
KIND_VIDEO = "video"

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
#: What may be *added* to a post. A GIF already on one is still read; a new one is not
#: taken, because Instagram publishes it as a still and says nothing.
UPLOAD_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

#: Instagram's documented bounds for a feed image.
IG_MIN_RATIO = 4 / 5
IG_MAX_RATIO = 1.91
IG_MAX_IMAGE_BYTES = 8 * 1024 * 1024
IG_MAX_CAROUSEL = 10
IG_CAPTION_MAX = 2_200
IG_HASHTAG_MAX = 30
#: Facebook's own ceiling for a photo upload.
FB_MAX_IMAGE_BYTES = 10 * 1024 * 1024
FB_BODY_MAX = 63_206
#: A small tolerance on the ratio: 1080×1350 is exactly 4:5, 1080×1349 is a rounding artefact.
RATIO_TOLERANCE = 0.01

#: How long a public media address lives if nothing withdraws it first. A video container may
#: take minutes to process and a delivery may be retried by the next sweep, so an hour; the
#: delivery's own end withdraws it long before that in the ordinary case.
TOKEN_TTL = timedelta(hours=1)


@dataclass(frozen=True)
class MediaItem:
    """One entry of ``MetaPost.media``, read."""

    kind: str
    file_id: uuid.UUID | None = None
    url: str | None = None
    alt: str = ""
    width: int | None = None
    height: int | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    filename: str | None = None

    @property
    def ratio(self) -> float | None:
        if not self.width or not self.height:
            return None
        return self.width / self.height

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "alt": self.alt}
        if self.file_id is not None:
            out["file_id"] = str(self.file_id)
        if self.url:
            out["url"] = self.url
        for key in ("width", "height", "content_type", "size_bytes", "filename"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


def read_media(raw: list | None) -> list[MediaItem]:
    """``MetaPost.media`` as items. Tolerant: a malformed entry is skipped, never raised on —
    the column is JSONB and outlives whatever wrote it."""
    out: list[MediaItem] = []
    for entry in raw or ():
        if not isinstance(entry, dict):
            continue
        file_id = None
        if entry.get("file_id"):
            try:
                file_id = uuid.UUID(str(entry["file_id"]))
            except ValueError:
                continue
        url = str(entry.get("url") or "") or None
        if file_id is None and url is None:
            continue
        out.append(
            MediaItem(
                kind=str(entry.get("kind") or KIND_IMAGE),
                file_id=file_id,
                url=url,
                alt=str(entry.get("alt") or ""),
                width=_int(entry.get("width")),
                height=_int(entry.get("height")),
                content_type=str(entry.get("content_type") or "") or None,
                size_bytes=_int(entry.get("size_bytes")),
                filename=str(entry.get("filename") or "") or None,
            )
        )
    return out


async def resolve_media(
    ctx: Any,
    post_id: uuid.UUID,
    entries: list[dict[str, Any]],
    known: list[MediaItem],
) -> list[MediaItem]:
    """What a caller sent as media, as measured items.

    A stored file must be **this post's own** — uploaded with ``entity_type=meta_post`` and
    this post's id — so a caller cannot make a post publish somebody else's attachment by
    naming its id. An entry already measured keeps its measurement.
    """
    measured = {item.file_id: item for item in known if item.file_id is not None}
    out: list[MediaItem] = []
    wanted = []
    for index, entry in enumerate(entries):
        kind = str(entry.get("kind") or KIND_IMAGE)
        if kind not in (KIND_IMAGE, KIND_VIDEO):
            raise _invalid(f"media.{index}.kind", "errors.validation")
        alt = str(entry.get("alt") or "")[:1_000]
        if entry.get("file_id"):
            try:
                file_id = uuid.UUID(str(entry["file_id"]))
            except ValueError:
                raise _invalid(f"media.{index}.file_id", "errors.validation") from None
            wanted.append((index, file_id, kind, alt))
            out.append(MediaItem(kind=kind, file_id=file_id, alt=alt))
            continue
        url = str(entry.get("url") or "").strip()
        if not url.lower().startswith("https://"):
            # Meta fetches it from its own servers, and refuses anything but https.
            raise _invalid(f"media.{index}.url", "errors.meta_media_url")
        out.append(MediaItem(kind=kind, url=url[:2_000], alt=alt))

    if not wanted:
        return out
    rows = {
        row.id: row
        for row in (
            await ctx.session.scalars(
                select(StoredFile).where(
                    StoredFile.org_id == ctx.org.id,
                    StoredFile.id.in_([file_id for _, file_id, _, _ in wanted]),
                    StoredFile.entity_type == ENTITY_TYPE,
                    StoredFile.entity_id == post_id,
                )
            )
        ).all()
    }
    for index, file_id, kind, alt in wanted:
        row = rows.get(file_id)
        if row is None:
            raise _invalid(f"media.{index}.file_id", "errors.meta_media_not_found")
        if kind == KIND_IMAGE and row.content_type not in IMAGE_TYPES:
            raise _invalid(f"media.{index}.file_id", "errors.meta_media_type")
        before = measured.get(file_id)
        width, height = (before.width, before.height) if before else (None, None)
        if kind == KIND_IMAGE and (width is None or height is None):
            width, height = await _measure(row)
        position = next(i for i, item in enumerate(out) if item.file_id == file_id)
        out[position] = MediaItem(
            kind=kind,
            file_id=file_id,
            alt=alt,
            width=width,
            height=height,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            filename=row.filename,
        )
    return out


async def read_bytes(stored: StoredFile) -> bytes:
    """A stored file's bytes, off the event loop. Called with the database released."""
    try:
        backend = storage_for(stored.backend)
    except StorageUnavailableError:
        raise AppError("not_found", "errors.storage_backend_unavailable", status_code=404) from None
    handle = await asyncio.to_thread(backend.open, stored.storage_key)
    try:
        return await asyncio.to_thread(handle.read)
    finally:
        close = getattr(handle, "close", None)
        if close is not None:
            await asyncio.to_thread(close)


def as_jpeg(data: bytes) -> bytes:
    """Any raster image as a JPEG Instagram will take. Pillow work — run it in a thread.

    Transparency is flattened onto white, EXIF orientation is applied (or every portrait
    photo from a phone lies down), and an image already a JPEG within bounds is returned
    byte for byte rather than re-encoded, because re-encoding costs quality for nothing.
    """
    from PIL import Image, ImageOps  # local import: Pillow loads only when an image is served

    with Image.open(io.BytesIO(data)) as source:
        already = (source.format or "").upper() == "JPEG"
        orientation = source.getexif().get(0x0112, 1) if already else 1
        if already and orientation == 1 and len(data) <= IG_MAX_IMAGE_BYTES:
            return data
        img = ImageOps.exif_transpose(source) or source
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (255, 255, 255))
            canvas.paste(rgba, mask=rgba.split()[-1])
            img = canvas
        else:
            img = img.convert("RGB")
        for quality in (90, 82, 74, 66):
            out = io.BytesIO()
            img.save(out, "JPEG", quality=quality, optimize=True)
            if out.tell() <= IG_MAX_IMAGE_BYTES:
                break
        return out.getvalue()


# --- the public address ----------------------------------------------------------------------- #


async def mint_token(ctx: Any, post_id: uuid.UUID, file_id: uuid.UUID) -> str:
    """A fresh capability for one file. Returns the token, not the address."""
    token = secrets.token_urlsafe(32)
    ctx.session.add(
        MetaMediaToken(
            org_id=ctx.org.id,
            token=token,
            file_id=file_id,
            post_id=post_id,
            expires_at=datetime.now(UTC) + TOKEN_TTL,
        )
    )
    await ctx.session.flush()
    return token


def public_url(org: Any, token: str, kind: str = KIND_IMAGE) -> str:
    """The address Meta fetches. Carries an extension because Meta sniffs the type off it.

    Lives under ``/api/`` because that is the only prefix the edge forwards to the API
    (CLAUDE.md §12): a route the edge does not forward is a route nobody has.
    """
    extension = "jpg" if kind == KIND_IMAGE else "mp4"
    return f"{org_base_url(org)}/api/v1/meta-business/media/{token}.{extension}"


async def revoke_tokens(ctx: Any, post_id: uuid.UUID) -> None:
    """Withdraw every address minted for a post. Retroactive by construction."""
    await ctx.session.execute(
        update(MetaMediaToken)
        .where(
            MetaMediaToken.org_id == ctx.org.id,
            MetaMediaToken.post_id == post_id,
            MetaMediaToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def file_for_token(session: Any, org_id: uuid.UUID, token: str) -> StoredFile | None:
    """The file a public address names, or ``None`` — for an unknown, expired or withdrawn one.

    The three are one answer on purpose: a 404 that distinguished "withdrawn" from "never
    existed" would confirm to a stranger that the address once worked.
    """
    now = datetime.now(UTC)
    rows = (
        await session.scalars(
            select(MetaMediaToken).where(
                MetaMediaToken.org_id == org_id,
                MetaMediaToken.revoked_at.is_(None),
                MetaMediaToken.expires_at > now,
            )
        )
    ).all()
    # The live set is a handful of rows — one per file of a delivery in flight — so comparing
    # in constant time against each is cheaper than it looks and leaks nothing through timing.
    match = next((row for row in rows if secrets.compare_digest(row.token, token)), None)
    if match is None:
        return None
    return await session.scalar(
        select(StoredFile).where(StoredFile.org_id == org_id, StoredFile.id == match.file_id)
    )


async def _measure(stored: StoredFile) -> tuple[int | None, int | None]:
    """An image's size in pixels as it will be *shown* — EXIF orientation applied."""
    try:
        data = await read_bytes(stored)
        return await asyncio.to_thread(_dimensions, data)
    except Exception:  # noqa: BLE001 — an unreadable image is an unmeasured one, not a 500
        return None, None


def _dimensions(data: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size
        if img.getexif().get(0x0112, 1) in (5, 6, 7, 8):
            width, height = height, width
        return width, height


def _invalid(field: str, key: str) -> AppError:
    return AppError("validation", "errors.validation", status_code=422, fields={field: key})


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
