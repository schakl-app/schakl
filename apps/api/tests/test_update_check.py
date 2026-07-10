"""The daily release check (issue #18): semver ordering, opt-out, and the no-nag rules.

Pure logic plus the cached-status branches. Nothing here reaches GitHub — the cron is the only
thing that ever does, and the API only reads what it cached.
"""

from __future__ import annotations

import json

import pytest

import app.core.cache as cache
import app.core.update_check as update_check
from app.core.update_check import cached_update_status, is_newer, parse_version


@pytest.fixture(autouse=True)
async def _reset_redis():
    cache._client = None
    yield
    client = cache._client
    cache._client = None
    if client is not None:
        await client.aclose()


def test_parse_version_rejects_junk():
    for junk in ("", "latest", "1.2", "1.2.3.4", "v1.x.0", "main"):
        assert parse_version(junk) is None, junk


def test_parse_version_strips_prefix_and_build_metadata():
    assert parse_version("v1.2.3") == parse_version("1.2.3")
    assert parse_version("1.2.3+build.7") == parse_version("1.2.3")


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v0.5.0", "0.4.1", True),
        ("0.4.2", "0.4.1", True),
        ("1.0.0", "0.9.9", True),
        ("0.4.1", "0.4.1", False),
        ("0.4.0", "0.4.1", False),
        # A final release outranks its own pre-releases, and vice versa.
        ("1.0.0", "1.0.0-rc.1", True),
        ("1.0.0-rc.1", "1.0.0", False),
        # Numeric pre-release identifiers compare as integers, not strings (semver §11.4).
        ("1.0.0-rc.10", "1.0.0-rc.2", True),
        ("1.0.0-rc.2", "1.0.0-rc.10", False),
        # Unparseable input never claims an update.
        ("garbage", "1.0.0", False),
        ("1.0.0", "garbage", False),
    ],
)
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) is expected


async def test_status_reports_disabled_without_touching_redis(monkeypatch):
    """The opt-out is the operator's env var, so nothing downstream should even be consulted."""
    monkeypatch.setattr(update_check.settings, "update_check_enabled", False)

    def explode():
        raise AssertionError("redis must not be touched when the check is disabled")

    monkeypatch.setattr(update_check, "get_redis", explode)

    status = await cached_update_status()
    assert status["enabled"] is False
    assert status["update_available"] is False
    assert status["latest"] is None


async def test_status_is_quiet_before_the_cron_has_ever_run(monkeypatch):
    monkeypatch.setattr(update_check.settings, "update_check_enabled", True)
    monkeypatch.setattr(update_check, "get_redis", lambda: _FakeRedis(None))

    status = await cached_update_status()
    assert status["enabled"] is True
    assert status["latest"] is None
    assert status["update_available"] is False


async def test_status_never_nags_an_unstamped_source_tree(monkeypatch):
    """A checkout reports 0.0.0+dev, which trails every release. It must not claim an update."""
    monkeypatch.setattr(update_check.settings, "update_check_enabled", True)
    monkeypatch.setattr(update_check.settings, "version", "0.0.0+dev")
    monkeypatch.setattr(update_check, "get_redis", lambda: _FakeRedis(_cached("v9.9.9")))

    status = await cached_update_status()
    assert status["latest"] == "v9.9.9"
    assert status["update_available"] is False


async def test_status_flags_a_newer_release_on_a_stamped_build(monkeypatch):
    monkeypatch.setattr(update_check.settings, "update_check_enabled", True)
    monkeypatch.setattr(update_check.settings, "version", "0.4.1")
    monkeypatch.setattr(update_check, "get_redis", lambda: _FakeRedis(_cached("v0.5.0")))

    status = await cached_update_status()
    assert status["current"] == "0.4.1"
    assert status["latest"] == "v0.5.0"
    assert status["update_available"] is True
    assert status["release_url"] == "https://example.test/releases/v0.5.0"


async def test_status_survives_an_unreachable_cache(monkeypatch):
    monkeypatch.setattr(update_check.settings, "update_check_enabled", True)
    monkeypatch.setattr(update_check, "get_redis", lambda: _FakeRedis(boom=True))

    status = await cached_update_status()
    assert status["update_available"] is False
    assert status["latest"] is None


async def test_cron_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(update_check.settings, "update_check_enabled", False)

    async def explode():
        raise AssertionError("no outbound request may be made when disabled")

    monkeypatch.setattr(update_check, "_fetch_latest_release", explode)
    assert await update_check.check_for_update(None) == "disabled"


async def test_cron_swallows_a_failing_github(monkeypatch):
    """A box with no egress must not accumulate crashed jobs."""
    monkeypatch.setattr(update_check.settings, "update_check_enabled", True)

    async def explode():
        raise OSError("no route to host")

    monkeypatch.setattr(update_check, "_fetch_latest_release", explode)
    assert await update_check.check_for_update(None) == "error"


async def test_cron_caches_what_it_found(monkeypatch):
    monkeypatch.setattr(update_check.settings, "update_check_enabled", True)
    fake = _FakeRedis(None)
    monkeypatch.setattr(update_check, "get_redis", lambda: fake)

    async def found():
        return {"latest": "v1.2.3", "release_url": "https://example.test/r"}

    monkeypatch.setattr(update_check, "_fetch_latest_release", found)
    assert await update_check.check_for_update(None) == "v1.2.3"

    stored = json.loads(fake.written)
    assert stored["latest"] == "v1.2.3"
    assert stored["checked_at"]


def _cached(tag: str) -> str:
    return json.dumps(
        {
            "latest": tag,
            "release_url": f"https://example.test/releases/{tag}",
            "checked_at": "2026-07-09T05:00:00+00:00",
        }
    )


class _FakeRedis:
    def __init__(self, payload: str | None = None, *, boom: bool = False) -> None:
        self._payload = payload
        self._boom = boom
        self.written: str | None = None

    async def get(self, _key: str) -> str | None:
        if self._boom:
            raise ConnectionError("redis is down")
        return self._payload

    async def set(self, _key: str, value: str, ex: int | None = None) -> None:
        self.written = value
