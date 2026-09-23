"""A colleague's picture as bytes, for a document that prints it.

The screens draw a person's picture straight from its URL — the browser fetches it, and that
is the browser's business. A document is different: the renderer's fetcher answers ``data:``
and nothing else (``core/documents``), so a picture only reaches paper as bytes the API read
itself. Two sources exist (#122's precedence, ``members.effective_avatar_url``):

* **A personal upload** (``custom_avatar_url`` → ``/api/v1/files/<id>``) is a stored file of
  this org, read through the org-scoped image loader.
* **The identity provider's picture** (``oidc_avatar_url``) is a URL on somebody else's
  server — for a Google Workspace sign-in, every colleague's picture. It is fetched, but only
  from a **closed list of IdP picture hosts**, over HTTPS, without following redirects, from a
  host that resolves to public addresses only (``net_guard``), capped in size and time, and
  answered as an image. A URL the list does not name is never requested: the column is filled
  from a login's claims, and a document render must not become a way to make this server call
  an arbitrary address. Every failure degrades to initials — a picture must never cost the
  document.

Pictures are cached per process for an hour: a meeting's roster is the same six people on
every preview of the settings screen, and the IdP's CDN has no reason to see each one.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.branding import load_org_image
from app.core.net_guard import SsrfBlocked, assert_host_public_sync

logger = logging.getLogger("schakl.avatars")

_LOCAL_FILE = re.compile(r"^/api/v\d+/files/(?P<id>[0-9a-fA-F-]{36})(?:/public)?/?$")

#: The identity providers' picture hosts. A suffix match on a dot boundary, so
#: ``lh3.googleusercontent.com`` matches and ``evilgoogleusercontent.com`` does not.
IDP_PICTURE_HOSTS: tuple[str, ...] = ("googleusercontent.com",)

_MAX_BYTES = 512 * 1024
_TIMEOUT = httpx.Timeout(4.0, connect=2.0)
_TTL_SECONDS = 3600
_CACHE_MAX = 256
_cache: dict[str, tuple[float, bytes | None, str | None]] = {}


def _allowed_host(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return None
    if any(host == h or host.endswith("." + h) for h in IDP_PICTURE_HOSTS):
        return host
    return None


async def _fetch_idp_picture(url: str) -> tuple[bytes | None, str | None]:
    host = _allowed_host(url)
    if host is None:
        return None, None
    now = time.monotonic()
    hit = _cache.get(url)
    if hit is not None and hit[0] > now:
        return hit[1], hit[2]
    payload: bytes | None = None
    content_type: str | None = None
    try:
        await asyncio.to_thread(assert_host_public_sync, host, allow_private=False)
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if response.status_code == 200 and kind.startswith("image/"):
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            chunks = []
                            break
                        chunks.append(chunk)
                    if chunks:
                        payload, content_type = b"".join(chunks), kind
    except (httpx.HTTPError, SsrfBlocked, OSError) as exc:
        logger.info("avatar %s could not be fetched: %s", host, exc)
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    # A miss is cached too, for a shorter while: a dead picture should not cost every render.
    ttl = _TTL_SECONDS if payload else _TTL_SECONDS // 6
    _cache[url] = (now + ttl, payload, content_type)
    return payload, content_type


async def load_user_avatar(ctx: Any, user: Any) -> tuple[bytes | None, str | None]:
    """``(bytes, content_type)`` of a colleague's picture, or ``(None, None)`` for initials.

    The precedence is the screens' own: a personal upload first, then the IdP's picture.
    """
    custom = (getattr(user, "custom_avatar_url", None) or "").strip()
    if custom:
        match = _LOCAL_FILE.match(custom)
        if match is not None:
            try:
                file_id = uuid.UUID(match.group("id"))
            except ValueError:
                file_id = None
            if file_id is not None:
                payload, content_type = await load_org_image(ctx, file_id, what="avatar")
                if payload:
                    return payload, content_type
        else:
            payload, content_type = await _fetch_idp_picture(custom)
            if payload:
                return payload, content_type
    oidc = (getattr(user, "oidc_avatar_url", None) or "").strip()
    if oidc:
        return await _fetch_idp_picture(oidc)
    return None, None


__all__ = ["IDP_PICTURE_HOSTS", "load_user_avatar"]
