"""Daily "is there a newer release?" check (issue #18).

Self-hosters upgrade by pulling a new image tag; nobody watches their Grafana, so the app has
to tell them a release exists. Design constraints:

* **Instance-level, not tenant-level.** One box makes one outbound call and the answer (a
  version number) is the same for every org on it. So the opt-out is the operator's env var
  ``SCHAKL_UPDATE_CHECK_ENABLED``, not a row in ``org_settings``.
* **No telemetry.** An unauthenticated ``GET`` to the public GitHub Releases API. Nothing
  about this instance is transmitted — not the version, not the org, not a ping.
* **Never auto-updates.** The result is cached and displayed; acting on it is a human's job.

The cron writes the cache; the API only ever reads it, so a slow or unreachable GitHub can
never make a request hang.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, NamedTuple

import httpx

from app.config import settings
from app.core.cache import UPDATE_CHECK_KEY, get_redis

logger = logging.getLogger("schakl.update_check")

#: Keep the last answer well past a day so a few failed checks don't blank the UI.
_CACHE_TTL = 60 * 60 * 24 * 7
_TIMEOUT = httpx.Timeout(10.0)


#: One pre-release identifier, ordered per semver §11.4: numeric ones rank below alphanumeric
#: ones, and numerics compare as integers (so ``rc.2`` precedes ``rc.10``).
_Identifier = tuple[int, int, str]


def _identifier(raw: str) -> _Identifier:
    return (0, int(raw), "") if raw.isdigit() else (1, 0, raw)


class Version(NamedTuple):
    """A comparable subset of semver. Build metadata is ignored, as the spec requires."""

    release: tuple[int, int, int]
    # A release outranks any of its own pre-releases (1.2.0 > 1.2.0-rc.1).
    is_final: bool
    prerelease: tuple[_Identifier, ...]

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Version):
            return NotImplemented
        if self.release != other.release:
            return self.release < other.release
        if self.is_final != other.is_final:
            return other.is_final
        return self.prerelease < other.prerelease


def parse_version(raw: str) -> Version | None:
    """Parse ``v1.2.3``, ``1.2.3-rc.1``, ``1.2.3+build`` → comparable. ``None`` if unparseable."""
    text = raw.strip().lstrip("vV")
    if not text:
        return None
    text = text.split("+", 1)[0]  # drop build metadata
    core, _, pre = text.partition("-")
    parts = core.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    major, minor, patch = (int(p) for p in parts)
    return Version(
        release=(major, minor, patch),
        is_final=not pre,
        prerelease=tuple(_identifier(p) for p in pre.split(".")) if pre else (),
    )


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a strictly newer release than ``current``."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    return b < a


async def _fetch_latest_release() -> dict[str, str] | None:
    """The newest **stable** release. GitHub's ``/releases/latest`` skips drafts + prereleases."""
    url = f"https://api.github.com/repos/{settings.update_check_repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "schakl-update-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 404:
            # Private repo, or no release cut yet. Not an error worth alarming about.
            logger.info("update check: no published release for %s", settings.update_check_repo)
            return None
        response.raise_for_status()
        body = response.json()

    tag = str(body.get("tag_name") or "").strip()
    if not tag:
        return None
    return {"latest": tag, "release_url": str(body.get("html_url") or "")}


async def check_for_update(_ctx: dict | None = None) -> str:
    """ARQ cron entrypoint: refresh the cached latest-release info.

    Instance-wide, so it deliberately does **not** go through ``run_per_org``. Failures are
    logged and swallowed — a self-hosted box with no egress must not accumulate crashed jobs.
    """
    if not settings.update_check_enabled:
        logger.debug("update check disabled by SCHAKL_UPDATE_CHECK_ENABLED")
        return "disabled"
    try:
        release = await _fetch_latest_release()
    except Exception:
        logger.exception("update check failed")
        return "error"
    if release is None:
        return "unknown"

    payload = {**release, "checked_at": datetime.now(UTC).isoformat()}
    await get_redis().set(UPDATE_CHECK_KEY, json.dumps(payload), ex=_CACHE_TTL)
    logger.info("update check: latest release is %s", release["latest"])
    return release["latest"]


async def cached_update_status() -> dict[str, Any]:
    """Read the cached result. Never performs I/O against GitHub — see the module docstring."""
    status: dict[str, Any] = {
        "enabled": settings.update_check_enabled,
        "current": settings.version,
        "latest": None,
        "release_url": None,
        "checked_at": None,
        "update_available": False,
    }
    if not settings.update_check_enabled:
        return status

    try:
        raw = await get_redis().get(UPDATE_CHECK_KEY)
    except Exception:
        logger.warning("update check: cache unreadable", exc_info=True)
        return status
    if not raw:
        return status  # cron hasn't run yet, or the last few checks failed

    cached = json.loads(raw)
    status["latest"] = cached.get("latest")
    status["release_url"] = cached.get("release_url") or None
    status["checked_at"] = cached.get("checked_at")
    # An unstamped source tree parses as 0.0.0 and would trail every release. Stay quiet.
    status["update_available"] = bool(
        settings.is_stamped_build
        and status["latest"]
        and is_newer(str(status["latest"]), settings.version)
    )
    return status
